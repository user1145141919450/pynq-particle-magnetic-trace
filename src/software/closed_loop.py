# -*- coding: utf-8 -*-
"""closed_loop.py — 笔记本端到端演示闭环（M3 逻辑先行验证）

真实流程与上板后完全一致（只差视频源与触发按键）：
  1. 画面左侧自由区用手推红色瓶盖/球，朝虚拟磁场滑去；
  2. 球心越过磁场左边界（入射边）瞬间自动抓拍，取最近 10 帧
     线性拟合出入射速度 v 与角度 θ（非匀速自动拒绝，需重新武装）；
  3. geometry.solve() 计算圆周轨迹与边界关系：穿出=绿 / 相切=橙 / 打回=红；
  4. 轨迹圆弧、参考圆、入射/出射点、v/θ/r/B 标注持续叠加显示；
  5. 抓拍后 v/θ 固定，拖动 B 滑动条 → 轨迹实时重算，绿→橙→红连续切换
     （本项目核心创新点的先行验证）。

操作：
  q 退出 | c 清空轨迹重新武装 | t 翻转电荷转向(turning) | s 截图 | 空格 手动抓拍
  滑动条：B x100（演示磁感应强度 0.05~3.0）、S min / V min（HSV 标定）

结果自动存档（captures/ 目录，比赛"实测比对"证据）：
  抓拍成功 → snap_时间.png（完整叠加画面）+ runs.csv 追加一行（P0/v/θ/B/verdict/r/出射点）
  拖 B 判定切换（绿→橙→红）→ 自动存 verdict_*.png，证据链自动留档供报告

与上板的差异：触发用"自动越线"代替板载 BTN0（上板版本两个都保留）；
视频源 cv2.VideoCapture(0) 换成 PL 帧缓冲。其余 PS 逻辑原样移植。

运行：python closed_loop.py
"""

import os
import sys
import time

# PC 专用：E 盘装的 opencv/numpy（迁移上板前删除本段——板载 cv2 已预装）
_PC_PKGS = [r'E:/py_pkgs/vision', r'E:/py_pkgs/pillow']
for _p in _PC_PKGS:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cv2

from detection import detect_red_ball, red_mask
from velocity import VelocityTracker
from geometry import Field, solve, verdict_color

# 虚拟磁场（像素坐标，按 640x480 设计；left 为入射边界）。
# 若摄像头分辨率不同，首帧按比例缩放。
FIELD = Field(400, 100, 580, 390)
K = 5.0                     # m/q 标定常数：r = k·|v|/B。
# K=5 让手推速度(约7~30px/f)的临界 B 落在滑动条范围(0.05~3.0)内，能扫出完整绿→橙→红
REARM_DIST = 30             # 抓拍失败后，球回到左边界左方 30px 才重新武装
MIN_SPEED = 3.0             # 越线瞬间最低速度门槛 (px/f)，低于视为"停住"不抓拍
WIN = 'closed_loop_demo'
CAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'captures')
CSV_PATH = os.path.join(CAP_DIR, 'runs.csv')
CSV_COLS = ['event', 'time', 'P0x', 'P0y', 'vx', 'vy', 'speed', 'theta',
            'B', 'turning', 'verdict', 'r', 'exit_x', 'exit_y']


