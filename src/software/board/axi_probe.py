# -*- coding: utf-8 -*-
"""axi_probe.py —— 挨个读一批 AXI 地址，回答"**到底哪些 IP 真的在这块 PL 里**"。

为什么要有它（2026-10-01，HDMI 首亮踩出来的）：
  自检脚本报 `dynclk@0x43C10000 ... OK`，紧接着读 `0x43C20000`（vtc_out）就
  `Bus error (core dumped)`。两根线索互相矛盾：
    · 这两个 IP 在 **同一份 hwh 里**，都标着 0x43C1/0x43C2；
    · 若 bitstream 里根本没有 HDMI 那一组，0x43C10000 也该一起 SIGBUS。
  ⇒ "hwh 上写了" ≠ "bitstream 里真有"。hwh 是 **BD** 的产物，
    bitstream 是 **网表** 的产物，两者中间隔着"综合跑没跑过"这一步。
    本次的嫌疑正是：只重跑实现（rerun_impl.tcl 复用 synth_1）时，网表可能比 BD 旧。
  所以别再靠推断，**直接问 PL**：每个地址发一次读，谁应答就是谁在。

★ 安全性（抄 pl_probe.py 的结论，这是本板用两次挂机换来的）：
  · PL 里**有** bit 时：地址没译码 -> 从机回 DECERR -> 干净的 **SIGBUS(7)**，
    只杀那个进程。可以放心逐个试。
  · PL **空**时：事务没人应答，而 Zynq 互连**没有超时** -> **整机挂死，只能断电**。
  ⇒ 所以本脚本**先查 fpga_manager state**，不是 operating 就**一个地址都不读**直接退。
    （state 只在成功下载后才变 operating，所以"有 bit"这个前提是可判的。）

★ 每个地址都在**独立子进程**里读：SIGBUS 是信号不是异常，try/except 拦不住。

用法（板上，要 root + 登录 shell）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u axi_probe.py"
  ... axi_probe.py --list 0x43C00000 0x43C10000 ...   # 自己给地址

退出码：0 = 全部读出一个值；1 = 有地址 SIGBUS；9 = 前提不满足（没敢读）
"""

import os
import subprocess
import sys

FPGA_STATE = '/sys/class/fpga_manager/fpga0/state'

# 默认探测表：名字, base, offset
#   ★ 前四个 = HDMI 通路（本次新加的，嫌疑对象）
#   ★ 后四个 = **对照组**：这几个是"老"IP，本次之前的 bit 里就该有。
#     对照组的用处：如果对照组全活、只有 HDMI 死 -> HDMI 那组确实不在网表里；
#     如果对照组也死 -> 说明下进去的压根不是这块板的 bit（问题在更上游）。
# ★ 2026-10-01 改：从"8 个抽样"扩成**全部 16 个 MI 口**。
#   为什么必须全扫：抽样只能回答"vtc 死没死"，回答不了"是不是只死了它"。
#   当时的推理链条是——BD/xci/OOC/时序/bit 血缘**逐级验过全对**，而立上只有
#   M12 不应答 ⇒ 要么配置没配全，要么网表里真没有这一格。**两种病因要的证据不一样**：
#     · 若全扫只有 M12 死  -> 指向"这一格的译码逻辑"（多半是配置问题，重下可解）
#     · 若还有别的格子也死 -> 指向"整片配置不完整/地址图整体错位"（重下必解）
#   所以一次把 16 格全扫掉，别挤牙膏。
#   MI 编号抄自 xbar_0.xci 的 M00..M15_A00_BASE_ADDR，一一对应，方便和构建产物对账。
TARGETS = [
    ("M00 axi_vdma_0      采集DMA  ", 0x43000000, 0x00),
    ("M01 axi_gpio_probe0          ", 0x43A00000, 0x00),
    ("M02 axi_gpio_probe1          ", 0x43A10000, 0x00),
    ("M03 axi_gpio_probe2          ", 0x43A20000, 0x00),
    ("M04 axi_gpio_probe3          ", 0x43A30000, 0x00),
    ("M05 axi_gpio_probe4          ", 0x43A40000, 0x00),
    ("M06 axi_gpio_probe5          ", 0x43A50000, 0x00),
    ("M07 axi_gpio_probe6          ", 0x43A60000, 0x00),
    ("M08 axi_bram_ctrl_vision     ", 0x43B00000, 0x00),
    ("M09 axi_gpio_vision          ", 0x43B10000, 0x00),
    ("M10 axi_gpio_vstat           ", 0x43B20000, 0x00),
    ("M11 axi_vdma_out    HDMI输出 ", 0x43C00000, 0x00),
    ("M12 vtc_out         时序发生器", 0x43C20000, 0x00),   # <- 本次的嫌疑对象
    ("M13 axi_dynclk      像素时钟 ", 0x43C10000, 0x00),
    ("M14 hdmi_out_hpd    热插拔   ", 0x43C30000, 0x00),
    ("M15 v3_bundle_axi_0  V3核    ", 0x40000000, 0x00),
]


def child(base, off):
    """子进程入口：只做那一次危险的读。"""
    from pynq import MMIO
    m = MMIO(base, 0x10000)
    v = m.read(off)
    sys.stdout.write('VAL 0x%08X\n' % v)
    sys.stdout.flush()
    return 0


def probe_one(base, off, timeout=30):
    """返回 (rc, 输出)。rc<0 表示被信号杀死（-7 = SIGBUS）。超时返回 (None,'')。"""
    try:
        r = subprocess.run(
            [sys.executable, '-u', os.path.abspath(__file__),
             '--child', hex(base), hex(off)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, ''
    return r.returncode, r.stdout.decode('utf-8', 'replace').strip()


def main(argv):
    if '--child' in argv:
        i = argv.index('--child')
        return child(int(argv[i + 1], 0), int(argv[i + 2], 0))

    targets = TARGETS
    if '--list' in argv:
        i = argv.index('--list')
        targets = [('(自定义)', int(a, 0), 0x00) for a in argv[i + 1:]]

    # ---- 前提：PL 里必须有 bit，否则读一次就能挂死整机 ----
    try:
        state = open(FPGA_STATE).read().strip()
    except Exception as e:
        state = '读不到(%s)' % str(e)[:40]
    print('[probe] fpga_manager state = %s' % state)
    if state != 'operating':
        print('[probe] ★ 不是 operating = PL 里没有 bit = **一个地址都不读**')
        print('[probe]   （PL 空时 AXI 事务没人应答，Zynq 互连没有超时 -> 整机挂死，')
        print('[probe]    实测过两次，只能断电。见 pl_probe.py 注释里的第五个坑）')
        return 9

    print('[probe] 每个地址一次隔离子进程读（有 bit 时，没译码的地址给干净的 SIGBUS）')
    print('')
    n_bus, n_ok = 0, 0
    for name, base, off in targets:
        rc, out = probe_one(base, off)
        if rc == 0 and out.startswith('VAL'):
            print('  [有] %s @0x%08X  %s' % (name, base, out[4:]))
            n_ok += 1
        elif rc == -7:
            print('  [无] %s @0x%08X  **SIGBUS** -> 这个地址上没有译码到从机' % (name, base))
            n_bus += 1
        elif rc is None:
            print('  [超时] %s @0x%08X  子进程 30s 没回 —— **先别再读了**' % (name, base))
        else:
            print('  [?]  %s @0x%08X  rc=%d  %s' % (name, base, rc, out[:80]))

    print('')
    print('[probe] 汇总：有外设 %d 个，SIGBUS %d 个' % (n_ok, n_bus))
    return 0 if n_bus == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
