# -*- coding: utf-8 -*-
"""line_engine_check.py —— 把 xsim 仿真写出的 line_writes.txt 与参考 DDA 逐笔对拍。

用法（在 vivado/ov5640_pynq/rtl/v3_bundle 跑完 xsim 后）：
    python tools/line_engine_check.py [line_writes.txt]

参考模型 = RTL 里的整数 DDA（主轴每步必进一格，不产生重复点）：
    ex += ax; if (ex >= steps) { x += sx; ex -= steps; }
    ey += ay; if (ey >= steps) { y += sy; ey -= steps; }
误差累积/越界丢弃/像素序号（y*1280+x）与 RTL 完全同口径。
"""
import io
import os
import sys

W, H = 1280, 720
FB_BASE = 0x10000000

# 与 tb_line_engine.v 的 8 条线逐条对应
LINES = [
    (100, 100, 200, 150, 0x0000FF),
    (0, 0, 1279, 719, 0x00FF00),
    (640, 0, 640, 719, 0xFF0000),
    (0, 360, 1279, 360, 0x123456),
    (1000, 700, 900, 650, 0xABCDEF),
    (1300, 100, 1400, 200, 0x0000FF),
    (1200, 700, 1400, 800, 0xFFFFFF),
    (500, 500, 500, 500, 0x00FF00),
]


def bgr_to_word(col):
    """{B,G,R} 24 位 -> framebuffer 32 位字 (R<<16)|(B<<8)|G（与 line_engine.v 一致）"""
    r = col & 0xFF
    g = (col >> 8) & 0xFF
    b = (col >> 16) & 0xFF
    return (r << 16) | (b << 8) | g


def dda_points(x0, y0, x1, y1):
    ax, ay = abs(x1 - x0), abs(y1 - y0)
    steps = max(ax, ay)
    sx = 1 if x1 > x0 else (-1 if x1 < x0 else 0)
    sy = 1 if y1 > y0 else (-1 if y1 < y0 else 0)
    x, y, ex, ey = x0, y0, 0, 0
    pts = []
    for i in range(steps + 1):
        if 0 <= x < W and 0 <= y < H:
            pts.append(y * W + x)
        if i < steps:
            if ex + ax >= steps:
                ex = ex + ax - steps
                x += sx
            else:
                ex += ax
            if ey + ay >= steps:
                ey = ey + ay - steps
                y += sy
            else:
                ey += ay
    return pts


def parse(path):
    groups = []
    cur = None
    with io.open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('LINE'):
                cur = []
                groups.append(cur)
                continue
            parts = line.split()
            if len(parts) != 2 or cur is None:
                raise SystemExit('坏行：%r' % line)
            cur.append((int(parts[0], 16), int(parts[1], 16)))
    return groups


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'line_writes.txt'
    if not os.path.exists(path):
        raise SystemExit('找不到 %s' % path)
    groups = parse(path)
    print('读到 %d 组（期望 %d 组）' % (len(groups), len(LINES)))
    if len(groups) != len(LINES):
        raise SystemExit('组数不对')

    bad = 0
    total = 0
    for i, ((x0, y0, x1, y1, col), got) in enumerate(zip(LINES, groups)):
        exp = dda_points(x0, y0, x1, y1)
        word = bgr_to_word(col)
        exp_list = [(FB_BASE + 4 * p, word) for p in exp]
        total += len(exp_list)
        if len(got) != len(exp_list):
            print('LINE %d 笔数不符：实际 %d / 期望 %d' % (i, len(got), len(exp_list)))
            bad += 1
            continue
        diff = [k for k, (a, b) in enumerate(zip(got, exp_list)) if a != b]
        if diff:
            k = diff[0]
            print('LINE %d 有 %d 笔不一致；第一处 #%d 实际 %08x %08x / 期望 %08x %08x'
                  % (i, len(diff), k, got[k][0], got[k][1], exp_list[k][0], exp_list[k][1]))
            bad += 1
        else:
            print('LINE %d  %4d 笔  逐笔一致 OK' % (i, len(got)))
    print('----')
    print('总像素 %d，失败组 %d' % (total, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
