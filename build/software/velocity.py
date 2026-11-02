# -*- coding: utf-8 -*-
"""velocity.py — 从最近若干帧的小球位置提取像素速度（环形缓冲 + 最小二乘拟合）

演示交互：小球到达入射边界瞬间按键抓拍，用抓拍前最近 N 帧的
(t, x)、(t, y) 序列各做一次线性拟合，斜率即 vx、vy（px/frame）。
t 用帧序号即可（匀速假设下时间轴零点/单位不影响斜率）；丢帧造成
的 t 间隔不均不影响最小二乘拟合。

匀速性校验：拟合残差 RMS 超过 max_residual 说明该段不是匀速
（手推抖动/加速），fit() 返回 ok=False，调用方应放弃本次抓拍——
对应 PLAN.md 风险表"推球非匀速导致 v 提取偏差"的对策。

上板后坐标比例换算：px/frame × 帧率 = px/s，再乘 m/px 标定系数
得真实速度；演示用 r = k·|v|/B 时直接取像素速度即可（k 含比例）。
"""

import math
from collections import deque


class VelocityTracker:
    """环形缓冲存 (t, x, y)，fit() 做线性拟合提取速度。"""

    def __init__(self, maxlen=10, max_residual=2.0):
        self.buf = deque(maxlen=maxlen)
        self.max_residual = max_residual

    @property
    def size(self):
        return len(self.buf)

    def clear(self):
        """球丢失（检测返回 None）时必须 clear()，避免旧点混入拟合。"""
        self.buf.clear()

    def push(self, t, x, y):
        self.buf.append((float(t), float(x), float(y)))

    @staticmethod
    def _fit(ts, vals):
        """vals ≈ v·t + b 的最小二乘，返回 (v, b, 残差RMS)。"""
        n = len(ts)
        sx = sum(ts)
        sy = sum(vals)
        sxx = sum(t * t for t in ts)
        sxy = sum(t * v for t, v in zip(ts, vals))
        den = n * sxx - sx * sx
        if den < 1e-12:
            return 0.0, 0.0, float('inf')   # 时间轴无跨度，无法拟合
        v = (n * sxy - sx * sy) / den
        b = (sy - v * sx) / n
        rms = math.sqrt(sum((v * t + b - val) ** 2 for t, val in zip(ts, vals)) / n)
        return v, b, rms

    def fit(self, tail=None):
        """返回 (vx, vy, speed, theta_deg, ok) 或 None。

        tail : 仅用最近 tail 个点拟合（手推常带减速，越线前短段更接近匀速）；
               默认 None = 全部缓冲点。时间轴始终用绝对帧号，间隔不均不影响。
        None   : 样本不足（<3 点）
        ok=True: 匀速段，速度可信
        ok=False: 拟合残差超限（非匀速），应放弃本次抓拍
        theta_deg 为速度与 +x 轴夹角（度，-180~180），
        与 geometry.solve() 的 theta_deg 口径一致（图像坐标 y 向下）。
        """
        pts = list(self.buf)
        if tail is not None:
            pts = pts[-tail:]
        if len(pts) < 3:
            return None
        ts = [p[0] for p in pts]
        xs = [p[1] for p in pts]
        ys = [p[2] for p in pts]
        vx, _, rms_x = self._fit(ts, xs)
        vy, _, rms_y = self._fit(ts, ys)
        ok = max(rms_x, rms_y) <= self.max_residual
        speed = math.hypot(vx, vy)
        return vx, vy, speed, math.degrees(math.atan2(vy, vx)), ok
