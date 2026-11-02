# -*- coding: utf-8 -*-
"""v3_pl.py —— V3 并行轨迹束的 PL 访问层（寄存器图 / 写场景 / 读结果）。

为什么单独抽一个模块（2026-09-28）：
  `v3_hwtest.py`（验证）和 `v3_scan.py`（游戏/演示）都要走同一条 AXI 通路。
  地址和寄存器偏移**写死两处迟早会打架** —— 今晚已经吃过一次：BD 改用 axi4
  自动化后地址从 0x43C00000 变成了 **0x40000000**，脚本里写死的那份吃了 SIGBUS。
  所以：**地址从 .hwh 里读出来的真值**（BASE 常量就写在下面，出处标清楚），
  寄存器图只此一份。

用法：
    from v3_pl import open_v3, write_scene, start_and_wait, read_results
"""

import time

import numpy as np

# ★ 地址出处：deploy/ov5640_m1a_wrapper.hwh 里 INSTANCE="v3_bundle_axi_0" 那条
#   MEMRANGE 的 BASEVALUE（BD 用 axi4 自动化分配，实测是 0x40000000）✓
BASE = 0x40000000
SPAN = 0x10000
NLANES, NBLK = 17, 9

# 寄存器偏移（与 rtl/v3_bundle/v3_bundle_axi.v 头注释一一对应）
R_CTRL, R_STATUS = 0x000, 0x004
R_X0, R_Y0, R_STEPS, R_BOUNCE = 0x010, 0x014, 0x018, 0x01C
R_BALLR, R_FW, R_FH, R_MISC = 0x020, 0x024, 0x028, 0x02C
R_BLK, R_HOLE, R_LANEV, R_RES = 0x100, 0x200, 0x300, 0x400
BLK_STRIDE, HOLE_STRIDE, LANEV_STRIDE, RES_STRIDE = 20, 12, 8, 16
# ★★ 2026-10-19 布置 PLACE（PL 原生）：AABB 障碍（5 字）+ 方形传送门（6 字）
R_OBS, R_PORT = 0x030, 0x044
R_OBS_ROT = 0x05C          # 旋转矩形：cx,cy,ehw,ehh,ca,sa（6 字）
# ★★ 2026-10-27 多件：1/2 号障碍 + 1 号门对（与 v3_bundle_axi 的 0x1D-0x37 对齐）
R_OBS1, R_OBS1_ROT = 0x074, 0x088
R_OBS2, R_OBS2_ROT = 0x0A0, 0x0B4
R_PORT1 = 0x0CC


def open_v3():
    """打开 V3 的寄存器窗口。**用项目自己的 fastmem（/dev/mem + mmap）而不是 pynq MMIO**。

    ★ 为什么（2026-09-28 实测）：pynq 的 `MMIO.read/write` 每次十几微秒，一簇要写约 140 次
      + 读 68 次 -> **墙上时间 50~85ms，而 PL 内核本身只要 0.1ms** ✗。
      换 fastmem 后照 V1 读 23KB 只要 0.9ms 那个量级走（同一套 mmap 路子）。
      FastMem 的方法名是 rd_u32/wr_u32（**不是** MMIO 的 read/write）—— 调用处要改 ✓。
    """
    from fastmem import FastMem
    mm = FastMem(BASE, SPAN)
    mm.rd_u32(R_STATUS)        # 探一下：映射不对这里就会炸
    return mm


