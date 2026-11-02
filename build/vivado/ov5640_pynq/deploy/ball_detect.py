# -*- coding: utf-8 -*-
"""ball_detect.py — M2a 板端球检测：从摄像头画面里找出红/蓝/黑球的位置

游戏里摄像头垂直俯拍桌面，桌面上摆红球(正电)/蓝球(负电)/黑球(不带电)。
本脚本把"看见球"做成可调、可看、可验证的三件事：

    抓帧 -> 找球 -> 分类 -> 画标注图 / 实时推流（带标注，边摆边看）

两种找球方式：
  A. 背景差分（推荐，需先采一次空桌背景）
     空桌先拍一张存 /boot/ball_bg.png；之后 "当前帧 - 背景" 的差异区域就是
     "新冒出来的东西"（球、还有手/胳膊/线）。再用 圆度 + 半径范围 把手筛掉，
     用颜色分出红/蓝/黑。
     优点：桌面什么颜色、有没有花纹都不怕；球在暗处只要与背景有差就能看见。
  B. 霍夫圆（免标定兜底）
     灰度图找圆 + 圆内 HSV 分类。桌面上圆形杂物多时会误报。

用法（板上，需先跑过 dvp_probe.py 让传感器健康，再 noprog 接力）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py               # 抓 10 帧
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py 20            # 抓 20 帧
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py --bgcap       # 采空桌背景
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py --live 9998   # 实时推流(带标注)
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py --nobg        # 强制用霍夫圆
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py --file a.png  # 离线跑一张图
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/ball_detect.py --selftest    # 合成场景自检

可调参数（球径是唯一必须先知道的东西，装上板子后按实测调）：
    --minr 10  --maxr 80      球在画面里的半径范围（px）
    --mindist 35              两个球心最近距离（px）
    --param2 30               霍夫圆灵敏度：小=找得多（易误报），大=找得少
    --diffthr 32              背景差分阈值（三通道取最大后的差）：小=灵敏

★ 对桌面的硬要求：**桌面必须是浅色的**。黑球（中性球）在暗桌面上与背景的差
  只有几十（黑 0 对暗底 30），任何相机检测器都看不见 —— 这是物理限制，
  不是算法问题。浅色桌面/桌垫是玩法前提。

输出：
    /boot/ball_detect.png    标注图（绿圈=球，标类别/置信/坐标/半径）
    /boot/ball_detect.txt    每帧球列表 + 计时（PC 侧 scp 回去分析）
电脑侧配合：python live_view.py 192.168.2.99 9998   看带标注的实时画面
"""

import os
import sys
import time

# ★ 不要把 '/boot' 无脑顶到 sys.path[0]（2026-09-29 板上实锤）：同一套脚本在
#   /home/xilinx/wp(ext4，**真身**) 和 /boot(FAT，**可能是老镜像**) 各有一份。
#   这里原来写的是 `sys.path.insert(0, '/boot')` —— 于是**谁 import 我，谁的
#   sys.path 最前面就变成 /boot**。台架里紧跟着的那句 `import ball_map_detect`
#   就是这么落到旧镜像上的（板上实测报
#   `module 'ball_map_detect' has no attribute display_swap_check`，
#   而 wp 那份明明有）。现在：先认自己的目录（谁跑我，就优先用谁那份），
#   /boot 只作**兜底**（同目录缺文件时才轮到它）。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if '/boot' not in sys.path:
    sys.path.append('/boot')
_pv = os.environ.get('PY_PKGS_VISION')        # PC 侧离线跑时用（板上无此路径，忽略）
if _pv and os.path.isdir(_pv):
    sys.path.insert(0, _pv)
os.environ.setdefault('PYNQ_JUPYTER_NOTEBOOKS', '/home/xilinx/jupyter_notebooks')

import cv2
import numpy as np

W, H = 1024, 720                               # 帧高 720 = 实测值（曾误用 722 → 撕裂+丢帧）
BG_PATH = '/boot/ball_bg.png'
OUT_PNG = '/boot/ball_detect.png'
OUT_TXT = '/boot/ball_detect.txt'

# 类别 -> 画图用的显示色（黑球用白字，否则在暗场景里看不见）
CLS_COLOR = {'red': (0, 0, 255), 'blue': (255, 0, 0), 'black': (255, 255, 255)}
# 类别 -> 真彩色（自检合成场景时画球用；黑球就该画成黑的）
CLS_DRAW = {'red': (0, 0, 255), 'blue': (255, 0, 0), 'black': (0, 0, 0)}
CLS_CN = {'red': '红(+)', 'blue': '蓝(-)', 'black': '黑(0)', 'other': '其它'}


