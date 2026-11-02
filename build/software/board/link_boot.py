# -*- coding: utf-8 -*-
"""链路唯一的上/下契约：把"每次上板这不行那不行"变成一句可判读的结论。

为什么要有这个文件（2026-09-29，用户原话："为什么这么不稳定…不要让它每次上板
都这不行那不行"）：
    以前每个脚本各写各的启动：自己脉冲 ctrl[3]、自己等 5 秒、自己判"环境问题"。
    结果是**同一个链路里有好几个演员同时在动手**（脚本的启动脉冲、脚本自己的
    存活确认、后台看门狗的自愈阶梯），互相打断、互相误判，而且没人事后能说清
    "刚才到底是谁动的手"。这个模块把启动收敛成**一条阶梯**，并且：
      · 动手前先看"现在能不能动手"（开机 ~120s 那个 base.bin 写入窗口里写 PL 必然出事）
      · 先确认"PL 里还是我们的 bit"（写读回自检，而且**在子进程里做** —— 读挂死/SIGBUS
        只伤子进程，不会把主脚本打死）
      · 一步一发、每发都限频、每发都打日志（>=5s 间隔；升级后静默 >=10s）
      · 连续失败**必须放弃**（不能自造死循环 —— 第一版救援在断电窗口里按了 35 次）
      · 最后给一句**可判读的结论**（健康 / 降级 / 死，附读数），不再靠猜

契约（所有上板脚本都该走这两句）：
    import link_boot
    v = link_boot.link_up(cap)        # cap = 已经 start() 的 S2mmCapture（消费者）
    ...跑业务...
    link_boot.link_down(cap)          # 幂等；同时挂在 atexit 上，正常退出/异常都有兜底
    if not v['ok']: ...              # 自己决定降级怎么跑，但别假装链路是好的

读数为王的健康判据（全部来自实测基线，见 docs 的验收记录）：
    pclk64 增量 ≈ 1,315,000/s（84.16MHz / 64）   容差 1.15M~1.45M
    vsync  增量 ≈ 30/s                            容差 25~35
    href   增量 ≈ 21,641/s                        容差 18k~25k
    行长度 (stat2>>21)&0x7FF ∈ {2046, 2047}       （2046/2047 = 720 行 x 2 字节的账本）
    frame_cnt 在动（它是"流有没有被消费掉"的唯一证据）
    DMASR halted=0、errs=0

用法（板上，从 /home/xilinx/wp 跑）：
    python3 -c "import link_boot; print(link_boot.health())"
    python3 link_boot.py --health              # 只读一次，给一句结论
    python3 link_boot.py --pretend             # 只做"能不能动手"的前置判断，不动 PL
"""

import os
import subprocess
import sys
import time

import dvp_regs

# ---- 判据常数（改这里就等于改全局口径）----
# ★★ 2026-10-17：**双档自适应**。pclk 落在哪一档，就套用那一档的 vsync/href 带
#   （降频版 bit 的传感器帧率/行率按比例减半；旧的 84MHz 基线会把健康链路误判
#   "死"，然后触发救援循环）。pclk 不在任何档 = 传感器真坏。
PCLK_PROFILES = (
    # (档名, pclk/64 每秒范围, vsync 每秒范围, href 每秒范围)
    ('84MHz', (1150000, 1450000), (25, 35), (18000, 25000)),
    ('42MHz', (580000, 740000), (12, 19), (9500, 12500)),
)
PCLK_PER_S = 1315000          # 84.16MHz/64，实测
VSYNC_PER_S = 30
HREF_PER_S = 21641
HLINES_OK = (2046, 2047)      # 行长度账本

# ---- 节奏纪律（救援阶梯不许自己造死循环）----
BOOT_QUIET_S = 150.0          # 开机后这段时间内**不许**碰 PL（base.bin 写入窗口）
ACTION_GAP_S = 5.0            # 两次动作之间至少 5s
ESCALATE_QUIET_S = 10.0       # 升级之后静默 >=10s
WAIT_FRAME_S = 4.0            # 每个动作之后等多久看 frame_cnt 动
MAX_ATTEMPTS = 3              # 动作总数上限（含升级）；到顶就放弃并给结论

