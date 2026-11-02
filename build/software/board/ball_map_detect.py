# -*- coding: utf-8 -*-
"""ball_map_detect.py — V2 第一步：在**缩略图**（128x90 块，每块 = 8x8 px 平均）上找球，
再与**全分辨率**参考检测逐球比对。

判据（路线图）：同一帧里"缩略图球心"与"全分辨率球心"的距离 **≤ 8 px**（= 一个块的
边长）。缩略图的分辨率极限就是 8 px —— 游戏要的是"球在哪"，不是细节，够用即可。

为什么值得这么做（V2 的动机）：
  全分辨率一帧 1024x720x2B = 1.47 MB，读回 + cv2 检测要几十毫秒；
  缩略图只有 23 KB（1/64），读回和检测都能压到毫秒级 —— 游戏才跑得动 30 fps。
  本脚本顺带把时序预算量出来：读回 ≤2ms、缩略图检测 ≤5ms、端到端 ≤33ms。

颜色判据**照抄 ball_detect.classify()** 的同一套 HSV 阈值，两边找到的球类别才可比：
  红: (H<=12 | H>=165) & S>=90 & V>=60
  蓝: 95<=H<=135      & S>=90 & V>=60
  黑: V < max(70, 0.55*缩略图中位V) & S<=110

用法（板上）：
  python3 /boot/ball_map_detect.py --n 20 [--nobit] [--dump]
    --n N     比对 N 帧（默认 20）
    --nobit   不下 bit（接力：PL 里应已有采集链）
    --dump    落盘 /boot/ballmap_d.png（缩略图 + 两种标记叠一起）和 /boot/ballref_d.png
退出码：
  0 = 每一帧的每个参考球都在 8 px 内找到同类球
  1 = 有帧超差 / 有球没找到 / 压根没找到过球
  9 = 环境问题（抓不到帧、bit 不对）
"""

import os
import sys
import time

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import cv2
import numpy as np

try:                                    # 与 vision_diag 的 C 项共用同一份黄金模型
    import vision_golden as vg
except Exception:
    vg = None

W, H = 1024, 720
BW, BH = W // 8, H // 8                 # 128 x 90
MAP_BYTES = BW * BH * 2                 # 23,040（每块 2 字节 RGB565）
BLK = 8

MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000                  # +0x00 ctrl / +0x08 frame_cnt
STAT_BASE = 0x43B20000
BIT = '/boot/ov5640_m1a_wrapper.bit'

TOL_PX = 8.0                            # ★ 判据：球心误差 ≤ 一个块

# 背景差分用的背景（--bgcap 拍，拍到就自动用起来；--no-bg 强制不用）
BG_MAP = '/home/xilinx/wp/ball_bg_map.npy'      # 缩略图背景（float 存不下，存 uint8）
BG_FULL = '/home/xilinx/wp/ball_bg_full.png'    # 全分辨率背景（给参考那路 detect_bg 用）


# ---------------------------------------------------------------- 采集
def _grab_raw(cap, tries=2):
    """抓一张全分辨率原图；流卡死就按 dvp_probe 的老办法救一次（ctrl[4] + 重跑初始化）。"""
    from pynq import MMIO
    from ball_detect import _init_video
    for k in range(tries + 1):
        try:
            idx, raw = cap.frame(timeout=3.0)
            return np.ascontiguousarray(np.asarray(raw, np.uint8)).copy(), idx
        except Exception as e:
            print('[原图] 第 %d 次抓帧失败：%s' % (k + 1, str(e)[:140]))
            if k == tries:
                raise
            print('[原图] 救流：ctrl[4] 脉冲（重跑传感器上电 + SCCB）')
            mm3 = MMIO(0x43A30000, 0x10000)
            cur = mm3.read(0x00)
            mm3.write(0x00, cur | (1 << 4))
            time.sleep(0.3)
            mm3.write(0x00, cur & ~(1 << 4))
            time.sleep(0.5)
            _init_video(cap)


def raw_to_bgr(raw, swap):
    """字节流 -> BGR。

    ★ 坑（2026-09-27 第一版就踩了）：`cv2.COLOR_BGR5652BGR` 要的是**两通道 uint8**
      （一像素两个字节），不是 2-D uint16 —— 传 uint16 会被当成"单通道图"而报
      `Invalid number of channels in input image (scn is 1)`。
      所以这里和 vision_diag 解缩略图用的是**同一个形式**：reshape 成 (H, W, 2) 的
      字节数组（先到的字节在前=低 8 位），swap 就是把这两个字节对调。

    ★ 2026-10-01 深夜提速：原来 swap 那笔写的是 `a[..., ::-1]` + ascontiguousarray,
      A9 上是一次**跨步拷贝**（_bench_swap.py 实测 90ms，是全帧 107ms 里的病根）。
      换成 cv2.mixChannels([a],[o],[1,0,0,1]) 同语义只要 31ms —— 板上三种候选
      （byteswap/shift/mixCh）逐位差 max=0, 与现役**逐位一致**, 所以换得安心。
      调用方全都能白拿（ball_game._build_live / v3_calib / 本文件 730/910 行）。
    """
    a = np.frombuffer(raw, np.uint8).reshape(H, W, 2)
    if swap:
        o = np.empty_like(a)
        cv2.mixChannels([a], [o], [1, 0, 0, 1])
        a = o
    return cv2.cvtColor(np.ascontiguousarray(a), cv2.COLOR_BGR5652BGR)


def map_to_bgr(buf, swap):
    """缩略图字节流 -> BGR（逐字复刻 vision_diag 的解码：128x90 块）。"""
    m = np.frombuffer(buf, np.uint16).reshape(BH, BW)
    b = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
    if swap:
        b = b[..., ::-1]
    return cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR5652BGR)


def map_gate(raw, buf):
    """★ 地图自检 —— 判据照抄 vision_diag 的 C 项："PL 地图 vs 黄金块平均，
    三通道同时 <=1 LSB 的比例（%）"，两种字节序各算一遍。

    它量的是什么、不量什么（2026-09-27 第二十一版，绕了这一晚的坑）：
      【量】地图的**字**（按小端读出来的 565 值）与真值的位域一致性 —— 也就是
           "地图到底是不是那幅画、视核算没算对"。
      【量】地图与原文的**字节序关系**：这一版实测 swap=1 赢（99.4% vs 0.2%），
           意思是**地图在内存里的两个字节，与 VDMA 那路原图的顺序正好相反**
           （地图是 [高,低]，原图是 [低,高]）。
      【不量】喂给 cv2 时该不该再翻一次 —— 那是**显示字节序**，位域比较对此完全无感，
           所以 C 项 99.4% 全绿也可能是**一张洋红图**（本晚就是这个情况：第 3/4 次
           上板的 ballmap_d.png 全是洋红噪声，而自检/黄金模型一切正常）。

    显示字节序**不在这里**决定：曾经是推导的（mdisp = 原图 swap ^ mswap），
    2026-09-29 改成由 `display_swap_check()` **实测** —— 因为推导悄悄依赖
    "驱动的 swap 与 PL 的 swap_bytes 配得一致"，任一环节翻面就静默翻第二次，
    而本函数（位域比较）对此完全无感。这里只负责"地图里装的是不是那幅画"。

    返回 (best_swap, pct[0], pct[1])；vg 不可用时返回 (None, None, None)。
    """
    if vg is None:
        return None, None, None
    m = np.frombuffer(buf, np.uint16).reshape(BH, BW)
    pix = vg.pixels_from_raw(raw)
    pcts = []
    for sw in (0, 1):
        g = vg.block_avg(vg.swap16(pix) if sw else pix)
        bad = None
        for sh, msk in ((11, 0x1F), (5, 0x3F), (0, 0x1F)):
            d = np.abs(((m >> sh) & msk).astype(np.int32) -
                       ((g >> sh) & msk).astype(np.int32)) > 1
            bad = d if bad is None else (bad | d)
        pcts.append(float((~bad).mean()) * 100.0)
    best = 0 if pcts[0] >= pcts[1] else 1
    return best, pcts[0], pcts[1]


DISP_TOL = 20.0         # "这张渲染像真图"的容差：单像素 BGR 最大通道差 <= 这个数
DISP_MIN_OK = 60.0      # 赢家至少要对到这么多（%）
DISP_MIN_GAP = 5.0      # 还得赢另一个这么多（%）才敢说"判得动"


