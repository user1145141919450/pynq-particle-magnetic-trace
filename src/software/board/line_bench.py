# -*- coding: utf-8 -*-
"""line_bench.py —— PL 画线引擎上板台架（2026-10-30）

不碰 VDMA / 相机 / HDMI：自己 allocate 一块 1280x720 的 CMA 缓冲，让 PL 往里面
画线，**PS 再把像素读回来逐点核对**。这样能单独验完 PL 通路的四件事：

  1. AXI-Lite 命令口能读能写（STATUS / 回读寄存器）
  2. AXI4 写主真的把像素写进了 DDR（PS 读回逐点比对）
  3. 颜色字节序对（(R<<16)|(B<<8)|G 口径）
  4. 速度：单条线耗时、17 路折线量级（与 cv2.polylines 同图对比）

用法（板上 root）：
    echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \
        /usr/local/share/pynq-venv/bin/python3 line_bench.py"

注意：**不分配 VDMA 缓冲、不碰帧泵**，所以不会和"野 DMA"纪律冲突；
      但和游戏同时跑会抢 HP0 带宽——测速请先停游戏。
"""

import sys
import time

W, H = 1280, 720
FB_BYTES = W * H * 4


def _phys(buf):
    p = getattr(buf, 'device_address', None)
    if p is None:
        p = buf.physical_address
    return int(p)


def _snap(buf):
    """读回前先把 CPU cache 作废（PYNQ 的 CMA 缓冲默认不分 cache，但各版本口径
    不完全一致——这里兜一手，免得验出"PL 没写进去"的假故障）。"""
    try:
        buf.invalidate()
    except Exception:
        pass
    return buf.copy()


def main():
    import numpy as np
    from pynq import allocate
    from line_pl import LinePL, bgr_word, R_X0, R_STATUS

    print('=' * 66)
    print('PL 画线引擎上板台架')
    print('=' * 66)

    lp = LinePL(verbose=True)

    # ---- 缓冲：1280x720 32 位（与 FramePump 的 CMA 同规格）----
    buf = allocate(shape=(H, W), dtype=np.uint32)
    phys = _phys(buf)
    print('framebuffer: 虚拟->物理 0x%08x（%.1f MB）' % (phys, FB_BYTES / 1e6))
    buf[:] = 0
    lp.set_fb(phys)

    ok = 0
    bad = 0

    def check(name, cond, extra=''):
        nonlocal ok, bad
        if cond:
            ok += 1
            print('  [OK]   %s %s' % (name, extra))
        else:
            bad += 1
            print('  [FAIL] %s %s' % (name, extra))

    # ---- 0) 寄存器回读 ----
    lp.mm.wr_u32(R_X0, 0x00321)
    check('寄存器回读 X0', lp.mm.rd_u32(R_X0) == 0x00321,
          '实际 0x%05x' % lp.mm.rd_u32(R_X0))

    # ---- 1) 单条水平线：像素逐个对 ----
    buf[:] = 0
    c = (10, 200, 30)                       # BGR
    lp.line(100, 200, 199, 200, c)
    a = _snap(buf)
    # 写进内存的 32 位字 = (R<<16)|(B<<8)|G（小端字节序 = [G,B,R,0]，FramePump 口径）
    r_, g_, b_ = c[2], c[1], c[0]
    exp = (r_ << 16) | (b_ << 8) | g_
    row = a[200, 100:200]
    check('水平线 100..199 全中', bool(np.all(row == exp)),
          '期望 0x%06x，实际首尾 0x%06x/0x%06x' % (exp, row[0], row[-1]))
    check('线上方无溢出', int(a[199].sum()) == 0 and int(a[201].sum()) == 0)
    check('线段外无写入', int(a[200, :100].sum()) == 0 and int(a[200, 200:].sum()) == 0)

    # ---- 2) 斜线：两端 + 中点 ----
    buf[:] = 0
    lp.line(0, 0, 1279, 719, (0, 0, 255))   # 红
    a = _snap(buf)
    nz = int(a.astype(bool).sum())
    check('对角线像素数 ≈1280', 1270 <= nz <= 1290, '实际 %d' % nz)
    check('对角两端命中', a[0, 0] != 0 and a[719, 1279] != 0)

    # ---- 3) 越界裁剪：全在屏外 -> 一个像素都不写 ----
    buf[:] = 0
    lp.line(2000, 2000, 2500, 2500, (1, 2, 3))
    check('全越界线不写', int(_snap(buf).astype(bool).sum()) == 0)

    # ---- 4) 速度：同一条 1280px 线画 200 次 ----
    buf[:] = 0
    t0 = time.perf_counter()
    for i in range(200):
        lp.line(0, 360, 1279, 360 + (i % 3), (0, 200, 0))
    dt = (time.perf_counter() - t0) / 200 * 1000.0
    print('  [ .. ] 单条 1280px 线：%.3f ms（PS 写寄存器 + PL 画 + 轮询）' % dt)

    # ---- 5) 17 路折线（每路 60 段，模拟一簇轨迹）----
    poly = []
    for k in range(17):
        pts = [(100 + 3 * j, 100 + k * 30 + 2 * j) for j in range(60)]
        poly.append(pts)
    t0 = time.perf_counter()
    for pts in poly:
        lp.polyline(pts, (0, 200, 0), step=1)
    ms_pl = (time.perf_counter() - t0) * 1e3

    # ---- 6) cv2 对照：同样 17 条折线画在一张 1280x720 uint32 图上 ----
    import cv2
    bg = np.zeros((H, W, 3), np.uint8)
    t0 = time.perf_counter()
    for pts in poly:
        cv2.polylines(bg, [np.asarray(pts, np.int32).reshape(-1, 1, 2)], False,
                      (0, 200, 0), 2)
    ms_cv_poly = (time.perf_counter() - t0) * 1e3
    ms_cv_pack = 0.0

    print('  [ .. ] 17 路×60 段：PL 折线 %.1f ms / cv2.polylines %.1f ms'
          % (ms_pl, ms_cv_poly))

    print('-' * 66)
    print('通过 %d / 失败 %d' % (ok, bad))
    print('画线统计：%s' % lp.stats())
    lp.close()
    try:
        buf.freebuffer()
    except Exception:
        pass
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
