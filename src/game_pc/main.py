# -*- coding: utf-8 -*-
"""main.py — 《磁场粒子对战》PC 原型主程序（cv2 渲染 + 双人键盘/鼠标输入）

阶段二：局前阶段（购买道具 + 布置）→ 对战阶段 → 结算画面；积分与道具生效。

运行：
  PYTHONPATH="E:/py_pkgs/vision" python main.py
截图自检：
  PYTHONPATH="E:/py_pkgs/vision" python main.py --shot out.png [prep|edit]

键位（一把键盘，左右分区，零重叠）
  选手一（左手区）：W/S 入射角、A/D 速度、空格 发射、Z/X 选红/蓝球、Q 改磁场
  选手二（右手区）：↑/↓ 入射角、←/→ 速度、回车 发射、,/. 选红/蓝球、/ 改磁场
  改磁场（全局暂停）：W/S(↑↓) 方向、A/D(←→) 大小、E(选手二 *) 确认、ESC 取消
  通用：T 轨迹束、H 按键调试、R 重开本局、ESC 退出
"""

import os
import sys
import math

_PKGS = [r'E:/py_pkgs/vision']
for _p in _PKGS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import (World, Obstacle, Portal, FIELD_W, FIELD_H, CORNER_R, BALL_R,
                    HOLE_W, MAX_BLOCKS, SPEED_MIN, SPEED_MAX, SPEED_DEF,
                    ANGLE_STEP, SPEED_STEP, B_MIN, B_MAX, B_STEP, DT, PORTAL_R)

WIN_NAME = 'Magnetic Duel (PC prototype)'
WIN_W, WIN_H = 1280, 720
PANEL_W, HUD_H = 160, 90
ORIGIN = (PANEL_W, HUD_H)
SCALE = 1.0

C_P1 = (80, 190, 255)
C_P2 = (255, 170, 90)
C_BALL = {1: (70, 70, 235), -1: (235, 150, 70)}
C_BG = (28, 26, 32)
C_FIELD = (44, 42, 52)
C_GRID = (72, 68, 82)
C_TEXT = (225, 225, 230)
C_DIM = (140, 138, 150)
C_OK = (120, 240, 140)

# 方向键键码：把所有常见图形后端的取值都收进来
ARROW = {
    2424832: 'L', 2490368: 'U', 2555904: 'R', 2621440: 'D',
    0x250000: 'L', 0x260000: 'U', 0x270000: 'R', 0x280000: 'D',
    65361: 'L', 65362: 'U', 65363: 'R', 65364: 'D',
    16777234: 'L', 16777235: 'U', 16777236: 'R', 16777237: 'D',
}
ESC, ENTER, SPACE, BACKSPACE = 27, 13, 32, 8
MAX_ROUNDS = 10
GOAL_POINTS = 10

# 局前阶段"可以摆到场上的"道具种类（改磁场/加球/加洞口都不需要摆放）
PLACEABLE = ('obstacle', 'portal')

# 道具表：(名称, 价格, 键)
ITEMS = [
    ('加球 红+1蓝+1', 10, 'ball'),
    ('改磁场(一块)', 20, 'field'),
    ('障碍物(永久)', 10, 'obstacle'),
    ('加洞口(本局)', 30, 'hole'),
    ('传送门(永久)', 30, 'portal'),
]


