# -*- coding: utf-8 -*-
"""v3_input.py —— ★ P2：演示输入层（手机网页 + 有线键盘，一个文件全包）

为什么要这一层：入射角/速度原来是**命令行参数**（跑起来就钉死了）。演示要的效果是
"球放在红色场区内任意位置（2026-10-28 起不必贴边），按 S/W 微调入射角，
屏幕上那一簇扇形跟着变"。手机和键盘是**两个源**，
但它们该改的是**同一个状态** —— 所以中间垫这一层：源只管写，游戏循环只管读。

三种角色（同一个文件、同一份状态文件）：

    python3 v3_input.py serve          # ① 手机网页遥控（板子上跑，手机连同一 WiFi）
    python3 v3_input.py key            # ② 有线键盘（读 /dev/input/eventX，要 root）
    python3 v3_input.py status         # 看当前状态（谁写的、什么时候写的）
    python3 v3_input.py set --angle 10 # 手动写一次（命令行兜底 / 脚本化用）
    python3 v3_input.py selftest       # 离线自检（不碰硬件）

游戏循环侧（ball_game.py --v3-input）：

    from v3_input import InputHub
    hub = InputHub(angle=0.0, speed=400.0)      # 初值 = 命令行给的
    ang, spd, changed = hub.poll()              # 每帧调用，~µs
    # ★ 2026-10-01 深夜（用户三条之③）：`hub.fire` = 状态文件里的发射计数（每笔+1）。
    #   游戏读**上升沿**（fire > 上次）发一杆；计数变低=文件被重建过 -> 只对基准，不发射。
    #   发射结果由**游戏**写到 /dev/shm/v3_shot.json（原子写），手机网页轮询本服务的
    #   `/shot` 端点拿它显示（"进洞/打回/拒绝原因"）——玩家手里有反馈，不用盯大屏。

★ 键位规则（沿用项目已定的口径 + 《磁场粒子对战规则速查表 v4》的"空格=发射"）：
    **W / ↑ = +2°**   **S / ↓ = -2°**   （角度；0°=右水平、逆时针为正、360°一圈）
    **D / → = +20 px/s**   **A / ← = -20 px/s**   （速度；游戏像素/秒）
    **空格 = 发射**（写一笔 fire 计数；手机上 = 页面里那个"发射"按钮）
    R = 复位（0° / 400 px/s）     Q / ESC = 退出（只对 key 模式）

硬约束（都是这个项目踩过坑记下来的）：
  * **只用标准库** —— 板上 pynq venv 里没装别的东西，装了也未必在这个 python 里。
  * **读的代价必须可忽略**：游戏循环每帧都读。做法是**把上次读到的原始字节留着比**
    （一模一样就什么都不做，不一样才 json.loads）。板上**实测**（2026-09-30，
    Cortex-A9 @667MHz）：一次 poll ≈ **54µs**（30fps 下占 0.16%），写一笔 ≈ **1.7ms**
    （只在人按键/划滑条时发生，一次一笔，无所谓）。第一版注释里我写"~4µs"是**估的**
    —— 量完差 36 倍，改真数。
    ★★ 第一版比的是 `os.stat` 的 **mtime_ns**，结果**会漏写**（PC 上实测复现）：
      Windows 的 NTFS **延迟更新目录项时间戳**，按路径 stat 可能拿到旧值；FAT32 的
      时间戳粒度更是 **2 秒**。比内容字节就没有这些语义问题，而且一样便宜。
  * **状态文件放 tmpfs**（默认 /dev/shm/v3_input.json）：键盘每按一下写一次，写 flash
    不划算；重启即清零也是好事（状态总是干净的）。
  * **写入必须原子**：先写同目录临时文件再 os.replace —— 读的一侧永远看到完整的
    旧值或新值，不会读到写了一半的 JSON。
  * ★★ **静默失败最贵**：文件缺失 / JSON 坏 / 值不是数（NaN、inf、字符串）——
    一律**保持上一次的值**，并且**第一次**出问题时打印一条明确的提示（之后安静，
    长跑不许刷屏）。绝不"悄悄退回默认值"：那才是最难查的 bug。

和别的工具的分工：本文件**不碰相机、不碰 PL、不碰 VDMA**，纯状态文件 + 标准库网络。
"""
import json
import os
import sys
import tempfile
import threading
import time

# ★★ 2026-10-09：write_state 的全局锁。三个并发写者——键盘主循环、鼠标
#   HID-debugfs 旁路线程（新）、serve 的多线程 HTTP——都做"读-改-写"同一份文件，
#   不加锁会互相覆盖（A 读了旧值、B 写完、A 把 B 的字段抹回去）。锁放函数内部，
#   所有调用点自动安全。
_WSLOCK = threading.Lock()

# ---- 路径与量程（钳位是防"手滑拖到底"把场区扫成 0/360）----------------------
PATH_DEFAULT = os.environ.get('V3_INPUT_PATH', '/dev/shm/v3_input.json')

ANG_DEF, SPD_DEF = 0.0, 400.0        # 与 ball_game.py / v3_scan.py 的规则默认一致
ANG_STEP, SPD_STEP = 2.0, 20.0       # ★ 项目规则：S/W 2°、A/D 20 px/s
# ★★ 2026-10-28 用户定案：界面角度 = **标准坐标系**（0°=右水平、逆时针为正，
#   360° 一圈，按 360 环绕）；速度单位 = **游戏像素/秒（px/s）**。
ANG_MIN, ANG_MAX = 0.0, 360.0
SPD_MIN, SPD_MAX = 50.0, 2000.0
# ★ 发射结果文件（游戏写、本服务的 /shot 端点读）：不是输入，是**回显**，
#   所以另开一份文件 —— 输入文件被 write_state 重建（只保留固定字段），
#   塞在同文件里会被下一笔手机写入抹掉。
SHOT_PATH = os.environ.get('V3_SHOT_PATH', '/dev/shm/v3_shot.json')
# ★ 2026-10-06（用户："网页应该实时刷新有几个球的序号"）：场上球列表（游戏写、
#   /balls 读）——网页的选球按钮做成**动态**（场上有几颗就显示几颗）。
BALLS_PATH = os.environ.get('V3_BALLS_PATH', '/dev/shm/v3_balls.json')
# ★ 2026-10-07 M2.3：对局状态（ball_game --match 每 0.5s 原子写；/match 端点读）
MATCH_PATH = os.environ.get('V3_MATCH_PATH', '/dev/shm/v3_match.json')


def clamp_angle(a):
    try:
        a = float(a)
    except Exception:
        return None
    if a != a or a in (float('inf'), float('-inf')):    # NaN / inf 一律当坏值
        return None
    # ★ 2026-10-28：0..360 环绕（999 -> 279；-10 -> 350），不再截到 ±45。
    return float(a) % 360.0


def clamp_speed(s):
    try:
        s = float(s)
    except Exception:
        return None
    if s != s or s in (float('inf'), float('-inf')):
        return None
    return max(SPD_MIN, min(SPD_MAX, s))


# ---- ★★ 2026-10-08 商店 v2：鼠标光标 ----------------------------------------
#  光标 = **1280x720 画布**（= HDMI 帧泵的 720p；商店画面就按这个尺寸渲染，
#  帧泵.show() 同尺寸时跳过缩放那 75ms —— 光标才有流畅可言）。
SHOP_W, SHOP_H = 1280, 720
MOUSE_GAIN = 1.0        # REL 计数 -> 画布像素（1.0 ≈ 标准 1:1；--mouse-gain 可调）


def mouse_step(x, y, dx, dy, gain=None):
    """把相对位移累进光标并 clamp 到画布内。纯函数——台架可测。
    REL 事件是**相对**的：读取器累进、80ms 节流写文件；缺省值/坏值不炸。"""
    if gain is None:
        gain = MOUSE_GAIN
    try:
        nx = int(x) + int(round(float(dx) * float(gain)))
        ny = int(y) + int(round(float(dy) * float(gain)))
    except Exception:
        return int(x), int(y)
    return max(0, min(SHOP_W - 1, nx)), max(0, min(SHOP_H - 1, ny))


# ---- ★★ 2026-10-09 鼠标 HID-debugfs 旁路 -------------------------------------
#  实况（10-09 一整天排障）：本镜像/内核上，**同一 Hub 里"最后插入"的 USB-HID
#  设备，其 evdev 节点全部 open=EBUSY**（先插的完全正常；键盘先插则键盘走 evdev
#  老路，后插的鼠标被锁）。锁定与进程占用无关、与 grab/inhibit 语义对不上，是
#  这套组合的系统性毛病。**旁路**：/sys/kernel/debug/hid/<id>/events 把原始 HID
#  报文直接吐成文本（键盘 340 行、鼠标 7.3 万行/20s 实测都在流），**完全不经过
#  evdev、不受锁影响**。鼠标报文实测（YJX ML MX301）：
#      report (size 9) (numbered) = 02 00 00 ba ff fc ff 00 00
#      [0]=id(0x02) [1:3]=按键 [3:5]=dx(int16 小端) [5:7]=dy(int16 小端) [7:9]=尾
def parse_hid_mouse(line):
    """HID-debugfs 的 report 行 -> (dx, dy, btns)；非 report 行/格式不符 -> None。
    纯函数——台架可测。"""
    s = (line or '').strip()
    if not s.startswith('report'):
        return None
    try:
        hexs = s.split('=', 1)[1].split()
        b = [int(x, 16) for x in hexs]
    except Exception:
        return None
    if len(b) < 9 or b[0] != 0x02:
        return None
    dx = b[3] | (b[4] << 8)
    dy = b[5] | (b[6] << 8)
    if dx >= 0x8000:
        dx -= 0x10000
    if dy >= 0x8000:
        dy -= 0x10000
    return dx, dy, (b[1] & 0x07)


def _hid_mouse_dbgfs():
    """找一个'鼠标类'设备的 HID debugfs events 路径：返回 (路径, 设备名) 或 (None, None)。
    靠 /dev/input/event* 的 sysfs 名字 + device 链接里的 0003:VID:PID.N 段定位
    （名字含 mouse 不含 keyboard；与 _find_mice 同款的"排除幻影"约定）。"""
    import glob
    for ev in sorted(glob.glob('/dev/input/event*')):
        base = os.path.basename(ev)
        try:
            name = open('/sys/class/input/%s/device/name' % base).read().strip()
        except Exception:
            continue
        low = name.lower()
        if 'mouse' not in low or 'keyboard' in low:
            continue
        try:
            # ★ 用 realpath 解绝对路径：/sys/class/input/eventN/device 是**相对**
            #   链接（../../inputM），readlink 拿到的裸串里没有 hid 段（板载实锤：
            #   第一版因此扫不到鼠标）。realpath 后 = .../0003:VID:PID.N/input/inputM。
            tgt = os.path.realpath('/sys/class/input/%s/device' % base)
        except Exception:
            continue
        for seg in tgt.split('/'):
            if seg.startswith('0003:'):
                p = '/sys/kernel/debug/hid/%s/events' % seg
                if os.path.exists(p):
                    return p, name
    return None, None


# ---- 状态文件：读 / 写 -------------------------------------------------------
def read_state(path=None):
    """读一次，不缓存。给 status / 网页的 /state 用；**游戏循环请用 InputHub.poll()**。
    读不出来一律返回 None（调用方自己决定怎么报）。"""
    try:
        # ★ encoding 显式给 utf-8：板子若在 C locale 下默认编码可能是 ASCII，
        #   而 /shot 那份（游戏写的）带中文 msg —— 不许靠 locale 的运气
        with open(path or PATH_DEFAULT, 'r', encoding='utf-8') as f:
            o = json.load(f)
        return o if isinstance(o, dict) else None
    except Exception:
        return None


def read_match(path=None):
    """★ 2026-10-07 M2.3：读"对局状态"（游戏写的 MATCH_PATH；/match 端点用）。
    **任何异常一律空 dict**（网页整块隐藏而已，绝不抛——同 /balls 纪律）。"""
    st = read_state(path or MATCH_PATH)
    return st if isinstance(st, dict) else {}


def read_balls(path=None):
    """★ 2026-10-06：读"场上现在有几号球"（游戏写的 BALLS_PATH）。

    返回 [{'no': N, 'cls': 'red'|'blue'|...}]；**任何异常一律空表**（网页少一行
    列表而已，绝不抛——这条纪律和 /shot 一样）。"""
    st = read_state(path or BALLS_PATH)
    if isinstance(st, dict) and isinstance(st.get('balls'), list):
        out = []
        for b in st['balls']:
            if isinstance(b, dict) and isinstance(b.get('no'), int):
                out.append({'no': b['no'], 'cls': str(b.get('cls'))})
        return out
    return []


def write_state(angle=None, speed=None, src='?', path=None, note='', fire=False,
                player=1, ball=None, sel=None, nav=None, buy=False,
                mx=None, my=None, mbtn=False, sopen=False,
                edit=False, hole=False, nav2=None, esc=False,
                hx=None, hy=None, hopos=False, pe=False, obs=False,
                ppl=False, aball=False, num=None, nbksp=False):
    """★ 全局锁包装（见 _WSLOCK）：body 见 _write_state_unlocked。"""
    with _WSLOCK:
        return _write_state_unlocked(
            angle=angle, speed=speed, src=src, path=path, note=note,
            fire=fire, player=player, ball=ball, sel=sel, nav=nav, buy=buy,
            mx=mx, my=my, mbtn=mbtn, sopen=sopen,
            edit=edit, hole=hole, nav2=nav2, esc=esc,
            hx=hx, hy=hy, hopos=hopos, pe=pe, obs=obs, ppl=ppl,
            aball=aball, num=num, nbksp=nbksp)


