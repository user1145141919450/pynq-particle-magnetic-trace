open_project proj/ov5640_m1a.xpr
set bdf [get_files -quiet *.bd]
puts "BD 文件: $bdf"
open_bd_design $bdf
puts "==== BD 单元 ===="
foreach c [get_bd_cells *] { puts "  CELL $c" }
puts "==== 空闲 AXI 主口 ===="
foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_cells *] -filter {MODE==Master}] { puts "  M $p" }
puts "==== 时钟/复位 ===="
foreach p [get_bd_pins -quiet -of_objects [get_bd_cells *] -filter {TYPE==clk}] { puts "  CLK $p" }
