# -*- coding: utf-8 -*-
"""bridge_account.py — 第十四版：桥写侧账本对账（悖论分裂器）

背景：第十三版上板 dvp_probe 健康（vsync 30/s、href 21660/s、pclk 83.3MHz、
SCCB 全写完），但桥输出只有 0.51M/s 水滴流、tuser/tlast 全 0 —— 桥 RTL
由构造保证 eol 必写（骑在每行最后一个推入拍上），矛盾只能进写侧内部对账。
本脚本读桥内 7 个影子计数器（快照制，第十四版 bit 才有，地址 0x43A60000）：

  0 c_href  href 高电平拍数（脉冲宽度真相）
  1 c_push  写侧推入数（wr_en 拍）
  2 c_eolw  eol 写入数（推入且 eol=1）
  3 c_vsync vsync 上升沿数（帧数）
  4 c_ovf   写侧溢出脉冲数
  5 c_rd    读侧弹出数（rd_en 拍）
  6 c_eolr  读侧弹出且 eol=1 数

加 probe w0(tvalid 拍)/w1(tuser 沿)/w2(tlast 沿) 交叉验证（probe 的 tlast
计数路径从没在健康传感器下验证过，桥账本是它的裁判）。

健康账本（每场）：href≈722 行×~1024 拍、push≈href、eolw=722、vsync=1、
ovf=0、c_rd≈c_push、c_eolr=c_eolw、w1Δ≈c_vsync、w2Δ≈c_eolr。

流程：
  1. ctrl[3] 脉冲复位视频域 → 写侧账本清零（读侧账本自 bit 下载起累计，取差值）
  2. 快照 A：ctrl[6] 上升沿锁存 → sel 0..6 轮读影子 + probe w0/w1/w2
  3. 等 T 秒（默认 4，argv 可改）
  4. 快照 B → 差值、速率、比值、判决

用法（串口，PYNQ 板上；bit 已下过时 noprog 接力用）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/bridge_account.py
    sudo -E PYTHONIOENCODING=gbk python3 /boot/bridge_account.py 10   # 窗口 10s

注意：脚本会先读 0x43A60000 —— 若板上是旧版 bit 没有 probe6，MMIO 直接
SIGBUS 崩掉，这本身就是"bit 不是第十四版"的报错。
"""

import sys
import time

from pynq import MMIO

CTRL_BASE = 0x43A30000   # axi_gpio_probe3 通道1：ctrl 输出寄存器
DBG_BASE  = 0x43A60000   # axi_gpio_probe6：桥账本影子读口
W0_BASE   = 0x43A40000   # axi_gpio_probe4：w0(tvalid)/w1(tuser)
W2_BASE   = 0x43A50000   # axi_gpio_probe5：w2(tlast)/w3(状态字)

CTRL_RST_VID  = 1 << 3          # ctrl[3] 复位视频域
CTRL_TRIG     = 1 << 6          # ctrl[6] 快照触发（上升沿）
CTRL_SEL_SH   = 7               # ctrl[9:7] 影子选择
CTRL_SEL_MASK = 0x7 << CTRL_SEL_SH

# (选择号, 名字, 说明)
SHADOW_NAME = (
    (0, 'c_href ', 'href 高电平拍数（pclk 域）'),
    (1, 'c_push ', '写侧推入数（wr_en 拍）'),
    (2, 'c_eolw ', 'eol 写入数（推入且 eol=1）'),
    (3, 'c_vsync', 'vsync 上升沿数（帧数）'),
    (4, 'c_ovf  ', '写侧溢出脉冲数'),
    (5, 'c_rd   ', '读侧弹出数（rd_en 拍）'),
    (6, 'c_eolr ', '读侧弹出且 eol=1 数'),
)

M32 = 0x100000000     # 32 位计数器回绕模


def rate(d, t):
    return '%.1f' % (d / t)