def display_swap_check(raw, buf, swap_raw, mswap):
    """★ 显示字节序**实测** —— 专门堵"自检全绿、图却是洋红"这个坑（2026-09-27）。

    为什么要单独量一次：
      map_gate 是**位域比较** —— 它把地图的字（按小端读出来的 565 数值）与黄金
      块平均逐通道比。它回答"地图里装的是不是那幅画"，但对"内存里两个字节谁先
      谁后"**完全无感**：99.4% 全绿的同时，喂给 cv2 的图可以是一张洋红噪声。
      而地图的显示字节序一直是**推**出来的：mdisp = 原图 swap ^ 地图字序关系。
      推导本身没错，但它悄悄依赖"驱动这一帧判的 swap 是对的"、"PL 的 swap_bytes
      真的按那个值配上了"—— 任一环节翻面，就静默翻第二次，谁也看不出来。

    这里改成**量**：把黄金模型按"原图那路的正确颜色"渲染成一张真图（truth），
    再把地图按两种字节序各渲染一张，谁像真图就是谁。为什么这个判据判得动：
    565 被字节交换 = R/B 字段整体挪位（纯灰 0x8410 -> 0x1084，一个 132 灰、一个
    33 暗红），渲染出来是两个颜色；只有 0x0000/0xFFFF 这类**自反字**（纯黑/纯白）
    才两种字节序渲染成一样 —— 那种场退回推导值也不会错。

    返回 dict：mdisp / pct0 / pct1 / ok / why。
    判得动就用实测值；判不动（场景近纯色、或地图内容本身就坏）就退回推导值，
    并把理由写进 why —— 调用方必须把它打出来，别让"退回"这件事静默发生。
    """
    derived = int(swap_raw) ^ int(mswap or 0)
    if vg is None:
        return {'mdisp': derived, 'pct0': None, 'pct1': None,
                'ok': False, 'why': 'vision_golden 不可用，没法量（退回推导值）'}
    pix = vg.pixels_from_raw(raw)
    # 真图 = 黄金模型按"原图那路的解码"算出的块平均，再按标准 565 渲染。
    # （swap_raw 是驱动判定的原图字节序 —— 原图那路的颜色对不对由驱动自检负责，
    #   这里只要求"地图与它一致"。）
    g_true = vg.block_avg(vg.swap16(pix) if swap_raw else pix)
    truth = vg.map_to_bgr(g_true, swap=False)
    m = np.frombuffer(buf, np.uint16).reshape(BH, BW)
    pcts = []
    for sw in (0, 1):
        img = vg.map_to_bgr(m, swap=bool(sw))
        d = np.abs(img.astype(np.int16) - truth.astype(np.int16)).max(axis=2)
        pcts.append(float((d <= DISP_TOL).mean()) * 100.0)
    best = 0 if pcts[0] >= pcts[1] else 1
    gap = abs(pcts[0] - pcts[1])
    ok = pcts[best] >= DISP_MIN_OK and gap >= DISP_MIN_GAP
    return {'mdisp': best if ok else derived, 'pct0': pcts[0], 'pct1': pcts[1],
            'ok': ok, 'gap': gap, 'derived': derived,
            'why': '' if ok else ('赢家只对到 %.1f%%（要 >=%.0f%%）' % (pcts[best], DISP_MIN_OK)
                                  if pcts[best] < DISP_MIN_OK
                                  else '两种字节序只差 %.1f%%（要 >=%.0f%%），场景多半近纯色'
                                       % (gap, DISP_MIN_GAP))}


def read_map(mm, mc, tries=8):
    """一致性读：读数前后帧号不变才算拿到"同一帧"的快照（vision_diag 同款）。

    顺带把**帧计数**返回给调用者 —— 它是"这一轮窗口里链路卡没卡过"的唯一凭据
    （2026-09-27 第八次上板：链路一卡、看门狗复位视频域，那一帧就是撕裂的，
    参考检测会在花帧上跑出 34~58 个"圆"、耗时 17 秒）。
    """
    for k in range(tries):
        a = mc.rd_u32(0x08)
        buf = mm.rd_u8_copy(0, MAP_BYTES)
        b = mc.rd_u32(0x08)
        if a == b:
            return buf, k + 1, a
    return buf, tries, b


# ---------------------------------------------------------------- 缩略图找球
def _ring_median_fast(hsv, cx, cy, r):
    """环带 V 中位数（与 `_ring_median_V` 同口径，但快 5~10 倍）。

    ★ 为什么换（2026-09-27 提速，微基准实测）：`_ring_median_V` 用 `np.ogrid`
      逐像素算"到圆心的距离平方"—— 球一大，patch 就大（半径 29 块 -> 87x87），
      实测 **r=5 要 1.2ms、r=29 要 2.5ms**，而它每个候选都要跑一次。
      这里改成用 `cv2.circle` 在 patch 上**画一个环**（两次 fill，C 速度），
      语义一样是"半径 1.1r~1.5r 的环带内 V 的中位数"，只在整数边界上差几个像素。
    """
    h, w = hsv.shape[:2]
    x0 = int(max(0, cx - r * 1.5))
    y0 = int(max(0, cy - r * 1.5))
    x1 = int(min(w, cx + r * 1.5 + 1))
    y1 = int(min(h, cy + r * 1.5 + 1))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    patch = hsv[y0:y1, x0:x1, 2]
    m = np.zeros(patch.shape, np.uint8)
    c = (int(round(cx - x0)), int(round(cy - y0)))
    cv2.circle(m, c, int(r * 1.5), 1, -1)
    cv2.circle(m, c, int(r * 1.1), 0, -1)
    sel = patch[m > 0]
    if sel.size < 20:
        return None
    return float(np.median(sel))


def _hist_median(v8):
    """V 的中位数（直方图版，比 np.median 的排序快一倍）—— 只用于全图 v_thr。"""
    hist = cv2.calcHist([v8], [0], None, [256], [0, 256]).ravel()
    return float(np.searchsorted(np.cumsum(hist), v8.size / 2.0))


def classify_map(hsv, cx, cy, r, ring_fn):
    """在**缩略图**上复判一个候选，判据逐条照抄 ball_detect.classify()。

    ★ 为什么必须抄（2026-09-27 第二十一版）：detect_map 早先只做"颜色掩码 + 连通域 +
      面积"，而参考那一路是 HoughCircles + classify()（黑球要多过三条硬判据：
      占比 >= 0.50 / 圆内亮度均匀(std<=28) / **与周围背景对比 >= 35**）。
      两边判据不一样，±8px 比对就无从谈起 —— 空场实测：缩略图报 25~35 个"球"，
      参考报 0 个，全是暗背景斑块。抄齐判据之后，暗斑块会卡在"对比"那一条上：
      一整片暗区里的圆，自身和周围一样暗，不是球（这正是那条判据的用意）。

    hsv/坐标/半径都是**缩略图**单位（块）；ring_fn 用原图那个 _ring_median_V ——
    V 是 0~255 的绝对量，与分辨率无关，所以同一个函数在缩略图上照样成立。
    """
    h, w = hsv.shape[:2]
    x0, x1 = int(max(0, cx - r)), int(min(w, cx + r + 1))
    y0, y1 = int(max(0, cy - r)), int(min(h, cy + r + 1))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return 'other', 0.0
    patch = hsv[y0:y1, x0:x1]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    m = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (r * 0.72) ** 2   # 内切圆，避开方块四角
    n = float(m.sum())
    if n < 4:                      # 原图是 20 像素；缩略图一个块顶 64 像素，门槛降到 4 块
        return 'other', 0.0

    # ★ 直接拿 uint8 比（2026-09-27 提速）：原来先 astype(int16) 再比 —— 三次
    #   拷贝、每个都是整块 patch，而比较运算符对 uint8 本来就成立（不涉及算术溢出）。
    #   count_nonzero 也比 bool.sum() 快一点，逐候选都要跑，省下来是复利。
    Hc = patch[..., 0][m]
    Sc = patch[..., 1][m]
    Vc = patch[..., 2][m]

    # ★ 2026-10-05 夜：彩色判据的 V 下限 60→40。实锤：画面暗区里的球 V≈45~58、
    #   S 很高——旧口径下"彩色"够不着（V<60）、"黑"又够不着（S>110）⇒ **两条规则
    #   都不沾、候选被复判杀掉**（顶红球/蓝球"没有圈"的根因）。降到 40 后暗区彩球
    #   能过；"必须是彩色物体"仍由 S>=90 把关（灰背景/桌面 S<90，照旧挡在门外）。
    #   （与 ball_detect.classify 的旧口径（V>=60）差异在此注明——V1 全分辨率路
    #   已不在游戏链路里；若将来要复用对比，两边要一起改。）
    f_red = float(np.count_nonzero(((Hc <= 12) | (Hc >= 165)) & (Sc >= 90) & (Vc >= 40))) / n
    f_blue = float(np.count_nonzero((Hc >= 85) & (Hc <= 135) & (Sc >= 90) & (Vc >= 40))) / n
    ring = ring_fn(hsv, cx, cy, r)
    v_thr = 70.0 if ring is None else max(70.0, 0.55 * ring)
    f_black = float(np.count_nonzero((Vc < v_thr) & (Sc <= 110))) / n

    best, best_f = 'other', 0.0
    for cls, f in (('red', f_red), ('blue', f_blue), ('black', f_black)):
        if f > best_f:
            best, best_f = cls, f
    if best == 'black':
        v_in = float(np.median(Vc))
        contrast_ok = (ring is None) or (ring - v_in >= 35.0)
        if best_f < 0.50 or float(Vc.std()) > 28 or not contrast_ok:
            return 'other', best_f
    elif best_f < 0.35:
        return 'other', best_f
    return best, best_f


