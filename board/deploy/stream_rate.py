# -*- coding: utf-8 -*-
"""stream_rate.py — 一次看清"流到底停在哪一段"

背景：vision_core 上板验收时反复出现"热身帧能来、随后 3s 内再无新帧"的卡死。
光看 VDMA 的帧计数只能知道"VDMA 收不到帧"，看不出是**源侧没数据**还是
**数据在但没进 VDMA**。本脚本按本工程的老规矩（看速率和比值，不看单点值）
把链路上每一段的速率同时打出来：

    OV5640 ─ 桥 ─ vision_core ─ axis_probe ─ VDMA
    ↑probe0~2   ↑probe6        ↑probe4/5      ↑DMASR帧计数
    （视频域）  （桥内影子）    （AXI域拍数）   （最终落 DDR）

读数（全部 mmap，不分配内存、不下载 bit —— 所以随时能跑）：
  probe4 +0x00 w0 = 源 tvalid 拍数（健康 ~44.4M/s，1 拍 = 1 字节）
  probe4 +0x08 w1 = 源 tuser 边沿（帧首，健康 ~30/s）
  probe5 +0x00 w2 = 源 tlast 边沿（行尾，健康 ~21660/s = 30 帧 x 720 行，实测 21652/s）
  probe5 +0x08 w3 = 状态字（见 axis_probe.v 文件头；bit4..7 是 live 电平）
  probe6 +0x00    = 桥内影子（dbg_sel 选），健康时 c_push == c_href
  0x43B1_0008     = vision_core 的 frame_cnt（每完成一帧 +1）
  0x4300_0014     = VDMA DMASR（bit[23:16] 帧计数、bit0 halted、bit4..11 错误）

判读表（谁停了就是谁的问题）：
  tvalid 停 → 源侧（传感器/桥）死了：查 dvp_probe.py，或 ctrl[4] 救
  tvalid 活但 tlast 停 → 桥只推不发 EOL：查桥影子 c_push/c_rd/c_ovf
  tvalid/tlast 都活但 VDMA 帧计数不动 → 数据到了 VDMA 门口没进去：
    看 live tready（w3 bit7）—— 常 0 就是 VDMA 没在收（DMACR 被清了 / halted）
  全都活 → 那就是好的，帧率问题另有原因（比如传感器 AE 把曝光拉长）

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/stream_rate.py
    ... --gap 0.5     采样间隔秒（默认 2.0）
    ... --n 5         采样次数（默认 4）
"""

import sys
import time

sys.path.insert(0, '/boot')

from pynq import MMIO

P4, P5, P6 = 0x43A40000, 0x43A50000, 0x43A60000
VCTRL = 0x43B10000
VSTAT = 0x43B20000
VDMA = 0x43000000
DMASR = 0x34      # ★ 真·S2MM_DMASR。本脚本第一版错写成 0x14（那是保留寄存器），
DMACR = 0x30      #   读出 0x00f74000 还当成"帧计数停在 247"白追了一轮。
                  #   权威来源是 vdma_capture.py 的常量（那份照 rfs.vhd 核过）：
                  #   S2MM_DMACR=0x30 / S2MM_DMASR=0x34，帧计数在 [23:16]、halted=bit0、错误=bit4..11。


def decode_w3(w3):
    """axis_probe w3 → 好读的一行"""
    return ('见过[tvalid%d tuser%d tlast%d tready%d] '
            'live[tvalid%d tlast%d tuser%d tready%d] '
            'ovf_live%d unf_live%d ovf_seen%d unf_seen%d tuser_lvl%d '
            '桥[sof_armed%d dropping%d wr_rst_busy%d rst_pclk%d]'
            % (w3 & 1, (w3 >> 1) & 1, (w3 >> 2) & 1, (w3 >> 3) & 1,
               (w3 >> 4) & 1, (w3 >> 5) & 1, (w3 >> 6) & 1, (w3 >> 7) & 1,
               (w3 >> 10) & 1, (w3 >> 11) & 1, (w3 >> 13) & 1, (w3 >> 14) & 1,
               (w3 >> 15) & 1,
               (w3 >> 31) & 1, (w3 >> 30) & 1, (w3 >> 29) & 1, (w3 >> 28) & 1))