def main():
    win = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    mm3 = MMIO(CTRL_BASE, 0x10000)
    mm6 = MMIO(DBG_BASE, 0x10000)
    mm4 = MMIO(W0_BASE, 0x10000)
    mm5 = MMIO(W2_BASE, 0x10000)

    c0 = mm3.read(0x00, 4)
    print('[账本] ctrl 初值 0x%08x；ctrl[3] 脉冲 0.3s 复位视频域（写侧账本清零）' % c0)
    mm3.write(0x00, c0 | CTRL_RST_VID)
    time.sleep(0.3)
    c0 &= ~CTRL_RST_VID
    mm3.write(0x00, c0)
    print('[账本] 等 0.5s：alive 重新鉴定 + 流稳定...')
    time.sleep(0.5)

    def snap(tag):
        # 快照：trig 0->1 锁存全部计数器 -> sel 轮读 7 个影子
        mm3.write(0x00, c0 | CTRL_TRIG)
        time.sleep(0.02)                    # 写侧锁存 + CDC 传播
        mm3.write(0x00, c0)                 # trig 回 0，备好下一次上升沿
        vals = []
        for sel, _, _ in SHADOW_NAME:
            mm3.write(0x00, (c0 & ~CTRL_SEL_MASK) | (sel << CTRL_SEL_SH))
            vals.append(mm6.read(0x00, 4))
        w0 = mm4.read(0x00, 4)
        w1 = mm4.read(0x08, 4)
        w2 = mm5.read(0x00, 4)
        w3 = mm5.read(0x08, 4)
        print('[账本] 快照 %s 影子：%s' % (tag, ' '.join('%10d' % v for v in vals)))
        print('[账本] 快照 %s probe：w0=%10d w1=%d w2=%d w3=0x%08x'
              % (tag, w0, w1, w2, w3))
        return vals, (w0, w1, w2, w3)

    a, pa = snap('A')
    print('[账本] 观测窗口 %.1fs...' % win)
    time.sleep(win)
    b, pb = snap('B')

    da = [(b[i] - a[i]) % M32 for i in range(7)]
    dw = [(pb[i] - pa[i]) % M32 for i in range(4)]

    print('')
    print('==== 账本对账（窗口 %.1fs）====' % win)
    for (_, name, desc), d in zip(SHADOW_NAME, da):
        print('  %s %10d   %8s/s   %s' % (name, d, rate(d, win), desc))
    print('  probe w0(tvalid) %9d  %8s/s   w1(tuser 沿)=%d  w2(tlast 沿)=%d'
          % (dw[0], rate(dw[0], win), dw[1], dw[2]))

    print('')
    print('==== 比值判决 ====')
    ch, cp, ce, cv, co, cr, cer = da
    verdicts = []

    # 每场行数 / 每行拍数（都要求帧数 > 0）
    if cv > 0:
        lpf = ce / float(cv)
        print('  每场 eol 数 = %d / %d = %.1f（健康 ≈ 722 行/场）' % (ce, cv, lpf))
        if ce > 0:
            bpl = ch / float(ce)
            print('  每行 href 拍数 = %d / %d = %.1f（健康 ≈ 1024 拍/行）' % (ch, ce, bpl))
            if bpl < 100:
                verdicts.append(
                    '★ href 脉冲宽度损坏：每行只有 %.0f 拍（健康 ~1024）。\n'
                    '  病灶在传感器输出层（坏态变体），代码改不了 —— 转硬件：\n'
                    '  全断电 + 重插 8 根数据线/href 线，或重下 bit 掷骰子。' % bpl)
    else:
        print('  整个窗口 vsync 一次没来：传感器场同步死了（查 vsync 线/重下 bit）')

    if ch > 0:
        pr = cp / float(ch)
        print('  推入/href 比 = %d / %d = %.3f（健康 ≈ 1，丢行时略小）' % (cp, ch, pr))
        if pr < 0.5:
            verdicts.append('★ push 远少于 href 高拍：dropping/wr_rst_busy 吞了大量拍 —— 查丢行逻辑与 c_ovf')
    else:
        print('  整个窗口 href 一次没高：行同步死了（查 href 线/重下 bit）')

    if co > 0:
        print('  写侧溢出 %d 次（健康应为 0）' % co)
        verdicts.append('★ 写侧溢出 %d 次：FIFO 满过 —— 读侧没跟上，查 tready/VDMA' % co)

    if ce > 0:
        ep = cer / float(ce)
        print('  eol 通过率 = 读侧 %d / 写侧 %d = %.3f（健康 = 1）' % (cer, ce, ep))
        if ep < 0.9:
            verdicts.append(
                '★ eol 写入 %d > 0 但读侧只弹出 %d：FIFO 搬运丢了 eol 字（或读侧账本\n'
                '  计数路径有 bug）—— 病灶在 FIFO/CDC，改桥。' % (ce, cer))
        if ep > 0.9 and dw[2] == 0:
            verdicts.append(
                '★ 桥读侧账本有 eol（%d）但 probe w2 一个 tlast 没数到：\n'
                '  axis_probe 的 tlast 计数路径有 bug —— 病灶在探针，改 axis_probe。' % cer)
    else:
        if cp > 0:
            verdicts.append(
                '★ 推入 %d > 0 但 eol 写入 = 0：桥 RTL 内 eol 生成失效\n'
                '  （静态分析漏掉的逻辑 bug）—— 病灶在桥写侧，改 dvp2axis_bridge。' % cp)

    # probe 交叉验证
    if dw[1] > 0 and cv > 0:
        print('  probe tuser 沿 / vsync 帧 = %d / %d = %.1f（健康 ≈ 1，每帧一个 SOF）'
              % (dw[1], cv, dw[1] / float(cv)))

    if not verdicts:
        print('  写侧账本全部健康：eol/sof 生成正常、FIFO 搬运完整。')
        print('  ★ 若 VDMA 仍 60s 无帧，病灶不在桥 —— 查 VDMA 配置')
        print('    （FSYNCSEL=2 / 参数寄存器 RS=1 后重写 / IRQThreshold=0xFF）。')
    else:
        for v in verdicts:
            print('')
            print(v)


if __name__ == '__main__':
    main()
