# -*- coding: utf-8 -*-
"""v3_scan.py — 板上跑 V3 的「出射临界扫描」：**PS 侧参考实现**。

它把今天验证过的两件事串起来，做出比赛要的那个功能：
  V2 的检测（缩略图 + 背景差分找球，误差 ≤8px）
    -> 球的**位置与速度**（最近 N 帧球心线性拟合）
    -> 喂给 game_pc/engine.py 的物理引擎，在入射角附近扫一簇轨迹（±win 度）
    -> 找出**临界轨迹**（最后一次不进球 / 擦洞口边）并渲染成图

同时量出 **PS 串行跑一簇要多少毫秒** —— 这是和 PL 并行版做"实测与参考比对"的基准
（比赛提交物里明确要求这一项）。★ 口径（2026-09-28 夜修）：要比就比**同一份定点模型、
同一簇角度**的串行 vs 并行；板上实测那组是 `v3_hwtest.py` 给的 **154.3 ms(PS 串行)
vs 0.10 ms(PL 内核) = 1541 倍**，而 PL 的**墙钟**还得加上 AXI 往返（fastmem 下 ≈2.5 ms）。

用法（板上）：
  python3 /home/xilinx/wp/v3_scan.py [--n 60] [--win 8] [--nobit] [--dump v3_scan.png]

  ★ 演示/出图模式（2026-09-28 夜加）：**完全不碰相机与视频链**
  python3 /home/xilinx/wp/v3_scan.py --no-cam --pos 100,170 --b 0.40 --fan 24 \
          --angle 0 --speed 400 --seed 7 --arcs --dump v3_demo.png
      · 场景由 --seed 决定（与相机无关）；球位由 --pos 直接给定
      · 拍定妆照 / 答辩现场演示**不再受"链路每几分钟死一次"影响**
      · --arcs：PL 模式下补画完整弧线（用定点黄金模型算折线，**与 PL 回读端点逐位自洽**，
        脚本内断言并打印一致性），否则 PL 模式只画终点实心点（图会很空）

退出码：0 = 正常（扫完并出图）；9 = 环境问题（抓不到帧/地图不可信）
"""

import os
import sys
import time

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import cv2
import numpy as np

import engine                        # 场区/物理引擎（板上 /home/xilinx/wp/engine.py）

# 游戏参数（与 game_pc/engine.py 对齐）
FIELD_W, FIELD_H = 960.0, 540.0
_MM_PL = [None]    # V3 的 MMIO 句柄（懒打开，只开一次；单元素列表当 holder）
BALL_R = 11.0                      # 游戏里的球半径（用于自动定标）
CORNER_R = 60.0

SCAN_WIN = 8                       # 扫描半宽（度）：±8°，与 engine.tangent_scan 的 win=8 一致
TRACK_N = 6                        # 用最近 6 次球心做速度拟合

# ★★ 弹跳预算：**现在是 3**（2026-10-05 用户规则订正：**预览最多显示 3 次反弹 +
#   收尾一小段**，快扇与预览同一把尺）。历史：2026-09-28 实锤 RTL 的 `max_bounce`
#   是**死输入**（撞墙即停）⇒ 当时全项目按 0 记账；**2026-10-04 修复 RTL**（夹住->
#   反射->续走->计数，与黄金模型 lane_fixed 逐位一致，xsim 六档全绿；PS 侧由
#   v3_pl.write_scene 把本常量写进寄存器）。语义：预算内的反弹都真实发生；
#   预算用尽后停（显示层负责"第 3 次反弹 + 70px 收尾"的截断，见 cut_at_bounces）。
PL_BOUNCE = 3


def fit_velocity(samples):
    """最近 N 个 (t, x, y) 线性拟合 -> (vx, vy)（像素/秒）。样本太少返回 None。"""
    if len(samples) < 3:
        return None
    t = np.array([s[0] for s in samples], dtype=np.float64)
    t = t - t[0]
    xs = np.array([s[1] for s in samples], dtype=np.float64)
    ys = np.array([s[2] for s in samples], dtype=np.float64)
    if t[-1] - t[0] < 1e-3:
        return None
    vx = float(np.polyfit(t, xs, 1)[0])
    vy = float(np.polyfit(t, ys, 1)[0])
    return vx, vy


def load_calib(path='/home/xilinx/wp/v3_calib.json', verbose=False):
    """读 v3_calib.py 生成的单应矩阵 H（**画面像素 -> 游戏坐标**）。没有返回 None。

    ★ 相机模式 / 游戏循环（P1 实时预测）**共用这一份**，别在两处各写一遍 ——
      映射一旦不一致，"球位"在两个脚本里就指两个地方，图会自相矛盾。
    """
    try:
        import json
        with open(path) as f:
            Hm = np.array(json.load(f)['H'], np.float64)
        if Hm.shape != (3, 3):
            raise ValueError('H 不是 3x3：%s' % (Hm.shape,))
        if verbose:
            print('[标定] 已载入 %s（球位/速度按标定映射）' % path)
        return Hm
    except Exception as e:
        if verbose:
            print('[标定] 没标定文件/读不动（%s）-> 用"画幅≙场区"默认假设' % str(e)[:60])
        return None


def cam_to_game(Hm, x, y, vx=0.0, vy=0.0):
    """相机像素 -> 游戏坐标（位置 + 速度）。返回 (bx, by, vgx, vgy, ang_deg)。

    有标定就用单应（而且**速度也跟着单应走**：近景下画面平移≠等量场区平移，
    直接拿像素速度当游戏速度会偏）；没有标定才退回"相机画幅 ≙ 场区"的默认假设
    （默认假设在近景下会把球位映到贴边 —— 那是摆场问题，标定后就没了）。

    ★ 2026-09-28 深夜抽出：原来这段内联在 main() 的相机路径里，
      P1（游戏循环里的实时预测）要用**一模一样**的映射，所以抽成函数两边共用。
    """
    if Hm is not None:
        p0 = cv2.perspectiveTransform(np.array([[[float(x), float(y)]]], np.float64), Hm)[0][0]
        p1 = cv2.perspectiveTransform(
            np.array([[[float(x) + float(vx), float(y) + float(vy)]]], np.float64), Hm)[0][0]
        bx, by = float(p0[0]), float(p0[1])
        gvx, gvy = float(p1[0] - p0[0]), float(p1[1] - p0[1])
    else:
        s_m = min(FIELD_W / 1024.0, FIELD_H / 720.0)
        bx = (float(x) - 512.0) * s_m + FIELD_W / 2
        by = (float(y) - 360.0) * s_m + FIELD_H / 2
        gvx, gvy = float(vx) * s_m, float(vy) * s_m
    return bx, by, gvx, gvy, float(np.degrees(np.arctan2(gvy, gvx)))


# ★ 2026-10-23（差距 2 流畅化）：单应逆矩阵缓存。
#   病根：game_to_cam 原来**每个点**都 np.linalg.inv(Hm) 一次 —— 直播档每帧
#   130+ 个点 = 130+ 次 3x3 求逆；A9 上 numpy 的 linalg 调用开销远大于变换本身，
#   单这一笔就能吃掉叠加时间的可观一块（PC 剖析占比 ~1/4，板上更贵）。
#   同一帧里 Hm 不变，逆也不变 —— 按字节内容缓存即可（标定换了新 Hm 自然换 key）。
#   ⚠ 不要把返回的矩阵原地改：它是共享缓存（调用方都只读）。
_HINV_CACHE = {}


def homography_inv(Hm):
    """Hm 的逆（带缓存）。Hm=None → None。返回值**只读**，别原地改。"""
    if Hm is None:
        return None
    a = np.ascontiguousarray(Hm, np.float64)
    key = a.tobytes()
    hit = _HINV_CACHE.get(key)
    if hit is None:
        hit = np.linalg.inv(a)
        if len(_HINV_CACHE) >= 8:       # 标定不会频繁换；只防无界增长
            _HINV_CACHE.clear()
        _HINV_CACHE[key] = hit
    return hit


def game_to_cam(Hm, x, y):
    """游戏坐标 -> 相机像素（cam_to_game 的**逆**）。没有标定就走默认假设的逆映射。

    ★ 2026-10-01 夜加：直播档要在**实拍画面上**把场区边界/洞口画出来（用户要的
      "划定区域"），所以必须有这一向的映射。与 cam_to_game 用**同一份** Hm——
      两向映射必须成对（一旦不成对，画出来的框和球位判定就指两处，图自相矛盾）。
    """
    if Hm is not None:
        Hi = homography_inv(Hm)
        p = cv2.perspectiveTransform(np.array([[[float(x), float(y)]]], np.float64), Hi)[0][0]
        return float(p[0]), float(p[1])
    s_m = min(FIELD_W / 1024.0, FIELD_H / 720.0)
    return (float(x) - FIELD_W / 2) / s_m + 512.0, (float(y) - FIELD_H / 2) / s_m + 360.0


