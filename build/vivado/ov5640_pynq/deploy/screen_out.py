# -*- coding: utf-8 -*-
"""screen_out.py —— 淘晶驰（TJC）串口屏状态输出。

协议依据（TJC 官方 USART HMI）：
  · 文本控件赋值：`<页面>.<控件>.txt="内容"` + **三个 0xFF 字节**；
  · 编码：TJC 默认 **GBK**（不是 UTF-8）；
  · 单控件文本长度有限（本工程实测 10 字节），超长按 GBK 安全截断；
  · 当前工程页面名 = `index`，控件 t0/t1/t2/t16/t17/t4/t5/t6/t9/t10/t11/t12-t15；
    道具剩余次数（2026-10-25 用户定稿布局）：P1 = t38/t42/t40/t41/t39，
    P2 = t44/t48/t46/t47/t45（每个道具一个控件，文案「加球 0」这种）。

纪律：
  · 只发**变化**的字段；调用方 5Hz 级节流；
  · 串口没插/屏幕掉线 → 不抛、不阻塞，游戏照跑；每 5 秒重试打开；
  · 任何异常只记一次日志，不带崩主循环。
"""
import os
import queue
import threading
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200
PAGE = 'index'
MAX_BYTES = 10

# 动态控件：t0/t1/t2 顶栏，t16/t17 局分/积分，
# t4/t5/t6 = P1 控球/角度/速度，t9/t10/t11 = P2，
# t12/t13/t14/t15 = P1 红/蓝、P2 红/蓝。
# ★ 2026-10-25 道具剩余次数（用户定稿布局：每个道具一个控件，带中文名）：
#   P1：t38 加球 / t42 加洞 / t40 加障碍 / t41 传送门 / t39 改磁场；
#   P2：t44 加球 / t48 加洞 / t46 加障碍 / t47 传送门 / t45 改磁场。
#   文案格式「加障碍 2」（≤10 GBK 字节；计数夹到 0..9）。
#   ⚠ t31~t36 是用户新加的**静态提示**（E加球 / V加障碍 / 设传送门…），
#     绝不能再往它们写计数（会覆盖提示）。
_CTRLS = ('t0', 't1', 't2', 't16', 't17',
          't4', 't5', 't6', 't9', 't10', 't11',
          't12', 't13', 't14', 't15',
          't38', 't42', 't40', 't41', 't39',
          't44', 't48', 't46', 't47', 't45')

_WARNED = set()


def _warn(msg):
    if msg not in _WARNED:
        _WARNED.add(msg)
        print('[屏幕] ' + msg, flush=True)


def fit_gbk(s, max_bytes=MAX_BYTES):
    """按 GBK 字节数截断（TJC 文本控件上限）；空串给 '--'。"""
    s = str(s)
    if not s:
        return '--'
    b = s.encode('gbk', errors='replace')
    if len(b) <= max_bytes:
        return s
    out = ''
    for ch in s:
        if len((out + ch).encode('gbk', errors='replace')) > max_bytes:
            break
        out += ch
    return out or '--'


def _phase_text(mf):
    if mf is None:
        return '未开赛'
    ph = mf.phase
    if ph == 'PREP':
        return '局前准备'
    if ph == 'PLAY':
        return '落定中' if mf.grace is not None else '进行中'
    if ph == 'EDIT':
        return '改场中'
    if ph == 'HOLE':
        return '加洞中'
    if ph == 'ROUND_END':
        return '结算中'
    if ph == 'MATCH_END':
        return '比赛结束'
    return str(ph)[:10]


def _timer_text(mf):
    if mf is None:
        return '倒计时--'
    if mf.phase != 'PLAY':
        return '倒计时--'
    if mf.grace is not None:
        import match_flow as _mf
        return '落定%d秒' % max(0, int(round(_mf.GRACE_SECS - mf.grace)))
    import match_flow as _mf
    return '倒计时%d秒' % max(0, int(round(_mf.TIMER_PERIOD - mf.timer)))