def write_scene(mm, sc, V, win=8):
    """把一个场景写进 PL：标量参数 + 块表（每块 5 字）+ 洞表（每洞 3 字）+ 每路初速度。

    sc: dict(fields=[(x0,y0,x1,y1,B)], holes=[dict(edge,c,w,target)],
             x0, y0, ang0, sp, q, steps, bounce, side)
    V : tools/v3_fixedpoint.py 模块（提供 to_pos/to_ang/to_vel 与常量）
    返回 (expect_angles, lane_v) —— 前者给画图用，后者是实际写进去的初速度。
    """
    import math
    n_holes = len(sc['holes'])
    mm.wr_u32(R_CTRL, 0)
    mm.wr_u32(R_X0, V.to_pos(sc['x0']))
    mm.wr_u32(R_Y0, V.to_pos(sc['y0']))
    mm.wr_u32(R_STEPS, sc['steps'])
    mm.wr_u32(R_BOUNCE, sc['bounce'])
    mm.wr_u32(R_BALLR, V.to_pos(V.BALL_R))
    mm.wr_u32(R_FW, V.to_pos(V.FIELD_W))
    mm.wr_u32(R_FH, V.to_pos(V.FIELD_H))
    mm.wr_u32(R_MISC, (n_holes << 2) | sc['side'])
    # ★★ 2026-10-19 用户定案：障碍 = **旋转矩形**。
    #   sc['obs'] = (cx, cy, hw, hh, ang_deg)。
    #   · R_OBS 写扩张后的 AABB（只作 RTL 的便宜预过滤）；
    #   · R_OBS_ROT 写真正的旋转参数：cx,cy,ehw=hw+BALL_R,ehh=hh+BALL_R,
    #     ca=cos(-ang), sa=sin(-ang)（Q2.20，23 位二进制补码）。
    #   传送门仍是圆形（sc['portals']=[(x,y),(x,y)]，半径 PORTAL_R）。
    # ★★ 多件：sc['obs_list'] 优先（3 根），旧 sc['obs'] 单根兼容
    _obs_list = sc.get('obs_list')
    if _obs_list is None:
        _obs_list = [sc['obs']] if sc.get('obs') else []
    _obs_list = list(_obs_list)[:3]
    for _oi in range(3):
        _base = (R_OBS, R_OBS1, R_OBS2)[_oi]
        _rot = (R_OBS_ROT, R_OBS1_ROT, R_OBS2_ROT)[_oi]
        _ob = _obs_list[_oi] if _oi < len(_obs_list) else None
        if _ob:
            _cx, _cy, _hw, _hh, _ang = [float(_v) for _v in _ob]
            _rad = math.radians(_ang)
            _ca, _sa = math.cos(_rad), math.sin(_rad)
            # ★ 2026-10-27：AABB 必须覆盖**扩张后**的旋转矩形（ehw=hw+BR,
            #   ehh=hh+BR）。旧式 abs(ca)*hw+abs(sa)*hh+BR 在斜 45° 时少
            #   BR*(|ca|+|sa|-1)≈4.5px，会把角部命中裁掉（多件对拍实锤）。
            _ax = abs(_ca) * (_hw + float(V.BALL_R)) \
                + abs(_sa) * (_hh + float(V.BALL_R))
            _ay = abs(_sa) * (_hw + float(V.BALL_R)) \
                + abs(_ca) * (_hh + float(V.BALL_R))
            _ab = V.clamp_aabb((_cx - _ax, _cy - _ay, _cx + _ax, _cy + _ay))
            mm.wr_u32(_base + 0, 1)
            mm.wr_u32(_base + 4,  V.to_pos(_ab[0]) & 0x3FFFF)
            mm.wr_u32(_base + 8,  V.to_pos(_ab[1]) & 0x3FFFF)
            mm.wr_u32(_base + 12, V.to_pos(_ab[2]) & 0x3FFFF)
            mm.wr_u32(_base + 16, V.to_pos(_ab[3]) & 0x3FFFF)
            mm.wr_u32(_rot + 0,  V.to_pos(_cx) & 0x3FFFF)
            mm.wr_u32(_rot + 4,  V.to_pos(_cy) & 0x3FFFF)
            mm.wr_u32(_rot + 8,  V.to_pos(_hw + float(V.BALL_R)) & 0x3FFFF)
            mm.wr_u32(_rot + 12, V.to_pos(_hh + float(V.BALL_R)) & 0x3FFFF)
            mm.wr_u32(_rot + 16,
                      int(round(math.cos(-_rad) * (1 << 20))) & 0x7FFFFF)
            mm.wr_u32(_rot + 20,
                      int(round(math.sin(-_rad) * (1 << 20))) & 0x7FFFFF)
        else:
            for _j in range(5):
                mm.wr_u32(_base + 4 * _j, 0)
            for _j in range(6):
                mm.wr_u32(_rot + 4 * _j, 0)
    # ★★ 多件：最多 2 对门（sc['portals'] 平铺 4 个门，按 0/1、2/3 成对）
    _pp = list(sc.get('portals') or [])
    if _pp and isinstance(_pp[0], (list, tuple)) and len(_pp[0]) == 2 \
            and isinstance(_pp[0][0], (list, tuple)):
        _pp = [g for pair in _pp for g in pair]
    for _pi in range(2):
        _base = (R_PORT, R_PORT1)[_pi]
        if 2 * _pi + 1 < len(_pp):
            _p0, _p1 = _pp[2 * _pi], _pp[2 * _pi + 1]
            mm.wr_u32(_base + 0, 1)
            mm.wr_u32(_base + 4,  V.to_pos(_p0[0]) & 0x3FFFF)
            mm.wr_u32(_base + 8,  V.to_pos(_p0[1]) & 0x3FFFF)
            mm.wr_u32(_base + 12, V.to_pos(_p1[0]) & 0x3FFFF)
            mm.wr_u32(_base + 16, V.to_pos(_p1[1]) & 0x3FFFF)
        else:
            for _j in range(5):
                mm.wr_u32(_base + 4 * _j, 0)
    mm.wr_u32(R_PORT + 20, V.to_pos(V.PORTAL_R) & 0x3FFFF)
    for i, (bx0, by0, bx1, by1, bb) in enumerate(sc['fields']):
        ang = V.to_ang(V.OMEGA_SIGN * sc['q'] * bb / V.K_RADIUS * V.DT) if abs(bb) > 1e-9 else 0
        mm.wr_u32(R_BLK + i*BLK_STRIDE + 0,  ang & 0x7FFFFF)
        mm.wr_u32(R_BLK + i*BLK_STRIDE + 4,  V.to_pos(bx0) & 0x3FFFF)
        mm.wr_u32(R_BLK + i*BLK_STRIDE + 8,  V.to_pos(by0) & 0x3FFFF)
        mm.wr_u32(R_BLK + i*BLK_STRIDE + 12, V.to_pos(bx1) & 0x3FFFF)
        mm.wr_u32(R_BLK + i*BLK_STRIDE + 16, V.to_pos(by1) & 0x3FFFF)
    for h in range(4):
        if h < n_holes:
            hh = sc['holes'][h]
            e = {'left': 0, 'right': 1, 'top': 2, 'bottom': 3}[hh['edge']]
            mm.wr_u32(R_HOLE + h*HOLE_STRIDE + 0, V.to_pos(hh['c']) & 0x3FFFF)
            mm.wr_u32(R_HOLE + h*HOLE_STRIDE + 4, V.to_pos(hh['w']) & 0x3FFFF)
            mm.wr_u32(R_HOLE + h*HOLE_STRIDE + 8,
                     ((1 if hh['target'] == sc['side'] else 0) << 3) | (e << 1) | 1)
        else:
            for k in range(3):
                mm.wr_u32(R_HOLE + h*HOLE_STRIDE + k*4, 0)
    angs, vs = [], []
    half = sc.get('fan_half', float(win))          # 张角优先；默认按 ±win 度、1 度步进
    step = half / float(win)
    for k in range(-win, win + 1):
        a = sc['ang0'] + k * step
        ar = math.radians(a)
        vx = V.to_vel(sc['sp'] * math.cos(ar))
        vy = V.to_vel(sc['sp'] * math.sin(ar))
        angs.append(a)
        vs.append((vx, vy))
        mm.wr_u32(R_LANEV + (k + win)*LANEV_STRIDE + 0, vx & 0xFFFFF)
        mm.wr_u32(R_LANEV + (k + win)*LANEV_STRIDE + 4, vy & 0xFFFFF)
    return angs, vs


