open_project proj/ov5640_m1a.xpr
set bdf [get_files -quiet *.bd]
open_bd_design $bdf
puts "== BD 打开成功 =="
if {[llength [get_bd_cells -quiet v3_bundle_axi_0]] > 0} { delete_bd_objs [get_bd_cells v3_bundle_axi_0] }
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
puts "== 单元已建 =="
close_bd_design
