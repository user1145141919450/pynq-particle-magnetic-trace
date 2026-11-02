#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_map_detect_dbg.py —— 把 TB 导出的逐像素 H/S/V 与 cv2 参考对拍。

用法：python sim/check_map_detect_dbg.py <vector.hex> <dbg.hex>
  vector.hex：11520 个 16 位 RGB565（raster 序，$readmemh 用的那份）
  dbg.hex   ：TB 的 "addr H S V"（每行 4 列十六进制）

判据（第一版）：
  · 三色掩码**逐像素完全一致**（分类口径以 PS 为准）；
  · med/vhi 由 TB 断言（不在这里重复）；
  · H/S 允许 ±1（cv2 的 SIMD 定点近似；方案文档 E1 已预警），只报数量。
退出码：0 = 掩码逐像素一致；1 = 不一致。
"""
import sys

import numpy as np

BLK = 8
BW, BH = 128, 90
N = BW * BH


def load_vector(path):
    vals = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                vals.append(int(line, 16))
    if len(vals) != N:
        raise SystemExit("向量长度不对：%d（应 %d）" % (len(vals), N))
    return np.array(vals, dtype=np.uint16).reshape(BH, BW)


def load_dbg(path):
    vals = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if len(p) == 4:
                vals.append([int(x, 16) for x in p])
    a = np.array(vals, dtype=np.int32)
    if a.shape[0] != N:
        raise SystemExit("调试口行数不对：%d（应 %d）" % (a.shape[0], N))
    if not np.array_equal(a[:, 0], np.arange(N)):
        raise SystemExit("调试口地址顺序不对（不是逐像素递增）")
    return a[:, 1], a[:, 2], a[:, 3]


def main(argv):
    if len(argv) != 2:
        raise SystemExit("用法：check_map_detect_dbg.py <vector.hex> <dbg.hex>")
    import cv2

    raw = load_vector(argv[0])
    r = (((raw >> 11) & 0x1F) << 3).astype(np.uint8)
    g = (((raw >> 5) & 0x3F) << 2).astype(np.uint8)
    b = ((raw & 0x1F) << 3).astype(np.uint8)
    hsv = cv2.cvtColor(np.dstack([b, g, r]), cv2.COLOR_BGR2HSV)
    Hc, Sc, Vc = [hsv[..., i].astype(np.int32).ravel() for i in range(3)]

    H, S, V = [x.astype(np.int32) for x in load_dbg(argv[1])]

    dH, dS, dV = H - Hc, S - Sc, V - Vc
    print("  H 差：%d 个像素，max ±%d" % (int((dH != 0).sum()), int(np.abs(dH).max())))
    print("  S 差：%d 个像素，max ±%d" % (int((dS != 0).sum()), int(np.abs(dS).max())))
    print("  V 差：%d 个像素，max ±%d" % (int((dV != 0).sum()), int(np.abs(dV).max())))

    # PS 的 vhi 来自直方图中位；第一版用 TB 已验证的 72
    vhi = 72
    mr_cv = (((Hc <= 12) | (Hc >= 165)) & (Sc >= 50) & (Vc >= 35))
    mb_cv = ((Hc >= 85) & (Hc <= 135) & (Sc >= 50) & (Vc >= 35))
    mk_cv = ((Vc <= vhi) & (Sc <= 110))
    mr_rt = (((H <= 12) | (H >= 165)) & (S >= 50) & (V >= 35))
    mb_rt = ((H >= 85) & (H <= 135) & (S >= 50) & (V >= 35))
    mk_rt = ((V <= vhi) & (S <= 110))

    dr = int((mr_cv != mr_rt).sum())
    db = int((mb_cv != mb_rt).sum())
    dk = int((mk_cv != mk_rt).sum())
    print("  掩码像素差：red=%d blue=%d black=%d" % (dr, db, dk))
    print("  计数：RTL red=%d blue=%d black=%d ｜ cv2 red=%d blue=%d black=%d"
          % (int(mr_rt.sum()), int(mb_rt.sum()), int(mk_rt.sum()),
             int(mr_cv.sum()), int(mb_cv.sum()), int(mk_cv.sum())))
    if dr or db or dk:
        print(">>> FAIL 掩码逐像素不一致")
        return 1
    print(">>> PASS 掩码逐像素一致（H/S 的 ±1 是 cv2 SIMD 定点差，不影响分类）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
