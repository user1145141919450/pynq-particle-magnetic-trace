# -*- coding: utf-8 -*-
"""v3_wall_sweep.py -- 量出"球贴到哪儿会让 PL 卡死"（板上跑）。

★ 为什么需要它（2026-09-29 凌晨）：P1 把 V3 接进游戏循环时发现，球位 (645,527)
  **按"球整体在场内"的几何判据完全合法**，却照样让 PL 一路 FSM 卡死（汇聚 done
  永不置位 -> start_and_wait 撞超时，读回的结果只有先跑完的那 16/17 路是新的）。
  几何判据不够用，真正的安全边界比它靠里 —— 于是沿四面墙**逐像素**扫一遍量出来。
  这份数据是 v3_scan.in_field() 里 IN_FIELD_MARGIN=8 的唯一依据。

★ 实测结果（2026-09-29，球半径 r=11、场区 960x540、17 路 ±12°、seed=7 b=0.40）：
    墙        安全        卡死        判据              卡死带宽度
    下墙(y)   y<=527      y>=528      y + r >= FH - 1   1px
    右墙(x)   x<=942      x>=943      x + r >= FW - 6   6px  <- 最宽
    上墙(y)   y>=13       y<=12       y - r <= 1        1px
    左墙(x)   x>=10       x<=9        x - r <= -2       2px（左墙还能容忍球探出 1px）
  · 与 max_steps **无关**：同一位置 steps=100/600/4000 全卡
    ⇒ 不是"步数不够算不完"，是那一路的步进计数器压根不走了（真死锁）。
  · 与角度**有关**：同一位置（600,528）ang=0/+12/+90 全卡，**ang=-12（背离墙）能跑完**
    ⇒ 触发条件是"起手就贴着墙、且朝墙走"。
  · 卡死**可恢复**：下一次 CTRL=1 会重新起一簇（卡死后紧接着跑场内位置立刻恢复 17/17）。
  · RTL 侧的根治办法（待办）：给每路的 FSM 加一个重试看门狗，任何情况下都必须终止
    并置 done —— 那样这类"意外位置"最多算错，不会把整簇拖住。

跑法（板上）：python3 /home/xilinx/wp/v3_wall_sweep.py
  判据：ok ≈ 2.7ms（稳定态 AXI 往返 2.4ms + 打印）；TIMEOUT ≈ 600ms（= start_and_wait 超时）
"""
import contextlib
import io
import sys
import time

sys.path.insert(0, '/home/xilinx/wp')

import v3_fixedpoint as V          # noqa: E402
import v3_pl                        # noqa: E402
import v3_scan                      # noqa: E402

R, FW, FH = v3_scan.BALL_R, v3_scan.FIELD_W, v3_scan.FIELD_H


def main():
    world = v3_scan.build_world(7, 0.40)
    mm = v3_pl.open_v3()

    def try_pos(x, y, ang=0.0, steps=600):
        sc = dict(fields=[(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks],
                  holes=list(world.holes), x0=float(x), y0=float(y), ang0=ang,
                  sp=400.0, q=1, steps=steps, bounce=v3_scan.PL_BOUNCE, side=0,
                  fan_half=12.0)
        buf = io.StringIO()
        t0 = time.perf_counter()
        try:
            with contextlib.redirect_stdout(buf):
                v3_scan.scan_bundle(world, V, [mm], sc, 8, True, False)
            return 'ok', (time.perf_counter() - t0) * 1000.0
        except v3_pl.PLTimeout:
            return 'TIMEOUT', (time.perf_counter() - t0) * 1000.0
        except Exception:                                   # noqa: BLE001
            return 'ERR', (time.perf_counter() - t0) * 1000.0

    print('[球半径=%g 场区=%gx%g]' % (R, FW, FH))
    print('')
    print('  下墙 y=526..529（粗扫: 526 ok / 528 TIMEOUT）')
    for y in (526, 527, 528):
        r, ms = try_pos(600, y)
        print('    y=%3d (底缘 %3.0f / 墙 %g)  %-9s %7.1f ms' % (y, y + R, FH, r, ms))
    print('  右墙 x=941..946（粗扫: 940 ok / 946 TIMEOUT）')
    for x in (941, 942, 943, 944, 945, 946):
        r, ms = try_pos(x, 300)
        print('    x=%3d (右缘 %3.0f / 墙 %g)  %-9s %7.1f ms' % (x, x + R, FW, r, ms))
    print('  上墙 y=13..20（粗扫: 12 TIMEOUT / 20 ok）')
    for y in (13, 14, 15, 16, 18, 20):
        r, ms = try_pos(400, y)
        print('    y=%3d (上缘 %3.0f)          %-9s %7.1f ms' % (y, y - R, r, ms))
    print('  左墙 x=7..10（粗扫: 8 TIMEOUT / 10 ok）')
    for x in (7, 8, 9, 10):
        r, ms = try_pos(x, 300)
        print('    x=%3d (左缘 %3.0f)          %-9s %7.1f ms' % (x, x - R, r, ms))

    print('')
    print('  ① 卡死与 max_steps 有关吗？（位置 (600,528) 本来就卡）')
    for st in (100, 600, 4000):
        r, ms = try_pos(600, 528, steps=st)
        print('    steps=%4d   %-9s %7.1f ms' % (st, r, ms))
    print('  ② 卡死与入射角有关吗？（位置 (600,528)，steps=600）')
    for a in (0.0, 12.0, -12.0, 90.0):
        r, ms = try_pos(600, 528, ang=a)
        print('    ang=%6.1f  %-9s %7.1f ms' % (a, r, ms))
    print('  ③ 场内安全位置 (600,500) 改步数/角度（应当一直 ok，用作对照）')
    for st in (100, 4000):
        r, ms = try_pos(600, 500, steps=st)
        print('    steps=%4d   %-9s %7.1f ms' % (st, r, ms))
    return 0


if __name__ == '__main__':
    sys.exit(main())
