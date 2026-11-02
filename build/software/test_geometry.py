# -*- coding: utf-8 -*-
"""test_geometry.py — geometry.py 的黄金参考测试

两类验证：
1. 手工构造的解析用例：几何参数按解析解预先算出（圆心/半径/交点全部精确），
   断言判定结果与出射点坐标（比赛要求的"参考结果比对"）。
2. 随机用例暴力采样：以极小角度步长沿圆弧走样 + 二分精定位独立求出首个边界
   交点，与解析算法互证（黄金参考）。

运行：PYTHONPATH=E:/py_pkgs/pillow python test_geometry.py
产物：render/*.png 可视化结果（供报告与调试）。
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geometry import Field, solve, verdict_color

FIELD = Field(100, 100, 580, 420)   # 480x320 的虚拟磁场
K = 10.0
PASS = []
FAIL = []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append((name, detail))
    print(f'[{"PASS" if cond else "FAIL"}] {name} {detail}')
    return cond


def near(a, b, tol=0.5):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


# ============ 1. 手工解析用例 ============
# 约定：solve() 中 turning=+1 时 O = P0 + r·n̂，n̂=(vy,-vx)/|v|；
#       以下各例先按解析几何构造 O/P0/v，再反推应使用的 turning。

# T1 水平入射, turning=-1: O=(100,1260) r=1000 -> 从右边界 (580,382.73) 穿出
tr = solve((100, 260), 20, 0, B=0.2, field=FIELD, k=K, turning=-1)
check('T1 出射-右边界', tr.verdict == 'exit' and near(tr.exit_point, (580, 382.73), 1.0),
      f'verdict={tr.verdict} exit={tuple(round(c,1) for c in tr.exit_point)} r={tr.r:.1f}')

# T2 右偏上入射, turning=+1: O=(-150,-740) r=1030.78 -> 从上边界 (447.42,100) 穿出
tr = solve((100, 260), 20, -5, B=0.2, field=FIELD, k=K, turning=+1)
check('T2 出射-上边界', tr.verdict == 'exit' and near(tr.exit_point, (447.42, 100), 1.5),
      f'verdict={tr.verdict} exit={tuple(round(c,1) for c in tr.exit_point)}')

# T3 右偏下入射, turning=-1: O=(-150,1260) -> 从下边界 (447.42,420) 穿出
tr = solve((100, 260), 20, 5, B=0.2, field=FIELD, k=K, turning=-1)
check('T3 出射-下边界', tr.verdict == 'exit' and near(tr.exit_point, (447.42, 420), 1.5),
      f'verdict={tr.verdict} exit={tuple(round(c,1) for c in tr.exit_point)}')

# T4 打回入射边: O=(-60,260) r=200, 入射 (100,380) v=(0.6,-0.8), turning=+1
#    与左边界两交点 (100,380)/(100,140)，圆弧全程在场内 -> 从 (100,140) 打回
tr = solve((100, 380), 0.6, -0.8, B=K * 1.0 / 200, field=FIELD, k=K, turning=+1)
check('T4 打回入射边', tr.verdict == 'return' and near(tr.exit_point, (100, 140), 1.0),
      f'verdict={tr.verdict} exit={tuple(round(c,1) for c in tr.exit_point)} r={tr.r:.1f}')

# T5 相切临界: O=(300,320) r=220, 上边界切点 (300,100)
#    入射 P0=(100, 320-√8400)=(100,228.35), v=(√8400,-200), turning=-1
h = math.sqrt(8400.0)
tr = solve((100, 320 - h), h, -200, B=K * math.hypot(h, 200) / 220,
           field=FIELD, k=K, turning=-1)
check('T5 相切-上边界', tr.verdict == 'critical' and near(tr.exit_point, (300, 100), 1.0),
      f'verdict={tr.verdict} exit={tuple(round(c,1) for c in tr.exit_point)} r={tr.r:.1f}')

# T6 假相切陷阱: 圆与上边界所在直线相切于 (600,100)，但切点在右边界之外(600>580)
#    O=(600,651) r=551, 入射 P0=(100, 651-√53601)=(100,419.48), v=(√53601,-500),
#    turning=-1。轨迹应先在 (580,100.36) 穿出右边界，不得误判为相切。
h = math.sqrt(53601.0)
tr = solve((100, 651 - h), h, -500, B=K * math.hypot(h, 500) / 551,
           field=FIELD, k=K, turning=-1)
check('T6 假相切不误判', tr.verdict == 'exit' and near(tr.exit_point, (580, 100.36), 1.5),
      f'verdict={tr.verdict} exit={tuple(round(c,1) for c in tr.exit_point)}')

# T7 非法输入
for name, args in [
    ('T7a 速度背离场内', ((100, 260), -20, 0, 0.2)),
    ('T7b 平行边界入射', ((100, 260), 0, 20, 0.2)),
    ('T7c 入射点不在左边界', ((200, 260), 20, 0, 0.2)),
    ('T7d B 非正', ((100, 260), 20, 0, 0.0)),
]:
    tr = solve(args[0], args[1], args[2], B=args[3], field=FIELD, k=K)
    check(name, tr.verdict == 'invalid', f'verdict={tr.verdict} note={tr.note}')


# ============ 2. 随机暴力采样黄金参考 ============

def brute_force(P0, vx, vy, B, field, k, turning, dtheta=2e-4):
    """独立实现：沿圆弧以 dtheta 步长走样，检测首个越界点并二分精定位。"""
    x0, y0 = float(P0[0]), float(P0[1])
    speed = math.hypot(vx, vy)
    r = k * speed / B
    nx, ny = vy / speed, -vx / speed
    ox, oy = x0 + turning * r * nx, y0 + turning * r * ny
    theta0 = math.atan2(y0 - oy, x0 - ox)
    dot = vx * (-math.sin(theta0)) + vy * math.cos(theta0)
    w = 1 if dot > 0 else -1

    def pt(th):
        return ox + r * math.cos(th), oy + r * math.sin(th)

    def inside(p, tol=1e-6):
        return (field.left + tol <= p[0] <= field.right - tol
                and field.top + tol <= p[1] <= field.bottom - tol)

    th_prev, p_prev = theta0, pt(theta0)
    for i in range(1, int(2 * math.pi / dtheta) + 2):
        th = theta0 + w * i * dtheta
        p = pt(th)
        if inside(p_prev) and not inside(p):
            lo, hi = th_prev, th
            for _ in range(40):
                mid = (lo + hi) / 2
                if inside(pt(mid)):
                    lo = mid
                else:
                    hi = mid
            x, y = pt(hi)
            if abs(x - field.left) < 1.5:
                edge = 'left'
            elif abs(x - field.right) < 1.5:
                edge = 'right'
            elif abs(y - field.top) < 1.5:
                edge = 'top'
            else:
                edge = 'bottom'
            d = abs(field.top - oy) if edge == 'top' else \
                abs(field.bottom - oy) if edge == 'bottom' else \
                abs(field.right - ox) if edge == 'right' else abs(field.left - ox)
            return (x, y), edge, abs(d - r) < 2.0   # 临界区标记
        th_prev, p_prev = th, p
    return None, None, False


random.seed(42)
total = strict_ok = border = mismatch = 0
mismatch_cases = []
for _ in range(400):
    y0 = random.uniform(FIELD.top + 5, FIELD.bottom - 5)
    ang = random.uniform(-1.45, 1.45)          # 速度指向场内，偏离法线 <83°
    sp = random.uniform(4, 40)
    vx, vy = sp * math.cos(ang), sp * math.sin(ang)
    B = 10 ** random.uniform(-1.2, 0.7)        # 半径覆盖 ~3px 到 ~2500px
    turning = random.choice([1, -1])
    tr = solve((FIELD.left, y0), vx, vy, B, FIELD, K, turning)
    bp, bedge, borderline = brute_force((FIELD.left, y0), vx, vy, B, FIELD, K, turning)
    total += 1
    if borderline or tr.verdict == 'critical':
        border += 1
        continue
    ok = False
    if tr.verdict in ('exit', 'return') and bp is not None:
        # 与 brute_force 完全一致的边分类优先级，避免角点处标签错配
        xp, yp = tr.exit_point
        if tr.verdict == 'return':
            exp_edge = 'left'
        elif abs(xp - FIELD.right) < 1.5:
            exp_edge = 'right'
        elif abs(yp - FIELD.top) < 1.5:
            exp_edge = 'top'
        elif abs(yp - FIELD.bottom) < 1.5:
            exp_edge = 'bottom'
        else:
            exp_edge = 'left'
        ok = exp_edge == bedge and near(tr.exit_point, bp, 2.0)
    if ok:
        strict_ok += 1
    else:
        mismatch += 1
        mismatch_cases.append(((FIELD.left, y0), round(vx, 2), round(vy, 2),
                               round(B, 3), turning, tr.verdict, tr.exit_point,
                               bedge, bp and tuple(round(c, 1) for c in bp)))

print(f'\n随机黄金参考：共 {total} 例 | 严格一致 {strict_ok} | '
      f'临界区跳过 {border} | 不一致 {mismatch}')
if mismatch_cases:
    print('不一致样例：')
    for c in mismatch_cases[:5]:
        print('  ', c)
check('随机暴力采样一致率 100%', mismatch == 0)


# ============ 3. 可视化渲染 ============

def render(tr, P0, B, fname, field=FIELD):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print(f'[SKIP] 无 Pillow，跳过渲染 {fname}')
        return
    W, H = 700, 520
    img = Image.new('RGB', (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    # 磁场区：蓝色深浅随 B（B 越大越深）
    shade = int(120 + 90 * min(1.0, B / 0.5))
    d.rectangle([field.left, field.top, field.right, field.bottom],
                fill=(235 - shade // 4, 235 - shade // 5, shade))
    d.rectangle([field.left, field.top, field.right, field.bottom],
                outline=(60, 60, 160), width=2)
    # 入射边界标红强调
    d.line([(field.left, field.top - 6), (field.left, field.bottom + 6)],
           fill=(220, 60, 60), width=3)
    # 圆弧轨迹（按判定着色）
    if tr.arc_points:
        d.line([(x, y) for x, y in tr.arc_points],
               fill=verdict_color(tr.verdict), width=3)
    # 完整参考圆（浅灰）
    ox, oy = tr.center
    if tr.r > 0:
        d.ellipse([ox - tr.r, oy - tr.r, ox + tr.r, oy + tr.r],
                  outline=(205, 205, 205), width=1)
    # 入射点（红点）/ 出射点（X 标记）
    d.ellipse([P0[0] - 5, P0[1] - 5, P0[0] + 5, P0[1] + 5], fill=(200, 0, 0))
    if tr.exit_point:
        x, y = tr.exit_point
        d.line([(x - 7, y - 7), (x + 7, y + 7)], fill=verdict_color(tr.verdict), width=3)
        d.line([(x - 7, y + 7), (x + 7, y - 7)], fill=verdict_color(tr.verdict), width=3)
    info = (f'v={tr.speed:.1f} px/f  theta={tr.theta_deg:+.1f} deg  '
            f'r={tr.r:.1f} px  B={B:.2f}  verdict={tr.verdict}')
    d.text((10, H - 24), info, fill=(20, 20, 20))
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render'),
                exist_ok=True)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render', fname)
    img.save(out)
    print(f'[渲染] {out}')


cases = [
    ('T1_exit_right.png',  (100, 260), (20, 0),     0.2,  -1),
    ('T2_exit_top.png',    (100, 260), (20, -5),    0.2,  +1),
    ('T3_exit_bottom.png', (100, 260), (20, 5),     0.2,  -1),
    ('T4_return.png',      (100, 380), (0.6, -0.8), K * 1.0 / 200, +1),
    ('T5_critical.png',    (100, 320 - math.sqrt(8400.0)),
     (math.sqrt(8400.0), -200), K * math.hypot(math.sqrt(8400.0), 200) / 220, -1),
]
for fname, P0, v, B, turning in cases:
    tr = solve(P0, v[0], v[1], B, FIELD, K, turning)
    try:
        render(tr, P0, B, fname)
    except Exception as e:
        print(f'[渲染跳过] {fname}: {e}')

print(f'\n===== 汇总：{len(PASS)} 通过 / {len(FAIL)} 失败 =====')
if FAIL:
    print('失败项：')
    for name, detail in FAIL:
        print(f'  {name}: {detail}')
    sys.exit(1)
