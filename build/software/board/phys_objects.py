# -*- coding: utf-8 -*-
"""phys_objects.py —— **实物识别**（2026-10-19 用户定案：不用键鼠画，识别桌上的实物）

    · 黑色长条实体   -> 反射障碍（长度**不固定**：中心/角度/长度/厚度全部从实物量）
    · 绿色扁椭圆     -> 传送门（两个成对；位置从实物量）
    · 黑色小球       -> 自定义洞口标记（投影到最近的左右边）

输入：128x90 的缩略图（ball_map_detect 的 mbgr，BGR）。输出：全部是 **map 像素坐标**
（整块心的连续值；调用方用 `*BLK+BLK/2` 换到相机像素，再走标定进游戏系）。

形状判据（二阶矩，纯 numpy/cv2，~1-2ms/帧）：
    · 协方差特征值 λ1≥λ2：长条按"均匀矩形" 长=√(12λ1)、厚=√(12λ2)；
      角度 = 0.5·atan2(2μ11, μ20−μ02)（度，图像系 x 向右/y 向下）
    · 黑长条 = 黑掩码团块 且 长宽比≥2.2 且 长≥5 块（≈40 相机像素）
    · 黑小球 = 黑掩码团块 且 长宽比<1.8 且 面积 8..350 块
    · 绿椭圆 = 绿掩码团块 且 长宽比≤3.5 且 面积 8..800 块
      黑掩码与 detect_map 同款：V ≤ max(70, 0.55·medianV)−1 且 S ≤ 110（真黑才进得来；
      影子/灰边 V 更高，被挡在外面——2026-09-27 的同一套口径）。
      绿窗：H 35..85（OpenCV 0..179）、S≥60、V≥40。
★ 阈值是**首版经验值**，上板按实物标定（识别输出带原始量，便于现场调）。
"""
import cv2
import numpy as np

BLK = 8                       # 一个 map 块 = 8×8 相机像素

# 判据常数（标定入口——改这里就等于改全局口径）
BAR_ASPECT = 2.2              # 长条长宽比下限
BAR_MIN_LEN_BLK = 5.0         # 长条最短（map 块）
BAR_MAX_LEN_BLK = 30.0        # ★ 2026-10-19 加门禁；★ 2026-10-23 收紧 60→30：
                              #   真黑条实测 ~197~206 游戏 px（≈25 块），
                              #   用户晃手时手臂/袖子被认成 258~392 的长条——拒掉
BALL_ASPECT_MAX = 1.6         # 黑球长宽比上限（更圆）
BALL_AREA = (14, 120)         # 黑球面积（map 块；★2026-10-23 上限 200→120：
                              #   手影/暗区的大团不再当洞口标记；8~13 的暗斑是
                              #   红球暗边/阴影，曾把左右洞都拽走）
GREEN_ASPECT_MAX = 3.5        # 绿椭圆长宽比上限（扁椭圆）
GREEN_AREA = (10, 800)        # 绿椭圆面积（map 块；成对门槛在消费侧做：
                              #   面积>=12 且间距>=80px 取两个最大的）
GREEN_HSV = (35, 120, 30, 80, 255, 255)  # (Hlo,Slo,Vlo,Hhi,Shi,Vhi)
                              # ★★ 2026-10-19 上板实锤：地图里的**白纸本身偏绿**
                              #   （H≈68~91，S≈48~100）——S 下限只要低于 100，纸
                              #   就和绿球连成一个几十万像素的大团，被面积上限
                              #   丢弃 → 绿门永远 0。绿球实测 S≈216~227、H≈44；
                              #   蓝球 H≈93（上界 80 再挡一道）。


