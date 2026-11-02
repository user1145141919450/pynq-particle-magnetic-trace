# -*- coding: utf-8 -*-
"""test_vision.py — 红球检测 + 速度提取的合成数据黄金测试

黄金参考思路（与 test_geometry.py 同源，也是 skill/ 候选之一）：
用 PIL（与 OpenCV 无关的独立实现）按已知真值生成"摄像头帧"——
每帧红球的圆心/半径/颜色全部已知，叠加高斯像素噪声与曝光抖动模拟
真实拍摄；再让 detection.py + velocity.py 跑完整流水线，断言：
  1. 每帧检测圆心与真值偏差 ≤ 2.5 px；
  2. 多帧速度拟合与真值偏差 ≤ 0.5 px/frame、角度 ≤ 3°；
  3. 负例：非匀速段被残差校验拒绝（ok=False）、空帧返回 None、
     双红球取面积大者、大噪声下仍可检出。

运行：PYTHONPATH=E:/py_pkgs/vision;E:/py_pkgs/pillow python test_vision.py
产物：render/vision_*.png（检测结果可视化，供报告与调试）。
"""

import math
import os
import sys

# PC 专用：E 盘装的 opencv/numpy/pillow（迁移上板前删除本段——板载 cv2 已预装）
_PC_PKGS = [r'E:/py_pkgs/vision', r'E:/py_pkgs/pillow']
for _p in _PC_PKGS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detection import detect_red_ball
from velocity import VelocityTracker

W, H = 640, 480
PASS = []
FAIL = []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append((name, detail))
    print(f'[{"PASS" if cond else "FAIL"}] {name} {detail}')
    return cond


def near(a, b, tol):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def ang_diff(a, b):
    d = (a - b + 180.0) % 360.0 - 180.0
    return abs(d)


# ============ 1. 合成帧生成器（黄金参考：真值全部已知） ============