def _it(v):
    """道具计数显示：夹到 0..9（保证控件的 10 GBK 字节上限不被撑爆）。"""
    try:
        return max(0, min(9, int(v)))
    except Exception:
        return 0


def _item_line(name, v):
    """一个道具控件：「加障碍 2」（≤10 GBK 字节；计数夹 0..9）。"""
    return '%s %d' % (name, _it(v))


def snapshot(mf, a1, s1, b1, a2, s2, b2):
    """游戏状态 -> {控件名: 文本}。纯函数，PC 可测。"""
    import match_flow as _mf
    if mf is None:
        return {
            't0': '第--/--局', 't1': '未开赛', 't2': '倒计时--',
            't16': '局分 --:--', 't17': '积分 --:--',
            't4': '控球 --', 't5': '角度 %d°' % int(round(a1)),
            't6': '速度 %d' % int(round(s1)),
            't9': '控球 --', 't10': '角度 %d°' % int(round(a2)),
            't11': '速度 %d' % int(round(s2)),
            't12': '红球--', 't13': '蓝球--',
            't14': '红球--', 't15': '蓝球--',
            't38': _item_line('加球', 0), 't42': _item_line('加洞', 0),
            't40': _item_line('加障碍', 0), 't41': _item_line('传送门', 0),
            't39': _item_line('改磁场', 0),
            't44': _item_line('加球', 0), 't48': _item_line('加洞', 0),
            't46': _item_line('加障碍', 0), 't47': _item_line('传送门', 0),
            't45': _item_line('改磁场', 0),
        }
    rnd = int(mf.round)
    if rnd <= _mf.MAX_ROUNDS:
        t0 = '第%d/%d局' % (rnd, _mf.MAX_ROUNDS)
    else:
        t0 = '加赛局'
    goals, pts = mf.goals, mf.points
    ammo = mf.ammo
    items = getattr(mf, 'items', {1: {}, 2: {}}) or {1: {}, 2: {}}
    _i1 = items.get(1) or {}
    _i2 = items.get(2) or {}
    # ★★ 2026-10-29 用户实况：开局每人有 **1 次免费加洞**，串口屏只显示 items
    #   （free_hole 是独立字段）→ 开局显示"加洞 0"，误导。显示**可用总数**
    #   = 免费额度 + 已购道具。
    _free = getattr(mf, 'free_hole', {1: 0, 2: 0}) or {}
    b1s = '%d号' % int(b1) if b1 is not None else '--'
    b2s = '%d号' % int(b2) if b2 is not None else '--'
    return {
        't0': t0,
        't1': _phase_text(mf),
        't2': _timer_text(mf),
        # ★ 2026-10-31 修：网页/结算文案的"局分"是 goals×GOAL_POINTS（进球+10），
        #   这里原来只显示 goals（进球数），导致同一局网页 30:30、串口屏 3:3。
        # ★ 2026-11-01 实况：比分到 110:90 后，"局分 110:90" = 11 字节，
        #   超过 TJC 控件 10 字节上限 -> 被截成 "局分 110:9"（丢最后一位）。
        #   改成单字标签 + 去空格："局110:90"/"积110:90" = 8~9 字节，3 位数也放得下。
        't16': '局%d:%d' % (goals[1] * _mf.GOAL_POINTS,
                            goals[2] * _mf.GOAL_POINTS),
        't17': '积%d:%d' % (pts[1], pts[2]),
        't4': '控球 %s' % b1s,
        't5': '角度 %d°' % int(round(a1)),
        't6': '速度 %d' % int(round(s1)),
        't9': '控球 %s' % b2s,
        't10': '角度 %d°' % int(round(a2)),
        't11': '速度 %d' % int(round(s2)),
        't12': '红球 %d' % ammo[1][1],
        't13': '蓝球 %d' % ammo[1][-1],
        't14': '红球 %d' % ammo[2][1],
        't15': '蓝球 %d' % ammo[2][-1],
        't38': _item_line('加球', _i1.get('ball', 0)),
        't42': _item_line('加洞', int(_free.get(1, 0))
                          + int(_i1.get('hole', 0))),
        't40': _item_line('加障碍', _i1.get('obstacle', 0)),
        't41': _item_line('传送门', _i1.get('portal', 0)),
        't39': _item_line('改磁场', _i1.get('field', 0)),
        't44': _item_line('加球', _i2.get('ball', 0)),
        't48': _item_line('加洞', int(_free.get(2, 0))
                          + int(_i2.get('hole', 0))),
        't46': _item_line('加障碍', _i2.get('obstacle', 0)),
        't47': _item_line('传送门', _i2.get('portal', 0)),
        't45': _item_line('改磁场', _i2.get('field', 0)),
    }


