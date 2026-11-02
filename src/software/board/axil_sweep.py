#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""axil_sweep.py —— 把 0x43C00000..0x43C40000 每 4KB 一页读一遍，看**哪些页有从机应答**。

为什么需要它（2026-10-01 上午，在板上）：
  `vtc_after_clk_test.py` 跑出 **rc=1 —— 假设被否**：
  配好 dynclk（MMCM 已锁、PXL_CLK 在跑）之后，`0x43C20000` **依然 SIGBUS**。
  也就是说"v_tc 的核心复位挂在死掉的像素钟域上"那套推理**不成立**，
  known_issues.md §6 的结论要重写。

  于是回到最朴素的问题：**这个地址到底有没有从机？** 三种可能，一扫就分清：
    A) 只有 0x43C2xxxx 这一片死、四邻都活   ⇒ 问题在 v_tc 自己（复位/时钟/没例化）
    B) 整片 0x43C00000+ 全死               ⇒ 我们的 bit 根本不在 PL 里
                                            （但 md5 对得上，就要查 fpga_manager）
    C) 死的是**别的一片**                  ⇒ 地址图跟我们手上的 .hwh 对不上

★ 为什么必须"子进程 + 边走边打 + 死了接着走"：
  SIGBUS 是**信号**不是异常，Python 的 try/except 抓不住，进程当场没。
  所以让子进程顺序读、每读一页就 `print + flush`；父进程看见子进程以 -7 死掉时，
  **最后一个 OK 的下一页**就是肇事页，从它之后重新起一个子进程接着走。
  这样最坏 64 次重启，每次 ~2s，比一页一个进程快得多。

用法：
    sudo -S -E bash -lc "... python3 -u axil_sweep.py"
    python3 axil_sweep.py --base 0x43C00000 --top 0x43C40000 --step 0x1000

