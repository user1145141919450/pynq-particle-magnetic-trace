# -*- coding: utf-8 -*-
"""v3_calib_view.py —— 标定**验收图**：把标定到底把哪儿当地场、球在游戏里哪儿，画出来。

为什么要有它（2026-09-27 用户反馈"我没看到你摆的场和你的标定"）：
  报一串坐标人是看不出对错的 —— 必须**画出来**：
    · 左图：相机原图 + 我在画面里定的场区四边形（四角标 A/B/C/D + 坐标）
    · 右图：**按标定 warp 过去的游戏视角**（960x540）—— 这才是游戏"看到"的场区，
            球映射到哪儿、场区边缘在画面哪一条线上，一眼可辨
  两图都标出球的检测位置；右图再画上网格，方便对准。

用法（板上）：
  python3 /home/xilinx/wp/v3_calib_view.py [--nobit]
产出：/home/xilinx/wp/calib_check_cam.png（相机视角）
      /home/xilinx/wp/calib_check_game.png（游戏视角）
"""

import os
import sys

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import cv2
import numpy as np

FIELD_W, FIELD_H = 960, 540
CALIB = '/home/xilinx/wp/v3_calib.json'


def main():
    import v3_calib as VC           # 复用它的"抓干净帧"（带质量门）
    from fastmem import FastMem
    from ball_map_detect import (MAP_BASE, CTRL_BASE, BLK, read_map, map_to_bgr,
                                 detect_map, detect_map_bg, _ring_median_fast, BG_MAP,
                                 map_gate, raw_to_bgr)

    H = None
    src = None
    try:
        import json
        with open(CALIB) as f:
            d = json.load(f)
        H = np.array(d['H'], np.float64)
        src = np.array(d['src'], np.float32)
    except Exception as e:
        print('★ 没有标定文件（%s）—— 先跑 v3_calib.py --set' % str(e)[:60])
        return 9

    # ★ 用自检把两路字节序都定下来（别写死：实测驱动会判不出来，退回 swap=0 就解错颜色）
    swap = 1 if getattr(VC, '_LAST_SWAP', None) else 0
    bgr = VC.grab_one()
    if bgr is None:
        print('=> 环境问题：抓不到干净帧')
        return 9
    swap = 1 if getattr(VC, '_LAST_SWAP', None) else 0
    cam = bgr.copy()
    h, w = cam.shape[:2]

    # 左图：场区四边形 + 四角
    pts = [(int(src[i][0]), int(src[i][1])) for i in range(4)]
    for i in range(4):
        cv2.line(cam, pts[i], pts[(i + 1) % 4], (0, 255, 0), 2)
    for name, p_ in zip('ABCD', pts):
        cv2.circle(cam, p_, 7, (0, 0, 255), -1)
        cv2.putText(cam, '%s(%d,%d)' % (name, p_[0], p_[1]), (p_[0] + 8, p_[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.putText(cam, 'A=TL B=TR C=BR D=BL   (this quad -> game field 960x540)',
                (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 0), 2)

    # 球检测（缩略图 + 背景差分那套；和 v3_scan 一致）
    ball = None
    bg = None
    try:
        bg = np.load(BG_MAP)
    except Exception:
        pass
    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc:
        buf, _nt, _fc = read_map(mm, mc)
        # 地图显示字节序 = 原图 swap ^ 实测字序关系（与 ball_map_detect 同一套）
        mdisp = swap
        try:
            _idx2, _raw2 = VC._LAST_CAP.frame(timeout=2.0)
            _raw2 = np.ascontiguousarray(np.asarray(_raw2, np.uint8)).copy()
            bs, p0, p1 = map_gate(_raw2, buf)
            if bs is not None:
                mdisp = swap ^ bs
                print('[字节序] 原图 swap=%d，地图字序关系=%d -> 地图显示 swap=%d'
                      % (swap, bs, mdisp))
        except Exception as e:
            print('[字节序] 地图自检跳过（%s）' % str(e)[:50])
        mb = map_to_bgr(buf, mdisp)
        if bg is not None:
            det, _ = detect_map_bg(mb, bg, _ring_median_fast, max_area=6031)
        else:
            det, _ = detect_map(mb, _ring_median_fast, max_area=6031)
        if det:
            ball = max(det, key=lambda b: b.get('area', 0.0))
    if ball is not None:
        cv2.drawMarker(cam, (int(ball['x']), int(ball['y'])), (255, 0, 255),
                       cv2.MARKER_CROSS, 30, 3)
        cv2.circle(cam, (int(ball['x']), int(ball['y'])), 12, (255, 0, 255), 2)

    # 右图：按标定 warp 到游戏视角
    game = cv2.warpPerspective(bgr, H, (FIELD_W, FIELD_H))
    for x in range(0, FIELD_W, 60):
        cv2.line(game, (x, 0), (x, FIELD_H), (170, 170, 170), 1)
    for y in range(0, FIELD_H, 60):
        cv2.line(game, (0, y), (FIELD_W, y), (170, 170, 170), 1)
    if ball is not None:
        p = cv2.perspectiveTransform(
            np.array([[[float(ball['x']), float(ball['y'])]]], np.float64), H)[0][0]
        bx, by = float(p[0]), float(p[1])
        cv2.drawMarker(game, (int(bx), int(by)), (255, 0, 255), cv2.MARKER_CROSS, 34, 3)
        cv2.circle(game, (int(bx), int(by)), 14, (255, 0, 255), 2)
        inside = 0 <= bx < FIELD_W and 0 <= by < FIELD_H
        cv2.putText(game, 'ball -> game (%.0f,%.0f) %s' % (bx, by, 'OK' if inside else 'OUT!'),
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2)
        print('[球] 画面(%d,%d) -> 游戏(%.0f,%.0f) %s'
              % (ball['x'], ball['y'], bx, by, '场内' if inside else '★在场外'))
    cv2.putText(game, 'game view (960x540) via calibration', (10, FIELD_H - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 255, 0), 2)

    outdir = '/home/xilinx/wp' if os.path.isdir('/home/xilinx/wp') else '/boot'
    cv2.imwrite(os.path.join(outdir, 'calib_check_cam.png'), cam)
    cv2.imwrite(os.path.join(outdir, 'calib_check_game.png'), game)
    print('[输出] %s/calib_check_cam.png（相机视角+场区四边形）' % outdir)
    print('[输出] %s/calib_check_game.png（标定后的游戏视角）' % outdir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
