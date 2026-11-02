#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bounce_preview.py —— 「单虚线 + 最多 N 次反弹」预设轨迹的 **PC 端原型**（2026-10-04）

产品需求（用户 2026-10-03 定案）：发射后的预设轨迹现在是 17 根角度扇线，乱、看不出
趋势、难调参。改成：**一根**沿输入角度的完整轨迹**虚线**，考虑弹性边界回弹，
**最多 2 次反弹，第三次碰壁就停**。

物理/几何全部复用**项目自己的**东西（不另造一套）：
  · game_pc/engine.py       —— 场区构造（分块磁场 + 洞），与板上同一份
  · tools/v3_fixedpoint.py  —— 定点黄金模型 lane_fixed()，**本来就支持弹跳**
    （多撞几次、反射、bounce_limit 停）——只是 RTL 的 max_bounce 是死输入，
    板上按 0 口径跑；这里按真语义跑。

判定语义（与 lane_fixed 逐字一致）：
  · goal         = 从目标侧（side=0 → 右洞）的洞里出去
  · bounce_limit = 反弹次数用尽 —— `bounces > max_bounce` 时停，
    即 max_bounce=2 ⇒ 撞壁①反弹、撞壁②反弹、**撞壁③停**（= 用户要的语义）

用法（在仓库根目录跑）：
  python tools/bounce_preview.py                                    # 配方一杆
  python tools/bounce_preview.py --angle -20 --speed 400
  python tools/bounce_preview.py --sweep "-50,-38.5,-25,0,20"       # 每个角度一张
  python tools/bounce_preview.py --pos 880,40 --angle -38.5 --speed 1630 --max-bounce 2
