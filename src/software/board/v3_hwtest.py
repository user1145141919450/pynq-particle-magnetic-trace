# -*- coding: utf-8 -*-
"""v3_hwtest.py —— V3 并行轨迹束的**板上实测与参考比对**（比赛硬要求的那一项）。

做什么：
  1) 在板上用**定点黄金模型**（tools/v3_fixedpoint.py 那份，逐位基准）算一遍
     17 路扫描的结果 + 记下 **PS 串行耗时**
  2) 把同一场景写进 PL（AXI-Lite @0x43C00000）→ 启动 → 等 done → 读回 17 路结果
  3) **逐位比对**（res / 终点 / 步数 / 反弹数），并量出 PL 的墙上时间
  4) 打印 PS 串行 vs PL 并行的对比

用法（板上）：
  python3 /home/xilinx/wp/v3_hwtest.py [--nobit] [--case fan|goal]

退出码：0 = 逐位一致；1 = 有差异；9 = 环境问题（没有 V3 的 bit / MMIO 不通）
"""

import os
import sys
import time

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import numpy as np

BASE = 0x40000000          # V3 的 AXI-Lite 窗口（**从新 .hwh 里读出来的真值**：BD 里用
                           #  axi4 自动化分配，它挑的是 0x40000000，不是我手写的 0x43C00000
                           #  —— 第一次跑就是读错地址吃了个 SIGBUS）
NLANES, NBLK = 17, 9

# 寄存器偏移（与 rtl/v3_bundle/v3_bundle_axi.v 头注释一致）
R_CTRL, R_STATUS = 0x000, 0x004
R_X0, R_Y0, R_STEPS, R_BOUNCE = 0x010, 0x014, 0x018, 0x01C
R_BALLR, R_FW, R_FH, R_MISC = 0x020, 0x024, 0x028, 0x02C
R_BLK, R_HOLE, R_LANEV, R_RES = 0x100, 0x200, 0x300, 0x400
BLK_STRIDE, HOLE_STRIDE, LANEV_STRIDE, RES_STRIDE = 20, 12, 8, 16


def build_scene(case='fan'):
    """与 tools/v3_dump_tb.py 同一个场景（seed=7，3 块场，±8° 扇形）。"""
    import random
    from engine import World
    import v3_fixedpoint as V
    random.seed(7)
    w = World()
    w.setup_round(3)
    if case == 'goal':
        fields = [(0.0, 0.0, 960.0, 540.0, 0.0)]
        holes = [dict(edge='right', c=127.0, w=44.0, target=0),
                 dict(edge='left', c=134.0, w=44.0, target=1)]
        x0, y0, ang0, sp, q = 100.0, 127.0, 0.0, 400.0, 1
        steps, bounce = 3000, 0
    else:
        fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in w.blocks]
        holes = list(w.holes)
        x0, y0, ang0, sp, q = 100.0, 270.0, 0.0, 400.0, 1
        steps, bounce = 120, 0
    side = 0 if x0 < V.FIELD_W / 2 else 1
    return dict(fields=fields, holes=holes, x0=x0, y0=y0, ang0=ang0, sp=sp, q=q,
                steps=steps, bounce=bounce, side=side, V=V)


def golden(sc):
    """ps 串行跑一遍（顺便计时）—— 这就是"参考值"，也是 PS 的性能基准。"""
    V = sc['V']
    t0 = time.time()
    exp, vs = [], []
    for k in range(-8, 9):
        ang = sc['ang0'] + k
        f = V.lane_fixed(sc['x0'], sc['y0'], ang, sc['sp'], sc['q'],
                         sc['fields'], sc['holes'], sc['side'],
                         steps=sc['steps'], max_bounce=sc['bounce'])
        import math
        a = math.radians(ang)
        vs.append((V.to_vel(sc['sp'] * math.cos(a)), V.to_vel(sc['sp'] * math.sin(a))))
        exp.append(((f['rtl_res'] & 3) << 52) | ((V.to_pos(f['x']) & 0x3FFFF) << 34) |
                   ((V.to_pos(f['y']) & 0x3FFFF) << 16) |
                   ((f['steps'] & 0xFFF) << 4) | (f['bounces'] & 0xF))
    return exp, vs, (time.time() - t0) * 1000.0


