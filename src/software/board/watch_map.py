# -*- coding: utf-8 -*-
"""watch_map.py — 这张 map 到底还在不在被更新？（C 项排障的关键一刀）

为什么要这一刀：vision_diag 里 A/B/D 全过（`stat` = 720 行/5760 字、`errs`=0、
`frame_cnt` 涨 29fps），可 map 只有前 23 个块行有内容、后面 67 行全 0。**这两组事实
不能同时为真**，除非其中一组是**陈旧的**：

  (a) map 还在被写，只是写入提前停住 → 那 map 每次读都该有**新的变化**
  (b) map 早就不动了（视核卡在 resync / 写入停住），现在读到的全是**旧数据** →
      那么 `stat` 里那个"上一帧 5760 字"也是**旧快照**（它只在帧结束那一刻更新）

区分办法只有一个：**隔一段时间读两次，看 map 变不变。**
  - 变了 → (a)：写入中途停，去查写入条件/地址
  - 没变 → (b)：视核根本没在写，A/B 项全是被陈旧状态字骗过的假通过 ★

★ 这条也是本工程的纪律：**"状态字说对了"不等于"东西在动"**。判据要选"必须一直在变"
的那种（frame_cnt 涨、map 的内容变），不能选"看起来正确"的常数。

流程：下 bit → _make_cap + _init_video（标准启动序列，**全程不冻结**）→
隔 1 秒读 4 次 map，每次报：非零块行数、全 0 起始行、map 与上一次相比变了几个字。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/watch_map.py
    ... --nobit   沿用当前 PL（不重下 bit）
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
MAP_BYTES = 23040
BW, BH = 128, 90


def main():
    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')

    from ball_detect import _make_cap, _init_video
    cap = _make_cap()
    _init_video(cap)

    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc:
        print('[配置] ctrl=0x%02x（不冻结、不清错，就让它跑）' % mc.rd_u32(0x00))
        prev = None
        for k in range(4):
            a = mc.rd_u32(0x08)
            mp = mm.rd_u16_copy(0, MAP_BYTES // 2).reshape(BH, BW)
            b = mc.rd_u32(0x08)
            nz = (mp != 0).any(axis=1)
            nzr = np.nonzero(nz)[0]
            zero = np.nonzero(~nz)[0]
            chg = -1 if prev is None else int((mp != prev).sum())
            print('')
            print('[读 %d] frame_cnt %d -> %d（%s）'
                  % (k + 1, a, b, '一致' if a == b else '★读期间变了'))
            print('       非零块行 %s..%s（共 %d 行）；全 0 起始行 %s'
                  % (nzr.min() if nzr.size else '-', nzr.max() if nzr.size else '-',
                     nzr.size, zero.min() if zero.size else '无'))
            print('       与上次相比变化的字：%d' % chg)
            print('       map 校验和 = %d' % int(mp.astype(np.uint32).sum()))
            prev = mp
            time.sleep(1.0)

        print('')
        print('=' * 60)
        if prev is None:
            return 1
        print('判读：')
        print('  变化的字一直 >0 → (a) map 在被更新，是"写入提前停住"')
        print('  变化的字 = 0     → (b) map 是死的（旧数据）—— A/B 项是被陈旧')
        print('                       状态字骗过的假通过，先治"视核在不在干活"')
    return 0


if __name__ == '__main__':
    sys.exit(main())
