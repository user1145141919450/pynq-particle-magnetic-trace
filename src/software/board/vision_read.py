# -*- coding: utf-8 -*-
"""vision_read.py — 读 PL 视觉前处理（vision_core）的 128x90 缩略图

地址（见 bd/m1a_capture.tcl）：
    map   0x43B00000   32KB 窗口，缩略图占 23,040 B = 90x128 个 uint16
    ctrl  0x43B10000   通道1(0x00) = 输出 ctrl：bit0 freeze / bit1 soft_rst / bit2 swap_bytes
                       通道2(0x08) = 输入 frame_cnt
    stat  0x43B20000   通道1(0x00) = {上一帧行数[31:16], 上一帧写字数[15:0]}
                       通道2(0x08) = {行尾bcnt[31:21], errs[20:15], 本帧字数[14:0]}
                       健康值：stat=0x02D01680  stat2=0xFFE01680
                       （v2 起；完整的四项验收见 vision_diag.py）

为什么用 mmap 而不是 pynq MMIO：MMIO.read 只支持 <=8 字节、15µs/次，读 23KB 要
5760 次 = 86ms，30fps 下来不及；mmap + numpy 读 23KB 只要 ~1ms（见 fastmem.py）。

一致性：读前/读后各读一次 frame_cnt，相同才认为读到的是同一帧的完整缩略图。

用法（板上，需先跑过 dvp_probe.py 让传感器健康）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/vision_read.py
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/vision_read.py --raw   # 顺带抓全分辨率原图
输出：
    /boot/vmap.bin           缩略图原始字节（PC 侧 vision_golden.py --cmp 用）
    /boot/vmap.png           缩略图放大 8 倍（肉眼）
    /boot/frame_raw_v.bin    全分辨率原样字节（--raw 时有）
"""

import sys
import time

sys.path.insert(0, '/boot')

import cv2
import numpy as np

from fastmem import FastMem

W, H = 1024, 720
BW, BH = W // 8, H // 8                 # 128 x 90
MAP_BYTES = BW * BH * 2                 # 23,040
MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
STAT_BASE = 0x43B20000


def main():
    do_raw = '--raw' in sys.argv
    swap = 0
    raw_bytes = None

    if do_raw:
        # 复用球检测那套采集初始化（内含字节序自动判定）
        from ball_detect import _make_cap, _init_video
        cap = _make_cap()
        _init_video(cap)
        try:
            idx, raw = cap.frame(timeout=3.0)
            raw_bytes = np.ascontiguousarray(np.asarray(raw, np.uint8))
            with open('/boot/frame_raw_v.bin', 'wb') as f:
                f.write(raw_bytes.tobytes())
            print('[原图] %d 字节 -> /boot/frame_raw_v.bin' % raw_bytes.size)
        except Exception as e:
            print('[原图] 抓帧失败（%s），只读缩略图' % e)
        swap = 1 if getattr(cap, '_swap_decided', False) else 0
        print('[字节序] 驱动判定 swap=%d，写入 vision ctrl[2]' % swap)

    with FastMem(MAP_BASE, 0x8000) as mm, \
         FastMem(CTRL_BASE, 0x1000) as mc, \
         FastMem(STAT_BASE, 0x1000) as ms:

        # ---- 设 ctrl：freeze=0（正常跑），soft_rst 脉冲一次清干净 ----
        ctrl = (swap << 2)
        mc.wr_u32(0x00, ctrl | 0x02)          # 脉冲 soft_rst
        time.sleep(0.2)
        mc.wr_u32(0x00, ctrl)

        def show_stat(tag):
            st = ms.rd_u32(0x00)
            s2 = ms.rd_u32(0x08)
            print('[stat %s] 上一帧 %d 行（应 720）写 %d 字（应 5760） | '
                  'errs=0x%02X（应 0）行尾 bcnt=%d（应 2047）本帧已写 %d'
                  % (tag, st >> 16, st & 0xFFFF,
                     (s2 >> 15) & 0x3F, (s2 >> 21) & 0x7FF, s2 & 0x7FFF))

        show_stat('配置后')

        # ---- 等 frame_cnt 涨（证明缩略图在这套配置下真的在刷新）----
        c0 = mc.rd_u32(0x08)
        t0 = time.time()
        while mc.rd_u32(0x08) == c0 and time.time() - t0 < 3.0:
            time.sleep(0.01)
        c1 = mc.rd_u32(0x08)
        if c1 == c0:
            print('★ frame_cnt 3s 内没涨：vision_core 没在刷新（查 freeze 位、'
                  'bit 是否是新版、流是否在动）')
        else:
            print('[刷新] frame_cnt %d -> %d（%.0f ms 内），缩略图在更新'
                  % (c0, c1, (time.time() - t0) * 1000))

        # ---- 一致性读：读前后帧号相同才算同一帧 ----
        buf = None
        for k in range(6):
            a = mc.rd_u32(0x08)
            buf = mm.rd_u8_copy(0, MAP_BYTES)
            b = mc.rd_u32(0x08)
            if a == b:
                print('[读取] 第 %d 次拿到一致快照（frame %d，%d 字节）'
                      % (k + 1, a, MAP_BYTES))
                break
            print('  读期间帧号变了 %d->%d，重读' % (a, b))
        if buf is None:
            print('★ 6 次都没读到一致快照，放弃')
            return 1

        with open('/boot/vmap.bin', 'wb') as f:
            f.write(buf.tobytes())
        m = np.frombuffer(buf, np.uint16).reshape(BH, BW)
        b = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
        if swap:
            b = b[..., ::-1]
        bgr = cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR5652BGR)
        big = cv2.resize(bgr, (BW * 8, BH * 8), interpolation=cv2.INTER_NEAREST)
        cv2.imwrite('/boot/vmap.png', big)
        print('[输出] /boot/vmap.bin（%d 字节）+ /boot/vmap.png（放大 8 倍）'
              % MAP_BYTES)
        print('[统计] 缩略图 BGR 均值 %.1f 标准差 %.1f'
              % (bgr.mean(), bgr.std()))
        show_stat('读完后')
    return 0


if __name__ == '__main__':
    sys.exit(main())
