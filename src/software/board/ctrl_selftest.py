# -*- coding: utf-8 -*-
"""ctrl_selftest.py — 纯软件闭环自检：PS 的 ctrl 写到底有没有生效？

■ 为什么需要它
万用表在【模块端】读到 XCLK 恒定 3.3V、PWDN 恒定 0V、不随开关变化。
这有两种截然不同、修法完全相反的解释：

  (A) ctrl 写根本没生效（dvp_ctrl 逻辑或 BD 连线有 bug）
      → FPGA 引脚本来就没动过，万用表当然看不到变化 → 得回炉重建
  (B) ctrl 生效了、FPGA 引脚确实在动
      → 是线没通 / 接错脚（模块 XVCLK 悬空被上拉到 3.3V、
        PWDN 悬空被内部下拉到 0V，正好就是那两个读数）→ 修线

不把 A / B 分开，下一步必然朝错的方向修。而探针自己就是现成的示波器：

  w6 = XCLK 引脚的翻转计数   → 停钟后必须掉到 0，开钟后必须回到 ~24MHz
  w5 bit18 = PWDN 引脚实际电平 → 强制拉高后必须读回 1，放开后必须读回 0

全程不需要万用表、不需要重新构建，跑一次就能定案。

■ 用法
    sudo -E PYTHONIOENCODING=gbk python3 /boot/ctrl_selftest.py

■ 判读
  4 项全对  → PS→ctrl→引脚 全链路证明有效，问题 100% 在下游（线 / 模块 / 传感器）
  任一项错  → dvp_ctrl 或 BD 连线有问题，我这边重新构建
"""

import time

from pynq import MMIO, Bitstream, Overlay

G0, G1, G2, G3 = 0x43A00000, 0x43A10000, 0x43A20000, 0x43A30000
BIT = '/boot/ov5640_m1a_wrapper.bit'
GPIO_DATA, GPIO2_DATA = 0x00, 0x08
CTRL_NORMAL = 0x0     # bit0=0 XCLK 开；bit1=0 PWDN 随 pl  ← 默认态=正常工作
CTRL_XCLK_OFF = 0x1   # bit0=1 停 XCLK
CTRL_PWDN_HI = 0x2    # bit1=1 强制 PWDN 拉高

results = []


def load():
    try:
        Overlay(BIT)
        print('[selftest] overlay 已下载')
    except Exception as e:
        print(f'[selftest] Overlay 失败（{str(e)[:60]}...），Bitstream 直下')
        Bitstream(BIT).download()
        print('[selftest] bitstream 已直下')


def read_w6(mm3):
    """在 0.2s 窗口内测 XCLK 翻转增量 → 返回 (翻转数, 实测MHz)"""
    a = mm3.read(GPIO2_DATA)
    t0 = time.time()
    while time.time() - t0 < 0.2:
        time.sleep(0.004)
    b = mm3.read(GPIO2_DATA)
    dt = time.time() - t0
    n = int(b) - int(a)
    return n, n / (2.0 * dt) / 1e6


def read_w5(mm2):
    return int(mm2.read(GPIO2_DATA))


def check(idx, name, ok, detail):
    # 注意：标记只用 ASCII。勾/叉符号(U+2714/U+2718)不在 GBK 码表里，
    # 串口是 GBK 编码，打印它们会直接 UnicodeEncodeError 崩掉。
    results.append((idx, name, ok))
    print(f'  [{idx}] {"[通过]" if ok else "[失败]"}  {name}')
    print(f'      {detail}')


if __name__ == '__main__':
    load()
    mm0, mm1, mm2, mm3 = (MMIO(G0, 0x10000), MMIO(G1, 0x10000),
                          MMIO(G2, 0x10000), MMIO(G3, 0x10000))

    print()
    print('=' * 66)
    print('PS → ctrl → 引脚 闭环自检（全程软件，不用万用表）')
    print('=' * 66)

    # ---------- T1：默认态，XCLK 应为 24MHz ----------
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    time.sleep(0.4)
    n1, f1 = read_w6(mm3)
    check('T1', 'ctrl=0x0 默认态 → XCLK 应常开 24MHz',
          f1 > 20.0,
          f'实测 {f1:.2f} MHz（翻转 {n1} / 0.2s）')

    # ---------- T2：停钟，w6 必须冻结 ----------
    mm3.write(GPIO_DATA, CTRL_XCLK_OFF)
    time.sleep(0.4)
    n2, f2 = read_w6(mm3)
    lv2 = (read_w5(mm2) >> 17) & 1          # 停钟后 xclk 电平应恒为 0
    time.sleep(0.05)
    lv2b = (read_w5(mm2) >> 17) & 1
    check('T2', 'ctrl=0x1 停钟 → XCLK 必须停（w6 掉到 0）',
          n2 == 0,
          f'实测 {f2:.2f} MHz（翻转 {n2} / 0.2s，应为 0）')

    # ---------- T3：停钟后引脚电平应被钉在 0 ----------
    check('T3', '停钟后 xclk 引脚电平应恒为 0（连读两次）',
          lv2 == 0 and lv2b == 0,
          f'读回 {lv2} / {lv2b}（应都是 0；若为 1 说明门控没接上）')

    # ---------- T4：重开，w6 必须回来 ----------
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    time.sleep(0.4)
    n4, f4 = read_w6(mm3)
    check('T4', 'ctrl=0x0 重开 → XCLK 必须回来',
          f4 > 20.0,
          f'实测 {f4:.2f} MHz（翻转 {n4} / 0.2s）')

    # ---------- T5：强制 PWDN 高，w5 bit18 必须读回 1 ----------
    mm3.write(GPIO_DATA, CTRL_PWDN_HI)
    time.sleep(0.3)
    p5 = (read_w5(mm2) >> 18) & 1
    check('T5', 'ctrl=0x2 强制 PWDN 高 → pwdn 位必须读回 1',
          p5 == 1,
          f'读回 {p5}（应为 1；若为 0 说明 ctrl 没进到 dvp_ctrl）')

    # ---------- T6：放开，w5 bit18 必须读回 0 ----------
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    time.sleep(0.3)
    p6 = (read_w5(mm2) >> 18) & 1
    check('T6', 'ctrl=0x0 放开 → pwdn 位必须读回 0',
          p6 == 0,
          f'读回 {p6}（应为 0）')

    mm3.write(GPIO_DATA, CTRL_NORMAL)
    w5 = read_w5(mm2)
    print(f'  （已恢复 ctrl=0x0 默认态；locked={(w5 >> 0) & 1} '
          f'xclk={((w5 >> 17) & 1)} pwdn={((w5 >> 18) & 1)} rst_n={((w5 >> 19) & 1)}）')

    print()
    print('=' * 66)
    npass = sum(1 for _, _, ok in results if ok)
    print(f'汇总：{npass} / {len(results)} 通过')
    if npass == len(results):
        print('★ 结论：PS→ctrl→引脚 全链路有效。FPGA 引脚【确实】在按命令动。')
        print('  →模块端读不到变化，只能是【线没通 / 接错脚】。')
        print('  →下一步：拿万用表在模块排针上逐脚扫，'
              '用 XCLK 开/关当差分信号找出 XCLK 到底跑到了哪个脚。')
    else:
        print('★ 结论：ctrl 这条路本身有问题（不是线的问题）。')
        print('  →是我的 dvp_ctrl 或 BD 连线 bug，把上面这段贴回来我重新构建。')
    print('=' * 66)
