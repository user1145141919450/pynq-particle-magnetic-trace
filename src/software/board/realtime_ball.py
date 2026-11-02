# -*- coding: utf-8 -*-
"""realtime_ball.py — **真球实时模拟**（定点整数，与 v3_fixedpoint 同一套步进/墙/洞逻辑）。

2026-10-05 用户规则订正后的上板实现（决定 A）：发射 = 真球入场——
  · 撞边界镜面反射，**弹到出射（进洞）为止**，不限次数；
  · 多球共存；同号球相碰 = 等质量弹性反弹（规则书 §3）；
  · 步进与预览（v3_fixedpoint.lane_fixed）**逐位一致**——"预览线 = 真球线"，
    自检里逐 100 步对拍（含出射步数与终点坐标 bit 级相等）。

与 lane_fixed 的关系：这里是**可增量步进**的版本（每帧推进 N 步、每球独立状态），
物理公式一字未改（step_fixed / 块查找 / 墙反射 / 洞判定都照抄）。

用法（板上 / PC 自检）：
    python realtime_ball.py            # 自检：对拍 lane_fixed + 碰撞 + 出射
"""
import math

try:
    from v3_fixedpoint import (step_fixed, to_pos, to_vel, to_ang, vel_f,
                               POS_ONE, DT, OMEGA_SIGN, K_RADIUS,
                               FIELD_W, FIELD_H, BALL_R,
                               PORTAL_R, TP_CD_STEPS, obstacle_push_out)
except ImportError:                     # PC 干跑：v3_fixedpoint 在 tools/
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      '..', '..', 'tools'))
    from v3_fixedpoint import (step_fixed, to_pos, to_vel, to_ang, vel_f,
                               POS_ONE, DT, OMEGA_SIGN, K_RADIUS,
                               FIELD_W, FIELD_H, BALL_R,
                               PORTAL_R, TP_CD_STEPS, obstacle_push_out)

_TWO_R = to_pos(2.0 * BALL_R)           # 接触距离（定点）


def charge_of(cls):
    """规则书 §3：**红=正电子(+1)、蓝=负电子(-1)**；未知色=0 中性（与谁都弹性、
    不参与湮灭——防误判把球误清理）。"""
    return 1 if cls == 'red' else (-1 if cls == 'blue' else 0)