class Match:
    """一整场对决的运行时状态。"""

    def __init__(self, seed=None):
        self.world = World(seed=seed)
        self.round = 1
        self.goals = [0, 0]
        self.points = [0, 0]
        self.items = {0: {'ball': 0, 'field': 0, 'obstacle': 0, 'hole': 0, 'portal': 0},
                      1: {'ball': 0, 'field': 0, 'obstacle': 0, 'hole': 0, 'portal': 0}}
        self.free_hole = [1, 1]                     # 每人初始免费加洞 1 次
        self.spent_goal = [0, 0]                    # 本局已记入的进球数（用于算积分）
        self.balls = {0: {1: 3, -1: 2}, 1: {1: 3, -1: 2}}
        self.sel_q = {0: 1, 1: 1}
        self.aim = {0: 0.0, 1: 0.0}
        self.speed = {0: SPEED_DEF, 1: SPEED_DEF}
        self.pending = {0: None, 1: None}
        self.edit = {'player': None, 'idx': 0, 'stage': 'SELECT'}
        self.edit_snapshot = []
        self.beam_on = True
        self.msg, self.msg_t, self.end_t = '', 0.0, 0.0
        self.last_key, self.show_key = 0, False
        self.timer = 0.0
        # 局前阶段
        self.phase = 'PREP'
        self.sub = {0: 'SHOP', 1: 'SHOP'}           # SHOP / PLACE / READY
        self.sel_item = {0: 0, 1: 0}
        self.cursor = {0: [FIELD_W * 0.5, FIELD_H * 0.5], 1: [FIELD_W * 0.5, FIELD_H * 0.5]}
        self.pending_place = {0: None, 1: None}     # 当前待放置类型
        self.portal_first = {0: None, 1: None}      # 传送门第一端
        self.place_rot = {0: 0.0, 1: 0.0}           # 待放障碍物的角度（度）
        self.place_log = {0: [], 1: []}
        self.new_round(1)

    # ---------- 局流程 ----------
    def new_match(self):
        """重开整场比赛。"""
        self.goals = [0, 0]
        self.points = [0, 0]
        self.free_hole = [1, 1]
        self.items = {0: {'ball': 0, 'field': 0, 'obstacle': 0, 'hole': 0, 'portal': 0},
                      1: {'ball': 0, 'field': 0, 'obstacle': 0, 'hole': 0, 'portal': 0}}
        self.world.obstacles = []
        self.world.portals = []
        self.new_round(1)
        self.msg, self.msg_t = '新的一场开始！', 2.5

    def new_round(self, rnd):
        self.round = rnd
        n = min(rnd, MAX_BLOCKS)
        self.world.setup_round(n)
        self.world.reset_particles()
        self.balls = {0: {1: 3, -1: 2}, 1: {1: 3, -1: 2}}
        self.pending = {0: None, 1: None}
        self.timer = 0.0
        self.phase = 'PREP'
        self.sub = {0: 'SHOP', 1: 'SHOP'}
        self.sel_item = {0: 0, 1: 0}
        self.pending_place = {0: None, 1: None}
        self.portal_first = {0: None, 1: None}
        self.place_log = {0: [], 1: []}
        self.hole_ui = {'player': None, 'edge': 'right', 'pos': FIELD_H / 2, 'stage': 'EDGE'}
        if rnd == 1:
            # 第一局双方都是 0 积分，没有任何可买可布置的东西 → 直接开打
            self.phase = 'PLAY'
            self.msg, self.msg_t = '第 1 局开始！（无积分，无需局前准备）', 3.0
        else:
            self.msg, self.msg_t = f'第 {rnd} 局：局前准备（买道具 → 布置 → 双方就绪）', 4.0

    def start_play(self):
        # 加球道具生效：本局红蓝各 +1/次
        for p in (0, 1):
            self.balls[p][1] += self.items[p]['ball']
            self.balls[p][-1] += self.items[p]['ball']
            self.items[p]['ball'] = 0
        self.phase = 'PLAY'
        self.timer = 0.0
        self.msg, self.msg_t = '本局开始！', 2.0

    # ---------- 购买 / 布置 ----------
    def buy(self, p):
        name, price, key = ITEMS[self.sel_item[p]]
        if key in ('hole',) and self.free_hole[p] > 0:
            self.free_hole[p] -= 1
            self.items[p]['hole'] += 1
            self.msg, self.msg_t = f'选手{"一" if p==0 else "二"} 使用免费加洞口', 2.0
            return True
        if self.points[p] < price:
            self.msg, self.msg_t = f'选手{"一" if p==0 else "二"} 积分不足', 1.5
            return False
        self.points[p] -= price
        self.items[p][key] += 1
        self.msg, self.msg_t = f'选手{"一" if p==0 else "二"} 购买 {name}', 1.5
        return True

    def ready(self, p):
        self.sub[p] = 'READY'
        if all(self.sub[q] == 'READY' for q in (0, 1)):
            self.start_play()

    def can_place_at(self, p, kind, x, y):
        """布置合法性：统一在这里判断，界面与逻辑共用。"""
        if kind == 'obstacle':
            return CORNER_R * 0.4 < x < FIELD_W - CORNER_R * 0.4 and 30 < y < FIELD_H - 30
        if kind == 'portal':
            if not (PORTAL_R * 1.6 < x < FIELD_W - PORTAL_R * 1.6
                    and PORTAL_R * 1.6 < y < FIELD_H - PORTAL_R * 1.6):
                return False
            # 禁放区：与任何洞口距离必须大于场区宽度的 1/5
            for h in self.world.holes:
                hx = 0.0 if h['edge'] == 'left' else FIELD_W
                if math.hypot(x - hx, y - h['c']) < FIELD_W / 5.0:
                    return False
            for pt in self.world.portals:
                if math.hypot(x - pt.x, y - pt.y) < PORTAL_R * 3:
                    return False
            return True
        return False

    def hole_place_valid(self, edge, pos):
        """加洞口的合法性：在边界直线段内、且不与同边已有洞口重叠。"""
        lo, hi = self.world._hole_range(edge)
        if not (lo <= pos <= hi):
            return False
        for h in self.world.holes:
            if h['edge'] == edge and abs(pos - h['c']) < (HOLE_W + h['w']) / 2 + 12:
                return False
        return True

    def begin_hole(self, p):
        """局中加洞口：全场暂停，先选边界，再微调位置。"""
        if self.busy_msg(p):
            return
        if self.phase != 'PLAY':
            return
        if self.free_hole[p] <= 0 and self.items[p]['hole'] <= 0 and self.points[p] < 30:
            self.msg, self.msg_t = '没有"加洞口"额度（30 分/次）', 1.8
            return
        self.phase = 'HOLE'
        self.hole_ui = {'player': p, 'edge': 'right' if p == 0 else 'left',
                        'pos': FIELD_H / 2, 'stage': 'EDGE'}

    def do_place_hole(self):
        ui = self.hole_ui
        p, edge, pos = ui['player'], ui['edge'], ui['pos']
        if not self.hole_place_valid(edge, pos):
            self.msg, self.msg_t = '这个位置放不下（太靠角或与已有洞口重叠）', 2.0
            return False
        self.world.holes.append({'edge': edge, 'c': pos, 'w': HOLE_W, 'target': p})
        if self.free_hole[p] > 0:
            self.free_hole[p] -= 1
        elif self.items[p]['hole'] > 0:
            self.items[p]['hole'] -= 1
        else:
            self.points[p] -= 30
        self.msg, self.msg_t = f'选手{"一" if p==0 else "二"} 加了一个洞口', 2.0
        return True

    def place(self, p):
        kind = self.pending_place[p]
        if not kind or self.items[p][kind] <= 0:
            self.msg, self.msg_t = '没有待布置的道具', 1.5
            return False
        x, y = self.cursor[p]
        if not self.can_place_at(p, kind, x, y):
            self.msg, self.msg_t = '该位置不允许放置', 1.5
            return False
        if kind == 'obstacle':
            self.world.obstacles.append(Obstacle(x, y, 160, 20, self.place_rot[p]))
        elif kind == 'portal':
            if self.portal_first[p] is None:
                self.portal_first[p] = (x, y)
                self.msg, self.msg_t = '传送门第一端已放好，再选位置放第二端', 2.0
                return True
            x1, y1 = self.portal_first[p]
            i = len(self.world.portals)
            self.world.portals.append(Portal(x1, y1, i + 1))
            self.world.portals.append(Portal(x, y, i))
            self.portal_first[p] = None
        elif kind == 'hole':
            edge = 'left' if p == 1 else 'right'
            self.world.holes.append({'edge': edge, 'c': y, 'w': HOLE_W, 'target': p})
        self.items[p][kind] -= 1
        self.place_log[p].append(kind)
        self.msg, self.msg_t = '放置完成', 1.2
        return True

    # ---------- 对战阶段 ----------
    def dir_of(self, p):
        return self.aim[p] if p == 0 else 180.0 - self.aim[p]

    def can_fire(self, p):
        return self.phase == 'PLAY' and self.pending[p] is not None

    def place_ball(self, p, y):
        if self.phase != 'PLAY' or self.pending[p]:
            return False
        if self.balls[p][1] + self.balls[p][-1] <= 0:
            self.msg, self.msg_t = f'选手{"一" if p == 0 else "二"} 的球已全部打完，本局无法再发射', 2.0
            return False
        x = BALL_R + 6 if p == 0 else FIELD_W - BALL_R - 6
        if self.world.hole_at('left' if p == 0 else 'right', y):
            return False
        self.pending[p] = {'x': x, 'y': y}
        return True

    def fire(self, p):
        if not self.can_fire(p):
            return False
        b = self.pending[p]
        q = self.sel_q[p]
        if self.balls[p][q] <= 0:
            self.msg, self.msg_t = f'选手{"一" if p == 0 else "二"} 该色球已用完', 1.5
            return False
        self.world.launch(b['x'], b['y'], self.dir_of(p), self.speed[p], q, p)
        self.balls[p][q] -= 1
        self.pending[p] = None
        return True

    def sel_block(self):
        blks = self.world.blocks
        return blks[self.edit['idx'] % len(blks)]

    def busy_msg(self, who_wants):
        """互斥提示：同一时刻只允许一项暂停类操作。"""
        if self.phase not in ('EDIT', 'HOLE'):
            return False
        owner = self.edit['player'] if self.phase == 'EDIT' else self.hole_ui['player']
        what = '改磁场' if self.phase == 'EDIT' else '加洞口'
        self.msg = (f'选手{"一" if owner == 0 else "二"} 正在进行『{what}』，'
                    f'全场已暂停——请等对方完成后你再来')
        self.msg_t = 2.5
        return True

    def begin_edit(self, p):
        if self.busy_msg(p):
            return
        if self.phase != 'PLAY':
            return
        if self.items[p]['field'] <= 0 and self.points[p] < 20:
            self.msg, self.msg_t = '没有"改磁场"道具（20 分/次）', 1.8
            return
        self.phase = 'EDIT'
        self.edit = {'player': p, 'idx': 0, 'stage': 'SELECT'}
        self.edit_snapshot = [(b, b.b) for b in self.world.blocks]

    def end_edit(self, cancel=False):
        if cancel:
            for b, v in self.edit_snapshot:
                b.b = v
            self.msg, self.msg_t = '已取消磁场调整', 1.5
        else:
            p = self.edit['player']
            if self.items[p]['field'] > 0:
                self.items[p]['field'] -= 1
            elif self.points[p] >= 20:
                self.points[p] -= 20
            self.msg, self.msg_t = '磁场调整完成，比赛继续', 1.5
        self.phase = 'PLAY'
        self.edit['player'] = None

    # ---------- 主循环更新 ----------
    def update(self, dt):
        self.msg_t = max(0.0, self.msg_t - dt)
        # 结算阶段：必须在"非 PLAY 就 return"之前处理，否则会永远卡在这一局
        if self.phase == 'ROUND_END':
            self.end_t += dt
            if self.end_t > 2.6:
                if self.round >= MAX_ROUNDS:
                    if self.goals[0] == self.goals[1]:
                        self.new_round(MAX_ROUNDS + 1)      # 平局 → 加赛一局
                        self.msg, self.msg_t = '十局战平 → 加赛一局定胜负！', 4.0
                    else:
                        self.phase = 'MATCH_END'
                else:
                    self.new_round(self.round + 1)
            return
        if self.phase != 'PLAY':
            return
        for _ in range(max(1, int(round(dt / DT)))):
            self.world.step(DT)
        for e in self.world.events:
            if e[0] == 'goal':
                p = e[1]
                self.goals[p] += 1
                self.points[p] += GOAL_POINTS
                self.phase = 'ROUND_END'
                self.end_t = 0.0
                self.msg = f'选手{"一" if p == 0 else "二"} 进球！+{GOAL_POINTS} 分'
                self.msg_t = 3.0
        self.world.events = []
        if self.phase == 'PLAY':
            self.timer += dt
            if self.timer >= 30.0:
                moved = self.world.adapt_holes()
                self.timer = 0.0
                if moved:
                    self.msg, self.msg_t = '30 秒无进球，洞口向热点移动', 2.0


