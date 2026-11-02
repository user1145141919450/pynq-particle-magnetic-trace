# -*- coding: utf-8 -*-
"""map_snapshot.py — 把 PL 里**此刻**那张 128x90 缩略图存成 dump（纯只读）。

为什么要有它（2026-09-29）：
  要回答"检测为什么 88ms"，得拿**真实数据**去量（mapdet_bench），而以前唯一的
  dump 入口是 `ball_map_detect.py --dump` —— 那个工具**要开相机 / 要动 VDMA**。
  可"量性能"这件事不该跟"动场景"绑在一起：用户在摆球、链路又不稳的时候，
  每次量一笔账都要先打招呼，账就永远量不完。
  地图 BRAM 里躺着视觉核写进去的最后一帧 —— **读它不需要任何相机动作**。

  ★ 只读：不写 ctrl、不碰 VDMA、不 allocate、不 reset —— 既不救链路也不害链路。
  ★ 一致性：读数前后比帧号（照抄 ball_map_detect.read_map 的口径），不一致就重读，
    免得把夹着两帧的撕裂图当数据（那张图上的候选数会离谱）。

用法（板上，**root + 登录 shell**，理由见 pl_probe）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u map_snapshot.py [out.bin]"
默认 out.bin = /home/xilinx/wp/map_snap.bin，打完直接喂：
  python3 -u mapdet_bench.py --bin /home/xilinx/wp/map_snap.bin
"""

import os
import sys

sys.path.insert(0, '/home/xilinx/wp')

MAP_BASE = 0x43B00000            # 缩略图窗口（128x90 uint16 = 23040 字节）
CTRL_BASE = 0x43B10000           # +0x08 = 帧计数
MAP_BYTES = 128 * 90 * 2


def main(argv):
    out = argv[0] if argv else '/home/xilinx/wp/map_snap.bin'
    from fastmem import FastMem

    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc:
        buf, ok = None, False
        for k in range(8):
            a = mc.rd_u32(0x08)
            buf = mm.rd_u8_copy(0, MAP_BYTES)
            b = mc.rd_u32(0x08)
            if a == b:
                print('[快照] 一致读成功（第 %d 次），frame_cnt=%d' % (k + 1, a))
                ok = True
                break
        if not ok:
            # 链路在抖的时候也别浪费这一趟：存下来 + 明确标注夹帧风险
            print('[快照] ★ 8 次都没读到前后一致的帧号（链路在抖）—— 这张图可能夹着两帧，'
                  '拿它量候选数会偏大')

    with open(out, 'wb') as f:
        f.write(buf)
    print('[快照] 已存 %s（%d 字节）' % (out, os.path.getsize(out)))
    print('[快照] 下一步：python3 -u mapdet_bench.py --bin %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