def game_to_cam_batch(Hm, pts):
    """**批量** 游戏坐标 -> 相机像素（2026-10-05 夜加）。

    ★ 为什么：直播档每帧要折算几百个点，而逐点 game_to_cam 是"每点一次
      np.linalg.inv + 一次 1×1 的 perspectiveTransform"——板上实测这是
      "叠加 200~320ms、fps 掉到 1.4"的主凶（用户："球发射多了画面不实时"）。
      批量后每帧只要几次调用。返回 (N,2) float64。
    """
    a = np.asarray(pts, np.float64).reshape(-1, 1, 2)
    if Hm is not None:
        Hi = homography_inv(Hm)
        return cv2.perspectiveTransform(a, Hi).reshape(-1, 2)
    s_m = min(FIELD_W / 1024.0, FIELD_H / 720.0)
    out = np.empty((a.shape[0], 2), np.float64)
    out[:, 0] = (a[:, 0, 0] - FIELD_W / 2) / s_m + 512.0
    out[:, 1] = (a[:, 0, 1] - FIELD_H / 2) / s_m + 360.0
    return out


# ★ 卡死带宽度（2026-09-29 凌晨沿四面墙**逐像素**扫出来的，见 software/board/v3_wall_sweep.py）：
#     墙        安全        卡死        判据
#     下墙(y)   y<=527      y>=528      y + r >= FH - 1
#     右墙(x)   x<=942      x>=943      x + r >= FW - 6
#     上墙(y)   y>=13       y<=12       y - r <= 1
#     左墙(x)   x>=10       x<=9        x - r <= -2
#   四面不对称是定点/取整的结果；**最宽的一条是右墙的 6px**（球右缘离墙 7px 就卡）。
#   与 max_steps 无关：同一位置 steps=100/600/4000 全卡（不是"步数不够"，
#   是那一路的步进计数器压根不走了 = 真死锁）；与角度有关：起手**朝墙**的会卡、
#   背离墙的（如贴下墙时 ang=-12）能跑完。
#   统一留 8px（比最宽的卡死带还多 1~2px），四面都用同一个 margin。
IN_FIELD_MARGIN = 8.0


def in_field(x, y, r=BALL_R, margin=IN_FIELD_MARGIN):
    """球（含半径 r）是否**安全地落在场区内**（离每面墙至少 margin）。★ 喂 V3 前的必经之门。

    ★★ 2026-09-29 凌晨实锤（P1 上板，两个真 bug 是同一件事的两面）：
      球位在场区外（或**贴着墙**）时，PL 有一路 FSM 会**卡死** -> 汇聚 done
      （STATUS bit0）永不置位 -> `v3_pl.start_and_wait` 撞超时，读回的结果只有
      先跑完的那 16/17 路是新的。而"球被误检到场外/贴墙"在相机模式里**很常见**
      （误检、球滚出纸面、标定漂移、球贴边滚），所以这道门必须由调用方在喂场景前走。
    对照实验（同一块板、同一个 bit、--no-cam，**只改球位**，steps 无关）：
      pos=300,230（场内） steps=2000 -> AXI 14.76 ms  端点 17/17 ✓ 判定 17/17 ✓
      pos=300,230（场内） steps=600  -> AXI 12.47 ms  端点 17/17 ✓ 判定 17/17 ✓
      pos=591,750（场外） steps=600  -> AXI 2012 ms ✗ 端点 1/17 ✗（ang=-10.5）
      pos=591,750（场外） steps=2000 -> AXI 2011 ms ✗ 端点 1/17 ✗（ang=-10.5）
    ⇒ **PL 与 bit 本身完全健康**；"2002 ms"是 start_and_wait 的 2.0 s 超时墙钟，
      不是 PL 的算力。修法见 v3_pl.PLTimeout（超时改为抛异常）与本函数（提前拒绝）。
    ★ 上表那 14.76/12.47 ms 里还含**一次性 mmap 开销**（首次 open_v3 约 10ms）；
      稳定态的 PL AXI 往返是 **2.4 ms**（P1 上板 9 次实测 2.37ms P90 2.40ms）。
    """
    m = float(margin)
    return (m + r) <= float(x) <= (FIELD_W - r - m) and \
           (m + r) <= float(y) <= (FIELD_H - r - m)


def scan_txt_of(ang, spd, fan_half, steps):
    """图注第一行 = **本次扫描的输入**（角度/速度/扇角/步数），相机模式和游戏循环共用。
    ★ 原样保留 2026-09-28 修掉的那个口径：图注写输入，**不能**写跟踪值。"""
    return ('scan: angle=%.1f deg  speed=%.0f px/s  fan=+-%.0f deg  steps=%d'
            % (ang, spd, fan_half, steps))


# ---------------------------------------------------------------- 判据与出图辅助
def hole_of(world, ex, ey, side):
    """由**出射点**反查它是从哪个洞出去的（PL 的结果里没有洞信息，PS 有）。"""
    if ex <= BALL_R + 2.5:
        edge = 'left'
    elif ex >= FIELD_W - BALL_R - 2.5:
        edge = 'right'
    elif ey <= BALL_R + 2.5:
        edge = 'top'
    elif ey >= FIELD_H - BALL_R - 2.5:
        edge = 'bottom'
    else:
        return None
    for h in world.holes:
        if h['edge'] == edge and h['target'] == side:
            return h
    return None


def find_crit(fan, world, side=0, tol=6.0):
    """**真临界**：进洞的那些路里，出射点离洞口边缘最近的一条（≤tol px）。

    ★ 为什么换掉旧判据（2026-09-28 夜）：原来 PL 分支写的是"最后一条撞墙的"，
      它**永远落在扇形边缘**（实测图注上的 8.0°/16.0°/21.0° 全是扇边界，不是临界）。
      PS 分支本来就是"擦洞口边 ≤6px"，这里统一成同一个判据 —— 而且 PL 的端点
      就在边界上，"离洞边多远"是**现成数据**，不需要任何新硬件。
    返回 (下标 或 None, 裕量 px 或 None)。
    """
    best, bestm = None, None
    for i, (ang, pts, rr) in enumerate(fan):
        kind, _nb, hole = rr
        if kind != 'goal' or not pts:
            continue
        ex, ey = pts[-1][0], pts[-1][1]
        if not hole:
            hole = hole_of(world, ex, ey, side)
        if not hole:
            continue
        c = ey if hole['edge'] in ('left', 'right') else ex
        m = abs(c - hole['c']) - hole['w'] / 2.0
        if bestm is None or abs(m) < abs(bestm):
            best, bestm = i, m
    if best is None or bestm is None or abs(bestm) > tol:
        return None, bestm
    return best, bestm


