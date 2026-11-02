# -*- coding: utf-8 -*-
"""patch_bd2.py —— 把 bd/m1a_capture.tcl 里"手搓 M11 + 手写 assign_bd_address"换成
**apply_bd_automation 的 axi4 规则**。

为什么（2026-09-28 实测）：
  · 手搓版：连了 M11_AXI、也把时钟/复位加进了已有的 connect_bd_net 列表，
    但 `assign_bd_address [get_bd_addr_segs v3_bundle_axi_0/s_axi/Reg]` 报
    "A peripheral must be specified when '-offset and -range' option exists"
    —— 段名/可寻址性没对上，构建在 BD 这一步就死了。
  · 自动化版（add_v3_to_bd.tcl 那条路，用 intc_ip 指到现有互联）：
    **validate_bd_design 通过、save_bd_design 成功** ✓ —— 它以 Xilinx 认的
    方式加 MI 端口、连时钟/复位、分配地址，别再手搓了。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'bd/m1a_capture.tcl'
s = io.open(p, encoding='utf-8').read()

reps = []

# 1) NUM_MI 回退为 11（自动化自己会加端口）
reps.append((
    'set_property -dict [list CONFIG.NUM_MI {12}] [get_bd_cells ps7_0_axi_periph]',
    'set_property -dict [list CONFIG.NUM_MI {11}] [get_bd_cells ps7_0_axi_periph]'))

# 2) 手搓的 M11 连线 -> 自动化
reps.append((
    '''# ---- V3 并行轨迹束（M11）：PS 写扫描参数、启动、读回 17 路结果 ----
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M11_AXI] [get_bd_intf_pins v3_bundle_axi_0/s_axi]''',
    '''# ---- V3 并行轨迹束：PS 写扫描参数、启动、读回 17 路结果 ----
# ★ 用 **axi4 自动化**接进现有互联（intc_ip 指过去）：它会自己加 MI 端口、连时钟/复位、
#   分配地址段。手搓 M11 + 手写 assign_bd_address 那条路实测在地址段名上就死了
#   （"A peripheral must be specified ..."），别再手搓。
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \\
    Master "/processing_system7_0/M_AXI_GP0" \\
    Slave  "/v3_bundle_axi_0/s_axi" \\
    intc_ip "/ps7_0_axi_periph" \\
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \\
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins v3_bundle_axi_0/s_axi]'''))

# 3) 时钟/复位列表里的手搓项去掉（自动化已连）
reps.append((
    '''    [get_bd_pins axi_gpio_vstat/s_axi_aclk] \\
    [get_bd_pins ps7_0_axi_periph/M11_ACLK] \\
    [get_bd_pins v3_bundle_axi_0/s_axi_aclk]''',
    '    [get_bd_pins axi_gpio_vstat/s_axi_aclk]'))
reps.append((
    '''    [get_bd_pins axi_gpio_vstat/s_axi_aresetn] \\
    [get_bd_pins ps7_0_axi_periph/M11_ARESETN] \\
    [get_bd_pins v3_bundle_axi_0/s_axi_aresetn]''',
    '    [get_bd_pins axi_gpio_vstat/s_axi_aresetn]'))

# 4) 手写的 assign_bd_address 去掉（自动化已分配）
reps.append((
    '''# V3 并行轨迹束：0x43C00000 起 64KB（现有最远用到 0x43B20000，这段是空的）
assign_bd_address -offset 0x43C00000 -range 0x00010000 \\
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \\
    [get_bd_addr_segs v3_bundle_axi_0/s_axi/Reg] -force

''',
    ''))

n = 0
for old, new in reps:
    if old in s:
        s = s.replace(old, new, 1)
        n += 1
    else:
        print('跳过（找不到锚点）: %s' % old.strip().splitlines()[0][:60])
io.open(p, 'w', encoding='utf-8').write(s)
print('m1a_capture.tcl 改了 %d 处（换成 axi4 自动化）' % n)
