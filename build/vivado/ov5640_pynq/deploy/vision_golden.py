# -*- coding: utf-8 -*-
"""vision_golden.py — PL 视觉前处理（vision_core 8x8 块平均）的黄金模型

RTL 在做什么（docs/vision_ip_design.md），这里就逐位复刻什么：

    1) 字节流 → 16 位像素：**先到的字节在低 8 位**（与桥/VDMA 的小端拼法一致）
    2) swap_bytes 时先高低字节对调
    3) 拆通道 R=pix[15:11] G=pix[10:5] B=pix[4:0]
    4) 每个 8x8 块**按通道分别求和**（不能先加 16 位值 —— 会跨字段进位）
    5) 各自 >>6（除以 64）后拼回 RGB565

★ 整数口径必须一致：RTL 用的是"求和后右移 6 位"，不是浮点/四舍五入的均值。
  黄金模型若用 float 平均再 round，会在大片渐变的块上系统性差 1 LSB。

用法：
    python vision_golden.py 原始帧.bin [--swap] [--png 输出.png]
    python vision_golden.py --cmp 板端map.bin 原始帧.bin [--swap]

原始帧.bin：板端 frame_dump.py / vision_read.py 存下来的原样字节（720 x 2048）。
板端map.bin：vision_read.py 从 0x43B00000 读回的 23,040 字节（90 x 128 个 uint16）。
"""

import os
import struct
import sys

_pv = os.environ.get('PY_PKGS_VISION')
if _pv and os.path.isdir(_pv):
    sys.path.insert(0, _pv)

import cv2
import numpy as np

W, H = 1024, 720
BLK = 8
BW, BH = W // BLK, H // BLK          # 128 x 90


def pixels_from_raw(raw, h=H, w=W):
    """原样字节流 → 16 位像素（先到的字节进低 8 位）"""
    a = np.frombuffer(raw, np.uint8).reshape(h, w, 2).astype(np.uint16)
    return (a[..., 0] | (a[..., 1] << 8)).astype(np.uint16)


def swap16(pix):
    return (((pix & 0x00FF) << 8) | (pix >> 8)).astype(np.uint16)


def block_avg(pix16, blk=BLK):
    """逐位复刻 RTL：分通道求和 -> >>6 -> 拼回 RGB565"""
    R = (pix16 >> 11) & 0x1F
    G = (pix16 >> 5) & 0x3F
    B = pix16 & 0x1F
    h, w = pix16.shape
    bh, bw = h // blk, w // blk
    sh = (bh, blk, bw, blk)
    sR = R[:bh * blk, :bw * blk].reshape(sh).sum(axis=(1, 3))
    sG = G[:bh * blk, :bw * blk].reshape(sh).sum(axis=(1, 3))
    sB = B[:bh * blk, :bw * blk].reshape(sh).sum(axis=(1, 3))
    aR = (sR >> 6).astype(np.uint16)
    aG = (sG >> 6).astype(np.uint16)
    aB = (sB >> 6).astype(np.uint16)
    return ((aR << 11) | (aG << 5) | aB).astype(np.uint16)


def map_to_bgr(m, swap=False):
    """缩略图（90x128 uint16）→ BGR 图，供肉眼对照（用 cv2 的 BGR565 转换）

    ★ 默认 `swap=False` 才是对的（2026-09-27 踩过一小时）：地图在内存里的两个字节
      与 VDMA 那路原图**正好相反**，所以"原图用的那个 swap"套到地图上等于翻了第二次
      —— 出来是一张洋红噪声图，而 C 项/黄金对比照样 89.8%/99.4% 全绿（位域比较
      对"显示字节序"完全无感）。别照抄调用者的 swap，除非你自己量过字序关系。
    """
    b = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
    if swap:
        b = b[..., ::-1]
    return cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR5652BGR)


