# -*- coding: utf-8 -*-
"""PC 侧直播叠加耗时分解（差距 2 流畅化用；不进 deploy）。

跑法：
    python -I tools/_bench_live_build.py
"""
import cProfile
import os
import pstats
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 顺序：本仓 software/board 优先；deploy 只补 engine.py 这类只在部署包里的文件；
# game_pc 最后（engine.py 的源）。
sys.path.insert(0, os.path.join(ROOT, 'game_pc'))
sys.path.insert(0, os.path.join(ROOT, 'vivado', 'ov5640_pynq', 'deploy'))
sys.path.insert(0, os.path.join(ROOT, 'software', 'board'))

import numpy as np  # noqa: E402

import ball_game as bg  # noqa: E402
import v3_scan  # noqa: E402

W, H = 1024, 720
raw = np.zeros(W * H * 2, np.uint8)
Hm = v3_scan.load_calib(os.path.join(ROOT, 'logs', 'v3_calib_board.json'))
world = v3_scan.build_world(7, 0.40, n_blocks=3)

pts = [(120 + i * 40, 100 + (i % 5) * 60) for i in range(17)]
previews = [dict(pts=pts, color=(0, 230, 0),
                 fan=[(x, y, i % 2 == 0) for i, (x, y) in enumerate(pts)])]
trail = [(200 + i * 2, 300 + i) for i in range(60)]


class T(object):
    def __init__(self, cls, x, y, r, no):
        self.cls = cls
        self.sx = x
        self.sy = y
        self.sr = r
        self.no = no
        self.trail = [(x - i * 2, y + i) for i in range(30)]
        self.confirmed = True


tracks = [T('red', 300, 200, 22, 1), T('blue', 500, 300, 23, 2),
          T('red', 700, 400, 21, 3)]
ball = dict(x=500, y=300, r=23, cls='blue')


def call(scene=True):
    if scene:
        return bg._build_live(raw, 0, Hm, world, ball, trail, 20.0, 1200.0,
                              previews=previews, tracks=tracks, bno1=1, bno2=2,
                              match_line='第 1 局 60s')
    return bg._build_live(raw, 0, None, None, None, [], 20.0, 1200.0)


def run(tag, scene=True, n=8):
    call(scene)                       # 热一遍（_zh_hud 字贴片缓存）
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        img = call(scene)
        ts.append((time.perf_counter() - t0) * 1000.0)
    ts.sort()
    print('%-18s shape=%s  median %.1f ms  min %.1f' %
          (tag, img.shape, ts[len(ts) // 2], ts[0]))


class FakePump(object):
    def __init__(self, w=1280, h=720):
        self.width, self.height = w, h
        self.n_show = 0

    def show(self, img):
        self.n_show += 1
        return {'ms_total': 0.0, 'flow': None}

    def show_rect(self, img, rect):
        self.n_show += 1
        return {'ms_total': 0.0, 'flow': None}


def run_push():
    img = call(True)
    pump = FakePump()
    st = {'prev_s': None, 'cur_s': None, 'buf': None, 't_full': 0.0,
          'n_show_after': -1, 'n_full': 0, 'n_rect': 0, 'n_skip': 0}
    # 1) 首帧整帧
    t0 = time.perf_counter()
    h = bg._live_show(pump, img, st, now=100.0)
    t_full = (time.perf_counter() - t0) * 1000.0
    # 2) 静止帧 -> skip
    t0 = time.perf_counter()
    h = bg._live_show(pump, img, st, now=100.1)
    t_skip = (time.perf_counter() - t0) * 1000.0
    # 3) 只动一小块 -> rect
    img2 = img.copy()
    img2[300:340, 500:560] = 255
    t0 = time.perf_counter()
    h = bg._live_show(pump, img2, st, now=100.2)
    t_rect = (time.perf_counter() - t0) * 1000.0
    # 4) 整帧变化 -> full
    img3 = np.full_like(img, 200)
    t0 = time.perf_counter()
    h = bg._live_show(pump, img3, st, now=100.3)
    t_chg = (time.perf_counter() - t0) * 1000.0
    print('push(full first) %.2f ms | skip %.2f | rect %.2f | full change %.2f'
          % (t_full, t_skip, t_rect, t_chg))


if __name__ == '__main__':
    import cv2
    print('cv2', cv2.__version__, 'numpy', np.__version__)
    print('Hm', None if Hm is None else Hm.shape, 'world blocks',
          len(world.blocks), 'holes', len(world.holes),
          'obs', len(getattr(world, 'obstacles', []) or []),
          'portals', len(getattr(world, 'portals', []) or []))
    run('full_scene')
    run('raw_only', scene=False)
    run_push()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(5):
        call(True)
    pr.disable()
    st = pstats.Stats(pr).sort_stats('cumulative')
    st.print_stats(18)
