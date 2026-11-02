# -*- coding: utf-8 -*-
"""_pack_bench.py —— pack_bgr32 三种实现的板上对比（2026-10-31，临时诊断）

目标口径：uint32 小端 [G,B,R,0] == (R<<16)|(B<<8)|G。
  ① 现实现：view 成 4 字节，三笔 strided 切片
  ② cv2.merge：把 G/B/R/0 合成 4 通道（OpenCV SIMD）
  ③ 位运算：R<<16 | B<<8 | G（连续写）
"""
import time
import numpy as np
import cv2

H, W = 720, 1024
img = np.random.randint(0, 255, (H, W, 3), np.uint8)


def pack_view(img):
    h, w = img.shape[:2]
    out = np.empty((h, w), np.uint32)
    v = out.view(np.uint8).reshape(h, w, 4)
    v[:, :, 0] = img[:, :, 1]
    v[:, :, 1] = img[:, :, 0]
    v[:, :, 2] = img[:, :, 2]
    v[:, :, 3] = 0
    return out


def pack_cv(img):
    h, w = img.shape[:2]
    z = np.zeros((h, w), np.uint8)
    m = cv2.merge([img[:, :, 1], img[:, :, 0], img[:, :, 2], z])
    return m.view(np.uint32).reshape(h, w)


def pack_bits(img):
    b = img[:, :, 0].astype(np.uint32)
    g = img[:, :, 1].astype(np.uint32)
    r = img[:, :, 2].astype(np.uint32)
    return (r << 16) | (b << 8) | g


def bench(name, fn, n=5):
    fn(img)
    t0 = time.perf_counter()
    for _ in range(n):
        out = fn(img)
    dt = (time.perf_counter() - t0) * 1e3 / n
    print('%-12s %7.2f ms' % (name, dt))
    return out


a = bench('view切片', pack_view)
b = bench('cv2.merge', pack_cv)
c = bench('位运算', pack_bits)
print('三种结果一致:', bool((a == b).all()), bool((a == c).all()))

# 直接写进已有缓冲（游戏里 src[:] = ...）的开销
src = np.zeros((H, W), np.uint32)
t0 = time.perf_counter()
for _ in range(5):
    src[:] = pack_view(img)
print('view+入缓冲  %7.2f ms' % ((time.perf_counter() - t0) * 1e3 / 5))
t0 = time.perf_counter()
for _ in range(5):
    src[:] = pack_cv(img)
print('cv2+入缓冲   %7.2f ms' % ((time.perf_counter() - t0) * 1e3 / 5))