def _mask_black(hsv):
    """黑掩码（自适应 vhi + S≤110）——**扣掉绿色像素**。

    ★★ 2026-10-29 用户实况（"把传送门认成障碍"）：深绿的实体门在偏暗/低饱和时
    会落进黑掩码（S≤110、V低），被当成黑条。这里把宽绿窗（H 25..100、S≥45、
    V≥15）从黑掩码里减掉——绿门/绿纸不会变成障碍；真黑条（S 很低）不受影响。
    """
    v = hsv[..., 2].astype(np.int16)
    med = float(np.median(v))
    v_thr = max(70.0, 0.55 * med)
    vhi = int(max(0, min(255, int(np.ceil(v_thr)) - 1)))
    m = cv2.inRange(hsv, (0, 0, 0), (179, 110, vhi))
    # ★ 2026-10-29 用户点单（"会不会和红色搞混"）：**显式扣红**——暗红球/
    #   红胶带 S 通常 >=70，而真黑条 S 很低；先把红色从黑掩码里减掉。
    _red = cv2.bitwise_or(
        cv2.inRange(hsv, (0, 70, 30), (12, 255, 255)),
        cv2.inRange(hsv, (168, 70, 30), (179, 255, 255)))
    m = cv2.bitwise_and(m, cv2.bitwise_not(_red))
    # ★ 2026-10-29 二修（宽绿窗把黑条边缘也吃了 -> 条 0）：只扣**传送门的
    #   高饱和绿**（GREEN_HSV，S>=120）——绿纸（S 48~100）/黑条边缘不受影响。
    g = cv2.inRange(hsv, (GREEN_HSV[0], GREEN_HSV[1], GREEN_HSV[2]),
                    (GREEN_HSV[3], GREEN_HSV[4], GREEN_HSV[5]))
    return cv2.bitwise_and(m, cv2.bitwise_not(g))


def _components(mask, min_area, max_area, ksize=3):
    """闭运算 + 8 连通；返回 (m8, lab, [(i, area), ...])。

    ★ 2026-10-19 上板实锤：**开运算会把黑长条腐蚀掉**——缩略图 8px/块，
      一条 2cm 宽的黑条在 map 上只有 ~2.5 块厚，3×3 OPEN 一腐蚀就没了
      （现场日志一直是"条 0"）。缩略图本身已经是降噪过的，改成**只闭不开**，
      孤立噪点交给面积下限挡。
    """
    _k = int(max(1, ksize))
    m8 = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((_k, _k), np.uint8))
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(m8, 8)
    kept = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if min_area <= a <= max_area:
            kept.append((i, a))
    return m8, lab, stats, kept


