# -*- coding: utf-8 -*-
"""视频域/DVP 寄存器最小工具箱（纯 /dev/mem，不依赖 PYNQ/zocl）+ 退出兜底。

为什么要有这个文件（2026-09-29 实测）：
    `vdma_capture._sensor_power_cycle()` 做"传感器断电重上电"时先写了
    `ctrl |= 0x03`（bit0 xclk_off + bit1 force_pwdn），然后 sleep 0.5s。
    这一段**没有 try/finally、没有 atexit、没有信号处理**。进程只要在这 0.5 秒里
    死掉（被 kill / 异常 / PYNQ 段错误），这两位就**没有人清** —— 它们是普通寄存器
    位，PL 不会自己复位。实测后果：
        ctrl = 0x383（bit0/bit1 锁着）-> xclk=0 / pwdn=1
        -> pclk64 与 xclk 同时掉到 0.348 倍（比值 2.874 vs 2.877 精确一致）
        -> 传感器彻底停摆，一直保持到有人手写一次 ctrl 才活过来。
    所以：**凡是动这两位的代码都必须走这里**，由 CtrlGuard 保证任何退出路径都清掉。

位定义（逐条核对 rtl/debug/dvp_ctrl.v 第 24~31 行，不凭记忆写位号）：
    bit0 = xclk_off          1 = 拉低停钟，0 = 输出 24MHz        （默认 0）
    bit1 = force_pwdn        1 = 强制传感器掉电，0 = 随 pl       （默认 0）
    bit3 = vid_rst_force     1 = 强制复位视频域（桥 + 视核）
    bit4 = force_sensor_rst  1 = 按住传感器复位：ov5640_top 重跑完整上电时序 +
                                 304 条 SCCB，**同时按住视频域复位**
                                 （不按住视频域，坏状态攒下的旧垃圾字节堵在 FIFO 头，
                                  新帧的 SOF 永远到不了 VDMA —— 修复是假的）
    bit6 = dbg_trig          0->1 沿触发桥账本快照
    bits[9:7] = dbg_sel      影子选择，7 = FIFO 水位/full/empty/SOF/dropping

放开传感器的语义（rtl/ov5640/power_ctrl.v:70，务必按 RTL 记）：
    ov5640_pwdn = (cnt_6ms >= 300000) ? 0 : 1
    cnt_6ms 只在 ov5640_pwdn==1 时计数、**只被 s_rst_n 清零** -> 开机 6ms 之后
    它就单调停在"已放开"。所以 **单独清掉 ctrl[1] 就等于放开传感器**，
    不需要再撑着写 6ms（这一条以前搞反过，记在这里）。

三个动作的区别（别混用，混用 = 把传感器按来按去 = 自造不稳定）：
    unlatch()        只清 bit0/bit1 -> 恢复 xclk + 放开 pwdn。**不动 bit4**，
                     所以不会打断传感器已经装好的配置。这是"把锁住的位松开"。
    reinit_sensor()  脉冲 bit4 -> ov5640_top 重跑上电 + SCCB + 桥复位。
                     用于"位没锁但半天不出图/时序不对"。
    power_cycle()    0/1 拉高 0.5s -> 放开 -> 再脉冲 bit4（现行救援序列，最重的一档）。
                     只有在"脉冲视频域完全无效"时才该用它。

用法（板上）：
    from dvp_regs import open_regs, CtrlGuard
    mem = open_regs(); g = CtrlGuard(mem)   # 构造即挂 atexit + 信号兜底
    g.unlatch('启动基线')                    # 也可以不管它，交给退出兜底

命令行（也是 CtrlGuard 退出兜底真正调用的入口）：
    sudo -E PYTHONIOENCODING=utf-8 python3 -u dvp_regs.py --dump
    sudo -E PYTHONIOENCODING=utf-8 python3 -u dvp_regs.py --unlatch
    sudo -E PYTHONIOENCODING=utf-8 python3 -u dvp_regs.py --unlatch --force
    （--force = 跳过写读回自检；只在"读回明显不可信但你就是想写"时用）
"""

import mmap
import os
import struct
import sys
import time

# ---- 寄存器窗口（与 link_doctor / stall_probe / pl_probe 保持同一套）----
P0, P1, P2, P3 = 0x43A00000, 0x43A10000, 0x43A20000, 0x43A30000
CTRL, VSTAT = 0x43B10000, 0x43B20000
MAP, VDMA = 0x43B00000, 0x43000000