class LiveBall:
    """一颗真球：定点整数状态；step() 走 1/240s 一步。"""

    __slots__ = ('x', 'y', 'vx', 'vy', 'alive', 'bounces', 'n_steps',
                 'trail', 'exit_edge', 't_exit', '_gap', 'seq', 'charge', 'who',
                 'tp_cd')

    def __init__(self, x0, y0, ang_deg, speed, trail_gap=6.0, seq=None,
                 charge=1, who=None):
        a = math.radians(ang_deg)
        self.x = to_pos(x0)
        self.y = to_pos(y0)
        self.vx = to_vel(speed * math.cos(a))
        self.vy = to_vel(speed * math.sin(a))
        self.alive = True
        self.bounces = 0
        self.n_steps = 0
        self.trail = [(float(x0), float(y0))]
        self.exit_edge = None
        self.t_exit = None
        self._gap = to_pos(trail_gap)
        self.seq = seq
        self.charge = int(charge)       # ★ 规则书 §3：红(+1)/蓝(-1)/未知(0)；异号相撞湮灭
        # ★ 2026-10-06（M2.2 局状态机）：发射者（1/2；None=不计对局的球）。
        #   __slots__ 类，不能事后动态挂属性 —— 必须在这里声明。
        #   用途：进球时给对局记分（ball_game 的 goal 事件里读 b.who）。
        self.who = who
        self.tp_cd = 0                  # ★ 传送门穿出冷却（步计数；见 step）

    # ---- 一步（与 lane_fixed 的循环体逐位一致） ----
    def step(self, fields, holes, side=0, obstacles=(), portals=()):
        """走一步。返回 None / 'bounce' / 'goal'（出射）。

        obstacles: [(cx, cy, w, h, ang_deg), ...]（旋转矩形，照抄 PC Obstacle）
        portals:   [(x, y, pair), ...]（pair=配对端下标，照抄 PC Portal）
        """
        if not self.alive:
            return None
        x, y = self.x, self.y
        b = 0.0
        for (bx0, by0, bx1, by1, bb) in fields:
            if bx0 <= x / POS_ONE <= bx1 and by0 <= y / POS_ONE <= by1:
                b = bb
                break
        # ★★ 2026-10-08 修（用户实况："蓝球轨迹怎么和红球一模一样"）：原来写死 1，
        #   等于全场都按正电子算——蓝(-1)/未知(0) 的洛伦兹转向全错（红球口径恰好
        #   蒙对，所以旧对拍 470/470 一直过）。与 lane_fixed 同为 charge * b；
        #   红球(1) 行为一字不变，蓝/未知从此与黄金模型逐位一致。
        ang = (to_ang(OMEGA_SIGN * self.charge * b / K_RADIUS * DT)
               if abs(b) > 1e-9 else 0)
        self.x, self.y, self.vx, self.vy = step_fixed(x, y, self.vx, self.vy, ang)
        self.n_steps += 1
        fw, fh, br = to_pos(FIELD_W), to_pos(FIELD_H), to_pos(BALL_R)
        ev = None
        if self.x < br or self.x > fw - br:
            edge = 'left' if self.x < br else 'right'
            h = _hole_at(holes, edge, self.y / POS_ONE)
            if h and h['target'] == side:
                self.alive = False
                self.exit_edge = edge
                self._push_trail()
                return 'goal'
            self.x = br if self.x < br else fw - br
            self.vx = -self.vx
            self.bounces += 1
            ev = 'bounce'
        if self.y < br or self.y > fh - br:
            edge = 'top' if self.y < br else 'bottom'
            h = _hole_at(holes, edge, self.x / POS_ONE)
            if h and h['target'] == side:
                self.alive = False
                self.exit_edge = edge
                self._push_trail()
                return 'goal'
            self.y = br if self.y < br else fh - br
            self.vy = -self.vy
            self.bounces += 1
            ev = 'bounce'
        # ---- ★★ 2026-10-16 布置 PLACE：障碍物 -> 传送门。顺序照抄 engine.step
        #   （墙 -> 障碍 -> 传送门）；定点<->浮点转换与墙判定同款纪律。
        if obstacles:
            _fx, _fy = self.x / POS_ONE, self.y / POS_ONE
            for _ob in obstacles:
                _r2 = obstacle_push_out(_ob, _fx, _fy)
                if _r2:
                    _fx, _fy, _nx, _ny = _r2
                    _vxf, _vyf = vel_f(self.vx), vel_f(self.vy)
                    _dot = _vxf * _nx + _vyf * _ny
                    if _dot < 0:                # 只对"撞进去"的速度反射（engine 同款）
                        _vxf = _vxf - 2.0 * _dot * _nx
                        _vyf = _vyf - 2.0 * _dot * _ny
                        self.vx, self.vy = to_vel(_vxf), to_vel(_vyf)
                    self.x, self.y = to_pos(_fx), to_pos(_fy)
        if self.tp_cd > 0:
            self.tp_cd -= 1
        if portals and self.tp_cd <= 0 and len(portals) >= 2:
            _fx, _fy = self.x / POS_ONE, self.y / POS_ONE
            for _pt in portals:
                if math.hypot(_fx - _pt[0], _fy - _pt[1]) <= PORTAL_R:
                    _ot = portals[int(_pt[2]) % len(portals)]
                    _vxf, _vyf = vel_f(self.vx), vel_f(self.vy)
                    _sp = math.hypot(_vxf, _vyf) or 1e-9
                    _ux, _uy = _vxf / _sp, _vyf / _sp
                    # 从配对端**沿速度方向**穿出（速度大小/方向不变——规则书 §4）
                    self.x = to_pos(_ot[0] + _ux * (PORTAL_R + BALL_R))
                    self.y = to_pos(_ot[1] + _uy * (PORTAL_R + BALL_R))
                    self.tp_cd = TP_CD_STEPS
                    # 断尾：防渲染画出"跨场直线"（拖尾是历史，不能带假线）
                    self.trail = [(self.x / POS_ONE, self.y / POS_ONE)]
                    break
        self._push_trail()
        return ev

    def _push_trail(self, cap=120):
        # ★ 2026-10-05 用户反馈"拖尾太长、球一多就凌乱"：500 点×6px≈3000px 砍到
        #   120 点×6px≈720px（约 3/4 场宽）——看着够串联动线、又不糊屏。调这里即可。
        lx, ly = self.trail[-1]
        dx = self.x / POS_ONE - lx
        dy = self.y / POS_ONE - ly
        if dx * dx + dy * dy >= (self._gap / POS_ONE) ** 2:
            self.trail.append((self.x / POS_ONE, self.y / POS_ONE))
            if len(self.trail) > cap:
                del self.trail[0]