class ScreenOut(object):
    """TJC 串口屏 driver（raw termios，不依赖 pyserial）。"""

    def __init__(self, path=PORT, baud=BAUD, page=PAGE):
        self.path = path
        self.baud = baud
        self.page = page
        self.fd = None
        self.last = {}
        self._next_try = 0.0
        self._rbuf = b''                # ★ 串口读缓冲（触摸按钮事件）
        # ★ 2026-10-25 页面感知：TJC 只接受**当前页**的控件文本命令；在 addball
        #   页写 index 控件会返回 0x1A，但驱动会误以为发成功 -> 缓存污染 ->
        #   回 index 后道具数不更新。记住当前页，非状态页一律不写。
        self._cur_page = None

    def open(self):
        if self.fd is not None:
            return True
        now = time.monotonic()
        if now < self._next_try:
            return False
        self._next_try = now + 5.0
        try:
            import termios          # ★ 延迟 import：PC 台架（Windows）也要能 import 本模块
            speed = getattr(termios, 'B%d' % self.baud, termios.B115200)
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            a = termios.tcgetattr(fd)
            a[0] = 0
            a[1] = 0
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            a[3] = 0
            a[4] = speed
            a[5] = speed
            a[6][termios.VMIN] = 0
            a[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, a)
            termios.tcflush(fd, termios.TCIOFLUSH)
            self.fd = fd
            self.last = {}                 # 重连后全部重发
            self._cur_page = self.page
            # ★ 2026-10-25：每次打开/重连都先切回状态页，保证后续文本命令
            #   都落在当前页（TFT 重下/屏重启后可能停在别的页）。
            try:
                os.write(fd, ('page %s' % self.page).encode('gbk') +
                         b'\xff\xff\xff')
            except Exception:
                pass
            print('[屏幕] 已打开 %s @ %d（页面 %s）' %
                  (self.path, self.baud, self.page), flush=True)
            return True
        except Exception as e:
            _warn('串口打不开（%s）；游戏照跑，5 秒后重试' % str(e)[:100])
            return False

    def send(self, name, text):
        if self.fd is None and not self.open():
            return False
        txt = fit_gbk(text)
        txt = txt.replace('\\', '\\\\').replace('"', '\\"')
        cmd = '%s.%s.txt="%s"' % (self.page, name, txt)
        try:
            os.write(self.fd, cmd.encode('gbk', errors='replace') +
                     b'\xff\xff\xff')
            return True
        except Exception as e:
            _warn('写串口失败（%s）；先关掉，5 秒后重试' % str(e)[:100])
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            return False

    def raw(self, cmd):
        """★ 2026-10-25 发一条原始 TJC 指令（自动补 FF FF FF）。

        用于 `page addball` / `page index` 这类页面切换；与文本赋值走同一
        串口，失败不抛（屏幕掉线只影响屏幕）。"""
        if self.fd is None and not self.open():
            return False
        try:
            os.write(self.fd, cmd.encode('gbk', errors='replace') +
                     b'\xff\xff\xff')
            # ★ 2026-10-25 实况（加球后回 index 红/蓝数不变）：TJC 切页会
            #   **重载页面**（控件回到工程初始值）。这里作废文本缓存并记下
            #   当前页，下一次状态快照会**全量重写**。
            if cmd.startswith('page '):
                self._cur_page = cmd[5:].strip()
                self.last = {}
            return True
        except Exception as e:
            _warn('写指令失败（%s）；先关掉，5 秒后重试' % str(e)[:100])
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            return False

    def set_page(self, page):
        """切到指定页面（TJC `page <name>`）。"""
        return self.raw('page %s' % page)

    def read_events(self):
        """非阻塞读串口，把触摸按钮发来的单字节命令放进 _EV。

        协议（2026-10-25 定）：按钮触摸事件 `printh 52 FF FF FF`（红球）、
        `printh 42 FF FF FF`（蓝球）、`printh 43 FF FF FF`（取消）。
        只认**整帧等于单字节 R/B/C（大小写均可）**，其余数据忽略。"""
        if self.fd is None:
            return
        try:
            data = os.read(self.fd, 256)
        except BlockingIOError:
            return
        except Exception as e:
            _warn('读串口失败（%s）；先关掉，5 秒后重试' % str(e)[:100])
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            return
        if not data:
            return
        self._rbuf += data
        if len(self._rbuf) > 4096:
            self._rbuf = self._rbuf[-4096:]
        while True:
            q = self._rbuf.find(b'\xff\xff\xff')
            if q < 0:
                break
            frame = self._rbuf[:q]
            self._rbuf = self._rbuf[q + 3:]
            if os.environ.get('SCREEN_DEBUG', '') == '1':
                print('[屏幕] 收到帧 %r' % bytes(frame), flush=True)
            ev = _event_from_frame(frame)
            if ev:
                try:
                    _EV.put_nowait(ev)
                except queue.Full:
                    pass

    def update(self, snap):
        """snap: {控件名: 文本}；只发变化字段。

        ★ 页面感知：当前不在 index（如 addball）时**整段跳过**——TJC 会拒绝
          非当前页的控件命令（0x1A），若仍写缓存，回到 index 后就再也不发。"""
        if self.fd is None and not self.open():
            return
        if self._cur_page is not None and self._cur_page != self.page:
            return
        for name in _CTRLS:
            if name not in snap:
                continue
            txt = snap[name]
            if self.last.get(name) == txt:
                continue
            if self.send(name, txt):
                self.last[name] = txt

    def close(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None


# ---- 模块级：主循环只把快照塞进队列；串口写在**后台线程** ----
# ★ 2026-10-25 实况：游戏卡死时最后一行是直播存图，随后 SSH reset、/match 冻结。
#   屏幕和键盘/鼠标/WiFi 共用 MTT 坞，`os.write` 在 USB 串口层**可能阻塞在核里**
#   —— 如果直接在主循环写串口，一次 USB 卡顿就能冻住整个游戏。现在主循环只
#   `put_nowait`（tmpfs/内存），串口写放 daemon 线程：线程卡住最多屏幕不更新，
#   游戏/HDMI/SSH 照跑。
# ★★ 2026-10-25 加球选择页：同一个后台线程既写状态、也读触摸按钮命令。
#   _Q   = 状态快照（主循环 put_nowait，线程取）
#   _CMD = 原始指令（page 切换；主循环 put_nowait，线程取）
#   _EV  = 触摸事件（线程 put，主循环 poll）
_Q = queue.Queue(maxsize=2)
_CMD = queue.Queue(maxsize=16)
_EV = queue.Queue(maxsize=16)
_OPEN = {'v': False}
_THREAD = None
_LAST_T = 0.0

_EV_MAP = {b'R': 'red', b'r': 'red',
           b'B': 'blue', b'b': 'blue',
           b'C': 'cancel', b'c': 'cancel'}


def _event_from_frame(frame):
    """整帧 -> 事件名（纯函数，台架可测）；不认识返回 None。

    ★ 2026-10-25 实况（取消按钮"同步很慢"）：TJC 按钮可能多发 0x00 / CRLF
      （"发送数据"模式），先去 NUL/空白再匹配；只接受**单字节** R/B/C，
      长串（如 CANCEL）仍忽略，避免误判。"""
    _f = bytes(frame or b'').replace(b'\x00', b'').strip()
    if len(_f) == 1:
        return _EV_MAP.get(_f)
    return None


def _worker():
    out = ScreenOut()
    while True:
        # 1) 原始指令（page 切换）——先发，保证触摸页尽快出来
        while True:
            try:
                cmd = _CMD.get_nowait()
            except queue.Empty:
                break
            if cmd is None:
                out.close()
                _OPEN['v'] = False
                return
            out.raw(cmd)
        # 2) 打开串口 + 读触摸事件（打不开就 5 秒后重试）
        if out.fd is None:
            out.open()
        out.read_events()
        _OPEN['v'] = out.fd is not None
        # 3) 状态快照（50ms 一取，兼作读串口的节拍）
        try:
            snap = _Q.get(timeout=0.05)
        except queue.Empty:
            continue
        if snap is None:
            out.close()
            _OPEN['v'] = False
            return
        try:
            out.update(snap)
        except Exception as e:
            _warn('线程更新异常（游戏照跑）：%s' % str(e)[:120])
            time.sleep(0.5)


def update(mf, a1, s1, b1, a2, s2, b2, min_period=0.18):
    """主循环调它：5Hz 节流 + 只把快照入队；不碰串口、不阻塞。"""
    global _THREAD, _LAST_T
    now = time.monotonic()
    if now - _LAST_T < min_period:
        return
    _LAST_T = now
    try:
        snap = snapshot(mf, a1, s1, b1, a2, s2, b2)
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD = threading.Thread(target=_worker, name='screen_out',
                                       daemon=True)
            _THREAD.start()
        try:
            _Q.put_nowait(snap)
        except queue.Full:
            pass                      # 屏幕跟不上就丢旧快照，绝不卡主循环
    except Exception as e:
        _warn('入队异常（游戏照跑）：%s' % str(e)[:120])


def set_page(page):
    """★ 主循环调：请求切页（后台线程发）。返回 False=队列满（极少）。"""
    try:
        _CMD.put_nowait('page %s' % page)
        return True
    except queue.Full:
        return False


def _build_text_cmd(page, name, text, max_bytes=MAX_BYTES):
    """构造**指定页面**的文本赋值命令（纯函数，台架可测）。

    与 index 的状态刷新不同：addball 页的 t8/t12 只在切页前写一次，
    所以单独走原始命令队列（保证在 `page addball` 之前发出去）。"""
    txt = fit_gbk(text, max_bytes)
    txt = txt.replace('\\', '\\\\').replace('"', '\\"')
    return '%s.%s.txt="%s"' % (page, name, txt)


def set_text(page, name, text, max_bytes=MAX_BYTES):
    """★ 主循环调：给指定页面的控件写文本（后台线程发）。"""
    try:
        _CMD.put_nowait(_build_text_cmd(page, name, text, max_bytes))
        return True
    except queue.Full:
        return False


def poll_event():
    """★ 主循环调：取一条触摸事件（'red'/'blue'/'cancel'）；没有返回 None。"""
    try:
        return _EV.get_nowait()
    except queue.Empty:
        return None


def available():
    """串口是否已打开（有屏在线）。"""
    return bool(_OPEN.get('v'))


def close():
    global _THREAD
    try:
        _Q.put_nowait(None)
    except queue.Full:
        pass
    _THREAD = None


def _reset_for_test():
    global _THREAD, _LAST_T
    for _q in (_Q, _CMD, _EV):
        while True:
            try:
                _q.get_nowait()
            except queue.Empty:
                break
    _THREAD = None
    _LAST_T = 0.0
    _OPEN['v'] = False
    _WARNED.clear()