def synthetic_frame(P, color, radius, noise_sigma=2.5, jitter=3.0,
                    size=(W, H), seed=None):
    """生成一帧：白背景 + 位置 P 处半径 radius 的纯色圆 + 高斯噪声 + 曝光抖动。

    P/radius 即本帧真值；噪声与抖动模拟传感器噪声与曝光波动。
    """
    if seed is not None:
        np.random.seed(seed)
    from PIL import Image, ImageDraw
    img = Image.new('RGB', size, (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.ellipse([P[0] - radius, P[1] - radius, P[0] + radius, P[1] + radius],
              fill=color)
    arr = np.asarray(img, np.float32)[:, :, ::-1]      # PIL RGB → BGR（模拟摄像头输出）
    arr += np.random.uniform(-jitter, jitter)          # 每帧曝光抖动
    arr += np.random.normal(0.0, noise_sigma, arr.shape)  # 像素噪声
    return np.clip(arr, 0, 255).astype(np.uint8)


# ============ 2. 匀速用例：逐帧定位 + 速度拟合 vs 真值 ============
# (名称, 起点, 真速度, 球色, 半径, 帧数, 噪声σ, 曝光抖动)
CASES = [
    ('V1 水平匀速-暖红',      (60, 240), (28.0, 0.0),   (210, 20, 20), 22, 16, 2.5, 3.0),
    ('V2 斜向-暖红',          (60, 240), (18.0, -10.0), (205, 25, 20), 20, 16, 2.5, 3.0),
    ('V3 慢速-暖红',          (60, 240), (8.0, 3.0),    (210, 20, 20), 16, 16, 2.5, 3.0),
    ('V4 快速-洋红偏红',      (50, 240), (36.0, 10.0),  (200, 10, 30), 18, 16, 2.5, 3.0),
    ('V8 大噪声-暖红',        (60, 240), (20.0, -6.0),  (210, 20, 20), 20, 16, 8.0, 5.0),
]
POS_TOL = 2.5      # px
V_TOL = 0.5        # px/frame
ANG_TOL = 3.0      # 度

traj_render = []
for name, P0, v, color, radius, frames, sigma, jitter in CASES:
    tracker = VelocityTracker(maxlen=frames)
    pos_ok = True
    det_positions = []
    for i in range(frames):
        true_P = (P0[0] + v[0] * i, P0[1] + v[1] * i)
        det = detect_red_ball(synthetic_frame(true_P, color, radius, sigma, jitter))
        if det is None:
            pos_ok = False
            break
        det_positions.append(det[:2])
        tracker.push(i, det[0], det[1])
        if not near(det[:2], true_P, POS_TOL):
            pos_ok = False
    check(f'{name} 逐帧定位', pos_ok,
          f'{len(det_positions)}/{frames} 帧，最大偏差 '
          f'{max(math.hypot(d[0] - (P0[0] + v[0] * i), d[1] - (P0[1] + v[1] * i)) for i, d in enumerate(det_positions)):.2f}px'
          if det_positions else '检测丢失')
    res = tracker.fit()
    if res is None:
        check(f'{name} 速度拟合', False, '样本不足')
        continue
    vx, vy, speed, theta, ok = res
    speed_true = math.hypot(v[0], v[1])
    theta_true = math.degrees(math.atan2(v[1], v[0]))
    check(f'{name} 速度拟合',
          ok and abs(vx - v[0]) <= V_TOL and abs(vy - v[1]) <= V_TOL
          and ang_diff(theta, theta_true) <= ANG_TOL,
          f'真值 v=({v[0]:.1f},{v[1]:.1f}) |v|={speed_true:.1f} θ={theta_true:.1f}° → '
          f'拟合 v=({vx:.2f},{vy:.2f}) |v|={speed:.2f} θ={theta:.1f}° ok={ok}')
    traj_render.append((name, P0, v, color, radius, det_positions))


# ============ 3. 负例与鲁棒性 ============

# V5 非匀速（匀加速推球）：位置仍可检出，但速度拟合必须被残差校验拒绝
tr5 = VelocityTracker(maxlen=16)
a = (0.8, 0.0)
for i in range(16):
    P = (60 + 8 * i + 0.5 * a[0] * i * i, 240 + 0.5 * a[1] * i * i)
    det = detect_red_ball(synthetic_frame(P, (210, 20, 20), 22))
    if det is not None:
        tr5.push(i, det[0], det[1])
r5 = tr5.fit()
check('V5 非匀速段拒绝', r5 is not None and r5[4] is False,
      f'rms 超限 → ok={r5[4]}' if r5 else '拟合失败')

# V6 空帧（无红球）：返回 None
det6 = detect_red_ball(synthetic_frame((300, 200), (245, 245, 245), 0, 5.0, 0.0))
check('V6 无球帧返回 None', det6 is None, f'结果={det6}')

# V7 双红球：必须锁定面积大者
big, small = (24, (400, 240), (10.0, 0.0)), (12, (200, 240), (-5.0, 0.0))
v7_ok = True
for i in range(8):
    frame = synthetic_frame((0, 0), (0, 0, 0), 0)      # 白底
    from PIL import Image, ImageDraw
    img = Image.fromarray(frame)
    d = ImageDraw.Draw(img)
    for radius, P0, v in (big, small):
        d.ellipse([P0[0] + v[0] * i - radius, P0[1] - radius,
                   P0[0] + v[0] * i + radius, P0[1] + radius], fill=(210, 20, 20))
    arr = np.asarray(img, np.float32)[:, :, ::-1] + np.random.normal(0, 2.5, img.size[::-1] + (3,))
    det = detect_red_ball(np.clip(arr, 0, 255).astype(np.uint8))
    if det is None or not near(det[:2], (400 + 10 * i, 240), 3.0) or det[2] < 18:
        v7_ok = False
        break
check('V7 双红球取大者', v7_ok,
      f'锁定大球 {det[:2] if det else None}' if not v7_ok or i == 7 else '')


# ============ 4. 可视化渲染（供报告） ============

def render_vision(name, P0, v, color, radius, det_positions):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print(f'[SKIP] 无 Pillow，跳过渲染 {name}')
        return
    img = Image.new('RGB', (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.ellipse([P0[0] - radius, P0[1] - radius, P0[0] + radius, P0[1] + radius],
              fill=color)
    # 真值轨迹（绿线）
    n = len(det_positions)
    d.line([(P0[0] + v[0] * i, P0[1] + v[1] * i) for i in range(n)],
           fill=(30, 180, 60), width=2)
    # 检测位置（红点 + 连线）
    for x, y in det_positions:
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(220, 30, 30))
    d.line(det_positions, fill=(220, 30, 30), width=1)
    tr = VelocityTracker(maxlen=n)
    for i, p in enumerate(det_positions):
        tr.push(i, p[0], p[1])
    vx, vy, speed, theta, ok = tr.fit()
    info = (f'{name}: true v=({v[0]:.1f},{v[1]:.1f}) -> '
            f'fit v=({vx:.2f},{vy:.2f}) |v|={speed:.2f} theta={theta:.1f} deg')
    d.text((10, H - 20), info, fill=(20, 20, 20))
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render'),
                exist_ok=True)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render',
                       f'vision_{name.split()[0]}.png')
    img.save(out)
    print(f'[渲染] {out}')


for name, P0, v, color, radius, det_positions in traj_render:
    try:
        render_vision(name, P0, v, color, radius, det_positions)
    except Exception as e:
        print(f'[渲染跳过] {name}: {e}')

print(f'\n===== 汇总：{len(PASS)} 通过 / {len(FAIL)} 失败 =====')
if FAIL:
    print('失败项：')
    for name, detail in FAIL:
        print(f'  {name}: {detail}')
    sys.exit(1)