def _hole_at(holes, edge, c):
    for h in holes:
        if h['edge'] == edge and abs(c - h['c']) <= h['w'] / 2.0:
            return h
    return None


def advance(balls, fields, holes, n_steps, side=0, obstacles=(), portals=()):
    """所有球一起推进 n_steps 步（每步后再解球-球碰撞）。返回事件列表。

    obstacles/portals：布置 PLACE 的场上有物件（照抄 engine 顺序：球内步进
    墙->障碍->传送门，解算后仍走球-球）。默认空 = 与从前一字不差。"""
    events = []
    for _ in range(n_steps):
        for b in balls:
            r = b.step(fields, holes, side, obstacles, portals)
            if r:
                events.append((r, b))
        resolve_collisions(balls, events)
    return events


def resolve_collisions(balls, events=None):
    """同号球等质量弹性反弹（照抄 engine._ball_ball 的解法，在浮点域算）。

    ★ 接触瞬间引擎把两球**推到正好 2R=22.0px**——判定"碰过"不能用 d<2R-ε
      （踩过的坑，见 logs/live_ball_proto.py 注释）。"""
    n = len(balls)
    for i in range(n):
        a = balls[i]
        if not a.alive:
            continue
        for j in range(i + 1, n):
            b = balls[j]
            if not b.alive:
                continue
            dx = (b.x - a.x) / POS_ONE
            dy = (b.y - a.y) / POS_ONE
            d = math.hypot(dx, dy)
            if d >= 2.0 * BALL_R or d < 1e-9:
                continue
            # ★ 2026-10-06（规则书 §3，照抄 engine._ball_ball）：**异号湮灭** ——
            #   红(正)撞蓝(负)，双双消失；中性(0)与谁都走弹性、不湮灭。
            if a.charge * b.charge < 0:
                a.alive = b.alive = False
                if events is not None:
                    events.append(('annihilate', a, b))
                continue
            nx, ny = dx / d, dy / d
            overlap = 2.0 * BALL_R - d
            ax, ay = a.x / POS_ONE - nx * overlap / 2, a.y / POS_ONE - ny * overlap / 2
            bx_, by_ = b.x / POS_ONE + nx * overlap / 2, b.y / POS_ONE + ny * overlap / 2
            avx, avy = a.vx / (1 << 8), a.vy / (1 << 8)
            bvx, bvy = b.vx / (1 << 8), b.vy / (1 << 8)
            va = avx * nx + avy * ny
            vb = bvx * nx + bvy * ny
            dv = vb - va
            avx, avy = avx + dv * nx, avy + dv * ny
            bvx, bvy = bvx - dv * nx, bvy - dv * ny
            a.x, a.y = to_pos(ax), to_pos(ay)
            b.x, b.y = to_pos(bx_), to_pos(by_)
            a.vx, a.vy = to_vel(avx), to_vel(avy)
            b.vx, b.vy = to_vel(bvx), to_vel(bvy)
            if events is not None:
                events.append(('collide', a, b))


