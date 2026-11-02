#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hdmi_pump_bench.py —— 量帧泵最贵的两笔（缩放 / 打包），并比替代方案。

为什么要有它（2026-10-01）：`hdmi_out.py pump` 第一次真跑，10 帧实测
**396.5ms/次**（缩放 INTER_AREA 215 + 打包 173 + 拷入 7.8 + 切换 0.5）。
游戏循环摊不起 400ms/次 —— 那是又一份"写盘"的钱。两笔都得压：

  · 缩放：1440x810 -> 1280x720（0.889x 下采样）。INTER_AREA 质量最好但最慢；
    LINEAR/NEAREST 便宜多少**要量**（本工程铁律：别估，量）。
  · 打包：BGR uint8 -> (R<<16)|(B<<8)|G 的 uint32（rgb2dvi 的字节序，别改）。
    现在那行表达式每像素建 5~6 个 uint32 临时数组（18MB 内存流量）。
    替代：把目标 uint32 数组 view 成 uint8 (h,w,4)，3 笔跨步字节拷贝（写 ~2.7MB）。
  · 另：拷入 CMA 的 7.8ms 也许能省 —— 打包**直接写进** CMA 缓冲的 uint8 视图。

本工具**纯 CPU**（不 import pynq、不碰 /dev/mem、不碰 HDMI）—— xilinx 直接跑：
    /usr/local/share/pynq-venv/bin/python3 hdmi_pump_bench.py
每个候选跑 R 轮，报中位（避免被一次调度噪声带偏）。
"""
import time

import cv2
import numpy as np

W_IN, H_IN = 1440, 810      # v3_scan.render 的输出尺寸（帧泵的真实输入）
W_OUT, H_OUT = 1280, 720    # HDMI 720p
R = 7                       # 每项轮数（取中位）


def med(fn, r=R):
    ts = []
    for _ in range(r):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e3)
    return float(np.median(ts))


def mkimg():
    """造一张**像 v3 图**的测试图：底色 + 线 + 文字（逼着缓存干点实事）。"""
    img = np.full((H_IN, W_IN, 3), 35, np.uint8)
    for i in range(17):
        cv2.polylines(img, [np.array([[100 + i * 60, 700],
                                      [400 + i * 40, 300],
                                      [700 + i * 30, 650]], np.int32)],
                      False, (40 + i * 10, 200 - i * 8, 180), 2)
    cv2.putText(img, "bench 1440x810", (150, 380),
                cv2.FONT_HERSHEY_SIMPLEX, 2.6, (235, 235, 235), 6)
    cv2.putText(img, "PL scan: 11.99 ms", (150, 470),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, (200, 220, 255), 3)
    return img


img = mkimg()
print("输入 %dx%d BGR（照 v3_scan.render 的尺寸/风格）" % (W_IN, H_IN))

# ---- 1) 缩放候选（都用预分配 dst=，把"分配"这笔钉死不含在里头）----
small = np.empty((H_OUT, W_OUT, 3), np.uint8)
print("\n== 缩放 1440x810 -> 1280x720（各 %d 轮取中位）==" % R)
for name, interp in (("INTER_AREA", cv2.INTER_AREA),
                     ("INTER_LINEAR", cv2.INTER_LINEAR),
                     ("INTER_NEAREST", cv2.INTER_NEAREST)):
    t = med(lambda i=interp: cv2.resize(img, (W_OUT, H_OUT), dst=small,
                                        interpolation=i))
    print("  %-14s %7.1f ms" % (name, t))
cv2.resize(img, (W_OUT, H_OUT), dst=small, interpolation=cv2.INTER_AREA)
frame = small                     # 后面打包都用 INTER_AREA 的结果（最坏情况）

# ---- 2) 打包候选 ----
print("\n== 打包 BGR -> (R<<16)|(B<<8)|G 的 uint32（各 %d 轮）==" % R)


def pack_expr():
    # 现在 show() 里那行（每像素 ~6 个临时数组）
    return ((frame[:, :, 2].astype(np.uint32) << 16)
            | (frame[:, :, 0].astype(np.uint32) << 8)
            | frame[:, :, 1])


t_expr = med(lambda: pack_expr())
print("  表达式（现状）        %7.1f ms" % t_expr)

words = np.empty((H_OUT, W_OUT), np.uint32)
v = words.view(np.uint8).reshape(H_OUT, W_OUT, 4)


def pack_bytes():
    # 字节序：内存低->高 = G,B,R,0（小端下 (R<<16)|(B<<8)|G 就是这个排法）
    v[:, :, 0] = frame[:, :, 1]
    v[:, :, 1] = frame[:, :, 0]
    v[:, :, 2] = frame[:, :, 2]
    v[:, :, 3] = 0


t_bytes = med(pack_bytes)
print("  view 字节三笔        %7.1f ms" % t_bytes)

words2 = np.zeros((H_OUT, W_OUT), np.uint32)
v2 = words2.view(np.uint8).reshape(H_OUT, W_OUT, 4)


def pack_bytes_nz():
    # 不写第 4 字节（高字节反正没人看）—— 省一笔 0.9MB
    v2[:, :, 0] = frame[:, :, 1]
    v2[:, :, 1] = frame[:, :, 0]
    v2[:, :, 2] = frame[:, :, 2]


t_bytes_nz = med(pack_bytes_nz)
print("  view 字节三笔(不写α) %7.1f ms" % t_bytes_nz)

# ---- 3) 拷入"CMA 替身"（预分配的 uint32 大数组 = nxt 的角色）----
print("\n== 拷入（打包 -> 目标缓冲）==")
dst = np.zeros((H_OUT, W_OUT), np.uint32)
t_copy = med(lambda: dst.__setitem__((slice(None), slice(None)), words))
print("  先打包再整块拷入      %7.1f ms（words[:] = dst 那一笔）" % t_copy)

vdst = dst.view(np.uint8).reshape(H_OUT, W_OUT, 4)


def pack_direct():
    # ★ 打包**直接写进**目标缓冲（省掉上面那一整块拷贝）—— 帧泵里目标是 CMA 缓冲
    vdst[:, :, 0] = frame[:, :, 1]
    vdst[:, :, 1] = frame[:, :, 0]
    vdst[:, :, 2] = frame[:, :, 2]


t_direct = med(pack_direct)
print("  直接写进目标(uint8 视图) %7.1f ms" % t_direct)

# ---- 4) 结论 ----
print("\n== 算总账 ==")
print("  现状（AREA + 表达式 + 拷入）= %.1f ms" % ((214.9) + t_expr + t_copy))
print("  候选（LINEAR/NEAREST 另量 + 直接写目标）见上表；"
      "全套最快 ≈ 缩放最好那档 + %.1f ms" % t_direct)
print("  ★ 判据：单次推帧总预算 ≤ 50ms 才值得每 6 帧推一次（摊到每帧 <10ms）")
