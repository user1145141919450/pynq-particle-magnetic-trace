# -*- coding: utf-8 -*-
"""真球实时原型（PC 离线）—— 规则书语义（engine.World）：
  · 发射=真球：镜面反射（含圆角），弹到出射（进洞）为止，出去才消失；
  · 发射后实时显示轨迹（拖尾，engine 自带 2400px 上限=真实游戏行为）；
  · 多球共存 + 同号球弹性反弹（规则书 §3）；
  · 预览虚线 = 最多 3 次反弹 + 收尾一小段（只是显示上限）。
输出：logs/live_ball_proto.mp4（2× 速）+ logs/live_ball_proto_sheet.png
2026-10-04 用户规则订正后的 PC 原型第一步。"""
import math
import os
import random
import sys
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

_H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_H, '..', 'game_pc'))
import engine as E                                    # noqa: E402

W, H, R = 960, 540, 11.0
SC = 1.5
FPS = 30
K = 8                      # 每视频帧 8 个物理步 => 实时速（DT=1/240）
DT = E.DT


def build_world(seed=7, b_fix=0.40, n_blocks=3):
    random.seed(seed)               # 与 v3_scan.build_world 完全同序
    w = E.World()
    w.setup_round(n_blocks)
    for bi, b in enumerate(w.blocks):
        b.b = b_fix if bi % 2 == 0 else -b_fix
    for h in w.holes:
        h['target'] = 0             # 规则 A：两洞都算进
    return w


def cut_preview(pts, n_bounce=3, tail=70.0):
    """把 preview 折线截到“第 n_bounce 次急转（反弹）后 tail 像素”——显示上限。"""
    if len(pts) < 3:
        return pts
    turns = []
    for i in range(1, len(pts) - 1):
        a1 = math.atan2(pts[i][1]-pts[i-1][1], pts[i][0]-pts[i-1][0])
        a2 = math.atan2(pts[i+1][1]-pts[i][1], pts[i+1][0]-pts[i][0])
        if abs((a2 - a1 + math.pi) % (2*math.pi) - math.pi) > 0.9:
            turns.append(i)
    if len(turns) < n_bounce:
        return pts
    idx = turns[n_bounce - 1]
    acc = 0.0
    for j in range(idx, len(pts) - 1):
        acc += math.hypot(pts[j+1][0]-pts[j][0], pts[j+1][1]-pts[j][1])
        if acc >= tail:
            return pts[:j+1]
    return pts


# ---------------- 1) 搜一个“会相撞”的场景 ----------------
A = dict(t=0.0, x=170.0, y=21.5, ang=102.5, sp=590.0)


def solo_path(L, tmax):
    """单飞一条轨迹（无球-球），返回 [(t, x, y), ...]（240Hz 采样）。"""
    w = build_world()
    p = w.launch(L['x'], L['y'], L['ang'], L['sp'], +1, 0)
    out = []
    for i in range(int(tmax / DT)):
        w.step(DT)
        if not p.alive:
            break
        out.append((i * DT, p.x, p.y))
    return out


print('== 搜碰撞场景（碰撞前两球独立 => 分别单飞、按时间对齐找最近距离，精确且快）==')
TSEARCH = 25.0
pathA = solo_path(A, TSEARCH)
print('   A 轨迹采样 %d 点' % len(pathA))


def find_collision(starts, angles, delays):
    for (xB, yB) in starts:
        for dly in delays:
            for angB in angles:
                pathB = solo_path(dict(t=0.0, x=xB, y=yB, ang=float(angB), sp=590.0), TSEARCH)
                ib0 = int(dly / DT)
                m = min(len(pathA) - ib0, len(pathB))
                for i in range(m):
                    ax, ay = pathA[i + ib0][1], pathA[i + ib0][2]
                    bx, by = pathB[i][1], pathB[i][2]
                    if math.hypot(ax - bx, ay - by) < 2 * E.BALL_R - 0.6:
                        return dict(t=dly, x=xB, y=yB, ang=float(angB),
                                    tc=(i + ib0) * DT, px=ax, py=ay)
    return None


