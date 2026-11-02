# -*- coding: utf-8 -*-
"""frame_dump.py — 原样导出几帧摄像头的**原始字节**，供 PC 侧离线解剖

为什么要在板端导原始字节：颜色不对的根因有好几种（字节序反、字节错位、
传感器其实是 YUV422），在板子上瞎试太慢。原样存下来，PC 上一次把
"偏移 0/1/2... × 交换/不交换 × RGB565/YUV422" 全搜一遍，一次定死。

用法（板上，需先跑过 dvp_probe.py 让传感器健康）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/frame_dump.py [帧数]
输出：
    /boot/frame_raw_0.bin ...   原始字节（2048 x 722，每像素 2 字节）
    /boot/frame_cur_0.png ...   当前驱动解码出来的样子（对照用）
"""

import sys
import time

sys.path.insert(0, '/boot')

import cv2
import numpy as np

from ball_detect import W, H, _make_cap, _init_video

N = 3


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N
    cap = _make_cap()
    _init_video(cap)
    for k in range(n):
        try:
            idx, raw = cap.frame(timeout=3.0)
        except Exception as e:
            print(f'[导出] 第 {k} 帧失败（{e}）')
            continue
        arr = np.ascontiguousarray(np.asarray(raw, dtype=np.uint8))
        p = f'/boot/frame_raw_{k}.bin'
        arr.tofile(p)
        cv2.imwrite(f'/boot/frame_cur_{k}.png', cap.to_bgr(raw))
        print(f'[导出] buf{idx}  shape={arr.shape}  ->  {p}  '
              f'（{arr.size} 字节）', flush=True)
    print('[导出] 驱动的字节序判定 =', getattr(cap, '_swap_decided', None))
    print('[导出] 完。PC 侧跑 frame_analyze.py 解剖')


if __name__ == '__main__':
    main()
