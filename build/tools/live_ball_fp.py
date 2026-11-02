# -*- coding: utf-8 -*-
"""live_ball_fp.py — 真球实时原型（**定点版 = 决定 A**，与上板代码同一物理）。

物理 = software/board/realtime_ball（与预览 lane_fixed 逐位一致）；
演示：A 从左上发射 -> B 延时从下边发射 -> 两球相撞（弹性）-> 各自弹到出射。
输出：logs/live_ball_fp.gif（10fps，实时剧情）+ logs/live_ball_fp_sheet.png
"""
import math
import os
import sys
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

_H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _H)
sys.path.insert(0, os.path.join(_H, '..', 'software', 'board'))
sys.path.insert(0, os.path.join(_H, '..', 'game_pc'))
import v3_scan as _vs                      # noqa: E402
import v3_fixedpoint as _V                 # noqa: E402
import realtime_ball as _rtb               # noqa: E402

W, H = 960, 540
SC = 1.5
FPS = 30
K = 8                                       # 每视频帧 8 个物理步 = 实时（240Hz）
DT = _rtb.DT

world = _vs.build_world(7, 0.40)
for h in world.holes:
    h['target'] = 0
FIELDS = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks]
HOLES = list(world.holes)
print('holes:', [(h['edge'], round(float(h['c']), 1), h['target']) for h in HOLES])

A = dict(t=0.0, x=170.0, y=21.5, ang=107.5, sp=590.0)   # 46 次弹的长跑球（当碰撞目标）


def solo_path(L, tmax):
    b = _rtb.LiveBall(L['x'], L['y'], L['ang'], L['sp'])
    out = []
    for i in range(int(tmax / DT)):
        if b.step(FIELDS, HOLES, 0) == 'goal':
            break
        out.append((i * DT, b.x / 256.0, b.y / 256.0))
    return out


print('== 搜相撞场景（单飞独立段对齐，命中距离<21.5px）==')
TSEARCH = 25.0
pathA = solo_path(A, TSEARCH)
print('   A 采样 %d 点' % len(pathA))
bestB = None
for (xB, yB) in ((170.0, 518.5), (21.5, 270.0), (789.5, 270.0)):
    for dly in (2.0, 4.0, 6.0, 8.0, 10.0, 12.0):
        for angB in range(-88, -1, 4):
            pathB = solo_path(dict(x=xB, y=yB, ang=float(angB), sp=590.0), TSEARCH)
            ib0 = int(dly / DT)
            m = min(len(pathA) - ib0, len(pathB))
            for i in range(0, m, 2):
                ax, ay = pathA[i + ib0][1], pathA[i + ib0][2]
                bx, by = pathB[i][1], pathB[i][2]
                if math.hypot(ax - bx, ay - by) < 21.5:
                    bestB = dict(t=dly, x=xB, y=yB, ang=float(angB),
                                 tc=(i + ib0) * DT)
                    break
            if bestB:
                break
        if bestB:
            break
    if bestB:
        break
print('   找到:', bestB)
if bestB is None:
    print('   （没有相撞组合——退化为不碰撞双球）')
    bestB = dict(t=4.0, x=170.0, y=518.5, ang=-45.0)
LAUNCHES = [dict(A), dict(t=bestB['t'], x=bestB['x'], y=bestB['y'],
                          ang=bestB['ang'], sp=590.0)]

# ---------------- 录制 ----------------
print('== 录制 ==')
bg = np.full((int(H*SC), int(W*SC), 3), 245, np.uint8)


def g2p(x, y):
    return (int(x*SC), int(y*SC))


for b in world.blocks:
    m = min(abs(b.b)/3.0, 1.0)
    col = ((int(235-70*m), int(235-55*m), int(245-20*m)) if b.b > 0
           else (int(245-20*m), int(235-55*m), int(235-70*m)))
    cv2.rectangle(bg, g2p(b.x0, b.y0), g2p(b.x1, b.y1), col, -1)
    cv2.rectangle(bg, g2p(b.x0, b.y0), g2p(b.x1, b.y1), (205, 205, 205), 1)
for h in world.holes:
    c, hw = float(h['c']), float(h['w'])/2
    if h['edge'] == 'left':
        cv2.line(bg, g2p(0, c-hw), g2p(0, c+hw), (0, 160, 0), 9)
    else:
        cv2.line(bg, g2p(W, c-hw), g2p(W, c+hw), (0, 160, 0), 9)

try:
    F = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 26)
    F2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
except Exception:
    F = F2 = ImageFont.load_default()

# 预览虚线（发射瞬间算一次；用与上板一样的 cut_at_bounces 口径）
PREV = {}
for i, L in enumerate(LAUNCHES):
    _d = _V.lane_fixed(
        L['x'], L['y'], L['ang'], L['sp'], 1, FIELDS, HOLES, 0,
        steps=4000, max_bounce=50, trail_gap=2.0, max_pts=100000)
    PREV[i] = dict(pts=_vs.cut_at_bounces(_d['pts'], 3, 70.0), t0=L['t'],
                   color=(0, 150, 255) if i == 0 else (255, 90, 220))

