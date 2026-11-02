# add_map_detect_to_bd.tcl —— 把「PL 检测 v0」（map_detect_core + map_detect_axi）接进 BD
#
# 照抄 add_pe_to_bd.tcl 的套路：
#   1) RTL 进工程
#   2) 建 module reference 单元 map_detect_axi_0、AXI4 自动化、地址钉死 0x40002000
#   3) **tap 并联**：把 vision_core_0 的 wr_en/wr_addr/wr_data 三根网线各加一个
#      汇点（map_detect_axi_0/tap_*）——只读观察，不改采集链
#   4) 校验 + 保存（不综合；综合走 build_after_patch.tcl）
#
# 用法（vivado/ov5640_pynq 目录）：
#   F:/Xilinx/Vivado2023/Vivado/2023.2/bin/vivado.bat -mode batch -source add_map_detect_to_bd.tcl

set proj_dir [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a
set rtl_dir "rtl/map_detect"
open_project $proj_dir/proj/$proj_name.xpr

# ---- 1) 先打开 BD ----
# ★ 2026-10-20 实锤：先 add_files 再 open_bd_design 会触发 IP 规则初始化错误
#   （roe_framer / pcie4_uscale_plus 的 automation 明明在盘上，却报读不到）；
#   同样的工程先 open_bd_design 再 add_files 就正常。顺序不能反。
set bdf [get_files -quiet *.bd]
open_bd_design $bdf

# ---- 2) RTL 进工程（BD 已打开之后）----
add_files -norecurse [list \
    "$proj_dir/$rtl_dir/map_detect_core.v" \
    "$proj_dir/$rtl_dir/map_detect_axi.v" \
    "$proj_dir/$rtl_dir/map_cc.v" ]
update_compile_order -fileset sources_1

# ---- 3) 建单元 ----
if {[llength [get_bd_cells -quiet map_detect_axi_0]] > 0} {
    puts "== 已存在 map_detect_axi_0，先删掉重建 =="
    delete_bd_objs [get_bd_cells map_detect_axi_0]
}
create_bd_cell -type module -reference map_detect_axi map_detect_axi_0
puts "== 已建单元 map_detect_axi_0 =="

# ---- 3) AXI4 自动化（同 PE 的规则/互联）----
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/map_detect_axi_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins map_detect_axi_0/s_axi]
puts "== AXI 自动化已应用 =="

# ---- 4) tap 并联（找到现有网线对象，再加一个汇点）----
set tap_ok 1
foreach {src dst} {wr_en tap_wr_en wr_addr tap_wr_addr wr_data tap_wr_data} {
    set spin [get_bd_pins -quiet vision_core_0/$src]
    set dpin [get_bd_pins -quiet map_detect_axi_0/$dst]
    if {[llength $spin] == 0 || [llength $dpin] == 0} {
        puts "ERROR: 找不到引脚 vision_core_0/$src 或 map_detect_axi_0/$dst"
        set tap_ok 0
        continue
    }
    # ★ 2026-10-20：**引脚对引脚**这一种能成功（会并入已有网线）；
    #   `connect_bd_net <net> <pin>` 在 2023.2 里报 BD 41-701（实测）。
    connect_bd_net $spin $dpin
    puts "== tap 已接：vision_core_0/$src -> map_detect_axi_0/$dst =="
}

# ---- 5) 地址钉死 0x40002000（0x40000000=v3、0x40001000=pe）----
set seg ""
foreach s [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces processing_system7_0/Data]] {
    if {[string match *map_detect_axi_0* $s]} { set seg $s }
}
if {$seg eq ""} {
    puts "ERROR: 找不到 map_detect_axi_0 的地址段"
    set tap_ok 0
} else {
    assign_bd_address -offset 0x40002000 -range 0x00001000 \
        -target_address_space /processing_system7_0/Data $seg
    puts "== 地址段：$seg OFF=0x40002000 =="
}

# ---- 6) 校验 + 保存 ----
set nerr 0
if {!$tap_ok} { set nerr 1 }
if {[catch {validate_bd_design} msg]} {
    puts "★ validate_bd_design 报错：$msg"
    set nerr 1
} else {
    puts "== validate_bd_design 通过 =="
}
if {$nerr == 0} {
    save_bd_design
    puts "== BD 已保存 =="
    foreach seg2 [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces processing_system7_0/Data]] {
        puts "   $seg2  [get_property OFFSET $seg2]  [get_property RANGE $seg2]"
    }
} else {
    puts "★ 有错，没有保存（工程保持原样）"
}
close_bd_design [get_bd_designs]
close_project
puts "== 收工 =="
