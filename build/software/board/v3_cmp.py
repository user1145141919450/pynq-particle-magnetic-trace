# -*- coding: utf-8 -*-
"""v3_cmp.py — PL 与定点黄金模型**逐路**对照，定位"不一致出现在哪一类路上"。

为什么写它（2026-09-28 深夜实锤）：
  `v3_scan.py --arcs` 的板上自检报 **1/17 一致**，而 2026-09-27 的 `v3_hwtest.py`
  是**逐位一致**。两者差别只有一个显眼的：**hwtest 用 120 步，这轮用 2000 步**。
  所以本脚本用**同一场景**跑若干步数（120 / 400 / 1000 / 2000），逐路打印
  (角度, 黄金模型的 res/x/y/步数/弹跳, PL 的 res/x/y/步数/弹跳)：
    · 若"步数少时一致、步数大时不一致" -> 分歧随积分长度增长（数值/规则差异）
    · 若"某类路（如弹跳的）整体错"     -> 判定规则不同（bounce 计数口径）
    · 若"连 120 步都不一致"            -> 寄存器/写场景通路出了问题（往回查 v3_pl）

用法（板上，sudo）：
  python3 -u /home/xilinx/wp/v3_cmp.py            # 默认 seed=7 b=0.40 球位(100,170)
"""

import os
import sys

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import v3_scan                       # 复用 build_world（与出图用的是同一份场区构造）
import v3_pl
import v3_fixedpoint as V

X0, Y0, ANG0, SPD = 100.0, 170.0, 0.0, 400.0
FAN, WIN, BOUNCE = 24.0, 8, 10
STEPS_LIST = [120, 400, 1000, 2000]


def one(mm, world, steps):
    sc = dict(fields=[(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks],
              holes=list(world.holes), x0=X0, y0=Y0, ang0=ANG0, sp=SPD,
              q=1, steps=steps, bounce=BOUNCE, side=0, fan_half=FAN)
    angs, _vs = v3_pl.write_scene(mm, sc, V, win=WIN)
    ms, st = v3_pl.start_and_wait(mm)
    rl = v3_pl.read_results(mm, V)
    print('')
    print('===== 步数 %d ：PL 启动到读回 %.2f ms，STATUS 报周期低 8 位 %d ====='
          % (steps, ms, (st >> 8) & 0xFF))
    print(' 路    角度 |        黄金模型 res  x        y     步  弹 |          PL res  x        y     步  弹 | 判')
    bad = 0
    for kk, a in enumerate(angs):
        ln = V.lane_fixed(X0, Y0, a, SPD, 1, sc['fields'], sc['holes'], 0,
                          steps=steps, max_bounce=BOUNCE, max_pts=8000)
        res, ex, ey, nst, nb = rl[kk]
        same = (abs(ln['x'] - ex) < 0.02 and abs(ln['y'] - ey) < 0.02
                and ln['rtl_res'] == res)
        if not same:
            bad += 1
        print('%2d %+7.1f |  %d %9.2f %9.2f %5d %3d |  %d %9.2f %9.2f %5d %3d | %s'
              % (kk, a, ln['rtl_res'], ln['x'], ln['y'], ln['steps'], ln['bounces'],
                 res, ex, ey, nst, nb, 'ok' if same else '**DIFF**'))
    print('  --> 一致 %d/%d' % (len(angs) - bad, len(angs)))
    return bad


def main():
    world = v3_scan.build_world(7, 0.40)
    print('[场景] seed=7  B=±0.40  块=%d  洞=%s'
          % (len(world.blocks), [(h['edge'], round(h['c']), h['target']) for h in world.holes]))
    print('[扫描] 球位(%.0f,%.0f) 角度%.0f 速度%.0f 扇角±%.0f 弹跳上限%d'
          % (X0, Y0, ANG0, SPD, FAN, BOUNCE))
    mm = v3_pl.open_v3()
    print('[PL] 已就位（0x%08X）' % v3_pl.BASE)
    tot = 0
    for s in STEPS_LIST:
        tot += one(mm, world, s)
    print('')
    print('==== 合计不一致 %d 路次（4 个步数 x 17 路）====' % tot)
    return 0


if __name__ == '__main__':
    sys.exit(main())