def main():
    gap = 2.0
    n = 4
    with_vdma = False
    for i, a in enumerate(sys.argv):
        if a == '--gap' and i + 1 < len(sys.argv):
            gap = float(sys.argv[i + 1])
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
        if a == '--vdma':
            with_vdma = True

    # ★ --vdma：先把 VDMA 跑起来再采样。为什么必须加这个开关：驱动一退出
    #   （进程结束 / __del__）就会 stop() 把 DMACR 清 0 —— 此时 VDMA 的
    #   m_axis_tready 是低的，桥的读侧 rd_en 恒 0 → FIFO 灌满 → **每次写都溢出**。
    #   不带 --vdma 采样，看到的"读侧 0 弹出 + 溢出 178M 次"全是被测对象的
    #   正常反应，不是故障（第一天就差点据此去改桥，幸亏对了一遍 c_rd 的累计值）。
    cap = None
    if with_vdma:
        from ball_detect import _make_cap
        cap = _make_cap()
        print('[VDMA] 驱动已建（4 缓冲 + start()）—— DMACR 应为非 0')

    mm0 = MMIO(0x43A00000, 0x1000)     # 传感器域探针：pclk/64、vsync
    mm1 = MMIO(0x43A10000, 0x1000)     # href、scl
    mm4 = MMIO(P4, 0x10000)
    mm5 = MMIO(P5, 0x10000)
    mm6 = MMIO(P6, 0x10000)
    mmv = MMIO(VCTRL, 0x1000)
    mms = MMIO(VSTAT, 0x1000)
    mmd = MMIO(VDMA, 0x10000)

    def snap():
        sr = mmd.read(DMASR)
        return dict(
            pclk=mm0.read(0x00), vsync=mm0.read(0x08),
            href=mm1.read(0x00),
            tvalid=mm4.read(0x00), tuser=mm4.read(0x08),
            tlast=mm5.read(0x00), w3=mm5.read(0x08),
            br=mm6.read(0x00),
            vframe=mmv.read(0x08),
            stat=mms.read(0x00), stat2=mms.read(0x08),
            dmasr=sr, dmaframe=(sr >> 16) & 0xFF,
            dmacr=mmd.read(DMACR),
        )

    print('== 采样 %d 次 x %.2fs（只读，不动任何状态）==' % (n, gap))
    prev = snap()
    print('[t0 ] DMACR=0x%08x DMASR=0x%08x halted=%d err=%d 帧计数=%d'
          % (prev['dmacr'], prev['dmasr'], prev['dmasr'] & 1,
             bool(prev['dmasr'] & 0xFF0), prev['dmaframe']))
    print('     传感器累计 pclk/64=%d vsync=%d href=%d'
          % (prev['pclk'], prev['vsync'], prev['href']))
    print('     vision_core frame_cnt=%d  stat=0x%08x  stat2=0x%08x'
          % (prev['vframe'], prev['stat'], prev['stat2']))
    print('     %s' % decode_w3(prev['w3']))

    bad = []
    for k in range(n):
        time.sleep(gap)
        cur = snap()
        dt = gap
        dv = (cur['tvalid'] - prev['tvalid']) & 0xFFFFFFFF
        du = (cur['tuser'] - prev['tuser']) & 0xFFFFFFFF
        dl = (cur['tlast'] - prev['tlast']) & 0xFFFFFFFF
        db = (cur['br'] - prev['br']) & 0xFFFFFFFF
        df = (cur['dmaframe'] - prev['dmaframe']) & 0xFF
        dvf = cur['vframe'] - prev['vframe']
        dpk = (cur['pclk'] - prev['pclk']) & 0xFFFFFFFF
        dvs = (cur['vsync'] - prev['vsync']) & 0xFFFFFFFF
        dhr = (cur['href'] - prev['href']) & 0xFFFFFFFF
        print('[t%d ] 传感器 pclk/64 %.2fM/s  vsync %.1f/s  href %.0f/s || '
              'AXI 域 tvalid %.2fM/s  tuser %.1f/s  tlast %.0f/s  桥影子 %.2fM/s | '
              'DMASR 帧 +%d  vision_core 帧 +%d'
              % ((k + 1) * gap, dpk / dt / 1e6, dvs / dt, dhr / dt,
                 dv / dt / 1e6, du / dt, dl / dt,
                 db / dt / 1e6, df, dvf))
        print('      %s' % decode_w3(cur['w3']))
        if dv == 0 and dhr > 1000:
            bad.append('传感器在出数据（href 活着）但桥一拍没推 —— **桥这一级的问题**：'
                       '看 ovf（bit10/13）。桥写侧 FIFO 满了就整行丢弃，'
                       '溢出后要复位才恢复')
        elif dv == 0:
            bad.append('源侧 tvalid 停了（传感器/桥死了）→ 跑 dvp_probe.py 或 ctrl[4] 救流')
        elif dl == 0:
            bad.append('tvalid 活但 tlast 停（桥只推不发 EOL）→ 查桥影子 c_push/c_rd/c_ovf')
        elif df == 0 and dvf == 0:
            if not ((cur['w3'] >> 7) & 1):
                bad.append('数据在流、但 live tready=0（VDMA 没在收）→ 查 DMACR/halted')
            else:
                bad.append('tvalid/tlast 都在、tready 也在，但两边帧计数都不动 → '
                           '大概率是"帧尺寸不符"被 VDMA 整帧丢弃（查 fsize 是否 2048x720）')
        prev = cur

    print('')
    if bad:
        print('★ 诊断：')
        for b in set(bad):
            print('   - %s' % b)
    else:
        print('链路各段速率都正常 —— 卡死不是"流断了"，得从帧率/AE/驱动轮询上找')
    return 0


if __name__ == '__main__':
    sys.exit(main())
