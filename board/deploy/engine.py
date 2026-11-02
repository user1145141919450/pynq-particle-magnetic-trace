# -*- coding: utf-8 -*-
"""engine.py — 《磁场粒子对战》PC 原型：物理与规则引擎（与渲染解耦）

坐标：屏幕坐标，场区左上角为 (0,0)，x 向右、y 向下。

物理模型
  - 分块均匀磁场（默认 3x3），每块有独立的有符号强度 B（正=出屏、负=入屏）
  - 半径 r = K_RADIUS * v / |B|  →  角速度 ω = |B| / K_RADIUS（rad/s，与速度无关）
  - 定步长积分（dt=1/240s）：速度旋转 → 位移取新速度方向
  - 边界（含四角圆弧）反弹；洞口处不反弹而判定进洞
  - 球-球：同号弹性反弹（等质量交换法向分量）；异号湮灭消失
  - 传送门：进入即从配对门穿出，速度大小与方向不变

自检（不开窗口，纯跑物理）：
  PYTHONPATH=<你的视觉包目录> python engine.py
"""

import math
import random

# ---------------- 基本参数（与 docs/game_rules_quickref.md 对齐） ----------------
FIELD_W, FIELD_H = 960.0, 540.0
CORNER_R = 60.0          # 四角圆角半径
BALL_R = 11.0            # 球半径（直径 22 px）
HOLE_W = 44.0            # 洞口宽度 = 2 倍球径
GRID_N = 3               # 3x3 磁场块
MAX_BLOCKS = 9           # 磁场块数上限（第 9、10 局）
K_RADIUS = 1.0           # r = K_RADIUS * v / |B|
DT = 1.0 / 240.0         # 物理步长

SPEED_MIN, SPEED_MAX, SPEED_DEF = 200.0, 600.0, 400.0
ANGLE_STEP = 2.0         # 度
SPEED_STEP = 20.0
B_MIN, B_MAX, B_STEP = 0.5, 3.0, 0.2      # 磁场大小可调范围与按键步进
PORTAL_R = 18.0
TELEPORT_COOLDOWN = 0.35  # 秒，防止刚穿出又立刻被吸回

# 旋转方向：屏幕坐标下，正电荷 + B 出屏 时的转向。
# 若试玩发现左右弯反了，把这里改成 +1.0 即可。
OMEGA_SIGN = -1.0

CHARGE_COLOR_NAME = {1: '红(正电)', -1: '蓝(负电)'}


class Block:
    """一块均匀磁场（矩形区域）。B 有符号：>0 出屏，<0 入屏。

    「第 n 局 n 块磁场」= 整个场区被划分成 n 块，每块各有一个磁场；
    n=1 时整场就是一块（全场均匀），不是"九宫格里只填一格"。
    """

    def __init__(self, x0, y0, x1, y1, b=1.0):
        self.x0, self.y0, self.x1, self.y1 = float(x0), float(y0), float(x1), float(y1)
        self.b = float(b)
        self.axis = None          # None = 叶子；'v' 竖切（分左右）/ 'h' 横切（分上下）
        self.ratio = 0.5
        self.kids = None
        self.parent = None

    def contains(self, x, y):
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    @property
    def area(self):
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    @property
    def out(self):
        return self.b > 0

    # ---- 分割树操作 ----
    @property
    def is_leaf(self):
        return self.kids is None

    def leaves(self):
        if self.is_leaf:
            return [self]
        return self.kids[0].leaves() + self.kids[1].leaves()

    def split(self):
        """把自己一分为二（沿长边、对半）。"""
        if not self.is_leaf:
            return
        self.axis = 'v' if (self.x1 - self.x0) >= (self.y1 - self.y0) else 'h'
        self.ratio = 0.5
        self.kids = [Block(self.x0, self.y0, self.x1, self.y1),
                     Block(self.x0, self.y0, self.x1, self.y1)]
        for k in self.kids:
            k.parent = self
        self.apply_geom()

    def apply_geom(self):
        """按 axis/ratio 重算两个子块的矩形（递归）。"""
        if self.is_leaf:
            return
        a, b = self.kids
        if self.axis == 'v':
            mid = self.x0 + (self.x1 - self.x0) * self.ratio
            a.x0, a.y0, a.x1, a.y1 = self.x0, self.y0, mid, self.y1
            b.x0, b.y0, b.x1, b.y1 = mid, self.y0, self.x1, self.y1
        else:
            mid = self.y0 + (self.y1 - self.y0) * self.ratio
            a.x0, a.y0, a.x1, a.y1 = self.x0, self.y0, self.x1, mid
            b.x0, b.y0, b.x1, b.y1 = self.x0, mid, self.x1, self.y1
        a.apply_geom()
        b.apply_geom()

    def shift_divider(self, delta):
        """拖动本块与其兄弟之间的分界线 → 改变本块的大小。"""
        p = self.parent
        if p is None:
            return False
        p.ratio = min(0.85, max(0.15, p.ratio + delta))
        r = p
        while r.parent is not None:
            r = r.parent
        r.apply_geom()
        return True


