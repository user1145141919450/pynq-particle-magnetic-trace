# -*- coding: utf-8 -*-
"""match_flow.py — 《磁场粒子对战》**局状态机**（M2.2；规则书 v4 / `game_pc/main.py` 移植）

纯逻辑、零硬件：PC 上直接 `python match_flow.py` 自检；板上由 `ball_game.py --match`
接线（默认关 —— 不带 --match 时 ball_game 行为一个字节都不变）。

## 与 PC 原型（game_pc/main.py 的 Match）的对照

忠实移植的：10 局流程（第 n 局 n 块→封顶 9——块数由调用方建场时落实）、局前(第 2 局起)
→局中→暂停→结算(2.6s)→下一局、平局加赛 1 局、进球 +10 分、弹药每局 3 红(+1) 2 蓝(-1)、
道具经济（free_hole 先用 / 积分扣减 / 加球局中选红蓝 +1）、改磁场/加洞口两种暂停互斥。

★★ **v5 改规则（2026-10-07 用户拍板，覆盖规则书 v4 的"进球即止"）**：
- **进球只得分、不停局**——本局继续打，没进的球继续弹，一局可进多球
  （本局进球记 `round_goals`，结算播报）；
- **本局结束条件 = 双方弹药打光**（各 3红2蓝 全出）+ **5s 落定等待**
  （`GRACE_SECS`；这 5 秒里球照飞、进球照算）→ ROUND_END（2.6s 结算画面）；
- 结算 = 调用方**冻结全部球**（截止运动）；**上一局还在飞的球，下一局原轨迹
  续飞**——它们记着发球人（LiveBall.who），之后进洞仍算原发球人的分。

上板适配（有意偏离，都有标注）：
1. **玩家号 1/2**（PC 是 0/1）——与 ball_game / 手机网页 / shot.json 的 P1/P2 约定一致。
2. **弹药"按实物球颜色充能"**：PC 是"选色→摆球→发射"扣对应色；板上球是实物的，
   发射时按**相机判到的颜色**（= charge_of(cls)）扣。**未知色(0) 不设限、不消耗**——
   判色抖动不该把玩家卡死（和 realtime_ball 对中性球的处理同一条纪律）。
3. **不加 update 里的世界步进**：板上真球由 realtime_ball 自己推进；本模块只管
   局/分/弹药/计时/暂停。
4. **暂停 30s 自动确认**（规则书 §7.1 第 5 条）：PC 原型没有这条，这里补上并进自检。
5. 洞口的几何合法性（范围/重叠）在调用方用世界数据判，本模块只管"额度怎么扣"。

事件机制：任何状态变化产出的"要调用方动手的事"（清场、比分播报、洞口该动了）都进
`self.pending`；调用方在每次交互/每帧 `drain()` 拿走。事件类型：
    ('round_start', n, 'first'|'next'|'tiebreak')   → 调用方清场
    ('match_end', winner)                            → winner = 1/2/None(平)…
    ('goal', p, goals_p1, goals_p2, points_p1, points_p2)
    ('hole_due',)                                    → M2.4 接洞口自适应
    ('edit_timeout', p)                              → 改磁场暂停超 30s 自动确认
    ('ready', p, both)
"""

MAX_ROUNDS = 10
GOAL_POINTS = 10
ROUND_END_DELAY = 2.6          # 结算画面停留（PC 同值）
TIMER_PERIOD = 30.0            # 洞口自适应计时（规则书 §8）
PAUSE_MAX = 30.0               # 单次改磁场暂停上限（规则书 §7.1-5；超时自动确认）
GRACE_SECS = 5.0               # ★ v5（2026-10-07 用户改规则）：双方弹药打光后的
                               #   "落定等待"——这 5 秒里场上球照飞、**进球照算**；
                               #   到点结算并冻结（在飞的球下一局原轨迹续飞）

AMMO0 = {1: 3, -1: 2}          # 每人每局：3 红(正电子) + 2 蓝(负电子)
ITEM_KEYS = ('ball', 'field', 'obstacle', 'hole', 'portal')
# 道具表（照抄 PC 顺序/价格）：key, 价格, 中文名
ITEMS = (('ball', 10, '加球(局中选色 +1)'),
         ('field', 20, '改磁场(一块)'),
         ('obstacle', 10, '障碍物(永久)'),
         ('hole', 30, '加洞口(本局)'),
         ('portal', 30, '传送门(永久)'))
ITEM_SHORT = {'ball': '加球', 'field': '改磁场', 'obstacle': '障碍',
              'hole': '加洞', 'portal': '传送门'}   # 屏上/播报短名（全名照 ITEMS）

EDGES = ('top', 'right', 'bottom', 'left')       # 加洞口切边顺序（规则书 §6.4）


def _who(p):
    return '选手一' if p == 1 else '选手二'