class PLTimeout(RuntimeError):
    """PL 在超时窗口内**没有报 done**（STATUS bit0 = bundle_done 汇聚信号）。

    ★★ 2026-09-29 凌晨实锤（P1 上板）：这不是"慢"，是**内核卡死**。
      反推证据：那 2002 ms 里 `cyc[7:0]` 一路在涨（34 / 236 两次按 f·t 反解都给出
      f ≈ 100.000 MHz），也就是 done 一次都没置、计数器自由跑到超时。
      触发条件：**球位落在场区外**时，某一路 FSM 卡住 -> 汇聚 done 永不置位。
      对照实验（同一块板、同一个 bit、只改球位，见 v3_scan.in_field 的注释）。
    """


def start_and_wait(mm, timeout=0.6):
    """启动一簇，等 done。返回 (墙钟毫秒, STATUS)；**超时抛 PLTimeout**。

    ★★ 为什么改成抛异常（2026-09-29 凌晨）：原来超时是**静默返回** —— 把没置位的
      STATUS 当成功交给调用方，于是"球在场外 -> 一路卡死 -> done 永不置位"在游戏
      循环里表现为"每次预测 2002 ms"，而脚本照样把这 2002 ms 当成 PL 的耗时印在
      图上、照样拿 16/17 的残缺结果画图。**一晚上就被这个静默失败骗过去了**：
      看到的是"PL 慢了 167 倍"，真相是"PL 卡住了、我们在干等"。静默的失败最贵。
    ★ timeout 2.0 -> 0.6：健康的一簇是 ~12 ms（v3_shoot.sh 头部实测），0.6 s 已是
      50 倍余量；而卡死时游戏循环**每帧都要赔**这个时间（2.0 s 直接把 fps 打到 1.8）。
    ★ 卡死**可恢复**：下一次 CTRL=1 会重新起一簇（实测：场外那簇卡死后，紧接着跑
      场内 (300,230) 立刻回到 17/17 逐位一致）。所以抛异常不需要任何复位动作。
    """
    t0 = time.perf_counter()
    mm.wr_u32(R_CTRL, 1)
    st = 0
    while time.perf_counter() - t0 < timeout:
        st = mm.rd_u32(R_STATUS)
        if st & 1:
            return (time.perf_counter() - t0) * 1000.0, st
    st = mm.rd_u32(R_STATUS)
    raise PLTimeout(
        'PL 在 %.0f ms 内没报 done（STATUS=0x%08X，cyc 低 8 位 %d）—— 这一簇卡死了，'
        '读回的结果不可信（只有先跑完的那几路是新的）。已知触发条件：球位在**场区外**'
        '（如 y > FIELD_H）时某一路 FSM 卡住、汇聚 done 永不置位。'
        '修法：喂场景之前先过 v3_scan.in_field()。'
        % (timeout * 1000.0, st, (st >> 8) & 0xFF))


