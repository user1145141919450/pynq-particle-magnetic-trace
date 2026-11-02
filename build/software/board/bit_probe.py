# -*- coding: utf-8 -*-
"""bit_probe.py — 把"下 bit"和"用 PL"两步分开测，定位段错误死在哪一步

背景：2026-09-26 晚，verify_onboard.sh 跑到第 2 步（bram_write_map2）一切正常，
      紧接着第 3 步 wcnt_poll、第 4 步 vision_diag **双双段错误**，而且
      **一行输出都没有**（两个都用 python3 -u，所以不是缓冲问题）——
      说明崩在脚本最开头，最可能就是 `from pynq import Bitstream` 或
      `Bitstream(...).download()` 这一步（本板已知：Overlay(BIT) 必段错误，
      而 Bitstream 直下一直是能用的绕法；现在它也可能开始崩了）。

本脚本按顺序打印 + flush，哪一步之后再没输出，就是那一步崩的。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/bit_probe.py
"""

import os
import sys

BIT = '/boot/ov5640_m1a_wrapper.bit'


def step(n, s):
    print('[%d] %s' % (n, s))
    sys.stdout.flush()


step(1, '解释器与依赖')
print('    python %s' % sys.version.split()[0])
try:
    import numpy
    print('    numpy %s' % numpy.__version__)
except Exception as e:
    print('    numpy ★ %s' % e)
sys.stdout.flush()

step(2, '/boot 与 /home 的空间（FAT 写满会让很多事莫名其妙）')
os.system('df -h /boot /home/xilinx 2>&1')
step(3, '/boot 里我们自己的文件')
os.system('ls -la /boot/*.bin /boot/*.py 2>/dev/null | tail -25')

step(4, 'import pynq')
sys.stdout.flush()
from pynq import Bitstream
step(5, 'import 成功，开始 download bit')
sys.stdout.flush()
Bitstream(BIT).download()
step(6, '★ bit 下载成功 —— 问题不在这一步')
sys.stdout.flush()

step(7, '试着读一个 PL 寄存器（MMIO）')
from pynq import MMIO
mm = MMIO(0x43B20000, 0x1000)
print('    stat  = 0x%08X' % mm.read(0x00))
print('    stat2 = 0x%08X' % mm.read(0x08))
sys.stdout.flush()
step(8, '★ 全过 —— PL 侧没问题，段错误在别处')
