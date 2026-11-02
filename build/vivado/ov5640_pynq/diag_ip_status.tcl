# diag_ip_status.tcl —— 只读：查 BD 里 IP 为什么被锁（交接后 .gen/.cache 缺失的后果）
set proj_dir [file dirname [file normalize [info script]]]
open_project $proj_dir/proj/ov5640_m1a.xpr
set_property ip_repo_paths [list $proj_dir/ip_repo] [current_project]
update_ip_catalog -rebuild
open_bd_design [get_files ov5640_m1a.bd]
report_ip_status -name locked -quiet
puts "INFO: ===== report_ip_status 完 ====="
