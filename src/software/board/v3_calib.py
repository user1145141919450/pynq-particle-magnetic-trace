# -*- coding: utf-8 -*-
"""v3_calib.py —— V3 的**场区标定**：把相机画幅里的场区四角映到游戏坐标。

为什么需要它（2026-09-27）：
  `v3_scan.py` 早先用的是"相机画幅 ≙ 场区"的默认假设 —— 相机一拉近景，
  按球半径自动定标后**球位就被映射到贴边**，轨迹缩成一点。标定就是把这个假设
  换成实测：在画面里指出场区（那块白纸/板子）四角，算一个 homography，
  之后球位/速度都按它映射，`--pos/--speed` 那些演示覆盖就不再需要。

用法（板上，两步）：
  1) 抓快照 + 叠坐标网格：
       python3 /home/xilinx/wp/v3_calib.py --shot
     产出 /home/xilinx/wp/calib_shot.png（每 64 像素一条网格线、标注像素坐标）。
     把它 scp 回 PC 看，读出**场区四角**在画面里的像素坐标。
  2) 用四角生成标定：
       python3 /home/xilinx/wp/v3_calib.py --set 150,100,870,95,880,600,140,610
     顺序：左上, 右上, 右下, 左下（画面像素坐标）。产出 v3_calib.json。

  之后 v3_scan.py 会自动读 v3_calib.json（没有就退回"画幅≙场区"的默认假设）。
"""

import os
import sys
import time

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import cv2
import numpy as np

FIELD_W, FIELD_H = 960.0, 540.0
CALIB = '/home/xilinx/wp/v3_calib.json'
SHOT = '/home/xilinx/wp/calib_shot.png'


def grab_one(with_bit=True):
    """抓一张**干净**的全分辨率原图（BGR）。

    ★ 必须过质量门（2026-09-27 实测教训）：第一版直接抓一张就交差，结果抓到
      链路热身时卡死留下的**撕裂帧** —— 画面是花的（彩色乱纹），拿它标定/看图
      全是废的。这里用 V1 那套"地图 vs 黄金块平均"的自检当门，不干净就重抓，
      并且把驱动的字节序判定也一并打出来（解出来花不花要能自证）。

    ★★ 门槛定 95%（2026-09-27 用户问"为什么照片还有花屏"）：
      撕裂有两种 —— **整帧撕裂**（自检掉到个位数，好抓）和**部分撕裂**
      （只有若干行是花的，自检还能到 60~80% ✗）。60% 的门会放进后者，
      交出去的图就带着一条彩虹噪声。给"给人看的图"定 95%，并允许多试几轮。
    """
    if with_bit and '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')
    from ball_detect import _make_cap, _init_video
    from fastmem import FastMem
    from ball_map_detect import (MAP_BASE, CTRL_BASE, BLK, read_map, map_to_bgr,
                                 raw_to_bgr, map_gate)
    caps = _make_cap()
    _init_video(caps)
    swap = 1 if getattr(caps, '_swap_decided', False) else 0
    globals()['_LAST_SWAP'] = swap
    globals()['_LAST_CAP'] = caps
    print('[字节序] 驱动判定 swap=%d' % swap)
    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc:
        for k in range(40):      # 门槛提到 95% 后要多给几次机会
            try:
                _idx, raw = caps.frame(timeout=3.0)
                raw = np.ascontiguousarray(np.asarray(raw, np.uint8)).copy()
            except Exception as e:
                print('[抓帧] 第%d次失败：%s' % (k + 1, str(e)[:80]))
                time.sleep(0.3)
                continue
            buf, _nt, _fc = read_map(mm, mc)
            bs, p0, p1 = map_gate(raw, buf)
            if bs is None:
                continue
            if max(p0, p1) < 95.0:
                print('[抓帧] 第%d次：自检只有 %.1f%%/%.1f%%（撕裂帧）-> 重抓'
                      % (k + 1, p0, p1))
                time.sleep(0.3)
                continue
            print('[抓帧] 第%d次：自检 %.1f%% ✓ 用这一帧' % (k + 1, max(p0, p1)))
            return raw_to_bgr(raw, swap)
    print('=> 40 次都没抓到干净帧（链路在持续吐撕裂帧）')
    return None


