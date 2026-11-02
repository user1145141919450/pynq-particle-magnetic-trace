# -*- coding: utf-8 -*-
"""vtc_after_clk_test.py —— 判决实验：`vtc_out` (M12 @0x43C20000) 的 SIGBUS
到底是"译码缺失"（要重建），还是"被自己的像素时钟域复位钉住"（只要先配钟）。

背景（见 docs/known_issues.md 第六节，2026-10-01 从门级网表逐位解出）：

  BD HDL `ov5640_m1a.v` 第 3782-3788 行，`vtc_out` 是全项目**唯一**一个
  核心复位挂在像素时钟域上的从机：

      ov5640_m1a_rst_pixel_0 rst_pixel (
          .dcm_locked  (axi_dynclk_LOCKED_O),      // MMCM 锁了才放行
          .slowest_sync_clk(axi_dynclk_PXL_CLK_O));

  而 `axi_dynclk` 没编程 => PXL_CLK 无时钟 => MMCM 不锁 => `rst_pixel` 永不出复位
  => `vtc_out.resetn` 恒 0 => v_tc 核心里"地址译码 -> ipif_RdAck/WrAck"整条死掉。

  于是（v_tc 的 `slave_attachment` 自带超时，所以**不挂死，而是报错**）：
      IPIF 不应答 -> `INCLUDE_DPHASE_TIMER` 到顶 -> 伪造一次应答
      -> 把寄存的 `ipif_Error` 当 `rresp` 送出去 -> AXI4-Lite `2'b01` = **SLVERR**
      -> ARM 外部中止 -> **SIGBUS**

  网表实锤（`ov5640_m1a_vtc_out_0_sim_netlist.v`）：
      1336-1343 行  `s_axi_rresp_i_reg[1] .CE(s_axi_rresp_i) .D(ipif_Error) .Q(s_axi_rresp)`
      285/340 行    `cs_out_i[0/1]_i_1` 的 `D = aresetn & cs_decode & ~RdAck & ~WrAck`
                    => `aresetn` 是片选寄存器的**硬与项**，低就是全零片选
      842 行        `I_DECODER (.s_axi_awready_0(\\INCLUDE_DPHASE_TIMER.dpto_cnt_reg ))`
                    => 那个 [9:0] 输入不是地址，是超时计数器

所以本实验就三步，一步不多：

  ① 读 0x43C20000            -> 预期 SIGBUS（复现原现象）
  ② **只**编程 axi_dynclk     -> 让 PXL_CLK 起来、MMCM 锁上、rst_pixel 释放
  ③ 再读 0x43C20000          -> 若变正常，根因坐实

判据：
  · ①SIGBUS + ③正常  => **根因坐实**，"要重建"是误判；直接跑 ./hdmi_shot.sh 出图
  · ①SIGBUS + ③SIGBUS => **假设被否**，回到"重建 + 交换 MI 顺序"那条线
  · ①就正常           => 板子这次本来就活着（比如之前已经配过钟），本实验对根因**无判别力**

★ 安全性（比只读探测还小心）：
  · **只碰 `axi_dynclk` 一个窗口**（0x43C10000，已实测正常应答）。
  · 主进程**从不 mmap VTC 窗口** —— 两次读都在**独立子进程**里做
    （SIGBUS 是信号不是异常，try/except 拦不住）。
  · 读的是 v_tc 的 `VER`(0x10) 和 `CTL`(0x00)，**纯只读，一个寄存器都不写**。
  · 不提权、不停服务、不碰相机、不下 bit、不动 VDMA。

用法（板上，root + 登录 shell，脚本在 /home/xilinx/wp）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u vtc_after_clk_test.py"

  --freq 371.25   MMCM 原速 MHz（像素钟 = /5 = 74.25），默认 371.25
  --child-read    内部用，不经命令行调用

退出码：0 = 根因坐实；1 = 假设被否；2 = 本来就没病（无判别力）；9 = 前提不满足
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FPGA_STATE = '/sys/class/fpga_manager/fpga0/state'

BASE_VTC = 0x43C20000          # vtc_out (M12)，本次的嫌疑对象
BASE_DYNCLK = 0x43C10000       # 只碰这一个

OFF_CTL = 0x00                 # v_tc 控制寄存器（只读即可）
OFF_VER = 0x10                 # v_tc 版本寄存器（纯只读）

DC_CTRL = 0x00
DC_STATUS = 0x04
DC_CLK_L = 0x08
DC_FB_L = 0x0C
DC_FB_H_CLK_H = 0x10
DC_DIV = 0x14
DC_LOCK_L = 0x18
DC_FLTR_LOCK_H = 0x1C
DC_BIT_START = 0
DC_BIT_RUNNING = 0

DEFAULT_FREQ = 371.25          # MMCM 原速；像素钟 = /5 = 74.25 MHz


# --------------------------------------------------------------------------
# 子进程：只读 VTC，一次读都不多做
# --------------------------------------------------------------------------
def child_read():
    from pynq import MMIO
    m = MMIO(BASE_VTC, 0x1000)
    ver = m.read(OFF_VER)
    ctl = m.read(OFF_CTL)
    sys.stdout.write('VTC_VER=0x%08X VTC_CTL=0x%08X\n' % (ver, ctl))
    sys.stdout.flush()
    return 0


def read_vtc():
    """在隔离子进程里读一次 VTC。返回 (rc, 输出文本)。"""
    try:
        r = subprocess.run(
            [sys.executable, '-u', os.path.abspath(__file__), '--child-read'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    except subprocess.TimeoutExpired:
        return 9, '(30s 没回 —— 挂住了，先别再碰这个地址)'
    return r.returncode, r.stdout.decode('utf-8', 'replace').strip()


# --------------------------------------------------------------------------
# 只编程 dynclk
# --------------------------------------------------------------------------
def program_dynclk(freq_mhz, verbose=True):
    from pynq import MMIO
    try:
        from hdmi_out import clk_find_params, clk_find_reg
    except Exception as e:
        print('[clk] ★ 不能 import hdmi_out 的算寄存器函数：%s' % e)
        print('[clk]   （本脚本不自己重算 MMCM，宁可停下也不猜寄存器值）')
        return False

    pick = clk_find_params(freq_mhz)
    if pick is None:
        print('[clk] MMCM 参数搜不到（freq=%s）' % freq_mhz)
        return False
    clkdiv, fbmult, maindiv, actual = pick
    regs = clk_find_reg(clkdiv, fbmult, maindiv)
    if regs is None:
        print('[clk] MMCM 寄存器算不出来')
        return False

    m = MMIO(BASE_DYNCLK, 0x10000)
    m.write(DC_CLK_L, regs['clk0L'])
    m.write(DC_FB_L, regs['clkFBL'])
    m.write(DC_FB_H_CLK_H, regs['clkFBH_clk0H'])
    m.write(DC_DIV, regs['divclk'])
    m.write(DC_LOCK_L, regs['lockL'])
    m.write(DC_FLTR_LOCK_H, regs['fltr_lockH'])
    if verbose:
        print('[clk] 寄存器已写: CLK_L=0x%08X FB_L=0x%08X FB_H=0x%08X DIV=0x%08X '
              'LOCK_L=0x%08X FLTR=0x%08X'
              % (regs['clk0L'], regs['clkFBL'], regs['clkFBH_clk0H'],
                 regs['divclk'], regs['lockL'], regs['fltr_lockH']))
        print('[clk] MMCM %.4f MHz -> 像素钟 %.4f MHz（目标 %.4f）'
              % (actual, actual / 5.0, freq_mhz / 5.0))

    # video_display.c:174-183 的开钟握手，逐句照抄（带超时，绝不无限等）
    m.write(DC_CTRL, 0)
    t0 = time.time()
    while m.read(DC_STATUS) & (1 << DC_BIT_RUNNING):
        if time.time() - t0 > 1.0:
            print('[clk] ★ 关了之后 STATUS.bit0 没掉下来（停不下来）')
            return False
        time.sleep(0.001)
    m.write(DC_CTRL, (1 << DC_BIT_START))
    t0 = time.time()
    while not (m.read(DC_STATUS) & (1 << DC_BIT_RUNNING)):
        if time.time() - t0 > 1.0:
            print('[clk] ★ 启动后 STATUS.bit0 一直不置位（MMCM 没锁上）')
            return False
        time.sleep(0.001)
    print('[clk] STATUS.bit0 = 1 —— MMCM 已锁，PXL_CLK 已在跑')
    return True


# --------------------------------------------------------------------------
def main(argv):
    if '--child-read' in argv:
        return child_read()

    freq = DEFAULT_FREQ
    for i, a in enumerate(argv):
        if a == '--freq' and i + 1 < len(argv):
            freq = float(argv[i + 1])

    print('=' * 72)
    print('vtc_after_clk_test —— M12 @0x43C20000：是"没译码"还是"被复位钉住"？')
    print('=' * 72)

    try:
        state = open(FPGA_STATE).read().strip()
    except Exception as e:
        state = '读不到(%s)' % str(e)[:40]
    print('[env] fpga_manager state = %s' % state)
    if state != 'operating':
        print('[env] ★ 不是 operating = PL 里没有 bit = 一个寄存器都不碰')
        print('[env]   （PL 空时 AXI 事务没人应答，Zynq 互连没有超时 -> 整机挂死）')
        return 9

    # ---- ① 先复现 ----
    print('')
    print('[1/3] 配钟之前先读一次 0x%08X —— 预期 SIGBUS（复现原现象）' % BASE_VTC)
    rc1, out1 = read_vtc()
    print('[1/3] rc=%d  %s' % (rc1, out1))
    if rc1 == 0:
        print('[1/3] ★ 现在**就是活的** —— 板子这次本来就没病。')
        print('[1/3]   本实验对根因**无判别力**（多半是之前已经配过钟）。')
        print('')
        print('=> rc=2：直接去跑 ./hdmi_shot.sh 出图；根因判定留到下次冷启动再复现。')
        return 2
    if rc1 != -7:
        print('[1/3] ★ 既不是 0 也不是 SIGBUS(-7)，是别的问题，先停手。')
        return 9
    print('[1/3] ✓ SIGBUS 复现了（与 known_issues.md 第六节记录一致）')

    # ---- ② 只编程 dynclk ----
    print('')
    print('[2/3] 只编程 axi_dynclk @ 0x%08X（不碰 vtc / vdma / hpd）' % BASE_DYNCLK)
    if not program_dynclk(freq):
        print('[2/3] ★ 配钟失败 —— 实验做不下去，但**板子没被弄坏**（只写过 dynclk）')
        return 9
    print('[2/3] ✓ 配钟完成；等 0.5s 让 rst_pixel 的复位同步器走完')
    time.sleep(0.5)

    # ---- ③ 再读 ----
    print('')
    print('[3/3] 配钟之后再读一次 0x%08X —— 若变正常，根因坐实' % BASE_VTC)
    rc3, out3 = read_vtc()
    print('[3/3] rc=%d  %s' % (rc3, out3))

    print('')
    print('=' * 72)
    if rc3 == 0:
        print('★★ 根因坐实：SIGBUS 是 **v_tc 被自己的像素时钟域复位钉住**，不是译码缺失。')
        print('   网表证据链：resetn 硬与进片选(0x0008) -> IPIF 不应答 -> DPHASE 超时假应答')
        print('               -> rresp = ipif_Error = 1 = SLVERR -> SIGBUS。')
        print('   => **不需要重建**。hdmi_out.py 本来就是先配钟再碰 vtc，')
        print('      直接跑 ./hdmi_shot.sh 出图即可。')
        print('   => 另：axi_probe.py 单独跑时 M12 会报 SIGBUS，那是**预期行为**，')
        print('      要在跑过 hdmi_out.py 之后再复查。')
        print('=' * 72)
        return 0
    if rc3 == -7:
        print('★★ 假设被否：配了钟还是 SIGBUS。')
        print('   说明 v_tc 的 AXI 从机侧与像素时钟复位无关，')
        print('   回到 known_issues.md §6.5 的"重建 + 交换 MI 顺序"那条线。')
        print('   （本次实验至少排除了一整类解释，且只动过 dynclk 一个窗口。）')
        print('=' * 72)
        return 1
    print('★ 第三种结果 rc=%d —— 既不是 OKAY 也不是 SIGBUS，别急着下结论，把输出留档。' % rc3)
    print('=' * 72)
    return 9


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