_V = {'ok': False, 'grade': 'unknown', 'why': '', 'acts': [], 'health': {}}
_DOWN_DONE = False


def _log(msg):
    print('[link] %s' % (msg,), flush=True)


# ============================================================================
#  前置：现在能不能动手
# ============================================================================

def uptime_s():
    """开机到现在多少秒（读 /proc/uptime；板子没有 RTC，墙钟不可信，systemd 的
    uptime 才是可靠的那一个）。读不到就返回 None（= 不设限，但会明说）。"""
    try:
        with open('/proc/uptime') as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def boot_window(need_quiet=BOOT_QUIET_S):
    """是否还在"开机写 PL"的窗口里。返回 (在窗口内?, uptime, 还要等几秒)。

    ★ 为什么是 150 秒：2026-09-29 实测两次开机，`fpga_manager` 都在 **开机后
      ~120 秒**写一次 base.bin（镜像自带的启动机制，xml/UUID 每次都不一样）。
      我们在 t=125.6s 下的 bit 在 t=127.8s 被它换掉 —— 之后所有寄存器读全部
      SIGBUS（rc=135），日志里只有一行。以前这叫"环境问题"，其实是**时间窗**问题。
      规矩：开机 150 秒内不碰 PL（不下载、不写寄存器、不读寄存器）。
    """
    up = uptime_s()
    if up is None:
        return False, None, 0.0
    if up < need_quiet:
        return True, up, need_quiet - up
    return False, up, 0.0


def require_board_ready(wait=True, need_quiet=BOOT_QUIET_S, quiet=False):
    """**碰 PL 之前**的前置检查：处在开机 base.bin 写入窗口里就一律不许动手。

    ★ 为什么必须放在"建 capture 之前"：`S2mmCapture.__init__/start()` 本身就在写
      PL 寄存器（DMACR/DMASR）。以前是"先建 capture，再检查环境"——窗口里那几笔
      写正好撞在 base.bin 编程中，轻则写飞（半编程态、MMIO 读挂死总线），重则
      SIGBUS 把脚本打死（rc=135，日志只有一行）。所以顺序必须是：先问能不能动手，
      再动手。
    返回 True = 可以动手；False = 等了也没用（调用方应直接放弃）。
    """
    inside, up, need = boot_window(need_quiet)
    if not inside:
        return True
    if up is None:
        return True                       # 读不到 uptime：不设限（但明说）
    if not wait:
        _log('开机才 %.1fs —— 还在 base.bin 写 PL 的窗口里（<%.0fs），拒绝动手'
             % (up, need_quiet))
        return False
    _log('开机才 %.1fs —— base.bin 还在写 PL（实测开机后 ~120s 写一次）；'
         '现在碰 PL 会拿到半编程态/SIGBUS。等 %.0fs 再动手'
         % (up, need_quiet, need))
    while need > 0:
        time.sleep(min(10.0, need))
        inside, up, need = boot_window(need_quiet)
        if not inside:
            if not quiet:
                _log('已出开机窗口（uptime=%.1fs），继续' % (up,))
            return True
        if not quiet:
            _log('  还在窗口里，还剩 %.0fs' % need)
    if inside:
        _log('等完了还在窗口里（uptime=%s）—— 放弃动手' % up)
        return False
    return True


def wait_out_boot_window(need_quiet=BOOT_QUIET_S, quiet=False):
    """等到过了开机窗口。返回 True = 可以动手。"""
    inside, up, need = boot_window(need_quiet)
    if not inside:
        return True
    if not quiet:
        _log('开机才 %.1fs，还在 base.bin 写 PL 的窗口里（<%.0fs）—— 等 %.0fs 再动手'
             % (up, need_quiet, need))
    t0 = time.time()
    while time.time() - t0 < need + 0.5:
        time.sleep(min(5.0, need + 0.5 - (time.time() - t0)))
    inside, up, _ = boot_window(need_quiet)
    if inside:
        _log('等完了还在窗口里（uptime=%.1fs）—— 放弃动手' % up)
        return False
    if not quiet:
        _log('已出开机窗口（uptime=%.1fs），可以动手' % uptime_s())
    return True


