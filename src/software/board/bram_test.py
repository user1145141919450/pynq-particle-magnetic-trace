# -*- coding: utf-8 -*-
"""bram_test.py — 单独测 map BRAM 的读/写通路与真实容量

背景：vision_read 读回的缩略图几乎全 0，而同一时刻的全分辨率帧是有内容的
（均值 32.7）。要先分清是"BRAM 没这么大/没写进去"还是"RTL 的写有问题"。

做法：PS 通过 axi_bram_ctrl 写几个已知模式到不同偏移，再读回。
    @0x0000     一个字（最低地址）
    @0x1000     中间
    @0x7FFC     最后一个字（32KB 边界）
若三个都能读回 -> 32KB 全在，容量没问题；若高地址读回错/别名 -> 容量小于 32KB。

用法（板上）：sudo -E PYTHONIOENCODING=utf-8 python3 /boot/bram_test.py
"""

import sys

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

MAP_BASE = 0x43B00000
MAP_SIZE = 0x8000          # 32KB（BD 里 assign_bd_address 给的 range）

PATS = [(0x0000, 0xDEADBEEF), (0x1000, 0xABCD1234),
        (0x4000, 0x5555AAAA), (0x7FFC, 0x12345678)]


def main():
    with FastMem(MAP_BASE, MAP_SIZE) as m:
        # ---- 写前先看现状（vision_core 每帧都在写，所以这里可能是它的数据）----
        head = m.view_u32(0, 8).copy()
        print('[写前] 前 8 个字：' + ' '.join('%08x' % v for v in head))
        tail = m.view_u32(0x7FF0 // 4, 4).copy()
        print('[写前] 末 4 个字：' + ' '.join('%08x' % v for v in tail))
        mid = m.view_u32(0x4000 // 4, 4).copy()
        print('[写前] 0x4000 处 4 字：' + ' '.join('%08x' % v for v in mid))

        # ---- 写已知模式 ----
        for off, val in PATS:
            m.wr_u32(off, val)
        ok = 0
        print('[写后] 逐点核对：')
        for off, val in PATS:
            got = m.rd_u32(off)
            flag = 'ok ' if got == val else 'FAIL'
            if got == val:
                ok += 1
            print('  @0x%04X 写入 %08x -> 读回 %08x  [%s]' % (off, val, got, flag))
        print('[结论] %d/%d 个点读回一致' % (ok, len(PATS)))
        if ok == len(PATS):
            print('  => 32KB 都在、读写通路正常 → 问题在 RTL 的写侧（vision_core）')
        else:
            print('  => BRAM 容量或通路有问题（高地址读不回 = 实际深度小于 8192 字）')


if __name__ == '__main__':
    main()
