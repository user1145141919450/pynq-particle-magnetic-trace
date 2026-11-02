#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_map_cc.py —— map_cc（形态学 + 8 连通 CCL）候选逐值对拍

用法：python sim/check_map_cc.py <mask.hex> <cand.txt>
  mask.hex：11520 行，每行 1 个十六进制数字（bit0=red, bit1=blue, bit2=black）
  cand.txt：TB 导出的 "cls area sx sy"（每行一个候选）

参考：对三色掩码分别 cv2.morphologyEx(MORPH_OPEN, 3x3) +
      cv2.connectedComponentsWithStats(8)，按 area∈[4,6031] 过滤。
判据：候选多重集合**逐值相等**（cls/area/Σx/Σy）。
"""
import sys

import numpy as np

BW, BH = 128, 90
MIN_AREA, MAX_AREA = 4, 6031


def main(argv):
    if len(argv) != 2:
        raise SystemExit("用法：check_map_cc.py <mask.hex> <cand.txt>")
    import cv2

    vals = []
    with open(argv[0], "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                vals.append(int(line, 16))
    if len(vals) != BW * BH:
        raise SystemExit("掩码长度不对：%d" % len(vals))
    m = np.array(vals, dtype=np.uint8).reshape(BH, BW)

    ker = np.ones((3, 3), np.uint8)
    exp = []
    for cls in range(3):
        mask = ((m >> cls) & 1).astype(np.uint8)
        op = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ker)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(op, 8)
        for i in range(1, n):
            a = int(stats[i, cv2.CC_STAT_AREA])
            if a < MIN_AREA or a > MAX_AREA:
                continue
            ys, xs = np.nonzero(lab == i)
            exp.append((cls, a, int(xs.sum()), int(ys.sum())))

    got = []
    with open(argv[1], "r", encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if len(p) == 4:
                got.append(tuple(int(x) for x in p))

    print("  参考 %d 个候选 ｜ PL %d 个候选" % (len(exp), len(got)))
    ok = sorted(exp) == sorted(got)
    if not ok:
        se, sg = sorted(exp), sorted(got)
        for i in range(max(len(se), len(sg))):
            e = se[i] if i < len(se) else None
            g = sg[i] if i < len(sg) else None
            mark = "" if e == g else "   <-- 不一致"
            print("   [%2d] ref=%s  pl=%s%s" % (i, e, g, mark))
        print(">>> FAIL 候选不一致")
        return 1
    print(">>> PASS 候选逐值一致（cls/area/Σx/Σy）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
