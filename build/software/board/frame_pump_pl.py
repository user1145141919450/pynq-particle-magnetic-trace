# -*- coding: utf-8 -*-
"""frame_pump_pl.py —— PL 帧泵（透视矫正+打包写显存）的 PS 访问层（2026-10-31）

寄存器图（与 rtl/v3_bundle/frame_pump.v 头注释一一对应）：
    0x00 SRC_BASE  源图物理地址（32 位/像素，与 framebuffer 同口径）
    0x04 DST_BASE  framebuffer 物理地址
    0x08 SRC_W     源宽（**必须是 1024**：RTL 里行距用移位 1<<12）
    0x0C SRC_H     源高
    0x10 DST_W     输出宽（16 的倍数，=1280）
    0x14 DST_H     输出高（=720）
    0x18 TBL_BASE  行表物理地址（DST_H x 16B）
    0x1C CTRL      [0]=START
    0x20 STATUS    [0]=BUSY [1]=DONE（粘滞）
    0x24 FRAMES    完成帧数

用法：
    from frame_pump_pl import FramePumpPL, build_row_table, pack_bgr32
    fp = FramePumpPL()
    src = allocate(shape=(720,1024), dtype=np.uint32)   # 打包好的源图
    dst = allocate(shape=(720,1280), dtype=np.uint32)   # framebuffer
    tbl = allocate(shape=(720,4), dtype=np.uint32)      # 行表
    ...
    fp.setup(src, dst, tbl)
    fp.start_wait()
"""

import time

# ★ 地址出处：BD 里 apply_bd_automation 后由 assign_bd_address 钉到 0x43B4_0000
#   （bd/m1a_capture.tcl 收尾处；bd_only 日志会打 "帧泵命令口 → 0x43B40000"）
BASE = 0x43B40000
SPAN = 0x10000

R_SRC, R_DST, R_SW, R_SH, R_DW, R_DH, R_TBL = 0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18
R_CTRL, R_STAT, R_FRAMES = 0x1C, 0x20, 0x24

ST_BUSY, ST_DONE = 0x1, 0x2


def _phys(b):
    p = getattr(b, 'device_address', None)
    return int(p if p is not None else b.physical_address)


def pack_bgr32(img):
    """BGR(1024x720x3) -> 32 位数组（framebuffer 口径 (R<<16)|(B<<8)|G）。

    板 A9 上实测约 12~18ms（三笔 numpy 切片）；比 cv2 打包+缩放省得多。
    """
    import numpy as np
    h, w = img.shape[:2]
    out = np.empty((h, w), np.uint32)
    v = out.view(np.uint8).reshape(h, w, 4)
    v[:, :, 0] = img[:, :, 1]      # G
    v[:, :, 1] = img[:, :, 0]      # B
    v[:, :, 2] = img[:, :, 2]      # R
    v[:, :, 3] = 0
    return out


def build_row_table(Hm, dw=1280, dh=720, margin=0.03, vpad=0.012,
                    sw=1024, sh=720):
    """按标定单应生成行表（Q16.8）：每行 (sx0, sy0, dsx, dsy)。

    与 _live_warp_matrix 同一份口径：源图（相机 1024x720）四角 = 场区四角，
    目标 = 屏幕内 16:9 内框（margin 留白）。
    """
    import cv2
    import numpy as np
    import v3_scan
    mx = float(margin) * 1280.0
    my = (float(margin) + vpad) * 720.0
    dst_q = np.array([[mx, my], [1280.0 - mx, my],
                      [1280.0 - mx, 720.0 - my], [mx, 720.0 - my]], np.float32)
    src_q = np.array([
        list(v3_scan.game_to_cam(Hm, 0.0, 0.0))[:2],
        list(v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, 0.0))[:2],
        list(v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, v3_scan.FIELD_H))[:2],
        list(v3_scan.game_to_cam(Hm, 0.0, v3_scan.FIELD_H))[:2]], np.float32)
    M = cv2.getPerspectiveTransform(src_q, dst_q)
    Minv = np.linalg.inv(M)
    ys = np.arange(dh, dtype=np.float32)
    p0 = np.stack([np.zeros(dh, np.float32), ys], 1).reshape(-1, 1, 2)
    p1 = np.stack([np.full(dh, dw - 1, np.float32), ys], 1).reshape(-1, 1, 2)
    q0 = cv2.perspectiveTransform(p0, Minv).reshape(-1, 2)
    q1 = cv2.perspectiveTransform(p1, Minv).reshape(-1, 2)
    # ★ 两端都夹进源图再算步长 —— RTL 不做钳位（省比较器，保时序），
    #   必须由行表保证"整行地址都在缓冲内"，否则最后一行会读到缓冲外
    q0[:, 0] = np.clip(q0[:, 0], 0, sw - 1)
    q0[:, 1] = np.clip(q0[:, 1], 0, sh - 1)
    q1[:, 0] = np.clip(q1[:, 0], 0, sw - 1)
    q1[:, 1] = np.clip(q1[:, 1], 0, sh - 1)
    dsx = (q1[:, 0] - q0[:, 0]) / float(dw - 1)
    dsy = (q1[:, 1] - q0[:, 1]) / float(dw - 1)
    tbl = np.zeros((dh, 4), np.int32)
    tbl[:, 0] = np.round(q0[:, 0] * 256.0)
    tbl[:, 1] = np.round(q0[:, 1] * 256.0)
    tbl[:, 2] = np.round(dsx * 256.0)
    tbl[:, 3] = np.round(dsy * 256.0)
    return tbl.astype(np.uint32).reshape(-1)


class FramePumpPL(object):
    def __init__(self, mm=None, verbose=False):
        from fastmem import FastMem
        self._own = mm is None
        self.mm = mm if mm is not None else FastMem(BASE, SPAN)
        st = self.mm.rd_u32(R_STAT)
        self.verbose = verbose
        self.n = 0
        self.ms = 0.0
        if verbose:
            print('[PL帧泵] 寄存器窗口 OK（STATUS=0x%02x）' % st)

    def close(self):
        if self._own:
            try:
                self.mm.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def setup(self, src, dst, tbl, sw=1024, sh=720, dw=1280, dh=720):
        m = self.mm
        m.wr_u32(R_SRC, _phys(src))
        m.wr_u32(R_DST, _phys(dst))
        m.wr_u32(R_SW, int(sw))
        m.wr_u32(R_SH, int(sh))
        m.wr_u32(R_DW, int(dw))
        m.wr_u32(R_DH, int(dh))
        m.wr_u32(R_TBL, _phys(tbl))
        return self

    def start_wait(self, timeout_s=0.5):
        t0 = time.perf_counter()
        self.mm.wr_u32(R_CTRL, 1)
        while True:
            st = self.mm.rd_u32(R_STAT)
            if st & ST_DONE:
                break
            if time.perf_counter() - t0 > timeout_s:
                raise RuntimeError('PL 帧泵超时（STATUS=0x%02x）' % st)
        dt = (time.perf_counter() - t0) * 1e3
        self.n += 1
        self.ms += dt
        return dt

    def frames(self):
        return self.mm.rd_u32(R_FRAMES)


if __name__ == '__main__':
    fp = FramePumpPL(verbose=True)
    print('FRAMES=%d' % fp.frames())
    fp.close()