def _shape(lab, i, stats=None):
    """二阶矩形状量：返回 (cx, cy, length, thick, ang_deg)。

    ★★ 2026-10-28：**只算连通块的包围盒**。原来 `np.nonzero(lab == i)` 每次扫
    整幅图——缩略图 128x90 无所谓，但摆放用的 1024x720 原图上，几个块就是
    几百万次比较/块，一次识别里要扫十几遍（板上就是"摆放卡一下"的主因）。"""
    if stats is not None:
        _x0 = int(stats[i, cv2.CC_STAT_LEFT])
        _y0 = int(stats[i, cv2.CC_STAT_TOP])
        _w = int(stats[i, cv2.CC_STAT_WIDTH])
        _h = int(stats[i, cv2.CC_STAT_HEIGHT])
        ys, xs = np.nonzero(lab[_y0:_y0 + _h, _x0:_x0 + _w] == i)
        xs = xs + _x0
        ys = ys + _y0
    else:
        ys, xs = np.nonzero(lab == i)
    # ★★ 2026-10-29 用户点单（"网上没有好的黑长条算法吗"）：改用
    #   **cv2.minAreaRect 最小外接旋转矩形**——对连通域直接拟合紧贴的旋转框，
    #   是识别长条的标准做法；比二阶矩/投影都稳（不会因轴角不稳把长度
    #   算短，也不会把长宽比小的条搞得时大时小）。
    pts = np.column_stack((xs, ys)).astype(np.float32)
    if len(pts) > 8000:                    # 稠密点云抽样，结果不变、更快
        pts = pts[::max(1, len(pts) // 8000)]
    (rcx, rcy), (_w0, _h0), _a0 = cv2.minAreaRect(pts)
    _box = cv2.boxPoints(((rcx, rcy), (_w0, _h0), _a0))
    _e01 = float(np.hypot(_box[1][0] - _box[0][0], _box[1][1] - _box[0][1]))
    _e12 = float(np.hypot(_box[2][0] - _box[1][0], _box[2][1] - _box[1][1]))
    if _e01 >= _e12:
        length, thick = _e01, _e12
        ang = float(np.degrees(np.arctan2(_box[1][1] - _box[0][1],
                                          _box[1][0] - _box[0][0])))
    else:
        length, thick = _e12, _e01
        ang = float(np.degrees(np.arctan2(_box[2][1] - _box[1][1],
                                          _box[2][0] - _box[1][0])))
    while ang > 90.0:                      # 统一到 (-90, 90]，与旧口径一致
        ang -= 180.0
    while ang <= -90.0:
        ang += 180.0
    cx = float(_box[:, 0].mean())
    cy = float(_box[:, 1].mean())
    return cx, cy, max(1e-9, length), max(1e-9, thick), ang


def detect(bgr_map, scale=1.0, loose=False):
    """一帧识别。返回 dict（坐标 = 传入图像的像素坐标）。

    ★★ 2026-10-28：加了 `scale` —— 判据常数是按 **map（128x90，1 块=8 相机像素）**
    标定的。摆放/调整界面改用 **1024x720 原图**识别时传 scale=BLK(=8)：
    长度/厚度 ×8、面积 ×64，输出坐标仍是该图像素（调用方按原图口径映射）。
    缩略图路径照旧 scale=1.0，一个字都没变。
    """
    sc = float(scale)
    sc2 = sc * sc
    # ★★ 2026-10-28 用户实况（"小绿门/短条很难认"）：摆放态用**放宽**阈值
    #   （只影响摆放识别，正常对局口径不变）。缩略图上小门只有 10~13 块，
    #   卡在 GREEN_AREA[0]=10 边缘；短条 5 块也是下限。
    _ga = GREEN_AREA[0] * (0.55 if loose else 1.0)
    _bl = BAR_MIN_LEN_BLK * (0.75 if loose else 1.0)
    # ★★ 2026-10-29 用户实况（"换个位置就识别不到/下面难认"）：透视让条在
    #   近处/下方变大，超过 BAR_MAX_LEN_BLK=30 块会被整块丢弃；反光又把它
    #   切成两段。**摆放模式**放宽到 1.6 倍长度上限；**正式对局也改用 5×5
    #   闭运算**桥接反光缝（2026-10-30 上电实锤：PLAY 仍 3×3，208 长条被切成
    #   82 碎段、基线锁成 2 根）。长度上限正式对局仍保持 30 块严格门禁。
    _bmax = BAR_MAX_LEN_BLK * (1.6 if loose else 1.0)
    _ks = 5
    hsv = cv2.cvtColor(bgr_map, cv2.COLOR_BGR2HSV)
    out = {'bars': [], 'balls': [], 'greens': []}

    _m8, lab, _st, comps = _components(_mask_black(hsv),
                                       int(8 * sc2), int(4000 * sc2),
                                       ksize=_ks)
    for i, a in comps:
        cx, cy, ln, th, ang = _shape(lab, i, _st)
        asp = ln / max(1e-9, th)
        # ★ 2026-10-29 用户规格（黑条 4mm 宽）：缩略图上只有 ~1 块厚，原
        #   th>=1.2 会直接拒掉；降到 0.8 块（长条仍要求长度/长宽比）。
        if (asp >= BAR_ASPECT
                and _bl * sc <= ln <= _bmax * sc
                and th >= 0.8 * sc):
            out['bars'].append(dict(cx=cx, cy=cy, length=ln, thick=th,
                                    ang=ang, area=a))
        elif (asp < BALL_ASPECT_MAX
                and BALL_AREA[0] * sc2 <= a <= BALL_AREA[1] * sc2
                and th >= 1.2 * sc):
            out['balls'].append(dict(cx=cx, cy=cy,
                                     r=float(np.sqrt(a / np.pi)), area=a))

    hlo, slo, vlo, hhi, shi, vhi = GREEN_HSV
    _mg, labg, _stg, compsg = _components(
        cv2.inRange(hsv, (hlo, slo, vlo), (hhi, shi, vhi)),
        int(_ga * sc2), int(GREEN_AREA[1] * sc2), ksize=_ks)
    for i, a in compsg:
        cx, cy, ln, th, ang = _shape(labg, i, _stg)
        asp = ln / max(1e-9, th)
        if asp <= GREEN_ASPECT_MAX and th >= 1.2 * sc:
            out['greens'].append(dict(cx=cx, cy=cy,
                                      r=float(np.sqrt(a / np.pi)),
                                      asp=asp, ang=ang, area=a))
    return out