BIT_XCLK_OFF = 1 << 0
BIT_PWDN = 1 << 1
BIT_VID_RST = 1 << 3
BIT_SEN_RST = 1 << 4
BIT_DBG_TRIG = 1 << 6
LATCH_MASK = BIT_XCLK_OFF | BIT_PWDN
# ★ "静止位"：这些位在**任何时候只要没有人在故意按住**，就都应该是 0。
#   以前只盯着 bit0/bit1，其实 bit3/bit4 崩在脉冲中间同样是锁：
#   bit3 锁着 = 视频域永远在复位（不出帧）；bit4 锁着 = 传感器永远被按在复位里。
#   "退出必净"的清法一律按这张表来（bit2 保留不动、bit6 是自清沿、dbg_sel 是指针）。
IDLE_MASK = BIT_XCLK_OFF | BIT_PWDN | BIT_VID_RST | BIT_SEN_RST
DBG_SEL7 = 0x380

BASES = dict(p0=P0, p1=P1, p2=P2, p3=P3, ctrl=CTRL, vstat=VSTAT,
             map=MAP, vdma=VDMA)

# VDMA S2MM（0x43000000）：只读/清 RS 用，别在这里启动
S2MM_DMACR, S2MM_DMASR = 0x30, 0x34
CR_RS = 1 << 0


# ============================================================================
#  纯 /dev/mem 的寄存器访问
# ============================================================================

class Mem:
    """一组 mmap 好的寄存器窗口。read/write 都带偏移，单位是字（4 字节）。"""

    def __init__(self, bases=None, size=4096):
        self._m = {}
        self._fds = {}
        for name, addr in (bases or BASES).items():
            fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
            try:
                self._m[name] = mmap.mmap(fd, size, mmap.MAP_SHARED,
                                          mmap.PROT_READ | mmap.PROT_WRITE,
                                          offset=addr)
            finally:
                os.close(fd)          # mmap 已持有自己的引用，fd 可以关
        self.size = size

    def _chk(self, base, off):
        if base not in self._m:
            raise KeyError('没有这个窗口：%r（可用：%s）'
                           % (base, '/'.join(sorted(self._m))))
        if off < 0 or off > self.size - 4:
            # ★ 越界一律报错，**不回卷**：回卷会静默读到另一个寄存器，
            #   而"静默失败最贵"（本工程反复吃亏的那一条）。
            raise ValueError('%s 窗口偏移 0x%X 越界（窗口只有 0x%X 字节）'
                             % (base, off, self.size))
        return self._m[base]

    def read(self, base, off=0):
        m = self._chk(base, off)
        return struct.unpack('<I', m[off:off + 4])[0]

    def write(self, base, off, val):
        # ★ off 故意**不给默认值**：漏写 off 会变成 TypeError。这个严格是有代价换来的
        #   —— 2026-09-29 离线台架第一次跑就抓到 4 处漏 off 的写（pulse/power_cycle/
        #   unlatch）。那种 TypeError 如果发生在看门狗线程里，会被 except Exception
        #   吞掉 -> 变成"自愈静默失效"，板上完全看不出来（本工程最贵的一类 bug）。
        m = self._chk(base, off)
        m[off:off + 4] = struct.pack('<I', val & 0xFFFFFFFF)

    def close(self):
        for m in self._m.values():
            try:
                m.close()
            except Exception:
                pass
        self._m.clear()


def open_regs(bases=None, size=4096):
    return Mem(bases, size)


def _d(a, b):
    """32 位环绕安全的增量（探针计数器是自由跑的累加器，会回绕）。"""
    return (b - a) & 0xFFFFFFFF


# ============================================================================
#  控制字动作
# ============================================================================

def unlatch(mem, quiet=False):
    """把 ctrl 里锁住的 bit0(xclk_off)/bit1(force_pwdn) 清掉。

    只做一次读改写（保留 dbg_sel 等其它位，不整字覆盖），然后写读回自检：
    **读回仍然是干净的**才认为窗口是我们的、写真的落地了。
    返回 (ok, 详情字符串)。

    为什么写读回自检：PL 有可能已经被换成别的 bit（开机 ~120s 那个 base.bin
    窗口就干这事），那时 0x43A30000 后面的 IP 不是我们的 —— 写进去要么报错、
    要么落到别的地方。读回是唯一不用魔法寄存器就能自证的办法。
    """
    before = mem.read('p3')
    want = before & ~IDLE_MASK
    mem.write('p3', 0x00, want)
    time.sleep(0.05)
    after = mem.read('p3')
    ok = (after & IDLE_MASK) == 0
    detail = ('ctrl 0x%03X -> 0x%03X（读回 0x%03X；xclk_off=%d pwdn=%d vid_rst=%d '
              'sensor_rst=%d）'
              % (before, want, after, after & 1, (after >> 1) & 1,
                 (after >> 3) & 1, (after >> 4) & 1))
    if not quiet:
        print('[dvp] unlatch：%s  %s' % ('OK' if ok else '!! 读回不对', detail),
              flush=True)
    return ok, detail


