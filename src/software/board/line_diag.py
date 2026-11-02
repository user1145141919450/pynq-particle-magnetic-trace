# -*- coding: utf-8 -*-
"""line_diag.py —— PL 画线「写了但读不到」的定位（2026-10-31 上板实锤用）

三个假设，一次分清：
  A) 缓存问题：PYNQ 缓冲是 cacheable，CPU 读到旧值 -> 用 /dev/mem 绕过缓存再读
  B) 地址问题：FB_BASE 寄存器没写进 / 写进去的地址不对 -> 回读寄存器 + 用已知地址试写
  C) 写事务没出去：连 /dev/mem 也读不到 -> 问题在 AXI 写主/互联

用法（板上 root）：
    python3 line_diag.py
"""
import sys
import time

W, H = 1280, 720
BASE = 0x43B30000
R_FB, R_CTRL, R_STATUS, R_COLOR = 0x14, 0x18, 0x1C, 0x10


def _phys(b):
    p = getattr(b, 'device_address', None)
    return int(p if p is not None else b.physical_address)


def main():
    import numpy as np
    from pynq import allocate
    from fastmem import FastMem
    from line_pl import LinePL

    print('=' * 60)
    lp = LinePL(verbose=False)
    buf = allocate(shape=(H, W), dtype=np.uint32)
    phys = _phys(buf)
    print('buffer phys = 0x%08x' % phys)
    for attr in ('cacheable', 'device_address', 'physical_address'):
        try:
            print('  %-16s = %r' % (attr, getattr(buf, attr)))
        except Exception as e:
            print('  %-16s <none>' % attr)

    # ---- B) 寄存器回读 ----
    lp.set_fb(phys)
    print('FB 回读 = 0x%08x（写入 0x%08x）%s'
          % (lp.mm.rd_u32(R_FB), phys,
             'OK' if lp.mm.rd_u32(R_FB) == phys else '★ 不一致'))

    # ---- 清 0 之后画一条短线 ----
    buf[:] = 0
    try:
        buf.flush()
    except Exception:
        pass
    col = (7, 222, 99)
    lp.line(10, 10, 100, 10, col)
    print('STATUS = 0x%02x（画完）' % lp.status())

    # ---- A) 缓存 vs 物理内存，两条路都读 ----
    a_cpu = buf.copy()
    print('CPU 读到 (10,10) = 0x%08x，(50,10) = 0x%08x'
          % (a_cpu[10, 10], a_cpu[10, 50]))
    # ★ 注意 fastmem 的 off 是**字节偏移**（pixel*4）
    MAP = 8 * 1024 * 1024
    with FastMem(phys, MAP) as m:
        v = m.rd_u32(4 * (10 * W + 10))
        v2 = m.rd_u32(4 * (10 * W + 50))
    print('/dev/mem 读到 (10,10) = 0x%08x，(50,10) = 0x%08x' % (v, v2))

    # ---- C) CPU 写 -> /dev/mem 读：CPU 写到底进没进 DDR（缓存是否回写）----
    buf[30, 30] = 0xCAFEBABE
    try:
        buf.flush()
        print('buf.flush() 可用')
    except Exception as e:
        print('buf.flush() 不可用：%s' % e)
    with FastMem(phys, MAP) as m:
        print('CPU 写 0xCAFEBABE 后 /dev/mem 读到 0x%08x（相等=CPU 写已到 DDR）'
              % m.rd_u32(4 * (30 * W + 30)))

    # ---- 再试：把 fb 指到更低的地址（排除高地址段问题）----
    buf2 = allocate(shape=(H, W), dtype=np.uint32)
    phys2 = _phys(buf2)
    buf2[:] = 0
    print('\n第二块 buffer phys = 0x%08x' % phys2)
    lp.set_fb(phys2)
    lp.line(0, 0, 1279, 719, (255, 0, 0))
    a3 = buf2.copy()
    nz = int((a3 != 0).sum())
    print('对角线非零像素 = %d（CPU 读）' % nz)
    with FastMem(phys2, 8 * 1024 * 1024) as m:
        print('/dev/mem (0,0) = 0x%08x' % m.rd_u32(0))

    lp.close()
    try:
        buf.freebuffer(); buf2.freebuffer()
    except Exception:
        pass
    print('=' * 60)


if __name__ == '__main__':
    sys.exit(main())
