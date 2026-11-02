# -*- coding: utf-8 -*-
"""mapdet_bench.py — 量清楚 detect_map() 的时间到底花在哪一段。

为什么要有它（2026-09-27，V2 判据通过之后的第一件事）：
  判据过了，但**缩略图检测 20.9ms，预算是 5ms**（游戏要 30fps）。而且候选一多
  就涨到 80~100ms —— 说明既有"固定开销"又有"逐候选开销"，两笔账要分开量。
  不先量就优化，等于猜。

数据用**真实 dump**（/home/xilinx/wp/ballmap_d.bin，23040 字节原始地图字），
这样掩码命中数、连通域个数都和实战一致；没有 dump 就退化成合成图。

用法（板上，不需要 PYNQ、不需要相机/VDMA）：
  /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/wp/mapdet_bench.py \
      [--n 30] [--bin /home/xilinx/wp/ballmap_d.bin]
  ★ dump 从哪来：`ball_map_detect.py --dump`（要开相机）或
    `map_snapshot.py`（**纯 MMIO 只读**，把 PL 里此刻那张图存下来 —— 量性能不必动场景）
"""

import sys
import time

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import cv2
import numpy as np

BW, BH = 128, 90


def load_map(path='/home/xilinx/wp/ballmap_d.bin'):
    try:
        with open(path, 'rb') as f:
            buf = f.read()
        m = np.frombuffer(buf, np.uint16).reshape(BH, BW)
        b = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
        bgr = cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR5652BGR)
        print('[数据] %s（真实 dump）' % path)
        return bgr
    except Exception as e:
        print('[数据] 读不到 dump（%s），改用合成图' % str(e)[:60])
        rng = np.random.RandomState(0)
        img = rng.randint(0, 255, (BH, BW, 3), dtype=np.uint8)
        cv2.circle(img, (64, 45), 12, (40, 40, 200), -1)     # 一个红球
        return img


def _hist_median(v8):
    """用直方图求中位（cv2.calcHist 是 C，比 np.median 的排序便宜）"""
    h = cv2.calcHist([v8], [0], None, [256], [0, 256]).ravel()
    return float(np.searchsorted(np.cumsum(h), v8.size / 2.0))


def bench(name, fn, n=30):
    fn()                                    # 预热（第一次有分配开销）
    t = time.perf_counter()
    for _ in range(n):
        fn()
    ms = (time.perf_counter() - t) / n * 1000.0
    print('  %-34s %7.3f ms' % (name, ms))
    return ms


def main():
    n = 30
    binpath = '/home/xilinx/wp/ballmap_d.bin'
    # ★ 2026-09-29：`--bin` 以前只写在文档里、**代码根本没收**（一直读默认那个旧
    #   dump）—— 量性能最怕这种静默错位：换了个数据源，数字却还是老文件的。
    for i, a in enumerate(sys.argv):
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
        if a == '--bin' and i + 1 < len(sys.argv):
            binpath = sys.argv[i + 1]
    bgr = load_map(binpath)

    print('---- 固定开销（**当前实现**的每一段）----')
    tot = 0.0
    tot += bench('cvtColor BGR2HSV', lambda: cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV), n)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    Vc = hsv[..., 2]
    tot += bench('np.median(Vc)', lambda: float(np.median(Vc)), n)
    tot += bench('cv2.calcHist 求中位（候选替代）', lambda: _hist_median(Vc), n)
    tot += bench('inRange x1（红 0-12）',
                 lambda: cv2.inRange(hsv, (0, 50, 35), (12, 255, 255)), n)
    tot += bench('red = inRange x2 + bitwise_or',
                 lambda: cv2.bitwise_or(cv2.inRange(hsv, (0, 50, 35), (12, 255, 255)),
                                        cv2.inRange(hsv, (165, 50, 35), (179, 255, 255))), n)
    tot += bench('blue inRange', lambda: cv2.inRange(hsv, (95, 50, 35), (135, 255, 255)), n)
    tot += bench('black inRange', lambda: cv2.inRange(hsv, (0, 0, 0), (179, 110, 69)), n)
    m_red = cv2.bitwise_or(cv2.inRange(hsv, (0, 50, 35), (12, 255, 255)),
                           cv2.inRange(hsv, (165, 50, 35), (179, 255, 255)))
    tot += bench('morphologyEx OPEN 3x3',
                 lambda: cv2.morphologyEx(m_red, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), n)
    m8 = cv2.morphologyEx(m_red, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    tot += bench('connectedComponentsWithStats',
                 lambda: cv2.connectedComponentsWithStats(m8, 8), n)
    print('  %-34s %7.3f ms' % ('小计（可解释的部分）', tot))

    print('---- 逐候选开销（classify_map 一件）----')
    from ball_map_detect import classify_map, detect_map
    from ball_detect import _ring_median_V
    # 拿真实候选
    real = detect_map(bgr, _ring_median_V, max_area=1154)
    cands = real[0] if isinstance(real, tuple) else real
    print('  （本帧真实候选 %d 个）' % len(cands))
    if not cands:
        # 这一步的参数随球径变（演示用的是大球），单独扫一遍半径的影响
        print('  （没有真实候选 —— 用合成球量 ring 的半径敏感性）')
        for rr in (5, 12, 29):
            bench('  ring_median_V r=%2d 块' % rr,
                  lambda rr=rr: _ring_median_V(hsv, 64.0, 45.0, float(rr)), n)
    if cands:
        c = cands[0]
        cx, cy, r = c['x'] / 8.0, c['y'] / 8.0, c['r'] / 8.0
        one = bench('classify_map x1（含 ring）',
                    lambda: classify_map(hsv, cx, cy, r, _ring_median_V), n)
        bench('  └ ring_median_V x1', lambda: _ring_median_V(hsv, cx, cy, r), n)
        print('  => %d 个候选 ≈ %.1f ms' % (len(cands), one * len(cands)))
    t = time.perf_counter()
    for _ in range(n):
        detect_map(bgr, _ring_median_V, max_area=1154)
    ms = (time.perf_counter() - t) / n * 1000.0
    print('  %-34s %7.3f ms' % ('detect_map 整体', ms))
    return 0


if __name__ == '__main__':
    sys.exit(main())