class MatchFlow:
    """一整场对决的运行时状态（局/分/弹药/道具/暂停）。无硬件依赖。"""

    def __init__(self):
        self.round = 1
        self.goals = {1: 0, 2: 0}            # 局胜数（=得分/10）
        self.points = {1: 0, 2: 0}           # 积分（货币）
        self.items = {p: {k: 0 for k in ITEM_KEYS} for p in (1, 2)}
        self.free_hole = {1: 1, 2: 1}        # 每人初始免费加洞 1 次
        self.ammo = {1: dict(AMMO0), 2: dict(AMMO0)}
        self.timer = 0.0                     # 30s 洞口计时（仅 PLAY 累计）
        self.round_goals = {1: 0, 2: 0}      # ★ v5：本局进球数（结算播报用）
        self.grace = None                    # ★ v5：弹药打光后的落定等待计时
                                             #   （None=未开始；PLAY 里累计到 GRACE_SECS）
        self.phase = 'PREP'                  # PREP/PLAY/EDIT/HOLE/ROUND_END/MATCH_END
        self.sub = {1: 'SHOP', 2: 'SHOP'}    # 局前子状态：SHOP/PLACE/READY
        # ★★ 2026-10-28 用户定案：买完实体件 -> 进摆放界面；双方就绪后
        #   若本局没买过障碍/传送门 -> 进「调整」界面，再按开始才开局。
        self.phys_bought = {1: False, 2: False}
        # ★★ 2026-10-29 用户定案（"买了但没摆，就绪也不进调整界面"）：
        #   就绪时看**本局有没有真正摆过**（phys_placed），不是看买没买。
        #   买了没摆（选"先留着"/ESC 取消）-> 仍进调整界面，让玩家 V/G 摆。
        self.phys_placed = {1: False, 2: False}
        self.adjust = False                  # True=双方就绪、等「开始本局」
        self.last_buy = None                 # 最近一次成功购买的道具 key
        self.sel_item = {1: 0, 2: 0}         # 局前选中的道具下标
        self.edit = {'player': None, 'idx': 0, 'stage': 'SELECT', 't': 0.0}
        self.hole = {'player': None, 'edge': 'right', 'pos': 0.0, 'stage': 'EDGE'}
        # ★★ 2026-10-28 用户定案（第 3 条）：对局中临时摆实体件 = **暂停阶段**
        #   （PLACE）——球/计时/结算全冻结，摆完 end_place() 回 PLAY。
        self.place = {'player': None, 't': 0.0}
        self.msg, self.msg_t = '', 0.0
        self.end_t = 0.0
        self.pending = []                    # 待调用方处理的事件（drain 取走）
        self.new_round(1)

    # ---------- 事件 ----------
    def _emit(self, *ev):
        self.pending.append(ev)

    def drain(self):
        ev, self.pending = self.pending, []
        return ev

    # ---------- 状态文本 ----------
    def winner(self):
        if self.goals[1] > self.goals[2]:
            return 1
        if self.goals[2] > self.goals[1]:
            return 2
        return None

    def result_text(self):
        w = self.winner()
        if w is None:
            return '平局'
        return '%s 获胜（%d : %d）' % (_who(w), self.goals[1] * GOAL_POINTS,
                                       self.goals[2] * GOAL_POINTS)

    def status_line(self):
        """给控制台/日志的一行中文状态。"""
        ph = {'PREP': '局前准备', 'PLAY': '对战中', 'EDIT': '暂停·改磁场',
              'HOLE': '暂停·加洞口', 'PLACE': '暂停·摆放实体件',
              'ROUND_END': '本局结束',
              'MATCH_END': '比赛结束'}.get(self.phase, self.phase)
        return ('第 %d/%d 局·%s | 比分 %d:%d | P1 红%d蓝%d P2 红%d蓝%d | %s'
                % (self.round, MAX_ROUNDS, ph,
                   self.goals[1] * GOAL_POINTS, self.goals[2] * GOAL_POINTS,
                   self.ammo[1][1], self.ammo[1][-1],
                   self.ammo[2][1], self.ammo[2][-1], self.msg))

    def hud_line(self):
        """★ 屏上 HUD 一行 —— **只许 ASCII**（cv2.putText 画不出中文，铁律）。"""
        rnd = ('R%d/%d' % (self.round, MAX_ROUNDS) if self.round <= MAX_ROUNDS
               else 'TIEBREAK')
        score = ('P1 %d : %d P2' % (self.goals[1] * GOAL_POINTS,
                                    self.goals[2] * GOAL_POINTS))
        ammo = ('R%d B%d | R%d B%d' % (self.ammo[1][1], self.ammo[1][-1],
                                       self.ammo[2][1], self.ammo[2][-1]))
        if self.phase == 'PLAY':
            # ★ v5：落定等待期间显示 AMMO DONE 倒计时（其余仍是 30s 洞口计时）
            if self.grace is not None:
                tail = ('AMMO DONE - settle in %.0fs'
                        % max(0.0, GRACE_SECS - self.grace))
            else:
                tail = '%ds' % max(0, int(round(TIMER_PERIOD - self.timer)))
        elif self.phase == 'PREP':
            # ★ 2026-10-07 上板实玩发现：局前按发射键=就绪，但玩家看不到"谁已就绪"
            #   -> 以为"按了发射没反应"。HUD 直接摆出双方就绪状态（ASCII）。
            if self.adjust:
                tail = ('ADJUST: P1 READY / P2 READY  '
                        '(E/KP* = START ROUND, ESC = back to shop)')
            else:
                tail = ('PREP: P1 %s / P2 %s  (SPACE/ENTER = ready)'
                        % ('READY' if self.sub[1] == 'READY' else 'wait',
                           'READY' if self.sub[2] == 'READY' else 'wait'))
        elif self.phase == 'ROUND_END':
            tail = 'ROUND END - next in %.1fs' % max(0.0, ROUND_END_DELAY - self.end_t)
        elif self.phase == 'EDIT':
            tail = 'PAUSED: magnet edit by P%d' % self.edit['player']
        elif self.phase == 'HOLE':
            tail = 'PAUSED: add hole by P%d' % self.hole['player']
        elif self.phase == 'PLACE':
            tail = 'PAUSED: item placement by P%d' % (self.place['player'] or 0)
        else:
            tail = 'MATCH END - %s' % ('P1 wins' if self.winner() == 1 else
                                       ('P2 wins' if self.winner() == 2 else 'tie'))
        return '%s  %s  | ammo %s |  %s' % (rnd, score, ammo, tail)

    # ---------- 局流程 ----------
    def new_round(self, rnd):
        self.round = rnd
        self.ammo = {1: dict(AMMO0), 2: dict(AMMO0)}
        self.timer = 0.0
        self.round_goals = {1: 0, 2: 0}      # ★ v5：每局重置本局进球账
        self.grace = None                    # ★ v5：落定等待每局重置
        self.phase = 'PREP'
        self.sub = {1: 'SHOP', 2: 'SHOP'}
        self.phys_bought = {1: False, 2: False}
        self.phys_placed = {1: False, 2: False}
        self.adjust = False
        self.last_buy = None
        self.sel_item = {1: 0, 2: 0}
        self.edit = {'player': None, 'idx': 0, 'stage': 'SELECT', 't': 0.0}
        self.hole = {'player': None, 'edge': 'right', 'pos': 0.0, 'stage': 'EDGE'}
        self.place = {'player': None, 't': 0.0}
        if rnd == 1:
            # 第 1 局双方 0 积分：没得可买可布置 → 直接开打（PC 同）
            self.phase = 'PLAY'
            self.msg, self.msg_t = '第 1 局开始！（无积分，无需局前准备）', 3.0
            self._emit('round_start', 1, 'first')
        else:
            why = '加赛' if rnd > MAX_ROUNDS else '下一局'
            self.msg = ('第 %d 局：局前准备（买道具 → 布置 → 双方就绪）'
                        % rnd if rnd <= MAX_ROUNDS else '加赛局：局前准备')
            self.msg_t = 4.0
            self._emit('round_start', rnd, 'tiebreak' if rnd > MAX_ROUNDS else 'next')

    def start_play(self):
        # ★ 2026-10-25 用户改口径：加球**不再开局自动转换**——道具保留，
        #   对局中按 F/小键盘8 并在串口屏选红/蓝，选完才扣 1 个并 +1 对应弹药。
        #   （原规则书 §6.2.1 的"开局 +1红+1蓝"作废；由 ball_game._aball_finish
        #     在 PLAY 里即时生效。）
        self.phase = 'PLAY'
        self.adjust = False
        self.timer = 0.0
        self.msg, self.msg_t = '本局开始！', 2.0

    def begin_play(self):
        """★ 2026-10-28：「调整障碍/传送门」界面里的**开始本局**。

        只有双方都已就绪（PREP + READY/READY）才允许。返回 (ok, msg)。"""
        if self.phase != 'PREP':
            return False, '不在局前'
        if not all(self.sub[q] == 'READY' for q in (1, 2)):
            return False, '还有一方没就绪'
        self.start_play()
        return True, '本局开始！'

    def back_to_shop(self):
        """调整界面里按 ESC -> 双方回到商店（可继续购买/调整）。"""
        if self.phase != 'PREP':
            return False
        self.adjust = False
        self.sub = {1: 'SHOP', 2: 'SHOP'}
        self.msg, self.msg_t = '回到商店（继续购买/调整）', 2.0
        return True

    def shop_label(self, p):
        """★ 2026-10-08 道具系统：局前选购当前项的短标签（屏/日志/手机共用）。"""
        _key, _price, _name = ITEMS[self.sel_item[p]]
        return '%s %d分' % (ITEM_SHORT.get(_key, _key), _price)

    def shop_move(self, p, d):
        """局前选购：选单上(-1)/下(+1)挪一格（循环）。返回新标签；不在选购态 None。"""
        if self.phase != 'PREP' or self.sub[p] != 'SHOP':
            return None
        self.sel_item[p] = (self.sel_item[p] + int(d)) % len(ITEMS)
        return self.shop_label(p)

    def ready(self, p):
        """局前就绪；双方都按 -> 开局。

        ★★ 2026-10-28 用户定案：**本局没买过障碍/传送门**时，双方就绪不直接
          开打——先进「调整障碍/传送门」界面（`adjust=True`），按「开始本局」
          才 `begin_play()`。买过的（已走过摆放界面）则照旧直接开局。"""
        if self.phase != 'PREP':
            return False
        self.sub[p] = 'READY'
        both = all(self.sub[q] == 'READY' for q in (1, 2))
        self._emit('ready', p, both)
        self.msg, self.msg_t = ('%s 已就绪' % _who(p)), 1.5
        if both:
            if self.phys_placed[1] or self.phys_placed[2]:
                self.start_play()
            else:
                self.adjust = True
                self.msg, self.msg_t = ('双方就绪——调整障碍/传送门后'
                                        '按 E / 小键盘* 开始本局'), 5.0
        return True

    def mark_placed(self, p):
        """本局**真正摆过**实体件（摆放确认那一刻由 ball_game 调）。"""
        if p in (1, 2):
            self.phys_placed[int(p)] = True

    def buy(self, p):
        """买当前选中的道具（免费加洞口优先用掉）。返回 (ok, msg)。"""
        if self.phase != 'PREP' or self.sub[p] != 'SHOP':
            return False, '不在购买阶段'
        key, price, name = ITEMS[self.sel_item[p]]
        self.last_buy = None
        if key == 'hole' and self.free_hole[p] > 0:
            self.free_hole[p] -= 1
            self.items[p]['hole'] += 1
            self.last_buy = key
            self.msg, self.msg_t = '%s 使用免费加洞口' % _who(p), 2.0
            return True, self.msg
        if self.points[p] < price:
            self.msg, self.msg_t = '%s 积分不足（%s 要 %d 分）' % (_who(p), name, price), 1.8
            return False, self.msg
        self.points[p] -= price
        self.items[p][key] += 1
        self.last_buy = key
        if key in ('obstacle', 'portal'):
            self.phys_bought[p] = True       # 本局买过实体件 -> 就绪后直接开局
        self.msg, self.msg_t = '%s 购买 %s' % (_who(p), name), 1.5
        return True, self.msg

    # ---------- 对战：弹药 / 进球 ----------
    def can_fire(self, p, charge):
        """按实物球颜色查弹药。返回 (ok, why)：why ∈ paused/round_end/no_ammo。

        charge = +1 红 / -1 蓝 / 0 未知色。★ 未知色不设限、不消耗（防判色抖动卡死
        玩家；与 realtime_ball 对中性球的弹性处理同一条纪律）。
        """
        if self.phase in ('EDIT', 'HOLE', 'PLACE'):
            return False, 'paused'
        if self.phase == 'ROUND_END':
            return False, 'round_end'
        if self.phase != 'PLAY':
            return False, 'round_end'
        if charge == 0:
            return True, ''
        if self.ammo[p][charge] <= 0:
            return False, 'no_ammo'
        return True, ''

    def consume(self, p, charge):
        if charge in (1, -1) and self.ammo[p][charge] > 0:
            self.ammo[p][charge] -= 1

    def on_goal(self, p):
        """真球进洞（调用方在 realtime_ball 的 goal 事件里调）。

        ★★ v5（2026-10-07 用户改规则）：**进球只得分、不停局**——本局继续打，
        没进的球继续弹；一局可以进多球（本局进球记在 `round_goals`，结算时播报）。
        """
        if self.phase != 'PLAY' or p not in (1, 2):
            return False
        self.goals[p] += 1
        self.round_goals[p] += 1
        self.points[p] += GOAL_POINTS
        self.msg = '%s 进球！+%d 分（本局继续，总比分 %d : %d；本局 %d : %d）' % (
            _who(p), GOAL_POINTS, self.goals[1] * GOAL_POINTS,
            self.goals[2] * GOAL_POINTS, self.round_goals[1], self.round_goals[2])
        self.msg_t = 3.0
        self._emit('goal', p, self.goals[1], self.goals[2],
                   self.points[1], self.points[2])
        return True

    def ammo_empty(self):
        """★ v5：本局弹药是否已打光（双方 3红2蓝 全出）。"""
        return all(self.ammo[p][1] <= 0 and self.ammo[p][-1] <= 0 for p in (1, 2))

    # ---------- 暂停（改磁场 / 加洞口，两者互斥）----------
    def busy_msg(self, who_wants):
        """规则书 §7：同一时刻只允许一项暂停类操作。返回 True=被占用（msg 已写）。"""
        if self.phase not in ('EDIT', 'HOLE', 'PLACE'):
            return False
        owner = (self.edit['player'] if self.phase == 'EDIT'
                 else self.hole['player'] if self.phase == 'HOLE'
                 else self.place['player'])
        what = {'EDIT': '改磁场', 'HOLE': '加洞口',
                'PLACE': '摆放实体件'}[self.phase]
        self.msg = ('%s 正在进行『%s』，全场已暂停——请等对方完成后你再来'
                    % (_who(owner), what))
        self.msg_t = 2.5
        return True

    def begin_place(self, p):
        """对局中临时摆实体件：进入 PLACE 暂停阶段（球/计时全冻结）。"""
        if self.phase != 'PLAY':
            return False, '不在对战中'
        if self.busy_msg(p):
            return False, self.msg
        self.phase = 'PLACE'
        self.place = {'player': p, 't': 0.0}
        self.msg, self.msg_t = '%s 暂停·摆放实体件（全场冻结）' % _who(p), 2.0
        return True, self.msg

    def end_place(self):
        """摆放结束（确认/取消/超时都走它）——回到对局。"""
        if self.phase != 'PLACE':
            return False
        self.phase = 'PLAY'
        self.place['player'] = None
        self.msg, self.msg_t = '摆放结束，比赛继续', 1.5
        return True

    def begin_edit(self, p):
        """发起改磁场：需要 field 道具或 ≥20 分。返回 (ok, msg)。"""
        if self.busy_msg(p):
            return False, self.msg
        if self.phase != 'PLAY':
            return False, '不在对战中'
        if self.items[p]['field'] <= 0 and self.points[p] < 20:
            self.msg, self.msg_t = '没有"改磁场"道具（20 分/次）', 1.8
            return False, self.msg
        self.phase = 'EDIT'
        # ★★ 2026-10-29 用户定案：先选块（A/D 或鼠标）→ 再定方向（W/S）→
        #   弹输入框输数值 → 确认。P1/P2 键位不同，先来后到（谁发起谁操作）。
        self.edit = {'player': p, 'idx': 0, 'stage': 'SELECT', 't': 0.0,
                     'sign': 1, 'txt': ''}
        self.msg, self.msg_t = '%s 暂停·改磁场（全场冻结）' % _who(p), 2.0
        return True, self.msg

    def edit_next_block(self, n_blocks, d=1):
        """SELECT 阶段：切块（P1 A/D、P2 ←/→；鼠标点块走 edit_pick_block）。"""
        if self.phase != 'EDIT' or self.edit['stage'] != 'SELECT':
            return None
        n = max(1, int(n_blocks))
        self.edit['idx'] = (int(self.edit['idx']) + int(d)) % n
        return self.edit['idx']

    def edit_pick_block(self, idx, n_blocks):
        if self.phase != 'EDIT' or self.edit['stage'] != 'SELECT':
            return None
        n = max(1, int(n_blocks))
        try:
            self.edit['idx'] = max(0, min(n - 1, int(idx)))
        except Exception:
            return None
        return self.edit['idx']

    def edit_confirm_stage(self):
        """★ 2026-10-29 用户定案：三步都要确认——SELECT -> DIR -> VALUE。

        SELECT 确认 = 选好块（此后方向键才生效）；DIR 确认 = 定好方向（此后才能
        输数值）；VALUE 确认 = 返回 'CONFIRM'（调用方落值+结束）。
        返回新阶段 / 'CONFIRM' / None（不在 EDIT）。"""
        if self.phase != 'EDIT':
            return None
        _st = str(self.edit.get('stage') or 'SELECT')
        if _st == 'SELECT':
            self.edit['stage'] = 'DIR'
        elif _st == 'DIR':
            self.edit['stage'] = 'VALUE'
            self.edit['txt'] = ''
        elif _st == 'VALUE':
            return 'CONFIRM'
        return self.edit['stage']

    def edit_set_sign(self, sign):
        """DIR 阶段：定方向（P1 W/S、P2 ↑/↓）——**选块确认之后才生效**。"""
        if self.phase != 'EDIT' or self.edit['stage'] != 'DIR':
            return None
        self.edit['sign'] = 1 if int(sign) >= 0 else -1
        return self.edit['sign']

    def edit_back(self):
        """ESC 退一步：VALUE -> DIR（清输入）-> SELECT -> None（调用方取消）。"""
        if self.phase != 'EDIT':
            return None
        _st = str(self.edit.get('stage') or 'SELECT')
        if _st == 'VALUE':
            self.edit['stage'] = 'DIR'
            self.edit['txt'] = ''
            return 'DIR'
        if _st == 'DIR':
            self.edit['stage'] = 'SELECT'
            return 'SELECT'
        return None

    def edit_type(self, ch):
        """VALUE 阶段：数字/小数点追加（最多 6 字符、至多一个小数点）。"""
        if self.phase != 'EDIT' or self.edit['stage'] != 'VALUE':
            return None
        ch = str(ch)
        if ch not in '0123456789.' or len(ch) != 1:
            return None
        _txt = str(self.edit.get('txt') or '')
        if ch == '.' and '.' in _txt:
            return None
        if len(_txt) >= 6:
            return None
        self.edit['txt'] = _txt + ch
        return self.edit['txt']

    def edit_backspace(self):
        if self.phase != 'EDIT' or self.edit['stage'] != 'VALUE':
            return None
        self.edit['txt'] = str(self.edit.get('txt') or '')[:-1]
        return self.edit['txt']

    def edit_to_adjust(self):
        if self.phase == 'EDIT' and self.edit['stage'] == 'SELECT':
            self.edit['stage'] = 'ADJUST'
            return True
        return False

    def end_edit(self, confirm=True):
        """结束改磁场。confirm=True 扣额度（item 优先、否则 20 分）；取消不动。"""
        if self.phase != 'EDIT':
            return False
        p = self.edit['player']
        if confirm:
            if self.items[p]['field'] > 0:
                self.items[p]['field'] -= 1
            elif self.points[p] >= 20:
                self.points[p] -= 20
            self.msg, self.msg_t = '磁场调整完成，比赛继续', 1.5
        else:
            self.msg, self.msg_t = '已取消磁场调整', 1.5
        self.phase = 'PLAY'
        self.edit['player'] = None
        return True

    def begin_hole(self, p):
        """发起加洞口（规则书 §6.4）：免费额度 / 道具 / ≥30 分。返回 (ok, msg)。"""
        if self.busy_msg(p):
            return False, self.msg
        if self.phase != 'PLAY':
            return False, '不在对战中'
        if self.free_hole[p] <= 0 and self.items[p]['hole'] <= 0 and self.points[p] < 30:
            self.msg, self.msg_t = '没有"加洞口"额度（30 分/次）', 1.8
            return False, self.msg
        self.phase = 'HOLE'
        # 默认边 = 对方那侧（自己打的靶），与 PC 同
        self.hole = {'player': p, 'edge': 'right' if p == 1 else 'left',
                     'pos': 0.0, 'stage': 'EDGE', 't': 0.0}
        self.msg, self.msg_t = '%s 暂停·加洞口（全场冻结）' % _who(p), 2.0
        return True, self.msg

    def hole_cycle_edge(self):
        if self.phase != 'HOLE':
            return None
        EDGE_ORDER = ('top', 'right', 'bottom', 'left')
        self.hole['edge'] = EDGE_ORDER[(EDGE_ORDER.index(self.hole['edge']) + 1) % 4]
        return self.hole['edge']

    def hole_to_fine(self, pos):
        """确定边界 → 微调阶段（pos 由调用方按该边的合法范围夹好）。"""
        if self.phase != 'HOLE' or self.hole['stage'] != 'EDGE':
            return False
        self.hole['stage'] = 'FINE'
        self.hole['pos'] = float(pos)
        return True

    def hole_set_pos(self, edge, pos):
        """★ 2026-10-15（加洞口 v2·鼠标跟随）：直接把边+位置设到鼠标所指处。

        与 hole_to_fine 的区别：边**也跟着动**（最近边语义），且从 EDGE 自动进
        FINE——鼠标一旦接管，之后每帧都由调用方用本方法刷新（合法性判定仍在
        调用方 _hole_valid，本模块只管状态）。
        """
        if self.phase != 'HOLE':
            return False
        self.hole['edge'] = edge
        self.hole['pos'] = float(pos)
        self.hole['stage'] = 'FINE'
        return True

    def hole_adjust(self, dpos, lo, hi):
        """微调 10px/按（lo/hi 由调用方按该边算）。"""
        if self.phase != 'HOLE' or self.hole['stage'] != 'FINE':
            return None
        self.hole['pos'] = min(hi, max(lo, self.hole['pos'] + dpos))
        return self.hole['pos']

    def hole_place(self, valid):
        """确认放置。valid = 调用方按世界几何判的合法性（太靠角/重叠 = False）。"""
        if self.phase != 'HOLE' or self.hole['stage'] != 'FINE':
            return False
        if not valid:
            self.msg, self.msg_t = '这个位置放不下（太靠角或与已有洞口重叠）', 2.0
            return False
        p = self.hole['player']
        if self.free_hole[p] > 0:
            self.free_hole[p] -= 1
        elif self.items[p]['hole'] > 0:
            self.items[p]['hole'] -= 1
        else:
            self.points[p] -= 30
        self.msg, self.msg_t = '%s 加了一个洞口' % _who(p), 2.0
        self.phase = 'PLAY'
        self.hole['player'] = None
        return True

    def end_hole(self, cancel=True):
        """取消加洞口（规则书 §6.4：ESC 取消）。放置成功走 hole_place；取消不扣额度。"""
        if self.phase != 'HOLE':
            return False
        self.msg, self.msg_t = '已取消加洞口', 1.5
        self.phase = 'PLAY'
        self.hole['player'] = None
        return True

    # ---------- 每帧推进 ----------
    def update(self, dt):
        """推进计时与阶段；产出的事件进 pending（调用方 drain）。"""
        if dt <= 0:
            return
        self.msg_t = max(0.0, self.msg_t - dt)

        if self.phase == 'ROUND_END':
            self.end_t += dt
            if self.end_t > ROUND_END_DELAY:
                if self.round >= MAX_ROUNDS:
                    if self.goals[1] == self.goals[2]:
                        self.new_round(MAX_ROUNDS + 1)      # 平局 → 加赛一局
                        self.msg, self.msg_t = '十局战平 → 加赛一局定胜负！', 4.0
                    else:
                        self.phase = 'MATCH_END'
                        self.msg = '比赛结束：' + self.result_text()
                        self.msg_t = 999.0
                        self._emit('match_end', self.winner())
                else:
                    self.new_round(self.round + 1)
            return

        if self.phase == 'EDIT':
            # 规则书 §7.1-5：单次暂停最长 30s，超时自动确认并恢复（修改保留）。
            # ★ 2026-10-29 三步确认：只有 VALUE（已输数值）超时才算"确认"；
            #   SELECT/DIR 还没改任何东西 -> 取消、不扣额度。
            self.edit['t'] += dt
            if self.edit['t'] >= PAUSE_MAX:
                p = self.edit['player']
                _done = (str(self.edit.get('stage') or 'SELECT') == 'VALUE')
                self.end_edit(confirm=_done)
                self._emit('edit_timeout', p, _done)
            return

        if self.phase == 'HOLE':
            # ★ 2026-10-14（规则书 §7：暂停类操作单次最长 30s）：HOLE 的超时**交给
            #   调用方定夺**——它持有世界几何：能放就 hole_place(True)，放不下就
            #   end_hole(True) 取消。这里每帧递增、到点发事件（调用方 drain 后本帧
            #   内就会改 phase，不会重复发）。
            self.hole['t'] = self.hole.get('t', 0.0) + dt
            if self.hole['t'] >= PAUSE_MAX:
                self._emit('hole_timeout', self.hole['player'])
            return

        if self.phase == 'PLACE':
            # ★ 2026-10-28：对局中摆放实体件也是暂停类操作（30s 上限）；
            #   超时只发事件，由调用方取消布置（额度不扣）。
            self.place['t'] = self.place.get('t', 0.0) + dt
            if self.place['t'] >= PAUSE_MAX:
                self._emit('place_timeout', self.place['player'])
            return

        if self.phase != 'PLAY':
            return                              # PREP/MATCH_END：计时不动

        # ★★ v5（2026-10-07 用户改规则）：**本局结束条件 = 双方弹药打光**。
        #   打光后给 GRACE_SECS 秒"落定等待"——这 5 秒里场上球照飞、进球照算
        #   （由调用方继续推 physics）；到点 -> ROUND_END（调用方冻结全部球，
        #   在飞的球下一局原轨迹续飞）。grace 只触发一次（None -> 计时中）。
        if self.grace is None:
            if self.ammo_empty():
                self.grace = 0.0
                self.msg, self.msg_t = (
                    '双方弹药打光——等场上球落定（%.0fs，期间进球照算）' % GRACE_SECS,
                    GRACE_SECS)
                self._emit('ammo_done')
        else:
            self.grace += dt
            if self.grace >= GRACE_SECS:
                self.phase = 'ROUND_END'
                self.end_t = 0.0
                self.msg = ('本局结束（弹药打光）：本局 P1 进 %d / P2 进 %d；'
                            '总比分 %d : %d' % (
                                self.round_goals[1], self.round_goals[2],
                                self.goals[1] * GOAL_POINTS,
                                self.goals[2] * GOAL_POINTS))
                self.msg_t = 999.0
                self._emit('round_over')
                return

        self.timer += dt
        if self.timer >= TIMER_PERIOD:
            self.timer = 0.0
            self._emit('hole_due')              # M2.4 接洞口自适应