def _write_state_unlocked(angle=None, speed=None, src='?', path=None, note='',
                          fire=False, player=1, ball=None, sel=None, nav=None,
                          buy=False, mx=None, my=None, mbtn=False, sopen=False,
                          edit=False, hole=False, nav2=None, esc=False,
                          hx=None, hy=None, hopos=False, pe=False, obs=False,
                          ppl=False, aball=False, num=None, nbksp=False):
    """原子写一次状态。angle/speed 给 None = 沿用旧值（只改一个也好使）。
    ★ `fire=True` = 这是一笔**发射**（"发射"按钮 / 键盘空格）：把 fire 计数 +1。
      普通写入**保持**原计数不动（不回落、不清零）—— 游戏读的是它的**上升沿**。
    ★★ 2026-10-05 夜：**双人**（用户："网页改双人控制"）——`player=1` 写旧字段
      `angle/speed/fire`（老口径一字不变，台架断言全保）；`player=2` 写
      `p2_angle/p2_speed/p2_fire`。两组字段**互不相干**：写谁只动谁，另一组保留。
    返回值就是写进去的那个 dict（方便调用方回显）。"""
    path = path or PATH_DEFAULT
    old = read_state(path) or {}

    def _compute(tag, angle_v, speed_v, do_fire):
        ka = ('p2_angle' if tag == 2 else 'angle')
        ks = ('p2_speed' if tag == 2 else 'speed')
        kf = ('p2_fire' if tag == 2 else 'fire')
        a_ = clamp_angle(angle_v if angle_v is not None else old.get(ka, ANG_DEF))
        if a_ is None:
            a_ = clamp_angle(old.get(ka, ANG_DEF)) or ANG_DEF
        s_ = clamp_speed(speed_v if speed_v is not None else old.get(ks, SPD_DEF))
        if s_ is None:
            s_ = clamp_speed(old.get(ks, SPD_DEF)) or SPD_DEF
        try:
            f_ = int(old.get(kf, 0) or 0)
        except Exception:
            f_ = 0
        if do_fire:
            f_ += 1
        return float(a_), float(s_), f_

    try:
        seq = int(old.get('seq', 0)) + 1
    except Exception:
        seq = 1
    if int(player) == 2:
        a2, s2, f2 = _compute(2, angle, speed, fire)
        a1, s1, f1 = _compute(1, None, None, False)
    else:
        a1, s1, f1 = _compute(1, angle, speed, fire)
        a2, s2, f2 = _compute(2, None, None, False)

    def _ball_no(tag, v):
        k = ('p2_ball' if tag == 2 else 'p1_ball')
        if v is None:
            v = old.get(k, tag)
        try:
            v = int(v)
        except Exception:
            v = tag
        return max(1, min(9, v))

    b1 = _ball_no(1, ball if int(player) == 1 else None)
    b2 = _ball_no(2, ball if int(player) == 2 else None)

    def _sel_cnt(tag, dlt):
        """★ 2026-10-06 M2.1：选球键计数（键盘 Z/X、,/.）。dlt=±1 时对应
        p{N}_sup / p{N}_sdn +1（游戏读上升沿循环控球编号）；其余沿用旧值。"""
        pre = 'p2_' if tag == 2 else 'p1_'
        out = {}
        for suf in ('sup', 'sdn'):
            try:
                out[pre + suf] = int(old.get(pre + suf, 0) or 0)
            except Exception:
                out[pre + suf] = 0
        if dlt:
            out[pre + ('sup' if int(dlt) > 0 else 'sdn')] += 1
        return out

    su1 = _sel_cnt(1, sel if int(player) == 1 else None)
    su2 = _sel_cnt(2, sel if int(player) == 2 else None)

    def _nav_cnt(tag, dlt, dlt2, do_buy, do_sopen, do_edit, do_hole, do_pe,
                 do_obs=False, do_ppl=False, do_aball=False):
        """★ 道具系统计数器总表（缺/坏沿用旧值，与 sup/sdn 同款）：
        dlt=±1  -> p{N}_mup/mdn（W/S·↑↓ 的第二语义：局前选单 / 局中 SELECT）；
        dlt2=±1 -> p{N}_aup/adn（★ 2026-10-14：A/D·←→ 的第二语义——EDIT-ADJUST
                    的强度 ∓）；
        do_buy  -> p{N}_buy（E/小键盘*/手机购买）；do_sopen -> p{N}_sopen（手机进店）；
        do_edit -> p{N}_edit（Q/小键盘. 发起改磁场/切阶段）；do_hole -> p{N}_hole
                    （C/小键盘+ 发起加洞口/切边）；
        do_obs  -> p{N}_obs（V/小键盘- 布置实体障碍）；
        do_ppl  -> p{N}_ppl（G/小键盘7 布置实体传送门）；
        do_aball-> p{N}_aball（F/小键盘8 局中用加球道具）。"""
        pre = 'p2_' if tag == 2 else 'p1_'
        out = {}
        for suf in ('mup', 'mdn', 'buy', 'sopen', 'edit', 'hole', 'aup', 'adn',
                    'pe', 'obs', 'ppl', 'aball'):
            try:
                out[pre + suf] = int(old.get(pre + suf, 0) or 0)
            except Exception:
                out[pre + suf] = 0
        if dlt:
            out[pre + ('mup' if int(dlt) > 0 else 'mdn')] += 1
        if dlt2:
            out[pre + ('aup' if int(dlt2) > 0 else 'adn')] += 1
        if do_buy:
            out[pre + 'buy'] += 1
        if do_sopen:
            out[pre + 'sopen'] += 1
        if do_edit:
            out[pre + 'edit'] += 1
        if do_hole:
            out[pre + 'hole'] += 1
        if do_pe:
            out[pre + 'pe'] += 1
        if do_obs:
            out[pre + 'obs'] += 1
        if do_ppl:
            out[pre + 'ppl'] += 1
        if do_aball:
            out[pre + 'aball'] += 1
        return out

    nv1 = _nav_cnt(1, nav if int(player) == 1 else None,
                   nav2 if int(player) == 1 else None,
                   bool(buy) and int(player) == 1,
                   bool(sopen) and int(player) == 1,
                   bool(edit) and int(player) == 1,
                   bool(hole) and int(player) == 1,
                   bool(pe) and int(player) == 1,
                   bool(obs) and int(player) == 1,
                   bool(ppl) and int(player) == 1,
                   bool(aball) and int(player) == 1)
    nv2 = _nav_cnt(2, nav if int(player) == 2 else None,
                   nav2 if int(player) == 2 else None,
                   bool(buy) and int(player) == 2,
                   bool(sopen) and int(player) == 2,
                   bool(edit) and int(player) == 2,
                   bool(hole) and int(player) == 2,
                   bool(pe) and int(player) == 2,
                   bool(obs) and int(player) == 2,
                   bool(ppl) and int(player) == 2,
                   bool(aball) and int(player) == 2)

    def _hpos_state(tag, hxv, hyv, do_pos):
        """★★ 2026-10-15 加洞口 v2（手机俯视图）：点位置通道。
        p{N}_hx/hy = 游戏坐标（960x540 场区系）；p{N}_hopos = 点位置计数
        （do_pos=True 时 +1——游戏读上升沿，与 fire/buy 同款纪律：缺/坏沿用旧值）。"""
        pre = 'p2_' if tag == 2 else 'p1_'
        out = {}
        try:
            out[pre + 'hopos'] = int(old.get(pre + 'hopos', 0) or 0)
        except Exception:
            out[pre + 'hopos'] = 0
        for _sfx, _v in (('hx', hxv), ('hy', hyv)):
            try:
                out[pre + _sfx] = (round(float(_v), 1) if _v is not None
                                   else float(old.get(pre + _sfx, 0.0) or 0.0))
            except Exception:
                try:
                    out[pre + _sfx] = float(old.get(pre + _sfx, 0.0) or 0.0)
                except Exception:
                    out[pre + _sfx] = 0.0
        if do_pos:
            out[pre + 'hopos'] += 1
        return out

    hp1 = _hpos_state(1, hx if int(player) == 1 else None,
                      hy if int(player) == 1 else None,
                      bool(hopos) and int(player) == 1)
    hp2 = _hpos_state(2, hx if int(player) == 2 else None,
                      hy if int(player) == 2 else None,
                      bool(hopos) and int(player) == 2)
    # ★ 全局 ESC（取消当前暂停操作）——不分玩家，单计数器
    try:
        _esc_n = int(old.get('esc', 0) or 0)
    except Exception:
        _esc_n = 0
    if esc:
        _esc_n += 1

    def _m_state(mxv, myv, do_click):
        """★★ 2026-10-08 商店 v2：鼠标（mx/my=绝对光标值，None=沿用旧值；
        mbtn=左键计数，True 时 +1——游戏读上升沿；缺/坏同 fire 款）。"""
        out = {}
        for k, v in (('mx', mxv), ('my', myv)):
            try:
                out[k] = (int(v) if v is not None
                          else int(old.get(k, 0) or 0))
            except Exception:
                out[k] = 0
        try:
            out['mbtn'] = int(old.get('mbtn', 0) or 0)
        except Exception:
            out['mbtn'] = 0
        if do_click:
            out['mbtn'] += 1
        return out

    ms = _m_state(mx, my, mbtn)
    # ★★ 2026-10-29 改磁场数值：全局数字/退格计数（nd0..nd9 + nbksp）
    _nst = {}
    for _i in range(10):
        try:
            _nst['nd%d' % _i] = int(old.get('nd%d' % _i, 0) or 0)
        except Exception:
            _nst['nd%d' % _i] = 0
    try:
        _nst['nbksp'] = int(old.get('nbksp', 0) or 0)
    except Exception:
        _nst['nbksp'] = 0
    if num is not None:
        try:
            _nst['nd%d' % (int(num) % 10)] += 1
        except Exception:
            pass
    if nbksp:
        _nst['nbksp'] += 1
    obj = dict(angle=round(a1, 3), speed=round(s1, 3), src=str(src),
               t=time.time(), seq=seq, note=str(note), fire=f1,
               p1_ball=b1, p2_ball=b2,
               p2_angle=round(a2, 3), p2_speed=round(s2, 3), p2_fire=f2,
               esc=_esc_n, **su1, **su2, **nv1, **nv2, **hp1, **hp2, **ms,
               **_nst)
    d = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.v3in_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)          # ★ 原子换名：读的一侧永远看不到半截 JSON
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    return obj


# ---- 游戏循环侧：每帧 poll ---------------------------------------------------
class InputHub:
    """每帧 poll() 一次，代价 ~54µs（内容字节没变就直接返回上次结果，板上实测）。

    ★ 为什么比**内容**而不是比 mtime：见文件头注（NTFS 延迟时间戳 / FAT 2 秒粒度
      都会让 mtime 快路径**漏写**，实测在 PC 上复现过）。内容比对比时间戳更精确，
      代价一样是"读一个 150 字节的文件"。

    ★ 失败语义（照"静默失败最贵"办事）：读不到就**保持上一次的值**，
      **第一次**出问题打印一条明确的提示，之后安静（长跑不刷屏）。
      并且把 `n_bad` 计数交出去 —— 汇总里要能报"这一趟输入层坏了 N 次"。
    """

    def __init__(self, path=None, angle=ANG_DEF, speed=SPD_DEF,
                 verbose=True, tag='[输入]'):
        self.path = path or PATH_DEFAULT
        self.angle = float(angle)
        self.speed = float(speed)
        # ★ 2026-10-05 双人：P2 独立状态（p2_angle/p2_speed/p2_fire）
        self.angle2 = float(angle)
        self.speed2 = float(speed)
        self.fire2 = 0
        self.changed2 = False
        # ★ 2026-10-05 夜：**选球**（每个玩家控哪颗编号的球；1..9）
        self.ball1 = 1
        self.ball2 = 2
        # ★ 2026-10-06 M2.1：选球键计数（键盘 Z/X、,/.；游戏侧读上升沿循环）
        self.sup1 = self.sdn1 = self.sup2 = self.sdn2 = 0
        # ★ 2026-10-08 道具系统：局前选购计数器（mup/mdn=选单，buy=购买）
        self.mup1 = self.mdn1 = self.buy1 = 0
        self.mup2 = self.mdn2 = self.buy2 = 0
        # ★★ 2026-10-08 商店 v2：手机「进入商店」计数（独立于 buy，防误购）
        self.sopen1 = self.sopen2 = 0
        # ★★ 2026-10-14 局中道具（规则书 §6.4/§7.1）：
        #   edit=改磁场（Q/小键盘.）、hole=加洞口（C/小键盘+）、
        #   aup/adn=A/D·←→ 的第二语义（EDIT-ADJUST 强度 ∓）、esc=全局取消
        self.edit1 = self.edit2 = self.hole1 = self.hole2 = 0
        self.aup1 = self.adn1 = self.aup2 = self.adn2 = 0
        self.esc = 0
        # ★★ 2026-10-15 加洞口 v2（手机俯视图）：点位置计数 + 位置（游戏坐标）
        self.hopos1 = self.hopos2 = 0
        self.hx1 = self.hy1 = self.hx2 = self.hy2 = 0.0
        # ★★ 2026-10-16 粒子引擎风暴触发计数（B/N）
        self.pe1 = self.pe2 = 0
        self.obs1 = self.obs2 = 0      # ★ 2026-10-19 布置 PLACE：障碍摆放触发计数
        self.ppl1 = self.ppl2 = 0      # ★ 2026-10-25 实体传送门摆放触发计数（G/小键盘7）
        self.aball1 = self.aball2 = 0  # ★ 2026-10-25 局中加球道具触发计数（F/小键盘8）
        # ★★ 2026-10-29 改磁场数值输入：全局数字/退格计数
        self.nd = [0] * 10
        self.nbksp = 0
        # ★★ 2026-10-08 商店 v2：鼠标（mx/my=光标绝对值 0..1279/0..719；mbtn=左键计数）
        self.mx = self.my = 0
        self.mbtn = 0
        self.src = 'cli'
        self.seq = 0
        self.fire = 0                  # ★ 发射计数（上升沿=发一杆；见文件头注）
        self.n_bad = 0                 # 读失败的次数（汇总里报）
        self.n_change = 0              # 值真的变了几次
        self.t_change = 0.0
        self.verbose = bool(verbose)
        self.tag = tag
        self._raw = None               # 上次读到的**原始字节**（比它才知道要不要重新解析）
        self._warned = set()           # 每类问题只吱一声

    def _once(self, key, msg):
        if key in self._warned:
            return
        self._warned.add(key)
        print('%s ★ %s' % (self.tag, msg), flush=True)

    def poll(self):
        """返回 (angle, speed, changed)。changed=True 表示这次真的变了，调用方可以报一条。"""
        try:
            # ★ 用 os.open/read/close 而不是 `with open(...)`：**一样是精确比内容**，
            #   但板上实测快 2.5 倍（136µs -> 54µs，少的是 Python 文件对象的开销）。
            fd = os.open(self.path, os.O_RDONLY)
            try:
                raw = os.read(fd, 4096)
            finally:
                os.close(fd)
        except OSError:
            self._raw = None            # 文件没了：下次出现必须重新解析（别被快路径吃掉）
            self.n_bad += 1
            self._once('missing',
                       '输入状态文件不在（%s）—— **保持** %.1f° / %.0f px/s 不变；'
                       '要手机遥控就起来跑 `v3_input.py serve`'
                       % (self.path, self.angle, self.speed))
            return self.angle, self.speed, False
        if raw == self._raw:            # ★★ 快路径：**内容字节**一模一样才算没变
            return self.angle, self.speed, False
        self._raw = raw                 # 坏内容也记账，免得每帧重读一个坏文件
        try:
            obj = json.loads(raw.decode('utf-8'))
            if not isinstance(obj, dict):
                obj = None
        except Exception:
            obj = None
        if obj is None:
            self.n_bad += 1
            self._once('badjson',
                       '输入状态文件读不出来（%s）—— **保持** %.1f° / %.0f px/s 不变'
                       % (self.path, self.angle, self.speed))
            return self.angle, self.speed, False
        a = clamp_angle(obj.get('angle'))
        s = clamp_speed(obj.get('speed'))
        if a is None or s is None:
            self.n_bad += 1
            self._once('badval',
                       '输入状态里的值不是数（angle=%r speed=%r）—— 忽略这一笔，'
                       '保持 %.1f° / %.0f px/s' % (obj.get('angle'), obj.get('speed'),
                                                   self.angle, self.speed))
            return self.angle, self.speed, False
        src = str(obj.get('src', '?'))
        try:
            self.seq = int(obj.get('seq', self.seq))
        except Exception:
            pass
        try:
            # ★ 发射计数：坏值/缺字段一律**保持上次**（不回落成 0 —— 回落会被
            #   游戏侧当成"文件重建过"的跨基准，顶多吞一杆、绝不误发一杆）。
            if 'fire' in obj:
                self.fire = int(obj.get('fire') or 0)
        except Exception:
            pass
        # ★ 2026-10-05 双人：P2 字段（缺/坏 = 保持上次，与 p1 同款语义）
        a2 = clamp_angle(obj.get('p2_angle'))
        s2 = clamp_speed(obj.get('p2_speed'))
        if a2 is not None and s2 is not None:
            self.changed2 = ((abs(a2 - self.angle2) > 1e-9)
                             or (abs(s2 - self.speed2) > 1e-9))
            self.angle2, self.speed2 = a2, s2
        else:
            self.changed2 = False
        try:
            if 'p2_fire' in obj:
                self.fire2 = int(obj.get('p2_fire') or 0)
        except Exception:
            pass
        try:
            # ★ 选球（坏值保持上次）
            self.ball1 = max(1, min(9, int(obj.get('p1_ball', self.ball1))))
            self.ball2 = max(1, min(9, int(obj.get('p2_ball', self.ball2))))
        except Exception:
            pass
        try:
            # ★ 2026-10-29：改磁场数字/退格计数（缺/坏 = 保持上次）
            for _i in range(10):
                _k = 'nd%d' % _i
                if _k in obj:
                    self.nd[_i] = int(obj.get(_k) or 0)
            if 'nbksp' in obj:
                self.nbksp = int(obj.get('nbksp') or 0)
        except Exception:
            pass
        try:
            # ★ 2026-10-06 M2.1：选球键计数（缺/坏 = 保持上次，与 fire 同款语义）
            if 'p1_sup' in obj:
                self.sup1 = int(obj.get('p1_sup') or 0)
            if 'p1_sdn' in obj:
                self.sdn1 = int(obj.get('p1_sdn') or 0)
            if 'p2_sup' in obj:
                self.sup2 = int(obj.get('p2_sup') or 0)
            if 'p2_sdn' in obj:
                self.sdn2 = int(obj.get('p2_sdn') or 0)
            # ★ 2026-10-08 道具系统：局前选购计数（缺/坏=保持上次，同 sup/sdn）
            if 'p1_mup' in obj:
                self.mup1 = int(obj.get('p1_mup') or 0)
            if 'p1_mdn' in obj:
                self.mdn1 = int(obj.get('p1_mdn') or 0)
            if 'p1_buy' in obj:
                self.buy1 = int(obj.get('p1_buy') or 0)
            if 'p2_mup' in obj:
                self.mup2 = int(obj.get('p2_mup') or 0)
            if 'p2_mdn' in obj:
                self.mdn2 = int(obj.get('p2_mdn') or 0)
            if 'p2_buy' in obj:
                self.buy2 = int(obj.get('p2_buy') or 0)
            # ★★ 2026-10-08 商店 v2：手机「进入商店」（缺/坏=保持上次）
            if 'p1_sopen' in obj:
                self.sopen1 = int(obj.get('p1_sopen') or 0)
            if 'p2_sopen' in obj:
                self.sopen2 = int(obj.get('p2_sopen') or 0)
            # ★ 2026-10-14 局中道具（缺/坏=保持上次；文件字段名 -> 属性名映射）
            for _k, _at in (('p1_edit', 'edit1'), ('p1_hole', 'hole1'),
                            ('p2_edit', 'edit2'), ('p2_hole', 'hole2'),
                            ('p1_aup', 'aup1'), ('p1_adn', 'adn1'),
                            ('p2_aup', 'aup2'), ('p2_adn', 'adn2'),
                            ('esc', 'esc'),
                            ('p1_hopos', 'hopos1'), ('p2_hopos', 'hopos2'),
                            ('p1_pe', 'pe1'), ('p2_pe', 'pe2'),
                            ('p1_obs', 'obs1'), ('p2_obs', 'obs2'),
                            ('p1_ppl', 'ppl1'), ('p2_ppl', 'ppl2'),
                            ('p1_aball', 'aball1'), ('p2_aball', 'aball2')):
                if _k in obj:
                    setattr(self, _at, int(obj.get(_k) or 0))
            # ★★ 2026-10-15 加洞口 v2：点位置（游戏坐标，浮点；缺/坏=保持上次）
            for _k, _at in (('p1_hx', 'hx1'), ('p1_hy', 'hy1'),
                            ('p2_hx', 'hx2'), ('p2_hy', 'hy2')):
                if _k in obj:
                    try:
                        setattr(self, _at, float(obj.get(_k) or 0.0))
                    except Exception:
                        pass
            # ★★ 2026-10-08 商店 v2：鼠标（缺/坏=保持上次，同 fire 款）
            if 'mx' in obj:
                self.mx = int(obj.get('mx') or 0)
            if 'my' in obj:
                self.my = int(obj.get('my') or 0)
            if 'mbtn' in obj:
                self.mbtn = int(obj.get('mbtn') or 0)
        except Exception:
            pass
        changed = (abs(a - self.angle) > 1e-9) or (abs(s - self.speed) > 1e-9)
        if changed:
            self.n_change += 1
            self.t_change = time.time()
        self.angle, self.speed, self.src = a, s, src
        return self.angle, self.speed, changed


