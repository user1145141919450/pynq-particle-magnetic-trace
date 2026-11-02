# -*- coding: utf-8 -*-
"""v3_fixedpoint.py — V3 并行轨迹束的**定点黄金模型**（RTL 的逐位对照基准）

为什么要有它（2026-09-27，V3 第一步）：
  V3 要把 engine.py 里 `tangent_scan()` 的串行扫描搬到 PL 里**并行**做
  （源码注释原文："PC 端串行版；上板后由 PL 并行完成"）。上板之前必须回答两件事：
    ① 定点化之后**精度够不够**（浮点黄金 vs 定点，端点差多少像素？判定会不会翻？）
    ② RTL 写出来对不对（需要一份**位精确**的参考 —— 用 float 对照是没法查位错的）
  所以这里按 RTL 的算法规格用**整数**重写一遍，逐位可复现：
    · 位置 x,y  : Q10.8 无符号 18bit（0..1023.996 px，分辨率 1/256 px）
    · 速度 vx,vy: Q11.8 有符号 20bit（±1024 px/s）
    · 每步转角 a: Q2.20 有符号 23bit（±2 rad，分辨率 ~1e-6）
    · 旋转用二阶小角近似：cos≈1-a²/2, sin≈a（|a|<=0.0125 rad 时四阶项 <1e-9）
    · 位移用常数乘：dx = (vx * 273) >> 16   （273/65536 = 1/240.0 = dt）
  用法：
    python tools/v3_fixedpoint.py            # 精度自检：定点 vs engine.py 浮点
    python tools/v3_fixedpoint.py --dump s.json   # 导出场景与期望结果（给 RTL TB 用）
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'game_pc'))

FIELD_W, FIELD_H = 960, 540
BALL_R = 11.0
DT = 1.0 / 240.0
K_RADIUS = 1.0
OMEGA_SIGN = -1.0

# ---- 定点格式 ----
POS_F = 8                      # 位置小数位
POS_ONE = 1 << POS_F           # 256
VEL_F = 8
VEL_ONE = 1 << VEL_F
ANG_F = 20                     # 转角小数位
ANG_ONE = 1 << ANG_F
DT_MUL = 273                   # round(2^16/240) = 273 -> dx = (v*273)>>16


def to_pos(f):
    return int(round(f * POS_ONE))


def to_vel(f):
    return int(round(f * VEL_ONE))


def to_ang(f):
    return int(round(f * ANG_ONE))


def pos_f(v):
    return v / float(POS_ONE)


def vel_f(v):
    return v / float(VEL_ONE)


def ang_f(v):
    return v / float(ANG_ONE)


# ---- ★★ 2026-10-16 布置 PLACE（障碍物/传送门）公用件（照抄 game_pc/engine.py）----
PORTAL_R = 18.0                          # 传送门半径（游戏 px）
TP_CD_STEPS = int(round(0.35 / DT))      # 穿出冷却 0.35s（防刚穿出又被吸回）


def obstacle_aabb(ob, ball_r=BALL_R):
    """旋转矩形 -> **扩张后的轴对齐包围盒**（RTL 的**便宜预过滤**）。

    ★ 2026-10-19 用户定案：真正的碰撞体是**旋转矩形**（RTL 用它算局部坐标）；
      这个 AABB 只用来快速跳过"肯定不在障碍附近"的步，**不再当碰撞体**。
      真球物理用下面的精确旋转矩形 obstacle_push_out（浮点）。
    ob: engine.Obstacle（.cx/.cy/.hw/.hh/.ang[rad]）
    """
    ca, sa = abs(math.cos(ob.ang)), abs(math.sin(ob.ang))
    ax = ob.hw * ca + ob.hh * sa + ball_r
    ay = ob.hw * sa + ob.hh * ca + ball_r
    return (ob.cx - ax, ob.cy - ay, ob.cx + ax, ob.cy + ay)


def clamp_aabb(ab, fw=FIELD_W, fh=FIELD_H):
    """把 AABB 夹到场区内。★ 2026-10-19 上板实锤：障碍贴边时 AABB 会越界
    （例如 y0=-3），写进 RTL 的 **18 位无符号**寄存器会回绕成巨大值 →
    `y>=y0` 永远不成立，PL/PE 完全看不见障碍（软件预览用 Python 有符号数没事，
    所以只坏 PL 侧）。所有写寄存器的路径必须先过这里。"""
    return (max(0.0, min(float(fw), float(ab[0]))),
            max(0.0, min(float(fh), float(ab[1]))),
            max(0.0, min(float(fw), float(ab[2]))),
            max(0.0, min(float(fh), float(ab[3]))))


def obstacle_push_out(ob, x, y):
    """球（圆）对**旋转矩形**障碍：从穿透最浅的一侧推出。

    ob = (cx, cy, w, h, ang_deg)；返回 (新x, 新y, 法线nx, 法线ny)（世界系）或 None。
    照抄 engine.Obstacle.push_out（扩张 BALL_R；浮点游戏坐标域算——定点<->浮点
    转换由调用方做，realtime_ball 与 lane_fixed **共用这一份**，别复制第二份）。"""
    ang = math.radians(ob[4])
    ca, sa = math.cos(-ang), math.sin(-ang)
    dx, dy = x - ob[0], y - ob[1]
    lx, ly = dx * ca - dy * sa, dx * sa + dy * ca          # 局部坐标
    ehw, ehh = ob[2] / 2.0 + BALL_R, ob[3] / 2.0 + BALL_R
    if abs(lx) >= ehw or abs(ly) >= ehh:
        return None
    px, py = ehw - abs(lx), ehh - abs(ly)
    if px < py:
        sx, sy = (1.0 if lx > 0 else -1.0), 0.0
        lx = ehw if lx > 0 else -ehw
    else:
        sx, sy = 0.0, (1.0 if ly > 0 else -1.0)
        ly = ehh if ly > 0 else -ehh
    ca2, sa2 = math.cos(ang), math.sin(ang)
    wx, wy = lx * ca2 - ly * sa2, lx * sa2 + ly * ca2      # 位置回世界系
    nx, ny = sx * ca2 - sy * sa2, sx * sa2 + sy * ca2      # 法线回世界系
    return ob[0] + wx, ob[1] + wy, nx, ny


# ---- 一步：与 RTL 逐位一致 ----
def step_fixed(x, y, vx, vy, ang):
    """输入输出都是定点整数。ang 是 Q2.20。

    RTL 里同一拍做的事（见 rtl/v3_bundle/traj_lane.v）：
        ang2 = (ang*ang) >> ANG_F          # Q2.20 * Q2.20 -> 右移回 Q2.20
        half = ang2 >> 1                   # a^2/2
        cos  = ANG_ONE - half              # Q2.20，≈1
        sin  = ang                         # Q2.20
        nvx  = ((vx*cos) >> ANG_F) - ((vy*sin) >> ANG_F)
        nvy  = ((vx*sin) >> ANG_F) + ((vy*cos) >> ANG_F)
        x   += (nvx * DT_MUL) >> 16
        y   += (nvy * DT_MUL) >> 16
    注意乘法都要先算再右移（保持 Q 格式），右移用**算术**移位（Python 的 >> 对负数
    是算术移位，与 Verilog 的 >>> 一致）。
    """
    # ★ 右移一律**加半 LSB 再移**（round 而不是 floor）。第一版用纯 floor，每步
    #   最多截掉 1/256 px，600 步累积成 ~2px 的系统性偏慢 —— 实测逐步对拍看到的
    #   0.0033px/步 增长就是它。加半 LSB 后偏置消除，误差降回 ~0.2px 量级。
    HALF_A = 1 << (ANG_F - 1)
    ang2 = (ang * ang + HALF_A) >> ANG_F
    cosv = ANG_ONE - (ang2 >> 1)
    sinv = ang
    nvx = ((vx * cosv + HALF_A) >> ANG_F) - ((vy * sinv + HALF_A) >> ANG_F)
    nvy = ((vx * sinv + HALF_A) >> ANG_F) + ((vy * cosv + HALF_A) >> ANG_F)
    x = x + ((nvx * DT_MUL + 32768) >> 16)
    y = y + ((nvy * DT_MUL + 32768) >> 16)
    return x, y, nvx, nvy


def lane_fixed(x0, y0, angle_deg, speed, charge, fields, holes, side,
               steps=1200, max_bounce=6, trail_gap=8.0, max_pts=600,
               obstacles=(), portals=()):
    """一条轨迹（定点整数版，照抄 engine.World.preview 的逻辑）。

    fields: [(x0,y0,x1,y1,B), ...] 块表；holes: [dict(edge,c,w,target)]
    返回 dict(x, y, res, hole, steps, bounces, pts)
    """
    a = math.radians(angle_deg)
    x, y = to_pos(x0), to_pos(y0)
    vx, vy = to_vel(speed * math.cos(a)), to_vel(speed * math.sin(a))
    ballr = to_pos(BALL_R)
    fw, fh = to_pos(FIELD_W), to_pos(FIELD_H)
    pts = [(x, y)]
    bounces = 0
    res, hole_hit = 'none', None
    stop_reason = 'steps'
    # ★★ 2026-10-27 多件：3 障碍 / 2 对门。与 RTL traj_lane 同一套整数语义
    #   （按列表顺序逐件作用；障碍全部走完再走门；门命中即 break）。
    _obs = []
    for _o in list(obstacles)[:3]:
        _ang = math.radians(float(_o[4]))
        _ca = int(round(math.cos(-_ang) * ANG_ONE))
        _sa = int(round(math.sin(-_ang) * ANG_ONE))
        _obs.append((to_pos(_o[0]), to_pos(_o[1]),
                     to_pos(float(_o[2]) + BALL_R),
                     to_pos(float(_o[3]) + BALL_R), _ca, _sa))
    _pt = []
    _npt = len(portals or [])
    for _i, _p in enumerate(list(portals)[:4]):
        # 1 对门（2 元）沿用旧口径（pair=另一端）；多对用显式 pair 下标
        if _npt <= 2:
            _pair = _i ^ 1
        else:
            _pair = int(_p[2]) if len(_p) > 2 else (_i ^ 1)
        _pt.append((to_pos(_p[0]), to_pos(_p[1]), _pair))
    _tp_cd = 0
    HALF_A = 1 << (ANG_F - 1)
    for n in range(steps):
        # ★ 块查找必须与 engine.Block.contains **完全一致**：闭区间 + 表中靠前优先。
        #   第一版写成半开区间 [x0,x1)，起点 (100,270) 恰好在两块共享边 y=270 上
        #   -> 漏掉第一块、命中第二块（B 的符号相反）-> 从第 0 步转向就反了，
        #   300 步后终点差 537px（看起来像"精度崩了"，其实是边界条件写错）。
        b = 0.0
        for (bx0, by0, bx1, by1, bb) in fields:
            if bx0 <= pos_f(x) <= bx1 and by0 <= pos_f(y) <= by1:
                b = bb
                break
        if abs(b) > 1e-9:
            ang = to_ang(OMEGA_SIGN * charge * b / K_RADIUS * DT)
        else:
            ang = 0
        x, y, vx, vy = step_fixed(x, y, vx, vy, ang)
        if x < ballr or x > fw - ballr:
            edge = 'left' if x < ballr else 'right'
            h = _hole_at(holes, edge, pos_f(y))
            if h and h['target'] == side:
                pts.append((x, y))
                res, hole_hit = 'goal', h
                stop_reason = 'goal'
                break
            x = ballr if x < ballr else fw - ballr
            vx = -vx
            bounces += 1
            stop_reason = 'wall'
        if y < ballr or y > fh - ballr:
            edge = 'top' if y < ballr else 'bottom'
            h = _hole_at(holes, edge, pos_f(x))
            if h and h['target'] == side:
                pts.append((x, y))
                res, hole_hit = 'goal', h
                stop_reason = 'goal'
                break
            y = ballr if y < ballr else fh - ballr
            vy = -vy
            bounces += 1
            stop_reason = 'wall'
        # ---- ★ 2026-10-19 布置 PLACE（PL 原生近似）：障碍 -> 传送门 ----
        #   RTL 在"本步继续"（没进洞/没弹尽/没超步数）时执行 OP_PHYS；
        #   步数用尽那一步 RTL 仍然先跑 phys 再在下一次判定结束（见 traj_lane
        #   OP_JUDGE 的 step_i>=ms_r 分支），所以这里只在弹尽时跳过。
        if bounces <= max_bounce:
            # ★★ 多件：障碍按列表顺序逐件作用（每件都用**上一件推出后**的位置/速度）
            for _ob in _obs:
                # 旋转矩形（与 RTL 同一套整数语义）：局部坐标 -> 最浅面推出 ->
                # 世界系回投 + 法线反射。HALF_A = 1<<19（Q2.20 的加半 LSB）。
                _ox, _oy, _ehw, _ehh, _ca, _sa = _ob
                _dxo, _dyo = x - _ox, y - _oy
                _lx = (_dxo * _ca - _dyo * _sa + HALF_A) >> ANG_F
                _ly = (_dxo * _sa + _dyo * _ca + HALF_A) >> ANG_F
                if abs(_lx) < _ehw and abs(_ly) < _ehh:
                    _px = _ehw - abs(_lx)
                    _py = _ehh - abs(_ly)
                    if _px < _py:
                        _sx = 1 if _lx > 0 else -1
                        _lx = _sx * _ehw
                        _sy = 0
                    else:
                        _sy = 1 if _ly > 0 else -1
                        _ly = _sy * _ehh
                        _sx = 0
                    _wx = (_lx * _ca + _ly * _sa + HALF_A) >> ANG_F
                    _wy = (-_lx * _sa + _ly * _ca + HALF_A) >> ANG_F
                    x = _ox + _wx
                    y = _oy + _wy
                    _nx = _sx * _ca + _sy * _sa
                    _ny = -_sx * _sa + _sy * _ca
                    _dot = (vx * _nx + vy * _ny + HALF_A) >> ANG_F
                    if _dot < 0:
                        vx = vx - 2 * ((_dot * _nx + HALF_A) >> ANG_F)
                        vy = vy - 2 * ((_dot * _ny + HALF_A) >> ANG_F)
            # ★ 冷却口径与 PS realtime_ball / RTL 一致：**先减再判**（0=可穿）。
            #   旧版是"0 才判、否则减"，比 PS/RTL 晚一步，多件对拍会露出来。
            if _tp_cd > 0:
                _tp_cd -= 1
            if _tp_cd == 0:
                if len(_pt) >= 2:
                    # ★ 多对门：按列表顺序查，命中即穿到配对门并 break
                    _pr2 = to_pos(PORTAL_R)
                    _pr2 = _pr2 * _pr2
                    for _ptx in _pt:
                        _gx, _gy, _pair = _ptx
                        _dxp, _dyp = x - _gx, y - _gy
                        if _dxp * _dxp + _dyp * _dyp <= _pr2:
                            _ot = _pt[int(_pair) % len(_pt)]
                            _of = to_pos(PORTAL_R) + ballr
                            if abs(vx) >= abs(vy):
                                x = min(max(_ot[0] + (_of if vx >= 0 else -_of),
                                            ballr), fw - ballr)
                                y = _ot[1]
                            else:
                                x = _ot[0]
                                y = min(max(_ot[1] + (_of if vy >= 0 else -_of),
                                            ballr), fh - ballr)
                            _tp_cd = TP_CD_STEPS
                            break
        if math.hypot(pos_f(x) - pos_f(pts[-1][0]), pos_f(y) - pos_f(pts[-1][1])) > trail_gap:
            pts.append((x, y))
        if bounces > max_bounce:
            stop_reason = 'bounce_limit'
            break
        if len(pts) > max_pts:
            stop_reason = 'pts_limit'
            break
    # RTL 的结果码（与 traj_lane.v 的 res 输出一一对应）：
    #   1 = goal / 2 = 撞墙停 / 0 = 步数或点数用尽
    rtl_res = 1 if res == 'goal' else (2 if stop_reason in ('wall', 'bounce_limit') else 0)
    return dict(x=pos_f(x), y=pos_f(y), res=res, hole=hole_hit, rtl_res=rtl_res,
                stop=stop_reason, steps=n + 1, bounces=bounces,
                pts=[(pos_f(a), pos_f(b)) for a, b in pts])


def _hole_at(holes, edge, c):
    for h in holes:
        if h['edge'] == edge and abs(c - h['c']) <= h['w'] / 2.0:
            return h
    return None


# ---------------------------------------------------------------- 精度自检
def selftest():
    """定点 vs engine.py 浮点：端点差多少 px？判定会不会翻？"""
    import random
    from engine import World
    random.seed(7)                 # 块布局/洞口是随机的 -> 固定种子保证可复现
    w = World()
    w.setup_round(3)
    # 造一个 3x3 的块表（与 engine 的 partition 一致：递归切长边）
    fields = []
    for blk in w.blocks:
        fields.append((blk.x0, blk.y0, blk.x1, blk.y1, blk.b))
    holes = list(w.holes)
    cases = [
        (100.0, 270.0, 0.0, 400.0, 1),
        (60.0, 100.0, 30.0, 350.0, 1),
        (480.0, 60.0, 90.0, 500.0, -1),
        (100.0, 480.0, -30.0, 300.0, 1),
        (30.0, 300.0, 5.0, 600.0, 1),
    ]
    # ★ 只比**短窗口、不含反弹**的那一段（= 轨迹束的真实用法）：
    #   游戏里轨迹束只画发射点附近一小段扇形；一旦撞墙，6~7 次反弹会把亚像素差异
    #   混沌放大（实测端点能差 600px）—— 那种长路径本来就不该由 PL 的扫描去复现。
    STEPS, BOUNCE = 300, 0
    print('（短窗口：steps=%d max_bounce=%d，即"撞墙即停"）' % (STEPS, BOUNCE))
    print('%-28s %-10s %-10s %-10s' % ('场景(x,y,ang,v,q)', '浮点终点', '定点终点', '端点差 px'))
    worst = 0.0
    for (x, y, ang, v, q) in cases:
        pts, res, hole = w.preview(x, y, ang, v, q, steps=STEPS, max_bounce=BOUNCE)
        fx, fy = pts[-1]
        f = lane_fixed(x, y, ang, v, q, fields, holes, 0 if x < FIELD_W / 2 else 1,
                       steps=STEPS, max_bounce=BOUNCE)
        d = math.hypot(fx - f['x'], fy - f['y'])
        worst = max(worst, d)
        same = '一致' if (res == f['res']) else '★不一致'
        print('%-28s (%6.1f,%6.1f) (%6.1f,%6.1f) %8.3f  %s:%s/%s'
              % ('(%.0f,%.0f,%.0f°,%.0f,q%+d)' % (x, y, ang, v, q), fx, fy,
                 f['x'], f['y'], d, same, res, f['res']))
    print('')
    print('最大端点差 = %.3f px（目标 < 1px；判定一致 = %s）'
          % (worst, '是' if worst < 2.0 else '否'))
    return worst


if __name__ == '__main__':
    if '--dump' in sys.argv:
        i = sys.argv.index('--dump')
        path = sys.argv[i + 1] if i + 1 < len(sys.argv) else 'v3_scene.json'
        dump_scene(path)
    else:
        selftest()