def partition(n):
    """把整个场区划分成 n 块：反复把当前面积最大的块沿长边对半切开。

    n=1 → 整场一块；n=2 → 左右两半；n=3 → 半场再切一刀；
    n=4 → 2x2；n=9 附近自然逼近 3x3。
    """
    root = Block(0.0, 0.0, FIELD_W, FIELD_H)
    while len(root.leaves()) < n:
        big = max(root.leaves(), key=lambda k: k.area)
        big.split()
    return root


class Particle:
    __slots__ = ('x', 'y', 'vx', 'vy', 'q', 'owner', 'alive', 'trail', 'age', 'tp_cd')

    def __init__(self, x, y, vx, vy, q, owner):
        self.x, self.y, self.vx, self.vy = float(x), float(y), float(vx), float(vy)
        self.q = int(q)            # +1 正电 / -1 负电
        self.owner = int(owner)    # 0 选手一 / 1 选手二
        self.alive = True
        self.trail = [(self.x, self.y)]
        self.age = 0.0
        self.tp_cd = 0.0           # 传送冷却

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)

    @property
    def angle(self):
        return math.degrees(math.atan2(self.vy, self.vx))


class Obstacle:
    """旋转矩形障碍（实体黑条）：撞上按最近面法线镜面反射。"""

    def __init__(self, cx, cy, w, h, ang_deg=0.0):
        self.cx, self.cy = float(cx), float(cy)
        self.hw, self.hh = float(w) / 2.0, float(h) / 2.0
        self.ang = math.radians(ang_deg)

    def push_out(self, p):
        """若粒子中心落在（扩张后）矩形内，返回 (新位置, 法线)，否则 None。"""
        ca, sa = math.cos(-self.ang), math.sin(-self.ang)
        dx, dy = p.x - self.cx, p.y - self.cy
        lx, ly = dx * ca - dy * sa, dx * sa + dy * ca          # 局部坐标
        ehw, ehh = self.hw + BALL_R, self.hh + BALL_R
        if abs(lx) >= ehw or abs(ly) >= ehh:
            return None
        # 从穿透最浅的一侧推出
        px, py = ehw - abs(lx), ehh - abs(ly)
        if px < py:
            sx, sy = (1.0 if lx > 0 else -1.0), 0.0
            lx = (ehw if lx > 0 else -ehw)
        else:
            sx, sy = 0.0, (1.0 if ly > 0 else -1.0)
            ly = (ehh if ly > 0 else -ehh)
        ca2, sa2 = math.cos(self.ang), math.sin(self.ang)
        nx, ny = sx * ca2 - sy * sa2, sx * sa2 + sy * ca2      # 法线回到世界坐标
        wx, wy = lx * ca2 - ly * sa2, lx * sa2 + ly * ca2
        return (self.cx + wx, self.cy + wy), (nx, ny)


