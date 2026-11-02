# -*- coding: utf-8 -*-
"""（已并入 dvp_regs）把 PL 里**锁住的静止位**清掉 —— 兼容用的薄壳。

2026-09-29 变更：这个脚本原来的逻辑（读现状 -> 写 dbg_sel=7 -> 撑 100ms -> 放开 ->
盯 12 秒判活）**整体搬进了 `dvp_regs.py`**，因为：
  · 同一件事有两份实现 = 迟早分叉（本工程最贵的一类 bug）；
  · 老脚本的结论行还有个口径错：它拿**自由跑的累加计数器**（pclk64/vsync 是
    累计值，不是每秒速率）去对"每秒 1315000 / 30"的阈值，于是链路明明是好的，
    它也会打印"还是死的"。实测里这条误判差点把一次成功的解锁当成失败。
  · 位定义也在里面按 RTL 逐条核对了（bit0 xclk_off / bit1 pwdn / bit3 vid_rst /
    bit4 sensor_rst），并加了"退出必净"的兜底。

现在直接用 dvp_regs（用法见它的文件头）：
    sudo -E PYTHONIOENCODING=utf-8 python3 -u dvp_regs.py --dump       # 只读，一屏读数
    sudo -E PYTHONIOENCODING=utf-8 python3 -u dvp_regs.py --unlatch    # 清静止位（写读回自检）
    sudo -E PYTHONIOENCODING=utf-8 python3 -u dvp_regs.py --unlatch --force
想一句判"链路到底活没活"，用 link_boot：
    python3 link_boot.py --health       # 判据齐全 + 一句结论（速率是按增量算的）

这个文件保留只为了让老命令/肌肉记忆还能用：它把参数原样转给 dvp_regs。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    if '--health' in argv:                       # 想要"活/死"的结论 -> link_boot
        cmd = [sys.executable, os.path.join(HERE, 'link_boot.py'), '--health']
    else:                                        # 否则 -> dvp_regs（--unlatch/--dump）
        args = [a for a in argv if a in ('--unlatch', '--force')] or ['--dump']
        cmd = [sys.executable, os.path.join(HERE, 'dvp_regs.py')] + args
    print('[pl_revive] 已并入 %s，转交：%s'
          % (os.path.basename(cmd[1]), ' '.join(cmd[1:])), flush=True)
    return subprocess.call(cmd)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