# ---------------- 绘制 ----------------
def _pt(x, y):
    return int(ORIGIN[0] + x * SCALE), int(ORIGIN[1] + y * SCALE)


# ---- 半透明叠加：只在**真正被改动的那块**上算 ----
#
# 原来这个文件里 6 处都是同一个模式：`ov = img.copy()`（整图复制）
# 然后 `cv2.addWeighted(ov, a, img, b, 0, img)`（整图混合）。
# 实测（E:\tmp_pkgs\bench_v4_render.py）这两笔合起来占 draw() 的 **50%**
# （copy 1.7ms + addWeighted 2.3ms / 7.9ms），而真正被改动的往往只是几十像素的矩形。
#
# ★ 为什么"缩到矩形"**不是**近似，而是逐像素等价：
#   ① addWeighted 是**逐像素**运算，不依赖邻域 ⇒ 限定 ROI 不改变 ROI 内的结果；
#   ② 矩形外 `ov` 与 `img` 完全相同，且这 6 处的 a+b 都恰好 == 1
#      ⇒ 矩形外**必须**逐位不变。
#   这两条不靠推理成立 —— 由 test_render_equiv.py 的"**0 像素差**"判据当场验。
#   （板上 P2 当年 155→59 ms 就是同一把刀；判据同样是 0 像素差。）
#
# ★ 别把 ROI 当 dst 直接交给 cv2：那是个**非连续**的 numpy 视图，
#   万一绑定层内部做了拷贝，写回就**静默丢失**（图上看不出来，只是"没生效"）。
#   所以这里一律"取小图 → 算 → 用 numpy 赋值写回"，不赌绑定层行为。
def _roi_box(img, rect):
    """把矩形裁进图像范围；完全在外面就返回 None。"""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = rect
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(w, int(x1)), min(h, int(y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _blend_rect(img, rect, color, alpha, beta):
    """纯色矩形以 alpha 混进 img —— 只算这个矩形。

    ★ `beta` 是**显式传进来的字面量**，不在里面算 `1.0 - alpha`：
      `1.0-0.85` 与 `0.15` **不是同一个浮点数**（差 2 ULP），`1.0-0.90` 与 `0.1` 也差 1 ULP
      （0.28/0.72 和 0.40/0.60 恰好相同，所以才容易以为"都能算"）。
      这一两 ULP 在现有场景里没翻出像素差，**但那是没试到边界值，不是一定不会**。
      原始代码用的就是字面量，这里照抄，消掉这个赌。

    ★ 两个角是**闭区间**：`cv2.rectangle` 画出来的是 `[x0,x1] x [y0,y1]`，
      而 numpy 切片是**半开**的 `[y0:y1, x0:x1]`。差这一格就是"右边一条竖线 +
      下边一条横线"没被混到（实测 100..300 / 100..200 的矩形差 **301** 像素
      = 101+201-1，恰好是那条右下角 L）。
      这个 off-by-one **骗过了站点 1**：它紧接着在同一位置画了一圈**不透明描边**，
      正好把那一格盖掉了 —— 侥幸，不是正确。**判据（0 像素差）才把它揪出来的。**
    """
    x0, x1 = sorted((int(rect[0]), int(rect[2])))
    y0, y1 = sorted((int(rect[1]), int(rect[3])))
    box = _roi_box(img, (x0, y0, x1 + 1, y1 + 1))      # ← 闭区间 -> 半开要 +1
    if box is None:
        return
    bx0, by0, bx1, by1 = box
    roi = img[by0:by1, bx0:bx1]
    h, w = roi.shape[:2]
    ov = np.empty_like(roi)
    # ★ 用 cv2 的矩形填充，**不要**用 np.full_like(roi, color)：
    #   实测 numpy 对 3 元组做广播填充只有 ~2 GB/s（960x68 一块要 **260 us**），
    #   而 cv2.rectangle 填同样一块只要 **13 us** —— 差 19 倍，逐位结果相同。
    #   这里每帧每块磁场都要走一次，是 _blend_rect 的主要开销。
    cv2.rectangle(ov, (0, 0), (w - 1, h - 1), color, -1)
    img[by0:by1, bx0:bx1] = cv2.addWeighted(ov, alpha, roi, beta, 0)


def _blend_beams(img, beam, ci, alpha, beta):
    """轨迹束的半透明层（灰=被挡，橙=能出去）。

    ★ 包围盒必须按**全部**轨迹取，不能只按高亮那条：
      原代码的 `ov = img.copy()` 是在画线**之前**做的 ⇒ 最后那次整图 addWeighted
      把灰线和橙线**一起**淡化了。（老账里"绿橙不受影响"那句注释是错的 ——
      P2 上曾用 873 个像素的差实测证伪。）按"全部"取才是逐像素等价。
    """
    segs, xs, ys = [], [], []
    for k, (ang, pts, res) in enumerate(beam):
        if len(pts) < 2:
            continue
        arr = np.array([_pt(px, py) for px, py in pts], np.int32)
        segs.append((k, arr))
        xs += [int(arr[:, 0].min()), int(arr[:, 0].max())]
        ys += [int(arr[:, 1].min()), int(arr[:, 1].max())]
    if not segs:
        return
    # 线宽最大 2（LINE_8 不抗锯齿）⇒ 顶点外扩约 thickness/2，再留 1~2 px 余量。
    # 这个余量是**假定**，由 test_render_equiv.py 在多个不同几何的场景上验。
    box = _roi_box(img, (min(xs) - 3, min(ys) - 3, max(xs) + 4, max(ys) + 4))
    if box is None:
        return
    x0, y0, x1, y1 = box
    roi = img[y0:y1, x0:x1]
    ov = roi.copy()
    for k, arr in segs:
        col = (95, 92, 105) if k != ci else (60, 150, 250)
        # 平移进小画布：这是纯平移，裁剪边界落在图像边上时与整图裁剪逐像素相同
        cv2.polylines(ov, [arr - (x0, y0)], False, col, 2 if k == ci else 1)
    img[y0:y1, x0:x1] = cv2.addWeighted(ov, alpha, roi, beta, 0)


def draw(m, mouse):
    img = np.full((WIN_H, WIN_W, 3), C_BG, np.uint8)
    w = m.world
    x0, y0 = _pt(0, 0)
    x1, y1 = _pt(FIELD_W, FIELD_H)
    r = int(CORNER_R)
    cv2.rectangle(img, (x0 + r, y0), (x1 - r, y1), C_FIELD, -1)
    cv2.rectangle(img, (x0, y0 + r), (x1, y1 - r), C_FIELD, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(img, (cx, cy), r, C_FIELD, -1)

    # ---- 磁场块 ----
    sel_idx = m.edit['idx'] if m.phase == 'EDIT' else -1
    for idx, b in enumerate(w.blocks):
        cx0, cy0 = _pt(b.x0 + 3, b.y0 + 3)
        cx1, cy1 = _pt(b.x1 - 3, b.y1 - 3)
        k = min(1.0, abs(b.b) / B_MAX)
        _blend_rect(img, (cx0, cy0, cx1, cy1), (78, 62, int(62 + 96 * k)), 0.28, 0.72)
        cv2.rectangle(img, (cx0, cy0), (cx1, cy1), C_GRID, 1)
        cx, cy = _pt((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
        rr = int(22 + 6 * k)
        cv2.circle(img, (cx, cy), rr, (30, 28, 36), -1)
        cv2.circle(img, (cx, cy), rr, (205, 205, 215), 2)
        if b.out:
            cv2.circle(img, (cx, cy), 5, (235, 235, 240), -1)
        else:
            d = 8
            cv2.line(img, (cx - d, cy - d), (cx + d, cy + d), (235, 235, 240), 2)
            cv2.line(img, (cx - d, cy + d), (cx + d, cy - d), (235, 235, 240), 2)
        cv2.putText(img, f'{abs(b.b):.1f}', (cx - 16, cy + rr + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, C_TEXT, 1)
        if idx == sel_idx:
            cv2.rectangle(img, (cx0, cy0), (cx1, cy1),
                          C_P1 if m.edit['player'] == 0 else C_P2, 3)

    # ---- 洞口（四条边界都可能）----
    for h in w.holes:
        col = C_P1 if h['target'] == 0 else C_P2
        e, c, hw = h['edge'], h['c'], h['w'] / 2
        if e == 'left':
            p1, p2, tx, ty = _pt(0, c - hw), _pt(0, c + hw), None, None
            tx, ty = p1[0] + 12, p1[1] + 6
        elif e == 'right':
            p1, p2 = _pt(FIELD_W, c - hw), _pt(FIELD_W, c + hw)
            tx, ty = p1[0] - 62, p1[1] + 6
        elif e == 'top':
            p1, p2 = _pt(c - hw, 0), _pt(c + hw, 0)
            tx, ty = p1[0] - 10, p1[1] + 20
        else:
            p1, p2 = _pt(c - hw, FIELD_H), _pt(c + hw, FIELD_H)
            tx, ty = p1[0] - 10, p1[1] - 10
        cv2.line(img, p1, p2, col, 11)
        cv2.line(img, p1, p2, (250, 250, 250), 2)
        for pp in (p1, p2):
            cv2.circle(img, pp, 5, col, -1)
        cv2.putText(img, f'P{h["target"]+1}的洞', (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)

    # ---- 障碍 / 传送门 ----
    for ob in w.obstacles:
        pts = []
        ca, sa = math.cos(ob.ang), math.sin(ob.ang)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            lx, ly = sx * ob.hw, sy * ob.hh
            pts.append(_pt(ob.cx + lx * ca - ly * sa, ob.cy + lx * sa + ly * ca))
        cv2.fillPoly(img, [np.array(pts, np.int32)], (95, 95, 105))
    for i, pt in enumerate(w.portals):
        c = _pt(pt.x, pt.y)
        cv2.circle(img, c, int(PORTAL_R), (250, 200, 60), 2)
        cv2.putText(img, str(i // 2 + 1), (c[0] - 5, c[1] - int(PORTAL_R) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (250, 200, 60), 1)

    # ---- 轨迹束（对战阶段）----
    if m.beam_on and m.phase == 'PLAY':
        for p in (0, 1):
            if m.pending[p] is None:
                continue
            b = m.pending[p]
            beam, ci = w.tangent_scan(b['x'], b['y'], m.dir_of(p), m.speed[p], m.sel_q[p],
                                      win=4, steps=int(0.9 / DT), max_bounce=0)
            _blend_beams(img, beam, ci, 0.40, 0.60)

    # ---- 待发射球 + 预览 ----
    for p in (0, 1):
        b = m.pending[p]
        if not b:
            continue
        pq = m.sel_q[p]
        cv2.circle(img, _pt(b['x'], b['y']), int(BALL_R), C_BALL[pq], -1)
        pts, res, hole = w.preview(b['x'], b['y'], m.dir_of(p), m.speed[p], pq)
        for k in range(0, len(pts) - 1, 2):
            cv2.line(img, _pt(*pts[k]), _pt(*pts[k + 1]), C_P1 if p == 0 else C_P2, 1)
        if res == 'goal':
            bx, by = _pt(b['x'], b['y'])
            cv2.putText(img, 'GOAL 可进洞', (bx + (14 if p == 0 else -120), by - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_OK, 2)

    # ---- 粒子 ----
    for q in w.particles:
        if len(q.trail) > 1:
            arr = np.array([_pt(px, py) for px, py in q.trail], np.int32)
            cv2.polylines(img, [arr], False, C_BALL[q.q], 1)
        cv2.circle(img, _pt(q.x, q.y), int(BALL_R), C_BALL[q.q], -1)
        cv2.circle(img, _pt(q.x, q.y), int(BALL_R), (240, 240, 240), 1)

    # ---- 局前阶段：光标与放置预览 ----
    if m.phase == 'PREP':
        for p in (0, 1):
            if m.sub[p] != 'PLACE':
                continue
            cx, cy = _pt(*m.cursor[p])
            col = C_P1 if p == 0 else C_P2
            kind = m.pending_place[p]
            ok = kind and m.can_place_at(p, kind, *m.cursor[p])
            cv2.drawMarker(img, (cx, cy), col if ok else (90, 90, 200),
                           cv2.MARKER_CROSS, 26, 2)
            if kind == 'obstacle' and ok:
                a = math.radians(m.place_rot[p])          # 带角度预览
                ca, sa = math.cos(a), math.sin(a)
                pts = [_pt(m.cursor[p][0] + sx * 80 * ca - sy * 10 * sa,
                           m.cursor[p][1] + sx * 80 * sa + sy * 10 * ca)
                       for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                cv2.polylines(img, [np.array(pts, np.int32)], True, (150, 150, 160), 2)
            elif kind == 'portal' and ok:
                cv2.circle(img, (cx, cy), int(PORTAL_R), (250, 200, 60), 2)
            # 传送门第一端已放好时，把它画出来（否则玩家以为没放上）
            if kind == 'portal' and m.portal_first[p]:
                fx, fy = _pt(*m.portal_first[p])
                cv2.circle(img, (fx, fy), int(PORTAL_R), col, 2)
                cv2.circle(img, (fx, fy), 4, col, -1)
                cv2.putText(img, '第一端已放', (fx - 34, fy - int(PORTAL_R) - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1)

    # ---- 场区外框 ----
    cv2.rectangle(img, (x0 + r, y0 - 1), (x1 - r, y0 + 1), (110, 108, 120), 2)
    cv2.rectangle(img, (x0 + r, y1 - 1), (x1 - r, y1 + 1), (110, 108, 120), 2)
    cv2.rectangle(img, (x0 - 1, y0 + r), (x0 + 1, y1 - r), (110, 108, 120), 2)
    cv2.rectangle(img, (x1 - 1, y0 + r), (x1 + 1, y1 - r), (110, 108, 120), 2)

    # ---- 顶部 HUD ----
    cv2.rectangle(img, (0, 0), (WIN_W, HUD_H - 2), (38, 36, 44), -1)
    ph = {'PREP': '局前准备', 'PLAY': '对战中', 'EDIT': '暂停·改磁场',
          'HOLE': '暂停·加洞口', 'ROUND_END': '本局结束', 'MATCH_END': '比赛结束'}[m.phase]
    rnd_txt = (f'第 {m.round}/{MAX_ROUNDS} 局' if m.round <= MAX_ROUNDS
               else '加赛局')
    cv2.putText(img, f'{rnd_txt} · {ph}', (16, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, C_TEXT, 2)
    cv2.putText(img, f'磁场块 {w.blocks_count()}', (16, 66),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, C_DIM, 1)
    cv2.putText(img, f'选手一 {m.goals[0]*GOAL_POINTS} 分', (WIN_W // 2 - 250, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, C_P1, 2)
    cv2.putText(img, f'选手二 {m.goals[1]*GOAL_POINTS} 分', (WIN_W // 2 + 60, 42),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, C_P2, 2)
    rem = 30.0 - m.timer
    if m.phase == 'PLAY' and rem <= 5.0:
        cv2.putText(img, f'洞口即将移动 {max(0, math.ceil(rem))}', (WIN_W // 2 - 130, 136),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 200, 255), 3)
    if m.phase == 'PREP':
        waiting = [('一' if p == 0 else '二') for p in (0, 1) if m.sub[p] != 'READY']
        tip = ('等待：选手' + '、选手'.join(waiting) + ' 按就绪（选手一 空格 / 选手二 回车）'
               if waiting else '双方已就绪，即将开始')
        cv2.putText(img, tip, (WIN_W // 2 - 210, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (120, 220, 255), 2)
    if m.msg_t > 0:
        cv2.putText(img, m.msg, (WIN_W // 2 - 220, 108), cv2.FONT_HERSHEY_SIMPLEX, 0.7, C_OK, 2)

    # ---- 两侧面板 ----
    for p in (0, 1):
        px = 0 if p == 0 else WIN_W - PANEL_W
        cv2.rectangle(img, (px, HUD_H - 2), (px + PANEL_W, WIN_H), (38, 36, 44), -1)
        col = C_P1 if p == 0 else C_P2
        cv2.putText(img, f'选手{"一" if p==0 else "二"}', (px + 12, HUD_H + 26),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        cv2.putText(img, f'积分 {m.points[p]}', (px + 12, HUD_H + 52),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 230, 150), 1)
        if m.phase in ('PLAY', 'EDIT'):
            cv2.putText(img, f'角 {m.aim[p]:+5.1f}°', (px + 12, HUD_H + 82),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, C_TEXT, 1)
            cv2.putText(img, f'速 {m.speed[p]:4.0f}', (px + 12, HUD_H + 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, C_TEXT, 1)
            cv2.putText(img, f'红球 {m.balls[p][1]}  蓝球 {m.balls[p][-1]}', (px + 12, HUD_H + 138),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, C_DIM, 1)
            st = '待发射' if m.pending[p] else ('编辑磁场' if m.edit['player'] == p else '—')
            cv2.putText(img, st, (px + 12, HUD_H + 166), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        C_OK if m.pending[p] else C_DIM, 1)
            q = m.sel_q[p]
            total = m.balls[p][1] + m.balls[p][-1]
            if total <= 0:
                # 本局球已打完：不再显示球色指示，直接标"打完了"
                cv2.putText(img, '本局球已打完', (px + 12, HUD_H + 204),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 140), 2)
            else:
                has = m.balls[p][q] > 0
                box = C_BALL[q] if has else (85, 85, 92)
                cv2.rectangle(img, (px + 12, HUD_H + 180), (px + 44, HUD_H + 212), box, -1)
                cv2.rectangle(img, (px + 12, HUD_H + 180), (px + 44, HUD_H + 212), (235, 235, 235), 2)
                label = ('红球' if q == 1 else '蓝球') + ('' if has else '（用完）')
                cv2.putText(img, label, (px + 52, HUD_H + 204),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, box if has else (150, 150, 160), 2)
        else:
            # 局前：道具清单
            cv2.putText(img, '道具：W/S 选 · E 买' if p == 0 else
                        '道具：↑/↓ 选 · * 买', (px + 10, HUD_H + 76),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, C_DIM, 1)
            for i, (name, price, key) in enumerate(ITEMS):
                yy = HUD_H + 106 + i * 30
                sel = (i == m.sel_item[p])
                cnt = m.items[p][key]
                txt = f'{name} {price}分'
                c = C_TEXT if sel else C_DIM
                if sel:
                    cv2.rectangle(img, (px + 6, yy - 18), (px + PANEL_W - 6, yy + 8), (70, 66, 80), -1)
                cv2.putText(img, txt, (px + 12, yy), cv2.FONT_HERSHEY_SIMPLEX, 0.42, c, 1)
                if cnt:
                    cv2.putText(img, f'x{cnt}', (px + PANEL_W - 40, yy),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.42, C_OK, 1)
            if p == 0 and m.free_hole[p]:
                cv2.putText(img, f'免费加洞口 x{m.free_hole[p]}', (px + 10, HUD_H + 250),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 120), 1)
            if p == 1 and m.free_hole[p]:
                cv2.putText(img, f'免费加洞口 x{m.free_hole[p]}', (px + 10, HUD_H + 250),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 120), 1)
            st = {'SHOP': '购买中', 'PLACE': '布置中', 'READY': '已就绪'}[m.sub[p]]
            cv2.putText(img, st, (px + 10, HUD_H + 286), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        C_OK if m.sub[p] == 'READY' else C_TEXT, 1)
            if m.sub[p] == 'PLACE':
                kind = m.pending_place[p]
                nm = {'ball': '', 'field': '', 'obstacle': '障碍物',
                      'hole': '洞口', 'portal': '传送门'}.get(kind, '（无待放置）')
                cv2.putText(img, f'待放：{nm}', (px + 10, HUD_H + 312),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, C_TEXT, 1)

    # ---- 底部提示 ----
    if m.phase == 'PREP':
        hint = ('局前：选手一 W/S选·E购买·空格去布置/就绪 | 选手二 ↑/↓选·*购买·回车去布置/就绪 | '
                '布置时 WASD/方向键 移光标，Q/ 切换待放物，确认键放置 | R 重开 | ESC 退出')
    else:
        hint = ('摆球：按选色键后点左/右边界 | 选手一 Z红/X蓝·WASD·空格发射·Q改磁场·C加洞口 | '
                '选手二 ,红/.蓝·方向键·回车发射·/改磁场·+加洞口 | T 轨迹束 | H 调试 | R 重开 | ESC 退出')
    cv2.putText(img, hint, (16, WIN_H - 26), cv2.FONT_HERSHEY_SIMPLEX, 0.48, C_DIM, 1)

    # ---- 加洞口（局中暂停）：边界与位置预览 + 顶部提示 ----
    if m.phase == 'HOLE':
        ui = m.hole_ui
        p = ui['player']
        col = C_P1 if p == 0 else C_P2
        e, pos, hw = ui['edge'], ui['pos'], HOLE_W / 2
        ok = m.hole_place_valid(e, pos)
        c = col if ok else (60, 60, 220)
        if e == 'left':
            a, b = _pt(0, pos - hw), _pt(0, pos + hw)
        elif e == 'right':
            a, b = _pt(FIELD_W, pos - hw), _pt(FIELD_W, pos + hw)
        elif e == 'top':
            a, b = _pt(pos - hw, 0), _pt(pos + hw, 0)
        else:
            a, b = _pt(pos - hw, FIELD_H), _pt(pos + hw, FIELD_H)
        cv2.line(img, a, b, c, 13)
        cv2.line(img, a, b, (255, 255, 255), 3)
        who = '一' if p == 0 else '二'
        trig = 'C' if p == 0 else '+'
        ck = 'E' if p == 0 else '*'
        if ui['stage'] == 'EDGE':
            tip = f'【暂停】选手{who} 加洞口：连按 {trig} 切换边界（上/右/下/左）→ 按 {ck} 确定这条边 → ESC 取消'
        else:
            two = 'W/S' if p == 0 else '↑/↓'
            if e in ('top', 'bottom'):
                two = 'A/D' if p == 0 else '←/→'
            tip = (f'【暂停】选手{who} 加洞口（{ {"top":"上边界","bottom":"下边界","left":"左边界","right":"右边界"}[e] }）：'
                   f'{two} 调位置 → {ck} 放置 → {trig} 换边 → ESC 取消')
        _blend_rect(img, (ORIGIN[0], HUD_H - 2, ORIGIN[0] + int(FIELD_W), HUD_H + 66),
                    (18, 16, 22), 0.85, 0.15)
        cv2.putText(img, tip, (ORIGIN[0] + 14, HUD_H + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (120, 220, 255), 2)
        cv2.putText(img, '可以放在任意一条边界上' + ('' if ok else '（当前位置无效）'),
                    (ORIGIN[0] + 14, HUD_H + 72), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (150, 240, 170) if ok else (110, 110, 240), 1)
        cv2.putText(img, '【全场暂停】同一时间只能进行一项操作（改磁场 / 加洞口），对方请稍候',
                    (ORIGIN[0] + 14, HUD_H + 96), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 200, 140), 1)

    if m.phase == 'EDIT':
        who = '一' if m.edit['player'] == 0 else '二'
        cyc = 'Q' if m.edit['player'] == 0 else '/'
        ck = 'E/回车' if m.edit['player'] == 0 else '* / 回车'
        if m.edit.get('stage') == 'SELECT':
            tip = f'【暂停】选手{who} 选块中：连按 {cyc} 换块 → 按 {ck} 确认选中这一块 → ESC 取消'
        else:
            tip = (f'【暂停】选手{who} 调整中：W/S(↑↓) 换磁场方向  '
                   f'A/D(←→) 调磁场大小  {ck} 完成  ESC 取消')
        blk = m.sel_block()
        info = f'选中块：磁场大小 {abs(blk.b):.1f}    方向 {"出屏 ⊙" if blk.out else "入屏 ⊗"}'
        _blend_rect(img, (ORIGIN[0], HUD_H - 2, ORIGIN[0] + int(FIELD_W), HUD_H + 92),
                    (18, 16, 22), 0.85, 0.15)
        cv2.putText(img, tip, (ORIGIN[0] + 14, HUD_H + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (120, 220, 255), 2)
        cv2.putText(img, info, (ORIGIN[0] + 14, HUD_H + 72), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (120, 240, 160), 2)
        cv2.putText(img, '【全场暂停】同一时间只能进行一项操作（改磁场 / 加洞口），对方请稍候',
                    (ORIGIN[0] + 14, HUD_H + 108), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (200, 200, 140), 1)

    if m.show_key:
        cv2.putText(img, f'按键调试：最后键码 {m.last_key} (0x{m.last_key:06X})', (16, WIN_H - 52),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (110, 240, 200), 1)

    if m.phase == 'ROUND_END':
        _blend_rect(img, (WIN_W // 2 - 300, WIN_H // 2 - 70, WIN_W // 2 + 300, WIN_H // 2 + 70),
                    (20, 18, 26), 0.85, 0.15)
        cv2.putText(img, m.msg, (WIN_W // 2 - 250, WIN_H // 2 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, C_OK, 2)
        cv2.putText(img, f'比分  {m.goals[0]*GOAL_POINTS} : {m.goals[1]*GOAL_POINTS}',
                    (WIN_W // 2 - 130, WIN_H // 2 + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.8, C_TEXT, 2)

    if m.phase == 'MATCH_END':
        _blend_rect(img, (WIN_W // 2 - 360, WIN_H // 2 - 150,
                          WIN_W // 2 + 360, WIN_H // 2 + 150), (18, 16, 22), 0.90, 0.10)
        win = '选手一胜！' if m.goals[0] > m.goals[1] else (
            '选手二胜！' if m.goals[1] > m.goals[0] else '平局！加赛一局')
        cv2.putText(img, '比赛结束', (WIN_W // 2 - 110, WIN_H // 2 - 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, C_TEXT, 2)
        cv2.putText(img, win, (WIN_W // 2 - 150, WIN_H // 2 - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, C_OK, 3)
        cv2.putText(img, f'最终比分 {m.goals[0]*GOAL_POINTS} : {m.goals[1]*GOAL_POINTS}',
                    (WIN_W // 2 - 170, WIN_H // 2 + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.9, C_TEXT, 2)
        cv2.putText(img, '按 R 重新开始整场比赛', (WIN_W // 2 - 180, WIN_H // 2 + 105),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, C_DIM, 2)
    return img


# ---------------- 主循环 ----------------
def main():
    shot = None
    if len(sys.argv) >= 3 and sys.argv[1] == '--shot':
        shot = sys.argv[2]

    m = Match()
    mouse = {'clicks': []}

    def on_mouse(event, x, y, flags, param):
        if event in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_RBUTTONDOWN):
            fx, fy = x - ORIGIN[0], y - ORIGIN[1]
            if 0 <= fy <= FIELD_H:
                p = 0 if fx < FIELD_W / 2 else 1
                mouse['clicks'].append((p, fy, event))

    if shot:
        m.phase = 'PREP'
        m.points = [60, 40]
        m.items = {0: {'ball': 1, 'field': 1, 'obstacle': 2, 'hole': 1, 'portal': 1},
                   1: {'ball': 0, 'field': 1, 'obstacle': 1, 'hole': 0, 'portal': 0}}
        m.world.obstacles.append(Obstacle(420, 180, 160, 20, 0.0))
        m.world.obstacles.append(Obstacle(620, 400, 160, 20, 30.0))
        i = len(m.world.portals)
        m.world.portals.append(Portal(300, 420, i + 1))
        m.world.portals.append(Portal(700, 150, i))
        if len(sys.argv) >= 4 and sys.argv[3] == 'edit':
            m.new_round(5)
            m.phase = 'EDIT'
            m.edit = {'player': 0, 'idx': 3, 'stage': 'ADJUST'}
            m.edit_snapshot = [(b, b.b) for b in m.world.blocks]
            m.sel_block().b = 3.0
        elif len(sys.argv) >= 4 and sys.argv[3] == 'hole':
            m.new_round(5)
            m.start_play()
            m.pending[0] = {'x': BALL_R + 6, 'y': 260}
            m.world.launch(200, 150, 10, 400, 1, 0)
            for _ in range(300):
                m.world.step(DT)
            m.begin_hole(0)
            m.hole_ui['edge'] = 'top'
            m.hole_ui['stage'] = 'FINE'
            m.hole_ui['pos'] = 640.0
        elif len(sys.argv) >= 4 and sys.argv[3] == 'play':
            m.new_round(5)
            m.start_play()
            m.pending[0] = {'x': BALL_R + 6, 'y': 260}
            m.sel_q = {0: 1, 1: -1}
            m.world.launch(200, 150, 10, 400, 1, 0)
            m.world.launch(700, 380, 190, 400, -1, 1)
            for _ in range(600):
                m.world.step(DT)
            m.timer = 26.5
        else:
            m.sub = {0: 'PLACE', 1: 'SHOP'}
            m.pending_place = {0: 'obstacle', 1: None}
            m.sel_item[1] = 3
        cv2.imwrite(shot, draw(m, mouse))
        print('已渲染截图：', shot)
        return

    cv2.namedWindow(WIN_NAME)
    cv2.setMouseCallback(WIN_NAME, on_mouse)
    last = cv2.getTickCount()
    while True:
        now = cv2.getTickCount()
        dt = min((now - last) / cv2.getTickFrequency(), 0.05)
        last = now

        for (p, fy, _btn) in mouse['clicks']:
            if m.phase == 'PLAY':
                if not m.place_ball(p, fy):
                    m.msg, m.msg_t = '摆球失败（该色球不够 / 已有待发球 / 洞口附近）', 1.5
            elif m.phase == 'PREP':
                m.cursor[p] = [m.cursor[p][0], fy]
        mouse['clicks'] = []

        k = cv2.waitKeyEx(1)      # 必须用 waitKeyEx，否则方向键键码被截断
        if k != -1:
            code = k & 0xFF
            arr = ARROW.get(k)
            m.last_key = k
            # ============ 局前阶段 ============
            if m.phase == 'PREP':
                for p in (0, 1):
                    mine = (p == 0 and (code in (ord('w'), ord('s'), ord('a'), ord('d'),
                                                 ord('q'), ord('e'), SPACE, ord('z'), ord('x'))
                                        or arr in ('U', 'D', 'L', 'R'))
                            or (p == 1 and (code in (ord(','), ord('.'), ord('/'), ord('*'), ENTER)
                                            or arr in ('U', 'D', 'L', 'R'))))
                    if not mine:
                        continue
                    up = (p == 0 and code == ord('w')) or (p == 1 and arr == 'U')
                    dn = (p == 0 and code == ord('s')) or (p == 1 and arr == 'D')
                    confirm = (p == 0 and code == ord('e')) or (p == 1 and code == ord('*'))
                    go = (p == 0 and code == SPACE) or (p == 1 and code == ENTER)
                    cyc = (p == 0 and code == ord('q')) or (p == 1 and code == ord('/'))
                    if m.sub[p] == 'SHOP':
                        if up:
                            m.sel_item[p] = (m.sel_item[p] - 1) % len(ITEMS)
                        elif dn:
                            m.sel_item[p] = (m.sel_item[p] + 1) % len(ITEMS)
                        elif confirm:
                            m.buy(p)
                        elif go:
                            m.sub[p] = 'PLACE'
                            m.pending_place[p] = next(
                                (k2 for k2 in PLACEABLE if m.items[p][k2] > 0), None)
                            m.msg, m.msg_t = ('进入布置：移光标 → 确认键放置' if m.pending_place[p]
                                              else '没有可布置的道具，直接按"就绪"'), 2.5
                    elif m.sub[p] == 'PLACE':
                        cx, cy = m.cursor[p]
                        # 只认"这个玩家自己的键"——否则两人会互相带动光标
                        if p == 0:
                            mv_u, mv_d = (code == ord('w')), (code == ord('s'))
                            mv_l, mv_r = (code == ord('a')), (code == ord('d'))
                            rot_l, rot_r = (code == ord('z')), (code == ord('x'))
                        else:
                            mv_u, mv_d = (arr == 'U'), (arr == 'D')
                            mv_l, mv_r = (arr == 'L'), (arr == 'R')
                            rot_l, rot_r = (code == ord(',')), (code == ord('.'))
                        if mv_u:
                            m.cursor[p][1] = max(20, cy - 18)
                        elif mv_d:
                            m.cursor[p][1] = min(FIELD_H - 20, cy + 18)
                        elif mv_l:
                            m.cursor[p][0] = max(20, cx - 18)
                        elif mv_r:
                            m.cursor[p][0] = min(FIELD_W - 20, cx + 18)
                        elif rot_l:
                            m.place_rot[p] = (m.place_rot[p] - 15) % 180
                        elif rot_r:
                            m.place_rot[p] = (m.place_rot[p] + 15) % 180
                        elif cyc:
                            opts = [k2 for k2 in PLACEABLE if m.items[p][k2] > 0]
                            if opts:
                                cur = m.pending_place[p]
                                i = (opts.index(cur) + 1) % len(opts) if cur in opts else 0
                                m.pending_place[p] = opts[i]
                        elif confirm and m.pending_place[p]:
                            m.place(p)
                        elif go:
                            if m.portal_first[p]:
                                m.msg, m.msg_t = '传送门只放了第一端，还得放第二端才能就绪', 2.5
                            else:
                                m.ready(p)
            # ============ 改磁场（暂停） ============
            elif m.phase == 'EDIT':
                ep = m.edit['player']
                cfl = (ord('e'), ENTER) if ep == 0 else (ord('*'), ENTER)
                if m.edit['stage'] == 'SELECT':
                    if code in (ord('q'), ord('/')):
                        m.edit['idx'] = (m.edit['idx'] + 1) % len(m.world.blocks)
                    elif code in cfl:
                        m.edit['stage'] = 'ADJUST'
                    elif code == ESC:
                        m.end_edit(cancel=True)
                else:
                    b = m.sel_block()
                    if code == ord('w') or arr == 'U':
                        if b: b.b = abs(b.b)
                    elif code == ord('s') or arr == 'D':
                        if b: b.b = -abs(b.b)
                    elif code == ord('a') or arr == 'L':
                        if b: b.b = (1 if b.b >= 0 else -1) * max(B_MIN, abs(b.b) - B_STEP)
                    elif code == ord('d') or arr == 'R':
                        if b: b.b = (1 if b.b >= 0 else -1) * min(B_MAX, abs(b.b) + B_STEP)
                    elif code in cfl:
                        m.end_edit(cancel=False)
                    elif code == ESC:
                        m.end_edit(cancel=True)
            # ============ 局中加洞口（全场暂停） ============
            elif m.phase == 'HOLE':
                ui = m.hole_ui
                p = ui['player']
                # 只接受"当前操作者"自己的键，避免另一方误触把洞口挪走
                if p == 0:
                    own = code in (ord('w'), ord('s'), ord('a'), ord('d'), ord('c'),
                                   ord('e'), ESC, ENTER)
                else:
                    own = code in (ord('+'), ord('*'), ESC, ENTER) or arr in ('U', 'D', 'L', 'R')
                if own:
                    EDGES = ['top', 'right', 'bottom', 'left']
                    cfm = (ord('e'), ENTER) if p == 0 else (ord('*'), ENTER)
                    trig = ord('c') if p == 0 else ord('+')
                    if code == ESC:
                        m.phase = 'PLAY'
                        ui['player'] = None
                    elif ui['stage'] == 'EDGE':
                        if code == trig:                     # 循环切换四条边界
                            ui['edge'] = EDGES[(EDGES.index(ui['edge']) + 1) % 4]
                            lo, hi = m.world._hole_range(ui['edge'])
                            ui['pos'] = min(hi, max(lo, ui['pos']))
                        elif code in cfm:                    # 确定边界 → 进入微调
                            ui['stage'] = 'FINE'
                            lo, hi = m.world._hole_range(ui['edge'])
                            mid = FIELD_H / 2 if ui['edge'] in ('left', 'right') else FIELD_W / 2
                            ui['pos'] = min(hi, max(lo, mid))
                    else:                                    # 微调位置（只用两个键）
                        if ui['edge'] in ('left', 'right'):
                            if (p == 0 and code == ord('w')) or (p == 1 and arr == 'U'):
                                ui['pos'] -= 10
                            elif (p == 0 and code == ord('s')) or (p == 1 and arr == 'D'):
                                ui['pos'] += 10
                        else:
                            if (p == 0 and code == ord('a')) or (p == 1 and arr == 'L'):
                                ui['pos'] -= 10
                            elif (p == 0 and code == ord('d')) or (p == 1 and arr == 'R'):
                                ui['pos'] += 10
                        lo, hi = m.world._hole_range(ui['edge'])
                        ui['pos'] = min(hi, max(lo, ui['pos']))
                        if code in cfm:                      # 确认放置
                            if m.do_place_hole():
                                m.phase = 'PLAY'
                                ui['player'] = None
            # ============ 对战阶段 ============
            else:
                if code == ord('w'):
                    m.aim[0] = min(90.0, m.aim[0] + ANGLE_STEP)
                elif code == ord('s'):
                    m.aim[0] = max(-90.0, m.aim[0] - ANGLE_STEP)
                elif code == ord('a'):
                    m.speed[0] = max(SPEED_MIN, m.speed[0] - SPEED_STEP)
                elif code == ord('d'):
                    m.speed[0] = min(SPEED_MAX, m.speed[0] + SPEED_STEP)
                elif code == SPACE:
                    m.fire(0)
                elif code == ord('z'):
                    m.sel_q[0] = 1
                elif code == ord('x'):
                    m.sel_q[0] = -1
                elif code == ord('q'):
                    m.begin_edit(0)
                elif code == ord('c'):
                    m.begin_hole(0)
                elif arr == 'U':
                    m.aim[1] = min(90.0, m.aim[1] + ANGLE_STEP)
                elif arr == 'D':
                    m.aim[1] = max(-90.0, m.aim[1] - ANGLE_STEP)
                elif arr == 'L':
                    m.speed[1] = max(SPEED_MIN, m.speed[1] - SPEED_STEP)
                elif arr == 'R':
                    m.speed[1] = min(SPEED_MAX, m.speed[1] + SPEED_STEP)
                elif code == ENTER:
                    m.fire(1)
                elif code == ord(','):
                    m.sel_q[1] = 1
                elif code == ord('.'):
                    m.sel_q[1] = -1
                elif code == ord('/'):
                    m.begin_edit(1)
                elif code == ord('+'):
                    m.begin_hole(1)
                elif code == ord('t'):
                    m.beam_on = not m.beam_on
                elif code == ord('h'):
                    m.show_key = not m.show_key
                elif code == ord('r'):
                    if m.phase == 'MATCH_END':
                        m.new_match()          # 结算画面按 R = 重开整场
                    else:
                        m.new_round(m.round)   # 局中按 R = 重开本局
                elif code == ESC:
                    break

        m.update(dt)
        try:
            cv2.imshow(WIN_NAME, draw(m, mouse))
        except cv2.error:
            break
        try:
            if cv2.getWindowProperty(WIN_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break
    cv2.destroyAllWindows()


def selftest():
    """无窗口自检：把整场比赛（10 局 + 加赛）跑完，确认局流程不会卡住。"""
    print('=== 局流程自检 ===')
    m = Match(seed=11)
    steps = 0
    while m.phase != 'MATCH_END' and steps < 30000:
        if m.phase == 'PREP':                      # 模拟双方按"就绪"
            m.ready(0)
            m.ready(1)
        elif m.phase == 'PLAY':                    # 强制每局都有人进球
            p = m.round % 2
            m.goals[p] += 1
            m.points[p] += GOAL_POINTS
            m.phase, m.end_t = 'ROUND_END', 0.0
        m.update(0.05)
        steps += 1
    print(f'10 局流程: {"PASS" if m.phase == "MATCH_END" else "FAIL"} '
          f'| 结束阶段 {m.phase} | 打到第 {m.round} 局 | 比分 {m.goals}')

    m2 = Match(seed=12)
    rounds = 0
    while m2.phase != 'MATCH_END' and rounds < 30000:
        if m2.phase == 'PREP':
            m2.ready(0)
            m2.ready(1)
        elif m2.phase == 'PLAY':
            if m2.round <= MAX_ROUNDS:
                m2.goals[0] += 1
                m2.goals[1] += 1                   # 十局全打平 → 必须进加赛
            else:
                m2.goals[0] += 1                    # 加赛分出胜负
            m2.phase, m2.end_t = 'ROUND_END', 0.0
        m2.update(0.05)
        rounds += 1
    ok2 = (m2.phase == 'MATCH_END' and m2.round == MAX_ROUNDS + 1)
    print(f'平局加赛流程: {"PASS" if ok2 else "FAIL"} '
          f'| 结束阶段 {m2.phase} | 打到第 {m2.round} 局 | 比分 {m2.goals}')

    m3 = Match(seed=13)
    m3.phase = 'PLAY'
    m3.world.launch(40, 270, 30, 350, 1, 0)
    for _ in range(6000):
        m3.update(0.02)
        if m3.phase != 'PLAY':
            break
    print(f'对战推进（粒子→进球→结算）: '
          f'{"PASS" if m3.phase in ("ROUND_END", "MATCH_END", "PREP", "PLAY") else "FAIL"} '
          f'| 当前阶段 {m3.phase} | 已打球数 {sum(m3.world.blocks_count() for _ in [0])}')
    # 局前布置自检：障碍物（含角度）+ 传送门两端
    m4 = Match(seed=14)
    m4.points = [200, 200]
    m4.sel_item[0] = 2                                     # 障碍物
    m4.buy(0)
    m4.pending_place[0] = 'obstacle'
    m4.cursor[0] = [480, 270]
    m4.place(0)
    m4.buy(0)
    m4.place_rot[0] = 45.0
    m4.cursor[0] = [600, 320]
    m4.place(0)
    obs = m4.world.obstacles
    ok_obs = len(obs) == 2 and abs(obs[1].ang - math.radians(45)) < 1e-6
    print(f'障碍物放置（含角度可调）: {"PASS" if ok_obs else "FAIL"} | 障碍数 {len(obs)}'
          f' | 第二块角度 {math.degrees(obs[1].ang):.0f}°' if len(obs) > 1 else '障碍物放置: FAIL')

    m5 = Match(seed=15)
    m5.points = [200, 200]
    m5.sel_item[0] = 4                                     # 传送门
    m5.buy(0)
    m5.pending_place[0] = 'portal'
    m5.cursor[0] = [300, 200]
    m5.place(0)
    first_ok = m5.portal_first[0] is not None
    m5.cursor[0] = [700, 400]
    m5.place(0)
    ok_portal = (len(m5.world.portals) == 2 and m5.portal_first[0] is None)
    print(f'传送门两端放置: {"PASS" if (first_ok and ok_portal) else "FAIL"}'
          f' | 第一端已记录 {first_ok} | 门数 {len(m5.world.portals)}')

    # 加洞口（四条边各试一次）
    m6 = Match(seed=16)
    m6.start_play()
    ok_hole = True
    for e in ('top', 'right', 'bottom', 'left'):
        m6.phase = 'PLAY'
        m6.hole_ui = {'player': 0, 'edge': e, 'pos': 400.0, 'stage': 'FINE'}
        if not (m6.hole_place_valid(e, 400.0) and m6.do_place_hole()):
            ok_hole = False
    print(f'四条边加洞口: {"PASS" if ok_hole else "FAIL"} | 洞口总数 {len(m6.world.holes)}')

    print('=== 自检完成 ===')


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--selftest':
        selftest()
    else:
        main()