def scan_bundle(world, V, mm_pl, sc, win, use_pl, arcs):
    """跑一簇 17 路（PL 或 PS），返回 (fan, crit, margin, ms_scan, ms_ref, note, st)。

    fan 元素 = (ang, pts, (kind, nbounce, hole))
      · PS 模式      ：pts = engine 浮点预览的整条折线（有弧线）
      · PL + --arcs  ：pts = **定点黄金模型**的折线（与 PL 逐位自洽，脚本内断言）
      · PL（默认）   ：pts 只有终点一个点（图上画实心点；图会很空，出图请加 --arcs）
    ms_scan = 本次扫描墙钟；ms_ref = PS 串行算同一簇的参考耗时（仅 --arcs 时量到）
    """
    import time as _t
    t0 = _t.perf_counter()
    angs = []
    ms_ref, note, st = None, '', None
    if use_pl:
        import v3_pl
        if mm_pl[0] is None:
            t_open = _t.perf_counter()
            mm_pl[0] = v3_pl.open_v3()
            # ★ 口径（2026-09-29 凌晨修）：首次 open_v3 的 mmap 约 10ms，**不算 PL 的账**，
            #   也不算进"一簇的往返"里（否则图注上会印出 12ms 而稳定态其实是 2.4ms）。
            print('[PL] V3 已就位（0x%08X，首次 mmap 开销 %.1f ms，不计入下面的往返）'
                  % (v3_pl.BASE, (_t.perf_counter() - t_open) * 1000.0))
            t0 = _t.perf_counter()          # 计时从这里重起：只算 写场景->启动->读回
        angs, _vs = v3_pl.write_scene(mm_pl[0], sc, V, win=win)
        pl_ms, st = v3_pl.start_and_wait(mm_pl[0])
        rl = v3_pl.read_results(mm_pl[0], V)
        # ★ 计时口径（2026-09-28 夜修）：**PL 的墙钟只算 AXI 这一段**
        #   （写场景 -> 启动 -> 读回）。原来把下面"画折线"的黄金模型耗时也算进去了，
        #   于是图上印出 "PL scan: 2463 ms" —— 那 2.4 秒其实是**板子的 Python 在算
        #   17 条折线**，跟 PL 一点关系没有。分开量，两个数各自诚实。
        ms_scan = (_t.perf_counter() - t0) * 1000.0
        kmap = {1: 'goal', 2: 'bounce', 0: 'none'}
        fan = []
        if arcs:
            # ★ 展示用折线：**同一份定点黄金模型**（与 PL 逐位一致）——
            #   顺便量出"PS 串行跑同一簇"的参考耗时，以及和 PL 回读端点的一致性。
            t1 = _t.perf_counter()
            bad, bad_res, first_bad = 0, 0, None
            for kk, a2 in enumerate(angs):
                ln = V.lane_fixed(sc['x0'], sc['y0'], a2, sc['sp'], sc['q'],
                                  sc['fields'], sc['holes'], sc['side'],
                                  steps=sc['steps'], max_bounce=sc['bounce'],
                                  max_pts=4000,
                                  obstacles=(sc.get('obs_list') or ([sc['obs']] if sc.get('obs') else ())),
                                  portals=(sc.get('portals') or ()))
                res, ex, ey, nst, nb = rl[kk]
                # 端点 + **判定**都要对：判定不一致比端点差几个像素严重得多
                if abs(ln['x'] - ex) > 0.02 or abs(ln['y'] - ey) > 0.02:
                    bad += 1
                    if first_bad is None:
                        first_bad = (a2, ln['rtl_res'], ln['x'], ln['y'], res, ex, ey)
                if ln['rtl_res'] != res:
                    bad_res += 1
                fan.append((a2, ln['pts'],
                            (kmap[res], nb,   # ★ 2026-10-04：RTL 修好后 nb 是真反弹数（此前写死占位）
                             hole_of(world, ex, ey, sc['side']) if res == 1 else None)))
            ms_ref = (_t.perf_counter() - t1) * 1000.0
            note = 'golden vs PL: endpoint %d/%d, verdict %d/%d' \
                   % (len(angs) - bad, len(angs), len(angs) - bad_res, len(angs))
            print('[自检] 黄金模型 vs PL 回读：端点 %s（%d/%d）  判定 %s（%d/%d）  [口径 max_bounce=%d]'
                  % ('逐位一致' if bad == 0 else '不一致 ✗', len(angs) - bad, len(angs),
                     '一致' if bad_res == 0 else '不一致 ✗', len(angs) - bad_res, len(angs),
                     sc['bounce']))
            if first_bad is not None:
                print('       首个不一致：ang=%.1f 金 res=%d (%.2f,%.2f) | PL res=%d (%.2f,%.2f)'
                      % first_bad)
        else:
            for kk, (res, ex, ey, nst, nb) in enumerate(rl):
                fan.append((angs[kk], [(ex, ey)],
                            (kmap[res], nb,   # ★ 2026-10-04：RTL 修好后 nb 是真反弹数（此前写死占位）
                             hole_of(world, ex, ey, sc['side']) if res == 1 else None)))
        print('        -> PL 扫完 %d 路：AXI 往返（写场景->启动->读回）%.2f ms，'
              '模块自报周期低 8 位 %d' % (len(fan), ms_scan, (st >> 8) & 0xFF))
        if ms_ref:
            print('        （另有 %.0f ms 是**板子 CPU 上**跑黄金模型画折线的耗时，'
                  '不算 PL 的账）' % ms_ref)
    else:
        fan = []
        t1 = _t.perf_counter()
        # ★ 步距与 PL 一致（fan_half/win）：否则 PS 参考和 PL 扫的不是同一簇角度，
        #   "实测与参考比对"就不成立了
        _span = sc.get('fan_half', float(win)) / float(win)
        for kk in range(-win, win + 1):
            a2 = sc['ang0'] + kk * _span
            pts, res, hole = world.preview(sc['x0'], sc['y0'], a2, sc['sp'], sc['q'],
                                           steps=sc['steps'], max_bounce=sc['bounce'])
            touched = any(p_[0] <= BALL_R + 2.5 or p_[0] >= FIELD_W - BALL_R - 2.5 or
                          p_[1] <= BALL_R + 2.5 or p_[1] >= FIELD_H - BALL_R - 2.5
                          for p_ in pts)
            fan.append((a2, pts, (res, 1 if touched else 0, hole)))
        ms_scan = (_t.perf_counter() - t1) * 1000.0
        print('        -> %d 条轨迹（PS 串行 %.1f ms）' % (len(fan), ms_scan))

    # ★ 临界判据两种模式统一（擦洞口边 ≤6px）
    fan = [(a, p, (r[0], r[1], r[2] if r[2] else (hole_of(world, p[-1][0], p[-1][1], sc['side'])
                                                 if (r[0] == 'goal' and p) else None)))
           for (a, p, r) in fan]
    crit, margin = find_crit(fan, world, side=sc['side'])
    if crit is not None and not arcs and use_pl:
        # 只给临界那条补一次真弧线（PS 预览 ~十几 ms），其余仍是终点小点
        pts_c, _r, _h = world.preview(sc['x0'], sc['y0'], fan[crit][0], sc['sp'], sc['q'],
                                      steps=sc['steps'], max_bounce=sc['bounce'])
        fan[crit] = (fan[crit][0], pts_c, fan[crit][2])
    print('        -> 临界 = %s' % ('None（本簇没有擦边/分界）' if crit is None else
                                  '%.1f°（离洞口边 %.1f px）' % (fan[crit][0], abs(margin))))
    return fan, crit, margin, ms_scan, ms_ref, note, st