退出码：0 = 扫完（无论死活，结论在输出里）；9 = 前提不足（连 /dev/mem 都打不开）
"""
import argparse
import os
import re
import subprocess
import sys

DEF_BASE = 0x43C00000
DEF_TOP = 0x43C40000
DEF_STEP = 0x1000

FPGA_STATE = '/sys/class/fpga_manager/fpga0/state'

# 四个"应该"在的 IP —— 从 deploy/ov5640_m1a_wrapper.hwh 抄来，扫描结果拿它做对照
EXPECT = [
    (0x40000000, 'v3_bundle_axi_0'),
    (0x43000000, 'axi_vdma_0'),
    (0x43A00000, 'axi_gpio_probe0'),
    (0x43A10000, 'axi_gpio_probe1'),
    (0x43A20000, 'axi_gpio_probe2'),
    (0x43A30000, 'axi_gpio_probe3'),
    (0x43A40000, 'axi_gpio_probe4'),
    (0x43A50000, 'axi_gpio_probe5'),
    (0x43A60000, 'axi_gpio_probe6'),
    (0x43B00000, 'axi_bram_ctrl_vision'),
    (0x43B10000, 'axi_gpio_vision'),
    (0x43B20000, 'axi_gpio_vstat'),
    (0x43C00000, 'axi_vdma_out'),
    (0x43C10000, 'axi_dynclk'),
    (0x43C20000, 'vtc_out'),
    (0x43C30000, 'hdmi_out_hpd_video'),
]

# ★ `--map`：把 .hwh 里**每一个** MEMRANGE 都扫一遍。
#   为什么要它：0x43C2xxxx 整槽死掉之后，第一个要排除的就是
#   「板上这份 bit 是不是根本没含新加的 HDMI IP / 是不是旧的」。
#   把整张图扫出来，如果别的都在、只有 vtc_out 不在 ⇒ bit 是对的，是 v_tc 自己的问题；
#   如果连 v3_bundle/gpio 那些老 IP 都不在 ⇒ bit 根本不是这份设计，别往下查了。
MAP_RANGES = [
    (0x40000000, 0x40001000),
    (0x43000000, 0x43010000),
    (0x43A00000, 0x43A70000),
    (0x43B00000, 0x43B08000),
    (0x43B10000, 0x43B20000),
    (0x43B20000, 0x43B30000),
    (0x43C00000, 0x43C40000),
]

# ---------------------------------------------------------------------------
# 子进程：从 off0 开始顺序读，每页打一行。被 SIGBUS 杀掉时，父进程靠已打印的行定位。
CHILD_SRC = r'''
import sys
from pynq import MMIO
BASE = %d
TOP  = %d
STEP = %d
START = %d
m = MMIO(BASE, TOP - BASE)
for off in range(START, TOP - BASE, STEP):
    v = m.read(off)
    sys.stdout.write("OK 0x%%08X 0x%%08X\n" %% (BASE + off, v))
    sys.stdout.flush()
sys.stdout.write("DONE\n")
sys.stdout.flush()
'''

LINE_OK = re.compile(r'^OK 0x([0-9A-Fa-f]{8}) 0x([0-9A-Fa-f]{8})$')


def sweep(base, top, step, log=print):
    """返回 {addr: value 或 None(=-7 死了)}，按地址排序。"""
    seen = {}
    addr = base
    guard = 0
    while addr < top:
        guard += 1
        if guard > 500:
            log('!! 重启次数异常，停下来（可能是每页都死）')
            break
        src = CHILD_SRC % (base, top, step, addr - base)
        try:
            r = subprocess.run([sys.executable, '-u', '-c', src],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=90)
            out = r.stdout.decode('utf-8', 'replace')
            rc = r.returncode
        except subprocess.TimeoutExpired:
            log('!! 子进程超时（挂住了，不是 SIGBUS）—— 停')
            break

        last = None
        for ln in out.splitlines():
            ln = ln.strip()
            mo = LINE_OK.match(ln)
            if mo:
                a = int(mo.group(1), 16)
                seen[a] = int(mo.group(2), 16)
                last = a

        if 'DONE' in out:
            break                      # 全扫完

        # 子进程死了 —— 下一页就是肇事页
        nxt = (last + step) if last is not None else addr
        if nxt >= top:
            break
        seen[nxt] = None               # None 表示 **SIGBUS**
        log('    [死] 0x%08X  rc=%d（%s）' %
            (nxt, rc, {0: '正常退出?', -7: 'SIGBUS', -11: 'SIGSEGV'}.get(rc, '信号 %d' % (-rc))))
        addr = nxt + step

    return dict(sorted(seen.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description='AXI-Lite 地址空间存活扫描（只读）')
    ap.add_argument('--base', default=hex(DEF_BASE))
    ap.add_argument('--top', default=hex(DEF_TOP))
    ap.add_argument('--step', default=hex(DEF_STEP))
    ap.add_argument('--map', action='store_true',
                    help='扫 .hwh 里的每一个 MEMRANGE（而不是单个连续区间）')
    args = ap.parse_args(argv)
    step = int(args.step, 16)

    print('=' * 72)
    if args.map:
        print('axil_sweep --map —— 把 .hwh 的**每一个** MEMRANGE 都扫一遍（只读）')
        ranges = MAP_RANGES
    else:
        print('axil_sweep —— 每 0x%X 一页，只读' % step)
        ranges = [(int(args.base, 16), int(args.top, 16))]
    print('=' * 72)
    try:
        with open(FPGA_STATE) as f:
            print('[env] fpga_manager state = %s' % f.read().strip())
    except OSError as e:
        print('[env] 读不到 %s：%s' % (FPGA_STATE, e))

    res = {}
    for i, (lo, hi) in enumerate(ranges):
        print('')
        print('--- 区间 %d/%d：0x%08X..0x%08X ---' % (i + 1, len(ranges), lo, hi))
        got = sweep(lo, hi, step, log=print)
        res.update(got)
        bad = [a for a, v in got.items() if v is None]
        print('    该区间：活 %d / 死 %d' % (len(got) - len(bad), len(bad)))

    base, top = min(res), max(res) + step

    print('')
    print('--- 结果表（★ 标出来了的是 .hwh 里登记过的 IP）---')
    dead, alive = [], []
    for a, v in sorted(res.items()):
        note = ''
        for ea, en in EXPECT:
            if a == ea:
                note = '   <- 应该是 %s' % en
        if v is None:
            dead.append(a)
            print('  [XX]  0x%08X%s' % (a, note))
            if note:
                print('          ^^^ **这个 IP 死了**')
        else:
            alive.append(a)
            print('  [OK]  0x%08X  0x%08X%s' % (a, v, note))

    print('')
    print('--- 汇总 ---')
    print('  活的页 %d 个，死的页 %d 个（共 %d）' % (len(alive), len(dead), len(res)))

    print('')
    print('--- 判读 ---')
    if not dead:
        print('  全活 —— 那就不是"地址有没有从机"的问题，回去查别的原因。')
    else:
        lo, hi = min(dead), max(dead)
        contiguous = (len(dead) == (hi - lo) // step + 1)
        if contiguous and lo == base and hi >= top - step:
            print('  B) **整片都死** ⇒ PL 里的 bit 不是我们的那个（或 fpga_manager 没真下进去）。')
            print('     先比对板上 bit 与 .hwh 是谁的。')
        elif contiguous and len(dead) <= 4:
            print('  A) 死的是**一小片连续的**（0x%08X..0x%08X，%d 页）⇒ 问题在那几个从机自己。'
                  % (lo, hi, len(dead)))
        else:
            print('  C) 死的页**不连续**或分布怪 ⇒ 地址图与 .hwh 对不上，逐个对名字。')

    print('')
    print('★ 本次扫描**只读**，没写任何一个寄存器。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
