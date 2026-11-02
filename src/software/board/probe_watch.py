# -*- coding: utf-8 -*-
"""probe_watch.py — 探针 v2 增量监视 + XCLK 主动测试（判决实验）

用法（PYNQ 板上，需 deploy/ 的探针 v2 bit）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/probe_watch.py          # 只监视 20s
    sudo -E PYTHONIOENCODING=gbk python3 /boot/probe_watch.py xclk     # 加 XCLK 开关实验

寄存器（BD 固定地址）：
  probe0 0x43A00000: +0x00 w0=pclk/64计数   +0x08 w1=vsync
  probe1 0x43A10000: +0x00 w2=href          +0x08 w3=scl
  probe2 0x43A20000: +0x00 w4=sda           +0x08 w5=位域
  probe3 0x43A30000: +0x00 ctrl(只写)       +0x08 w6=xclk翻转计数(只读)

w5 位域：bit0=locked  [8:1]=见过1  [16:9]=见过0  [17]=xclk电平
         [18]=pwdn电平  [19]=rst_n电平

ctrl 位：bit0=1 停 XCLK（**0=常开，默认**）    bit1=1 强制 PWDN 拉高（0=随 pl）
  ★ 极性是"停钟位"不是"开钟位"：axi_gpio 上电为 0，必须让 0 对应正常工作。

判决实验判读：
  [1] XCLK 开 → w6 应涨（实测频率 ≈24MHz）
  [2] XCLK 关 → w6 应冻结
  [3] XCLK 重开 → 若 pclk/64 跟着从 0 变活 = 传感器确实在用这根 XCLK，
      问题在 SCCB 配置；若 pclk 始终 0 = XCLK 没到传感器 / 传感器没启用 PLL。
"""

import sys
import time

from pynq import MMIO, Bitstream, Overlay

G0, G1, G2, G3 = 0x43A00000, 0x43A10000, 0x43A20000, 0x43A30000
BIT = '/boot/ov5640_m1a_wrapper.bit'
GPIO_DATA, GPIO2_DATA = 0x00, 0x08
CTRL_NORMAL = 0x0        # bit0=0 → XCLK 开；bit1=0 → PWDN 随 pl
CTRL_XCLK_OFF = 0x1      # bit0=1 → 停钟


def load():
    try:
        Overlay(BIT)
        print('[watch] overlay 已下载')
    except Exception as e:
        print(f'[watch] Overlay 失败（{str(e)[:80]}...），Bitstream 直下')
        Bitstream(BIT).download()
        print('[watch] bitstream 已直下')


def rd(mm0, mm1, mm2, mm3):
    """读 7 个字：w0..w6"""
    return (mm0.read(GPIO_DATA), mm0.read(GPIO2_DATA),
            mm1.read(GPIO_DATA), mm1.read(GPIO2_DATA),
            mm2.read(GPIO_DATA), mm2.read(GPIO2_DATA),
            mm3.read(GPIO2_DATA))


def win(mm0, mm1, mm2, mm3, sec):
    """在 sec 秒窗口内测各计数增量，返回 (增量列表, 实际时长)"""
    t0 = time.time()
    a = rd(mm0, mm1, mm2, mm3)
    while time.time() - t0 < sec:
        time.sleep(0.005)
    b = rd(mm0, mm1, mm2, mm3)
    return [int(y) - int(x) for x, y in zip(a, b)], time.time() - t0


def show_levels(w5):
    print(f'     电平：locked={w5 & 1}  xclk={((w5 >> 17) & 1)}  '
          f'pwdn={((w5 >> 18) & 1)}  rst_n={((w5 >> 19) & 1)}  '
          f'（正常应为 locked=1 xclk=1 pwdn=0 rst_n=1）')
    print(f'     数据线：见过1={((w5 >> 1) & 0xFF):08b}  见过0={((w5 >> 9) & 0xFF):08b}')


if __name__ == '__main__':
    load()
    do_xclk = len(sys.argv) > 1 and sys.argv[1] == 'xclk'
    mm0, mm1, mm2, mm3 = (MMIO(G0, 0x10000), MMIO(G1, 0x10000),
                          MMIO(G2, 0x10000), MMIO(G3, 0x10000))

    print('[watch] 立即采样 20s（0.2s 一行，增量；xclk 单位=翻转数）')
    prev = rd(mm0, mm1, mm2, mm3)
    t0 = time.time()
    while time.time() - t0 < 20.0:
        time.sleep(0.2)
        cur = rd(mm0, mm1, mm2, mm3)
        d = [int(c) - int(p) for c, p in zip(cur, prev)]
        print(f'  {time.time()-t0:5.1f}  pclk/64={d[0]:8d}  vsync={d[1]:4d}  '
              f'href={d[2]:4d}  scl={d[3]:5d}  sda={d[4]:5d}  xclk={d[6]:9d}')
        prev = cur

    w = rd(mm0, mm1, mm2, mm3)
    print(f'---- 累计：pclk/64={w[0]} vsync={w[1]} href={w[2]} '
          f'scl={w[3]} sda={w[4]} xclk={w[6]}')
    show_levels(w[5])

    if not do_xclk:
        sys.exit(0)

    print('==== XCLK 主动测试（判决实验）====')
    # ---- [1] XCLK 开（默认态，先写一次确保确定）----
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    time.sleep(0.3)
    d, dt = win(mm0, mm1, mm2, mm3, 0.2)
    fx = d[6] / (2.0 * dt) / 1e6          # 翻转数/2 = 周期数
    fp = d[0] * 64 / dt / 1e6
    print(f'[1] XCLK 开 │ 实测 XCLK ≈ {fx:6.2f} MHz   PCLK ≈ {fp:6.2f} MHz'
          f'   （应为 24.00 / 84 附近；XCLK≈0 = 没输出）')

    # ---- [2] XCLK 关 ----
    mm3.write(GPIO_DATA, CTRL_XCLK_OFF)
    time.sleep(0.3)
    d, dt = win(mm0, mm1, mm2, mm3, 0.2)
    fx = d[6] / (2.0 * dt) / 1e6
    fp = d[0] * 64 / dt / 1e6
    print(f'[2] XCLK 关 │ 实测 XCLK ≈ {fx:6.2f} MHz   PCLK ≈ {fp:6.2f} MHz'
          f'   （XCLK 应≈0.00；若 PCLK 同时消失 = 传感器在跟 XCLK）')

    # ---- [3] 重开，看 PCLK 是否回来 ----
    mm3.write(GPIO_DATA, CTRL_NORMAL)
    time.sleep(0.5)
    d, dt = win(mm0, mm1, mm2, mm3, 0.2)
    fx = d[6] / (2.0 * dt) / 1e6
    fp = d[0] * 64 / dt / 1e6
    print(f'[3] XCLK 重开 │ 实测 XCLK ≈ {fx:6.2f} MHz   PCLK ≈ {fp:6.2f} MHz')
    print('    判读：PCLK 在 [2] 消失、[3] 回来 → **传感器确实在用这根 XCLK**，'
          '链路通，问题在 SCCB 配置；')
    print('          PCLK 三档全 0（现在就是）→ XCLK 没送到传感器，'
          '或传感器 PLL 没启用 → 查 XCLK 走线/PWDN/RST 电平。')

    w = rd(mm0, mm1, mm2, mm3)
    show_levels(w[5])