def main():
    global FIELD
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    except Exception:
        cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print('[错误] 摄像头打不开。检查 Win11 相机隐私设置 / 是否被其他程序占用')
        sys.exit(1)
    print('推球越过左边界自动抓拍；q退出 c清空 t翻转转向 s截图 空格手动抓拍')
    os.makedirs(CAP_DIR, exist_ok=True)
    csv_new = not os.path.exists(CSV_PATH)
    csv_w = open(CSV_PATH, 'a', newline='', encoding='utf-8')
    if csv_new:
        csv_w.write(','.join(CSV_COLS) + '\n')
        csv_w.flush()

    def write_csv(event, tr, B):
        """把一次事件写入 runs.csv。snapshot 为 (P0, vx, vy) 或 None。"""
        row = {
            'event': event,
            'time': time.strftime('%H:%M:%S'),
            'P0x': f'{snapshot[0][0]:.1f}' if snapshot else '',
            'P0y': f'{snapshot[0][1]:.1f}' if snapshot else '',
            'vx': f'{snapshot[1]:.2f}' if snapshot else '',
            'vy': f'{snapshot[2]:.2f}' if snapshot else '',
            'speed': f'{tr.speed:.2f}' if tr else '',
            'theta': f'{tr.theta_deg:.2f}' if tr else '',
            'B': f'{B:.2f}',
            'turning': str(turning),
            'verdict': tr.verdict if tr else '',
            'r': f'{tr.r:.1f}' if tr else '',
            'exit_x': f'{tr.exit_point[0]:.1f}' if tr and tr.exit_point else '',
            'exit_y': f'{tr.exit_point[1]:.1f}' if tr and tr.exit_point else '',
        }
        csv_w.write(','.join(row[c] for c in CSV_COLS) + '\n')
        csv_w.flush()

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.createTrackbar('B x100', WIN, 100, 300, lambda x: None)
    cv2.createTrackbar('S min', WIN, 100, 255, lambda x: None)
    cv2.createTrackbar('V min', WIN, 60, 255, lambda x: None)

    # 手持瓶盖比滚动的真球抖动大：原型放宽残差到 6px（上板滚球可收紧回默认 2.0）
    tracker = VelocityTracker(maxlen=10, max_residual=6.0)
    turning = 1
    frame_idx = 0
    snapshot = None          # 抓拍结果 (P0, vx, vy)；None = 未抓拍
    snap_flash = 0           # 抓拍成功绿框闪烁剩余帧数
    just_snapped = False     # 本帧刚抓拍成功（用于存档）
    last_verdict = None      # 上一次轨迹判定（判定切换时自动存档）
    n_snaps = 0
    armed = True
    prev = None              # 上一帧球心 (cx, cy)

    while True:
        ok, frame = cap.read()
        if not ok:
            print('[警告] 读帧失败')
            break
        frame_idx += 1
        if frame_idx == 1:   # 首帧按实际分辨率缩放磁场
            sx = frame.shape[1] / 640.0
            sy = frame.shape[0] / 480.0
            FIELD = Field(400 * sx, 100 * sy, 580 * sx, 390 * sy)

        B = cv2.getTrackbarPos('B x100', WIN) / 100.0
        smin = cv2.getTrackbarPos('S min', WIN)
        vmin = cv2.getTrackbarPos('V min', WIN)
        ranges = [((0, smin, vmin), (10, 255, 255)),
                  ((170, smin, vmin), (180, 255, 255))]
        det = detect_red_ball(frame, ranges=ranges)
        mask = red_mask(frame, ranges)
        vis = frame.copy()

        # ---- 球跟踪与自动抓拍 ----
        if det is None:
            tracker.clear()
            prev = None
            status = 'no red ball'
            sc = (60, 60, 220)
        else:
            cx, cy, radius = det
            tracker.push(frame_idx, cx, cy)
            cv2.circle(vis, (int(cx), int(cy)), int(radius), (255, 255, 255), 2)
            cv2.circle(vis, (int(cx), int(cy)), 2, (255, 255, 255), -1)

            # 重新武装：球回到左边界左方足够远
            if cx < FIELD.left - REARM_DIST and snapshot is None:
                armed = True

            # 自动越线抓拍（prev 与当前帧跨过左边界，且 y 落在场内）
            if (snapshot is None and armed and prev is not None
                    and tracker.size >= 5
                    and prev[0] < FIELD.left <= cx
                    and FIELD.top <= cy <= FIELD.bottom):
                res = tracker.fit()
                if not (res and res[4]):
                    res = tracker.fit(tail=5)   # 越线前短段更接近匀速（手推减速兜底）
                if res and res[4] and res[2] >= MIN_SPEED:
                    vx, vy = res[0], res[1]
                    t = (FIELD.left - prev[0]) / (cx - prev[0])
                    P0 = (FIELD.left, prev[1] + t * (cy - prev[1]))
                    snapshot = (P0, vx, vy)
                    snap_flash = 20
                    just_snapped = True
                    n_snaps += 1
                    print('===== 抓拍成功 =====')
                    print(f'[抓拍] P0=({P0[0]:.0f},{P0[1]:.0f}) '
                          f'v=({vx:.1f},{vy:.1f}) |v|={res[2]:.1f} theta={res[3]:.0f} deg')
                else:
                    armed = False
                    reason = ('非匀速' if res and not res[4] else
                              '太慢' if res else '样本不足')
                    print(f'[拒绝] {reason}，把球退回左边界左方重新推')
            prev = (cx, cy)

            res = tracker.fit()
            if res is None:
                status = f'tracking {tracker.size}/10'
                sc = (200, 200, 200)
            else:
                vx, vy, speed, theta, uniform = res
                status = (f'v=({vx:+.0f},{vy:+.0f}) |v|={speed:.0f} px/f '
                          f'theta={theta:+.0f} deg  '
                          f'{"UNIFORM" if uniform else "REJECT"}')
                sc = (0, 200, 0) if uniform else (0, 0, 220)

        # ---- 磁场区域绘制（蓝色深浅随 B） ----
        shade = int(120 + 90 * min(1.0, B / 1.0))
        ov = vis.copy()
        cv2.rectangle(ov, (int(FIELD.left), int(FIELD.top)),
                      (int(FIELD.right), int(FIELD.bottom)),
                      (235 - shade // 4, 235 - shade // 5, shade), -1)
        vis = cv2.addWeighted(ov, 0.45, vis, 0.55, 0)
        cv2.rectangle(vis, (int(FIELD.left), int(FIELD.top)),
                      (int(FIELD.right), int(FIELD.bottom)), (150, 120, 30), 2)
        cv2.line(vis, (int(FIELD.left), int(FIELD.top) - 8),
                 (int(FIELD.left), int(FIELD.bottom) + 8), (60, 60, 220), 3)

        # ---- 抓拍后：轨迹随 B 实时重算与绘制（绿→橙→红） ----
        tr = None
        if snapshot is not None:
            P0, vx, vy = snapshot
            tr = solve(P0, vx, vy, B, FIELD, K, turning)
            col = verdict_color(tr.verdict)
            ox, oy = tr.center
            if 0 < tr.r < 2000:      # 过大的参考圆不画（会糊满画面）
                cv2.circle(vis, (int(ox), int(oy)), int(tr.r), (180, 180, 180), 1)
            if tr.arc_points:
                pts = [(int(x), int(y)) for x, y in tr.arc_points]
                for i in range(len(pts) - 1):
                    cv2.line(vis, pts[i], pts[i + 1], col, 2)
            cv2.circle(vis, (int(P0[0]), int(P0[1])), 5, (0, 0, 255), -1)
            if tr.exit_point:
                ex, ey = (int(tr.exit_point[0]), int(tr.exit_point[1]))
                cv2.drawMarker(vis, (ex, ey), col, cv2.MARKER_TILTED_CROSS, 14, 2)
            info = (f'v={tr.speed:.0f} px/f  theta={tr.theta_deg:+.0f} deg  '
                    f'r={tr.r:.0f} px  B={B:.2f}  turn={turning:+d}  '
                    f'{tr.verdict.upper()}')
            cv2.putText(vis, info, (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
        else:
            msg = ('ARMED 推球越过左边界' if armed else
                   'REARM 把球退回左边界左边再推')
            cv2.putText(vis, msg, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 150, 0) if armed else (0, 0, 220), 2)

        # ---- 状态文字 + 掩膜小窗（左下角） ----
        cv2.putText(vis, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, sc, 2)
        small = cv2.resize(mask, (160, 120))
        h, w = vis.shape[:2]
        vis[h - 120:h, 0:160] = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
        cv2.rectangle(vis, (0, h - 120), (160, h), (100, 100, 100), 1)
        if snap_flash > 0:                    # 抓拍成功：绿框闪烁提示
            cv2.rectangle(vis, (0, 0), (w - 1, h - 1), (0, 255, 0), 3)
            snap_flash -= 1

        # ---- 结果自动存档（比赛"实测比对"证据） ----
        if just_snapped:
            ts = time.strftime('%Y%m%d_%H%M%S')
            png = os.path.join(CAP_DIR, f'snap_{ts}.png')
            cv2.imwrite(png, vis)
            write_csv('snap', tr, B)
            print(f'[存档] {png}')
            print(f'[存档] {CSV_PATH} 追加一行')
            last_verdict = tr.verdict if tr else None
            just_snapped = False
        elif snapshot is not None and tr is not None and tr.verdict != last_verdict:
            ts = time.strftime('%H%M%S')
            png = os.path.join(CAP_DIR,
                               f'verdict_{tr.verdict}_B{int(B * 100):03d}_{ts}.png')
            cv2.imwrite(png, vis)
            write_csv('verdict', tr, B)
            print(f'[存档-判定切换] {os.path.basename(png)}')
            last_verdict = tr.verdict
        cv2.imshow(WIN, vis)

        key = cv2.waitKey(20) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('c'):
            snapshot = None
            tracker.clear()
            armed = True
            prev = None
            last_verdict = None
            print('[清空] 重新武装')
        elif key == ord('t'):
            turning = -turning
            print(f'[翻转] turning={turning:+d}')
        elif key == ord('s'):
            ts = time.strftime('%H%M%S')
            cv2.imwrite('snapshot_frame.png', frame)
            cv2.imwrite('snapshot_mask.png', mask)
            cv2.imwrite(os.path.join(CAP_DIR, f'shot_{ts}.png'), vis)
            print('[保存] snapshot_frame.png / snapshot_mask.png / captures/shot_*.png')
        elif key == 32 and det is not None:   # 空格：自动漏抓时的手动补救
            if FIELD.left - 20 <= cx <= FIELD.left + 30 and FIELD.top <= cy <= FIELD.bottom:
                res = tracker.fit()
                if not (res and res[4]):
                    res = tracker.fit(tail=5)
                if res and res[4] and res[2] >= MIN_SPEED:
                    snapshot = ((FIELD.left, cy), res[0], res[1])
                    snap_flash = 20
                    just_snapped = True
                    n_snaps += 1
                    print('===== 手动抓拍成功 =====')
                    print(f'[手动抓拍] y={cy:.0f} v=({res[0]:.1f},{res[1]:.1f})')
                else:
                    print('[手动抓拍] 速度不可用（非匀速/太慢/样本不足）')
            else:
                print('[手动抓拍] 球不在左边界附近，先滑到边界再按空格')

    cap.release()
    cv2.destroyAllWindows()
    csv_w.close()
    print(f'[记录] 本次共 {n_snaps} 次抓拍，数据与图片都在 captures/ 目录')


if __name__ == '__main__':
    main()
