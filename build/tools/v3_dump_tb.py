# -*- coding: utf-8 -*-
"""v3_dump_tb.py — 从定点黄金模型导出 xsim 测试平台的向量（hex，一行一个值）。

为什么单独一个文件（2026-09-27 的教训）：这份代码里有大量 '\\n' 与行尾续行，
而**用 Bash heredoc 写文件时工具层会吞掉转义**（'\\n' 变成真换行、行尾 '\\' 被吃掉）
—— 之前把 dump_tb 写进 v3_fixedpoint.py 时整个模块语法都坏了。用 Write 工具写
就没有这个问题。原则：**代码文件用 Write 工具写，Bash heredoc 只用来跑命令。**

导出的场景（与 engine.tangent_scan 的 win=8 一致）：
  17 路 = 入射角 base±8°、1° 步进；steps=300、max_bounce=0（= 撞墙即停）
  —— RTL v3.1 不做反弹，与黄金模型这一档完全一致。

用法：
  PYTHONPATH="E:/py_pkgs/vision" python tools/v3_dump_tb.py sim/v3
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v3_fixedpoint as V


def dump_tb(outdir='sim/v3', case='fan'):
    """case='fan'  : 真实 3 块场地、17 路 ±8° 扇形、steps=120（= UI 上那一小段扇形的尺寸）
       case='goal' : 零磁场直线场 + 一个我方洞（right c=127）—— 覆盖**进洞判定**分支
                     （±1° 以内那 3 路应 res=1 goal，其余撞墙 res=2）
    """
    from engine import World

    random.seed(7)
    w = World()
    w.setup_round(3)
    obstacles, portals = [], []
    if case == 'goal':
        fields = [(0.0, 0.0, 960.0, 540.0, 0.0)]          # 单块、零磁场 -> 直线
        holes = [dict(edge='right', c=127.0, w=44.0, target=0),
                 dict(edge='left', c=134.0, w=44.0, target=1)]
        x0, y0, ang0, sp, q = 100.0, 127.0, 0.0, 400.0, 1
        STEPS, BOUNCE = 3000, 0                            # 直线要够长才撞得到右墙
    elif case == 'bounce':
        # ★ 2026-10-04（第二版）：**与 v3_shoot/定妆照逐字同配置的真实场景**——
        #   seed7 三块场地、B=±0.40（隔块翻转，与游戏 build_world 的 b_fix 一致）、
        #   球位(300,230)、扇心 0°、速度 400、steps=2000、max_bounce=2。
        #   为什么坚持用生产配置（第一版踩过）：首版是零场几何，**罩不住"只有带
        #   B 场才现形"的错**——反射翻了旧速度而非新速度，零场下新旧速度相同、
        #   仿真六档全绿，上板定妆照却 10/17 端点不符。生产配置必须直接进仿真。
        for bi, b in enumerate(w.blocks):
            b.b = 0.40 if bi % 2 == 0 else -0.40
        fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in w.blocks]
        holes = list(w.holes)
        x0, y0, ang0, sp, q = 300.0, 230.0, 0.0, 400.0, 1
        STEPS, BOUNCE = 2000, 2
    elif case == 'phys':
        # ★ 2026-10-27 多件：零场 + 3 障碍 + 2 对门（走通 OP_PHYS 的按件循环）
        fields = [(0.0, 0.0, 960.0, 540.0, 0.0)]
        holes = [dict(edge='right', c=127.0, w=44.0, target=0),
                 dict(edge='left', c=134.0, w=44.0, target=0)]
        x0, y0, ang0, sp, q = 300.0, 270.0, 0.0, 400.0, 1
        STEPS, BOUNCE = 600, 3
        obstacles = [(500.0, 270.0, 40.0, 6.0, 0.0),
                     (900.0, 150.0, 30.0, 6.0, 45.0),
                     (600.0, 330.0, 30.0, 6.0, -45.0)]
        portals = [(420.0, 270.0), (850.0, 150.0),
                   (420.0, 400.0), (850.0, 400.0)]
    else:
        fields = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in w.blocks]
        holes = list(w.holes)
        x0, y0, ang0, sp, q = 100.0, 270.0, 0.0, 400.0, 1
        STEPS, BOUNCE = 120, 0
    side = 0 if x0 < V.FIELD_W / 2 else 1
    outdir = os.path.join(outdir, case)
    os.makedirs(outdir, exist_ok=True)

    def wr(name, values, width):
        path = os.path.join(outdir, name)
        with open(path, 'w') as f:
            for v in values:
                f.write('%0*x\n' % ((width + 3) // 4, int(v) & ((1 << width) - 1)))
        print('  %-16s %d 行 x %d bit' % (name, len(values), width))

    # ---- 块表：每项 95 位 = {ang[22:0], x1[17:0], y1[17:0], x0[17:0], y0[17:0]} ----
    blk = []
    for (bx0, by0, bx1, by1, bb) in fields:
        ang = V.to_ang(V.OMEGA_SIGN * q * bb / V.K_RADIUS * V.DT) if abs(bb) > 1e-9 else 0
        item = ((ang & ((1 << 23) - 1)) << 72)
        item |= (V.to_pos(bx0) & 0x3FFFF) << 54
        item |= (V.to_pos(by0) & 0x3FFFF) << 36
        item |= (V.to_pos(bx1) & 0x3FFFF) << 18
        item |= (V.to_pos(by1) & 0x3FFFF)
        blk.append(item)
        print('  块 B=%+.1f  x[%.0f..%.0f] y[%.0f..%.0f]  ang=%d'
              % (bb, bx0, bx1, by0, by1, ang))
    wr('blk_tab.mem', blk, 95)

    # ---- 洞表：每项 40 位 = {en, edge[1:0], target, w[17:0], c[17:0]} ----
    hl = []
    for i in range(4):
        if i < len(holes):
            h = holes[i]
            e = {'left': 0, 'right': 1, 'top': 2, 'bottom': 3}[h['edge']]
            item = (1 << 39) | ((e & 3) << 37)
            item |= (1 if h['target'] == side else 0) << 36
            item |= (V.to_pos(h['w']) & 0x3FFFF) << 18
            item |= (V.to_pos(h['c']) & 0x3FFFF)
            print('  洞 %s c=%.1f w=%.1f target=%d%s'
                  % (h['edge'], h['c'], h['w'], h['target'],
                     '  <- 算我方(可进)' if h['target'] == side else '  (对方洞=墙)'))
        else:
            item = 0
        hl.append(item)
    wr('hole_tab.mem', hl, 40)

    # ---- 17 路初速度 + 期望结果 ----
    lanes_v, exp = [], []
    for k in range(-8, 9):
        ang = ang0 + k
        a = math.radians(ang)
        vxi, vyi = V.to_vel(sp * math.cos(a)), V.to_vel(sp * math.sin(a))
        lanes_v.append(((vxi & 0xFFFFF) << 20) | (vyi & 0xFFFFF))
        f = V.lane_fixed(x0, y0, ang, sp, q, fields, holes, side,
                         steps=STEPS, max_bounce=BOUNCE,
                         obstacles=obstacles, portals=portals)
        item = (f['rtl_res'] & 3) << 52
        item |= (V.to_pos(f['x']) & 0x3FFFF) << 34
        item |= (V.to_pos(f['y']) & 0x3FFFF) << 16
        item |= (f['steps'] & 0xFFF) << 4
        item |= (f['bounces'] & 0xF)
        exp.append(item)
        print('  路%2d  ang=%+5.1f  res=%d  终点(%7.2f,%7.2f)  步数=%3d  反弹=%d  [%s]'
              % (k + 9, ang, f['rtl_res'], f['x'], f['y'], f['steps'], f['bounces'], f['stop']))
    wr('lane_v.mem', lanes_v, 40)
    wr('exp.mem', exp, 54)

    # ---- 参数：x0 y0 max_steps max_bounce ball_r field_w field_h side n_holes ----
    params = [V.to_pos(x0), V.to_pos(y0), STEPS, BOUNCE, V.to_pos(V.BALL_R),
              V.to_pos(V.FIELD_W), V.to_pos(V.FIELD_H), side, len(holes)]
    wr('params.mem', params, 32)
    if case == 'phys':
        # ---- 43 字：3 障碍 x11 + 2 对门 x5（与 tb_traj_bundle 的 phys_mem 对齐）----
        phys = []
        for oi in range(3):
            if oi < len(obstacles):
                cx, cy, hw, hh, ang = [float(v) for v in obstacles[oi]]
                rad = math.radians(ang)
                ca, sa = math.cos(rad), math.sin(rad)
                # ★ 与 v3_pl.write_scene 同口径：覆盖扩张后的旋转矩形
                ax = abs(ca) * (hw + V.BALL_R) + abs(sa) * (hh + V.BALL_R)
                ay = abs(sa) * (hw + V.BALL_R) + abs(ca) * (hh + V.BALL_R)
                ab = V.clamp_aabb((cx - ax, cy - ay, cx + ax, cy + ay))
                phys += [1,
                         V.to_pos(ab[0]) & 0x3FFFF, V.to_pos(ab[1]) & 0x3FFFF,
                         V.to_pos(ab[2]) & 0x3FFFF, V.to_pos(ab[3]) & 0x3FFFF,
                         V.to_pos(cx) & 0x3FFFF, V.to_pos(cy) & 0x3FFFF,
                         V.to_pos(hw + V.BALL_R) & 0x3FFFF,
                         V.to_pos(hh + V.BALL_R) & 0x3FFFF,
                         int(round(math.cos(-rad) * (1 << 20))) & 0x7FFFFF,
                         int(round(math.sin(-rad) * (1 << 20))) & 0x7FFFFF]
            else:
                phys += [0] * 11
        for pi in range(2):
            if 2 * pi + 1 < len(portals):
                p0, p1 = portals[2 * pi], portals[2 * pi + 1]
                phys += [1, V.to_pos(p0[0]) & 0x3FFFF, V.to_pos(p0[1]) & 0x3FFFF,
                         V.to_pos(p1[0]) & 0x3FFFF, V.to_pos(p1[1]) & 0x3FFFF]
            else:
                phys += [0] * 5
        wr('phys.mem', phys, 32)
    print('向量已写入 %s/' % outdir)
    return 0


if __name__ == '__main__':
    base = sys.argv[1] if len(sys.argv) > 1 else 'sim/v3'
    for c in ('fan', 'goal', 'bounce', 'phys'):
        print('==== case %s ====' % c)
        dump_tb(base, c)
    sys.exit(0)
