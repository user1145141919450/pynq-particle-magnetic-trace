# -*- coding: utf-8 -*-
"""particle_pl.py —— 粒子引擎（pe_axi.v）的 PS 侧驱动 + **上板探针自检**。

设计口径（与 v3_pl.py 同款）：
  · 用项目自己的 fastmem（/dev/mem + mmap），**不用** pynq MMIO（一次十几微秒）；
  · 地址与寄存器图只此一份（出处：add_pe_to_bd.tcl 的 axi4 自动化分配输出）。

用法（板上）：
    python3 particle_pl.py probe      # 上板自检（T0/T2/T1/T3/T4 逐位对硬件的断言）
    from particle_pl import open_pe, set_scene, spawn, run, get_particle, pop_event
"""
import math
import sys
import time

# ★ 地址出处：add_pe_to_bd.tcl 运行输出
#   "Slave segment '/pe_axi_0/s_axi/reg0' ... assigned ... at <0x4000_1000 [4K]>"
BASE = 0x40001000
SPAN = 0x1000

# 寄存器（字节偏移）
# ★★ 2026-10-17 板上实锤：pe_axi 的读译码与 v3_bundle_axi **不同** ——
#   字地址 0x000：写=CTRL、**读=STATUS 束**（busy bit0 / paused bit1 / n_alive[14:8]）；
#   0x004 读 = {24'd0, n_alive}（不是 STATUS！）。
#   此前 R_STATUS=0x004，run() 轮询到的是 n_alive 的 bit0 —— 场上有一颗活粒子就
#   永远"busy"（首次上板自检 P2 卡 30s 的真身；引擎其实已经把 2000 子步跑完、
#   结果与仿真逐位一致）。pe_axi.v 头注释"0x004 STATUS"是过期注释，以译码为准。
R_CTRL, R_STATUS, R_RUN_N = 0x000, 0x000, 0x008
R_ST_SUB, R_ST_GOAL, R_ST_ANNIH = 0x00C, 0x010, 0x014
R_ST_COLL, R_ST_BOUNCE, R_ST_EVDROP = 0x018, 0x01C, 0x020
R_SP_META, R_SP_X, R_SP_Y, R_SP_VX, R_SP_VY = 0x024, 0x028, 0x02C, 0x030, 0x034
R_BALLR, R_FW, R_FH, R_NHOLES, R_RDIDX = 0x038, 0x03C, 0x040, 0x044, 0x048
# ★★ 2026-10-19 布置 PLACE（PL 原生）：AABB 障碍 + 方形传送门
R_OBS_EN, R_OBS_X0, R_OBS_Y0, R_OBS_X1, R_OBS_Y1 = 0x04C, 0x050, 0x054, 0x058, 0x05C
R_PORT_EN, R_PORT_X0, R_PORT_Y0, R_PORT_X1, R_PORT_Y1, R_PORT_R = \
    0x060, 0x064, 0x068, 0x06C, 0x070, 0x074
R_OBS_ROT = 0x078          # 旋转矩形：cx,cy,ehw,ehh,ca,sa（6 字）
R_BLK, R_HOLE, R_PART = 0x100, 0x200, 0x400
BLK_STRIDE, HOLE_STRIDE, PART_STRIDE = 20, 12, 16
R_EV_CNT, R_EV_DATA, R_EV_EMPTY = 0xE00, 0xE04, 0xE08

EV_KIND = {1: 'goal', 2: 'annihilate'}
EDGE_NAME = {0: 'left', 1: 'right', 2: 'top', 3: 'bottom'}


def open_pe(base=BASE, span=SPAN):
    from fastmem import FastMem
    mm = FastMem(base, span)
    mm.rd_u32(R_STATUS)                      # 探一下：映射不对这里就炸
    return mm


