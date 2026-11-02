# add_v3_to_bd.tcl —— 把 V3「并行轨迹束」接进现有 BD（**走 AXI4 自动化**）
#
# 前置：备份已在 backup/bd_before_v3_2026-09-28/（整份 proj + good_bit.bit）
# 做法：
#   1) 把三份 V3 RTL 加进工程（模块引用需要模块在工程里）
#   2) 打开 BD，建一个 module reference 单元
#   3) apply_bd_automation 的 axi4 规则：Slave=我们的 s_axi、intc_ip=现有 ps7_0_axi_periph
#      —— 它会自动加 MI 端口、连时钟/复位、分配地址段
#   4) 校验 + 保存（**不综合**，综合单独一条命令，方便失败时只重跑一段）
#
# 用法（vivado/ov5640_pynq 目录）：
#   vivado -mode batch -source add_v3_to_bd.tcl

set rtl_dir "rtl/v3_bundle"
open_project proj/ov5640_m1a.xpr

# ---- 1) RTL 进工程（模块引用）----
add_files -norecurse [list \
    "$rtl_dir/traj_lane.v" \
    "$rtl_dir/traj_bundle.v" \
    "$rtl_dir/v3_bundle_axi.v" ]
update_compile_order -fileset sources_1

# ---- 2) 打开 BD，建单元 ----
set bdf [get_files -quiet *.bd]
open_bd_design $bdf
if {[llength [get_bd_cells -quiet v3_bundle_axi_0]] > 0} {
    puts "== 已存在 v3_bundle_axi_0，先删掉重建 =="
    delete_bd_objs [get_bd_cells v3_bundle_axi_0]
}
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
puts "== 已建单元 v3_bundle_axi_0 =="

# ---- 3) AXI4 自动化（接到现有互联上）----
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/v3_bundle_axi_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins v3_bundle_axi_0/s_axi]
puts "== AXI 自动化已应用 =="

# ---- 4) 校验 + 保存 ----
set nerr 0
if {[catch {validate_bd_design} msg]} {
    puts "★ validate_bd_design 报错：$msg"
    set nerr 1
} else {
    puts "== validate_bd_design 通过 =="
}
if {$nerr == 0} {
    save_bd_design
    puts "== BD 已保存 =="
    puts "== 地址段：=="
    foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces processing_system7_0/Data]] {
        puts "   $seg  [get_property OFFSET $seg]  [get_property RANGE $seg]"
    }
} else {
    puts "★ 有错，**没有保存**（工程保持原样；备份在 backup/ 里）"
}
close_bd_design [get_bd_designs]
puts "== 收工 =="