bestB = find_collision([(170.0, 518.5), (21.5, 270.0)],
                       list(range(-88, -1, 3)),
                       [2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
print('   粗扫:', bestB)
if bestB is None:
    bestB = find_collision([(170.0, 518.5), (21.5, 270.0), (789.5, 270.0)],
                           list(range(-90, 91, 5)),
                           [3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0])
    print('   细扫:', bestB)
if bestB is None:
    print('   （仍然没有——退化为不碰撞的两球演示）')
    bestB = dict(t=4.0, x=170.0, y=518.5, ang=-45.0)
dlyB, angB = bestB['t'], bestB['ang']
LAUNCHES = [dict(A), dict(t=dlyB, x=bestB['x'], y=bestB['y'], ang=float(angB), sp=590.0)]

# ---------------- 2) 带渲染重放 ----------------
print('== 录制 ==')
w = build_world()
PREV = {}


def make_prev(L, color):
    pts, res, hole = w.preview(L['x'], L['y'], L['ang'], L['sp'], +1,
                               steps=int(60.0/DT), max_bounce=3)
    return dict(pts=cut_preview(pts, 3, 70.0), res=res, hole=hole, color=color, t0=L['t'])


# 背景（一次画好）
bg = np.full((int(H*SC), int(W*SC), 3), 245, np.uint8)


def g2p(x, y):
    return (int(x*SC), int(y*SC))


for b in w.blocks:
    m = min(abs(b.b)/3.0, 1.0)
    col = ((int(235-70*m), int(235-55*m), int(245-20*m)) if b.b > 0
           else (int(245-20*m), int(235-55*m), int(235-70*m)))
    cv2.rectangle(bg, g2p(b.x0, b.y0), g2p(b.x1, b.y1), col, -1)
    cv2.rectangle(bg, g2p(b.x0, b.y0), g2p(b.x1, b.y1), (205, 205, 205), 1)
for h in w.holes:
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

vout = cv2.VideoWriter(os.path.join(_H, '..', 'logs', 'live_ball_proto.mp4'),
                       cv2.VideoWriter_fourcc(*'mp4v'), FPS, (int(W*SC), int(H*SC)))
COLORS = [(0, 150, 255), (255, 90, 220)]          # A 橙 / B 紫粉 (BGR)
fired, prevs, exits, colls = [], {}, {}, []
ev_i, li, fi = 0, 0, 0
TMAX = 70.0
total_frames = int(TMAX / (DT*K)) + 1
for fi in range(total_frames):
    t = fi * DT * K
    while li < len(LAUNCHES) and LAUNCHES[li]['t'] <= t:
        L = LAUNCHES[li]
        p = w.launch(L['x'], L['y'], L['ang'], L['sp'], +1, 0)
        fired.append(p)
        prevs[id(p)] = make_prev(L, COLORS[li % 2])
        li += 1
    for _k in range(K):
        w.step(DT)
        t_cur = t + (_k + 1) * DT
        na = [p for p in w.particles if p.alive]
        for ii in range(len(na)):
            for jj in range(ii+1, len(na)):
                a, b = na[ii], na[jj]
                if math.hypot(a.x-b.x, a.y-b.y) <= 2*E.BALL_R + 1e-6:
                    # ★ 引擎在接触帧把两球推到**正好 2R=22.0px**（解算后的分离状态），
                    #   所以判"碰过"必须用 <= 2R（用 <2R-ε 永远抓不到——踩过）。
                    if not colls or t_cur - colls[-1][0] > 0.3:
                        colls.append((t_cur, (a.x, b.x, a.y, b.y)))
    for ev in w.events[ev_i:]:
        if ev[0] == 'goal':
            x, y = ev[2], ev[3]
            exits.setdefault(len(exits), (t, 'left' if x < 100 else 'right', (x, y)))
    ev_i = len(w.events)
    if fi % 300 == 0:
        print('   frame %d/%d  t=%.1fs  球=%d 事件=%d' % (fi, total_frames, t, len(na), len(w.events)))
    img = bg.copy()
    for pid, pv in prevs.items():
        if t - pv['t0'] < 8.0:
            pp = pv['pts']
            for a_, b_ in zip(pp[:-1], pp[1:]):
                if (int(a_[0]+a_[1]) % 14) < 9:      # 简易虚线
                    cv2.line(img, g2p(*a_), g2p(*b_), pv['color'], 3)
    for i, p in enumerate(fired):
        col = COLORS[i % 2]
        for a_, b_ in zip(p.trail[:-1], p.trail[1:]):
            cv2.line(img, g2p(*a_), g2p(*b_), col, 3)
        if p.alive:
            cv2.circle(img, g2p(p.x, p.y), int(11*SC), col, -1)
            cv2.circle(img, g2p(p.x, p.y), int(11*SC), (40, 40, 40), 2)
    for _, (te, edge, (ex, ey)) in exits.items():
        cv2.circle(img, g2p(ex, ey), int(16*SC/1.5), (0, 190, 0), 4)
    for te, (ax, bx, ay, by) in colls:
        if abs(t - te) < 0.8:
            cv2.circle(img, g2p((ax+bx)/2, (ay+by)/2), int(20*SC), (0, 0, 255), 3)
    pim = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    dr = ImageDraw.Draw(pim)
    dr.text((14, 10), 't = %.1f s   （2x 速）' % t, font=F, fill=(30, 30, 30))
    st = []
    for i, p in enumerate(fired):
        st.append('球%s：%s' % ('AB'[i], '在场（弹着飞）' if p.alive else '已出射'))
    dr.text((14, 44), '   '.join(st), font=F2, fill=(60, 60, 60))
    dr.text((14, 72), '虚线 = 预设轨迹（最多显示 3 次反弹）；实线 = 真球拖尾；绿圈=出射；红圈=两球相碰',
            font=F2, fill=(90, 90, 90))
    img = cv2.cvtColor(np.asarray(pim), cv2.COLOR_RGB2BGR)
    vout.write(img)
    if exits and len(exits) >= 2 and t > max(e[0] for e in exits.values()) + 2.0:
        break
    if t > TMAX:
        break
vout.release()
print('视频完成, exits=', exits, ' colls=', [round(c[0], 1) for c in colls])

cap = cv2.VideoCapture(os.path.join(_H, '..', 'logs', 'live_ball_proto.mp4'))
cells = []
while True:
    ok, fr = cap.read()
    if not ok:
        break
    cells.append(fr)
cap.release()
n = len(cells)
picks = [cells[int(x*(n-1))] for x in np.linspace(0, 1, 9)] if n else []
sheet = np.full((810, 1440, 3), 255, np.uint8)
for i, c in enumerate(picks):
    small = cv2.resize(c, (480, 270))
    r_, c_ = divmod(i, 3)
    sheet[r_*270:(r_+1)*270, c_*480:(c_+1)*480] = small
cv2.imwrite(os.path.join(_H, '..', 'logs', 'live_ball_proto_sheet.png'), sheet)
print('拼图完成 frames=%d' % n)
