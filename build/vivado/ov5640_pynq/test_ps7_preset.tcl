# test_ps7_preset.tcl —— 快速验证 ps7_pynq_z2_preset.tcl 能否把关键 MIO 配好
# （不建整个 BD；1 分钟看结果）
set proj_dir [file dirname [file normalize [info script]]]
file delete -force $proj_dir/_ps7test
create_project _ps7test $proj_dir/_ps7test -part xc7z020clg400-1 -force
create_bd_design t
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 ps
source $proj_dir/bd/ps7_pynq_z2_preset.tcl
ps7_apply_preset [get_bd_cells ps]

proc show {name} {
    puts "CHECK: $name = [get_property CONFIG.$name [get_bd_cells ps]]"
}
puts "======== 关键参数核对 ========"
show PCW_PRESET_BANK1_VOLTAGE
show PCW_MIO_16_IOTYPE
show PCW_MIO_40_IOTYPE
show PCW_USE_SDIO0
show PCW_USE_UART0
show PCW_USE_ENET0
show PCW_USE_USB0
show PCW_MIO_14_PERIPHERAL
show PCW_MIO_15_PERIPHERAL
show PCW_MIO_40_PERIPHERAL
show PCW_MIO_16_PERIPHERAL
show PCW_MIO_52_PERIPHERAL
puts "MIO_TREE_PERIPHERALS = [get_property CONFIG.PCW_MIO_TREE_PERIPHERALS [get_bd_cells ps]]"
puts "MIO_TREE_SIGNALS     = [get_property CONFIG.PCW_MIO_TREE_SIGNALS [get_bd_cells ps]]"
puts "DDR_PART = [get_property CONFIG.PCW_UIPARAM_DDR_PARTNO [get_bd_cells ps]]"
puts "UART0_IO = [get_property CONFIG.PCW_UART0_GRP_FULL_ENABLE [get_bd_cells ps]] / [get_property CONFIG.PCW_UART0_PERIPHERAL_ENABLE [get_bd_cells ps]]"
exit 0
