# -*- coding: utf-8 -*-
"""find_pins.py — 让 FPGA 自己把 PMOD 的孔"喊"出来（不需要任何丝印/资料）

■ 为什么需要它
PYNQ-Z2 的 PMOD 排针没有可读的丝印，而 Pmod 2×6 的编号方式本身就不直观
（官方规格里自己标为 non-standard）。靠猜容易把 8 根线全插错一格。

但我们有两个**已经用 ctrl_selftest.py 证明过、PS 能完全控制**的输出：
    JB8 = ov5640_pwdn   → 能干净地在 0V / 3.3V 之间切换
    JB4 = ov5640_xclk   → 能整根停掉（输出被钉在 0V）

于是：让它们慢慢翻转，拿万用表挨个孔戳 —— **哪个孔跟着动，哪个就是它**。
找到 JB4 和 JB8 之后，整个排针就唯一确定了（两者在同一列，上 4 下 8）。

■ 用法
    sudo -E PYTHONIOENCODING=gbk python3 /boot/find_pins.py

■ 阶段 1：找 JB8（pwdn）
  红笔挨个戳 JB 排针的 12 个孔，每档 20 秒。
  【高度】那一档读到 **≈3.3V**、【正常】那一档读到 **0.00V** 的孔 = **JB8**
  （这个信号是干净的直流，读数非常好认，优先用它定位）

■ 阶段 2：找 JB4（xclk）
  【关】那一档读到 **0.00V**、【开】那一档读数**明显不同**的孔 = **JB4**
  （24MHz 方波用直流档量大约是 1.6V，但表不同读数会飘，
    判据看"关=0.00V 且开≠0.00V"，不要纠结具体数值）

■ 找到之后
  JB4 在上排、JB8 在下排，**两者上下对齐（同一列）**：
      上排： JB1  JB2  JB3  JB4  GND  3V3
      下排： JB7  JB8  JB9  JB10 GND  3V3
  也就是 JB1~JB4 是上排那 4 个孔、JB7~JB10 是下排那 4 个孔，
  **1 对 7、2 对 8、3 对 9、4 对 10**（不是"上排 1~6、下排接着 7~12"）。

  JA 的排布完全一样，只是没有翻转实验可做 —— 按上面的规律推断即可
  （JA5=GND、JA6=3V3，别接反）。
"""

import sys
import time

from pynq import MMIO, Bitstream, Overlay

G3 = 0x43A30000
BIT = '/boot/ov5640_m1a_wrapper.bit'
GPIO_DATA = 0x00
XD = 20.0            # 每档保持秒数（够你一个一个戳完 12 个孔）

CTRL_NORMAL = 0x0
CTRL_XCLK_OFF = 0x1
CTRL_PWDN_HI = 0x2


def load():
    try:
        Overlay(BIT)
        print('[find] overlay 已下载')
    except Exception as e:
        print(f'[find] Overlay 失败（{str(e)[:60]}...），Bitstream 直下')
        Bitstream(BIT).download()
        print('[find] bitstream 已直下')


def step(mm3, val, label, expect, hold=XD):
    mm3.write(GPIO_DATA, val)
    print()
    print(f'{time.strftime("%H:%M:%S")}  >>> {label}   （该孔此时应读 {expect}）')
    for left in range(int(hold), 0, -1):
        print(f'      剩余 {left:2d}s …', end='\r', flush=True)
        time.sleep(1)
    print(' ' * 20, end='\r')


if __name__ == '__main__':
    load()
    mm3 = MMIO(G3, 0x10000)

    print()
    print('=' * 66)
    print('阶段 1：找 JB8 = ov5640_pwdn')
    print('  红笔挨个戳 JB 的 12 个孔；【强制高】档读 ≈3.3V、')
    print('  【正常】档读 0.00V 的那个孔，就是 JB8。')
    print('=' * 66)
    time.sleep(3)
    for i in range(3):
        step(mm3, CTRL_PWDN_HI, f'第{i+1}轮  PWDN 强制高', '≈3.3V')
        step(mm3, CTRL_NORMAL, f'第{i+1}轮  PWDN 正常  ', '0.00V')
    mm3.write(GPIO_DATA, CTRL_NORMAL)

    print()
    print('=' * 66)
    print('阶段 2：找 JB4 = ov5640_xclk')
    print('  红笔挨个戳 JB 的 12 个孔；【关】档读 0.00V、')
    print('  【开】档读数明显不同的那个孔，就是 JB4。')
    print('=' * 66)
    time.sleep(3)
    for i in range(3):
        step(mm3, CTRL_XCLK_OFF, f'第{i+1}轮  XCLK 关', '0.00V')
        step(mm3, CTRL_NORMAL, f'第{i+1}轮  XCLK 开', '读数非 0')
    mm3.write(GPIO_DATA, CTRL_NORMAL)

    print()
    print('=' * 66)
    print('已恢复常开（ctrl=0x0）。')
    print('把找到的 JB4 / JB8 两个孔的位置报回来，就能定死整排的编号。')
    print('提示：JB4 与 JB8 必然上下对齐（同一列）。')
    print('=' * 66)
