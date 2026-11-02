# -*- coding: utf-8 -*-
"""map_detect_compare.py —— PL 检测 v0 与 PS 参考的**同帧对拍**（只读，不动相机）

做法：
  1) 读一次 PL 的 SEQ；
  2) 用 map_snapshot 同一口径读 128x90 缩略图（读数前后 frame_cnt 一致）；
  3) 再读一次 PL 的 SEQ —— 若没变，说明这张图就是 PL 刚处理完的那类帧；
  4) 用 ball_map_detect 的同一套阈值在 PS 上算 med/vhi/三色计数，与 PL 比。

判据（v0）：
  · med/vhi 必须相等；
  · 三色计数允许差 1~2 个像素（PL 的 S 是数学精确整数式，cv2 是 SIMD 定点，
    实拍快照上 S 有约 1.7% 像素 ±1；远离阈值时不影响，但边界像素可能差）。

用法（板上 root + 登录壳）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u map_detect_compare.py"
退出码：0=一致；1=不一致；9=环境问题（读不到帧/PL 版本不对）。
"""
import sys
import time

import numpy as np

sys.path.insert(0, '/home/xilinx/wp')

MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
MAP_BYTES = 128 * 90 * 2

from map_detect_pl import MapDetect, VER_V0, VER_V1


def read_map(fm_map, fm_ctrl, tries=8):
    buf, ok, cnt = None, False, -1
    for _ in range(tries):
        a = fm_ctrl.rd_u32(0x08)
        buf = fm_map.rd_u8_copy(0, MAP_BYTES)
        b = fm_ctrl.rd_u32(0x08)
        if a == b:
            ok, cnt = True, a
            break
        time.sleep(0.01)
    return buf, cnt, ok


def ps_reference(raw):
    """与 ball_map_detect.detect_map 前几行同口径（只算计数，不做 CCL/复判）。

    ★ 565 解码用手工位域（r=<<3 / g=<<2 / b=<<3）——与 2026-10-20 的 PS 基准
      快照同口径，也与 RTL 的 f_r/f_g/f_b 一字不差。不用 cv2 的 BGR5652BGR：
      各版本对输入通道数/字节序的要求不一样（本机 OpenCV 5 就拒单通道 uint16）。
    """
    import cv2
    m = np.frombuffer(raw, np.uint16).reshape(90, 128)
    r = (((m >> 11) & 0x1F) << 3).astype(np.uint8)
    g = (((m >> 5) & 0x3F) << 2).astype(np.uint8)
    b = ((m & 0x1F) << 3).astype(np.uint8)
    bgr = np.dstack([b, g, r])
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = [hsv[..., i].astype(np.int32) for i in range(3)]
    hist = np.bincount(v.ravel(), minlength=256)
    med = int(np.searchsorted(np.cumsum(hist), v.size / 2.0))
    v_thr = max(70.0, 0.55 * med)
    vhi = int(max(0, min(255, int(np.ceil(v_thr)) - 1)))
    red = int((((h <= 12) | (h >= 165)) & (s >= 50) & (v >= 35)).sum())
    blue = int(((h >= 85) & (h <= 135) & (s >= 50) & (v >= 35)).sum())
    black = int(((v <= vhi) & (s <= 110)).sum())
    return dict(med=med, vhi=vhi, red=red, blue=blue, black=black)


def main():
    try:
        from fastmem import FastMem
    except Exception as e:
        print('!! import fastmem 失败：%s' % e)
        return 9

    md = MapDetect()
    fm_map = FastMem(MAP_BASE, 0x8000)
    fm_ctrl = FastMem(CTRL_BASE, 0x1000)
    try:
        rd = md.read()
        if rd['version'] not in (VER_V0, VER_V1):
            print('!! VERSION=0x%08X（应 v0=0x%08X / v1=0x%08X）——bit 不对或地址不对'
                  % (rd['version'], VER_V0, VER_V1))
            return 9
        if not md.status()['en']:
            print('!! en=0；先跑 map_detect_pl.py on')
            return 9

        for attempt in range(5):
            seq0 = md.read()['seq']
            raw, cnt, ok = read_map(fm_map, fm_ctrl)
            if not ok:
                print('!! 缩略图一致性读失败（frame_cnt 一直在变）')
                time.sleep(0.1)
                continue
            pl = md.read()
            if pl['seq'] != seq0:
                print('  （第 %d 次：快照期间 PL 又处理了 %d 帧，重来）'
                      % (attempt + 1, pl['seq'] - seq0))
                time.sleep(0.05)
                continue
            break
        else:
            print('!! 5 次都没拿到稳定快照')
            return 1

        ps = ps_reference(raw)
        print('[对比] frame_cnt=%d PL.SEQ=%d' % (cnt, pl['seq']))
        print('[对比]          med  vhi  red blue black')
        print('[对比] PS   : %5d %4d %4d %4d %5d'
              % (ps['med'], ps['vhi'], ps['red'], ps['blue'], ps['black']))
        print('[对比] PL   : %5d %4d %4d %4d %5d'
              % (pl['med'], pl['vhi'], pl['red'], pl['blue'], pl['black']))
        bad = 0
        for k in ('med', 'vhi'):
            if ps[k] != pl[k]:
                print('!! %s 不等（PS=%d PL=%d）' % (k, ps[k], pl[k]))
                bad += 1
        for k in ('red', 'blue', 'black'):
            d = pl[k] - ps[k]
            if abs(d) > 2:
                print('!! %s 差 %d（PS=%d PL=%d）' % (k, d, ps[k], pl[k]))
                bad += 1
        if bad:
            print('>>> FAIL PL/PS 不一致（%d 项）' % bad)
            return 1
        print('>>> PASS PL 检测 v0 与 PS 参考同帧一致')
        return 0
    finally:
        fm_map.close()
        fm_ctrl.close()
        md.close()


if __name__ == '__main__':
    sys.exit(main())