class Portal:
    def __init__(self, x, y, pair):
        self.x, self.y, self.pair = float(x), float(y), int(pair)

    def reflects(self, portal_x, portal_y, dist=None):
        """校验用：与另一门（或洞口）叠加时返回 False。"""
        d = math.hypot(self.x - portal_x, self.y - portal_y)
        return d > 2 * PORTAL_R


class World:
    """一场对决的全部状态。"""

    def __init__(self, seed=None):
        if seed is not None:
            random.seed(seed)
        self.root = partition(1)
        self.blocks = self.root.leaves()
        self.holes = []        # {'edge':'left'|'right', 'c':y, 'w':宽, 'target':得分方}
        self.obstacles = []
        self.portals = []
        self.particles = []
        self.events = []       # ('goal', side, x, y) / ('annihilate', x, y)
        self.time = 0.0
        self.hit_marks = {'left': [], 'right': [], 'top': [], 'bottom': []}  # 落点统计
        self.setup_round(1)

    # ---------- 关卡设置 ----------
    def blocks_count(self):
        return len(self.blocks)

    def setup_round(self, n_blocks, hole_adapt=True):
        """按局数布置：整场划分为 n_blocks 块，每块随机方向与强度；每侧一个随机洞口。"""
        n = max(1, min(int(n_blocks), MAX_BLOCKS))
        self.root = partition(n)
        self.blocks = self.root.leaves()
        for b in self.blocks:
            mag = round(random.uniform(0.8, 2.5), 1)
            b.b = mag if random.random() < 0.5 else -mag
        self.holes = []
        if hole_adapt:
            for edge, target in (('right', 0), ('left', 1)):
                c = random.uniform(CORNER_R + HOLE_W, FIELD_H - CORNER_R - HOLE_W)
                self.holes.append({'edge': edge, 'c': c, 'w': HOLE_W, 'target': target})
        self.hit_marks = {'left': [], 'right': [], 'top': [], 'bottom': []}

    def reset_particles(self):
        self.particles = []
        self.events = []

    # ---------- 几何 ----------
    def block_at(self, x, y):
        for b in self.blocks:
            if b.contains(x, y):
                return b
        return self.blocks[0]

    def hole_at(self, edge, y):
        for h in self.holes:
            if h['edge'] == edge and abs(y - h['c']) <= h['w'] / 2.0:
                return h
        return None

    def inside_rounded(self, x, y, margin=0.0):
        """点是否在圆角矩形内（margin>0 表示向内收缩）。"""
        if not (margin <= x <= FIELD_W - margin and margin <= y <= FIELD_H - margin):
            return False
        lim = CORNER_R - margin
        for sx in (0, 1):
            for sy in (0, 1):
                in_x = (x < CORNER_R) if sx == 0 else (x > FIELD_W - CORNER_R)
                in_y = (y < CORNER_R) if sy == 0 else (y > FIELD_H - CORNER_R)
                if in_x and in_y:
                    cx = CORNER_R if sx == 0 else FIELD_W - CORNER_R
                    cy = CORNER_R if sy == 0 else FIELD_H - CORNER_R
                    if math.hypot(x - cx, y - cy) > lim:
                        return False
        return True

    # ---------- 物理 ----------
    def launch(self, x, y, angle_deg, speed, charge, owner):
        a = math.radians(angle_deg)
        p = Particle(x, y, speed * math.cos(a), speed * math.sin(a), charge, owner)
        self.particles.append(p)
        return p

    def step(self, dt=DT):
        self.time += dt
        for p in self.particles:
            if not p.alive:
                continue
            self._step_particle(p, dt)
        self._ball_ball()
        self.particles = [p for p in self.particles if p.alive]

    def _step_particle(self, p, dt):
        b = self.block_at(p.x, p.y).b
        if abs(b) > 1e-9:
            omega = OMEGA_SIGN * p.q * b / K_RADIUS      # rad/s（与速度无关）
            a = omega * dt
            ca, sa = math.cos(a), math.sin(a)
            vx, vy = p.vx * ca - p.vy * sa, p.vx * sa + p.vy * ca
            p.vx, p.vy = vx, vy
        p.x += p.vx * dt
        p.y += p.vy * dt
        p.age += dt
        if p.tp_cd > 0:
            p.tp_cd -= dt

        self._walls(p)
        if not p.alive:
            return
        self._obstacles(p)
        self._portals(p)
        if len(p.trail) == 0 or math.hypot(p.x - p.trail[-1][0], p.y - p.trail[-1][1]) > 6:
            p.trail.append((p.x, p.y))
            if len(p.trail) > 400:
                del p.trail[0]

    def _walls(self, p):
        # 左右边界（可能有洞）。
        # ★ 规则：球只能进「对面那侧的洞」——只有洞口的目标方 == 球的归属方时才算进洞；
        #   自己那侧的洞对它等于墙（否则会打出"乌龙球"，把分送给对手）。
        if p.x < BALL_R:
            h = self.hole_at('left', p.y)
            if h and h['target'] == p.owner:
                p.alive = False
                self.events.append(('goal', h['target'], p.x, p.y))
                return
            p.x = BALL_R
            p.vx = -p.vx
            self.hit_marks['left'].append(p.y)
        elif p.x > FIELD_W - BALL_R:
            h = self.hole_at('right', p.y)
            if h and h['target'] == p.owner:
                p.alive = False
                self.events.append(('goal', h['target'], p.x, p.y))
                return
            p.x = FIELD_W - BALL_R
            p.vx = -p.vx
            self.hit_marks['right'].append(p.y)
        # 上下边界（也可能有玩家加的洞）
        if p.y < BALL_R:
            h = self.hole_at('top', p.x)
            if h and h['target'] == p.owner:
                p.alive = False
                self.events.append(('goal', h['target'], p.x, p.y))
                return
            p.y, p.vy = BALL_R, -p.vy
            self.hit_marks['top'].append(p.x)
        elif p.y > FIELD_H - BALL_R:
            h = self.hole_at('bottom', p.x)
            if h and h['target'] == p.owner:
                p.alive = False
                self.events.append(('goal', h['target'], p.x, p.y))
                return
            p.y, p.vy = FIELD_H - BALL_R, -p.vy
            self.hit_marks['bottom'].append(p.x)
        # 四角圆弧
        lim = CORNER_R - BALL_R
        for sx in (0, 1):
            for sy in (0, 1):
                in_x = (p.x < CORNER_R) if sx == 0 else (p.x > FIELD_W - CORNER_R)
                in_y = (p.y < CORNER_R) if sy == 0 else (p.y > FIELD_H - CORNER_R)
                if not (in_x and in_y):
                    continue
                cx = CORNER_R if sx == 0 else FIELD_W - CORNER_R
                cy = CORNER_R if sy == 0 else FIELD_H - CORNER_R
                dx, dy = p.x - cx, p.y - cy
                d = math.hypot(dx, dy)
                if d > lim and d > 1e-9:
                    nx, ny = dx / d, dy / d
                    p.x, p.y = cx + nx * lim, cy + ny * lim
                    dot = p.vx * nx + p.vy * ny
                    if dot > 0:
                        p.vx, p.vy = p.vx - 2 * dot * nx, p.vy - 2 * dot * ny

    def _obstacles(self, p):
        for ob in self.obstacles:
            r = ob.push_out(p)
            if r:
                (p.x, p.y), (nx, ny) = r
                dot = p.vx * nx + p.vy * ny
                if dot < 0:                      # 只对"撞进去"的速度做反射
                    p.vx, p.vy = p.vx - 2 * dot * nx, p.vy - 2 * dot * ny

    def _portals(self, p):
        if p.tp_cd > 0 or len(self.portals) < 2:
            return
        for i, pt in enumerate(self.portals):
            if math.hypot(p.x - pt.x, p.y - pt.y) <= PORTAL_R:
                other = self.portals[pt.pair]
                sp = max(p.speed, 1e-6)
                ux, uy = p.vx / sp, p.vy / sp
                p.x, p.y = other.x + ux * (PORTAL_R + BALL_R), other.y + uy * (PORTAL_R + BALL_R)
                p.tp_cd = TELEPORT_COOLDOWN
                self.events.append(('portal', p.x, p.y))
                break

    def _ball_ball(self):
        n = len(self.particles)
        for i in range(n):
            a = self.particles[i]
            if not a.alive:
                continue
            for j in range(i + 1, n):
                b = self.particles[j]
                if not b.alive:
                    continue
                dx, dy = b.x - a.x, b.y - a.y
                d = math.hypot(dx, dy)
                if d >= 2 * BALL_R or d < 1e-9:
                    continue
                if a.q * b.q < 0:                       # 异号 → 湮灭
                    a.alive = b.alive = False
                    self.events.append(('annihilate', (a.x + b.x) / 2, (a.y + b.y) / 2))
                else:                                    # 同号 → 弹性反弹（等质量）
                    nx, ny = dx / d, dy / d
                    overlap = 2 * BALL_R - d
                    a.x -= nx * overlap / 2; a.y -= ny * overlap / 2
                    b.x += nx * overlap / 2; b.y += ny * overlap / 2
                    va = a.vx * nx + a.vy * ny
                    vb = b.vx * nx + b.vy * ny
                    dv = vb - va
                    a.vx += dv * nx; a.vy += dv * ny
                    b.vx -= dv * nx; b.vy -= dv * ny
        self.particles = [p for p in self.particles if p.alive]

    # ---------- 洞口自适应 ----------
    def _hole_range(self, edge):
        """洞口中心在边界上的合法范围（避开四角圆弧）。"""
        if edge in ('left', 'right'):
            return (CORNER_R + HOLE_W / 2, FIELD_H - CORNER_R - HOLE_W / 2)
        return (CORNER_R + HOLE_W / 2, FIELD_W - CORNER_R - HOLE_W / 2)

    def adapt_holes(self):
        """把每个洞口朝该侧边界上"落点热点"移动一小步（≤1.5 倍洞宽，带随机偏移）。"""
        moved = []
        for h in self.holes:
            marks = self.hit_marks[h['edge']]
            if len(marks) < 3:
                continue
            marks_sorted = sorted(marks)
            best_c, best_n = h['c'], 0
            win = max(h['w'], 40.0)
            for m in marks_sorted:
                cnt = sum(1 for x in marks_sorted if abs(x - m) <= win / 2)
                if cnt > best_n:
                    best_n, best_c = cnt, m
            # 已经落在热点附近就别动了——否则每 30 秒都会无意义地晃一下，
            # 玩家刚瞄准好洞口就飘走（"每隔 30 秒变换一次洞口"就是这么来的）
            dist = abs(best_c - h['c'])
            if dist < 40.0:
                continue
            step = min(1.5 * h['w'], 66.0)
            # 离得越远越敢移；只差一点点时加很小的偏移，避免"一调就进"
            jitter = random.uniform(-15, 15) if dist > 80 else random.uniform(-6, 6)
            target = best_c + jitter
            delta = max(-step, min(step, target - h['c']))
            lo, hi = self._hole_range(h['edge'])
            new_c = min(hi, max(lo, h['c'] + delta))
            # 约束：不能与其他同边洞口重叠
            if all(o is h or o['edge'] != h['edge']
                   or abs(new_c - o['c']) > (h['w'] + o['w']) / 2 + 10 for o in self.holes):
                # 约束：若会让当前飞行粒子 2 秒内直接进洞 → 取消
                if not self._would_score_soon(h, new_c):
                    h['c'] = new_c
                    moved.append((h['edge'], round(new_c, 1)))
        self.hit_marks = {'left': [], 'right': [], 'top': [], 'bottom': []}
        return moved

    def _would_score_soon(self, hole, new_c):
        probe = dict(hole, c=new_c)
        for p in self.particles:
            if not p.alive:
                continue
            x, y, vx, vy = p.x, p.y, p.vx, p.vy
            q = p.q
            for _ in range(int(2.0 / DT)):
                b = self.block_at(x, y).b
                if abs(b) > 1e-9:
                    a = OMEGA_SIGN * q * b / K_RADIUS * DT
                    ca, sa = math.cos(a), math.sin(a)
                    vx, vy = vx * ca - vy * sa, vx * sa + vy * ca
                x += vx * DT
                y += vy * DT
                if probe['target'] != p.owner:
                    continue          # 该洞不是这颗粒子的目标洞，它进不去
                if probe['edge'] == 'left' and x < BALL_R and abs(y - probe['c']) <= probe['w'] / 2:
                    return True
                if probe['edge'] == 'right' and x > FIELD_W - BALL_R and abs(y - probe['c']) <= probe['w'] / 2:
                    return True
                if probe['edge'] == 'top' and y < BALL_R and abs(x - probe['c']) <= probe['w'] / 2:
                    return True
                if probe['edge'] == 'bottom' and y > FIELD_H - BALL_R and abs(x - probe['c']) <= probe['w'] / 2:
                    return True
                if x < -50 or x > FIELD_W + 50 or y < -50 or y > FIELD_H + 50:
                    break
        return False

    # ---------- 预览与轨迹束 ----------
    def preview(self, x, y, angle_deg, speed, charge, steps=900, dt=DT, max_bounce=6):
        """(不含球-球碰撞) 预测轨迹折线，用于虚线预览与轨迹束。"""
        a = math.radians(angle_deg)
        vx, vy = speed * math.cos(a), speed * math.sin(a)
        side = 0 if x < FIELD_W / 2 else 1     # 从哪一侧发射 → 只能进对面那侧的洞
        pts = [(x, y)]
        bounces = 0
        for _ in range(steps):
            b = self.block_at(x, y).b
            if abs(b) > 1e-9:
                ang = OMEGA_SIGN * charge * b / K_RADIUS * dt
                ca, sa = math.cos(ang), math.sin(ang)
                vx, vy = vx * ca - vy * sa, vx * sa + vy * ca
            x += vx * dt
            y += vy * dt
            if x < BALL_R or x > FIELD_W - BALL_R:
                h = self.hole_at('left' if x < BALL_R else 'right', y)
                if h and h['target'] == side:      # 只有归属自己的洞才算进洞
                    pts.append((x, y))
                    return pts, 'goal', h
                x = BALL_R if x < BALL_R else FIELD_W - BALL_R
                vx = -vx
                bounces += 1
            if y < BALL_R or y > FIELD_H - BALL_R:
                h = self.hole_at('top' if y < BALL_R else 'bottom', x)
                if h and h['target'] == side:
                    pts.append((x, y))
                    return pts, 'goal', h
                y = BALL_R if y < BALL_R else FIELD_H - BALL_R
                vy = -vy
                bounces += 1
            if len(pts) == 0 or math.hypot(x - pts[-1][0], y - pts[-1][1]) > 8:
                pts.append((x, y))
            if bounces > max_bounce or len(pts) > 600:
                break
        return pts, 'none', None

    def tangent_scan(self, x, y, angle_deg, speed, charge, win=6,
                     steps=1200, max_bounce=10):
        """在入射角附近扫一簇轨迹（PC 端串行版；上板后由 PL 并行完成）。

        steps/max_bounce 控制每条轨迹画多长——**画太长会糊屏**，
        界面上只应显示发射点附近的一小段扇形，因此外部调用时给小值。
        返回 (轨迹列表, 临界轨迹索引)。
        """
        out = []
        critical_idx = None
        for k in range(-win, win + 1):
            ang = angle_deg + k * 1.0
            pts, res, hole = self.preview(x, y, ang, speed, charge,
                                          steps=steps, max_bounce=max_bounce)
            out.append((ang, pts, res))
            if res == 'goal' and critical_idx is None:
                # 与洞口边缘擦过的判定：终点离洞边缘很近
                if hole and abs(abs(pts[-1][1] - hole['c']) - hole['w'] / 2) < 6:
                    critical_idx = len(out) - 1
        if critical_idx is None:
            # 找"最后一次不进球"的轨迹作为临界候选
            last_none = None
            for i, (_, _, res) in enumerate(out):
                if res == 'none':
                    last_none = i
            critical_idx = last_none
        return out, critical_idx


