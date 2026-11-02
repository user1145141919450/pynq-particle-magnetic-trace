# -*- coding: utf-8 -*-
"""maprw_test.py — map_ram 的 PS 侧读写覆盖测试（**不涉及 vision_core**）

为什么要有这个测试：vision_diag 的 C) 项没过时，`stat` 说"写了 5760 字"、而读回的
map 只有前 ~1440 个字非 0。**这两个数字不能同时为真**，所以必须先把"写错"和"读错"
分开 —— 办法是让 PL 完全不参与：

    1. 冻结 vision_core（ctrl[0]=1）→ 它绝对不写 map
    2. PS 往整张 map（5760 个字 = 字节 0..23039）写已知图案
    3. PS 整体读回，逐字比对

判定：
    全覆盖 → 读写通路都好，那 1440 之谜在**写侧**（vision_core 的地址/计数器）
    只有前 1440 个能读回 → **病灶在 map_ram / axi_bram_ctrl / mmap 这一侧**，
        vision_core 是无辜的（C 项失败是读回假象）

★ 1440 这个数值得盯：v1 的病历写的正是"写地址只覆盖前 1440 个字"，连数都一样。
   1440 字 = 5760 字节 = 整个 map 的 1/4。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/maprw_test.py
    ... --keep   测完不撤销冻结（默认恢复 freeze=0）
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
MAP_BYTES = 23040
WORDS = MAP_BYTES // 2

# 图案：每个字 = 0xA000 | 序号低 12 位（一眼看出是哪个字被读成了什么）
def pattern(n):
    return (0xA000 | (n & 0x0FFF)).astype(np.uint16)


def main():
    keep = '--keep' in sys.argv
    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc:
        # 1) 冻结 vision_core：ctrl[0]=1
        c0 = mc.rd_u32(0x00)
        mc.wr_u32(0x00, c0 | 0x01)
        time.sleep(0.3)
        print('[1] ctrl 0x%02x -> 0x%02x（freeze=1，vision_core 停止写 map）'
              % (c0, c0 | 0x01))

        # 2) 写图案（view_u16 是可写的零拷贝视图，赋值即写进 /dev/mem）
        pat = pattern(np.arange(WORDS))
        mm.view_u16(0, WORDS)[:] = pat
        print('[2] 已写入 %d 个字（0x0000 起）' % WORDS)

        # 3) 读回
        raw = np.frombuffer(mm.rd_u8_copy(0, MAP_BYTES), np.uint16)
        nz = int((raw != 0).sum())
        print('[3] 读回 %d 个字，其中非 0 的 %d 个（%.1f%%）' % (raw.size, nz, 100.0 * nz / raw.size))

        ok = raw == pat
        print('[4] 与写入一致的字：%d / %d（%.2f%%）' % (int(ok.sum()), WORDS, 100.0 * ok.mean()))

        # 逐"块行"（每 64 字一块行）看覆盖
        print('[5] 每 64 个字（=1 个块行）里的相符数（满 = 64）：')
        for r0 in range(0, 90, 10):
            row = []
            for b in range(r0, min(r0 + 10, 90)):
                seg = ok[b * 64:(b + 1) * 64]
                row.append('%2d' % int(seg.sum()))
            print('     块行 %2d~%2d: %s' % (r0, min(r0 + 9, 89), ' '.join(row)))

        bad = np.nonzero(~ok)[0]
        if bad.size:
            print('[6] 第一个不符的字号 = %d（字节偏移 0x%04X）；不符总数 %d'
                  % (int(bad[0]), int(bad[0]) * 2, int(bad.size)))
            print('     前 8 个不符处（字号: 写入值 -> 读回值）：')
            for i in bad[:8]:
                print('       %5d: 0x%04X -> 0x%04X' % (i, pat[i], raw[i]))
            # 读回为 0 的最大字号 —— 覆盖到哪里
            zero = np.nonzero(raw == 0)[0]
            if zero.size:
                print('     读回为 0 的字：%d 个，最大字号 %d（若"全 0 从某处开始"= 地址没接上）'
                      % (int(zero.size), int(zero.max())))
        else:
            print('  => 全 5760 个字读写一致 ✓ 读写通路完全正常 —— '
                  '1440 之谜在**写侧**（vision_core 地址/计数器），继续查 RTL')

        # 4) 撤销冻结
        if not keep:
            mc.wr_u32(0x00, c0)
            print('[4] 已恢复 ctrl = 0x%02x（freeze=0）' % c0)
        else:
            print('[4] --keep：保持冻结')
    return 0


if __name__ == '__main__':
    sys.exit(main())
