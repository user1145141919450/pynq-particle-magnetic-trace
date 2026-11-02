#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cma.py — CMA 缓冲的缓存一致性封装（通用 PYNQ）

为什么需要它：
  PL 通过 AXI 直接读写 DDR，**不经过 CPU 的 cache**。而 PYNQ 的 `allocate()`
  拿到的是 **cacheable** 的 CMA 缓冲（CPU 读写会进 cache），于是：

    · CPU 写好数据 -> PL 去读：如果没 flush，PL 读到的可能是**旧的 DDR 内容**；
    · PL 写好数据 -> CPU 去读：如果没 invalidate，CPU 读到的是**自己的旧 cache**。

  症状极具欺骗性：小数据"有时对有时不对"、连续两次读结果不同、换一块缓冲就好，
  换成 `cacheable=False` 又变慢 —— 看起来像硬件不稳定，其实少了一次 flush。

本模块把这件事变成**不可能忘**的接口：写数据用 fill()、读结果用 snapshot()，
flush / invalidate 都在里面。`--selftest` 用假缓冲验证调用顺序。

用法：
  from cma import PlBuffer
  with PlBuffer((720, 1024), 'u8') as buf:
      buf.fill(frame)          # CPU -> DDR（自动 flush）
      start_transfer(buf)
      wait_done()
      out = buf.snapshot()     # DDR -> CPU（自动 invalidate，返回副本）

命令行：
  python3 cma.py --selftest
"""

import sys

RC_OK = 0
RC_FAIL = 1

DTYPE = {'u8': 'uint8', 'u16': 'uint16', 'u32': 'uint32', 'i8': 'int8',
         'i16': 'int16', 'i32': 'int32', 'f32': 'float32'}


class PlBuffer(object):
    """一个"知道自己是共享内存"的缓冲：写要 flush，读要 invalidate。"""

    def __init__(self, shape, dtype='u8', cacheable=True, alloc_fn=None):
        self.shape = shape
        self.dtype = dtype
        self.cacheable = cacheable
        self._alloc_fn = alloc_fn
        self._buf = None
        self.flushes = 0
        self.invalidates = 0

    def _alloc(self):
        if self._buf is not None:
            return self._buf
        fn = self._alloc_fn
        if fn is None:
            from pynq import allocate
            fn = allocate
        import numpy as np
        self._buf = fn(shape=self.shape, dtype=getattr(np, DTYPE[self.dtype]),
                       cacheable=self.cacheable)
        return self._buf

    # -- 写：CPU -> PL ---------------------------------------------------
    def fill(self, data=None):
        buf = self._alloc()
        if data is not None:
            buf[:] = data
        buf.flush()                    # ★ 把 CPU cache 里的新值写回 DDR
        self.flushes += 1
        return buf

    # -- 读：PL -> CPU ---------------------------------------------------
    def snapshot(self):
        buf = self._alloc()
        buf.invalidate()               # ★ 丢掉 CPU cache 里的旧值，从 DDR 重读
        self.invalidates += 1
        return buf.copy()

    def device_address(self):
        return self._alloc().device_address

    def close(self):
        if self._buf is not None:
            try:
                self._buf.close()
            finally:
                self._buf = None

    # -- 上下文管理器 ----------------------------------------------------
    def __enter__(self):
        self._alloc()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--selftest' in argv:
        return _selftest()
    print(__doc__)
    return RC_OK


def _selftest():
    """假缓冲：验证 fill 一定 flush、snapshot 一定 invalidate。"""
    import types

    class FakeBuf(object):
        def __init__(self, shape, dtype, cacheable):
            self.shape, self.dtype, self.cacheable = shape, dtype, cacheable
            self.calls = []
            self.data = None
            self.device_address = 0x1000

        def __setitem__(self, k, v):
            self.data = v

        def flush(self):
            self.calls.append('flush')

        def invalidate(self):
            self.calls.append('invalidate')

        def copy(self):
            self.calls.append('copy')
            return self.data

        def close(self):
            self.calls.append('close')

    # 假 numpy：只要能提供 uint8 这个名字就行，用于在无 numpy 的 PC 上跑判据
    np = types.ModuleType('numpy')
    np.uint8 = 'uint8'
    _saved = sys.modules.get('numpy')
    sys.modules['numpy'] = np

    def alloc_fn(shape, dtype, cacheable):
        b = FakeBuf(shape, dtype, cacheable)
        alloc_fn.last = b
        return b

    cases = []
    buf = PlBuffer((4, 4), 'u8', alloc_fn=alloc_fn)

    # 1) fill 之后必然 flush
    buf.fill('DATA')
    cases.append((alloc_fn.last.calls[:1], ['flush'], 'fill 先写再 flush'))
    # 2) snapshot 之后必然 invalidate，且返回副本
    out = buf.snapshot()
    cases.append((alloc_fn.last.calls[1:3], ['invalidate', 'copy'],
                  'snapshot 先 invalidate 再 copy'))
    # 3) 返回值就是数据
    cases.append((out, 'DATA', 'snapshot 返回数据'))
    # 4) 计数
    cases.append((buf.flushes, 1, 'flush 计数'))
    # 5) 退出时关闭
    with PlBuffer((2,), 'u8', alloc_fn=alloc_fn) as b2:
        b2.fill()
    cases.append(('close' in alloc_fn.last.calls, True, 'with 退出时 close'))

    if _saved is None:
        sys.modules.pop('numpy', None)
    else:
        sys.modules['numpy'] = _saved

    bad = 0
    for got, want, name in cases:
        ok = got == want
        bad += 0 if ok else 1
        print('[selftest] %-26s %s' % (name, 'OK' if ok else
                                       'FAIL got=%r want=%r' % (got, want)))
    print('[selftest] %d/%d 通过' % (len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