# ---- ① 手机网页遥控 ----------------------------------------------------------
_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>磁场演示 · 遥控</title><style>
:root{color-scheme:dark}body{margin:0;padding:14px;background:#14161a;color:#e8eaee;
font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
h1{font-size:17px;margin:2px 0 12px;font-weight:600;color:#9fb3c8}
.card{background:#1d2026;border-radius:14px;padding:14px;margin-bottom:12px}
.lab{font-size:13px;color:#8a97a8;margin-bottom:6px}
.val{font-size:34px;font-weight:700;font-variant-numeric:tabular-nums}
.val small{font-size:15px;font-weight:400;color:#8a97a8;margin-left:4px}
.row{display:flex;gap:10px;margin-top:10px}
button{flex:1;padding:16px 0;font-size:19px;font-weight:600;border:0;border-radius:11px;
background:#2b303a;color:#e8eaee;touch-action:manipulation}
button:active{background:#3a414e}
button.hi{background:#1f4d33}button.hi:active{background:#2a6a45}
button.warn{background:#4d1f28}button.warn:active{background:#6a2a35}
button.fire{background:#a04a12;font-size:23px;padding:20px 0;width:100%;display:block;margin:14px 0 0}
button.fire:active{background:#c25a17}
/* ★★ 2026-10-08 商店 v2：手机端选购小按钮 */
button.sb{flex:0 0 auto;padding:11px 17px;font-size:16px;margin:3px;border-radius:9px}
.numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}
/* ★ 2026-10-31 用户点单：道具/改磁场都要「确定」，就放在「取消」旁边 */
.okrow{grid-column:span 3;display:flex;gap:8px}
.okrow button{flex:1;padding:14px 0;font-size:18px;font-weight:700}
.numpad button{padding:15px 0;font-size:21px;font-weight:600;border-radius:9px}
.sub button.sb{vertical-align:middle}
input[type=range]{width:100%;margin-top:8px;accent-color:#4ea1ff}
.foot{font-size:12px;color:#6b7686;line-height:1.6}
.k{display:inline-block;min-width:26px;text-align:center;background:#2b303a;border-radius:5px;
padding:1px 5px;margin:0 2px;font-weight:600}
/* ★ 2026-10-05 双人：两块面板、各自配色（绿=玩家1/球1，紫=玩家2/球2） */
.p1{border:1px solid #2a6a45}.p2{border:1px solid #6a2a75}
.ttl{font-size:15px;font-weight:700;margin-bottom:4px}
.p1 .ttl{color:#3ddc84}.p2 .ttl{color:#c06ae8}
.p1 input[type=range]{accent-color:#3ddc84}.p2 input[type=range]{accent-color:#c06ae8}
.p1 button.fire{background:#1f4d33}.p1 button.fire:active{background:#2a6a45}
.p2 button.fire{background:#4d2b5e}.p2 button.fire:active{background:#6a3a80}
.card .row button.bsel{padding:9px 0;font-size:14px;font-weight:600}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.dot.red{background:#e74c3c}.dot.blue{background:#3b82f6}.dot.other{background:#9aa4b2}
/* ★★ 2026-10-31 用户（平板拍摄）：两个玩家面板**左右并排**，不再上下滑切换；
   窄屏（手机竖屏）仍保持上下，避免按钮挤成一团。 */
@media (min-width:700px){
  #players{display:flex;gap:12px;align-items:flex-start}
  #players>.card{flex:1 1 0;min-width:0;margin-bottom:0}
  h1{font-size:14px;margin:2px 0 8px}
  .card{padding:10px}
  .val{font-size:26px}
  .lab{font-size:12px;margin-bottom:3px}
  input[type=range]{margin-top:4px}
  .row{margin-top:6px}
  button{padding:11px 0;font-size:17px}
  .numpad{gap:5px;margin-top:6px}
  .numpad button{padding:10px 0;font-size:18px}
  button.fire{padding:13px 0;font-size:20px;margin-top:8px}
  #match{padding:7px 10px;margin-bottom:8px;font-size:13.5px}
  .foot{font-size:11px}
  /* 进一步压缩：角度/速度并排；键盘 6 列（3 行变 2 行）；宽屏隐藏标题和最近一杆 */
  .two{display:flex;gap:10px}
  .two>.col{flex:1 1 0;min-width:0}
  .numpad{grid-template-columns:repeat(6,1fr)}
  .okrow{grid-column:span 6}
  #players .foot{display:none}
  h1{display:none}
  body{padding:8px}
  .card{padding:8px}
  .val{font-size:22px}
  .lab{font-size:11px;margin-bottom:2px}
  input[type=range]{margin-top:2px}
  .row{margin-top:5px}
  button{padding:9px 0;font-size:16px}
  button.sb{padding:8px 10px;font-size:14px;margin:2px}
  .numpad{gap:4px;margin-top:5px}
  .numpad button{padding:8px 0;font-size:17px}
  button.fire{padding:11px 0;font-size:19px;margin-top:6px}
  #match{padding:6px 9px;margin-bottom:6px;font-size:13px}
}
/* ★ 2026-10-07 M2.3：对局状态栏 + 链路告警（局/比分/弹药/倒计时/就绪 一眼可见） */
#match{background:#1d3a24;border-radius:11px;padding:10px 12px;margin-bottom:12px;
font-size:15px;font-weight:600;color:#d6ffe4;font-variant-numeric:tabular-nums;line-height:1.55}
#match .sub{font-weight:400;font-size:12.5px;color:#9fd8b2}
#match .hline{text-align:center;margin-top:5px;font-size:17px}
#linkwarn{background:#5a3a0d;border-radius:11px;padding:10px 12px;margin-bottom:12px;
font-size:13.5px;font-weight:600;color:#ffd88a}
/* ★★ 2026-10-15 加洞口 v2：手机俯视图（点位置→再点确定；谁发起扣谁） */
/* ★★ 2026-10-31 修（用户实况："手机上找不到确定按钮"）：原来这块挂在页面**最顶端**，
   用户在下方点「加洞」时浮层出现在屏幕外 ⇒ 看起来根本没有确定键。
   改成**全屏浮层**（position:fixed 铺满视口 + 自身滚动），按钮行 sticky 吸底。 */
#holewrap{position:fixed;left:0;right:0;top:0;bottom:0;z-index:60;
background:rgba(6,8,12,.95);overflow-y:auto;-webkit-overflow-scrolling:touch;
padding:12px 10px 18px}
#holewrap .hbox{max-width:760px;margin:0 auto;background:#151a21;
border:1px solid #b8842a;border-radius:14px;padding:12px}
#holewrap .ttl{color:#ffcf7a}
#hc{display:block;width:100%;border-radius:10px;background:#101216;
touch-action:manipulation;margin-top:6px}
#holewrap .row{position:sticky;bottom:4px;background:#151a21;padding-top:8px}
#holewrap .row button{flex:1;padding:15px 0;font-size:18px;font-weight:700}
button:disabled{opacity:.38}
/* ★★ 2026-10-31：实时摆放（加洞口/加障碍/传送门）的手机确认条 —— 以前只能摸键盘 E */
#placebar{position:fixed;left:8px;right:8px;bottom:8px;z-index:70;
background:#1b1410;border:1px solid #b8842a;border-radius:14px;padding:10px 12px;
box-shadow:0 6px 24px rgba(0,0,0,.6)}
#placebar .pt{color:#ffcf7a;font-size:14px;font-weight:600;text-align:center;
line-height:1.45}
#placebar .row{margin-top:8px}
#placebar .row button{flex:1;padding:14px 0;font-size:18px;font-weight:700}
</style></head><body>
<h1>磁场演示 · 双人遥控（玩家1 绿 · 玩家2 紫）</h1>
<div id="match" style="display:none"></div>
<div id="linkwarn" style="display:none"></div>
<div id="holewrap" style="display:none">
  <div class="hbox">
    <div class="ttl" id="htitle">加洞口 · 俯视图</div>
    <div class="foot" id="hstat">点图上的位置来放洞口</div>
    <canvas id="hc" width="640" height="360"></canvas>
    <div class="row">
      <button class="hi" id="hok" onclick="holeOk()">✔ 确定放洞</button>
      <button class="warn" onclick="holeNo()">✖ 取消</button>
    </div>
  </div>
</div>

<div id="placebar" style="display:none">
  <div class="pt" id="ptxt">布置中…</div>
  <div class="row">
    <button class="hi" onclick="placeOk()">✔ 确定</button>
    <button class="warn" onclick="placeNo()">✖ 取消</button>
  </div>
</div>

<div id="players">
<div class="card p1">
  <div class="ttl">玩家1 · 控球<span id="bn1">–</span>（绿）</div>
  <div class="row" id="ballrow1" style="margin-bottom:12px"><span class="foot">读取场上球…</span></div>
  <div class="two">
    <div class="col">
      <div class="lab">角度（0°=右）</div>
      <div class="val"><span id="a1">--</span><small>度</small></div>
      <input type="range" id="ra1" min="0" max="360" step="1" value="0">
    </div>
    <div class="col">
      <div class="lab">速度（px/s）</div>
      <div class="val"><span id="s1">--</span><small>px/s</small></div>
      <input type="range" id="rs1" min="50" max="2000" step="10" value="400">
    </div>
  </div>
  <div class="row">
    <button onclick="inc(P1,-2,0)">−2°</button>
    <button onclick="inc(P1,2,0)">+2°</button>
    <button onclick="inc(P1,0,-20)">−20</button>
    <button onclick="inc(P1,0,20)">+20</button>
  </div>
  <button class="fire" id="f1">发 射</button>
  <div class="row" style="margin-top:10px;flex-wrap:wrap;justify-content:center">
    <button class="sb" onclick="holeEnter(1)">加洞</button>
    <button class="sb" onclick="act(1,'edit')">改磁场</button>
    <button class="sb" onclick="act(1,'obs')">加障碍</button>
    <button class="sb" onclick="act(1,'ppl')">传送门</button>
    <button class="sb" onclick="act(1,'aball')">加球</button>
  </div>
  <div class="numpad">
    <button onclick="act(1,'num',1)">1</button>
    <button onclick="act(1,'num',2)">2</button>
    <button onclick="act(1,'num',3)">3</button>
    <button onclick="act(1,'num',4)">4</button>
    <button onclick="act(1,'num',5)">5</button>
    <button onclick="act(1,'num',6)">6</button>
    <button onclick="act(1,'num',7)">7</button>
    <button onclick="act(1,'num',8)">8</button>
    <button onclick="act(1,'num',9)">9</button>
    <button onclick="act(1,'dot')">.</button>
    <button onclick="act(1,'num',0)">0</button>
    <button onclick="act(1,'bksp')">⌫</button>
    <div class="okrow">
      <button class="hi" onclick="act(1,'ok')">✔ 确定</button>
      <button class="warn" onclick="cancelAll(1)">✖ 取消</button>
    </div>
  </div>
  <div class="foot" id="sr1" style="margin-top:10px">最近一杆：——</div>
</div>

<div class="card p2">
  <div class="ttl">玩家2 · 控球<span id="bn2">–</span>（紫）</div>
  <div class="row" id="ballrow2" style="margin-bottom:12px"><span class="foot">读取场上球…</span></div>
  <div class="two">
    <div class="col">
      <div class="lab">角度（0°=右）</div>
      <div class="val"><span id="a2">--</span><small>度</small></div>
      <input type="range" id="ra2" min="0" max="360" step="1" value="0">
    </div>
    <div class="col">
      <div class="lab">速度（px/s）</div>
      <div class="val"><span id="s2">--</span><small>px/s</small></div>
      <input type="range" id="rs2" min="50" max="2000" step="10" value="400">
    </div>
  </div>
  <div class="row">
    <button onclick="inc(P2,-2,0)">−2°</button>
    <button onclick="inc(P2,2,0)">+2°</button>
    <button onclick="inc(P2,0,-20)">−20</button>
    <button onclick="inc(P2,0,20)">+20</button>
  </div>
  <button class="fire" id="f2">发 射</button>
  <div class="row" style="margin-top:10px;flex-wrap:wrap;justify-content:center">
    <button class="sb" onclick="holeEnter(2)">加洞</button>
    <button class="sb" onclick="act(2,'edit')">改磁场</button>
    <button class="sb" onclick="act(2,'obs')">加障碍</button>
    <button class="sb" onclick="act(2,'ppl')">传送门</button>
    <button class="sb" onclick="act(2,'aball')">加球</button>
  </div>
  <div class="numpad">
    <button onclick="act(2,'num',1)">1</button>
    <button onclick="act(2,'num',2)">2</button>
    <button onclick="act(2,'num',3)">3</button>
    <button onclick="act(2,'num',4)">4</button>
    <button onclick="act(2,'num',5)">5</button>
    <button onclick="act(2,'num',6)">6</button>
    <button onclick="act(2,'num',7)">7</button>
    <button onclick="act(2,'num',8)">8</button>
    <button onclick="act(2,'num',9)">9</button>
    <button onclick="act(2,'dot')">.</button>
    <button onclick="act(2,'num',0)">0</button>
    <button onclick="act(2,'bksp')">⌫</button>
    <div class="okrow">
      <button class="hi" onclick="act(2,'ok')">✔ 确定</button>
      <button class="warn" onclick="cancelAll(2)">✖ 取消</button>
    </div>
  </div>
  <div class="foot" id="sr2" style="margin-top:10px">最近一杆：——</div>
</div>
</div>

<div class="card">
  <div class="foot" id="st">状态：读取中…</div>
  <button class="warn" id="sdbtn" style="margin-top:12px;width:100%;display:block"
    onclick="doShutdown(this)">安 全 关 机</button>
  <div class="foot" id="sdtip" style="display:none;margin-top:8px;color:#ffcf7a;
       text-align:center;font-size:15px;font-weight:600"></div>
  <div class="foot" id="balls" style="margin-top:6px">场上：读取中…</div>
  <div class="foot" style="margin-top:6px">
    两台手机可各开本页（各用自己那半区）；键盘 = 玩家1：
    <span class="k">W</span><span class="k">S</span> 角 ±2°、
    <span class="k">A</span><span class="k">D</span> 速 ±20、
    <span class="k">空格</span> 发射
    <button onclick="location.reload()" style="margin-top:8px">刷新页面</button></div>
</div>

<script>
function mkP(p){return {p:p,ball:p,
  ra:document.getElementById('ra'+p),rs:document.getElementById('rs'+p),
  av:document.getElementById('a'+p),sv:document.getElementById('s'+p),
  sr:document.getElementById('sr'+p),lock:false};}
const P1=mkP(1),P2=mkP(2);
function show(P,a,s){P.av.textContent=a.toFixed(1);P.sv.textContent=s.toFixed(0);}
async function pull(){try{const r=await fetch('state',{cache:'no-store'});const j=await r.json();
  if(j&&typeof j.angle==='number'){show(P1,j.angle,j.speed);
    if(!P1.lock){P1.ra.value=j.angle;P1.rs.value=j.speed;}
    if(typeof j.p2_angle==='number'){show(P2,j.p2_angle,j.p2_speed);
      if(!P2.lock){P2.ra.value=j.p2_angle;P2.rs.value=j.p2_speed;}}
    if(typeof j.p1_ball==='number'){markBall(P1,j.p1_ball);}
    if(typeof j.p2_ball==='number'){markBall(P2,j.p2_ball);}
    document.getElementById('st').textContent='状态：'+j.src+' 写的 · 第 '+j.seq+' 笔';}
  else{document.getElementById('st').textContent='状态：还没人写过（等任一手机/键盘/滑条）';}}
  catch(e){document.getElementById('st').textContent='状态：读不到（'+e+'）';}}
async function push(P){P.lock=true;try{await fetch('set?p='+P.p+'&angle='+P.ra.value+'&speed='
    +P.rs.value,{cache:'no-store'});}
  finally{setTimeout(()=>{P.lock=false;},250);}await pull();}
/* ★ 2026-10-30：安全关机——点完给出**明确的"可以断电了"**提示。
   板子按 停游戏→sync→poweroff 走，约 12~20 秒断电；这期间页面显示倒计时，
   关掉后页面自然连不上。 */
function doShutdown(btn){
  if(!confirm('确定安全关机？会先停游戏、把数据写盘，再断电')) return;
  btn.disabled=true; btn.textContent='正在安全关机…';
  const tip=document.getElementById('sdtip'); tip.style.display='';
  tip.style.color='#ffcf7a';
  tip.textContent='正在停游戏并写盘…（会一直探测，板子真断了才提示可拔电）';
  fetch('shutdown',{cache:'no-store'}).catch(()=>{});
  let miss=0;
  const probe=()=>{
    const ctl=new AbortController();
    const to=setTimeout(()=>ctl.abort(),2500);
    fetch('balls',{cache:'no-store',signal:ctl.signal})
      .then(r=>{clearTimeout(to); miss=0;
        tip.textContent='正在关机…（板子仍在响应，请稍等）';
        setTimeout(probe,1500);})
      .catch(()=>{clearTimeout(to); miss++;
        if(miss>=3){tip.style.color='#7CFC9A';
          tip.textContent='✔ 板子已完全断电 —— 现在可以拔电';}
        else{tip.textContent='关机中…（连续 '+miss+'/3 次无响应）';
          setTimeout(probe,1500);}});
  };
  setTimeout(probe,3000);
}
async function act(p,a,n){let u='act?p='+p+'&a='+a;if(n!==undefined)u+='&n='+n;
  try{await fetch(u,{cache:'no-store'});}catch(e){}await pull();}
/* ★★ 2026-10-31（用户实况："按取消没取消"）：游戏里 ESC 是**分步**的 ——
   加洞口第一下只关确认弹窗、改磁场每次退一步。手机上的「取消」应该是
   **整件事取消**，所以这里连发 ESC（间隔 500ms：游戏帧 ~330ms，连发会
   被计数合并成一次事件，必须拉开），直到 /match 看到流程真的结束（最多 4 下）。 */
async function _matchJ(){try{const r=await fetch('match',{cache:'no-store'});
  return await r.json();}catch(e){return null;}}
function _busyFlow(j){return !!(j&&(j.phase==='EDIT'||j.phase==='HOLE'
  ||(j.hole&&j.hole.active)));}
async function cancelAll(p){
  for(let i=0;i<4;i++){
    try{await fetch('act?p='+p+'&a=esc',{cache:'no-store'});}catch(e){}
    await new Promise(r=>setTimeout(r,500));
    const j=await _matchJ();
    if(j&&!j.on)break;
    if(j&&!j.phase)break;
    /* 加洞口/改磁场：/match 能看到流程，结束就停；
       布置/传送门在 PLAY 里、手机看不到 —— 至少发 3 下（覆盖它们的三段退出）。 */
    if(i>=2&&!_busyFlow(j))break;
  }
  setTimeout(pullMatch,300);
}
function bindP(P){P.ra.onchange=P.rs.onchange=()=>push(P);
  P.ra.oninput=P.rs.oninput=()=>show(P,+P.ra.value,+P.rs.value);}
bindP(P1);bindP(P2);
function inc(P,da,ds){let a=(+P.ra.value+da)%360;if(a<0)a+=360;
  P.ra.value=a.toFixed(0);
  P.rs.value=(+P.rs.value+ds).toFixed(2);
  show(P,a,+P.rs.value);push(P);}
/* ★★ 2026-10-06（用户："网页应该实时刷新有几个球的序号……有五个球但只能控制
   123球"）：选球按钮改成**动态**——按 /balls 给的"场上球列表"渲染（几颗显示几颗，
   带颜色圆点）；选中的高亮。 */
function renderBalls(P,list){P.nums=list||[];
  const row=document.getElementById('ballrow'+P.p);row.innerHTML='';
  if(!P.nums.length){const s=document.createElement('span');
    s.className='foot';s.textContent='场上还没有球';row.appendChild(s);return;}
  for(const b of P.nums){const btn=document.createElement('button');
    btn.className='bsel';
    const dot=document.createElement('i');
    dot.className='dot '+(b.cls==='red'?'red':(b.cls==='blue'?'blue':'other'));
    btn.appendChild(dot);
    btn.appendChild(document.createTextNode('球'+b.no));
    btn.onclick=()=>selBall(P,b.no);
    if(b.no===P.ball){btn.style.outline='2px solid #f2f2f2';}
    row.appendChild(btn);}}
function markBall(P,n){P.ball=n;
  const e=document.getElementById('bn'+P.p);if(e)e.textContent=n;
  const f=document.getElementById('f'+P.p);if(f)f.textContent='发 射（球'+n+'）';
  renderBalls(P,P.nums||[]);}
function selBall(P,n){markBall(P,n);
  fetch('set?p='+P.p+'&ball='+n+'&angle='+P.ra.value+'&speed='+P.rs.value,
        {cache:'no-store'});}
async function pullShot(P){try{const r=await fetch('shot?p='+P.p,{cache:'no-store'});
  const j=await r.json();
  if(!j||!j.msg){return;}   // ★ 2026-10-05 修：原来判的是**不存在的** j.res，结果永远不显示
  P.sr.textContent='最近一杆 ['+new Date(j.t*1000).toLocaleTimeString()+'] '+j.msg;}
  catch(e){}}
function fire(P){P.sr.textContent='已按发射，等结果…';
  fetch('fire?p='+P.p+'&angle='+P.ra.value+'&speed='+P.rs.value,{cache:'no-store'})
    .catch(e=>{P.sr.textContent='发射没发出去（'+e+'）';});
  setTimeout(()=>pullShot(P),700);}
document.getElementById('f1').onclick=()=>fire(P1);
document.getElementById('f2').onclick=()=>fire(P2);
setInterval(pull,700);pull();
async function pullBalls(){try{const r=await fetch('balls',{cache:'no-store'});
  const j=await r.json();const L=(j&&j.balls)||[];
  renderBalls(P1,L);renderBalls(P2,L);
  document.getElementById('balls').textContent='场上 '+L.length+' 颗'+
    (L.length?'：'+L.map(b=>b.no+'.'+(b.cls==='red'?'红':(b.cls==='blue'?'蓝':'?'))).join('  '):'');
  }catch(e){}}
setInterval(pullBalls,1200);pullBalls();
setInterval(()=>{pullShot(P1);pullShot(P2);},900);
/* ★ 2026-10-07 M2.3：对局状态（游戏写的 /match；没开对局时整块隐藏） */
async function pullMatch(){try{const r=await fetch('match',{cache:'no-store'});
  const j=await r.json();const el=document.getElementById('match');
  const w=document.getElementById('linkwarn');
  holeFromMatch(j);                      /* ★ 2026-10-15 加洞口 v2：俯视图联动 */
  if(!j||!j.on){el.style.display='none';if(w)w.style.display='none';return;}
  el.style.display='';
  const ph={PLAY:(j.grace!=null?'落定中':'进行中'),PREP:'局前准备',
    ROUND_END:'结算中',EDIT:'改场中',HOLE:'加洞中',MATCH_END:'比赛结束'}[j.phase]||j.phase;
  const rn=(j.round<=j.max)?('第'+j.round+'/'+j.max+'局'):'加赛局';
  const t=(j.phase==='PLAY'&&j.timer!=null)?('　'+(j.grace_left!=null?('落定'+Math.max(0,Math.round(j.grace_left))+'秒'):('倒计时'+Math.round(j.timer)+'秒'))):'';
  let rd='';
  if(j.phase==='PREP'){/* ★★ 2026-10-08 商店 v2：屏上商店没开 -> 先给"进入商店" */
    const so=!!j.shop_open;
    const sb=(p)=>{const sd=j['p'+p];
      if(sd.ready)return 'P'+p+' <b>已就绪</b>';
      if(!so)return 'P'+p+' <button class="sb hi" onclick="shopEnter('+p+')">进入商店</button>';
      return 'P'+p+' '+(sd.shop!=null?('['+sd.shop+']'):'')+
        '<button class="sb" onclick="shopPrev('+p+')">◀</button>'+
        '<button class="sb" onclick="shopNext('+p+')">▶</button>'+
        '<button class="sb hi" onclick="shopBuy('+p+')">购买</button>'+
        '<button class="sb" onclick="shopDone('+p+')">完成</button>';};
    rd='<div class="sub">局前商店　积分 P1 '+j.p1.points+' / P2 '+j.p2.points+
       (so?'':'（先"进入商店"开屏上画面）')+'</div><div class="sub">'+sb(1)+
       '</div><div class="sub">'+sb(2)+'</div>';}
  el.innerHTML=rn+'　'+ph+t+'<div class="hline">局分 <b>'+j.p1.score+':'+j.p2.score+'</b>　积分 '+j.p1.points+':'+j.p2.points+'</div>'+
    '<div class="sub">弹药 P1 红'+j.p1.red+' 蓝'+j.p1.blue+' ｜ P2 红'+j.p2.red+' 蓝'+j.p2.blue+
    (j.msg?('　'+j.msg):'')+'</div>'+rd;
  if(w){if(j.link_warn){w.style.display='';
    w.textContent='链路退化（卡顿变频）——建议停游戏后 load_bit 重下 bit';}
    else{w.style.display='none';}}
  }catch(e){}}
setInterval(pullMatch,1000);pullMatch();
/* ★★ 2026-10-08 商店 v2：手机端选购按钮（act=prev/next/buy/done；done=就绪） */
async function shopPost(p,act){try{await fetch('shop?p='+p+'&act='+act,
  {cache:'no-store'});pullMatch();}catch(e){}}
function shopPrev(p){shopPost(p,'prev')}
function shopNext(p){shopPost(p,'next')}
function shopBuy(p){shopPost(p,'buy')}
function shopDone(p){shopPost(p,'done')}
function shopEnter(p){shopPost(p,'enter')}
/* ★★ 2026-10-15 加洞口 v2：手机俯视图——点位置 → 再点「确定」。
   数据链：/match 的 hole/scene/balls（游戏每 0.5s 写）→ 本地画布即时渲染；
   点按走 /hole?act=pos（游戏坐标；取哪条边/夹角距的口径由游戏统一定，
   本地只做同样的"最近边+夹紧"给即时反馈，最终以游戏判定为准）；
   确定/取消复用 E/ESC 计数器（谁发起扣谁——p 用 hole.who，不是本页玩家）。 */
const HV={active:false,who:0,edge:'top',pos:0,valid:null,stage:'',left:0,
  scene:null,balls:null,mark:null,_fast:null};
const HC=document.getElementById('hc'),HCTX=HC.getContext('2d');
const HW=960,HH=540,HK=HC.width/HW;
function g2c(x,y){return [x*HK,y*HK];}
function holeEnter(p){fetch('hole?p='+p+'&act=enter',{cache:'no-store'}).catch(e=>{});
  setTimeout(pullMatch,500);}
function placeOk(){const _pj=HV.place;if(!_pj)return;
  act(_pj.who||1,'ok');setTimeout(pullMatch,400);}
function placeNo(){const _pj=HV.place;if(!_pj)return;
  cancelAll(_pj.who||1);}
function holeOk(){fetch('hole?p='+(HV.who||1)+'&act=ok',{cache:'no-store'}).catch(e=>{});
  setTimeout(pullMatch,500);}
async function holeNo(){
  /* ★ 2026-10-31：同 cancelAll —— 加洞口 ESC 两段式（关弹窗 / 整个取消），
     手机点一次「取消」就要整个取消：连发到 /match 的 hole.active 变 false。 */
  const p=(HV.who||1);
  for(let i=0;i<4;i++){
    try{await fetch('hole?p='+p+'&act=no',{cache:'no-store'});}catch(e){}
    await new Promise(r=>setTimeout(r,500));
    const j=await _matchJ();
    if(!(j&&j.hole&&j.hole.active))break;
  }
  setTimeout(pullMatch,300);}
function holeEdgePos(gx,gy){   /* 与 _hole_from_game 同形：最近边(上/下/左/右)+夹角距 */
  const s=HV.scene||{};const W=s.W||960,H=s.H||540;
  const m=(s.params&&s.params.margin)||50;
  const d=[['top',gy],['bottom',H-gy],['left',gx],['right',W-gx]];
  d.sort(function(a,b){return a[1]-b[1];});const e=d[0][0];
  const lim=((e==='top'||e==='bottom')?W:H)-m;
  let v=(e==='top'||e==='bottom')?gx:gy;
  return [e,Math.min(lim,Math.max(m,v))];}
function holeValidAt(e,v){     /* 只差重叠一条（角距由夹紧保证——同 _hole_valid） */
  const s=HV.scene;if(!s)return true;
  const p=s.params||{mw:44,gap:12};
  for(const h of (s.holes||[])){
    if(h[0]===e&&Math.abs(v-h[1])<(+p.mw+ +h[2])/2+ +p.gap)return false;}
  return true;}
function holeSeg(e,v){         /* 边+位置 -> 画布上的线段两端 */
  const s=HV.scene||{};const W=s.W||960,H=s.H||540,w=22;
  if(e==='left')return [[0,v-w],[0,v+w]];
  if(e==='right')return [[W,v-w],[W,v+w]];
  if(e==='top')return [[v-w,0],[v+w,0]];
  return [[v-w,H],[v+w,H]];}
function holeDraw(){
  HCTX.setTransform(1,0,0,1,0,0);
  HCTX.clearRect(0,0,HC.width,HC.height);
  HCTX.fillStyle='#101216';HCTX.fillRect(0,0,HC.width,HC.height);
  const s=HV.scene;if(!s)return;
  HCTX.strokeStyle='#ff9f40';HCTX.lineWidth=2;
  HCTX.strokeRect(1,1,HC.width-2,HC.height-2);
  for(const b of (s.blocks||[])){
    const x0=b[0]*HK,y0=b[1]*HK,x1=b[2]*HK,y1=b[3]*HK,bv=+b[4];
    HCTX.fillStyle=bv>=0?'rgba(70,120,200,0.16)':'rgba(200,70,70,0.16)';
    HCTX.fillRect(x0,y0,x1-x0,y1-y0);
    HCTX.strokeStyle='rgba(150,150,150,0.45)';HCTX.lineWidth=1;
    HCTX.strokeRect(x0,y0,x1-x0,y1-y0);
    HCTX.fillStyle=bv>=0?'#7fb0ff':'#ff9c9c';
    HCTX.font='11px system-ui,sans-serif';HCTX.textAlign='center';
    HCTX.textBaseline='middle';
    HCTX.fillText('B'+(bv>=0?'+':'')+bv.toFixed(1),(x0+x1)/2,(y0+y1)/2);
  }
  HCTX.strokeStyle='#2ecc71';HCTX.lineWidth=7;HCTX.lineCap='round';
  for(const h of (s.holes||[])){
    const seg=holeSeg(h[0],+h[1]);
    const p1=g2c(seg[0][0],seg[0][1]),p2=g2c(seg[1][0],seg[1][1]);
    HCTX.beginPath();HCTX.moveTo(p1[0],p1[1]);HCTX.lineTo(p2[0],p2[1]);HCTX.stroke();
  }
  for(const b of (HV.balls||[])){
    const c=g2c(+b[0],+b[1]);HCTX.beginPath();HCTX.arc(c[0],c[1],5,0,6.2832);
    HCTX.fillStyle=(+b[2]===1)?'#e74c3c':((+b[2]===-1)?'#3b82f6':'#9aa4b2');
    HCTX.fill();HCTX.strokeStyle='rgba(255,255,255,0.6)';HCTX.lineWidth=1.5;HCTX.stroke();
  }
  let e=HV.edge,v=+HV.pos,ok=HV.valid,fromTap=false;
  if(HV.mark){e=HV.mark[0];v=HV.mark[1];ok=HV.mark[2];fromTap=true;}
  if(e&&v!=null&&isFinite(v)){
    const seg=holeSeg(e,v);
    const p1=g2c(seg[0][0],seg[0][1]),p2=g2c(seg[1][0],seg[1][1]);
    HCTX.strokeStyle=(ok===false)?'#ff5252':'#ffe066';HCTX.lineWidth=8;
    HCTX.beginPath();HCTX.moveTo(p1[0],p1[1]);HCTX.lineTo(p2[0],p2[1]);HCTX.stroke();
    if(fromTap){   /* 点 -> 落边：虚线告诉玩家"取的最近边" */
      const c=g2c(HV.mark[3],HV.mark[4]);
      HCTX.setLineDash([4,4]);HCTX.strokeStyle='rgba(255,224,102,0.75)';
      HCTX.lineWidth=1.5;HCTX.beginPath();HCTX.moveTo(c[0],c[1]);
      HCTX.lineTo((p1[0]+p2[0])/2,(p1[1]+p2[1])/2);HCTX.stroke();
      HCTX.setLineDash([]);
      HCTX.beginPath();HCTX.arc(c[0],c[1],3,0,6.2832);
      HCTX.fillStyle='#ffe066';HCTX.fill();
    }
  }
}
function holeTap(ev){
  if(!HV.active)return;
  ev.preventDefault();
  const r=HC.getBoundingClientRect();
  const gx=(ev.clientX-r.left)*HW/r.width;
  const gy=(ev.clientY-r.top)*HH/r.height;
  const ep=holeEdgePos(gx,gy);
  HV.mark=[ep[0],ep[1],holeValidAt(ep[0],ep[1]),gx,gy];
  HV.edge=ep[0];HV.pos=ep[1];HV.valid=HV.mark[2];
  holeDraw();
  fetch('hole?p='+(HV.who||1)+'&act=pos&x='+gx.toFixed(1)+'&y='+gy.toFixed(1),
        {cache:'no-store'}).catch(e=>{});
  setTimeout(pullMatch,400);
}
HC.addEventListener('pointerdown',holeTap);
function holeFromMatch(j){
  /* ★★ 2026-10-31：实时摆放条（加洞口/障碍/传送门）—— /match 的 place 字段驱动。
     以前这些流程手机完全看不到，只能摸键盘 E。 */
  const _pb=document.getElementById('placebar'),_pj=(j&&j.place)||null;
  if(_pj&&_pj.on){
    HV.place=_pj;_pb.style.display='';
    const _kn={hole:'加洞口',obstacle:'加障碍',portal:'传送门'}[_pj.kind]||'布置';
    const _wh=(_pj.who===2)?'选手二':'选手一';
    document.getElementById('ptxt').textContent=_wh+' · '+_kn+
      ((_pj.confirm&&_pj.edge)
        ?('：已识别 '+_pj.edge+' '+Math.round(_pj.pos)+' —— 点「确定」生效')
        :'：把黑球贴到场区边框上…');
  }else{HV.place=null;_pb.style.display='none';}
  const w=document.getElementById('holewrap');
  const hj=(j&&j.hole)?j.hole:null;
  if(j&&j.scene)HV.scene=j.scene;
  if(j&&j.balls)HV.balls=j.balls;
  HV.active=!!(hj&&hj.active);
  if(hj){HV.who=hj.who||0;HV.edge=hj.edge;HV.pos=hj.pos;
    HV.valid=hj.valid;HV.stage=hj.stage||'';HV.left=hj.left||0;}
  const canHole=!!(j&&j.on&&j.phase==='PLAY'&&!HV.active);
  const b1=document.getElementById('hb1'),b2=document.getElementById('hb2');
  if(b1)b1.disabled=!canHole;if(b2)b2.disabled=!canHole;
  if(HV.active){
    if(w.style.display==='none'){w.scrollTop=0;}   /* ★ 刚弹出：滚到顶部，别停在半截 */
    w.style.display='';
    const who=(HV.who===1)?'选手一':'选手二';
    document.getElementById('htitle').textContent=
      '加洞口 · '+who+'（剩 '+Math.ceil(HV.left)+'s'+
      ((HV.stage==='confirm')?' · 待确定':'')+'）';
    document.getElementById('hstat').textContent=
      (HV.stage==='confirm')
        ? ((HV.valid===false)
            ?'这个位置放不下（太靠角/与已有洞口重叠）——再点别处'
            :'已定位置——点「✔ 确定放洞」（再点别处可挪）')
        : '点图上位置 = 洞口预览（自动取最近的一条边）';
    if(HV.mark&&HV.edge===HV.mark[0]&&Math.abs((+HV.pos)-HV.mark[1])<1.5)HV.mark=null;
    if(HV.stage==='confirm'&&HV.valid)HV.mark=null;
    /* ★★ 2026-10-31：确定键只在"已选位置"后可点（没选就灰着，防止误点白扣额度） */
    const _okb=document.getElementById('hok');
    if(_okb)_okb.disabled=!(((HV.stage==='confirm')||!!HV.mark)&&HV.valid!==false);
    holeDraw();
    if(!HV._fast)HV._fast=setInterval(pullMatch,450);   /* 俯视图开着 -> 加密轮询 */
  }else{
    HV.mark=null;
    w.style.display='none';
    if(HV._fast){clearInterval(HV._fast);HV._fast=null;}
  }
}
</script></body></html>
"""


def _lan_ip():
    """拿板子在同一网段里的 IP（给手机上敲用）。不发任何包：UDP connect 只选路由。"""
    import socket
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(('8.8.8.8', 53))
        ip = sk.getsockname()[0]
        sk.close()
        return ip
    except Exception:
        return '127.0.0.1'


def cmd_serve(argv):
    """手机网页遥控：标准库 http.server，无依赖、无框架、无鉴权（局域网内用）。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs
    port, host, path = 8080, '0.0.0.0', None
    for i, a in enumerate(argv):
        if a == '--port' and i + 1 < len(argv):
            port = int(argv[i + 1])
        if a == '--host' and i + 1 < len(argv):
            host = argv[i + 1]
        if a == '--path' and i + 1 < len(argv):
            path = argv[i + 1]

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):      # 默认每请求一行日志会把长跑刷爆
            pass

        def _send(self, code, body, ctype):
            b = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path in ('/', '/index.html'):
                    return self._send(200, _PAGE, 'text/html; charset=utf-8')
                if u.path == '/state':
                    st = read_state(path)
                    if st is None:
                        return self._send(200, b'{}', 'application/json')
                    return self._send(200, json.dumps(st),
                                      'application/json; charset=utf-8')
                if u.path == '/set':
                    old = read_state(path) or {}
                    pl = int(q.get('p', ['1'])[0] or 1)      # ★ 双人：p=1/2
                    if pl == 2:
                        a = q.get('angle', [old.get('p2_angle', ANG_DEF)])[0]
                        s = q.get('speed', [old.get('p2_speed', SPD_DEF)])[0]
                    else:
                        a = q.get('angle', [old.get('angle', ANG_DEF)])[0]
                        s = q.get('speed', [old.get('speed', SPD_DEF)])[0]
                    bl = q.get('ball', [None])[0]      # ★ 选球（可缺省=保持）
                    o = write_state(angle=a, speed=s, src='phone', path=path,
                                    player=pl, ball=bl)
                    print('[遥控] 手机 P%d -> %.1f° / %.0f px/s（控球%d）'
                          % (pl, o['p2_angle'] if pl == 2 else o['angle'],
                             o['p2_speed'] if pl == 2 else o['speed'],
                             o['p2_ball'] if pl == 2 else o['p1_ball']),
                          flush=True)
                    return self._send(200, json.dumps(o),
                                      'application/json; charset=utf-8')
                if u.path == '/fire':
                    # ★ 2026-10-01 深夜：手机上的"发射"按钮（角度/速度一拍写下去
                    #   = 快照一致，fire 计数 +1，游戏读上升沿）。
                    # ★ 2026-10-05 双人：p=1/2 各发各的（p2 动 p2_fire 计数）。
                    old = read_state(path) or {}
                    pl = int(q.get('p', ['1'])[0] or 1)
                    if pl == 2:
                        a = q.get('angle', [old.get('p2_angle', ANG_DEF)])[0]
                        s = q.get('speed', [old.get('p2_speed', SPD_DEF)])[0]
                    else:
                        a = q.get('angle', [old.get('angle', ANG_DEF)])[0]
                        s = q.get('speed', [old.get('speed', SPD_DEF)])[0]
                    o = write_state(angle=a, speed=s, src='phone', path=path,
                                    fire=True, player=pl)
                    print('[遥控] 手机 P%d **发射**（角 %.1f° 速度 %.0f，fire=%s）'
                          % (pl, o['p2_angle'] if pl == 2 else o['angle'],
                             o['p2_speed'] if pl == 2 else o['speed'],
                             o.get('p2_fire') if pl == 2 else o.get('fire')),
                          flush=True)
                    return self._send(200, json.dumps(o),
                                      'application/json; charset=utf-8')
                if u.path == '/shot':
                    # 最近一杆的结果（**游戏写的**，不是输入）——手机轮询这个显示。
                    # ★ 2026-10-05 双人：/shot?p=N 取该玩家那格；旧的单块格式
                    #   （顶层有 msg）在 p1 时原样返回（兼容）。
                    st = read_state(SHOT_PATH)
                    if st is None:
                        return self._send(200, b'{}', 'application/json')
                    pl = int(q.get('p', ['1'])[0] or 1)
                    blk = None
                    if isinstance(st, dict):
                        if isinstance(st.get('p%d' % pl), dict):
                            blk = st['p%d' % pl]
                        elif pl == 1 and 'msg' in st:
                            blk = st
                    return self._send(200, json.dumps(blk or {}, ensure_ascii=False),
                                      'application/json; charset=utf-8')
                if u.path == '/balls':
                    # ★ 2026-10-06（用户："网页应该实时刷新有几个球的序号"）：
                    #   游戏写的"场上球列表"（号+颜色）；文件不在/坏 -> 空表。
                    return self._send(200, json.dumps(
                        {'balls': read_balls()}, ensure_ascii=False),
                        'application/json; charset=utf-8')
                if u.path == '/match':
                    # ★ 2026-10-07 M2.3：对局状态（局/比分/弹药/倒计时/就绪/链路健康）；
                    #   文件不在/坏 -> {}（网页整块隐藏）。
                    return self._send(200, json.dumps(
                        read_match(), ensure_ascii=False),
                        'application/json; charset=utf-8')
                if u.path == '/shop':
                    # ★★ 2026-10-08 商店 v2：手机端选购。
                    #   act=next/prev（挪选单，同 W/S·↑↓）、buy（购买）、
                    #   done（完成=就绪——与键盘空格同一条 fire 上升沿）。
                    pl = int(q.get('p', ['1'])[0] or 1)
                    act = str(q.get('act', [''])[0] or '')
                    if act in ('next', 'prev'):
                        kw = dict(nav=(1 if act == 'next' else -1))
                    elif act == 'buy':
                        kw = dict(buy=True)
                    elif act == 'enter':
                        kw = dict(sopen=True)     # ★ 商店 v2：进入商店画面
                    elif act == 'done':
                        kw = dict(fire=True)
                    else:
                        return self._send(400, 'act?', 'text/plain; charset=utf-8')
                    o = write_state(player=pl, src='phone', path=path, **kw)
                    print('[遥控] 手机 P%d 商店 %s' % (pl, act), flush=True)
                    return self._send(200, json.dumps(o),
                                      'application/json; charset=utf-8')
                if u.path == '/hole':
                    # ★★ 2026-10-15 加洞口 v2（手机俯视图，对话6 加）：
                    #   act=enter 发起加洞口（=键盘 C / 小键盘+，谁发起扣谁）、
                    #   act=pos&x&y 点位置（游戏坐标 960x540，走 p{N}_hopos/hx/hy
                    #   计数通道；坐标不在这层夹——落边/夹角距由游戏的
                    #   _hole_from_game 统一定夺，两处夹法不许有两套）、
                    #   act=ok 确定（=E）、act=no 取消（=ESC）。
                    pl = int(q.get('p', ['1'])[0] or 1)
                    act = str(q.get('act', [''])[0] or '')
                    if act == 'enter':
                        kw = dict(hole=True)
                    elif act == 'ok':
                        kw = dict(buy=True)
                    elif act == 'no':
                        kw = dict(esc=True)
                    elif act == 'pos':
                        try:
                            hx = float(q.get('x', ['0'])[0])
                            hy = float(q.get('y', ['0'])[0])
                        except Exception:
                            return self._send(400, 'x/y?',
                                              'text/plain; charset=utf-8')
                        kw = dict(hx=hx, hy=hy, hopos=True)
                    else:
                        return self._send(400, 'act?', 'text/plain; charset=utf-8')
                    o = write_state(player=pl, src='phone', path=path, **kw)
                    print('[遥控] 手机 P%d 加洞口 %s' % (pl, act), flush=True)
                    return self._send(200, json.dumps(o),
                                      'application/json; charset=utf-8')
                if u.path == '/shutdown':
                    # ★ 2026-10-30 用户要求：手机**安全关机**（SD 卡曾被断电写坏）。
                    # ★★ 2026-10-31 实锤修复：原来先 `systemctl stop magnetic-demo`
                    #   再 sync/poweroff —— 但这个 HTTP 服务本身就在 magnetic-demo
                    #   的 cgroup 里，stop 会 SIGTERM 掉整组进程，**连带杀掉后面的
                    #   sleep/sync/poweroff** ⇒ 游戏停了、机器永远不断电（用户手机
                    #   点了关机却拔不了电）。现在改为：先 sync，再直接把 poweroff
                    #   任务交给 PID1（--no-block 立即返回）；停服务由关机流程自己
                    #   完成，不再由本脚本亲手杀自己。
                    print('[遥控] ★ 收到安全关机请求', flush=True)
                    os.system('nohup bash -c "sync; sleep 1; sync; '
                              'systemctl --no-block poweroff" '
                              '>/dev/null 2>&1 &')
                    return self._send(200, 'shutting down', 'text/plain; charset=utf-8')
                if u.path == '/act':
                    # ★ 2026-10-30：手机替代键盘的其余动作（改磁场/粒子/障碍/
                    #   传送门/加球/ESC/数字/退格）。a=动作名，与键盘同一套语义。
                    pl = int(q.get('p', ['1'])[0] or 1)
                    a = str(q.get('a', [''])[0] or '')
                    kw = None
                    if a == 'edit':
                        kw = dict(edit=True)
                    elif a == 'pe':
                        kw = dict(pe=True)
                    elif a == 'obs':
                        kw = dict(obs=True)
                    elif a == 'ppl':
                        kw = dict(ppl=True)
                    elif a == 'aball':
                        kw = dict(aball=True)
                    elif a == 'ok':
                        # ★ 2026-10-31：手机「确定」= 键盘 E / 小键盘*（同一个 buy 计数）
                        #   改磁场三步确认 / 加障碍 / 传送门 的确认键都是它。
                        kw = dict(buy=True)
                    elif a == 'esc':
                        kw = dict(esc=True)
                    elif a == 'bksp':
                        kw = dict(nbksp=True)
                    elif a == 'num':
                        try:
                            n = int(q.get('n', ['0'])[0])
                        except Exception:
                            return self._send(400, 'n?', 'text/plain; charset=utf-8')
                        kw = dict(num=n)
                    elif a == 'dot':
                        # 小数点：游戏侧固定读 p2_sup 当 '.'（ball_game 的 nddot）
                        o = write_state(player=2, sel=1, src='phone', path=path)
                        print('[遥控] 小数点', flush=True)
                        return self._send(200, json.dumps(o),
                                          'application/json; charset=utf-8')
                    else:
                        return self._send(400, 'a?', 'text/plain; charset=utf-8')
                    o = write_state(player=pl, src='phone', path=path, **kw)
                    print('[遥控] 手机 P%d 动作 %s' % (pl, a), flush=True)
                    return self._send(200, json.dumps(o),
                                      'application/json; charset=utf-8')
                return self._send(404, 'no such path', 'text/plain; charset=utf-8')
            except Exception as e:
                return self._send(500, 'ERR %s' % e, 'text/plain; charset=utf-8')

    # ★★ 起服务时**不写**任何状态（第一版写了个 src='serve-boot' 的 0°/400 占位。
    #   那是个假数据：没人动过滑条，文件里却"有"一个值 —— 游戏一扫开局就把
    #   命令行给的 `ANGLE=10` 悄悄盖成 0°，现场只会看到"参数不生效"这种最难查的
    #   现象。现在：**没人动过就没有文件**，页面上如实显示"还没人写过"。
    srv = ThreadingHTTPServer((host, port), H)
    print('[遥控] 手机浏览器打开： http://%s:%d/' % (_lan_ip(), port), flush=True)
    print('[遥控] 状态文件：%s（和键盘共用同一份）' % (path or PATH_DEFAULT), flush=True)
    print('[遥控] 板子要和手机连同一个 WiFi；Ctrl+C 退出', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('[遥控] 收到 Ctrl+C，退出', flush=True)
    finally:
        srv.server_close()
    return 0


# ---- ② 有线键盘（/dev/input/eventX）------------------------------------------
#  ★ 内置的输入子系统是编进内核的（CONFIG_INPUT/EVDEV/HID/USB_HID 全是 =y），
#    内核里 usbhid 也注册着 —— 插上键盘就该出 /dev/input/event*，本文件只管读。
#
#  ★★ 2026-10-06 M2.1：**双人键位**（规则书 v4 §6.1"左右分区、零重叠"；选球键
#     按板上语义 = 循环"控球编号"）。一把键盘两人用：
#       P1（左手区）：S/W 角度±2°、A/D 速度±20、空格=发射、Z=上一颗球/X=下一颗、
#                     R 复位、Q/ESC 退出（Q 以后让给"改磁场"，届时挪退出键）
#       P2（右手区）：↓/↑ 角度±2°、←/→ 速度±20、回车（含小键盘回车）=发射、
#                     ,=上一颗球 / .=下一颗
#     ★ 变更：**方向键从"P1 别名"正式划归 P2**（旧台架两条断言同步改 ——
#       改键位先搜台架，铁律）。
KEY2 = {17: (1, 'ang', 2.0), 31: (1, 'ang', -2.0),          # W / S
        32: (1, 'spd', 20.0), 30: (1, 'spd', -20.0),        # D / A
        103: (2, 'ang', 2.0), 108: (2, 'ang', -2.0),        # ↑ / ↓
        106: (2, 'spd', 20.0), 105: (2, 'spd', -20.0),      # → / ←
        57: (1, 'fire', None), 28: (2, 'fire', None),       # 空格 / 回车
        96: (2, 'fire', None),                              # 小键盘回车
        44: (1, 'sel', -1), 45: (1, 'sel', 1),              # Z 上一颗 / X 下一颗
        51: (2, 'sel', -1), 52: (2, 'sel', 1),              # , 上一颗 / . 下一颗
        19: (1, 'reset', None),                             # R 复位
        # ★★ 2026-10-08 道具系统（规则书 §6.2 局前）：购买键。P1=E、P2=小键盘 *。
        #   搭配"同键双语义"：W/S/↑↓ 局中仍是角度键，局前同一批键同时记
        #   p{N}_mup/mdn 菜单计数器（见 apply_key/write_state）——互不干扰。
        #   ★★ 当日实况修（用户"按 * 没反应"）：小键盘 * 的 Linux 码 = **55
        #   (KEY_KPASTERISK)**；原来写的 98 其实是**小键盘 /** —— 抓包实锤：
        #   按 * 出码 55、映射表里没有 -> 整条路静默。98 不再映射（留给 §6.4
        #   改磁场时用）。
        18: (1, 'buy', None), 55: (2, 'buy', None),          # E / 小键盘 *
        # ★★ 2026-10-14 局中道具（规则书 §6.4/§7.1）：
        #   Q / 小键盘. = 改磁场（选块/切阶段）；C / 小键盘+ = 加洞口（切边）；
        #   ESC = 取消当前暂停操作（全局信号，不再是"退出读取器"——Q 让位给
        #   改磁场，规则书 §6.5 原话："ESC 在改磁场/加洞口里第一次按是取消该操作"）。
        16: (1, 'edit', None), 83: (2, 'edit', None),        # Q / 小键盘.
        46: (1, 'hole', None), 78: (2, 'hole', None),
        # ★ 2026-10-16（用户："加洞口我怎么切换选手一还是选手二"）：入口要显眼、好按——
        #   P2 的加洞口除小键盘+ 再补一个**主键盘 / **（就在 P2 选球键 , . 旁边，
        #   和小键盘+ 完全等价；53 此前无映射，无冲突）。
        53: (2, 'hole', None),                               # C=P1 / 小键盘+ 或 / =P2
        # ★★ 2026-10-16 粒子引擎（档3）风暴触发：B=P1 / N=P2（各自从控球位放一簇粒子）
        48: (1, 'pe', None), 49: (2, 'pe', None),            # B / N
        # ★ 2026-10-19 布置 PLACE v1：障碍摆放触发 V=47(P1) / 小键盘 - =74(P2)
        #   （局前有效；与商店/道具键无冲突——47/74 此前无映射）
        47: (1, 'obs', None), 74: (2, 'obs', None),          # V / 小键盘-
        # ★ 2026-10-25 实体传送门摆放：G=34(P1) / 小键盘 7 =71(P2)
        #   （与障碍摆放 V/小键盘- 同一套流程；34/71 此前无映射）
        34: (1, 'ppl', None), 71: (2, 'ppl', None),          # G / 小键盘7
        # ★ 2026-10-25 局中加球：F=33(P1) / 小键盘 8 =72(P2)
        #   （消耗 1 个「加球」道具，立即红+1 蓝+1；局前商店仍用 E/* 购买）
        33: (1, 'aball', None), 72: (2, 'aball', None),      # F / 小键盘8
        # ★ 2026-10-25 实况（用户"按8没反应"）：补**主键盘 8** 作 P2 别名
        #   （屏上提示写的是 8加球，不写"小键盘"会被按主键盘 8）
        9: (2, 'aball', None),                               # 主键盘 8（P2 别名）
        # ★★ 2026-10-29 改磁场数值输入：数字键（全局计数 nd0..nd9）+ 退格。
        #   顶排 1..0：码 2..11（8=码9 已占作 P2 加球别名 -> 由游戏在 EDIT 里
        #   把 aball2 上升沿当数字 8；小键盘 7/8=码71/72 同理当 7/8）。
        2: (0, 'num', 1), 3: (0, 'num', 2), 4: (0, 'num', 3),
        5: (0, 'num', 4), 6: (0, 'num', 5), 7: (0, 'num', 6),
        8: (0, 'num', 7), 10: (0, 'num', 9), 11: (0, 'num', 0),
        75: (0, 'num', 4), 76: (0, 'num', 5), 77: (0, 'num', 6),
        79: (0, 'num', 1), 80: (0, 'num', 2), 81: (0, 'num', 3),
        73: (0, 'num', 9), 82: (0, 'num', 0),
        14: (0, 'nbksp', None),                              # 退格
        1: (1, 'esc', None)}                                 # ESC 取消
KEY_NAME = {17: 'W', 31: 'S', 32: 'D', 30: 'A', 103: '↑', 108: '↓',
            106: '→', 105: '←', 57: '空格', 28: '回车', 96: '小键盘回车',
            44: 'Z', 45: 'X', 51: ',', 52: '.', 53: '/', 19: 'R', 16: 'Q',
            1: 'ESC',
            18: 'E', 55: '小键盘*', 16: 'Q', 83: '小键盘.', 46: 'C',
            78: '小键盘+', 48: 'B', 49: 'N', 47: 'V', 74: '小键盘-',
            34: 'G', 71: '小键盘7', 33: 'F', 72: '小键盘8', 9: '8'}
EV_KEY, EV_SYN = 1, 0


def key_route(code):
    """键码 -> (player, action, delta)；不认识的返回 None。双人全套键位表。"""
    return KEY2.get(code)


def key_apply(angle, speed, code):
    """【P1 单体口径·兼容保留】键码作用到 P1 的 (angle, speed)，返回
    (angle, speed, act)。act = 'set'/'fire'/'reset'/'quit'/'sel'/None。
    ★ 只服务 P1；P2 与选球的**整份状态**写法走 apply_key()。
      台架老断言吃这一份；键位变更后 ↑/←（P2）在这里返回 act=None。
    """
    r = KEY2.get(code)
    if r is None:
        return angle, speed, None
    pl, act, d = r
    if pl != 1:
        return angle, speed, None
    if act == 'reset':
        return ANG_DEF, SPD_DEF, 'reset'
    # ★ 2026-10-14：edit/hole/esc（局中道具）同透传；'quit' 已让位给 edit（ESC 改
    #   为全局取消信号），保留分支仅为老断言兼容（现在没有键映射到 quit）。
    if act in ('quit', 'fire', 'sel', 'buy', 'edit', 'hole', 'esc',
               'num', 'nbksp'):
        return angle, speed, act
    if act == 'ang':
        return clamp_angle(angle + d), speed, 'set'
    if act == 'spd':
        return angle, clamp_speed(speed + d), 'set'
    return angle, speed, None


def apply_key(st, code):
    """★ 2026-10-06 M2.1（纯函数，可单测）：把键码作用到**整份状态字典**。

    返回 (patch, act, pl)：patch = 交给 write_state 的 kwargs（player/angle/
    speed/fire/sel）；act ∈ {'set','fire','reset','sel','quit'}；pl = 1/2。
    不认识的键 -> (None, None, None)。
    """
    r = KEY2.get(code)
    if r is None:
        return None, None, None
    pl, act, d = r
    if act == 'quit':
        return None, 'quit', pl
    if act == 'fire':
        return dict(player=pl, fire=True), 'fire', pl
    if act == 'reset':
        return dict(player=pl, angle=ANG_DEF, speed=SPD_DEF), 'reset', pl
    if act == 'sel':
        return dict(player=pl, sel=int(d)), 'sel', pl
    if act == 'buy':
        return dict(player=pl, buy=True), 'buy', pl
    if act == 'edit':
        return dict(player=pl, edit=True), 'edit', pl
    if act == 'hole':
        return dict(player=pl, hole=True), 'hole', pl
    if act == 'esc':
        return dict(esc=True), 'esc', pl
    if act == 'num':
        return dict(num=int(d)), 'num', 0
    if act == 'nbksp':
        return dict(nbksp=True), 'nbksp', 0
    if act == 'pe':
        return dict(player=pl, pe=True), 'pe', pl
    if act == 'obs':
        return dict(player=pl, obs=True), 'obs', pl
    if act == 'ppl':
        return dict(player=pl, ppl=True), 'ppl', pl
    if act == 'aball':
        return dict(player=pl, aball=True), 'aball', pl
    ka = 'p2_angle' if pl == 2 else 'angle'
    ks = 'p2_speed' if pl == 2 else 'speed'
    if act == 'ang':
        cur = clamp_angle(st.get(ka, ANG_DEF))
        if cur is None:
            cur = ANG_DEF
        # ★ 同键双语义（2026-10-08 道具系统）：角度键同时记一份菜单导航计数
        #   （局前选购用；局中游戏不看它，零干扰）。nav=±1 走 write_state 的
        #   p{N}_mup/mdn 计数器。
        return (dict(player=pl, angle=clamp_angle(cur + d),
                     nav=(1 if d > 0 else -1)), 'set', pl)
    if act == 'spd':
        cur = clamp_speed(st.get(ks, SPD_DEF))
        if cur is None:
            cur = SPD_DEF
        # ★ 同键双语义（2026-10-14 局中道具）：A/D（←/→）在 EDIT-ADJUST 里是
        #   "强度 ∓" 的增量——同拍记一份 p{N}_aup/adn 计数器（同 ang 的 nav）。
        return (dict(player=pl, speed=clamp_speed(cur + d),
                     nav2=(1 if d > 0 else -1)), 'set', pl)
    return None, None, None


def key_event(path, code, note='', fallback=None):
    """★ 2026-10-07（实锤修）：键盘的**一笔事件** -> 写到状态文件。返回 (cur, act, pl)。

    ★★ 基准必须是**文件现值**。现场实锤：手机把角度调到 20°（/set 一直是"读文件现值
      当基准"，见 cmd_serve），键盘再按一下 W 却跳回 18°——因为 cmd_key 原来拿**进程
      启动快照 + 自己写过的值**当基准，两个源各算各的、后写者把对方设的值带走
      （两人一人键盘一人手机时最明显："我设好的角度，他按一下就被带回旧值"）。
      现在统一口径：**谁都基于文件当前值**做相对调整（clamp 后原子写回）。
      `fallback` = 读不到文件时的兜底（用进程内最后一次的状态，别退回默认把玩家吓一跳）。
      `note` = 写进状态文件的按键名（网页/排查用）。
    """
    cur = read_state(path)
    if cur is None:
        cur = fallback if isinstance(fallback, dict) else {}
    patch, act, pl = apply_key(cur, code)
    if patch is None and act is None:
        return None, act, pl
    cur = write_state(src='key', path=path, note=note, **patch)
    return cur, act, pl


def _find_keyboards():
    """列出候选输入设备：(path, name, 是不是键盘)。找键盘靠名字里有没有 kbd/keyboard。"""
    import fcntl
    out = []
    for p in sorted(os.listdir('/dev/input')) if os.path.isdir('/dev/input') else []:
        if not p.startswith('event'):
            continue
        fp = '/dev/input/' + p
        try:
            fd = os.open(fp, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            out.append((fp, '（打不开：%s）' % e, False))
            continue
        try:
            buf = bytearray(256)
            fcntl.ioctl(fd, 0x80FF4506, buf, True)      # EVIOCGNAME(256)
            name = buf.split(b'\0')[0].decode('utf-8', 'replace')
        except Exception:
            name = '?'
        finally:
            os.close(fd)
        low = name.lower()
        out.append((fp, name, ('kbd' in low) or ('keyboard' in low) or ('键盘' in name)))
    return out


def _find_mice():
    """候选**鼠标**设备：(path, name)。名字含 mouse 且**不含 keyboard**——
    后半句是实锤：Telink 接收器有个 'Keyboard Mouse' 幻影接口（名字带 mouse、
    晃鼠标零事件），用户的真鼠标（YJX ML MX301）才作数。扫不到就只当键盘用。"""
    import fcntl
    out = []
    for p in sorted(os.listdir('/dev/input')) if os.path.isdir('/dev/input') else []:
        if not p.startswith('event'):
            continue
        fp = '/dev/input/' + p
        try:
            fd = os.open(fp, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            buf = bytearray(256)
            fcntl.ioctl(fd, 0x80FF4506, buf, True)      # EVIOCGNAME(256)
            name = buf.split(b'\0')[0].decode('utf-8', 'replace')
        except Exception:
            name = '?'
        finally:
            os.close(fd)
        low = name.lower()
        # ★ 2026-10-10：'receiver' 也认——用户的 367B 接收器名字就叫
        #   "USB Receiver USB Receiver"，只有类名没有 mouse 字样（实锤漏配过一次）。
        if ('mouse' in low or '鼠标' in name or 'receiver' in low)                 and ('keyboard' not in low):
            out.append((fp, name))
    return out


def cmd_key(argv):
    """键盘 + 鼠标：读 evdev 事件 -> 写同一份状态文件。要 root（/dev/input 是 root:input）。"""
    import select
    import struct
    dev, path, grab = None, None, False
    mdev, mgain = None, MOUSE_GAIN
    for i, a in enumerate(argv):
        if a == '--dev' and i + 1 < len(argv):
            dev = argv[i + 1]
        if a == '--path' and i + 1 < len(argv):
            path = argv[i + 1]
        if a == '--grab':
            grab = True
        if a == '--dev-mouse' and i + 1 < len(argv):
            mdev = argv[i + 1]
        if a == '--mouse-gain' and i + 1 < len(argv):
            try:
                mgain = float(argv[i + 1])
            except Exception:
                pass

    cands = _find_keyboards()
    print('[键盘] 扫到的输入设备（%d 个）：' % len(cands), flush=True)
    for fp, nm, isk in cands:
        print('        %s  %s  %s' % (fp, '★键盘' if isk else '     ', nm), flush=True)
    if dev is None:
        pick = [c for c in cands if c[2]]
        if not pick:
            print('[键盘] ★ 没扫到键盘。/dev/input 下有设备的话用 --dev 指定；'
                  '一个都没有就是**键盘没插上/没枚举**（先看 lsusb 有没有它）', flush=True)
            return 9
        dev = pick[0][0]
        print('[键盘] 选：%s（%s）—— 不对就用 --dev 指定' % (dev, pick[0][1]), flush=True)

    fd = os.open(dev, os.O_RDONLY)
    if grab:
        try:
            import fcntl
            fcntl.ioctl(fd, 0x4004551B, 1)              # EVIOCGRAB
            print('[键盘] 已独占（EVIOCGRAB）：敲键不会漏到 shell', flush=True)
        except Exception as e:
            print('[键盘] 独占失败（%s）—— 继续，只是敲键也会被 shell 看到' % e, flush=True)

    # ★★ 2026-10-08/09：鼠标。优先 evdev；被系统锁死（EBUSY，见 parse_hid_mouse
    #   头注）就自动回落到 HID-debugfs 旁路（后台线程，不挡键盘）。
    mfd = None
    if mdev is not None:
        try:
            mfd = os.open(mdev, os.O_RDONLY | os.O_NONBLOCK)
            print('[鼠标] 用 --dev-mouse 指定：%s' % mdev, flush=True)
        except OSError as e:
            print('[鼠标] --dev-mouse 打不开（%s）' % e, flush=True)
    else:
        mc = _find_mice()
        print('[鼠标] 扫到 %d 个候选：%s'
              % (len(mc), ['%s(%s)' % c for c in mc]), flush=True)
        if mc:
            try:
                mfd = os.open(mc[0][0], os.O_RDONLY | os.O_NONBLOCK)
                print('[鼠标] 选：%s（%s）—— 不对就用 --dev-mouse 指定'
                      % (mc[0][0], mc[0][1]), flush=True)
            except OSError as e:
                print('[鼠标] evdev 打不开（%s）—— 转 HID-debugfs 旁路' % e,
                      flush=True)
        else:
            print('[鼠标] 没扫到 evdev 鼠标 —— 试 HID-debugfs 旁路', flush=True)
    #  光标状态：从文件现值接续（读取器重启不跳 (0,0)）；位移 80ms 节流写。
    _cur0 = read_state(path) or {}
    _ms = {'x': int(_cur0.get('mx', 0) or 0), 'y': int(_cur0.get('my', 0) or 0),
           'dirty': False, 'last': 0.0, 'clicks': 0, 'lb': 0}
    if mfd is None:
        _hp, _hn = _hid_mouse_dbgfs()
        if _hp:
            print('[鼠标] ★ HID-debugfs 旁路：%s（%s）' % (_hp, _hn), flush=True)

            def _hid_mouse_loop():
                while True:
                    try:
                        _f = open(_hp, 'r')
                    except Exception as e:
                        print('[鼠标] 旁路打不开（%s）—— 5s 后重试' % e,
                              flush=True)
                        time.sleep(5)
                        continue
                    print('[鼠标] 旁路已连上，等报文…', flush=True)
                    try:
                        for _ln in _f:
                            _r = parse_hid_mouse(_ln)
                            if _r is None:
                                continue
                            _dx, _dy, _bt = _r
                            _ms['x'], _ms['y'] = mouse_step(
                                _ms['x'], _ms['y'], _dx, _dy, mgain)
                            _ms['dirty'] = True
                            _nowm = time.monotonic()
                            if (_bt & 1) and not _ms['lb']:
                                _ms['clicks'] += 1
                                try:
                                    write_state(mx=_ms['x'], my=_ms['y'],
                                                mbtn=True, src='mouse', path=path)
                                except Exception:
                                    pass
                                _ms['last'] = _nowm
                                _ms['dirty'] = False
                                print('[鼠标] 左键 @ (%d,%d) 第 %d 笔'
                                      % (_ms['x'], _ms['y'], _ms['clicks']),
                                      flush=True)
                            if _ms['dirty'] and _nowm - _ms['last'] > 0.04:
                                _ms['dirty'] = False
                                _ms['last'] = _nowm
                                try:
                                    write_state(mx=_ms['x'], my=_ms['y'],
                                                src='mouse', path=path)
                                except Exception:
                                    pass
                            _ms['lb'] = _bt & 1
                    except Exception as e:
                        print('[鼠标] 旁路断流（%s）—— 2s 后重连' % e, flush=True)
                        time.sleep(2)
                    finally:
                        try:
                            _f.close()
                        except Exception:
                            pass

            threading.Thread(target=_hid_mouse_loop, daemon=True).start()
        else:
            print('[鼠标] 没找到可用的 HID-debugfs 鼠标 —— 键盘照常', flush=True)

    fmt = 'llHHi'                                       # struct input_event（64 位）
    sz = struct.calcsize(fmt)                           # = 24
    cur = read_state(path) or {}
    a = clamp_angle(cur.get('angle', ANG_DEF)) or ANG_DEF
    s = clamp_speed(cur.get('speed', SPD_DEF)) or SPD_DEF
    print('[键盘] 就绪（★ 双人键位，规则书 v4 分区）——'
          'P1: S/W 角度 A/D 速度 空格=发射 Z/X 选球 R 复位 ｜ '
          'P2: ↓/↑ 角度 ←/→ 速度 回车=发射 , / . 选球 ｜ '
          '局前商店：W/S·↑↓ 选、E/小键盘*=购买、点鼠标左键进入 ｜ '
          '局中道具：Q/小键盘. 改磁场；加洞口 **C=选手一、小键盘+ 或 /=选手二**'
          '（各自额度独立）；E/小键盘*=确认、ESC 取消（原 Q 退出已挪给改磁场）'
          '（P1 当前 %.1f° / %.0f px/s）' % (a, s), flush=True)
    n = 0
    # ★ 2026-10-06（用户实况："按住有卡顿/延迟感"）：**重复事件限频**。
    #   内核软连发 ~30/s，每个都写一遍状态文件 —— 游戏 2.1fps 采样时看到的是
    #   "一路冲到边界"（实测按住 1 秒速度就从 400 冲到 2000 上限）。点按(1)永不
    #   限频、即时生效；重复(2)同一键最多 10 次/秒 —— 按住 = 稳稳地 ±20°/s、
    #   ±200px/s 扫过去，和显示节奏对得上。
    _rep_t = {}
    _fds = [fd] + ([mfd] if mfd is not None else [])

    def _flush_mouse(force=False):
        """鼠标位移 **40ms 节流**落盘（2026-10-13：80→40——用户实况"光标迟缓"，
        写一笔 ~1.7ms，40ms 即 25 笔/秒 ≈ 0.4% CPU，换来链路延迟减半）；
        左键在事件处**立刻**写（点击是边沿，不能等节流）。"""
        now = time.monotonic()
        if not _ms['dirty']:
            return
        if not force and now - _ms['last'] < 0.04:
            return
        _ms['dirty'] = False
        _ms['last'] = now
        try:
            write_state(mx=_ms['x'], my=_ms['y'], src='mouse', path=path)
        except Exception as e:
            print('[鼠标] 写状态失败：%s' % e, flush=True)

    try:
        while True:
            r, _, _ = select.select(_fds, [], [], 1.0)
            if not r:
                _flush_mouse()                      # 停手后把最后一笔落盘
                continue
            if mfd is not None and mfd in r:
                try:
                    _mdata = os.read(mfd, sz * 256)
                except OSError:
                    _mdata = b''
                for _mo in range(0, len(_mdata) - sz + 1, sz):
                    _s2, _u2, _typ2, _cod2, _val2 = struct.unpack_from(
                        fmt, _mdata, _mo)
                    if _typ2 == 2:                  # EV_REL：相对位移
                        if _cod2 == 0:              # REL_X
                            _ms['x'], _ms['y'] = mouse_step(
                                _ms['x'], _ms['y'], _val2, 0, mgain)
                            _ms['dirty'] = True
                        elif _cod2 == 1:            # REL_Y
                            _ms['x'], _ms['y'] = mouse_step(
                                _ms['x'], _ms['y'], 0, _val2, mgain)
                            _ms['dirty'] = True
                    elif _typ2 == 1 and _cod2 == 272 and _val2 == 1:
                        _ms['clicks'] += 1          # BTN_LEFT 按下
                        try:
                            write_state(mx=_ms['x'], my=_ms['y'], mbtn=True,
                                        src='mouse', path=path)
                        except Exception:
                            pass
                        _ms['last'] = time.monotonic()
                        _ms['dirty'] = False
                        print('[鼠标] 左键 @ (%d,%d) 第 %d 笔'
                              % (_ms['x'], _ms['y'], _ms['clicks']), flush=True)
                _flush_mouse()
            if fd not in r:
                continue
            data = os.read(fd, sz * 64)
            for off in range(0, len(data) - sz + 1, sz):
                _sec, _usec, typ, code, val = struct.unpack_from(fmt, data, off)
                if typ != EV_KEY or val == 0:           # 只认按下(1)/重复(2)
                    continue
                if val == 2:
                    _nowk = time.monotonic()
                    if _nowk - _rep_t.get(code, 0.0) < 0.10:
                        continue
                    _rep_t[code] = _nowk
                elif val == 1:
                    _rep_t[code] = time.monotonic()     # 按下：重置节拍
                # ★ 2026-10-07（"手机设的值被键盘带回旧值"实锤修）：路由先判
                #   （quit / 选球长按过滤必须在**写之前**），真正的读-改-写一律走
                #   key_event —— 它按键前**先读文件现值**当基准，与手机（/set）同
                #   口径；回归网在台架 input_hub（"手机设 20° -> 键盘 W -> 22°"）。
                _r = key_route(code)
                if _r is None:
                    continue
                _act = _r[1]
                if _act == 'quit':
                    print('[键盘] 退出键 -> 收工', flush=True)
                    return 0
                if _act == 'sel' and val != 1:
                    continue                            # 选球不认长按连发（会乱转）
                # ★★ 2026-10-29 用户实况（"卡了一下 30 分买了两个洞"）：购买/确认
                #   键只认**按下沿**——长按连发(val=2)会重复购买。
                if _act == 'buy' and val != 1:
                    continue
                # ★ 2026-10-29：数字/退格也只认按下沿（长按会连打）
                if _act in ('num', 'nbksp') and val != 1:
                    continue
                _nm = KEY_NAME.get(code, '?')
                cur, act, pl = key_event(path, code, note=_nm, fallback=cur)
                if cur is None:
                    continue
                n += 1
                if act == 'fire':
                    print('[键盘] P%d 发射（%s）-> P1 %.1f°/%.0f  P2 %.1f°/%.0f'
                          '（第 %d 笔）' % (pl, _nm, cur['angle'], cur['speed'],
                                           cur['p2_angle'], cur['p2_speed'], n),
                          flush=True)
                elif act == 'sel':
                    print('[键盘] P%d 选球 %+d（%s）第 %d 笔'
                          % (pl, int(_r[2]), _nm, n), flush=True)
                else:
                    print('[键盘] P%d %-4s -> P1 %.1f°/%.0f  P2 %.1f°/%.0f'
                          '（第 %d 笔）' % (pl, _nm, cur['angle'], cur['speed'],
                                           cur['p2_angle'], cur['p2_speed'], n),
                          flush=True)
    except KeyboardInterrupt:
        print('[键盘] Ctrl+C 退出', flush=True)
        return 0
    finally:
        os.close(fd)
        if mfd is not None:
            try:
                os.close(mfd)
            except Exception:
                pass
    return 0


# ---- 命令行 ----------------------------------------------------------------
def cmd_status(argv):
    path = None
    for i, a in enumerate(argv):
        if a == '--path' and i + 1 < len(argv):
            path = argv[i + 1]
    st = read_state(path)
    print('[状态] 文件：%s' % (path or PATH_DEFAULT))
    if st is None:
        print('[状态] （还没有 / 读不出来）—— 默认就是 %.1f° / %.0f px/s'
              % (ANG_DEF, SPD_DEF))
        return 1
    age = time.time() - float(st.get('t', 0) or 0)
    print('[状态] 角度 %.1f°  速度 %.0f px/s  来源 %s  第 %s 笔  '
          '（%.1f 秒前写的）'
          % (float(st.get('angle', ANG_DEF)), float(st.get('speed', SPD_DEF)),
             st.get('src', '?'), st.get('seq', '?'), age))
    return 0


def cmd_set(argv):
    path, a, s = None, None, None
    for i, x in enumerate(argv):
        if x == '--path' and i + 1 < len(argv):
            path = argv[i + 1]
        if x == '--angle' and i + 1 < len(argv):
            a = argv[i + 1]
        if x == '--speed' and i + 1 < len(argv):
            s = argv[i + 1]
        if x == '--inc-angle' and i + 1 < len(argv):
            a = (read_state(path) or {}).get('angle', ANG_DEF) + float(argv[i + 1])
        if x == '--inc-speed' and i + 1 < len(argv):
            s = (read_state(path) or {}).get('speed', SPD_DEF) + float(argv[i + 1])
    o = write_state(angle=a, speed=s, src='cli', path=path)
    print('[状态] 写入：%.1f° / %.0f px/s（%s，第 %d 笔）'
          % (o['angle'], o['speed'], path or PATH_DEFAULT, o.get('seq', 0)))
    return 0


def selftest():
    """离线自检：坏文件/坏值/快路径/钳位 —— 不碰硬件、不碰网络。给台架和手跑都能用。"""
    import tempfile as _tf
    n = ok = 0
    d = _tf.mkdtemp(prefix='v3in_')
    p = os.path.join(d, 'st.json')

    def chk(name, cond, detail=''):
        nonlocal n, ok
        n += 1
        ok += 1 if cond else 0
        print('   %s %s %s' % ('[OK]' if cond else '[!!]', name, detail), flush=True)

    hub = InputHub(path=p, angle=7.0, speed=333.0, verbose=False)
    a, s, c = hub.poll()
    chk('文件不在 -> 保持初值', (a, s, c) == (7.0, 333.0, False), '%.1f/%.0f' % (a, s))
    chk('文件不在 -> 记一笔坏', hub.n_bad == 1, 'n_bad=%d' % hub.n_bad)

    with open(p, 'w') as f:
        f.write('{ 这不是 json')
    a, s, c = hub.poll()
    chk('坏 JSON -> 保持', (a, s, c) == (7.0, 333.0, False))
    chk('坏 JSON -> 记一笔坏', hub.n_bad == 2, 'n_bad=%d' % hub.n_bad)

    write_state(angle=12.5, speed=600, src='test', path=p)
    a, s, c = hub.poll()
    chk('正常写入 -> 读到新值', (a, s, c) == (12.5, 600.0, True), '%.1f/%.0f' % (a, s))
    a, s, c = hub.poll()
    chk('内容没变 -> 快路径（changed=False，不重复解析）', c is False)

    # ★★ 回归哨兵（2026-09-30 实测抓到过）：**紧挨着写两笔，第二笔必须读到**。
    #   第一版快路径比的是 mtime_ns，而 NTFS 延迟更新目录项时间戳 / FAT 粒度 2 秒
    #   -> 第二笔会被当成"没变"漏掉（演示时就是"按了没反应"）。比内容字节才对。
    for _v in (13.0, 14.0, 15.0):
        write_state(angle=_v, speed=600, src='burst', path=p)
        a, s, c = hub.poll()
        if (a, c) != (_v, True):
            break
    chk('紧接着写：连着三笔紧挨着写，每一笔都读到（不许漏）',
       (a, c) == (15.0, True), '%.1f/changed=%s' % (a, c))
    # 同一个值再写一遍（内容变了、值没变）：要读到、但 changed=False（不算"改了一次"）
    write_state(angle=15.0, speed=600, src='same', path=p)
    a, s, c = hub.poll()
    chk('原值重写 -> 值没变所以 changed=False，但要读到',
       (a, s, c) == (15.0, 600.0, False), '%.1f/%.0f/%s' % (a, s, c))

    write_state(angle='NaN', speed=float('inf'), src='bad', path=p)
    a, s, c = hub.poll()
    chk('NaN/inf -> 驳回保持', (a, s, c) == (15.0, 600.0, False), '%.1f/%.0f' % (a, s))

    write_state(angle=999, speed=1, src='clamp', path=p)
    a, s, c = hub.poll()
    chk('角度 360 环绕（999 -> 279）+ 速度钳位 %.0f' % SPD_MIN,
        (a, s) == (279.0, SPD_MIN))

    write_state(angle=3, src='only-angle', path=p)      # 只改角度
    a, s, c = hub.poll()
    chk('只给 angle -> 速度沿用旧值', (a, s) == (3.0, SPD_MIN))

    st = read_state(p)
    chk('seq 递增（删文件之前一共写了 8 笔）', st.get('seq') == 8, 'seq=%s' % st.get('seq'))
    chk('src 记下来了（谁写的看得出来）', st.get('src') == 'only-angle',
        str(st.get('src')))

    # ---- ★ 发射计数（2026-10-01 深夜，用户三条之③：手机要有"发射"键）----
    #   游戏读的是**上升沿**：普通写入不许动它、fire=True 才 +1、坏值保持、
    #   文件重建后归零（游戏侧按"跨过基准"处理 -> 不会误发一杆）。
    write_state(angle=5, speed=500, src='pre', path=p)
    hub.poll()
    chk('普通一笔不动 fire 计数', hub.fire == 0, 'fire=%d' % hub.fire)
    write_state(angle=5, speed=500, src='phone', path=p, fire=True)
    a, s, c = hub.poll()
    chk('fire=True -> 计数 1', hub.fire == 1, 'fire=%d' % hub.fire)
    write_state(angle=6, speed=500, src='phone', path=p, fire=True)
    a, s, c = hub.poll()
    chk('再一笔 -> 计数 2（角度也读到 6）', hub.fire == 2 and a == 6.0,
        'fire=%d a=%.1f' % (hub.fire, a))
    write_state(angle=7, speed=500, src='phone', path=p)      # 调滑条但没按发射
    a, s, c = hub.poll()
    chk('调滑条（不带 fire）-> 计数保持 2，不回落', hub.fire == 2,
        'fire=%d' % hub.fire)
    a2, s2, act = key_apply(3.0, 500.0, 57)
    chk('空格键 -> act=fire（角度/速度原样）',
        act == 'fire' and (a2, s2) == (3.0, 500.0), str(act))
    a2, s2, act = key_apply(3.0, 500.0, 19)
    chk('R 键仍是复位', act == 'reset', str(act))

    # 文件被删掉再出现：必须重新解析（第一版快路径拿 mtime 记账，这里会漏）
    os.unlink(p)
    hub.poll()
    write_state(angle=21.0, speed=777, src='back', path=p)
    a, s, c = hub.poll()
    chk('删掉又回来 -> 新值必须读到', (a, s, c) == (21.0, 777.0, True), '%.1f/%.0f' % (a, s))
    chk('删过之后 seq 从头数（文件都没了，没有历史可续）',
        read_state(p).get('seq') == 1, 'seq=%s' % read_state(p).get('seq'))
    chk('文件重建后 fire 归零（游戏侧"跨过基准" -> 不会误发射一杆）',
        hub.fire == 0, 'fire=%d' % hub.fire)

    # ---- ★★ 2026-10-05 双人（用户："网页改双人控制"）：p2_* 与 p1 完全独立 ----
    write_state(angle=10, speed=500, src='phone', path=p)      # P1 归位
    hub.poll()
    write_state(angle=-30, speed=800, src='phone', path=p, player=2)   # 只动 P2（-30° -> 330° 环绕）
    a, s, c = hub.poll()
    st2 = read_state(p)
    chk('P2 写一笔：p2 值到位、P1 一分没动',
        (st2.get('p2_angle'), st2.get('p2_speed')) == (330.0, 800.0)
        and (a, s) == (10.0, 500.0) and c is False, str(st2))
    chk('hub 读到 P2 且 changed2=True（p1 的 changed=False）',
        (hub.angle2, hub.speed2) == (330.0, 800.0) and hub.changed2 is True
        and c is False, 'a2=%.1f ch2=%s' % (hub.angle2, hub.changed2))
    write_state(angle=12, speed=500, src='phone', path=p)      # 只动 P1
    a, s, c = hub.poll()
    chk('P1 写一笔：P2 保持 330/800、changed2=False',
        (hub.angle2, hub.speed2) == (330.0, 800.0) and hub.changed2 is False
        and c is True, 'a2=%.1f ch2=%s c=%s' % (hub.angle2, hub.changed2, c))
    write_state(angle=-31, speed=800, src='phone', path=p, fire=True, player=2)
    a, s, c = hub.poll()
    chk('P2 发射一笔 -> p2_fire=1；P1 的 fire 纹丝不动',
        hub.fire2 == 1 and hub.fire == 0 and read_state(p).get('p2_fire') == 1,
        'fire2=%d fire=%d' % (hub.fire2, hub.fire))
    write_state(angle=0, speed=400, src='phone', path=p, player=1, ball=3)
    hub.poll()
    st3 = read_state(p)
    chk('★ 选球：P1 选 3 -> p1_ball=3、P2 选球保持 2',
        st3.get('p1_ball') == 3 and st3.get('p2_ball') == 2
        and hub.ball1 == 3 and hub.ball2 == 2, str(st3))

    # ---- ★ 2026-10-06（用户："网页应该实时刷新有几个球"）：/balls 的数据源 ----
    bp = os.path.join(os.path.dirname(p), 'balls.json')
    chk('球列表文件不在 -> 空表（不抛）', read_balls(bp) == [])
    with open(bp, 'w') as f:
        f.write('{"t":1,"balls":[{"no":1,"cls":"blue"},{"no":3,"cls":"red"}]}')
    chk('球列表读取（号+颜色原样）',
        read_balls(bp) == [{'no': 1, 'cls': 'blue'}, {'no': 3, 'cls': 'red'}],
        str(read_balls(bp)))
    with open(bp, 'w') as f:
        f.write('{ 坏 json')
    chk('球列表坏 JSON -> 空表（不抛）', read_balls(bp) == [])
    os.unlink(bp)

    # ---- ★ 2026-10-06 M2.1：双人键位表 + 选球计数 ----
    patch, act, pl = apply_key({'angle': 5.0, 'speed': 400.0}, 17)          # W
    chk('W 仍是 P1 角度 +2°', pl == 1 and act == 'set'
        and patch.get('angle') == 7.0, str(patch))
    patch, act, pl = apply_key({'p2_angle': 10.0, 'p2_speed': 400.0}, 106)  # →
    chk('★ 双人键位：→ 归 P2（速度 +20）',
        pl == 2 and patch.get('speed') == 420.0, str(patch))
    patch, act, pl = apply_key({}, 28)                                      # 回车
    chk('★ 回车 = P2 发射', pl == 2 and act == 'fire', str(patch))
    patch, act, pl = apply_key({}, 45)                                      # X
    chk('★ X = P1 选球 +1', pl == 1 and act == 'sel' and patch.get('sel') == 1)
    patch, act, pl = apply_key({}, 51)                                      # ,
    chk('★ , = P2 选球 -1', pl == 2 and act == 'sel' and patch.get('sel') == -1)
    o = write_state(src='key', path=p, player=1, sel=1)
    chk('选球计数写进文件（p1_sup=1）', o.get('p1_sup') == 1,
        str(o.get('p1_sup')))
    o = write_state(src='key', path=p, player=2, sel=-1)
    chk('选球计数 P2（p2_sdn=1）且 p1 不动', o.get('p2_sdn') == 1
        and o.get('p1_sup') == 1, str(o.get('p2_sdn')))
    hub.poll()
    chk('hub 读到选球计数（sup1/sdn2）',
        hub.sup1 == 1 and hub.sdn2 == 1 and hub.sdn1 == 0 and hub.sup2 == 0,
        'sup1=%s sdn1=%s sup2=%s sdn2=%s'
        % (hub.sup1, hub.sdn1, hub.sup2, hub.sdn2))

    # ★ 2026-10-08 道具系统（规则书 §6.2 局前）：购买键 + 同键双语义导航计数
    _pp, _act, _pll = apply_key({}, 18)                    # E
    chk('E = P1 购买（act=buy）', _act == 'buy' and _pll == 1
        and _pp and _pp.get('buy') is True, str(_pp))
    _pp, _act, _pll = apply_key({}, 55)                    # 小键盘 *（码 55，实锤）
    chk('小键盘* = P2 购买（码 55 而非 98——98 是 /）',
        _act == 'buy' and _pll == 2, str(_pp))
    _pp, _act, _pll = apply_key({'angle': 0.0}, 17)        # W
    chk('W 同键双语义：角度 +2 且 nav=+1',
        _pp.get('angle') == 2.0 and _pp.get('nav') == 1, str(_pp))
    write_state(player=1, nav=1, src='t', path=p)
    o = write_state(player=1, nav=1, src='t', path=p)
    chk('选单计数 p1_mup 累计 2（另一次按下后）', o.get('p1_mup') == 2,
        str(o.get('p1_mup')))
    o = write_state(player=2, buy=True, src='t', path=p)
    chk('购买计数 p2_buy = 1', o.get('p2_buy') == 1, str(o.get('p2_buy')))
    hub2 = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub2.poll()
    chk('hub 读到选购计数（mup1=2 / buy2=1 / mdn1=0）',
        hub2.mup1 == 2 and hub2.buy2 == 1 and hub2.mdn1 == 0,
        'mup1=%s buy2=%s mdn1=%s' % (hub2.mup1, hub2.buy2, hub2.mdn1))

    # ★★ 2026-10-08 商店 v2：鼠标（纯函数步进 + 字段落盘/读回）
    _mx1, _my1 = mouse_step(100, 100, 50, -20)
    chk('鼠标步进：位移累进', (_mx1, _my1) == (150, 80), str((_mx1, _my1)))
    _mx2, _my2 = mouse_step(1270, 5, 400, -400)
    chk('鼠标步进：clamp 到画布（1280x720）内',
        (_mx2, _my2) == (SHOP_W - 1, 0), str((_mx2, _my2, SHOP_W, SHOP_H)))
    o = write_state(mx=321, my=123, src='t', path=p)
    chk('鼠标位置写进文件（mx=321 my=123）',
        o.get('mx') == 321 and o.get('my') == 123,
        str((o.get('mx'), o.get('my'))))
    o = write_state(mbtn=True, src='t', path=p)
    chk('左键计数 mbtn=1（且 mx/my 沿用）',
        o.get('mbtn') == 1 and o.get('mx') == 321, str((o.get('mbtn'), o.get('mx'))))
    hub3 = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub3.poll()
    chk('hub 读回鼠标（mx=321 my=123 mbtn=1）',
        hub3.mx == 321 and hub3.my == 123 and hub3.mbtn == 1,
        'mx=%s my=%s mbtn=%s' % (hub3.mx, hub3.my, hub3.mbtn))
    o = write_state(player=2, sopen=True, src='t', path=p)
    chk('进入商店计数 p2_sopen=1（独立于 buy，防误购）',
        o.get('p2_sopen') == 1 and o.get('p2_buy') == 1,   # buy 应保持上一笔的 1
        'sopen=%s buy=%s' % (o.get('p2_sopen'), o.get('p2_buy')))
    hub4 = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub4.poll()
    chk('hub 读回 sopen2=1', hub4.sopen2 == 1, str(hub4.sopen2))

    # ★★ 2026-10-14 局中道具（规则书 §6.4/§7.1）：键位 + 计数器
    _pp, _a, _pl = apply_key({}, 16)                       # Q
    chk('Q = P1 改磁场（act=edit）', _a == 'edit' and _pl == 1
        and _pp and _pp.get('edit') is True, str(_pp))
    _pp, _a, _pl = apply_key({}, 83)                       # 小键盘 .
    chk('小键盘. = P2 改磁场', _a == 'edit' and _pl == 2, str(_pp))
    _pp, _a, _pl = apply_key({}, 46)                       # C
    chk('C = P1 加洞口（act=hole）', _a == 'hole' and _pl == 1, str(_pp))
    _pp, _a, _pl = apply_key({}, 78)                       # 小键盘 +
    chk('小键盘+ = P2 加洞口', _a == 'hole' and _pl == 2, str(_pp))
    _pp, _a, _pl = apply_key({}, 53)                       # /（2026-10-16 第二入口）
    chk('★ / = P2 加洞口（码 53；与 小键盘+ 等价，就在 , . 旁边）',
        _a == 'hole' and _pl == 2, str(_pp))
    _pp, _a, _pl = apply_key({}, 48)                       # B（粒子引擎风暴）
    chk('★ B = P1 粒子风暴（act=pe，档3）', _a == 'pe' and _pl == 1
        and _pp and _pp.get('pe') is True, str(_pp))
    _pp, _a, _pl = apply_key({}, 49)                       # N
    chk('★ N = P2 粒子风暴', _a == 'pe' and _pl == 2, str(_pp))
    # ★ 2026-10-19 布置 PLACE v1：V / 小键盘- = 障碍摆放触发 + 计数回读
    _pp, _a, _pl = apply_key({}, 47)                       # V
    chk('★ V = P1 障碍摆放（act=obs，布置 PLACE v1）',
        _a == 'obs' and _pl == 1 and _pp and _pp.get('obs') is True, str(_pp))
    _pp, _a, _pl = apply_key({}, 74)                       # 小键盘 -
    chk('★ 小键盘- = P2 障碍摆放', _a == 'obs' and _pl == 2, str(_pp))
    write_state(player=2, obs=True, src='t', path=p)
    hub7o = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub7o.poll()
    chk('hub 读回障碍计数（obs2=1，obs1=0）',
        hub7o.obs2 == 1 and hub7o.obs1 == 0,
        'obs1=%s obs2=%s' % (hub7o.obs1, hub7o.obs2))
    # ★ 2026-10-25 实体传送门摆放：G / 小键盘7
    _pp, _a, _pl = apply_key({}, 34)                       # G
    chk('★ G = P1 传送门摆放（act=ppl）',
        _a == 'ppl' and _pl == 1 and _pp and _pp.get('ppl') is True, str(_pp))
    _pp, _a, _pl = apply_key({}, 71)                       # 小键盘 7
    chk('★ 小键盘7 = P2 传送门摆放', _a == 'ppl' and _pl == 2, str(_pp))
    write_state(player=1, ppl=True, src='t', path=p)
    hub7p = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub7p.poll()
    chk('hub 读回传送门摆放计数（ppl1=1，ppl2=0）',
        hub7p.ppl1 == 1 and hub7p.ppl2 == 0,
        'ppl1=%s ppl2=%s' % (hub7p.ppl1, hub7p.ppl2))
    # ★ 2026-10-25 局中加球：F / 小键盘8
    _pp, _a, _pl = apply_key({}, 33)                       # F
    chk('★ F = P1 局中加球（act=aball）',
        _a == 'aball' and _pl == 1 and _pp and _pp.get('aball') is True, str(_pp))
    _pp, _a, _pl = apply_key({}, 72)                       # 小键盘 8
    chk('★ 小键盘8 = P2 局中加球', _a == 'aball' and _pl == 2, str(_pp))
    _pp, _a, _pl = apply_key({}, 9)                        # 主键盘 8（别名）
    chk('★ 主键盘8 = P2 局中加球（别名）', _a == 'aball' and _pl == 2, str(_pp))
    write_state(player=1, aball=True, src='t', path=p)
    hub7ab = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub7ab.poll()
    chk('hub 读回加球计数（aball1=1，aball2=0）',
        hub7ab.aball1 == 1 and hub7ab.aball2 == 0,
        'aball1=%s aball2=%s' % (hub7ab.aball1, hub7ab.aball2))
    write_state(player=1, pe=True, src='t', path=p)
    hub7 = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub7.poll()
    chk('hub 读回风暴计数（pe1=1，pe2=0）', hub7.pe1 == 1 and hub7.pe2 == 0,
        'pe1=%s pe2=%s' % (hub7.pe1, hub7.pe2))
    _pp, _a, _pl = apply_key({}, 1)                        # ESC
    chk('ESC = 全局取消（act=esc，不再退出读取器）', _a == 'esc', str(_pp))
    _pp, _a, _pl = apply_key({'speed': 400.0}, 32)         # D
    chk('D 同键双语义：速度 +20 且 nav2=+1',
        _pp.get('speed') == 420.0 and _pp.get('nav2') == 1, str(_pp))
    o = write_state(player=1, edit=True, src='t', path=p)
    o = write_state(player=2, hole=True, src='t', path=p)
    o = write_state(player=1, nav2=-1, esc=True, src='t', path=p)
    chk('道具计数落盘（p1_edit=1 p2_hole=1 p1_adn=1 esc=1）',
        o.get('p1_edit') == 1 and o.get('p2_hole') == 1
        and o.get('p1_adn') == 1 and o.get('esc') == 1, str(
            (o.get('p1_edit'), o.get('p2_hole'), o.get('p1_adn'), o.get('esc'))))
    hub5 = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub5.poll()
    chk('hub 读回局中道具计数', hub5.edit1 == 1 and hub5.hole2 == 1
        and hub5.adn1 == 1 and hub5.esc == 1,
        'e1=%s h2=%s adn1=%s esc=%s' % (hub5.edit1, hub5.hole2, hub5.adn1,
                                        hub5.esc))

    # ★★ 2026-10-15 加洞口 v2（手机俯视图）：点位置通道（hx/hy/hopos）
    o = write_state(player=2, hx=123.4, hy=321.0, hopos=True, src='t', path=p)
    chk('点位置落盘（p2_hopos=1 p2_hx=123.4 p2_hy=321.0；P1 不动）',
        o.get('p2_hopos') == 1 and o.get('p2_hx') == 123.4
        and o.get('p2_hy') == 321.0 and o.get('p1_hopos') == 0,
        str((o.get('p2_hopos'), o.get('p2_hx'), o.get('p2_hy'))))
    write_state(player=1, hx=5.0, hy=6.0, hopos=True, src='t', path=p)
    o2 = write_state(src='t', path=p)                # 普通一笔：坐标/计数都不动
    chk('点位置计数只增不改：P1=1、P2=1；普通写入不动坐标',
        o2.get('p1_hopos') == 1 and o2.get('p2_hopos') == 1
        and o2.get('p2_hx') == 123.4 and o2.get('p1_hx') == 5.0,
        str((o2.get('p1_hopos'), o2.get('p2_hopos'), o2.get('p2_hx'))))
    hub6 = InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
    hub6.poll()
    chk('hub 读回点位置（hopos1=1 hx1=5.0；hopos2=1 hx2=123.4 hy2=321.0）',
        hub6.hopos1 == 1 and hub6.hx1 == 5.0 and hub6.hy1 == 6.0
        and hub6.hopos2 == 1 and hub6.hx2 == 123.4 and hub6.hy2 == 321.0,
        'h1=%s(%s,%s) h2=%s(%s,%s)' % (hub6.hopos1, hub6.hx1, hub6.hy1,
                                       hub6.hopos2, hub6.hx2, hub6.hy2))
    with open(os.path.abspath(__file__), encoding='utf-8') as _f6:
        _src6 = _f6.read()
    chk('★ /hole 端点四动作接线（enter/pos/ok/no）+ 俯视图页面元素',
        "u.path == '/hole'" in _src6 and 'kw = dict(hole=True)' in _src6
        and "act == 'ok'" in _src6 and "act == 'no'" in _src6
        and 'hopos=True' in _src6 and 'holewrap' in _src6
        and 'holeTap' in _src6 and 'holeFromMatch' in _src6,
        'endpoint/page wiring')

    # ★★ 2026-10-09 鼠标 HID-debugfs 旁路：报文解析（第一行是板上实测样例）
    _r = parse_hid_mouse('report (size 9) (numbered) =  02 00 00 ba ff fc ff 00 00')
    chk('HID 鼠标报文解析（实测样例：dx=-70 dy=-4 按钮0）',
        _r == (-70, -4, 0), str(_r))
    _r = parse_hid_mouse('report (size 9) (numbered) =  02 01 00 10 00 00 00 00 00')
    chk('HID 鼠标报文解析（正位移 + 左键按下）', _r == (16, 0, 1), str(_r))
    chk('HID 解析：非 report 行/坏行/空行安全 None',
        parse_hid_mouse('Button.0001 = 0') is None
        and parse_hid_mouse('garbage') is None
        and parse_hid_mouse('') is None
        and parse_hid_mouse(None) is None)

    try:
        os.unlink(p)
        os.rmdir(d)
    except Exception:
        pass
    print('   ---- v3_input 自检：%d 项，%d 通过，%d 失败 ----' % (n, ok, n - ok), flush=True)
    return 0 if ok == n else 1


def _utf8_stdout():
    """PC 的 cmd（cp936）会把中文打成乱码；板上本来就是 UTF-8，这一步是空操作。
    ★ 只在 main() 里调，**不**在 import 时调 —— ball_game 导入本模块时不该动它的 stdout。"""
    try:
        if (sys.stdout.encoding or '').lower().replace('-', '') not in ('utf8', 'utf_8'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def main(argv):
    _utf8_stdout()
    if not argv or argv[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    c = argv[0]
    if c == 'serve':
        return cmd_serve(argv[1:])
    if c == 'key':
        return cmd_key(argv[1:])
    if c == 'status':
        return cmd_status(argv[1:])
    if c == 'set':
        return cmd_set(argv[1:])
    if c == 'selftest':
        return selftest()
    print('不认识的角色：%s（serve / key / status / set / selftest）' % c)
    return 2


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except BrokenPipeError:
        # `./v3_remote.sh status | head` 这种被截断的管道**不是错误** —— 默认行为是
        # 打一串 traceback（第一版实测就这样，看着像工具崩了）。静悄悄走人。
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:
            pass
        os._exit(0)
