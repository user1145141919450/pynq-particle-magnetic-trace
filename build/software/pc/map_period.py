# -*- coding: utf-8 -*-
"""map_period.py — 两件事用数说话：① map 是不是每 32 块横向重复 ② 整帧的字节怎么配才成画面

背景（2026-09-24 深夜）：vision_diag 的 C) 项 0.2%，把两张图拉出来一看**都是彩色噪点**，
且 map 那张能看出**横向重复 4 次**（= 每 32 块一轮）。记忆里 v1 的病历写的正是
"横向每 32 块重复一次，指向累加器索引回绕到 32"。但"看着像"不算证据，这里量出来。

判据：
  ① 周期性：算 map[:, c] == map[:, c+P] 的列占比。
     P=32 若接近 100% → 列索引确实在 32 处回绕（不是巧合）。
  ② 整帧配对：真画面相邻像素高度相关（水平梯度小）；配错字节会变成噪点（梯度大）。
     对字节偏移 0..3 各算一次平均 |水平梯度|，**最小的那个才是对的配对**。
     这一步能把"图是好的、只是配错字节"和"图本身就是烂的"分开。

用法（板上，纯文件运算）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/map_period.py
"""

import sys

sys.path.insert(0, '/boot')

import numpy as np

W, H, BLK = 1024, 720, 8
BW, BH = W // BLK, H // BLK


def main():
    mp = np.fromfile('/boot/vmap_d.bin', np.uint16).reshape(BH, BW)
    raw = np.fromfile('/boot/frame_raw_d.bin', np.uint8)
    print('[输入] map %dx%d   raw %d 字节（%.2f 帧）' % (BW, BH, raw.size, raw.size / float(W * H * 2)))

    # ---- ① map 的横向周期 ----
    print('')
    print('① map 横向周期性（列占比越高越可疑）')
    for p in (8, 16, 32, 64):
        eq = (mp[:, :-p] == mp[:, p:]).mean() * 100.0
        print('    周期 %2d 块：%5.1f%% 的列完全相同' % (p, eq))
    # 只看有内容的前 22 块行（后面全 0 会把比例冲高，是假象）
    nz = (mp != 0).any(axis=1)
    first_zero = int(np.argmax(~nz)) if (~nz).any() else BH
    print('    （前 %d 块行有内容，后面全 0 —— 下面只看有内容的部分，免得被 0 冲高）' % first_zero)
    sub = mp[:first_zero]
    for p in (16, 32, 64):
        eq = (sub[:, :-p] == sub[:, p:]).mean() * 100.0
        print('    周期 %2d 块（只算前 %d 行）：%5.1f%%' % (p, first_zero, eq))
    nzrow = np.nonzero(nz)[0]
    print('    有内容的块行：%d .. %d（共 %d 行）' % (nzrow.min(), nzrow.max(), nzrow.size))

    # ---- ② 整帧的字节配对 ----
    print('')
    print('② 整帧配对测试：平均 |水平梯度|（越小越像真画面）')
    n = min(raw.size // (W * H * 2), 1) * H
    a = raw[:W * H * 2].reshape(H, W * 2)
    best = None
    for off in range(4):
        # 每行从 off 开始取 W*2 字节，配成 W 个 16 位像素
        seg = np.empty((H, W * 2), np.uint8)
        if off == 0:
            seg = a
        else:
            seg[:, :W * 2 - off] = a[:, off:]
            seg[:, W * 2 - off:] = 0
        pix = (seg[:, 0::2].astype(np.int32) | (seg[:, 1::2].astype(np.int32) << 8))
        g = float(np.abs(np.diff(pix.astype(np.int32), axis=1)).mean())
        gv = float(np.abs(np.diff(pix.astype(np.int32), axis=0)).mean())
        print('    行内偏移 %d：水平梯度 %8.0f   垂直梯度 %8.0f' % (off, g, gv))
        if best is None or g < best[0]:
            best = (g, off)
    print('    → 水平梯度最小的偏移 = %d（%.0f）。真画面应在 1000 量级以下；'
          '若都在 8000 以上，说明整帧数据本身就是烂的或不对齐' % (best[1], best[0]))

    # 参考：板上的 map（缩略图，真画面的话梯度也应该很小）
    print('')
    print('③ 参考：板端 map 自己的水平梯度 = %.0f（缩略图 128 宽）'
          % float(np.abs(np.diff(mp[:first_zero].astype(np.int32), axis=1)).mean()))

    # ④ 用正确配对算一遍黄金，看与 map 的相符有没有变化
    print('')
    print('④ 交叉检查：map 有没有可能对上了 raw 的**某 8 行块**')
    seg = a
    pix = (seg[:, 0::2].astype(np.uint16) | (seg[:, 1::2].astype(np.uint16) << 8))
    sys.path.insert(0, '/boot')
    import vision_golden as vg
    for sw in (0, 1):
        p = vg.swap16(pix) if sw else pix
        gm = vg.block_avg(p)
        d = np.abs(mp.astype(np.int32) - gm.astype(np.int32))
        print('    swap=%d：<=1LSB %.2f%%  最大差 %d' % (sw, (d <= 1).mean() * 100, int(d.max())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
