# -*- coding: utf-8 -*-
"""param_order.py — 寄存器写入顺序的 A/B/C/D 对照（M1a 第九轮）

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/param_order.py

★ 根因（2026-09-13 第八轮上板结果 + 反查 axi_vdma_v6_3_rfs.vhd）：

  上一轮两个判别器把范围压到了假说 A：**S2MM 的 chnl_ready 从来没被拉开过**，
  数据一整晚都在被 dummy tready 收下丢掉（缓冲全零、帧计数恒 255）。

  顺着 chnl_ready 往上游追（rfs.vhd 行号）：
    73002-73004  chnl_ready <= '1' 只在 s2mm_fsync_out_i = '1' 时
    72387        tuser 要传成 fsync_out，必须 d_tready_before_fsync_clr_flag1 = '0'
    72469-72471  这个 flag：halted 时置 1，**只有 s2mm_prmtr_updt_complete 能清 0**
    20507-20518  prmtr_updt_complete_i 的产生：
                     if (resetn='0' or run_stop='0') -> '0'    ← 通道没跑就恒 0
                     elsif (写 VSIZE 寄存器)         -> '1'    ← 只在那一拍
                     else                            -> '0'    ← 只有一拍宽

  **整条链就断在最后这三行。** 我们的驱动顺序是：
        写 VSIZE -> 写 HSIZE/STRIDE -> 写地址 -> 最后才 start()
  写 VSIZE 的那一刻 run_stop 还是 0 —— 脉冲被第一分支吃掉，之后再没人写过
  VSIZE 寄存器 —— prmtr_updt_complete 永远不来 —— flag 永远清不掉 ——
  tuser 永远传不下去 —— chnl_ready 永远是 0 —— 数据永远被丢。

  这不是 IP 的毛病，是**顺序**：Xilinx 自家的 XAxiVdma_StartWriteFrame 就是
  先 DmaStart（置 RS=1）再把参数寄存器写进去。我们写反了。

本脚本做四组对照，全部纯 PS 侧、不用重烧 bit：

  变体 1  写参数 -> start            （现行做法，预期仍然零帧，作为对照组）
  变体 2  ★ start -> 写 VSIZE        （只补那一拍脉冲 —— 最小改动）
  变体 3  ★ start -> 写 VSIZE+HSIZE+STRIDE
  变体 4  ★ start -> 写全套（含 4 个起始地址，对齐官方驱动）

  判读：哪一变体的帧计数开始动、缓冲出现非零 -> 就是它。之后把该顺序固化进
        vdma_capture.py 的 start()。
"""

import time

GPIO3 = 0x43A30000
GPIO4 = 0x43A40000
GPIO5 = 0x43A50000
GPIO_RANGE = 0x10000

BIT = '/boot/ov5640_m1a_wrapper.bit'
CTRL_VID_RST_BIT = 3

VSIZE = 720
HSIZE = 2048
BUF_H = 800          # 缓冲开高一点，免得写越界污染相邻缓冲