def pl_present(timeout=8.0):
    """PL 里还是**我们的** bit 吗？返回 ('ours'|'not_ours'|'unknown', 详情)。

    ★ 为什么放在**子进程**里做：这个判断要对 0x43A30000 做一次写读回，而 PL 若
      已经被换成别的 bit / 处于半编程态，这次访问可能挂死 AXI 或直接 SIGBUS。
      在子进程里做，最坏结果是子进程死掉，主脚本还能正常报错退出。
    ★ 判据：dvp_regs --unlatch 的写读回自检（顺带把 xclk_off/pwdn 清了 —— 一举两得）。
      rc=0 -> 窗口是我们的；rc=1 -> 读回不对（不是我们的）；其它 -> 说不清。
    """
    cmd = [sys.executable, os.path.abspath(dvp_regs.__file__), '--unlatch']
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return 'unknown', '子进程 8s 没回来（总线挂死？）'
    except Exception as e:
        return 'unknown', '子进程没跑起来（%r）' % (e,)
    out = (p.stdout or b'').decode('utf-8', 'replace').strip()
    tail = out.splitlines()[-1] if out else '(无输出)'
    if p.returncode == 0:
        return 'ours', tail
    if p.returncode == 1:
        return 'not_ours', tail
    return 'unknown', 'rc=%d：%s' % (p.returncode, tail)


# ============================================================================
#  读数：健康判据
# ============================================================================

def _rate(mem, getter, sec=1.0):
    """两个采样求增量 -> 每秒速率（探针计数器是自由跑累加器，只有增量有意义）。"""
    a = getter(mem)
    time.sleep(sec)
    b = getter(mem)
    return dvp_regs._d(a, b) / sec


def health(mem=None, sec=1.0, quiet=True):
    """采一次读数并给判据。返回 dict（含 ok / grade / 各读数 / 不合格的原因）。"""
    own = mem is None
    if own:
        mem = dvp_regs.open_regs()
    try:
        fc0 = mem.read('ctrl', 0x08)
        pclk = _rate(mem, lambda m: m.read('p0'), sec)
        vs = _rate(mem, lambda m: m.read('p0', 0x08), sec)
        href = _rate(mem, lambda m: m.read('p1'), sec)
        fc1 = mem.read('ctrl', 0x08)
        st = mem.read('vstat')
        st2 = mem.read('vstat', 0x08)
        hl = (st2 >> 21) & 0x7FF
        errs = (st2 >> 15) & 0x3F
        dmacr = mem.read('vdma', dvp_regs.S2MM_DMACR)
        dmasr = mem.read('vdma', dvp_regs.S2MM_DMASR)
        ctrl = mem.read('p3')
    except Exception as e:
        return {'ok': False, 'grade': 'unreadable', 'why': '%s: %s'
                % (type(e).__name__, e), 'ctrl': None}
    h = dict(pclk=pclk, vsync=vs, href=href, hlen=hl, errs=errs,
             frame_cnt=fc1, fc_moving=(fc1 != fc0),
             dma_rs=dmacr & 1, dma_halted=dmasr & 1,
             latch_xclk_off=ctrl & 1, latch_pwdn=(ctrl >> 1) & 1,
             stat=st, ctrl=ctrl)
    bad = []
    _prof = next((pr for pr in PCLK_PROFILES if pr[1][0] <= pclk <= pr[1][1]), None)
    if _prof is None:
        bad.append('pclk %.0f/s 不在任何已知档位（%s）'
                   % (pclk, '/'.join('%s:%d~%d' % (pr[0], pr[1][0], pr[1][1])
                                     for pr in PCLK_PROFILES)))
    else:
        _pname, _ptol, _vtol, _htol = _prof
        if not (_vtol[0] <= vs <= _vtol[1]):
            bad.append('vsync %.1f/s 不在 %d~%d（%s 档=帧率）'
                       % (vs, _vtol[0], _vtol[1], _pname))
        if not (_htol[0] <= href <= _htol[1]):
            bad.append('href %.0f/s 不在 %d~%d（%s 档）'
                       % (href, _htol[0], _htol[1], _pname))
    if hl not in HLINES_OK:
        bad.append('行长度 %d 不在 %s（传感器时序不对）' % (hl, HLINES_OK))
    if not h['fc_moving']:
        bad.append('frame_cnt 不动（%d）—— 没有消费者，或视核没在出帧' % fc1)
    if h['dma_halted']:
        bad.append('VDMA halted=1（RS=%d）' % h['dma_rs'])
    if errs:
        bad.append('视核 errs=0x%02X' % errs)
    if h['latch_xclk_off'] or h['latch_pwdn']:
        bad.append('ctrl 里 xclk_off/pwdn 还锁着（0x%03X）' % ctrl)
    # 分级：传感器侧全好但没人消费 = 降级（多半是 VDMA 没开）；传感器侧坏 = 死
    sensor_bad = any(('pclk' in b) or ('vsync' in b) or ('href' in b)
                     or ('行长度' in b) or ('xclk' in b) for b in bad)
    h.update(ok=not bad, grade=('healthy' if not bad
                                else ('dead' if sensor_bad else 'degraded')),
             why='；'.join(bad))
    if not quiet:
        print(_health_line(h), flush=True)
    if own:
        mem.close()
    return h


