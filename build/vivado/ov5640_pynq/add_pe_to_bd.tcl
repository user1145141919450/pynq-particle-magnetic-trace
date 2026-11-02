# add_pe_to_bd.tcl —— 把「粒子引擎」（particle_engine.v + pe_axi.v）接进现有 BD
#
# 照抄 add_v3_to_bd.tcl 的套路（同一个 AXI 自动化规则 + 同一互联）：
#   1) RTL 进工程（traj_lane.v 早已在工程里——v3 那次加的；本次补
#      particle_engine.v + pe_axi.v）
#   2) 建 module reference 单元 pe_axi_0、axi4 自动化接上、自动分配地址段
#   3) 校验 + 保存（不综合；综合走 build_after_patch.tcl）
#
# 用法（vivado/ov5640_pynq 目录）：
#   F:/Xilinx/Vivado2023/Vivado/2023.2/bin/vivado.bat -mode batch -source add_pe_to_bd.tcl

set rtl_dir "rtl/v3_bundle"
open_project proj/ov5640_m1a.xpr

# ---- 1) RTL 进工程 ----
add_files -norecurse [list \
    "$rtl_dir/traj_lane.v" \
    "$rtl_dir/particle_engine.v" \
    "$rtl_dir/pe_axi.v" ]
update_compile_order -fileset sources_1

# ---- 2) 打开 BD，建单元 ----
set bdf [get_files -quiet *.bd]
open_bd_design $bdf
if {[llength [get_bd_cells -quiet pe_axi_0]] > 0} {
    puts "== 已存在 pe_axi_0，先删掉重建 =="
    delete_bd_objs [get_bd_cells pe_axi_0]
}
create_bd_cell -type module -reference pe_axi pe_axi_0
puts "== 已建单元 pe_axi_0 =="

# ---- 3) AXI4 自动化（同 v3 的规则/互联）----
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/pe_axi_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins pe_axi_0/s_axi]
puts "== AXI 自动化已应用 =="

# ---- 4) 校验 + 保存 + 打印地址 ----
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
    puts "== 地址段（找 pe_axi_0 那条）：=="
    foreach seg [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces processing_system7_0/Data]] {
        puts "   $seg  [get_property OFFSET $seg]  [get_property RANGE $seg]"
    }
} else {
    puts "★ 有错，没有保存（工程保持原样）"
}
close_bd_design [get_bd_designs]
puts "== 收工 =="
