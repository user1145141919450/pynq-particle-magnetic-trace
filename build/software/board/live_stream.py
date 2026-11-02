# -*- coding: utf-8 -*-
"""live_stream.py — 板端实时推流（调焦 / 调试用）

抓一帧 → JPEG 编码 → TCP 推到电脑。配合 PC 侧 live_view.py 使用：
电脑上开一个实时窗口，画面角上带清晰度分数，转镜头时看着分数调焦。

用法（板上，需先跑过 dvp_probe.py 让传感器健康）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/live_stream.py [端口]

注意：本脚本**不重下 bit**（noprog 接力），只启动 VDMA 并推流。
"""

import os
import socket
import struct
import sys
import time

sys.path.insert(0, '/boot')
os.environ.setdefault('PYNQ_JUPYTER_NOTEBOOKS', '/home/xilinx/jupyter_notebooks')

import cv2
from vdma_capture import S2mmCapture

PORT_DEF = 9999
W, H = 1024, 720                    # 帧高 720 是实测值（722 会撕裂+丢帧）


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT_DEF
    print('[推流] 启动 VDMA 采集（先等 1s 让传感器跑完 SCCB 配置）...', flush=True)
    time.sleep(1.0)
    cap = S2mmCapture(W, H)
    cap.start()
    # 与 run_selfcheck 同款：脉冲 ctrl[3] 复位视频域 —— "sink 先就绪、源后开"，
    # 否则通道会在半帧中间武装，首帧全黑且后续可能不再出帧（本脚本必须带这一步）
    from pynq import MMIO
    time.sleep(0.2)
    mm3 = MMIO(0x43A30000, 0x10000)
    c0 = mm3.read(0x00)
    mm3.write(0x00, c0 | (1 << 3))
    time.sleep(0.3)
    mm3.write(0x00, c0)
    # 与驱动 _selfcheck 完全对齐：清掉启动瞬态错误位、再稳 0.5s
    time.sleep(0.5)
    try:
        cap.clear_errors()
    except Exception:
        pass
    time.sleep(0.5)
    print('[推流] 视频域已重启（ctrl[3] 脉冲）+ 清错误位，先抓一帧热身...', flush=True)
    # ★ 关键：必须"启动后立刻抓帧"（与 _selfcheck 一致）。若先阻塞等电脑连接，
    #   VDMA 会在无人取帧的状态下空转，之后就再也收不到帧（实测）。
    try:
        idx, bgr = cap.bgr_frame(timeout=5.0)
        print(f'[推流] 热身帧 OK（buf{idx}，均值 {bgr.mean():.1f}，'
              f'标准差 {bgr.std():.1f}）', flush=True)
    except Exception as e:
        print(f'[推流] 热身帧失败（{e}）——继续，等连接后再试', flush=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', port))
    srv.listen(1)
    print(f'[推流] 等待电脑连接：{port} 端口 ...（可反复重连，Ctrl-C 结束）')
    sys.stdout.flush()

    while True:                                   # 循环接受：电脑断开后可重连
        conn, addr = srv.accept()
        print(f'[推流] 电脑已连接 {addr}，开始推流')
        sys.stdout.flush()
        n, t0 = 0, time.time()
        try:
            while True:
                try:
                    idx, bgr = cap.bgr_frame(timeout=2.0)   # 返回 (缓冲号, BGR 图)
                except Exception as e:
                    print(f'[推流] 抓帧失败（{e}），继续', flush=True)
                    continue
                ok, jpg = cv2.imencode('.jpg', bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not ok:
                    continue
                data = jpg.tobytes()
                conn.sendall(struct.pack('<I', len(data)) + data)
                n += 1
                if n % 50 == 0:
                    dt = time.time() - t0
                    print(f'[推流] 已推 {n} 帧，{n / dt:.1f} fps，约 {len(data) // 1024} KB/帧')
                    sys.stdout.flush()
        except Exception as e:
            print(f'[推流] 客户端断开（{e}），回到等待状态')
            sys.stdout.flush()
        finally:
            conn.close()


if __name__ == '__main__':
    main()
