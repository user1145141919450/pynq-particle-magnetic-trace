# archive_project.tcl —— 导出“可打开的完整工程”归档（2026-10-31）
# 赛方要求“完整的工程文件与源码”；本脚本用 Vivado 官方 archive_project 打包：
#   .xpr + BD + 约束 + RTL + IP + 源文件（默认不含 impl/synth run 结果）。
# 用法：<vivado> -mode batch -source archive_project.tcl -tclargs <输出.zip>
set proj_dir [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a
set out [expr {[llength $argv] > 0 ? [lindex $argv 0] : \
               [file join $proj_dir deploy ov5640_m1a_project_archive.zip]}]
open_project $proj_dir/proj/$proj_name.xpr
puts "INFO: archiving -> $out"
archive_project $out -force
puts "DONE: [file size $out] bytes"
exit
