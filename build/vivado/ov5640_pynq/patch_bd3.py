# -*- coding: utf-8 -*-
"""patch_bd3.py —— 把 V3 的那段（建单元 + axi4 自动化）**挪到 BD 收尾处**。

为什么（2026-09-28 实测，真正的根因）：
  放在中途时，`apply_bd_automation` 会顺手把
  `processing_system7_0/FCLK_RESET0_N -> rst_ps7_0_100M/ext_reset_in` 连上；
  而脚本**后面**自己还有一行 `connect_bd_net` 要连同一对引脚 ->
  "all ports/pins are already connected" -> [BD 5-4] ERROR，整个构建死在 BD 这一步。
  （前面 "A peripheral must be specified" 是另一个症状：手搓 assign_bd_address
    的段名不对；那条已经换成自动化了。）

  挪到 `validate_bd_design` 之前，就没人和它抢那对网了。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'bd/m1a_capture.tcl'
s = io.open(p, encoding='utf-8').read()

BLOCK = '''# ---- V3 并行轨迹束：PS 写扫描参数、启动、读回 17 路结果 ----
# ★ 用 **axi4 自动化**接进现有互联（intc_ip 指过去）：它会自己加 MI 端口、连时钟/复位、
#   分配地址段。手搓 M11 + 手写 assign_bd_address 那条路实测在地址段名上就死了
#   （"A peripheral must be specified ..."），别再手搓。
# ★★ 必须放在**收尾处**：自动化会连 FCLK_RESET0_N -> rst_ps7_0_100M/ext_reset_in，
#   而脚本前面自己也要连这一对 —— 放中途就会撞 "already connected" 报 [BD 5-4]。
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \\
    Master "/processing_system7_0/M_AXI_GP0" \\
    Slave  "/v3_bundle_axi_0/s_axi" \\
    intc_ip "/ps7_0_axi_periph" \\
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \\
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins v3_bundle_axi_0/s_axi]

'''

# 1) 从原位置删掉
if BLOCK in s:
    s = s.replace(BLOCK, '', 1)
    print('已从原位置移出')
else:
    print('★ 原位置找不到那段（可能已改过），继续尝试插入')

# 2) 插到 validate 之前
anchor = 'validate_bd_design\nsave_bd_design'
if anchor in s and 'v3_bundle_axi_0' not in s.split(anchor)[0].split('create_bd_cell')[-1][:2000]:
    pass
if anchor in s:
    s = s.replace(anchor, BLOCK + anchor, 1)
    print('已插到 validate 之前')
else:
    print('★ 找不到 validate_bd_design 锚点')

io.open(p, 'w', encoding='utf-8').write(s)
print('create_bd_cell v3_bundle_axi_0 出现次数:', s.count('create_bd_cell -type module -reference v3_bundle_axi'))
