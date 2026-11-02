# -*- coding: utf-8 -*-
"""warp_bench.py —— 透视矫正的两种实现，在 A9 上实测（2026-10-31）

背景：profile 实锤 `cv2.warpPerspective` 每帧 ~100ms（占"叠加 108ms"的九成）。
候选：预计算 map + `cv2.remap`（纯 gather，理论更快；map 只在标定/尺寸变化时重算）。

用法（板上 root）：python3 warp_bench.py
"""
import time


def bench(fn, n=10, name=''):
    # 预热 1 次（首次含内存分配）
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t0) / n * 1000.0


def main():
    import cv2
    import numpy as np
    import v3_scan

    Hm = v3_scan.load_calib(verbose=False)
    print('calib Hm =', None if Hm is None else np.asarray(Hm).shape)
    if Hm is None:
        return 1
    img = (np.random.rand(720, 1024, 3) * 255).astype(np.uint8)
    margin, vpad = 0.03, 0.012
    mx = float(margin) * 1280.0
    my = (float(margin) + vpad) * 720.0
    dst = np.array([[mx, my], [1280 - mx, my], [1280 - mx, 720 - my],
                    [mx, 720 - my]], np.float32)
    src = np.array([list(v3_scan.game_to_cam(Hm, 0.0, 0.0))[:2],
                    list(v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, 0.0))[:2],
                    list(v3_scan.game_to_cam(Hm, v3_scan.FIELD_W,
                                             v3_scan.FIELD_H))[:2],
                    list(v3_scan.game_to_cam(Hm, 0.0, v3_scan.FIELD_H))[:2]],
                   np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    Minv = np.linalg.inv(M)

    # ---- A) 现状：warpPerspective（nearest + replicate）----
    def warp_now():
        return cv2.warpPerspective(img, M, (1280, 720),
                                   flags=cv2.INTER_NEAREST,
                                   borderMode=cv2.BORDER_REPLICATE)
    ms_warp = bench(warp_now, name='warp')

    # ---- B) 预计算 map + remap ----
    t0 = time.perf_counter()
    xs, ys = np.meshgrid(np.arange(1280, dtype=np.float32),
                         np.arange(720, dtype=np.float32))
    pts = np.stack([xs, ys], -1).reshape(-1, 1, 2)
    back = cv2.perspectiveTransform(pts, Minv).reshape(720, 1280, 2)
    map1 = np.ascontiguousarray(back[..., 0])
    map2 = np.ascontiguousarray(back[..., 1])
    ms_mapbuild = (time.perf_counter() - t0) * 1000.0

    def warp_remap():
        return cv2.remap(img, map1, map2, cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_REPLICATE)
    ms_remap = bench(warp_remap, name='remap')

    # 一致性：两种做法的像素差
    a = warp_now()
    b = warp_remap()
    d = int(np.abs(a.astype(np.int16) - b.astype(np.int16)).max())
    n_diff = int((a != b).any(axis=2).sum())

    # ---- C) 16 位定点 map（cv2.convertMaps）----
    m1f = np.ascontiguousarray(back[..., 0])
    m2f = np.ascontiguousarray(back[..., 1])
    t0 = time.perf_counter()
    m1_16, m2_16 = cv2.convertMaps(m1f, m2f, cv2.CV_16SC2)
    ms_cvt = (time.perf_counter() - t0) * 1000.0

    def warp_remap16():
        return cv2.remap(img, m1_16, m2_16, cv2.INTER_NEAREST,
                         borderMode=cv2.BORDER_REPLICATE)
    ms_remap16 = bench(warp_remap16, name='remap16')
    c = warp_remap16()
    d16 = int(np.abs(a.astype(np.int16) - c.astype(np.int16)).max())
    n16 = int((a != c).any(axis=2).sum())

    print('warpPerspective : %.1f ms/帧' % ms_warp)
    print('remap(预计算)   : %.1f ms/帧   （map 重建一次 %.1f ms）'
          % (ms_remap, ms_mapbuild))
    print('加速            : %.2fx' % (ms_warp / max(1e-6, ms_remap)))
    print('remap(定点16位) : %.1f ms/帧   （convertMaps 一次 %.1f ms）'
          % (ms_remap16, ms_cvt))
    print('定点 vs warpPerspective 最大差 %d，不同像素 %d / %d'
          % (d16, n16, 720 * 1280))
    print('两种做法最大像素差 %d，不同像素 %d / %d'
          % (d, n_diff, 720 * 1280))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