def main():
    case = 'fan'
    for i, a in enumerate(sys.argv):
        if a == '--case' and i + 1 < len(sys.argv):
            case = sys.argv[i + 1]
    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载（注意：必须是含 V3 的新 bit）')

    sc = build_scene(case)
    exp, vs, ps_ms = golden(sc)
    print('[参考] 黄金模型（PS 串行）%d 路跑完：%.1f ms' % (NLANES, ps_ms))

    try:
        from pynq import MMIO
        mm = MMIO(BASE, 0x10000)
    except Exception as e:
        print('=> 环境问题：MMIO 打不开 0x%08X（%s）—— 这份 bit 里没有 V3？' % (BASE, str(e)[:60]))
        return 9
    try:
        rid = mm.read(R_STATUS)
    except Exception as e:
        print('=> 环境问题：读 0x%08X 失败（%s）' % (BASE + R_STATUS, str(e)[:60]))
        return 9
    print('[探针] STATUS=0x%08X（新 bit 应为 0）' % rid)

    V = sc['V']
    mm.write(R_CTRL, 0)                      # 清 start
    mm.write(R_X0, V.to_pos(sc['x0']))
    mm.write(R_Y0, V.to_pos(sc['y0']))
    mm.write(R_STEPS, sc['steps'])
    mm.write(R_BOUNCE, sc['bounce'])
    mm.write(R_BALLR, V.to_pos(V.BALL_R))
    mm.write(R_FW, V.to_pos(V.FIELD_W))
    mm.write(R_FH, V.to_pos(V.FIELD_H))
    mm.write(R_MISC, (len(sc['holes']) << 2) | sc['side'])
    for i, (bx0, by0, bx1, by1, bb) in enumerate(sc['fields']):
        ang = V.to_ang(V.OMEGA_SIGN * sc['q'] * bb / V.K_RADIUS * V.DT) if abs(bb) > 1e-9 else 0
        mm.write(R_BLK + i*BLK_STRIDE + 0, ang & 0x7FFFFF)
        mm.write(R_BLK + i*BLK_STRIDE + 4, V.to_pos(bx0))
        mm.write(R_BLK + i*BLK_STRIDE + 8, V.to_pos(by0))
        mm.write(R_BLK + i*BLK_STRIDE + 12, V.to_pos(bx1))
        mm.write(R_BLK + i*BLK_STRIDE + 16, V.to_pos(by1))
    for h in range(4):
        if h < len(sc['holes']):
            hh = sc['holes'][h]
            e = {'left': 0, 'right': 1, 'top': 2, 'bottom': 3}[hh['edge']]
            mm.write(R_HOLE + h*HOLE_STRIDE + 0, V.to_pos(hh['c']))
            mm.write(R_HOLE + h*HOLE_STRIDE + 4, V.to_pos(hh['w']))
            mm.write(R_HOLE + h*HOLE_STRIDE + 8,
                     (1 if hh['target'] == sc['side'] else 0) << 3 | (e << 1) | 1)
        else:
            mm.write(R_HOLE + h*HOLE_STRIDE + 0, 0)
            mm.write(R_HOLE + h*HOLE_STRIDE + 4, 0)
            mm.write(R_HOLE + h*HOLE_STRIDE + 8, 0)
    for i, (vx, vy) in enumerate(vs):
        mm.write(R_LANEV + i*LANEV_STRIDE + 0, vx & 0xFFFFF)
        mm.write(R_LANEV + i*LANEV_STRIDE + 4, vy & 0xFFFFF)

    # 启动 + 计时 + 等 done
    t0 = time.time()
    mm.write(R_CTRL, 1)
    st = 0
    for _ in range(200000):
        st = mm.read(R_STATUS)
        if st & 1:
            break
    pl_ms = (time.time() - t0) * 1000.0
    if not (st & 1):
        print('=> ★ 等不到 done（STATUS=0x%08X）' % st)
        return 9
    print('[实测] PL 扫完（含 MMIO 往返）：%.2f ms   模块自报 %d 周期（低 8 位）'
          % (pl_ms, (st >> 8) & 0xFF))

    # 读回 + 逐位比对
    nbad = 0
    for i in range(NLANES):
        w0 = mm.read(R_RES + i*RES_STRIDE + 0)
        w1 = mm.read(R_RES + i*RES_STRIDE + 4)
        w2 = mm.read(R_RES + i*RES_STRIDE + 8)
        w3 = mm.read(R_RES + i*RES_STRIDE + 12)
        got = ((w0 >> 18) & 3) << 52 | (w0 & 0x3FFFF) << 34 | (w1 & 0x3FFFF) << 16 | \
              (w2 & 0xFFF) << 4 | (w3 & 0xF)
        if got != exp[i]:
            nbad += 1
            print('  ★ 路 %2d 不一致：期望 %014X 实测 %014X' % (i, exp[i], got))
    print('')
    if nbad == 0:
        print('>>> PASS：板上 17 路结果与定点黄金模型**逐位一致**')
    else:
        print('>>> FAIL：%d 路不一致' % nbad)
    print('    对比：PS 串行 %.1f ms  /  PL（含 MMIO 往返）%.2f ms  ->  加速 %.0f 倍'
          % (ps_ms, pl_ms, ps_ms / max(1e-6, pl_ms)))
    return 0 if nbad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
