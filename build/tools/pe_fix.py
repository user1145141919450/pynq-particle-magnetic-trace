# -*- coding: utf-8 -*-
"""pe_fix.py — **粒子引擎（particle_engine.v）的 Python 逐位镜像**（碰撞解算部分）。

用途：
  · 仿真向量生成：给 tb 造输入、算期望输出（RTL 与它**逐位**一致才算过）；
  · 语义对拍：与 realtime_ball.resolve_collisions 的浮点版比"行为是否同类"
    （位置推出、等质量弹性、异号湮灭——不要求同值，要求同语义）。

定点约定（与 traj_lane 同一套）：
  位置 = px × 256（Q10.8）；速度 = px/s × 256（Q11.8）。引擎里所有运算都是
  整数（算术右移 = Python 的 >>；除法 = 向零截断 = int(a/b)），本文件一行不差
  地照抄这些整数运算——RTL 侧写的就是它。
"""
import math

POS_ONE = 256
SC = 256                     # 位置/速度缩放
BALL_R = 11.0
TWO_R_S = int(round(2.0 * BALL_R * SC))          # 2R 的定点值 = 5632
TWO_R2 = TWO_R_S * TWO_R_S                        # (2R)^2 = 31,719,424


def isqrt_floor(n):
    """整数平方根（向下取整）——RTL 里是 21 轮恢复式开方，结果一致。"""
    return math.isqrt(int(n))


def div_trunc(a, b):
    """向零截断的整数除法（C 语义；Python // 是向下取整，别用错）。"""
    q = abs(int(a)) // abs(int(b))
    return q if (a >= 0) == (b >= 0) else -q


def resolve_pair(a, b):
    """一对粒子的一笔碰撞解算（就地改 a/b 的 x/y/vx/vy/alive）。

    与 realtime_ball.resolve_collisions 的对应关系（逐条注明）：
      · d >= 2R 或 d == 0           -> 不动（浮点版同款两个门槛）
      · charge 乘积 < 0             -> 双双湮灭（异号）
      · 否则等质量弹性：先沿法线把两球推到正好 2R，再交换法向速度分量。
    整数化差异（RTL 与镜像一致、对浮点版的近似）：d 用 floor 开方；nx/ny 用
    Q20 截断除法；推出量一步右移 21 合并取整；投影/回写速度全在 Q8 域做。
    返回 'skip' / 'annihilate' / 'elastic'。
    """
    ax, ay, bx, by = a['x'], a['y'], b['x'], b['y']
    dx = bx - ax
    dy = by - ay
    d2 = dx * dx + dy * dy
    if d2 >= TWO_R2 or d2 == 0:
        return 'skip'
    if a['chg'] * b['chg'] < 0:
        a['alive'] = False
        b['alive'] = False
        return 'annihilate'
    d = isqrt_floor(d2)
    nx = div_trunc(dx << 20, d)                  # Q20 单位法线 x
    ny = div_trunc(dy << 20, d)
    ov = TWO_R_S - d                             # 覆盖量（定点）
    a['x'] = ax - ((nx * ov) >> 21)              # 沿法线推出（各推一半）
    a['y'] = ay - ((ny * ov) >> 21)
    b['x'] = bx + ((nx * ov) >> 21)
    b['y'] = by + ((ny * ov) >> 21)
    avx, avy, bvx, bvy = a['vx'], a['vy'], b['vx'], b['vy']
    va = ((avx * nx) >> 20) + ((avy * ny) >> 20)    # 法向分量（Q8）
    vb = ((bvx * nx) >> 20) + ((bvy * ny) >> 20)
    dv = vb - va
    dxn = (dv * nx) >> 20
    dyn = (dv * ny) >> 20
    a['vx'] = avx + dxn
    a['vy'] = avy + dyn
    b['vx'] = bvx - dxn
    b['vy'] = bvy - dyn
    return 'elastic'


def _selftest():
    """语义对拍：与 realtime_ball 的浮点版逐例比对行为类别 + 量级接近度。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', 'software', 'board'))
    import realtime_ball as rb

    cases = [
        # (a_x, a_y, a_vx, a_vy, a_chg, b_x, b_y, b_vx, b_vy, b_chg)
        (480.0, 270.0, 400.0, 0.0, 1, 480.0, 280.0, -300.0, 0.0, 1),     # 正碰
        (480.0, 270.0, 400.0, 0.0, 1, 480.0, 280.0, -300.0, 0.0, -1),    # 异号湮灭
        (480.0, 270.0, 400.0, 0.0, 1, 500.0, 285.0, -100.0, -50.0, 1),   # 斜碰
        (480.0, 270.0, 400.0, 0.0, 1, 480.0, 293.0, -300.0, 0.0, 1),     # 恰好 23px -> 跳过
        (480.0, 270.0, 400.0, 0.0, 0, 480.0, 281.0, -300.0, 0.0, -1),    # 中性 x 负 -> 弹性
    ]
    bad = 0
    for (ax, ay, avx, avy, ac, bx, by, bvx, bvy, bc) in cases:
        A = dict(x=int(round(ax * SC)), y=int(round(ay * SC)),
                 vx=int(round(avx * SC)), vy=int(round(avy * SC)), chg=ac, alive=True)
        B = dict(x=int(round(bx * SC)), y=int(round(by * SC)),
                 vx=int(round(bvx * SC)), vy=int(round(bvy * SC)), chg=bc, alive=True)
        r = resolve_pair(A, B)
        # 浮点参考
        ra = rb.LiveBall(ax, ay, 0.0, 1.0, charge=ac)
        rbb = rb.LiveBall(bx, by, 0.0, 1.0, charge=bc)
        ra.vx, ra.vy = int(round(avx * SC)), int(round(avy * SC))
        rbb.vx, rbb.vy = int(round(bvx * SC)), int(round(bvy * SC))
        ra.x, ra.y = int(round(ax * SC)), int(round(ay * SC))
        rbb.x, rbb.y = int(round(bx * SC)), int(round(by * SC))
        rb.resolve_collisions([ra, rbb], None)
        # 类别
        if r == 'annihilate':
            ok = (not ra.alive) and (not rbb.alive) and (not A['alive'])
        elif r == 'skip':
            ok = (A['x'] == int(round(ax * SC))) and (ra.alive)
        else:
            # 位置/速度的量级接近（允许 2px / 8px/s 的整数化偏差）
            ok = (abs(A['x'] / SC - ra.x / SC) < 2.0
                  and abs(A['y'] / SC - ra.y / SC) < 2.0
                  and abs(A['vx'] / SC - ra.vx / SC) < 8.0
                  and abs(A['vy'] / SC - ra.vy / SC) < 8.0
                  and abs(B['vx'] / SC - rbb.vx / SC) < 8.0)
        print('%s  %s' % ('OK ' if ok else 'BAD', r))
        bad += 0 if ok else 1
    print('pe_fix 语义对拍：%d/%d' % (len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    import sys
    sys.exit(_selftest())
