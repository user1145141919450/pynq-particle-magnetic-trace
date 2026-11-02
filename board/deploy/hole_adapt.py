# -*- coding: utf-8 -*-
"""hole_adapt.py — 洞口自适应（M2.4；规则书 §8 / `game_pc/engine.py` 的 adapt_holes 移植）

每 30 秒无进球（`match_flow` 的 `'hole_due'` 事件）调一次：每个洞口朝**该侧边界上
本局落点热点**移动一小步。语义逐句照抄 engine（含它注释里写明踩过的坑）：

  · 热点 = 窗口 max(洞宽, 40px) 内**落点最密**的那颗落点；落点 <3 个不评估；
  · **死区 40px**：热点离当前位置 < 40px 就不动——engine 原话："否则每 30 秒都会
    无意义地晃一下，玩家刚瞄准好洞口就飘走（'每隔 30 秒变换一次洞口'就是这么来的）"；
  · 单次位移 ≤ min(1.5×洞宽, 66px)；离得远 jitter ±15、离得近 ±6（防"一调就进"）；
  · 夹进合法范围（距边界端点 ≥ 半洞宽 + 圆角）＋ 同边洞口不许重叠（间隔 > (w1+w2)/2+10）；
  · **2 秒前视否决**：若移动会让**任何在飞的真球** 2 秒内直接进洞 → 本次作废。
    前视用 `realtime_ball` 克隆步进——与真球物理**逐位同源**（clone 只读探路，
    不动原球状态）。

调用方（ball_game）：落点从真球 `'bounce'` 事件记（按边、game 坐标；每局开始清）；
命中 `'hole_due'` 时 `pick_moves` → 逐条 2s 否决 → 改 `v3_world.holes[i]['c']`。
真球物理每帧拿同一份 dict、PL 预览每次扫描都会 `write_scene` 重写洞表、画面直读
世界—— 三处自动同步，没有额外写点（M2.4 不动 RTL）。
"""

import random as _random_mod

# ★★ 2026-10-07 实锤修（r48/r49 两次开局随机值一模一样 242/393）：
#   `v3_scan.build_world` 会 `random.seed(seed)` 播**全局** random（seed=7 恒定），
#   于是用全局 random 掷随机位 = 每次开局重放同一条序列（「假随机」）。
#   本模块一律用**专用实例**：Random() 自播种（os.urandom），与全局种子解耦。
#   ★ 台架有回归：种死全局 seed 后连掷两次必须不同（global 版本会相同）。
_RNG = _random_mod.Random()

MIN_MARKS = 3          # 落点不足 3 个不评估（engine 同款）
DEADBAND = 40.0        # 热点离现位 <40px 不动（反抖动死区）
MAX_STEP = 66.0        # 单次位移上限（engine：min(1.5*w, 66)）


def _hole_range(edge, field_w, field_h, corner_r, hole_w):
    """洞口中心在边界上的合法范围（距端点 ≥ 半洞宽 + 圆角；engine 同式）。"""
    if edge in ('left', 'right'):
        return (corner_r + hole_w / 2.0, field_h - corner_r - hole_w / 2.0)
    return (corner_r + hole_w / 2.0, field_w - corner_r - hole_w / 2.0)


