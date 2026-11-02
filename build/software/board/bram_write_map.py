# -*- coding: utf-8 -*-
"""bram_write_map.py — 测出 vision_core **究竟写了哪些字**（一刀切开两种可能）

两种可能无法用"读内容"区分：
  A) 视核只写了前 1440 个字（后半根本没写）
  B) 视核写满了 5760 个字，但后半的值本来就是 0（暗场景把块平均压到 0）
做法：先把整张图用 PS 填成已知图案 → 让视核跑一帧 → 再读回。
  A 情况：超过 1440 的字**仍是我的图案**
  B 情况：全被覆盖成 0
★ 关键：测试期间必须让视核真跑起来（freeze=0），且跑够一帧（>35ms）。

用法（板上）：sudo -E PYTHONIOENCODING=utf-8 python3 /boot/bram_write_map.py
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

MAP_BASE = 0x43B00000
MAP_SIZE = 0x8000
CTRL_BASE = 0x43B10000
NW = 5760                      # 缩略图占用的字数（90 块行 x 64 字）


def main():
    with FastMem(MAP_BASE, MAP_SIZE) as m, FastMem(CTRL_BASE, 0x1000) as mc:
        # 1) 冻结视核
        mc.wr_u32(0x00, 0x1)
        time.sleep(0.05)

        # 1b) ★ 先 soft_rst（bit1）清 sticky 错误位 + 让模块 resync 到下一个帧首。
        #     为什么必须加这一步：若上一帧已经"字节相位错位"（errs bit0 亮），
        #     列完成条件 bcnt[3:0]==15 会在错的列上触发 → 测出来的地址图是坏的相位
        #     下的地址图，不干净。soft_rst 之后从 tuser 重新开工，相位是确定的。
        #     冻结期间软复位不影响 map 内容（不写），所以图案填在后面。
        mc.wr_u32(0x00, 0x1 | 0x2)
        time.sleep(0.05)
        mc.wr_u32(0x00, 0x1)
        time.sleep(0.10)                 # 等 ≥1 帧，resync 完成（下一帧首开工）

        # 2) 整图填图案（numpy 直写 mmap，快）
        pat = (0xDEAD0000 | np.arange(NW, dtype=np.uint32)).astype(np.uint32)
        m.view_u32(0, NW)[:] = pat
        time.sleep(0.05)
        back = m.view_u32(0, NW).copy()
        print('[填充] 写入 %d 字图案，立即读回一致 %d 字' %
              (NW, int((back == pat).sum())))

        # 3) 放行视核，跑一帧多（30fps -> 33ms/帧）
        mc.wr_u32(0x00, 0x0)
        time.sleep(0.30)
        c = mc.rd_u32(0x08)
        time.sleep(0.10)
        c2 = mc.rd_u32(0x08)
        mc.wr_u32(0x00, 0x1)                     # 再冻结
        time.sleep(0.05)
        print('[跑帧] 放行期间 frame_cnt %d -> %d（涨了才说明真在跑）' % (c, c2))

        # 4) 读回，看哪些字被视核覆盖了
        now = m.view_u32(0, NW).copy()
        survived = (now == pat)
        print('[结果] 图案幸存 %d 字 / %d（幸存 = 视核没写）' % (int(survived.sum()), NW))
        if survived.all():
            print('  => 视核**一个字都没写**（查 freeze 极性 / 视核是否真在跑）')
        elif not survived.any():
            print('  => 5760 个字全被覆盖：视核写满了整张图（后半本来就是 0）')
        else:
            idx = np.where(survived)[0]
            print('  => 部分覆盖。幸存字址范围: %d .. %d，连续段: %s'
                  % (idx.min(), idx.max(),
                     '是' if (idx.max() - idx.min() + 1) == len(idx) else '否'))
            # 打印被覆盖区域的分界
            cov = np.where(~survived)[0]
            print('     被覆盖字址范围: %d .. %d（共 %d）' % (cov.min(), cov.max(), len(cov)))
        # 顺带看看前几个字的实际内容
        print('[抽样] 字 0/1440/2880/5759 = %08x %08x %08x %08x'
              % (now[0], now[1440], now[2880], now[5759]))
        mc.wr_u32(0x00, 0x0)
        print('[收尾] 已解冻')


if __name__ == '__main__':
    main()
