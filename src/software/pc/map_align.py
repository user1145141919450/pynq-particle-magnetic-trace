# -*- coding: utf-8 -*-
"""map_align.py — 板端缩略图对不上黄金模型时，**定位错在哪**（纯文件运算，不碰硬件）

什么时候用：vision_diag.py 的 C) 项没过，且 `errs` 里有 bit0（"tlast 落在半个像素上"）。
bit0 的含义是"某一行有奇数个字节"—— 那一刻之后所有像素的**字节配对整体错一位**，
于是缩略图有内容、有彩色、行数也对，但数值全错。这跟 v1 的经典症状是同一类，
所以定位方法也沿用 v1 那套：**别猜，把"错了几位、从哪一行开始错"量出来。**

本脚本做四件事：
  1. 板端 map 里 0 字的分布 —— 区分"没写到"和"写进去是 0"
  2. 逐块行相符率 —— 字节错位是**从某一行起整片坏**（前半对、后半错），
     空间错位（整体平移）则**处处都差**。这一眼就能分开两种病因
  3. 行内字节相位扫描（phase 0..7，两种 swap）—— 找"每行内配对偏了几字节"
  4. 块级平移搜索（±3 行/列）—— 找"缩略图整体平移了几块"

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/map_align.py
"""

import sys

sys.path.insert(0, '/boot')

import numpy as np
import vision_golden as vg

W, H, BLK = 1024, 720, 8
BW, BH = W // BLK, H // BLK          # 128 x 90
RAW = '/boot/frame_raw_d.bin'
MAP = '/boot/vmap_d.bin'


def main():
    raw = np.fromfile(RAW, np.uint8)
    mp = np.fromfile(MAP, np.uint16).reshape(BH, BW)
    print('[输入] raw %d 字节（%.2f 帧的量）  map %d 字（%dx%d）'
          % (raw.size, raw.size / float(W * H * 2), mp.size, BW, BH))
    if raw.size < W * H * 2:
        print('★ raw 不足一帧 —— 抓帧本身没成，先解决采集')
        return 1

    # ---- 1) 板端 map 的 0 字分布（"没写到" vs "写的是 0"）----
    zr = (mp == 0).mean(axis=1) * 100.0
    print('')
    print('[1] map 里 0 字占比 %.1f%%；逐块行（每 10 行一档，单位 %%）：' % (100.0 * (mp == 0).mean()))
    for r0 in range(0, BH, 10):
        print('    块行 %2d~%2d: %s'
              % (r0, min(r0 + 9, BH - 1), ' '.join('%4.0f' % v for v in zr[r0:r0 + 10])))

    rows = raw[:H * W * 2].reshape(H, W * 2)

    def golden_phase(phase, swap):
        """按"行内配对偏移 phase 字节"重建黄金模型（phase=0 就是正常口径）"""
        r = rows
        if phase:
            r = np.empty_like(rows)
            r[:, :W * 2 - phase] = rows[:, phase:]
            r[:, W * 2 - phase:] = 0
        p = vg.pixels_from_raw(r.reshape(-1))
        if swap:
            p = vg.swap16(p)
        return vg.block_avg(p)

    # ---- 3) 行内相位 + swap 扫描 ----
    print('')
    print('[3] 行内字节相位扫描（找"每行内配对偏了几字节"）')
    best = None
    for phase in range(8):
        for swap in (0, 1):
            g = golden_phase(phase, swap)
            d = np.abs(mp.astype(np.int32) - g.astype(np.int32))
            w1 = float((d <= 1).mean()) * 100.0
            ex = float((d == 0).mean()) * 100.0
            print('    phase=%d swap=%d: 相同 %5.2f%%  <=1LSB %5.2f%%  max %6d  mean %8.1f'
                  % (phase, swap, ex, w1, int(d.max()), float(d.mean())))
            if best is None or w1 > best[0]:
                best = (w1, phase, swap, d, g)

    w1, phase, swap, d, g = best
    print('    → 最好的组合：phase=%d swap=%d（%.2f%% 在 1LSB 内）' % (phase, swap, w1))

    # ---- 2) 逐块行相符率（用最好那一组）----
    print('')
    print('[2] 逐块行相符率（<=1LSB %%，每 10 行一档）—— 前半对后半错 = 字节错位；'
          '处处均匀地差 = 整体平移/别的原因')
    pr = (d <= 1).mean(axis=1) * 100.0
    for r0 in range(0, BH, 10):
        print('    块行 %2d~%2d: %s'
              % (r0, min(r0 + 9, BH - 1), ' '.join('%4.0f' % v for v in pr[r0:r0 + 10])))

    # 找"从哪一块行开始整片坏"：连续低于 50% 的起点
    bad = pr < 50.0
    if bad.all():
        print('    全部块行都差 → 不是"从中间断"，更像整体平移或口径不符')
    elif bad.any():
        first = int(np.argmax(bad))
        print('    ★ 前 %d 个块行还行、从块行 %d（画面第 %d 行）开始整片坏'
              % (first, first, first * BLK))
        if first > 0 and not bad[:first].all():
            print('      → 字节数错位：第 %d 块行附近（画面第 %d~%d 行）有行少了/多了字节'
                  % (first, (first - 1) * BLK, first * BLK + BLK - 1))

    # ---- 4) 块级平移搜索 ----
    print('')
    print('[4] 块级平移搜索（±3 块）—— 若某个平移明显更好，就是空间错位不是数值错位')
    bests = None
    for dr in range(-3, 4):
        for dc in range(-3, 4):
            if abs(dr) + abs(dc) == 0:
                continue
            a = mp[max(0, dr):BH + min(0, dr), max(0, dc):BW + min(0, dc)]
            b = g[max(0, -dr):BH + min(0, -dr), max(0, -dc):BW + min(0, -dc)]
            dd = np.abs(a.astype(np.int32) - b.astype(np.int32))
            v = float((dd <= 1).mean()) * 100.0
            if bests is None or v > bests[0]:
                bests = (v, dr, dc)
    print('    最好的平移 = 行偏移 %+d、列偏移 %+d → %.2f%% 在 1LSB 内（不偏时 %.2f%%）'
          % (bests[1], bests[2], bests[0], w1))
    if bests[0] > w1 + 20:
        print('    ★ 平移后明显变好 → 是**空间错位**（整块平移），去查写入地址/列映射')
    else:
        print('    → 没有哪个平移能救回来 → 不是空间平移问题')

    # 末了：把最好的相位差按"画面行"聚合，看看坏区的密度
    print('')
    print('[附] 最大差所在位置（用最好那一组）的前 8 个：')
    idx = np.dstack(np.unravel_index(np.argsort(d.ravel())[::-1][:8], d.shape))[0]
    for r, c in idx:
        print('    块(行%3d,列%3d) 画面像素(%4d,%4d)  板端=%5d 黄金=%5d 差=%d'
              % (r, c, c * BLK, r * BLK, mp[r, c], g[r, c], d[r, c]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