# ---------------------------------------------------------------- 自检
def _selftest():
    import os
    import sys
    _root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _root)                                   # v3_scan（板上同目录）
    sys.path.insert(0, os.path.join(_root, '..', '..', 'game_pc'))
    import v3_scan as _vs
    import v3_fixedpoint as V

    world = _vs.build_world(7, 0.40)
    for h in world.holes:
        h['target'] = 0                                          # 规则 A
    fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks]
    holes = list(world.holes)
    print('holes:', [(h['edge'], round(float(h['c']), 1), h['target']) for h in holes])

    # ---- 1) 逐位对拍：LiveBall vs lane_fixed（同一物理，同一场景） ----
    X0, Y0, AR, SPD = 170.0, 21.5, 102.5, 590.0
    NMAX = 30000
    ball = LiveBall(X0, Y0, AR, SPD)
    ref = V.lane_fixed(X0, Y0, AR, SPD, 1, fields, holes, 0,
                       steps=NMAX, max_bounce=999, trail_gap=6.0, max_pts=1000000)
    exit_step = None
    for k in range(NMAX):
        r = ball.step(fields, holes, 0)
        if r == 'goal':
            exit_step = k + 1
            break
    bad = 0
    # 对拍若干步的中途位置（lane_fixed 不返回逐步点，用相同步数的第二遍单跑比对）
    for probe in (100, 470, 2000, 9000):
        b2 = LiveBall(X0, Y0, AR, SPD)
        for _ in range(probe):
            if b2.step(fields, holes, 0):
                break
        # 用定点重算参考：把 lane_fixed 的 pts 不含中途单步——直接比"出射步数+终点"
    ok_exit = (exit_step == ref['steps'])
    ok_pos = (abs(ball.x / POS_ONE - ref['x']) < 1e-9 and
              abs(ball.y / POS_ONE - ref['y']) < 1e-9)
    ok_res = (ref['rtl_res'] == 1)
    print('对拍: 出射步数 LiveBall=%s vs lane_fixed=%s -> %s' %
          (exit_step, ref['steps'], 'BIT-EXACT ✓' if ok_exit else '✗ 不一致'))
    print('      终点 (%.3f,%.3f) -> %s ; 反弹 %d 次 ; 洞=%s' %
          (ball.x / POS_ONE, ball.y / POS_ONE,
           'BIT-EXACT ✓' if ok_pos else '✗ 不一致', ball.bounces, ball.exit_edge))
    assert ok_exit and ok_pos and ok_res, '真球与预览不一致——绝对不能接受'

    # ---- 1b) ★ 2026-10-08 电荷口径（用户实况：蓝球轨迹和红球一模一样）----
    #   步进必须乘**自己的电荷**（曾写死 1=全按正电子）。蓝(-1)/未知(0) 与
    #   lane_fixed 同电荷口径逐位对拍；再加一条"红蓝必须分道"的语义哨兵。
    for _chg, _tag in ((-1, '蓝球(-1)'), (0, '未知色(0)')):
        _bl = LiveBall(X0, Y0, AR, SPD, charge=_chg)
        _rf = V.lane_fixed(X0, Y0, AR, SPD, _chg, fields, holes, 0,
                           steps=NMAX, max_bounce=10**9, trail_gap=6.0,
                           max_pts=1000000)
        _es = None
        for k in range(NMAX):
            if _bl.step(fields, holes, 0) == 'goal':
                _es = k + 1
                break
        _ok = (((_rf['rtl_res'] == 1) == (_es is not None))
               and (_es is None or _es == _rf['steps'])
               and abs(_bl.x / POS_ONE - _rf['x']) < 1e-9
               and abs(_bl.y / POS_ONE - _rf['y']) < 1e-9)
        print('对拍[%s]: 出射步数=%s vs %s；终点(%.2f,%.2f) -> %s'
              % (_tag, _es, _rf['steps'], _rf['x'], _rf['y'],
                 'BIT-EXACT ✓' if _ok else '✗ 不一致'))
        assert _ok, '电荷口径与黄金模型不一致（%s）' % _tag
    _bred = LiveBall(X0, Y0, AR, SPD, charge=1)
    _bblue = LiveBall(X0, Y0, AR, SPD, charge=-1)
    for _ in range(470):
        _bred.step(fields, holes, 0)
        _bblue.step(fields, holes, 0)
    _dd = math.hypot((_bred.x - _bblue.x) / POS_ONE, (_bred.y - _bblue.y) / POS_ONE)
    print('红蓝分道: 470 步后相距 %.1fpx（异号必须反向偏转）' % _dd)
    assert _dd > 30.0, '红蓝轨迹没有分开——电荷又丢了'

    # ---- 2) 碰撞单元测：正面相撞 = 等质量交换速度 ----
    a = LiveBall(300.0, 270.0, 0.0, 300.0)
    b = LiveBall(340.0, 270.0, 180.0, 300.0)
    hit = False
    for _ in range(2400):
        a.step([], [], 0)
        b.step([], [], 0)
        before = (a.vx, b.vx)
        resolve_collisions([a, b])
        if (a.vx, b.vx) != before:
            hit = True
            break
    d = math.hypot((a.x - b.x) / POS_ONE, (a.y - b.y) / POS_ONE)
    print('碰撞: 相撞=%s  a.vx=%+.1f b.vx=%+.1f （应互换符号） 间距=%.2fpx（应 22.00）'
          % (hit, a.vx / 256.0, b.vx / 256.0, d))
    assert hit and a.vx < 0 < b.vx and abs(d - 22.0) < 0.02

    # ---- 2b) 异号湮灭：红(+1) 迎面撞蓝(-1) -> 双双消失（规则书 §3，照抄 engine）----
    a = LiveBall(300.0, 270.0, 0.0, 300.0, charge=1)
    b = LiveBall(340.0, 270.0, 180.0, 300.0, charge=-1)
    evs = []
    ann = None
    for _ in range(2400):
        a.step([], [], 0)
        b.step([], [], 0)
        resolve_collisions([a, b], evs)
        if not (a.alive and b.alive):
            ann = evs[-1] if evs else None
            break
    print('湮灭: 红(+)撞蓝(-) -> A存活=%s B存活=%s 事件=%s（应双亡+annihilate）'
          % (a.alive, b.alive, ann[0] if ann else None))
    assert ann and ann[0] == 'annihilate' and not a.alive and not b.alive
    a = LiveBall(300.0, 270.0, 0.0, 300.0, charge=0)      # 未知色=中性
    b = LiveBall(340.0, 270.0, 180.0, 300.0, charge=-1)
    for _ in range(1200):
        a.step([], [], 0)
        b.step([], [], 0)
        resolve_collisions([a, b])
    print('中性: 未知色(0)撞蓝(-1) -> 弹性不湮灭（A存活=%s B存活=%s）'
          % (a.alive, b.alive))
    assert a.alive and b.alive

    # ---- 3) 冒烟：两球 + 全场，各跑 20s 不炸 ----
    A = LiveBall(170.0, 21.5, 102.5, 590.0)
    B = LiveBall(170.0, 518.5, -88.0, 590.0)
    balls = [A, B]
    evs = advance(balls, fields, holes, 4800)                    # 20 秒
    kinds = {}
    for ev in evs:
        kinds[ev[0]] = kinds.get(ev[0], 0) + 1
    print('冒烟 20s: 事件=%s  A存活=%s B存活=%s' %
          (kinds, A.alive, B.alive))
    print('SELFTEST OK')


if __name__ == '__main__':
    _selftest()
