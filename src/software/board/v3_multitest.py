# -*- coding: utf-8 -*-
"""v3_multitest.py —— 板上多件物理 17/17 逐位对拍（3 障碍 + 2 对门）。

不碰相机/VDMA；只写 V3 AXI 场景 + 启动 + 读回，与定点黄金模型逐位比。
用法（root + pynq venv；游戏必须停）：
  echo xilinx | sudo -S -E bash -lc \
    "cd /home/xilinx/wp && /usr/local/share/pynq-venv/bin/python3 v3_multitest.py"

覆盖：
  none  无件（基础回归）
  axis  单根轴对齐障碍
  rot   单根 45° 旋转障碍（AABB 预过滤的角部）
  multi 3 根障碍 + 2 对门（本次新功能）
"""
import sys

import v3_fixedpoint as V
import v3_pl

FIELDS = [(0.0, 0.0, 960.0, 540.0, 0.0)]
HOLES = [dict(edge='right', c=127.0, w=44.0, target=0),
         dict(edge='left', c=134.0, w=44.0, target=0)]
X0, Y0, ANG0, SP, Q = 300.0, 270.0, 0.0, 400.0, 1
STEPS, BOUNCE = 600, 3

OBS_AXIS = [(500.0, 270.0, 40.0, 6.0, 0.0)]
OBS_ROT = [(900.0, 150.0, 30.0, 6.0, 45.0)]
OBS_MULTI = [(500.0, 270.0, 40.0, 6.0, 0.0),
             (900.0, 150.0, 30.0, 6.0, 45.0),
             (600.0, 330.0, 30.0, 6.0, -45.0)]
PORT_MULTI = [(420.0, 270.0, 1), (850.0, 150.0, 0),
              (420.0, 400.0, 3), (850.0, 400.0, 2)]

CASES = [
    ('none', [], []),
    ('axis', OBS_AXIS, []),
    ('rot', OBS_ROT, []),
    ('multi', OBS_MULTI, PORT_MULTI),
]


def _scene(obstacles, portals):
    return dict(fields=FIELDS, holes=HOLES, x0=X0, y0=Y0, ang0=ANG0,
                sp=SP, q=Q, steps=STEPS, bounce=BOUNCE, side=0,
                fan_half=8.0,
                obs=(obstacles[0] if obstacles else None),
                obs_list=list(obstacles), portals=list(portals))


def run_case(mm, name, obstacles, portals):
    sc = _scene(obstacles, portals)
    angs, _ = v3_pl.write_scene(mm, sc, V)
    ms, st = v3_pl.start_and_wait(mm)
    got = v3_pl.read_results(mm, V)
    bad = []
    for k, a in enumerate(angs):
        f = V.lane_fixed(X0, Y0, a, SP, Q, FIELDS, HOLES, 0,
                         steps=STEPS, max_bounce=BOUNCE,
                         obstacles=obstacles, portals=portals)
        res, ex, ey, nst, nb = got[k]
        if (res != f['rtl_res'] or abs(ex - f['x']) > 0.02
                or abs(ey - f['y']) > 0.02
                or nst != f['steps'] or nb != f['bounces']):
            bad.append((k, a, (res, ex, ey, nst, nb),
                        (f['rtl_res'], f['x'], f['y'], f['steps'], f['bounces'])))
    ok = not bad
    print('[%s] %-5s  17 路 %s  PL %.1fms  STATUS=0x%08X'
          % ('PASS' if ok else 'FAIL', name,
             '逐位一致' if ok else '有 %d 路不符' % len(bad), ms, st))
    for b in bad[:5]:
        print('   路%2d ang=%+.1f  PL=%s  黄金=%s' % b)
    return ok


def main():
    mm = v3_pl.open_v3()
    all_ok = True
    for name, obs, ports in CASES:
        try:
            all_ok = run_case(mm, name, obs, ports) and all_ok
        except Exception as e:
            print('[FAIL] %-5s  异常：%s' % (name, e))
            all_ok = False
    print('=> %s' % ('全部 PASS（多件物理与黄金逐位一致）' if all_ok
                     else '有 FAIL —— 不要下结论，逐条看上面'))
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
