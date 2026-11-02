# -*- coding: utf-8 -*-
"""v3_cmp2.py — 定位"PL 一撞墙就停"：先回读寄存器，再扫 BOUNCE / STEPS。

从 v3_cmp.py 的实测（2026-09-28 深夜）得到的关键事实：
  · **不碰墙的路**（steps 以内没到边界）→ PL 与黄金模型**逐位一致** ✓
    所以场区/洞/球径/坐标/初速度/积分全都是对的，寄存器图也是对的
  · **碰了一次墙的路** → PL 就停在那儿（res=2, n_bounce=1），黄金模型却继续弹
  · 同一路在 120/400/1000/2000 步下 PL 回读**完全一样**（因为都停在第一次撞墙处）

所以嫌疑只剩一个：**弹跳上限（MAX_BOUNCE）没生效 / 语义不同**。本脚本分三步验：
  A. **回读标量寄存器**：写完场景后把 0x010~0x02C 读回来，看写到底有没有落进去
     （尤其 0x018 MAX_STEPS、0x01C MAX_BOUNCE）—— 写不进去，PL 就只能按复位默认值
     `r_steps=96 / r_bounce=0` 跑（RTL 复位值就写着 0！），那"撞墙即停"就完全说得通
  B. **扫 BOUNCE**（0/1/2/5/10）：看 PL 的行是不是随手写的值变
  C. **扫 STEPS**（120/300/600）：看 n_step 是不是随写的值变

用法（板上，sudo）：python3 -u /home/xilinx/wp/v3_cmp2.py
"""

import sys

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import v3_scan
import v3_pl
import v3_fixedpoint as V

X0, Y0, ANG0, SPD, FAN, WIN = 100.0, 170.0, 0.0, 400.0, 24.0, 8
WATCH = [0, 8, 16]                      # 挑 3 路看：-24°（撞上墙）、0°（0°附近）、+24°（撞右墙）


def scene(world, steps, bounce):
    return dict(fields=[(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks],
                holes=list(world.holes), x0=X0, y0=Y0, ang0=ANG0, sp=SPD,
                q=1, steps=steps, bounce=bounce, side=0, fan_half=FAN)


def readback(mm, sc, V):
    """把写进去的东西读回来对一遍 —— 寄存器通路的最直接证据。"""
    print('  [回读] X0=%d(期望%d) Y0=%d(期望%d)' %
          (mm.rd_u32(v3_pl.R_X0), V.to_pos(sc['x0']),
           mm.rd_u32(v3_pl.R_Y0), V.to_pos(sc['y0'])))
    print('  [回读] STEPS=%d(期望%d)  BOUNCE=%d(期望%d)' %
          (mm.rd_u32(v3_pl.R_STEPS), sc['steps'],
           mm.rd_u32(v3_pl.R_BOUNCE), sc['bounce']))
    print('  [回读] BALLR=%d(期望%d) FW=%d FH=%d MISC=%d(期望%d)' %
          (mm.rd_u32(v3_pl.R_BALLR), V.to_pos(V.BALL_R),
           mm.rd_u32(v3_pl.R_FW), mm.rd_u32(v3_pl.R_FH), mm.rd_u32(v3_pl.R_MISC),
           (len(sc['holes']) << 2) | sc['side']))
    w0 = mm.rd_u32(v3_pl.R_BLK + 0)
    print('  [回读] 块0: ang=%d(期望%d) x0=%d y0=%d x1=%d y1=%d' %
          (w0, V.to_ang(1 * sc['fields'][0][4] / V.K_RADIUS * V.DT) if abs(sc['fields'][0][4]) > 1e-9 else 0,
           mm.rd_u32(v3_pl.R_BLK + 4), mm.rd_u32(v3_pl.R_BLK + 8),
           mm.rd_u32(v3_pl.R_BLK + 12), mm.rd_u32(v3_pl.R_BLK + 16)))
    print('  [回读] 洞0: c=%d(期望%d) w=%d(期望%d) meta=%d' %
          (mm.rd_u32(v3_pl.R_HOLE + 0), V.to_pos(sc['holes'][0]['c']) & 0x3FFFF,
           mm.rd_u32(v3_pl.R_HOLE + 4), V.to_pos(sc['holes'][0]['w']) & 0x3FFFF,
           mm.rd_u32(v3_pl.R_HOLE + 8)))
    k16 = WIN + 8
    print('  [回读] 路16 速度: vx=%d vy=%d' %
          (mm.rd_u32(v3_pl.R_LANEV + k16 * v3_pl.LANEV_STRIDE),
           mm.rd_u32(v3_pl.R_LANEV + k16 * v3_pl.LANEV_STRIDE + 4)))


def run(mm, world, steps, bounce, tag):
    sc = scene(world, steps, bounce)
    angs, _vs = v3_pl.write_scene(mm, sc, V, win=WIN)
    ms, st = v3_pl.start_and_wait(mm)
    rl = v3_pl.read_results(mm, V)
    print('')
    print('--- %s：steps=%d bounce=%d   PL 往返 %.2f ms  STATUS[15:8]=%d ---'
          % (tag, steps, bounce, ms, (st >> 8) & 0xFF))
    print('  路   角度 | 黄金: res  x        y      步 弹 | PL: res  x        y      步 弹')
    for kk in WATCH:
        a = angs[kk]
        ln = V.lane_fixed(X0, Y0, a, SPD, 1, sc['fields'], sc['holes'], 0,
                          steps=steps, max_bounce=bounce, max_pts=8000)
        res, ex, ey, nst, nb = rl[kk]
        print('  %2d %+6.1f |  %d %8.2f %8.2f %4d %2d |  %d %8.2f %8.2f %4d %2d'
              % (kk, a, ln['rtl_res'], ln['x'], ln['y'], ln['steps'], ln['bounces'],
                 res, ex, ey, nst, nb))
    return rl


def main():
    world = v3_scan.build_world(7, 0.40)
    mm = v3_pl.open_v3()
    print('[PL] 已就位（0x%08X）  场区块=%d 洞=%s'
          % (v3_pl.BASE, len(world.blocks),
             [(h['edge'], round(h['c']), h['target']) for h in world.holes]))

    print('')
    print('===== A) 寄存器回读（写完场景后立刻读回来）=====')
    sc = scene(world, 600, 10)
    v3_pl.write_scene(mm, sc, V, win=WIN)
    readback(mm, sc, V)

    print('')
    print('===== B) 扫 BOUNCE（steps=600 固定）=====')
    for bo in (0, 1, 2, 5, 10):
        run(mm, world, 600, bo, 'B')

    print('')
    print('===== C) 扫 STEPS（bounce=10 固定）=====')
    for stp in (120, 300, 600):
        run(mm, world, stp, 10, 'C')
    return 0


if __name__ == '__main__':
    sys.exit(main())
