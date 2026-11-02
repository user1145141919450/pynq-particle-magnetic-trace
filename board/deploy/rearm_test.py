# -*- coding: utf-8 -*-
"""rearm_test.py — 判决性实验：VDMA 先就绪，再重启视频域

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/rearm_test.py

为什么有这么个脚本（2026-09-13 第七轮上板读数）：

  第七轮把复位时序修对之后，源**真的活了**：上电后推了约 7 帧
  （tvalid=8594469 拍、tuser=7 个 SOF、tlast=4197 个行尾），
  而且状态字 bit13「见过溢出」= 1 —— 坐实了
  `VID_RESET = vid_reset | fifo_overflow_from_coupler` 这条死锁链。

  但它**还是死了**。时间线说明了原因：

      20:20:32  bit 加载 → 传感器配置 → 视频域放复位 → 源开始推 7 帧 → 溢出 → 死
      20:20:34  读基线（此时已死透，2s 只抖了 60 拍）
      20:20:47  vdma_capture._selfcheck() 才启动 VDMA   ← 晚了 15 秒

  **VDMA 一直没开机 → IP 内部 FIFO 没人排空 → 必然溢出 → 视频域被自己的
  溢出按死。** 那 7 帧数据是产出来了、但没人接。

  所以正确的上电次序应该是「**sink 先就绪，源后开**」。本脚本就按这个次序来：
    ① 先 start() 把 VDMA 拉起来（此时它等着帧同步）
    ② 再用 ctrl[3] 把视频域复位一次、放出来（源从头开始干净地推）
    ③ 200ms 粒度细看它到底是"一放出来就死"还是"跑一会儿才死"

  这个次序是**纯 PS 侧**的 —— 不需要重烧 bit。若成功，M1a 当场跑通。

  判读：
    出帧    -> 成功，次序就是根因，改回 vdma_capture 的启动流程即可
    仍不出帧且立刻见溢出 -> FIFO 在"第一个 tuser 到达前"就满了。
                            说明 tuser(FSYNCSEL=2) 这条帧同步路太慢，
                            得给 VDMA 喂一路独立的 vsync 派生 fsync（要重建）
"""

import time

GPIO3 = 0x43A30000      # +0x00 ctrl(输出)
GPIO4 = 0x43A40000      # +0x00 w0 tvalid 拍数   +0x08 w1 tuser 沿
GPIO5 = 0x43A50000      # +0x00 w2 tlast 沿      +0x08 w3 状态字
GPIO_RANGE = 0x10000

BIT = '/boot/ov5640_m1a_wrapper.bit'
CTRL_VID_RST_BIT = 3

W3_LATCH = ((0, 'tvalid'), (1, 'tuser'), (2, 'tlast'), (3, 'tready'),
            (13, '溢出'), (14, '下溢'))
W3_LIVE = ((4, 'tvalid'), (5, 'tlast'), (6, 'tuser'), (7, 'tready'),
           (10, '溢出'), (11, '下溢'), (12, '复位中'))


def show_w3(w3):
    lat = [n for b, n in W3_LATCH if w3 & (1 << b)]
    live = ['%s=%d' % (n, (w3 >> b) & 1) for b, n in W3_LIVE]
    print(f'    状态字 0x{w3:08x}: 见过[{",".join(lat) if lat else "无"}]  '
          f'电平[{" ".join(live)}]  vsync={w3 >> 16}')