def main():
    import numpy as np
    from pynq import Bitstream, MMIO
    import vdma_capture as vc

    Bitstream(BIT).download()
    print('[加载] bitstream 已直下')
    time.sleep(2)

    mm3 = MMIO(GPIO3, GPIO_RANGE)
    mm4 = MMIO(GPIO4, GPIO_RANGE)
    mm5 = MMIO(GPIO5, GPIO_RANGE)

    cap = vc.S2mmCapture(1024, BUF_H)

    def zero_bufs():
        for b in cap.bufs:
            np.asarray(b, dtype=np.uint8)[:] = 0
            if hasattr(b, 'flush'):
                b.flush()

    def buf_max_std():
        best = 0.0
        for b in cap.bufs:
            if hasattr(b, 'invalidate'):
                b.invalidate()
            s = float(np.asarray(b, dtype=np.uint8).std())
            if s > best:
                best = s
        return best

    def write_addr():
        for i, b in enumerate(cap.bufs):
            cap.mmio.write(vc.S2MM_START + 4 * i, int(b.physical_address))

    # ------------------------------------------------------------------
    print('=' * 74)
    print('[步骤 0] 起手：启动视频域（只做一次，后面对照组不再动它）')
    print('=' * 74)
    cap.reset()
    cap.start()
    c0 = mm3.read(0x00, 4)
    mm3.write(0x00, c0 | (1 << CTRL_VID_RST_BIT))
    time.sleep(0.3)
    mm3.write(0x00, c0 & ~(1 << CTRL_VID_RST_BIT))
    print('    视频域已重启，源开始推数据')
    cap.stop()

    # ------------------------------------------------------------------
    def run_variant(name, order):
        """order: 'param_first' 或 'start_first'；writes: 要写哪些寄存器"""
        cap.stop()
        cap.reset()
        zero_bufs()

        if order == 'param_first':
            cap.mmio.write(vc.S2MM_VSIZE, VSIZE)
            cap.mmio.write(vc.S2MM_HSIZE, HSIZE)
            cap.mmio.write(vc.S2MM_STRIDE, HSIZE)
            write_addr()
            cap.start()
        else:
            write_addr()                       # 地址先写好（此时通道还停着）
            cap.start()                        # ★ 先跑起来（run_stop=1）
            cap.mmio.write(vc.S2MM_VSIZE, VSIZE)     # ★ 这一拍产生 prmtr_updt 脉冲
            cap.mmio.write(vc.S2MM_HSIZE, HSIZE)
            cap.mmio.write(vc.S2MM_STRIDE, HSIZE)

        time.sleep(0.3)
        cap.clear_errors()

        a_l = mm5.read(0x00, 4)
        a_u = mm4.read(0x08, 4)
        time.sleep(2.0)
        b_l = mm5.read(0x00, 4)
        b_u = mm4.read(0x08, 4)
        sr = cap.mmio.read(vc.S2MM_DMASR)
        fc = (sr >> vc.SR_FRMCNT_SHIFT) & 0xFF
        std = buf_max_std()
        d_tl = (b_l - a_l) & 0xFFFFFFFF
        d_tu = (b_u - a_u) & 0xFFFFFFFF
        rows = (d_tl / d_tu) if d_tu else 0.0
        ok = (fc != 255) or (std > 0.5)
        print(f'    {name:<34} 帧计数={fc:<4} 缓冲std={std:<8.1f} '
              f'tlast/2s={d_tl:<7} tuser/2s={d_tu:<4} 行/帧={rows:.1f} '
              f'{"  <<< 成功!" if ok else ""}')
        return ok, fc, std

    print()
    print('=' * 74)
    print('[步骤 1] 四组对照')
    print('=' * 74)
    print('    VSIZE=720  HSIZE=STRIDE=2048  每组观察 2.0s')
    print()

    results = []
    results.append(run_variant('变体1 写参数 -> start (现行)', 'param_first'))
    results.append(run_variant('变体2 start -> 写 VSIZE', 'start_first'))
    results.append(run_variant('变体3 start -> 写 V/H/S', 'start_first'))

    # 变体 4：全套照官方驱动来 —— 先 stop/reset，写地址，start，再把
    # 视频参数整组重写一遍（等价于 XAxiVdma_StartWriteFrame）
    cap.stop()
    cap.reset()
    zero_bufs()
    write_addr()
    cap.start()
    cap.mmio.write(vc.S2MM_VSIZE, VSIZE)
    cap.mmio.write(vc.S2MM_HSIZE, HSIZE)
    cap.mmio.write(vc.S2MM_STRIDE, HSIZE)
    write_addr()
    time.sleep(0.3)
    cap.clear_errors()
    time.sleep(2.0)
    sr = cap.mmio.read(vc.S2MM_DMASR)
    fc = (sr >> vc.SR_FRMCNT_SHIFT) & 0xFF
    std = buf_max_std()
    ok4 = (fc != 255) or (std > 0.5)
    print(f'    {"变体4 start -> 写全套(含地址)":<34} 帧计数={fc:<4} 缓冲std={std:<8.1f} '
          f'{"  <<< 成功!" if ok4 else ""}')
    results.append(ok4)

    # ------------------------------------------------------------------
    print()
    print('=' * 74)
    print('[步骤 2] 结论')
    print('=' * 74)
    if any(results):
        which = ['变体1(现行/写参数在前)', '变体2(start 后写 VSIZE)',
                 '变体3(start 后写 V/H/S)', '变体4(start 后写全套)'][results.index(True)]
        print(f'    ★★ {which} 出帧了！')
        print()
        print('    与 rfs.vhd 20507-20518 的推断完全一致：')
        print('      prmtr_updt_complete 那一拍脉冲必须在 run_stop=1 时打出来，')
        print('      否则 chnl_ready 永远拉不起来，数据全被 dummy tready 丢掉。')
        print()
        print('    下一步：把这个顺序固化进 vdma_capture.py 的 start()，')
        print('            然后跑完整驱动自检 + 10 帧连拍存 PNG，M1a 就通了。')
    else:
        print('    四组都不出帧。那说明 prmtr_updt_complete 这条推断也不对，')
        print('    需要直接观测 0x30(DMACR) 写完之后的内部状态 —— 下一步考虑')
        print('    在 BD 里把 vdma 的 fsync 改成外部端口，用 DVP vsync 直接驱动。')

    print()
    print(f'    最后 DMASR=0x{cap.mmio.read(vc.S2MM_DMASR):08x}'
          f'  DMACR=0x{cap.mmio.read(vc.S2MM_DMACR):08x}')
    cap.stop()


if __name__ == '__main__':
    main()
