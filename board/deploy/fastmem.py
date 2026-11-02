# -*- coding: utf-8 -*-
"""fastmem.py — /dev/mem 直接 mmap + numpy 零拷贝读写（绕开 pynq MMIO 的开销）

为什么需要：pynq 3.1 的 `MMIO.read` 只支持 1/2/4/8 字节，实测 **15 µs/次** ——
读一张 23KB 的缩略图要 5760 次 = 86ms，30fps 下根本来不及。而自己 mmap
/dev/mem 之后用 numpy 读：**23KB 拷贝只要 1.05 ms**，零拷贝视图 0.02ms。

用法：
    with FastMem(0x43B00000, 0x8000) as m:
        a = m.rd_u16(0, 11520)            # 零拷贝视图（会被硬件改写）
        b = m.rd_u16(0, 11520).copy()     # 安全副本
        v = m.rd_u32(0)                   # 单字读
        m.wr_u32(4, 0x1234)               # 单字写

★ 注意：零拷贝视图**持有 mmap 的引用**，视图还活着时 close() 会报 BufferError。
  要先 copy() 或先删掉视图再关；用 with 语句时更要注意别把视图带出去。
"""

import mmap
import os
import struct

import numpy as np


class FastMem(object):
    def __init__(self, base, size):
        self.base = base
        self.size = size
        self.fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(self.fd, size, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE, offset=base)

    # ---- 单字 ----
    def rd_u32(self, off):
        return struct.unpack_from('<I', self.m, off)[0]

    def wr_u32(self, off, v):
        struct.pack_into('<I', self.m, off, v & 0xFFFFFFFF)

    def rd_u16(self, off):
        return struct.unpack_from('<H', self.m, off)[0]

    def wr_u16(self, off, v):
        struct.pack_into('<H', self.m, off, v & 0xFFFF)

    # ---- 批量（numpy，零拷贝视图）----
    def view_u32(self, off, n):
        return np.frombuffer(self.m, dtype=np.uint32, count=n, offset=off)

    def view_u16(self, off, n):
        return np.frombuffer(self.m, dtype=np.uint16, count=n, offset=off)

    def view_u8(self, off, n):
        return np.frombuffer(self.m, dtype=np.uint8, count=n, offset=off)

    # ---- 批量（安全副本）----
    def rd_u16_copy(self, off, n):
        return np.frombuffer(self.m, dtype=np.uint16, count=n, offset=off).copy()

    def rd_u8_copy(self, off, n):
        return np.frombuffer(self.m, dtype=np.uint8, count=n, offset=off).copy()

    def close(self):
        self.m.close()
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


if __name__ == '__main__':
    # 自测：在 VDMA 寄存器区（0x43000000）读一读，验证 mmap 通路
    import time
    with FastMem(0x43000000, 0x10000) as m:
        cr = m.rd_u32(0x00)
        sr = m.rd_u32(0x04)
        print('VDMA CR=0x%08x DMASR=0x%08x' % (cr, sr))
        t = time.time()
        for _ in range(50):
            a = m.view_u32(0, 1024).copy()
        print('4KB 批量读: %.3f ms/次' % ((time.time() - t) / 50 * 1000))