def main():
    if '--set' in sys.argv:
        i = sys.argv.index('--set')
        v = [float(t) for t in sys.argv[i + 1].split(',')]
        if len(v) != 8:
            print('★ 需要 8 个数：左上x,左上y, 右上x,右上y, 右下x,右下y, 左下x,左下y')
            return 9
        src = np.array([[v[0], v[1]], [v[2], v[3]], [v[4], v[5]], [v[6], v[7]]], np.float32)
        dst = np.array([[0, 0], [FIELD_W, 0], [FIELD_W, FIELD_H], [0, FIELD_H]], np.float32)
        Hm = cv2.getPerspectiveTransform(src, dst)
        # 校验：把画面中心映射回去看看
        c = cv2.perspectiveTransform(np.array([[[(v[0] + v[4]) / 2, (v[1] + v[5]) / 2]]],
                                             np.float32), Hm)[0][0]
        import json
        with open(CALIB, 'w') as f:
            json.dump(dict(H=Hm.tolist(), src=src.tolist(), field=[FIELD_W, FIELD_H]), f, indent=1)
        print('[标定] 四角 -> %s' % CALIB)
        print('  画面中心映射到场区 (%.0f, %.0f)（应接近 (480,270)）' % (c[0], c[1]))
        print('  四角跨距：上边 %.0f px、下边 %.0f px、左边 %.0f px、右边 %.0f px'
              % (np.hypot(v[2] - v[0], v[3] - v[1]), np.hypot(v[6] - v[4], v[7] - v[5]),
                 np.hypot(v[6] - v[0], v[7] - v[1]), np.hypot(v[4] - v[2], v[5] - v[3])))
        return 0

    bgr = grab_one()
    if bgr is None:
        print('=> 环境问题：抓不到帧')
        return 9
    img = bgr.copy()
    h, w = img.shape[:2]
    # ★ 自动猜场区四角：白纸/白板是画面里最亮的**大块**区域 -> 阈值 + 最大轮廓 + 多边形逼近。
    #   猜出来只为**给人确认**（打印出来），不直接写标定 —— 相机有畸变/桌面有反光时
    #   自动猜会偏，最终以人报的坐标为准。
    if '--auto' in sys.argv:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (9, 9), 0)
        thr = int(max(110, np.percentile(gray, 75)))
        m = (gray > thr).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            peri = cv2.arcLength(c, True)
            ap = cv2.approxPolyDP(c, 0.02 * peri, True)
            print('[自动猜] 阈值 %d，最大亮区面积 %.0f px^2，逼近出 %d 个角'
                  % (thr, cv2.contourArea(c), len(ap)))
            pts = [tuple(int(t) for t in q[0]) for q in ap]
            print('  角点：%s' % (pts,))
            if len(ap) == 4:
                # 按 y 排序分成上下两组，组内按 x 排 -> 左上/右上/右下/左下
                up = sorted(pts, key=lambda t: t[0])[:2]
                dn = sorted(pts, key=lambda t: t[0])[2:]
                tl = min(up, key=lambda t: t[1]); tr = max(up, key=lambda t: t[1])
                bl = min(dn, key=lambda t: t[1]); br = max(dn, key=lambda t: t[1])
                print('  建议：--set %d,%d,%d,%d,%d,%d,%d,%d'
                      % (tl[0], tl[1], tr[0], tr[1], br[0], br[1], bl[0], bl[1]))
            else:
                print('  （不是 4 个角 —— 场区可能有遮挡/反光，请人工读网格坐标）')
        else:
            print('[自动猜] 没找到亮区（阈值 %d 太高？场区不是白的？）' % thr)
    for x in range(0, w, 64):
        cv2.line(img, (x, 0), (x, h), (0, 200, 255) if x % 256 else (0, 0, 255), 1)
        cv2.putText(img, str(x), (x + 3, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    for y in range(0, h, 64):
        cv2.line(img, (0, y), (w, y), (0, 200, 255) if y % 256 else (0, 0, 255), 1)
        cv2.putText(img, str(y), (3, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
    cv2.imwrite(SHOT, img)
    print('[输出] %s（%dx%d，每 64px 一条网格、红线是 256px 的整数倍）' % (SHOT, w, h))
    print('  请读出**场区四角**的像素坐标，然后：')
    print('    python3 /home/xilinx/wp/v3_calib.py --set 左上x,左上y,右上x,右上y,右下x,右下y,左下x,左下y')
    return 0


if __name__ == '__main__':
    sys.exit(main())
