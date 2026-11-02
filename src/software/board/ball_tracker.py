# -*- coding: utf-8 -*-
"""ball_tracker.py — **多目标追踪**（2026-10-05 用户需求："多颗实物球各自被追踪"）。

为什么（老代码之弊，实锤在案）：原实现每帧 `max(cands, key=area)` —— **没有时间关联**：
两颗球面积一波动，目标就在两颗之间跳（实测 ±121px 跳变、轨迹画成锯齿、速度拟合全废）。

本模块把"每帧候选列表"变成**稳定的轨迹列表**：
  · **预测-关联**：每条轨迹用平滑速度外推，按残差做全局贪心配对（先近后远、**同色
    才允许配对**、一帧一候选不许重配）。gate 随帧间隔放大（base + 850px/s×dt）——
    低帧率下快球也跟得住。★ 2026-10-06：跨色配对**禁止**（原来只是"贵 1.3 倍"，
    在 400px 级门限下形同虚设——手影轨迹会把回来的球劫持走，判色/编号全乱；
    同色才配 = 宁缺勿错，轻微判色抖动导致的断轨由"户口簿"认领兜底）。
  · **平滑位（sx/sy/sr）**（★ 2026-10-06 用户实况："识别框时大时小、质心位置跟着
    变，会影响轨迹计算"）：候选的位/尺寸自带 ±10px 级抖动 —— 内部关联与"停稳"
    判据继续用**原始位**；**显示/拖尾/瞄准/发射起点**改用指数平滑位：
    位置（sx/sy）用小抖动 EMA、位移 > pos_snap 视为"真挪动"立即跟住（零滞后）；
    **半径（sr）只慢平滑、没有 snap**（r 只影响显示——"1 号还是时大时小"的教训：
    给了 snap 就会在跳变时直接跟过去）。摆好的球：平滑值 = 真实值。
  · **发号双门槛**：confirmed + 活够 num_min_age + **停稳 ≥ num_still** 才发号 ——
    运动中的手影永不占号（用户实况："手放进去误识别到 3、放的小球变成了 4"）。
  · **生命周期**：新候选 = 新生轨迹（连续 confirm_hits 帧命中才 confirmed）；漏检记 miss，
    超过 max_miss_s 删除；每条轨迹自带 trail（限长）供绘制。
  · **编号**（★★ 2026-10-05 夜修 + 10-06 两轮加强）：
    ① 只有 confirmed 且活够 **num_min_age** 秒的轨迹才发号（短命手影/杂影不占号）；
    ② 活着期间绝不换号；跟丢删除后编号进**"户口簿"**（grave_s=90s / grave_r=600px，
       同色或未知色或红蓝漂移兜底）——"拿起来挪一段再放回"认回原号；
    ③ ★★ **摆放停稳 → 按场上数量重排 1..N**（用户 2026-10-06 定案）：全场轨迹
       静止 ≥ **settle_s** 秒时，按**左→右**重新发 1..N 号——"球不动了号就固定，
       而且要顺，不许 1/2/3/7/8"。★★ 两条防翻号铁律（10-06 实况"1号一会红一会
       蓝"——弱球反复走失把全场掀翻了一遍）：
       · **短暂走失 = 留位**（按"死前在不在动"分辨，2026-10-06 两轮反馈定案）：
         户口里"刚走失、还可能回来"的**球色条目**在重排时**占住自己的序列位**
         ——别人不许顶号、不许压缩；它回来直接认领。**停着闪丢**留
         `mia_hold_blink`（15s，防反复闪断掀翻全场）；**拿起就走不留位**
         （0s）——死讯一到立刻压缩连号（用户："拿走后有迟钝，不要保留了"）。
       · **排序带滞回**：以"当前号序"为基底做相邻交换，只有"更左的明显更靠右"
         （x 差 > order_hys）才换位——x 挨得近的球抖动不翻号。
       运动中保持原号（②兜底），停下的那一刻全体归位。编号只此一把（发射/
       预览点名用它）——任何地方都不许再把"列表序号"当编号（ball_game 圈旁数字
       画的也是 t.no）。
  · **纯逻辑零依赖**（不 import cv2/numpy，时钟由调用方传入 now）——台架/自检可完全复现。

用法（ball_game 主循环每帧）：
    tracks = trk.update(cands, now)     # cands=[{cls,x,y,r,area}]（相机像素），无候选给 []
"""
import math


class Track(object):
    __slots__ = ('id', 'no', 'cls', 'x', 'y', 'r', 'area', 'trail', 'vx', 'vy',
                 't_last', 'hit', 'confirmed', 'born', 'still',
                 'sx', 'sy', 'sr', 'ref_x', 'ref_y')

    def __init__(self, tid, cand, now):
        self.id = tid
        self.no = None          # ★ 选球/展示编号（=发射点名那把号）：confirmed 且活
        #                           够 num_min_age 才发；活着绝不换；跟丢可凭"户口簿"
        #                           认回原号；未确认/未成熟=None
        self.cls = cand.get('cls')
        self.x = float(cand['x'])
        self.y = float(cand['y'])
        self.r = float(cand.get('r', 0.0))
        self.area = float(cand.get('area', 0.0))
        self.trail = [(self.x, self.y)]
        self.vx = 0.0
        self.vy = 0.0
        self.t_last = now
        self.hit = 1
        self.confirmed = False
        self.born = now
        self.still = 0.0        # 停稳累积（秒）——还在锚点 still_radius 圈内就涨
        self.ref_x = self.x     # ★ "停稳"锚点：离开它一圈才算真挪动（两态抖动不算）
        self.ref_y = self.y
        self.sx = self.x        # ★ 平滑位（显示/拖尾/瞄准/发射起点用；内部关联用原始位）
        self.sy = self.y
        self.sr = self.r