COLORS = [(0, 150, 255), (255, 90, 220)]
balls, prev_map, exits, colls = [], {}, [], []
frames_gif = []
li, fi = 0, 0
TMAX = 70.0
NF = int(TMAX / (DT*K)) + 1
exit_t = {}
for fi in range(NF):
    t = fi * DT * K
    while li < len(LAUNCHES) and LAUNCHES[li]['t'] <= t:
        L = LAUNCHES[li]
        b = _rtb.LiveBall(L['x'], L['y'], L['ang'], L['sp'], seq=li)
        balls.append(b)
        prev_map[id(b)] = li
        li += 1
    evs = _rtb.advance(balls, FIELDS, HOLES, K, side=0) if balls else []
    for ev in evs:
        if ev[0] == 'goal':
            b = ev[1]
            exit_t[id(b)] = t
            exits.append((t, b.x / 256.0, b.y / 256.0, b.exit_edge))
        elif ev[0] == 'collide':
            ob = ev[2]
            colls.append((t, (ev[1].x + ob.x) / 2 / 256.0, (ev[1].y + ob.y) / 2 / 256.0))
    if fi % 300 == 0:
        print('   frame %d/%d t=%.1fs 活球 %d exits %d colls %d'
              % (fi, NF, t, sum(1 for b in balls if b.alive), len(exits), len(colls)))
    img = bg.copy()
    for b in balls:
        i_l = prev_map.get(id(b), 0)
        pv = PREV.get(i_l)
        if pv and t - pv['t0'] < 8.0:
            pp = pv['pts']
            for a_, b_ in zip(pp[:-1], pp[1:]):
                if (int(a_[0]+a_[1]) % 14) < 9:
                    cv2.line(img, g2p(*a_), g2p(*b_), pv['color'], 3)
        col = COLORS[i_l % 2]
        tr = b.trail
        for a_, b_ in zip(tr[:-1], tr[1:]):
            cv2.line(img, g2p(*a_), g2p(*b_),
                     col if b.alive else (170, 120, 160), 3)
        if b.alive:
            cv2.circle(img, g2p(b.x/256.0, b.y/256.0), int(11*SC), col, -1)
            cv2.circle(img, g2p(b.x/256.0, b.y/256.0), int(11*SC), (40, 40, 40), 2)
    for (et, ex, ey, _e) in exits:
        if t - et < 4.0:
            cv2.circle(img, g2p(ex, ey), 24, (0, 190, 0), 4)
    for (ct, cx, cy) in colls:
        if t - ct < 1.5:
            cv2.circle(img, g2p(cx, cy), 30, (0, 0, 255), 4)
    pim = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    dr = ImageDraw.Draw(pim)
    dr.text((14, 10), 't = %.1f s' % t, font=F, fill=(30, 30, 30))
    st = []
    for i_l, b in enumerate(balls):
        st.append('球%s：%s' % ('AB'[i_l],
                  ('已出射(%s洞)' % b.exit_edge) if not b.alive else '在场（弹着飞，已弹%d次）' % b.bounces))
    dr.text((14, 44), '    '.join(st), font=F2, fill=(60, 60, 60))
    dr.text((14, 72), '虚线=预设轨迹（最多显示3次反弹）；实线=真球拖尾；绿圈=出射；红圈=相撞；定点物理=上板同款',
            font=F2, fill=(90, 90, 90))
    img = cv2.cvtColor(np.asarray(pim), cv2.COLOR_RGB2BGR)
    if fi % 3 == 0:
        small = cv2.resize(img, (800, 450), interpolation=cv2.INTER_AREA)
        frames_gif.append(Image.fromarray(
            cv2.cvtColor(small, cv2.COLOR_BGR2RGB)).convert('P', palette=Image.ADAPTIVE, colors=128))
    if len(exits) >= 2 and t > max(e[0] for e in exits) + 2.0:
        break
frames_gif[0].save(os.path.join(_H, '..', 'logs', 'live_ball_fp.gif'),
                   save_all=True, append_images=frames_gif[1:],
                   duration=100, loop=0, optimize=True, disposal=2)
print('GIF =', os.path.getsize(os.path.join(_H, '..', 'logs', 'live_ball_fp.gif')) / 1e6, 'MB')

# 拼图：从 GIF 回读（★ P 模式图转 RGB 再用——直接拿调色板索引当像素会出黑图，踩过）
from PIL import ImageSequence                                    # noqa: E402
_gif = Image.open(os.path.join(_H, '..', 'logs', 'live_ball_fp.gif'))
gframes = [fr.convert('RGB') for fr in ImageSequence.Iterator(_gif)]
ng = len(gframes)
sheet = np.full((810, 1440, 3), 255, np.uint8)
for i in range(9):
    f_ = np.asarray(gframes[int(i * (ng - 1) / 8)])
    bgr = cv2.cvtColor(f_, cv2.COLOR_RGB2BGR)
    small = cv2.resize(bgr, (480, 270))
    r_, c_ = divmod(i, 3)
    sheet[r_*270:(r_+1)*270, c_*480:(c_+1)*480] = small
cv2.imwrite(os.path.join(_H, '..', 'logs', 'live_ball_fp_sheet.png'), sheet)
print('拼图完成; exits=', [(round(e[0], 1), e[3]) for e in exits],
      ' colls=', [round(c[0], 1) for c in colls])
