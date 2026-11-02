#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""usb_diag.py —— USB-A(键盘口) 供电/端口 直读直写诊断（**不依赖 PYNQ 库**）。

★ 发现链（2026-10-01，键盘"零枚举"的根因侦查）：
  现象：键盘插在板上 USB-HOST 口、重启期间也插着，但**一次枚举都没有**。
        dmesg：ULPI 0x0451/0x1507 → Found TI TUSB1210 → 完整性检查 passed →
        EHCI Host Controller 起来 → root hub 在、usbhid 已注册；
        但 0 个 "new USB device"，`/dev/input/` 不存在 ⇒ 软件栈全好，问题在更外面。
  驱动链（读源码实锤）：
    · drivers/usb/phy/phy-ulpi.c：DT 有 `drv-vbus` 时只把
      ULPI_OTG_DRVVBUS|ULPI_OTG_DRVVBUS_EXT 记进 phy->flags 并挂回调；
      probe 时唯一跑的 ulpi_set_flags() **从不写 DRVVBUS 位**。
    · 真正会写 DRVVBUS 的路径只有 usb_phy_vbus_on()，它只在 chipidea host 的
      ehci_ci_portpower()（ci_host.c:66，条件 CI_HDRC_PHY_VBUS_CONTROL，
      Zynq 的 pdata 确实带这个 flag）里被调用。
    · 而 ehci_ci_portpower 只有 **HCSPARAMS.PPC=1** 时 EHCI 才会去调
      （drivers/usb/host/ehci-hub.c: `if (HCS_PPC(...)) ehci_port_power(...)`）。
 原理图（TUL_PYNQ_Schematic_R12.pdf）：
    板上 USB-A 口的 VBUS **不是常通 5V** —— 来自 U10 = TI TPS2065DB 功率开关
    （IN=VCC5V0, OUT=USB_OTG_VBUS），其 EN 由 TUSB1210 的 **CPEN(pin18)** 驱动
    （R175 4.7K 下拉 ⇒ 默认关断）。CPEN 正由 ULPI OTG_CTRL 的
    DRVVBUS/DRVVBUS_EXT 位控制。
  ⇒ 若 PPC=0：内核永不调 port_power ⇒ DRVVBUS 恒 0 ⇒ CPEN 恒低 ⇒ 口上无 5V ⇒
    键盘**完全没电** ⇒ 连"设备插入"事件都不会有 —— 与现象逐条吻合。
  本工具先只读判决（read），确认后现场把 DRVVBUS 置上（vbus on）。

动的是什么：
  · 只读：0xE0002000+0x170（ULPI viewport）的 READ 事务 + EHCI 侧几个只读寄存器；
    另有对 ULPI 0x16(SCRATCH) 的一次往返（内核开机做 integrity 用的是同一招，
    此处用来证明"本路径读+写都通"；SCRATCH 无功能语义，运行期没人用）。
  · vbus on/off：读改写 OTG_CTRL(0x0A) 的 bit5 DRVVBUS / bit6 DRVVBUS_EXT，
    其余位原样保留（pulldown 不动）。
不动的：EHCI 的 PORTSC/USBCMD 等一律只读 —— 那些归内核管，写了会打架。

用法（板上；/dev/mem 需要 root）：
    sudo python3 usb_diag.py read        # 只读诊断。rc: 0=DRVVBUS 已在位 / 3=为 0
    sudo python3 usb_diag.py vbus on     # 置 DRVVBUS|DRVVBUS_EXT → U10 开通（口上 5V）
    sudo python3 usb_diag.py vbus off    # 清位，恢复默认（口断电）
    sudo python3 usb_diag.py watch [S] [on|off]
        # ★ 2026-10-01 深夜加：盯**线上的实际状态**（DEBUG 0x19 的 LineState）——
        #   这是"键盘到底在不在线上"的直接证据。以前所有脚本都没读过这一格。
        #   默认 20 秒、0.25s 一采样；给 on/off 先切一次 VBUS 再盯。
    python3 usb_diag.py --selftest       # PC 离线：假总线跑握手/RMW/超时/判读（不碰板）