def read_results(mm, V, n=NLANES):
    """读回 n 路结果 -> [(res, ex, ey, steps, bounces)]（ex/ey 已换算成像素浮点）。"""
    out = []
    for i in range(n):
        w0 = mm.rd_u32(R_RES + i*RES_STRIDE + 0)
        w1 = mm.rd_u32(R_RES + i*RES_STRIDE + 4)
        w2 = mm.rd_u32(R_RES + i*RES_STRIDE + 8)
        w3 = mm.rd_u32(R_RES + i*RES_STRIDE + 12)
        res = (w0 >> 18) & 3
        ex = V.pos_f(w0 & 0x3FFFF)
        ey = V.pos_f(w1 & 0x3FFFF)
        out.append((res, ex, ey, w2 & 0xFFF, w3 & 0xF))
    return out


def results_to_hex(res_list):
    """把读回的结果打包成与黄金模型 exp.mem 同格式的 54 位整数（逐位比对用）。"""
    out = []
    for (res, ex, ey, stp, nb) in res_list:
        out.append(((res & 3) << 52) | ((int(round(ex * 256)) & 0x3FFFF) << 34) |
                   ((int(round(ey * 256)) & 0x3FFFF) << 16) |
                   ((stp & 0xFFF) << 4) | (nb & 0xF))
    return out
