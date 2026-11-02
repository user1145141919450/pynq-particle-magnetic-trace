open_project proj/ov5640_m1a.xpr
add_files -norecurse [list rtl/v3_bundle/traj_lane.v rtl/v3_bundle/traj_bundle.v rtl/v3_bundle/v3_bundle_axi.v]
update_compile_order -fileset sources_1
set bdf [get_files -quiet *.bd]
open_bd_design $bdf
if {[llength [get_bd_cells -quiet v3_bundle_axi_0]] > 0} { delete_bd_objs [get_bd_cells v3_bundle_axi_0] }
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
puts "==== 接口引脚 ===="
foreach p [get_bd_intf_pins -quiet -of_objects [get_bd_cells v3_bundle_axi_0]] { puts "  INTF $p" }
puts "==== 该单元的地址段 ===="
foreach s [get_bd_addr_segs -quiet -of_objects [get_bd_cells v3_bundle_axi_0]] { puts "  SEG $s" }
puts "==== 时钟/复位引脚 ===="
foreach p [get_bd_pins -quiet -of_objects [get_bd_cells v3_bundle_axi_0] -filter {TYPE==clk || TYPE==rst}] { puts "  PIN $p" }
close_bd_design [get_bd_designs]
