# -*- coding: utf-8 -*-
"""live_view.py — 电脑端实时预览 + 清晰度评分（调焦用）

连接板子的 live_stream.py，实时显示画面，并在画面角上打出清晰度分数：
    中心清晰度 —— 只看画面中心 1/3 区域（**调焦盯这个**，越大越合焦）
    全图清晰度 —— 整幅的拉普拉斯方差
    高频能量   —— 相邻像素平均差

调焦方法：慢慢转镜头 → 盯着"中心清晰度" → 让这个数字尽量大。
按 s 保存当前帧（含分数）到 captures/，按 q 退出。

用法：
    python live_view.py [板子IP] [端口]
"""

import os
import socket
import struct
import sys
import time

sys.path.insert(0, 'E:/py_pkgs/vision')
sys.path.insert(0, 'E:/py_pkgs/pillow')

import cv2
import numpy as np

HOST_DEF, PORT_DEF = '192.168.2.99', 9999
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'captures')
WIN = 'PYNQ 摄像头实时预览'


def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('连接断开')
        buf += chunk
    return buf


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else HOST_DEF
    port = int(sys.argv[2]) if len(sys.argv) > 2 else PORT_DEF
    print(f'连接板子 {host}:{port} ...')
    s = socket.create_connection((host, port), timeout=5)
    print('已连接。调焦方法：慢慢转镜头，让"中心清晰度"越大越好。s 存图，q 退出。')

    n, t0, fps = 0, time.time(), 0.0
    best = (0.0, None)
    while True:
        size = struct.unpack('<I', recv_exact(s, 4))[0]
        img = cv2.imdecode(np.frombuffer(recv_exact(s, size), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        n += 1
        if n % 10 == 0:
            fps = 10 / max(1e-6, time.time() - t0)
            t0 = time.time()

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        c = gray[h // 3:2 * h // 3, w // 3:2 * w // 3]
        lap_all = cv2.Laplacian(gray, cv2.CV_64F).var()
        lap_c = cv2.Laplacian(c, cv2.CV_64F).var()
        hf = np.abs(np.diff(gray.astype(np.float32), axis=1)).mean()

        if lap_c > best[0]:
            best = (lap_c, img.copy())

        cv2.putText(img, f'中心清晰度 {lap_c:8.0f}   （转镜头让这个数最大）',
                    (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.putText(img, f'全图 {lap_all:8.0f}   高频 {hf:5.2f}   {fps:4.1f} fps',
                    (12, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(img, 's 保存当前帧  q 退出', (12, 104),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        cv2.imshow(WIN, img)
        k = cv2.waitKey(1) & 0xFF
        if k == ord('q'):
            break
        if k == ord('s'):
            os.makedirs(OUT_DIR, exist_ok=True)
            p = os.path.join(OUT_DIR, time.strftime('live_%H%M%S.png'))
            cv2.imwrite(p, img)
            print('已保存:', p)
        try:
            if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break

    if best[1] is not None:
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, 'live_best.png')
        cv2.imwrite(p, best[1])
        print(f'本轮最清晰一帧（中心清晰度 {best[0]:.0f}）已存: {p}')
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