def _boundary_pts(lab, i):
    """连通域 i 的**边界点**（4 邻域腐蚀后取差集）—— 圆弧拟合要的是弧上的点。"""
    m = (lab == i)
    e = m.copy()
    e[1:, :] &= m[:-1, :]
    e[:-1, :] &= m[1:, :]
    e[:, 1:] &= m[:, :-1]
    e[:, :-1] &= m[:, 1:]
    ys, xs = np.nonzero(m & ~e)
    return np.c_[xs, ys].astype(np.float64)


def _fit_circle(pts):
    """Kasa 最小二乘圆拟合 -> (cx, cy, r)；点太少/退化/发散返回 None。

    ★ 为什么要拟合圆心而不是直接用质心（2026-09-27 第九次上板，拿红球量出来的）：
      球的下半部在阴影里，块平均之后掉出颜色掩码 —— 掩码只剩"球的上半块"，
      直接取质心会**往上偏 10~20px**（实测 39px -> 放宽掩码后 12~17px）。
      而"一段圆弧"本身就足以定出圆心（弧越长越准），所以拿边界点做圆拟合，
      把真圆心**外推**出来 —— 这是几何信息，质心用不到。
    """
    if pts is None or len(pts) < 6:
        return None
    x = pts[:, 0]
    y = pts[:, 1]
    A = np.c_[2.0 * x, 2.0 * y, np.ones(len(x))]
    b = x * x + y * y
    try:
        sol, _res, rank, _sv = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    if rank < 3:
        return None
    cx, cy, c = float(sol[0]), float(sol[1]), float(sol[2])
    r2 = c + cx * cx + cy * cy
    if not np.isfinite(r2) or r2 <= 0.0:
        return None
    return cx, cy, float(np.sqrt(r2))


def frame_sane_map(bgr_map, n_cand=0, rc_max=14.0, cand_max=8):
    """这张地图帧**是不是撕裂/垃圾**的廉价判据（不碰相机、不用黄金模型，~0.3ms）。

    为什么必须有（2026-09-29 夜，接屏幕前补的一环）：那趟 20fps 的长跑里"球"每帧
    跳 ±600px、颜色报 black —— 那不是球，是**半死链路 / 撕裂**送来的垃圾地图。
    它有两个特征，各自都便宜：
      ① **候选一大堆**：真场景 1~3 个（离线复跑 r4 那帧 = 1 个），撕裂帧 16 个。
         候选越多 classify_map 越慢 -> 坏帧还是**最贵**的那种帧（38ms 里大半是它们）；
      ② **相邻行对不上**：离线对比真场景帧行差均值 5.5（r4）/ 8.5（r6），撕裂帧 18.0。
    两个证据**一起**看：单独一个都可能误伤（强水平边缘会抬行差；场上真有 8 个色块时
    候选也会多），凑一起才既打得准又不误杀。
    返回 (ok, why, rc)：rc = 相邻行差均值（无论 ok 与否都给，便于记进汇总）。
    """
    g = cv2.cvtColor(bgr_map, cv2.COLOR_BGR2GRAY).astype(np.int16)   # ★ 必须升到有符号：
    rc = float(np.mean(np.abs(np.diff(g, axis=0))))                  #   uint8 相减会回绕
    if n_cand > cand_max and rc > rc_max:
        return False, '候选 %d 个(>%d) 且相邻行差 %.1f(>%.1f)' % (n_cand, cand_max, rc, rc_max), rc
    return True, '', rc


def detect_map(bgr_map, ring_fn, min_area=4, max_area=350, fit_circle=False):
    """在 128x90 的缩略图上找球。返回 ([{cls,x,y,r,area}], 被杀掉的候选数)
    （x/y 已换算成全分辨率像素）。

    面积下限 4 块（≈ 半径 9 px，对齐参考检测的 min_r=10）；
    上限由 `--max-r` 推出来（默认 max_r=80 -> ~350 块 ≈ 半径 84 px）。

    ★ 候选掩码比 classify() 松一档，**判定仍由 classify_map() 严格把关**
      （2026-09-27 第九次上板，拿真球量出来的）：
      球半径 ~90px，下半部是阴影 —— 全分辨率像素还在 V>=60 里，但**块平均之后**
      掉到阈值以下，于是掩码只剩上半球，质心被拉高 **约 39px**（正是那次 d=38.7/39.1
      的来源）。所以定位用的掩码放宽（S>=50 / V>=35），把整只球圈进候选；
      "是不是球"依旧按 classify() 那三条硬判据判，口径没变。
    """
    hsv = cv2.cvtColor(bgr_map, cv2.COLOR_BGR2HSV)
    # ★ 掩码改用 cv2.inRange（2026-09-27 提速，微基准实测量出来的账）：
    #   原来用 numpy 布尔表达式算三个掩码 —— 128x90 上要 **3.38 ms**，是固定开销里
    #   最大的一笔（每写一个中间结果就分配一次 11520 字节的临时数组，ARM 上吃不消）；
    #   `astype(uint8)*255` 又花 1.61 ms。inRange 是单次 C 调用、直接吐 0/255，
    #   阈值一个没改（H 在 OpenCV 里是 0~179；V < v_thr 等价于 V <= ceil(v_thr)-1）。
    v_thr = max(70.0, 0.55 * _hist_median(hsv[..., 2]))   # 与 classify() 的 ring 判据同款
    vhi = int(max(0, min(255, int(np.ceil(v_thr)) - 1)))
    m_red = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 50, 35), (12, 255, 255)),
        cv2.inRange(hsv, (165, 50, 35), (179, 255, 255)))
    masks = (
        ('red', m_red),
        # ★ 2026-10-05 夜：蓝窗下限 95→85 —— 青蓝色小球缩略图 H≈84~93（全分辨率 103），
        #   差 2~11 点全被挡（用户："蓝球识别不明显"的根）。青/蓝桌垫仍在外（多半
        #   在场区外，由 _track_filter 一票否决）。
        ('blue', cv2.inRange(hsv, (85, 50, 35), (135, 255, 255))),
        ('black', cv2.inRange(hsv, (0, 0, 0), (179, 110, vhi))),
    )
    out = []
    n_rej = 0
    for cls, m in masks:
        m8 = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(m8, 8)
        for i in range(1, n):
            a = int(stats[i, cv2.CC_STAT_AREA])
            if a < min_area or a > max_area:
                n_rej += 1
                continue
            cx, cy = cent[i]
            r = float(np.sqrt(a / np.pi))
            # ★ 圆拟合（默认**关**，2026-09-27 第十次上板实测是回归）：
            #   想用它把"阴影切掉下半球"造成的质心偏移外推回来，结果红球误差从
            #   2~13px 变成 32~85px —— 因为掩码下缘那条**被阴影切断的直边**不是圆弧上
            #   的点（那是弦，不是弧），Kasa 拟合被它拉偏；而护栏（圆心离质心 <=1.5r）
            #   对 r=8~11 块来说等于放行 96~130px，太松没拦住。要用得先把"弦点"剔掉
            #   （只喂凸包上的弧点，或 RANSAC），并收紧护栏 —— 那是下一步的活。
            if fit_circle:
                fit = _fit_circle(_boundary_pts(_lab, i))
                if fit is not None:
                    fx, fy, fr = fit
                    if (np.hypot(fx - cx, fy - cy) <= 0.5 * r
                            and 0.6 * r <= fr <= 1.6 * r):
                        cx, cy, r = fx, fy, fr
            # ★ 复判：与参考那路同款判据（暗背景斑块会卡在"对比"这一条上被杀掉）
            cls2, f = classify_map(hsv, float(cx), float(cy), r, ring_fn)
            if cls2 != cls:
                n_rej += 1
                continue
            out.append(dict(cls=cls2,
                            x=float(cx * BLK + BLK / 2.0),      # 块心 -> 像素心
                            y=float(cy * BLK + BLK / 2.0),
                            r=r * BLK,
                            area=a, f=f))
    return out, n_rej          # (留下的球, 被复判/面积杀掉的候选数 —— 打印出来好判断口径)


