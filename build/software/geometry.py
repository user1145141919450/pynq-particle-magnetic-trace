# -*- coding: utf-8 -*-
"""geometry.py — 矩形有界磁场中带电粒子圆周轨迹的几何判定核心

坐标约定：屏幕/图像像素坐标，原点左上、x 向右、y 向下。
物理模型：洛伦兹力 F = q·v×B；圆周半径 r = k·|v|/B（k = m/q 标定常数，
把真实小球像素速度 |v| (px/frame) 映射成演示半径 r (px)）。
turning = sign(q·Bz)，+1 对应正电荷、B 垂直屏幕向外；圆心
O = P0 + turning·r·n̂，其中 n̂ = (vy, -vx)/|v|。

判定三态（对应显示颜色）：
  'exit'     穿出磁场（轨迹与上/右/下边相交）        -> 绿色
  'critical' 临界相切（轨迹与某边相切，切点在该边上） -> 橙色
  'return'   打回入射边（先回到左边界）              -> 红色
  'invalid'  输入不合法
"""

import math
from dataclasses import dataclass, field as dc_field

TANGENT_EPS = 1.0   # |圆心到边线距离 - r| < 1px 视为相切
SEG_TOL = 0.5       # 交点落在线段内的容差 (px)
ENTRY_TOL = 1.5     # 入射点在左边界上的容差 (px)

VERDICT_COLORS = {
    'exit':     (0, 190, 60),
    'critical': (255, 140, 0),
    'return':   (230, 50, 50),
    'invalid':  (128, 128, 128),
}


@dataclass
class Field:
    """矩形磁场区域。left 边为入射边界。"""
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def on_left_edge(self, x: float, y: float, tol: float = ENTRY_TOL) -> bool:
        return (abs(x - self.left) <= tol
                and self.top - tol <= y <= self.bottom + tol)

    def point_in_segment(self, edge: str, x: float, y: float,
                         tol: float = SEG_TOL) -> bool:
        if edge in ('top', 'bottom'):
            return self.left - tol <= x <= self.right + tol
        return self.top - tol <= y <= self.bottom + tol


@dataclass
class HitEvent:
    """轨迹与边界的一个相交/相切事件"""
    edge: str            # 'top' | 'right' | 'bottom' | 'left'
    x: float
    y: float
    delta_theta: float   # 从入射点沿运动方向到达该点的扫角 (rad, >= 0)
    is_tangent: bool = False


@dataclass
class Trajectory:
    """一次入射求解的全部结果"""
    r: float
    center: tuple
    theta0: float        # 入射点在圆上的参数角
    omega_dir: int       # +1 / -1，沿运动方向角度增减方向
    theta_deg: float     # 入射角：速度与 +x 轴（边界法线）夹角，单位度
    speed: float
    hits: list = dc_field(default_factory=list)   # 按 delta_theta 升序
    verdict: str = 'invalid'
    exit_point: tuple = None
    arc_points: list = dc_field(default_factory=list)  # 入射点->出射/打回点的圆弧采样
    note: str = ''


def _wrap2pi(a: float) -> float:
    a = a % (2.0 * math.pi)
    return a


def _circle_line_hits(edge, ox, oy, r, f: Field):
    """圆与某条边所在直线的交点（落在该边线段内的），返回 [(x, y, is_tangent), ...]"""
    out = []
    if edge in ('top', 'bottom'):
        ye = f.top if edge == 'top' else f.bottom
        dy = ye - oy
        d2 = r * r - dy * dy
        tangent = abs(abs(dy) - r) < TANGENT_EPS
        if tangent:
            xs = [ox]
        elif d2 < 0:
            return out
        else:
            s = math.sqrt(d2)
            xs = [ox - s, ox + s]
        for x in xs:
            if f.point_in_segment(edge, x, ye):
                out.append((x, ye, tangent))
    else:  # 'left' / 'right'
        xe = f.left if edge == 'left' else f.right
        dx = xe - ox
        d2 = r * r - dx * dx
        tangent = abs(abs(dx) - r) < TANGENT_EPS
        if tangent:
            ys = [oy]
        elif d2 < 0:
            return out
        else:
            s = math.sqrt(d2)
            ys = [oy - s, oy + s]
        for y in ys:
            if f.point_in_segment(edge, xe, y):
                out.append((xe, y, tangent))
    return out


