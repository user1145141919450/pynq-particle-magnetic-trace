# -*- coding: utf-8 -*-
"""dvp_probe.py — DVP 寄存器探针（M1a 排障专用）：读 PL 里 dvp_probe 的计数器

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/dvp_probe.py

探针版 BD 在采集链之外加了 dvp_probe 模块 + 3 个 axi_gpio（地址固定）：
  w0 = pclk/64 上升沿计数  （84MHz 时每秒约 131 万；0 或不再涨 = 像素时钟死）
  w1 = vsync 上升沿计数    （传感器出过几帧）
  w2 = href 上升沿计数     （传感器出过几行）
  w3 = scl 上升沿计数      （SCCB：304 条寄存器 x 27 SCL ≈ 8200+；0 = 配置从未发出）
  w4 = sda 上升沿计数
  w5 = {15'b0, 见过0[7:0], 见过1[7:0], locked}   （bit i 对应 D{i}）

第十三版新增：健康自检 + ctrl[4] 自动修复。上板发现传感器状态逐次下载随机
（同一天 1/5 健康：pclk 在跑但 vsync 永不来）。本脚本 6s 判读后若传感器没在
出帧，就用 ctrl[4] 重跑一次完整上电时序 + 304 条 SCCB 配置（连带复位桥的
FIFO —— 旧垃圾字节堵在 FIFO 头部的话新 SOF 到不了 VDMA），最多修 3 次。
修好后 bit 保持加载、传感器保持健康 —— 后续脚本加 noprog 参数接力，不再
重下 bit（重下 = 重新掷骰子）。

推荐上板验证链（只掷一次骰子，其余全部 noprog 接力）：
  dvp_probe.py（本脚本，含自动修复）
  → stream_probe.py noprog（桥：tvalid≈44.4M/s、tuser 30/s、tlast 21660/s）
  → line_count.py noprog（每帧恒 722 行）
  → run_selfcheck.py noprog（出真图）
"""

import time

from pynq import MMIO, Bitstream, Overlay

G0, G1, G2 = 0x43A00000, 0x43A10000, 0x43A20000
G3 = 0x43A30000          # +0x00 ctrl 输出（通道1）
BIT = '/boot/ov5640_m1a_wrapper.bit'

CTRL_SENSOR_RST_BIT = 4  # ctrl[4] = 按住传感器复位重跑上电+SCCB（第十三版）
MAX_HEAL = 3             # 最多修复 3 次


def load_bit():
    try:
        Overlay(BIT)
        print('[探针] overlay 已下载')
    except Exception as e:
        msg = str(e).replace('\n', ' ')[:120]
        print(f'[探针] Overlay 方式失败（{msg}...），Bitstream 直下')
        Bitstream(BIT).download()
        print('[探针] bitstream 已直下')


def read_words(mm0, mm1, mm2):
    return (mm0.read(0x00), mm0.read(0x08),
            mm1.read(0x00), mm1.read(0x08),
            mm2.read(0x00), mm2.read(0x08))


def measure(mm0, mm1, mm2, tag):
    """采 12 次 × 0.5s 增量，返回每行增量（vsync/href 在 1/2 下标）"""
    prev = read_words(mm0, mm1, mm2)
    names = ['pclk/64', 'vsync', 'href', 'scl', 'sda']
    print(f'[{tag}] 每 0.5s 采一次增量共 12 次（0 = 该信号这 6s 内无活动）：')
    print('  t(s)  ' + '  '.join(f'{n:>10}' for n in names))
    rows = []
    for i in range(12):
        time.sleep(0.5)
        cur = read_words(mm0, mm1, mm2)
        d = tuple(int(c) - int(p) for c, p in zip(cur[:5], prev[:5]))
        rows.append(d)
        print(f'  {(i + 1) * 0.5:4.1f}  ' + '  '.join(f'{v:>10}' for v in d))
        prev = cur
    return rows