def pulse(mem, bit, hold_ms=200.0, quiet=False, tag=''):
    """脉冲一个控制位：置位 hold_ms -> 清零（清的时候只清这一位）。

    ★ 置位和清零包在 try/finally 里：崩在 hold 期间也一定要把这一位放掉 ——
      bit3 锁着 = 视频域永远复位（不出帧）；bit4 锁着 = 传感器永远按在复位里。
      这两种锁跟 bit0/bit1 是同一类事故（2026-09-29）。
    """
    v = mem.read('p3')
    try:
        mem.write('p3', 0x00, v | bit)
        time.sleep(hold_ms / 1000.0)
    finally:
        try:
            mem.write('p3', 0x00, mem.read('p3') & ~bit)
        except Exception:
            pass
    if not quiet:
        print('[dvp] 脉冲 ctrl bit%d（%s）%dms，ctrl=0x%03X'
              % (bit.bit_length() - 1, tag or '无标签', int(hold_ms),
                 mem.read('p3')), flush=True)


def pulse_vid_rst(mem, hold_ms=200.0, quiet=False):
    """ctrl[3]：复位视频域（桥 + 视核），清掉账本、重找同步。"""
    pulse(mem, BIT_VID_RST, hold_ms, quiet, 'vid_rst 视频域复位')


def reinit_sensor(mem, hold_ms=300.0, quiet=False):
    """ctrl[4]：ov5640_top 重跑完整上电时序 + 304 条 SCCB + 桥复位（约 1s 出流）。"""
    pulse(mem, BIT_SEN_RST, hold_ms, quiet, 'sensor_rst 传感器重跑上电+SCCB')


def power_cycle(mem, quiet=False, hold_s=0.5):
    """最重的救援：传感器断电重上电（0/1 拉高 hold_s）-> 放开 -> 再脉冲 bit4。

    ★ 全程 try/finally + 每一段都保证"最终一定放开"。这里就是 2026-09-29
      那次"锁死 xclk/pwdn 40 秒"的事故现场 —— 现在任何异常都走 finally。
    """
    base = mem.read('p3') & ~IDLE_MASK           # 干净基线：静止位一定是 0
    try:
        try:
            mem.write('p3', 0x00, base | BIT_XCLK_OFF | BIT_PWDN)
            time.sleep(hold_s)
        finally:
            # 不管上面怎么炸，先把 xclk/pwdn 放开（再抛）
            mem.write('p3', 0x00, base)
        time.sleep(0.2)
        reinit_sensor(mem, quiet=quiet)
    except Exception:
        # 最后一道：连 finally 里的写都失败，再尽力写一次 base 才往上抛。
        # 任何异常路径的最后一次写，静止位都必须干净。
        try:
            mem.write('p3', 0x00, base)
        except Exception:
            pass
        raise
    if not quiet:
        print('[dvp] power_cycle 完成（断电 %.2fs -> 放开 -> 传感器重跑），ctrl=0x%03X'
              % (hold_s, mem.read('p3')), flush=True)


# ============================================================================
#  退出兜底
# ============================================================================

