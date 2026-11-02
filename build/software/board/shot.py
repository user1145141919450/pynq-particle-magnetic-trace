# -*- coding: utf-8 -*-
"""shot.py — 指定 VSIZE 抓一帧存图（验收 VSIZE 拟合结果用）

为什么不能只看帧率：VSIZE 比真实行数**小**时，一帧传感器画面会被 VDMA 切成
两帧（帧率虚高、画面撕裂）；只能看图确认"一帧 = 一幅完整画面"。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/shot.py 720
输出：/boot/shot_<VSIZE>.png
"""

import sys
import time

sys.path.insert(0, '/boot')

import cv2
import numpy as np

from pynq import MMIO
from vdma_capture import S2mmCapture

W = 1024


def main():
    vsize = int(sys.argv[1]) if len(sys.argv) > 1 else 720
    cap = S2mmCapture(W, vsize)
    cap.start()
    mm3 = MMIO(0x43A30000, 0x10000)
    c0 = mm3.read(0x00, 4)
    mm3.write(0x00, c0 | (1 << 3))
    time.sleep(0.3)
    mm3.write(0x00, c0)
    time.sleep(0.5)
    try:
        cap.clear_errors()
    except Exception:
        pass
    time.sleep(0.3)

    # 丢前两帧（启动瞬态），存第三帧
    bgr = None
    for _ in range(3):
        idx, raw = cap.frame(timeout=5.0)
        bgr = cap.to_bgr(raw)
    p = '/boot/shot_%d.png' % vsize
    cv2.imwrite(p, bgr)
    # 行间误配检测：真正"一帧=一幅画面"时，相邻行高度相关；
    # 若 VSIZE 切错了，某个位置会出现一条明显的**横向断裂**（相关系数骤降）
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    rd = []
    for y in range(1, g.shape[0]):
        a, b = g[y - 1], g[y]
        if a.std() < 1e-3 or b.std() < 1e-3:
            rd.append(1.0)
            continue
        rd.append(float(np.corrcoef(a, b)[0, 1]))
    rd = np.array(rd)
    ymin = int(np.argmin(rd))
    print('[抓图] VSIZE=%d -> %s  均值 %.1f 标准差 %.1f' % (vsize, p, bgr.mean(), bgr.std()))
    print('[行间] 最差相邻行相关 = %.3f @ y=%d（接近 1 = 行间连贯；'
          '某处骤降 = 那里是切帧缝）' % (rd[ymin], ymin + 1))
    print('[行间] 小于 0.5 的行数 = %d / %d' % (int((rd < 0.5).sum()), len(rd)))


if __name__ == '__main__':
    main()
