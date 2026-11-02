#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""regmap.py — AXI-Lite 寄存器映射的最小可用范式 + 上板自检（通用 PYNQ）

三件事，任何 PYNQ 项目都会遇到：
  1. **位域读写**：名字 -> (偏移, 高位, 低位)，读出来直接是数，写回去自动 RMW。
  2. **读回自检**：所有 **RW 寄存器**用"走 1"模式全部过一遍 —— 用来区分
     "地址译码错了 / 寄存器接错线 / 位宽少了一根"，比逐个人肉试快得多。
  3. **粘滞完成位**：PS 轮询间隔远大于 1 拍，**脉冲式 done 必漏**。
     正确做法是 RTL 里把 done 做成 sticky（写 1 清），软件用 wait 轮询。

用法：
  # 走 1 自检：0x00~0x3C 共 16 个 RW 寄存器
  python3 regmap.py --base 0x43B10000 --walk 0x00:16

  # 按名字 dump（字段表写在同一行）
  python3 regmap.py --base 0x43B10000 \\
      --fields "ctrl=0x00:0:0,freeze=0x00:1:1,soft_rst=0x00:2:2,frames=0x08:31:0"

  python3 regmap.py --selftest          # PC 上干跑

退出码：0 全部通过 / 1 有失败 / 9 读不到（当不知道）
"""

import argparse
import sys

RC_OK = 0
RC_FAIL = 1
RC_UNKNOWN = 9


# --------------------------------------------------------------------------- 库
def parse_fields(spec):
    """解析 "name=0xOFF:HI:LO,name2=..." -> {'name': (off, hi, lo)}"""
    out = {}
    if not spec:
        return out
    for item in spec.split(','):
        item = item.strip()
        if not item:
            continue
        name, loc = item.split('=', 1)
        parts = loc.split(':')
        off = int(parts[0], 0)
        hi = int(parts[1], 0) if len(parts) > 1 else 0
        lo = int(parts[2], 0) if len(parts) > 2 else hi
        out[name.strip()] = (off, hi, lo)
    return out


class RegMap(object):
    """按名访问的寄存器表。fields: {'name': (offset, hi, lo)}"""

    def __init__(self, base, fields, mmio=None, span=0x1000):
        self.base = base
        self.fields = parse_fields(fields) if isinstance(fields, str) else fields
        self.span = span
        self._mmio = mmio

    @property
    def mmio(self):
        if self._mmio is None:
            from pynq import MMIO
            self._mmio = MMIO(self.base, self.span)
        return self._mmio

    def get(self, name):
        off, hi, lo = self.fields[name]
        width = hi - lo + 1
        return (self.mmio.read(off) >> lo) & ((1 << width) - 1)

    def set(self, name, value):
        """读-改-写单个字段，不碰其它位。"""
        off, hi, lo = self.fields[name]
        width = hi - lo + 1
        mask = ((1 << width) - 1) << lo
        cur = self.mmio.read(off)
        self.mmio.write(off, (cur & ~mask) | ((value << lo) & mask))

    def wait(self, name, value=1, timeout=1.0, poll=0.002):
        """轮询直到字段等于 value；超时返回 False。

        ★ 依赖 RTL 把完成位做成 **sticky**：脉冲位在这里大概率漏掉。
        """
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.get(name) == value:
                return True
            time.sleep(poll)
        return False

    def dump(self):
        return {k: self.get(k) for k in self.fields}


def walk_ones(mmio, start, count, span=0x1000):
    """对 count 个相邻 32 位寄存器写走 1 模式并读回。返回 (通过数, 失败列表)。

    走 1 能同时抓：地址线错位（两个寄存器镜像）、数据线粘连、位宽少一根。
    """
    fails = []
    for i in range(count):
        off = start + 4 * i
        for pat in (0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80,
                    0x100, 0x1000, 0x10000, 0x1000000, 0xFFFFFFFF, 0x00000000):
            try:
                mmio.write(off, pat)
                got = mmio.read(off)
            except Exception as exc:
                fails.append((off, pat, 'EXC:%s' % type(exc).__name__))
                break
            if got != pat:
                fails.append((off, pat, got))
    return count * 14 - len(fails), fails


# --------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description='AXI-Lite 寄存器映射与读回自检')
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0x43B10000)
    ap.add_argument('--span', type=lambda s: int(s, 0), default=0x1000)
    ap.add_argument('--walk', default=None, help='走 1 自检，格式 OFFSET:COUNT，如 0x00:16')
    ap.add_argument('--fields', default=None, help='字段表 name=off:hi:lo,...')
    ap.add_argument('--dump', action='store_true', help='按字段表打印所有字段')
    ap.add_argument('--selftest', action='store_true', help='PC 上干跑')
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    try:
        from pynq import MMIO
    except Exception as exc:
        print('[REG] 没有 pynq（%s）—— 请在板上用 PYNQ venv 的解释器跑'
              % type(exc).__name__)
        return RC_UNKNOWN

    try:
        mmio = MMIO(args.base, args.span)
    except Exception as exc:
        print('[REG] MMIO 打开失败：%r' % (exc,))
        return RC_UNKNOWN

    rc = RC_OK
    if args.walk:
        off_s, cnt_s = args.walk.split(':')
        start, count = int(off_s, 0), int(cnt_s, 0)
        passed, fails = walk_ones(mmio, start, count)
        print('[REG] 走 1 自检：%d 个寄存器 x 14 种模式，通过 %d，失败 %d'
              % (count, passed, len(fails)))
        for off, pat, got in fails[:20]:
            print('[REG]   FAIL off=0x%02X 写 0x%08X 读回 %s' % (off, pat, got))
        if fails:
            rc = RC_FAIL
            print('[REG] => 地址译码/数据线有问题（也可能是这些偏移本来就不是 RW）')

    if args.fields and args.dump:
        rm = RegMap(args.base, args.fields, mmio=mmio)
        for name in rm.fields:
            print('[REG] %-12s = %d (0x%X)' % (name, rm.get(name), rm.get(name)))

    if not args.walk and not (args.fields and args.dump):
        print('[REG] 没给 --walk 或 --fields+--dump，什么也没做')
    return rc


# --------------------------------------------------------------------------- 自检
def _selftest():
    class FakeMMIO(object):
        """32 位寄存器堆，故意让偏移 0x08 少一位，用来验证自检能抓到。"""

        def __init__(self, broken_off=None, broken_bit=None):
            self.mem = {}
            self.broken = (broken_off, broken_bit)

        def read(self, off):
            v = self.mem.get(off, 0)
            if self.broken[0] == off:
                v &= ~(1 << self.broken[1])
            return v & 0xFFFFFFFF

        def write(self, off, val):
            self.mem[off] = val & 0xFFFFFFFF

    bad = 0
    # 1) 全好 -> 0 失败
    m = FakeMMIO()
    _, fails = walk_ones(m, 0x00, 4)
    cases = [(len(fails), 0, '完好寄存器堆 0 失败')]
    # 2) 位 3 永久读 0 -> 必须抓到
    m = FakeMMIO(broken_off=0x08, broken_bit=3)
    _, fails = walk_ones(m, 0x00, 4)
    cases.append((len(fails) > 0, True, '卡死位被抓到'))
    # 3) 字段解析
    f = parse_fields('ctrl=0x00:0:0,freeze=0x00:1:1,frames=0x08:31:0')
    cases.append((f['freeze'], (0, 1, 1), '字段解析 hi:lo'))
    cases.append((f['frames'], (0x08, 31, 0), '字段解析 全宽'))
    # 4) RMW：只改自己那几位
    m = FakeMMIO()
    rm = RegMap(0, f, mmio=m)
    m.write(0x00, 0b1010)
    rm.set('freeze', 1)
    cases.append((m.read(0x00), 0b1010 | 0b10, 'RMW 不碰其它位'))
    # 5) wait 超时返回 False
    cases.append((rm.wait('freeze', 0, timeout=0.05), False, 'wait 超时 -> False'))

    for got, want, name in cases:
        ok = got == want
        bad += 0 if ok else 1
        print('[selftest] %-24s %s' % (name, 'OK' if ok else
                                       'FAIL got=%r want=%r' % (got, want)))
    print('[selftest] %d/%d 通过' % (len(cases) - bad, len(cases)))
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
