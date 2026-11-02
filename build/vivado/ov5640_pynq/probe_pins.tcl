# probe_pins.tcl — 一次性列出 blk_mem_gen / axi_bram_ctrl 的真实引脚名
# 起因：BD 脚本里写 blk_mem_gen_vision/BRAM_PORTB_we 报 "No pins matched"，
# 与其猜，不如让 Vivado 把引脚名说出来。
create_bd_design probe_tmp

create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 bmg
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Write_Depth_A {8192} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
] [get_bd_cells bmg]

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 bctl
set_property -dict [list CONFIG.DATA_WIDTH {32} CONFIG.SINGLE_PORT_BRAM {0} \
    CONFIG.PROTOCOL {AXI4LITE} CONFIG.ECC_TYPE {0}] [get_bd_cells bctl]

puts "=== blk_mem_gen 接口 ==="
foreach i [get_bd_intf_pins bmg/*] { puts "INTF $i" }
puts "=== blk_mem_gen 普通引脚 ==="
foreach p [get_bd_pins bmg/*] { puts "PIN  $p" }
puts "=== blk_mem_gen BRAM_PORTB 的子引脚 ==="
foreach p [get_bd_pins -of_objects [get_bd_intf_pins bmg/BRAM_PORTB]] {
    puts "SUBPIN $p"
}
puts "=== axi_bram_ctrl 接口 ==="
foreach i [get_bd_intf_pins bctl/*] { puts "INTF $i" }
exit
