# -*- coding: utf-8 -*-
"""v3_shot_search.py — 离线搜"定妆照"场景（纯 PC，不碰相机/板子）。

为什么要有它（2026-09-28 夜）：
  上板拍一张好看的图，成本很高（链路每几分钟死一次 + 物理摆球位置不可控）。
  但**场景参数是可离线复现的**：v3_scan.py 的画面 = (seed 决定的块/洞) + (--b) +
  (球位/速度/角度) + (--fan)。而 V3 的轨迹由 `tools/v3_fixedpoint.py`（定点黄金模型）
  给出 —— 它和 PL **逐位一致**（板上实测过 ✓）。所以：**在 PC 上把参数搜好，
  上板只要照参数跑一条命令**，不用试错。

判据（什么算"好看的定妆照"）：
  ① 绿（进洞）≥ 2 条   ② 红（撞墙没进）≥ 3 条   ③ 有真正的**临界**：
  按角度排序后，绿→非绿的**换档位置**那一两条（而不是"最后一条撞墙的"——
  那是现有 PL 分支的判据，永远落在扇形边缘，图注会骗人）。

用法：
  set PYTHONPATH=E:\\py_pkgs\\vision
  python tools\\v3_shot_search.py [--seed 7] [--b 0.4] [--fan 20] [--top 8]
  python tools\\v3_shot_search.py --render best.png --pos 240,270 --b 0.4 --fan 20
"""

import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, 'game_pc'), os.path.join(_ROOT, 'software', 'board')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np

import engine
import v3_fixedpoint as V

WIN = 8                     # 17 路 = 2*WIN+1
STEPS = 2000
MAX_BOUNCE = 0      # ★ 与 PL 同口径：RTL 的 max_bounce 是死输入（撞墙即停）
SPEED = 400.0
FIELD_W, FIELD_H = 960.0, 540.0


def build_world(seed, b_fix):
    """**逐字复刻 v3_scan.py 的场景构造**，否则上板对不上。"""
    import random
    random.seed(seed)
    w = engine.World()           # World(None) 不再播种；与板上一致
    w.setup_round(3)
    if b_fix is not None:
        for bi, b in enumerate(w.blocks):
            b.b = b_fix if bi % 2 == 0 else -b_fix
    return w


def scan(w, x0, y0, ang0, fan_half, speed=SPEED, mb=MAX_BOUNCE):
    """17 路定点扫描 -> [(ang, lane_dict, kind)]"""
    fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in w.blocks]
    holes = list(w.holes)
    span = fan_half / float(WIN)
    out = []
    for k in range(-WIN, WIN + 1):
        a = ang0 + k * span
        ln = V.lane_fixed(x0, y0, a, speed, 1, fields, holes, 0,
                          steps=STEPS, max_bounce=mb)
        kind = {1: 'goal', 2: 'bounce', 0: 'none'}[ln['rtl_res']]
        out.append((a, ln, kind))
    return out


def analyse(fan, w):
    """判据：绿/红条数 + 真正的临界（绿↔非绿换档处）+ 换档裕量（px）。"""
    kinds = [f[2] for f in fan]
    n_goal = kinds.count('goal')
    n_bou = kinds.count('bounce')
    n_non = kinds.count('none')
    trans = None                     # 换档下标（从上一条到这条，命运变了）
    for i in range(1, len(kinds)):
        if (kinds[i] == 'goal') != (kinds[i - 1] == 'goal'):
            trans = i
    margin = None
    if trans is not None:
        # 用"离洞口边最近"的那条定义临界：绿的那侧的端点离洞边多远
        cand = [j for j in (trans - 1, trans) if 0 <= j < len(fan)]
        best = None
        for j in cand:
            ln = fan[j][1]
            if fan[j][2] != 'goal':
                continue
            for h in w.holes:
                if h['target'] != 0:
                    continue
                ex, ey = (ln['x'], ln['y']) if h['edge'] in ('left', 'right') else (ln['x'], ln['y'])
                c = ey if h['edge'] in ('left', 'right') else ex
                m = abs(c - h['c']) - h['w'] / 2.0
                if best is None or abs(m) < abs(best):
                    best = m
        margin = best
    score = 0.0
    if n_goal >= 1:
        score += 2
    if n_goal >= 2:
        score += 1
    if n_bou >= 3:
        score += 2
    if n_bou >= 8:
        score += 1
    if trans is not None:
        score += 3
    if margin is not None and abs(margin) <= 12:
        score += 3
    return dict(goal=n_goal, bounce=n_bou, none=n_non, trans=trans,
                margin=margin, score=score)


