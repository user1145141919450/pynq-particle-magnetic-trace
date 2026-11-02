# -*- coding: utf-8 -*-
"""vsize_sweep.py — 用两个判别器把"零帧完成"劈成两半

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/vsize_sweep.py

背景（2026-09-13 第八轮）：

  第七轮修好复位时序后，源侧数据**完全健康**：tvalid 44.5M/s、tuser 30/s、
  tlast 21700/s（比值 44.5M/21700 = 恰好 2048 字节/行 = 1024 像素 x 2 字节，
  与 ov5640_cfg.v 里 DVPHO=1024 吻合），连跑 5s 零溢出。
  但 VDMA 帧计数死死停在 255（= 写完 IRQThreshold 后的初值，即**零帧完成**），
  DMASR=0x00ff4810。按 rfs.vhd 的位定义（10700-10715 行）解码：
      bit4  DMAInternalErr          bit11 FSizeMoreOrSOFLate
      bit14 Err_Irq                 bit3  Err=0、Halted=0、Idle=0
  —— 通道**没被错误停住**，只是干等着。

  去 axi_vdma_v6_3_rfs.vhd 翻 S2MM 的垂直计数器（72990-73029 行
  NO_DWIDTH_VERT_COUNTER），拿到这条确定的链：

      elsif(s2mm_fsync_out_i = '1')then            -- 收到 SOF
          vsize_counter <= s2mm_crnt_vsize_d2;     -- 载入 VSIZE
          chnl_ready    <= '1';                    -- ★ 唯一能拉起来的地方
      elsif(decr_vcount = '1' and vsize_counter = 1)then
          chnl_ready    <= '0';                    -- 收满 VSIZE 个 tlast → 收工

  而送给源的 tready 是（72304 行）：
      tready = (真tready and chnl_ready) or dummy_tready
  也就是 chnl_ready=0 时数据被"收下然后丢掉"，一帧都完不成。

  **所以"零帧完成"只剩两种可能，而且痕迹完全不同：**

    假说 A「SOF 那一路没打通」：chnl_ready 恒 0，数据全被丢掉
                              -> DDR 缓冲里**全是 0**
    假说 B「SOF 通了、行数对不上」：chnl_ready=1，数据真写进 DDR
                              -> 缓冲里**有真实图像**（标准差很大）

  本脚本就干两件事，各用一个判别器切开：

    判别器一（步骤 2）：扫 VSIZE 720/721/.../730，每档读 4 个缓冲的均值/标准差
                        —— 只要哪一档缓冲非零，就是假说 B，且能顺带定出行数
    判别器二（步骤 3）：VSIZE 写 **1**。若 chnl_ready 正常，每收 1 个 tlast
                        就该完成一帧，帧计数应以 ~21700/s 飞涨。
                        **若帧计数仍纹丝不动 -> 与行数无关，坐实假说 A。**

  这两个判别器都是**纯 PS 侧**的，不用重烧 bit。

判读表：
  步骤 2 某档缓冲非零且帧计数动了 -> 找到真实行数，改驱动即可，M1a 通
  步骤 3 VSIZE=1 帧计数飞涨      -> 行数是变量，回到步骤 2 的候选里挑
  两个判别器都毫无反应           -> 假说 A 成立：SOF 没送进 VDMA，
                                    查 axis_probe 的 tuser 合成（那边有个
                                    s_axis_tuser | sof_armed 的相或，可能
                                    把每帧的 SOF 变成两个脉冲）
"""

import time

GPIO3 = 0x43A30000      # +0x00 ctrl(输出)
GPIO4 = 0x43A40000      # +0x00 w0 tvalid 拍数   +0x08 w1 tuser 沿
GPIO5 = 0x43A50000      # +0x00 w2 tlast 沿      +0x08 w3 状态字
GPIO_RANGE = 0x10000

BIT = '/boot/ov5640_m1a_wrapper.bit'
CTRL_VID_RST_BIT = 3

