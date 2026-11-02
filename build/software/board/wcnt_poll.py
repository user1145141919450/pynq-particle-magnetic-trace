# -*- coding: utf-8 -*-
"""wcnt_poll.py — 盯住 stat2 里的**实时**字段：本帧已写字数 / 行尾 bcnt / errs

为什么必须看这个：`stat` = {上一帧行数, 上一帧写字数} **只在帧真正结束那一刻更新**
（`blk_row == BRW_LAST`）。所以它可能是几小时前的快照 —— 今晚就因此被"假通过"骗了一轮
（`stat=0x02D01680` 看着完美，其实视核当时一个字都没写）。

而 `stat2` 的低 15 位是 **wcnt = 本帧实时已写字数**，每写一个字就变 —— 它是"活着"的量。

读法（实测已知：地址只覆盖前 1440 个字）：
  wcnt 在 0..1440 之间来回跳   → 每帧照常结束，只是**只写了 1440 个字**就够到帧尾
                                 ⇒ 行/列计数走得太快（8 行才该算一块行）
  wcnt 爬到 ~1440 后**停住不动** → 写入中途停死（帧结束不了）
  wcnt 能爬到 5760           → 写入其实是满的，"1440"是读侧的假象

用法（板上，只读，不动任何状态）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/wcnt_poll.py [--n 40]
"""

import sys
import time

sys.path.insert(0, '/boot')

from pynq import MMIO


def main():
    n = 200
    for i, a in enumerate(sys.argv):
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])

    # 先把采集链起起来（刚重启的板子上 PL 是空的、VDMA 也没跑；
    # 流不走的话视核压根不会写，测出来又是个"没在跑"的假象）
    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')
    from ball_detect import _make_cap, _init_video
    _init_video(_make_cap())
    print('[启动] 采集链已按标准顺序起来')

    mms = MMIO(0x43B20000, 0x1000)
    mmv = MMIO(0x43B10000, 0x1000)

    print('读 stat2 的实时字段（每 5ms 一次，共 %d 次 ≈ %.1fs）'
          % (n, n * 0.005))
    print('  %-6s %-8s %-6s %-6s' % ('序号', 'wcnt', 'bcnt', 'errs'))
    prev = None
    seq = []
    for k in range(n):
        st2 = mms.read(0x08)
        wcnt = st2 & 0x7FFF
        bcnt = (st2 >> 21) & 0x7FF
        errs = (st2 >> 15) & 0x3F
        seq.append(wcnt)
        mark = ''
        if prev is not None and wcnt < prev:
            mark = '  ← 回卷（帧边界）'
        print('  %-6d %-8d %-6d 0x%02X%s' % (k, wcnt, bcnt, errs, mark))
        prev = wcnt
        time.sleep(0.005)

    print('')
    mx = max(seq)
    print('wcnt 最大值 = %d（满帧应到 5760）' % mx)
    if mx > 5000:
        print('  → 写入是满的，1440 是**读侧/地址**的假象')
    elif mx < 100:
        print('  → 几乎没在写（视核没跑或写入被禁）')
    elif any(seq[i + 1] < seq[i] for i in range(len(seq) - 1)):
        print('  → wcnt 在 0..%d 之间回卷 = 帧照常结束，但**每帧只写 %d 个字**：'
              % (mx, mx))
        print('     行/列计数比真实快 ~%.1f 倍（5760/%d）—— 去查 row_in_blk / blk_row'
              % (5760.0 / mx, mx))
    else:
        print('  → wcnt 爬到 %d 后不动 = **写入中途停死**（帧结束不了）' % mx)
        print('     去查"什么让列完成条件 bcnt[3:0]==15 或 tlast 不再成立"')
    # 附：frame_cnt 与 stat 的陈旧性对照
    print('')
    print('frame_cnt = %d（注意：它涨不代表帧完成，见脚本头注释）' % mmv.read(0x08))
    print('stat  = 0x%08X（上一帧快照，可能是旧的）' % mms.read(0x00))
    return 0


if __name__ == '__main__':
    sys.exit(main())
