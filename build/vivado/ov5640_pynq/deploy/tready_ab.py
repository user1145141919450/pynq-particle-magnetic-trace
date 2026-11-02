# -*- coding: utf-8 -*-
"""tready_ab.py — 把"流是碎的"归因到 VDMA 的 tready（或洗清它）

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/tready_ab.py

背景（2026-09-13 第十轮）：

  22:27 那次 line_count.py 的自检读出：
      tvalid 16.64 M/s   tuser 30.00 /s   tlast 8818.7 /s
      每行字节数 = tvalid/tlast = 1887.3   （应为 2048）
      每帧行数   = tlast /tuser =  294.0   （应为 720）
      状态字 0x697d208f   见过溢出=1   下溢=0 复位中=0

  传感器本身是好的（vsync 与 tuser 都恰好 30.00/s —— 这已经是第 4 次
  量到 30.00，非常稳）。问题在 **IP 的输出流被削掉了**：
  行**变短**（1887 < 2048）而且行数**少了**（294 < 720），
  两件事同时发生 —— 这是"采样在行中间被丢弃"的指纹，不是"帧结构配错"。

  与 21:00 那次（同一份 bit）相比，唯一的变量是：
      21:00  VDMA 卡在 chnl_ready=0，tready 恒为 dummy=1，**从不回压源**
      现在   VDMA 真的在收帧，tready 随帧边界起落
  => 头号嫌疑：**VDMA 的 tready 在抖，把 v_vid_in_axi4s 的异步 FIFO 顶满，
     IP 只好丢采样**（丢在行中间 -> 行变短；丢掉整行 -> 行数少）。

  RTL 依据（axi_vdma_v6_3_rfs.vhd）：
      72304  s_axis_s2mm_tready <= (真tready and chnl_ready) or dummy_tready
      72377  dummy_tready(本配置 C_USE_S2MM_FSYNC=2) <= d_tready_sof_late_cmb
                                                       or d_tready_before_fsync_cmb
      ==> dummy_tready **不是恒 1**，它在帧边界会掉下去。

  但"嫌疑"不等于"定罪"。本脚本做一个**只改一个变量**的对照：

      A 相：VDMA 停着（RS=0）。此时 dmasr_halted=1
            -> d_tready_before_fsync_clr_flag1=1        (72468-72469)
            -> d_tready_before_fsync_cmb=1              (72443)
            -> dummy_tready=1 -> **tready 恒为 1**
            => 源完全不受回压，正好是 21:00 那个状态
      B 相：VDMA 正常跑（现行驱动顺序）=> tready 随帧起落

  走 A -> B -> A 三趟，各量 2 秒。**可逆就是因果，不可逆就只是相关。**

判读：
  A 相 720 行/帧、2048 字节/行，B 相掉到 294  -> ★★ 定罪：tready 抖动
                                                -> 改 PL：① 去掉双 SOF
                                                   ② 加大 IP 的 C_FIFO_DEPTH
  A 相**也**是 294 行                        -> 与 VDMA 无关，是源侧的问题
                                                -> 查 IP 的 C_FIFO_DEPTH / 格式，
                                                   或拔电源排除传感器偶发
  两相都是 720                               -> 之前的读数是偶发，直接进 M1b

★ 这个脚本只读寄存器、只动 DMACR 的 RS 位，不写视频参数，
  所以不会引入第九轮那种"参数写入顺序"的额外变量。
"""

import time

GPIO3 = 0x43A30000      # +0x00 ctrl(输出)
GPIO4 = 0x43A40000      # +0x00 w0 tvalid 拍数   +0x08 w1 tuser 沿
GPIO5 = 0x43A50000      # +0x00 w2 tlast 沿      +0x08 w3 状态字
GPIO_RANGE = 0x10000

BIT = '/boot/ov5640_m1a_wrapper.bit'
CTRL_VID_RST_BIT = 3

T = 2.0                 # 每相观察时长


