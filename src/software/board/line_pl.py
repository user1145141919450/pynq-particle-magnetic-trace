# -*- coding: utf-8 -*-
"""line_pl.py —— PL 画线引擎（line_engine_top）的 PS 访问层。

寄存器图（与 rtl/v3_bundle/line_ctrl_axi.v 头注释一一对应）：
    0x00 X0      [17:0]  起点 x（framebuffer 像素坐标 0..1279）
    0x04 Y0      [17:0]
    0x08 X1      [17:0]  终点 x
    0x0C Y1      [17:0]
    0x10 COLOR   [23:0]  {B,G,R}（与 cv2 的 BGR 元组同序）
    0x14 FB_BASE [31:0]  framebuffer 物理基地址（字节）
    0x18 CTRL    [0]     START（写 1 启动一条线；忙时写入被忽略）
    0x1C STATUS  [0]=BUSY [1]=DONE（粘滞，下一次 START 清）

用法：
    from line_pl import LinePL
    lp = LinePL()
    lp.set_fb(int(buf.physical_address))     # pynq allocate 的 CMA 缓冲
    lp.line(100, 100, 900, 700, (0, 200, 0)) # BGR
    lp.polyline([(x0,y0), (x1,y1), ...], (0, 200, 0))
    lp.close()

★ 为什么用 fastmem（/dev/mem + mmap）而不是 pynq MMIO：`MMIO.read/write` 板上实测
  ~15us/次，一条线要 6 写 + 轮询，逐点下发根本来不及；fastmem 单字是 0.3us 量级
  （见 v3_pl.open_v3 的同一段说明）。**这里要 root 跑**（/dev/mem 权限）。
"""

import time

# ★ 地址出处：BD 里 apply_bd_automation 分配后由 assign_bd_address 钉到 0x43B3_0000
#   （bd/m1a_capture.tcl 收尾处；BD 校验日志里打 "画线引擎命令口 -> 0x43B30000"）。
BASE = 0x43B30000
SPAN = 0x10000

R_X0, R_Y0, R_X1, R_Y1 = 0x00, 0x04, 0x08, 0x0C
R_COLOR, R_FB = 0x10, 0x14
R_CTRL, R_STATUS = 0x18, 0x1C

ST_BUSY, ST_DONE = 0x1, 0x2


def bgr_word(color):
    """(B, G, R) 元组 -> 24 位 {B,G,R}（RTL 的 COLOR 寄存器口径）。"""
    b, g, r = int(color[0]) & 0xFF, int(color[1]) & 0xFF, int(color[2]) & 0xFF
    return (b << 16) | (g << 8) | r


class LinePL(object):
    def __init__(self, mm=None, verbose=False):
        from fastmem import FastMem
        self._own = mm is None
        self.mm = mm if mm is not None else FastMem(BASE, SPAN)
        # 探一下：映射不对（bit 没下 / 地址变了）这里就会炸，别等到画线才发现
        st = self.mm.rd_u32(R_STATUS)
        self.verbose = verbose
        self.n_lines = 0
        self.ms_total = 0.0
        if verbose:
            print('[PL画线] 寄存器窗口 OK（STATUS=0x%08x）' % st)

    # ---------------- 生命周期 ----------------
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

    # ---------------- 寄存器 ----------------
    def set_fb(self, phys):
        """framebuffer 物理基地址（字节）。pynq: int(buf.physical_address)。"""
        self.mm.wr_u32(R_FB, int(phys) & 0xFFFFFFFF)
        return self

    def status(self):
        return self.mm.rd_u32(R_STATUS)

    # ---------------- 画线 ----------------
    def line(self, x0, y0, x1, y1, color, timeout_s=0.05):
        """画一条直线（1px，PL 内部 DDA），**阻塞**到这条线画完（等 DONE）。

        返回这条线的耗时（毫秒，含 PS 写寄存器 + PL 画 + 轮询）。
        端点超出屏幕的点会被 PL 直接丢弃（不写 framebuffer），不报错。
        """
        t0 = time.perf_counter()
        mm = self.mm
        # 忙就先等（正常情况下不会：上一笔已经在 line() 里等过 DONE 了）
        n = 0
        while (mm.rd_u32(R_STATUS) & ST_BUSY) and n < 10000:
            n += 1
        if mm.rd_u32(R_STATUS) & ST_BUSY:
            raise RuntimeError('PL 画线引擎一直 BUSY（卡死？先 load_bit.py 重下 bit）')
        mm.wr_u32(R_X0, int(x0) & 0x3FFFF)
        mm.wr_u32(R_Y0, int(y0) & 0x3FFFF)
        mm.wr_u32(R_X1, int(x1) & 0x3FFFF)
        mm.wr_u32(R_Y1, int(y1) & 0x3FFFF)
        mm.wr_u32(R_COLOR, bgr_word(color))
        mm.wr_u32(R_CTRL, 1)
        # 轮询 DONE（PL 一条 1280px 线 ~13us；正常几十次读就够）
        t_end = t0 + timeout_s
        while True:
            st = mm.rd_u32(R_STATUS)
            if st & ST_DONE:
                break
            if time.perf_counter() > t_end:
                raise RuntimeError('PL 画线超时（STATUS=0x%02x, 端点 %d,%d -> %d,%d）'
                                   % (st, x0, y0, x1, y1))
        dt = (time.perf_counter() - t0) * 1e3
        self.n_lines += 1
        self.ms_total += dt
        return dt

    def polyline(self, pts, color, step=1, timeout_s=0.05):
        """一条折线 = 逐段 line()。pts 可以是 [(x,y), ...]（int 或 float）。

        step：隔几个点画一段（>1 时抽稀，减少 AXI-Lite 命令数；轨迹本身就是
        1~2px 一个点，抽稀肉眼几乎看不出来）。
        返回总耗时（毫秒）。
        """
        t0 = time.perf_counter()
        _pts = [(int(round(p[0])), int(round(p[1]))) for p in pts]
        if step > 1:
            _pts = _pts[::step] + ([_pts[-1]] if (_pts and _pts[-1] != _pts[::step][-1]) else [])
        if len(_pts) == 2:
            self.line(_pts[0][0], _pts[0][1], _pts[1][0], _pts[1][1], color, timeout_s)
        else:
            for i in range(len(_pts) - 1):
                a, b = _pts[i], _pts[i + 1]
                self.line(a[0], a[1], b[0], b[1], color, timeout_s)
        return (time.perf_counter() - t0) * 1e3

    def stats(self):
        return dict(n=self.n_lines, ms_total=self.ms_total,
                    ms_avg=(self.ms_total / self.n_lines if self.n_lines else 0.0))


if __name__ == '__main__':
    # 命令行自测：python3 line_pl.py [像素数]
    # ★ 只读 STATUS、不写 framebuffer —— 不上屏也能验证寄存器通路
    import sys
    lp = LinePL(verbose=True)
    print('STATUS=0x%02x（bit0=busy bit1=done）' % lp.status())
    lp.close()
