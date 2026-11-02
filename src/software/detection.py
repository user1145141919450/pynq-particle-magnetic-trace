# -*- coding: utf-8 -*-
"""detection.py — 红色小球检测（HSV 双区间掩膜 + 形态学 + 最大轮廓）

红色在 HSV 色相环上跨越 0°（OpenCV 中 H∈[0,180)）：暖红 H≈0~10，
洋红偏红 H≈170~180，两段都要掩。流程：
  BGR → HSV → 双区间 inRange 合成掩膜 → 开运算去噪 + 闭运算填高光孔
  → 最大轮廓 → 面积矩质心得 (cx, cy)，最小外接圆得 radius。

坐标约定与 geometry.py 一致：图像像素坐标，原点左上、x 向右、y 向下。

S/V 阈值与环境光照强相关，现场用 live_demo.py 的滑动条标定后，
把标定值写回 RED_RANGES 常量（上板前固化）。
"""

import cv2
import numpy as np

# 红球 HSV 阈值（现场标定后可调整 S/V 下限；H 双区间固定）
RED_RANGES = [
    ((0, 100, 60), (10, 255, 255)),     # 低端红（H 0~10）
    ((170, 100, 60), (180, 255, 255)),  # 高端红（H 170~180）
]

MIN_AREA = 40      # 轮廓面积下限 (px²)，滤掉噪点
OPEN_KERNEL = 5    # 开运算椭圆核尺寸（去噪）
CLOSE_KERNEL = 9   # 闭运算椭圆核尺寸（填球面高光小孔）


def red_mask(frame_bgr, ranges=None):
    """返回红色二值掩膜 (H×W uint8)。

    ranges = [(lo, hi), ...]，lo/hi 各为 HSV 三元组，默认 RED_RANGES。
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in (ranges or RED_RANGES):
        mask = cv2.bitwise_or(
            mask, cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8)))
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (OPEN_KERNEL, OPEN_KERNEL))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (CLOSE_KERNEL, CLOSE_KERNEL))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)


def detect_red_ball(frame_bgr, ranges=None, min_area=MIN_AREA):
    """检测画面中最大的红色目标，返回 (cx, cy, radius) 或 None。

    cx, cy 为轮廓面积矩质心（亚像素），radius 为最小外接圆半径 (px)。
    画面内无红色目标或目标过小（< min_area）时返回 None。
    """
    mask = red_mask(frame_bgr, ranges)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < min_area:
        return None
    m = cv2.moments(c)
    cx = m['m10'] / (m['m00'] or 1.0)
    cy = m['m01'] / (m['m00'] or 1.0)
    _, radius = cv2.minEnclosingCircle(c)
    return float(cx), float(cy), float(radius)
