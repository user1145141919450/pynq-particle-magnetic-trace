#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""axil_fast_scan.py —— 把**整个** PS AXI-Lite 窗口扫一遍，列出**哪些 64KB 槽有从机**。

为什么不用 axil_sweep.py（2026-10-01）：
  axil_sweep 的子进程要 `import pynq`（~1.5s 启动）。它扫 233 页、死了 16 页，
  重启 16 次还受得了。但要扫**整个 0x40000000..0x80000000**（16384 个 64KB 槽，
  绝大多数是空的）就是 4 小时 —— 不行。
  本脚本的子进程只用 **stdlib**（`os`+`mmap`+`struct`），启动 ~0.05s，
  一页一个进程也只要几分钟。

背景（为什么非扫不可）：
  板上实测：`.hwh` 登记的 16 个 IP 里**只有 vtc_out(0x43C20000) 那一个 64KB 槽
  整个不响应**，其余 15 个全活。而 v_tc 的时钟/复位/时钟使能/地址接线在 BD 网表里
  逐条看都**没问题**。所以现在必须回答一个更基本的问题：
    **v_tc 到底在不在总线上？是不是被排到了别的地址？**
  扫全空间：如果多出一个 `.hwh` 里没有的活槽 ⇒ 就是它，地址对不上；
            如果一个都不多 ⇒ v_tc 压根没接到总线上（或不在 bit 里）。

★ 为什么还是"一页一个子进程"：
  SIGBUS 是**信号**不是异常，Python 的 try/except 抓不住。
  子进程顺序读、每读一页 print+flush；父进程看到 -7，就知道**最后一个 OK 的下一页**
  是肇事页，从它之后接着走。（同 axil_sweep.py 的思路。）

用法：
    python3 axil_fast_scan.py                      # 默认 0x40000000..0x44000000，步 0x10000
    python3 axil_fast_scan.py --top 0x44000000
    python3 axil_fast_scan.py --step 0x1000 --base 0x43C20000 --top 0x43C30000
    python3 axil_fast_scan.py --only-live          # 只打活槽（长扫描时用）

退出码：0 = 扫完（结论在输出里）；9 = 打不开 /dev/mem（记得要 root）
"""
import argparse
import os
import re
import subprocess
import sys

DEF_BASE = 0x40000000
DEF_TOP  = 0x44000000
DEF_STEP = 0x10000          # 64KB —— 交叉开关每个从机占一个 64KB 槽

# ★ 子进程只用 stdlib：这是本脚本比 axil_sweep.py 快的唯一原因
CHILD_SRC = r'''
import mmap, os, struct, sys
base = %d
top  = %d
step = %d
start = %d
CHUNK = 0x4000000                      # 64MB 一段，避免一次映射太大
fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
try:
    a = start
    # 把 start..top 按 CHUNK 分段映射；每段内部顺读
    seg_lo = base + ((a - base) // CHUNK) * CHUNK
    while a < top:
        seg_hi = min(seg_lo + CHUNK, top)
        if a < seg_lo:
            a = seg_lo
        try:
            m = mmap.mmap(fd, CHUNK,
                          mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                          offset=seg_lo)
        except OSError as e:
            sys.stdout.write("MAPFAIL 0x%%08X %%s\n" %% (seg_lo, e))
            sys.stdout.flush()
            # 这段映射不了，整段跳过（逐页试也没意义）
            while a < seg_hi:
                sys.stdout.write("NOMAP 0x%%08X\n" %% a)
                sys.stdout.flush()
                a += step
            seg_lo = seg_hi
            continue
        while a < seg_hi:
            v = struct.unpack_from('<I', m, a - seg_lo)[0]
            sys.stdout.write("OK 0x%%08X 0x%%08X\n" %% (a, v))
            sys.stdout.flush()
            a += step
        m.close()
        seg_lo = seg_hi
finally:
    os.close(fd)
sys.stdout.write("DONE\n")
sys.stdout.flush()
'''

LINE_OK = re.compile(r'^OK 0x([0-9A-Fa-f]{8}) 0x([0-9A-Fa-f]{8})$')
LINE_NOMAP = re.compile(r'^NOMAP 0x([0-9A-Fa-f]{8})$')


def scan(base, top, step, log=None):
    """返回 {addr: value 或 None(=SIGBUS)}，按地址排序。"""
    res = {}
    addr = base
    restarts = 0
    while addr < top:
        restarts += 1
        if restarts > 3000:
            (log or (lambda *a: None))('!! 重启次数过多，停')
            break
        src = CHILD_SRC % (base, top, step, addr)
        try:
            r = subprocess.run([sys.executable, '-u', '-c', src],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=180)
            out = r.stdout.decode('utf-8', 'replace')
            rc = r.returncode
        except subprocess.TimeoutExpired:
            (log or (lambda *a: None))('!! 子进程超时 —— 停（挂住不是 SIGBUS）')
            break

        last = None
        nomap_last = None
        for ln in out.splitlines():
            ln = ln.strip()
            mo = LINE_OK.match(ln)
            if mo:
                a, v = int(mo.group(1), 16), int(mo.group(2), 16)
                res[a] = v
                last = a
                continue
            mo = LINE_NOMAP.match(ln)
            if mo:
                a = int(mo.group(1), 16)
                res[a] = None
                nomap_last = a

        if 'DONE' in out:
            break

        if nomap_last is not None and (last is None or nomap_last > last):
            addr = nomap_last + step
            continue

        if last is None:
            # 第一页就是坏的 —— 没线索，只能按 gap 推
            res[addr] = None
            addr += step
            continue

        nxt = last + step
        if nxt >= top:
            break
        res[nxt] = None
        addr = nxt + step

    return dict(sorted(res.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description='AXI-Lite 全空间存活扫描（只读 /dev/mem）')
    ap.add_argument('--base', default=hex(DEF_BASE))
    ap.add_argument('--top', default=hex(DEF_TOP))
    ap.add_argument('--step', default=hex(DEF_STEP))
    ap.add_argument('--only-live', action='store_true', help='只打印活的槽')
    args = ap.parse_args(argv)
    base, top, step = int(args.base, 16), int(args.top, 16), int(args.step, 16)

    print('=' * 74)
    print('axil_fast_scan  0x%08X..0x%08X  步长 0x%X  （%d 页，只读）'
          % (base, top, step, (top - base) // step))
    print('=' * 74)

    res = scan(base, top, step, log=lambda s: print(s))

    live = [(a, v) for a, v in res.items() if v is not None]
    print('')
    print('--- 活的槽（%d 个）---' % len(live))
    for a, v in live:
        print('   0x%08X   读回 0x%08X' % (a, v))

    if not args.only_live:
        dead = [a for a, v in res.items() if v is None]
        print('')
        print('--- 死的槽（%d 个，只列首尾）---' % len(dead))
        if dead:
            print('   0x%08X .. 0x%08X' % (dead[0], dead[-1]))

    print('')
    print('★ 只读，没写任何一个寄存器。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