def render_shot(w, fan, crit_idx, ball, vel, path, ang=0.0, speed=SPEED, fan_half=WIN,
                bounce=MAX_BOUNCE):
    """用板上同一个 render() 出图（但画**完整弧线** —— 离线能拿到 pts）。"""
    import v3_scan
    f = [(a, ln['pts'], (k, ln['bounces'], ln['hole'])) for (a, ln, k) in fan]
    # ★ vel 传 None：这里的角度/速度是**输入值**不是跟踪值，不能印成 "tracked:"
    img = v3_scan.render(w, f, crit_idx, ball, None, engine.BALL_R,
                         extra='offline search (golden model = PL bit-exact, max_bounce=%d)' % bounce,
                         scan_txt='scan: angle=%.1f deg  speed=%.0f px/s  fan=+-%.1f deg'
                                  % (ang, speed, fan_half))
    import cv2
    cv2.imwrite(path, img)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--b', type=float, default=0.4)
    ap.add_argument('--fan', type=float, default=20.0)
    ap.add_argument('--speed', type=float, default=SPEED)
    ap.add_argument('--ang', type=float, default=0.0)
    ap.add_argument('--bounce', type=int, default=MAX_BOUNCE)
    ap.add_argument('--top', type=int, default=8)
    ap.add_argument('--pos', type=str, default=None, help='只用这一个球位（配 --render）')
    ap.add_argument('--render', type=str, default=None, help='出图路径')
    a = ap.parse_args()

    w = build_world(a.seed, a.b)
    print('[场景] seed=%d  ±%.2f  块=%d  口径 max_bounce=%d（=PL）'
          % (a.seed, a.b, len(w.blocks), a.bounce))
    print('       洞: %s' % [(h['edge'], round(h['c']), h['target']) for h in w.holes])

    if a.pos:
        cands = [tuple(float(t) for t in a.pos.split(','))]
        fans = [a.fan]
    else:
        cands = [(x, y) for x in (100, 140, 180, 220, 260, 300) for y in (110, 170, 230, 290, 350, 410)]
        fans = [8.0, 12.0, 16.0, 20.0, 24.0]

    rows = []
    for (x0, y0) in cands:
        if not w.inside_rounded(x0, y0, engine.BALL_R + 2):
            continue
        for fh in fans:
            fan = scan(w, x0, y0, a.ang, fh, a.speed, a.bounce)
            r = analyse(fan, w)
            r.update(pos=(x0, y0), fan=fh, fanobj=fan)
            rows.append(r)

    rows.sort(key=lambda r: -r['score'])
    print('\n%-14s %5s | %4s %4s %4s | %5s %8s | %5s' %
          ('球位', '扇角', '绿', '红', '灰', '换档', '离洞边px', '得分'))
    for r in rows[:a.top]:
        print('(%3d,%3d)      %5.0f | %4d %4d %4d | %5s %8s | %5.1f' %
              (r['pos'][0], r['pos'][1], r['fan'], r['goal'], r['bounce'], r['none'],
               '-' if r['trans'] is None else r['trans'],
               '-' if r['margin'] is None else '%+.1f' % r['margin'], r['score']))

    if a.render and rows:
        best = rows[0]
        fan = best['fanobj']
        crit = None
        if best['trans'] is not None:
            j = best['trans']
            crit = j if fan[j][2] == 'goal' else j - 1
            if fan[crit][2] != 'goal':
                crit = None
        p = render_shot(w, fan, crit, best['pos'], None, a.render,
                        ang=a.ang, speed=a.speed, fan_half=best['fan'], bounce=a.bounce)
        print('\n[出图] %s   球位=%s 扇角=%.0f 临界=%s' %
              (p, best['pos'], best['fan'],
               '-' if crit is None else '%.1f°' % fan[crit][0]))
        print('  上板命令：v3_scan.py --nobit --pos %d,%d --b %.2f --fan %.0f --angle %.0f --speed %.0f --seed %d'
              % (best['pos'][0], best['pos'][1], a.b, best['fan'], a.ang, a.speed, a.seed))
        print('  （若 RTL 以后补上真弹跳，把 --bounce 调回 10 再搜一轮）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