def detect_map_from_stats(stats, hsv, ring_fn):
    """PL 候选统计 -> 与 detect_map 同口径的复判 + 输出（差距 1 混合口径）。

    PL（map_cc）已经做完 掩码 -> 3x3 OPEN -> 8 连通 CCL，给出每个连通域的
    cls/area/Σx/Σy；这里只做 detect_map 的最后一步：
      质心 (cx,cy)=Σ/area、r=√(area/π) -> classify_map 复判 -> 口径输出。
    **等价于把 detect_map 的颜色掩码/形态学/连通域整段搬进 PL**；PS 只剩复判
    （每个候选一个几十块的小 patch），不再扫全图。

    stats: iterable of dict(cls=0/1/2, area, sx, sy)
           （cls 0=red 1=blue 2=black，与 RTL 的 ccl_c 一致）
    返回 (候选列表, 被复判杀掉的个数)，候选字段与 detect_map 完全一致。
    """
    names = ('red', 'blue', 'black')
    out = []
    n_rej = 0
    for s in stats:
        a = int(s['area'])
        if a <= 0:
            n_rej += 1
            continue
        cx = float(s['sx']) / float(a)
        cy = float(s['sy']) / float(a)
        r = float(np.sqrt(a / np.pi))
        cls2, f = classify_map(hsv, cx, cy, r, ring_fn)
        if cls2 != names[int(s['cls'])]:
            n_rej += 1
            continue
        out.append(dict(cls=cls2,
                        x=float(cx * BLK + BLK / 2.0),
                        y=float(cy * BLK + BLK / 2.0),
                        r=r * BLK,
                        area=a,
                        f=f))
    return out, n_rej


def _diff_map_m(bgr_map, bg_map):
    """缩略图版差分：三通道各自作差取最大（照抄 ball_detect._diff_map 的口径）。

    灰度差对彩球很钝（红球在灰桌面上灰度只差 40，R 通道差 137）—— 所以不转灰度。
    地图分辨率低，模糊核用 3x3（全分辨率那边是 5x5）。
    """
    d = cv2.absdiff(bgr_map, bg_map).max(axis=2)
    return cv2.GaussianBlur(d, (3, 3), 0)


