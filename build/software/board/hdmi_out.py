#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hdmi_out.py —— PYNQ-Z2 HDMI 输出通路（默认 720p60）裸 MMIO 驱动

    VDMA(MM2S,32bpp) ──> v_axi4s_vid_out ──> rgb2dvi ──> TMDS ──> 显示器
                          ▲
                    vtc_out(出同步) + axi_dynclk(像素钟)

==============================================================================
为什么要自己写而不用 PYNQ 的 HDMIOut
==============================================================================
不是不想用 —— 是**用不了**，而且理由要写清楚免得以后有人来"优化"：

  1. 我们的 overlay 是**自研 bitstream**，`pynq.Overlay(BIT)` 在这块板的 hwh 上
     会段错误（本工程的老坑）。全项目一律 `Bitstream(BIT).download()` + 裸 MMIO。
  2. PYNQ 的 `HDMIOutFrontend` 依赖 hwh 里有 `vtc_out` / `axi_dynclk` 这些**层级名**，
     还要 `pynq.lib._video` 那个 **C 扩展**。裸 MMIO 路线把这两样都绕开了。
  3. 但**语义**完全照抄 PYNQ：本文件里的寄存器偏移、MMCM 参数算法、开钟握手的
     顺序，全部来自 PYNQ 2.7 的 C 源码（见下面每处的出处），不是猜的。

==============================================================================
寄存器地图（每一个的出处都写在旁边 —— 本工程不允许"我记得是"）
==============================================================================
【axi_dynclk】双出处互相印证：
    · Digilent vivado-library 里 axi_dynclk 驱动的 ddynclk.h
    · software/pc/ref/pynq27_video_display.h（PYNQ 2.7，跑通过的那份）
      CTRL 0x00 (bit0=启动/使能)   STATUS 0x04 (bit0=时钟在跑)
      CLK_L 0x08  FB_L 0x0C  FB_H_CLK_H 0x10  DIV 0x14
      LOCK_L 0x18  FLTR_LOCK_H 0x1C  REF_CLK_FREQ 0x20（只读，=100000000）
    ★ PXL_CLK_O = MMCM 输出的 **1/5**（片内是 BUFR_DIVIDE="5"，
      axi_dynclk.vhd:210-220、268），PXL_CLK_5X_O = MMCM 原速（BUFIO）。
      ⇒ 要 74.25MHz 的像素钟，就**要按 5×74.25=371.25 去配**。
        这就是 PYNQ 里 `fHdmi` 那个参数干的事（video_display.c:146-153）。

【v_tc(发生器)】出处：software/pc/ref 同源的 bsp/vtc/xvtc_hw.h + xvtc.c
      CTL 0x000  ISR 0x004  VER 0x010
      GASIZE 0x060  GHSIZE 0x070  GVSIZE 0x074
      CTL: SW(bit0)=核使能  RU(bit1)=寄存器更新  GE(bit2)=发生器使能
    ★★ 三个使能位**复位后全是 0** ⇒ 不开就是**全黑**，而且综合时序全绿、
       什么错都不报。PYNQ 的 DisplayStart 里那条 XVtc_EnableGenerator 干的就是这个。
    ★ 时序参数（720p 的 1650/750 等）是**建 BD 时**用 C_GEN_* 写死的，
      所以这里不需要再配一遍 —— 只开使能。下面 info 会把寄存器读回来对账。

【VDMA MM2S】出处：Vivado 安装目录下 axi_vdma_v6_3 的
                    hdl/axi_vdma_v6_3_rfs.vhd（权威寄存器文件）
      DMACR 0x00  DMASR 0x04
      VSIZE 0x50  HSIZE 0x54  FRMDLY_STRIDE 0x58  STARTADDR1 0x5C
    ★ 是 **0x50 偏移不是 0x30**（S2MM 那边才是 0x30 起；照搬会写错地方）。
    ★ 32bpp：HSIZE = 宽×4，STRIDE = 宽×4，一拍一像素，低 3 字节进流。
      ★★ 像素的 G/B **交换坑**见 fill_bars() 的说明（rgb2dvi 的输入字节序），
         2026-10-01 首亮实测撞上过。
    ★ DMASR 的**错误位是 4..11**，绝不能用低 8 位当掩码 —— bit1 是 IDLE！
      （这条是 S2MM 那边踩出来的，MM2S 同样适用）

【HPD】axi_gpio，DATA 0x00 / TRI 0x04。
    ★ 默认**不去动它**：base overlay 在同一块板上就是 C_ALL_OUTPUTS=1、
      C_DOUT_DEFAULT=0x0，而且 PYNQ 的 python 侧从来不写它 —— 它是通的。
      所以我们照抄"不动"。现场要用 `--hpd 1` 单独试，别一上来就改。

==============================================================================
用法（板上，必须在 /home/xilinx/wp 下、用带 pynq 的那个 python3）
==============================================================================
    python3 hdmi_out.py selftest          # 只读寄存器，验地址/验 IP 活着（无副作用）
    python3 hdmi_out.py info              # 打印 vtc 时序寄存器 + dynclk 实配频率
    python3 hdmi_out.py run --seconds 20  # 真出图：配钟 → 开 vtc → 彩条 → 开 VDMA
    python3 hdmi_out.py pump --seconds 20 # ★ P2 帧泵演示：每 --interval 秒换一张图（验"换帧"这条路）
    python3 hdmi_out.py stop              # 停 DMA + 关 vtc + 关钟
    python3 hdmi_out.py plan              # ★ 只在 PC 上算一遍 MMCM 参数（不碰板子）