# ============================================================================
#  自检（PC 直接跑：python match_flow.py）
# ============================================================================

def _selftest():
    n_ok = n_bad = 0

    def ck(name, cond, detail=''):
        nonlocal n_ok, n_bad
        if cond:
            n_ok += 1
            print('   [OK] %s%s' % (name, ('  -- ' + str(detail)) if detail else ''))
        else:
            n_bad += 1
            print('   [FAIL] %s%s' % (name, ('  -- ' + str(detail)) if detail else ''))

    def start_round(m):
        """★ 2026-10-28：双方就绪 -> 若进「调整」界面再按开始本局（测试用）。"""
        m.ready(1)
        m.ready(2)
        if m.phase == 'PREP' and m.adjust:
            m.begin_play()
        return m

    def end_round(m):
        """★ v5：打光双方弹药 -> 落定 5s -> 结算 2.6s -> 下一局；返回这一串事件。"""
        m.ammo[1] = {1: 0, -1: 0}
        m.ammo[2] = {1: 0, -1: 0}
        m.update(0.01)                        # 检测到打光 -> grace 起表
        m.update(GRACE_SECS + 0.01)           # 落定窗口走完 -> ROUND_END
        m.update(ROUND_END_DELAY + 0.01)      # 结算走完 -> 下一局
        return m.drain()

    print('== 1) 十局流程跑到 MATCH_END ==')
    m = MatchFlow()
    ev = m.drain()
    ck('第 1 局直接 PLAY（无需局前）', m.phase == 'PLAY' and m.round == 1)
    ck('开局产出 round_start(first) 事件',
       bool(ev) and ev[0][0] == 'round_start' and ev[0][2] == 'first')
    ev_all = []
    for r in range(MAX_ROUNDS):
        if m.phase == 'PREP':                   # 第 2 局起要先就绪
            start_round(m)
        m.on_goal(1)                            # v5：进球不停局，顺手进一球
        ev_all += end_round(m)                  # 打光弹药才完局
    ck('10 局后进入 MATCH_END', m.phase == 'MATCH_END' and m.round == MAX_ROUNDS)
    ck('P1 十球、比分 100:0', m.goals[1] == 10 and m.points[1] == 100
       and m.goals[2] == 0)
    ck('winner=1', m.winner() == 1)
    ck('match_end 事件带 winner', any(e[0] == 'match_end' and e[1] == 1
                                      for e in ev_all))

    print('== 2) 平局加赛 ==')
    m2 = MatchFlow()
    m2.drain()
    for r in range(MAX_ROUNDS):
        if m2.phase == 'PREP':
            start_round(m2)
        m2.on_goal(1 if r % 2 == 0 else 2)      # 5:5 收场 → 必须进加赛
        end_round(m2)
    ck('十局全平 → 进加赛（round 11, PREP→等就绪）', m2.round == MAX_ROUNDS + 1
       and m2.phase == 'PREP' and m2.goals[1] == m2.goals[2])
    m2.ready(1)
    ck('单人 ready 不开局', m2.phase == 'PREP')
    m2.ready(2)
    ck('双人 ready -> 调整界面（本局无实体购买）', m2.phase == 'PREP'
       and m2.adjust)
    m2.begin_play()
    ck('调整后「开始本局」-> PLAY', m2.phase == 'PLAY' and not m2.adjust)
    m2.on_goal(2)
    end_round(m2)
    ck('加赛分出胜负 → MATCH_END、winner=2', m2.phase == 'MATCH_END'
       and m2.winner() == 2)

    print('== 3) ★v5 规则：进球不停局 / 弹药打光+5s 才结算 / 下局弹药重置 ==')
    m3 = MatchFlow()
    m3.drain()
    m3.consume(1, 1)
    ck('扣红球弹药 3→2', m3.ammo[1][1] == 2)
    m3.on_goal(1)
    ck('进球 +10、本局继续（phase 仍是 PLAY）', m3.phase == 'PLAY'
       and m3.points[1] == 10 and m3.round_goals[1] == 1)
    m3.on_goal(1)
    m3.on_goal(2)
    ck('一局可进多球（本局 P1 2 / P2 1；总 20:10）',
       m3.round_goals == {1: 2, 2: 1} and m3.goals[1] == 2 and m3.goals[2] == 1)
    m3.update(1.0)
    ck('弹药没打光就不结算（update 后仍是 PLAY）', m3.phase == 'PLAY'
       and m3.grace is None)
    m3.ammo[1] = {1: 0, -1: 0}
    m3.ammo[2] = {1: 0, -1: 0}
    m3.update(0.01)
    ck('打光 -> ammo_done 事件 + 落定计时开表（仍 PLAY）',
       any(e[0] == 'ammo_done' for e in m3.drain()) and m3.grace is not None
       and m3.phase == 'PLAY')
    m3.update(GRACE_SECS - 0.2)
    ck('落定未满 5s 仍在 PLAY（球照飞、进球照算）', m3.phase == 'PLAY')
    m3.update(0.3)
    ck('5s 到点 -> ROUND_END（round_over 事件）',
       m3.phase == 'ROUND_END' and any(e[0] == 'round_over' for e in m3.drain()))
    m3.update(ROUND_END_DELAY + 0.01)
    ck('结算 2.6s -> 第 2 局 PREP、弹药重置 3红2蓝',
       m3.round == 2 and m3.phase == 'PREP'
       and m3.ammo[1][1] == 3 and m3.ammo[1][-1] == 2)

    print('== 4) 弹药口径（按实物球颜色）==')
    m4 = MatchFlow()
    m4.drain()
    ok, why = m4.can_fire(1, -1)
    ck('蓝球可发（2 颗）', ok)
    m4.consume(1, -1)
    m4.consume(1, -1)
    ok, why = m4.can_fire(1, -1)
    ck('蓝球扣完 → no_ammo', (not ok) and why == 'no_ammo')
    ok, _ = m4.can_fire(1, 1)
    ck('红球不受影响（还剩 3）', ok and m4.ammo[1][1] == 3)
    ok, _ = m4.can_fire(1, 0)
    ck('未知色不设限（charge=0）', ok)
    m4.consume(1, 0)
    ck('未知色不消耗弹药', m4.ammo[1][1] == 3 and m4.ammo[1][-1] == 0)
    ok, why = m4.can_fire(2, 1)
    ck('P2 弹药独立（红 3）', ok)

    print('== 5) 加球道具：买后保留，局中选色才 +1 ==')
    m5 = MatchFlow()
    m5.drain()
    m5.on_goal(1)
    end_round(m5)                                 # 进第 2 局局前（才能购买）
    m5.points[1] = 25
    m5.sel_item[1] = 0                            # 加球
    ok, _ = m5.buy(1)
    ck('买加球成功（-10 分）', ok and m5.points[1] == 15 and m5.items[1]['ball'] == 1)
    start_round(m5)
    ck('开局后加球道具保留（不再自动加弹药）',
       m5.ammo[1][1] == 3 and m5.ammo[1][-1] == 2
       and m5.items[1]['ball'] == 1)

    print('== 6) 购买 / 免费加洞口 ==')
    m6 = MatchFlow()
    m6.drain()
    m6.on_goal(1)
    end_round(m6)                                 # 进第 2 局局前
    m6.sel_item[2] = 3                            # 加洞口
    ok, _ = m6.buy(2)
    ck('免费加洞口先用掉（不扣分）', ok and m6.free_hole[2] == 0
       and m6.items[2]['hole'] == 1 and m6.points[2] == 0)
    ok, _ = m6.buy(2)
    ck('积分不够 30 → 拒绝', (not ok) and m6.points[2] == 0)
    m6.points[2] = 30
    ok, _ = m6.buy(2)
    ck('30 分买成（-30）', ok and m6.points[2] == 0 and m6.items[2]['hole'] == 2)

    print('== 7) 暂停互斥 + 改磁场额度 ==')
    m7 = MatchFlow()
    m7.drain()
    m7.points[1] = 50
    ok, _ = m7.begin_edit(1)
    ck('P1 发起改磁场 → phase EDIT', ok and m7.phase == 'EDIT')
    ok2, msg2 = m7.begin_hole(2)
    ck('P2 同时发起加洞口 → 被互斥拒绝（提示对方在改磁场）',
       (not ok2) and ('改磁场' in m7.msg) and m7.phase == 'EDIT')
    m7.end_edit(confirm=True)
    ck('确认结束 → 扣 20 分、回 PLAY', m7.points[1] == 30 and m7.phase == 'PLAY')
    ok, _ = m7.begin_edit(1)
    m7.end_edit(confirm=False)
    ck('取消 → 不扣分', m7.points[1] == 30 and m7.phase == 'PLAY')

    print('== 8) 30s 计时（暂停冻结）==')
    m8 = MatchFlow()
    m8.drain()
    m8.update(29.5)
    ck('29.5s 无 hole_due', not any(e[0] == 'hole_due' for e in m8.pending))
    m8.update(0.6)
    ck('30s 到点 → hole_due 且计时清零',
       any(e[0] == 'hole_due' for e in m8.pending) and m8.timer == 0.0)
    m8.drain()
    m8.points[1] = 50
    m8.begin_edit(1)
    m8.update(35.0)
    ck('暂停中计时冻结、并触发 30s 自动确认（规则书 §7.1-5）',
       any(e[0] == 'edit_timeout' for e in m8.pending)
       and m8.phase == 'PLAY' and m8.timer == 0.0)

    print('== 9) 加洞口四边/顺序 ==')
    m9 = MatchFlow()
    m9.drain()
    ok, _ = m9.begin_hole(1)
    ck('P1 发起加洞口（免费额度在）→ HOLE 默认 right 边',
       ok and m9.phase == 'HOLE' and m9.hole['edge'] == 'right')
    seq = [m9.hole_cycle_edge() for _ in range(4)]
    ck('连按切边 上→右→下→左 循环', seq == ['bottom', 'left', 'top', 'right'])
    m9.hole_to_fine(400.0)
    ck('定边 → 微调阶段', m9.hole['stage'] == 'FINE')
    m9.hole_adjust(-10, 100, 500)
    ck('微调 -10px', abs(m9.hole['pos'] - 390.0) < 1e-9)
    ok = m9.hole_place(True)
    ck('放置成功 → 免费额度 1→0、回 PLAY', ok and m9.free_hole[1] == 0
       and m9.phase == 'PLAY')
    m9.points[1] = 30                             # 免费额度已用光，改拿 30 分买
    ok, _ = m9.begin_hole(1)
    ck('第二次靠 30 分额度进 HOLE', ok and m9.phase == 'HOLE')
    m9.hole_to_fine(400.0)
    ok = m9.hole_place(False)
    ck('非法位置 → 拒绝且不扣分', (not ok) and m9.points[1] == 30
       and m9.items[1]['hole'] == 0)

    print('== 9b) ★ 加洞口 v2：鼠标跟随直接设边+位置（hole_set_pos）==')
    m9b = MatchFlow()
    m9b.drain()
    ok, _ = m9b.begin_hole(1)
    ck('发起后默认 EDGE/right（等鼠标接管）',
       ok and m9b.hole['stage'] == 'EDGE' and m9b.hole['edge'] == 'right')
    r = m9b.hole_set_pos('top', 480.0)
    ck('hole_set_pos：EDGE 自动进 FINE、边/位置一次到位',
       r is True and m9b.hole['stage'] == 'FINE'
       and m9b.hole['edge'] == 'top' and m9b.hole['pos'] == 480.0)
    r = m9b.hole_set_pos('left', 123.0)
    ck('连跟第二笔：FINE 里继续换边/挪位（鼠标滑动语义）',
       r is True and m9b.hole['edge'] == 'left' and m9b.hole['pos'] == 123.0)
    ok = m9b.hole_place(True)                    # 合法由调用方保证；这里只验扣额
    ck('放好后 hole_set_pos 变 no-op（已回 PLAY）',
       ok and m9b.hole_set_pos('top', 480.0) is False)
    m9b.points[1] = 30                           # 免费额度已用光，改拿 30 分买
    ok, _ = m9b.begin_hole(1)
    ck('再发起（30 分额度）', ok and m9b.phase == 'HOLE')
    ck('未发起（PLAY）时 hole_set_pos 是 False',
       MatchFlow().hole_set_pos('top', 480.0) is False)

    print('== 10) 局前就绪事件流 ==')
    m10 = MatchFlow()
    m10.drain()
    m10.on_goal(1)
    ev = m10.drain()
    ck('goal 事件带比分', any(e[0] == 'goal' and e[2] == 1 for e in ev))
    ev = end_round(m10)
    ck('下一局 round_start 事件（调用方据此"续局+剔旧"）',
       any(e[0] == 'round_start' and e[1] == 2 and e[2] == 'next' for e in ev))
    m10.ready(1)
    ev = m10.drain()
    ck('ready 事件 both=False', ev and ev[0][0] == 'ready' and ev[0][2] is False)
    m10.ready(2)
    ev = m10.drain()
    ck('双 ready 事件 both=True（本局无实体购买 -> 调整界面）',
       ev[0][2] is True and m10.phase == 'PREP' and m10.adjust)
    m10.begin_play()
    ck('「开始本局」-> PLAY', m10.phase == 'PLAY')

    print('== 11) ★ 局前选购（道具系统 v1，规则书 §6.2）==')
    m11 = MatchFlow()
    m11.drain()
    m11.on_goal(1)
    end_round(m11)                       # -> 第 2 局 PREP/SHOP
    ck('第 2 局局前在 SHOP 子态', m11.phase == 'PREP' and m11.sub[1] == 'SHOP')
    ck('shop_label 初始 = 加球 10分', m11.shop_label(1) == '加球 10分',
       m11.shop_label(1))
    _lb = m11.shop_move(1, 1)
    ck('shop_move 下移 -> 改磁场 20分', _lb == '改磁场 20分', str(_lb))
    _lb = m11.shop_move(1, -1)
    ck('上移 -> 加球 10分', _lb == '加球 10分', str(_lb))
    _lb = m11.shop_move(1, -1)
    ck('上移循环到末项 -> 传送门 30分', _lb == '传送门 30分', str(_lb))
    _lb = m11.shop_move(1, 1)
    ck('下移回绕到首项 -> 加球 10分', _lb == '加球 10分', str(_lb))
    _ok11, _ = m11.buy(1)                # 10 分（上一局进了一球）买加球
    ck('买加球成功（-10 分、计数 +1）', _ok11 and m11.points[1] == 0
       and m11.items[1]['ball'] == 1)
    m11.ready(1)
    ck('就绪后选单锁死（shop_move=None）且购买被拒',
       m11.shop_move(1, 1) is None and (not m11.buy(1)[0]))
    m11.ready(2)
    m11.begin_play()
    ck('开局后加球道具保留（局中 F + 选色才生效）', m11.phase == 'PLAY'
       and m11.ammo[1][1] == 3 and m11.ammo[1][-1] == 2
       and m11.items[1]['ball'] == 1)

    print('== 12) ★ 局中道具：改磁场/HOLE 的取消与 30s 超时（2026-10-14）==')
    m12 = MatchFlow()
    m12.drain()
    m12.on_goal(1)
    end_round(m12)                       # -> 第 2 局 PREP
    start_round(m12)                     # -> PLAY
    _ok12, _m12m = m12.begin_hole(1)
    ck('begin_hole -> HOLE/EDGE', _ok12 and m12.phase == 'HOLE'
       and m12.hole['stage'] == 'EDGE', str(_m12m))
    _e12 = m12.hole_cycle_edge()
    ck('连按切边（right -> bottom）', _e12 == 'bottom', str(_e12))
    _f12 = m12.hole_to_fine(200.0)
    ck('确定边 -> FINE', bool(_f12) and m12.hole['stage'] == 'FINE')
    _p12 = m12.hole_adjust(10.0, 80.0, 400.0)
    ck('微调 +10 -> 210', _p12 == 210.0, str(_p12))
    _c12 = m12.end_hole(cancel=True)
    ck('ESC 取消 -> 回 PLAY 且不扣额度',
       bool(_c12) and m12.phase == 'PLAY' and m12.free_hole[1] == 1)
    m12.begin_hole(2)
    m12.update(PAUSE_MAX + 0.01)
    _ev12 = m12.drain()
    ck('HOLE 30s 超时 -> 事件 hole_timeout',
       any(e[0] == 'hole_timeout' and e[1] == 2 for e in _ev12), str(_ev12))
    m12.end_hole(cancel=True)
    m12.points[1] = 25                # 凑够 20 分才能发起改磁场（测试变量充钱）
    _ok13, _m13m = m12.begin_edit(1)
    ck('begin_edit -> EDIT/SELECT', _ok13 and m12.phase == 'EDIT'
       and m12.edit['stage'] == 'SELECT', str(_m13m))
    m12.edit_to_adjust()
    ck('edit_to_adjust -> ADJUST', m12.edit['stage'] == 'ADJUST')
    m12.end_edit(confirm=False)
    ck('end_edit 取消 -> 回 PLAY 不扣额度（仍 25 分）',
       m12.phase == 'PLAY' and m12.points[1] == 25 and m12.items[1]['field'] == 0)

    print('== 13) ★ 2026-10-28 局前流程：买实体件 -> 摆放 -> 就绪 -> 开始本局 ==')
    m13 = MatchFlow()
    m13.drain()
    m13.on_goal(1)
    end_round(m13)                        # -> 第 2 局 PREP
    m13.points[1] = 80
    m13.sel_item[1] = 2                   # 障碍 10 分
    _okb, _ = m13.buy(1)
    ck('买障碍 -> 记本局已买 + last_buy=obstacle',
       _okb and m13.phys_bought[1] and m13.last_buy == 'obstacle'
       and m13.items[1]['obstacle'] == 1)
    m13.ready(1)
    m13.ready(2)
    ck('买了但没摆 -> 双就绪先进调整界面',
       m13.phase == 'PREP' and m13.adjust)
    m13.mark_placed(1)
    m13.begin_play()
    ck('摆过实体件 -> 开始本局 -> PLAY',
       m13.phase == 'PLAY' and not m13.adjust)
    # 下一局：没买/没摆 -> 就绪后进调整界面；ESC 回商店；再就绪 -> 开始本局
    end_round(m13)
    ck('下一局回 PREP/SHOP、phys_bought/phys_placed 已复位',
       m13.phase == 'PREP' and not m13.phys_bought[1]
       and not m13.phys_bought[2]
       and not m13.phys_placed[1] and not m13.phys_placed[2])
    m13.ready(1)
    m13.ready(2)
    ck('无实体购买 -> 双就绪进「调整」界面（仍 PREP）',
       m13.phase == 'PREP' and m13.adjust)
    m13.back_to_shop()
    ck('ESC 回商店 -> SHOP/SHOP、adjust 关',
       m13.phase == 'PREP' and not m13.adjust
       and m13.sub[1] == 'SHOP' and m13.sub[2] == 'SHOP')
    start_round(m13)
    ck('调整界面「开始本局」-> PLAY', m13.phase == 'PLAY')

    print('== 14) ★ 2026-10-28 对局中摆放 = PLACE 暂停阶段 ==')
    m14 = MatchFlow()
    m14.drain()                              # 第 1 局直接 PLAY
    _ok14, _ = m14.begin_place(1)
    ck('begin_place -> PLACE（全场暂停）', _ok14 and m14.phase == 'PLACE')
    _okf14, _why14 = m14.can_fire(1, 1)
    ck('PLACE 中不能发射（paused）', (not _okf14) and _why14 == 'paused')
    _t14 = m14.timer
    m14.update(10.0)
    ck('PLACE 冻结洞口计时（10s 不涨）', m14.timer == _t14)
    _okb14, _ = m14.begin_hole(2)
    ck('PLACE 中另一项暂停操作被拒（互斥）', (not _okb14) and m14.phase == 'PLACE')
    m14.end_place()
    ck('end_place -> PLAY', m14.phase == 'PLAY' and m14.place['player'] is None)
    m14.begin_place(2)
    m14.update(PAUSE_MAX + 0.1)
    _ev14 = m14.drain()
    ck('摆放 30s 超时 -> 事件 place_timeout',
       any(e[0] == 'place_timeout' and e[1] == 2 for e in _ev14), str(_ev14))
    m14.end_place()

    print('\n==== match_flow 自检：%d 项，%d 通过，%d 失败 ====' % (n_ok + n_bad, n_ok, n_bad))
    return 0 if n_bad == 0 else 1


if __name__ == '__main__':
    import sys as _sys
    _sys.exit(_selftest())