# 行尾字节数：探针实测 tvalid/tlast = 恰 2048，与 DVPHO=1024 x 2 字节一致
HSIZE = 2048

# 缓冲开 800 行 —— 比所有候选档都高，免得某一档写越界污染下一档的判断
BUF_H = 800

CANDIDATES = [720, 721, 722, 723, 724, 726, 728, 730]


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

    # ------------------------------------------------------------------
    # 起手：先清掉所有缓冲，这样后面"非零"一定是 VDMA 写进去的
    # ------------------------------------------------------------------
    for b in cap.bufs:
        a = np.asarray(b, dtype=np.uint8)
        a[:] = 0
        if hasattr(b, 'flush'):
            b.flush()

    print('=' * 72)
    print('[步骤 1] 让 sink 先就绪：启动 VDMA，再用 ctrl[3] 重启视频域')
    print('=' * 72)
    cap.start()
    c0 = mm3.read(0x00, 4)
    mm3.write(0x00, c0 | (1 << CTRL_VID_RST_BIT))
    time.sleep(0.3)
    mm3.write(0x00, c0 & ~(1 << CTRL_VID_RST_BIT))
    print('    视频域已重启（次序沿用第七轮验证过的"sink 先就绪、源后开"）')

    def snap_probe():
        return (mm4.read(0x00, 4), mm4.read(0x08, 4), mm5.read(0x00, 4))

    def measure(T=1.2):
        """返回 (dtlast, dtuser, dmasr, 四缓冲最大标准差, 该缓冲均值)"""
        a = snap_probe()
        time.sleep(T)
        b = snap_probe()
        d_tlast = (b[2] - a[2]) & 0xFFFFFFFF
        d_tuser = (b[1] - a[1]) & 0xFFFFFFFF
        sr = cap.mmio.read(vc.S2MM_DMASR)
        best_std, best_mean = 0.0, 0.0
        for bf in cap.bufs:
            if hasattr(bf, 'invalidate'):
                bf.invalidate()          # PL 直写 DDR，统计前必须失效缓存
            arr = np.asarray(bf, dtype=np.uint8)
            s = float(arr.std())
            if s > best_std:
                best_std, best_mean = s, float(arr.mean())
        return d_tlast, d_tuser, sr, best_std, best_mean

    def try_vsize(v, T=1.2):
        """把通道整到 VSIZE=v 重来一遍，返回 measure() 的结果。"""
        cap.stop()
        cap.reset()
        cap.mmio.write(vc.S2MM_VSIZE, v)
        cap.mmio.write(vc.S2MM_HSIZE, HSIZE)
        cap.mmio.write(vc.S2MM_STRIDE, HSIZE)
        for i, bf in enumerate(cap.bufs):
            cap.mmio.write(vc.S2MM_START + 4 * i, int(bf.physical_address))
        cap.start()
        time.sleep(0.25)
        cap.clear_errors()
        return measure(T)

    # ------------------------------------------------------------------
    print()
    print('=' * 72)
    print('[判别器一] 扫 VSIZE：缓冲里有非零数据 → 假说 B（数据真写进去了）')
    print('=' * 72)
    print(f'    HSIZE/STRIDE 固定 = {HSIZE}   缓冲 = 4 x 1024 x {BUF_H}')
    print()
    print('    VSIZE   帧计数  tlast/1.2s  tuser/1.2s  行/帧  缓冲std  缓冲mean  DMASR')
    print('    ' + '-' * 78)

    results = []
    for v in CANDIDATES:
        d_tl, d_tu, sr, std, mean = try_vsize(v)
        fc = (sr >> vc.SR_FRMCNT_SHIFT) & 0xFF
        rows = (d_tl / d_tu) if d_tu else 0.0
        results.append((v, fc, d_tl, d_tu, rows, std, mean, sr))
        tag = ''
        if std > 0.5:
            tag += '  <缓冲非零!'
        if fc != 255:
            tag += '  <帧计数动了!'
        print(f'    {v:<7}  {fc:<6}  {d_tl:<10}  {d_tu:<10}  '
              f'{rows:<6.1f}  {std:<8.1f}  {mean:<9.1f}  0x{sr:08x}{tag}')

    # ------------------------------------------------------------------
    print()
    print('=' * 72)
    print('[判别器二] ★ VSIZE=1：若 chnl_ready 正常，帧计数应以 ~21700/s 飞涨')
    print('=' * 72)
    print('    这一档的意义不在"能不能用"，而在**把两个假说彻底分开**：')
    print('    VSIZE=1 时每收到 1 个 tlast 就满一帧 —— 只要 SOF 那一路是通的，')
    print('    帧计数必然飞涨；若它纹丝不动，那就跟行数毫无关系。')
    print()
    d_tl, d_tu, sr, std, mean = try_vsize(1, T=2.0)
    fc = (sr >> vc.SR_FRMCNT_SHIFT) & 0xFF
    print(f'    VSIZE=1   帧计数={fc}  tlast/2.0s={d_tl}  tuser/2.0s={d_tu}  '
          f'缓冲std={std:.1f}  DMASR=0x{sr:08x}')
    disc_worked = (fc != 255)

    # ------------------------------------------------------------------
    print()
    print('=' * 72)
    print('[结论]')
    print('=' * 72)

    hit = [r for r in results if r[5] > 0.5]        # 缓冲非零
    moved = [r for r in results if r[1] != 255]     # 帧计数动了

    if moved:
        v = moved[0][0]
        print(f'    ★★ 帧计数在 VSIZE={v} 动了（缓冲std={moved[0][5]:.1f}）。')
        print(f'    实测 {moved[0][4]:.1f} 行/帧。把 vdma_capture 的默认高度改成 {v} 即可。')
    elif disc_worked:
        print('    ★★ VSIZE=1 就让帧计数飞涨了 —— 行数确实是变量。')
        print('    但上面 720~730 全档都不动，说明真实行数不在这区间，')
        print('    得换个思路量它（下一步：读 DMASR 的 fsize 相关位反推）。')
    else:
        if hit:
            print('    ★ 缓冲里有**非零数据**（假说 B 成立）—— datamover 在写 DDR，')
            print('      只是"帧完成"永远不认账。非零的档位：')
            for r in hit:
                print(f'        VSIZE={r[0]}  std={r[5]:.1f}  mean={r[6]:.1f}')
        else:
            print('    ★★ 两个判别器都毫无反应：缓冲全零、帧计数恒 255、')
            print('      连 VSIZE=1 都不动 —— **假说 A 成立**。')
            print()
            print('    即：S2MM 的 chnl_ready 从来没被拉开过，数据全被 dummy tready')
            print('    收下丢掉了。按 rfs.vhd 72990-73029，chnl_ready 只能由')
            print('    s2mm_fsync_out_i 拉起，而它派生于 tuser —— **SOF 没送进 VDMA**。')
            print()
            print('    下一个要查的：rtl/debug/axis_probe.v 第 131 行')
            print('        assign m_axis_tuser = s_axis_tuser | sof_armed;')
            print('    探针只统计了 s_axis_tuser（IP 来的），**没统计送出去的那一路**。')
            print('    若 sof_armed(vsync 派生) 与 IP 的 tuser 差 1~2 拍，VDMA 每帧')
            print('    就收到**两个 SOF 脉冲** —— 正好对应 FSizeMoreOrSOFLate。')

    print()
    print(f'    最后 DMASR=0x{cap.mmio.read(vc.S2MM_DMASR):08x}'
          f'  DMACR=0x{cap.mmio.read(vc.S2MM_DMACR):08x}'
          f'  ctrl=0x{mm3.read(0x00, 4):08x}')

    # 收尾：把通道停掉，免得下次上电时它还挂在半截状态
    cap.stop()


if __name__ == '__main__':
    main()