rc：0=好 / 1=用法错 / 2=ULPI 通道不通(超时/ID 不符) / 3=判决"口无电"(read) / 4=改完回读不一致
"""
import mmap
import os
import struct
import sys
import time

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# ---- 硬件常量（板上实测口径）------------------------------------------------
BASE_USB = 0xE0002000   # PS USB0（chipidea + ULPI viewport + EHCI 都在这一格里）
SIZE_USB = 0x1000
VP_OFF = 0x170          # DT 里 phy0 的 view-port 属性
VIEW_WAKEUP = 1 << 31
VIEW_RUN = 1 << 30
VIEW_WRITE = 1 << 29

REG_OTG_CTRL = 0x0a
REG_SCRATCH = 0x16
REG_DEBUG = 0x19            # ULPI_DEBUG（内核 include/linux/ulpi/regs.h：0x16=SCRATCH, 0x19=DEBUG）
OTG_DRVVBUS = 1 << 5        # ULPI_OTG_CTRL_DRVVBUS
OTG_DRVVBUS_EXT = 1 << 6    # ULPI_OTG_CTRL_DRVVBUS_EXT
DRVVBUS_BOTH = OTG_DRVVBUS | OTG_DRVVBUS_EXT
# DEBUG[1:0] = LineState（ULPI 规范）。判读看的是**物理线**：
#   00 SE0 两边都为低（没设备拉 / 设备没电）；01 J  D+ 被拉高（全速设备在位）；
#   10 K  D- 被拉高（低速设备在位）；11 SE1（几乎不该出现）
LINESTATE = {0: "SE0(00)", 1: "J(01)", 2: "K(10)", 3: "SE1(11)"}

OTG_BITS = [("ID_PULLUP", 0), ("DP_PULLDOWN", 1), ("DM_PULLDOWN", 2),
            ("DISCHRGVBUS", 3), ("CHRGVBUS", 4),
            ("DRVVBUS", 5), ("DRVVBUS_EXT", 6), ("EXTVBUSIND", 7)]
# TUSB1210 的 ID：0x0451/0x1507（dmesg 的 "ULPI transceiver vendor/product ID"）
EXPECT_ID = {0x00: 0x51, 0x01: 0x04, 0x02: 0x07, 0x03: 0x15}


class Mem:
    """极简 /dev/mem 映射（与 link_doctor 同款做法，不依赖 pynq）。

    0xE0002000 是 4K 对齐的设备内存（非 System RAM），STRICT_DEVMEM 不禁它。
    """

    def __init__(self, base, size):
        self._f = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self._m = mmap.mmap(self._f, size, mmap.MAP_SHARED,
                                mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
        except Exception:
            os.close(self._f)
            raise

    def read(self, off):
        return struct.unpack_from("<I", self._m, off)[0]

    def write(self, off, v):
        struct.pack_into("<I", self._m, off, v & 0xFFFFFFFF)

    def close(self):
        try:
            self._m.close()
        finally:
            os.close(self._f)


class UlpiVp:
    """ULPI viewport 事务，语义逐位照抄内核 phy-ulpi-viewport.c。

    内核原文（v6.6 drivers/usb/phy/phy-ulpi-viewport.c）：
      read : 写 WAKEUP|WRITE → 等 WAKEUP 清；写 RUN|READ|ADDR(reg) → 等 RUN 清；
             数据 = (读回值 >> 8) & 0xff
      write: 写 WAKEUP|WRITE → 等 WAKEUP 清；写 RUN|WRITE|DATA|ADDR(reg) → 等 RUN 清
    超时 2000µs（readl_poll_timeout_atomic(..., 1, 2000)）。
    """

    def __init__(self, bus, off=VP_OFF):
        self.bus = bus
        self.off = off

    def _wait(self, mask, timeout_us=2000):
        t0 = time.monotonic()
        while True:
            v = self.bus.read(self.off)
            if not (v & mask):
                return v
            if (time.monotonic() - t0) * 1e6 > timeout_us:
                raise TimeoutError(
                    "viewport 忙等超时 mask=0x%08X last=0x%08X" % (mask, v))

    def _wakeup(self):
        self.bus.write(self.off, VIEW_WAKEUP | VIEW_WRITE)
        self._wait(VIEW_WAKEUP)

    def read(self, reg):
        self._wakeup()
        self.bus.write(self.off, VIEW_RUN | ((reg & 0xff) << 16))
        v = self._wait(VIEW_RUN)
        return (v >> 8) & 0xff

    def write(self, reg, val):
        self._wakeup()
        self.bus.write(self.off, VIEW_RUN | VIEW_WRITE |
                       ((reg & 0xff) << 16) | (val & 0xff))
        self._wait(VIEW_RUN)


def decode_otg(v):
    return " ".join(n for n, b in OTG_BITS if v & (1 << b)) or "(全 0)"


def scratch_roundtrip(vp):
    """内核开机的 integrity 同款：0x55 / 0xAA 两轮往返。返回 True/False。"""
    for val in (0x55, 0xAA):
        vp.write(REG_SCRATCH, val)
        got = vp.read(REG_SCRATCH)
        if got != val:
            return False, "写 0x%02X 回读 0x%02X" % (val, got)
    return True, "0x55/0xAA 两轮一致"


def ehci_dump(bus, out):
    """只读 EHCI 侧：CAPLENGTH/HCSPARAMS(PPC!)/USBCMD/USBSTS/PORTSC1。"""
    caplen = bus.read(0x100) & 0xff
    hcs = bus.read(0x104)
    nports = hcs & 0xF
    ppc = (hcs >> 4) & 1
    op = 0x100 + caplen                      # 运行寄存器基址 = 能力基址 + CAPLENGTH
    usbcmd = bus.read(op + 0x00)
    usbsts = bus.read(op + 0x04)
    portsc = bus.read(op + 0x44)             # PORTSC1
    ccs = portsc & 1
    pe = (portsc >> 2) & 1
    oca = (portsc >> 4) & 1
    pp = (portsc >> 12) & 1
    ls = (portsc >> 10) & 0x3
    ls_s = {0: "SE0(00)", 1: "J(01)", 2: "K(10)", 3: "Undef(11)"}[ls]
    print("== EHCI / 控制器（只读） ==", file=out)
    print("  CAPLENGTH 0x%02x  HCSPARAMS 0x%08X  N_PORTS=%d  PPC=%d"
          % (caplen, hcs, nports, ppc), file=out)
    print("  USBCMD 0x%08X (RS=%d)  USBSTS 0x%08X (HCHalted=%d)"
          % (usbcmd, usbcmd & 1, usbsts, (usbsts >> 12) & 1), file=out)
    print("  PORTSC1 0x%08X  CCS=%d(有设备=1) PE=%d PP=%d(端口电源位) "
          "OCA=%d(过流) LSTATUS=%s" % (portsc, ccs, pe, pp, oca, ls_s), file=out)
    return dict(nports=nports, ppc=ppc, portsc=portsc, ccs=ccs, pp=pp)


def cmd_read(bus, out=sys.stdout):
    print("== ULPI 收发器（viewport @ 0x%08X+0x%X，直读） =="
          % (BASE_USB, VP_OFF), file=out)
    vp = UlpiVp(bus)
    try:
        idv = {r: vp.read(r) for r in EXPECT_ID}
        func = vp.read(0x04)
        ifc = vp.read(0x07)
        otg = vp.read(REG_OTG_CTRL)
        ints = vp.read(0x13)
    except TimeoutError as e:
        print("  ✗ ULPI viewport 不通：%s" % e, file=out)
        print("    （PHY 钟没跑 / 控制器挂起？先别改任何寄存器）", file=out)
        return 2
    for r, name in ((0x00, "VID_LOW "), (0x01, "VID_HIGH"), (0x02, "PID_LOW "),
                    (0x03, "PID_HIGH")):
        print("  0x%02X %s 0x%02X" % (r, name, idv[r]), file=out)
    ok_id = all(idv[r] == v for r, v in EXPECT_ID.items())
    print("       → %s" % ("TI TUSB1210 ✓（与 dmesg 的 0x0451/0x1507 一致）"
                          if ok_id else "**ID 不符**（对错板/对错寄存器？）"),
          file=out)
    print("  0x04 FUNC_CTRL  0x%02X   (XCVRSEL=%d OPMODE=%d SUSPENDM=%d)"
          % (func, func & 3, (func >> 3) & 3, (func >> 6) & 1), file=out)
    print("  0x07 IFC_CTRL   0x%02X" % ifc, file=out)
    print("  0x0A OTG_CTRL   0x%02X   = %s" % (otg, decode_otg(otg)), file=out)
    print("        bit5 DRVVBUS     = %d   ← 关键" % ((otg >> 5) & 1), file=out)
    print("        bit6 DRVVBUS_EXT = %d   ← 关键" % ((otg >> 6) & 1), file=out)
    print("  0x13 USB_INT_STS 0x%02X" % ints, file=out)
    try:
        ok, msg = scratch_roundtrip(vp)
    except TimeoutError as e:
        ok, msg = False, "超时 %s" % e
    print("  0x16 SCRATCH 往返：%s %s" % ("✓" if ok else "✗", msg), file=out)

    info = ehci_dump(bus, out)

    print("== 判决 ==", file=out)
    if not ok_id:
        print("  ID 不符 ⇒ 以上读数不可信，先查环境。", file=out)
        return 2
    if not ok:
        print("  SCRATCH 往返失败 ⇒ viewport 写路径不可信，"
              "先别做 vbus on。", file=out)
        return 2
    drv = (otg & DRVVBUS_BOTH) == DRVVBUS_BOTH
    if info["ppc"] == 0:
        print("  HCSPARAMS.PPC=0 ⇒ EHCI 永远不会调 port_power ⇒ 内核**从不**写"
              " DRVVBUS（驱动链实锤）", file=out)
    else:
        print("  HCSPARAMS.PPC=1 ⇒ 内核本应在 root hub 上电时调 port_power"
              "（若 DRVVBUS 仍为 0，看 dmesg 是否 hub 上电失败）", file=out)
    if drv:
        print("  DRVVBUS 两位已置 1 ⇒ CPEN 应为高 ⇒ U10(TPS2065) 开通。"
              "若键盘仍死，查键盘/线/口或 U10 本身。", file=out)
        return 0
    print("  DRVVBUS=0 ⇒ CPEN 低 ⇒ U10 关断 ⇒ USB-A 口**无 5V** ⇒ 键盘无电、"
          "零事件（与现象吻合）", file=out)
    print("  下一步：sudo python3 usb_diag.py vbus on   然后看 dmesg / lsusb",
          file=out)
    return 3


def cmd_vbus(bus, on, out=sys.stdout):
    vp = UlpiVp(bus)
    try:
        ok, msg = scratch_roundtrip(vp)
        if not ok:
            # ★ 2026-10-01：降级为**警告**（原来直接 return 4）。理由：SCRATCH 只是个
            #   演习，真门禁是下面"写 OTG 然后回读"——写路径真断了，回读对不上一样会拦。
            #   （且发现过 viewport 读有时返回 0x00 的怪癖，别让演习卡住正事。）
            print("⚠ SCRATCH 往返失败（%s）—— 降级为警告，继续；"
                  "以 OTG 写后回读为准" % msg, file=out)
        cur = vp.read(REG_OTG_CTRL)
        want = (cur | DRVVBUS_BOTH) if on else (cur & ~DRVVBUS_BOTH)
        vp.write(REG_OTG_CTRL, want)
        back = vp.read(REG_OTG_CTRL)
    except TimeoutError as e:
        print("✗ ULPI viewport 不通：%s" % e, file=out)
        return 4
    print("OTG_CTRL: 改前 0x%02X（%s）" % (cur, decode_otg(cur)), file=out)
    print("          改后 0x%02X（%s）" % (back, decode_otg(back)), file=out)
    got = (back & DRVVBUS_BOTH) == (DRVVBUS_BOTH if on else 0)
    kept = (back & ~DRVVBUS_BOTH) == (cur & ~DRVVBUS_BOTH)
    if got and kept:
        print("✓ 位已%s，其余位原样保留。" % ("置上（口上应有 5V）" if on
                                            else "清掉（口断电）"), file=out)
        if on:
            print("  现在看板子：dmesg 出现 'new USB device'？lsusb 多出设备？"
                  "ls /dev/input/ 出现 event*？", file=out)
        return 0
    print("✗ 回读不一致（got=%s kept=%s）—— 别当成成功" % (got, kept), file=out)
    return 4


def cmd_watch(bus, seconds=20.0, vbus=None, out=sys.stdout):
    """★★ 盯线（2026-10-01 深夜加）：DEBUG(0x19).LineState + PORTSC + OTG，0.25s 采样。

    为什么要有它：之前判断键盘在不在，靠的都是**间接**证据（dmesg、PORTSC.CCS、
    我切 VBUS 后的枚举轮次）。而 ULPI 有一颗 DEBUG 寄存器直接报**物理线的状态**：
      · LINE=J / K ⇒ 线上**有设备在拉**（全速=J / 低速=K）⇒ 键盘活着，
        问题在数据/协议层（-71 那类）；
      · LINE=SE0 稳态 ⇒ 线上**什么都没有**（设备没电 / 线断 / 收发器没探头在听）
        ⇒ 该动的是供电和线，不是软件。
    ★ 前提：PHY 不处于挂起（FUNC_CTRL.SUSPENDM=1 才说明探头开着）——每轮打印。
    ★ 采样期**只读** MMIO（vbus 参数只在开头切一次）；PORTSC 的 CSC 只读不清，
      清位归内核的中断处理，别抢。
    """
    vp = UlpiVp(bus)
    op = 0x100 + (bus.read(0x100) & 0xff)      # 运行寄存器基址（同 ehci_dump）
    portsc_off = op + 0x44
    if vbus is not None:
        cur = vp.read(REG_OTG_CTRL)
        want = (cur | DRVVBUS_BOTH) if vbus else (cur & ~DRVVBUS_BOTH)
        vp.write(REG_OTG_CTRL, want)
        print("先切 VBUS：OTG 0x%02X -> 读回 0x%02X（要的是 %s）"
              % (cur, vp.read(REG_OTG_CTRL), "on" if vbus else "off"), file=out)
    n = {k: 0 for k in ("SE0", "J", "K", "SE1")}
    n_csc = n_ccs = 0
    t0 = time.time()
    print("--- 盯线 %.1fs（0.25s 一采样，只读）---" % seconds, file=out)
    while time.time() - t0 < seconds:
        otg = vp.read(REG_OTG_CTRL)
        func = vp.read(0x04)
        dbg = vp.read(REG_DEBUG)
        ls = dbg & 3
        p = bus.read(portsc_off)
        ccs, csc = p & 1, (p >> 1) & 1
        key = LINESTATE[ls].split("(")[0]
        n[key] += 1
        n_ccs += ccs
        n_csc += csc
        print("[t+%5.1fs] LINE=%-7s DEBUG=0x%02X | OTG=0x%02X DRVVBUS=%d | "
              "SUSPENDM=%d | PORTSC=0x%08X CCS=%d CSC=%d"
              % (time.time() - t0, LINESTATE[ls], dbg, otg, (otg >> 5) & 1,
                 (func >> 6) & 1, p, ccs, csc), file=out)
        time.sleep(0.25)
    print("--- 统计：SE0 %d / J %d / K %d / SE1 %d ；CCS=1 的采样 %d ；CSC=1 的采样 %d ---"
          % (n["SE0"], n["J"], n["K"], n["SE1"], n_ccs, n_csc), file=out)
    if n["J"] or n["K"]:
        print("判决：线上**出现过设备在拉**（J/K）⇒ 电脑/键盘侧物理上是通的，"
              "下一步查数据/协议层（把本轮输出 + dmesg 留档）", file=out)
    else:
        print("判决：全程 SE0 ⇒ 线上**没有任何东西在拉** ⇒ 键盘没电 / 线断 / "
              "口坏（或 PHY 挂起，看上面的 SUSPENDM）——**该动的是供电和线**。", file=out)
    try:
        import subprocess
        print("lsusb: %s" % subprocess.run("lsusb | tail -3", shell=True,
                                           capture_output=True, text=True)
              .stdout.strip().replace("\n", " | "), file=out)
        print("--- dmesg tail 8 ---", file=out)
        print(subprocess.run("dmesg | tail -8", shell=True, capture_output=True,
                             text=True).stdout.strip(), file=out)
    except Exception as e:
        print("（dmesg/lsusb 拿不到：%s）" % e, file=out)
    return 0


# ---- 离线自检：假总线 ------------------------------------------------------
class FakeBus:
    """模拟一块寄存器空间 + 一个"1 拍完成"的 viewport。

    stuck=True 时 RUN 位永不清 —— 用来验证超时路径真的会抛。
    """

    def __init__(self, regs=None, mm=None, stuck=False):
        self.regs = dict(regs or {})
        self.mm = dict(mm or {})
        self.stuck = stuck
        self._vp = 0
        self.wakeups = 0
        self.txns = []

    def read(self, off):
        if off == VP_OFF:
            return self._vp
        return self.mm.get(off, 0)

    def write(self, off, v):
        if off != VP_OFF:
            self.mm[off] = v
            return
        if v & VIEW_WAKEUP:
            self.wakeups += 1
            self._vp = v & ~VIEW_WAKEUP          # 立刻完成 → WAKEUP 自清
            return
        if v & VIEW_RUN:
            if self.stuck:
                self._vp = v                     # RUN 永不清 → 调用方该超时
                return
            reg = (v >> 16) & 0xff
            if v & VIEW_WRITE:
                self.regs[reg] = v & 0xff
                self.txns.append(("W", reg, v & 0xff))
                self._vp = v & ~VIEW_RUN
            else:
                d = self.regs.get(reg, 0)
                self.txns.append(("R", reg, d))
                self._vp = d << 8                # 数据在 bits 15:8，RUN 清
            return


def selftest():
    import io
    n_pass = n_fail = 0

    def check(name, cond):
        nonlocal n_pass, n_fail
        if cond:
            n_pass += 1
            print("  ✓ %s" % name)
        else:
            n_fail += 1
            print("  ✗ %s" % name)

    print("== usb_diag.py 离线自检 ==")

    # 1) 读事务：握手 + 数据位置
    b = FakeBus(regs={0x00: 0x51, 0x01: 0x04, 0x02: 0x07, 0x03: 0x15})
    vp = UlpiVp(b)
    check("read(0x00)==0x51（数据取 bits15:8）", vp.read(0x00) == 0x51)
    check("read(0x03)==0x15", vp.read(0x03) == 0x15)
    check("每次事务前都先 WAKEUP", b.wakeups >= 2)
    check("READ 事务不带 WRITE 位",
          all(t[0] == "R" for t in b.txns))

    # 2) 写事务
    vp.write(0x0a, 0x66)
    check("write(0x0a,0x66) 落到寄存器", b.regs[0x0a] == 0x66)

    # 3) 超时路径
    bs = FakeBus(stuck=True)
    try:
        UlpiVp(bs).read(0x00)
        check("stuck 总线应超时抛错", False)
    except TimeoutError:
        check("stuck 总线应超时抛错", True)

    # 4) cmd_read 判读与 rc（捕获输出）
    buf = io.StringIO()
    b2 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x06},
                 mm={0x100: 0x40, 0x104: 0x00000011, 0x144: 0x00001000})
    rc = cmd_read(b2, out=buf)
    check("read: DRVVBUS=0 时 rc=3（判决口无电）", rc == 3)
    check("read: 报告含 DRVVBUS 关键行", "DRVVBUS" in buf.getvalue())
    check("read: PPC=1 会打印对应分支", "PPC=1" in buf.getvalue())

    b3 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x66},
                 mm={0x100: 0x40, 0x104: 0x00111001})
    rc = cmd_read(b3, out=io.StringIO())
    check("read: DRVVBUS 已在位时 rc=0", rc == 0)

    # 5) vbus on/off 的读改写：只动 bit5/6，pulldown 等其余位原样
    b4 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x06})
    rc = cmd_vbus(b4, True, out=io.StringIO())
    check("vbus on: rc=0 且 0x06→0x66", rc == 0 and b4.regs[0x0a] == 0x66)
    rc = cmd_vbus(b4, False, out=io.StringIO())
    check("vbus off: rc=0 且回到 0x06", rc == 0 and b4.regs[0x0a] == 0x06)

    b5 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x02})   # 只 DM_PULLDOWN
    cmd_vbus(b5, True, out=io.StringIO())
    check("RMW 保留无关位（0x02→0x62）", b5.regs[0x0a] == 0x62)

    # 6) 解码
    check("decode_otg(0x66) 列出两位 DRVVBUS",
          "DRVVBUS" in decode_otg(0x66) and "DRVVBUS_EXT" in decode_otg(0x66))
    check("decode_otg(0x06) 不含 DRVVBUS", "DRVVBUS" not in decode_otg(0x06))

    # 6.5) watch 的线状态判读（假总线：DEBUG=0x19 给 J/K/SE0 各一段）
    b8 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x67, 0x19: 0x01, 0x04: 0x45},
                 mm={0x100: 0x40, 0x184: 0x00001001})   # LINE=J, CCS=1
    buf = io.StringIO()
    rc = cmd_watch(b8, seconds=0.01, out=buf)
    check("watch: LINE=J 印出且判「线上有设备」",
          rc == 0 and "J(01)" in buf.getvalue()
          and "出现过设备在拉" in buf.getvalue()
          and "CCS=1 的采样 1" in buf.getvalue())
    b9 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x07, 0x19: 0x00, 0x04: 0x45},
                 mm={0x100: 0x40, 0x184: 0x00001000})   # LINE=SE0, CCS=0
    buf = io.StringIO()
    cmd_watch(b9, seconds=0.01, out=buf)
    check("watch: 全程 SE0 判「线上什么都没有」",
          "SE0(00)" in buf.getvalue() and "全程 SE0" in buf.getvalue())
    b10 = FakeBus(regs={**EXPECT_ID, 0x0a: 0x07, 0x19: 0x00, 0x04: 0x45},
                  mm={0x100: 0x40, 0x184: 0x00001000})
    cmd_watch(b10, seconds=0.01, vbus=True, out=io.StringIO())
    check("watch --vbus on：OTG 0x07→0x67", b10.regs[0x0a] == 0x67)

    # 7) EHCI 解码：PPC 位置（bit4）
    buf = io.StringIO()
    b6 = FakeBus(mm={0x100: 0x40, 0x104: 0x00001001, 0x184: 0x00001000})
    info = ehci_dump(b6, out=buf)
    check("HCSPARAMS bit4=PPC（0x1001→PPC=0）", info["ppc"] == 0)
    b7 = FakeBus(mm={0x100: 0x40, 0x104: 0x00000011, 0x184: 0x00001000})
    info = ehci_dump(b7, out=io.StringIO())
    check("HCSPARAMS 0x11→PPC=1, N_PORTS=1",
          info["ppc"] == 1 and info["nports"] == 1)

    print("== 自检：%d/%d 过 ==" % (n_pass, n_pass + n_fail))
    return 0 if n_fail == 0 else 1


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if len(argv) == 2 and argv[1] == "read":
        try:
            bus = Mem(BASE_USB, SIZE_USB)
        except Exception as e:
            print("✗ /dev/mem 打不开/映射失败：%s" % e)
            print("  （要 root：sudo python3 usb_diag.py read）")
            return 2
        try:
            return cmd_read(bus)
        finally:
            bus.close()
    if len(argv) == 3 and argv[1] == "vbus" and argv[2] in ("on", "off"):
        try:
            bus = Mem(BASE_USB, SIZE_USB)
        except Exception as e:
            print("✗ /dev/mem 打不开/映射失败：%s" % e)
            return 4
        try:
            return cmd_vbus(bus, argv[2] == "on")
        finally:
            bus.close()
    if len(argv) >= 2 and argv[1] == "watch":
        # watch [秒数] [on|off]（位置随意，on/off 至多一个）
        secs, mode = 20.0, None
        for a in argv[2:]:
            if a in ("on", "off"):
                mode = (a == "on")
            else:
                try:
                    secs = float(a)
                except ValueError:
                    print("看不懂的参数：%s（用法：watch [秒数] [on|off]）" % a)
                    return 1
        try:
            bus = Mem(BASE_USB, SIZE_USB)
        except Exception as e:
            print("✗ /dev/mem 打不开/映射失败：%s" % e)
            return 2
        try:
            return cmd_watch(bus, seconds=secs, vbus=mode)
        finally:
            bus.close()
    print(__doc__.strip().splitlines()[-8])
    print("用法：usb_diag.py read | vbus on|off | watch [秒数] [on|off] | --selftest")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
