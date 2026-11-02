# -*- coding: utf-8 -*-
"""wdt_check.py — 只读：看门狗触发过吗？FIFO 水位现在多少？（**不动任何状态**）

为什么要这么"干净"的一个脚本：`stream_ledger.py --quiet` 会先跑 `_init_video`
（脉冲 ctrl[3] = 复位视频域），那本身就能把卡死解掉 —— 于是你永远看不到"卡死当时"
的水位和看门狗标记。本脚本**只读寄存器**，不建驱动、不下 bit、不复位任何东西。

读：
  0x43B1_0000 +0x08  vision_core 的 frame_cnt（两次读数看涨不涨 = 视核在不在跑）
  0x43A6_0000        桥的 7 号影子（先写 sel=7 到 ctrl[9:7]）：
                     [31] 看门狗触发过（粘滞）
                     [19] dropping  [18] sof_armed  [17] rd_rst_busy  [16] empty
                     [15] wr_rst_busy  [14] full  [13:0] wr_data_count（写侧水位）
  0x43A4_0008        probe w1（tuser 上升沿累计）

判读：
  看门狗=1 且仍卡死 → 看门狗触发了但**救不回来**（复位后帧首还是送不出去）
  看门狗=0 → **根本没触发**（水位没到 6144？计数器没跑？条件写错？）

用法（板上）：sudo -E PYTHONIOENCODING=utf-8 python3 /boot/wdt_check.py
"""

import sys
import time

sys.path.insert(0, '/boot')

from pynq import MMIO

CTRL = 0x43A30000
DBG = 0x43A60000
W0B = 0x43A40000
VCTL = 0x43B10000


def main():
    mm3 = MMIO(CTRL, 0x10000)
    mm6 = MMIO(DBG, 0x10000)
    mm4 = MMIO(W0B, 0x10000)
    mmc = MMIO(VCTL, 0x1000)

    # sel=7 读出桥的 7 号影子；其余位（trig 等）保持原样
    c0 = mm3.read(0x00)
    mm3.write(0x00, (c0 & ~(0x7 << 7)) | (7 << 7))
    time.sleep(0.05)
    d7 = mm6.read(0x00)
    mm3.write(0x00, c0)

    print('8+3 号影子 = 0x%08x' % d7)
    print('  看门狗触发过 = %d   dropping = %d   sof_armed = %d'
          % ((d7 >> 31) & 1, (d7 >> 19) & 1, (d7 >> 18) & 1))
    print('  rd_rst_busy = %d   empty = %d   wr_rst_busy = %d   full = %d'
          % ((d7 >> 17) & 1, (d7 >> 16) & 1, (d7 >> 15) & 1, (d7 >> 14) & 1))
    print('  写侧水位 wr_data_count = %d（阈值：普通行 4096 / SOF 行 6144；深度 8192）'
          % (d7 & 0x3FFF))
    if d7 == 0:
        print('  （全 0 —— 板上可能是旧版 bit，没有 7 号槽）')

    fc0 = mmc.read(0x08)
    w1a = mm4.read(0x08)
    time.sleep(2.0)
    fc1 = mmc.read(0x08)
    w1b = mm4.read(0x08)
    print('')
    print('frame_cnt %d -> %d（%+.1f fps）：%s'
          % (fc0, fc1, (fc1 - fc0) / 2.0,
             '视核在跑 ✓' if fc1 != fc0 else '★ 视核没在跑（卡死）'))
    print('probe w1(tuser 累计) %d -> %d（+%d，%+.1f/s）：%s'
          % (w1a, w1b, w1b - w1a, (w1b - w1a) / 2.0,
             '帧首在产生 ✓' if w1b != w1a else '★ 帧首没产生'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
