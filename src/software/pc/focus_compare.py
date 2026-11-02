# -*- coding: utf-8 -*-
"""focus_compare.py — 多图清晰度对比（判断调焦是否真的变好了）

对每张图算三个数：
    中心清晰度 —— 中心 1/3 区域的拉普拉斯方差（调焦看这个）
    全图清晰度 —— 整幅的拉普拉斯方差
    高频能量   —— 相邻像素平均绝对差

用法：
    python focus_compare.py 图1.png 图2.png ...
"""

import os
import sys

sys.path.insert(0, 'E:/py_pkgs/vision')

import cv2
import numpy as np


def score(path):
    img = cv2.imread(path)
    if img is None:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    c = g[h // 3:2 * h // 3, w // 3:2 * w // 3]
    return dict(
        center=float(cv2.Laplacian(c, cv2.CV_64F).var()),
        full=float(cv2.Laplacian(g, cv2.CV_64F).var()),
        hf=float(np.abs(np.diff(g.astype(np.float32), axis=1)).mean()),
        mean=float(g.mean()), std=float(g.std()), size=f'{w}x{h}')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    rows = []
    for p in sys.argv[1:]:
        r = score(p)
        name = os.path.basename(p)
        if r is None:
            print(f'{name:30s}  读不到（文件不存在或不是图片）')
            continue
        rows.append((name, r))
        print(f'{name:30s}  中心 {r["center"]:9.1f}   全图 {r["full"]:9.1f}   '
              f'高频 {r["hf"]:6.2f}   均值 {r["mean"]:6.1f}  '
              f'标准差 {r["std"]:5.1f}  {r["size"]}')
    if len(rows) >= 2:
        base = rows[0]
        print(f'\n以 {base[0]} 为基准的倍率：')
        for name, r in rows[1:]:
            if base[1]['center'] > 1e-6:
                print(f'  {name:30s}  中心 x{r["center"] / base[1]["center"]:.2f}   '
                      f'全图 x{r["full"] / base[1]["full"]:.2f}')


if __name__ == '__main__':
    main()
