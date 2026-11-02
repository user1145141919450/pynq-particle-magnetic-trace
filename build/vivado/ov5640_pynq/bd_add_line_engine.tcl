# ============================================================================
# bd_add_line_engine.tcl —— 在**现有工程**的 BD 上原地加 PL 画线引擎
#   （2026-10-30 深夜；本机没有 PYNQ-Z2 板卡文件，build_project.tcl 的
#    create_project + apply_board_preset 流程跑不了，沿用 patch_hdmi_slave.tcl
#    那套"打开已有工程原地改"的路线）
#
# 做的事（与 bd/m1a_capture.tcl 的改动一一对应，改完两边一致）：
#   1) 把 5 个画线 RTL 加进工程（module reference 靠工程源码解析模块名）
#   2) axi_mem_intercon：NUM_SI 1 -> 2（VDMA + 画线引擎共用 HP0）
#   3) 实例 line_engine_top_0：m_axi -> axi_mem_intercon/S01_AXI
#      S01_ACLK/S01_ARESETN 接 FCLK_CLK0 / peripheral_aresetn
#   4) s_axi 用 apply_bd_automation 接进 ps7_0_axi_periph（自动加 M 口/时钟/复位/地址段）
#   5) 地址钉到 0x43B30000；validate + save
#
# 用法：vivado.bat -mode batch -source bd_add_line_engine.tcl
# 退出：失败 exit 1（vivado exit code 不可信，看日志的 GATE 行）
# ============================================================================
set proj_dir [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a
set ::fail 0
proc gate {what cond} {
    if {!$cond} { puts "ERROR: GATE: 失败 —— $what"; set ::fail 1 }
}

open_project $proj_dir/proj/$proj_name.xpr
set_property ip_repo_paths [list $proj_dir/ip_repo] [current_project]
update_ip_catalog -rebuild

# ---- 1) 源码入工程 ----
set rtl_files [list \
    $proj_dir/rtl/v3_bundle/line_draw.v \
    $proj_dir/rtl/v3_bundle/axi_wr_master.v \
    $proj_dir/rtl/v3_bundle/line_engine.v \
    $proj_dir/rtl/v3_bundle/line_ctrl_axi.v \
    $proj_dir/rtl/v3_bundle/line_engine_top.v ]
foreach f $rtl_files {
    if {[llength [get_files -quiet $f]] == 0} {
        add_files -norecurse $f
        puts "INFO: 加入源码 [file tail $f]"
    }
}
update_compile_order -fileset sources_1

open_bd_design [get_files $proj_name.bd]

# ---- 2) mem 互联加一个 SI ----
set ns [get_property CONFIG.NUM_SI [get_bd_cells axi_mem_intercon]]
if {$ns < 2} {
    set_property -dict [list CONFIG.NUM_SI {2}] [get_bd_cells axi_mem_intercon]
    puts "INFO: axi_mem_intercon NUM_SI $ns -> 2"
}

# ---- 3) 实例 + M_AXI 连接 ----
if {[llength [get_bd_cells -quiet line_engine_top_0]] == 0} {
    create_bd_cell -type module -reference line_engine_top line_engine_top_0
    puts "INFO: 创建 line_engine_top_0"
}
if {[llength [get_bd_intf_pins -quiet line_engine_top_0/m_axi]] == 0} {
    puts "ERROR: GATE: 没有 line_engine_top_0/m_axi 接口 —— X_INTERFACE_INFO 属性没生效？"
    set ::fail 1
}
if {[llength [get_bd_intf_pins -quiet line_engine_top_0/s_axi]] == 0} {
    puts "ERROR: GATE: 没有 line_engine_top_0/s_axi 接口 —— X_INTERFACE_INFO 属性没生效？"
    set ::fail 1
}
if {!$::fail} {
    if {[llength [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins line_engine_top_0/m_axi]]] == 0} {
        connect_bd_intf_net [get_bd_intf_pins line_engine_top_0/m_axi] \
                            [get_bd_intf_pins axi_mem_intercon/S01_AXI]
        puts "INFO: m_axi -> axi_mem_intercon/S01_AXI"
    }
}

# S01 的时钟/复位
proc conn_net_once {pin netpin} {
    if {[llength [get_bd_nets -quiet -of_objects [get_bd_pins $pin]]] == 0} {
        connect_bd_net [get_bd_pins $netpin] [get_bd_pins $pin]
    }
}
conn_net_once axi_mem_intercon/S01_ACLK      processing_system7_0/FCLK_CLK0
conn_net_once axi_mem_intercon/S01_ARESETN   rst_ps7_0_100M/peripheral_aresetn

# ---- 4) s_axi 自动化（M 口 + 时钟 + 复位 + 地址段）----
if {[llength [get_bd_intf_nets -quiet -of_objects [get_bd_intf_pins line_engine_top_0/s_axi]]] == 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
        Master "/processing_system7_0/M_AXI_GP0" \
        Slave  "/line_engine_top_0/s_axi" \
        intc_ip "/ps7_0_axi_periph" \
        Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
        ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins line_engine_top_0/s_axi]
    puts "INFO: s_axi 自动化完成"
}

# ---- 5) 地址钉到 0x43B30000 ----
set _seg [get_bd_addr_segs -of_objects [get_bd_cells line_engine_top_0]]
puts "INFO: line_engine 地址段 = $_seg"
if {[llength $_seg] > 0} {
    assign_bd_address -offset 0x43B30000 -range 0x00010000 \
        -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
        $_seg -force
    set off [get_property OFFSET [lindex $_seg 0]]
    puts "INFO: 命令口地址 = $off"
    gate "地址应为 0x43B30000（实际 $off）" [expr {$off == 0x43B30000}]
} else {
    puts "ERROR: GATE: 找不到 line_engine_top_0 的地址段"
    set ::fail 1
}

# ---- 6) 校验 + 保存 ----
if {!$::fail} {
    if {[catch {validate_bd_design} _ve]} {
        puts "ERROR: GATE: validate_bd_design 抛错：$_ve"
        set ::fail 1
    }
}
if {!$::fail} {
    save_bd_design
    puts "INFO: GATE: BD 已保存"
} else {
    puts "ERROR: GATE: 有失败项，**不保存**"
}

if {$::fail} { exit 1 }
puts "INFO: GATE: 全部通过"
exit 0