def set_scene(mm, V, blocks, holes, ball_r=11.0, fw=960.0, fh=540.0,
              obstacles=None, portals=None):
    """写场景：blocks=[(x0,y0,x1,y1,b)]、holes=[dict(edge,c,w,target)]。

    ang 口径与 v3 的 write_scene **同一公式**（q=+1 的那份；引擎内部按电荷取正/负）。
    """
    mm.wr_u32(R_BALLR, V.to_pos(ball_r))
    mm.wr_u32(R_FW, V.to_pos(fw))
    mm.wr_u32(R_FH, V.to_pos(fh))
    mm.wr_u32(R_NHOLES, len(holes))
    for i, (x0, y0, x1, y1, b) in enumerate(blocks):
        ang = V.to_ang(V.OMEGA_SIGN * float(b) / V.K_RADIUS * V.DT)
        base = R_BLK + i * BLK_STRIDE
        mm.wr_u32(base + 0, ang & 0x7FFFFF)
        mm.wr_u32(base + 4, V.to_pos(x0))
        mm.wr_u32(base + 8, V.to_pos(y0))
        mm.wr_u32(base + 12, V.to_pos(x1))
        mm.wr_u32(base + 16, V.to_pos(y1))
    _EDGE = {'left': 0, 'right': 1, 'top': 2, 'bottom': 3}
    for h, hh in enumerate(holes):
        base = R_HOLE + h * HOLE_STRIDE
        mm.wr_u32(base + 0, V.to_pos(hh['c']))
        mm.wr_u32(base + 4, V.to_pos(hh['w']))
        # ★ en=1、tgt=1 恒开：引擎 v1 无"分边靶"语义（洞对所有粒子都算数）。
        #   ★ 别再拿 world 洞的 target 当使能——演示口径里它被摊成 0
        #   （"两洞都算进"），照抄会变成"洞全禁用、粒子永远不进球"（自查抓出）。
        meta = 1 | (_EDGE[hh['edge']] << 1) | (1 << 3)
        mm.wr_u32(base + 8, meta)
    # ★★ 2026-10-19 用户定案：障碍 = **旋转矩形**（AABB 只作 RTL 预过滤）。
    #   与 V3 场景 sc['obs']=(cx,cy,hw,hh,ang) 同一套口径。
    _ob = list(obstacles or [])[:1]
    if _ob:
        _o0 = _ob[0]
        _a = V.obstacle_aabb(_o0, ball_r)
        _a = V.clamp_aabb(_a, fw, fh)
        mm.wr_u32(R_OBS_EN, 1)
        mm.wr_u32(R_OBS_X0, V.to_pos(_a[0]))
        mm.wr_u32(R_OBS_Y0, V.to_pos(_a[1]))
        mm.wr_u32(R_OBS_X1, V.to_pos(_a[2]))
        mm.wr_u32(R_OBS_Y1, V.to_pos(_a[3]))
        mm.wr_u32(R_OBS_ROT + 0, V.to_pos(_o0.cx))
        mm.wr_u32(R_OBS_ROT + 4, V.to_pos(_o0.cy))
        mm.wr_u32(R_OBS_ROT + 8, V.to_pos(_o0.hw + ball_r))
        mm.wr_u32(R_OBS_ROT + 12, V.to_pos(_o0.hh + ball_r))
        mm.wr_u32(R_OBS_ROT + 16,
                  int(round(math.cos(-_o0.ang) * (1 << 20))) & 0x7FFFFF)
        mm.wr_u32(R_OBS_ROT + 20,
                  int(round(math.sin(-_o0.ang) * (1 << 20))) & 0x7FFFFF)
    else:
        for _r in (R_OBS_EN, R_OBS_X0, R_OBS_Y0, R_OBS_X1, R_OBS_Y1):
            mm.wr_u32(_r, 0)
        for _r in (R_OBS_ROT + 0, R_OBS_ROT + 4, R_OBS_ROT + 8,
                   R_OBS_ROT + 12, R_OBS_ROT + 16, R_OBS_ROT + 20):
            mm.wr_u32(_r, 0)
    _pp = list(portals or [])[:2]
    if len(_pp) >= 2:
        mm.wr_u32(R_PORT_EN, 1)
        mm.wr_u32(R_PORT_X0, V.to_pos(_pp[0].x))
        mm.wr_u32(R_PORT_Y0, V.to_pos(_pp[0].y))
        mm.wr_u32(R_PORT_X1, V.to_pos(_pp[1].x))
        mm.wr_u32(R_PORT_Y1, V.to_pos(_pp[1].y))
        mm.wr_u32(R_PORT_R, V.to_pos(V.PORTAL_R))
    else:
        for _r in (R_PORT_EN, R_PORT_X0, R_PORT_Y0,
                   R_PORT_X1, R_PORT_Y1, R_PORT_R):
            mm.wr_u32(_r, 0)


def spawn(mm, V, idx, x, y, ang_deg, speed, chg, who):
    """出生一颗粒子：位置/方向角/速度（游戏单位）-> 定点由 V 换算。"""
    a = math.radians(ang_deg)
    mm.wr_u32(R_SP_X, V.to_pos(x))
    mm.wr_u32(R_SP_Y, V.to_pos(y))
    mm.wr_u32(R_SP_VX, V.to_vel(speed * math.cos(a)) & 0xFFFFFFFF)
    mm.wr_u32(R_SP_VY, V.to_vel(speed * math.sin(a)) & 0xFFFFFFFF)
    _c = {1: 0b01, -1: 0b11, 0: 0b00}[int(chg)]
    meta = (idx & 0x3F) | (_c << 6) | ((who & 0x3) << 8)
    mm.wr_u32(R_SP_META, meta)               # 写即出生