def _health_line(h):
    if h.get('ctrl') is None:
        return '[link] 读不到寄存器：%s' % h.get('why')
    return ('[link] %s  pclk64=%.0f/s vsync=%.1f/s href=%.0f/s 行长度=%s '
            'frame_cnt=%s%s errs=0x%02X VDMA(RS=%d halted=%d) ctrl=0x%03X%s'
            % ('健康' if h['ok'] else ('死' if h['grade'] == 'dead' else '降级'),
               h['pclk'], h['vsync'], h['href'], h['hlen'], h['frame_cnt'],
               '(在动)' if h['fc_moving'] else '(不动)', h['errs'],
               h['dma_rs'], h['dma_halted'], h['ctrl'],
               '' if h['ok'] else '  <- ' + h['why']))


# ============================================================================
#  上：一条阶梯，够用就停，连败就放弃
# ============================================================================

def link_up(cap=None, attempts=MAX_ATTEMPTS, quiet=False,
            need_quiet=BOOT_QUIET_S, allow_boot_wait=False):
    """把链路拉到"有帧在流"。返回 verdict dict（ok / grade / why / acts / health）。

    阶梯（一发一停，每发都记账）：
      [0] 开机窗口检查 -> 在里面就等着（或直接给结论，看 allow_boot_wait）
      [1] 子进程身份自检 + 清锁位（dvp_regs --unlatch）
      [2] 等 frame_cnt 动（最多 WAIT_FRAME_S）
      [3] 不动 -> 脉冲 ctrl[3]（隔 >=5s）
      [4] 还不动 -> 再脉冲 ctrl[3]
      [5] 还不动 -> 升级 ctrl[4]（传感器重跑上电+SCCB），然后静默 >=10s
      [6] 到 attempts 上限还不成就放弃，给出结论（不再动手）
      [7] 最终一律跑一遍 health()，把读数写进结论
    """
    acts = []
    v = {'ok': False, 'grade': 'unknown', 'why': '', 'acts': acts, 'health': {}}

    def _record(kind, detail):
        acts.append((round(time.time(), 1), kind, detail))
        _log('%s：%s' % (kind, detail))

    # [0] 能不能动手（同一个前置检查，见 require_board_ready）
    inside, up, need = boot_window(need_quiet)
    if inside:
        if not allow_boot_wait:
            v.update(grade='blocked', why='还在开机 base.bin 写 PL 的窗口里'
                                          '（uptime=%.1fs < %.0fs），不许碰 PL'
                                          % (up, need_quiet))
            _log('★ 拒绝动手：%s' % v['why'])
            return v
        if not require_board_ready(wait=True, need_quiet=need_quiet, quiet=quiet):
            v.update(grade='blocked', why='等出开机窗口失败')
            _log('★ 拒绝动手：%s' % v['why'])
            return v
    elif not quiet:
        _log('开机 %.1fs，不在写入窗口内' % (up if up is not None else -1))

    # [1] 身份自检 + 清锁位（这一步本身就证明了"窗口是我们的"）
    kind, detail = pl_present()
    _record('身份自检+清锁位', '%s（%s）' % (kind, detail))
    if kind == 'not_ours':
        v.update(grade='dead', why='PL 里不是我们的 bit（写读回自检不过）——'
                                   '先下 bit，再谈别的')
        return v
    if kind == 'unknown':
        v.update(grade='unknown', why='身份自检说不清（%s）' % detail)
        return v
    # 清掉粘连的旧错误位（DMASR 是 W1C），这样后面的健康门只看**本次**的新错误
    if cap is not None and hasattr(cap, 'clear_errors'):
        try:
            cap.clear_errors()
        except Exception:
            pass

    # [2..6] 阶梯：等 -> 动手 -> 再等 ... 动作数封顶，等不动了就放弃
    t_last_action = 0.0
    mem = dvp_regs.open_regs()
    try:
        # 顺手把"别人留下没放掉的冻结位"清掉：留着一个 freeze 位，下一条脚本
        # 看到的就是"帧计数不动"，会被误判成掉链（这个坑很隐蔽，明说一句）。
        fz = mem.read('ctrl', 0x00)
        if fz & 0x01:
            mem.write('ctrl', 0x00, fz & ~0x01)
            _record('清冻结位', 'ctrl=0x%08X -> 0x%08X（上一个脚本/工具留下没放掉的）'
                    % (fz, fz & ~0x01))
        acts_n = 0
        for _round in range(attempts + 1):        # +1 = 动作做完之后还要再等一轮
            t0 = time.time()
            fc0 = mem.read('ctrl', 0x08)
            moved = False
            while time.time() - t0 < WAIT_FRAME_S:
                time.sleep(0.25)
                fc1 = mem.read('ctrl', 0x08)
                if fc1 != fc0:
                    _record('等到帧', 'frame_cnt %d -> %d（%.1fs）'
                            % (fc0, fc1, time.time() - t0))
                    moved = True
                    break
            if moved:
                break
            if acts_n >= max(1, attempts):
                _record('放弃', '动作已用满 %d 次，frame_cnt 仍停在 %d —— '
                        '不再动手（连败必须放弃，不能自锁）'
                        % (max(1, attempts), fc0))
                break
            gap = time.time() - t_last_action                 # 动作之间限频
            if t_last_action and gap < ACTION_GAP_S:
                time.sleep(ACTION_GAP_S - gap)
            if acts_n < 2:
                dvp_regs.pulse_vid_rst(mem, 200.0)
                _record('脉冲 ctrl[3]', '视频域复位（第 %d 次动作），等 %.0fs 看帧计数'
                        % (acts_n + 1, WAIT_FRAME_S))
            else:
                dvp_regs.reinit_sensor(mem, 300.0)
                _record('升级 ctrl[4]', '传感器重跑上电+SCCB（第 %d 次动作），静默 %.0fs'
                        % (acts_n + 1, ESCALATE_QUIET_S))
                time.sleep(ESCALATE_QUIET_S)
            acts_n += 1
            t_last_action = time.time()
        # [7] 结论
        h = health(mem)
        v['health'] = h
        v['ok'] = h['ok']
        v['grade'] = h['grade']
        v['why'] = h['why']
        if not quiet:
            print(_health_line(h), flush=True)
    finally:
        mem.close()
    if not v['ok']:
        n_act = len([a for a in acts if a[1] in ('脉冲 ctrl[3]', '升级 ctrl[4]')])
        _log('★ 结论：%s（动手 %d 次）—— %s'
             % ({'degraded': '降级', 'dead': '链路是死的'}.get(v['grade'], v['grade']),
                n_act, v['why']))
    return v


