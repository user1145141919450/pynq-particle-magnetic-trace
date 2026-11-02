# -*- coding: utf-8 -*-
"""_remap_bench.py —— live 透视 remap：float32 映射 vs 16 位定点映射（2026-10-31）

口径与 _live_crop_image 相同：src=1024x720 -> dst=1280x720，BORDER_REPLICATE。
只比 remap 本体（映射表都预先建好）。
"""
import json
import time

import cv2
import numpy as np

j = json.load(open('/home/xilinx/wp/v3_calib.json'))
H = np.array(j['H'], np.float64)
Hi = np.linalg.inv(H)


def g2c(x, y):
    return cv2.perspectiveTransform(
        np.array([[[x, y]]], np.float64), Hi)[0][0]


FW, FH = 960.0, 540.0
src_q = np.array([g2c(0, 0), g2c(FW, 0), g2c(FW, FH), g2c(0, FH)], np.float32)
mx = 0.03 * 1280.0
my = (0.03 + 0.012) * 720.0
dst_q = np.array([[mx, my], [1280 - mx, my],
                  [1280 - mx, 720 - my], [mx, 720 - my]], np.float32)
M = cv2.getPerspectiveTransform(src_q, dst_q)
Minv = np.linalg.inv(M)
xs, ys = np.meshgrid(np.arange(1280, dtype=np.float32),
                     np.arange(720, dtype=np.float32))
pts = np.stack([xs, ys], -1).reshape(-1, 1, 2)
back = cv2.perspectiveTransform(pts, Minv).reshape(720, 1280, 2)
m1 = np.ascontiguousarray(back[..., 0])
m2 = np.ascontiguousarray(back[..., 1])

img = np.random.randint(0, 255, (720, 1024, 3), np.uint8)
out = np.empty((720, 1280, 3), np.uint8)

# 定点表（一次转换）
fm1, fm2 = cv2.convertMaps(m1, m2, cv2.CV_16SC2)
fm2 = None   # ★ CV_16SC2：第二张表必须传 None（传 convertMaps 返回的 f2 会全错）


def bench(name, fn, n=6):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) * 1e3 / n
    print('%-22s %7.2f ms' % (name, dt))
    return dt


def f_float():
    return cv2.remap(img, m1, m2, cv2.INTER_NEAREST,
                     dst=out, borderMode=cv2.BORDER_REPLICATE)


def f_fixed():
    return cv2.remap(img, fm1, fm2, cv2.INTER_NEAREST,
                     dst=out, borderMode=cv2.BORDER_REPLICATE)


def f_float_lin():
    return cv2.remap(img, m1, m2, cv2.INTER_LINEAR,
                     dst=out, borderMode=cv2.BORDER_REPLICATE)


def f_fixed_lin():
    return cv2.remap(img, fm1, fm2, cv2.INTER_LINEAR,
                     dst=out, borderMode=cv2.BORDER_REPLICATE)


a = bench('float/NN', f_float)
b = bench('fixed16/NN', f_fixed)
c = bench('float/LIN', f_float_lin)
d = bench('fixed16/LIN', f_fixed_lin)
print('加速: NN %.2fx  LIN %.2fx' % (a / max(b, 1e-6), c / max(d, 1e-6)))
print('NN 两版像素差: %d' % int((np.asarray(
    cv2.remap(img, m1, m2, cv2.INTER_NEAREST,
              borderMode=cv2.BORDER_REPLICATE))
    != np.asarray(cv2.remap(img, fm1, fm2, cv2.INTER_NEAREST,
                            borderMode=cv2.BORDER_REPLICATE))).sum()))