class MultiTracker(object):
    def __init__(self, gate_base=60.0, gate_speed=850.0, max_miss_s=1.2,
                 trail_len=30, confirm_hits=2, smooth=0.5,
                 grave_s=90.0, grave_r=600.0, num_min_age=2.0):
        self.gate_base = gate_base
        self.gate_speed = gate_speed
        self.max_miss_s = max_miss_s
        self.trail_len = trail_len
        self.confirm_hits = confirm_hits
        self.smooth = smooth
        self.grave_s = grave_s          # ★ 编号"户口簿"保留时长（秒）—— 一整个演示回合的尺度
        self.grave_r = grave_r          # ★ 认领的最远距离（相机像素，≈大半个场区）
        self.num_min_age = num_min_age  # ★ 新轨迹活够这么久才发号（短命杂影不占号）
        self.still_radius = 30.0        # ★ "停稳"锚点半径（相机像素）——圈内都算停着；
        #                                   两态抖动/裁剪跳动不再被打断（10-06 修复）
        self.settle_s = 2.0             # ★ 全场静止这么久 -> 结算重排 1..N（左->右）
        self.mia_hold_blink = 4.0       # ★ "停着闪丢"的留位窗口（秒）——防弱球反复闪断掀翻全场
                                        # ★★ 2026-10-07 15s→4s（用户实况："拿走球不能很快
                                        #   重新分配号"）：2.5fps 下"抓起就走"的移动帧常没被
                                        #   拍到（手一挡就没了）→ 死前 still 还是老值 → 误判
                                        #   "闪丢" → 白白留位 15 秒。4s 仍保护典型检测闪断
                                        #   （台架自检里的"短走失"=2.8s 照过）；更快的拿放
                                        #   节奏靠它兜底，再慢的真丢就该重排了。
        self.mia_hold_moved = 0.0       # ★ "拿起就走"不留位（0s）——死讯一到立刻压缩连号
        self.blink_still_ref = 0.8      # ★ 死前静止 ≥ 它 -> 判"闪丢"；否则判"被拿走"
        self.renum_ev = []              # ★★ 2026-10-07：重排事件账 [(Track, 旧号, 新号)]
                                        #   —— 调用方 drain 后让玩家的"控球号"跟着球走
                                        #   （治"序号重排时屏幕跳动"：高亮/预览/发射
                                        #   都按号锚定，号换球时玩家的圈就跳走了）
        self.smooth_pos = 0.5           # ★ 平滑位（sx/sy）的 EMA 系数（显示/瞄准用）
        self.pos_snap = 25.0            # ★ 原始位-平滑位差超过它 = 真挪动 -> 立即跟住
        self.smooth_r = 0.3             # ★ 半径平滑更慢、**没有 snap**（r 只影响显示；
        #                                   "识别框时大时小"必须彻底压住）
        self.num_still = 1.0            # ★ 发号还要"停稳 ≥ 它"——运动中的手影永不发号
        self.order_hys = 30.0           # ★ 排序滞回：x 差 ≤ 它不换位（防邻近抖翻号；
        #                                   30px≈半颗球径——真实错位照样能归位）
        self.tracks = []
        self._grave = []                # 户口簿：(no, x, y, cls, 时刻)
        self._grave_cap = 16            # 只留最新的几条，防无限涨
        self._next_id = 1

    def take_renumber(self):
        """取走本帧的重排事件 [(Track, 旧号, 新号)]（取完清空）。
        调用方据此让玩家的控球号跟着**物理球**走（见 ball_game 的接线）。"""
        ev, self.renum_ev = self.renum_ev, []
        return ev

    def update(self, cands, now):
        """一帧一次。cands=[{cls,x,y,r,area}]（相机像素，可为空 list）。返回轨迹列表
        （按 area 降序；confirmed 的在前——挑"主球"看 .confirmed）。"""
        cands = list(cands or [])
        # 1) 预测
        preds = []
        for t in self.tracks:
            dt = max(0.0, now - t.t_last)
            preds.append((t.x + t.vx * dt, t.y + t.vy * dt, dt))
        # 2) 全局贪心配对：所有 (残差, track, cand) 排序后一帧一配，宁缺勿抢
        pairs = []
        for ti, t in enumerate(self.tracks):
            px, py, dt = preds[ti]
            gate = self.gate_base + self.gate_speed * dt
            for j, c in enumerate(cands):
                # ★★ 2026-10-06（"手一进去号就变"实况，自检当场复现）：**只允许
                #   同色关联**。原来"跨色贵一点（×1.3）"在低帧率下形同虚设——
                #   门限本身就有 400px 级，一条手影轨迹（离球回来点仅 122px）能
                #   把球候选"劫持"过去，球从此挂在杂影轨迹上、判色和编号全乱。
                #   跨色宁缺勿错；轻微判色抖动造成的断轨由"户口簿"认领兜底
                #   （红<->蓝漂移也能认回原号），不会乱号。
                if c.get('cls') != t.cls:
                    continue
                d = math.hypot(c['x'] - px, c['y'] - py)
                if d <= gate:
                    pairs.append((d, ti, j))
        pairs.sort(key=lambda p: p[0])
        used_t, used_c = set(), set()
        for d, ti, j in pairs:
            if ti in used_t or j in used_c:
                continue
            used_t.add(ti)
            used_c.add(j)
            t = self.tracks[ti]
            c = cands[j]
            dt = max(1e-3, now - t.t_last)
            # ★★ 停稳累积 = **锚点半径**判据（10-06"球1轨迹变来变去"根因修复）：
            #   原来按"逐拍位移 ≤10px"——贴边缘的球被裁剪、质心两态抖动（每拍
            #   >10px）永远被判"在动"，跟丢时误分类成"拿起拿走"→ 号被顶、预览
            #   跟着换球。现在：只要还在锚点 **still_radius** 圈内就算"停着"；
            #   真挪动（离开锚点一圈）→ 锚点跟过去、still 清零。
            if math.hypot(float(c['x']) - t.ref_x,
                          float(c['y']) - t.ref_y) <= self.still_radius:
                t.still += dt
            else:
                t.ref_x, t.ref_y = float(c['x']), float(c['y'])
                t.still = 0.0
            nvx = (float(c['x']) - t.x) / dt
            nvy = (float(c['y']) - t.y) / dt
            t.vx += (nvx - t.vx) * self.smooth
            t.vy += (nvy - t.vy) * self.smooth
            t.x = float(c['x'])
            t.y = float(c['y'])
            t.r = float(c.get('r', t.r))
            t.area = float(c.get('area', t.area))
            # ★ 平滑位（★ 2026-10-06 用户："识别框时大时小、质心跟着抖"）：
            #   小抖动 EMA 抹平；差 > pos_snap = 真挪动 -> 立即跟住（零滞后）。
            _dx, _dy = t.x - t.sx, t.y - t.sy
            if math.hypot(_dx, _dy) > self.pos_snap:
                t.sx, t.sy = t.x, t.y
            else:
                t.sx += _dx * self.smooth_pos
                t.sy += _dy * self.smooth_pos
            # 半径：只慢平滑、**不给 snap** —— ★ 10-06 用户："1号还是时大时小"
            #（原来 ±50% 就跳，而 r 抖动 17<->29 恰好常过线 -> 圈还在呼吸）。
            # r 只影响显示（发射判据用的是固定 BALL_R），慢收没有副作用。
            t.sr += (t.r - t.sr) * self.smooth_r
            t.t_last = now
            t.hit += 1
            if t.hit >= self.confirm_hits:
                t.confirmed = True
            t.trail.append((t.sx, t.sy))          # 拖尾用平滑位（不再毛刺）
            if len(t.trail) > self.trail_len:
                t.trail.pop(0)
        # 3) 剩下没配上的候选 = 新生
        for j, c in enumerate(cands):
            if j not in used_c:
                self.tracks.append(Track(self._next_id, c, now))
                self._next_id += 1
        # 4) 超时淘汰 + 记"户口簿"
        # ★★ 2026-10-05/06 两晚演进（"控球3按发射没反应" -> "手一进去号就变"）：
        #   轨迹跟丢删除后，它**带过的编号**进户口簿（no,x,y,cls,时刻）。户口
        #   保留整段演示的尺度（90s）+ 大认得动（600px）—— 摆球时"拿起来挪一段
        #   再放回"号码钉在球上不换。其它新目标不许捡户口簿里的号（保证不重号）。
        for t in self.tracks:
            if now - t.t_last > self.max_miss_s and t.no is not None:
                # 第 6 格 = 死前一瞬的"静止时长"：分辨"闪丢"（停着）还是"拿走"（在动）
                self._grave.append([t.no, t.x, t.y, t.cls, now, t.still])
        if len(self._grave) > self._grave_cap:
            self._grave = self._grave[-self._grave_cap:]
        self.tracks = [t for t in self.tracks if now - t.t_last <= self.max_miss_s]
        self._grave = [e for e in self._grave if now - e[4] <= self.grave_s]
        # 5) 发号 / 认领：只有 **confirmed 且活够 num_min_age** 的轨迹才参与 ——
        #    两帧即灭或刚进来的手影/杂影根本拿不到号，也就搅不乱号池。
        _used = set(t.no for t in self.tracks if t.no)
        _live = set(t.no for t in self.tracks if t.no)   # 在号码（认领时不许撞车）
        _used |= set(e[0] for e in self._grave)      # 户口簿的号先占位，等人认领
        for t in self.tracks:
            if t.no is not None or not t.confirmed:
                continue
            if now - t.born < self.num_min_age:
                continue
            # ★★ 10-06 用户实况（"手放进去误识别到 3、放的小球就变成了 4"）：
            #   发号还要"停稳 ≥ num_still" —— **运动中的手影永不发号**（它在动，
            #   本就拿不到 still）；放稳的球照常拿号；手一走结算归位。
            if t.still < self.num_still:
                continue
            # ① 户口簿认领：先同色（任一边"未知色"也算同色 —— 判色会漂）；
            #    没有再"红<->蓝"兜底（判色漂移的实况就是这两色互换）。
            _best, _bd = None, None
            for e in self._grave:
                if e[0] in _live:                    # 撞"在号码"的老条目 = 已被结算重排作废
                    continue
                if e[3] != t.cls and 'unknown' not in (e[3], t.cls):
                    continue
                _d = math.hypot(t.x - e[1], t.y - e[2])
                if _d <= self.grave_r and (_bd is None or _d < _bd):
                    _best, _bd = e, _d
            if _best is None and t.cls in ('red', 'blue'):
                for e in self._grave:
                    if e[0] in _live:
                        continue
                    if e[3] in ('red', 'blue') and e[3] != t.cls:
                        _d = math.hypot(t.x - e[1], t.y - e[2])
                        if _d <= self.grave_r and (_bd is None or _d < _bd):
                            _best, _bd = e, _d
            if _best is not None:
                t.no = _best[0]
                _live.add(_best[0])
                self._grave.remove(_best)
                continue
            # ② 无亲可认：取最小空号
            _n = 1
            while _n in _used:
                _n += 1
            t.no = _n
            _used.add(_n)
            _live.add(_n)
        # 6) ★★ 2026-10-06（用户定案）：**摆放停稳 -> 按场上数量重排 1..N**。
        #   "球不动了，编号就固定，而且要顺（1/2/3/4）——不是 1/2/3/7/8。"
        #   ★★ 两条防翻号铁律（同夜实况"1号一会红一会蓝"——弱球反复走失把全场
        #     掀翻了一遍）：
        #     (a) **短暂走失 = 留位**：户口里"刚走失（<mia_hold_s）、还可能回来"
        #         的**球色条目**在排序里**占住自己的序列位**——别人不许顶号、不许
        #         压缩；它回来直接认领自己的号。超时才算真移除，这时才压缩。
        #     (b) **排序带滞回**：以"当前号序"为基底做相邻交换，只有"更左的明显
        #         更靠右"（x 差 > order_hys）才换位 —— x 挨得近的球抖动不翻号。
        #   重排后清户口簿里撞"在号码"的老条目（那个号已经换主了）。
        class _Slot(object):
            __slots__ = ('x', 'no', 'entry')      # 走失球的"虚位"（挂在户口条目上）

            def __init__(self, x, no, entry):
                self.x, self.no, self.entry = x, no, entry

        _cf = [t for t in self.tracks if t.no is not None]
        if _cf and len(_cf) == len(self.tracks) and \
                all(t.still >= self.settle_s for t in _cf):
            _items = list(_cf)
            for e in self._grave:                 # 球色的"新鲜户口" = 留位
                if e[3] not in ('red', 'blue'):
                    continue
                _hold = (self.mia_hold_blink if e[5] >= self.blink_still_ref
                         else self.mia_hold_moved)
                if now - e[4] < _hold:
                    _items.append(_Slot(e[1], e[0], e))
            _items.sort(key=lambda o: o.no)       # 基底 = 当前号序
            _chg = True
            while _chg:                            # 带滞回的相邻交换（气泡排序）
                _chg = False
                for _i in range(len(_items) - 1):
                    if _items[_i].x > _items[_i + 1].x + self.order_hys:
                        _items[_i], _items[_i + 1] = _items[_i + 1], _items[_i]
                        _chg = True
            for _pos, _o in enumerate(_items, start=1):
                if _o.no != _pos:
                    if not isinstance(_o, _Slot):        # ★ 活轨迹：记"号换主"事件
                        self.renum_ev.append((_o, _o.no, _pos))
                    _o.no = _pos
                    if isinstance(_o, _Slot):
                        _o.entry[0] = _pos       # 虚位号写回户口（回来就认这个号）
                        # 写回的号若撞上别的户口条目，旧条目作废（号已换主）
                        self._grave[:] = [e2 for e2 in self._grave
                                          if e2 is _o.entry or e2[0] != _pos]
            _live2 = set(t.no for t in self.tracks if t.no is not None)
            self._grave = [e for e in self._grave if e[0] not in _live2]
        # 排序键对 no=None 必须安全（未确认/未成熟可能同帧出现多条 —— 直接比 None 会炸）
        return sorted(self.tracks,
                      key=lambda t: (not t.confirmed,
                                     t.no if t.no is not None else 1 << 30))