def split565(v):
    """RGB565 拆三通道（R 5 位 / G 6 位 / B 5 位）"""
    return (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F


def compare(board_map_bytes, raw_bytes, swap=False, noise_floor=None):
    """板端读回的 map 与黄金模型逐块对比

    ★★ 判据为什么必须是"逐通道 ≤1 LSB"，而不是"打包值 ≤1"（2026-09-26 改）：

      打包值 = R<<11 | G<<5 | B，三个通道的 1 个量化级在打包值里分别是
      **2048 / 32 / 1**。于是老判据 "|打包差| <= 1" 实际上等价于要求
      **R 一模一样、G 也一模一样**，只有 B 允许差 1 —— 它测的不是"块平均对不对"，
      而是"这张画面的 R、G 平均值能不能一比特都不差"。

      而传感器帧间抖动本来就会让某个通道的块平均差 1：实测三通道"完全相同"
      率各约 70%，乘起来只剩 ~35%，这正是老版 C 项一直报 34~47% 的全部原因
      （看起来像块平均算错了，其实是被打包口径把 R 的 1 LSB 放大成了 2048）。

      块平均的语义本来就是"每个通道各自的平均值差不超过 1 个量化级"，
      所以判据必须逐通道下。此口径与 PL 无关，PC 与板端共用本文件。

    参数 noise_floor：黄金模型**自己**两张相邻帧之间"三通道同时 ≤1"的比例(%)。
      板端 vs 黄金 若能追平它，说明 PL 没有引入输入噪声之上的额外误差 ——
      这比任何绝对阈值都硬：动态场景下 100% 那条线本来就到不了。
    """
    golden = block_avg(swap16(pixels_from_raw(raw_bytes)) if swap
                       else pixels_from_raw(raw_bytes))
    got = np.frombuffer(board_map_bytes, np.uint16).reshape(BH, BW)
    n = golden.size
    gR, gG, gB = split565(golden)
    bR, bG, bB = split565(got)

    print('==== 黄金模型对比（%d 块 = %dx%d，逐通道判据）====' % (n, BW, BH))
    print('  通道   完全相同      <=1 LSB     最大差    平均差')
    diff = {}
    for nm, g, b in (('R', gR, bR), ('G', gG, bG), ('B', gB, bB)):
        d = np.abs(g.astype(np.int32) - b.astype(np.int32))
        diff[nm] = d
        print('  %-4s  %6.2f%%      %6.2f%%      %5d    %7.3f'
              % (nm, (d == 0).mean() * 100.0, (d <= 1).mean() * 100.0,
                 int(d.max()), float(d.mean())))
    joint = float(((diff['R'] <= 1) & (diff['G'] <= 1) & (diff['B'] <= 1)).mean()) * 100.0
    print('  三通道同时 <=1 LSB : %.2f%%      （判据 >=98%%）' % joint)

    # 打包口径只作参考/定位：它的数被 R 的 2048 倍权重支配
    dp = np.abs(got.astype(np.int32) - golden.astype(np.int32))
    print('  [参考] 打包值完全相同 %.2f%%  <=1 %.2f%%  （R 的 1 LSB = 2048，'
          '此栏只用于定位，不作判据）'
          % ((dp == 0).mean() * 100.0, (dp <= 1).mean() * 100.0))

    if noise_floor is not None:
        print('  输入自身的帧间噪声底（黄金相邻两帧）：%.2f%%' % noise_floor)
        print('  → 板端额外误差 = %+.2f 个百分点（≈0 = PL 没引入误差）'
              % (joint - noise_floor))

    if joint >= 98.0 or (noise_floor is not None and joint >= noise_floor - 0.5):
        print('  => 判据通过：PL 块平均与黄金模型逐通道一致 ✓')
        return 0
    print('  => 未达判据。先确认：① 板端 map 与原始帧是不是相邻帧（差几十帧时'
          '画面一变就对不上）② swap 参数是否与实际字节序一致')
    # 把差异最大的 10 块列出来，便于定位（行/列 -> 画面位置）
    idx = np.dstack(np.unravel_index(np.argsort(dp.ravel())[::-1][:10], dp.shape))[0]
    for r, c in idx:
        print('    块(行%3d,列%3d) 画面像素(%4d,%4d)  板端=%5d(R%2d G%2d B%2d)'
              '  黄金=%5d(R%2d G%2d B%2d)'
              % (r, c, c * BLK, r * BLK, got[r, c], bR[r, c], bG[r, c], bB[r, c],
                 golden[r, c], gR[r, c], gG[r, c], gB[r, c]))
    return 1


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    swap = '--swap' in sys.argv
    png = None
    if '--png' in sys.argv:
        png = sys.argv[sys.argv.index('--png') + 1]

    if '--cmp' in sys.argv:
        if len(args) < 2:
            print(__doc__)
            return 1
        with open(args[0], 'rb') as f:
            bmap = f.read()
        with open(args[1], 'rb') as f:
            raw = f.read()
        return compare(bmap, raw, swap)

    if not args:
        print(__doc__)
        return 1
    with open(args[0], 'rb') as f:
        raw = f.read()
    h = len(raw) // (W * 2)
    pix = pixels_from_raw(raw, h, W)
    if swap:
        pix = swap16(pix)
    m = block_avg(pix)
    print('[黄金模型] 原始帧 %s  %dx%d  -> 缩略图 %dx%d' % (args[0], W, h, BW, m.shape[0]))
    bgr = map_to_bgr(m, False)
    print('[统计] 缩略图 BGR 均值 %.1f 标准差 %.1f' % (bgr.mean(), bgr.std()))
    # 用 8 倍最近邻放大，肉眼就能看出球/桌面/杂物的分布
    big = cv2.resize(bgr, (BW * 8, m.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
    out = png or (os.path.splitext(args[0])[0] + '_golden.png')
    cv2.imwrite(out, big)
    print('[输出] 缩略图放大 8 倍已存 %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