# ---------------- 自检 ----------------
if __name__ == '__main__':
    print('=== engine 自检 ===')
    w = World(seed=7)
    w.setup_round(3)
    print('磁场块数 =', w.blocks_count(), '| 洞口 =',
          [(h['edge'], round(h['c'], 1)) for h in w.holes])
    w.launch(20, 270, 0, 400, +1, 0)
    w.launch(FIELD_W - 20, 270, 180, 400, -1, 1)
    steps = 0
    while steps < 240 * 30 and w.particles:
        w.step()
        steps += 1
        if w.events:
            for e in w.events:
                print(f'  [{steps*DT:6.2f}s] 事件 {e}')
            w.events = []
            if any(True for _ in []):
                break
    print('模拟结束：剩余粒子', len(w.particles), '| 总时长 %.2fs' % (steps * DT))
    print('边界落点统计：left=%d right=%d' % (len(w.hit_marks['left']), len(w.hit_marks['right'])))
    print('自适应移动：', w.adapt_holes())
    pts, res, hole = w.preview(20, 270, 10, 400, +1)
    print('预览轨迹点数 =', len(pts), '结果 =', res)
    beam, ci = w.tangent_scan(20, 270, 10, 400, +1, win=4)
    print('轨迹束条数 =', len(beam), '临界候选 =', ci, '(角度 %.1f°)' % beam[ci][0])
    # 规则校验：自己那侧的洞进不去（不会"乌龙球"）
    w2 = World(seed=3)
    w2.setup_round(1)
    w2.blocks[0].b = 0.0                          # 关掉磁场，走直线，结果确定
    w2.holes = [{'edge': 'left', 'c': 270.0, 'w': HOLE_W, 'target': 1}]   # 左洞的目标方是选手二
    w2.launch(60, 270, 180, 300, +1, 0)          # 选手一的球朝左飞向"自己那侧的洞"
    for _ in range(240):
        w2.step()
    left_hole_blocked = (len(w2.particles) == 1 and not w2.events)
    print('乌龙球校验（自己那侧的洞进不去）：', 'PASS' if left_hole_blocked else 'FAIL',
          '| 剩余粒子', len(w2.particles), '| 事件', w2.events)
    # 对面的洞必须能进
    w3 = World(seed=3)
    w3.setup_round(1)
    w3.blocks[0].b = 0.0
    w3.holes = [{'edge': 'right', 'c': 270.0, 'w': HOLE_W, 'target': 0}]  # 右洞的目标方是选手一
    w3.launch(60, 270, 0, 400, +1, 0)
    for _ in range(240 * 4):
        w3.step()
        if w3.events:
            break
    print('对面洞口可进校验：', 'PASS' if w3.events and w3.events[0][0] == 'goal' else 'FAIL',
          '| 事件', w3.events)
    # 上/下边界的洞口（玩家局中加的洞）也要能进
    w4 = World(seed=5)
    w4.setup_round(1)
    w4.blocks[0].b = 0.0
    w4.holes = [{'edge': 'top', 'c': 300.0, 'w': HOLE_W, 'target': 1}]   # 上边界的洞归选手二
    w4.launch(300, 400, -90, 400, -1, 1)               # 选手二的球从洞正下方朝上飞
    for _ in range(240 * 3):
        w4.step()
        if w4.events:
            break
    print('上边界洞口可进校验：', 'PASS' if w4.events and w4.events[0][0] == 'goal' else 'FAIL',
          '| 事件', w4.events)
    print('=== 自检完成 ===')
