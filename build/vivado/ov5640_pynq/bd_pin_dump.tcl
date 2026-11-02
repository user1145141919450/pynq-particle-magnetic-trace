# ============================================================================
# bd_pin_dump.tcl — 临时只读脚本：写 BD 改动前，先把两个 AXI-Stream 接口的
# 子引脚名、现有 axi_gpio、互连 NUM_MI、已分配地址段全部打出来。
# 不改任何东西，跑完 exit。
#   F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source bd_pin_dump.tcl
# ============================================================================
set proj_dir [file dirname [file normalize [info script]]]
open_project $proj_dir/proj/ov5640_m1a.xpr
open_bd_design $proj_dir/proj/ov5640_m1a.srcs/sources_1/bd/ov5640_m1a/ov5640_m1a.bd

foreach ipin {v_vid_in_axi4s_0/video_out axi_vdma_0/S_AXIS_S2MM} {
    puts "=== INTF $ipin ==="
    if {[llength [get_bd_intf_pins -quiet $ipin]] == 0} {
        puts "    (不存在！)"
    } else {
        puts "    MODE=[get_property -quiet MODE [get_bd_intf_pins $ipin]]"
        foreach p [get_bd_pins -quiet -of_objects [get_bd_intf_pins $ipin]] {
            puts "    PIN [get_property PATH $p]  DIR=[get_property -quiet DIR $p]  W=[get_property -quiet LEFT $p]:[get_property -quiet RIGHT $p]"
        }
    }
}

puts "=== 现有 axi_gpio / 双通道情况 ==="
foreach c [get_bd_cells -quiet -filter {VLNV =~ *axi_gpio*}] {
    puts "    [get_property PATH $c]  IS_DUAL=[get_property -quiet CONFIG.C_IS_DUAL $c]  WIDTH=[get_property -quiet CONFIG.C_GPIO_WIDTH $c]"
}

puts "=== ps7_0_axi_periph NUM_MI = [get_property CONFIG.NUM_MI [get_bd_cells ps7_0_axi_periph]] ==="

puts "=== 已分配地址段 ==="
foreach s [get_bd_addr_segs -quiet] {
    set off [get_property -quiet OFFSET $s]
    set rng [get_property -quiet RANGE $s]
    puts "    [get_property PATH $s]  OFF=$off RANGE=$rng"
}

puts "=== 时钟/复位网络 ==="
foreach n [get_bd_nets -quiet -filter {NAME =~ *clk* || NAME =~ *reset* || NAME =~ *aresetn*}] {
    puts "    NET [get_property PATH $n]"
}
puts "=== ov5640_top_0 的 vsync / href 相关引脚 ==="
foreach p [get_bd_pins -quiet ov5640_top_0/*] {
    puts "    [get_property PATH $p]"
}
puts "DONE"
exit