def render(world, fan, crit_idx, ball_game, vel_game, radius_game, extra='',
           scan_txt='', crit_margin=None, trail_game=None):
    """把场区 + 分块磁场 + 轨迹束 + 标注画出来（返回 BGR 图）。"""
    S = 1.0                                    # 先按游戏坐标画，最后整体放大
    img = np.full((int(FIELD_H * S), int(FIELD_W * S), 3), 245, np.uint8)
    # 分块磁场：B 越强底色越深（出屏偏蓝、入屏偏红）
    for b in world.blocks:
        x0, y0 = int(b.x0 * S), int(b.y0 * S)
        x1, y1 = int(b.x1 * S), int(b.y1 * S)
        mag = min(abs(b.b) / 3.0, 1.0)
        col = (int(235 - 70 * mag), int(235 - 55 * mag), int(245 - 20 * mag)) if b.b > 0 \
            else (int(245 - 20 * mag), int(235 - 55 * mag), int(235 - 70 * mag))
        cv2.rectangle(img, (x0, y0), (x1, y1), col, -1)
        cv2.rectangle(img, (x0, y0), (x1, y1), (190, 190, 190), 1)
        # 标签放**块的左下角**：左上角会被顶部那 4 行图注压住（2026-09-28 夜发现）
        cv2.putText(img, 'B=%+.1f' % b.b, (x0 + 6, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (90, 90, 90), 1)
    # 洞口
    for h in world.holes:
        if h['edge'] == 'left':
            cv2.rectangle(img, (0, int(h['c'] - h['w'] / 2)), (12, int(h['c'] + h['w'] / 2)),
                          (0, 160, 0), -1)
        elif h['edge'] == 'right':
            cv2.rectangle(img, (int(FIELD_W) - 12, int(h['c'] - h['w'] / 2)),
                          (int(FIELD_W), int(h['c'] + h['w'] / 2)), (0, 160, 0), -1)
        elif h['edge'] == 'top':
            cv2.rectangle(img, (int(h['c'] - h['w'] / 2), 0), (int(h['c'] + h['w'] / 2), 12),
                          (0, 160, 0), -1)
        else:
            cv2.rectangle(img, (int(h['c'] - h['w'] / 2), int(FIELD_H) - 12),
                          (int(h['c'] + h['w'] / 2), int(FIELD_H)), (0, 160, 0), -1)
    # 轨迹束：绿=能出射 / 橙=临界 / 红=打回(撞过墙) / 灰=窗口内还没走到边界
    # ★ 画法（2026-09-28 夜）：不进洞的那些路**半透明**画（和 game_pc/main.py 原型
    #   同一手法 addWeighted 0.45）—— 满场 10 次弹跳的红线会把绿/橙淹掉，图上就
    #   读不出"临界分岔"这个故事了；判定本身没变，只是线淡。
    #   ★★ 2026-09-29 夜提速（板上逐笔拆账量的，见 E:\tmp_pkgs\render_breakdown.py）：
    #     ① 逐段 cv2.line -> 一次 cv2.polylines（17 路 × 最多 600 点 ≈ 1 万次 Python->C
    #        调用）。取整口径不变：astype 与 int() 都是"向零取整"，负坐标也一致。
    #     ② `ov = img.copy()`(10.1ms) + **全图** addWeighted(95.9ms) = 106ms，而这一簇
    #        17 路一共 68 个点、只盖住 0.76% 的像素 —— 拿 100% 的像素成本干了 1% 的活。
    #        改成只混**轨迹束的包围盒**那一块：addWeighted 是逐像素运算，做在子区域里
    #        结果**逐位相同**（板上 A/B：不同像素 0）。一簇全是绿/橙时这 106ms 直接归零。
    #     ③ 试过但**被数据否掉**的：静态底图（块/洞）缓存 —— `img.copy()` 要 10.1ms，
    #        比重画一遍（np.full 5.5 + 块 0.6）还贵，不划算，保持每帧重画。
    #   ★★ 混合范围必须盖住**所有**轨迹，不能只盖淡化的那些（板上 A/B 抓出来的）：
    #     老注释写"绿/橙没进 ov，不受影响"是**错的** —— ov 是在画任何线**之前**复制的
    #     底图，最后那次整图 addWeighted 把**每条线**（含绿/橙）都按 0.45*底 + 0.55*线
    #     混了一遍。所以包围盒要按全部轨迹取，否则绿/橙会比原来亮一档
    #     （实测"全绿"那档差 873 个像素，正好是绿线的像素数）。
    plan = []
    for i, (ang, pts, res) in enumerate(fan):
        nbou = res[1] if isinstance(res, tuple) else 0
        kind = res[0] if isinstance(res, tuple) else res
        if i == crit_idx:
            col, wdt = (0, 165, 255), 3           # 橙：临界
        elif kind == 'goal':
            col, wdt = (0, 200, 0), 2             # 绿：能出射
        elif nbou > 0:
            col, wdt = (60, 60, 220), 1           # 红：撞回来了
        else:
            col, wdt = (170, 170, 170), 1         # 灰：窗口内没到边界
        # 临界/绿直接画在主图上（醒目），其余画在"淡化层"上
        plan.append(((i == crit_idx or kind == 'goal'), i == crit_idx, col, wdt, pts))
    allp = [p for (_a, pts, _r) in fan for p in pts]
    x0 = y0 = x1 = y1 = 0
    sub = None
    if allp:
        # 包围盒 +10px 余量（终点那 r=5、临界 r=9 的实心点会伸出去），再裁到画布内
        x0 = max(0, int(min(p[0] for p in allp) * S) - 10)
        y0 = max(0, int(min(p[1] for p in allp) * S) - 10)
        x1 = min(int(FIELD_W * S), int(max(p[0] for p in allp) * S) + 11)
        y1 = min(int(FIELD_H * S), int(max(p[1] for p in allp) * S) + 11)
        if x1 > x0 and y1 > y0:
            sub = img[y0:y1, x0:x1].copy()        # 画线**之前**的底色，只这一块（不是整图）
    for direct, is_crit, col, wdt, pts in plan:
        if not pts or (not direct and sub is None):
            continue
        tgt = img if direct else sub
        ox, oy = (0, 0) if direct else (-x0, -y0)
        if len(pts) > 1:
            arr = (np.asarray(pts, np.float64) * S).astype(np.int32)
            arr[:, 0] += ox
            arr[:, 1] += oy
            cv2.polylines(tgt, [arr.reshape(-1, 1, 2)], False, col, wdt)
        # ★ PL 模式只给终点（没有中间轨迹点）-> 在终点画个实心点，颜色仍按命运分
        cv2.circle(tgt, (int(pts[-1][0] * S) + ox, int(pts[-1][1] * S) + oy),
                   9 if is_crit else 5, col, -1)
    if sub is not None:
        # 淡化叠回：只做在包围盒里，逐像素运算 -> 与全图版**逐位相同**（板上 A/B：0 像素差）
        cv2.addWeighted(sub, 0.45, img[y0:y1, x0:x1], 0.55, 0, img[y0:y1, x0:x1])
    # ★ P1（游戏循环）用：把**跟踪到的历史轨迹**画成灰线 —— 一眼看出球是从哪来的、
    #   往哪走。定妆照模式不传（合成球位没有历史，画了反而是编的）。
    if trail_game and len(trail_game) > 1:
        arr = (np.asarray(trail_game, np.float64) * S).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [arr], False, (120, 120, 120), 2)
    # 球的位置
    bx, by = int(ball_game[0] * S), int(ball_game[1] * S)
    cv2.circle(img, (bx, by), max(4, int(radius_game * S)), (0, 0, 255), 2)
    # 标注 —— ★ 图上的字**一律只用 ASCII**：cv2.putText 用的是 Hershey 字体，
    #   画不出中文（板上实测 "离洞口边" 变成一排 "?????????"，报告里就废了）。
    #   中文留给**控制台打印**（那里是 UTF-8，没问题）。
    #   ★ 第一行是**本次扫描的输入**（不是跟踪值！），跟踪值单独一行标 tracked。
    #   2026-09-28 夜修：原来只印跟踪值，而扫描其实用的是 --angle/--speed 的输入，
    #   图注与实际输入不符（报告里会自相矛盾）。
    txt = []
    if scan_txt:
        txt.append(scan_txt)
    if vel_game:
        txt.append('tracked: v=%.0f px/s  theta=%+.1f deg'
                   % (float(np.hypot(*vel_game)),
                      float(np.degrees(np.arctan2(vel_game[1], vel_game[0])))))
    if crit_idx is not None:
        txt.append('critical angle = %.1f deg%s'
                   % (fan[crit_idx][0],
                      '' if crit_margin is None else
                      '  (margin to hole edge %.1f px)' % abs(crit_margin)))
    else:
        txt.append('critical: none (no graze / no transition in this fan)')
    txt.append('bundle: %d lanes  (green=%d exit, red=%d bounce, grey=%d inside)'
               % (len(fan),
                  sum(1 for f in fan if (f[2][0] if isinstance(f[2], tuple) else f[2]) == 'goal'),
                  sum(1 for f in fan if (f[2][0] if isinstance(f[2], tuple) else f[2]) != 'goal'
                      and (f[2][1] if isinstance(f[2], tuple) else 0)),
                  sum(1 for f in fan if (f[2][0] if isinstance(f[2], tuple) else f[2]) != 'goal'
                      and not (f[2][1] if isinstance(f[2], tuple) else 0))))
    if extra:
        txt.append(extra)
    for k, s in enumerate(txt):
        cv2.putText(img, s, (12, 26 + 26 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.62,
                    (40, 40, 40), 2)
    return cv2.resize(img, (int(FIELD_W * 1.5), int(FIELD_H * 1.5)),
                      interpolation=cv2.INTER_NEAREST)


# ============================================================================
# ★★ 2026-10-04 新样式复盘图（用户定案）：单虚线主轨迹 + ±5°/1° 淡扇 + 中文标注
#
# 为什么并存而不是改 render()：旧 render 是 CLI（定妆照/--arcs 报告图）的既定
# 视觉语言（扇形图讲"角度敏感度"的故事，论文口径），游戏复盘讲"这一杆会怎样"
# 的故事——两张图服务两个场景，互不干扰。游戏侧（ball_game._v3_live 重 tick）
# 从今天起走下面这套。
#
# ★ 中文文字层：cv2.putText 的 Hershey 画不出中文（旧铁律的由来）——这里用
#   **PIL 渲染中文**（板端随 deploy 带一个字体；没有 PII/字体时自动退 ASCII 并
#   只警告一次）。铁律升级为：图上可以有中文，但必须走 PIL 这条文字层。
# ============================================================================

_ZH_FONT_CANDS = [
    # ★ 随 deploy 带的**子集化中文字体**（164 字形，25KB；fontTools 从微软雅黑抽的，
    #   只含图注用到的字）——板端十有八九没有 CJK 字体，靠它。
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'zh_font_subset.ttf'),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wqy-microhei.ttc'),
    '/home/xilinx/wp/wqy-microhei.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    r'C:\Windows\Fonts\msyh.ttc',
]
_ZH_FONTS = {}
_ZH_WARNED = [False]


def _zh_font(size):
    if size in _ZH_FONTS:
        return _ZH_FONTS[size]
    f = None
    try:
        from PIL import ImageFont
        for p in _ZH_FONT_CANDS:
            if os.path.exists(p):
                try:
                    f = ImageFont.truetype(p, size)
                    break
                except Exception:
                    continue
    except Exception:
        f = None
    _ZH_FONTS[size] = f
    return f