# ============================================================================
#  下：退出必净
# ============================================================================

def link_down(cap=None, freeze=True, quiet=False):
    """收尾：冻视频域 -> 停 VDMA -> 放掉冻结位 -> 清锁位。幂等，绝不抛异常。

    顺序讲究（2026-09-29），三步都有理由：
      1. **先冻**（ctrl[0]=1）：让视核停止出帧、不再往桥的 FIFO 里灌，收尾期间
         不会再有新的半帧推进来。
      2. **再停 VDMA**（RS=0）：PL 不再往 **已经要被释放的 CMA 页**写 —— 这就是
         "野 DMA"（脚本崩着退出时 atexit 不跑，VDMA 带着已释放的地址继续跑，
         下一个进程 allocate 到的页立刻被覆盖：段错误 / 一致性 10% / 3fps）。
      3. **最后放掉冻结位**（ctrl[0]=0）：**故意不留冻结位**。留着的坏处很实在：
         下一条脚本看到"帧计数不动"会以为掉链死（_init_video 的存活确认本来就
         会跳过被冻的链路），而冻结位是给"故意冻住画面量东西"的工具用的，
         不该被当成退出后的常态。
      4. 清锁位：ctrl 的 bit0/bit1 一个都不许留（2026-09-29 那次 40 秒锁死）。
    """
    global _DOWN_DONE
    if _DOWN_DONE:
        return
    _DOWN_DONE = True
    mem = None
    try:
        mem = dvp_regs.open_regs()
        if freeze:
            try:
                mem.write('ctrl', 0x00, mem.read('ctrl', 0x00) | 0x01)
            except Exception:
                pass
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass
        try:
            # 视频域也放掉冻结位之前先停 DMA —— 顺序反了会有一小段"没消费者"的窗口
            if freeze:
                mem.write('ctrl', 0x00, mem.read('ctrl', 0x00) & ~0x01)
            dvp_regs.unlatch(mem, quiet=True)
        except Exception:
            pass
        if not quiet:
            _log('收尾完成：视频域冻结位已放掉、VDMA 已停、ctrl 锁位已清')
    except Exception as e:
        if not quiet:
            _log('收尾没做全（%r）—— atexit/下次启动还会兜一层' % (e,))
    finally:
        if mem is not None:
            try:
                mem.close()
            except Exception:
                pass