def run(mm, n_sub, timeout=30.0):
    """推进 n_sub 个子步并等到完成。超时抛异常。

    ★★ 2026-10-17 板上修：完成判据改为「busy=0 **且** st_substeps 推进 ≥ n」
    （16 位回绕安全）。原版"先等 busy=1 再等 busy=0"在**短跑**上必挂 ——
    400 子步 ≈ 0.4ms，第一次轮询（1ms 后）之前就跑完了，永远看不到 busy=1
    （首次上板自检 P3 卡 30s 的真身）。顺带：R_STATUS 见上方注释（0x000 读=STATUS）。
    """
    n = int(n_sub)
    sub0 = mm.rd_u32(R_ST_SUB) & 0xFFFF
    mm.wr_u32(R_RUN_N, n)
    mm.wr_u32(R_CTRL, 1)                     # run 脉冲
    t0 = time.time()
    while time.time() - t0 < timeout:
        busy = mm.rd_u32(R_STATUS) & 1
        sub = mm.rd_u32(R_ST_SUB) & 0xFFFF
        if (not busy) and (((sub - sub0) & 0xFFFF) >= n):
            return time.time() - t0
        time.sleep(0.0005)
    raise RuntimeError('engine 没跑完（busy=%d，子步 %d/期望 +%d）'
                       % (busy, (sub - sub0) & 0xFFFF, n))


def clear(mm):
    mm.wr_u32(R_CTRL, 4)


def stop(mm, on):
    mm.wr_u32(R_CTRL, (2 if on else 0))


def get_particle(mm, i):
    base = R_PART + i * PART_STRIDE
    x = mm.rd_u32(base) & 0x3FFFF
    y = mm.rd_u32(base + 4) & 0x3FFFF
    vx = mm.rd_u32(base + 8)
    vx = vx - (1 << 32) if vx & 0x80000000 else vx
    w3 = mm.rd_u32(base + 12)
    vy = w3 & 0xFFFFF
    vy = vy - (1 << 20) if vy & 0x80000 else vy
    # w3 = {7'd0, alive, who, chg, vy}：alive=bit24, who=[23:22], chg=[21:20]
    alive = (w3 >> 24) & 1
    who = (w3 >> 22) & 3
    _c = (w3 >> 20) & 3
    chg = 1 if _c == 1 else (-1 if _c == 3 else 0)
    return dict(x=x, y=y, vx=vx, vy=vy, alive=bool(alive), who=who, chg=chg)


def pop_event(mm):
    """弹一条事件（无则 None）。返回 dict(pidx, edge, who, chg, kind)。"""
    if (mm.rd_u32(R_EV_CNT) & 0x1F) == 0:
        return None
    mm.wr_u32(R_EV_CNT, 1)                   # 写即弹
    time.sleep(0.0005)
    d = mm.rd_u32(R_EV_DATA) & 0x3FFF
    pidx = (d >> 8) & 0x3F
    edge = (d >> 6) & 3
    who = (d >> 4) & 3
    _c = (d >> 2) & 3
    chg = 1 if _c == 1 else (-1 if _c == 3 else 0)
    return dict(pidx=pidx, edge=edge, who=who, chg=chg, kind=d & 3)


def stats(mm):
    return dict(sub=mm.rd_u32(R_ST_SUB) & 0xFFFF,
                goal=mm.rd_u32(R_ST_GOAL) & 0xFFFF,
                annih=mm.rd_u32(R_ST_ANNIH) & 0xFFFF,
                coll=mm.rd_u32(R_ST_COLL) & 0xFFFF,
                bounce=mm.rd_u32(R_ST_BOUNCE) & 0xFFFF,
                evdrop=mm.rd_u32(R_ST_EVDROP) & 0xFFFF,
                n_alive=(mm.rd_u32(R_STATUS) >> 8) & 0x7F)


