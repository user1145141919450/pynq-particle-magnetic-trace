# -*- coding: utf-8 -*-
"""patch_scan_pl.py —— 把 v3_scan.py 的"临场扫描"从 PS（Python）换成 **PL（V3）**。

改三处：
  1) render()：每条轨迹的**终点画实心点**（PL 只给终点、没有中间点），
     于是 PL 模式 = 一排彩色终点 + **临界那一条的完整圆弧**。
  2) 扫描主体：写场景 -> 启动 -> 读 17 路结果（用共享模块 v3_pl，地址/寄存器图只此一份）；
     保留 `--ps` 回退（两边对比用）。
  3) 顶部 import v3_fixedpoint（PL 路要用它的定点换算）。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'v3_scan.py'
s = io.open(p, encoding='utf-8').read()

# ---------- 1) render：终点打点 ----------
old = """        for j in range(1, len(pts)):
            p0 = (int(pts[j - 1][0] * S), int(pts[j - 1][1] * S))
            p1 = (int(pts[j][0] * S), int(pts[j][1] * S))
            cv2.line(img, p0, p1, col, wdt)"""
new = """        for j in range(1, len(pts)):
            p0 = (int(pts[j - 1][0] * S), int(pts[j - 1][1] * S))
            p1 = (int(pts[j][0] * S), int(pts[j][1] * S))
            cv2.line(img, p0, p1, col, wdt)
        # ★ PL 模式只给终点（没有中间轨迹点）-> 在终点画个实心点，颜色仍按命运分
        if pts:
            cv2.circle(img, (int(pts[-1][0] * S), int(pts[-1][1] * S)),
                       5 if i != crit_idx else 9, col, -1)"""
assert old in s, 'render 里找不到画线那段'
s = s.replace(old, new, 1)

# ---------- 2) import 黄金模型 ----------
old = "    bg_words = load_bg_words()"
new = """    import v3_fixedpoint as V          # 黄金模型：PL 路的定点换算（to_pos/to_ang/to_vel）
    bg_words = load_bg_words()"""
assert old in s
s = s.replace(old, new, 1)

# ---------- 3) 扫描主体 ----------
a = s.index('                spd = speed_fix if speed_fix > 0 else float(np.hypot(vx, vy))')
b = s.index("                    print('[输出] %s/%s' % (outdir, dump))")
b = s.index(chr(10), b) + 1
new_block = '''                spd = speed_fix if speed_fix > 0 else float(np.hypot(vx, vy))
                # ---- 场景（PL / PS 共用同一份参数）----
                sc = dict(fields=[(b_.x0, b_.y0, b_.x1, b_.y1, b_.b) for b_ in world.blocks],
                          holes=list(world.holes), x0=bx, y0=by, ang0=ang, sp=spd,
                          q=1, steps=steps, bounce=10, side=0)
                t0 = time.perf_counter()
                if use_pl:
                    import v3_pl
                    global _mm_pl
                    if _mm_pl is None:
                        _mm_pl = v3_pl.open_v3()
                        print('[PL] V3 已就位（0x%08X）' % v3_pl.BASE)
                    angs, _vs = v3_pl.write_scene(_mm_pl, sc, V, win=win)
                    pl_ms, st = v3_pl.start_and_wait(_mm_pl)
                    rl = v3_pl.read_results(_mm_pl, V)
                    fan = []
                    for kk, (res, ex, ey, nst, nb) in enumerate(rl):
                        kind = {1: 'goal', 2: 'hit_wall', 0: 'none'}[res]
                        fan.append((angs[kk], [(ex, ey)], (kind, 1 if res == 2 else 0, None)))
                    crit = None
                    for i, (a2, _pts, rr) in enumerate(fan):
                        if rr[1] > 0 and rr[0] != 'goal':
                            crit = i
                    if crit is not None:
                        # 只给临界那条画**真弧线**（一次 PS preview ~ 十几 ms；其余只画终点）
                        pts_c, _r, _h = world.preview(bx, by, fan[crit][0], spd, 1,
                                                      steps=steps, max_bounce=10)
                        fan[crit] = (fan[crit][0], pts_c, fan[crit][2])
                    wall_ms = (time.perf_counter() - t0) * 1000.0
                    scan_ms = wall_ms
                    print('        -> PL 扫完 %d 路：启动到读回 %.2f ms（含 MMIO 往返），'
                          '临界 = %s' % (len(fan), wall_ms,
                                       'None' if crit is None else '%.1f°' % fan[crit][0]))
                else:
                    fan = []
                    for kk in range(-win, win + 1):
                        a2 = ang + kk
                        pts, res, hole = world.preview(bx, by, a2, spd, 1,
                                                       steps=steps, max_bounce=10)
                        touched = any(p_[0] <= BALL_R + 2.5 or p_[0] >= FIELD_W - BALL_R - 2.5 or
                                      p_[1] <= BALL_R + 2.5 or p_[1] >= FIELD_H - BALL_R - 2.5
                                      for p_ in pts)
                        fan.append((a2, pts, (res, 1 if touched else 0, hole)))
                    crit = None
                    for i, (a2, pts, rr) in enumerate(fan):
                        if (rr[0] == 'goal' and rr[2] and pts and
                                abs(abs(pts[-1][1] - rr[2]['c']) - rr[2]['w'] / 2.0) < 6.0):
                            crit = i
                            break
                    if crit is None:
                        for i, (a2, pts, rr) in enumerate(fan):
                            if rr[1] > 0 and rr[0] != 'goal':
                                crit = i
                    scan_ms = (time.perf_counter() - t0) * 1000.0
                    print('        -> %d 条轨迹，临界 = %s，PS 串行耗时 %.1f ms'
                          % (len(fan), 'None' if crit is None else
                             '%.1f°' % fan[crit][0], scan_ms))
                last_ball_game = (bx, by)
                last_vel_game = (vx, vy)
                last_radius_game = BALL_R
                if dump and last_ball_game is not None:
                    outdir = '/home/xilinx/wp' if os.path.isdir('/home/xilinx/wp') else '/boot'
                    img = render(world, fan, crit, last_ball_game, last_vel_game,
                                 last_radius_game,
                                 extra='%s scan: %.2f ms' % ('PL' if use_pl else 'PS',
                                                             scan_ms or 0.0))
                    cv2.imwrite(os.path.join(outdir, dump), img)
                    print('[输出] %s/%s' % (outdir, dump))
'''
s = s[:a] + new_block + s[b:]

# ---------- 4) CLI：--ps 回退 + 全局 mm ----------
s = s.replace("    track_angle = '--track-angle' in sys.argv",
              "    track_angle = '--track-angle' in sys.argv\n"
              "    use_pl = '--ps' not in sys.argv      # 默认走 PL；--ps 回退 Python 扫描（对比用）")
s = s.replace("FIELD_W, FIELD_H = 960.0, 540.0",
              "FIELD_W, FIELD_H = 960.0, 540.0\n_mm_pl = None      # V3 的 MMIO（懒打开，只开一次）")

io.open(p, 'w', encoding='utf-8').write(s)
print('v3_scan.py 已改：PL 扫描（默认）+ render 终点打点 + --ps 回退')