def solve(P0, vx, vy, B, field: Field, k: float = 10.0,
          turning: int = 1, arc_samples: int = 512) -> Trajectory:
    """求解一次入射。

    P0   : (x, y) 入射点（应在左边界上）
    vx,vy: 入射速度 (px/frame)，要求 vx > 0（指向场内）
    B    : 磁感应强度（演示单位，越大半径越小）
    k    : m/q 标定常数，r = k·|v|/B
    turning: sign(q·Bz)，+1 正电荷/B 出屏
    """
    tr = Trajectory(r=0.0, center=(0.0, 0.0), theta0=0.0, omega_dir=1,
                    theta_deg=0.0, speed=0.0)
    x0, y0 = float(P0[0]), float(P0[1])

    # ---- 输入合法性 ----
    if B <= 0:
        tr.note = 'B 必须为正'
        return tr
    speed = math.hypot(vx, vy)
    if speed < 1e-9:
        tr.note = '速度为零'
        return tr
    if vx <= 0:
        tr.note = '速度未指向场内 (vx<=0)'
        return tr
    if not field.on_left_edge(x0, y0):
        tr.note = f'入射点不在左边界上: ({x0}, {y0})'
        return tr

    # ---- 圆周参数 ----
    r = k * speed / B
    nx, ny = vy / speed, -vx / speed          # v 顺时针转 90° 的单位法向
    ox, oy = x0 + turning * r * nx, y0 + turning * r * ny
    theta0 = math.atan2(y0 - oy, x0 - ox)
    # 运动方向：v 与圆在 theta0 处切向 t=(-sin, cos) 同向则角度增大
    dot = vx * (-math.sin(theta0)) + vy * math.cos(theta0)
    omega_dir = 1 if dot > 0 else -1

    tr.r = r
    tr.center = (ox, oy)
    tr.theta0 = theta0
    tr.omega_dir = omega_dir
    tr.speed = speed
    tr.theta_deg = math.degrees(math.atan2(vy, vx))

    # ---- 收集所有边界事件 ----
    events: list[HitEvent] = []
    for edge in ('top', 'right', 'bottom', 'left'):
        for (x, y, tangent) in _circle_line_hits(edge, ox, oy, r, field):
            th = math.atan2(y - oy, x - ox)
            dth = _wrap2pi((th - theta0) * omega_dir)
            # 左边界上的入射点自身（dth≈0）不算事件
            if edge == 'left' and dth < 1e-3:
                continue
            events.append(HitEvent(edge, x, y, dth, tangent))

    # 合并重复点（角点被两条边同时记录）
    uniq: list[HitEvent] = []
    for e in sorted(events, key=lambda e: e.delta_theta):
        dup = any(math.hypot(e.x - u.x, e.y - u.y) < SEG_TOL * 2
                  and abs(e.delta_theta - u.delta_theta) < 1e-3
                  for u in uniq)
        if not dup:
            uniq.append(e)
    tr.hits = uniq

    # ---- 判定 ----
    if not uniq:
        tr.verdict = 'invalid'
        tr.note = '未找到任何边界事件（数值异常）'
        return tr

    first = uniq[0]
    tr.exit_point = (first.x, first.y)
    if first.edge == 'left':
        tr.verdict = 'return'
    elif first.is_tangent:
        tr.verdict = 'critical'
    else:
        tr.verdict = 'exit'

    # ---- 圆弧采样（入射点 -> 首个事件点） ----
    theta_end = theta0 + omega_dir * first.delta_theta
    n = max(8, arc_samples)
    tr.arc_points = [(ox + r * math.cos(theta0 + omega_dir * first.delta_theta * i / (n - 1)),
                      oy + r * math.sin(theta0 + omega_dir * first.delta_theta * i / (n - 1)))
                     for i in range(n)]
    return tr


def verdict_color(verdict: str) -> tuple:
    return VERDICT_COLORS.get(verdict, (128, 128, 128))