class CtrlGuard:
    """保证"任何退出路径都不留 xclk_off/pwdn 位"。

    为什么不是简单的 atexit 里写一次：
      · 退出路径上 PL 可能已经不在（被重编程 / 半编程态）——那时读这个寄存器
        可能挂死 AXI 或者直接 SIGBUS。所以在**子进程**里做这次读改写：
        最坏结果是子进程死掉，主进程的退出流程照走。
      · SIGTERM/SIGKILL：SIGKILL 谁都救不了（那种情况由下次启动的 unlatch 兜底）；
        SIGTERM/SIGINT 在这里接管。
      · 双重保险：即便这次兜底没跑成，下一次启动的 link_boot 也会清一遍。

    构造即生效，不需要显式 arm；把 mem 传进来就行。
    """

    def __init__(self, mem=None, install_signals=True, verbose=True):
        self.mem = mem
        self.verbose = verbose
        self._done = False
        self._in_signal = False
        self._old_term = None
        self._base_ctrl = None
        try:
            self._base_ctrl = self.mem.read('p3')      # 只读一次，赚一份基线
        except Exception:
            self._base_ctrl = None
        import atexit
        atexit.register(self.restore, 'atexit')
        if install_signals:
            self._install_signals()

    # ---- 对外 ----
    def unlatch(self, tag=''):
        ok, detail = unlatch(self.mem, quiet=False)
        if not ok:
            print('[dvp] ！！unlatch 读回不对（%s）：窗口可能已经不是我们的 PL'
                  % tag, flush=True)
        return ok

    def restore(self, tag=''):
        """退出路径：把 bit0/bit1 清掉。幂等，且**在子进程里做**（见类注释）。"""
        if self._done:
            return True
        self._done = True
        ok, out = self._restore_isolated()
        if self.verbose:
            print('[dvp] 退出兜底（%s）：%s' % (tag, out), flush=True)
        return ok

    # ---- 内部 ----
    def _restore_isolated(self):
        """在子进程里做读改写：挂死/SIGBUS 都只伤到子进程。"""
        import subprocess
        try:
            cmd = [sys.executable, os.path.abspath(__file__), '--unlatch']
            p = subprocess.run(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=8.0)
            out = (p.stdout or b'').decode('utf-8', 'replace').strip()
            out = out.splitlines()[-1] if out else '(无输出)'
            if p.returncode == 0:
                return True, '已清 xclk_off/pwdn —— %s' % out
            return False, '子进程返回 %d：%s' % (p.returncode, out)
        except Exception as e:
            return False, '子进程兜底没跑成（%r）—— 下次启动的 link_boot 会再清一遍' % (e,)

    def _install_signals(self):
        import signal

        def _handler(signum, frame):
            if self._in_signal:                 # 第二次信号 = 立刻死，别互相等
                os._exit(128 + signum)
            self._in_signal = True
            name = {signal.SIGTERM: 'SIGTERM', signal.SIGINT: 'SIGINT'}.get(
                signum, 'sig%d' % signum)
            self.restore(name)
            # ★ 这里**不能** os._exit()：本工程另一半的病是"野 DMA"——
            #   缓冲是 CMA 页、进程一退就释放，而 PL 里的 VDMA 是硬件不会跟着停，
            #   它会继续往刚释放的页写。停 VDMA 的钩子挂在 atexit 里
            #   （S2mmCapture.start() 注册的 _stop_quiet）。os._exit 会跳过 atexit
            #   -> 清了 ctrl 却留下野 DMA，等于治好了半边弄坏了另半边。
            #   所以：抛异常走正常退栈，让 atexit 全跑完。restore 是幂等的。
            raise SystemExit(128 + signum)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._old_term = signal.getsignal(sig)
                signal.signal(sig, _handler)
            except Exception:
                pass


# ============================================================================
#  自检 / 命令行
# ============================================================================

def dump(mem):
    """一屏读数：ctrl 位解码 + 传感器侧计数器 + 视核账本 + VDMA 状态。"""
    c = mem.read('p3')
    pclk = mem.read('p0')
    vs = mem.read('p0', 0x08)
    href = mem.read('p1')
    st = mem.read('vstat')
    st2 = mem.read('vstat', 0x08)
    fc = mem.read('ctrl', 0x08)
    dmacr = mem.read('vdma', S2MM_DMACR)
    dmasr = mem.read('vdma', S2MM_DMASR)
    hl = (st2 >> 21) & 0x7FF
    errs = (st2 >> 15) & 0x3F
    print('[ctrl] 0x%03X  xclk_off=%d force_pwdn=%d vid_rst=%d sensor_rst=%d '
          'dbg_trig=%d dbg_sel=%d'
          % (c, c & 1, (c >> 1) & 1, (c >> 3) & 1, (c >> 4) & 1, (c >> 6) & 1,
             (c >> 7) & 7))
    print('[传感器] pclk64=%d vsync=%d href=%d（自由跑累加器，两次读求增量才判活）'
          % (pclk, vs, href))
    print('[视核] stat=0x%08X 行长度=%d errs=0x%02X frame_cnt=%d'
          % (st, hl, errs, fc))
    print('[VDMA] DMACR=0x%08X RS=%d | DMASR=0x%08X halted=%d errs=0x%02X'
          % (dmacr, dmacr & CR_RS, dmasr, dmasr & 1, (dmasr >> 4) & 0xFF))
    print('  判读：pclk64 两次读增量应约 1315000/s、vsync 约 30/s、href 约 21641/s、'
          '行长度应 2046/2047')
    return 0


def main(argv):
    do_dump = '--dump' in argv
    do_unlatch = '--unlatch' in argv
    force = '--force' in argv
    if not (do_dump or do_unlatch):
        print(__doc__)
        print('没给动作。可用：--dump | --unlatch [--force]')
        return 2
    mem = open_regs()
    try:
        if do_dump:
            dump(mem)
        if do_unlatch:
            if force:
                before = mem.read('p3')
                mem.write('p3', 0x00, before & ~LATCH_MASK)
                time.sleep(0.05)
                after = mem.read('p3')
                print('[dvp] --force：ctrl 0x%03X -> 0x%03X'
                      % (before, after), flush=True)
                return 0
            ok, _ = unlatch(mem)
            return 0 if ok else 1
    finally:
        mem.close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