def detect_map_bg(bgr_map, bg_map, ring_fn, max_area=350, diff_thr=32, need_frac=0.5):
    """缩略图版背景差分 = **颜色掩码出候选 + 差分做"是不是新出现的"确认**。

    ★ 为什么是这个组合（2026-09-27 实测，第一版走错了）：
      第一版照抄 detect_bg：差分出连通域 -> minEnclosingCircle -> 圆度。结果
      **一帧一个球都找不到** —— 因为球**带影子**，差分块是"球+影子"，外接圆半径
      偏大、圆内红占比被影子拉低，被 classify_map 自己刷掉（dump 里黄圈正中球心、
      绿圈一个都没有，就是这个状态）。
      反过来的组合才顺：**形状和大小由颜色掩码给**（球的轮廓不带影子，干净），
      **差分只回答"这块地方是不是新出现的"** —— 桌面、线缆、阴影这些静态东西
      差分为零，自然被排除。这正好也是路线图要的那句话：
      "拍一张没有球的背景，做差之后球才真正孤立"。

    need_frac：候选圆盘内"差分超阈值"的像素比例下限（默认 0.5 = 过半）。
    """
    d = _diff_map_m(bgr_map, bg_map)
    det, n_rej = detect_map(bgr_map, ring_fn, max_area=max_area)
    out = []
    for ball in det:
        cx = ball['x'] / float(BLK) - 0.5          # 像素心 -> 块坐标
        cy = ball['y'] / float(BLK) - 0.5
        r = ball['r'] / float(BLK)
        rr = max(1.0, r * 0.7)                     # 只看圆盘内部，避开边缘过渡
        x0 = int(max(0, np.floor(cx - rr)))
        y0 = int(max(0, np.floor(cy - rr)))
        x1 = int(min(BW, np.ceil(cx + rr) + 1))
        y1 = int(min(BH, np.ceil(cy + rr) + 1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            n_rej += 1
            continue
        patch = d[y0:y1, x0:x1]
        yy, xx = np.ogrid[y0:y1, x0:x1]
        disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= rr * rr
        if not disk.any():
            n_rej += 1
            continue
        frac = float(np.count_nonzero(patch[disk] > diff_thr)) / float(disk.sum())
        if frac < need_frac:
            n_rej += 1                            # 和背景一样 = 不是新出现的 -> 不是球
            continue
        ball['newfrac'] = frac
        out.append(ball)
    return out, n_rej


def _fill_holes(comp):
    """把连通域内部的洞填上。返回填好的布尔掩码。

    ★ 为什么必须填（2026-09-27 第十二次上板，一张 dump 把问题定死了）：
      球面上有**白字和高光**（低饱和度）→ 红色掩码在球中间挖了个洞 →
      连通域成了"环"，质心被拉到一边（实测参考偏了 85px，而缩略图的绿圈正中球心）。
      填洞只补内部、不粘连外部，比"大核闭运算"安全（闭运算会把邻近的块糊在一起）。
    """
    p = np.pad(comp.astype(np.uint8), 1)
    ff = p.copy()
    m2 = np.zeros((p.shape[0] + 2, p.shape[1] + 2), np.uint8)
    cv2.floodFill(ff, m2, (0, 0), 1)          # 从角上灌背景
    return (p | (ff == 0))[1:-1, 1:-1].astype(bool)


def detect_full(bgr, classify, ring_fn, min_area_px=300, max_r=140.0):
    """全分辨率参考：**颜色连通域 + 项目自己的 classify() 复判**（取代霍夫）。

    ★ 为什么换（2026-09-27 第十/十一次上板）：这个场景（亮窗 + 线缆 + 木桌）把
      HoughCircles 拖到 **110 秒/帧**，还找出一堆假圆（一帧 6 个"红球"）——
      分母被撑大，严格判据没法看。而球是**有颜色**的：全分辨率上跑同一条颜色掩码、
      取连通域质心，既快（几十毫秒）又更接近"球心真值"。
      判据仍用项目自己的 classify()（全分辨率版），与缩略图那边的 classify_map()
      同源同口径 —— 这才是"两边可比"。
      （霍夫那路保留：--ref hough 可切回去，路线图原始判据还认它。）
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    Hc = hsv[..., 0].astype(np.int16)
    Sc = hsv[..., 1].astype(np.int16)
    Vc = hsv[..., 2].astype(np.int16)
    v_thr = max(70.0, 0.55 * float(np.median(Vc)))
    masks = (
        ('red', ((Hc <= 12) | (Hc >= 165)) & (Sc >= 50) & (Vc >= 35)),
        ('blue', (Hc >= 95) & (Hc <= 135) & (Sc >= 50) & (Vc >= 35)),
        ('black', (Vc < v_thr) & (Sc <= 110)),
    )
    out = []
    for cls, m in masks:
        m8 = m.astype(np.uint8) * 255
        m8 = cv2.morphologyEx(m8, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        n, lab, stats, cent = cv2.connectedComponentsWithStats(m8, 8)
        for i in range(1, n):
            # ★ 先填洞再算面积/质心：球上的白字会把掩码挖成环，直接用连通域质心
            #   会偏（见 _fill_holes 的说明）。填完的才是"球面"这个圆盘。
            comp = _fill_holes(lab == i)
            a = int(comp.sum())
            if a < min_area_px:
                continue
            ys, xs = np.nonzero(comp)
            cx, cy = float(xs.mean()), float(ys.mean())
            r = float(np.sqrt(a / np.pi))
            if r > max_r:
                continue
            cls2, f = classify(bgr, float(cx), float(cy), r)   # 项目自己的判据
            if cls2 != cls:
                continue
            out.append(dict(cls=cls2, x=float(cx), y=float(cy), r=r, area=a, f=f))
    return out


def bg_words_to_bgr(words, mdisp):
    """背景（原始地图字）-> BGR。**必须在 mswap 定下来之后调用**（懒解码）。

    背景存原始字就是为了让它与"当时的字节序判定"解耦：今天的板子上驱动时判 0 时判 1，
    存解码图的话背景会和当前解码逐字节错位，差分永远对不上。
    """
    m = np.ascontiguousarray(np.asarray(words, np.uint16).reshape(BH, BW))
    b = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
    if mdisp:
        b = b[..., ::-1]
    return cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR5652BGR)


def load_bg_words(path=None):
    """读背景原始字；文件不存在/格式不对返回 None。"""
    try:
        w = np.load(path or BG_MAP)
        if w.ndim != 1 or w.size != BH * BW:
            return None
        return w
    except Exception:
        return None


def compare(ref, det):
    """参考球 <-> 缩略图球的一对一配对（同类、按距离从近到远贪心）。

    ★ 2026-09-27 改（第一次拿真球跑就暴露出问题）：原来是"每个参考球各自取最近的
      同类缩略图球"，**允许多个参考球抢同一个缩略图球**。红球那轮缩略图仍有一堆
      误报，于是同一个假 blob 被三个参考球各配一次，误差被算成 157px 的假数字 ——
      到底"真球有没有被找到、差几像素"根本看不出来。
      贪心一对一至少保证：每个缩略图球只认领一个参考球，距离排序后先满足最近的配对。
      返回 [(cls, d, dx, dy, dr)]，没配上 d=None。
    """
    pairs = []
    for i, rb in enumerate(ref):
        for j, d in enumerate(det):
            if d['cls'] != rb['cls']:
                continue
            dist = float(np.hypot(d['x'] - rb['x'], d['y'] - rb['y']))
            pairs.append((dist, i, j))
    pairs.sort(key=lambda t: t[0])

    rows = [(rb['cls'], None, None, None, None) for rb in ref]
    used_d = set()
    for dist, i, j in pairs:
        if rows[i][1] is not None or j in used_d:
            continue
        rb, d = ref[i], det[j]
        rows[i] = (rb['cls'], dist, d['x'] - rb['x'], d['y'] - rb['y'], d['r'] - rb['r'])
        used_d.add(j)
    return rows


# ---------------------------------------------------------------- 主流程
def main():
    n = 20
    max_r = 80
    param2 = None
    want_fit = '--fit-circle' in sys.argv
    ref_mode = 'blob'
    bgcap = 0
    force_swap = None
    no_bg = '--no-bg' in sys.argv
    for i, a in enumerate(sys.argv):
        if a == '--ref' and i + 1 < len(sys.argv):
            ref_mode = sys.argv[i + 1]
        if a == '--bgcap' and i + 1 < len(sys.argv):
            bgcap = int(sys.argv[i + 1])
        if a == '--swap' and i + 1 < len(sys.argv):
            force_swap = int(sys.argv[i + 1])
    dump = '--dump' in sys.argv
    for i, a in enumerate(sys.argv):
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
        if a == '--max-r' and i + 1 < len(sys.argv):
            max_r = float(sys.argv[i + 1])
        if a == '--param2' and i + 1 < len(sys.argv):
            param2 = float(sys.argv[i + 1])

    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream(BIT).download()
        print('[加载] bit 已下载')

    from ball_detect import (_make_cap, _init_video, detect, detect_bg, parse,
                             _ring_median_V, classify as _classify)
    from fastmem import FastMem

    # ★ parse() 返回的是 6 元组 (opts, frames, live_port, img_file, do_bgcap, do_selftest)
    #   —— 只取第 0 项；直接拿元组去当 dict 用会报 "tuple indices must be integers"
    opts = parse([])[0]                  # 参考检测用默认参数（min_r=10 max_r=80 等）
    # ★ 球比默认上限还大（2026-09-27 第九次上板实测：桌上的红球半径 ~90px，而默认
    #   max_r=80 —— 参考检测**每帧都找不到它**，而且 HoughCircles 自己也不会搜到
    #   半径 80 以上的圆）。演示用的球多大就设多大：--max-r 140 留够余量。
    opts['max_r'] = max_r
    if param2 is not None:
        # 参考侧的霍夫累加器阈值：默认 30，亮窗/条纹那种场景会找出一堆假圆
        # （实测一帧 4~7 个"蓝球"），调高更严、地面真值更干净。
        opts['param2'] = param2
        print('[配置] 参考 Hough param2 = %g（默认 30，越高越严）' % param2)
    print('[配置] 参考实现 = %s' % ref_mode)
    map_max_area = int(np.pi * (max_r / float(BLK)) ** 2 * 1.2)
    print('[配置] 参考 max_r=%d px  ->  缩略图 max_area=%d 块（约半径 %.0f px）'
          % (max_r, map_max_area, np.sqrt(map_max_area / np.pi) * BLK))
    cap = _make_cap()
    _init_video(cap)
    _swd = getattr(cap, '_swap_decided', None)
    if force_swap is not None:
        _swd = bool(force_swap)
        print('[配置] 字节序由 --swap %d 强制指定（绕过驱动的相关性判定）' % force_swap)
    swap = 1 if _swd else 0
    print('[配置] 原图字节序 swap = %d（驱动按通道相关性判定，与 C 项同一套）' % swap)
    # ★ 驱动判不出来（None）= 它在说"这帧疑似错位/撕裂，我不敢定" —— 这时候按
    #   swap=0 硬跑，原图解码多半是错的，参考那路整个作废（2026-09-27 实测）。
    #   直接中止，让上层去救链路。
    if _swd is None:
        print('=> ★ 环境问题：驱动的字节序判定是 None（本帧疑似撕裂/错位）—— '
              '先救链路再跑，别拿这一轮的数字当结论')
        return 9
    # ★ 地图的字节序**不跟着驱动走**：驱动是从原图的相关性判的（随场景明暗会变），
    #   而地图走的是另一条 PL 路径。所以它由 map_gate() 拿黄金模型实测决定（见下）。
    mswap = None

    with FastMem(MAP_BASE, 0x8000) as mm, \
         FastMem(CTRL_BASE, 0x1000) as mc, \
         FastMem(STAT_BASE, 0x1000) as ms:

        # soft_rst：清 sticky errs + 让缩略图用复位后的新帧重建（reset 后头几帧还是旧内容）
        mc.wr_u32(0x00, (swap << 2) | 0x02)
        time.sleep(0.2)
        mc.wr_u32(0x00, (swap << 2))
        time.sleep(0.5)

        # 流健康门（与 vision_diag 的 A/B 项同一套账本）：不健康就说清楚，别让后面
        # "参考找不到球"被误读成"缩略图检测不行"
        st = ms.rd_u32(0x00)
        st2 = ms.rd_u32(0x08)
        print('[账本] A 行数=%d 字数=%d  行长度=%d errs=0x%02X'
              % ((st >> 16) & 0xFFFF, st & 0xFFFF, (st2 >> 21) & 0x7FF, (st2 >> 15) & 0x3F))

        # ★ 背景采集模式（--bgcap N）：画面里**没有球**时拍 N 帧，取中位当背景。
        #   为什么取中位而不是均值：中位对偶发噪声/一帧抖动免疫。
        if bgcap:
            print('[背景] 采集 %d 帧 —— 画面里**不要有球**（也不要有手在动）...' % bgcap)
            mlist, flist = [], []
            for i in range(bgcap):
                try:
                    raw, idx = _grab_raw(cap)
                except Exception as e:
                    print('[背景] 第 %d 帧抓失败：%s' % (i + 1, str(e)[:100]))
                    continue
                b, _nt, fc = read_map(mm, mc)
                # ★★ 逐帧把关（2026-09-27 补的漏洞）：第一版拍背景**不看帧的质量**，
                #   采集期间链路卡了两次（撕裂帧），自检两种字序都只有 %（0.1~2.1%），
                #   脚本却照样把它存成背景 —— 一张撕裂的背景会让后面所有差分都错。
                #   现在：每一帧都过 map_gate，只有干净帧才算数；不够 3 帧就拒收。
                bs, p0, p1 = map_gate(raw, b)
                if bs is None:
                    print('[背景] 第 %d 帧：拿不到黄金模型，跳过' % (i + 1))
                    continue
                if max(p0, p1) < 60.0:
                    print('[背景] 第 %d 帧自检只有 %.1f%%/%.1f%% —— 撕裂帧，丢'
                          % (i + 1, p0, p1))
                    continue
                if mswap is None:                     # 背景采集顺便定字节序
                    mswap = bs
                    print('[背景] 自检 swap=0 %.1f%% / swap=1 %.1f%% -> 用 %d'
                          % (p0, p1, mswap))
                    # ★ 顺手把**显示字节序**也量了：这条路径会把 mswap 提前定下来，
                    #   主循环里那段自检就不会再跑 —— 而 --bgcap 这一趟正是出图的那趟，
                    #   漏了它等于"出图时用的还是推导值"（洋红图的入口就在这里）。
                    disp = display_swap_check(raw, b, swap, mswap)
                    mdisp = disp['mdisp']
                    if disp['pct0'] is None:
                        print('[背景] 显示字节序没法量（%s）—— 退回推导值 mdisp=%d'
                              % (disp['why'], mdisp))
                    else:
                        print('[背景] 显示字节序%s：mdisp = %d（swap0 %.1f%% / swap1 %.1f%%，'
                              '推导 %d）'
                              % ('**实测**' if disp['ok'] else '测不出来，退回推导值',
                                 mdisp, disp['pct0'], disp['pct1'], disp['derived']))
                elif bs != mswap:
                    print('[背景] 第 %d 帧字序关系变了（%d -> %d），跳过' % (i + 1, mswap, bs))
                    continue
                mlist.append(np.frombuffer(b, np.uint16).copy())   # ★ 存原始字（与字节序无关）
                flist.append(raw_to_bgr(raw, swap).astype(np.float32))
                print('[背景] 收到第 %d 帧（自检 %.1f%%）' % (len(mlist), max(p0, p1)))
                time.sleep(0.05)
            if mswap is None:
                print('=> ★ 一帧干净的都没拍到 —— 链路正在卡，先救链路再拍背景')
                return 9
            if len(mlist) < 3:
                print('=> ★ 只拍到 %d 帧干净帧，不足以做背景（要 >=3）—— 没存，'
                      '先救链路再拍' % len(mlist))
                return 9
            bg_map_words = np.median(np.stack(mlist), axis=0).astype(np.uint16)
            bg_map = bg_map_words   # 变量名沿用（下面存的就是原始字）
            bg_full = np.median(np.stack(flist), axis=0).astype(np.uint8)
            np.save(BG_MAP, bg_map)
            cv2.imwrite(BG_FULL, bg_full)
            print('[背景] 已存：%s（原始地图字 %d 个，与字节序无关）+ %s（%dx%d）'
                  % (BG_MAP, bg_map.size, BG_FULL, bg_full.shape[1], bg_full.shape[0]))
            print('=> 背景采集完成。把球放回画面，再跑一次本脚本（不加 --bgcap）就自动用差分')
            return 0

        # 有背景就自动启用差分（--no-bg 可强制关掉，走单帧颜色掩码那套）
        bg_map = bg_full = None
        bg_words = None
        if not no_bg:
            try:
                _w = np.load(BG_MAP)
                # ★ 背景存的是**原始地图字**（与字节序无关）-> 等 mswap 测出来之后再
                #   解码（懒解码，见主循环）。若在这里就解，等于按 swap 猜一个显示
                #   字节序；驱动判定一变（今天实测时 0 时 1），背景与当前解码就逐字节
                #   错位 -> 差分对不上 -> 球一个都检测不到（实测只攒到 1 个有效帧）。
                if _w.ndim != 1 or _w.size != BH * BW:
                    raise ValueError('背景不是原始字格式')
                bg_words = _w
                bg_full = cv2.imread(BG_FULL)
                if bg_full is not None and bg_full.shape[0] != H:
                    print('[背景] ★ 全分辨率背景尺寸不对，忽略它（只用缩略图背景）')
                    bg_full = None
            except Exception as e:
                bg_map = bg_full = None
                bg_words = None
            if bg_words is not None:
                print('[背景] 已载入差分背景（原始字格式，稍后按当前字节序解码）')
            else:
                print('[背景] 没有背景图 —— 走单帧颜色掩码（先 --bgcap 12 拍一张更好）')

        rows, timing = [], []
        per_frame = []
        n_ref_frames = 0
        last = None
        gate = None                       # 地图字节序自检结果 (p0, p1, mswap)
        mdisp = None                      # 显示字节序：**实测**值（测不出来时退回推导值）
        fc_prev = None
        t_prev = None
        n_skip = 0
        gate_tries = 0
        for k in range(n):
            gate_ms = None            # 自检只在第 1 帧跑一次，不能混进"每帧"的时序
            t0 = time.perf_counter()
            try:
                raw, idx = _grab_raw(cap)
            except Exception as e:
                print('[抓帧] ★ 第 %d 帧失败：%s' % (k + 1, str(e)[:140]))
                continue
            t1 = time.perf_counter()
            buf, ntry, fc = read_map(mm, mc)
            t2 = time.perf_counter()

            # ★★ 丢帧判据（2026-09-27 第八次上板）：链路一卡、看门狗复位视频域，
            #   那一帧就是**撕裂帧** —— 参考检测会在花帧上跑出 34~58 个"圆"、耗时
            #   17 秒，地图也跟着错位。花帧的特征：这一轮窗口里 frame_cnt 几乎没涨
            #   （一轮迭代约 1s ≈ 30 帧，卡过就只剩个位数）。丢掉它既避免假结论，
            #   也顺手省掉那 17 秒的垃圾霍夫。
            # ★★ 用**速率**判，不用"涨没涨"：一轮迭代 1~10 秒（霍夫占大头），
            #   中途卡 1.3 秒就少 ~35 帧，而 frame_cnt 照样在涨 —— 原来的
            #   "涨 <10 帧才丢"在慢迭代下永远不触发（第十次上板实测：5 次自愈、
            #   0 次丢帧）。这正是路线图里写过的"慢速半死"盲区，这里用速率补上。
            if fc_prev is not None and t_prev is not None:
                dt_iter = max(1e-3, t2 - t_prev)
                fps_now = (fc - fc_prev) / dt_iter
                if fps_now < 20.0:
                    n_skip += 1
                    print('[帧 %2d] 丢：这一轮窗口里链路卡过（%.1f fps，应 ~27）'
                          % (k + 1, fps_now))
                    fc_prev, t_prev = fc, t2
                    continue
            fc_prev, t_prev = fc, t2
            # 第二条判据（更直接）：账本里"上一帧写字数"没到 5760 = 那一帧根本没写完，
            # 抓到的原图与地图必然错位。健康值 stat=0x02D01680（行数=720 字数=5760）。
            st_now = ms.rd_u32(0x00)
            if (st_now & 0xFFFF) != 5760:
                n_skip += 1
                print('[帧 %2d] 丢：上一帧没写完（字数=%d，应 5760）—— 撕裂帧'
                      % (k + 1, st_now & 0xFFFF))
                continue

            # ★ 地图字节序自检：拿**干净帧**来定（撕裂帧上它必然失败，白判一轮）。
            #   没定下来之前每轮都试，最多 3 次；一直不成功就按 RC=9 报环境问题。
            if mswap is None and gate_tries < 3:
                gate_tries += 1
                _tg = time.perf_counter()
                bs, p0, p1 = map_gate(raw, buf)
                gate_ms = (time.perf_counter() - _tg) * 1000.0
                content_ok = (bs is None)        # bs=None = 没黄金模型，没法判内容
                if bs is None:
                    mswap = 1
                    print('[地图] ★ 没有 vision_golden，自检做不了 —— 按本版实测的'
                          '"地图字序关系 = 1"解码，但这一轮的比对结论要打个问号')
                else:
                    mswap = bs
                    gate = (p0, p1, mswap)
                    content_ok = max(p0, p1) >= 60.0
                    print('[地图] 自检（第 %d 次，与黄金块平均逐通道 <=1 LSB）：'
                          'swap=0 -> %.1f%% / swap=1 -> %.1f%%  => 地图字序关系 = %d'
                          % (gate_tries, p0, p1, mswap))
                    if not content_ok:
                        if gate_tries < 3:
                            mswap = None   # 判不出来：下一轮换个干净帧再试
                            print('[地图] ★★ 两种字节序都低（<=60%%）—— 这一帧的地图'
                                  '内容不可信（多半是撕裂帧），换一帧再判')
                        else:
                            # ★ 三次都判不出来 = 链路在持续吐撕裂帧。**中止**，不要
                            #   "硬着头皮跑"（2026-09-27 实测：那种状态下缩略图恒报 0 个、
                            #   参考报几十个假球，跑完只会得到一屏假结论，还白耗几分钟）。
                            print('[地图] ★★ 三次都没判出来（最后一帧 %.1f%%/%.1f%%）——'
                                  ' 链路在持续吐撕裂帧，本轮到此为止' % (p0, p1))
                            print('=> 环境问题：地图内容不可信（不是字节序问题），'
                                  '先救链路（stall_probe --pwr-at 或整机断电）再跑')
                            return 9

                # ★★ 内容自检过了 ≠ 图渲染得对：再**量一次显示字节序**（位域比较对
                #    "内存里谁先谁后"完全无感 —— 99.4% 全绿也可能是张洋红图）。
                #    判得动就用实测值；判不动（近纯色场景/地图内容本身坏）退回推导值，
                #    并且**必须把退回这件事打出来**（静默失败最贵）。
                if content_ok:
                    _td = time.perf_counter()
                    disp = display_swap_check(raw, buf, swap, mswap)
                    mdisp = disp['mdisp']
                    gate_ms = (time.perf_counter() - _tg) * 1000.0
                    if disp['pct0'] is None:
                        print('[地图] 显示字节序没法量（%s）' % disp['why'])
                    elif disp['ok']:
                        print('[地图] 显示字节序**实测**（两种渲染 vs 真图，逐像素）：'
                              'swap=0 -> %.1f%% / swap=1 -> %.1f%%  => mdisp = %d（差 %.1f%%，'
                              '耗时 %.0fms）'
                              % (disp['pct0'], disp['pct1'], mdisp, disp['gap'],
                                 (time.perf_counter() - _td) * 1000.0))
                        if mdisp != disp['derived']:
                            print('[地图]   ★★ 实测 %d 与推导值 %d **不一致** —— 以实测为准。'
                                  '这正是"自检全绿、图是洋红"那个坑的入口：'
                                  '推导依赖驱动/PL 两处 swap 一致，实测不依赖'
                                  % (mdisp, disp['derived']))
                    else:
                        print('[地图] 显示字节序**测不出来**（swap=0 -> %.1f%% / '
                              'swap=1 -> %.1f%%）：%s —— 退回推导值 mdisp = %d '
                              '（= 原图 swap %d ^ 字序关系 %d）'
                              % (disp['pct0'], disp['pct1'], disp['why'],
                                 mdisp, swap, mswap))

            if mswap is None:      # 自检这一帧没过：本帧丢弃，下一轮换个干净帧再判
                n_skip += 1
                continue
            if bg_words is not None and bg_map is None:
                # mswap 已定 -> 现在才按显示字节序解背景（懒解码，见上）
                _d = mdisp if mdisp is not None else (swap ^ mswap)
                _m = np.ascontiguousarray(bg_words.reshape(BH, BW).astype(np.uint16))
                _b = np.dstack([(_m & 0xFF).astype(np.uint8), (_m >> 8).astype(np.uint8)])
                if _d:
                    _b = _b[..., ::-1]
                bg_map = cv2.cvtColor(np.ascontiguousarray(_b), cv2.COLOR_BGR5652BGR)
                print('[背景] 已按当前显示字节序解码（mdisp=%d，swap=%d mswap=%d）'
                      % (_d, swap, mswap))

            mbgr = map_to_bgr(buf, mdisp if mdisp is not None else (swap ^ mswap))
            if bg_map is not None:
                det, n_rej = detect_map_bg(mbgr, bg_map, _ring_median_fast,
                                           max_area=map_max_area)
            else:
                det, n_rej = detect_map(mbgr, _ring_median_fast, max_area=map_max_area,
                                        fit_circle=want_fit)
            t3 = time.perf_counter()
            fbgr = raw_to_bgr(raw, swap)
            if bg_full is not None:
                # 参考优先用差分（项目自己的 detect_bg：圆度判据 + classify）——
                # 两边同口径才是"可比"的。★ 但差分对参考更苛刻：球**带影子**时
                # 差分块是"球+影子"，minEnclosingCircle 半径偏大、圆度掉下来，
                # 于是被 detect_bg 自己的判据刷掉（实测一帧 0 个）。所以配一条回退：
                # 差分一个都没找到就退回颜色连通域（detect_full），并说出来 —— 
                # 不然"参考 0 个"会被误读成"没有球"。
                ref = detect_bg(fbgr, bg_full, opts.get('diff_thr', 32),
                                opts['min_r'], max_r)
                if not ref and gate is not None and max(gate[0], gate[1]) >= 60.0:
                    # ★ 只在"地图自检认下这一轮"（= 帧可信）时才回退。撕裂帧上
                    #   detect_bg 会给 0 个、detect_full 会给几十个假球 ——
                    #   回退就变成"生产假结论"（2026-09-27 实测 52 个假球）。
                    ref = detect_full(fbgr, _classify, _ring_median_V, max_r=max_r)
                    print('     （差分参考 0 个 -> 回退颜色连通域，%d 个）' % len(ref))
            elif ref_mode == 'hough':
                ref = detect(fbgr, opts, None)   # 路线图原判据：霍夫圆 + classify
            else:
                ref = detect_full(fbgr, _classify, _ring_median_V, max_r=max_r)

            t4 = time.perf_counter()

            rows_k = compare(ref, det)
            rows.extend(rows_k)
            # ★ 每帧"最佳同类配对"（2026-09-27 第九次上板加的诊断）：这一晚的真问题
            #   不是"缩略图找不准球"，而是**两端各自的误报**把严格判据的分母撑大了
            #   （参考在亮窗条纹上找 4~7 个假蓝球、缩略图在暗线缆上找 8~16 个假黑块）。
            #   严格判据（每个参考球都 <=8px）先照原样判，但另外把"球到底定没定准"
            #   单独量出来 —— 这才是游戏真正要的那个数。
            per_frame.append(dict(
                k=k + 1,
                ref_n={c: sum(1 for b in ref if b['cls'] == c) for c in ('red', 'blue', 'black')},
                best={c: min([r[1] for r in rows_k if r[0] == c and r[1] is not None]
                             or [None]) for c in ('red', 'blue', 'black')}))
            if ref:
                n_ref_frames += 1
            timing.append(dict(read=(t2 - t1), grab=(t1 - t0), mapdet=(t3 - t2),
                               refdet=(t4 - t3), e2e=(t4 - t0), gate=gate_ms))
            # ★ 落盘用**哪一帧**：挑"参考检测最快"的那帧（2026-09-27 第七次上板）。
            #   末帧可能正好撞上链路卡死后被看门狗救回来的**撕裂帧** —— 那次两张
            #   调试图上半幅全是花的，白看图。干净帧的特征就是 Hough 跑得快
            #   （花帧边缘爆炸：34 个圆、15.9 秒）。所以按耗时挑最小的那帧。
            if last is None or (t4 - t3) < last['refdet_s']:
                last = dict(mbgr=mbgr, det=det, ref=ref, fbgr=fbgr, k=k, idx=idx, ntry=ntry,
                            mapbuf=buf, rawbuf=raw, refdet_s=(t4 - t3))

            def _cnt(lst):
                c = {}
                for b in lst:
                    c[b['cls']] = c.get(b['cls'], 0) + 1
                return ' '.join('%s=%d' % (k2, c[k2]) for k2 in ('red', 'blue', 'black') if k2 in c)
            shown = ' '.join('%s d=%s' % (r[0], 'None' if r[1] is None else '%.1f' % r[1])
                             for r in rows_k)
            print('[帧 %2d] 缩略图 %2d 个[%s] / 参考 %2d 个[%s]（复判杀掉 %d）  | %s'
                  % (k + 1, len(det), _cnt(det), len(ref), _cnt(ref), n_rej,
                     shown if shown else '（参考没找到球）'))

    # ------------------------------------------------------------ 汇总
    n_ref = len(rows)
    matched = [r for r in rows if r[1] is not None]
    ds = np.array([r[1] for r in matched]) if matched else np.array([])
    print()
    if gate is not None:
        print('地图自检：swap=0 %.1f%% / swap=1 %.1f%%（用 %d）—— 这一行是"比对上不上算"'
              '的前提' % (gate[0], gate[1], gate[2]))
    print('==== 比对汇总（%d 帧，其中链路卡过被丢掉 %d 帧；参考共找到 %d 个球；'
          '有球的帧 %d）====' % (n, n_skip, n_ref, n_ref_frames))
    if n_ref == 0:
        print('  ★ 参考检测一个球都没找到 —— 没东西可比。先看 /boot/ballref_d.png')
    else:
        print('  同类匹配上 %d/%d（%.0f%%）' % (len(matched), n_ref, 100.0 * len(matched) / n_ref))
        if ds.size:
            print('  球心误差：均值 %.2f px，中位 %.2f，P90 %.2f，最大 %.2f（判据 ≤ %.0f）'
                  % (ds.mean(), float(np.median(ds)), float(np.percentile(ds, 90)),
                     ds.max(), TOL_PX))
            for cls in ('red', 'blue', 'black'):
                c = np.array([r[1] for r in matched if r[0] == cls])
                if c.size:
                    print('    %-5s n=%2d  均值 %.2f  最大 %.2f px'
                          % (cls, c.size, c.mean(), c.max()))
            n_over = int((ds > TOL_PX).sum())
            print('  超差（> %.0f px）%d 个' % (TOL_PX, n_over))

        # ---- 诊断：球到底定没定准（与上面的严格判据分开看）----
        print('')
        print('---- 球定位诊断（每帧"参考球 <-> 缩略图球"的最佳同类配对）----')
        for cls in ('red', 'blue', 'black'):
            fr = [p for p in per_frame if p['ref_n'][cls] > 0]
            if not fr:
                continue
            best = np.array([p['best'][cls] for p in fr if p['best'][cls] is not None])
            n_ok = int((best <= TOL_PX).sum()) if best.size else 0
            print('  %-5s 有球的帧 %2d，其中最佳配对 <=%.0f px 的 %2d 帧（%.0f%%）'
                  % (cls, len(fr), TOL_PX, n_ok, 100.0 * n_ok / len(fr)))
            if best.size:
                print('        最佳配对距离：均值 %.1f  中位 %.1f  最小 %.1f  最大 %.1f px'
                      % (best.mean(), float(np.median(best)), best.min(), best.max()))
        n_multi = sum(1 for p in per_frame
                      if sum(p['ref_n'].values()) > 3)
        print('  （参考每帧找到 >3 个球的帧数：%d/%d —— 这一栏高说明参考自己在误报，'
              '严格判据的分母被撑大了）' % (n_multi, len(per_frame)))
    if timing:
        # ★ 只统计"每帧都跑"的那些帧：第 1 帧多跑了一次地图自检（一次性），
        #   混进来会把"缩略图检测"的均值抬高一倍（实测 16ms -> 98ms），是假数字。
        per = [t for t in timing if not t.get('gate')] or timing
        for key, name, lim in (('grab', '抓原图', None), ('mapdet', '缩略图检测', 5.0),
                               ('refdet', '全分辨率参考检测', None), ('e2e', '端到端', 33.0)):
            v = np.array([t[key] for t in per]) * 1000.0
            tag = '' if lim is None else ('  √' if v.mean() <= lim else '  × 超预算')
            print('  [时序] %-18s 均值 %6.2f ms  最大 %6.2f ms%s'
                  % (name, v.mean(), v.max(), tag))
        g = [t['gate'] for t in timing if t.get('gate')]
        if g:
            print('  [时序] %-18s 一次性 %.1f ms（只在第 1 帧，不计入上面的均值）'
                  % ('地图自检', g[0]))

    # 读回单独量一次（不含检测，纯净口径）
    if timing:
        v = np.array([t['read'] for t in per]) * 1000.0
        tag = '  √' if v.mean() <= 2.0 else '  × 超预算'
        print('  [时序] %-18s 均值 %6.2f ms  最大 %6.2f ms%s'
              % ('读回缩略图(23KB)', v.mean(), v.max(), tag))

    # ------------------------------------------------------------ 落盘
    # ★ 写 ext4（/home/xilinx/wp），不写 /boot（FAT）—— 路线图里定下的：
    #   FAT 那边出过 "Volume was not properly unmounted. Some data may be corrupt"，
    #   调试产物没必要冒那个险；PC 用 scp 从 wp 取就行。
    if dump and last is not None:
        outdir = '/home/xilinx/wp' if os.path.isdir('/home/xilinx/wp') else '/boot'
        # ★ 原始字节也落盘（第二十一版）：PNG 只能看，.bin 能在板上/PC 上**离线**把
        #   字节序、块平均、掩码全重算一遍 —— 下次再遇到"图不对"，不用重跑相机。
        # ★ 差分图落盘（只有 --dump 才算）：参考那路"0 个"时，一眼看是阈值太高、
        #   圆度不够，还是影子把块撑大了 —— 省得再猜一轮。
        if bg_map is not None:
            dm = _diff_map_m(last['mbgr'], bg_map)
            cv2.imwrite(outdir + '/balldiff_map.png',
                        cv2.applyColorMap(cv2.convertScaleAbs(dm, alpha=3.0),
                                          cv2.COLORMAP_JET))
            print('[输出] %s/balldiff_map.png（缩略图差分，x3 增强）' % outdir)
        if bg_full is not None:
            df = cv2.absdiff(last['fbgr'], bg_full).max(axis=2)
            cv2.imwrite(outdir + '/balldiff_full.png', df)
            print('[输出] %s/balldiff_full.png（全分辨率差分，原始灰度）' % outdir)
        with open(outdir + '/ballmap_d.bin', 'wb') as f:
            f.write(last['mapbuf'])
        with open(outdir + '/ballref_raw.bin', 'wb') as f:
            f.write(np.asarray(last['rawbuf'], np.uint8).tobytes())
        print('[输出] %s/ballmap_d.bin（%d 字节原始地图字）+ ballref_raw.bin（%d 字节原图）'
              % (outdir, len(last['mapbuf']), last['rawbuf'].size))
        big = cv2.resize(last['mbgr'], (BW * 8, BH * 8), interpolation=cv2.INTER_NEAREST)
        for d in last['det']:                           # 缩略图找到的：绿圈
            cv2.circle(big, (int(d['x']), int(d['y'])), max(4, int(d['r'])),
                       (0, 255, 0), 2)
        for r in last['ref']:                           # 参考找到的：黄圈
            cv2.circle(big, (int(r['x']), int(r['y'])), max(4, int(r['r'])),
                       (0, 255, 255), 2)
        cv2.imwrite(outdir + '/ballmap_d.png', big)
        fbgr = last['fbgr']
        for r in last['ref']:
            cv2.circle(fbgr, (int(r['x']), int(r['y'])), int(r['r']), (0, 255, 255), 3)
            cv2.putText(fbgr, r['cls'], (int(r['x']) - 30, int(r['y']) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
        cv2.imwrite(outdir + '/ballref_d.png', fbgr)
        print('[输出] %s/ballmap_d.png（绿=缩略图检测 / 黄=参考）'
              ' 与 ballref_d.png（原图+参考）—— 取自第 %d 帧（该帧参考检测 %.0f ms，'
              '是最干净的一帧）' % (outdir, last['k'] + 1, last['refdet_s'] * 1000.0))

    # ------------------------------------------------------------ 判定
    # ★ 先判"这一轮的图能不能信"：地图自检过低 = 地图内容本身坏了（不是字节序）。
    #   这时"缩略图没找到球 / 找到一堆球"都不是检测器的结论，不能说成"判据未过"。
    if gate is not None and max(gate[0], gate[1]) < 60.0:
        print('=> 环境问题：地图自检 %.1f%%/%.1f%% 都 < 60%% —— 地图内容不可信'
              '（不是字节序问题），先查视核/健康门，别拿这次的比对结果当检测器的成绩'
              % (gate[0], gate[1]))
        return 9
    if n_ref == 0:
        return 1
    if len(matched) < n_ref or (ds.size and ds.max() > TOL_PX):
        print('=> 判据未过（要求：每个参考球都同类匹配上，且误差 ≤ %.0f px）' % TOL_PX)
        return 1
    print('=> 判据通过：缩略图找球与全分辨率一致（全部 ≤ %.0f px）√' % TOL_PX)
    return 0


if __name__ == '__main__':
    sys.exit(main())
