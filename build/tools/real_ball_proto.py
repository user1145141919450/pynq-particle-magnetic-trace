# -*- coding: utf-8 -*-
"""真球语义原型（PC 离线）——2026-10-04 用户规则订正后的第一张效果图。
同一条轨迹画两遍：青虚线=预览口径（前 3 次弹+70px 收尾）；橙实线=真球（一直弹到出洞）。"""
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
import v3_scan as _vs                       # noqa: E402
import v3_fixedpoint as V                   # noqa: E402

W, H, R = 960, 540, 11.0
SC = 1.5
world = _vs.build_world(7, 0.40)
for h in world.holes:
    h['target'] = 0
fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks]
holes = list(world.holes)

X0, Y0, SPD, AIN = 170.0, 21.5, 590.0, 17.5
d = V.lane_fixed(X0, Y0, AIN + 90.0, SPD, 1, fields, holes, 0,
                 steps=20000, max_bounce=999, trail_gap=1.0, max_pts=300000)
pts = d['pts']
res, bounces = d['rtl_res'], d['bounces']

# 反弹点（贴壁聚簇）
bs = []
i = 0
NX, NY = W - R, H - R
while i < len(pts):
    x, y = pts[i]
    if abs(x - R) < 0.4 or abs(x - NX) < 0.4 or abs(y - R) < 0.4 or abs(y - NY) < 0.4:
        j = i
        while j + 1 < len(pts) and (abs(pts[j+1][0]-R) < 0.6 or abs(pts[j+1][0]-NX) < 0.6
                                    or abs(pts[j+1][1]-R) < 0.6 or abs(pts[j+1][1]-NY) < 0.6):
            j += 1
        bs.append(((i + j) // 2, pts[(i + j) // 2]))
        i = j + 1
    else:
        i += 1

cut = len(pts) - 1
if len(bs) >= 3:
    cut = min(len(pts) - 1, bs[2][0] + 70)

img = np.full((int(H*SC), int(W*SC), 3), 245, np.uint8)
def g2p(p):
    return (int(p[0]*SC), int(p[1]*SC))

for (bx0, by0, bx1, by1, bb) in fields:
    m = min(abs(bb)/3.0, 1.0)
    col = (int(235-70*m), int(235-55*m), int(245-20*m)) if bb > 0 else (int(245-20*m), int(235-55*m), int(235-70*m))
    cv2.rectangle(img, g2p((bx0, by0)), g2p((bx1, by1)), col, -1)
    cv2.rectangle(img, g2p((bx0, by0)), g2p((bx1, by1)), (205, 205, 205), 1)
for h in holes:
    e, c, w = h['edge'], float(h['c']), float(h['w'])/2
    p1, p2 = {'left': ((0, c-w), (0, c+w)), 'right': ((W, c-w), (W, c+w)),
              'top': ((c-w, 0), (c+w, 0)), 'bottom': ((c-w, H), (c+w, H))}[e]
    cv2.line(img, g2p(p1), g2p(p2), (0, 160, 0), 8)

def dash_line(pp, col, thick, dash=11, gap=7):
    run, draw = 0.0, True
    for a, b in zip(pp[:-1], pp[1:]):
        L = math.hypot(b[0]-a[0], b[1]-a[1])
        if L == 0: continue
        t0 = 0.0
        while t0 < L:
            t1 = min(L, t0 + (dash - run if draw else gap - run))
            seg = (run + (t1 - t0))
            if draw:
                pa = (a[0] + (b[0]-a[0])*t0/L, a[1] + (b[1]-a[1])*t0/L)
                pb = (a[0] + (b[0]-a[0])*t1/L, a[1] + (b[1]-a[1])*t1/L)
                cv2.line(img, g2p(pa), g2p(pb), col, thick)
            run = seg
            if run >= (dash if draw else gap):
                run = 0.0; draw = not draw
            t0 = t1

# 预览口径（青虚线粗） / 真球剩余（橙实线细）
dash_line(pts[:cut+1], (255, 255, 0), 4)
for a, b in zip(pts[cut:-1], pts[cut+1:]):
    cv2.line(img, g2p(a), g2p(b), (0, 140, 255), 2)
# 反弹圈
for k, (idx, p) in enumerate(bs):
    col = (0, 0, 255) if k < 3 else (180, 120, 0)
    cv2.circle(img, g2p(p), 6, col, 2)
    cv2.putText(img, str(k+1), (g2p(p)[0]+8, g2p(p)[1]-8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 2)
# 起点 / 终点
cv2.circle(img, g2p((X0, Y0)), 8, (0, 0, 255), -1)
if res == 1:
    cv2.circle(img, g2p((d['x'], d['y'])), 9, (0, 200, 0), 3)

im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
dr = ImageDraw.Draw(im)
try:
    f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 26)
    f2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 22)
except Exception:
    f = f2 = ImageFont.load_default()
exit_txt = ("真球：共弹 %d 次后【进洞】（%s洞）" % (bounces, d['hole']['edge'] if d['hole'] else '?')) if res == 1 else ("真球：%d 步内没出洞" % d['steps'])
dr.text((14, 10), "预览口径 = 最多显示 3 次反弹 + 收尾一小段（青虚线）", font=f, fill=(0, 100, 100))
dr.text((14, 44), exit_txt + "（橙实线 = 第 4 次弹往后）", font=f, fill=(150, 60, 0))
dr.text((14, 78), "球位(%.0f,%.0f)  角=%.1f°  速度=%.0f  seed7 B=0.4  两洞都算" % (X0, Y0, AIN, SPD), font=f2, fill=(60, 60, 60))
out = os.path.join(_H, '..', 'logs', 'real_ball_proto_A.png')
im.save(out)
print('图:', out)
print('真球结果: res=%s stop=%s 反弹=%d次 步数=%d 末端=(%.1f,%.1f)' % (d['rtl_res'], d['stop'], d['bounces'], d['steps'], d['x'], d['y']))
print('反弹点前 6 个:', [(round(p[0],1), round(p[1],1)) for _, p in bs[:6]], '总反弹点=', len(bs))