def main():
    from pynq import Bitstream, MMIO
    import vdma_capture as vc

    Bitstream(BIT).download()
    print('[加载] bitstream 已直下')
    time.sleep(2)

    mm3 = MMIO(GPIO3, GPIO_RANGE)
    mm4 = MMIO(GPIO4, GPIO_RANGE)
    mm5 = MMIO(GPIO5, GPIO_RANGE)

    def w0():
        return mm4.read(0x00, 4)

    def w1():
        return mm4.read(0x08, 4)

    def w2():
        return mm5.read(0x00, 4)

    def w3():
        return mm5.read(0x08, 4)

    cap = vc.S2mmCapture(1024, 720)
    cap.stop()

    def restart_src():
        """脉冲 ctrl[3] 重启视频域，让每个相位都从同一起点开始。"""
        c0 = mm3.read(0x00, 4)
        mm3.write(0x00, c0 | (1 << CTRL_VID_RST_BIT))
        time.sleep(0.3)
        mm3.write(0x00, c0 & ~(1 << CTRL_VID_RST_BIT))
        time.sleep(0.3)

    def measure(tag):
        a0, a1, a2 = w0(), w1(), w2()
        time.sleep(T)
        b0, b1, b2 = w0(), w1(), w2()
        sw = w3()
        r0 = ((b0 - a0) & 0xFFFFFFFF) / T
        r1 = ((b1 - a1) & 0xFFFFFFFF) / T
        r2 = ((b2 - a2) & 0xFFFFFFFF) / T
        bpl = (r0 / r2) if r2 > 0 else 0.0      # 每行字节数，应 2048
        rpf = (r2 / r1) if r1 > 0 else 0.0      # 每帧行数，  应 720
        sr = cap.mmio.read(vc.S2MM_DMASR)
        print(f'  {tag}')
        print(f'    tvalid {r0 / 1e6:7.2f} M/s    tuser {r1:6.2f} /s    '
              f'tlast {r2:8.1f} /s')
        print(f'    ★ 每行字节数 {bpl:8.1f} (应 2048)      '
              f'★ 每帧行数 {rpf:7.1f} (应 720)')
        print(f'    w3=0x{sw:08x}  溢出={sw >> 10 & 1} 下溢={sw >> 11 & 1} '
              f'复位中={sw >> 12 & 1} 见过溢出={sw >> 13 & 1} '
              f'  live: tready={sw >> 7 & 1} tvalid={sw >> 4 & 1}')
        print(f'    DMASR=0x{sr:08x}  halted={bool(sr & 1)}')
        return (r0, r1, r2, bpl, rpf)

    # ------------------------------------------------------------------
    print()
    print('=' * 74)
    print('[A1 相] VDMA 停着（RS=0）—— tready 恒 1，源完全不受回压')
    print('=' * 74)
    cap.stop()
    restart_src()
    A1 = measure('A1  (VDMA halted)')

    # ------------------------------------------------------------------
    print()
    print('=' * 74)
    print('[B 相] VDMA 正常跑 —— tready 随帧起落（现行驱动顺序）')
    print('=' * 74)
    cap.reset()
    cap.start()                 # start() 内部已按第九轮的顺序重写参数
    restart_src()
    B1 = measure('B1  (VDMA running)')

    # ------------------------------------------------------------------
    print()
    print('=' * 74)
    print('[A2 相] 再把 VDMA 停掉 —— 看能不能变回来（可逆 = 因果）')
    print('=' * 74)
    cap.stop()
    restart_src()
    A2 = measure('A2  (VDMA halted again)')

    # ------------------------------------------------------------------
    print()
    print('=' * 74)
    print('[汇总]')
    print('=' * 74)
    print('    相位                  tvalid(M/s)   tuser(/s)   tlast(/s)   '
          '字节/行   行/帧')
    print('    ' + '-' * 78)
    for name, r in (('A1 VDMA 停', A1), ('B  VDMA 跑', B1), ('A2 VDMA 停', A2)):
        print(f'    {name:<18}  {r[0] / 1e6:9.2f}   {r[1]:9.2f}   {r[2]:9.1f}   '
              f'{r[3]:8.1f}  {r[4]:7.1f}')

    # ------------------------------------------------------------------
    print()
    print('=' * 74)
    print('[结论]')
    print('=' * 74)

    a_bpl = (A1[3] + A2[3]) / 2.0
    a_rpf = (A1[4] + A2[4]) / 2.0
    healthy_a = (abs(a_rpf - 720) < 6) and (abs(a_bpl - 2048) < 64)
    bad_b = (abs(B1[4] - 720) > 6) or (abs(B1[3] - 2048) > 64)

    if healthy_a and bad_b:
        print('    ★★ 定罪：**VDMA 的 tready 就是元凶**。')
        print(f'       VDMA 停着时 {a_rpf:.1f} 行/帧、{a_bpl:.0f} 字节/行 —— 都对得上；')
        print(f'       VDMA 一跑就掉到 {B1[4]:.1f} 行/帧、{B1[3]:.0f} 字节/行。')
        print('       两趟 A 相都健康 => **可逆**，这就不是相关而是因果。')
        print()
        print('       => 改 PL，一次把两条都做掉（只需重烧一次 bit）：')
        print('          1) 去掉 rtl/debug/axis_probe.v:131 的 `| sof_armed`。')
        print('             IP 自己每帧就给一个 tuser（探针量到 30/s），')
        print('             sof_armed 是第七轮"以为源不给 SOF"时加的保险，')
        print('             现在它是纯粹的风险：两个 SOF 差几拍，VDMA 每帧')
        print('             重触发一次 fsync，tready 因此抖。')
        print('             同时把 m_axis_tuser 也接一路计数器 —— 那是')
        print('             一直以来的盲区（探针只数了 IP 侧那一路）。')
        print('          2) 把 v_vid_in_axi4s 的 C_FIFO_DEPTH 加到最大，')
        print('             让 IP 的异步 FIFO 能吸收 VDMA 的帧边界抖动。')
    elif not healthy_a:
        print('    !! A 相（VDMA 停着、tready 恒 1）**也不健康** —— 与 VDMA 无关。')
        print(f'       A 相就有 {a_rpf:.1f} 行/帧、{a_bpl:.0f} 字节/行。')
        print('       这是源侧自己的问题，往下查：')
        print('         - v_vid_in_axi4s 的 C_FIFO_DEPTH / 格式参数配置')
        print('         - 传感器这次上电起的模式（先拔电源再跑一次排除偶发）')
    elif healthy_a and not bad_b:
        print('    A/B 两相都健康 —— 之前那些读数是偶发，直接进 M1b。')
    else:
        print('    A 相健康、B 相也勉强 —— 看上面的数字定夺。')

    print()
    cap.stop()


if __name__ == '__main__':
    main()
