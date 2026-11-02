# -*- coding: utf-8 -*-
"""link_boot / dvp_regs 的**离线台架**：用假寄存器跑七种链路故障，验证行为。

为什么不省这一步（2026-09-29，用户："不要让它每次上板都这不行那不行"）：
    板上的时间是稀缺资源，而"救援阶梯"这种东西**在板上一旦跑错就是自造不稳定**
    （第一版救援在断电窗口里按了 35 次、把传感器初始化打断了 120 秒）。判据先在
    PC 上验证：假寄存器 + 假时钟，把七种故障各跑一遍，检查
      · 该动手时动手（脉冲 -> 升级），不该动手时一次都不动
      · 动作之间 >=5s、升级后静默 >=10s、动作总数封顶、放弃后**不再动**
      · 身份自检说"不是我们的 bit" / 还在开机写入窗口 -> 一个寄存器都不许写
      · 任何异常路径（sleep 里被打断 / 写失败）之后，静止位一定是干净的
    全绿才算"稳定性这块的代码可信"。板子只用来验最后一遍真机。

跑法（PC，不需要任何硬件）：
    python link_boot_selftest.py            # 全部
    python link_boot_selftest.py never -l   # 单跑某个场景
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dvp_regs                                            # noqa: E402

PCLK_PER_S, VSYNC_PER_S, HREF_PER_S = 1315000, 30, 21641   # 实测基线

# 本项目自己的模块：它们同时存在于 /home/xilinx/wp(真身) 和 /boot(旧镜像)，
# 一旦被"另一份"先 import 进 sys.modules，后面所有检查验的都是错的那份。
_PROJ_MODS = ('ball_map_detect', 'ball_detect', 'vdma_capture', 'link_boot',
              'link_doctor', 'vision_golden', 'dvp_regs', 'pl_revive',
              'ball_game', 'match_flow', 'hole_adapt')


def _prefer_here():
    """把本文件所在目录钉回 sys.path[0]，并丢掉已经 import 进来的**别处副本**。

    ★ 为什么必须有它（2026-09-29 板上实锤）：`link_doctor` 会
      `sys.path.insert(0, '/boot')`（它要 import fastmem），于是 doctor 这一节
      一跑完，**后面所有 import 都先撞上 /boot 那份旧镜像**。实测后果：
        display_swap 一节报
        `module 'ball_map_detect' has no attribute display_swap_check`
      而 wp 那份明明有这个函数 —— /boot 那份是五月三日的旧文件（53873 字节，
      md5 caa378e4；wp 是 60764 字节，md5 dca7a78b）。
      台架的职责是"验**刚部署**的这份代码"，所以每节开工前路径必须先钉回来；
      光改 sys.path 不够 —— 已经 import 过的旧模块还躺在 sys.modules 里，
      所以**同时**要把解析到别处的副本丢掉。
    """
    while HERE in sys.path:
        sys.path.remove(HERE)
    sys.path.insert(0, HERE)
    for m in _PROJ_MODS:
        mod = sys.modules.get(m)
        f = getattr(mod, '__file__', None) if mod is not None else None
        if f and not os.path.abspath(f).startswith(HERE):
            sys.modules.pop(m, None)


# ============================================================================
#  假时钟：sleep 只推进虚拟时间（秒级节奏才跑得动），可选在某次 sleep 上"出事"
# ============================================================================

def _font_cmap(path):
    """极简 TTF cmap 读取：返回码点集合（format 4/12；不依赖 fontTools——板上也要跑）。
    format 4 = 段表（start/end 对）；format 12 = 组表（start..end）。失败返回空集。"""
    out = set()
    try:
        import struct
        with open(path, 'rb') as f:
            d = f.read()
        ntab = struct.unpack_from('>H', d, 4)[0]
        cmap_off = None
        for i in range(ntab):
            off = 12 + 16 * i
            if d[off:off + 4] == b'cmap':
                cmap_off = struct.unpack_from('>I', d, off + 8)[0]
        if cmap_off is None:
            return out
        n = struct.unpack_from('>H', d, cmap_off + 2)[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack_from('>HHI', d, cmap_off + 4 + 8 * i)
            fmt = struct.unpack_from('>H', d, cmap_off + off)[0]
            if fmt == 12 and pid == 3:
                best = (cmap_off + off, 12)
            elif fmt == 4 and pid == 3 and (best is None or best[1] == 4):
                best = (cmap_off + off, 4)
        if best is None:
            return out
        base, fmt = best
        if fmt == 12:
            ngr = struct.unpack_from('>I', d, base + 12)[0]
            for i in range(ngr):
                s0, s1, _g = struct.unpack_from('>III', d, base + 16 + 12 * i)
                out.update(range(s0, s1 + 1))
        else:
            segx2 = struct.unpack_from('>H', d, base + 6)[0]
            seg = segx2 // 2
            ends = struct.unpack_from('>%dH' % seg, d, base + 14)
            starts = struct.unpack_from('>%dH' % seg, d, base + 16 + segx2)
            for s0, s1 in zip(starts, ends):
                if s1 != 0xFFFF:
                    out.update(range(s0, s1 + 1))
    except Exception:
        pass
    return out


class Clock:
    def __init__(self, t0=1000.0):
        self.t = t0
        self.sleeps = []          # [(时刻, 时长)]
        self.boom_on_sleep = None  # 第 n 次 sleep 抛 KeyboardInterrupt（模拟被打断）
        self.n_sleep = 0

    def time(self):
        return self.t

    def sleep(self, s):
        self.n_sleep += 1
        self.sleeps.append((round(self.t, 3), float(s)))
        if self.boom_on_sleep is not None and self.n_sleep == self.boom_on_sleep:
            raise KeyboardInterrupt('台架模拟：sleep 期间被打断（SIGINT/信号）')
        self.t += max(0.0, float(s))


# ============================================================================
#  假链路 + 假寄存器
# ============================================================================

class FakeLink:
    """一条假链路的状态机。因果照实模拟：
    · bit0/bit1 被按住 -> 传感器停摆（pclk/vsync/href 不再累加、frame_cnt 冻住）
    · 脉冲 bit3/bit4 走完（下降沿）-> 按 heal_on 决定复不复活
    """

    def __init__(self, clk, alive=True, heal_on=None):
        self.clk = clk
        self.heal_on = heal_on            # None | ('vidrst', n) | ('reinit', n)
        self.n_vidrst = 0
        self.n_reinit = 0
        self.ctrl = 0x380                 # dvp_ctrl：dbg_sel=7，静止位全 0
        self.vctrl = 0x00000000           # 视觉核 ctrl（bit0 = freeze）
        self.cap_started = True           # 契约：link_up 时消费者已经在跑
        self.fc = 500
        self.pclk = 0.0
        self.t_last = clk.t
        self.alive_since = clk.t if alive else None
        self.writes = []                  # [(t, base, off, val)]
        self.errs = 0
        self.hlen = 2046
        self.scl = 0.0                    # p1+0x08：SCCB 时钟上升沿（cfg 在跑的证据）
        self.boom_on_write = None         # 第 n 笔 p3 写抛异常（模拟写失败）

    # ---- 内部 ----
    def _advance(self):
        now = self.clk.time()
        dt = now - self.t_last
        # scl 与视频流无关：ov5640_cfg 在重跑上电序列时就会发 SCCB（link_doctor 拿它
        # 区分"SCCB 根本没在跑"和"跑完了但传感器没接受"），所以它**不随链路死活归零**。
        self.scl += dt * 5000.0
        if self.alive_since is not None:
            self.pclk += dt * PCLK_PER_S
            self.fc += int(dt * VSYNC_PER_S)
        self.t_last = now

    def alive(self):
        return self.alive_since is not None

    def kill(self):
        self.alive_since = None

    def _revive(self):
        self.alive_since = self.clk.time()

    # ---- 寄存器接口 ----
    def read(self, base, off=0):
        self._advance()
        key = (base, off)
        if key == ('p0', 0):
            return int(self.pclk)
        if key == ('p0', 0x08):
            return int(self.pclk / PCLK_PER_S * VSYNC_PER_S)
        if key == ('p1', 0):
            return int(self.pclk / PCLK_PER_S * HREF_PER_S)
        if key == ('p1', 0x08):
            return int(self.scl)                       # scl 上升沿（SCCB 在跑）
        if key == ('p2', 0x08):
            # 电平字：bit0 locked / bit18 pwdn / bit19 rst_n（照 RTL 的位号）
            return ((1 if self.alive_since is not None else 0)
                    | ((1 if (self.ctrl & dvp_regs.BIT_PWDN) else 0) << 18)
                    | ((0 if (self.ctrl & dvp_regs.BIT_SEN_RST) else 1) << 19))
        if key == ('p3', 0):
            return self.ctrl
        if key == ('ctrl', 0x00):
            return self.vctrl
        if key == ('ctrl', 0x08):
            return self.fc
        if key == ('vstat', 0):
            return 0x02D01680
        if key == ('vstat', 0x08):
            return ((self.hlen & 0x7FF) << 21) | ((self.errs & 0x3F) << 15) | 2047
        if key == ('vdma', dvp_regs.S2MM_DMACR):
            return 1 if self.cap_started else 0
        if key == ('vdma', dvp_regs.S2MM_DMASR):
            return 0 if self.cap_started else 1
        raise KeyError('假寄存器没有这个地址：%s +0x%X' % (base, off))

    def write(self, base, off, val):
        self._advance()
        key = (base, off)
        if key == ('p3', 0):
            n_p3 = len([1 for _, b, o, _ in self.writes if (b, o) == ('p3', 0)])
            if self.boom_on_write is not None and n_p3 + 1 == self.boom_on_write:
                raise RuntimeError('台架模拟：第 %d 笔 ctrl 写失败' % (n_p3 + 1))
        self.writes.append((round(self.clk.time(), 3), base, off, val))
        if key == ('ctrl', 0x00):
            self.vctrl = val
            return
        if key == ('vdma', dvp_regs.S2MM_DMACR):
            self.cap_started = bool(val & 1)
            return
        if key == ('p3', 0):
            prev, self.ctrl = self.ctrl, val
            if val & (dvp_regs.BIT_XCLK_OFF | dvp_regs.BIT_PWDN):
                self.kill()                      # 按住电源位 = 传感器停摆
            for bit, name in ((dvp_regs.BIT_VID_RST, 'vidrst'),
                              (dvp_regs.BIT_SEN_RST, 'reinit')):
                if (prev & bit) and not (val & bit):        # 下降沿 = 脉冲走完
                    if name == 'vidrst':
                        self.n_vidrst += 1
                        n = self.n_vidrst
                    else:
                        self.n_reinit += 1
                        n = self.n_reinit
                    if self.heal_on == (name, n):
                        self._revive()
            return
        raise KeyError('假寄存器不接受写：%s +0x%X' % (base, off))

    def close(self):
        pass


class FakeCap:
    """假 S2mmCapture（契约是"已经 start() 过了"）。"""

    def __init__(self, link):
        self.link = link
        self.stopped = False
        self.errors_cleared = 0

    def stop(self):
        self.stopped = True
        self.link.cap_started = False

    def clear_errors(self):
        self.errors_cleared += 1


# ============================================================================
#  框架
# ============================================================================

import link_boot                                           # noqa: E402

RESULTS = []


def _prep(clk, link, pl='ours', uptime=9999.0):
    for mod in (link_boot, dvp_regs):
        mod.time = clk
    dvp_regs.open_regs = lambda *a, **k: link
    link_boot.pl_present = lambda timeout=8.0: (pl, '台架伪造')
    link_boot.uptime_s = lambda: uptime
    link_boot._DOWN_DONE = False


def _check(name, cond, detail=''):
    RESULTS.append((name, bool(cond), detail))
    print('   %s %s%s' % ('[OK]' if cond else '[FAIL]', name,
                          ('  -- ' + detail) if detail else ''), flush=True)
    return bool(cond)


def _action_times(link):
    """动作时刻 = 脉冲位的**上升沿**（一次动作只算一次）。"""
    ts, prev = [], None
    for t, b, o, v in link.writes:
        if (b, o) != ('p3', 0):
            continue
        cur = v & (dvp_regs.BIT_VID_RST | dvp_regs.BIT_SEN_RST)
        if prev is not None:
            rise = cur & ~prev
            if rise:
                ts.append(t)
        prev = cur
    return ts


def _actions(v):
    return [a[1] for a in v['acts']]


# ============================================================================
#  场景
# ============================================================================

def s_healthy():
    print('\n== healthy：链路本来就是好的（只该等，一次都不许动手）')
    clk = Clock()
    link = FakeLink(clk, alive=True)
    cap = FakeCap(link)
    _prep(clk, link)
    v = link_boot.link_up(cap, quiet=True)
    _check('结论健康', v['ok'] and v['grade'] == 'healthy', str(v['why']))
    _check('没有脉冲/升级动作', not any('ctrl[' in a for a in _actions(v)),
           str(_actions(v)))
    _check('没有写过 ctrl 控制字', not [w for w in link.writes
                                        if (w[1], w[2]) == ('p3', 0)],
           '%d 笔' % len(link.writes))


def s_heal_first():
    print('\n== heal_first：死了，第一次 ctrl[3] 脉冲就救活（动手一次就停）')
    clk = Clock()
    link = FakeLink(clk, alive=False, heal_on=('vidrst', 1))
    cap = FakeCap(link)
    _prep(clk, link)
    v = link_boot.link_up(cap, quiet=True)
    acts = _actions(v)
    _check('结论健康', v['ok'], str(v['why']))
    _check('只脉冲 1 次 ctrl[3]', acts.count('脉冲 ctrl[3]') == 1, str(acts))
    _check('没有升级 ctrl[4]', '升级 ctrl[4]' not in acts, str(acts))


def s_never():
    print('\n== never：怎么都不活（动作封顶 + 限频 + 升级静默 + 放弃后不再动）')
    clk = Clock()
    link = FakeLink(clk, alive=False, heal_on=None)
    cap = FakeCap(link)
    _prep(clk, link)
    t0 = clk.t
    v = link_boot.link_up(cap, quiet=True)
    acts = _actions(v)
    _check('结论不是健康', not v['ok'], str(v['why']))
    _check('动作数 = 3（2 脉冲 + 1 升级）',
           acts.count('脉冲 ctrl[3]') == 2 and acts.count('升级 ctrl[4]') == 1,
           str(acts))
    _check('有"放弃"记录', '放弃' in acts, str(acts))
    give = [a for a in v['acts'] if a[1] == '放弃']
    if give:
        tail = [w for w in link.writes
                if (w[1], w[2]) == ('p3', 0) and w[0] > give[0][0] + 0.01]
        _check('放弃后再没写过 ctrl', not tail, '%d 笔' % len(tail))
    ts = _action_times(link)
    gaps = [round(ts[i + 1] - ts[i], 2) for i in range(len(ts) - 1)]
    _check('动作之间 >=5s（限频硬线）', bool(gaps) and all(g >= 5.0 - 0.05
                                                          for g in gaps), str(gaps))
    quiet = [s for t, s in clk.sleeps if s >= 10.0 and t >= (ts[-1] if ts else 0)]
    _check('升级后有 >=10s 静默（给传感器重跑 SCCB）', bool(quiet), str(quiet))
    _check('总耗时合理（>=20s，说明节奏是真的按秒走的）',
           clk.t - t0 >= 20.0, '%.1fs' % (clk.t - t0))


def s_not_ours():
    print('\n== not_ours：PL 里不是我们的 bit（一个字都不许写，直接叫人下 bit）')
    clk = Clock()
    link = FakeLink(clk, alive=False)
    cap = FakeCap(link)
    _prep(clk, link, pl='not_ours')
    v = link_boot.link_up(cap, quiet=True)
    _check('结论"死"且指向下 bit', v['grade'] == 'dead' and 'bit' in v['why'],
           str(v['why']))
    _check('没有写任何寄存器', not link.writes, '%d 笔' % len(link.writes))


def s_boot_window():
    print('\n== boot_window：开机才 100s（base.bin 写 PL 的窗口里，不许碰 PL）')
    clk = Clock()
    link = FakeLink(clk, alive=True)
    cap = FakeCap(link)
    _prep(clk, link, uptime=100.0)
    v = link_boot.link_up(cap, quiet=True, allow_boot_wait=False)
    _check('结论 blocked', v['grade'] == 'blocked', str(v['why']))
    _check('没有写任何寄存器', not link.writes, '%d 笔' % len(link.writes))


def s_latch_interrupt():
    print('\n== latch：断电重上电中途被打断（静止位必须干净 —— 头号事故）')
    # (a) sleep 期间收到信号（KeyboardInterrupt）—— 这是真实会发生的那个
    clk = Clock()
    clk.boom_on_sleep = 1
    link = FakeLink(clk, alive=True)
    _prep(clk, link)
    raised = None
    try:
        dvp_regs.power_cycle(link, quiet=True)
    except BaseException as e:
        raised = e
    _check('异常确实抛了出来（没被吞）', raised is not None,
           '%s: %s' % (type(raised).__name__, raised) if raised else '')
    _check('★ 静止位干净（xclk_off/pwdn/vid_rst/sensor_rst 全 0）',
           (link.ctrl & dvp_regs.IDLE_MASK) == 0, 'ctrl=0x%03X' % link.ctrl)


def s_latch_write_fail():
    print('\n== latch2：重跑 SCCB 那一步的写失败（最后一道兜底要把它擦干净）')
    clk = Clock()
    link = FakeLink(clk, alive=True)
    link.boom_on_write = 3          # 第 3 笔 = 按住 bit4（中间那一步）之后所有写都失败
    _prep(clk, link)
    raised = None
    try:
        dvp_regs.power_cycle(link, quiet=True)
    except BaseException as e:
        raised = e
    # 第 3 笔失败之后，第 4 笔（finally/兜底）是好的 -> 应该干净
    _check('异常抛了出来', raised is not None,
           '%s: %s' % (type(raised).__name__, raised) if raised else '')
    _check('★ 静止位干净（finally 那一笔把它擦了）',
           (link.ctrl & dvp_regs.IDLE_MASK) == 0, 'ctrl=0x%03X' % link.ctrl)


def s_unlatch_and_down():
    print('\n== unlatch/down：松开锁住的位 + 收尾顺序 + 幂等')
    clk = Clock()
    link = FakeLink(clk, alive=True)
    cap = FakeCap(link)
    _prep(clk, link)
    link.ctrl = 0x383                     # 实测锁死现场：bit0|bit1 锁着 + dbg_sel=7
    ok, detail = dvp_regs.unlatch(link, quiet=True)
    _check('unlatch 成功且读回干净', ok, detail)
    _check('静止位清了、dbg_sel=7 保留',
           (link.ctrl & dvp_regs.IDLE_MASK) == 0 and ((link.ctrl >> 7) & 7) == 7,
           'ctrl=0x%03X' % link.ctrl)

    link.cap_started = True
    link.vctrl = 0x00000001               # 假装上个脚本留了个冻结位
    link_boot.link_down(cap, quiet=True)
    _check('cap.stop() 被调用（没有野 DMA）', cap.stopped)
    _check('收尾后冻结位是放掉的（不留"看起来像死链"的状态）',
           (link.vctrl & 1) == 0, 'vctrl=0x%08X' % link.vctrl)
    _check('收尾后静止位干净', (link.ctrl & dvp_regs.IDLE_MASK) == 0,
           'ctrl=0x%03X' % link.ctrl)
    before = len(link.writes)
    link_boot.link_down(cap, quiet=True)
    _check('link_down 幂等（第二次什么都不写）', len(link.writes) == before,
           '%d -> %d' % (before, len(link.writes)))


def s_frame_sane():
    print('\n== frame_sane：退化帧守卫（全 0/常值/NaN 都不算"抓到帧"）')
    try:
        import ball_detect
    except Exception as e:
        print('   （跳过：import ball_detect 失败 —— %s）' % e)
        return
    f = ball_detect._frame_sane

    class Arr:
        def __init__(self, mean, std):
            self._m, self._s = mean, std

        def mean(self):
            return self._m

        def std(self):
            return self._s

    cases = [('全 0 缓冲', Arr(0.0, 0.0), False),
             ('常值缓冲（均值 128 标准差 0）', Arr(128.0, 0.0), False),
             ('几乎全黑', Arr(0.5, 2.0), False),
             ('NaN', Arr(float('nan'), float('nan')), False),
             ('真实场景（均值 120 标准差 40）', Arr(120.0, 40.0), True),
             ('有点噪点的暗场景（均值 40 标准差 8）', Arr(40.0, 8.0), True)]
    for name, arr, want in cases:
        ok, why = f(arr)
        _check('%s -> %s' % (name, '接受' if want else '拒绝'), ok == want,
               why or ('std=%.1f' % arr.std()))


def s_init_video():
    print('\n== init_video：启动路径（退化帧要重试、看门狗只在健康时挂）')
    try:
        # ★ 顺序要紧：先 import ball_detect —— 它会把 PC 侧离线视觉包
        #   E:/py_pkgs/vision 插进 sys.path（板上无此路径会忽略），numpy/cv2
        #   是从那儿来的。反过来先 import numpy 就会在没设 PYTHONPATH 时失败。
        import ball_detect
        import numpy as np
    except Exception as e:
        print('   （跳过：PC 上 import 不了 numpy/cv2/ball_detect —— %s）' % e)
        print('   （PC 侧离线视觉包可用 PY_PKGS_VISION 指定，见 ball_detect.py）')
        return
    import types as _types

    calls = {'wd': 0}

    def _fake_wd_mod():
        m = _types.ModuleType('vdma_capture')
        m.STALL_LIMIT_S = 1.2

        def start_stall_watchdog():
            calls['wd'] += 1
        m.start_stall_watchdog = start_stall_watchdog
        return m

    class FakeCapV:
        def __init__(self, frames, swap='auto'):
            self.frames = list(frames)
            self._swap_decided = swap
            self.stopped = False
            self.i = 0

        def bgr_frame(self, timeout=2.0):
            f = self.frames[min(self.i, len(self.frames) - 1)]
            self.i += 1
            return 0, f

        def stop(self):
            self.stopped = True

        def clear_errors(self):
            return 0

    real = (np.ones((8, 8, 3), np.uint8) * 120
            + np.arange(64, dtype=np.uint8).reshape(8, 8, 1))
    zero = np.zeros((8, 8, 3), np.uint8)
    print('   （numpy %s / cv2 来自离线包；热身帧均值 %.1f 标准差 %.1f）'
          % (np.__version__, real.mean(), real.std()))

    # (a) 前两帧退化 -> 应该拒掉重试，第 3 帧才认
    sys.modules['vdma_capture'] = _fake_wd_mod()
    clk = Clock()
    link = FakeLink(clk, alive=True)
    cap = FakeCapV([zero, zero, real])
    _prep(clk, link)
    calls['wd'] = 0
    warm = ball_detect._init_video(cap)
    _check('退化帧被拒、第 3 帧被接受', warm is real,
           'warm 是第 %d 次抓的那一帧（共抓了 %d 次）' % (cap.i, cap.i))
    _check('链路健康时才挂看门狗', calls['wd'] == 1, 'wd=%d' % calls['wd'])

    # (b) 一直是退化帧 -> 明确说"没拿到有效帧"，看门狗照挂（链路本身是健康的）
    clk = Clock()
    link = FakeLink(clk, alive=True)
    cap2 = FakeCapV([zero] * 5)
    _prep(clk, link)
    calls['wd'] = 0
    warm2 = ball_detect._init_video(cap2)
    _check('全是退化帧 -> 返回 None（不假装成功）', warm2 is None)
    _check('健康链路上看门狗仍然挂（帧坏不等于链路坏）', calls['wd'] == 1,
           'wd=%d' % calls['wd'])

    # (c) 链路是死的 -> 看门狗不许挂
    clk = Clock()
    link = FakeLink(clk, alive=False, heal_on=None)
    cap3 = FakeCapV([real])
    _prep(clk, link)
    calls['wd'] = 0
    ball_detect._init_video(cap3)
    _check('链路结论不是健康 -> 看门狗不挂', calls['wd'] == 0, 'wd=%d' % calls['wd'])


def s_wd_timeline():
    """看门狗判据：拿假时钟把一条"永远不好"的时间线整条喂给 WDDecider。

    为什么不连线程/寄存器一起测：真线程要真的 sleep（几十秒一轮，跑不动），
    而判据本身是纯的 —— 抽出来就是为了让这条时间线能在 PC 上 0.1 秒跑完。
    这里钉死四件事：升级必须有据可依、动作限频 5s、升级后真静默 20s、
    连败到头必须放弃且**放弃之后一次都不许再动**。
    """
    print('\n== wd_timeline：看门狗判据（假时钟驱动，不碰硬件）')
    try:
        import ball_detect                                  # noqa: F401（插视觉包路径）
        sys.modules.pop('vdma_capture', None)               # init_video 那节塞过假模块
        import vdma_capture
    except Exception as e:
        print('   （跳过：PC 上 import 不了 vdma_capture —— %s）' % e)
        return

    st0 = dict(vdma_capture.WD_STAT)
    _check('WD_STAT 初值就是那套干净的默认值',
           not any([st0['n_recover'], st0['n_esc'], st0['pulse_fails'],
                    st0['last_kind'], st0['gave_up']]), str(st0))

    # ---- (a) 一条永远不好的链路：每 0.25s 喂一拍，喂 200 秒 ----
    dec = vdma_capture.WDDecider(dict(vdma_capture.WD_STAT))
    t0, t, seq = 1000.0, 1000.0, []
    while t - t0 <= 200.0:
        a = dec.decide(t)
        if a != 'skip':
            seq.append((round(t - t0, 2), a))
        t += 0.25
    acts = [a for _, a in seq]
    print('   （时间线：%s）' % ' '.join('%s@%.2fs' % (a, ts) for ts, a in seq))
    _check('升级恰好 3 次（_WD_MAX_ESC）', acts.count('power') == 3, str(acts))
    _check('恰好放弃 1 次，且是最后一个动作',
           acts.count('giveup') == 1 and acts[-1] == 'giveup', str(acts))

    # 放弃之后：再喂 60 秒，一个动作都不许有（老代码在这里会永远脉冲下去）
    n_before = len(seq)
    while t - t0 <= 260.0:
        if dec.decide(t) != 'skip':
            seq.append((round(t - t0, 2), dec.decide(t)))
        t += 0.25
    _check('★ 放弃之后一次都不再动链路', len(seq) == n_before,
           '%d -> %d 个动作' % (n_before, len(seq)))

    # 限频 + 静默 + "升级有据可依"
    gaps = [round(b[0] - a[0], 2) for a, b in zip(seq, seq[1:])]
    _check('任意两个动作间隔 >=5s（限频硬线）',
           all(g >= vdma_capture._WD_MIN_GAP_S - 0.3 for g in gaps), str(gaps))
    pw_t = [ts for ts, a in seq if a == 'power']
    aft = [ts for ts, a in seq if ts > pw_t[0]]
    _check('每次升级之后 >=20s 真静默（连脉冲都不许打）',
           bool(aft) and aft[0] - pw_t[0] >= vdma_capture._WD_PWR_COOLDOWN_S,
           '升级@%.2fs -> 下一个动作@%.2fs' % (pw_t[0], aft[0]))
    ok_ev = True
    n_p = 0
    for _, a in seq:
        if a == 'pulse':
            n_p += 1
        elif a == 'power':
            ok_ev = ok_ev and n_p >= vdma_capture._WD_ESCALATE_N   # 有据可依
            n_p = 0
    _check('每次升级之前都攒够 %d 次"脉冲不管用"' % vdma_capture._WD_ESCALATE_N,
           ok_ev, '升级前脉冲数 %d' % n_p)

    # ---- (b) 限频边界：动作后 1s 内再卡 -> skip；满 5s -> 才动 ----
    dec = vdma_capture.WDDecider(dict(vdma_capture.WD_STAT))
    _check('第一拍就脉冲', dec.decide(2000.0) == 'pulse')
    _check('1 秒后又卡 -> 不许动（限频）', dec.decide(2001.0) == 'skip')
    _check('4.9 秒 -> 仍不许动', dec.decide(2004.9) == 'skip')
    _check('满 5 秒 -> 才允许再脉冲', dec.decide(2005.0) == 'pulse')

    # ---- (c) 干净状态下的判定：没有卡死就不该有任何状态变化 ----
    dec = vdma_capture.WDDecider(dict(vdma_capture.WD_STAT))
    before = dict(dec.st)
    dec.decide(3000.0)                       # 只有真的有人喂它才会被调用
    _check('决策器不自己长出状态（字段集合不变）',
           set(dec.st) == set(before), str(sorted(set(dec.st) ^ set(before))))


def s_display_swap():
    """显示字节序**实测**：合成 raw + map 字节流，把四种情形各跑一遍。

    钉死的是"自检全绿、图是洋红"这个坑（2026-09-27）：位域自检对内存字节序无感，
    所以显示字节序必须**量**。合成的图里有纯红/纯绿/纯蓝大块 —— 565 被字节交换
    之后这些块会变成别的颜色，判据就是靠这个分开的。
    """
    print('\n== display_swap：显示字节序实测（洋红图那个坑）')
    try:
        import ball_detect                                    # noqa: F401（插视觉包路径）
        _pc = os.path.join(os.path.dirname(HERE), 'pc')        # 黄金模型在 software/pc
        if _pc not in sys.path:
            sys.path.insert(0, _pc)
        import numpy as np
        import vision_golden as vg
        # 上面那句 `import ball_detect` 自己动过 sys.path（老版本是无脑插 '/boot'），
        # 所以**导入项目模块之前**必须把路径钉回来一次 —— 台架的职责是验刚部署的代码。
        _prefer_here()
        sys.modules.pop('ball_map_detect', None)
        import ball_map_detect
    except Exception as e:
        print('   （跳过：PC 上 import 不了 vg/ball_map_detect —— %s）' % e)
        return

    _check('★ 加载的是本目录这份代码（不是 /boot 那份旧镜像）',
           os.path.abspath(getattr(ball_map_detect, '__file__', '?')).startswith(HERE),
           getattr(ball_map_detect, '__file__', '?'))

    yy, xx = np.mgrid[0:vg.H, 0:vg.W]
    TRUE = (((xx * 31) // (vg.W - 1)) << 11 | ((yy * 63) // (vg.H - 1)) << 5
            | (((xx + yy) * 31) // (vg.W + vg.H - 2))).astype(np.uint16)
    TRUE[:, :vg.W // 4] = 0xF800                 # 纯红
    TRUE[:, vg.W // 4:vg.W // 2] = 0x07E0        # 纯绿
    TRUE[:, vg.W // 2:3 * vg.W // 4] = 0x001F    # 纯蓝
    print('   （合成图 %dx%d：彩条 + 红/绿/蓝三个纯色块）' % (vg.W, vg.H))

    def _bytes(w):
        return np.ascontiguousarray(w, np.uint16).astype('<u2').tobytes()

    def _run(tag, raw_words, map_words, swap_raw):
        raw, buf = _bytes(raw_words), _bytes(map_words)
        bs, p0, p1 = ball_map_detect.map_gate(raw, buf)
        d = ball_map_detect.display_swap_check(raw, buf, swap_raw, bs)
        print('   %s：字序关系=%s（%.1f%%/%.1f%%）  显示实测 swap0=%.1f%% swap1=%.1f%%'
              '  -> mdisp=%d（推导 %d，ok=%s）'
              % (tag, bs, p0, p1, d['pct0'], d['pct1'], d['mdisp'], d['derived'], d['ok']))
        return bs, d

    # (a) 驱动 swap=1 / PL swap_bytes=1（正常路径）：地图字 == 真值的块平均
    pix = vg.swap16(TRUE)                         # 内存里的字（真值被字节交换）
    bs, d = _run('A 驱动1+PL1（正常）', pix, vg.block_avg(vg.swap16(pix)), 1)
    _check('A 字序关系=1（黄金模型要对调才配得上）', bs == 1, str(bs))
    _check('A 显示字节序实测 = 0（地图字节别动）', d['ok'] and d['mdisp'] == 0,
           'mdisp=%d ok=%s' % (d['mdisp'], d['ok']))
    _check('★ A 错的候选必须被判死（这就是"洋红图"那张）', d['pct1'] < 60.0,
           'swap=1 只对到 %.1f%%' % d['pct1'])
    _check('A 实测与推导一致（推导 = 原图swap ^ 字序关系）', d['mdisp'] == d['derived'],
           '%d vs %d' % (d['mdisp'], d['derived']))

    # (b) 驱动 swap=0 / PL swap_bytes=0（另一条正常路径）
    bs, d = _run('B 驱动0+PL0（正常）', TRUE, vg.block_avg(TRUE), 0)
    _check('B 字序关系=0 且实测 mdisp=0', bs == 0 and d['ok'] and d['mdisp'] == 0,
           'bs=%s mdisp=%d ok=%s' % (bs, d['mdisp'], d['ok']))

    # (c) 驱动 swap=0 / PL 却按 swap_bytes=1 配（配置不一致 = 推导会翻面）
    bs, d = _run('C 驱动0+PL1（不一致）', TRUE, vg.block_avg(vg.swap16(TRUE)), 0)
    _check('C 字序关系=1 -> 推导值 1', bs == 1 and d['derived'] == 1,
           'bs=%s derived=%d' % (bs, d['derived']))
    _check('C 显示字节序落在 1（实测或退回都必须落在 1）', d['mdisp'] == 1,
           'mdisp=%d（ok=%s: %s）' % (d['mdisp'], d['ok'], d['why']))

    # (d) 地图内容本身坏了（随机字）-> 判不动，必须退回推导值而不是硬报一个
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 0x10000, (vg.BH, vg.BW), dtype=np.uint16)
    bs, d = _run('D 地图是噪声', TRUE, noise, 0)
    _check('D 判不动 -> ok=False 且退回推导值', (not d['ok']) and d['mdisp'] == d['derived'],
           'ok=%s mdisp=%d derived=%d' % (d['ok'], d['mdisp'], d['derived']))

    # (e) 纯白场（0xFFFF 是自反字：两种字节序渲染完全一样）-> 测不出来，退回不更差
    white = np.full(TRUE.shape, 0xFFFF, np.uint16)
    bs, d = _run('E 纯白场', white, vg.block_avg(white), 0)
    _check('E 纯白场测不出来（不许假装量出来了）', not d['ok'],
           'swap0=%.1f%% swap1=%.1f%%' % (d['pct0'], d['pct1']))
    _check('E 退回推导值（而且两种渲染本来就一样，退回不会更差）',
           d['mdisp'] == d['derived'] and abs(d['pct0'] - d['pct1']) < 0.5,
           '差 %.2f%%' % abs(d['pct0'] - d['pct1']))


def s_doctor():
    """link_doctor：板子不舒服时第一个被叫来的工具，必须在台架上跑得通。

    它 2026-09-29 被重构过（删掉一份重复的电源体检、救援动作改为交给 dvp_regs），
    而这类"救援脚本"正是最不能靠肉眼信的东西 —— 用假 /dev/mem + 假 FastMem 把
    `--sensor-on` / `--sensor-cycle` / `--pulse` 三条路各跑一遍，检查：
      · 被按住的静止位会被放开，且放开后**读回是干净的**
      · 该补的 ctrl[4]（重跑 SCCB）补了
      · 中途被打断（sleep 里 KeyboardInterrupt）也不留锁位
    """
    print('\n== doctor：link_doctor 的救援路径（假寄存器）')
    try:
        sys.modules.pop('link_doctor', None)
        import link_doctor
    except Exception as e:
        print('   （跳过：import 不了 link_doctor —— %s）' % e)
        return
    import types as _types

    class FakeFastMem:
        """link_doctor 的 FastMem 接口：rd_u32/wr_u32/close（同一个假链路）。"""

        def __init__(self, link, base, size=0x1000):
            self.link, self.base = link, base

        def rd_u32(self, off):
            return self.link.read(_alias(self.base), off)

        def wr_u32(self, off, val):
            self.link.write(_alias(self.base), off, val)

        def close(self):
            pass

    def _alias(base):
        return {0x43000000: 'vdma', 0x43A00000: 'p0', 0x43A10000: 'p1',
                0x43A20000: 'p2', 0x43A30000: 'p3', 0x43B10000: 'ctrl',
                0x43B20000: 'vstat'}.get(base, base)

    fake = _types.ModuleType('fastmem')
    fake.FastMem = lambda base, size=0x1000: FakeFastMem(CUR['link'], base, size)
    CUR = {}

    def run(argv, link, boom=None):
        CUR['link'] = link
        sys.modules['fastmem'] = fake
        clk = Clock()
        link.clk = clk
        # ★ 连 link_doctor 自己的 time 也换成假时钟：不然它的 sleep(2.0)/(3.0) 是**真的**
        #   睡（一轮 ~15s，三轮 45s），台架就跑不动了 —— 而且"在哪一次 sleep 上出事"
        #   必须是确定的（boom=1 要落在救援序列里）。
        for mod in (link_boot, dvp_regs, link_doctor):
            mod.time = clk
        dvp_regs.open_regs = lambda *a, **k: link
        clk.boom_on_sleep = boom
        old = sys.argv
        sys.argv = ['link_doctor.py'] + argv
        try:
            rc = link_doctor.main()
        finally:
            sys.argv = old
            clk.boom_on_sleep = None
        return rc

    # (a) 电源位被按住 + --sensor-on：必须放开 + 补 ctrl[4] + 读回干净
    link = FakeLink(Clock(), alive=False)
    link.ctrl = 0x383                     # bit0/bit1 举着（2026-09-29 事故原值）
    n_p3 = len([1 for w in link.writes if (w[1], w[2]) == ('p3', 0)])
    rc = run(['--sensor-on'], link)
    after = [w for w in link.writes[n_p3:] if (w[1], w[2]) == ('p3', 0)]
    _check('--sensor-on：静止位被放开且读回干净',
           (link.ctrl & dvp_regs.IDLE_MASK) == 0, 'ctrl=0x%03X' % link.ctrl)
    _check('--sensor-on：补发了 ctrl[4] 重跑 SCCB（掉电后不补就不出流）',
           any(v & dvp_regs.BIT_SEN_RST for _, _, _, v in after),
           '%d 笔 p3 写' % len(after))
    _check('--sensor-on：dbg_sel 没被碰掉', (link.ctrl & 0x380) == 0x380,
           'ctrl=0x%03X' % link.ctrl)
    _check('退出码 = 1（链路本来就不健康）', rc == 1, 'rc=%d' % rc)
    _check('scl 探针在动（SCCB 在跑的尺子，link_doctor 靠它分"没跑"和"跑了没接受"）',
           int(link.scl) > 0, 'scl=%d' % int(link.scl))

    # (b) --sensor-cycle：断电重上电整条走完，最后静止位还是干净的
    link = FakeLink(Clock(), alive=True)
    rc = run(['--sensor-cycle'], link)
    _check('--sensor-cycle：静止位干净收尾', (link.ctrl & dvp_regs.IDLE_MASK) == 0,
           'ctrl=0x%03X' % link.ctrl)
    _check('--sensor-cycle：真的做过断电（bit0|bit1 出现过）',
           any((v & (dvp_regs.BIT_XCLK_OFF | dvp_regs.BIT_PWDN))
               for _, _, _, v in link.writes), '%d 笔写' % len(link.writes))

    # (c) --sensor-cycle 中途被打断（sleep 里 KeyboardInterrupt）：**不许留锁位**
    link = FakeLink(Clock(), alive=True)
    try:
        run(['--sensor-cycle'], link, boom=1)
        raised = None
    except KeyboardInterrupt as e:
        raised = e
    _check('被打断时异常确实抛出（没被吞）', raised is not None, str(raised))
    _check('★ 被打断也不留锁位（静止位干净）',
           (link.ctrl & dvp_regs.IDLE_MASK) == 0, 'ctrl=0x%03X' % link.ctrl)


def s_path_pin():
    """★ 回归测试："/boot 旧镜像盖住真代码" 这个坑（2026-09-29 板上实锤）。

    做法不打折：造一个临时目录，里面放一份**诱饵** ball_map_detect.py（只有一句
    MARK），把它插到 sys.path[0]（这正是 link_doctor 干的事），先证明"确实会撞上
    诱饵"，再调 _prefer_here() 证明"拿回来的是本目录这份、且带着本次部署的新函数"。
    在 PC 上跑（没有 /boot）也一样有效 —— 它验的是机制，不是那台机器。
    """
    print('\n== path_pin：路径钉回本目录（/boot 旧镜像那个坑）')
    try:
        import ball_detect                                # noqa: F401（插视觉包路径）
        # ★ 它自己也会动 sys.path（老版本是无脑插 '/boot'）—— 板上实测就是这么
        #   把后面那句 import ball_map_detect 顶到旧镜像上的。这里钉住这个不变量：
        #   import 完别人之后，'/boot' 不许出现在 sys.path 的最前面。
        _check('★ import ball_detect 之后 /boot 没被顶到 sys.path[0]（它自己也修了）',
               (sys.path[0] if sys.path else '') != '/boot' or HERE == '/boot',
               'path[:3]=%s' % (sys.path[:3],))
        _pc = os.path.join(os.path.dirname(HERE), 'pc')
        if _pc not in sys.path:
            sys.path.insert(0, _pc)
        import tempfile
        decoy_dir = tempfile.mkdtemp(prefix='decoy_bmd_')
        with open(os.path.join(decoy_dir, 'ball_map_detect.py'), 'w') as f:
            f.write('MARK = "decoy"\n')
    except Exception as e:
        print('   （跳过：环境里 import 不了依赖 —— %s）' % e)
        return

    sys.path.insert(0, decoy_dir)
    try:
        sys.modules.pop('ball_map_detect', None)
        import ball_map_detect
        _check('★ 诱饵被顶到 sys.path[0] 时，import 确实先撞上它（坑是真的）',
               getattr(ball_map_detect, 'MARK', None) == 'decoy',
               'file=%s' % getattr(ball_map_detect, '__file__', '?'))
        sys.modules.pop('ball_map_detect', None)
        _prefer_here()
        import ball_map_detect
        real = os.path.abspath(getattr(ball_map_detect, '__file__', '?'))
        _check('★ _prefer_here() 之后拿回本目录这份真代码',
               real.startswith(HERE) and not hasattr(ball_map_detect, 'MARK'), real)
        _check('  而且它带着本次部署的新函数（显示字节序实测那支）',
               hasattr(ball_map_detect, 'display_swap_check'), real)
    except Exception as e:
        _check('path_pin：真代码 import 得动', False, '%s: %s' % (type(e).__name__, str(e)[:60]))
    finally:
        sys.path.remove(decoy_dir)
        sys.modules.pop('ball_map_detect', None)
        try:
            os.remove(os.path.join(decoy_dir, 'ball_map_detect.py'))
            os.rmdir(decoy_dir)
        except OSError:
            pass


def s_load_bit():
    """★ load_bit 的下 bit 阶梯（假 pl_probe + 假 Bitstream，不碰硬件）。

    钉死的是最贵的那条不变量：**pl_probe rc=0（流水线正在跑）时一次都不许下 bit**
    —— 那正是"下飞 -> PCFG_INIT 超时 -> 半编程 -> AXI 挂死总线，只能断电"的成因。
    顺带钉住：开机窗口内不下、说不清不下、md5 对不上不下、下完必须复查。
    """
    print('\n== load_bit：下 bit 阶梯（能不能下由 pl_probe 说了算）')
    import types
    try:
        sys.modules.pop('load_bit', None)
        import link_boot                                     # noqa: F401
        import load_bit
    except Exception as e:
        print('   （跳过：import 不了 load_bit —— %s）' % e)
        return

    calls = {'dl': 0, 'probe': []}
    real_probe, real_md5 = load_bit._pl_probe, load_bit.md5_of
    real_window = link_boot.boot_window
    real_pynq = sys.modules.get('pynq')

    class _FakeBS(object):
        def __init__(self, path):
            self.path = path

        def download(self):
            calls['dl'] += 1

    fake_pynq = types.ModuleType('pynq')
    fake_pynq.Bitstream = _FakeBS
    sys.modules['pynq'] = fake_pynq
    # 模拟"文件就是期望的那份"：直接取 _expected_md5 的结论，**别抄常量**
    # —— 抄字面量会过期（WANT_MD5_FALLBACK 就比现役 bit 老一代；且改名后
    # 旧名 WANT_MD5 直接 AttributeError，被 main 的 except 吞成 rc=9，
    # 只在板上跑才暴露）。取 _expected_md5([]) 则 PC（用内置兜底）与
    # 板上（用真边车 25dae086）同时成立。
    load_bit.md5_of = lambda p, chunk=0: load_bit._expected_md5([])[0]

    seq = {'rcs': []}

    def _probe():
        rc = seq['rcs'].pop(0) if seq['rcs'] else 9
        calls['probe'].append(rc)
        return rc

    load_bit._pl_probe = _probe        # ★ 忘了这一行，跑的就是**真的** pl_probe 了

    def _run(tag, rcs, want_rc, want_dl, want_probes=None, window=False):
        calls['dl'] = 0
        calls['probe'] = []
        seq['rcs'] = list(rcs)
        if window:
            link_boot.boot_window = lambda *a, **k: (True, 30.0, 120.0)
        else:
            link_boot.boot_window = lambda *a, **k: (False, 400.0, 0.0)
        got = load_bit.main([])
        print('   [%s] rc=%d（期望 %d）  下 bit 次数=%d  pl_probe 调用=%s'
              % (tag, got, want_rc, calls['dl'], calls['probe']))
        _check('%s：退出码 %d' % (tag, want_rc), got == want_rc, 'got=%d' % got)
        _check('%s：下 bit 次数 = %d' % (tag, want_dl), calls['dl'] == want_dl,
               'dl=%d' % calls['dl'])
        if want_probes is not None:
            _check('%s：pl_probe 调用次数 = %d' % (tag, want_probes),
                   len(calls['probe']) == want_probes, str(calls['probe']))

    try:
        _run('① 流水线正在跑(rc=0)', [0], load_bit.RC_REFUSE, 0, want_probes=1)
        _run('② 说不清(rc=9)', [9], load_bit.RC_UNKNOWN, 0, want_probes=1)
        _run('③ bit 不在(rc=1) -> 下 -> 复查成功', [1, 2], load_bit.RC_OK, 1, want_probes=2)
        _run('④ bit 在但没出流(rc=2) -> 重下', [2, 2], load_bit.RC_OK, 1, want_probes=2)
        _run('⑤ 下完仍不是我们的(rc=1,1)', [1, 1], load_bit.RC_NOT_OURS, 1, want_probes=2)
        _run('⑥ 开机窗口内', [], load_bit.RC_UNKNOWN, 0, want_probes=0, window=True)
        load_bit.md5_of = lambda p, chunk=0: 'deadbeef'
        _run('⑦ md5 对不上', [1], load_bit.RC_REFUSE, 0, want_probes=1)
        _check('⑦ md5 对不上时**没有**下 bit（文件闸门先于下载）', calls['dl'] == 0,
               'dl=%d' % calls['dl'])
    finally:
        load_bit._pl_probe, load_bit.md5_of = real_probe, real_md5
        link_boot.boot_window = real_window
        if real_pynq is None:
            sys.modules.pop('pynq', None)
        else:
            sys.modules['pynq'] = real_pynq
        sys.modules.pop('load_bit', None)


def s_scene_gate():
    print('\n== scene_gate：现场门禁（热身帧均值 -> 放行/提醒/拒跑）')
    try:
        import ball_detect
    except Exception as e:
        print('   （跳过：import ball_detect 失败 —— %s）' % e)
        return
    g = ball_detect.scene_gate
    cases = [('镜头对场区+灯亮（实测 91.8）', 91.8, 'ok'),
             ('正常下限附近（55.0）', 55.0, 'ok'),
             ('偏低（实测没到这档，45.0）', 45.0, 'low'),
             ('★ 镜头朝下对着桌面（实测 13.5）', 13.5, 'dark'),
             ('几乎全黑（2.0）', 2.0, 'dark'),
             ('NaN（读不出来）', float('nan'), 'dark')]
    for name, m, want in cases:
        lvl, why = g(m)
        _check('%s -> %s' % (name, want), lvl == want, why or ('均值 %.1f' % m))
    _check('阈值可调（dark_min=20 时 25.0 只算偏低）', g(25.0, dark_min=20.0)[0] == 'low')


def s_frame_fuse():
    """坏帧熔断（frame_sane_map）：两个证据**同时**成立才熔断。

    锚点有两份**真数据**（离线的 map dump，23KB/张，就放在本文件旁边）：
      fx_map_clean.bin = 2026-09-27 真场景（候选 1 个，行差均值 5.5）-> 必须放行
      fx_map_torn.bin  = 2026-09-29 深夜那张洋红撕裂帧（候选 16，行差 18.0）-> 必须熔断
    """
    print('\n== frame_fuse：坏帧熔断（候选多 + 相邻行对不上 -> 这帧的球不可信）')
    try:
        # ★ 顺序要紧：先 import ball_detect —— 它会把 PC 侧离线视觉包 E:/py_pkgs/vision
        #   插进 sys.path（板上无此路径会忽略），cv2/numpy 是从那儿来的。单跑本场景时
        #   少了这一步就是 "No module named 'cv2'"（第一次单跑就撞上了）。
        import ball_detect            # noqa: F401  （要的是它的 sys.path 副作用）
        import ball_map_detect
        import numpy as np
    except Exception as e:
        print('   （跳过：import 失败 —— %s）' % e)
        return
    f = ball_map_detect.frame_sane_map

    # ---- 真数据（有 fixture 就验，没有就说明白，不假装验过）----
    import cv2
    BH, BW = ball_map_detect.BH, ball_map_detect.BW
    for fn, n_cand, want in (('fx_map_clean.bin', 1, True),
                             ('fx_map_torn.bin', 16, False)):
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            print('   （真数据 fixture 不在：%s —— 这一段没验）' % fn)
            continue
        m = np.frombuffer(open(p, 'rb').read(), np.uint16).reshape(BH, BW)
        bgr = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
        bgr = cv2.cvtColor(np.ascontiguousarray(bgr), cv2.COLOR_BGR5652BGR)
        ok, why, rc = f(bgr, n_cand)
        _check('%s（候选 %d）-> %s' % (fn, n_cand, '放行' if want else '熔断'),
               ok == want, '行差 %.1f  %s' % (rc, why))

    # ---- 合成：单独一个证据都不许熔断（防误杀真场景）----
    torn = np.zeros((90, 128, 3), np.uint8)
    for r in range(90):
        torn[r] = 200 if r % 2 else 0                     # 横条：行内平、行间跳满量程
    _check('合成撕裂帧（0/200 横条，候选 16）-> 熔断', f(torn, 16)[0] is False,
           '行差 %.1f' % f(torn, 16)[2])
    _check('同一张撕裂帧，但候选只有 1 个 -> 放行（证据不足不熔断）',
           f(torn, 1)[0] is True, '行差 %.1f' % f(torn, 1)[2])
    smooth = np.zeros((90, 128, 3), np.uint8)
    for r in range(90):
        smooth[r] = int(255.0 * r / 89)                   # 竖渐变：行差小
    _check('合成真场景帧（候选 1）-> 放行', f(smooth, 1)[0] is True,
           '行差 %.1f' % f(smooth, 1)[2])
    _check('合成真场景帧，但候选有 16 个 -> 放行（行差不够大，不误杀）',
           f(smooth, 16)[0] is True, '行差 %.1f' % f(smooth, 16)[2])
    _check('行差是**有符号**算的（uint8 相减会回绕成小值 —— 那会让撕裂帧溜过去）',
           f(torn, 1)[2] > 100.0, '行差 %.1f（0/200 横条应该是 200 左右）' % f(torn, 1)[2])
    # 阈值 14 的两侧（把标定摆在明面上）：真场景 5.5/8.5 / 轻度条纹 ~14 / 真撕裂 18.0
    mild = np.zeros((90, 128, 3), np.uint8)
    rng = np.random.RandomState(7)
    for r in range(90):
        mild[r] = rng.randint(0, 40)
    _check('轻度条纹（行差 ~14，压在阈值下）-> 放行：阈值不追求把"有点花的帧"也杀光',
           f(mild, 16)[0] is True, '行差 %.1f' % f(mild, 16)[2])


def s_input_hub():
    """★ P2 输入层（手机网页 + 有线键盘）：两个源、一份状态、一个读法。

    为什么值得单开一节：键盘**还没插上**（用户明天才找键盘），手机那条路也只在
    演示时才有人碰 —— 而它俩恰好是"现场最显眼、最不能挂"的东西（评委面前按不动）。
    所以：键盘映射用**纯函数**喂键码测（不碰 /dev/input）、手机那条路**真起一个
    HTTP 服务真发请求**测（不碰相机、不碰 PL、不碰 VDMA，纯 loopback）。
    """
    import ast
    import json as _json
    import socket
    import subprocess
    import tempfile
    import time as _t
    import urllib.request

    import v3_input as vi

    print('\n== input_hub：手机网页 / 有线键盘 共用一份状态（P2）')
    d = tempfile.mkdtemp(prefix='v3in_sc_')
    p = os.path.join(d, 'st.json')
    shp = os.path.join(d, 'shot.json')          # 发射结果的替身文件（/shot 读它）
    bp2 = os.path.join(d, 'balls.json')         # 场上球列表替身文件（/balls 读它）
    mp = os.path.join(d, 'match.json')          # 对局状态替身文件（/match 读它，M2.3）

    # ---- 1) 键盘映射：喂键码，验的是"按一下到底改了多少"（项目规则）----
    KEY_W, KEY_S, KEY_A, KEY_D = 17, 31, 30, 32
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = 103, 108, 105, 106
    KEY_SPC, KEY_Q, KEY_X, KEY_R = 57, 16, 45, 19
    a, s, act = vi.key_apply(0.0, 400.0, KEY_W)
    _check('W -> +2°（规则）', (a, s, act) == (2.0, 400.0, 'set'), '%.1f/%.0f' % (a, s))
    a, s, act = vi.key_apply(0.0, 400.0, KEY_S)
    _check('S -> -2° 环绕为 358°（2026-10-28 绝对角）',
           (a, s, act) == (358.0, 400.0, 'set'), '%.1f/%.0f' % (a, s))
    a, s, act = vi.key_apply(0.0, 400.0, KEY_D)
    _check('D -> +20 px/s（规则）', (a, s, act) == (0.0, 420.0, 'set'), '%.1f/%.0f' % (a, s))
    a, s, act = vi.key_apply(0.0, 400.0, KEY_A)
    _check('A -> -20 px/s（规则）', (a, s, act) == (0.0, 380.0, 'set'), '%.1f/%.0f' % (a, s))
    # ★★ 2026-10-06 M2.1 键位变更：**方向键从"P1 别名"正式划归 P2**（规则书
    #    §6.1 左右分区）。旧断言"↑ 与 W 等价"在这里**刻意改掉** —— 改键位先搜
    #    台架，铁律；下面按新双人键位表重新钉。
    patch, act, pl = vi.apply_key({'angle': 0.0, 'speed': 400.0}, KEY_UP)
    _check('★ 双人键位：↑ 归 P2（角度 +2°）', pl == 2 and act == 'set'
           and patch.get('angle') == 2.0, str(patch))
    patch, act, pl = vi.apply_key({'p2_angle': 0.0, 'p2_speed': 400.0}, KEY_LEFT)
    _check('★ 双人键位：← 归 P2（速度 -20）', pl == 2
           and patch.get('speed') == 380.0, str(patch))
    patch, act, pl = vi.apply_key({}, 28)                    # 回车
    _check('★ 双人键位：回车 = P2 发射', pl == 2 and act == 'fire', str(patch))
    patch, act, pl = vi.apply_key({}, KEY_X)                 # X = 45
    _check('★ 双人键位：X = P1 选球 +1',
           pl == 1 and act == 'sel' and patch.get('sel') == 1, str(patch))
    patch, act, pl = vi.apply_key({}, 52)                    # . = 下一颗
    _check('★ 双人键位：. = P2 选球 +1',
           pl == 2 and act == 'sel' and patch.get('sel') == 1, str(patch))
    # ★ 2026-10-01 深夜（用户三条之③）：空格从"复位"改成**发射**（发射模式的主入口）；
    #   复位挪到 R。台架上原来断言的是旧键位 —— 键位一改，这里必须跟着改（不然
    #   回归网就在骗人：它会"通过"一个没人按得出来的键位）。
    a, s, act = vi.key_apply(10.0, 900.0, KEY_SPC)
    _check('空格 -> **发射**（角度/速度原样带出去，计数由写状态那侧 +1）',
           (a, s, act) == (10.0, 900.0, 'fire'), '%.1f/%.0f/%s' % (a, s, act))
    a, s, act = vi.key_apply(10.0, 900.0, KEY_R)
    _check('R -> 复位 0°/400', (a, s, act) == (0.0, 400.0, 'reset'))
    a, s, act = vi.key_apply(0.0, 400.0, KEY_Q)
    _check('Q -> 改磁场（2026-10-14 让位：原 quit 挪给 ESC 作取消信号）',
           act == 'edit')
    a, s, act = vi.key_apply(0.0, 400.0, 46)
    _check('C -> 加洞口', act == 'hole')
    a, s, act = vi.key_apply(0.0, 400.0, 88)     # F12：未映射的键
    _check('不认识的键 -> 原样不动（不许瞎改）', (a, s, act) == (0.0, 400.0, None))
    # ★ P1 用 KEY_W（17）；KEY_UP 是 P2，key_apply 对 P2 原样返回（旧断言曾因此假通过）
    a, s, act = vi.key_apply(vi.ANG_MAX, 400.0, KEY_W)
    _check('角度 360 环绕（%.0f+2 -> 2，P1 W）' % vi.ANG_MAX,
           abs(a - 2.0) < 1e-9 and act == 'set', '%.1f/%s' % (a, act))
    a, s, _ = vi.key_apply(0.0, vi.SPD_MIN, KEY_A)
    _check('速度底到 %.0f px/s 就钳住' % vi.SPD_MIN, s == vi.SPD_MIN, '%.0f' % s)

    # ---- 1.5) ★ 2026-10-07 实锤修复的回归网：键盘基准 = **文件现值** ----
    # 现场事故：手机把角度调到 20°（/set 一直基于"文件现值"），键盘一按 W 跳回
    # 18°——键盘原来拿**进程启动快照 + 自己写过的值**当基准，两源各算各的，
    # 后写者把对方设的值带走（两人一人键盘一人手机时最明显）。修法 = key_event()
    # 按键前先读文件；这四条把口径钉死（含"键盘连按"与"不碰另一玩家"两条边）。
    pkb = os.path.join(d, 'st_keybase.json')
    vi.write_state(angle=20.0, speed=400.0, src='phone', path=pkb)
    cur, act, pl = vi.key_event(pkb, KEY_W)
    _check('★ 键盘基准=文件现值：手机设 20° 后按 W -> 22°（旧 bug 会退回 18°）',
           act == 'set' and cur is not None and abs(cur['angle'] - 22.0) < 1e-6,
           'angle=%s' % (cur['angle'] if cur else None))
    cur, act, pl = vi.key_event(pkb, KEY_W)
    _check('★ 键盘自己连按照常逐笔 +2（22 -> 24）',
           cur is not None and abs(cur['angle'] - 24.0) < 1e-6,
           'angle=%s' % (cur['angle'] if cur else None))
    vi.write_state(player=2, angle=10.0, speed=500.0, src='phone', path=pkb)
    cur, act, pl = vi.key_event(pkb, KEY_W)
    _check('★ 键盘写 P1 不碰 P2 组（P2 10° 保留、P1 24->26）',
           cur is not None and abs(cur['p2_angle'] - 10.0) < 1e-6
           and abs(cur['angle'] - 26.0) < 1e-6,
           'P1=%.1f P2=%.1f' % (cur['angle'], cur['p2_angle']))
    cur, act, pl = vi.key_event(pkb, KEY_SPC)
    _check('★ 空格经 key_event 仍是"发射一笔"（fire +1、角度不动）',
           act == 'fire' and cur is not None and cur['fire'] == 1
           and abs(cur['angle'] - 26.0) < 1e-6,
           'fire=%s' % (cur['fire'] if cur else None))

    # ---- 2) 手机那条路：真起服务、真发请求（loopback，不碰任何硬件）----
    sk = socket.socket()
    sk.bind(('127.0.0.1', 0))
    port = sk.getsockname()[1]
    sk.close()
    proc = None
    try:
        proc = subprocess.Popen([sys.executable, '-u', os.path.join(HERE, 'v3_input.py'),
                                 'serve', '--host', '127.0.0.1', '--port', str(port),
                                 '--path', p],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=dict(os.environ, V3_SHOT_PATH=shp,
                                         V3_BALLS_PATH=bp2, V3_MATCH_PATH=mp))
        base = 'http://127.0.0.1:%d' % port
        up = False
        for _ in range(40):                     # 最多等 4 秒起来
            try:
                urllib.request.urlopen(base + '/state', timeout=0.5).read()
                up = True
                break
            except Exception:
                _t.sleep(0.1)
        _check('手机那条路：服务起来了', up, base)
        _check('★ 起服务本身**不写**状态（没人动过就不该有值，否则会盖掉命令行给的角）',
               not os.path.exists(p), '文件存在=%s' % os.path.exists(p))

        if up:
            urllib.request.urlopen(base + '/', timeout=2).read()
            html = urllib.request.urlopen(base + '/', timeout=2).read().decode('utf-8')
            _check('控制页出得来（有滑条 + 按钮）',
                   ('<input type="range"' in html) and ('inc(' in html),
                   '%d 字节' % len(html))

            # ★★ 2026-10-06（用户："网页应该实时刷新有几个球——不然场上有五个球
            #   只能控制 123 球"）：动态选球 —— 页面接上 /balls，端到端真发请求。
            _check('★ 页面接上动态球列表（pullBalls + 动态渲染容器）',
                   ('pullBalls' in html) and ('ballrow' in html))
            _check('★ 动态选球只有一个数据源：旧的写死 b11/b13 按钮已移除',
                   ('id="b11"' not in html) and ('id="b13"' not in html))
            jb = _json.loads(urllib.request.urlopen(
                base + '/balls', timeout=2).read().decode('utf-8'))
            _check('/balls：文件不在 -> 空表（不抛）', jb.get('balls') == [],
                   str(jb))
            with open(bp2, 'w') as f:
                f.write('{"t":1,"balls":[{"no":1,"cls":"blue"},'
                        '{"no":2,"cls":"red"},{"no":5,"cls":"red"}]}')
            jb = _json.loads(urllib.request.urlopen(
                base + '/balls', timeout=2).read().decode('utf-8'))
            _check('★ /balls 端到端：游戏写 3 颗 -> 网页读到 3 颗（号+色）',
                   [b.get('no') for b in jb.get('balls', [])] == [1, 2, 5]
                   and jb['balls'][0].get('cls') == 'blue',
                   str(jb.get('balls')))
            # ★ 2026-10-07 M2.3：对局状态端点（手机看局/比分/弹药/就绪）
            _check('★ 页面接上对局状态栏（pullMatch + #match + 链路告警条）',
                   ('pullMatch' in html) and ('id="match"' in html)
                   and ('linkwarn' in html))
            jm = _json.loads(urllib.request.urlopen(
                base + '/match', timeout=2).read().decode('utf-8'))
            _check('/match：文件不在 -> 空 dict（不抛）', jm == {}, str(jm))
            with open(mp, 'w') as f:
                f.write('{"on":true,"round":3,"max":10,"phase":"PREP",'
                        '"p1":{"score":20,"red":3,"blue":2,"ready":true},'
                        '"p2":{"score":10,"red":3,"blue":2,"ready":false},'
                        '"timer":null,"msg":"","link_n":0,"link_warn":false}')
            jm = _json.loads(urllib.request.urlopen(
                base + '/match', timeout=2).read().decode('utf-8'))
            _check('★ /match 端到端：游戏写对局 -> 网页读到（轮次/比分/就绪）',
                   jm.get('round') == 3 and jm.get('p1', {}).get('score') == 20
                   and jm.get('p1', {}).get('ready') is True
                   and jm.get('p2', {}).get('ready') is False,
                   str(jm)[:70])

            urllib.request.urlopen(base + '/set?angle=8&speed=550', timeout=2).read()
            hub = vi.InputHub(path=p, angle=0.0, speed=400.0, verbose=False)
            a, s, ch = hub.poll()
            _check('手机 /set 一笔 -> 游戏侧 poll 立刻吃到',
                   (a, s, ch) == (8.0, 550.0, True), '%.1f/%.0f' % (a, s))
            _check('来源记的是 phone', hub.src == 'phone', hub.src)
            a, s, ch = hub.poll()
            _check('没人动 -> 快路径（changed=False，不再解析）', ch is False)

            st = _json.loads(urllib.request.urlopen(base + '/state', timeout=2).read())
            _check('页面能读回当前值（显示用）',
                   st.get('angle') == 8.0 and st.get('speed') == 550.0, str(st))

            # 键盘写一笔（模拟按下 D），手机页面应该读到**键盘**写的那笔
            a2, s2, act = vi.key_apply(a, s, KEY_D)
            vi.write_state(angle=a2, speed=s2, src='key', path=p, note='D')
            a3, s3, ch = hub.poll()
            _check('键盘写一笔 -> 同一个 hub 也能吃到（两个源一份状态）',
                   (a3, s3, ch) == (8.0, 570.0, True), '%.1f/%.0f' % (a3, s3))
            _check('来源变成 key（谁写的看得出来）', hub.src == 'key', hub.src)
            st = _json.loads(urllib.request.urlopen(base + '/state', timeout=2).read())
            _check('手机页面看得到键盘那笔（页面与游戏同一份）', st.get('src') == 'key',
                   str(st.get('src')))

            # ★ 发射键的通路（2026-10-01 深夜，用户③：手机上要有"发射"键）：
            #   /fire 一笔 = fire 计数 +1（游戏侧读上升沿）；/set 普通改值**不动**它；
            #   /shot 读**游戏写的**那一杆结果（这里用替身文件验通路与 UTF-8 编码）。
            st = _json.loads(urllib.request.urlopen(base + '/shot', timeout=2).read())
            _check('没人发过 -> /shot 回空对象', st == {}, str(st)[:40])
            st = _json.loads(urllib.request.urlopen(
                base + '/fire?angle=8&speed=570', timeout=2).read())
            _check('手机 /fire 一笔 -> fire 计数 1（发射键的通路）',
                   st.get('fire') == 1, 'fire=%s' % st.get('fire'))
            hub.poll()
            _check('游戏侧 poll 读到发射计数（读的是上升沿）', hub.fire == 1,
                   'fire=%d' % hub.fire)
            st = _json.loads(urllib.request.urlopen(
                base + '/set?angle=9&speed=570', timeout=2).read())
            _check('/set 普通改值**不动** fire 计数（不许把发射吞掉）',
                   st.get('fire') == 1, 'fire=%s' % st.get('fire'))
            with open(shp, 'w', encoding='utf-8') as f:
                _json.dump({'ok': True, 'kind': 'goal',
                            'msg': '从左边发射，角 8.0°：进洞（GOAL）'},
                           f, ensure_ascii=False)
            st = _json.loads(urllib.request.urlopen(base + '/shot', timeout=2).read())
            _check('手机 /shot 读回游戏写的那一杆（中文 UTF-8 不乱码）',
                   st.get('msg') == '从左边发射，角 8.0°：进洞（GOAL）',
                   repr(st.get('msg'))[:40])

            # 手滑把状态文件删了（演示现场真会有人干这个）—— 必须"保持上一次 + 记账"
            os.unlink(p)
            a4, s4, ch = hub.poll()
            _check('★ 状态文件被删 -> 保持上一次的值（绝不偷偷回默认）',
                   (a4, s4) == (8.0, 570.0), '%.1f/%.0f' % (a4, s4))
            _check('★ 并且记账 n_bad+1（汇总里要报得出来）', hub.n_bad >= 1,
                   'n_bad=%d' % hub.n_bad)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        for f in (p, shp, bp2):
            try:
                os.unlink(f)
            except Exception:
                pass
        try:
            os.rmdir(d)
        except Exception:
            pass

    # ---- 3) 铁律：不带 --v3-input 时，游戏循环**一次都不碰**输入层 ----
    #  静态验（真跑 ball_game 要 pynq + 相机，这里没有）：ball_game 里凡是**真正用到**
    #  v3_hub 的地方（取属性/调方法）都必须罩着 —— 罩子可以是 `v3_hub is not None`
    #  或 `v3_input is not None`（v3_hub 只在前者的分支里被造出来）。
    #  ★ 第一版检查器只认 `if v3_hub is not None:` 这一种写法，于是把
    #    `if V3_STAT[...] or v3_hub is not None:` 和 `if v3_input is not None:` 里的
    #    正常用法全判成漏网（假报警）。所以这里**在整棵 test 表达式里搜**那个比较，
    #    而不是只看 test 的左半边。
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        src = f.read()
    tree = ast.parse(src)
    # ★ 计数单位是**每处使用**，不是每行：518 行上有 path/n_change/n_bad/src 四处，
    #   第一版拿 set 去重，5 行 != 8 处 -> 假报警（8 处其实全罩着）。
    uses, n_guard, bad_lines = [], 0, set()

    def _is_guard(test):
        for n in ast.walk(test):
            if (isinstance(n, ast.Compare) and isinstance(n.left, ast.Name)
                    and n.left.id in ('v3_hub', 'v3_input')):
                return True
        return False

    def walk(node, ok):
        nonlocal n_guard
        for ch in ast.iter_child_nodes(node):
            g = ok
            if isinstance(ch, ast.If) and _is_guard(ch.test):
                g = True
            if (isinstance(ch, ast.Attribute) and isinstance(ch.value, ast.Name)
                    and ch.value.id == 'v3_hub'):
                uses.append(ch.lineno)
                if ok:
                    n_guard += 1
                else:
                    bad_lines.add(ch.lineno)
            walk(ch, g)

    walk(tree, False)
    _check('ball_game 里 v3_hub 的每处使用都在"输入层开着吗"的罩子里',
           bool(uses) and n_guard == len(uses),
           '用 %d 处，罩住 %d 处，漏网行 %s'
           % (len(uses), n_guard, sorted(bad_lines) or '无'))
    _check('默认值就是 None（没给 --v3-input 时输入层根本不存在）',
           'v3_hub = None' in src and 'v3_input = None' in src)
    _check('输入层只喂 V3 那一簇（--v3 没开时它被显式关掉，不是默默失效）',
           'v3_input = None' in src and '--v3 没开' in src)
    # ★ 2026-10-07（启动阶段 [输入] 日志刷屏 1.5 万行实锤）：changed2 只在"文件内容
    #   变化那一次 parse"里被重算，内容没变的快路径 return 时**不会清 False** ——
    #   消费侧裸判 `if v3_hub.changed2:` 会在这之后**每帧**重复打日志（坏帧空转期
    #   一秒几百行）。口径：只在"文件真的变了"那一拍（_ch）才看它。
    _check('★ P2 变更日志只在"文件真的变了"那一拍打（changed2 裸判会每帧刷屏）',
           'if _ch and v3_hub.changed2:' in src)


def s_review_push():
    """★ 复盘图"推一次"状态机（2026-10-01 现场 bug 的回归网）。

    现场（fire_run5，用户："按发射看不到图"）：live 档的复盘图（发射那杆 /
    进球）贴进 `_REV` 之后**全代码库没有任何一处把它推上屏** —— 显示块那一支
    只有一句 pass：屏幕干冻 v3_review 秒再回直播，图从头到尾没显示过。修法 =
    窗口内推一次，判据抽在 `ball_game._rev_needs_push`（可离线测）。

    ★ 这里**必须拿真 numpy 数组**当图（不是小替身）：最危险的改法（把 `is`
      写成 `==`）只有 numpy 才当场暴露 —— `==` 返回的是数组，`if` 真值判断
      直接抛异常，而且是在游戏循环里。
    """
    import numpy as np

    print('\n== review_push：复盘图"推一次"状态机（用户"看不到图"的回归网）')
    try:
        import ball_game as bg
    except Exception as e:
        print('   （跳过：PC 上 import 不了 ball_game —— %s）' % e)
        return

    img1 = np.zeros((4, 4, 3), np.uint8)
    img1[0, 0] = (1, 2, 3)
    img2 = img1.copy()                       # 像素全同、**不是**同一个对象

    rev = {'img': None, 'until': 0.0, 'prev': False, 'n': 0}
    _check('窗口外（img=None）-> 不推', bg._rev_needs_push(rev) is False)
    rev['img'] = img1
    _check('新图（连 pushed 键都还没有）-> 推', bg._rev_needs_push(rev) is True)
    _check('推过之后把**对象**记下来了（成败都算推过）',
           rev.get('pushed') is img1)
    _check('同一张图 -> 一次就够，不再推', bg._rev_needs_push(rev) is False)
    rev['img'] = img2
    _check('换图（新对象，哪怕像素全同）-> 再推',
           bg._rev_needs_push(rev) is True, '`is` 语义：新对象=新内容')
    rev['img'] = None
    _check('图被清掉（窗口结束）-> 不推', bg._rev_needs_push(rev) is False)

    # 源码级守卫（真跑 ball_game 要 pynq+相机，PC 没有）：显示块必须**真的
    # 调用**这个判据、窗口结束必须把 img/pushed 一起清 —— 防"改回内联/删掉"。
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        src = f.read()
    _check('显示块调用 _rev_needs_push（推帧不再依赖内联条件）',
           '_rev_needs_push(_REV)' in src)
    _check('窗口结束把 img 与 pushed 一起清（"推过"不跨窗口）',
           "_REV['pushed'] = None" in src)

    # ---- 复盘触发（上升沿）也一起钉：`_review_tick` 本来就是纯函数 ----
    bg._REV.update({'img': None, 'until': 0.0, 'prev': False, 'n': 0,
                    'pushed': None})
    _check('真进洞那一下 -> 触发，窗口 = now + review',
           bg._review_tick('goal', True, 6.0, 100.0, True) is True
           and bg._REV['until'] == 106.0)
    _check('一直瞄着洞 -> 不重复弹（上升沿）',
           bg._review_tick('goal', True, 6.0, 100.5, True) is False)
    _check('打回了 -> 记下降沿（prev=False）',
           bg._review_tick('bounce', True, 6.0, 101.0, True) is False
           and bg._REV['prev'] is False)
    _check('没开直播档 -> 不触发，但 prev **照样更新**（不攒假上升沿）',
           bg._review_tick('goal', False, 6.0, 102.0, True) is False
           and bg._REV['prev'] is True)
    _check('★ 从"没开"切回"开了"的那一下**不许**把老 goal 当新进球',
           bg._review_tick('goal', True, 6.0, 103.0, True) is False)
    _check('泵没开 -> 不触发（图推不出去就别切屏）',
           bg._review_tick('bounce', True, 6.0, 104.0, False) is False
           and bg._review_tick('goal', True, 6.0, 105.0, False) is False)
    bg._review_tick('bounce', True, 6.0, 105.5, True)
    _check('--v3-review 0（用户关掉复盘）-> 不触发',
           bg._review_tick('goal', True, 0.0, 106.0, True) is False)


def s_live_partial():
    """★ 差距 2（2026-10-23）：直播变化区局部推帧 + 单应逆缓存。

    病根（10-23 实况）：直播档单缓冲 ~3fps 整帧原地写 → 每帧一道撕裂带
    （用户"晃手屏幕闪"）。修法 = `_live_show`：resize 到帧泵尺寸后与上一张
    已上屏图差分，只 `show_rect` 变化块；整帧/别的画面推过时退回整帧。
    这里把纯函数与三态状态机钉死（板上只验最后一遍手感）。
    """
    import numpy as np

    print('\n== live_partial：差距 2 变化区局部推帧 + 单应逆缓存')
    try:
        import ball_game as bg
    except Exception as e:
        print('   （跳过：PC 上 import 不了 ball_game —— %s）' % e)
        return

    # ---- 1) _diff_rects：纯函数（无变化 / 小块 / 整帧 / 两块分离）----
    prev = np.zeros((240, 320, 3), np.uint8)
    cur = prev.copy()
    _r, _f = bg._diff_rects(prev, cur)
    _check('无变化 -> rects=[] 且 frac=0（一字节都不写）',
           _r == [] and _f == 0.0, 'rects=%s frac=%.3f' % (_r, _f))
    cur[100:112, 150:166] = 255
    _r, _f = bg._diff_rects(prev, cur)
    _ok = (bool(_r) and len(_r) == 1
           and _r[0][0] <= 150 and _r[0][1] <= 100
           and _r[0][2] >= 166 and _r[0][3] >= 112)
    _check('小块变化 -> 一个覆盖它（含 margin）的矩形', _ok, str(_r))
    _check('小块变化 frac 很小', 0.0 < _f < 0.05, 'frac=%.3f' % _f)
    cur2 = prev.copy()
    cur2[:] = 255
    _r, _f = bg._diff_rects(prev, cur2)
    _check('整帧变化 -> rects=None（调用方整帧推）',
           _r is None and _f > 0.6, 'rects=%s frac=%.3f' % (_r, _f))
    cur3 = prev.copy()
    cur3[8:48, 8:48] = 255
    cur3[192:232, 272:312] = 255
    _r, _f = bg._diff_rects(prev, cur3)
    _check('两块分离变化 -> 两个矩形（不拉成一整幅大框）',
           _r is not None and len(_r) == 2, str(_r))
    _check('阈值可调：把 thresh 抬到 300 -> 看不见这块变化',
           bg._diff_rects(prev, cur, thresh=300)[0] == [])
    # ★ 2026-10-23 板上实测：相机噪声在 320x180 上碎成 ~38 个连通域（总面积仅 1.6%），
    #   旧的 max_rects=8 会让每一帧都退回整帧推。这里钉住"碎块多也走局部"。
    cur4 = prev.copy()
    for _i in range(30):
        _x = 8 + (_i % 10) * 30
        _y = 8 + (_i // 10) * 60
        cur4[_y:_y + 8, _x:_x + 8] = 255
    _r4, _f4 = bg._diff_rects(prev, cur4, min_cells=2, margin=2, max_rects=48)
    _check('30 个碎噪声块 -> 仍判局部（max_rects 放宽后退整帧的坑）',
           _r4 is not None and len(_r4) >= 20, 'n=%s frac=%.3f'
           % (len(_r4) if _r4 else None, _f4))

    # ---- 2) _live_show：三态 + "别的画面推过 -> 下一帧整帧" ----
    class _FP(object):
        def __init__(self, w=1280, h=720):
            self.width, self.height = w, h
            self.n_show = 0
            self.calls = []

        def show(self, img):
            self.n_show += 1
            self.calls.append(('full', img.shape))
            return {'ms_total': 1.0, 'flow': None}

        def show_rect(self, img, rect):
            self.n_show += 1
            self.calls.append(('rect', tuple(rect)))
            return {'ms_total': 0.1, 'flow': None}

    pump = _FP()
    st = {'prev_s': None, 'cur_s': None, 'buf': None, 't_full': 0.0,
          'n_show_after': -1, 'n_full': 0, 'n_rect': 0, 'n_skip': 0}
    img = np.zeros((720, 1024, 3), np.uint8)      # 与直播 _build_live 同尺寸
    h1 = bg._live_show(pump, img, st, now=100.0)
    _check('第一帧 -> 整帧（prev_s 还没建立）',
           h1['kind'] == 'full' and pump.calls[-1][0] == 'full'
           and st.get('prev_s') is not None, str(h1))
    _check('resize 到帧泵尺寸（1280x720），差分小图 160x90（降采样 8x）',
           st['prev_s'].shape == (90, 160, 3), str(st.get('prev_s').shape))
    n0 = pump.n_show
    h2 = bg._live_show(pump, img, st, now=100.1)
    _check('画面没变 -> skip（一个字节不写、n_show 不动）',
           h2['kind'] == 'skip' and pump.n_show == n0, str(h2))
    img[300:340, 500:560] = 255                   # 40x60 的块（≈一颗球）
    h3 = bg._live_show(pump, img, st, now=100.2)
    _rc = pump.calls[-1][1] if pump.calls[-1][0] == 'rect' else None
    _check('小块变化 -> rect（只走 show_rect）',
           h3['kind'] == 'rect' and pump.calls[-1][0] == 'rect'
           and pump.n_show == n0 + 1, str(h3))
    _check('局部矩形覆盖变化块（放大回帧泵坐标，x≈625~700 / y≈300~340）',
           _rc is not None and _rc[0] <= 630 and _rc[1] <= 305
           and _rc[2] >= 695 and _rc[3] >= 335, str(_rc))
    _check('prev_s 在推完后跟上变化块（小图 (40,80) 处=255、左上=0）',
           st['prev_s'][40, 80, 0] == 255 and st['prev_s'][0, 0, 0] == 0,
           'block=%d bg=%d' % (st['prev_s'][40, 80, 0], st['prev_s'][0, 0, 0]))
    pump.n_show += 1                      # 模拟商店/瞄准图/复盘图推了一帧
    h4 = bg._live_show(pump, img, st, now=100.3)
    _check('别的画面推过（n_show 变了）-> 下一帧整帧重推',
           h4['kind'] == 'full', str(h4))
    h5 = bg._live_show(pump, img, st, now=106.0, full_every=5.0)
    _check('每 full_every 秒强制整帧刷新（防噪声累积）',
           h5['kind'] == 'full', str(h5))
    img[:] = 200
    h6 = bg._live_show(pump, img, st, now=106.1)
    _check('整帧变化 -> full 且返回合法（rects=None 不许 len() 炸）',
           h6['kind'] == 'full' and h6['rects'] is None and h6['n_rects'] == 0,
           str(h6))
    # ---- 2c) ★★ 2026-10-31 PL 帧泵（PLPUMP=1，默认关）----
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as _f_pp:
        _src_bg = _f_pp.read()
    _check('PL帧泵：默认关（只有 PLPUMP 环境变量为真才启动）',
           hasattr(bg, '_PLP') and bg._PLP.get('on') is False
           and "os.environ.get('PLPUMP'" in _src_bg)
    _check('PL帧泵：开时强制单缓冲（帧泵写固定地址）',
           "if _plp_want and v3_hdmi_live:" in _src_bg
           and "_hdmi_mode = 'live'" in _src_bg)
    _check('PL帧泵：失败自动回退 PS 渲染（if not _plp_ok 走老路）',
           'def _plp_show' in _src_bg and 'if not _plp_ok:' in _src_bg
           and "_PLP['on'] = False" in _src_bg)
    # ---- 2b) ★★ 2026-10-31 球的高频插值叠加（SMOOTH=1，默认关）----
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as _f_sm:
        _src_bg = _f_sm.read()
    _check('平滑插值：默认关（只有 SMOOTH 环境变量为真才启动）',
           hasattr(bg, '_SM') and bg._SM.get('on') is False
           and "os.environ.get('SMOOTH'" in _src_bg)
    _check('平滑插值：线程 + 开关都在；球头默认仍由 _build_live 画（else 分支）',
           'def _smooth_loop' in _src_bg and 'def _sm_start' in _src_bg
           and 'pump.show_rect(own' in _src_bg
           and "_SM['balls'].append(" in _src_bg)
    _check('平滑插值：只在有飞行球时动作（balls 空 -> 线程不画）',
           "_SM['balls'] = []" in _src_bg
           and 'if fr is None or Mm is None or pump is None or not balls:' in _src_bg)
    _check('平滑插值：外推封顶 160ms（急转不过冲）',
           'if dt > 0.16:' in _src_bg)
    _check('平滑插值：用引擎步进生成航点（不是线性外推），连反弹都准',
           '_rtb2.advance(' in _src_bg and "'seq': _seq" in _src_bg
           and 'len(_sq) >= 2' in _src_bg)
    _check('三态计数都在（板上验收看 全/局部/跳）',
           st['n_full'] >= 3 and st['n_rect'] == 1 and st['n_skip'] == 1,
           '全%d 局部%d 跳%d' % (st['n_full'], st['n_rect'], st['n_skip']))

    # 帧泵批量矩形写：有 show_rects() 时只调一次（不是逐块 show_rect）
    class _FPB(_FP):
        def show_rects(self, img, rects):
            self.n_show += len(rects)
            self.calls.append(('rects', tuple(rects)))
            return {'ms_total': 0.1 * len(rects), 'flow': None}

    pb = _FPB()
    stb = {'prev_s': None, 'cur_s': None, 'buf': None, 't_full': 0.0,
           'n_show_after': -1, 'n_full': 0, 'n_rect': 0, 'n_skip': 0}
    img_b = np.zeros((720, 1024, 3), np.uint8)
    bg._live_show(pb, img_b, stb, now=200.0)
    img_b[300:340, 500:560] = 255
    hb = bg._live_show(pb, img_b, stb, now=200.2)
    _check('批量矩形写：有 show_rects() 时走一次批量（不逐块 show_rect）',
           hb['kind'] == 'rect' and pb.calls[-1][0] == 'rects'
           and len(pb.calls[-1][1]) >= 1, str(pb.calls[-1][0]))

    # ---- 3) 源码级守卫：直播块真的走 _live_show（防改回整帧 show）----
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        src = f.read()
    _check('直播块调用 _live_show(..., _LIVEPUSH)',
           '_live_show(' in src and '_LIVEPUSH' in src)
    _check('直播三态计数打印在周期行里',
           "V3_STAT['n_live_rect']" in src and "V3_STAT['n_live_skip']" in src)
    with open(os.path.join(HERE, 'hdmi_out.py'), encoding='utf-8') as f:
        _hsrc = f.read()
    _check('帧泵批量矩形写 show_rects 在位 + 直播调用它',
           'def show_rects' in _hsrc and 'show_rects(small, rects)' in src)

    # ---- 4) 单应逆缓存：50 次 game_to_cam 只许求逆 1 次 ----
    try:
        for _p in (os.path.join(os.path.dirname(HERE), '..', 'game_pc'),
                   os.path.join(os.path.dirname(HERE), '..', 'vivado',
                                'ov5640_pynq', 'deploy')):
            _p = os.path.abspath(_p)
            if _p not in sys.path:
                sys.path.append(_p)
        _prefer_here()
        import v3_scan as vs
    except Exception as e:
        print('   （跳过逆缓存：PC 上 import 不了 v3_scan —— %s）' % e)
        return
    vs._HINV_CACHE.clear()
    H = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    _cnt = {'n': 0}
    _orig_inv = np.linalg.inv

    def _counting_inv(a):
        _cnt['n'] += 1
        return _orig_inv(a)
    np.linalg.inv = _counting_inv
    try:
        for _ in range(50):
            vs.game_to_cam(H, 10.0, 20.0)
        _n1 = _cnt['n']
        H2 = H.copy()
        H2[0, 2] = 5.0
        vs.game_to_cam(H2, 10.0, 20.0)
        _n2 = _cnt['n']
    finally:
        np.linalg.inv = _orig_inv
    _check('★ 单应逆缓存：50 次换算只求逆 1 次', _n1 == 1, 'inv=%d' % _n1)
    _check('换了一份 Hm -> 自然重新求逆（缓存 key 按内容）', _n2 == 2, 'inv=%d' % _n2)

    # ---- 5) live2 双缓冲：Park 指针只改低 5 位，其余位保留 ----
    try:
        import hdmi_out as ho

        class _FV(object):
            def __init__(self):
                self.v = {ho.VD_FRMPTR: 0x00050000}

            def read(self, off):
                return self.v.get(off, 0)

            def write(self, off, val):
                self.v[off] = val

        _fx = type('FX', (), {})()
        _fx.vdma = _FV()
        ho.FramePump._park_switch(_fx, 1)
        _w1 = _fx.vdma.v[ho.VD_FRMPTR]
        ho.FramePump._park_switch(_fx, 0)
        _w0 = _fx.vdma.v[ho.VD_FRMPTR]
        _check('live2 Park 换页：只改 RdFrmPtrRef 低 5 位，其余位保留',
               (_w1 & ~0x1F) == 0x00050000 and (_w1 & 0x1F) == 1
               and (_w0 & 0x1F) == 0, '0x%08X 0x%08X' % (_w1, _w0))
    except Exception as e:
        print('   （跳过 live2：import 不了 hdmi_out —— %s）' % e)
    with open(os.path.join(HERE, 'hdmi_out.py'), encoding='utf-8') as f:
        _hs2 = f.read()
    _check('live2 档在位（start/show/show_rects 双缓冲分支 + HDMI_DBUF 开关）',
           '"live2"' in _hs2 and 'VD_STARTADDR2' in _hs2
           and 'HDMI_DBUF' in src)


def s_screen():
    """★ 2026-10-24 串口屏状态输出（TJC 115200/GBK/index.tX，只发变化）。

    钉死：字段映射（局/阶段/倒计时/局分/积分/角度/速度/控球/弹药）、GBK 10 字节
    截断、PLAY/落定/结算/结束的文案、变化才发、以及 --screen 的接线。
    """
    print('\n== screen：串口屏状态输出（GBK/10 字节/变化才发）')
    try:
        import screen_out as so
        import match_flow as mf
    except Exception as e:
        print('   （跳过：PC 上 import 不了 screen_out/match_flow —— %s）' % e)
        return

    _check('GBK 10 字节截断不超长', len(so.fit_gbk('积100:100').encode('gbk')) <= 10,
           so.fit_gbk('积100:100'))
    _check('短串原样返回', so.fit_gbk('角度 20°') == '角度 20°')
    _check('空串给 --', so.fit_gbk('') == '--')

    m = mf.MatchFlow()
    # ★ 第 1 局直接 PLAY（规则：跳过局前）；这里手动置 PREP 验文案
    m.phase = 'PREP'
    snap = so.snapshot(m, 20.0, 1200.0, 1, -15.0, 1000.0, 2)
    _check('PREP：局/阶段/倒计时',
           snap['t0'] == '第1/10局' and snap['t1'] == '局前准备'
           and snap['t2'] == '倒计时--',
           str((snap['t0'], snap['t1'], snap['t2'])))
    _check('P1/P2 角度速度控球',
           snap['t5'] == '角度 20°' and snap['t6'] == '速度 1200'
           and snap['t4'] == '控球 1号' and snap['t10'] == '角度 -15°'
           and snap['t11'] == '速度 1000' and snap['t9'] == '控球 2号',
           str((snap['t4'], snap['t5'], snap['t6'],
                snap['t9'], snap['t10'], snap['t11'])))
    _check('初始局分/积分/弹药',
           snap['t16'] == '局0:0' and snap['t17'] == '积0:0'
           and snap['t12'] == '红球 3' and snap['t13'] == '蓝球 2'
           and snap['t14'] == '红球 3' and snap['t15'] == '蓝球 2',
           str((snap['t16'], snap['t17'], snap['t12'], snap['t13'],
                snap['t14'], snap['t15'])))

    m.phase = 'PLAY'
    m.timer = 10.0
    snap = so.snapshot(m, 0, 400, 1, 0, 400, 2)
    _check('PLAY：进行中 + 倒计时20秒',
           snap['t1'] == '进行中' and snap['t2'] == '倒计时20秒',
           str((snap['t1'], snap['t2'])))
    m.grace = 2.0
    snap = so.snapshot(m, 0, 400, 1, 0, 400, 2)
    _check('落定期：落定中 + 落定3秒',
           snap['t1'] == '落定中' and snap['t2'] == '落定3秒',
           str((snap['t1'], snap['t2'])))
    m.grace = None
    m.phase = 'ROUND_END'
    snap = so.snapshot(m, 0, 400, 1, 0, 400, 2)
    _check('结算：结算中 + 倒计时--',
           snap['t1'] == '结算中' and snap['t2'] == '倒计时--',
           str((snap['t1'], snap['t2'])))
    m.phase = 'MATCH_END'
    _check('比赛结束', so.snapshot(m, 0, 400, 1, 0, 400, 2)['t1'] == '比赛结束')

    m.phase = 'PLAY'
    m.timer = 0.0
    m.goals = {1: 2, 2: 1}
    m.points = {1: 30, 2: 20}
    m.ammo = {1: {1: 2, -1: 1}, 2: {1: 3, -1: 0}}
    snap = so.snapshot(m, 0, 400, 1, 0, 400, 2)
    _check('局分/积分/弹药逐项',
           snap['t16'] == '局20:10' and snap['t17'] == '积30:20'
           and snap['t12'] == '红球 2' and snap['t13'] == '蓝球 1'
           and snap['t14'] == '红球 3' and snap['t15'] == '蓝球 0', str(snap))
    _bad = [(k, v) for k, v in snap.items() if len(v.encode('gbk')) > 10]
    _check('所有动态文本 ≤10 GBK 字节', not _bad, str(_bad))

    # ★ 2026-10-25 道具剩余次数（用户定稿布局：每个道具一个控件，带中文名）
    m.items[1]['obstacle'] = 2
    m.items[1]['portal'] = 1
    m.items[2]['hole'] = 3
    snap = so.snapshot(m, 0, 400, 1, 0, 400, 2)
    _check('★ 道具剩余次数（P1 加球/加洞/加障碍/传送门/改磁场；P2 同）',
           snap.get('t38') == '加球 0' and snap.get('t42') == '加洞 1'
           and snap.get('t40') == '加障碍 2'
           and snap.get('t41') == '传送门 1'
           and snap.get('t39') == '改磁场 0'
           and snap.get('t44') == '加球 0' and snap.get('t48') == '加洞 4'
           and snap.get('t46') == '加障碍 0'
           and snap.get('t47') == '传送门 0'
           and snap.get('t45') == '改磁场 0',
           str({k: snap.get(k) for k in ('t38', 't42', 't40', 't41', 't39',
                                         't44', 't48', 't46', 't47', 't45')}))
    _bad2 = [(k, v) for k, v in snap.items()
             if len(v.encode('gbk')) > 10]
    _check('道具计数也 ≤10 GBK 字节', not _bad2, str(_bad2))
    _check('★ 驱动不碰 t31~t36 静态提示（只写 10 个道具计数控件）',
           not any(k in so._CTRLS for k in
                   ('t31', 't32', 't33', 't34', 't35', 't36'))
           and all(k in so._CTRLS for k in
                   ('t38', 't42', 't40', 't41', 't39',
                    't44', 't48', 't46', 't47', 't45')))

    class _Fake(so.ScreenOut):
        def __init__(self):
            so.ScreenOut.__init__(self)
            self.fd = 1
            self.sent = []

        def send(self, name, text):
            self.sent.append((name, text))
            return True

    fs = _Fake()
    fs.update(snap)
    _n1 = len(fs.sent)
    fs.update(snap)
    _check('同一快照第二次不重发', _n1 > 0 and len(fs.sent) == _n1,
           '%d -> %d' % (_n1, len(fs.sent)))
    snap2 = dict(snap)
    snap2['t5'] = '角度 30°'
    fs.update(snap2)
    _check('只有变化字段单独发', len(fs.sent) == _n1 + 1
           and fs.sent[-1] == ('t5', '角度 30°'), str(fs.sent[-1]))

    # ★ 2026-10-25 页面感知：addball 页不写 index 状态（0x1A 会污染缓存）；
    #   切回 index 后必须**全量重发**（局数/阶段/倒计时/比分/积分/弹药/道具）。
    fs3 = _Fake()
    fs3._cur_page = 'addball'
    fs3.update(snap)
    _check('★ 非 index 页不写状态（防 0x1A 污染缓存）',
           not fs3.sent, str(fs3.sent))
    fs3._cur_page = 'index'
    fs3.update(snap)
    _names = set(n for n, _ in fs3.sent)
    _check('★ 回 index 后全量重发（所有 _CTRLS 都写一遍）',
           _names == set(so._CTRLS), str(sorted(_names)))

    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        src = f.read()
    _check('ball_game --screen 接线（参数/import/主循环 update）',
           "'--screen' in sys.argv" in src
           and 'import screen_out as _screen_out' in src
           and '_screen_out.update(' in src)
    with open(os.path.join(HERE, 'v3_input.py'), encoding='utf-8') as f:
        _vis = f.read()
    _check('★ 实体传送门摆放键 G=34 / 小键盘7=71（ppl 计数器）',
           "34: (1, 'ppl'" in _vis and "71: (2, 'ppl'" in _vis
           and "if act == 'ppl'" in _vis and 'p1_ppl' in _vis)
    _check('★ 局中加球键 F=33 / 小键盘8=72（aball 计数器）',
           "33: (1, 'aball'" in _vis and "72: (2, 'aball'" in _vis
           and "if act == 'aball'" in _vis and 'p1_aball' in _vis)
    _check('★ 加球选择页接线（F/小键盘8 -> addball 页 -> 触摸红/蓝）',
           'def _aball_begin' in src and '_aball_begin(' in src
           and 'def _aball_finish' in src and '_aball_finish(' in src
           and 'poll_event()' in src and 'ABALL_PAGE' in src
           and 'aball1' in src and 'aball2' in src)
    _check('★ 测试种子 TEST_GIVE_BALLS 接线（默认关）',
           'TEST_GIVE_BALLS' in src and '加球道具各' in src)
    _check('★ 测试种子 TEST_GIVE_POINTS 接线（默认关；商店仍真买）',
           'TEST_GIVE_POINTS' in src and '商店购买仍是真买' in src)
    _check('★ 布置键：PREP/PLAY 都能摆（2026-10-28 第 3 条），其它阶段给提示',
           "phase not in ('PREP', 'PLAY')" in src
           and '当前阶段不能摆放——道具保留' in src)
    _check('★ 布置态实时相机视图（V/G：相机+场区/黄圈+文案；抓不到退回合成）',
           'def _build_pplace_live' in src and '_build_pplace_live(' in src
           and 'def _pplace_text' in src and 'if _pp_live:' in src
           and "_SHOPV['lk'] = None" in src)
    _check('★ 实体布置确认/取消（P1=E / P2=小键盘*，不自动扣）',
           'def _pplace_commit_now' in src and '_pplace_commit_now(' in src
           and '候选是每 0.3s 实时识别的' in src and '_pplace_clear()' in src)
    _check('★ ESC 在实时摆放里一次到底（不再"先取消候选"——会被 0.3s 重识别吃掉）',
           'ESC **一次到底**' in src)
    _check('★ /match 带实时摆放态 _place_snap（手机据此显示确定/取消条）',
           'def _place_snap' in src and 'place=_place_snap()' in src
           and "place=None" in src)
    _check('★ 布置提示按玩家区分确认键（P1=E / P2=小键盘*）',
           'def _pplace_key' in src
           and "'E' if int(who) == 1 else '小键盘*'" in src
           and '_pplace_key(_PPLACE' in src and '_pplace_key(pplace' in src
           and '_pplace_key(_pk)' in src)
    _check('★ 布置态鼠标点选黑条（点哪根锁哪根，黄框 + E/小键盘* 确认）',
           'def _pplace_pick_bar' in src
           and 'def _pplace_click_game' in src
           and 'def _pplace_mouse_click' in src
           and '_pplace_mouse_click(' in src
           and 'def _live_crop_rect' in src and '_live_crop_rect(' in src
           and "_PPLACE['pick']" in src
           and "_PHYS.get('bars')" in src
           and '已点选黑条' in src)
    _check('★ 实体障碍多件（最多 3 根，已购优先、确定性顺序）',
           '_own_act = [t for t in _act' in src
           and '_bars3 = _pool[:3]' in src
           and 'world.obstacles = _obs if _obs else manual_obs' in src)
    _check('★ 实体传送门多件（最多 2 对，已购优先）',
           '_pairs = []' in src and '_used = set()' in src
           and '2 * _k + 1' in src and '2 * _k' in src)
    _check('★ 布置候选黄色框（黑条旋转框/绿门方框）',
           'def _pplace_draw_cand' in src and '_pplace_draw_cand(' in src
           and "kind') == 'bar'" in src)
    _check('★ _MAS 覆盖 aball 计数 + _mas_ev 容错（板上 KeyError 事故）',
           "'aball1': 0, 'aball2': 0" in src
           and '_MAS.get(_k, 0)' in src
           and "_mas_ev('aball%d'" in src)
    _check('★ 切页后再写 addball 文本（TJC 切页会重载页面）',
           't_ready' in src and 'def _aball_set_who' in src
           and "time.time() < float(_ABALL.get('t_ready')" in src)
    _check('★ 实体道具购买→使用接线（V/G 入口/局前实物识别/生效门/布置画面）',
           '_pplace_enter(' in src and '_pplace_check(' in src
           and 'def _build_pplace_view' in src
           and 'def _phys_active' in src and 'PHYS_ITEM_GATE' in src
           and '_PPLACE[' in src and 'PHYS_PLACE_DET' in src)
    with open(os.path.join(HERE, 'game_v3.sh'), encoding='utf-8') as f:
        sh = f.read()
    _check('game_v3.sh SCREEN=1 接线',
           'SCREEN="${SCREEN:-0}"' in sh and 'ARGS="$ARGS --screen"' in sh)
    _check('HDMI 去 HUD/场区裁剪接线',
           "'--no-hud' in sys.argv" in src and "'--live-crop' in sys.argv" in src
           and '_live_crop_image(' in src and 'hud=not no_hud' in src)
    _check('game_v3.sh HUD/CROP 接线（有屏默认 0/1，无屏默认 1/0）',
           'HUD="${HUD:-0}"' in sh and 'CROP="${CROP:-1}"' in sh
           and 'HUD="${HUD:-1}"' in sh and 'CROP="${CROP:-0}"' in sh
           and 'ARGS="$ARGS --no-hud"' in sh
           and 'ARGS="$ARGS --live-crop"' in sh)
    try:
        # v3_scan（裁剪里的 game_to_cam）import engine；PC 上 engine 只在
        # game_pc/ 或 deploy/ 里，补一下路径（板上 engine.py 在 wp 根目录）
        for _p in (os.path.join(os.path.dirname(HERE), '..', 'game_pc'),
                   os.path.join(os.path.dirname(HERE), '..', 'vivado',
                                'ov5640_pynq', 'deploy')):
            _p = os.path.abspath(_p)
            if _p not in sys.path:
                sys.path.append(_p)
        import ball_game as bg
        import numpy as np
        _im = np.zeros((720, 1024, 3), np.uint8)
        _im[0:540, 0:960] = 255
        _out = bg._live_crop_image(_im, np.eye(3), 0.05)
        _check('场区裁剪输出 1280x720', _out.shape == (720, 1280, 3),
               str(_out.shape))
        _check('红/蓝粒子轨迹/球体按电荷着色（不再统一紫）',
               bg._charge_color(1) == (0, 0, 255)
               and bg._charge_color(-1) == (255, 0, 0)
               and bg._charge_color(0) == (0, 200, 200)
               and bg._charge_color(1, False) != bg._charge_color(1),
               str((bg._charge_color(1), bg._charge_color(-1),
                    bg._charge_color(0))))

        # ★★ 2026-10-25 实体道具「购买→使用」（用户定案：只认新摆的）：
        #   免费基线+已购件生效；未认领的新件只是摆件；摆放态识别到才扣次数。
        _bars = [dict(gx=480.0, gy=270.0, glen=120.0),
                 dict(gx=200.0, gy=100.0, glen=80.0),
                 dict(gx=700.0, gy=400.0, glen=60.0)]
        _act = bg._phys_active(_bars, [(480.0, 270.0)], [(200.0, 100.0)],
                               bg.PHYS_BAR_MATCH)
        _unc = bg._phys_unclaimed(_bars, [(480.0, 270.0)], [(200.0, 100.0)],
                                  bg.PHYS_BAR_MATCH)
        _check('★ 实体件生效门：免费/已购生效，新件未认领',
               [(b['gx'], b['gy']) for b in _act]
               == [(480.0, 270.0), (200.0, 100.0)]
               and [(b['gx'], b['gy']) for b in _unc] == [(700.0, 400.0)],
               str((_act, _unc)))
        # ★★ 2026-10-28：实物条碎片合并 + 贴条假黑球过滤 + PLAY 锁定
        _mb = bg._merge_bars([
            dict(gx=100.0, gy=100.0, glen=80.0, gthick=12.0, gang=0.0),
            dict(gx=160.0, gy=101.0, glen=80.0, gthick=12.0, gang=1.0)])
        _check('★ 实物条碎片合并（共线近邻 2 段 -> 1 根）',
               len(_mb) == 1 and float(_mb[0]['glen']) > 110.0,
               str(_mb))
        _check('★ 贴条黑球候选过滤（碎片/反光不当洞口标记）',
               bg._ball_near_bars(dict(gx=160.0, gy=100.0), _mb) is True
               and bg._ball_near_bars(dict(gx=300.0, gy=300.0), _mb) is False)
        # ★ 2026-10-29 用户实况（"有障碍的地方识别出传送门"）：世界生成/调整
        #   界面配对绿门时也要剔掉压/贴黑条的绿点（此前只有购买摆放有这条）。
        _check('★ 压条绿点不当传送门（世界生成同口径）',
               len(bg._drop_greens_on_bars(
                   [dict(gx=170.0, gy=100.0, area=50.0),
                    dict(gx=700.0, gy=400.0, area=50.0)], _mb)) == 1)
        # ★ 2026-10-29 用户实况（"调整后确认开局，门跳到障碍上"）：锁定/应用
        #   锁定时再兜一道——整对门里一扇压条就丢整对，另一对保留。
        _check('★ 锁定兜底：压条的一对门整对丢弃，另一对保留',
               bg._drop_ports_on_obs(
                   [(100.0, 100.0, 0), (700.0, 400.0, 1),
                    (500.0, 300.0, 2), (600.0, 300.0, 3)],
                   [(100.0, 100.0, 120.0, 14.0, 0.0)])
               == [(500.0, 300.0, 2), (600.0, 300.0, 3)])
        import engine as _eng
        _w = type('W', (), {})()
        _w.obstacles = [_eng.Obstacle(480.0, 270.0, 200.0, 14.0, 30.0)]
        _w.portals = [_eng.Portal(100.0, 100.0, 0)]
        _w.holes = [dict(edge='left', c=200.0, w=44.0, target=0)]
        bg._PHYS['lock'] = False
        bg._phys_lock(_w)
        _w2 = type('W2', (), {})()
        bg._phys_apply_lock(_w2)
        _check('★ 开局锁定：PLAY 从锁定副本恢复障碍/门（洞口不锁）',
               len(bg._PHYS.get('lock_obs') or []) == 1
               and len(_w2.obstacles) == 1
               and abs(_w2.obstacles[0].cx - 480.0) < 1e-6
               and abs(_w2.obstacles[0].hw - 100.0) < 1e-6
               and len(_w2.portals) == 1
               and not (bg._PHYS.get('lock_holes') or []))
        bg._PHYS['lock'] = False
        bg._PHYS['lock_obs'] = []
        bg._PHYS['lock_ports'] = []
        bg._PHYS['lock_holes'] = []
        _cand = bg._pplace_candidates('obstacle', _bars, [],
                                      [(480.0, 270.0)], [],
                                      [(200.0, 100.0)], [])
        _check('★ 摆放候选（障碍）：只挑未认领的黑条',
               len(_cand) == 1 and _cand[0]['kind'] == 'bar'
               and round(_cand[0]['gx']) == 700, str(_cand))
        _g = [dict(gx=350.0, gy=300.0, area=20),
              dict(gx=650.0, gy=300.0, area=20),
              dict(gx=360.0, gy=305.0, area=3)]
        _cp = bg._pplace_candidates('portal', [], _g, [], [], [], [])
        _check('★ 摆放候选（传送门）：一对绿门（间距>=80）',
               len(_cp) == 2 and round(_cp[0]['gx']) == 350
               and round(_cp[1]['gx']) == 650, str(_cp))

        _sv_phys = {k: (list(v) if isinstance(v, list) else v)
                    for k, v in bg._PHYS.items()}
        _sv_pp = dict(bg._PPLACE)

        class _FM:
            phase = 'PREP'
            items = {1: {'obstacle': 1, 'portal': 1},
                     2: {'obstacle': 0, 'portal': 0}}
        _fm = _FM()
        bg._PHYS.update({'base_ready': True,
                         'base_bars': [(480.0, 270.0)], 'base_greens': [],
                         'own_bars': [], 'own_pairs': [],
                         'bars': _bars, 'greens': [],
                         'applied_bars': _bars, 'applied_greens': []})
        bg._PPLACE.update({'on': True, 'who': 1, 'kind': 'obstacle',
                           't': 0.0, 't_det': 0.0, 'cand': [],
                           'status': '', 'confirm': False})
        _okc = bg._pplace_check(_fm)
        _check('★ 摆放候选：识别到但**不自动扣**，进入待确认',
               (_okc is False) and bg._PPLACE.get('confirm')
               and _fm.items[1]['obstacle'] == 1
               and not (bg._PHYS.get('own_bars') or []),
               str((_okc, bg._PPLACE, _fm.items[1])))
        _okc2, _msgc2 = bg._pplace_commit_now(_fm)
        _check('★ E 确认后才扣 1 次并登记（退出摆放态）',
               _okc2 and _fm.items[1]['obstacle'] == 0
               and len(bg._PHYS.get('own_bars') or []) == 1
               and not bg._PPLACE['on'],
               str((_okc2, _msgc2, _fm.items,
                    bg._PHYS.get('own_bars'))))
        _fm.items[1]['portal'] = 1
        _gos = [dict(gx=350.0, gy=300.0, area=20),
                dict(gx=650.0, gy=300.0, area=20)]
        bg._PHYS.update({'base_ready': True, 'base_bars': [],
                         'base_greens': [], 'own_bars': [],
                         'own_pairs': [], 'bars': [], 'greens': _gos,
                         'applied_bars': [], 'applied_greens': _gos})
        bg._PPLACE.update({'on': True, 'who': 1, 'kind': 'portal',
                           't': 0.0, 't_det': 0.0, 'cand': [],
                           'status': '', 'confirm': False})
        _okp = bg._pplace_check(_fm)
        _check('★ 摆放候选（传送门）：不自动扣，待确认',
               (_okp is False) and bg._PPLACE.get('confirm')
               and _fm.items[1]['portal'] == 1,
               str((_okp, bg._PPLACE, _fm.items[1])))
        _okp2, _msgp2 = bg._pplace_commit_now(_fm)
        _check('★ E 确认后扣 1 个传送门道具',
               _okp2 and _fm.items[1]['portal'] == 0
               and len(bg._PHYS.get('own_pairs') or []) == 1,
               str((_okp2, _msgp2, _fm.items[1],
                    bg._PHYS.get('own_pairs'))))

        # ★ 2026-10-25 局中加球（F/小键盘8）-> 串口屏 addball 页选红/蓝；
        #   **选完才扣道具**，取消不扣。
        class _FakeScreen:
            def __init__(self):
                self.pages = []
                self.texts = []
                self.ok = True

            def available(self):
                return self.ok

            def set_text(self, page, name, text, max_bytes=10):
                self.texts.append((page, name, text))
                return True

            def set_page(self, p):
                self.pages.append(p)
                return True

        class _AB:
            phase = 'PLAY'
            grace = None
            items = {1: {'ball': 1}, 2: {'ball': 0}}
            ammo = {1: {1: 3, -1: 2}, 2: {1: 3, -1: 2}}
        _fs = _FakeScreen()
        _ab = _AB()
        _oka, _msga = bg._aball_begin(_ab, 1, _fs)
        _check('★ 加球：按 F 打开串口屏 addball 页（此时不扣道具）',
               _oka and bg._ABALL['on'] and _fs.pages[-1] == 'addball'
               and _ab.items[1]['ball'] == 1,
               str((_oka, _fs.pages, _ab.items[1])))
        # ★ TJC 切页会重载页面：切页前写文本会被初始值覆盖 -> 模拟 0.5s 后补写
        bg._ABALL['t_ready'] = 0.0
        _w0 = bg._aball_set_who(_fs)
        _n0 = bg._aball_set_count(_ab, _fs)
        _check('★ 切页后补写动态文案：t8 选手一 / t13 蓝球 / t14 红球 / t12 加球',
               _w0 and _n0 == 3
               and ('addball', 't8', '选手一使用') in _fs.texts
               and ('addball', 't13', '当前有蓝球 2 个') in _fs.texts
               and ('addball', 't14', '当前有红球 3 个') in _fs.texts
               and ('addball', 't12', '剩余加球 1 个') in _fs.texts,
               str((_w0, _n0, _fs.texts)))
        _tc = so._build_text_cmd('addball', 't12', '剩余加球 2 个', 32)
        _check('★ 指定页面文本命令（GBK；t12 允许 32 字节）',
               _tc == 'addball.t12.txt="剩余加球 2 个"', _tc)
        # ★ t12 实时刷新：剩余数变了才重发（没变不刷屏）
        _ab.items[1]['ball'] = 0
        _r1 = bg._aball_set_count(_ab, _fs)
        _r2 = bg._aball_set_count(_ab, _fs)
        _check('★ t12 实时刷新（剩余 0 直接提示，且变化才重发）',
               _r1 and not _r2
               and ('addball', 't12', '没道具了，点取消') in _fs.texts,
               str((_r1, _r2, _fs.texts)))
        _ab.items[1]['ball'] = 1
        bg._aball_set_count(_ab, _fs)      # 同步 last_n=1（刷新测试把 last_n 改成了 0）
        _fs.texts = []
        _okb, _msgb = bg._aball_finish(_ab, 'red', _fs)
        _check('★ 加球选红：扣 1 个、红弹+1、**页面保持 addball**',
               _okb and _ab.items[1]['ball'] == 0
               and _ab.ammo[1][1] == 4 and _ab.ammo[1][-1] == 2
               and _fs.pages[-1] == 'addball' and bg._ABALL['on'],
               str((_okb, _msgb, _ab.items[1], _ab.ammo[1], _fs.pages)))
        _check('★ 选完先写 t14/t12（红球 4、t12 提示没道具）',
               ('addball', 't12', '没道具了，点取消') in _fs.texts
               and ('addball', 't14', '当前有红球 4 个') in _fs.texts,
               str(_fs.texts))
        _okb2, _msg2 = bg._aball_finish(_ab, 'blue', _fs)
        _check('★ 没道具再点蓝：不扣、页面保持、t12 直接提示',
               (not _okb2) and _ab.items[1]['ball'] == 0
               and _ab.ammo[1][-1] == 2 and bg._ABALL['on']
               and _fs.pages[-1] == 'addball'
               and ('addball', 't12', '没道具了，点取消') in _fs.texts,
               str((_okb2, _msg2, _ab.ammo[1], _fs.pages, _fs.texts)))
        _msgc = bg._aball_cancel(_fs)
        _check('★ 手动取消才回 index',
               _fs.pages[-1] == 'index' and not bg._ABALL['on'],
               str((_msgc, _fs.pages)))
        _ab.items[1]['ball'] = 1
        bg._aball_begin(_ab, 1, _fs)
        _msgc = bg._aball_cancel(_fs)
        _check('★ 加球取消：不扣道具、切回 index',
               _ab.items[1]['ball'] == 1 and _fs.pages[-1] == 'index'
               and not bg._ABALL['on'], str((_msgc, _ab.items[1])))
        _ab.items[2]['ball'] = 1
        _fs.texts = []
        _okp2, _ = bg._aball_begin(_ab, 2, _fs)
        bg._ABALL['t_ready'] = 0.0
        _w2 = bg._aball_set_who(_fs)
        bg._aball_set_count(_ab, _fs)
        _check('★ P2 加球：t8 = 选手二使用（不是选手一）',
               _okp2 and _w2
               and ('addball', 't8', '选手二使用') in _fs.texts,
               str(_fs.texts))
        bg._aball_cancel(_fs)
        _ab.items[2]['ball'] = 0
        _ab.phase = 'PREP'
        _okd, _ = bg._aball_begin(_ab, 1, _fs)
        _ab.phase = 'PLAY'
        _ab.grace = 1.0
        _oke, _ = bg._aball_begin(_ab, 1, _fs)
        _ab.grace = None
        _okf, _ = bg._aball_begin(_ab, 2, _fs)
        bg._ABALL['t_ready'] = 0.0
        bg._aball_set_count(_ab, _fs)
        _check('★ 0 个加球也开页（t12 提示没道具，不再静默无反应）',
               _okf and ('addball', 't12', '没道具了，点取消') in _fs.texts,
               str((_okf, _fs.texts)))
        bg._aball_cancel(_fs)
        _fs.ok = False
        _okg, _ = bg._aball_begin(_ab, 1, _fs)
        _fs.ok = True
        _check('★ 加球拒绝（局前 / 落定期 / 串口屏离线）',
               not _okd and not _oke and not _okg,
               str((_okd, _oke, _okg)))
        _evf = so._event_from_frame
        _check('★ 串口屏触摸事件解析（R/B/C；容忍 NUL/CRLF）',
               _evf(b'R') == 'red' and _evf(b'B') == 'blue'
               and _evf(b'C') == 'cancel'
               and _evf(b'C\x00') == 'cancel'
               and _evf(b'R\r\n') == 'red'
               and _evf(b'X') is None and _evf(b'CANCEL') is None)

        bg._PHYS.clear()
        bg._PHYS.update(_sv_phys)
        bg._PPLACE.clear()
        bg._PPLACE.update(_sv_pp)
    except Exception as e:
        print('   （跳过裁剪/颜色/实体道具函数测试：%s）' % e)
    _check('直播真球/风暴的轨迹与球体都走 _charge_color',
           '_charge_color(getattr(_lb' in src and '_brad, _bcol' in src)
    # ★ 2026-10-25：deploy_check.py 是 **PC 端**文件；板上跑台架时没有
    #   /home/xilinx/pc/ —— 之前直接 open 会把整个板上台架带崩（刚实锤）。
    _dcp = os.path.join(os.path.dirname(HERE), 'pc', 'deploy_check.py')
    if os.path.exists(_dcp):
        with open(_dcp, encoding='utf-8') as f:
            dc = f.read()
        _check('screen_out.py 已进 deploy 清单', 'screen_out.py' in dc)
    else:
        print('   （板上没有 ../pc/deploy_check.py —— 跳过 deploy 清单断言）')
    with open(os.path.join(HERE, 'screen_out.py'), encoding='utf-8') as f:
        _sosrc = f.read()
    _check('串口屏写串口在后台线程（USB 卡顿不带走主循环）',
           'threading.Thread' in _sosrc and 'queue.Queue' in _sosrc
           and '_Q.put_nowait' in _sosrc)
    _check('★ 串口屏读触摸/切页接线（page 指令 + R/B/C 事件）',
           'def set_page' in _sosrc and 'def poll_event' in _sosrc
           and 'def read_events' in _sosrc and 'page %s' in _sosrc
           and '_EV_MAP' in _sosrc)
    _check('★ 切页后作废文本缓存（回 index 全量重发，红/蓝数不再不变）',
           "cmd.startswith('page ')" in _sosrc
           and 'self.last = {}' in _sosrc)
    _check('★ 串口屏页面感知（非 index 页整段跳过，防 0x1A 污染）',
           '_cur_page' in _sosrc and '!= self.page' in _sosrc
           and "'page %s' % self.page" in _sosrc)


def s_rescue_ladder():
    """★ 主循环救援的两把尺 + 阶梯时间线（2026-10-01 fire_run5 调参的回归网）。

    现场（用户："一卡一卡"）：旧判据 1.2s 阈值 / 5s 限频在慢性退化链路上每抽
    一下就按 —— run5 里 15 次脉冲 + 11 次断电重上电（每按一次屏幕冻几秒，
    救援自己成了卡顿源）。新判据 = **stale ≥2.5s 才算真卡、两次动作 ≥12s、
    30s 窗口内卡到第 3 回才升级**（升级后窗口重起算）。

    与 wd_timeline 同一套办法：假时钟 + 假 MMIO，把"什么时候脉冲 / 什么时候
    升级 / 任何退出路径静止位干不干净"整条时间线在 PC 上钉死（0.1 秒跑完），
    板上只验最后一遍。
    """
    import time as _rt

    print('\n== rescue_ladder：主循环救援（两把尺 + 阶梯 + 静止位还原）')
    try:
        import ball_game as bg
    except Exception as e:
        print('   （跳过：PC 上 import 不了 ball_game —— %s）' % e)
        return

    # ---- 1) 触发判据（纯函数）：两把尺 = 2.5s stale + 12s 限频 ----
    _check('刚有帧 -> 不动手',
           bg._rescue_due(100.0, 99.5, {'t_last': 0.0}) is False)
    _check('★ 旧阈值那种自恢复抽风（stale 1.5s）不再触发',
           bg._rescue_due(101.5, 100.0, {'t_last': 0.0}) is False)
    _check('真卡（stale 3s）且 13s 没动过手 -> 动手',
           bg._rescue_due(113.0, 110.0, {'t_last': 100.0}) is True)
    _check('真卡但 5s 前刚动过（= 旧限频值）-> 仍不动手',
           bg._rescue_due(105.0, 101.5, {'t_last': 100.0}) is False)
    _check('边界：stale 刚好 2.5s -> 不动（判据是严格 >）',
           bg._rescue_due(102.5, 100.0, {'t_last': 0.0}) is False)
    _check('边界：距上次动作刚好 12s -> 不动（严格 >）',
           bg._rescue_due(112.0, 110.0, {'t_last': 100.0}) is False)
    _check('两条都刚过线 -> 动手',
           bg._rescue_due(112.5, 109.9, {'t_last': 100.0}) is True)

    # ---- 2) 阶梯时间线：假时钟 + 假 MMIO（记录每一笔写）----
    class FakeMM3:
        def __init__(self, v=0x380):
            self.v = v
            self.writes = []
            self.boom_on = None              # 第 n 笔写抛异常（模拟写失败）
        def read(self, off):
            return self.v
        def write(self, off, val):
            self.writes.append((off, val))
            if self.boom_on is not None and len(self.writes) == self.boom_on:
                raise RuntimeError('台架模拟：寄存器写失败')
            self.v = val

    clk = Clock(t0=1000.0)
    real_time, real_sleep = _rt.time, _rt.sleep
    bg._RESCUE.update({'n': 0, 't_win': 0.0, 'in_win': 0,
                       'n_pulse': 0, 'n_pwr': 0, 't_last': 0.0})
    mm3 = FakeMM3()
    _rt.time, _rt.sleep = clk.time, clk.sleep
    try:
        bg._rescue_escalate(mm3)
        _check('第 1 回：脉冲 ctrl[3]（v|0x08），退出时还原静止位（v&~0x03）',
               mm3.writes == [(0x00, 0x388), (0x00, 0x380)], str(mm3.writes))
        _check('计数：脉冲 1 / 升级 0',
               (bg._RESCUE['n_pulse'], bg._RESCUE['n_pwr']) == (1, 0))
        _check('★ 动完手触发判据立刻闭嘴（t_last 已更新）',
               bg._rescue_due(clk.time(), clk.time() - 10.0, bg._RESCUE)
               is False)

        mm3.writes = []
        clk.t += 12.5
        bg._rescue_escalate(mm3)
        _check('第 2 回（12.5s 后，同一 30s 窗口）：还是脉冲',
               mm3.writes == [(0x00, 0x388), (0x00, 0x380)], str(mm3.writes))

        mm3.writes = []
        clk.t += 12.5
        bg._rescue_escalate(mm3)
        _check('第 3 回（同一窗口内）-> 升级【传感器断电重上电】：'
               '断电 -> 上电 -> 传感器复位 -> 退出还原',
               mm3.writes == [(0x00, 0x383), (0x00, 0x380),
                              (0x00, 0x390), (0x00, 0x380)], str(mm3.writes))
        _check('计数：升级 1 次；窗口清零重起算',
               (bg._RESCUE['n_pwr'], bg._RESCUE['in_win']) == (1, 0))

        mm3.writes = []
        clk.t += 12.5
        bg._rescue_escalate(mm3)
        _check('升级之后的那一回：从 0 起数（脉冲，不连升）',
               mm3.writes == [(0x00, 0x388), (0x00, 0x380)]
               and bg._RESCUE['in_win'] == 1)

        mm3.writes = []
        clk.t += 31.0
        bg._rescue_escalate(mm3)
        _check('隔了 31s（新窗口）：失败计数重新攒，不继承旧窗口',
               bg._RESCUE['in_win'] == 1, 'in_win=%d' % bg._RESCUE['in_win'])

        # ---- 3) 异常安全：写炸了也**必须**还原静止位（finally 的职责）----
        mm3 = FakeMM3()
        mm3.boom_on = 1                      # 第 1 笔（脉冲那笔）就炸
        threw = False
        try:
            bg._rescue_escalate(mm3)
        except Exception:
            threw = True
        _check('写失败会抛出（不吞）—— 调用处的护栏负责留话', threw is True)
        _check('★ 但 finally 一定还原静止位（最后那笔是 v&~0x03）',
               bool(mm3.writes) and mm3.writes[-1] == (0x00, 0x380),
               str(mm3.writes))
    finally:
        _rt.time, _rt.sleep = real_time, real_sleep
        bg._RESCUE.update({'n': 0, 't_win': 0.0, 'in_win': 0,
                           'n_pulse': 0, 'n_pwr': 0, 't_last': 0.0})


def s_fire_ballno():
    """★ 选球编号口径回归网（2026-10-05 现场 bug："控球3（蓝球）按发射没反应"）。

    两个直接根因各钉一条（复盘见 MEMORY/ball-number-drift-noball.md）：
      ① 圈旁编号画的必须是 **t.no**（发射/预览点名用的号）。原实现画列表序号
         (_ti+1) —— 编号不连续（如 {1,2,4}）时屏幕把"4号"画成"3"，玩家选 3
         -> 按 t.no==3 点名找不到 -> 拒绝 noball（v3_shot.json why:"noball"）。
      ② 拒绝文案：noball 有一半是"有球但没有你选的编号"——必须说出"N 号不在、
         场上现有哪些号"（原来只说"没找到球——把球摆到边界上"，对②是误导）。
    """
    print('\n== fire_ballno：选球编号=发射口径（"控球3没反应"回归网）')
    try:
        import ball_game as bg
    except Exception as e:
        print('   （跳过：import 不了 ball_game —— %s）' % e)
        return

    class _Tk(object):
        pass

    _t4 = _Tk()
    _t4.no = 4
    _tn = _Tk()
    _tn.no = None
    _lbl = getattr(bg, '_track_num_label', None)
    _check('★ ball_game 有 _track_num_label（编号=发射点名那把号）',
           callable(_lbl))
    if callable(_lbl):
        _check('★ 圈旁编号 = t.no（{1,2,4} 时不许把 4 号画成 3）；None 不画',
               _lbl(_t4) == 4 and _lbl(_tn) is None)
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        _src2 = f.read()
    _check('★ 绘制块真的接了 _track_num_label(_tk)（不是列表序号）',
           '_track_num_label(_tk)' in _src2 and '_ti + 1' not in _src2)
    _m = bg._fire_msg('noball', {'no_such': 3, 'have': [1, 2, 4]}, 70.0)
    _check('★ noball 文案点名："3 号不在、现在场上是 1、2、4 号"',
           ('3 号' in _m) and ('1' in _m) and ('4' in _m), _m[:60])
    _mg = bg._fire_msg('noball', {}, 70.0)
    _check('★ 场上真没球时保持老文案（兼容）', '没找到球' in _mg, _mg[:40])

    class _ModG:
        FIELD_W = 960.0
        FIELD_H = 540.0
        BALL_R = 11.0
        IN_FIELD_MARGIN = 8.0

        @staticmethod
        def cam_to_game(_H, x, y):
            return (float(x), float(y))

        @staticmethod
        def game_to_cam(_H, x, y):
            return (float(x), float(y))

    _okc, _whyc, _ = bg._fire_gate(
        _ModG, None, dict(x=480.0, y=270.0, r=11.0), 0.0, 70.0)
    _oko, _whyo, _ = bg._fire_gate(
        _ModG, None, dict(x=-40.0, y=270.0, r=11.0), 0.0, 70.0)
    _check('★ 发射不限边界带：场区中心球可发，球身不沾场区才拒（2026-10-28）',
           _okc is True and _whyc is None
           and _oko is False and _whyo == 'out')
    _okan, _whyan, _infoan = bg._fire_gate(
        _ModG, None, dict(x=5.0, y=270.0, r=11.0), 90.0, 70.0)
    _check('★ 界面绝对角 0°=右/逆时针：90° -> 物理 270°；贴边但球身压场区可发',
           _okan is True and abs(_infoan['ang'] - 270.0) < 1e-6
           and abs(_infoan['ang_ui'] - 90.0) < 1e-6)
    _check('★ 界面角->物理角：0=右/90=上/180=左/270=下（y 向下取反）',
           [round(bg._ui_ang_to_phys(a), 6) for a in (0, 90, 180, 270)]
           == [0.0, 270.0, 180.0, 90.0])
    _check('★ 重排时选球跟着球走（高位球号换主 -> 玩家跟到新号；治「重排屏跳」）',
           'take_renumber(' in _src2 and '重排跟随' in _src2
           and '_v3in_write(player=_pk3, ball=_nn' in _src2)


def s_match_flow():
    """★ M2.2 局状态机（--match）—— 10 局制/弹药/计分/暂停的回归网 + 接线静态核。

    为什么这么验：局状态机是**纯逻辑**（`match_flow.py`，零硬件），判据全部在 PC 上
    可复现；`ball_game.py` 的接线用"源码 + AST"静态钉 —— 尤其两条铁律：
      ① `--match` **默认关**（不带它时 ball_game 行为一个字节都不许变）；
      ② `match_flow` 的 import 必须在"输入层开着吗"的罩子里（对局靠输入层读
         发射/就绪，没输入层它没有任何输入源 —— 静默 N/A 的同族事故已发生 3 次）。
    屏上中文走 PIL 贴片层（`_zh_hud`；cv2.putText 仍只许 ASCII）——**字库覆盖**
    也在这节钉死（缺字=豆腐块；子集字体重生成过，加文案要同步加字）。
    """
    import ast

    print('\n== match_flow：M2.2 局状态机（10 局制/弹药/计分/暂停）+ 接线静态核')
    import match_flow as mf

    # ---- 1) 常量与规则书对齐（漂了就是口径事故）----
    _check('常量：10 局 / +10 分 / 3红2蓝 / 结算 2.6s / 落定 5s / 洞口 30s / 暂停 30s',
           mf.MAX_ROUNDS == 10 and mf.GOAL_POINTS == 10
           and mf.AMMO0 == {1: 3, -1: 2} and mf.ROUND_END_DELAY == 2.6
           and mf.GRACE_SECS == 5.0
           and mf.TIMER_PERIOD == 30.0 and mf.PAUSE_MAX == 30.0)

    def _start_round(mx):
        """★ 2026-10-28：双方就绪 -> 无实体购买时进调整界面 -> 开始本局。"""
        mx.ready(1)
        mx.ready(2)
        if mx.phase == 'PREP' and getattr(mx, 'adjust', False):
            mx.begin_play()
        return mx

    def _end_round(mx):
        """★ v5：打光弹药 -> 落定 5s -> 结算 2.6s -> 下一局（给各节复用）。"""
        mx.ammo[1] = {1: 0, -1: 0}
        mx.ammo[2] = {1: 0, -1: 0}
        mx.update(0.01)
        mx.update(mf.GRACE_SECS + 0.01)
        mx.update(mf.ROUND_END_DELAY + 0.01)
        return mx.drain()

    # ---- 2) 弹药口径（按实物球颜色扣；未知色不设限）----
    m = mf.MatchFlow()
    m.drain()
    _check('第 1 局直接 PLAY（跳过局前）', m.phase == 'PLAY' and m.round == 1)
    ok, _ = m.can_fire(1, -1)
    _check('蓝球可发（2 颗）', ok and m.ammo[1][-1] == 2)
    m.consume(1, -1)
    m.consume(1, -1)
    ok2, why2 = m.can_fire(1, -1)
    _check('蓝球扣完 -> no_ammo；红球不受影响',
           (not ok2) and why2 == 'no_ammo' and m.ammo[1][1] == 3)
    ok3, _ = m.can_fire(1, 0)
    m.consume(1, 0)
    _check('未知色(charge=0) 不设限、不消耗', ok3 and m.ammo[1][1] == 3)

    # ---- 3) ★v5：进球不停局 / 弹药打光+5s 才结算 / 续局事件 ----
    m.on_goal(1)
    _check('★ v5：进球 +10、本局**继续**（phase 仍 PLAY、本局账 +1）',
           m.goals[1] == 1 and m.points[1] == 10 and m.phase == 'PLAY'
           and m.round_goals[1] == 1)
    m.on_goal(1)
    m.on_goal(2)
    _check('★ v5：一局可进多球（本局 2:1；总 20:10）',
           m.round_goals == {1: 2, 2: 1} and m.goals[1] == 2 and m.goals[2] == 1)
    m.update(1.0)
    _check('★ v5：弹药没打光就不结算（仍 PLAY、grace 未起表）',
           m.phase == 'PLAY' and m.grace is None)
    ev = _end_round(m)
    _check('★ v5：打光 -> ammo_done/round_over -> 第 2 局 round_start(next)；弹药重置',
           any(e[0] == 'ammo_done' for e in ev)
           and any(e[0] == 'round_start' and e[1] == 2 for e in ev)
           and m.ammo[1][1] == 3 and m.ammo[1][-1] == 2)

    # ---- 4) 暂停互斥（改磁场 vs 加洞口）----
    _start_round(m)
    m.points[1] = 50
    ok, _ = m.begin_edit(1)
    ok2, _ = m.begin_hole(2)
    _check('暂停互斥：EDIT 中另一方发起加洞口被拒（提示正确）',
           ok and (not ok2) and m.phase == 'EDIT' and '改磁场' in m.msg)
    m.end_edit(confirm=True)
    _check('改磁场确认 -> 扣 20 分、回 PLAY', m.points[1] == 30 and m.phase == 'PLAY')

    # ---- 5) 30s 洞口计时（暂停冻结）----
    m.update(29.0)
    e1 = any(e[0] == 'hole_due' for e in m.drain())
    m.update(1.5)
    e2 = any(e[0] == 'hole_due' for e in m.drain())
    _check('29s 不发 / 30.5s 发 hole_due（规则书 §8 的计时口径）', (not e1) and e2)

    # ---- 6) 十局 5:5 -> 加赛局 ----
    m2 = mf.MatchFlow()
    m2.drain()
    for r in range(mf.MAX_ROUNDS):
        if m2.phase == 'PREP':
            _start_round(m2)
        m2.on_goal(1 if r % 2 == 0 else 2)
        _end_round(m2)
    _check('十局 5:5 -> 加赛局（round 11、PREP 等就绪）',
           m2.round == mf.MAX_ROUNDS + 1 and m2.phase == 'PREP')

    # ---- 7) HUD 行只许 ASCII（铁律）----
    lines = []
    for ph in ('PLAY', 'PREP', 'ROUND_END', 'EDIT', 'HOLE', 'MATCH_END'):
        m3 = mf.MatchFlow()
        m3.phase = ph
        m3.edit['player'] = 1
        m3.hole['player'] = 2
        lines.append(m3.hud_line())
    _check('hud_line 六个阶段全 ASCII（cv2 画不出中文）',
           all(all(ord(c) < 128 for c in ln) for ln in lines), lines[0][:60])
    # ★ 2026-10-07 上板实玩发现（"按了发射没反应"的真身=局前里发射键变就绪键、
    #   却没有玩家可见的反馈）：局前 HUD 必须摆出双方就绪状态。
    m4 = mf.MatchFlow()
    m4.on_goal(1)
    _end_round(m4)                                  # -> 第 2 局 PREP
    _l0 = m4.hud_line()
    m4.ready(1)
    _l1 = m4.hud_line()
    _check('★ 局前 HUD 显示双方就绪状态（P1/P2 wait -> P1 READY）',
           ('P1 wait' in _l0 and 'P2 wait' in _l0
            and 'P1 READY' in _l1 and 'P2 wait' in _l1), _l1[:74])

    # ★★ 2026-10-28 局前流程：买实体件 -> 摆放 -> 就绪 -> 开始本局 / 无购买 -> 调整
    m5 = mf.MatchFlow()
    m5.drain()
    m5.on_goal(1)
    _end_round(m5)                                  # -> 第 2 局 PREP
    m5.points[1] = 80
    m5.sel_item[1] = 2                              # 障碍 10 分
    _ok5, _ = m5.buy(1)
    _check('★ 买实体障碍：记本局已买 + last_buy=obstacle',
           _ok5 and m5.phys_bought[1] and m5.last_buy == 'obstacle'
           and m5.items[1]['obstacle'] == 1)
    m5.ready(1)
    m5.ready(2)
    _check('★ 买了但没摆 -> 双就绪先进调整界面（不直接开局）',
           m5.phase == 'PREP' and m5.adjust)
    m5.mark_placed(1)                               # 玩家在调整界面里摆了
    m5.begin_play()
    _check('★ 摆过实体件 -> 开始本局 -> PLAY',
           m5.phase == 'PLAY' and not m5.adjust)
    _end_round(m5)                                  # -> 下一局 PREP（phys_* 复位）
    m5.ready(1)
    m5.ready(2)
    _check('★ 无实体购买 -> 双就绪进调整界面（仍 PREP）',
           m5.phase == 'PREP' and m5.adjust)
    m5.back_to_shop()
    _check('★ 调整界面 ESC -> 回商店（SHOP/SHOP）',
           m5.phase == 'PREP' and not m5.adjust
           and m5.sub[1] == 'SHOP' and m5.sub[2] == 'SHOP')
    _start_round(m5)
    _check('★ 调整界面「开始本局」-> PLAY', m5.phase == 'PLAY')

    # ★★ 2026-10-29 改磁场三步确认：选块 -> 确认 -> 方向 -> 确认 -> 数值 -> 确认
    import v3_scan as _vs_h
    import ball_game as bg
    m13c = mf.MatchFlow()
    m13c.drain()
    m13c.points[1] = 50                  # 改磁场需要 ≥20 分
    _ok13c, _ = m13c.begin_edit(1)
    _check('★ 改磁场：begin_edit -> EDIT/SELECT（先来后到）',
           _ok13c and m13c.phase == 'EDIT' and m13c.edit['stage'] == 'SELECT')
    _i13 = m13c.edit_next_block(3, 1)
    _i13b = m13c.edit_pick_block(2, 3)
    _check('★ 改磁场：A/D 切块 + 鼠标点块', _i13 == 1 and _i13b == 2)
    _sg0 = m13c.edit_set_sign(-1)
    _check('★ 改磁场：选块未确认 -> 方向键无效',
           _sg0 is None and m13c.edit['stage'] == 'SELECT')
    _st13a = m13c.edit_confirm_stage()
    _check('★ 改磁场：确认选块 -> DIR', _st13a == 'DIR'
           and m13c.edit['stage'] == 'DIR')
    _ty0 = m13c.edit_type('0')
    _check('★ 改磁场：方向未确认 -> 数值输入无效',
           _ty0 is None and m13c.edit['stage'] == 'DIR')
    _sg13 = m13c.edit_set_sign(-1)
    _check('★ 改磁场：DIR 定方向（-1，未确认不前进）',
           _sg13 == -1 and m13c.edit['stage'] == 'DIR')
    _st13b = m13c.edit_confirm_stage()
    _check('★ 改磁场：确认方向 -> VALUE（输入框清空）',
           _st13b == 'VALUE' and m13c.edit['stage'] == 'VALUE'
           and m13c.edit['txt'] == '')
    for _ch in ('0', '.', '3', '5'):
        m13c.edit_type(_ch)
    _t13 = m13c.edit_type('.')          # 第二个小数点被拒
    _t13b = m13c.edit_backspace()
    _check('★ 改磁场：数值输入（0.35 / 第二小数点拒 / 退格 -> 0.3）',
           m13c.edit['txt'] == '0.3' and _t13 is None and _t13b == '0.3')
    _st13c = m13c.edit_confirm_stage()
    _check('★ 改磁场：确认数值 -> CONFIRM（此时才落值）',
           _st13c == 'CONFIRM' and m13c.edit['stage'] == 'VALUE')
    _bk13a = m13c.edit_back()
    _bk13b = m13c.edit_back()
    _bk13c = m13c.edit_back()
    _check('★ 改磁场：ESC 退步 VALUE->DIR->SELECT->None（再退=取消）',
           _bk13a == 'DIR' and _bk13b == 'SELECT' and _bk13c is None)
    m13c.edit['stage'] = 'DIR'
    _sg13b = m13c.edit_set_sign(-1)
    m13c.edit['stage'] = 'VALUE'
    m13c.edit['txt'] = '0.3'            # ESC 退步已清空输入，测试重新填
    _w13 = _vs_h.build_world(7, 0.4)
    _bv13 = bg._edit_apply_value(m13c, _w13)
    _check('★ 改磁场：确认值 = 方向×大小（-0.3，夹 0.10~0.80）',
           _sg13b == -1 and abs(_bv13 + 0.3) < 1e-9
           and abs(float(_w13.blocks[2].b) + 0.3) < 1e-9)
    m13c.edit['txt'] = '9'
    _bv13b = bg._edit_apply_value(m13c, _w13)
    _check('★ 改磁场：超上限 9 -> 0.80', abs(_bv13b + 0.8) < 1e-9)
    m13c.edit['txt'] = ''
    _bv13c = bg._edit_apply_value(m13c, _w13)
    _check('★ 改磁场：空输入 -> 保留原 |b|（只改方向）',
           abs(_bv13c + 0.8) < 1e-9)
    m13c.end_edit(confirm=False)
    _check('★ 改磁场：取消 -> PLAY', m13c.phase == 'PLAY')

    # ★ 改磁场俯视图 + 鼠标（点块/点确认按钮）
    import numpy as _np13
    m13d = mf.MatchFlow()
    m13d.drain()
    m13d.points[1] = 50
    m13d.begin_edit(1)
    m13d.edit_confirm_stage()           # 确认选块 -> DIR
    m13d.edit_set_sign(1)
    m13d.edit_confirm_stage()           # 确认方向 -> VALUE
    for _ch in ('0', '.', '3'):
        m13d.edit_type(_ch)
    bg._build_edit_view(1280, 720, m13d, mf, _w13, [])
    _rr13 = (m13d.edit.get('rects') or {}).get('ok')
    _check('★ 改磁场俯视图可渲染（输入框/按钮/块高亮）',
           _rr13 is not None and len(_rr13) == 4)
    _H13 = type('H13', (), {})
    _h13 = _H13()
    _h13.mx = (_rr13[0] + _rr13[2]) // 2
    _h13.my = (_rr13[1] + _rr13[3]) // 2
    _hit13 = bg._edit_mouse_hit(m13d, _h13, 1280, 720, _w13)
    _check('★ 改磁场：鼠标点「确认」按钮 -> 记动作 ok',
           _hit13 and bg._EDC.get('mouse_act') == 'ok')
    bg._EDC.pop('mouse_act', None)
    _b013 = _w13.blocks[0]
    _h13.mx = int((1280 - 960) // 2 + (_b013.x0 + _b013.x1) / 2)
    _h13.my = int((720 - 540) // 2 + (_b013.y0 + _b013.y1) / 2)
    bg._edit_mouse_hit(m13d, _h13, 1280, 720, _w13)
    _check('★ 改磁场：鼠标点块 -> 选中该块', int(m13d.edit['idx']) == 0)
    m13d.end_edit(confirm=False)

    # ★★ 2026-10-28：调整界面的画面/按键接线冒烟（纯函数 + 文案层）
    import ball_game as bg
    m6 = mf.MatchFlow()
    m6.drain()
    m6.on_goal(1)
    _end_round(m6)
    m6.ready(1)
    m6.ready(2)                                   # -> 调整界面
    bg._ADJ['on'] = False
    bg._adj_sync(m6)
    _check('★ _adj_sync：MatchFlow.adjust -> _ADJ 开（关商店）',
           bg._ADJ['on'] is True and m6.phase == 'PREP')
    import numpy as _np6
    _img_a = _np6.zeros((720, 1280, 3), _np6.uint8)
    bg._pplace_text(_img_a, 1280, 720, m6,
                    dict(bg._ADJ, status='当前：障碍 1 根 / 传送门 0 对'),
                    (10, 10))
    _check('★ 调整界面文案层可渲染（不抛错）', True)
    # ★ 购买询问：相机没稳时点「立即摆放」-> 失败但**询问框保留**（不许静默关掉）
    m7 = mf.MatchFlow(); m7.drain(); m7.on_goal(1)
    _end_round(m7)
    m7.points[1] = 50; m7.sel_item[1] = 2
    m7.buy(1)
    bg._PHYS['base_ready'] = False
    bg._shop_ask_use(m7, 1, 1, 0)
    _ok7, _ = bg._pbu_resolve(m7, True)
    _check('★ 购买询问：立即摆放失败 -> 询问框保留可重试',
           (not _ok7) and bg._PBUY['on'] is True)
    bg._PPLACE['on'] = False
    bg._PBUY['on'] = False
    bg._PHYS['base_ready'] = True

    # ★★ 2026-10-28：对局中摆放 = PLACE 暂停阶段（球/计时全冻）
    m8 = mf.MatchFlow()
    m8.drain()                                   # 第 1 局 PLAY
    m8.items[1]['obstacle'] = 1
    bg._PHYS['base_ready'] = True
    bg._PPLACE['on'] = False
    _ok8, _msg8 = bg._pplace_enter(m8, 1, 'obstacle')
    _check('★ 对局中按 V -> PLACE 暂停阶段（in_match）',
           _ok8 and m8.phase == 'PLACE' and bg._PPLACE.get('in_match'))
    bg._pplace_clear()
    _check('★ 摆放结束 -> end_place 回 PLAY', m8.phase == 'PLAY')
    _src8 = open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8').read()
    _check('★ "离开局前"守卫不再秒杀对局中摆放（PLACE 白名单）',
           "phase not in ('PREP', 'PLACE')" in _src8)

    # ★★ 2026-10-29：调整界面开始本局 -> 先锁**调整世界**（挪过的件才进对局）
    import engine as _eng8
    _w8 = type('W8', (), {})()
    _w8.obstacles = [_eng8.Obstacle(700.0, 400.0, 120.0, 14.0, 30.0)]
    _w8.portals = []
    _w8.holes = []
    m8b = mf.MatchFlow()
    m8b.drain()
    m8b.on_goal(1)
    _end_round(m8b)
    m8b.ready(1)
    m8b.ready(2)                        # -> adjust
    bg._ADJ['on'] = True
    bg._PHYS['lock'] = False
    bg._PHYS['lock_obs'] = []
    _ok8b, _ = bg._adj_start(m8b, 1, _w8)
    _check('★ 调整界面开始本局：先锁调整世界 + 基线重定到新位置',
           _ok8b and m8b.phase == 'PLAY'
           and len(bg._PHYS.get('lock_obs') or []) == 1
           and abs(bg._PHYS['lock_obs'][0][0] - 700.0) < 1e-6
           and [(round(x), round(y)) for (x, y)
                in (bg._PHYS.get('base_bars') or [])] == [(700, 400)]
           and len(bg._FROZEN.get('obs') or []) == 1)
    bg._PHYS['lock'] = False
    bg._PHYS['lock_obs'] = []
    bg._FROZEN['obs'] = []
    bg._FROZEN['ports'] = []
    bg._ADJ['on'] = True          # m6 仍在调整界面，保留给下一条用例

    _oka6, _ = bg._adj_start(m6, 1)
    _check('★ _adj_start：调整界面开始本局 -> PLAY',
           _oka6 and m6.phase == 'PLAY' and not bg._ADJ['on'])

    # ---- 8) ball_game 接线（源码 + AST 静态核）----
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        src = f.read()
    _check('--match 默认关（match_on = False；不带它行为不许变）',
           'match_on = False' in src and "a == '--match'" in src)
    _check('局前里发射键=就绪键（PREP -> ready(_pk)）',
           "phase == 'PREP'" in src and 'ready(_pk)' in src)
    _check('★ 2026-10-28 局前流程接线：购买询问/调整界面/开始本局',
           'def _shop_ask_use' in src and 'def _pbu_resolve' in src
           and src.count('_shop_ask_use(_MF') >= 2
           and 'def _adj_sync' in src and 'def _adj_start' in src
           and '_adj_start(_MF[' in src
           and 'if _PPLACE[' in src and '_ADJ[' in src and '_PBUY[' in src)
    _check('★ 购买询问进商店内容键（防"提示框被切块/关掉后旧图还在"）',
           "_PBUY['on'], _PBUY['who'], _PBUY['kind']," in src)
    _check('★ 2026-10-28 对局中临时摆放：_pplace_enter 允许 PLAY + 写入锁定副本',
           "m.phase not in ('PREP', 'PLAY')" in src
           and "lock_obs', []).append" in src
           and "'本局临时 '" in src)
    _check('★ 2026-10-28 渲染回滚：场区框原色 2px / B 标签无黑底牌',
           '(0, 165, 255), 2)' in src and '_blocks_b' not in src)
    _check('★ 2026-11-01 实球跨局保留：round_start 只清死球（v5 在飞续局恢复）',
           'live_balls[:] = [b for b in live_balls if b.alive]' in src
           and '实球跨局保留' in src)
    _check('对局拒发走同一条报告路（shot.json；n_paused/n_round_end/n_no_ammo 有账）',
           "'n_' + _mwhy" in src and 'n_no_ammo' in src and '_mwhy' in src)
    _check('发射带上发射者（who=_pk；LiveBall.__slots__ 声明了 who）',
           'who=_pk' in src
           and 'who' in open(os.path.join(HERE, 'realtime_ball.py'),
                             encoding='utf-8').read())
    _check('进球记分读 b.who（on_goal）', 'on_goal(_b.who)' in src)
    _check('★ v5 续局：round_start 保留活球（在飞续局+剔旧），不再 clear()',
           '在飞续局' in src and 'live_balls.clear()' not in src)
    _check('★ v5 冻结：非 PLAY 阶段不推球（advance 以 phase==PLAY 为门）',
           '全场冻结' in src and "phase != 'PLAY'" in src)
    _check('★ HUD 走中文贴片（_zh_hud/_match_line_cn/_hud_top_line；旧 cv2 长句已拆）',
           '_zh_hud(img, _hud)' in src and '_match_line_cn(' in src
           and '_hud_top_line(' in src and 'BALL CENTER' not in src)
    _check('★ 局前"就绪"写一笔 shot.json（手机可见反馈；why=ready）',
           "why='ready'" in src and '已就绪——' in src)
    _check('★ 控球场上高亮（玩家色外环+P1/P2 标——治"选球认错"）',
           '_sel.append(((' in src and "(0, 230, 0), 'P1'" in src
           and '控球%d' in src)
    _check('★ 旧版黄圈"主球"在 fire 模式已关（"屏幕跳动"实锤：会在同色球间翻面）',
           '球轨迹都跟着变' in src and 'and angle2 is None:' in src)
    _check('★ 标签夹进画面（"P1 跳出屏幕外"修复：左侧放不下翻转+两轴夹边）',
           'if _tlx < 4:' in src and '_tlx = max(4, min(_W2 - 44, _tlx))' in src
           and '_ny = max(22, min(_nh - 8, _tkl[1]))' in src)

    tree = ast.parse(src)

    def _is_in_guard(test):
        for n in ast.walk(test):
            if isinstance(n, ast.Name) and n.id in ('v3_input', 'v3_hub'):
                return True
        return False

    hit = []

    def walk(node, ok):
        for ch in ast.iter_child_nodes(node):
            g = ok
            if isinstance(ch, ast.If) and _is_in_guard(ch.test):
                g = True
            if isinstance(ch, ast.Import):
                for nm in ch.names:
                    if nm.name == 'match_flow':
                        hit.append((ch.lineno, ok))
            walk(ch, g)

    walk(tree, False)
    _check('match_flow 的 import 在"输入层开着吗"的罩子里（AST 走查）',
           bool(hit) and all(ok for _, ok in hit),
           'import 行 %s' % str([ln for ln, _ in hit]))

    # ---- 9) 拒发文案（手机网页直接显示的中文）----
    import ball_game as bg
    _m1 = bg._fire_msg('paused', {}, 70.0)
    _m2 = bg._fire_msg('round_end', {}, 70.0)
    _m3 = bg._fire_msg('no_ammo', {'red': 0, 'blue': 2}, 70.0)
    _check('对局拒发文案：暂停/结算中/弹药用完（带剩余数）',
           '暂停' in _m1 and '结算' in _m2 and ('0' in _m3 and '2' in _m3),
           (_m3[:44] if _m3 else ''))

    # ---- 9.5) ★ M2.3：对局状态快照 + 链路退化检测（今天花屏的收尾账）----
    _st = {'n_last': 0, 'times': []}
    _n = [bg._link_track(_st, v, t) for v, t in
          ((0, 1000.0), (1, 1010.0), (1, 1500.0), (4, 1600.0),
           (4, 1850.0), (4, 1950.0))]
    _check('★ 链路脉冲滚动窗口：差分计数 / 超窗滑出（%s）' % _n,
           _n == [0, 1, 0, 3, 3, 0])
    _sn = bg._match_snapshot(mf.MatchFlow(), 3, mf)
    _check('★ 对局快照：开/轮次/弹药/倒计时/阈警告置位',
           _sn and _sn['on'] and _sn['round'] == 1 and _sn['phase'] == 'PLAY'
           and _sn['p1']['red'] == 3 and _sn['p1']['blue'] == 2
           and _sn['timer'] is not None and _sn['link_warn'] is True
           and _sn['p1']['ready'] is None)
    _sn2 = bg._match_snapshot(m4, 0, mf)
    _check('★ 快照就绪字段（PREP：P1 True / P2 False；不警告）',
           _sn2['phase'] == 'PREP' and _sn2['p1']['ready'] is True
           and _sn2['p2']['ready'] is False and _sn2['link_warn'] is False)
    _check('★ 非对局快照 = None（网页整块隐藏）', bg._match_snapshot(None, 0) is None)
    _check('★ 对局状态每 0.5s 原子写（_match_write(_match_snapshot(...)) + _MATCH_PATH）',
           '_match_write(_match_snapshot(' in src and '_MATCH_PATH' in src)
    with open(os.path.join(HERE, 'v3_input.py'), encoding='utf-8') as f:
        _srci = f.read()
    _check('★ serve 有 /match 端点 + read_match（空/坏一律空 dict）',
           "u.path == '/match'" in _srci and 'def read_match(' in _srci
           and 'MATCH_PATH' in _srci)

    # ---- 9.6) ★ 屏上中文 HUD（贴片层）+ 字库覆盖（缺字=豆腐块，改文案必查）----
    _l1 = bg._hud_top_line(26.0, 400.0, 1, None, None, None)
    _l2 = bg._hud_top_line(26.0, 400.0, 1, -12.0, 1040.0, 2)
    _check('★ 顶行中文短句（单人/双人）',
           '角度' in _l1 and '球1' in _l2 and 'P2' in _l2, _l2)
    _cns = []
    for _ph in ('PLAY', 'PREP', 'ROUND_END', 'EDIT', 'MATCH_END'):
        _s = bg._match_snapshot(mf.MatchFlow(), 0, mf)
        _s['phase'] = _ph
        if _ph == 'PREP':
            _s['p1']['ready'] = True
        _cns.append(bg._match_line_cn(_s) or '')
    _check('★ 对局行中文五态（比分/弹药/就绪/结束/暂停/比赛结束）',
           ('比分' in _cns[0] and '弹药' in _cns[0] and '剩' in _cns[0]
            and '就绪' in _cns[1] and '本局结束' in _cns[2]
            and '暂停' in _cns[3] and '比赛结束' in _cns[4]),
           _cns[0][:46])
    _s2 = bg._match_snapshot(mf.MatchFlow(), 0, mf)
    _s2['grace_left'] = 3.2
    _m2 = bg._match_line_cn(_s2) or ''
    _check('★ 落定期中文（"弹药打光——等球落定 3 秒"）',
           ('打光' in _m2) and ('落定' in _m2), _m2)
    # ---- 9.7) ★ 局前选购（道具系统 v1，规则书 §6.2）----
    _sw = bg._shop_apply(mf.MatchFlow(), 1, 'buy')      # 第 1 局 PLAY 态
    _check('★ 选购事件在非 PREP/SHOP 态一律 None（局中 W 仍是角度键）',
           _sw is None)
    _m12 = mf.MatchFlow()
    _m12.drain()
    _m12.on_goal(1)
    _m12.ammo[1] = {1: 0, -1: 0}
    _m12.ammo[2] = {1: 0, -1: 0}
    _m12.update(0.01)
    _m12.update(mf.GRACE_SECS + 0.01)
    _m12.update(mf.ROUND_END_DELAY + 0.01)              # -> 第 2 局 PREP/SHOP
    _lb12 = bg._shop_apply(_m12, 1, 'mdn')
    _check('★ 选购接线：mdn 下移 -> 选：改磁场 20分',
           _lb12 == '选：改磁场 20分', str(_lb12))
    bg._shop_apply(_m12, 1, 'mup')                       # 挪回加球（10 分）
    _okb, _msgb = _m12.buy(1)                            # 10 分刚好够
    _okb2, _msgb2 = _m12.buy(1)                          # 钱已花光，再买被拒
    _check('★ 购买播报：成功文案 / 积分不足被拒',
           _okb and ('购买' in (_msgb or '')) and (not _okb2)
           and ('积分不足' in (_msgb2 or '')), str(_msgb2))
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as _f12:
        _srcbg2 = _f12.read()
    _check('★ 选购接线静态核（上升沿 + mup/mdn/buy 三键 + shot.json kind=shop）',
           '_shop_apply(_MF' in _srcbg2 and 'v3_hub.mup1' in _srcbg2
           and "kind='shop'" in _srcbg2 and '_SHOP[' in _srcbg2)
    _check('★ v3_input 记选购计数器（p1_mup/p1_mdn/p1_buy + hub 读回）',
           '_nav_cnt' in _srci and 'p1_mup' in _srci and 'self.mup1' in _srci)
    _labs12 = []
    _m13 = mf.MatchFlow()
    for _i12 in range(len(mf.ITEMS)):
        _m13.sel_item[1] = _i12
        _labs12.append(_m13.shop_label(1))
    _check('★ 五件道具短标签（屏/手机共用）',
           _labs12 == ['加球 10分', '改磁场 20分', '障碍 10分',
                       '加洞 30分', '传送门 30分'], str(_labs12))

    # ---- 9.8) ★★ 商店 v2（2026-10-08；用户定案：商店画面+鼠标+手机+玩家分区）----
    _r14 = bg._shop_layout(1280, 720)
    _inb14 = all(0 <= x0 and 0 <= y0 and x1 <= 1280 and y1 <= 720
                 for (x0, y0, x1, y1) in _r14.values())
    _check('★ 商店布局：14 个命中框全部在 1280x720 画布内（不越界）',
           _inb14 and len(_r14) == 14, '框数=%d' % len(_r14))
    _hit14 = (bg._shop_click(100, 200, _r14) == 'p1_it0'
              and bg._shop_click(700, 200, _r14) == 'p2_it0'
              and bg._shop_click(120, _r14['p1_buy'][1] + 10, _r14) == 'p1_buy'
              and bg._shop_click(1000, _r14['p2_done'][1] + 10, _r14) == 'p2_done')
    _check('★ 商店点击命中：左半=P1 / 右半=P2 / 购买与完成各归各位', _hit14,
           '%s' % [bg._shop_click(100, 200, _r14), bg._shop_click(700, 200, _r14)])
    _check('★ 商店点击：画布外/空表安全 None（不炸）',
           bg._shop_click(5000, 5000, _r14) is None
           and bg._shop_click(1, 1, {}) is None)
    _check('★ 商店 v2：/match 快照带 shop_open（手机按钮切换用）',
           bg._match_snapshot(_m13, 0, mf, shop_open=True).get('shop_open') is True
           and bg._match_snapshot(_m13, 0, mf).get('shop_open') is False)
    _skv14 = bg._match_snapshot(_m13, 0, mf)
    _skv14['shop_open'] = True
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as _fs2:
        _srcs2 = _fs2.read()
    _check('★ 商店 v2 接线静态核（入口浮层/商店画面/点击/自动关/shot 播报）',
           '_build_shop(' in _srcs2 and '_shop_prep_overlay(' in _srcs2
           and "_shop_click(v3_hub.mx, v3_hub.my" in _srcs2
           and "_SHOPV['open'] = False" in _srcs2
           and "'shop_open': bool(shop_open)" in _srcs2
           and "kind='shop'" in _srcs2)
    with open(os.path.join(HERE, 'v3_input.py'), encoding='utf-8') as _fs3:
        _srcs3 = _fs3.read()
    _check('★ 商店 v2 输入层静态核（鼠标 REL/左键 272/sopen//shop 端点）',
           'def mouse_step' in _srcs3 and 'def _find_mice' in _srcs3
           and '_cod2 == 272' in _srcs3 and 'sopen' in _srcs3
           and "u.path == '/shop'" in _srcs3 and "act == 'enter'" in _srcs3)
    _check('★ 鼠标 HID-debugfs 旁路接线（解析器 + 自动回落 + 线程 + 全局写锁）',
           'def parse_hid_mouse' in _srcs3 and '_hid_mouse_dbgfs' in _srcs3
           and '_hid_mouse_loop' in _srcs3 and 'threading.Thread' in _srcs3
           and '_WSLOCK' in _srcs3 and 'def _write_state_unlocked' in _srcs3)
    import v3_input as _vi9
    _rp9 = _vi9.parse_hid_mouse(
        'report (size 9) (numbered) =  02 00 00 ba ff fc ff 00 00')
    _check('★ HID 鼠标报文解析：实测样例得 (-70,-4,0)；垃圾行安全 None',
           _rp9 == (-70, -4, 0) and _vi9.parse_hid_mouse('Button.0001 = 0') is None
           and _vi9.parse_hid_mouse('') is None, str(_rp9))
    _check("★ 键位实锤：小键盘* = 码 55（不是 98）；E=P1 买",
           bg is not None and _srcs3.count('55: (2,') >= 1
           and _srcs3.count('98: (2,') == 0)
    _check('★ P2 加洞口第二入口 /（码 53，与 小键盘+ 等价；53 此前无映射）',
           '53: (2,' in _srcs3 and "53: '/'" in _srcs3
           and _srcs3.count('53: (2,') == 1)
    _check('★ 原图字序跟地图自检实时推导（swap^mswap^1——重下 bit 翻字序自愈）',
           'swap ^ mswap ^ 1' in _srcs2
           and '_rsw = ' in _srcs2)
    _check('★ 局前合成入口画面接线（_build_prep_gate + 统一门 _gate）',
           'def _build_prep_gate' in _srcs2 and '_prep_gate' in _srcs2
           and '_gate = _shop_on or _prep_gate' in _srcs2
           and ('if _gate:' in _srcs2 or "if _gate or _PPLACE['" in _srcs2)
           and _srcs2.count('not _gate and not _hole_on') >= 2)
    _check('★ 光标提速三件套：店里跳抓图检测 / 商店基图缓存 / 读取器 40ms 节流',
           '_shop_on' in _srcs2 and '_SHOPCACHE' in _srcs2
           and '_shop_on = _SHOPV[' in _srcs2
           and "< 0.04" in _srcs3 and '40ms' in _srcs3)
    # ---- 9.9) ★★ 局中道具（2026-10-14，规则书 §6.4/§7.1）----
    class _Blk9:
        def __init__(self, b):
            self.b = b
    _b9 = _Blk9(0.40)
    _r9 = tuple(round(bg._edit_apply(_b9, _a9), 3)
                for _a9 in ('dir-', 'mag-', 'dir+', 'mag+'))
    _check('★ _edit_apply 四式（序列 0.40→-0.40→-0.35→+0.35→+0.40）',
           _r9 == (-0.40, -0.35, 0.35, 0.40), str(_r9))
    _b9.b = 0.78
    _r9b = bg._edit_apply(_b9, 'mag+')
    _check('★ _edit_apply 强度封顶 0.80', _r9b == 0.80, str(_r9b))
    # ---- 9.16) ★ 布置 PLACE v1（2026-10-19 三天计划第 1 天）：障碍摆放 ----
    import v3_scan as _vs16
    import engine as _eng16
    _w16 = _vs16.build_world(7, 0.40, n_blocks=3)
    _ex16, _ey16 = bg._obst_half_extent(bg.OBS_W_GAME, bg.OBS_H_GAME, 0.0)
    _ov16 = (bg._obst_valid(_w16, 480.0, 270.0),           # 场中：过
             bg._obst_valid(_w16, 10.0, 270.0),            # 贴左边（中心在场内）：过
             bg._obst_valid(_w16, 480.0, 40.0),            # 上边：过
             bg._obst_valid(_w16, 480.0, 40.0, ang=90.0),  # 竖起（中心在场内）：过
             bg._obst_valid(_w16, 992.0, -53.0),           # 角落（中心出界 60 内）：过
             bg._obst_valid(_w16, -70.0, 270.0),           # 中心出界 >60：拒
             bg._obst_valid(_w16, 480.0, 610.0))           # 中心出界 >60：拒
    _check('★ 障碍合法性 _obst_valid（2026-10-29：中心可出界 60px，边角可摆）',
           _ov16 == (True, True, True, True, True, False, False), str(_ov16))
    _h16 = _w16.holes[0]
    _c16 = float(_h16['c'])
    if _h16['edge'] in ('left', 'right'):
        _cx16 = ((960.0 - 20.0 - _ex16 - 1.0) if _h16['edge'] == 'right'
                 else (20.0 + _ex16 + 1.0))
        _cy16 = _c16
    else:
        _cx16 = _c16
        _cy16 = ((20.0 + _ey16 + 1.0) if _h16['edge'] == 'top'
                 else (540.0 - 20.0 - _ey16 - 1.0))
    _check('★ 障碍合法性：盖住洞口拒绝（贴墙未越墙、但离洞太近）',
           not bg._obst_valid(_w16, _cx16, _cy16),
           '(%0.f,%0.f)' % (_cx16, _cy16))
    _w16.obstacles = [_eng16.Obstacle(480.0, 270.0, 120.0, 14.0, 0.0)]
    _ov16b = (bg._obst_valid(_w16, 480.0, 270.0),          # 与已有障碍叠：拒
              bg._obst_valid(_w16, 480.0, 450.0))          # 离开：过
    _check('★ 障碍合法性：与已有障碍叠放拒绝', _ov16b == (False, True),
           str(_ov16b))
    _tp16 = bg._obst_tuples([_eng16.Obstacle(100.0, 200.0, 120.0, 14.0, 90.0)])
    _check('★ engine.Obstacle -> realtime_ball 元组口径 (cx,cy,w,h,ang)',
           len(_tp16) == 1 and _tp16[0][:4] == (100.0, 200.0, 120.0, 14.0)
           and abs(_tp16[0][4] - 90.0) < 1e-9, str(_tp16))
    # ---- 9.17) ★ 实物识别（2026-10-19 用户定案：黑长条/绿椭圆/黑球）----
    import phys_objects as _po17
    import numpy as _np17
    try:
        import cv2 as _cv17
    except ImportError:
        _cv17 = None
    _ok17 = False
    if _cv17 is not None:
        _im17 = _np17.full((90, 128, 3), (232, 232, 232), _np17.uint8)
        _a17 = _np17.radians(30.0)
        # ★ 2026-10-23：黑条最长门禁 60→30（真条 ~25 map px）后，合成图原来
        #   半长 15（总长 31.3）刚好越过门禁被拒 —— 台架自己先撞线了。
        #   改半长 13（总长 ~27.3）：仍是合法长条，且离 30 有 2.7 余量。
        _h17 = 13.0
        _cv17.line(_im17, (int(60 - _h17 * _np17.cos(_a17)),
                           int(45 - _h17 * _np17.sin(_a17))),
                   (int(60 + _h17 * _np17.cos(_a17)),
                    int(45 + _h17 * _np17.sin(_a17))),
                   (10, 10, 10), 2, _cv17.LINE_AA)
        _cv17.ellipse(_im17, (100, 20), (8, 4), 0, 0, 360, (40, 200, 40), -1)
        _cv17.circle(_im17, (20, 70), 3, (15, 15, 15), -1)
        _r17 = _po17.detect(_im17)
        _ok17 = (len(_r17['bars']) == 1
                 and abs(_r17['bars'][0]['length'] - 30) < 4
                 and abs((_r17['bars'][0]['ang'] % 180) - 30) < 8
                 and abs(_r17['bars'][0]['cx'] - 60) < 2
                 and len(_r17['balls']) == 1
                 and abs(_r17['balls'][0]['cx'] - 20) < 1.5
                 and len(_r17['greens']) == 1
                 and abs(_r17['greens'][0]['cx'] - 100) < 1.5)
        _det17 = str(([round(b['length'], 1) for b in _r17['bars']],
                      len(_r17['balls']), len(_r17['greens'])))
    else:
        _det17 = 'no cv2'
    _check('★ 实物识别合成图：黑长条(心/长/角)/绿椭圆/黑球 全中', _ok17, _det17)
    # ★★ 2026-10-29：黑条归属按"点到线段"匹配 + 识别闪断 2s 保持
    _check('★ 条归属：中心漂到 58px 外、但基线点落在条身上仍算同一根',
           bg._near_any(242.0, 318.0, [(300.0, 300.0)], 30.0,
                        200.0, 0.0) is True
           and bg._near_any(242.0, 418.0, [(300.0, 300.0)], 30.0,
                            200.0, 0.0) is False)
    _phc = {}
    _c1 = bg._coast(_phc, 'k', [(480.0, 270.0, 120.0, 14.0, 0.0)], 100.0)
    _c2 = bg._coast(_phc, 'k', [], 101.0)
    _c3 = bg._coast(_phc, 'k', [], 104.0)
    import engine as _engF
    bg._FROZEN['obs'] = [(100.0, 100.0, 120.0, 14.0, 0.0)]
    bg._FROZEN['ports'] = []
    _check('★ 确认即冻结：同根识别件被剔除、冻结件优先',
           bg._frozen_hit_obs(_engF.Obstacle(110.0, 102.0, 120.0, 14.0, 0.0))
           and not bg._frozen_hit_obs(
               _engF.Obstacle(400.0, 300.0, 120.0, 14.0, 0.0))
           and len(bg._frozen_merge(
               [(110.0, 101.0, 120.0, 14.0, 0.0),
                (400.0, 300.0, 120.0, 14.0, 0.0)])) == 2)
    bg._FROZEN['obs'] = []
    _check('★ 识别闪断保持：漏 1s 还在、超 2s 才消失',
           len(_c1) == 1 and len(_c2) == 1 and len(_c3) == 0,
           str((len(_c1), len(_c2), len(_c3))))
    # ★★ 2026-10-29 用户实况（"两个洞只有一个能进球"）：target 全摊 0 + 左洞物理
    import v3_scan as _vs_h
    import realtime_ball as _rtb_h
    _w_h = _vs_h.build_world(7, 0.4)
    bg._norm_holes(_w_h)
    _check('★ 开局两个洞 target 全归 0（左右都算进球）',
           len(_w_h.holes) == 2
           and all(int(h.get('target', 9)) == 0 for h in _w_h.holes),
           str([(h['edge'], h['target']) for h in _w_h.holes]))
    _hl = [h for h in _w_h.holes if h['edge'] == 'left'][0]
    _bh = _rtb_h.LiveBall(30.0, float(_hl['c']), 180.0, 400.0)
    _fh = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in _w_h.blocks]
    _eh = None
    for _ in range(300):
        _eh = _bh.step(_fh, _w_h.holes, 0)
        if _eh:
            break
    _check('★ 左洞能进球（真球物理 side=0）', _eh == 'goal', str(_eh))
    # ★★ 2026-10-28：摆放界面改用**全分辨率原图**识别（scale=8）——单独钉一档
    _ok17r = False
    if _cv17 is not None:
        _im17r = _np17.full((720, 1024, 3), (232, 232, 232), _np17.uint8)
        _ar = _np17.radians(20.0)
        _hr = 100.0
        _cv17.line(_im17r,
                   (int(500 - _hr * _np17.cos(_ar)),
                    int(300 - _hr * _np17.sin(_ar))),
                   (int(500 + _hr * _np17.cos(_ar)),
                    int(300 + _hr * _np17.sin(_ar))),
                   (10, 10, 10), 12, _cv17.LINE_AA)
        _cv17.ellipse(_im17r, (700, 400), (22, 11), 0, 0, 360,
                      (40, 200, 40), -1)
        _cv17.circle(_im17r, (120, 500), 20, (15, 15, 15), -1)
        _r17r = _po17.detect(_im17r, scale=8.0)
        _ok17r = (len(_r17r['bars']) == 1
                  and abs(_r17r['bars'][0]['length'] - 200) < 14
                  and abs((_r17r['bars'][0]['ang'] % 180) - 20) < 8
                  and abs(_r17r['bars'][0]['cx'] - 500) < 4
                  and len(_r17r['balls']) == 1
                  and abs(_r17r['balls'][0]['cx'] - 120) < 4
                  and len(_r17r['greens']) == 1
                  and abs(_r17r['greens'][0]['cx'] - 700) < 4)
        _det17r = str(([round(b['length'], 1) for b in _r17r['bars']],
                       len(_r17r['balls']), len(_r17r['greens'])))
    else:
        _det17r = 'no cv2'
    _check('★ 原图识别（scale=8）：黑条/绿门/黑球在 1024x720 上全中',
           _ok17r, _det17r)
    _check('★ 实物识别接线（每帧调用 / 障碍物理优先 / 传送门进物理 / 黑洞标记 / 自适应让位）',
           '_po.detect(mbgr)' in _srcs2 and '_phys_update(' in _srcs2
           and 'portals=_portal_tuples(' in _srcs2
           and "_PHYS.get('mark_on')" in _srcs2
           and ('world.portals = []' in _srcs2
                or 'world.portals = [_eng.Portal(' in _srcs2)
           and 'obstacles=_obst_tuples(' in _srcs2)
    _check('★ 布置 PLACE 接线：键位/摆放画面/物理/世界回注/计数基准',
           "47: (1, 'obs'" in _srcs3 and "74: (2, 'obs'" in _srcs3
           and "47: 'V', 74: '小键盘-'" in _srcs3
           and 'def _build_obsp_view' in _srcs2
           and 'if _OBSP[' in _srcs2
           and 'obstacles=_obst_tuples(' in _srcs2
           # ★ 2026-11-01：残局种子也回注一次障碍 -> 3 处（初建/换局/种子）
           and _srcs2.count('v3_world.obstacles = _OBST') == 3
           and "'obs1': 0, 'obs2': 0" in _srcs2)
    # ---- 9.18) ★ PL 原生障碍/传送门（2026-10-19，**旋转矩形** + 圆形门）----
    import v3_fixedpoint as _vf18
    import engine as _eng18
    import math as _m18
    _ab18 = None
    try:
        _ob18 = _eng18.Obstacle(480.0, 270.0, 120.0, 14.0, 15.0)
        _ab18 = _vf18.obstacle_aabb(_ob18)
        _ca, _sa = _m18.cos(_m18.radians(15.0)), _m18.sin(_m18.radians(15.0))
        _ax, _ay = 60.0 * _ca + 7.0 * _sa + 11.0, 60.0 * _sa + 7.0 * _ca + 11.0
        _ok18a = (abs(_ab18[0] - (480.0 - _ax)) < 1e-6
                  and abs(_ab18[1] - (270.0 - _ay)) < 1e-6
                  and abs(_ab18[2] - (480.0 + _ax)) < 1e-6
                  and abs(_ab18[3] - (270.0 + _ay)) < 1e-6)
    except Exception:
        _ok18a = False
    _check('★ PL 障碍 AABB：旋转矩形扩张 BALL_R 后的包围盒', _ok18a, str(_ab18))
    _F18 = [(0, 0, 960, 540, 0.0)]
    _r18a = _vf18.lane_fixed(100, 270, 0.0, 400.0, 1, _F18, [], 0, steps=200,
                             max_bounce=6,
                             obstacles=[(310.0, 270.0, 10.0, 70.0, 0.0)])
    _r18b = _vf18.lane_fixed(100, 270, 0.0, 400.0, 1, _F18, [], 0, steps=200,
                             max_bounce=6, portals=[(200, 270, 18),
                                                    (400, 270, 18)])
    _check('★ 黄金模型：AABB 反弹 / 传送门瞬移（与 RTL tb_traj_phys 同值）',
           abs(_r18a['x'] - 145.555) < 0.01 and abs(_r18b['x'] - 679.195) < 0.01,
           '%.3f/%.3f' % (_r18a['x'], _r18b['x']))
    import v3_pl as _v3p18
    import particle_pl as _pep18
    _check('★ 场景寄存器常量：V3 OBS 0x030 / PORT 0x044；PE OBS 0x04C / PORT 0x060 / R 0x074',
           _v3p18.R_OBS == 0x030 and _v3p18.R_PORT == 0x044
           and _pep18.R_OBS_EN == 0x04C and _pep18.R_PORT_EN == 0x060
           and _pep18.R_PORT_R == 0x074)
    class _MM18:
        def __init__(self):
            self.w = {}
        def wr_u32(self, a, v):
            self.w[a] = v
    _mm18 = _MM18()
    _sc18 = dict(fields=_F18, holes=[], x0=100, y0=270, ang0=0.0, sp=400.0,
                 q=1, steps=10, bounce=0, side=0, fan_half=1.0,
                 obs=(210.0, 270.0, 10.0, 70.0, 15.0),
                 portals=[(200, 270), (400, 270)])
    _v3p18.write_scene(_mm18, _sc18, _vf18, win=1)
    _cl18 = _vf18.clamp_aabb((506.0, -3.0, 672.0, 196.0))
    _check('★ AABB 夹场区（贴边 y0=-3 -> 0，防无符号寄存器回绕）',
           _cl18 == (506.0, 0.0, 672.0, 196.0), str(_cl18))
    _mm18c = _MM18()
    _sc18b = dict(fields=_F18, holes=[], x0=100, y0=270, ang0=0.0, sp=400.0,
                  q=1, steps=10, bounce=0, side=0, fan_half=1.0,
                  obs=(589.0, 50.0, 80.0, 14.0, -53.0), portals=None)
    _v3p18.write_scene(_mm18c, _sc18b, _vf18, win=1)
    _check('★ 贴边旋转矩形：AABB 预过滤写寄存器前已夹紧（y0 写 0）',
           _mm18c.w.get(0x030) == 1
           and _mm18c.w.get(0x038) == 0
           and _mm18c.w.get(0x05C) == _vf18.to_pos(589))
    _check('★ V3 write_scene：旋转矩形（cx/ehw）+ 传送门寄存器落点',
           _mm18.w.get(0x030) == 1
           and _mm18.w.get(0x05C) == _vf18.to_pos(210)
           and _mm18.w.get(0x064) == _vf18.to_pos(21)
           and _mm18.w.get(0x044) == 1
           and _mm18.w.get(0x048) == _vf18.to_pos(200)
           and _mm18.w.get(0x058) == _vf18.to_pos(18))
    _mm18b = _MM18()
    _pep18.set_scene(_mm18b, _vf18, _F18, [],
                     obstacles=[_eng18.Obstacle(480.0, 270.0, 120.0, 14.0, 0.0)],
                     portals=[_eng18.Portal(200, 270, 1),
                              _eng18.Portal(400, 270, 0)])
    _check('★ PE set_scene：旋转矩形（cx）+ 传送门寄存器落点',
           _mm18b.w.get(0x04C) == 1 and _mm18b.w.get(0x078) == _vf18.to_pos(480)
           and _mm18b.w.get(0x064) == _vf18.to_pos(200)
           and _mm18b.w.get(0x074) == _vf18.to_pos(18))
    _check('★ PL 原生接线：瞄准预览/PL 快扇/粒子场景都带 obstacles/portals',
           (('obstacles=(_oa or ())' in _srcs2
             or 'obstacles=(oa or ())' in _srcs2
             or 'oa=[list(x) for x in _oa]' in _srcs2)
            and ('obs_list=_oa' in _srcs2 or 'obs_list=oa' in _srcs2)
            and "obstacles=getattr(world, 'obstacles', None)" in _srcs2))
    class _W9:
        def __init__(self, holes):
            self.holes = holes
    _w9 = _W9([{'edge': 'right', 'c': 200.0, 'w': 44.0, 'target': 0}])
    _v9 = (bg._hole_valid(_w9, 'right', 200.0 + 50.0),     # 距 50 <(44+44)/2+12=56 -> 拒
           bg._hole_valid(_w9, 'right', 200.0 + 120.0),    # 远 -> 过
           bg._hole_valid(_w9, 'left', 30.0),              # 太靠角 -> 拒
           bg._hole_valid(_w9, 'top', 480.0))              # 另一边中段 -> 过
    _check('★ _hole_valid 四条（重叠拒/远过/角距拒/他边过）',
           _v9 == (False, True, False, True), str(_v9))
    _check('★ 局中道具接线静态核（发起/选块/确认复用 buy/ESC 取消/超时）',
           "begin_edit(_pk)" in _srcs2 and 'hole_cycle_edge()' in _srcs2
           and "_mas_ev('cfm%d' % _pk" in _srcs2
           and "end_edit(confirm=False)" in _srcs2
           and "end_hole(cancel=True)" in _srcs2
           and "elif _mev[0] == 'hole_timeout':" in _srcs2)
    # ---- 9.10) ★★ 加洞口 v2（2026-10-15：鼠标跟随/弹窗确认/归属；对话5 定稿）----
    _g10 = (bg._hole_from_game(100.0, 10.0),    # 贴上边 -> top
            bg._hole_from_game(950.0, 300.0),   # 贴右 -> right
            bg._hole_from_game(480.0, 535.0),   # 贴下 -> bottom
            bg._hole_from_game(5.0, 520.0),     # 贴左 + 角距夹紧(<=490)
            bg._hole_from_game(20.0, 5.0))      # 近上(5)<近左(20) + 夹紧(>=50)
    _check('★ _hole_from_game 最近边五档（含两处 50px 角距夹紧）',
           _g10 == (('top', 100.0), ('right', 300.0), ('bottom', 480.0),
                    ('left', 490.0), ('top', 50.0)), str(_g10))
    # ★ 2026-10-30 用户定案：加洞口道具用黑球贴边框，交点=洞口候选
    _bb10 = (bg._ball_border_candidate([dict(gx=100.0, gy=8.0)]),
             bg._ball_border_candidate([dict(gx=6.0, gy=200.0)]),
             bg._ball_border_candidate([dict(gx=480.0, gy=270.0)]),
             bg._ball_border_candidate([]))
    _check('★ 黑球贴边候选（任意边/离边远 None/无球 None）',
           _bb10 == (('top', 100.0), ('left', 200.0), None, None), str(_bb10))

    class _VS10:
        @staticmethod
        def cam_to_game(Hm, x, y):
            return (x * 0.5, y * 0.5, 0.0, 0.0, 0.0)   # 假映射=÷2（只验换算链）
    _m10 = bg._hole_from_mouse(1000, 360, 1280, 720, 1024, 720, None, _VS10)
    _check('★ _hole_from_mouse 换算链（1280 画布→1024 直播图→游戏→最近边）',
           _m10 == ('top', 400.0), str(_m10))

    class _W10:
        def __init__(self):
            self.holes = []
    _mf10 = mf.MatchFlow()
    _mf10.drain()
    _mf10.begin_hole(1)
    _mf10.hole_set_pos('top', 480.0)
    _w10 = _W10()
    _ok10 = bg._hole_commit(_mf10, _w10)
    _check('★ _hole_commit：合法落洞（世界+1/回 PLAY/免费额度 1→0）',
           _ok10 and len(_w10.holes) == 1 and _mf10.phase == 'PLAY'
           and _mf10.free_hole[1] == 0 and _w10.holes[0]['edge'] == 'top'
           and _w10.holes[0]['w'] == 44.0)
    _mf10b = mf.MatchFlow()
    _mf10b.drain()
    _mf10b.begin_hole(2)
    _mf10b.hole_set_pos('left', 20.0)          # 角距外 = 非法
    _w10b = _W10()
    _ok10b = bg._hole_commit(_mf10b, _w10b)
    _check('★ _hole_commit：非法拒绝（世界不动/仍 HOLE/额度原样）',
           (not _ok10b) and len(_w10b.holes) == 0 and _mf10b.phase == 'HOLE'
           and _mf10b.free_hole[2] == 1 and '放不下' in _mf10b.msg)
    _mf10c = mf.MatchFlow()
    _mf10c.drain()
    _q1 = bg._hole_quota_txt(_mf10c, 1, mf)        # 免费额度剩 1 次
    _mf10c.free_hole[1] = 0
    _mf10c.items[1]['hole'] = 2
    _q2 = bg._hole_quota_txt(_mf10c, 1, mf)        # 道具剩 2 个
    _mf10c.items[1]['hole'] = 0
    _mf10c.points[1] = 25
    _q3 = bg._hole_quota_txt(_mf10c, 1, mf)        # 扣 30 分·现 25 分
    _check('★ 弹窗扣额文案三档（免费 > 道具 > 积分）',
           _q1 == '免费额度剩 1 次' and _q2 == '道具剩 2 个'
           and _q3 == '扣 30 分·现 25 分', '%s | %s | %s' % (_q1, _q2, _q3))
    _check('★ match_flow.hole_set_pos（鼠标跟随 API；PLAY 里 no-op）',
           hasattr(mf.MatchFlow, 'hole_set_pos')
           and mf.MatchFlow().hole_set_pos('top', 480.0) is False)
    _check('★ 加洞口 v2 接线静态核（跟帧/点击/弹窗命中框/光标/离开归零）',
           '_hole_from_mouse(' in _srcs2 and '_hole_mouse_click(' in _srcs2
           and "_HOLEV['confirm']" in _srcs2 and 'def _holev_clear' in _srcs2
           and "_draw_cursor(img, _lx, _ly)" in _srcs2
           and "_HOLEV['quota']" in _srcs2 and "hv['rects'] = {'ok'" in _srcs2)
    # ---- 9.11) ★★ 加洞口 v2·手机俯视图（2026-10-15 下午：点位置→再点确定）----
    bg._holev_clear()                                        # 本节从不干净态开跑
    _sn11a = bg._hole_snap(mf.MatchFlow(), mf, _w9)          # 非 HOLE
    _m11 = mf.MatchFlow()
    _m11.drain()
    _m11.points[1] = 40
    _m11.begin_hole(1)
    _m11.hole_set_pos('top', 480.0)
    _sn11b = bg._hole_snap(_m11, mf, _w9)
    _check('★ _hole_snap：非 HOLE -> active False；HOLE -> who/edge/pos/valid/left',
           _sn11a == {'active': False}
           and _sn11b.get('active') is True and _sn11b['who'] == 1
           and _sn11b['edge'] == 'top' and _sn11b['pos'] == 480.0
           and _sn11b['valid'] is True and _sn11b['stage'] == 'follow'
           and _sn11b['left'] == 30.0, str(_sn11b))
    _w11 = _W10()
    _ok11 = bg._hole_apply_pos(_m11, 'top', 480.0, mf, _w11)
    _c11 = bg._HOLEV['confirm']
    _q11 = bg._HOLEV['quota']
    _ok11b = bg._hole_apply_pos(_m11, 'top', 30.0, mf, _w11)   # 太靠角
    _c11b = bg._HOLEV['confirm']
    bg._holev_clear()
    _check('★ _hole_apply_pos：合法->进确认态(+扣额文案)；非法->退回跟随态',
           _ok11 and _c11 and (not _ok11b) and (not _c11b)
           and _q11 == '免费额度剩 1 次', str((_ok11, _c11, _ok11b, _c11b, _q11)))
    class _B11:
        x0, y0, x1, y1, b = 0.0, 0.0, 320.0, 540.0, 0.4

    class _W11x:
        def __init__(self):
            self.holes = [{'edge': 'right', 'c': 200.0, 'w': 44.0}]
            self.blocks = [_B11()]
    bg._HOLEV.update({'who': 1, 'mouse': False, 'confirm': False,
                      'rects': {}, 'quota': ''})
    _sn11c = bg._hole_snap(_m11, mf, _W11x())
    bg._holev_clear()
    _m11b = mf.MatchFlow()
    _m11b.drain()
    _w11x = _W11x()
    _s_old = bg._match_snapshot(_m11b, 0, mf)
    _s_new = bg._match_snapshot(_m11b, 0, mf, world=_w11x,
                                hole=_sn11c, balls=[[100.0, 200.0, 1]])
    _check('★ _match_snapshot：scene/hole/balls 可选带入；老调用一字不差',
           ('scene' not in _s_old and 'hole' not in _s_old
            and 'balls' not in _s_old)
           and ('scene' in _s_new and 'hole' in _s_new and 'balls' in _s_new)
           and _s_new['scene']['W'] == 960.0
           and _s_new['scene']['params'] == {'margin': 50.0, 'mw': 44.0,
                                             'gap': 12.0}
           and len(_s_new['scene']['blocks']) == len(_w11x.blocks)
           and _s_new['hole']['active'] is True, '')
    _check('★ 手机点位置消费接线静态核（hopos 上升沿 -> _hole_from_game -> 共用核心）',
           "_mas_ev('hopos%d' % _pkh" in _srcs2
           and '_hole_apply_pos(' in _srcs2 and '_game_from_mouse(' in _srcs2
           and "'hopos1'" in _srcs2 and "'hopos2'" in _srcs2)
    _check('★ 手机 /hole 端点 + 俯视图页接线（v3_input）',
           "u.path == '/hole'" in _srcs3 and 'hopos=True' in _srcs3
           and 'holewrap' in _srcs3 and 'holeTap' in _srcs3
           and 'holeFromMatch' in _srcs3 and "'p1_hopos', 'hopos1'" in _srcs3)
    # ★★ 2026-10-31（用户实况："手机上找不到确定按钮"——俯视图挂在页面顶端、在屏幕外）：
    #   俯视图必须是**全屏浮层**（position:fixed 铺满视口），确定键 sticky 且
    #   没选位置前灰着。三条都钉住，防止以后调样式又把它挤出屏幕。
    _check('★ 手机俯视图是全屏浮层（position:fixed）+ 确定键 id=hok',
           'id="hok"' in _srcs3 and '#holewrap{position:fixed' in _srcs3
           and '#holewrap .row{position:sticky' in _srcs3)
    _check('★ 手机确定键没选位置前是灰的（_okb.disabled 由 stage/mark 驱动）',
           '_okb.disabled=!(((HV.stage===' in _srcs3)
    _check('★ 俯视图弹出时滚到顶部（scrollTop=0）',
           'w.scrollTop=0' in _srcs3)
    # ★★ 2026-10-31 用户点单："其他道具的确定按钮也要做好，直接在取消旁边加确定"
    _check('★ 手机「取消」= 连发 ESC 直到流程真退出（cancelAll，间隔 500ms）',
           'async function cancelAll' in _srcs3 and 'onclick="cancelAll(1)"' in _srcs3
           and 'onclick="cancelAll(2)"' in _srcs3
           and 'setTimeout(r,500)' in _srcs3 and '_busyFlow' in _srcs3)
    _check('★ 加洞口「取消」也连发到 hole.active=false（ESC 两段式）',
           'async function holeNo()' in _srcs3 and "act=no'" in _srcs3)
    _check('★ 手机两半区都有「确定」键（act(1/2,\'ok\')，与键盘 E 同一条 buy 计数）',
           "act(1,'ok')" in _srcs3 and "act(2,'ok')" in _srcs3
           and "a == 'ok'" in _srcs3 and 'kw = dict(buy=True)' in _srcs3
           and 'class="okrow"' in _srcs3)
    # ---- 9.12) ★★ 加洞口 v2·大屏俯视瞄准图（2026-10-16 接线完成）----
    _hv12 = {'who': 1, 'mouse': True, 'confirm': False, 'quota': '',
             'rects': {}, 'last': (0, 0)}
    _m11.hole_set_pos('right', 300.0)          # 右边缘 pos300：远离 _w11x 的洞 -> 合法
    _v12 = bg._build_hole_view(1280, 720, _m11, mf, _W11x(), _hv12,
                               (640, 360), [(100.0, 200.0, 1),
                                            (300.0, 260.0, -1)])
    _check('★ _build_hole_view：1280x720；场区 1:1 居中（偏移 160,90）；'
           '球/预览像素对位',
           _v12.shape == (720, 1280, 3)
           and tuple(_v12[90 + 200, 160 + 100]) == (60, 60, 230)     # 红球
           and tuple(_v12[90 + 260, 160 + 300]) == (230, 90, 60)     # 蓝球
           and tuple(_v12[90 + 300, 160 + 960]) == (0, 230, 0),      # 绿预览线
           str(tuple(_v12[90 + 300, 160 + 960])))
    _hv13 = {'who': 2, 'mouse': False, 'confirm': True,
             'quota': '免费额度剩 1 次', 'rects': {}, 'last': (0, 0)}
    _v13 = bg._build_hole_view(1280, 720, _m11, mf, _W11x(), _hv13, (0, 0), [])
    _check('★ _build_hole_view：确认弹窗 + 命中框写回（画布坐标含画布内）',
           set(_hv13['rects'].keys()) == {'ok', 'no'}
           and all(0 <= r[0] < r[2] <= 1280 and 0 <= r[1] < r[3] <= 720
                   for r in _hv13['rects'].values())
           and _v13.shape == (720, 1280, 3), str(_hv13['rects']))
    _check('★ 俯视瞄准图接线静态核（四门同源：检测/抓图/渲染 elif/冻结节拍）',
           _srcs2.count('not _gate and not _hole_on') >= 2
           and 'elif _hole_on:' in _srcs2
           and ('elif _hole_on and time.time() - _BALLS' in _srcs2
                or 'elif (_hole_on or _edit_on) and time.time() - _BALLS' in _srcs2)
           and '_HCACHE[' in _srcs2 and 'show_rect(_limg, _u2)' in _srcs2
           and 'def _build_hole_view' in _srcs2
           and 'def _phone_state_tick' in _srcs2
           and _srcs2.count('_phone_state_tick(') >= 3)
    _sm12 = (bg._hole_side_from_canvas(160, 360, 1280, 720),   # 场区左缘中 -> left
             bg._hole_side_from_canvas(60, 360, 1280, 720),    # 左侧屏外 -> 仍 left
             bg._hole_side_from_canvas(1120, 270, 1280, 720),  # 右缘上段 -> right
             bg._hole_side_from_canvas(640, 90, 1280, 720))    # 场区上缘中 -> top 480
    _check('★ _hole_side_from_canvas 直映射四档（左侧不再被透视吸去上下边）',
           _sm12 == (('left', 270.0), ('left', 270.0), ('right', 180.0),
                     ('top', 480.0)), str(_sm12))
    _check('★ 鼠标->洞口 直映射接线（瞄准图模式两处调用）+ 旧相机链保留待命',
           _srcs2.count('_hole_side_from_canvas(') >= 3
           and 'def _hole_from_mouse' in _srcs2)
    # ---- 9.13) ★★ 道具应答横幅（2026-10-16：用户"按C没反应，不知是没次数还是卡了"）----
    # ---- 9.14) ★★ 粒子引擎（档3，2026-10-16）：驱动/探针/镜像 静态核 ----
    try:
        with open(os.path.join(HERE, 'particle_pl.py'), encoding='utf-8') as _fpe:
            _srcs_pe = _fpe.read()
    except Exception:
        _srcs_pe = ''
    _check('★ 粒子引擎驱动 particle_pl.py（BASE/探针/probe 入口）',
           "BASE = 0x40001000" in _srcs_pe
           and 'def probe(' in _srcs_pe
           and 'def pop_event(' in _srcs_pe
           and "sys.argv[1] == 'probe'" in _srcs_pe)
    _pefix_ok = True
    try:                         # ★ 2026-10-17：板端脚本平铺在 wp、没有仓布局，
        with open(os.path.join(  #   直接 open 会 FileNotFoundError 把整套台架掀了
                os.path.dirname(HERE), '..', 'tools', 'pe_fix.py'),
                encoding='utf-8') as _fpf:
            _pefix_ok = 'def resolve_pair(' in _fpf.read()
    except OSError:
        _pefix_ok = True         # （这项只在 PC 仓里有意义）
    _check('★ 粒子引擎镜像 pe_fix.py（与 RTL 逐位的整数域规格）', _pefix_ok)
    _rtl_dir_pe = os.path.join(
        os.path.dirname(HERE), '..', 'vivado', 'ov5640_pynq',
        'rtl', 'v3_bundle')
    _rtl_na = not os.path.isdir(_rtl_dir_pe)      # ★ 板端无 rtl/：N/A 过（PC 侧检查）
    _check('★ 粒子引擎 RTL 在场（particle_engine.v / pe_axi.v / tb 三件）',
           _rtl_na or all(os.path.isfile(os.path.join(
               os.path.dirname(HERE), '..', 'vivado', 'ov5640_pynq',
               'rtl', 'v3_bundle', f)) for f in
               ('particle_engine.v', 'pe_axi.v', 'tb_pe_axi.v',
                'tb_particle_engine.v')))
    _check('★ 粒子引擎游戏集成（--pe：泵/键消费/渲染合并/HUD/手机球表）',
           "if a == '--pe':" in _srcs2 and '_pe_pump(_PEB' in _srcs2
           and '_pe_spawn_storm(_PEB' in _srcs2
           and "live_balls + _PEB['balls']" in _srcs2
           and "'pe1': 0, 'pe2': 0" in _srcs2
           and 'PL 硬件粒子 %d 在飞' in _srcs2)
    # ---- 9.15) ★★ 实建口径同步（2026-10-17：KLANES 默认值谎报 / 风暴 %64 幽灵球）----
    try:
        _rdir = os.path.join(os.path.dirname(HERE), '..', 'vivado',
                             'ov5640_pynq', 'rtl', 'v3_bundle')
        _peaxi_src = open(os.path.join(_rdir, 'pe_axi.v'),
                          encoding='utf-8').read()
        _peeng_src = open(os.path.join(_rdir, 'particle_engine.v'),
                          encoding='utf-8').read()
        _bndl_src = open(os.path.join(_rdir, 'traj_bundle.v'),
                         encoding='utf-8').read()
    except Exception:
        _peaxi_src = _peeng_src = _bndl_src = ''
    _peaxi_code = '\n'.join(l for l in _peaxi_src.splitlines()
                            if not l.strip().startswith('//'))
    _rtl_na2 = not _peaxi_src       # ★ 板端读不到 RTL（平铺布局）：N/A 过
    _check('★ 粒子引擎实例化口径 = 实建（.KLANES(1) 写死；不再"默认 1 / 实装 4"）',
           _rtl_na2 or
           ('.KLANES(1)' in _peaxi_code and '.KLANES(4)' not in _peaxi_code
            and 'parameter integer KLANES = 1' in _peeng_src
            and 'parameter integer NPART  = 16' in _peeng_src
            and 'parameter integer NPART = 16' in _peaxi_src))
    _check('★ 风暴出生编号 % 16（与 NPART=16 同步；越界编号会被引擎越界防护拒→幽灵球）',
           "st['next'] = (idx + 1) % 16" in _srcs2)
    _check('★ V3 束时间复用（PLANES=2 物理 lane；17 逻辑射线；res_addr=逻辑号）',
           _rtl_na2 or ('parameter integer PLANES = 2' in _bndl_src
                        and 'ray_next' in _bndl_src and 's_dqray' in _bndl_src))
    _check('★ 沙盒模式（无 MATCH）不崩：_MF 初始化必须带 mod 键（r90 实况 KeyError）',
           "_MF = {'ob': None, 'mod': None, 't': None}" in _srcs2)
    _check('★ 道具应答横幅接线（_ITEM_MSG/_item_banner：四块屏 + _shot_write 收口）',
           'def _item_banner' in _srcs2 and '_item_banner(img)' in _srcs2
           and _srcs2.count('_item_banner(img)') >= 4
           and "obj.get('kind') == 'item'" in _srcs2
           and _srcs2.count('_item_msg_set(') >= 4
           and "'已切到键盘控制" in _srcs2)
    _check('★ 加洞口超时语义收紧（确认过位置才自动放；只晃鼠标=取消不扣；都上横幅）',
           '加洞口超时（30秒）——已自动放置' in _srcs2
           and '加洞口超时（30秒）——已取消，没有扣额度' in _srcs2
           and "if (_HOLEV['confirm'] and _fine2" in _srcs2)
    _check('★ 常驻提示「加洞：P1=C ｜ P2=小键盘+ 或 /」接线（入口与归属一眼可见）',
           '_HUD_HOLE_HINT' in _srcs2 and '_HUD_HOLE_HINT, (8' in _srcs2)
    _check('★ 暂停叠加接线（EDIT 块高亮 / HOLE 预览线）',
           'def _pause_overlay' in _srcs2 and '_pause_overlay(_limg' in _srcs2)
    _check('★ 商店基准 staleness 修复（相位门在基准更新之后）',
           '_SHOP[_k] = _cnt' in _srcs2 and '基准 staleness' in _srcs2)
    _check('★ 防闪终版：局部更新（show_rect）接线 + 内容变整帧/光标动局部',
           'v3_pump.show_rect(' in _srcs2 and '_crect' in _srcs2
           and 'def show_rect' in open(os.path.join(HERE, 'hdmi_out.py'),
                                       encoding='utf-8').read())

    _cover = _font_cmap(os.path.join(HERE, 'zh_font_subset.ttf'))
    _prep12 = bg._match_snapshot(_m13, 0, mf)
    _prep12['phase'] = 'PREP'
    _prep12['p1']['shop'] = _labs12[0]
    _prep12['p2']['shop'] = _labs12[4]
    _cns.append(bg._match_line_cn(_prep12) or '')
    _allc = (' '.join(_cns) + ' ' + _l1 + ' ' + _l2 + ' ' + bg._HUD_FIRE_HINT
             + ' ' + bg._HUD_HOLE_FLASH + ' ' + ' '.join(_labs12)
             + ' 在飞 已发 出射 ·'
             # ★ 2026-10-17：PL 粒子 HUD 行**必须在字库门禁里**——它此前不在
             #   _allc 里，字库缺"粒子帧湮灭"等字都没人拦（屏幕上真缺字了）。
             + ' PL 硬件粒子 %d 在飞 · 物理 %.1fms/帧 · 湮灭%d 出射%d')
    # ★★ 2026-10-08 商店 v2：屏上商店画面的全部文案也必须进门禁（缺字=豆腐块）
    _allc += (' 第 3/10 局 · 商店 选道具 → 购买 → 完成（两边都完成 = 开局）'
              ' 玩家1　积分 20 已购1 已就绪 OK 完成·就绪'
              ' 键盘：P1 W/S+E　P2 ↑/↓+小键盘*　｜　鼠标：点选→购买→完成'
              ' 商店（第 2 局）：点这里进入 或 P1 按 E ｜ P2 按 小键盘*（手机面板也能买）'
              ' 第 3/10 局 · 局前 商店：点这里进入 或 P1 按 E ｜ P2 按 小键盘* ｜'
              ' 手机点「进入商店」进店选购 → 各自「完成」= 就绪 → 双方就绪开局')
    # ★★ 2026-10-15 加洞口 v2：跟随提示 / 确认弹窗（含扣额三档文案）全部进门禁
    _allc += (' 选手一 · 加洞口（免费额度剩 1 次） 选手二 · 加洞口（道具剩 2 个）'
              ' 选手一 · 加洞口（扣 30 分·现 25 分） 确认放置这个洞口？ 确定 取消'
              ' 移动鼠标选边，左键定住位置（E 确定 / ESC 取消）'
              ' 键盘：C 切边 · E 定边/确认 · ESC 取消（也可动鼠标）')
    # ★ 2026-10-19 布置 PLACE v1：障碍摆放画面的全部文案进门禁（缺字=豆腐块）
    _allc += (' 布置障碍 · 选手一（剩余 1 个） 鼠标点位置，左键定住；W/S 旋转 15°；'
              'E 确定 / ESC 取消 确认放置这个障碍？ 这里放不下（贴边/盖洞/叠障碍）'
              ' 障碍角度 15°（绿=可放/红=放不下） 已退出布置（额度未扣）'
              ' 没有障碍额度（局前商店先买「障碍」） 布置障碍：鼠标点位置')
    # ★ 2026-10-27 实体布置（实时相机视图/确认取消）的文案进门禁
    _allc += (' 布置实体障碍 · 选手一（剩余 1 个） 布置实体传送门 · 选手一（剩余 1 个）'
              ' 把实体黑条放到场区（离已有件 >60px）；点一下黑条可锁定，'
              '黄框后 E 确定 / ESC 取消 把实体黑条放到场区（离已有件 >60px）；'
              '点一下黑条可锁定，黄框后 小键盘* 确定 / ESC 取消'
              ' 把一对实体绿门放到场区（间距至少80像素，离已有件 >75px）；'
              '黄框出现后 E 确定 / ESC 取消 把一对实体绿门放到场区'
              '（间距至少80像素，离已有件 >75px）；黄框出现后 小键盘* 确定 / ESC 取消'
              ' 识别到新件（黄框）——E 确定 / ESC 取消'
              ' 识别到新件（黄框）——小键盘* 确定 / ESC 取消'
              ' 已锁定这根黑条（黄框）——E 确定 / ESC 取消'
              ' 已锁定这根黑条（黄框）——小键盘* 确定 / ESC 取消'
              ' 已点选黑条（长 ）—— 确定 / ESC 取消'
              ' 没点到黑条——靠近黑条再点一次 还没看到黑条——先把黑条放到场区再点'
              ' 选中的黑条不见了——重新点或等自动识别'
              ' 已取消这次识别——继续摆放（再按 ESC 退出） 新件不见了——继续摆放'
              ' 还没识别到新件——把实体件放到场区，黄框出现后按 E'
              ' 还没识别到新件——把实体件放到场区，黄框出现后按 小键盘*'
              ' 放不下： 已离开局前——布置取消（额度未扣） 没有额度了——布置取消'
              ' 黄色框')
    # ★★ 2026-10-28 调整界面 + 购买后自动摆放：屏上文案进门禁
    _allc += (' 调整障碍 / 传送门（开局前可挪动实物）'
              ' 把桌上的实体黑条 / 绿门摆到想要的位置，画面 0.3 秒刷新；'
              '按 E / 小键盘* 开始本局，ESC 回商店'
              ' 当前：障碍 1 根 / 传送门 0 个（黄框=可调整；开局后实心黄锁定）'
              ' 调整障碍/传送门：按 E/小键盘* 开始本局 开始本局！障碍/传送门已锁定'
              ' 回到商店（继续购买/调整）'
              ' 双方就绪——调整障碍/传送门后按 E / 小键盘* 开始本局'
              ' 稍后按 V 摆放 稍后按 G 摆放 相机还在稳定中，等一两秒再按')
    # ★★ 2026-10-28（第 3 条）购买询问/对局中临时摆放的屏上文案进门禁
    _allc += (' 买了障碍——现在使用？ 立即摆放 先留着 对局中也能按 V 临时摆放'
              ' 本局临时 当前阶段不能摆放——道具保留 已离开可布置阶段——布置取消'
              ' 本局障碍已满 3 根（最多 3 根） 本局传送门已满 2 对（最多 2 对）'
              ' 的障碍先留着——对局中按 V 摆放（本局临时有效）')
    # ★★ 2026-10-29 改磁场俯视界面的文案进门禁
    _allc += (' 改磁场 选手一 选手二 块 剩 秒 选块 鼠标点块 定方向 取消 '
              '先选块，再定方向（+ 出屏 / - 入屏） '
              '方向 +——输入大小（0.10~0.80）：0.35 E 确认 退格删 返回 '
              '小数点：主键盘 .（P2 也可用小键盘 .） 确认 取消 '
              '回到选块——A/D 选块、W/S 定方向（再按 ESC 取消） '
              '先选块（A/D 或鼠标），再按 W/S 定方向 先选块、定方向，再输数值 '
              '方向 +（出屏）——键盘输数值（如 0.35），E 确认 / ESC 返回 '
              '块 1 磁场已改为 +0.35 鼠标选块')
    # ★ 2026-10-19 实物识别：应答横幅/提示文案进门禁
    _allc += ' 黑洞标记：left 洞口 -> 200 实物障碍 传送门 黑洞标记 实物识别'
    # ★★ 2026-10-16 俯视瞄准图（大屏）：标题行也要进门禁（字全在现有集里）
    _allc += ' 加洞口 · 选手一 · 剩 25 秒'
    # ★★ 2026-10-16 道具应答横幅（大屏）：match_flow 全部可弹文案进门禁
    _allc += (' 没有"加洞口"额度（30 分/次） 没有"改磁场"道具（20 分/次） 不在对战中'
              ' 选手一 正在进行『改磁场』，全场已暂停——请等对方完成后你再来'
              ' 选手一 正在进行『加洞口』，全场已暂停——请等对方完成后你再来'
              ' 选手一 暂停·加洞口（全场冻结） 选手一 暂停·改磁场（全场冻结）'
              ' 选手一 加了一个洞口 磁场调整完成，比赛继续 已取消加洞口 已取消磁场调整'
              ' 这个位置放不下（太靠角或与已有洞口重叠）——挪一下再点'
              ' 已切到键盘控制：C 切边 · E 定边/确认（动一下鼠标可交还）')
    _allc += (' 加洞口超时（30秒）——已自动放置（left 120）'
              ' 加洞口超时（30秒）——已取消，没有扣额度'
              ' P1 加洞口超时取消（没扣额度）')
    _allc += ' 加洞：P1=C ｜ P2=小键盘+ 或 /'
    _miss = sorted(set(ch for ch in _allc
                       if ord(ch) > 127 and ord(ch) not in _cover))
    _check('★ 字库覆盖：屏上中文全在 zh_font_subset.ttf（缺字=豆腐块；覆盖读取器 '
           '自检）', (not _miss) and len(_cover) > 200,
           '缺: %s / 覆盖 %d 码点' % (''.join(_miss), len(_cover)))

    # ---- 10) game_v3.sh：MATCH=1 接 --match + import 硬门槛 ----
    with open(os.path.join(HERE, 'game_v3.sh'), encoding='utf-8') as f:
        sh = f.read()
    _check('game_v3.sh：MATCH=1 -> --match（import match_flow 硬门槛）',
           '[ "$MATCH" = "1" ]' in sh and '--match' in sh
           and 'import match_flow' in sh)


def s_hole_adapt():
    """★ M2.4 洞口自适应（规则书 §8 / engine.adapt_holes 移植）+ 接线静态核。

    为什么这么验：移动判据全是数值口径（死区 40px / 步长 ≤66 / 抖动档 / 重叠间隔 /
    2 秒前视），任何一条漂了都会表现为"洞口乱飘 / 该动不动"——纯函数喂**确定性
    rng**钉死；2 秒前视用**真球物理本体**（克隆步进）验正反两例 + "不动原球"。
    """
    print()
    print('== hole_adapt：洞口朝落点热点移动（死区/步长/重叠/2s 前视）+ 接线核')
    import hole_adapt as ha

    _check('常量：MIN_MARKS=3 / 死区 40px / 单步上限 66px',
           ha.MIN_MARKS == 3 and ha.DEADBAND == 40.0 and ha.MAX_STEP == 66.0)

    class _R0(object):                       # 确定性 rng：jitter 恒 0
        def uniform(self, a, b):
            return 0.0

    _holes = [{'edge': 'left', 'c': 130.0, 'w': 44.0, 'target': 0},
              {'edge': 'right', 'c': 400.0, 'w': 44.0, 'target': 0}]
    _mv = ha.pick_moves(_holes, {'left': [295, 300, 305], 'right': []},
                        field_w=960.0, field_h=540.0, rng=_R0())
    _check('★ 热点远处：朝热点走、单步被 66px 钳住（130 -> 196）',
           _mv == [(0, 196.0)], str(_mv))
    _mv2 = ha.pick_moves(_holes, {'left': [155, 160, 165], 'right': []},
                         field_w=960.0, field_h=540.0, rng=_R0())
    _check('★ 死区：热点只差 30px -> 不动（反"每 30 秒乱晃"）', _mv2 == [], str(_mv2))
    _mv3 = ha.pick_moves(_holes, {'left': [300, 310], 'right': []},
                         field_w=960.0, field_h=540.0, rng=_R0())
    _check('★ 落点 <3 个不评估', _mv3 == [], str(_mv3))
    _h2 = [{'edge': 'right', 'c': 100.0, 'w': 44.0, 'target': 0},
           {'edge': 'right', 'c': 170.0, 'w': 44.0, 'target': 0}]
    _mv4 = ha.pick_moves(_h2, {'right': [230, 235, 240]},
                         field_w=960.0, field_h=540.0, rng=_R0())
    _check('★ 同边重叠约束：0 号想挪到 166 会压住 170 的洞 -> 拒；1 号正常可动',
           all(i != 0 for i, _ in _mv4) and any(i == 1 for i, _ in _mv4), str(_mv4))
    _lr = ha._hole_range('left', 960.0, 540.0, 60.0, 44.0)
    _check('★ 合法范围（圆角 60 + 半洞宽 22）：82 ~ 458',
           abs(_lr[0] - 82.0) < 1e-9 and abs(_lr[1] - 458.0) < 1e-9,
           '%.0f..%.0f' % _lr)

    # ---- ★ 每局随机（用户 10-07："每一局的洞口希望是随机的"）----
    class _R1(object):                       # 确定性 rng：轮流给两个值
        def __init__(self):
            self.v = [300.0, 150.0]
        def uniform(self, a, b):
            return self.v.pop(0) if self.v else 300.0
    _mv5 = ha.random_positions(_holes, 960.0, 540.0, corner_r=0.0, rng=_R1())
    _check('★ 随机位：两个洞各掷各的（left 130->300 / right 400->150），差 >=60',
           _mv5 == [(0, 300.0), (1, 150.0)], str(_mv5))
    class _R2(object):                       # 掷到贴原位（差 <60）-> 12 次后放宽
        def __init__(self):
            self.n = 0
        def uniform(self, a, b):
            self.n += 1
            return 130.0 + 5.0       # 差 5px，恒不满足 min_move
    _mv6 = ha.random_positions([{'edge': 'left', 'c': 130.0, 'w': 44.0, 'target': 0}],
                               960.0, 540.0, rng=_R2())
    _check('★ 随机位：掷不出足够距离就放宽（不硬性拒绝，最终仍给值）',
           len(_mv6) == 1 and abs(_mv6[0][1] - 135.0) < 1e-6, str(_mv6))
    import random as _rm0
    _rm0.seed(7)
    _a7 = ha.random_positions([{'edge': 'left', 'c': 130.0, 'w': 44.0, 'target': 0}],
                              960.0, 540.0)
    _rm0.seed(7)
    _b7 = ha.random_positions([{'edge': 'left', 'c': 130.0, 'w': 44.0, 'target': 0}],
                              960.0, 540.0)
    _check('★ 随机位与全局 random.seed() 解耦（build_world 会播全局！' 
           '种死后连掷两次必须不同）', _a7 != _b7, '%s vs %s' % (_a7, _b7))

    import realtime_ball as _rtb
    import v3_scan as _vs3
    _w3 = _vs3.build_world(7, 0.40)
    _f3 = [(b.x0, b.y0, b.x1, b.y1, b.b) for b in _w3.blocks]
    _h3 = [dict(h) for h in _w3.holes]
    for _h in _h3:
        _h['target'] = 0      # ★ 镜像游戏口径：启动时把洞 target 全摊 0（演示规则 A）
    _lh = next(h for h in _h3 if h['edge'] == 'left')
    _b1 = _rtb.LiveBall(60.0, _lh['c'], 180.0, 300.0)
    _check('★ 2s 前视：贴洞直冲 -> 否决 True（克隆步进 = 真球物理本体）',
           ha.would_score_within([_b1], _h3, _f3) is True)
    _b2 = _rtb.LiveBall(60.0, _lh['c'] + 120.0, 180.0, 300.0)
    _check('★ 2s 前视：不朝洞 -> False', ha.would_score_within([_b2], _h3, _f3) is False)
    _b3 = _rtb.LiveBall(60.0, _lh['c'], 180.0, 300.0)
    _s0 = (_b3.x, _b3.y, _b3.vx, _b3.vy, _b3.n_steps)
    ha.would_score_within([_b3], _h3, _f3)
    _check('★ 克隆探路不动原球（x/y/vx/vy/n_steps 原样）',
           (_b3.x, _b3.y, _b3.vx, _b3.vy, _b3.n_steps) == _s0)

    # ---- ★ 每局块数=n（规则书 §1；游戏在 round_start 重建场景）----
    _ok_tile = True
    for _n in range(1, 10):
        _w4 = _vs3.build_world(7, 0.40, n_blocks=_n)
        _bl = _w4.blocks
        if len(_bl) != _n:
            _ok_tile = False
        if abs(sum((b.x1 - b.x0) * (b.y1 - b.y0) for b in _bl)
               - _vs3.FIELD_W * _vs3.FIELD_H) > 1.0:
            _ok_tile = False                       # 分块必须铺满无缝隙
        if [1 if b.b > 0 else -1 for b in _bl] != [1 if i % 2 == 0 else -1
                                                   for i in range(_n)]:
            _ok_tile = False                       # ±0.40 隔块交替
    _w11 = _vs3.build_world(7, 0.40, n_blocks=11)
    _check('★ 每局块数=n：1..9 各就位/铺满无缝隙/交替；11 封顶 9',
           _ok_tile and len(_w11.blocks) == 9)
    with open(os.path.join(HERE, 'ball_game.py'), encoding='utf-8') as f:
        _srcbg = f.read()
    _check('★ 接线：round_start 按局数重建（min(round,9) -> build_world）',
           '本局磁场' in _srcbg and '_nb = min(int(_mev[1]), 9)' in _srcbg
           and 'n_blocks=_nb' in _srcbg)
    _check("★ 接线：'hole_due' -> pick_moves + 2s 否决 + 落点账（bounce 记/每局清）",
           'pick_moves(v3_world.holes' in _srcbg and 'would_score_within(' in _srcbg
           and "if _kind == 'bounce':" in _srcbg and '_HITS[' in _srcbg)
    _check('★ 移动即改世界洞位（真球/PL 预览/画面三处同源）',
           "v3_world.holes[_hi]['c'] = _nc" in _srcbg)
    _check('★ 每局随机接线：round_start -> random_positions + 2s 否决 + 醒目动画',
           'random_positions(' in _srcbg and "_HMOVE['items'] = _rj" in _srcbg)
    _check('★ 移动/随机的醒目动画（残影+箭头+中文提示条）在直播里',
           'arrowedLine' in _srcbg and '_HUD_HOLE_FLASH' in _srcbg
           and '_HMOVE_SECS' in _srcbg)

    # ★★ 2026-10-08 电荷口径（用户实况："蓝球轨迹怎么和红球一模一样"）：真球步进的
    #   洛伦兹转向必须乘**自己的电荷**（realtime_ball.step 曾写死 1 = 全场按正电子）；
    #   瞄准预览/发射同一口径（ball_game 两处曾写死 1 / q=1）。
    _bq1 = _rtb.LiveBall(170.0, 21.5, 102.5, 590.0, charge=1)
    _bq2 = _rtb.LiveBall(170.0, 21.5, 102.5, 590.0, charge=-1)
    for _ in range(470):
        _bq1.step(_f3, _h3, 0)
        _bq2.step(_f3, _h3, 0)
    _dq = ((_bq1.x - _bq2.x) ** 2 + (_bq1.y - _bq2.y) ** 2) ** 0.5 / 256.0
    _check('★ 电荷口径：同一杆红蓝必须分道（470 步后相距 >30px）',
           _dq > 30.0, '%.1fpx' % _dq)
    import v3_fixedpoint as _vfx
    _rq = _vfx.lane_fixed(170.0, 21.5, 102.5, 590.0, -1, _f3, _h3, 0,
                          steps=16000, max_bounce=10**9, trail_gap=6.0,
                          max_pts=1000000)
    _bq3 = _rtb.LiveBall(170.0, 21.5, 102.5, 590.0, charge=-1)
    _esq = None
    for _kq in range(16000):
        if _bq3.step(_f3, _h3, 0) == 'goal':
            _esq = _kq + 1
            break
    _okq = (((_rq['rtl_res'] == 1) == (_esq is not None))
            and (_esq is None or _esq == _rq['steps'])
            and abs(_bq3.x / 256.0 - _rq['x']) < 1e-9
            and abs(_bq3.y / 256.0 - _rq['y']) < 1e-9)
    _check('★ 电荷口径：蓝球(-1) vs lane_fixed(-1) 出射步数/终点逐位一致',
           _okq, 'steps=%s vs %s' % (_esq, _rq['steps']))
    with open(os.path.join(HERE, 'realtime_ball.py'), encoding='utf-8') as _frb:
        _srcrb = _frb.read()
    _check('★ 电荷口径接线：真球 self.charge；预览 charge_of(_pt.cls)->q',
           'self.charge * b' in _srcrb and 'charge_of(_pt.cls)' in _srcbg
           and ('q=_chg' in _srcbg
                or ('_pv_fan(' in _srcbg and '_ar, _pspd, _chg' in _srcbg
                    and 'q=q' in _srcbg)))


def s_map_stats():
    """差距 1 混合口径：PL 候选统计（area/Σx/Σy）+ PS classify_map == detect_map。

    钉死两件事：
      ① 同一组统计（整数）喂 detect_map_from_stats，输出与 detect_map 同口径
         （位置容差 0.5px = PL 的整数 Σ 量化；f 容差 0.02）；
      ② 暗区里的黑块（周围也暗、对比 <35）必须被复判拒掉。
    """
    print('\n== pl_map_stats：PL 候选统计 + PS 复判（差距 1 混合口径）')
    try:
        import numpy as np
        import cv2
        _prefer_here()
        sys.modules.pop('ball_map_detect', None)
        import ball_map_detect as bmd
    except Exception as e:
        print('   （跳过：PC 上 import 不了 numpy/cv2/ball_map_detect —— %s）' % e)
        return

    bgr = np.full((90, 128, 3), 200, np.uint8)      # 亮背景（V≈200）
    cv2.circle(bgr, (30, 20), 3, (0, 0, 255), -1)   # 红球（BGR）
    cv2.circle(bgr, (90, 50), 3, (255, 0, 0), -1)   # 蓝球
    cv2.circle(bgr, (20, 70), 4, (0, 0, 0), -1)     # 黑球（亮背景衬着）
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    ref, _ = bmd.detect_map(bgr, bmd._ring_median_fast, max_area=6031)
    _check('合成图 detect_map 找到 3 个球（红/蓝/黑）', len(ref) == 3,
           str([d['cls'] for d in ref]))

    cls_id = {'red': 0, 'blue': 1, 'black': 2}
    stats = []
    for d in ref:
        a = d['area']
        cx = (d['x'] - 4.0) / 8.0
        cy = (d['y'] - 4.0) / 8.0
        stats.append(dict(cls=cls_id[d['cls']], area=a,
                          sx=int(round(cx * a)), sy=int(round(cy * a))))
    mix, _ = bmd.detect_map_from_stats(stats, hsv, bmd._ring_median_fast)
    ok = (len(mix) == len(ref))
    if ok:
        for d in ref:
            cand = [e for e in mix if e['cls'] == d['cls']
                    and abs(e['x'] - d['x']) <= 0.5
                    and abs(e['y'] - d['y']) <= 0.5
                    and abs(e['r'] - d['r']) <= 0.5
                    and e['area'] == d['area']
                    and abs(e['f'] - d['f']) <= 0.02]
            ok = ok and len(cand) == 1
    _check('★ 整数统计 -> detect_map_from_stats 与 detect_map 同候选', ok,
           'ref=%d mix=%d' % (len(ref), len(mix)))

    # 暗区里的黑块：周围也暗（ring≈20），对比 < 35 -> 必须拒
    bgr2 = bgr.copy()
    bgr2[66:90, 100:128] = 20
    cv2.circle(bgr2, (114, 80), 3, (0, 0, 0), -1)
    hsv2 = cv2.cvtColor(bgr2, cv2.COLOR_BGR2HSV)
    extra = dict(cls=2, area=28, sx=114 * 28, sy=80 * 28)
    out2, rej2 = bmd.detect_map_from_stats([extra], hsv2, bmd._ring_median_fast)
    _check('★ 暗区里的黑候选被 classify_map 拒掉', len(out2) == 0 and rej2 == 1,
           'out=%d rej=%d' % (len(out2), rej2))


SCENARIOS = {
    'pl_map_stats': s_map_stats,
    'hole_adapt': s_hole_adapt,
    'match_flow': s_match_flow,
    'fire_ballno': s_fire_ballno,
    'rescue_ladder': s_rescue_ladder,
    'review_push': s_review_push,
    'live_partial': s_live_partial,
    'screen': s_screen,
    'input_hub': s_input_hub,
    'scene_gate': s_scene_gate,
    'frame_fuse': s_frame_fuse,
    'doctor': s_doctor,
    'display_swap': s_display_swap,
    'path_pin': s_path_pin,
    'load_bit': s_load_bit,
    'wd_timeline': s_wd_timeline,
    'healthy': s_healthy,
    'heal_first': s_heal_first,
    'never': s_never,
    'not_ours': s_not_ours,
    'boot_window': s_boot_window,
    'latch': s_latch_interrupt,
    'latch2': s_latch_write_fail,
    'down': s_unlatch_and_down,
    'frame_sane': s_frame_sane,
    'init_video': s_init_video,
}


def main(argv):
    if '-l' in argv:
        print('场景：%s' % ' '.join(SCENARIOS))
        return 0
    names = [a for a in argv if a in SCENARIOS] or list(SCENARIOS)
    for n in names:
        _prefer_here()       # 上一节可能把 /boot 顶到前面（见 _prefer_here 注释）
        SCENARIOS[n]()
    bad = [r for r in RESULTS if not r[1]]
    print('\n==== 台架小结：%d 项检查，%d 通过，%d 失败 ===='
          % (len(RESULTS), len(RESULTS) - len(bad), len(bad)))
    for name, ok, detail in bad:
        print('   [FAIL] %s  %s' % (name, detail))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
