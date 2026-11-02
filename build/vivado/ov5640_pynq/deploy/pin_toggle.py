# -*- coding: utf-8 -*-
"""pin_toggle.py — 用软件开关 + 万用表，验证 FPGA 控制线是否真的通到传感器模块

为什么需要它：探针只能测到 **FPGA 出脚处** 的电平，无法证明信号走到了模块。
而 SCCB 配置 FSM 是"盲写"、不检查 ACK —— 所以"配置成功"其实什么都不证明。
本脚本让 XCLK / PWDN 周期性翻转，用万用表在**模块端**看它跟不跟着变，
从而把"线没通/接错脚"和"传感器本身不工作"彻底分开。

用法（PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/pin_toggle.py          # XCLK+PWDN 两段翻转
    sudo -E PYTHONIOENCODING=gbk python3 /boot/pin_toggle.py scan     # 逐脚扫描模式

操作：
    1. 万用表打 **直流电压档**，黑笔接模块 GND，红笔戳模块的 XCLK 脚
    2. 运行脚本，盯着万用表看电压是否跟着屏幕上的 [开]/[关] 变化
    3. 阶段2 把红笔移到模块的 PWDN 脚，同样看是否跟着变

判读：
    - XCLK 开→约 1.5~1.7V（24MHz 方波的平均值），关→0.00V
      跟着变 = 这根线是通的；恒定不动 = 线没通 / 接错脚
    - PWDN 强制高→约 3.3V，正常→0.00V，同上判读
    - 想找"XCLK 到底跑到哪个脚去了"，就拿红笔逐个戳模块引脚，
      看哪一根跟着屏幕变化 —— 那就找到了接错的位置
"""

import sys
import time

from pynq import MMIO, Bitstream, Overlay

G3 = 0x43A30000
BIT = '/boot/ov5640_m1a_wrapper.bit'
GPIO_DATA = 0x00
XD = 4.0          # 每个状态保持秒数

CTRL_NORMAL = 0x0    # bit0=0 XCLK 开；bit1=0 PWDN 随 pl
CTRL_XCLK_OFF = 0x1  # bit0=1 停 XCLK
CTRL_PWDN_HI = 0x2   # bit1=1 强制 PWDN 拉高


def load():
    try:
        Overlay(BIT)
        print('[toggle] overlay 已下载')
    except Exception as e:
        print(f'[toggle] Overlay 失败（{str(e)[:60]}...），Bitstream 直下')
        Bitstream(BIT).download()
        print('[toggle] bitstream 已直下')


def step(mm3, val, label, expect, hold=XD):
    mm3.write(GPIO_DATA, val)
    print(f'{time.strftime("%H:%M:%S")}  {label}   （万用表应读 {expect}）')
    for left in range(int(hold), 0, -1):
        print(f'      …{left}s', end='\r', flush=True)
        time.sleep(1)
    print(' ' * 12, end='\r')


if __name__ == '__main__':
    load()
    mm3 = MMIO(G3, 0x10000)
    mode = sys.argv[1] if len(sys.argv) > 1 else 'both'

    # ---- 扫描模式：用 XCLK 开/关当差分，在模块排针上逐脚找出 XCLK 的落点 ----
    if mode == 'scan':
        print('=' * 62)
        print('扫描模式：XCLK 关 20s 与 开 20s 交替，共 3 轮')
        print('每档有 20 秒，够你一格一格戳【模块的每一个引脚】')
        print('  判读：在「关」档读 0.00V 的脚就是候选；')
        print('        若它在「开」档读数明显不同 → 那就是 XCLK 真正的落点（接线错位）')
        print('        若「关」档所有脚都读 0.00V 且「开」档全都不变 → 线确实没通')
        print('=' * 62)
        for k in range(3):
            step(mm3, CTRL_XCLK_OFF, f'第{k+1}轮 XCLK 关', '0.00V', hold=20.0)
            step(mm3, CTRL_NORMAL, f'第{k+1}轮 XCLK 开', '读数明显不同', hold=20.0)
        mm3.write(GPIO_DATA, CTRL_NORMAL)
        print('  → XCLK 已恢复常开')
        sys.exit(0)

    print('=' * 62)
    print('阶段 1：XCLK 开/关 交替 —— 红笔戳【模块的 XCLK 脚】')
    print('=' * 62)
    time.sleep(3)
    for i in range(6):
        if i % 2 == 0:
            step(mm3, CTRL_NORMAL, 'XCLK 开', '约 1.5~1.7V')
        else:
            step(mm3, CTRL_XCLK_OFF, 'XCLK 关', '0.00V')
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    print('  → XCLK 已恢复常开')

    print()
    print('=' * 62)
    print('阶段 2：PWDN 强制高/正常 交替 —— 红笔移到【模块的 PWDN 脚】')
    print('=' * 62)
    time.sleep(3)
    for i in range(4):
        if i % 2 == 0:
            step(mm3, CTRL_PWDN_HI, 'PWDN 强制高', '约 3.3V')
        else:
            step(mm3, CTRL_NORMAL, 'PWDN 正常', '0.00V')
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    print('  → 已恢复：XCLK 开、PWDN 正常（ctrl=0）')

    print()
    print('提示：若两根线都跟着变，说明控制线全通 —— 问题在传感器侧')
    print('      （检查模块 STB 脚，目前悬空；试接 GND，无效再试 3V3）。')
    print('      若某根不动，拿红笔逐脚扫模块排针，找哪根跟着变 = 接错了。')