# ---------------------------------------------------------------- 自检（喂假时间线）
def _selftest():
    # 1) ping-pong 回归：两颗**静止**球、面积每帧随机波动（老 bug 的触发条件）
    trk = MultiTracker()
    import random
    random.seed(1)
    ids_seen, swaps = {}, 0
    for i in range(30):
        cands = [{'cls': 'red', 'x': 424 + random.uniform(-3, 3),
                  'y': 120 + random.uniform(-2, 2), 'r': 26,
                  'area': 500 + random.uniform(-200, 200)},
                 {'cls': 'red', 'x': 545 + random.uniform(-3, 3),
                  'y': 113 + random.uniform(-2, 2), 'r': 26,
                  'area': 500 + random.uniform(-200, 200)}]
        tracks = trk.update(cands, i * 0.4)
        assert len(tracks) == 2, '应稳定保持 2 条轨迹，实际 %d' % len(tracks)
        for t in tracks:
            k = t.id
            bucket = 0 if t.x < 480 else 1
            if k in ids_seen and ids_seen[k] != bucket:
                swaps += 1
            ids_seen[k] = bucket
    assert swaps == 0, '静止双球出现 %d 次串线' % swaps
    for t in trk.tracks:
        assert max(p[0] for p in t.trail) - min(p[0] for p in t.trail) < 10, \
            '静止球轨迹不该抖动'
    print('1) ping-pong 回归 OK（30 帧双球零串线、轨迹稳）')

    # 2) 交叉：两球迎面交错而过，各自跟住
    trk = MultiTracker()
    for i in range(20):
        xa, xb = 300 + i * 15, 600 - i * 15
        tracks = trk.update([{'cls': 'red', 'x': xa, 'y': 200, 'r': 20, 'area': 400},
                             {'cls': 'blue', 'x': xb, 'y': 200, 'r': 20, 'area': 400}],
                            i * 0.1)
        assert len(tracks) == 2
    by_cls = {t.cls: t for t in trk.tracks}
    assert by_cls['red'].x > by_cls['blue'].x, '交过后红蓝应各走各的'
    print('2) 交叉不串线 OK（红现在右、蓝现在左，各跟各的）')

    # 3) 漏检容忍：一颗球消失 0.8s 再回来，轨迹 id 不变
    trk = MultiTracker()
    t0 = None
    for i in range(4):
        trk.update([{'cls': 'red', 'x': 200, 'y': 200, 'r': 20, 'area': 400},
                    {'cls': 'red', 'x': 500, 'y': 200, 'r': 20, 'area': 400}], i * 0.2)
    t0 = [t.id for t in trk.tracks if t.x < 300][0]
    for i in range(4, 8):                       # 左球消失 0.8s
        trk.update([{'cls': 'red', 'x': 500, 'y': 200, 'r': 20, 'area': 400}], i * 0.2)
    trk.update([{'cls': 'red', 'x': 200, 'y': 200, 'r': 20, 'area': 400},
                {'cls': 'red', 'x': 500, 'y': 200, 'r': 20, 'area': 400}], 8 * 0.2)
    ids = [t.id for t in trk.tracks if t.x < 300]
    assert ids and ids[0] == t0, '漏检 0.8s 后回来应还是同一条轨迹（id %s vs %s）' % (ids, t0)
    print('3) 漏检容忍 OK（消失 0.8s 回来不换 id）')

    # 4) 快球 + 低帧率：590px/s、dt=0.4s（位移 236px）也能跟上
    trk = MultiTracker()
    x = 200.0
    for i in range(8):
        trk.update([{'cls': 'red', 'x': x, 'y': 300, 'r': 20, 'area': 400}], i * 0.4)
        trk2 = trk
        x += 236
    assert len(trk.tracks) == 1, '快球应始终同一条轨迹（实际 %d 条）' % len(trk.tracks)
    assert abs(trk.tracks[0].x - (200 + 236 * 7)) < 1.0, '快球应跟到最新位置'
    print('4) 快球低帧率 OK（590px/s @ 0.4s/帧，单轨迹不丢）')

    # 5) 新生球：中途出现第三颗 → 变新轨迹、两帧后 confirmed
    trk = MultiTracker()
    for i in range(4):
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'red', 'x': 400, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    for i in range(4, 7):
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'red', 'x': 400, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'blue', 'x': 700, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    assert len(trk.tracks) == 3
    nb = [t for t in trk.tracks if t.cls == 'blue'][0]
    assert nb.confirmed, '新生球两帧后应 confirmed'
    print('5) 新生 OK（第三颗两帧内确认，cls=blue）')

    # 6) 固定编号：发号（要活够 2s）+ 活着不换 + 户口簿认领
    trk = MultiTracker()
    for i in range(16):                       # t=0..3.0：红=1、蓝=2（都过"2s 成熟线"）
        tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400}],
                            i * 0.2)
    nos = {t.cls: t.no for t in tracks}
    assert nos == {'red': 1, 'blue': 2}, '应在场顺序发号 1/2，实际 %s' % nos
    for i in range(16, 26):                   # t=3.2..5.0：红消失 2s（>1.2s -> 进户口簿）
        tracks = trk.update([{'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400}],
                            i * 0.2)
    assert [t.no for t in tracks] == [2], \
        '红球只是走失（不算移除）-> 蓝球保持 2 号不压缩，实际 %s' % [t.no for t in tracks]
    for i in range(26, 40):                   # t=5.2..：红原位回来 -> 2s 成熟后认回 1 号
        tracks = trk.update([{'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400}],
                            i * 0.2)
    got = sorted((t.cls, t.no) for t in tracks)
    assert got == [('blue', 2), ('red', 1)], '回来应认回 1 号，实际 %s' % got
    print('6) 固定编号 OK（2s 成熟发号 / 活着不换 / 户口簿认领）')

    # 7) ★★ 2026-10-05/06 回归（"控球3没反应"→"手一进去号就变"）：跟丢重认 ——
    #    蓝球被手遮住 + 挪到 450px 外再放回，仍要认回 2 号；期间杂影不许拿号。
    trk = MultiTracker()
    for i in range(16):                       # t=0..3.0：红=1、蓝=2
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    for i in range(16, 28):                   # t=3.2..5.4：蓝消失（>1.2s -> 进户口簿）
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                {'cls': 'black', 'x': 700, 'y': 300, 'r': 14, 'area': 120}], 5.6)
    trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], 5.8)
    for i in range(30, 44):                   # t=6.0..8.6：蓝在 450px 外回来
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'blue', 'x': 703, 'y': 422, 'r': 20, 'area': 400}], i * 0.2)
    tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                         {'cls': 'blue', 'x': 703, 'y': 422, 'r': 20, 'area': 400}], 8.8)
    got = sorted((t.cls, t.no) for t in tracks if t.cls in ('red', 'blue'))
    assert got == [('blue', 2), ('red', 1)], \
        '挪位后回来应认回原号（蓝=2），实际 %s' % got
    assert all(t.no is None or t.no in (1, 2) for t in tracks), \
        '杂影不许拿号/占号（场上编号只能有 1/2），实际 %s' % [
            (t.cls, t.no) for t in tracks]
    print('7) 跟丢重认 OK（遮 2s + 挪 450px 回来仍是 2 号；杂影没拿号）')

    # 8) ★ 回归：两个新目标**同帧**出现（未成熟排序崩溃点）+ 活够 2s 才发号。
    trk = MultiTracker()
    for i in range(12):                       # t=0..2.2：红=1
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                         {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400},
                         {'cls': 'black', 'x': 700, 'y': 300, 'r': 14,
                          'area': 120}], 2.4)     # 两个新生同帧（都还没成熟）
    _n0 = {t.cls: t.no for t in tracks}
    assert _n0.get('blue') is None and _n0.get('black') is None, \
        '未成熟不许发号，实际 %s' % _n0
    for i in range(13, 24):                   # t=2.6..4.6：过 2s 成熟线 -> 发 2/3
        tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'black', 'x': 700, 'y': 300, 'r': 14,
                              'area': 120}], i * 0.2)
    got = sorted((t.cls, t.no) for t in tracks)
    assert got == [('black', 3), ('blue', 2), ('red', 1)], \
        '成熟后按最小空号发（2/3），实际 %s' % got
    print('8) 同帧新生 OK（未成熟不发号、排序不炸；2s 后 2、3 号各就位）')

    # 9) ★★ 10-06 实况回归（"手一进去号就变"）：长遮挡 25s（远超旧 6s 窗口）
    #    + 途中一条"活够 2s、拿了号的杂影" —— 蓝球回来仍认原号 2（不被 3 带跑）。
    trk = MultiTracker()
    for i in range(16):                       # t=0..3.0：红=1、蓝=2
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    for i in range(16, 116):                  # t=3.2..23.0：蓝长消失（旧原语早失效）
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    for i in range(116, 131):                 # t=23.2..26.0：杂影活 2.8s（会拿号 3）
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'black', 'x': 700, 'y': 300, 'r': 14, 'area': 120}], i * 0.2)
    for i in range(131, 140):                 # t=26.2..27.8：杂影也走了
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    for i in range(140, 156):                 # t=28.0..：蓝原位回来
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                         {'cls': 'blue', 'x': 400, 'y': 100, 'r': 20, 'area': 400}], 31.2)
    got = sorted((t.cls, t.no) for t in tracks if t.cls in ('red', 'blue'))
    assert got == [('blue', 2), ('red', 1)], \
        '长遮挡回来仍应认回 2 号（不许被杂影的 3 号带跑），实际 %s' % got
    print('9) 长遮挡 OK（25s 后回来仍是 2 号；带号杂影的 3 号没抢走它）')

    # 10) ★★ 10-06 用户定案："摆放停稳 -> 按场上数量重排 1..N、左->右" ——
    #     先放的球不一定是最左的：停稳后要按空间序归位（不许 1/2 乱序）。
    trk = MultiTracker()
    for i in range(16):          # 先放右球（出生号早）再放左球 —— 号序与空间序相反
        trk.update([{'cls': 'red', 'x': 400, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'blue', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    tracks = trk.update([{'cls': 'red', 'x': 400, 'y': 100, 'r': 20, 'area': 400},
                         {'cls': 'blue', 'x': 100, 'y': 100, 'r': 20, 'area': 400}], 3.2)
    got = sorted((t.cls, t.no) for t in tracks)
    assert got == [('blue', 1), ('red', 2)], \
        '停稳后应按左->右重排：蓝(左)=1、红(右)=2，实际 %s' % got
    print('10) 结算重排 OK（停稳后按场上数量 1..N、左->右归位）')

    # 11) ★★ 10-06 实况："1号一会红一会蓝"——x 邻近两球缓慢交错（每拍 ±8px，
    #     仍在"停稳"预算内）**不许翻号**（排序滞回）。
    trk = MultiTracker()
    tracks = None
    for i in range(16):                       # 摆定：蓝(200) 红(240) -> 蓝=1 红=2
        tracks = trk.update([{'cls': 'blue', 'x': 200.0, 'y': 285, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': 240.0, 'y': 100, 'r': 20, 'area': 400}],
                            i * 0.2)
    nos = {t.cls: t.no for t in tracks}
    assert nos == {'blue': 1, 'red': 2}, '初始应 蓝=1 红=2，实际 %s' % nos
    _seq = [(208, 232), (216, 224), (224, 216), (232, 208),
            (224, 216), (216, 224), (208, 232), (200, 240)]
    for k, (_bx, _rx) in enumerate(_seq):     # 真实 x 顺序多次互越（相对偏移打进 ±24px）
        tracks = trk.update([{'cls': 'blue', 'x': float(_bx), 'y': 285, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': float(_rx), 'y': 100, 'r': 20, 'area': 400}],
                            3.2 + k * 0.2)
    nos = {t.cls: t.no for t in tracks}
    assert nos == {'blue': 1, 'red': 2}, \
        '邻近缓慢交错不许翻号（滞回），实际 %s' % nos
    print('11) 邻近不翻号 OK（缓越 ±24px、8 拍交错，1/2 号钉住）')

    # 12) ★★ 实况回归（"1号一会红一会蓝"的正主）：**弱球反复走失不许掀翻全场** ——
    #     短走失（<mia_hold）期间其它球保持原号等它回来认领；超时真移除才压缩。
    trk = MultiTracker()

    def _feed6(t, with_blue=True):
        c = [{'cls': 'red', 'x': 242, 'y': 98, 'r': 20, 'area': 400},
             {'cls': 'red', 'x': 428, 'y': 90, 'r': 20, 'area': 400}]
        if with_blue:
            c.append({'cls': 'blue', 'x': 204, 'y': 276, 'r': 20, 'area': 400})
        return trk.update(c, t)

    for i in range(16):                       # t=0..3.0：蓝(204)=1 红(242)=2 红(428)=3
        tracks = _feed6(i * 0.2)
    nos = {round(t.x): t.no for t in tracks}
    assert nos == {204: 1, 242: 2, 428: 3}, '初始 1/2/3 归位失败：%s' % nos
    for i in range(16, 31):                   # t=3.2..6.0：蓝走失 2.8s（弱球跟丢）
        tracks = _feed6(i * 0.2, with_blue=False)
    got = sorted((round(t.x), t.no) for t in tracks)
    assert got == [(242, 2), (428, 3)], \
        '短走失不许重排（要等它回来认领），实际 %s' % got
    for i in range(31, 46):                   # t=6.2..：蓝原位回来 -> 认回 1 号
        tracks = _feed6(i * 0.2)
    got = sorted((round(t.x), t.no) for t in tracks)
    assert got == [(204, 1), (242, 2), (428, 3)], '回来应认回 1 号，实际 %s' % got
    for k in range(5):                        # t=9.2..10.0：蓝球被"拿起来"快速移动
        tracks = trk.update([{'cls': 'red', 'x': 242, 'y': 98, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': 428, 'y': 90, 'r': 20, 'area': 400},
                             {'cls': 'blue', 'x': 204 + 40 * k, 'y': 276,
                              'r': 20, 'area': 400}], 9.2 + k * 0.2)
    trk.take_renumber()                       # ★ 清掉前段的旧事件，专看这一段的账
    for i in range(51, 60):                   # t=10.2..11.8：拿走了（死前在动 -> **不留位**）
        tracks = _feed6(i * 0.2, with_blue=False)
    got = sorted((round(t.x), t.no) for t in tracks)
    assert got == [(242, 1), (428, 2)], \
        '拿起拿走后应**立刻**压缩 1/2（不留位），实际 %s' % got
    _ev = trk.take_renumber()                 # ★★ 2026-10-07：重排事件账（选球跟随用）
    assert sorted((o, n) for _, o, n in _ev) == [(2, 1), (3, 2)], \
        '重排事件账应记 2->1 / 3->2（谁跟着谁换号用），实际 %s' % _ev
    print('12) 弱球走失 OK（停着闪丢长留位不重排、回来认原号；拿起拿走立刻压缩；'
          '重排事件账 2->1/3->2 OK）')

    # 13) ★★ 10-06 用户实况："识别框时大时小、质心跟着抖，影响轨迹计算" ——
    #     平滑位（sx）：小抖动被抹平、真挪动立即跟上（snap 零滞后）。
    trk = MultiTracker()
    for i in range(12):                       # ±12px 交替抖动（确定性）
        x = 288.0 if i % 2 == 0 else 312.0
        trk.update([{'cls': 'red', 'x': x, 'y': 200, 'r': 24, 'area': 400}], i * 0.2)
    t0 = trk.tracks[0]
    assert 294.0 <= t0.sx <= 306.0, \
        '±12px 交替抖动应被压到中心附近（±6），实际 sx=%.1f' % t0.sx
    assert t0.x in (288.0, 312.0), '原始位保持不变（内部关联用），实际 %.1f' % t0.x
    trk.update([{'cls': 'red', 'x': 500.0, 'y': 200, 'r': 24, 'area': 400}], 2.6)
    assert trk.tracks[0].sx == 500.0, \
        '真挪动(200px)必须立即跟上（snap），实际 %.1f' % trk.tracks[0].sx
    # r（识别框尺寸）也要压住："1 号还是时大时小"——r 在 20/30 之间跳时，
    # 平滑半径必须稳在中值附近（没有 snap 可跳）。
    trk2 = MultiTracker()
    for i in range(12):
        trk2.update([{'cls': 'red', 'x': 300, 'y': 200,
                      'r': (20.0 if i % 2 == 0 else 30.0), 'area': 400}], i * 0.2)
    assert 23.0 <= trk2.tracks[0].sr <= 27.0, \
        'r 在 20/30 间跳 -> 平滑半径应稳在中值（23~27），实际 %.1f' % trk2.tracks[0].sr
    print('13) 平滑位 OK（±12px 位置抖动压到 ±6、真挪动即时跟上；r 抖动压到中值）')

    # 14) ★★ 10-06 用户实况："手放进去误识别到 3 后，放的小球就变成了 4" ——
    #     **运动中的手影永不发号**（发号加了"停稳 ≥1s"门槛）；放稳的球照常拿号；
    #     手一走，号立刻归位。
    trk = MultiTracker()
    for i in range(16):                       # t=0..3.0：红(100)=1、红(500)=2
        trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                    {'cls': 'red', 'x': 500, 'y': 100, 'r': 20, 'area': 400}], i * 0.2)
    for i in range(16, 40):                   # t=3.2..8.0：手影一直在场里晃（每拍 60px）
        hx = 300 + 60 * ((i - 16) % 8)
        tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': 500, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'unknown', 'x': float(hx), 'y': 300, 'r': 18,
                              'area': 300},
                             {'cls': 'blue', 'x': 800, 'y': 300, 'r': 20,
                              'area': 400}], i * 0.2)
    _h = [t for t in tracks if t.cls == 'unknown'][0]
    assert _h.no is None, '运动中的手影不许发号，实际 no=%s' % _h.no
    _b = [t for t in tracks if t.cls == 'blue'][0]
    assert _b.no == 3, '放稳的球应正常拿到 3 号，实际 %s' % _b.no
    for i in range(40, 52):                   # t=8.2..：手走了（死讯 1.2s -> 归位）
        tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': 500, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'blue', 'x': 800, 'y': 300, 'r': 20,
                              'area': 400}], i * 0.2)
    got = sorted((t.cls, t.no) for t in tracks)
    assert got == [('blue', 3), ('red', 1), ('red', 2)], \
        '手走后应 红1/红2/蓝3，实际 %s' % got
    print('14) 手影不占号 OK（运动手影无号；放稳的球拿号；手走归位）')

    # 15) ★★ 10-06 实况（"球 1 的轨迹变来变去"根因）：**贴边缘的球被裁剪、质心
    #     两态抖动** —— "停稳"判据不许被抖动打断；它跟丢时按"闪丢"留位，
    #     号码不许被别的球顶掉（旧判据逐拍 >10px 永远判"在动" -> 被当"拿走"）。
    trk = MultiTracker()

    def _c15(bx, t):
        return trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                           {'cls': 'blue', 'x': bx, 'y': 100, 'r': 20, 'area': 400},
                           {'cls': 'red', 'x': 700, 'y': 100, 'r': 20, 'area': 400}],
                          t)

    for i in range(16):                       # t=0..3.0：红1=1、蓝=2、红2=3
        tracks = _c15(500.0, i * 0.2)
    nos = {round(t.x): t.no for t in tracks}
    assert nos == {100: 1, 500: 2, 700: 3}, '初始 1/2/3 失败：%s' % nos
    for i in range(16, 36):                   # t=3.2..7.0：蓝在 500 附近"两态裁剪抖动"
        _c15(526.0 if i % 2 == 0 else 498.0, i * 0.2)
    for i in range(36, 55):                   # t=7.2..10.8：蓝抖动中消失
        tracks = trk.update([{'cls': 'red', 'x': 100, 'y': 100, 'r': 20, 'area': 400},
                             {'cls': 'red', 'x': 700, 'y': 100, 'r': 20, 'area': 400}],
                            i * 0.2)
    got = sorted((round(t.x), t.no) for t in tracks)
    assert got == [(100, 1), (700, 3)], \
        '抖动中跟丢应判"闪丢"留位：红2 不许顶到 2 号，实际 %s' % got
    for i in range(55, 70):                   # 蓝回来 -> 认回 2 号
        tracks = _c15(500.0, i * 0.2)
    got = sorted((round(t.x), t.no) for t in tracks)
    assert got == [(100, 1), (500, 2), (700, 3)], \
        '蓝球回来应认回 2 号，实际 %s' % got
    print('15) 边缘抖动跟丢 OK（两态抖动不算"在动"；闪丢留位、号不被顶、回来认回）')
    print('SELFTEST OK（15 组全过）')


if __name__ == '__main__':
    _selftest()
