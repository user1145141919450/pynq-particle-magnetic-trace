# -*- coding: utf-8 -*-
"""live_demo.py — 笔记本摄像头实时红球检测 + 速度提取（视觉原型演示）

用途：
  1. 用真实摄像头验证 detection.py 的 HSV 阈值（滑动条现场标定 S/V 下限）；
  2. 手持红球/瓶盖匀速滑过画面，验证 velocity.py 的测速与匀速性校验；
  3. 标定满意后把 S/V 下限写回 detection.py 的 RED_RANGES（上板前固化）。

操作：
  q     退出
  c     清空速度缓冲（每次换一种推法前按一下）
  s     保存截图（snapshot_frame.png 原图 / snapshot_mask.png 掩膜），
        并把检测到的球区 HSV 均值打印到终端（远程调阈值用）
  滑动条：S min / V min（只调这两个即可，H 双区间固定）

运行：python live_demo.py（自动找 E 盘依赖；上板前删除文件头的 E 盘路径段）
"""

import os
import sys

# PC 专用：opencv/numpy 装在 E 盘（迁移上板前删除本段——板载 cv2 已预装）
_PC_PKGS = [r'E:/py_pkgs/vision', r'E:/py_pkgs/pillow']
for _p in _PC_PKGS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2

from detection import detect_red_ball, red_mask
from velocity import VelocityTracker

WIN = 'red_ball_demo'


def open_camera():
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    except Exception:
        cap = cv2.VideoCapture(0)
    return cap


def main():
    cap = open_camera()
    if not cap.isOpened():
        print('[错误] 摄像头打不开。检查：')
        print('  1. Win11 设置 → 隐私和安全性 → 相机 → 允许桌面应用访问相机')
        print('  2. 摄像头是否被其他程序（视频会议/微信视频）占用')
        sys.exit(1)
    print('q=退出 c=清空缓冲 s=截图 滑动条调 S/V 下限')
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.createTrackbar('S min', WIN, 100, 255, lambda x: None)
    cv2.createTrackbar('V min', WIN, 60, 255, lambda x: None)

    tracker = VelocityTracker(maxlen=10)
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            print('[警告] 读帧失败')
            break
        frame_idx += 1

        smin = cv2.getTrackbarPos('S min', WIN)
        vmin = cv2.getTrackbarPos('V min', WIN)
        ranges = [((0, smin, vmin), (10, 255, 255)),
                  ((170, smin, vmin), (180, 255, 255))]
        det = detect_red_ball(frame, ranges=ranges)
        mask = red_mask(frame, ranges)

        vis = frame.copy()
        if det is None:
            tracker.clear()
            cv2.putText(vis, 'no red ball', (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 220), 2)
        else:
            cx, cy, radius = det
            tracker.push(frame_idx, cx, cy)
            cv2.circle(vis, (int(cx), int(cy)), int(radius), (0, 200, 0), 2)
            cv2.line(vis, (int(cx) - 8, int(cy)), (int(cx) + 8, int(cy)),
                     (0, 200, 0), 2)
            cv2.line(vis, (int(cx), int(cy) - 8), (int(cx), int(cy) + 8),
                     (0, 200, 0), 2)
            res = tracker.fit()
            if res is None:
                status = f'tracking {tracker.size}/10 frames'
                color = (0, 0, 0)
            else:
                vx, vy, speed, theta, uniform = res
                status = (f'v=({vx:+.1f},{vy:+.1f}) |v|={speed:.1f} px/f '
                          f'theta={theta:+.1f} deg  '
                          f'{"UNIFORM" if uniform else "REJECT non-uniform"}')
                color = (0, 180, 0) if uniform else (0, 0, 220)
            cv2.putText(vis, status, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

        # 右侧并排显示掩膜（白=识别为红，观察背景有没有被误框）
        vis = cv2.hconcat([vis, cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)])
        cv2.imshow(WIN, vis)

        key = cv2.waitKey(20) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('c'):
            tracker.clear()
            print('[清空] 速度缓冲')
        elif key == ord('s'):
            cv2.imwrite('snapshot_frame.png', frame)
            cv2.imwrite('snapshot_mask.png', mask)
            print('[保存] snapshot_frame.png / snapshot_mask.png')
            if det is not None:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                cx, cy, radius = det
                r = max(2, int(radius * 0.6))
                x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
                x1 = min(hsv.shape[1], int(cx) + r)
                y1 = min(hsv.shape[0], int(cy) + r)
                region = hsv[y0:y1, x0:x1].reshape(-1, 3)
                h = region[:, 0].mean()
                s = region[:, 1].mean()
                v = region[:, 2].mean()
                print(f'[球区HSV均值] H={h:.0f} S={s:.0f} V={v:.0f}'
                      f'（当前阈值 S>{smin} V>{vmin}）')
            else:
                print('[截图] 当前没有检测到红球')

    cap.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
