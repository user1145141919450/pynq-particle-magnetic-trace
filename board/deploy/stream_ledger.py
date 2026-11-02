# -*- coding: utf-8 -*-
"""stream_ledger.py — 桥账本 + VDMA 真状态 + 真的在收帧，三者同时测

为什么要写这个脚本（两个已经踩过的坑，都记在这儿免得再踩）：

坑一：**bridge_account.py 测账本时 VDMA 是停的**。它先脉冲 ctrl[3] 把写侧账本
      清零，然后干等 4 秒采样 —— 窗口里没有任何消费者。没有消费者，桥的 FIFO
      约 0.18ms 就灌满（8192 拍 / 44.5M 拍每秒），此后**每一次推入都溢出**。
      所以它报出来的"c_ovf = c_push = 44.5M/s、c_rd = 0"是**没有消费者的正常
      反应，不是故障**。（实测再确认：本脚本跑到 c_rd > 0、c_ovf = 0 时，
      说明桥和 FIFO 本来就是好的。）账本必须**在 VDMA 真的在收帧的时候**测。

坑二：**DMASR 不是 0x14**。AXI VDMA 的 S2MM 寄存器在 0x30/0x34
      （见 vdma_capture.py 的常量，那份是照 rfs.vhd 核过的），0x14 是保留/无关
      寄存器。之前 stream_rate.py 里把它当 DMASR，读出 0x00f74000 还当成
      "帧计数停在 247" —— 白追了一轮。本脚本只用 0x30/0x34。

另外，**驱动一退出就 stop()**（DMACR 清 0），所以任何"事后空手去读账本"的
诊断都会看到坑一的假象。要测连接器，就得连着消费者一起测。

流程：
  1. _make_cap() + _init_video()（★ 顺序照抄 live_stream：start() -> ctrl[3]
     脉冲 -> 清瞬态错误 -> **立刻**抓一帧。空转一会儿就再也收不到帧，是老坑）
  2. 快照 A：ctrl[6] 上升沿锁存桥账本 -> sel 0..6 轮读 + probe + 真 DMASR
  3. 连抓 N 帧（每帧 1.5s 超时），逐帧报告成/败
  4. 快照 B -> 差值、速率、比值、判决

用法（板上，bit 已下过时用 --nobit 接力）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/stream_ledger.py
    ... --n 20        抓帧次数
    ... --nobit       不重下 bit
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np
from pynq import MMIO

CTRL_BASE = 0x43A30000
DBG_BASE = 0x43A60000
W0_BASE = 0x43A40000
W2_BASE = 0x43A50000
VCTRL = 0x43B10000
VSTAT = 0x43B20000

CTRL_TRIG = 1 << 6
CTRL_SEL_SH = 7
CTRL_SEL_MASK = 0x7 << CTRL_SEL_SH

VDMA = 0x43000000
DMACR = 0x30                     # ★ 真·S2MM_DMACR（不是 0x14）
DMASR = 0x34                     # ★ 真·S2MM_DMASR
SR_HALTED = 1 << 0
SR_ERR_MASK = 0xFF0

SHADOW_NAME = ['c_href', 'c_push', 'c_eolw', 'c_vsync', 'c_ovf', 'c_rd', 'c_eolr']
M32 = 0x100000000


def decode_w3(w3):
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
    n = 20
    win = 10.0
    nobit = '--nobit' in sys.argv
    quiet = '--quiet' in sys.argv
    for i, a in enumerate(sys.argv):
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
        if a == '--win' and i + 1 < len(sys.argv):
            win = float(sys.argv[i + 1])

    BIT = '/boot/ov5640_m1a_wrapper.bit'
    if nobit:
        print('[加载] --nobit：沿用当前 PL')
    else:
        # ★ 先用 Bitstream 直下，**不用 Overlay**。为什么（2026-09-24 深夜实测）：
        #   `Overlay(BIT)` 在本板的 hwh 上会**段错误**（SIGSEGV，不是 Python 异常
        #   —— 所以 try/except 拦不住，脚本连一行输出都来不及打就没了，只留下
        #   bash 的 "Segmentation fault" 和 dmesg 里的 apport）。而本工程这一整套
        #   脚本全是**裸 mmap 读写寄存器**，根本不需要 Overlay 解析出来的 IP 字典；
        #   `allocate()` 靠 Bitstream.download() 写下的全局状态文件照样能用
        #   （这正是采集链一直在用的路径）。所以直下既够用又绕开了那个坑。
        from pynq import Bitstream
        Bitstream(BIT).download()
        print('[加载] bit 已下载（Bitstream 直下；Overlay 在本板 hwh 上会段错误，见注释）')

    # ---- 起 VDMA 并**立刻**抓热帧：顺序不能改（见模块头 + _init_video 注释）----
    from ball_detect import _make_cap, _init_video
    cap = _make_cap()
    _init_video(cap)

    mm3 = MMIO(CTRL_BASE, 0x10000)
    mm6 = MMIO(DBG_BASE, 0x10000)
    mm4 = MMIO(W0_BASE, 0x10000)
    mm5 = MMIO(W2_BASE, 0x10000)
    mmv = MMIO(VCTRL, 0x1000)
    mms = MMIO(VSTAT, 0x1000)
    mmd = MMIO(VDMA, 0x10000)

    # 只想动 trig/sel 两位，其余位（含 ctrl[3]）保持原样 —— **不脉冲 ctrl[3]**，
    # 否则又把账本清零、又要把视频域重新鉴定 43.7ms，等于自找坑一。
    c0 = mm3.read(0x00, 4) & ~CTRL_TRIG

    def vdma_regs():
        cr = mmd.read(DMACR, 4)
        sr = mmd.read(DMASR, 4)
        return cr, sr

    def snap(tag):
        mm3.write(0x00, c0 | CTRL_TRIG)
        time.sleep(0.02)
        mm3.write(0x00, c0)
        vals = []
        for sel in range(7):
            mm3.write(0x00, (c0 & ~CTRL_SEL_MASK) | (sel << CTRL_SEL_SH))
            vals.append(mm6.read(0x00, 4))
        # 7 号槽（第十五版才有）：FIFO 内部状态实时字。旧 bit 读出来是 0 —— 那正好
        # 说明板上还是老 bit，别拿它的 0 当"水位为 0"。
        mm3.write(0x00, (c0 & ~CTRL_SEL_MASK) | (7 << CTRL_SEL_SH))
        d7 = mm6.read(0x00, 4)
        mm3.write(0x00, c0 & ~CTRL_SEL_MASK)
        print('         FIFO 内部：水位=%d full=%d wr_rst_busy=%d empty=%d rd_rst_busy=%d '
              'sof_armed=%d dropping=%d 看门狗触发过=%d'
              % (d7 & 0x3FFF, (d7 >> 14) & 1, (d7 >> 15) & 1, (d7 >> 16) & 1,
                 (d7 >> 17) & 1, (d7 >> 18) & 1, (d7 >> 19) & 1, (d7 >> 31) & 1))
        if d7 == 0:
            print('         （全 0 —— 板上可能还是老 bit，7 号槽恒 0）')
        w3 = mm5.read(0x08, 4)
        cr, sr = vdma_regs()
        d = dict(vals=vals, w0=mm4.read(0x00, 4), w1=mm4.read(0x08, 4),
                 w2=mm5.read(0x00, 4), w3=w3, cr=cr, sr=sr,
                 vf=mmv.read(0x08, 4), stat=mms.read(0x00, 4),
                 stat2=mms.read(0x08, 4))
        print('[快照 %s] 账本 %s' % (tag, ' '.join('%11d' % v for v in vals)))
        print('         probe w0(tvalid)=%d w1(tuser)=%d w2(tlast)=%d' % (d['w0'], d['w1'], d['w2']))
        print('         %s' % decode_w3(w3))
        print('         VDMA DMACR=0x%08x DMASR=0x%08x  halted=%d err=0x%03x 帧计数=%d'
              % (cr, sr, sr & 1, (sr & SR_ERR_MASK) >> 4, (sr >> 16) & 0xFF))
        print('         vision_core frame_cnt=%d stat=0x%08x stat2=0x%08x'
              % (d['vf'], d['stat'], d['stat2']))
        return d

    A = snap('A')
    print('')
    if quiet:
        # ★ 只睡不动：PS 完全不碰 DDR（不抓帧、不打印、不 memcpy）。
        # 目的：把"PS 抢 DDR 带宽"这个变量摘掉，看丢行风暴是不是它引起的。
        # 判据：醒来后 c_eolw/c_vsync 是否回到 ~720（健康）。是 → 反压来自 PS；
        #       否 → 反压是常态，与 PS 无关（那要改丢行机制/水位）。
        t0 = time.time()
        print('==== 安静窗口 %.1fs：PS 只睡，绝不动 DDR = ====' % win)
        time.sleep(win)
        dt = time.time() - t0
        B = snap('B')
        da = [(B['vals'][i] - A['vals'][i]) % M32 for i in range(7)]
        print('')
        print('==== 安静窗口对账（%.2fs）====' % dt)
        for nm, d in zip(SHADOW_NAME, da):
            print('  %-7s %12d  %10.0f/s' % (nm, d, d / dt))
        ch, cp, ce, cv, co, cr, cer = da
        if cv > 0:
            print('  ★ 每场 eol 写入 = %d / %d = %.1f（健康 = 720）' % (ce, cv, ce / float(cv)))
            print('  ★ 推入/href 比 = %.3f（健康 ≈ 1；越小 = dropping 吃得越多）'
                  % (cp / float(ch) if ch else 0))
        if cv > 0 and ce / float(cv) > 600:
            print('  → PS 一动不动的窗口里行是齐的：反压来自 PS 的 DDR 占用，不是桥')
        elif cv > 0:
            print('  → PS 完全不动也丢行：反压是常态 —— 要改丢行机制（见结论）')
        return 0

    print('==== 连抓 %d 帧（每帧 1.5s 超时；期间 VDMA 真的在收）= ====' % n)
    ok = 0
    t0 = time.time()
    for k in range(n):
        try:
            idx, bgr = cap.bgr_frame(timeout=1.5)
            ok += 1
            print('  帧 %2d ✓ buf%d 均值 %.1f  (+%.2fs)'
                  % (k + 1, idx, float(np.asarray(bgr).mean()), time.time() - t0))
        except Exception as e:
            print('  帧 %2d ✗ %s' % (k + 1, str(e).replace('\n', ' ')[:160]))
    dt = time.time() - t0
    B = snap('B')

    da = [(B['vals'][i] - A['vals'][i]) % M32 for i in range(7)]
    dw = [(B[k] - A[k]) % M32 for k in ('w0', 'w1', 'w2')]

    print('')
    print('==== 账本对账（窗口 %.2fs，期间成功抓帧 %d/%d）====' % (dt, ok, n))
    for nm, d in zip(SHADOW_NAME, da):
        print('  %-7s %12d  %10.0f/s' % (nm, d, d / dt))
    print('  probe   w0(tvalid)=%d (%.0f/s)  w1(tuser 沿)=%d  w2(tlast 沿)=%d'
          % (dw[0], dw[0] / dt, dw[1], dw[2]))
    print('  实测帧率 %.1f fps' % (ok / dt))

    ch, cp, ce, cv, co, cr, cer = da
    print('')
    print('==== 判决 ====')
    v = []
    if ch > 0 and ce > 0:
        print('  每行 href 拍数 = %d / %d = %.1f（健康 ≈ 1024）' % (ch, ce, ch / float(ce)))
    if ch > 0:
        print('  推入/href 比 = %.3f（健康 ≈ 1）' % (cp / float(ch)))
    if cv > 0:
        print('  每场 eol 写入 = %d / %d = %.1f（健康 = 720）' % (ce, cv, ce / float(cv)))
        print('  每场 tuser 沿 = %d / %d = %.1f（健康 = 1）' % (dw[1], cv, dw[1] / float(cv)))
    if cr > 0:
        print('  读侧弹出/写侧推入 = %d / %d = %.3f（健康 ≈ 1）' % (cr, cp, cr / float(cp)))
        print('  eol 通过率 = %d / %d = %.3f（健康 = 1）' % (cer, ce, cer / float(ce) if ce else 0))
    if co == 0 and cr > 0:
        print('  ★ 溢出 0 次、读侧在弹 —— 桥 + FIFO 在"有消费者"时完全健康：')
        print('    之前 c_ovf=c_push 的账本是"没消费者"的假象（见模块头 坑一）。')
    if co > 0:
        v.append('写侧溢出 %d 次：读侧跟不上 —— 查 c_rd 是否同步为 0' % co)
    if cr == 0 and cp > 0:
        v.append('有推入但读侧 0 弹出：rd_en = ~empty & ~rd_rst_busy & tready，'
                 '三者之一恒 0 —— 看 w3 的 live tready 与 ovf_live')
    if ok == 0:
        v.append('一帧都没收到：见上面 DMASR 的错误位（fsize_err 说明帧尺寸不符）')
    if v:
        for x in v:
            print('  ★ %s' % x)
    return 0


if __name__ == '__main__':
    sys.exit(main())
