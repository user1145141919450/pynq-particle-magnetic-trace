# -*- coding: utf-8 -*-
"""patch_bd.py —— 把 V3 并行轨迹束加进"从零重建 BD"的两个脚本里。

为什么用 Python 文件而不是 shell heredoc（2026-09-27/28 反复踩）：
  heredoc 会把 `\\n`、行尾 `\\`、嵌套引号吃掉，写坏过 1 个 Python 模块、
  1 个 PowerShell 脚本、好几次 tcl 补丁。**代码文件用 Write 工具写**就没这问题。

改两处：
  1) build_project.tcl：把 V3 的三份 RTL **显式**加进工程
     （不能 glob：目录里还有 tb_*.v 和旧版 traj_lane_1clock_verified.v，
       glob 会拉进同名模块 traj_lane 导致冲突）
  2) bd/m1a_capture.tcl：NUM_MI 11->12；建 v3_bundle_axi_0；连 M11；
     时钟/复位加进已有两条 connect_bd_net 列表；分配 0x43C00000 起 64KB
"""

import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# ---------------- 1) build_project.tcl ----------------
p = 'build_project.tcl'
s = io.open(p, encoding='utf-8').read()
anchor = 'add_files [glob -nocomplain $proj_dir/rtl/debug/*.v]'
add = anchor + '''

# V3 并行轨迹束（**显式列三份**，不能用 glob —— 目录里还有 tb_*.v 和旧版
# traj_lane_1clock_verified.v，glob 会把同名模块 traj_lane 一起拉进来冲突）
add_files -norecurse [list \\
    $proj_dir/rtl/v3_bundle/traj_lane.v \\
    $proj_dir/rtl/v3_bundle/traj_bundle.v \\
    $proj_dir/rtl/v3_bundle/v3_bundle_axi.v ]
puts "INFO: V3 并行轨迹束 RTL 已加入工程"'''
if 'v3_bundle_axi.v' not in s:
    assert anchor in s, 'build_project.tcl 里找不到锚点'
    s = s.replace(anchor, add, 1)
    io.open(p, 'w', encoding='utf-8').write(s)
    print('build_project.tcl 已改')
else:
    print('build_project.tcl 已包含 V3，跳过')

# ---------------- 2) bd/m1a_capture.tcl ----------------
p2 = 'bd/m1a_capture.tcl'
s2 = io.open(p2, encoding='utf-8').read()
edits = []

edits.append((
    'set_property -dict [list CONFIG.NUM_MI {11}] [get_bd_cells ps7_0_axi_periph]',
    'set_property -dict [list CONFIG.NUM_MI {12}] [get_bd_cells ps7_0_axi_periph]'))

edits.append((
    'connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M10_AXI] [get_bd_intf_pins axi_gpio_vstat/S_AXI]',
    '''connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M10_AXI] [get_bd_intf_pins axi_gpio_vstat/S_AXI]

# ---- V3 并行轨迹束（M11）：PS 写扫描参数、启动、读回 17 路结果 ----
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M11_AXI] [get_bd_intf_pins v3_bundle_axi_0/s_axi]'''))

edits.append((
    '    [get_bd_pins axi_gpio_vstat/s_axi_aclk]',
    '''    [get_bd_pins axi_gpio_vstat/s_axi_aclk] \\
    [get_bd_pins ps7_0_axi_periph/M11_ACLK] \\
    [get_bd_pins v3_bundle_axi_0/s_axi_aclk]'''))

edits.append((
    '    [get_bd_pins axi_gpio_vstat/s_axi_aresetn]',
    '''    [get_bd_pins axi_gpio_vstat/s_axi_aresetn] \\
    [get_bd_pins ps7_0_axi_periph/M11_ARESETN] \\
    [get_bd_pins v3_bundle_axi_0/s_axi_aresetn]'''))

edits.append((
    '''validate_bd_design
save_bd_design''',
    '''# V3 并行轨迹束：0x43C00000 起 64KB（现有最远用到 0x43B20000，这段是空的）
assign_bd_address -offset 0x43C00000 -range 0x00010000 \\
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \\
    [get_bd_addr_segs v3_bundle_axi_0/s_axi/Reg] -force

validate_bd_design
save_bd_design'''))

if 'v3_bundle_axi_0' in s2:
    print('m1a_capture.tcl 已包含 V3，跳过')
else:
    for old, new in edits:
        assert old in s2, '找不到锚点: %s' % old[:60]
        s2 = s2.replace(old, new, 1)
    io.open(p2, 'w', encoding='utf-8').write(s2)
    print('m1a_capture.tcl 已改（5 处）')
