# -*- coding: utf-8 -*-
"""map_detect_candidates.py —— PL 检测 v1（候选统计）上板验收

做三层对拍（同一帧；快照前后 frame_cnt/SEQ 一致才用）：
  A) PL 候选统计 vs PS 直接 CCL（掩码->OPEN->8连通）的统计：
     候选多重集合逐值（cls/area/Σx/Σy）相等；
  B) PL 统计 + PS classify_map（detect_map_from_stats） vs 全 PS detect_map：
     最终候选同口径（位置/半径 0.5px 容差 = PL 整数 Σ 的量化）；
  C) 打印最终候选（cls/x/y/r/f/area），供现场目检。

用法（板上 root + 登录壳）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u map_detect_candidates.py"
退出码：0=全对；1=不一致；9=环境/版本问题。
"""
import sys

import numpy as np

sys.path.insert(0, '/home/xilinx/wp')

MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
MAP_BYTES = 128 * 90 * 2
MIN_AREA, MAX_AREA = 4, 6031


def read_map(fm_map, fm_ctrl, tries=8):
    buf, cnt = None, -1
    for _ in range(tries):
        a = fm_ctrl.rd_u32(0x08)
        buf = fm_map.rd_u8_copy(0, MAP_BYTES)
        b = fm_ctrl.rd_u32(0x08)
        if a == b:
            return buf, a
    return buf, cnt


def map_to_bgr(raw):
    m = np.frombuffer(raw, np.uint16).reshape(90, 128)
    r = (((m >> 11) & 0x1F) << 3).astype(np.uint8)
    g = (((m >> 5) & 0x3F) << 2).astype(np.uint8)
    b = ((m & 0x1F) << 3).astype(np.uint8)
    return np.dstack([b, g, r])


def ps_stats(bgr):
    """PS 直接跑 掩码->OPEN->8连通，返回 [(cls,area,sx,sy)]（与 PL 同口径）。"""
    import cv2
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = [hsv[..., i].astype(np.int32) for i in range(3)]
    med = int(np.searchsorted(np.cumsum(np.bincount(v.ravel(), minlength=256)),
                              v.size / 2.0))
    vhi = int(max(0, min(255, int(np.ceil(max(70.0, 0.55 * med))) - 1)))
    masks = (
        (((h <= 12) | (h >= 165)) & (s >= 50) & (v >= 35)).astype(np.uint8),
        ((h >= 85) & (h <= 135) & (s >= 50) & (v >= 35)).astype(np.uint8),
        ((v <= vhi) & (s <= 110)).astype(np.uint8),
    )
    ker = np.ones((3, 3), np.uint8)
    out = []
    for cls, mk in enumerate(masks):
        op = cv2.morphologyEx(mk, cv2.MORPH_OPEN, ker)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(op, 8)
        for i in range(1, n):
            a = int(stats[i, cv2.CC_STAT_AREA])
            if a < MIN_AREA or a > MAX_AREA:
                continue
            ys, xs = np.nonzero(lab == i)
            out.append((cls, a, int(xs.sum()), int(ys.sum())))
    return out


def main():
    try:
        import cv2  # noqa: F401
        from fastmem import FastMem
        import ball_map_detect as bmd
        from map_detect_pl import MapDetect, VER_V1
    except Exception as e:
        print('!! import 失败：%s' % e)
        return 9

    md = MapDetect()
    fm_map = FastMem(MAP_BASE, 0x8000)
    fm_ctrl = FastMem(CTRL_BASE, 0x1000)
    try:
        rd = md.read()
        if rd['version'] != VER_V1:
            print('!! VERSION=0x%08X（需要 v1=0x%08X）——bit 不对'
                  % (rd['version'], VER_V1))
            return 9
        if not md.status()['en']:
            print('!! en=0；先跑 map_detect_pl.py on')
            return 9

        for attempt in range(5):
            raw, cnt = read_map(fm_map, fm_ctrl)
            pl, ovf, lovf, seq = md.read_candidates()
            if cnt < 0 or seq < 0:
                print('  （第 %d 次：快照/候选读不稳定，重来）' % (attempt + 1))
                continue
            break
        else:
            print('!! 5 次都没拿到稳定快照/候选')
            return 1

        bgr = map_to_bgr(raw)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        pstats = ps_stats(bgr)
        pl_t = sorted((c['cls'], c['area'], c['sx'], c['sy']) for c in pl)
        ps_t = sorted(pstats)

        print('[候选] frame_cnt=%d PL.SEQ=%d N=%d cand_ovf=%d label_ovf=%d'
              % (cnt, seq, len(pl), ovf, lovf))
        print('[候选] A) PL 统计 vs PS CCL 统计：PL=%d PS=%d' % (len(pl_t), len(ps_t)))
        okA = (pl_t == ps_t)
        if not okA:
            for i in range(max(len(pl_t), len(ps_t))):
                a = pl_t[i] if i < len(pl_t) else None
                b = ps_t[i] if i < len(ps_t) else None
                print('   [%2d] PL=%s  PS=%s%s' % (i, a, b, '' if a == b else '   <-- 不一致'))
        print('[候选] A) %s' % ('PASS 逐值一致' if okA else 'FAIL'))

        # B) 混合口径 vs 全 PS detect_map
        stats = [dict(cls=c['cls'], area=c['area'], sx=c['sx'], sy=c['sy']) for c in pl]
        mix, _ = bmd.detect_map_from_stats(stats, hsv, bmd._ring_median_fast)
        ref, _ = bmd.detect_map(bgr, bmd._ring_median_fast, max_area=MAX_AREA)
        okB = (len(mix) == len(ref))
        if okB:
            for d in ref:
                hit = [e for e in mix if e['cls'] == d['cls']
                       and abs(e['x'] - d['x']) <= 0.5
                       and abs(e['y'] - d['y']) <= 0.5
                       and abs(e['r'] - d['r']) <= 0.5
                       and e['area'] == d['area']
                       and abs(e['f'] - d['f']) <= 0.02]
                okB = okB and len(hit) == 1
        print('[候选] B) 混合口径 vs detect_map：mix=%d ref=%d  %s'
              % (len(mix), len(ref), 'PASS' if okB else 'FAIL'))
        print('[候选] C) 最终候选：')
        for d in mix:
            print('   %-5s x=%7.2f y=%7.2f r=%6.2f f=%.3f area=%d'
                  % (d['cls'], d['x'], d['y'], d['r'], d['f'], d['area']))

        if okA and okB:
            print('>>> PASS PL 检测 v1（候选统计与 PS 逐值一致，混合口径同 detect_map）')
            return 0
        print('>>> FAIL（A=%s B=%s）' % (okA, okB))
        return 1
    finally:
        fm_map.close()
        fm_ctrl.close()
        md.close()


if __name__ == '__main__':
    sys.exit(main())