def put_texts(img, items):
    """一次 PIL 通道画完所有文字。items = [(str, (x, y), size_px, (b, g, r)), ...]

    没有 PIL / 中文字体时退回 cv2（非 ASCII 字符替换成 '?'）并**只警告一次**。
    """
    if _zh_font(20) is None:
        if not _ZH_WARNED[0]:
            print('[字层] ⚠ 没有可用的中文字体/PIL —— 图上中文退化为 ASCII。'
                  '（板端：把字体放到 /home/xilinx/wp/ 或随 deploy 带）')
            _ZH_WARNED[0] = True
        for s, xy, size, col in items:
            cv2.putText(img, s.encode('ascii', 'replace').decode(), xy,
                        cv2.FONT_HERSHEY_SIMPLEX, size / 30.0, col, max(1, size // 16))
        return
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for s, xy, size, col in items:
        d.text(xy, s, font=_zh_font(size), fill=(col[2], col[1], col[0]))
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def _extend_tail(rr, tail_len, step=2.0):
    """★ 第 (max_bounce+1) 次碰壁**不要切在墙点上**：反射后画一小段收尾；
    若小段又碰到边界（第 4 次接触）就停在壁点（不再反射、不再延伸）。
    只对 bounce_limit（反弹用尽）生效。修改 rr['pts']，raw 点列存 rr['_raw_pts']。"""
    import math as _m
    pts = list(rr['pts'])
    if rr['stop'] != 'bounce_limit' or len(pts) < 3 or tail_len <= 0:
        return False
    rr['_raw_pts'] = pts
    px, py = pts[-1]
    dx, dy = px - pts[-2][0], py - pts[-2][1]
    if abs(px - BALL_R) < 0.8 or abs(px - (FIELD_W - BALL_R)) < 0.8:
        dx = -dx
    if abs(py - BALL_R) < 0.8 or abs(py - (FIELD_H - BALL_R)) < 0.8:
        dy = -dy
    L = _m.hypot(dx, dy)
    if L < 1e-6:
        return False
    ux, uy = dx / L, dy / L
    tx, ty = px, py
    for _ in range(max(1, int(tail_len / step))):
        nx_, ny_ = tx + ux * step, ty + uy * step
        if nx_ < BALL_R or nx_ > FIELD_W - BALL_R or ny_ < BALL_R or ny_ > FIELD_H - BALL_R:
            break
        tx, ty = nx_, ny_
        pts.append((tx, ty))
    rr['pts'] = pts
    rr['tail_drawn'] = True
    return True


def cut_at_bounces(pts, n=3, tail=70.0):
    """折线截到"第 n 次急转（镜面反弹 ≈ 急转 ≥0.9rad）后 tail 像素"。

    2026-10-05 用户定案："预设轨迹**最多显示 3 次反弹**，第 3 次反弹后再延伸一小部分"。
    圆角是缓弯（每段转角小），只有真反弹才会 >0.9 rad —— 用它当反弹指纹。
    若折线里的反弹不足 n 次，原样返回（没得截）。"""
    import math as _m
    if len(pts) < 3:
        return pts
    turns = []
    for i in range(1, len(pts) - 1):
        a1 = _m.atan2(pts[i][1]-pts[i-1][1], pts[i][0]-pts[i-1][0])
        a2 = _m.atan2(pts[i+1][1]-pts[i][1], pts[i+1][0]-pts[i][0])
        if abs((a2 - a1 + _m.pi) % (2*_m.pi) - _m.pi) > 0.9:
            turns.append(i)
    if len(turns) < n:
        return pts
    idx = turns[n - 1]
    acc = 0.0
    for j in range(idx, len(pts) - 1):
        acc += _m.hypot(pts[j+1][0]-pts[j][0], pts[j+1][1]-pts[j][1])
        if acc >= tail:
            return pts[:j+1]
    return pts


def _dash_segments(pts, dash=11.0, gap=7.0):
    """折线 -> 虚线段表 [(p0, p1), ...]。"""
    import math as _m
    segs = []
    drawing, rem = True, dash
    for (x0_, y0_), (x1_, y1_) in zip(pts[:-1], pts[1:]):
        L = _m.hypot(x1_ - x0_, y1_ - y0_)
        if L < 1e-9:
            continue
        ux, uy = (x1_ - x0_) / L, (y1_ - y0_) / L
        t = 0.0
        while t < L:
            take = min(rem, L - t)
            if drawing:
                segs.append(((x0_ + ux * t, y0_ + uy * t),
                             (x0_ + ux * (t + take), y0_ + uy * (t + take))))
            t += take
            rem -= take
            if rem <= 1e-6:
                drawing = not drawing
                rem = dash if drawing else gap
    return segs


def render_dashed(world, sc, shot=None, shot_nb=0, tag='', extra='',
                  ball_game=None, trail_game=None, tail_len=70.0,
                  fan_half=5.0, fan_step=1.0):
    """★ 新样式复盘图：**单虚线主轨迹**（输入角度；最多 sc['bounce'] 次反弹、
    第 (bounce+1) 次碰壁后画 tail_len 收尾小段）+ **±fan_half / 每 fan_step 度**
    的极淡参考扇（浅绿=该角度能进洞）+ 中文标注。

    判定口径：颜色/文字用调用方从 **PL** 拿到的 shot/shot_nb（正中那一路）；
    折线由定点黄金模型画（两者逐位一致是既有契约，scan_bundle 里当场自检）。
    返回 (img, main_rr, fan_rrs) 供调用方记账。
    """
    import v3_fixedpoint as V
    S = 1.5
    W, H = int(FIELD_W * S), int(FIELD_H * S)
    img = np.full((H, W, 3), 245, np.uint8)

    # ---- 场区（与旧 render 同一视觉语言：块底色按 B、洞绿块、注记 ASCII）----
    for b in world.blocks:
        x0_, y0_ = int(b.x0 * S), int(b.y0 * S)
        x1_, y1_ = int(b.x1 * S), int(b.y1 * S)
        mag = min(abs(b.b) / 3.0, 1.0)
        col = (int(235 - 70 * mag), int(235 - 55 * mag), int(245 - 20 * mag)) if b.b > 0 \
            else (int(245 - 20 * mag), int(235 - 55 * mag), int(235 - 70 * mag))
        cv2.rectangle(img, (x0_, y0_), (x1_, y1_), col, -1)
        cv2.rectangle(img, (x0_, y0_), (x1_, y1_), (190, 190, 190), 1)
        cv2.putText(img, 'B=%+.2f' % b.b, (x0_ + 8, y1_ - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55 * S, (110, 110, 110), 1)
    _edge_zh = {'left': '左', 'right': '右', 'top': '上', 'bottom': '下'}
    for h in world.holes:
        c, w2 = float(h['c']), float(h['w']) / 2.0
        if h['edge'] == 'left':
            cv2.rectangle(img, (0, int((c - w2) * S)), (int(12 * S), int((c + w2) * S)),
                          (0, 160, 0), -1)
        elif h['edge'] == 'right':
            cv2.rectangle(img, (W - int(12 * S), int((c - w2) * S)), (W, int((c + w2) * S)),
                          (0, 160, 0), -1)
        elif h['edge'] == 'top':
            cv2.rectangle(img, (int((c - w2) * S), 0), (int((c + w2) * S), int(12 * S)),
                          (0, 160, 0), -1)
        else:
            cv2.rectangle(img, (int((c - w2) * S), H - int(12 * S)),
                          (int((c + w2) * S), H), (0, 160, 0), -1)

    # ---- 轨迹（黄金模型）----
    _cf = dict(charge=sc['q'], fields=sc['fields'], holes=sc['holes'], side=sc['side'],
               steps=sc['steps'], max_bounce=sc['bounce'],
               obstacles=(sc.get('obs_list') or ([sc['obs']] if sc.get('obs') else ())),
               portals=(sc.get('portals') or ()))
    main_rr = V.lane_fixed(sc['x0'], sc['y0'], sc['ang0'], sc['sp'],
                           trail_gap=6.0, max_pts=4000, **_cf)
    _extend_tail(main_rr, tail_len)
    fan_rrs = []
    n_k = int(round(fan_half / max(fan_step, 1e-9)))
    # ★ 2026-10-04：淡扇是**视觉辅助**，步数封顶 800（主轨迹才用满 sc['steps']）——
    #   自适应步数下（低速 3360 步）10 路扇也要 1.7s 的话发射反馈就死了。
    _fsteps = min(int(sc['steps']), 800)
    _cf_fan = dict(_cf)
    _cf_fan['steps'] = _fsteps
    for k in range(-n_k, n_k + 1):
        if k == 0:
            continue                       # 中心那条 = 主轨迹，不重复算
        fan_rrs.append(V.lane_fixed(sc['x0'], sc['y0'], sc['ang0'] + k * fan_step,
                                    sc['sp'], trail_gap=8.0,
                                    max_pts=2000, **_cf_fan))

    # ---- 画：淡扇垫底 -> 主虚线 -> 碰壁标记 -> 起终点 -> 尾巴/球 ----
    for fr in fan_rrs:
        fp = fr['pts']
        if len(fp) >= 2:
            col = (150, 200, 150) if fr['res'] == 'goal' else (202, 202, 202)
            arr = (np.asarray(fp, np.float64) * S).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [arr], False, col, 1)
    mcol = (0, 160, 0) if shot == 'goal' else (0, 140, 255)
    for (a, b_) in _dash_segments(main_rr['pts']):
        cv2.line(img, (int(a[0] * S), int(a[1] * S)), (int(b_[0] * S), int(b_[1] * S)),
                 mcol, 3)
    # 碰壁点（用原始点列；尾段不算）-> 小圈 + "反弹N"
    raw_pts = main_rr.get('_raw_pts', main_rr['pts'])
    hits = []
    for (px_, py_) in raw_pts:
        near = (abs(px_ - BALL_R) < 0.6 or abs(px_ - (FIELD_W - BALL_R)) < 0.6 or
                abs(py_ - BALL_R) < 0.6 or abs(py_ - (FIELD_H - BALL_R)) < 0.6)
        if near and (not hits or abs(px_ - hits[-1][0]) + abs(py_ - hits[-1][1]) > 20):
            hits.append((px_, py_))
    if main_rr['res'] == 'goal' and hits:
        hits = [h0 for h0 in hits
                if abs(h0[0] - raw_pts[-1][0]) + abs(h0[1] - raw_pts[-1][1]) > 30]
    text_items = []
    for i, (px_, py_) in enumerate(hits):
        cv2.circle(img, (int(px_ * S), int(py_ * S)), int(8 * S), (0, 100, 255), 2)
        text_items.append(('反弹%d' % (i + 1),
                           (int(px_ * S) + int(12 * S), int(py_ * S) - int(24 * S)),
                           int(11 * S), (0, 100, 255)))
    pts_all = main_rr['pts']
    cv2.circle(img, (int(pts_all[0][0] * S), int(pts_all[0][1] * S)),
               int(6 * S), (255, 80, 0), -1)
    text_items.append(('起点', (int(pts_all[0][0] * S) + int(10 * S),
                                int(pts_all[0][1] * S) + int(2 * S)),
                       int(12 * S), (200, 60, 0)))
    ex_, ey_ = int(pts_all[-1][0] * S), int(pts_all[-1][1] * S)
    cv2.drawMarker(img, (ex_, ey_), (40, 40, 40), cv2.MARKER_TILTED_CROSS, int(14 * S), 2)
    if shot == 'goal':
        end_zh, end_col = '进洞', (0, 130, 0)
    elif main_rr.get('tail_drawn'):
        end_zh, end_col = '截断', (40, 40, 40)
    else:
        end_zh, end_col = '停止', (40, 40, 40)
    text_items.append((end_zh, (ex_ + int(10 * S), ey_ + int(2 * S)),
                       int(12 * S), end_col))
    if trail_game and len(trail_game) > 1:
        arr = (np.asarray(trail_game, np.float64) * S).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [arr], False, (130, 130, 130), 2)
    if ball_game:
        cv2.circle(img, (int(ball_game[0] * S), int(ball_game[1] * S)),
                   max(4, int(BALL_R * S)), (0, 0, 255), 2)

    # ---- 中文标注（一次 PIL 通道）----
    if shot == 'goal':
        v_zh = '结果：进洞！（%s侧洞口）反弹 %d 次' % (
            _edge_zh.get(main_rr['hole']['edge'], '?') if main_rr['hole'] else '?',
            shot_nb)
    elif main_rr['stop'] == 'bounce_limit':
        v_zh = '结果：未进洞 —— 反弹 %d 次后第 %d 次碰壁停（已画 %.0fpx 收尾小段）' % (
            shot_nb, shot_nb, tail_len if main_rr.get('tail_drawn') else 0.0)
    else:
        v_zh = '结果：未进洞 —— 反弹 %d 次后步数用尽' % shot_nb
    capt = [
        ('发射预览 · 单虚线 = 本杆轨迹，淡扇 = ±%g°（浅绿=能进洞）' % fan_half,
         (int(14 * S), int(10 * S)), int(16 * S), (30, 30, 30)),
        ('起点 (%.0f,%.0f)  方向 %+.1f°  速度 %.0f px/s  最多反弹 %d 次' % (
            sc['x0'], sc['y0'], sc['ang0'], sc['sp'], sc['bounce']),
         (int(14 * S), int(10 * S) + int(30 * S)), int(16 * S), (30, 30, 30)),
        (v_zh, (int(14 * S), int(10 * S) + int(60 * S)), int(16 * S),
         (0, 130, 0) if shot == 'goal' else (0, 90, 200)),
        ('注：标"截断"处为显示截断（小球会继续弹）；判定由 PL 给，折线=定点黄金模型（逐位一致）',
         (int(14 * S), int(10 * S) + int(90 * S)), int(12 * S), (140, 140, 140)),
    ]
    if tag:
        capt.append((tag, (int(14 * S), int(10 * S) + int(114 * S)),
                     int(12 * S), (140, 140, 140)))
    if extra:
        capt.append((extra, (int(14 * S), int(10 * S) + int(138 * S)),
                     int(12 * S), (140, 140, 140)))
    put_texts(img, capt + text_items)
    return img, main_rr, fan_rrs


def build_world(seed, b_fix, n_blocks=3):
    """场区构造（块/洞随种子随机，B 可固定为 ±b_fix）。

    ★ 与 tools/v3_shot_search.py 里的 build_world **逐字一致** —— 离线搜出来的
      参数要能上板原样复现，两边一旦不一致就白搜了（改这里请同时改那边）。
    """
    import random as _rnd
    _rnd.seed(seed)
    w = engine.World()                     # World(None) 自己不播种，靠上面这一行
    w.setup_round(n_blocks)
    if b_fix is not None:                  # 演示：固定 |B|，符号隔块翻转（"分块磁场"）
        for bi, b in enumerate(w.blocks):
            b.b = b_fix if bi % 2 == 0 else -b_fix
    return w


def run_no_cam(world, V, win, steps, seed, b_fix, pos_fix, ang_fix, speed_fix,
               fan_half, use_pl, arcs, dump, bounce=PL_BOUNCE):
    """★ 出图/演示模式：不碰相机、不抓帧、不依赖视频链，只做「扫描 + 画图」。

    为什么值得单独一条路（2026-09-28 夜，定妆照复盘）：
      上板出图的成本 = 链路随时会死（传感器/视核/野 DMA 三处）+ 球摆哪儿由人手决定，
      于是"图好不好看"变成撞运气。但**画面 = (seed 场区) + (--b) + (球位/速度/角度)
      + (--fan)**，全部可离线复现：判定由 PL 真硬件给，折线由定点黄金模型给
      （与 PL 回读端点逐位一致，scan_bundle 里当场自检）。所以出图不再需要相机。
    """
    if pos_fix is None or len(pos_fix) < 2:
        print('[用法] --no-cam 必须配 --pos x,y（球位是合成出来的，没有相机可跟踪）')
        print('       例：--no-cam --pos 100,170 --b 0.40 --fan 24 --angle 0 --speed 400 --seed 7 --arcs')
        return 9
    bx, by = float(pos_fix[0]), float(pos_fix[1])
    ang = float(ang_fix)
    spd = float(speed_fix) if speed_fix > 0 else 400.0
    fan_h = float(fan_half) if fan_half is not None else float(win)
    if not in_field(bx, by):
        # ★ 这里**不拦**（--pos 是命令行给的显式复现口径），但必须提前说清楚会发生什么：
        #   PL 分支会在 start_and_wait 里抛 PLTimeout（场外的球让一路 FSM 卡死）。
        print('★★ 球位(%.0f,%.0f) 在**场区外**（场区 %g x %g + 球半径）—— PL 会有一路'
              ' FSM 卡死、done 永不置位，PL 分支必然抛 PLTimeout。'
              '这是**故意的复现口径**；正常出图请把球放在场区内。'
              % (bx, by, FIELD_W, FIELD_H), flush=True)
    sc = dict(fields=[(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks],
              holes=list(world.holes), x0=bx, y0=by, ang0=ang, sp=spd,
              q=1, steps=steps, bounce=bounce, side=0, fan_half=fan_h)
    print('[出图] 球位(%.0f,%.0f) 入射角%.1f° 速度%.0f px/s 扇角±%.0f° 步数%d 弹跳上限%d  [无相机]'
          % (bx, by, ang, spd, fan_h, steps, bounce))
    fan, crit, margin, ms_scan, ms_ref, note, st = scan_bundle(
        world, V, _MM_PL, sc, win, use_pl, arcs)
    if use_pl:
        extra = 'PL scan: %.2f ms (17 lanes, max_bounce=%d)' % (ms_scan or 0.0, bounce)
        if ms_ref:
            extra += '   |   PS serial (bit-exact fp model): %.0f ms' % ms_ref
    else:
        extra = 'PS serial scan: %.0f ms (17 lanes, max_bounce=%d)' % (ms_scan or 0.0, bounce)
    scan_txt = scan_txt_of(ang, spd, fan_h, steps)
    #   ★ 注意 vel_game 传 None：输入角/速度≠跟踪值，不能让图注冒出 "tracked:"
    #     （这正是 2026-09-28 修掉的三个口径 bug 之一）
    img = render(world, fan, crit, (bx, by), None, BALL_R,
                 extra=extra, scan_txt=scan_txt, crit_margin=margin)
    outdir = '/home/xilinx/wp' if os.path.isdir('/home/xilinx/wp') else '.'
    out = os.path.join(outdir, dump)
    cv2.imwrite(out, img)
    print('[输出] %s' % out)
    print('')
    print('==== 汇总 ====')
    print('  模式 = 出图（--no-cam，无相机）；seed=%d  B=%s  球位(%.0f,%.0f)'
          % (seed, b_fix, bx, by))
    print('  %s 一簇 %d 路：墙钟 %.2f ms%s'
          % ('PL' if use_pl else 'PS', len(fan), ms_scan or 0.0,
             '' if not (use_pl and st is not None) else
             '（含 MMIO 往返；模块自报周期低 8 位 %d）' % ((st >> 8) & 0xFF)))
    if ms_ref:
        print('  同口径参照：PS 串行跑**同一份定点模型、同一簇角度**：%.1f ms' % ms_ref)
        print('              -> PL 加速 %.0f 倍（分子分母都是"一簇 17 路"）'
              % (ms_ref / max(1e-6, ms_scan or 1e-6)))
    print('  注：与 PC 串行黄金模型的**逐位一致**由 v3_hwtest.py 验证：'
          '17 路 × 120 步 = 154.3 ms(PS) vs 0.10 ms(PL)，1541 倍。')
    return 0


def main():
    n = 60
    win = SCAN_WIN
    dump = 'v3_scan.png'
    steps = 300                        # 每条轨迹积分多少步（600 步 ≈ 2.5s，够画出射）
    # ★★ 入射角与速度是**按键步进给的输入**，不是"把球推快"测出来的 ★★
    #    规则原文（docs/game_rules_quickref.md）：角度 S/W 步进 2°、速度 A/D 步进 20 px/s、
    #    范围 200~600、默认 400；球是**摆**在自己边界上由相机识别位置（不是推）。
    #    所以默认走输入值；要看"跟踪出来的运动"（原型模式）用 --track-angle / --speed 0。
    speed_fix = 400.0                  # 0 = 退回用跟踪测出的速度（原型模式）
    pos_fix = None                     # (x,y) 覆盖球位（演示用；None = 用相机识别的）
    ang_fix = 0.0                      # 0° = 垂直边界入射（按键输入；--track-angle 用跟踪方向）
    track_angle = '--track-angle' in sys.argv
    fan_half = None                      # 扇形半宽（度）；None = 用 --win（默认 ±8°）
    for _i, _a in enumerate(sys.argv):
        if _a == '--fan' and _i + 1 < len(sys.argv):
            fan_half = float(sys.argv[_i + 1])
    use_pl = '--ps' not in sys.argv      # 默认走 PL；--ps 回退 Python 扫描（对比用）
    b_fix = None                       # 覆盖磁场大小（演示用：随机 B 太强时球会圈在中间）
    seed = 7                           # 场区随机种子（演示要可复现，比赛也要求脚本可复现）
    # ★ bounce 默认 PL_BOUNCE(0) —— RTL 的 max_bounce 是死输入，写别的值 PL 也照样
    #   "撞墙即停"，两边口径不一致就会得出"对不上"的假结论（详见 PL_BOUNCE 注释）
    bounce = PL_BOUNCE
    for i, a in enumerate(sys.argv):
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
        if a == '--win' and i + 1 < len(sys.argv):
            win = int(sys.argv[i + 1])
        if a == '--dump' and i + 1 < len(sys.argv):
            dump = sys.argv[i + 1]
        if a == '--steps' and i + 1 < len(sys.argv):
            steps = int(sys.argv[i + 1])
        if a == '--speed' and i + 1 < len(sys.argv):
            speed_fix = float(sys.argv[i + 1])
        if a == '--pos' and i + 1 < len(sys.argv):
            pos_fix = [float(t) for t in sys.argv[i + 1].split(',')]
        if a == '--angle' and i + 1 < len(sys.argv):
            ang_fix = float(sys.argv[i + 1])
        if a == '--b' and i + 1 < len(sys.argv):
            b_fix = float(sys.argv[i + 1])
        if a == '--seed' and i + 1 < len(sys.argv):
            seed = int(sys.argv[i + 1])
        if a == '--bounce' and i + 1 < len(sys.argv):
            bounce = int(sys.argv[i + 1])
    no_cam = '--no-cam' in sys.argv       # ★ 出图模式：不碰相机/视频链（合成球位）
    arcs = ('--arcs' in sys.argv) or (no_cam and '--no-arcs' not in sys.argv)
    #   --arcs：把每一条**完整折线**画出来（判定仍由 PL 给，折线由定点黄金模型给
    #   —— 两者逐位一致，脚本里当场自检）。默认关：PL 模式只有终点小点，图会很空。
    # ---- 下载 bit：**只有真要跑 PL 时才需要**（--ps 是纯 Python，--no-cam --ps 在 PC 上就能跑）
    #      盲下 bit 是老坑：PL 半编程后 MMIO 读会挂死总线，必须先 pl_probe 判断（见 pl_guard）
    if use_pl and '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')

    import v3_fixedpoint as V          # 黄金模型：PL 路的定点换算（to_pos/to_ang/to_vel）
    world = build_world(seed, b_fix)
    if b_fix is not None:
        print('[场区] 磁场已固定为 ±%.2f' % b_fix)
    print('[场区] 块数=%d  洞=%s' % (len(world.blocks),
                                    [(h['edge'], round(h['c']), h['target']) for h in world.holes]))

    # ================= ★ 出图模式（--no-cam）：绕开相机与整条视频链 =================
    # 为什么要有它（2026-09-28 夜定妆照复盘）：上板出图的成本 = 链路随时会死
    # （传感器侧/视核/野DMA 三处）+ 球摆在哪由人手决定，于是"图好不好看"变成了
    # 撞运气。但**画面 = (seed 场区) + (--b) + (球位/速度/角度) + (--fan)**，全部可离线
    # 复现；判定那部分由 PL 给（真硬件），折线由定点黄金模型给（与 PL 逐位一致）。
    # 所以出图不再需要相机：给 --pos 就来。
    if no_cam:
        return run_no_cam(world, V, win, steps, seed, b_fix, pos_fix, ang_fix,
                          speed_fix, fan_half, use_pl, arcs, dump, bounce=bounce)

    from ball_detect import _make_cap, _init_video
    from fastmem import FastMem
    from ball_map_detect import (MAP_BASE, CTRL_BASE, STAT_BASE, BLK, read_map,
                                 map_to_bgr, map_gate, detect_map, detect_map_bg,
                                 _ring_median_fast, BG_MAP,
                                 load_bg_words, bg_words_to_bgr)

    cap = _make_cap()
    _init_video(cap)
    swap = 1 if getattr(cap, '_swap_decided', False) else 0
    print('[配置] swap=%d  扫描 ±%d 度  帧数 %d' % (swap, win, n))

    Hm = load_calib(verbose=True)
    import v3_fixedpoint as V          # （已在上面导入；这行留给读代码的人：V 是黄金模型）
    bg_words = load_bg_words()
    bg = None
    if bg_words is not None:
        print('[背景] 已载入背景原始字（等 mswap 测出来后解码）')
    else:
        print('[背景] 没背景图 -> 单帧颜色掩码')

    samples = []                             # (t, x_px, y_px)
    n_ok = 0
    n_out = 0                    # 因球位在场区外而跳过的扫描次数（相机模式的常态）
    scan_ms = None
    ms_ref = None                            # PS 串行同簇参考（--arcs 时才量得到）
    last_ball_game = None
    last_vel_game = None
    last_radius_game = BALL_R
    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc, \
         FastMem(STAT_BASE, 0x1000) as ms:
        mc.wr_u32(0x00, (swap << 2) | 0x02)
        time.sleep(0.2)
        mc.wr_u32(0x00, (swap << 2))
        time.sleep(0.5)
        mswap = None
        t_gate = 0.0
        for k in range(n):
            try:
                # ★ cap.frame() 返回的是 (索引, 原图) —— 别写反了（第一版写成
                #   raw, idx = ...，于是拿一个整数去 reshape，报 size 1 的错）
                _idx, raw = cap.frame(timeout=3.0)
                raw = np.ascontiguousarray(np.asarray(raw, np.uint8)).copy()
            except Exception as e:
                print('[抓帧] ★ 失败：%s' % str(e)[:90])
                continue
            buf, ntry, fc = read_map(mm, mc)
            if mswap is None and time.time() - t_gate > 0.5:
                t_gate = time.time()
                bs, p0, p1 = map_gate(raw, buf)
                if bs is not None and max(p0, p1) >= 60.0:
                    mswap = bs
                    print('[地图] 自检 swap=%d (%.1f%%)' % (mswap, max(p0, p1)))
            if mswap is None:
                continue
            if bg_words is not None and bg is None:
                bg = bg_words_to_bgr(bg_words, swap ^ mswap)
                print('[背景] 已按当前字节序解码（swap=%d mswap=%d）' % (swap, mswap))
            mbgr = map_to_bgr(buf, swap ^ mswap)
            if bg is not None:
                det, _rj = detect_map_bg(mbgr, bg, _ring_median_fast, max_area=6031)
            else:
                det, _rj = detect_map(mbgr, _ring_median_fast, max_area=6031)
            if not det:
                continue
            # ★ 只认**落在场区内**的候选（场区是我们规定的；框外的再大也不是球）
            #   —— 实测：按"面积最大"挑会挑中框外的杂物（鼠标/桌面反光），
            #   球位被映射到 (1023,751) 这种场外坐标，整张图就没意义了。
            def _to_game(bb):
                # ★ 与扫描用的映射**同一份**（cam_to_game）—— 原来这里内联了一遍，
                #   两处一旦不一致，"框内候选"判的和"喂给 V3 的球位"就不是一回事
                g = cam_to_game(Hm, bb['x'], bb['y'])
                return g[0], g[1]
            cands = [(b_, _to_game(b_)) for b_ in det]
            inside = [(b_, g_) for b_, g_ in cands
                      if 0 <= g_[0] < FIELD_W and 0 <= g_[1] < FIELD_H]
            if not inside:
                continue
            ball = max(inside, key=lambda t: t[0].get('area', 0.0))[0]
            n_ok += 1
            samples.append((time.time(), ball['x'], ball['y']))
            if len(samples) > TRACK_N:
                samples.pop(0)
            r_px = float(ball['r'])
            if n_ok % 20 == 0:
                v = fit_velocity(samples)
                print('  帧%3d 球(%.0f,%.0f) r=%.1f  速度=%s'
                      % (k + 1, ball['x'], ball['y'], r_px,
                         'None' if v is None else '(%.0f,%.0f) px/s' % v))
            # 累积够样本就做一次扫描（每 20 帧一次，别把循环拖慢）
            # 触发扫描：每 20 帧一次，**或者到最后一帧也做一次**
            #（原来只写 %20==0 -> 链路不稳时只攒到十几帧就白跑一趟）
            # 触发扫描：每 6 个有效帧一次 + 最后一帧兜底
            #（原来写 %20==0，链路不稳时只攒到十几帧就白跑一趟）
            if n_ok >= 6 and (n_ok % 6 == 0 or k == n - 1):
                v = fit_velocity(samples)
                if v is None:
                    continue
                # ★ 有标定就用标定（v3_calib.py 生成的 homography：画面像素 -> 游戏坐标），
                #   没有才退回'相机画幅 ≙ 场区'的默认假设。
                #   默认假设在**近景**下会把球位映到贴边 —— 那是摆场问题，标定后就没了。
                #   ★ 2026-09-28 深夜抽成 cam_to_game()：游戏循环（P1）用**同一份**映射。
                #   （顺带删掉了这里算完就没用的 `scale` —— 死代码，留着只会误导）
                bx, by, vx, vy, ang = cam_to_game(Hm, ball['x'], ball['y'], v[0], v[1])
                if pos_fix is not None:
                    bx, by = pos_fix[0], pos_fix[1]
                if not track_angle:
                    ang = ang_fix        # 按键输入的角度（默认 0° = 垂直边界入射）
                if not in_field(bx, by):
                    # ★★ 相机模式里"球不在场里"是**常态**（误检、球滚出纸面、标定漂移），
                    #    而场外的球会让 PL 一路 FSM 卡死、done 永不置位（实测 2012 ms）。
                    #    宁可这一帧不扫、不出图，也不拿 16/17 路的残缺结果出图。
                    n_out += 1
                    if n_out <= 3:
                        print('[扫描] ★ 球位(%.0f,%.0f) 在**场区外** -> 跳过这次扫描'
                              '（场外的球会让 PL 一路卡死，见 in_field 的对照实验）'
                              % (bx, by), flush=True)
                    continue
                r_px_game = BALL_R
                print('[扫描] 球位(%.0f,%.0f) 速度(%.0f,%.0f) 角度%.1f° 半径r=%.0f 游戏单位'
                      % (bx, by, vx, vy, ang, float(np.hypot(vx, vy)) / max(1e-6, abs(world.blocks[0].b))
                         if abs(world.blocks[0].b) > 1e-9 else 0))
                spd = speed_fix if speed_fix > 0 else float(np.hypot(vx, vy))
                # ---- 场景（PL / PS 共用同一份参数）----
                sc = dict(fields=[(b_.x0, b_.y0, b_.x1, b_.y1, b_.b) for b_ in world.blocks],
                          holes=list(world.holes), x0=bx, y0=by, ang0=ang, sp=spd,
                          q=1, steps=steps, bounce=bounce, side=0)
                if fan_half is not None:
                    # 17 路不变，只把张角换成 ±fan_half（PS 端算初速度，PL 完全不感知）
                    sc['fan_half'] = fan_half
                # ★ 扫描 + 判定 + 临界判据全在 scan_bundle 里（PL/PS 同一套判据；
                #   --arcs 时折线用定点黄金模型算，并当场与 PL 回读端点自检）
                fan, crit, margin, scan_ms, ms_ref, note, st = scan_bundle(
                    world, V, _MM_PL, sc, win, use_pl, arcs)
                last_ball_game = (bx, by)
                last_vel_game = (vx, vy)
                last_radius_game = BALL_R
                if dump:
                    outdir = '/home/xilinx/wp' if os.path.isdir('/home/xilinx/wp') else '/boot'
                    extra = '%s scan: %.2f ms' % ('PL' if use_pl else 'PS', scan_ms or 0.0)
                    if ms_ref:
                        extra += '   |   PS serial (same fp model): %.0f ms' % ms_ref
                    # 相机模式里 vx,vy 是**真的跟踪值**，所以这里给 tracked 行是诚实的
                    img = render(world, fan, crit, last_ball_game, last_vel_game,
                                 last_radius_game, extra=extra,
                                 scan_txt=scan_txt_of(
                                     ang, spd,
                                     fan_half if fan_half is not None else win, steps),
                                 crit_margin=margin,
                                 # ★ 轨迹线要**游戏坐标**（画在场区画布上）：
                                 #   samples 里存的是相机像素，得再过一次同一份映射
                                 trail_game=[cam_to_game(Hm, s[1], s[2])[:2]
                                             for s in samples])
                    cv2.imwrite(os.path.join(outdir, dump), img)
                    print('[输出] %s/%s' % (outdir, dump))

    print('')
    print('==== 汇总 ====')
    print('  有效帧 %d，扫描 %s' % (n_ok, '未做（样本不足）' if scan_ms is None else '已完成'))
    if n_out:
        print('  另有 %d 次因**球位在场区外**跳过（没碰 PL）—— 球进到场里才会扫' % n_out)
    if scan_ms is not None:
        # ★ 口径（2026-09-28 夜修）：原来这里把 **PL 的墙钟**印成"PS 串行"，再拿它
        #   除以 PL 内核的 4.4µs 说"差 575 倍" —— 分子分母不是一个东西，评委一问就穿。
        #   诚实的写法：报了哪一档就写哪一档；要比就比**同一份定点模型的串行**耗时。
        print('  本次扫描：%s 一簇 %d 路，墙钟 %.2f ms%s'
              % ('PL' if use_pl else 'PS', 2 * win + 1, scan_ms,
                 '' if not (use_pl and ms_ref) else
                 '（含 MMIO 往返；同簇 PS 串行定点模型 %.1f ms -> 加速 %.0f 倍）'
                 % (ms_ref, ms_ref / max(1e-6, scan_ms))))
        if use_pl and not ms_ref:
            print('  （PL 墙钟含 MMIO 往返；同口径的"串行 vs 并行"要加 --arcs 让它量一次'
                  'PS 串行同簇，或看 v3_hwtest.py：154.3 ms vs 0.10 ms = 1541 倍）')
    return 0 if n_ok > 0 else 9


if __name__ == '__main__':
    sys.exit(main())