def heal(mm3, attempt):
    """ctrl[4] 脉冲：重跑传感器上电时序 + SCCB（连带复位桥 FIFO）"""
    print(f'[修复] 第 {attempt} 次：ctrl[4]=1 按住传感器复位 0.5s ...')
    cur = mm3.read(0x00)
    mm3.write(0x00, cur | (1 << CTRL_SENSOR_RST_BIT))
    time.sleep(0.5)
    mm3.write(0x00, cur & ~(1 << CTRL_SENSOR_RST_BIT))
    print('[修复] ctrl[4]=0 释放：重跑 PWDN/RST 上电时序 + 304 条 SCCB（约 1~2s），等 3s')
    time.sleep(3)


def main():
    load_bit()
    print('[探针] 等 3s：MMCM 锁定 + OV5640 上电时序 + SCCB 配置窗口...')
    time.sleep(3)

    mm0, mm1, mm2 = MMIO(G0, 0x10000), MMIO(G1, 0x10000), MMIO(G2, 0x10000)
    mm3 = MMIO(G3, 0x10000)

    w0, w1, w2, w3, w4, w5 = read_words(mm0, mm1, mm2)
    locked = w5 & 1
    seen1 = (w5 >> 1) & 0xFF
    seen0 = (w5 >> 9) & 0xFF
    print('---- 判读（累计值 = 自 bit 下载以来的总量）----')
    print(f'MMCM locked = {locked}（应为 1，0 = 时钟没锁）')
    print(f'pclk/64 累计 = {w0}（84MHz 时每秒约 131 万；0 = PCLK 从未到过 PL）')
    print(f'vsync 累计 = {w1}（出过几帧场同步）  href 累计 = {w2}（出过几行）')
    print(f'scl 累计 = {w3}（SCCB 配置应 8200+；0 = 配置从未发出）  sda 累计 = {w4}')
    print(f'数据线 见过1 = {seen1:08b}  见过0 = {seen0:08b}（左起 bit7..bit0 对应 D7..D0）')
    print('  见过1 全 0 = 8 根数据线从未翻过 1（断线/插错嫌疑）；有 0 有 1 = 线活着')

    for attempt in range(1, MAX_HEAL + 1):
        tag = '首测' if attempt == 1 else f'修复后第{attempt - 1}次复测'
        rows = measure(mm0, mm1, mm2, tag)
        d_v = sum(d[1] for d in rows)
        d_h = sum(d[2] for d in rows)
        print(f'[{tag}] 近 6s 增量：pclk/64 = {sum(d[0] for d in rows)}  vsync = {d_v}  '
              f'href = {d_h}  scl = {sum(d[3] for d in rows)}  sda = {sum(d[4] for d in rows)}')
        if d_v > 0:
            print(f'[结论] ★ 传感器在出帧（6s 内 {d_v} 个场同步，约 {d_v / 6:.1f}/s）。')
            print(f'[结论]   href 约 {d_h / 6:.0f}/s（健康基线 vsync 30/s、href 21660/s）。')
            if attempt > 1:
                print(f'[结论]   本次是 ctrl[4] 修复第 {attempt - 1} 次后复活的 —— 掷骰子被救回来了。')
            print(f'[结论]   保持这个状态，后续脚本用 noprog 接力，别重下 bit。')
            return
        if attempt < MAX_HEAL:
            print(f'[结论] 传感器没在出帧（6s 内 vsync 增量 = 0）—— 下载掷骰子掷坏了，')
            print(f'[结论]   不重烧 bit，用 ctrl[4] 重跑上电时序 + SCCB 再掷一次。')
            heal(mm3, attempt)
        else:
            print(f'[结论] 修了 {MAX_HEAL - 1} 次还是没出帧 —— 不是掷骰子问题，是硬伤：')
            print('[结论]   1) 先查 LD0 是否亮（MMCM 锁）；2) 断电重插摄像头 16 根杜邦线')
            print('[结论]   3) 重点 JA5(3V3)/JA6(GND) 供电两根、JB1(pclk)/JB4(xclk)。')
            print('[结论]   物理排查后重跑本脚本；scl/sda 增量若为 0 = SCCB 根本没到传感器。')


if __name__ == '__main__':
    main()