# ---------------------------------------------------------------------------
# 上板探针自检（断言与仿真 tb 同一套常量）
# ---------------------------------------------------------------------------
def probe():
    sys.path.insert(0, '.')
    from v3_fixedpoint import to_pos, to_vel, to_ang, OMEGA_SIGN, K_RADIUS, DT
    import types
    V = types.SimpleNamespace(to_pos=to_pos, to_vel=to_vel, to_ang=to_ang,
                              OMEGA_SIGN=OMEGA_SIGN, K_RADIUS=K_RADIUS, DT=DT)

    mm = open_pe()
    nbad = 0

    def ck(tag, got, exp):
        nonlocal nbad
        ok = (got == exp)
        print('   [%s] %-10s got=%s exp=%s' % ('OK' if ok else 'FAIL', tag,
                                               got, exp))
        nbad += 0 if ok else 1

    # 场景：一块 B=+0.4（ang 由 set_scene 公式算）
    blocks = [(0.0, 0.0, 960.0, 540.0, 0.40)]
    clear(mm)
    set_scene(mm, V, blocks, [])

    print('== P1 出生 + 读回 ==')
    spawn(mm, V, 0, 100.0, 270.0, 0.0, 400.0, 1, 1)
    p = get_particle(mm, 0)
    ck('x', p['x'], 25600)
    ck('vy', p['vy'], 0)
    ck('alive', int(p['alive']), 1)
    ck('who/chg', (p['who'], p['chg']), (1, 1))

    print('== P2 run 2000 子步（逐位期望）==')
    run(mm, 2000)
    p = get_particle(mm, 0)
    ck('x', p['x'], 216171)
    ck('y', p['y'], 2930)
    st = stats(mm)
    ck('sub', st['sub'], 2000)

    print('== P3 进洞（1600px/s + 右洞 c=178）==')
    clear(mm)
    set_scene(mm, V, blocks, [dict(edge='right', c=178.0, w=44.0)])
    spawn(mm, V, 0, 100.0, 270.0, 0.0, 1600.0, 1, 1)
    run(mm, 400)
    st = stats(mm)
    ck('goal', st['goal'], 1)
    ev = pop_event(mm)
    ck('event.kind', EV_KIND.get(ev['kind']) if ev else None, 'goal')
    ck('event.edge', EDGE_NAME.get(ev['edge']) if ev else None, 'right')

    print('== P4 异号湮灭 ==')
    clear(mm)
    set_scene(mm, V, blocks, [])
    spawn(mm, V, 0, 300.0, 270.0, 0.0, 400.0, 1, 1)
    spawn(mm, V, 1, 305.0, 270.0, 180.0, 400.0, -1, 2)
    run(mm, 2)
    st = stats(mm)
    ck('annih', st['annih'], 1)
    ck('n_alive', st['n_alive'], 0)

    print('== P5 同号弹性（速度互换）==')
    clear(mm)
    spawn(mm, V, 0, 300.0, 270.0, 0.0, 400.0, 1, 1)
    spawn(mm, V, 1, 395.0, 270.0, 180.0, 400.0, 1, 2)
    run(mm, 40)
    st = stats(mm)
    ck('coll>=1', 1 if st['coll'] >= 1 else 0, 1)
    p0, p1 = get_particle(mm, 0), get_particle(mm, 1)
    ck('vx0 反向', 1 if p0['vx'] < 0 else 0, 1)
    ck('vx1 反向', 1 if p1['vx'] > 0 else 0, 1)

    print('== P6 16 粒容量（NPART=16 越界防护）==')
    # ★ 2026-11-01：引擎实装 16 粒。这里做**容量级**验证：
    #   ① idx 0..15 全部能出生并读回存活（旧 8 粒 bit 会在 8..15 处失败）；
    #   ② idx=16 必须被引擎越界防护拒掉（n_alive 仍 16）。
    clear(mm)
    set_scene(mm, V, blocks, [])
    for i in range(16):
        spawn(mm, V, i, 100.0 + 40.0 * (i % 4), 80.0 + 60.0 * (i // 4),
              0.0, 0.0, 1, 1)
    time.sleep(0.02)
    ok_all = all(get_particle(mm, i)['alive'] for i in range(16))
    ck('16 粒全部存活', 1 if ok_all else 0, 1)
    ck('n_alive', stats(mm)['n_alive'], 16)
    spawn(mm, V, 16, 500.0, 400.0, 0.0, 0.0, 1, 1)
    time.sleep(0.02)
    ck('idx=16 被拒（n_alive 仍 16）', stats(mm)['n_alive'], 16)

    print('== 上板探针：%s ==' % ('全过 ✓' if nbad == 0 else '★ %d 项失败' % nbad))
    return 0 if nbad == 0 else 1


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'probe':
        sys.exit(probe())
    print('用法: python3 particle_pl.py probe')