输出：logs/bounce_preview/*.png（1440x810，图上文字一律 ASCII）
"""
import argparse
import math
import os
import random
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)                                   # v3_fixedpoint
sys.path.insert(0, os.path.join(_ROOT, "game_pc"))          # engine

import engine                                               # noqa: E402
import v3_fixedpoint as V                                   # noqa: E402

# ---------------- 中文文字层 ----------------
# ★ 铁律"图上只用 ASCII"的本意 = cv2.putText 的 Hershey 字体画不了汉字（一排 ?????）。
#   面向中国用户的正解：**文字走 PIL + 中文字体**，形状仍用 cv2。
#   （板上以后移植时同理：打包一个子集化中文字体即可。）
_FONT_CANDIDATES = [
    r'C:\Windows\Fonts\msyh.ttc',      # 微软雅黑
    r'C:\Windows\Fonts\simhei.ttf',    # 黑体
    r'C:\Windows\Fonts\simsun.ttc',    # 宋体
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',   # 板端备选
]


def _font(size):
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return None


def put_cn(img, text, xy, size, color_bgr, anchor='la'):
    """在 cv2 图上写中文（PIL 渲染后原地贴回）。找不到中文字体时退回 cv2 ASCII 提示。"""
    font = _font(size)
    if font is None:
        cv2.putText(img, text.encode('ascii', 'replace').decode(),
                    xy, cv2.FONT_HERSHEY_SIMPLEX, size / 30.0, color_bgr, 1)
        return
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    d.text(xy, text, font=font, fill=(color_bgr[2], color_bgr[1], color_bgr[0]), anchor=anchor)
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def build_world(seed, b_fix, n_blocks=3):
    """与 software/board/v3_scan.py 的 build_world **逐字一致**（两边必须同场区）。"""
    random.seed(seed)
    w = engine.World()
    w.setup_round(n_blocks)
    if b_fix is not None:
        for bi, b in enumerate(w.blocks):
            b.b = b_fix if bi % 2 == 0 else -b_fix
    return w


def dash_segments(pts, dash=11.0, gap=7.0):
    """折线 -> 虚线段表 [(p0, p1), ...]（按弧长走，段间留 gap）。"""
    segs = []
    drawing, rem = True, dash
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-9:
            continue
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        t = 0.0
        while t < L:
            take = min(rem, L - t)
            if drawing:
                segs.append(((x0 + ux * t, y0 + uy * t),
                             (x0 + ux * (t + take), y0 + uy * (t + take))))
            t += take
            rem -= take
            if rem <= 1e-6:
                drawing = not drawing
                rem = dash if drawing else gap
    return segs


def render(world, rr, meta, scale=1.5):
    """把场区 + 单条虚线轨迹画出来。rr = lane_fixed 的返回 dict。"""
    S = scale
    W, H = int(V.FIELD_W * S), int(V.FIELD_H * S)
    img = np.full((H, W, 3), 245, np.uint8)

    # 分块磁场：B>0 偏蓝底、B<0 偏红底（与 v3_scan.render 同一手法）
    for b in world.blocks:
        x0, y0, x1, y1 = int(b.x0 * S), int(b.y0 * S), int(b.x1 * S), int(b.y1 * S)
        mag = min(abs(b.b) / 3.0, 1.0)
        col = (int(235 - 70 * mag), int(235 - 55 * mag), int(245 - 20 * mag)) if b.b > 0 \
            else (int(245 - 20 * mag), int(235 - 55 * mag), int(235 - 70 * mag))
        cv2.rectangle(img, (x0, y0), (x1, y1), col, -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (190, 190, 190), 1)
        cv2.putText(img, 'B=%+.2f' % b.b, (x0 + 8, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55 * S, (110, 110, 110), 1)

    # 洞口（绿实块，画在边缘 12px 厚，与 v3_scan.render 一致）+ 中文标签
    _edge_txt = {'left': '左洞', 'right': '右洞', 'top': '上洞', 'bottom': '下洞'}
    for h in world.holes:
        c, w2 = float(h['c']), float(h['w']) / 2.0
        if h['edge'] == 'left':
            cv2.rectangle(img, (0, int((c - w2) * S)), (int(12 * S), int((c + w2) * S)),
                          (0, 160, 0), -1)
        elif h['edge'] == 'right':
            cv2.rectangle(img, (W - int(12 * S), int((c - w2) * S)), (W, int((c + w2) * S)),
                          (0, 160, 0), -1)
        elif h['edge'] == 'top':
            cv2.rectangle(img, (int((c - w2) * S), 0), (int((c + w2) * S), int(12 * S)),
                          (0, 160, 0), -1)
        else:
            cv2.rectangle(img, (int((c - w2) * S), H - int(12 * S)),
                          (int((c + w2) * S), H), (0, 160, 0), -1)
        if h['edge'] == 'right':
            put_cn(img, _edge_txt['right'], (W - int(16 * S), int(c * S)),
                   int(12 * S), (0, 110, 0), anchor='ra')
        elif h['edge'] == 'left':
            put_cn(img, _edge_txt['left'], (int(16 * S), int(c * S)),
                   int(12 * S), (0, 110, 0))
        elif h['edge'] == 'top':
            put_cn(img, _edge_txt['top'], (int(c * S), int(16 * S)),
                   int(12 * S), (0, 110, 0))
        else:
            put_cn(img, _edge_txt['bottom'], (int(c * S), H - int(28 * S)),
                   int(12 * S), (0, 110, 0))

    # 场区边框
    cv2.rectangle(img, (0, 0), (W - 1, H - 1), (60, 60, 60), 2)

    # ---- 极淡的角度扇（可选参考层；画在主轨迹下面）----
    for fr in rr.get('fan', []):
        fp = fr['pts']
        if len(fp) >= 2:
            col = (150, 200, 150) if fr['res'] == 'goal' else (196, 196, 196)
            cv2.polylines(img, [np.array([[int(x * S), int(y * S)] for x, y in fp], np.int32)],
                          False, col, 1)

    # ---- 单条虚线轨迹 ----
    pts = rr['pts']
    if len(pts) >= 2:
        if rr['res'] == 'goal':
            col = (0, 160, 0)          # 绿：进洞
        elif rr['stop'] == 'bounce_limit':
            col = (0, 140, 255)        # 橙：反弹用尽停
        elif rr['stop'] == 'wall':
            col = (0, 0, 220)          # 红：撞墙停（max_bounce=0 时的形态）
        else:
            col = (130, 130, 130)      # 灰：步数/点数用尽
        for (a, b) in dash_segments(pts):
            cv2.line(img, (int(a[0] * S), int(a[1] * S)),
                     (int(b[0] * S), int(b[1] * S)), col, max(2, int(2.2 * S)))

        # 碰壁点（用**原始**点列：尾段不算碰壁）——画小圈并标 反弹1/2/3
        br = V.BALL_R
        fw, fh = V.FIELD_W, V.FIELD_H
        raw_pts = rr.get('_raw_pts', pts)
        hits = []
        for (px, py) in raw_pts:
            near = (abs(px - br) < 0.6 or abs(px - (fw - br)) < 0.6 or
                    abs(py - br) < 0.6 or abs(py - (fh - br)) < 0.6)
            if near and (not hits or math.hypot(px - hits[-1][0], py - hits[-1][1]) > 20):
                hits.append((px, py))
        # 去掉最后一个点（若就在壁上也可能是终点）：先把【进洞】情况跳过
        if rr['res'] == 'goal' and hits:
            hits = [h0 for h0 in hits
                    if math.hypot(h0[0] - pts[-1][0], h0[1] - pts[-1][1]) > 30]
        for i, (px, py) in enumerate(hits):
            cv2.circle(img, (int(px * S), int(py * S)), int(7 * S), (0, 100, 255),
                       max(1, int(1.5 * S)))
            put_cn(img, '反弹%d' % (i + 1),
                   (int(px * S) + int(12 * S), int(py * S) - int(20 * S)),
                   int(11 * S), (0, 100, 255))

        # 起点（蓝实心）/ 终点（黑叉）+ 中文标签
        cv2.circle(img, (int(pts[0][0] * S), int(pts[0][1] * S)), int(6 * S), (255, 80, 0), -1)
        ex, ey = int(pts[-1][0] * S), int(pts[-1][1] * S)
        cv2.drawMarker(img, (ex, ey), (40, 40, 40), cv2.MARKER_TILTED_CROSS, int(14 * S), 2)
        put_cn(img, '起点', (int(pts[0][0] * S) + int(10 * S), int(pts[0][1] * S) + int(2 * S)),
               int(11 * S), (200, 60, 0))
        # 端点标签按结果区分：进洞=真终点；反弹上限=只是**显示截断**（不是小球真的停那）
        if rr['res'] == 'goal':
            end_label, end_col = '进洞', (0, 130, 0)
        elif rr['stop'] == 'bounce_limit':
            end_label, end_col = '截断', (40, 40, 40)
        else:
            end_label, end_col = '停止', (40, 40, 40)
        put_cn(img, end_label, (ex + int(10 * S), ey + int(2 * S)), int(11 * S), end_col)

    # ---- 图注（中文）----
    _EC = {'left': '左', 'right': '右', 'top': '上', 'bottom': '下'}
    lines = [
        '弹跳轨迹预览（黄金模型出图；板端 RTL 的 max_bounce 修复待做）',
        '场区 seed=%d  B=%s  最多反弹 %d 次（第 %d 次碰壁停）' % (
            meta['seed'], meta['b_txt'], meta['max_bounce'], meta['max_bounce'] + 1),
        '球位=(%d,%d)  手机角=%.1f°（%s边带→实际方向 %+.1f°）  速度=%.0f px/s%s' % (
            meta['pos'][0], meta['pos'][1], meta['angle'], _EC[meta['edge']],
            meta['ang_real'], meta['speed'], meta['tag']),
        '结果：%s%s' % (meta['verdict'], meta['extra']),
    ]
    for i, t in enumerate(lines):
        put_cn(img, t, (int(16 * S), int(14 * S) + int(32 * S) * i), int(16 * S), (30, 30, 30))
    if meta.get('tail_note'):
        put_cn(img, '注：轨迹按显示上限截断（第3次碰壁+收尾小段）；标"截断"处不是小球真正停下的位置',
               (int(16 * S), int(14 * S) + int(32 * S) * len(lines)), int(13 * S), (120, 120, 120))
    return img


def extend_tail(rr, tail_len, step=2.0):
    """★ 2026-10-04 用户要求：第 3 次碰壁禁**不要切在墙点上**——反射后画一小段收尾；
    若这小段又碰到边界（第 4 次接触）就停在壁点（不再反射、不再延伸）。
    只对 bounce_limit（反弹用尽）生效；返回 True 表示画了尾段。
    方向取自原始轨迹的最后两点（trail_gap=6px，够定方向）；撞的是哪面墙就翻哪个分量。"""
    pts = list(rr['pts'])
    if rr['stop'] != 'bounce_limit' or len(pts) < 3 or tail_len <= 0:
        return False
    rr['_raw_pts'] = pts                       # 碰壁点标注用原始点列
    px, py = pts[-1]
    dx, dy = px - pts[-2][0], py - pts[-2][1]
    br, fw, fh = V.BALL_R, V.FIELD_W, V.FIELD_H
    if abs(px - br) < 0.8 or abs(px - (fw - br)) < 0.8:
        dx = -dx
    if abs(py - br) < 0.8 or abs(py - (fh - br)) < 0.8:
        dy = -dy
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return False
    ux, uy = dx / L, dy / L
    tx, ty = px, py
    for _ in range(max(1, int(tail_len / step))):
        nx_, ny_ = tx + ux * step, ty + uy * step
        if nx_ < br or nx_ > fw - br or ny_ < br or ny_ > fh - br:
            break                              # 尾段又碰到边界 -> 停（第 4 次接触，不再反射）
        tx, ty = nx_, ny_
        pts.append((tx, ty))
    rr['pts'] = pts
    rr['tail_drawn'] = True
    return True


def verdict_text(rr):
    if rr['res'] == 'goal':
        side = '右' if rr['hole']['edge'] == 'right' else '左'
        return '进洞！（%s侧洞口）' % side, '  洞口 y=%d  反弹 %d 次' % (
            round(rr['hole']['c']), rr['bounces'])
    if rr['stop'] == 'bounce_limit':
        return '反弹上限→停', '  碰壁 %d 次（前 %d 次反弹、第 %d 次停下）  终点=(%.0f,%.0f)' % (
            rr['bounces'], rr['bounces'] - 1, rr['bounces'], rr['x'], rr['y'])
    if rr['stop'] == 'wall':
        return '撞墙停（0 反弹的旧口径）', '  终点=(%.0f,%.0f)' % (rr['x'], rr['y'])
    return '未出结论（%s）' % rr['stop'], '  终点=(%.0f,%.0f)' % (rr['x'], rr['y'])


# 边法线基角（与 ball_game.py 第 1373 行 _EDGE_BASE **逐字一致**）：
# 发射实角 = 基角 + 手机输入角（上边带 input=-38.5 => 实角 +51.5，打向右洞）
_EDGE_BASE = {'left': 0.0, 'right': 180.0, 'top': 90.0, 'bottom': -90.0}


def nearest_edge(pos):
    x, y = float(pos[0]), float(pos[1])
    d = {'left': x, 'right': V.FIELD_W - x, 'top': y, 'bottom': V.FIELD_H - y}
    return min(d, key=d.get)


def real_angle(pos, ang_in):
    """手机输入角 -> 发射实角（边法线 + 输入角；与 ball_game 同一条公式）。"""
    a = (_EDGE_BASE[nearest_edge(pos)] + float(ang_in) + 180.0) % 360.0 - 180.0
    return a


def one_shot(world, pos, angle, speed, mb, outdir, tag='', tail=70.0, fan_half=0.0, fan_step=1.0):
    edge = nearest_edge(pos)
    ang_real = real_angle(pos, angle)
    fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks]
    rr = V.lane_fixed(float(pos[0]), float(pos[1]), ang_real, float(speed),
                      charge=1, fields=fields, holes=world.holes, side=0,
                      steps=3000, max_bounce=mb, trail_gap=6.0, max_pts=2000)
    if extend_tail(rr, tail):
        pass
    # 可选：极淡的角度扇参考层（17 路，±fan_half，与 PL 扫描同一套栅格）
    if fan_half > 0:
        fan = []
        n_k = int(round(fan_half / max(fan_step, 1e-9)))
        for k in range(-n_k, n_k + 1):
            a2 = ang_real + k * fan_step
            fan.append(V.lane_fixed(float(pos[0]), float(pos[1]), a2, float(speed),
                                    charge=1, fields=fields, holes=world.holes, side=0,
                                    steps=3000, max_bounce=mb, trail_gap=8.0, max_pts=1500))
        rr['fan'] = fan
    v, extra = verdict_text(rr)
    if rr.get('tail_drawn'):
        extra += '  [第3次碰壁后画收尾小段 %.0fpx]' % tail
    meta = dict(seed=world_seed, b_txt=b_txt, max_bounce=mb, pos=(int(pos[0]), int(pos[1])),
                angle=angle, speed=speed, verdict=v, extra=extra, tag=tag,
                edge=edge, ang_real=ang_real,
                tail_note=bool(rr.get('tail_drawn')))
    img = render(world, rr, meta)
    fn = os.path.join(outdir, 'bounce_%s%s.png' % (
        ('a%+.1f' % angle).replace('.', 'p'), tag))
    # ★ 写盘必须查返回值：看图器开着会锁文件，cv2.imwrite 失败**静默返回 False** ——
    #   2026-10-04 实踩：中文重跑后有两张图没更新，屏幕上还是旧英文版，查了半天。
    if not cv2.imwrite(fn, img):
        raise RuntimeError('写盘失败（文件被看图器锁住/只读？）：%s' % fn)
    print('  手机角=%+7.1f（%s带->实角%+6.1f）  %-40s pts=%d  -> %s'
          % (angle, edge, ang_real, v + extra, len(rr["pts"]), fn))
    return rr


def main():
    ap = argparse.ArgumentParser(description='单虚线弹跳轨迹预览（PC 原型）')
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--b', type=float, default=0.40)
    ap.add_argument('--pos', type=str, default='880,40', help='球位 x,y（游戏坐标）')
    ap.add_argument('--angle', type=float, default=-38.5)
    ap.add_argument('--speed', type=float, default=1630, help='px/s（滑条范围 50~2000）')
    ap.add_argument('--max-bounce', type=int, default=2, help='最多反弹次数（第三次碰壁停=2）')
    ap.add_argument('--sweep', type=str, default=None, help='一串角度，逗号分隔；每个角度一张')
    ap.add_argument('--out', type=str, default=os.path.join(_ROOT, 'logs', 'bounce_preview'))
    ap.add_argument('--fan', type=float, default=0.0, help='极淡扇线半角（度）；0=不画')
    ap.add_argument('--fan-step', type=float, default=1.0, help='扇线角度步进（度）')
    ap.add_argument('--tail', type=float, default=70.0,
                    help='第 3 次碰壁后的收尾小段长度（px，0=不画）')
    args = ap.parse_args()

    global world_seed, b_txt
    world_seed, b_txt = args.seed, ('+/-%.2f' % args.b)
    world = build_world(args.seed, args.b)
    pos = tuple(float(t) for t in args.pos.split(','))
    os.makedirs(args.out, exist_ok=True)

    print('== 单虚线弹跳轨迹预览（黄金模型，max_bounce=%d）==' % args.max_bounce)
    print('  场区 seed=%d  B=%s  洞=%s' % (
        args.seed, b_txt, [(h['edge'], round(h['c']), h['target']) for h in world.holes]))
    if args.sweep:
        for a in [float(t) for t in args.sweep.split(',')]:
            one_shot(world, pos, a, args.speed, args.max_bounce, args.out, tail=args.tail, fan_half=args.fan, fan_step=args.fan_step)
    else:
        one_shot(world, pos, args.angle, args.speed, args.max_bounce, args.out, tail=args.tail, fan_half=args.fan, fan_step=args.fan_step)
    print('  输出目录：%s' % args.out)


if __name__ == '__main__':
    main()