def main():
    from pynq import Bitstream, MMIO
    import vdma_capture as vc

    Bitstream(BIT).download()
    print('[加载] bitstream 已直下')
    time.sleep(2)

    mm3 = MMIO(GPIO3, GPIO_RANGE)
    mm4 = MMIO(GPIO4, GPIO_RANGE)
    mm5 = MMIO(GPIO5, GPIO_RANGE)

    def snap():
        return (mm4.read(0x00, 4), mm4.read(0x08, 4),
                mm5.read(0x00, 4), mm5.read(0x08, 4))

    print('=' * 68)
    print('[步骤 1] 上电后自然状态（此时按第七轮的结论应当已经死透）')
    print('=' * 68)
    a = snap()
    print(f'    tvalid={a[0]}  tuser={a[1]}  tlast={a[2]}  ctrl=0x{mm3.read(0x00, 4):08x}')
    show_w3(a[3])
    base_tv, base_tu, base_tl = a[0], a[1], a[2]

    # ------------------------------------------------------------------
    print('=' * 68)
    print('[步骤 2] ★ 先让 sink 就绪：启动 VDMA S2MM')
    print('=' * 68)
    cap = vc.S2mmCapture(1024, 720)
    cap.start()
    st = cap.status()
    print(f'    已启动 DMACR=0x{cap.mmio.read(vc.S2MM_DMACR):08x}  '
          f'DMASR=0x{st[0]:08x} halted={st[2]} err={st[3]} 帧计数={st[1]}')

    # ------------------------------------------------------------------
    print('=' * 68)
    print('[步骤 3] ★ 再重启视频域：ctrl[3] 拉高 0.3s 后放开')
    print('=' * 68)
    c0 = mm3.read(0x00, 4)
    mm3.write(0x00, c0 | (1 << CTRL_VID_RST_BIT))
    print(f'    ctrl: 0x{c0:08x} -> 0x{c0 | (1 << CTRL_VID_RST_BIT):08x}（复位中）')
    time.sleep(0.3)
    w3r = mm5.read(0x08, 4)
    print(f'    复位期间状态字 0x{w3r:08x}  live 复位中={(w3r >> 12) & 1}（应为 1）')
    mm3.write(0x00, c0 & ~(1 << CTRL_VID_RST_BIT))
    print(f'    ctrl: -> 0x{c0 & ~(1 << CTRL_VID_RST_BIT):08x}（撤复位，源重新开跑）')

    # ------------------------------------------------------------------
    print('=' * 68)
    print('[步骤 4] 200ms 粒度细看：它是一放出来就死，还是跑一会儿才死？')
    print('=' * 68)
    t0 = time.time()
    prev = snap()
    print(f'  t=+0.0s  tvalid={prev[0]} tuser={prev[1]} tlast={prev[2]}')
    show_w3(prev[3])
    for i in range(1, 26):                      # 25 x 200ms = 5s
        time.sleep(0.2)
        cur = snap()
        d_tv = (cur[0] - prev[0]) & 0xFFFFFFFF
        d_tu = (cur[1] - prev[1]) & 0xFFFFFFFF
        d_tl = (cur[2] - prev[2]) & 0xFFFFFFFF
        fr = (cap.mmio.read(vc.S2MM_DMASR) >> vc.SR_FRMCNT_SHIFT) & 0xFF
        print(f'  t=+{time.time() - t0:4.1f}s  dtvalid={d_tv:<9} dtuser={d_tu:<4} '
              f'dtlast={d_tl:<7} 帧计数={fr:<4} w3=0x{cur[3]:08x}')
        prev = cur
        if d_tv == 0 and d_tl == 0 and i > 3:
            print('    ^^ 已停（连续无增量），停止细看')
            break

    # ------------------------------------------------------------------
    print('=' * 68)
    print('[步骤 5] 结论')
    print('=' * 68)
    fin = cap.mmio.read(vc.S2MM_DMASR)
    fr = (fin >> vc.SR_FRMCNT_SHIFT) & 0xFF
    print(f'    VDMA DMASR=0x{fin:08x}  帧计数={fr}（255=零帧完成、与启动阈值相同）')
    print(f'    全程增量: tvalid {((prev[0] - base_tv) & 0xFFFFFFFF)}  '
          f'tuser {((prev[1] - base_tu) & 0xFFFFFFFF)}  '
          f'tlast {((prev[2] - base_tl) & 0xFFFFFFFF)}')
    if fr != 255:
        print('    ★★ VDMA 出帧了！「sink 先就绪、源后开」这个次序就是根因。')
        print('    下一步：把这个次序固化进 vdma_capture 的启动流程。')
    else:
        print('    仍未出帧。看上面的细节行：')
        print('      dtvalid 一直在涨但 dtlast=0  -> 源在推但没有行尾，流结构不对')
        print('      dtvalid 涨几行后归零且见过溢出 -> FIFO 在第一个 tuser 到达前就满了，')
        print('                                       tuser(FSYNCSEL=2) 这条路太慢，')
        print('                                       需要给 VDMA 喂独立 vsync 派生的 fsync')
        print('      dtvalid 一开始就是 0          -> 视频域根本没重开')

    vc._print_errors(cap)


if __name__ == '__main__':
    main()