def _atexit_down():
    try:
        link_down(quiet=True)
    except Exception:
        pass


def arm_atexit(cap=None):
    """把 link_down 挂到 atexit 上（幂等）。带上 cap 就能走全序：
    冻视频域 -> cap.stop()（停 VDMA，杜绝野 DMA）-> 放冻结位 -> 清静止位。

    ★ 注册顺序有讲究：这一发在 S2mmCapture.start() 的 _stop_quiet **之后**注册，
      atexit 是 LIFO —— 所以 link_down 先跑（冻 + 停），_stop_quiet 再跑（重复停，
      幂等）。反过来的话会先停 VDMA 再冻，中间那一小段"没消费者"正是链路最容易
      堵死的状态。
    """
    import atexit
    atexit.register(lambda: link_down(cap, quiet=True))


# ============================================================================
#  命令行
# ============================================================================

def main(argv):
    if '--health' in argv:
        inside, up, need = boot_window()
        if inside:
            print('[link] 开机才 %.1fs —— 还在 base.bin 写 PL 的窗口里（<%.0fs），'
                  '这次读数不可信，先等 %.0fs' % (up, BOOT_QUIET_S, need))
            return 9
        h = health(quiet=False)
        return 0 if h['ok'] else (1 if h['grade'] != 'dead' else 2)
    if '--pretend' in argv:
        inside, up, need = boot_window()
        print('[link] uptime=%s  开机窗口=%s'
              % ('%.1fs' % up if up is not None else '读不到',
                 '在里面，还要等 %.0fs' % need if inside else '已过'))
        kind, detail = pl_present()
        print('[link] PL 身份自检：%s（%s）' % (kind, detail))
        return 0
    print(__doc__)
    print('没给动作。可用：--health | --pretend')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
