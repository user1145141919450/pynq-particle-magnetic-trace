# -*- coding: utf-8 -*-
"""c_diag.py — C 项没过时，一次性回答"map 和**哪一个**相位的黄金模型对得上"

为什么需要它：板端 map 和黄金模型**都从帧首那个字节开始配对**，所以只要相位稳定，
两者就该一致。若还是对不上，只有两种可能，而它们需要完全不同的处理：

  (a) map 与黄金的**锚点相差整数个字节**（板端从帧首配对、黄金从 buffer 字节 0 配对，
      而帧首不在 buffer 的字节 0 上）→ 找出差几个字节即可定位
  (b) map 自己的内容就与 raw 无关（写入/累加逻辑另有问题）→ 换相位也救不回来

做法：对"行内字节偏移 0..3 × swap 0/1"共 8 种黄金模型，各与板端 map 逐块比，
打印 <=1LSB 相符率。**若有某一种明显突出，就是 (a)；全都 ~0 就是 (b)。**

同时打印 map 的**非零区域边界** —— 判断"是不是只有前若干块行被写过"
（写入提前停住）还是"写满了但内容不对"。

用法（板上，纯文件运算）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/c_diag.py
"""

import sys

sys.path.insert(0, '/boot')

import numpy as np
import vision_golden as vg

W, H, BLK = 1024, 720, 8
BW, BH = W // BLK, H // BLK


def golden_from(raw, off, swap):
    """把整帧按"每行从 off 字节开始配对"重建，再算块平均（off=0 就是黄金模型原口径）"""
    a = np.asarray(raw, np.uint8).reshape(H, W * 2)
    if off == 0:
        seg = a
    else:
        seg = np.empty_like(a)
        seg[:, :W * 2 - off] = a[:, off:]
        seg[:, W * 2 - off:] = 0
    pix = (seg[:, 0::2].astype(np.uint16) | (seg[:, 1::2].astype(np.uint16) << 8))
    if swap:
        pix = vg.swap16(pix)
    return vg.block_avg(pix)


def main():
    mp = np.fromfile('/boot/vmap_d.bin', np.uint16).reshape(BH, BW)
    raw = np.fromfile('/boot/frame_raw_d.bin', np.uint8)
    print('[输入] map %dx%d   raw %d 字节' % (BW, BH, raw.size))
    if raw.size < W * H * 2:
        print('★ raw 不足一帧')
        return 1

    # ---- map 的非零边界 ----
    nz = (mp != 0).any(axis=1)
    nzr = np.nonzero(nz)[0]
    zr = np.nonzero(~nz)[0]
    print('')
    print('[map 非零区域] 有内容的块行 %s .. %s（共 %d 行）；'
          % (nzr.min() if nzr.size else '-', nzr.max() if nzr.size else '-', nzr.size))
    if zr.size:
        print('               全 0 的块行 %d .. %d（共 %d 行）'
              % (zr.min(), zr.max(), zr.size))
        print('               → 说明写入**提前停住**了（不是"内容对但值小"）')
    else:
        print('               没有全 0 的块行 —— 写满了，是内容不对（查累加/地址映射）')
    # 列方向也看一下
    nzc = (mp != 0).any(axis=0)
    print('               非零列 %d .. %d（共 %d 列 / 128）'
          % (np.nonzero(nzc)[0].min(), np.nonzero(nzc)[0].max(), nzc.sum()))

    # ---- 8 种黄金模型逐个比 ----
    print('')
    print('[与板端 map 逐块比] 行列内偏移 x swap（<=1LSB 相符率，越高越像）')
    best = None
    for off in range(4):
        line = []
        for sw in (0, 1):
            g = golden_from(raw, off, sw)
            d = np.abs(mp.astype(np.int32) - g.astype(np.int32))
            v = float((d <= 1).mean()) * 100.0
            line.append('%5.1f%%' % v)
            if best is None or v > best[0]:
                best = (v, off, sw, d, g)
        print('    偏移 %d：swap0 %s   swap1 %s' % (off, line[0], line[1]))
    v, off, sw, d, g = best
    print('    → 最好：偏移 %d swap %d = %.1f%%' % (off, sw, v))

    if v < 5.0:
        print('    ★ 8 种全都对不上 → 是 (b)：map 的内容与这帧 raw 无关。'
              '先确认两次抓取之间场景有没有变（动态场景天然对不上），'
              '再看 map 的非零边界是不是"提前停住"。')
    else:
        print('    ★ 有明显突出者 → 是 (a)：锚点相差 %d 个字节。'
              '板端/黄金两头对齐到同一字节起始即可（改黄金的口径或改帧首位置）' % off)

    # ---- 用最好的那组，按块行看相符率分布（前半对后半错 = 写入中途变了）----
    pr = (d <= 1).mean(axis=1) * 100.0
    print('')
    print('[逐块行相符率]（<=1LSB %%，每 10 行一档）')
    for r0 in range(0, BH, 10):
        print('    块行 %2d~%2d: %s'
              % (r0, min(r0 + 9, BH - 1), ' '.join('%4.0f' % x for x in pr[r0:r0 + 10])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