"""

import argparse
import atexit
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# dynclk 的两张 MMCM 查表（自动生成，见 software/pc/gen_dynclk_tables.py）
# ★ 放在同目录，部署时一起拷；找不到就明说，不要 fallback 成空表
# ---------------------------------------------------------------------------
try:
    from dynclk_tables import LOCK_LOOKUP, FILTER_LOOKUP_LOW
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dynclk_tables import LOCK_LOOKUP, FILTER_LOOKUP_LOW

# ===========================================================================
# 常量表
# ===========================================================================
# ---- 外设基地址：建 BD 时用 assign_bd_address 定死的（add_hdmi_out.tcl 第 9 节）
#      ★ 不靠"我记得"，selftest 会按**内容**验它们（读 dynclk 的 REF_CLK_FREQ
#        必须 == 100000000，读 vtc 的 VER 必须非 0）——
#        地址错了内容就对不上，这比打印一句"地址=xxx"强得多。
BASE_VDMA = 0x43C00000
BASE_DYNCLK = 0x43C10000
BASE_VTC = 0x43C20000
BASE_HPD = 0x43C30000

# ---- axi_dynclk ----
DC_CTRL = 0x00
DC_STATUS = 0x04
DC_CLK_L = 0x08
DC_FB_L = 0x0C
DC_FB_H_CLK_H = 0x10
DC_DIV = 0x14
DC_LOCK_L = 0x18
DC_FLTR_LOCK_H = 0x1C
DC_REF_CLK_FREQ = 0x20
DC_BIT_START = 0        # CTRL bit0：写 1 启动
DC_BIT_RUNNING = 0      # STATUS bit0：时钟在跑

# ---- MMCM 参数算法里的位常量（video_display.h:67-70）----
CLK_BIT_WEDGE = 13
CLK_BIT_NOCOUNT = 12
ERR_CLKCOUNTCALC = 0xFFFFFFFF
ERR_CLKDIVIDER = (1 << CLK_BIT_WEDGE) | (1 << CLK_BIT_NOCOUNT)

# ---- v_tc ----
VTC_CTL = 0x000
VTC_ISR = 0x004
VTC_VER = 0x010
VTC_GASIZE = 0x060
VTC_GHSIZE = 0x070
VTC_GVSIZE = 0x074
VTC_CTL_SW = 0x1        # 核使能
VTC_CTL_RU = 0x2        # 寄存器更新
VTC_CTL_GE = 0x4        # 发生器使能

# ---- VDMA MM2S ----
VD_DMACR = 0x00
VD_DMASR = 0x04
VD_VSIZE = 0x50
VD_HSIZE = 0x54
VD_STRIDE = 0x58
VD_STARTADDR1 = 0x5C
VD_STARTADDR2 = 0x60        # ★ 2026-10-23 双缓冲第二块（C_NUM_FSTORES=4）
VD_FRMPTR = 0x28            # 帧指针：低 5 位=DesiredFrame（写），[20:16]=ActiveFrame（读）
VD_CR_RS = 1 << 0
VD_CR_RESET = 1 << 2
VD_CR_SYNCEN = 1 << 3       # genlock 使能（=IP tooltip 里的"Slave follows the Master"开关）
VD_CR_FRMCNTEN = 1 << 4
VD_CR_GENLCKSRC = 1 << 7    # genlock 源：1=内部（XAXIVDMA_INTERNAL_GENLOCK=1），0=外部
VD_CR_IOC_IRQEN = 1 << 12   # 帧完成中断使能（我们用它当"帧真的在跑"的证据）
VD_CR_ERR_IRQEN = 1 << 14
# ★ PYNQ 官方 video/dma.py 的 MM2S 启动值，逐位照抄（第十二例，见 start() 的说明）。
#   与 0x5001 的差别 = bit3(SYNCEN) + bit7(内部genlock源) + bit16（PYNQ 每次都带）。
VD_CR_START_PYNQ = 0x00011089
VD_SR_HALTED = 1 << 0
VD_SR_IDLE = 1 << 1
VD_SR_ERR = 1 << 3
VD_SR_IOCIRQ = 1 << 12
VD_SR_ERR_MASK = 0xFF0      # bits 4..11 —— ★ 绝不能用低位掩码，bit1 是 IDLE

# ---- axi_gpio ----
GPIO_DATA = 0x00
GPIO_TRI = 0x04

# ---- 720p60 时序（出处：PYNQ VMODE_1280x720，video_modes.h:86-99）----
#      ★ 这几个数**必须**和建 BD 时写进 vtc 的 C_GEN_* 一致，否则画面会飘/撕裂。
#        hps/hpe/hmax 是"含同步头的绝对位置"，hmax 是**最后一个**计数（总长=+1）。
MODE_720P = dict(
    label="1280x720@60Hz", width=1280, height=720,
    hps=1390, hpe=1430, hmax=1649, hpol=1,
    vps=725, vpe=730, vmax=749, vpol=1,
    freq=74.25,
)

DISPLAY_HDMI = 1  # PYNQ 的 fHdmi=1 ⇒ 要求 dynclk 出 5× 像素钟（片内 BUFR 再 /5）


def log(msg):
    print(msg)
    sys.stdout.flush()


# ===========================================================================
# MMCM 参数算法 —— 逐行照抄 video_display.c:376-511，一个字都没敢改
# ===========================================================================
def clk_divider(divide):
    """video_display.c:389 DisplayClkDivider"""
    if divide < 1 or divide > 128:
        return ERR_CLKDIVIDER
    if divide == 1:
        return 0x1041
    high_time = divide // 2
    if divide & 1:
        low_time = high_time + 1
        out = 1 << CLK_BIT_WEDGE
    else:
        low_time = high_time
        out = 0
    out |= 0x03F & low_time
    out |= 0xFC0 & (high_time << 6)
    return out


def clk_count_calc(divide):
    """video_display.c:376 DisplayClkCountCalc"""
    d = clk_divider(divide)
    if d == ERR_CLKDIVIDER:
        return ERR_CLKCOUNTCALC
    return (0xFFF & d) | ((d << 10) & 0x00C00000)


def clk_find_params(freq, ref_mhz=100.0):
    """video_display.c:462 DisplayClkFindParams —— 暴力搜最优。

    ★ 这个函数**假设参考钟 100MHz**（C 源码里就是这么写死的），
      所以调它之前必须先验 dynclk 的 REF_CLK_FREQ 寄存器 == 100000000。
    ★ 返回 (clkdiv, fbmult, maindiv, 实际频率)。实际频率多半 != 目标频率
      （MMCM 只能凑），PYNQ 也是这么认的（dispPtr->pxlFreq = clkMode.freq）。
    """
    best_error = 2000.0
    best = None
    for cur_div in range(1, 11):
        # VCO 下限 600MHz、上限 1200MHz（C 源码注释里写明的两个约束）
        min_fb = cur_div * 6
        max_fb = cur_div * 12
        if max_fb > 64:
            max_fb = 64
        cur_clk_mult = (ref_mhz / cur_div) / freq
        for cur_fb in range(min_fb, max_fb + 1):
            cur_clk_div = int((cur_clk_mult * cur_fb) + 0.5)
            if cur_clk_div < 1:
                cur_clk_div = 1
            cur_freq = ((ref_mhz / cur_div) / cur_clk_div) * cur_fb
            cur_error = abs(cur_freq - freq)
            if cur_error < best_error:
                best_error = cur_error
                best = (cur_clk_div, cur_fb, cur_div, cur_freq)
    return best


def clk_find_reg(clkdiv, fbmult, maindiv):
    """video_display.c:417 DisplayClkFindReg —— 算 6 个要写进 dynclk 的值"""
    if fbmult < 2 or fbmult > 64:
        return None
    clk0L = clk_count_calc(clkdiv)
    if clk0L == ERR_CLKCOUNTCALC:
        return None
    clkFBL = clk_count_calc(fbmult)
    if clkFBL == ERR_CLKCOUNTCALC:
        return None
    divclk = clk_divider(maindiv)
    if divclk == ERR_CLKDIVIDER:
        return None
    lockL = LOCK_LOOKUP[fbmult - 1] & 0xFFFFFFFF
    fltr_lockH = (LOCK_LOOKUP[fbmult - 1] >> 32) & 0x000000FF
    fltr_lockH |= (FILTER_LOOKUP_LOW[fbmult - 1] << 16) & 0x03FF0000
    return dict(clk0L=clk0L, clkFBL=clkFBL, clkFBH_clk0H=0,
                divclk=divclk, lockL=lockL, fltr_lockH=fltr_lockH)


# ===========================================================================
# VDMA MM2S 通道
# ===========================================================================
class VdmaMm2s(object):
    """MM2S（DDR → 流）。

    ★ 纪律照抄 vdma_capture.py（那边踩出来的坑这里一样会踩）：
      · **先 reset 再 allocate**：防"野 DMA"—— 上一个进程崩着退出时 atexit 没跑，
        VDMA 还带着**已释放的**缓冲地址在跑，新进程 allocate 到的页立刻被覆盖。
      · atexit 里停 DMA（硬件不随进程退出而停）。
      · 错误位用 4..11 的掩码，W1C 清。
    """

    def __init__(self, mmio, verbose=True):
        self.mmio = mmio
        self.verbose = verbose
        self._armed = False  # 是否注册过 atexit（只注册一次）

    def read(self, off):
        return self.mmio.read(off)

    def write(self, off, val):
        self.mmio.write(off, val)

    def reset(self):
        """软复位。**必须在 allocate 之前调**（野 DMA 防护）。"""
        self.write(VD_DMACR, VD_CR_RESET)
        t0 = time.time()
        while self.read(VD_DMACR) & VD_CR_RESET:
            if time.time() - t0 > 1.0:
                log("WARN: VDMA reset 位 1 秒了还没自清（DMACR=0x%08X）" % self.read(VD_DMACR))
                break
            time.sleep(0.001)

    def status(self):
        sr = self.read(VD_DMASR)
        return sr, bool(sr & VD_SR_HALTED), bool(sr & VD_SR_ERR), sr & VD_SR_ERR_MASK

    def clear_errors(self):
        """DMASR 是 W1C —— 写 1 清对应位。"""
        self.write(VD_DMASR, VD_SR_ERR_MASK | VD_SR_ERR | VD_SR_IOCIRQ)

    def stop(self):
        """位清零 + 等 halted。异常吞掉：atexit 里不能再抛。"""
        try:
            cr = self.read(VD_DMACR) & ~VD_CR_RS
            self.write(VD_DMACR, cr)
            t0 = time.time()
            while not (self.read(VD_DMASR) & VD_SR_HALTED):
                if time.time() - t0 > 0.5:
                    break
                time.sleep(0.001)
        except Exception:
            pass

    def start(self, phys_addr, hsize, vsize, stride, syncen=None, dmacr=None,
              phys_addr2=None):
        """配参数并启动，**然后自证"帧真的在跑"**。

        ★★ 2026-10-01 第十二例：默认值/顺序**照抄 PYNQ 官方 video/dma.py 的 MM2S**。
          触发它的是上一轮实测：0x5001（不带 genlock 那两位）在板上等 30 秒
          **一帧都没有**，而 DMASR 既不 halted 也不报错（"在跑但什么都不干"）。
          三个独立证据都指向 PYNQ 那一套值：

          ① `pynq.lib.video` 在**同型号板（PYNQ-Z2）的 base overlay** 上出 HDMI 是
             官方支持、带 demo、720p 在列的（docs: pynqz2_base_overlay）；它的
             MM2S 启动写的就是 `0x00011089`。
          ② base 的 VDMA 参数与我们的 axi_vdma_out **逐项相同**（`c_include_
             internal_genlock=1` / `c_mm2s_genlock_mode=1` / `c_mm2s_sof_enable=1`
             / `c_use_mm2s_fsync=0`）⇒ 那个值对我们也成立。
          ③ IP 自己的 GUI tooltip（axi_vdma_v6_3.tcl:283）写明 DMACR[3]=1 的语义是
             "**Slave follows the Master**" —— [3] 是"打开 genlock 跟随"的开关；
             驱动常量 `XAXIVDMA_INTERNAL_GENLOCK=1` ⇒ [7]=1 = 源选**内部**。
             `0x11089` = RS|[3]|[7]|IOC|bit16，正是这套配置该写的值。

          ⇒ 旧默认 0x5001 **缺 [3][7][16]**。旧说明担心的"[3]=1 会让 MM2S-only
             一直等主"，与①（PYNQ 在同参数 MM2S 上写的就是这个值）相矛盾；而
             0x5001 的实测结果本来就是"0 帧"。**默认已改成 PYNQ 原值。**

        ★ 顺序也照抄 PYNQ：尺寸/地址 → RS → **运行中再写一次 VSIZE** → 帧指针=0。
          （PYNQ 的 reload() 只在 running 时写 0x50，且每次换帧都写 —— 暗示
            0x50 是按帧取用的，所以前后各写一次，两边都不漏。）

        参数（对照实验用，平时都不用传）：
          syncen : None=PYNQ 原值（默认）; True=旧 --syncen（只加 bit3）;
                   False=旧的裸值（0x5001 那一版，实测 0 帧）
          dmacr  : 直接指定完整 DMACR（最高优先级，--dmacr 走这里）
        """
        self.clear_errors()
        self.write(VD_HSIZE, hsize)
        self.write(VD_STRIDE, stride)
        self.write(VD_STARTADDR1, phys_addr)
        if phys_addr2 is not None:
            # ★ 2026-10-23：双缓冲第二块（必须在 VSIZE 触发参数锁存之前写）。
            self.write(VD_STARTADDR2, phys_addr2)
        self.write(VD_VSIZE, vsize)

        if dmacr is not None:
            cr, src = dmacr, "--dmacr 指定"
        elif syncen is True:
            cr = VD_CR_RS | VD_CR_IOC_IRQEN | VD_CR_ERR_IRQEN | VD_CR_SYNCEN
            src = "旧 --syncen（只有 bit3）"
        elif syncen is False:
            cr = VD_CR_RS | VD_CR_IOC_IRQEN | VD_CR_ERR_IRQEN
            src = "旧裸值（无 genlock，实测 0 帧）"
        else:
            cr, src = VD_CR_START_PYNQ, "PYNQ 官方值"
        self.write(VD_DMACR, cr)

        if not self._armed:
            atexit.register(self.stop)
            self._armed = True

        # PYNQ start() 的后半段：等 halted 落下 → 运行中再写 VSIZE → 帧指针=0
        t0 = time.time()
        running = True
        while self.read(VD_DMASR) & VD_SR_HALTED:
            if time.time() - t0 > 0.5:
                running = False
                log("      WARN: RS 写了 0.5s halted 还没落下（DMASR=0x%08X）"
                    % self.read(VD_DMASR))
                break
            time.sleep(0.001)
        if running:
            self.write(VD_VSIZE, vsize)
            self.write(VD_FRMPTR, self.read(VD_FRMPTR) & ~0x1F)

        if self.verbose:
            log("      VDMA: DMACR=0x%08X（%s） VSIZE=%d HSIZE=%d STRIDE=%d ADDR=0x%08X"
                % (cr, src, vsize, hsize, stride, phys_addr))

    def report_wild(self):
        """看是不是有**上一个进程留下的野 DMA** 还在跑。

        ★ 本工程血的教训：脚本崩着退出时 atexit 不执行，而 VDMA 是 PL 硬件、
          不随进程退出而停 —— 它会带着**已释放的** CMA 地址继续读，
          下一个进程 allocate 到的页立刻被覆盖（段错误 / 结果乱 / 帧率骤降）。
          所以**任何验收的第一步都该是查野 DMA**。
        """
        cr = self.read(VD_DMACR)
        sr = self.read(VD_DMASR)
        halted = bool(sr & VD_SR_HALTED)
        if (cr & VD_CR_RS) and not halted:
            log("  [!] 野 DMA：DMACR.RS=1 且 DMASR.halted=0 —— "
                "有个**不是本进程**启动的 VDMA 还在跑（DMACR=0x%08X DMASR=0x%08X）"
                % (cr, sr))
            log("      处理：先 reset 再 allocate，别直接 allocate")
            return False
        log("  [OK ] 无野 DMA：DMACR.RS=%d  halted=%d" % (bool(cr & VD_CR_RS), halted))
        return True

    def wait_frame(self, timeout_s=0.5):
        """等一帧完成（DMASR.IOCIRQ 置位）。这是"帧真的在跑"的**硬证据**。

        返回 (ok, 实际等了多久, DMASR)。ok=False 时把 DMASR 一并返回，
        方便把"卡住"和"报错"分开 —— 这两种的修法完全不同。
        """
        t0 = time.time()
        while True:
            sr = self.read(VD_DMASR)
            if sr & VD_SR_IOCIRQ:
                self.write(VD_DMASR, VD_SR_IOCIRQ)  # W1C 清掉，下次还能再等
                return True, time.time() - t0, sr
            if time.time() - t0 > timeout_s:
                return False, time.time() - t0, sr
            time.sleep(0.001)


# ===========================================================================
# 主控
# ===========================================================================
# ---------------------------------------------------------------------------
# ★★ v_tc 的隔离探测（2026-10-01，M12 根因的直接产物，详见 docs/known_issues.md §6.6）
#
# ★★★ 2026-10-01 深夜·第十一例：**以下整段的前提已被推翻（保留做历史）。**
#   真根因 = `rst_pixel/aux_reset_in` 原接**恒定 0**（该脚低有效）⇒ peripheral_
#   aresetn 恒 0 ⇒ vtc_out/resetn 被钉死 ⇒ 对它的每个访问都 SLVERR -> SIGBUS。
#   修好 aux 复位（新增恒 1 网 + 重综合 + 重下 bit）之后 v_tc **完整可用**：
#   VER=0x0602000C 读得到、CTL 0x0 -> 0x7（SW|RU|GE）写得进、dynclk LOCK=1。
#   ⇒ "该实例没有 AXI 控制寄存器 / 发生器自由跑 / SIGBUS 是预期" **三条全部作废**；
#     v_tc 就是一颗正常的 v_tc，发生器**必须**软件开（CTL.SW|RU|GE）。
#   隔离子进程探测本身仍然保留（SIGBUS 是信号、try/except 抓不住，这个理由没变）。
#
# 为什么**必须**隔离、而且**必须**允许它是"死的"：
#   `vtc_out`（0x43C20000）是 `.hwh` 里 16 个从机中**唯一**一个对**每个**访问都
#   回 SLVERR 的 —— 而 SIGBUS 是**信号不是异常**，`try/except` 抓不住，
#   在主进程里读一下，进程当场没。所以探测只能放进隔离子进程。
#
#   **它是"死的"，这是预期状态，不是故障。** 根因（高置信度）：
#   这个 v_tc 实例**没有可访问的 AXI 控制寄存器** ——
#     · 网表顶层属性 `C_CONTROL="0"`
#     · `.xci` 里 `C_DETECT_EN=0`、`C_SYNC_EN=0`、`C_GENERATE_EN=1`
#     · 而 `C_HAS_AXI4_LITE="1"` ⇒ 建 BD 时自动化照样给它挂了 AXI 总线、
#       分配了 64KB 槽
#   => IPIF 每次译码都判无效 => 由核驱动的 `ipif_Error` 置 1
#   => 直接变成 `rresp[1]`/`bresp[1]` = AXI4-Lite `2'b01` = **SLVERR**
#   => ARM 外部中止 => **SIGBUS**（整个 64KB 槽，任何地址都一样）。
#
#   门级网表实锤（ov5640_m1a_vtc_out_0_sim_netlist.v）：
#     1336-1343 行  s_axi_rresp_i_reg[1] .CE(s_axi_rresp_i) .D(ipif_Error) .Q(s_axi_rresp)
#                   ⇒ SLVERR 的源头在**核**里，不在 IPIF 里（ipif_Error 是 IPIF 的**输入**）
#     1047-1061 行  \s_axi_bresp_i[1]_i_1  LUT3 .I0(ipif_Error)  ⇒ 写也是它
#     1040 行       LUT1 INIT 2'h1 → s_axi_aresetn = ~aresetn
#                   ⇒ IPIF 输入 aresetn=1 才是"不在复位"，而它与交叉开关同源 ⇒ 必然已释放
#     399-495 行    axi_lite_ipif **没有 aclken 端口** ⇒ 卡在时钟使能上也不可能
#   ⇒ 把"IPIF 自身不工作"和"复位/时钟使能"两条路都排除了，剩下的只能是核的
#     `C_CONTROL=0`（第 1458 行起是加密段 `pragma protect`，核内部读不到，到此为止）。
#
#   **旧说法作废**：曾写过"核心复位挂在死掉的像素钟域（rst_pixel/dcm_locked）"，
#   它已被**实测否掉** —— `vtc_after_clk_test.py` 配好 dynclk（MMCM 已锁、
#   PXL_CLK 在跑）之后**依然** SIGBUS，rc=1。
#
#   ⇒ 于是：发生器按 `C_GEN_*` 参数**自由跑**，HDMI 通路**不需要、也不可能**
#     用软件去开它。软件侧要做的只是"别碰它"（闸门 = `HdmiOut.vtc_axi_ok`）。
#   ⇒ 真伪的**唯一判据是显示器**：彩条出来 = 假设成立；全黑 = 假设不成立，
#     走硬件路（BD 里给 v_tc 打开 Control 或换个时序源 + 重综合）。
#
#   探测本身要解决的两件事：(a) 别让 SIGBUS 杀掉主进程（隔离子进程）；
#   (b) 把结果落进 `vtc_axi_ok`，作为**所有** v_tc 寄存器访问的闸门。
# ---------------------------------------------------------------------------
VTC_OFF_VER = 0x10          # v_tc 版本寄存器，纯只读


def vtc_probe_isolated(timeout_s=15.0):
    """在**独立子进程**里读一次 v_tc 的 VER。返回 (rc, 文本)。

    rc = 0   读到（配钟之后应当如此）
    rc = -7  SIGBUS —— **配钟之前这是预期状态**（见上面那段注释）
    rc = 9   超时/其它（子进程没回、或被杀）
    """
    code = ("import sys, pynq;"
            "m = pynq.MMIO(0x%08X, 0x1000);"
            "sys.stdout.write('%%08X\\n' %% m.read(0x%X))" % (BASE_VTC, VTC_OFF_VER))
    try:
        r = subprocess.run([sys.executable, '-c', code],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return 9, '(%.0fs 没回 —— 挂住了)' % timeout_s
    out = r.stdout.decode('utf-8', 'replace').strip()
    return r.returncode, out


class HdmiOut(object):
    def __init__(self, verbose=True):
        from pynq import MMIO
        self.verbose = verbose
        self.mmio_vdma = MMIO(BASE_VDMA, 0x10000)
        self.mmio_clk = MMIO(BASE_DYNCLK, 0x10000)
        self.mmio_vtc = MMIO(BASE_VTC, 0x10000)
        self.mmio_hpd = MMIO(BASE_HPD, 0x10000)
        # ★ v_tc 的 AXI 从机**只在隔离探针确认活着之后**才允许碰。
        #   为什么：它现在对 64KB 槽内每个访问都回 SLVERR -> SIGBUS，而 SIGBUS 是
        #   **信号不是异常**，try/except 拦不住 —— 碰一下就当场杀进程（包括 atexit 里）。
        #   None = 还没探过 -> 一律按"不能碰"处理（保守方向）。
        self.vtc_axi_ok = None
        self.vdma = VdmaMm2s(self.mmio_vdma, verbose=verbose)
        self.buf = None
        self._started = False
        atexit.register(self.cleanup)

    # ---------------- dynclk ----------------
    def clk_ref_hz(self):
        return self.mmio_clk.read(DC_REF_CLK_FREQ)

    def clk_running(self):
        return bool(self.mmio_clk.read(DC_STATUS) & (1 << DC_BIT_RUNNING))

    def clk_program(self, freq_mhz):
        """把 MMCM 配到 freq_mhz。注意这个 freq 是 **MMCM 原速**，
        像素钟 = freq/5（片内 BUFR）。要 74.25 的像素钟就传 371.25。"""
        pick = clk_find_params(freq_mhz)
        if pick is None:
            raise RuntimeError("MMCM 参数搜不到（freq=%s）" % freq_mhz)
        clkdiv, fbmult, maindiv, actual = pick
        regs = clk_find_reg(clkdiv, fbmult, maindiv)
        if regs is None:
            raise RuntimeError("MMCM 寄存器算不出来（clkdiv=%d fbmult=%d maindiv=%d）"
                               % (clkdiv, fbmult, maindiv))
        self.mmio_clk.write(DC_CLK_L, regs["clk0L"])
        self.mmio_clk.write(DC_FB_L, regs["clkFBL"])
        self.mmio_clk.write(DC_FB_H_CLK_H, regs["clkFBH_clk0H"])
        self.mmio_clk.write(DC_DIV, regs["divclk"])
        self.mmio_clk.write(DC_LOCK_L, regs["lockL"])
        self.mmio_clk.write(DC_FLTR_LOCK_H, regs["fltr_lockH"])
        if self.verbose:
            log("      dynclk: M=%d  VCO分频=%d  CLKOUT分频=%d  → MMCM %.4f MHz"
                % (fbmult, maindiv, clkdiv, actual))
            log("              像素钟 = %.4f MHz（MMCM/5），目标 %.4f，偏差 %.3f%%"
                % (actual / 5.0, freq_mhz / 5.0,
                   abs(actual / 5.0 - freq_mhz / 5.0) / (freq_mhz / 5.0) * 100.0))
            log("              寄存器: CLK_L=0x%08X FB_L=0x%08X DIV=0x%08X "
                "LOCK_L=0x%08X FLTR_LOCK_H=0x%08X"
                % (regs["clk0L"], regs["clkFBL"], regs["divclk"],
                   regs["lockL"], regs["fltr_lockH"]))
        return actual

    def clk_enable(self, timeout_s=1.0):
        """video_display.c:174-183 的开钟握手，逐句照抄（带超时，绝不无限等）。"""
        self.mmio_clk.write(DC_CTRL, 0)
        t0 = time.time()
        while self.clk_running():
            if time.time() - t0 > timeout_s:
                raise RuntimeError("dynclk 关了之后 STATUS.bit0 没掉下来（停不下来）")
            time.sleep(0.001)
        self.mmio_clk.write(DC_CTRL, (1 << DC_BIT_START))
        t0 = time.time()
        while not self.clk_running():
            if time.time() - t0 > timeout_s:
                raise RuntimeError(
                    "dynclk 启动后 STATUS.bit0 一直不置位（MMCM 没锁上）—— "
                    "多半是 MMCM 参数不合适")
            time.sleep(0.001)

    def clk_disable(self):
        try:
            self.mmio_clk.write(DC_CTRL, 0)
        except Exception:
            pass

    # ---------------- v_tc ----------------
    # ★★ 2026-10-01 起，**所有** v_tc 寄存器访问都必须先看 `self.vtc_axi_ok`。
    #    根因（高置信度，网表 + 实测双重支持）：`vtc_out` 这个 v_tc 实例
    #    **没有可访问的 AXI 控制寄存器**，于是 IPIF 每次译码都判无效 ->
    #    核驱动 `ipif_Error=1` -> `rresp[1]`/`bresp[1]`=1 = **SLVERR**
    #    -> ARM 外部中止 -> **SIGBUS**，对这个 64KB 槽里**每一个**地址都如此。
    #    证据：①网表顶层属性 `C_CONTROL="0"`、`.xci` 里 `C_DETECT_EN=0`/`C_GENERATE_EN=1`；
    #          ②`vtc_after_clk_test.py` rc=1 = 配好像素钟后**依然** SIGBUS ⇒
    #            旧说法"核心复位挂在死像素钟域"（known_issues.md §6）**已被实测否掉**；
    #          ③`.hwh` 的 16 个从机里**只有**它死，而且整槽 64KB 全死；
    #          ④SLVERR 的源头只能是核：IPIF 自身没有 aclken 端口、aresetn 必然已释放
    #            （网表 axi_lite_ipif 399-495 / slave_attachment 1336、1047 行）。
    #    ⇒ 这是**预期状态，不是故障**：`C_GENERATE_EN=1` ⇒ 发生器按 `C_GEN_*`
    #      参数**自由跑**，HDMI 通路根本不需要、也不可能用软件去开它。
    #    ⇒ 唯一还能证伪它的是**显示器**：彩条出来 = 对；全黑 = 假设不成立，走硬件路
    #      （在 BD 里给 v_tc 打开 Control 或换时序源 + 重综合）。
    #    ★ 为什么必须这样防：SIGBUS 是**信号**，Python 的 try/except 抓不住，
    #      碰一下进程当场没 —— 连 atexit 里的 cleanup 都会被它打断。
    def vtc_regs(self):
        """读 v_tc 寄存器；**AXI 没被探针证明活着就返回 None**（碰它会 SIGBUS）。"""
        if not self.vtc_axi_ok:
            return None
        return dict(CTL=self.mmio_vtc.read(VTC_CTL),
                    ISR=self.mmio_vtc.read(VTC_ISR),
                    VER=self.mmio_vtc.read(VTC_VER),
                    GASIZE=self.mmio_vtc.read(VTC_GASIZE),
                    GHSIZE=self.mmio_vtc.read(VTC_GHSIZE),
                    GVSIZE=self.mmio_vtc.read(VTC_GVSIZE))

    def vtc_enable(self):
        """开发生器：SW|RU|GE 三位一起置。

        ★ 这三位**复位后都是 0**，不开就是全黑（而且什么错都不报）。
          PYNQ 那边是 XVtc_Enable/RU/EnableGenerator 三个调用，效果一样。
        ★ 但本板这个 v_tc 实例**没有 AXI 控制寄存器**（见 vtc_regs 上方那段），
          它的发生器是**自由跑**的 —— 所以正常情况下这里会直接跳过并返回 False。
        """
        if not self.vtc_axi_ok:
            log("      vtc: ★跳过开使能 —— AXI 从机每次访问都 SIGBUS（探针没过）")
            log("           发生器按 C_GEN_* 参数**自由跑**，本来就不需要软件开它；")
            log("           硬写下去会当场 SIGBUS 杀掉进程（信号，try/except 拦不住）。")
            return False
        ctl = self.mmio_vtc.read(VTC_CTL)
        self.mmio_vtc.write(VTC_CTL, ctl | VTC_CTL_SW | VTC_CTL_RU | VTC_CTL_GE)
        back = self.mmio_vtc.read(VTC_CTL)
        if not (back & VTC_CTL_GE):
            raise RuntimeError("vtc 的 GE 位写不进去（回读 CTL=0x%08X）" % back)
        if self.verbose:
            log("      vtc: CTL 0x%08X -> 0x%08X（SW|RU|GE）" % (ctl, back))
        return True

    def vtc_disable(self):
        # ★ 探针没过就**一格都不碰**：cleanup()/cmd_stop 都会走到这儿，
        #   在这儿 SIGBUS 就是"进程被打死"——cleanup 的收尾逻辑全部作废。
        if not self.vtc_axi_ok:
            return
        try:
            ctl = self.mmio_vtc.read(VTC_CTL)
            self.mmio_vtc.write(VTC_CTL, ctl & ~VTC_CTL_GE)
        except Exception:
            pass

    # ---------------- HPD ----------------
    def hpd_drive(self, level):
        """把 HPD 那一根线拉到 level 并回读。

        ★ 默认**不驱动**它：base overlay 在同一块板上就是 C_ALL_OUTPUTS=1、
          C_DOUT_DEFAULT=0x0，而且 PYNQ 的 python 侧从来没人写它 —— 说明"不动"
          就是通的。所以这里只作为**现场旋钮**（`--hpd 1` / `--hpd 0`）存在：
          显示器不给画面时，先试试把它拉高 —— HPD 恒 0 时有些显示器直接认作
          "没有信号源"，连 EDID 都不谈。
        ★ 只写 DATA 不写 TRI：C_ALL_OUTPUTS=1 时 TRI 被忽略，
          照抄 base overlay 的行为（它也只设 C_DOUT_DEFAULT）。
        """
        self.mmio_hpd.write(GPIO_DATA, 1 if level else 0)
        back = self.mmio_hpd.read(GPIO_DATA)
        log("      HPD: 写 %d → 回读 DATA=0x%08X（TRI=0x%08X，C_ALL_OUTPUTS=1 时无意义）"
            % (level, back, self.mmio_hpd.read(GPIO_TRI)))
        if (back & 1) != (1 if level else 0):
            log("      WARN: HPD 回读和写进去的不一致 —— 这个口可能不是全输出配置")

    # ---------------- 自检 / 信息 ----------------
    def selftest(self, require_vtc=False):
        """**只读**，不碰时钟不碰 DMA。验的是"地址对不对 + IP 活没活"。

        ★ 判据是**内容**不是"能不能读"：地址错的时候 MMIO 读通常返回 0 或
          全 F 而不报错 —— 只看"读得到"是查不出地址错的。

        ★★ v_tc 那一项是**隔离探测**（理由见本文件 `vtc_probe_isolated` 上面那段
           注释），而它现在的结论是 **SIGBUS 属预期** —— 该 v_tc 实例没有可访问的
           AXI 控制寄存器（详见 vtc_regs 上方那段），发生器自由跑、不需要软件开。
           ⇒ **SIGBUS 一律不判 FAIL**（`require_vtc` 只影响措辞），
             否则冷启动跑 ./hdmi_shot.sh 会被一个已知的预期状态永久拒之门外。
           ⇒ 探针的结果同时落进 `self.vtc_axi_ok`，它是**所有** v_tc 寄存器访问的闸门。
        """
        ok = True
        ref = self.clk_ref_hz()
        good = (ref == 100000000)
        log("  [%s] dynclk@0x%08X  REF_CLK_FREQ = %d（期望 100000000）"
            % ("OK " if good else "FAIL", BASE_DYNCLK, ref))
        ok &= good

        rc, out = vtc_probe_isolated()
        if rc == 0:
            ver = -1
            try:
                ver = int(out.strip(), 16)
            except ValueError:
                pass
            good = (ver != 0 and ver != 0xFFFFFFFF and ver != -1)
            self.vtc_axi_ok = bool(good)
            log("  [%s] vtc@0x%08X     VER = 0x%08X（非 0 即活着）"
                % ("OK " if good else "FAIL", BASE_VTC,
                   ver if ver >= 0 else 0))
            if not good:
                log("        地址错时会读回 0 / 全 F —— 见 hdmi_shot.sh 底部的判读表")
            ok &= good
        elif rc == -7:
            # ★★ 2026-10-01 重写：这**不是** FAIL，是**预期状态**。
            #   新根因（高置信度）：`vtc_out` 这个 v_tc 实例没有可访问的 AXI 控制
            #   寄存器（网表 C_CONTROL="0" / .xci C_DETECT_EN=0 / C_GENERATE_EN=1），
            #   IPIF 每次译码都判无效 -> 核驱动 ipif_Error=1 -> rresp[1]=1 = SLVERR
            #   -> SIGBUS，整槽 64KB 全死、与像素钟无关。
            #   旧说法"核心复位挂在死像素钟域"**已被实测否掉**：配好钟之后依然 SIGBUS
            #   （vtc_after_clk_test.py rc=1），known_issues.md §6 那套作废。
            #   ⇒ 发生器自由跑，软件不碰它；真伪交给显示器（彩条出来就是对的）。
            self.vtc_axi_ok = False
            log("  [-- ] vtc@0x%08X     SIGBUS（**预期状态，不是故障**）" % BASE_VTC)
            log("        该 v_tc 实例没有可访问的 AXI 控制寄存器 ⇒ 每次访问都")
            log("        SLVERR -> SIGBUS，与像素钟/复位域/地址都无关（已实测）。")
            log("        发生器按 C_GEN_* 参数自由跑 ⇒ 软件**跳过**它即可。")
            log("        真伪判据是显示器：彩条出来 = 假设成立；全黑 = 走硬件路。")
            if not require_vtc:
                log("        （本次是配钟前调用，结论不变。）")
        else:
            log("  [FAIL] vtc@0x%08X     探测 rc=%d  %s" % (BASE_VTC, rc, out))
            ok = False

        sr = self.vdma.read(VD_DMASR)
        good = (sr != 0xFFFFFFFF)
        log("  [%s] vdma@0x%08X    DMASR = 0x%08X" % ("OK " if good else "FAIL", BASE_VDMA, sr))
        ok &= good

        hpd = self.mmio_hpd.read(GPIO_DATA)
        log("  [-- ] hpd@0x%08X     DATA = 0x%08X（默认不去动它，见文件头）"
            % (BASE_HPD, hpd))

        self.vdma.report_wild()
        return ok

    def info(self):
        v = self.vtc_regs()
        log("  --- v_tc 寄存器 ---")
        if v is None:
            log("      **跳过**：v_tc 的 AXI 从机对每个访问都回 SLVERR -> SIGBUS，读不了。")
            log("      这是**预期状态**（该实例没有 AXI 控制寄存器，发生器自由跑），")
            log("      见本文件 vtc_regs 上方那段。**判据是显示器，不是寄存器。**")
        else:
            log("      CTL    = 0x%08X   SW=%d RU=%d GE=%d"
                % (v["CTL"], bool(v["CTL"] & VTC_CTL_SW), bool(v["CTL"] & VTC_CTL_RU),
                   bool(v["CTL"] & VTC_CTL_GE)))
            log("      VER    = 0x%08X" % v["VER"])
            log("      GASIZE = 0x%08X   （发生器有效尺寸）" % v["GASIZE"])
            log("      GHSIZE = 0x%08X   （发生器帧水平总长）" % v["GHSIZE"])
            log("      GVSIZE = 0x%08X   F0 总行数 = %d（期望 750；"
                "★ 编码没实机验证过，这里只报不判）" % (v["GVSIZE"], v["GVSIZE"] & 0x1FFF))
        log("  --- dynclk ---")
        log("      REF_CLK_FREQ = %d" % self.clk_ref_hz())
        log("      CTRL=0x%08X STATUS=0x%08X 时钟在跑=%s"
            % (self.mmio_clk.read(DC_CTRL), self.mmio_clk.read(DC_STATUS),
               self.clk_running()))
        log("      实配: CLK_L=0x%08X FB_L=0x%08X DIV=0x%08X"
            % (self.mmio_clk.read(DC_CLK_L), self.mmio_clk.read(DC_FB_L),
               self.mmio_clk.read(DC_DIV)))
        log("  --- VDMA ---")
        sr, halted, err, errmask = self.vdma.status()
        log("      DMACR=0x%08X DMASR=0x%08X halted=%s err=%s errbits=0x%03X"
            % (self.vdma.read(VD_DMACR), sr, halted, err, errmask))

    def cleanup(self):
        """进程怎么退都要把硬件停干净（野 DMA 的唯一防线）。"""
        try:
            if self._started:
                self.vdma.stop()
                self.vtc_disable()
                self.clk_disable()
        except Exception:
            pass
        for name in ("buf",):
            b = getattr(self, name, None)
            if b is not None:
                try:
                    b.freebuffer()
                except Exception:
                    pass
                setattr(self, name, None)


# ===========================================================================
# ★★ P2：帧泵 —— 游戏主循环的 HDMI 直出（把 BGR 图持续换到屏幕上）
# ===========================================================================
def _phys_of(buf):
    """CMA 缓冲的物理地址（PYNQ 各版本属性名不同，两处都试）——与 cmd_run 同款。"""
    p = getattr(buf, "device_address", None)
    if p is None:
        p = buf.physical_address
    return p


class FramePump(HdmiOut):
    """把任意尺寸的 BGR 图**换**到显示器上（720p60）。

    为什么需要"泵"这一层：`cmd_run` 出的是**一张静态图** —— 一次 start 之后
    VDMA 会把同一帧**反复重发**（板上实测：IOC 稳定 60 次/秒、画面保持住，
    "保持"就是这个机制本身）。游戏主循环要的是**换画面**：每次 V3 预测画出
    一张新图就换上去。换法两条路：

      mode='stst'（默认）：新图写进另一块缓冲 → 停 DMA（等 halted）→ 改
          STARTADDR1 → 按 start() 的完整序列重启。走的是**板上已实跑过**的
          那条路；代价是换帧瞬间 ~1 帧的停机（显示器上未必看得出来）。
      mode='hot'（实验档）：不停机，直接改 STARTADDR1，赌"引擎每帧边界会重读
          它"。成立 = 零停机零闪烁；不成立 = **画面不再更新**（假死）。
          ⇒ 别拿它当默认，先用 `pump --mode hot` 在板上验过再说。
      mode='live'（★ 2026-10-01 夜加，直播档）：**单缓冲、DMA 全程不停** ——
          start() 之后所有 show() 都往同一块缓冲**原地写**，一个 VDMA 寄存器
          都再也不碰。屏幕**时刻有画面**（引擎 60fps 反复重发同一帧），代价是
          写入瞬间可能有一道撕裂带（没有黑闪、没有停机）。"时刻看到摄像头
          实拍"这个需求只有这一档给得了：stst 每次换帧都要停一下（现场实测
          "屏幕一闪一闪的"），hot 是假死赌局（已判死：引擎交替扫新旧缓冲 →
          时间混色）。

    ★ 打包格式与 fill_bars **完全一样**：(R<<16)|(B<<8)|G
      （rgb2dvi 的输入字节序，第十三例，2026-10-01 首亮实锤）—— 别在这里改。
    ★ 记账：每次 show() 返回 ms_resize/ms_pack/ms_switch/ms_total，
      并顺手做一次帧流检查（DMASR.IOC 有没有来）——**静默失败最贵**。
    """

    def __init__(self, verbose=True, interp="linear"):
        HdmiOut.__init__(self, verbose=verbose)
        # 换帧会高频调 vdma.start()，把它的逐次打印关掉（自己记账行负责报数）
        self.vdma = VdmaMm2s(self.mmio_vdma, verbose=False)
        self.width = MODE_720P["width"]
        self.height = MODE_720P["height"]
        self.hsize = self.width * 4
        self.bufs = []
        self.idx = 0
        self.mode = "stst"
        # ★ 2026-10-23 双缓冲（live2）：front=VDMA 正在读的那块，back=软件正在写的那块
        self.front = 0
        self.back = 1
        # ★ 2026-10-01 夜：缩放插值可调 —— "linear"（默认，质量够现场看）/
        #   "nearest"（板上实测 75ms -> 13.5ms/帧，直播档 fps 不够时换这一档）。
        if interp not in ("linear", "nearest"):
            raise ValueError("interp 只认 'linear'/'nearest'（给的是 %r）" % (interp,))
        self.interp = interp
        self.n_show = 0
        self.n_to = 0          # 帧流检查超时次数（不是每次都意味着错，但必须记账）
        self.ms = {k: [] for k in ("resize", "pack", "switch", "total")}
        self._small = None     # 缩放目标缓冲（start() 里预分配，免得每次推帧 malloc）

    # ---------------- 生命周期 ----------------
    def start(self, mode="stst"):
        """配钟 → 开 vtc → 起缓冲 → 开 VDMA（序列照抄 cmd_run，逐条都是板上验过的）。

        mode='live' 时只分配**一块**缓冲，start 完就再也不动 VDMA 寄存器。
        """
        if mode not in ("stst", "hot", "live", "live2"):
            raise ValueError("未知换帧模式 %r（可选 stst / hot / live / live2）" % (mode,))
        if not self.selftest():
            raise RuntimeError("HDMI 自检没过（bit 没下？地址错？）—— 帧泵不启动")
        self.vdma.stop()
        self.vdma.reset()                    # ★ 先 reset 再 allocate（野 DMA 铁律）
        self.clk_program(MODE_720P["freq"] * 5.0 if DISPLAY_HDMI else MODE_720P["freq"])
        self.clk_enable()
        if not self.selftest(require_vtc=True):
            raise RuntimeError("配钟后自检仍有真故障（dynclk / vdma）")
        self.vtc_enable()                    # 自带闸门：探针没过就跳过
        # ★ 2026-10-01：这里原来漏了 import numpy —— 帧泵这条路**从没真跑过**，
        #   第一次上板就 NameError: name 'np' is not defined（其余函数都是局部 import，
        #   只有本函数忘了）。教训同上："没跑过的代码 = 没有的代码"。
        import numpy as np
        from pynq import allocate
        n_bufs = 1 if mode == "live" else 2      # live2/stst/hot 都是两块
        self.bufs = [allocate(shape=(self.height, self.width), dtype=np.uint32)
                     for _ in range(n_bufs)]
        # 缓冲清零，两个作用：
        #   ① 第一次 show() 之前屏幕上显示的是 **0（黑）** 而不是上次残留的内存；
        #   ② 打包只写 uint8 视图的第 0/1/2 字节（[G,B,R]，见 show()），第 4 字节
        #      （高 8 位）**靠这次清零后不再被谁写** ⇒ 恒 0（与 fill_bars 的排法一致，
        #      首亮实测过的那条；若哪天有人往缓冲里写整字，这条就不成立了，别踩）。
        for b in self.bufs:
            b[:] = 0
        self._small = np.empty((self.height, self.width, 3), np.uint8)
        self.mode = mode
        self.idx = 0
        self.front = 0
        self.back = 1
        self.vdma.start(_phys_of(self.bufs[0]), self.hsize, self.height, self.hsize,
                        phys_addr2=(_phys_of(self.bufs[1])
                                    if mode == "live2" else None))
        self._started = True
        log("      帧泵就绪：%dx%d %s（各 %.1f MB），换帧模式=%s"
            % (self.width, self.height,
               "单缓冲" if mode == "live" else "双缓冲",
               self.height * self.hsize / 1e6, mode))
        if mode == "live":
            log("      live 档：DMA 全程不停，show() 原地写同一块 —— "
                "屏幕时刻有画面（撕裂带有、黑闪没有）")
        if mode == "live2":
            log("      live2 档（2026-10-23）：双缓冲 + Park 指针换页 —— "
                "整帧写后台缓冲，帧边界切 RdFrmPtrRef；无黑闪、无半帧撕裂")

    def close(self):
        """停干净（幂等）。atexit 也会走到这儿。"""
        try:
            self.vdma.stop()
            self.vtc_disable()
            self.clk_disable()
        except Exception:
            pass
        for b in self.bufs:
            try:
                b.freebuffer()
            except Exception:
                pass
        self.bufs = []

    def cleanup(self):
        # HdmiOut.__init__ 注册的是 self.cleanup —— 覆盖成带双缓冲释放的版本
        self.close()

    # ---------------- 换帧 ----------------
    def show(self, img, timeout_s=0.2):
        """把一张 BGR 图推上屏（尺寸随便，内部缩放到 720p）。返回记账 dict。

        live 档：写进唯一那块缓冲（引擎正在扫的那块），**不切、不停、不等帧**；
        flow 每 30 次推帧查一回（None = 本次没查），ms_switch 基本为 0。

        ★★ 2026-10-01 第一次真跑后重写（实测 396ms/次 -> 现在 ~115ms/次）：
          板载 CPU 逐笔量的（工具 `hdmi_pump_bench.py`，1440x810 -> 1280x720）：
            缩放：INTER_AREA 197ms / **INTER_LINEAR 75ms** / INTER_NEAREST 13.5ms
            打包：旧表达式（每像素一串 uint32 临时数组）168ms
                  -> 写 uint8 视图三笔 **39ms**，且**直接写进目标 CMA 缓冲**再省一笔拷贝
          取 LINEAR（比 AREA 快 2.6 倍，质量够现场看）；NEAREST 是"还要更快"的档
          （真到那一步再改这一个参数，别的地方不用动）。
        """
        import cv2
        import numpy as np
        if not self.bufs:
            raise RuntimeError("帧泵没启动（先 start()）")
        t0 = time.perf_counter()
        if img.shape[0] != self.height or img.shape[1] != self.width:
            small = cv2.resize(img, (self.width, self.height), dst=self._small,
                               interpolation=(cv2.INTER_LINEAR if self.interp == "linear"
                                              else cv2.INTER_NEAREST))
        else:
            small = img
        t1 = time.perf_counter()
        # live：**永远写同一块**（bufs[0]，引擎正在扫的这块，原地更新）；
        # live2：写**后台**那块，写完在帧边界切 park 指针；
        # stst/hot：写另一块，下一步再切过去
        if self.mode == "live":
            nxt = self.bufs[0]
        elif self.mode == "live2":
            nxt = self.bufs[self.back]
        else:
            nxt = self.bufs[self.idx ^ 1]
        # ★ (R<<16)|(B<<8)|G 的小端排法：内存低->高 = [G, B, R, X]（X=高 8 位=0，
        #   见 start() 的清零）。与 fill_bars 同一字节序（第十三例）——别改。
        v = nxt.view(np.uint8).reshape(self.height, self.width, 4)
        v[:, :, 0] = small[:, :, 1]          # G
        v[:, :, 1] = small[:, :, 0]          # B
        v[:, :, 2] = small[:, :, 2]          # R
        t2 = time.perf_counter()
        if self.mode == "live2":
            # 后台缓冲已写完整 -> 帧边界切 Park 指针；等一帧再复用旧前台
            self._park_switch(self.back)
            time.sleep(0.020)
            self.front, self.back = self.back, self.front
        elif self.mode != "live":
            self._switch(nxt)
            self.idx ^= 1
        t3 = time.perf_counter()
        if self.mode == "live":
            # ★ live 没有"换帧"这个天然对齐点，帧流自证改成**抽样**：第 1 次推帧
            #   之后每 30 次查一回 IOC。flow=None = 这次没查（不许把它当成"超时"）。
            ok = None
            if self.n_show % 30 == 0:
                ok, _dt, _sr = self.vdma.wait_frame(timeout_s=timeout_s)
                if not ok:
                    self.n_to += 1
        else:
            ok, _dt, _sr = self.vdma.wait_frame(timeout_s=timeout_s)
            if not ok:
                self.n_to += 1
        self.n_show += 1
        d = dict(ms_resize=(t1 - t0) * 1e3, ms_pack=(t2 - t1) * 1e3,
                 ms_switch=(t3 - t2) * 1e3,
                 ms_total=(t3 - t0) * 1e3, flow=ok)
        for k in ("resize", "pack", "switch", "total"):
            self.ms[k].append(d["ms_" + k])
            if len(self.ms[k]) > 200:
                self.ms[k].pop(0)
        return d

    def show_rect(self, img, rect):
        """★★ 2026-10-13：**局部原地更新**（仅 live 档、仅无缩放图）。把 rect 区域
        单独写进当前单缓冲——写入微秒级，撞上显示引擎扫线的概率≈0，光标这类小块
        改动**高帧率刷也看不见撕裂**（整帧 show() 在高速率下每帧一道撕裂带）。
        rect=(x0,y0,x1,y1)=img 像素坐标；需要缩放/非 live 档 → 退回整帧 show()。"""
        import cv2
        import numpy as np
        if self.mode not in ("live", "live2") or not self.bufs:
            return self.show(img)
        if img.shape[0] != self.height or img.shape[1] != self.width:
            return self.show(img)          # 有缩放：整帧路径（合成画面本来就同尺寸）
        x0, y0, x1, y1 = (int(v) for v in rect)
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(self.width, x1)
        y1 = min(self.height, y1)
        if x1 <= x0 or y1 <= y0:
            return dict(ms_resize=0.0, ms_pack=0.0, ms_switch=0.0,
                        ms_total=0.0, flow=None)
        t0 = time.perf_counter()
        _buf = (self.bufs[self.front] if self.mode == "live2" else self.bufs[0])
        v = _buf.view(np.uint8).reshape(self.height, self.width, 4)
        sub = img[y0:y1, x0:x1]
        v[y0:y1, x0:x1, 0] = sub[:, :, 1]      # G
        v[y0:y1, x0:x1, 1] = sub[:, :, 0]      # B
        v[y0:y1, x0:x1, 2] = sub[:, :, 2]      # R
        t1 = time.perf_counter()
        self.n_show += 1
        # ★ 不查 IOC：局部更新不承担帧流健康（抽样仍由整帧 show() 负责）。
        return dict(ms_resize=0.0, ms_pack=(t1 - t0) * 1e3, ms_switch=0.0,
                    ms_total=(t1 - t0) * 1e3, flow=None)

    def show_rects(self, img, rects):
        """★★ 2026-10-23（差距 2）：**一次取缓冲视图，批量写多个变化矩形**。

        为什么需要它：直播局部更新一帧可能有 30~50 个碎矩形（相机噪声），
        逐个 `show_rect()` 每次都要 `self.bufs[0].view(...).reshape(...)`
        重建整幅视图（1280x720x4），A9 上这一笔就够呛 —— 实测推帧仍 ~90ms。
        这里只取一次视图，循环里只做三笔切片赋值；写入的像素量与逐块相同。
        非 live 档 / 尺寸不符 -> 退回整帧 show（行为正确，只是没省）。
        """
        import numpy as np
        if self.mode not in ("live", "live2") or not self.bufs:
            h = self.show(img)
            return dict(ms_resize=0.0, ms_pack=h.get("ms_total", 0.0),
                        ms_switch=0.0, ms_total=h.get("ms_total", 0.0),
                        flow=h.get("flow"))
        if img.shape[0] != self.height or img.shape[1] != self.width:
            h = self.show(img)
            return dict(ms_resize=0.0, ms_pack=h.get("ms_total", 0.0),
                        ms_switch=0.0, ms_total=h.get("ms_total", 0.0),
                        flow=h.get("flow"))
        t0 = time.perf_counter()
        _buf = (self.bufs[self.front] if self.mode == "live2" else self.bufs[0])
        v = _buf.view(np.uint8).reshape(self.height, self.width, 4)
        n = 0
        for rect in (rects or ()):
            x0, y0, x1, y1 = (int(q) for q in rect)
            x0 = max(0, x0)
            y0 = max(0, y0)
            x1 = min(self.width, x1)
            y1 = min(self.height, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            sub = img[y0:y1, x0:x1]
            v[y0:y1, x0:x1, 0] = sub[:, :, 1]      # G
            v[y0:y1, x0:x1, 1] = sub[:, :, 0]      # B
            v[y0:y1, x0:x1, 2] = sub[:, :, 2]      # R
            n += 1
        t1 = time.perf_counter()
        self.n_show += n
        return dict(ms_resize=0.0, ms_pack=(t1 - t0) * 1e3, ms_switch=0.0,
                    ms_total=(t1 - t0) * 1e3, flow=None, n_rects=n)

    def _park_switch(self, idx):
        """★ 2026-10-23 live2：帧边界换页 —— 只改 PARK_PTR_REG.RdFrmPtrRef。

        DMACR.Circular_Park=0（start() 写的 0x11089 就是 Park 模式），引擎会在
        **当前帧结束**时改读 RdFrmPtrRef 指定的帧缓冲（STARTADDR1/2 都已在
        start() 里配好）。不停机、不黑闪，整帧内容完整 —— 没有半帧撕裂。
        """
        cur = self.vdma.read(VD_FRMPTR) & ~0x1F
        self.vdma.write(VD_FRMPTR, cur | (int(idx) & 0x1F))

    def _switch(self, buf):
        if self.mode in ("live", "live2"):
            # live 档**唯一**的纪律：start 之后一个 VDMA 寄存器都不许碰。
            # 谁要是从这里改地址/停机，屏幕立刻黑一下 —— 那正是这一档要消灭的东西。
            raise RuntimeError("live/live2 档是原地/帧边界换页 —— 不许走 _switch")
        phys = _phys_of(buf)
        if self.mode == "hot":
            # 实验档：不停机直接改地址（成不成立要板上验证；不成立=画面假死）
            self.vdma.write(VD_STARTADDR1, phys)
            self.vdma.write(VD_FRMPTR, self.vdma.read(VD_FRMPTR) & ~0x1F)
        else:
            self.vdma.stop()                 # RS=0 + 等 halted（≤0.5s）
            self.vdma.start(phys, self.hsize, self.height, self.hsize)

    def summary(self):
        import numpy as np
        if not self.ms["total"]:
            return "帧泵：一次都没推过"
        def m(k):
            return float(np.mean(self.ms[k])) if self.ms[k] else 0.0
        return ("帧泵[%s]：推帧 %d 次（帧流超时 %d），缩放 %.1f + 打包 %.1f + "
                "切换 %.1f = **%.1fms/次**"
                % (self.mode, self.n_show, self.n_to, m("resize"), m("pack"),
                   m("switch"), m("total")))


# ===========================================================================
# 测试图：彩条
# ===========================================================================
def fill_bars(buf, width, height):
    """写 8 条竖彩条 + 底部四分之一做**渐变色标**。

    ★★ 2026-10-01 首亮实测 + 源码实锤：**打包时要交换 G/B**。
      链路：内存字（低 3 字节）→ VDMA(32bpp) → v_axi4s_vid_out(24bit)
            → rgb2dvi → TMDS。
      Digilent rgb2dvi 的输入字节序是 {R=[23:16], **B=[15:8], G=[7:0]**}
      —— 中字节是 blue、低字节是 green：
        ip_repo/ip/rgb2dvi/src/rgb2dvi.vhd 第 215~217 行
            pDataOut(2) <= vid_pData(23 downto 16);  -- red is channel 2
            pDataOut(1) <= vid_pData(7 downto 0);    -- green is channel 1  ★
            pDataOut(0) <= vid_pData(15 downto 8);   -- blue is channel 0   ★
      PYNQ 的 base overlay 为此专门夹了一级硬件 color_swap
      （instance `video_hdmi_out_frontend_color_swap_0`，就在 vid_out 与
        rgb2dvi 之间 —— 见其 base.hwh）；**我们的 BD 没有这一级**，
      所以在软件侧补：写 (R << 16) | (**B** << 8) | **G**。
      首亮实测（未交换时）：黄↔品红、绿↔蓝两两互换，白/黑/红/青不动 ——
      与这条字节序规则逐条吻合。
      ★ 若哪天 BD 里也加进 color_swap（照抄 base 的接法），这行要改回
        (r<<16)|(g<<8)|b。**只改这一个函数**，别在别处乱动。
    """
    import numpy as np
    colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
              (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]
    # ★ G/B 交换 —— 理由见 docstring（rgb2dvi 的输入字节序）
    words = [(r << 16) | (b << 8) | g for (r, g, b) in colors]
    bars_h = height * 3 // 4
    bw = max(1, width // len(words))
    flat = buf.reshape(-1)
    for i, w in enumerate(words):
        x0 = i * bw
        x1 = width if i == len(words) - 1 else (i + 1) * bw
        # 每个颜色一次整块赋值（比逐像素快几个数量级）
        block = np.full((bars_h, x1 - x0), w, dtype=np.uint32)
        flat[: bars_h * width].reshape(bars_h, width)[:, x0:x1] = block
    # 底部渐变：从黑到白，方便看是否有**行错位/撕裂**
    ramp = np.tile(np.arange(width, dtype=np.uint32) * 255 // max(1, width - 1),
                   (height - bars_h, 1))
    flat[bars_h * width:].reshape(height - bars_h, width)[:] = \
        (ramp << 16) | (ramp << 8) | ramp
    return buf


# ===========================================================================
# 命令行
# ===========================================================================
def cmd_plan(args):
    """纯 PC 上算一遍参数 —— 不碰板子，先看清 MMCM 会给什么数。"""
    freq = args.freq
    pick = clk_find_params(freq)
    if pick is None:
        log("搜不到参数")
        return 1
    clkdiv, fbmult, maindiv, actual = pick
    regs = clk_find_reg(clkdiv, fbmult, maindiv)
    log("目标 MMCM = %.4f MHz（= 像素钟 %.4f × 5）" % (freq, freq / 5.0))
    log("搜到: maindiv=%d fbmult=%d clkdiv=%d  → 实际 %.4f MHz（像素钟 %.4f）"
        % (maindiv, fbmult, clkdiv, actual, actual / 5.0))
    log("      偏差 %.4f%%    VCO = %.2f MHz（须在 600~1200）"
        % (abs(actual - freq) / freq * 100.0, 100.0 / maindiv * fbmult))
    if regs is None:
        log("寄存器算不出来")
        return 1
    for k in ("clk0L", "clkFBL", "clkFBH_clk0H", "divclk", "lockL", "fltr_lockH"):
        log("      %-13s = 0x%08X" % (k, regs[k]))
    return 0


def cmd_selftest(args):
    d = HdmiOut(verbose=True)
    with_clk = getattr(args, "with_clk", False)
    log("=== HDMI 自检（%s）===" % ("配钟 + 全验" if with_clk else "只读，不碰时钟/DMA"))
    if with_clk:
        log("    --with-clk：会先配 dynclk（HDMI 通路的像素钟，**必须**配），再验 v_tc。")
        log("    v_tc 那一项现在的预期结果就是 SIGBUS（该实例没有 AXI 控制寄存器），")
        log("    所以它**不再**是门禁；详见本文件 vtc_regs 上方那段。")
        d.clk_program(MODE_720P["freq"] * 5.0 if DISPLAY_HDMI else MODE_720P["freq"])
        d.clk_enable()
        log("    dynclk 已启动，STATUS.bit0=%d" % d.clk_running())
    else:
        log("    注意：本模式不配 dynclk，所以只验地址图（vtc 那一项必然 SIGBUS）。")
        log("    出图前的门禁用：hdmi_out.py selftest --with-clk")
    ok = d.selftest(require_vtc=with_clk)
    log("=== %s ===" % ("全部通过" if ok else "★有 FAIL★"))
    return 0 if ok else 1


def cmd_info(args):
    d = HdmiOut(verbose=True)
    d.info()
    return 0


def cmd_stop(args):
    d = HdmiOut(verbose=True)
    d.vdma.stop()
    d.vtc_disable()          # ★ 探针没过时内部直接返回（碰它会 SIGBUS，见 vtc_regs 上方）
    d.clk_disable()
    log("已停：VDMA 停 + dynclk 关（vtc 若不可访问就跳过，不碰）")
    sr, halted, err, errmask = d.vdma.status()
    log("复查 DMASR=0x%08X halted=%s err=%s" % (sr, halted, err))
    return 0 if halted else 1


def cmd_run(args):
    d = HdmiOut(verbose=True)
    import numpy as np

    mode = MODE_720P
    log("=== HDMI 出图：%s ===" % mode["label"])

    # ---- 0) 前置：地址/内容自检 + 野 DMA ----
    if not d.selftest():
        log("ERROR: 自检没过，不往下走（地址错的时候继续只会浪费一次上板窗口）")
        return 9

    # ---- 1) 野 DMA 必须**先**清掉，再 allocate ----
    d.vdma.stop()
    d.vdma.reset()

    # ---- 2) 配 dynclk（按 5× 像素钟去配，片内 BUFR 再 /5）----
    d.clk_program(mode["freq"] * 5.0 if DISPLAY_HDMI else mode["freq"])
    d.clk_enable()
    log("      dynclk 已启动，STATUS.bit0=%d" % d.clk_running())

    # ---- 2.5) ★ 配钟后再跑一遍自检 ----
    # ★ 2026-10-01 改口径：这一步原来在 v_tc SIGBUS 时 `return 9`（当时以为
    #   "配钟后还 SIGBUS 就说明根因假设不成立"）。实测就是 SIGBUS，而新根因是
    #   **这个 v_tc 实例没有 AXI 控制寄存器**（见 vtc_regs 上方那段）——
    #   配钟**本来就救不了**它，把闸门压在这儿等于永远过不去。
    #   现在 v_tc 不再参与 ok 判定 ⇒ 这里能返回 False 的只剩**真故障**
    #   （dynclk 没起 / VDMA 读不回来），那才是该拦的。
    log("      --- 配钟后复查（v_tc 的 SIGBUS 已是预期状态，不拦）---")
    if not d.selftest(require_vtc=True):
        log("ERROR: 配钟后自检仍有**真**故障（dynclk / vdma）—— 不继续")
        return 9

    # ---- 3) 开 vtc 发生器 ----
    # ★ 这次调用**自带闸门**：探针没证明它活着（vtc_axi_ok 不为 True）就直接跳过，
    #   因为寄存器访问是 SIGBUS 信号，试一下进程就没了。
    #   该实例的 `C_GENERATE_EN=1` ⇒ 发生器按 `C_GEN_*` 参数自由跑，不需要软件开。
    d.vtc_enable()

    # ---- 3b) HPD（只在显式要求时动；默认碰都不碰，见 hpd_drive 的说明）----
    if args.hpd is not None:
        d.hpd_drive(args.hpd)
        log("      注意：HPD 是给显示器的『我在』信号。拉高后显示器才会去读 EDID。")
        log("            改完要复跑的话，先 power-cycle 显示器，或重新拔插一次线。")
    else:
        log("      HPD: 保持复位默认（未驱动）—— 与 base overlay 行为一致")

    # ---- 4) 分配帧缓冲 + 画彩条 ----
    hsize = mode["width"] * 4
    vsize = mode["height"]
    from pynq import allocate
    d.buf = allocate(shape=(vsize, mode["width"]), dtype=np.uint32)
    phys = getattr(d.buf, "device_address", None)
    if phys is None:
        phys = d.buf.physical_address
    log("      帧缓冲: %d 字节  物理地址=0x%08X（HSIZE=%d VSIZE=%d）"
        % (vsize * hsize, phys, hsize, vsize))
    fill_bars(d.buf, mode["width"], mode["height"])
    log("      彩条已写入（8 条竖色 + 底部渐变）")

    # ---- 5) 开 VDMA ----
    # ★ 默认走 PYNQ 官方 MM2S 序列（第十二例，见 VdmaMm2s.start 的说明）；
    #   对照实验用 --dmacr / --syncen 覆盖。
    d.vdma.start(phys, hsize, vsize, hsize, syncen=args.syncen, dmacr=args.dmacr)
    d._started = True

    # ---- 6) 硬证据：等一帧走完 ----
    ok, dt, sr = d.vdma.wait_frame(timeout_s=args.frame_timeout)
    if ok:
        log("      ★帧在跑★ 第一帧 %.1f ms 完成（DMASR=0x%08X）" % (dt * 1000, sr))
    else:
        sr, halted, err, errmask = d.vdma.status()
        log("      ★帧没走★ 等了 %.0f ms：DMASR=0x%08X halted=%s err=%s errbits=0x%03X"
            % (dt * 1000, sr, halted, err, errmask))
        log("      判读：halted=1 且 errbits=0 → VDMA 没启动（参数没写进去？）")
        log("            halted=0 且 errbits=0 → **在跑但帧不来**。默认值已是 PYNQ")
        log("              官方值（0x11089），它要也不行的话逐项排除：")
        log("              ① 先 --dmacr 0x5001 对照（旧值，实测同样 0 帧）")
        log("              ② 查像素侧：v_tc 是否在产时序 / vid_out 的 tready")
        log("              ③ gen_clken 布线（BD 里钉在常量 1 —— 与官方参考接线不同）")
        log("            errbits!=0 → 看 xaxivdma_hw.h 的 DMASR 位定义对号。")

    # ---- 7) 保持 N 秒（期间每 0.5s 数一次帧，确认不是只走了一帧）----
    if args.seconds > 0:
        log("      保持 %d 秒，期间数帧……" % args.seconds)
        t_end = time.time() + args.seconds
        frames = 0
        last = time.time()
        while time.time() < t_end:
            got, _, _ = d.vdma.wait_frame(timeout_s=0.5)
            if got:
                frames += 1
            now = time.time()
            if now - last >= 1.0:
                log("        t=%.0fs  累计帧=%d" % (args.seconds - (t_end - now), frames))
                last = now
        fps = frames / float(args.seconds)
        log("      ★帧率 ≈ %.1f fps（720p60 理想 60）" % fps)
        if frames == 0:
            log("      ★一帧都没走完 ⇒ 见上面的判读分支")

    if args.hold:
        log("      进入保持模式（Ctrl-C 退出；退出时会自动停 DMA/vtc/时钟）")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            log("      收到 Ctrl-C")
    return 0


def cmd_pump(args):
    """★★ P2：帧泵演示 —— 每 N 秒换一张图，肉眼看"换帧成不成立、闪不闪"。

    真用途是给 `ball_game.py --v3-hdmi` 做前置验证：先证明"这个板子上换帧
    这条路是通的"，再让游戏循环走它。演示图故意做成 **1440x810**（与
    v3_scan.render 同尺寸）—— 顺带把"缩放到 720p"那条路径也测了。
    """
    d = FramePump(verbose=True)
    log("=== HDMI 帧泵演示（%s，换帧模式=%s）===" % (MODE_720P["label"], args.mode))
    try:
        d.start(mode=args.mode)
    except Exception as e:
        log("ERROR: 帧泵启动失败：%s" % e)
        log("       先跑 hdmi_out.py selftest 确认 bit/地址；不行就把输出留档")
        return 9
    import cv2
    import numpy as np
    colors = [(40, 40, 220), (40, 200, 40), (220, 90, 40), (235, 235, 235)]
    names = ["RED", "GREEN", "BLUE", "WHITE"]
    t_end = time.time() + args.seconds
    k = 0
    while time.time() < t_end:
        img = np.full((810, 1440, 3), 35, np.uint8)      # 与 v3_scan.render 同尺寸
        cv2.rectangle(img, (90, 90), (1350, 720), colors[k % 4], -1)
        cv2.putText(img, "HDMI pump #%d" % (k + 1), (150, 380),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.6, (25, 25, 25), 6)
        cv2.putText(img, "mode=%s  %s" % (args.mode, names[k % 4]), (150, 470),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (25, 25, 25), 3)
        st = d.show(img)
        log("  %s #%d（%-5s）：缩放 %.1f + 打包 %.1f + 切换 %.1f = %.1fms%s"
            % ("推帧" if args.mode == "live" else "换帧",
               k + 1, names[k % 4], st["ms_resize"], st["ms_pack"],
               st["ms_switch"], st["ms_total"],
               {None: "", True: "", False: "  ★帧流超时"}[st["flow"]]))
        k += 1
        if time.time() < t_end:
            time.sleep(args.interval)
    log("  " + d.summary())
    if args.mode == "live":
        log("  判读：屏幕上的颜色每 %.1fs 变一次**且全程不黑**（撕裂带=写入瞬间，"
            "属预期）；整段 %ds 一次停机都没有 = live 档成立。"
            % (args.interval, args.seconds))
    else:
        log("  判读：屏幕上颜色每 %.1fs 换一次 = 换帧成立；整段 %ds 没有黑闪 = 切换干净。"
            % (args.interval, args.seconds))
    if args.hold:
        log("  进入保持（Ctrl-C 退出；退出时自动停干净）")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    d.close()
    log("  已停：DMA/vtc/时钟 收干净，缓冲已释放")
    return 0


def main():
    ap = argparse.ArgumentParser(description="PYNQ-Z2 HDMI 输出驱动（720p）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("selftest", help="只读自检（不碰时钟/DMA）")
    p.add_argument("--with-clk", action="store_true",
                   help="先配 dynclk 再验 v_tc（顺带把像素钟起起来；"
                        "v_tc 的 SIGBUS 是预期状态，不是门禁 —— 见 vtc_regs 上方那段）")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("info", help="打印 vtc/dynclk/VDMA 寄存器现状")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("stop", help="停 DMA + 关 vtc + 关钟")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("plan", help="只在 PC 上算 MMCM 参数（不碰板子）")
    p.add_argument("--freq", type=float, default=MODE_720P["freq"] * 5.0,
                   help="MMCM 频率 MHz（默认 371.25 = 720p 像素钟×5）")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="真出图")
    p.add_argument("--seconds", type=int, default=15, help="保持并数帧的秒数（0=不等）")
    p.add_argument("--hold", action="store_true", help="数完帧后一直保持画面")
    p.add_argument("--syncen", action="store_true", default=None,
                   help="旧的 genlock 开关（只加 bit3）。默认已走 PYNQ 官方值"
                        "（0x11089，含 bit3/bit7），一般不用它")
    p.add_argument("--dmacr", type=lambda s: int(s, 0), default=None,
                   help="直接指定 MM2S 的完整 DMACR 值（对照实验用，如 0x5001）")
    p.add_argument("--hpd", type=int, choices=(0, 1), default=None,
                   help="拉高/拉低 HPD 那根线（默认不碰）。显示器认不出信号源时试 1")
    p.add_argument("--frame-timeout", type=float, default=0.5,
                   help="等第一帧的超时秒数")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("pump", help="★ P2 帧泵演示：反复换帧（游戏 HDMI 直出的前置验证）")
    p.add_argument("--mode", choices=("stst", "hot", "live"), default="stst",
                   help="换帧方式：stst=停机-改地址-重启（默认，板上已验过）；"
                        "hot=不停机直接改地址（实验档：赌引擎每帧边界重读 STARTADDR，"
                        "不成立=画面假死 —— 已判死：时间混色）；"
                        "live=★直播档（单缓冲原地写，DMA 全程不停 —— "
                        "屏幕时刻有画面、只有撕裂带没有黑闪，游戏上屏用这一档）")
    p.add_argument("--seconds", type=int, default=20, help="演示总秒数")
    p.add_argument("--interval", type=float, default=2.0, help="每几秒换一张图")
    p.add_argument("--hold", action="store_true", help="演示完保持画面（Ctrl-C 退出）")
    p.set_defaults(func=cmd_pump)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
