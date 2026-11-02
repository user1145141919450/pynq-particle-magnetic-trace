# -*- coding: utf-8 -*-
"""rate_check.py — 实测真实帧率（不信 DMASR 帧计数，用传感器噪声当判据）

背景：VSIZE 改成 720 后，驱动的 `frame()` 报 36.5 fps，而传感器只有 30 fps
（账本独立算出：href 44.62M/s ÷ 2048 拍/行 ÷ 720 行/帧 = 30.3 帧/s）。
"比物理上限还快"只可能是**帧计数虚增**（一次传感器画面被计成不止一帧）。

判据：传感器有读出噪声 —— **不同帧的内容必然有细微差异**，而"重复读同一块
缓冲"是字节完全一致的。于是：
    连续两帧字节完全相同  -> 其实是同一帧（计数虚增）
    连续两帧字节有差异    -> 真的是新帧
再用"首个字节不同的帧"的间隔算出真实帧率。

用法（板上，bit 已下过）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/rate_check.py [帧数]
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from pynq import MMIO
from vdma_capture import S2mmCapture

W, H = 1024, 720


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cap = S2mmCapture(W, H)
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

    prev, samples, dup = None, [], 0
    t_start = None
    for k in range(n):
        idx, raw = cap.frame(timeout=3.0)
        arr = np.asarray(raw, dtype=np.uint8).copy()
        now = time.time()
        if t_start is None:
            t_start = now
        if prev is not None:
            if arr.shape == prev.shape and np.array_equal(arr, prev):
                dup += 1
                samples.append((k, now, True))
            else:
                samples.append((k, now, False))
        prev = arr

    dt = time.time() - t_start
    new_frames = sum(1 for _, _, d in samples if not d)
    print('[取样] 抓 %d 帧，耗时 %.2fs' % (n, dt))
    print('[取样] 其中**内容真正变化**的帧 = %d，与前帧字节完全相同（=同一帧重读）= %d'
          % (new_frames, dup))
    print('[结论] 驱动口径帧率 = %.1f fps（%.0f ms/帧）' % (n / dt, 1000.0 * dt / n))
    if new_frames:
        print('[结论] **真实帧率 = %.1f fps**（%.0f ms/帧）'
              % (new_frames / dt, 1000.0 * dt / new_frames))
    # 逐帧间隔（只在"新帧"处取样，反推传感器周期）
    gap = []
    last_t = None
    for k, t, d in samples:
        if d:
            continue
        if last_t is not None:
            gap.append(t - last_t)
        last_t = t
    if gap:
        g = np.array(gap) * 1000
        print('[结论] 新帧之间间隔：均值 %.1f ms，最小 %.1f，最大 %.1f（%d 个间隔）'
              % (g.mean(), g.min(), g.max(), len(g)))
    print('[参考] 账本独立算出的传感器帧率 = href 44.62M/s ÷ 2048 拍/行 ÷ 720 行 '
          '= 30.3 fps')


if __name__ == '__main__':
    main()