def pick_moves(holes, hits, field_w, field_h, corner_r=0.0, rng=None):
    """纯函数（rng 注入 = 可复现测试）：算出本轮移动计划 [(hole_index, new_c), ...]。

    hits = {'left': [c,...], 'right': [...], 'top': [...], 'bottom': [...]}
    （c 对左右边 = y、对上下边 = x，与 engine 的 hit_marks 口径一致）。
    只出"计划"——2 秒前视否决由调用方逐条做（would_score_within）。
    """
    rng = rng if rng is not None else _RNG
    moves = []
    for i, h in enumerate(holes):
        marks = list(hits.get(h.get('edge')) or [])
        if len(marks) < MIN_MARKS:
            continue
        w = float(h.get('w', 44.0))
        win = max(w, 40.0)
        # 热点：窗口内落点最密处（engine 的 O(n²) 数数——每 30 秒一次，够用）
        best_c, best_n = float(h['c']), 0
        for m in marks:
            cnt = sum(1 for x in marks if abs(x - m) <= win / 2.0)
            if cnt > best_n:
                best_n, best_c = cnt, float(m)
        dist = abs(best_c - float(h['c']))
        if dist < DEADBAND:
            continue                          # 反抖动死区（engine 同款）
        step = min(1.5 * w, MAX_STEP)
        jitter = rng.uniform(-15.0, 15.0) if dist > 80.0 else rng.uniform(-6.0, 6.0)
        delta = max(-step, min(step, best_c + jitter - float(h['c'])))
        lo, hi = _hole_range(h['edge'], field_w, field_h, corner_r, w)
        new_c = min(hi, max(lo, float(h['c']) + delta))
        # 同边重叠约束（对**当前**位置判——engine 同款，不看本轮其他计划）
        if all(j == i or holes[j].get('edge') != h['edge']
               or abs(new_c - float(holes[j]['c']))
               > (w + float(holes[j].get('w', 44.0))) / 2.0 + 10.0
               for j in range(len(holes))):
            moves.append((i, round(new_c, 3)))
    return moves


def random_positions(holes, field_w, field_h, corner_r=0.0, rng=None, min_move=60.0):
    """★ 2026-10-07 用户："每一局的洞口希望是随机的"（规则书 §2 本就是"电脑设置本局
    磁场与洞口"）：每个洞口在合法范围内**掷一个新位置**，返回 [(hole_index, new_c), ...]。

    约束：① 合法范围（圆角 + 半洞宽）内；② 尽量与原位相距 >= min_move（"一眼看出
    是新的一局"——掷 12 次都不满足就放宽取最后一次）；③ 同边洞口不重叠（沿用
    pick_moves 同一条间隔式）。**2 秒前视否决由调用方做**（v5 续局时在飞球可能
    正压着新洞位——被否决就保留原值）。
    """
    rng = rng if rng is not None else _RNG
    out = []
    for i, h in enumerate(holes):
        w = float(h.get('w', 44.0))
        lo, hi = _hole_range(h['edge'], field_w, field_h, corner_r, w)
        cand = None
        for _ in range(12):
            cand = rng.uniform(lo, hi)
            if abs(cand - float(h['c'])) >= min_move:
                break
        if all(j == i or holes[j].get('edge') != h['edge']
               or abs(cand - float(holes[j]['c']))
               > (w + float(holes[j].get('w', 44.0))) / 2.0 + 10.0
               for j in range(len(holes))):
            out.append((i, round(cand, 3)))
    return out


def would_score_within(balls, holes_after, fields, secs=2.0):
    """2 秒前视：任何在飞真球在"新洞位"下 ≤secs 秒内进洞 → True（该次移动作废）。

    ★ 克隆步进：**显式搬状态**建一颗新 LiveBall（x/y/vx/vy/charge/who 全是定点
      整数直接赋值）——不用 copy.copy（不赌 __slots__ 对象在板端 Python 3.10 的
      copy 行为）；trail 单独给（step() 的轨迹缓存要用，共用会污染原球）。
      只读探路，原球状态分毫不动。
    ★ 物理就是 realtime_ball.step —— 与我们真正的真球推进**逐位同源**，不近似。
    """
    import realtime_ball as _rtb
    n = int(secs * 240.0)
    for b in (balls or ()):
        if not b.alive:
            continue
        c = _rtb.LiveBall(0.0, 0.0, 0.0, 0.0)        # 占位构造，状态下面直接搬
        c.x, c.y, c.vx, c.vy = b.x, b.y, b.vx, b.vy
        c.alive, c.bounces, c.n_steps = True, b.bounces, b.n_steps
        c.trail = [b.trail[-1]] if b.trail else []
        c._gap = b._gap
        c.seq, c.charge, c.who = b.seq, b.charge, b.who
        c.exit_edge, c.t_exit = None, None
        for _ in range(n):
            r = c.step(fields, holes_after, 0)
            if r == 'goal':
                return True
            if not c.alive:
                break
    return False