# ---------------------------------------------------------------- 分类
def _ring_median_V(hsv, cx, cy, r):
    """圆外一圈背景的 V 中位数（用来把"黑球"和"本来就暗的桌面"分开）"""
    h, w = hsv.shape[:2]
    x0, x1 = int(max(0, cx - r * 1.5)), int(min(w, cx + r * 1.5 + 1))
    y0, y1 = int(max(0, cy - r * 1.5)), int(min(h, cy + r * 1.5 + 1))
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    p = hsv[y0:y1, x0:x1, 2]
    yy, xx = np.ogrid[y0:y1, x0:x1]
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    m = (d2 > (r * 1.1) ** 2) & (d2 <= (r * 1.5) ** 2)
    if m.sum() < 20:
        return None
    return float(np.median(p[m]))


def classify(bgr, cx, cy, r, shrink=0.72):
    """看圆内是什么颜色。返回 (类别, 置信度=该类别像素占比)

    红/蓝：HSV 色相 + 饱和度（用占比而不是中位数，抗反光和边缘过渡）
    黑  ：亮度既要"绝对低"，也要"明显低于周围背景"（否则暗桌面会被当成黑球）
    """
    h, w = bgr.shape[:2]
    x0, x1 = int(max(0, cx - r)), int(min(w, cx + r + 1))
    y0, y1 = int(max(0, cy - r)), int(min(h, cy + r + 1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return 'other', 0.0
    patch = bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    yy, xx = np.ogrid[y0:y1, x0:x1]
    m = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (r * shrink) ** 2   # 只取内切圆，避开方形四角
    n = float(m.sum())
    if n < 20:
        return 'other', 0.0

    Hc = hsv[..., 0][m].astype(np.int16)
    Sc = hsv[..., 1][m].astype(np.int16)
    Vc = hsv[..., 2][m].astype(np.int16)

    f_red = float((((Hc <= 12) | (Hc >= 165)) & (Sc >= 90) & (Vc >= 60)).sum()) / n
    f_blue = float(((Hc >= 95) & (Hc <= 135) & (Sc >= 90) & (Vc >= 60)).sum()) / n

    ring = _ring_median_V(hsv, cx, cy, r)          # 用全图 hsv 取环（坐标是全局的）
    # 黑=绝对暗 且 明显比周围暗：暗桌面上摆黑球，或亮桌面上摆黑球，都能判对
    v_thr = 70.0 if ring is None else max(70.0, 0.55 * ring)
    f_black = float(((Vc < v_thr) & (Sc <= 110)).sum()) / n

    best, best_f = 'other', 0.0
    for cls, f in (('red', f_red), ('blue', f_blue), ('black', f_black)):
        if f > best_f:
            best, best_f = cls, f
    if best == 'black':
        # 黑球判据要比红蓝严三条：
        #   ① 占比够高 ② 圆内亮度**均匀**（暗色杂物/线材忽明忽暗）
        #   ③ 与周围有**对比**：黑球是"暗圆盘 + 不暗的周围"；一整片暗区里的
        #      圆（黑包、暗桌子的一段）自身和周围一样暗，不是球
        v_in = float(np.median(Vc))
        contrast_ok = (ring is None) or (ring - v_in >= 35.0)
        if best_f < 0.50 or float(Vc.std()) > 28 or not contrast_ok:
            return 'other', best_f
    elif best_f < 0.35:
        return 'other', best_f
    return best, best_f


# ---------------------------------------------------------------- 找球
def _accept(cls, f, cx, cy, r, min_r, max_r):
    return cls != 'other' and min_r <= r <= max_r


def detect_hough(bgr, min_r=10, max_r=80, min_dist=35, param2=30, param1=120):
    """B 方案：霍夫圆（免标定兜底）"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1.2, minDist=min_dist,
                               param1=param1, param2=param2,
                               minRadius=int(min_r), maxRadius=int(max_r))
    out = []
    if circles is None:
        return out
    for cx, cy, r in circles[0]:
        cls, f = classify(bgr, float(cx), float(cy), float(r))
        if _accept(cls, f, cx, cy, r, min_r, max_r):
            out.append(dict(cls=cls, x=float(cx), y=float(cy), r=float(r), conf=f))
    return out


def _diff_map(bgr, bg_bgr):
    """当前帧与背景的差异图：**三通道各自作差取最大**，不转灰度。

    灰度差对彩色球很钝：红球(0,0,255) 在灰桌面(118,118,118)上灰度只差 40
    （勉强过阈值 → 掩膜被腐蚀 → 圆度/半径判定全崩），但 R 通道差 137。
    """
    d = cv2.absdiff(bgr, bg_bgr).max(axis=2)
    return cv2.GaussianBlur(d, (5, 5), 0)


def detect_bg(bgr, bg_bgr, diff_thr=32, min_r=10, max_r=80, circ_min=0.82):
    """A 方案：背景差分（需空桌背景图）

    判据是"圆度"而不是"填充率"：掩膜被阈值腐蚀后，一圈像素会掉掉，
    minEnclosingCircle 仍然给对半径，但 面积/(pi r^2) 会假性偏低（实测红球 0.56）。
    圆度 = 4*pi*A/P^2 与半径无关：正圆=1.0，100x60 的方块≈0.74，线材更低。
    """
    d = _diff_map(bgr, bg_bgr)
    mask = (d > diff_thr).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < 80:
            continue
        (cx, cy), r = cv2.minEnclosingCircle(c)
        if not (min_r <= r <= max_r):
            continue                               # 太大 = 手/胳膊；太小 = 噪点
        p = cv2.arcLength(c, True)
        circ = 4.0 * np.pi * a / (p * p) if p > 0 else 0.0
        if circ < circ_min:
            continue                               # 不够圆 = 线材/手/方块，不是球
        cls, f = classify(bgr, float(cx), float(cy), float(r))
        if cls != 'other':
            out.append(dict(cls=cls, x=float(cx), y=float(cy), r=float(r), conf=f))
    return out


def detect(bgr, opts, bg_bgr=None):
    if opts['use_bg'] and bg_bgr is not None:
        return detect_bg(bgr, bg_bgr, opts['diff_thr'], opts['min_r'], opts['max_r'])
    return detect_hough(bgr, opts['min_r'], opts['max_r'],
                        opts['min_dist'], opts['param2'])


# ---------------------------------------------------------------- 画图
def annotate(bgr, balls, header):
    img = bgr.copy()
    for b in balls:
        col = CLS_COLOR.get(b['cls'], (0, 255, 0))
        cv2.circle(img, (int(b['x']), int(b['y'])), int(b['r']), (0, 255, 0), 2)
        cv2.drawMarker(img, (int(b['x']), int(b['y'])), (0, 255, 0),
                       cv2.MARKER_CROSS, 12, 1)
        cv2.putText(img, f"{b['cls'][0].upper()} {b['conf']:.2f} "
                         f"({b['x']:.0f},{b['y']:.0f})r{b['r']:.0f}",
                    (int(b['x'] - b['r']), int(b['y'] - b['r'] - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
    # 表头画在**底部**：给电脑端 live_view 的清晰度文字留出左上角
    h = img.shape[0]
    cv2.putText(img, header, (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (0, 255, 0), 2)
    return img


# ---------------------------------------------------------------- 自检
def selftest(base_path=None):
    """合成场景自检：在真实桌面照片上画球，看能不能一个不多一个不少地认出来"""
    base = None
    for p in [base_path, '/boot/m1a_after_focus.png',
              os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', '..', 'captures', 'm1a_after_focus.png')]:
        if p and os.path.exists(p):
            base = cv2.imread(p)
            if base is not None:
                print(f'[自检] 底图用真实照片：{p}')
                break
    if base is None:
        print('[自检] 没找到真实照片，用程序生成的桌面底图')
        base = np.zeros((H, W, 3), np.uint8)
        base[:, :] = (60, 90, 130)                 # 木桌色（BGR）
        noise = np.random.randint(0, 26, (H, W, 1), np.uint8)
        base = cv2.add(base, np.repeat(noise, 3, axis=2))
    if base.shape[0] != H or base.shape[1] != W:
        base = cv2.resize(base, (W, H))

    # ★ 黑球摆在"底图最亮"的候选点上：黑球在暗底上**任何**相机检测器都看不见
    #   （黑球(0,0,0) 与暗底(30,30,30) 的差只有 30）；这是玩法对桌面的硬要求
    #   —— 桌面必须浅色，否则黑球（不带电的中性球）没法识别。
    cands = [(150, 620), (300, 680), (700, 680), (880, 650), (500, 700)]
    g_base = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    yy, xx = np.ogrid[:H, :W]
    bx, bv = cands[0], -1.0
    for (x, y) in cands:
        m = ((xx - x) ** 2 + (yy - y) ** 2) <= 34 ** 2
        v = float(g_base[m].mean())
        if v > bv:
            bx, bv = (x, y), v
    print(f'[自检] 黑球放在桌面最亮的候选点 {bx}（局部亮度 {bv:.0f}）'
          f'——暗底上的黑球谁都测不到，这是玩法对桌面的硬要求')

    truth = [
        dict(cls='red', x=210, y=170, r=30),
        dict(cls='blue', x=520, y=250, r=26),
        dict(cls='black', x=bx[0], y=bx[1], r=34),
    ]
    scene = base.copy()
    for t in truth:
        # 注意：画球用 CLS_DRAW（真彩色），不能用 CLS_COLOR（那是给标签文字用的，
        # 黑球的标签色是白色 —— 早先这一处把黑球画成了白球，自检假失败）
        cv2.circle(scene, (t['x'], t['y']), t['r'], CLS_DRAW[t['cls']], -1)
    # 干扰项：灰色圆（像鼠标/浅色杂物）不该被认成球
    cv2.circle(scene, (330, 590), 40, (200, 200, 200), -1)
    cv2.rectangle(scene, (600, 560), (700, 620), (40, 40, 40), -1)   # 暗色方块=干扰
    # 绿色圆（游戏里没有这种球）也不该被认成球
    cv2.circle(scene, (860, 160), 28, (60, 200, 60), -1)

    opts = dict(use_bg=False, min_r=14, max_r=60, min_dist=35, param2=30, diff_thr=28)
    t0 = time.time()
    got = detect(scene, opts)
    dt = (time.time() - t0) * 1000

    print(f'[自检] 霍夫圆模式找到 {len(got)} 个球（{dt:.0f} ms）')
    ok = True
    for t in truth:
        hit = None
        for g in got:
            if abs(g['x'] - t['x']) < 8 and abs(g['y'] - t['y']) < 8:
                hit = g
                break
        if hit is None:
            print(f'  [FAIL] {CLS_CN[t["cls"]]} @({t["x"]},{t["y"]}) 没找到')
            ok = False
        elif hit['cls'] != t['cls']:
            print(f'  [FAIL] @({t["x"]},{t["y"]}) 认成 {CLS_CN[hit["cls"]]}，'
                  f'应为 {CLS_CN[t["cls"]]}')
            ok = False
        else:
            print(f'  [ ok ] {CLS_CN[t["cls"]]} @({t["x"]},{t["y"]}) '
                  f'-> ({hit["x"]:.0f},{hit["y"]:.0f}) r{hit["r"]:.0f} '
                  f'置信 {hit["conf"]:.2f}')
    # 误报检查（灰圆/暗方块/绿圆都不是球）
    for g in got:
        if not any(abs(g['x'] - t['x']) < 8 and abs(g['y'] - t['y']) < 8 for t in truth):
            print(f'  [WARN] 误报：({g["x"]:.0f},{g["y"]:.0f}) r{g["r"]:.0f} '
                  f'判为 {CLS_CN[g["cls"]]} 置信 {g["conf"]:.2f}')
            if g['cls'] == 'black':
                print('         （这一条是霍夫模式的固有局限：单帧里"均匀暗圆 + 周围较亮"'
                      '和真黑球在局部判据上完全等价。'
                      '要根除就得用背景差分，或上多帧跟踪——见 M2b）')

    # 背景差分模式
    print('[自检] 换背景差分模式（背景=没摆球的底图）...')
    opts['use_bg'] = True
    t0b = time.time()
    got2 = detect(scene, opts, bg_bgr=base)
    dt2 = (time.time() - t0b) * 1000
    hit2 = sum(1 for t in truth
               if any(abs(g['x'] - t['x']) < 8 and abs(g['y'] - t['y']) < 8
                      and g['cls'] == t['cls'] for g in got2))
    print(f'[自检] 背景差分模式找到 {len(got2)} 个，命中 {hit2}/3（{dt2:.0f} ms）')
    if hit2 < 3:
        ok = False
        for g in got2:
            print(f'    -> {CLS_CN[g["cls"]]} ({g["x"]:.0f},{g["y"]:.0f}) r{g["r"]:.0f}')

    print('[自检] ==== ' + ('全部通过' if ok else '有失败项，见上') + ' ====')
    return 0 if ok else 1


# ---------------------------------------------------------------- 采集
def _make_cap(wait_boot_window=True):
    """建 capture（= AXI 流的消费者）。

    ★ 顺序不能改：**先问"现在能不能碰 PL"，再建 capture**。
      `S2mmCapture.__init__/start()` 本身就在写 PL 寄存器（DMACR/DMASR 等），
      而开机 ~120s 时镜像自带的机制会写一次 base.bin（把 PL 换掉）。那段时间里
      碰 PL 的代价实测是：半编程态 -> MMIO 读挂死总线 -> 只能断电；或者 SIGBUS
      直接把脚本打死（rc=135，日志只有一行）。这一条以前散在文档里，现在变成
      代码里的前置检查。
    """
    try:
        import link_boot
        if not link_boot.require_board_ready(wait=wait_boot_window):
            raise RuntimeError('还在开机 base.bin 写 PL 的窗口里，拒绝碰 PL'
                               '（等出窗口再来，或先跑 ./pl_probe 下 bit）')
    except RuntimeError:
        raise
    except Exception as e:
        print('[视频] （提示）开机窗口检查没做成（%r）—— 继续，但风险自负'
              % (e,), flush=True)
    from vdma_capture import S2mmCapture
    time.sleep(1.0)                                # 等传感器跑完 SCCB 配置
    cap = S2mmCapture(W, H)
    cap.start()
    return cap


def _frame_sane(bgr, min_std=1.0, min_mean=1.0):
    """退化帧守卫：全 0 / 常值 / 几乎全黑的缓冲**不算**"抓到帧"。

    为什么必须有（2026-09-29）：热身帧打印过
        `[视频] 热身帧 OK（buf2 均值 0.0 标准差 0.0）`
    —— 一整块全 0 的缓冲被当成了好帧。后果是连锁的：字节序自动判定会固化在错的
    一侧（这块帧对哪个方向都"匹配"），后面的测量全建立在一块假帧上。
    判据故意保守（std<1.0 或 mean<1.0 才算退化）：真实场景（桌面/白纸/木纹）的
    std 是几十，这个门只挡死缓冲，不会误杀低对比度场景。
    ★ 只用 .std()/.mean()（不碰 numpy 的其它东西），所以离线台架能用假数组测。
    """
    try:
        std = float(bgr.std())
        mean = float(bgr.mean())
    except Exception as e:
        return False, '读不出均值/标准差（%s）' % type(e).__name__
    if std != std or mean != mean:                  # NaN
        return False, '均值/标准差是 NaN'
    if std < min_std:
        return False, '几乎是常值（均值 %.2f 标准差 %.2f）' % (mean, std)
    if mean < min_mean:
        return False, '几乎全黑（均值 %.2f）' % mean
    return True, ''


def scene_gate(mean, dark_min=30.0, low_min=50.0):
    """现场门禁判据（**纯函数**，专门做得能上台架）：热身帧均值 -> (等级, 说明)。

    量纲是**实测的**，不是拍的：镜头对着场区 + 灯亮 + 球在场 = 91.8；
    镜头朝下对着桌面 = 13.5（2026-09-29 夜，用户一句话点破，我当时还猜成"光照/曝光"）。
    30 = 拒跑线，50 = 提醒线。
    返回 'ok' / 'low'（偏低，提醒但放行）/ 'dark'（暗，长跑工具要拒跑）。
    """
    try:
        m = float(mean)
    except Exception:
        return 'dark', '热身帧均值读不出来'
    if m != m or m < dark_min:                      # NaN 也算暗
        return 'dark', '画面很暗（热身帧均值 %.1f < %.0f）' % (m, dark_min)
    if m < low_min:
        return 'low', '热身帧均值 %.1f，比正常（~90）低不少' % m
    return 'ok', ''


def _init_video(cap, tries=3, dark_gate=False, dark_min=30.0):
    """视频域启动：一律走 link_boot 的**唯一契约**（见 link_boot.py 模块注释）。

    老版本（2026-09-29 之前）自己脉冲一次 ctrl[3]、自己等 5 秒、等不到就判"环境
    问题"；热身帧不拒退化帧；而且一上来就挂看门狗。现在三件事都改了：
      [1] link_boot.link_up(cap)：开机窗口检查 -> 子进程身份自检 + 清锁位 ->
          阶梯救援（动作间隔 >=5s / 升级后静默 >=10s / 动作封顶 / 连败放弃）->
          健康门（行长度 / 帧率 / href / frame_cnt / 静止位）-> 一句可判读的结论
      [2] 热身帧带退化帧守卫（全 0/常值不算数，重试到 tries 次），拿不到就明说
      [3] 看门狗**只在结论健康时**才挂 —— 链路已经死的时候再挂一个会自己动手的
          演员，只是往乱场面里加乱（2026-09-29 的教训）；健康时它才是有用的
      [4] 退出兜底：link_boot 的 link_down（冻视频域 -> 停 VDMA -> 清静止位）
      [5] ★ 场景门禁（dark_gate=True，长跑工具用）：热身帧均值 < dark_min 直接
          **exit 9 拒跑**（--allow-dark 才能绕过）—— 暗帧不报错、只静默产出废数据

    ★ "立刻抓一帧"这条老规矩还在，而且更要紧：若先阻塞（等电脑连接/等背景校验）
      让 VDMA 空转，之后就再也收不到帧（实测帧计数卡死）。link_up 内部的阶梯
      本来就是"边等边抓"，所以顺序天然满足。
    返回：有效热身帧；拿不到返回 None（调用方必须能处理 None）。
    """
    verdict = None
    try:
        import link_boot
        verdict = link_boot.link_up(cap)
        link_boot.arm_atexit(cap)          # 退出必净：冻 -> 停 VDMA -> 清静止位
    except Exception as e:
        print('[视频] ★ link_boot 没跑起来（%s: %s）—— 这一轮的链路结论**不可信**'
              % (type(e).__name__, str(e)[:80]), flush=True)

    warm, bad = None, []
    for k in range(max(1, tries)):
        try:
            idx, bgr = cap.bgr_frame(timeout=5.0)
        except Exception as e:
            bad.append('第 %d 次抓帧失败（%s）' % (k + 1, type(e).__name__))
            continue
        ok, why = _frame_sane(bgr)
        if ok:
            warm = bgr
            print('[视频] 热身帧 OK（buf%d 均值 %.1f 标准差 %.1f%s）'
                  % (idx, float(bgr.mean()), float(bgr.std()),
                     '，第 %d 次才拿到' % (k + 1) if k else ''), flush=True)
            print('[视频] 字节序自动判定：%s' % getattr(cap, '_swap_decided', None),
                  flush=True)
            break
        bad.append('第 %d 帧退化：%s' % (k + 1, why))
    if warm is None:
        print('[视频] ★ 热身帧 %d 次都没拿到有效帧：%s'
              % (max(1, tries), '；'.join(bad)), flush=True)
        print('[视频]   -> 这是"没有真帧"的状态：字节序判定、背景、C/D 项全部不可信；'
              '先看上面 [link] 那几行给的原因', flush=True)

    # ---- ★ 场景门禁（2026-09-29 夜）：热身帧均值就是"现场对不对"的一个数 ----
    #   实测：镜头对场区 + 灯亮 + 球在场 = 91.8；镜头朝下对着桌面 = 13.5。
    #   暗帧**不会报错**，只会静默地把整轮跑成废数据（昨晚 60s + 30s 两趟就是这么废的，
    #   而我当时还没问现场就把它归因成"光照/曝光问题"）。所以长跑工具（ball_game）传
    #   dark_gate=True：低于阈值直接 **exit 9 拒跑**，要硬跑得显式 --allow-dark。
    #   诊断工具不传（它们本来就要能在坏现场里量东西）。
    if warm is not None:
        _lvl, _msg = scene_gate(float(warm.mean()), dark_min=dark_min)
        if _lvl == 'dark':
            print('[视频] ★★ %s。先确认三件事：① 镜头对着场区（不是对着桌面/别处）'
                  ' ② 球在场区里 ③ 灯亮；另外**手别伸进画面**'
                  '（差分背景会把新出现的任何东西当球）' % _msg, flush=True)
            if dark_gate and '--allow-dark' not in sys.argv:
                print('[视频] ★★ 场景门禁：拒跑（rc=9）—— 现场摆好再来；'
                      '确实要在暗场里跑就加 --allow-dark', flush=True)
                sys.exit(9)
        elif _lvl == 'low':
            print('[视频] %s —— 顺手确认下镜头/灯' % _msg, flush=True)

    # ---- 看门狗：只在链路健康时挂（就绪判据来自 link_boot，不再自己猜）----
    if verdict is not None and verdict.get('ok'):
        try:
            from vdma_capture import start_stall_watchdog, STALL_LIMIT_S
            start_stall_watchdog()
            print('[视频] 掉链看门狗已挂上（心跳=frame_cnt，卡住 %.1fs 脉冲 ctrl[3]；'
                  '升级要"脉冲之后又卡"累计 2 次、动作间隔 >=5s、升级后静默 20s、'
                  '升级上限 3 次、到头就放弃不再动手）' % STALL_LIMIT_S, flush=True)
        except Exception as e:
            print('[视频] 掉链看门狗没挂上（%s: %s）'
                  % (type(e).__name__, str(e)[:60]), flush=True)
    else:
        print('[视频] 看门狗**不挂**：链路结论不是健康（%s）—— 死链路上挂看门狗'
              '只会多加一个乱动手的演员'
              % ((verdict or {}).get('why') or 'link_boot 没跑成'), flush=True)
    return warm


# ---------------------------------------------------------------- 主流程
def parse(argv):
    o = dict(use_bg=True, min_r=10, max_r=80, min_dist=35, param2=30, diff_thr=32,
             bgauto=False)
    frames = 10
    live_port, img_file, do_bgcap, do_selftest = None, None, False, False
    i, rest = 0, []
    while i < len(argv):
        a = argv[i]
        if a == '--live':
            i += 1
            live_port = int(argv[i]) if i < len(argv) else 9998
        elif a == '--file':
            i += 1
            img_file = argv[i]
        elif a == '--bgcap':
            do_bgcap = True
        elif a == '--nobg':
            o['use_bg'] = False
        elif a == '--bgauto':
            o['bgauto'] = True
        elif a == '--selftest':
            do_selftest = True
        elif a == '--minr':
            i += 1; o['min_r'] = float(argv[i])
        elif a == '--maxr':
            i += 1; o['max_r'] = float(argv[i])
        elif a == '--mindist':
            i += 1; o['min_dist'] = float(argv[i])
        elif a == '--param2':
            i += 1; o['param2'] = float(argv[i])
        elif a == '--diffthr':
            i += 1; o['diff_thr'] = float(argv[i])
        else:
            rest.append(a)
        i += 1
    if rest:
        try:
            frames = int(rest[0])
        except ValueError:
            pass
    return o, frames, live_port, img_file, do_bgcap, do_selftest


def main():
    o, frames, live_port, img_file, do_bgcap, do_selftest = parse(sys.argv[1:])
    if do_selftest:
        return selftest(img_file)

    if img_file:                                   # 离线跑一张图（PC 侧调参用）
        bgr = cv2.imread(img_file)
        if bgr is None:
            print(f'[错误] 读不到图：{img_file}')
            return 1
        if bgr.shape[0] != H or bgr.shape[1] != W:
            bgr = cv2.resize(bgr, (W, H))
        t0 = time.time()
        balls = detect(bgr, o)
        dt = (time.time() - t0) * 1000
        print(f'[离线] {img_file} 找到 {len(balls)} 个球（{dt:.0f} ms）')
        for b in balls:
            print(f'  {CLS_CN[b["cls"]]} ({b["x"]:.0f},{b["y"]:.0f}) '
                  f'r{b["r"]:.0f} 置信 {b["conf"]:.2f}')
        out = os.path.splitext(img_file)[0] + '_detect.png'
        cv2.imwrite(out, annotate(bgr, balls, f'{len(balls)} balls  {dt:.0f}ms'))
        print('已存:', out)
        return 0

    cap = _make_cap()
    warm = _init_video(cap)

    if do_bgcap:                                   # 采空桌背景（桌上别放球）
        print('[背景] 采 10 帧取中位数当背景（此时桌面必须干净）...', flush=True)
        stack = []
        while len(stack) < 10:
            try:
                _, f = cap.bgr_frame(timeout=2.0)
                stack.append(f)
            except Exception as e:
                print(f'  抓帧失败（{e}）', flush=True)
        bg = np.median(np.stack(stack, 0), 0).astype(np.uint8)
        cv2.imwrite(BG_PATH, bg)
        print(f'[背景] 已存 {BG_PATH}（像素 {bg.shape[1]}x{bg.shape[0]}）'
              f'——下次直接跑本脚本就会用它')
        return 0

    bg_bgr = None
    if o['use_bg']:
        bgi = cv2.imread(BG_PATH)
        if bgi is None:
            print(f'[背景] 没有 {BG_PATH}，本次用霍夫圆模式'
                  f'（想用背景差分先在空桌上跑 --bgcap）')
        elif bgi.shape[:2] != (H, W):
            print(f'[背景] 尺寸不符 {bgi.shape[:2]} != {(H, W)}，本次用霍夫圆模式')
        else:
            # 背景失效自检：整幅差异中位数过大 = 摄像头动过 / 桌面变了 / 字节序变了
            try:
                _, probe = cap.bgr_frame(timeout=2.0)
                med = float(np.median(_diff_map(probe, bgi)))
                if med > 30:
                    print(f'[背景] 差异中位数 {med:.0f} 过大 → 背景已失效'
                          f'（摄像头动过/桌面变了/字节序变了），本次退回霍夫圆；'
                          f'请重采 --bgcap')
                else:
                    bg_bgr = bgi
                    print(f'[背景] 使用 {BG_PATH}（差异中位数 {med:.0f}，正常）')
            except Exception as e:
                print(f'[背景] 校验失败（{e}），本次用霍夫圆模式')

    if bg_bgr is None and o['bgauto']:
        bg_bgr = warm
        print('[背景] --bgauto：拿热身帧当背景（只用来量耗时/试手感，'
              '正式用 --bgcap 采空桌）')

    if live_port:                                  # 实时推流（带标注，边摆边看）
        import socket
        import struct as _st
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('0.0.0.0', live_port))
        srv.listen(1)
        print(f'[推流] 等待电脑连接：{live_port} 端口（live_view.py 连它，可反复重连）')
        while True:
            conn, addr = srv.accept()
            print(f'[推流] 电脑已连接 {addr}')
            try:
                while True:
                    try:
                        _, bgr = cap.bgr_frame(timeout=2.0)
                    except Exception as e:
                        print(f'[推流] 抓帧失败（{e}）')
                        continue
                    t0 = time.time()
                    balls = detect(bgr, o, bg_bgr)
                    dt = (time.time() - t0) * 1000
                    mode = 'BG' if (o['use_bg'] and bg_bgr is not None) else 'HOUGH'
                    img = annotate(bgr, balls,
                                   f'{len(balls)} balls  {mode}  detect {dt:.0f}ms')
                    ok, jpg = cv2.imencode('.jpg', img,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                    if ok:
                        d = jpg.tobytes()
                        conn.sendall(_st.pack('<I', len(d)) + d)
            except Exception as e:
                print(f'[推流] 客户端断开（{e}），回到等待状态')
            finally:
                conn.close()

    # 单次检测：抓 frames 帧，逐帧检测，存最后一帧的标注图 + 全部结果
    cap_ms, det_ms, lines, last = [], [], [], None
    for k in range(frames):
        t0 = time.time()
        try:
            _, bgr = cap.bgr_frame(timeout=2.0)
        except Exception as e:
            print(f'[采集] 第 {k+1} 帧失败（{e}）')
            continue
        t1 = time.time()
        balls = detect(bgr, o, bg_bgr)
        t2 = time.time()
        cap_ms.append((t1 - t0) * 1000)
        det_ms.append((t2 - t1) * 1000)
        last = (bgr, balls, (t2 - t1) * 1000)
        cnt = {}
        for b in balls:
            cnt[b['cls']] = cnt.get(b['cls'], 0) + 1
        lines.append(f'frame {k:3d}  cap {(t1-t0)*1000:6.0f}ms  det {(t2-t1)*1000:6.0f}ms  '
                     f'红{cnt.get("red",0)} 蓝{cnt.get("blue",0)} '
                     f'黑{cnt.get("black",0)}  ' +
                     ' | '.join(f'{b["cls"]}({b["x"]:.0f},{b["y"]:.0f})'
                                f'r{b["r"]:.0f}/{b["conf"]:.2f}' for b in balls))

    if last is not None:
        bgr, balls, dt = last
        mode = 'BG' if (o['use_bg'] and bg_bgr is not None) else 'HOUGH'
        hdr = (f'{len(balls)} balls  mode={mode}  detect {dt:.0f}ms  '
               f'cap {np.mean(cap_ms):.0f}ms')
        cv2.imwrite(OUT_PNG, annotate(bgr, balls, hdr))
        print(f'[结果] 最后一帧找到 {len(balls)} 个球：')
        for b in balls:
            print(f'  {CLS_CN[b["cls"]]}  ({b["x"]:.0f}, {b["y"]:.0f})  '
                  f'r={b["r"]:.0f}  置信 {b["conf"]:.2f}')
        print(f'[计时] 抓帧 {np.mean(cap_ms):.0f}ms/帧   检测 {np.mean(det_ms):.0f}ms/帧'
              f'   -> 端到端约 {1000.0/(np.mean(cap_ms)+np.mean(det_ms)):.1f} fps')
        print(f'[输出] {OUT_PNG}')
        with open(OUT_TXT, 'w') as f:
            f.write(f'# mode={mode}  minr={o["min_r"]} maxr={o["max_r"]} '
                    f'param2={o["param2"]} diffthr={o["diff_thr"]}\n')
            f.write('\n'.join(lines) + '\n')
        print(f'[输出] {OUT_TXT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
