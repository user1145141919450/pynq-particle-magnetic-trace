# -*- coding: utf-8 -*-
"""bram_depth.py — 测 map BRAM 的**真实深度**（抓地址别名）

为什么单靠 bram_test 不够：写一个地址再读**同一地址**，别名是看不出来的
（0x7FFC 写进去、再读 0x7FFC，哪怕实际只有 1440 字深、地址回绕了，读回的
仍是刚写的那份）。必须**写两个若容量不足就会撞车的地址**，看它们是否互相覆盖。

先冻结 vision_core（ctrl[0]=freeze），否则它每帧都会把我的测试值覆盖掉。

用法（板上）：sudo -E PYTHONIOENCODING=utf-8 python3 /boot/bram_depth.py
"""

import sys

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

MAP_BASE = 0x43B00000
MAP_SIZE = 0x8000
CTRL_BASE = 0x43B10000

# 若深度为 1024 字 → 2048 会撞 0；若 1440 → 2880 撞 0；若 2048 → 4096 撞 0；
# 若 4096 → 8192(=0x8000 越界) 撞 0。各档一次测完。
PROBE_WORDS = [0, 1024, 1440, 2048, 2880, 4096, 5760, 8192]


def main():
    with FastMem(MAP_BASE, MAP_SIZE) as m, FastMem(CTRL_BASE, 0x1000) as mc:
        # 冻结 vision_core（ctrl[0]=1），别再让它改 map
        mc.wr_u32(0x00, 0x1)
        print('[准备] 已冻结 vision_core（ctrl[0]=1）')

        # 每个探针字写一个唯一值
        for i, w in enumerate(PROBE_WORDS):
            if w * 4 < MAP_SIZE:
                m.wr_u32(w * 4, 0xAA000000 | i)
        print('[写入] 探针字: %s' % PROBE_WORDS)

        print('[读回] 字号 -> 读到的值（应等于各自的 0xAA0000<i>）')
        seen = {}
        alias = []
        for i, w in enumerate(PROBE_WORDS):
            if w * 4 >= MAP_SIZE:
                continue
            v = m.rd_u32(w * 4)
            exp = 0xAA000000 | i
            tag = 'ok' if v == exp else 'ALIAS!'
            if v != exp:
                alias.append((w, v))
            print('  字 %5d -> 0x%08x  期望 0x%08x  [%s]' % (w, v, exp, tag))
            seen[w] = v
        if alias:
            print('[结论] 存在地址别名 → 实际深度小于 32KB：')
            for w, v in alias:
                print('    字 %d 读回 0x%08x（被别的地址覆盖）' % (w, v))
        else:
            print('[结论] 无别名 → 32KB 深度正常')
        # 解开冻结
        mc.wr_u32(0x00, 0x0)
        print('[收尾] 已解冻')


if __name__ == '__main__':
    main()
