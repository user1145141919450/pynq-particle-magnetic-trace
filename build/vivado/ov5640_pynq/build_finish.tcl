# ============================================================================
# build_finish.tcl —— 续跑：impl_1 从"route 完成"续到 write_bitstream + 门禁
#                    （2026-10-03，HDMI 相位修复构建的收尾段）
#
# 背景：build_after_patch.tcl 里 synth_1 与 impl_1(route) 已完成
#       （routed WNS=+0.074 / WHS=+0.011），但 `open_run impl_1` 对"停在
#       route_design"的 run 被拒 ⇒ 改按 rerun_impl.tcl 的实证顺序：
#       先续跑到 write_bitstream、再 open_run 过门禁。
#       **不 reset、不重综合**（那两步的成果原样复用）。
# ============================================================================

set proj_dir [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a

open_project $proj_dir/proj/$proj_name.xpr
set_property ip_repo_paths [list $proj_dir/ip_repo] [current_project]

# 顶层再钉一次（幂等；防自动层级把顶层换掉的老坑）
if {[llength [get_files -quiet *ov5640_m1a_wrapper.v]] == 0} {
    set wrapper $proj_dir/proj/$proj_name.gen/sources_1/bd/ov5640_m1a/hdl/ov5640_m1a_wrapper.v
    add_files -norecurse $wrapper
    update_compile_order -fileset sources_1
}
set_property top ov5640_m1a_wrapper [current_fileset]
set top_now [get_property top [current_fileset]]
puts "INFO: FINISH: 顶层 = $top_now"
if {$top_now ne "ov5640_m1a_wrapper"} {
    puts "ERROR: FINISH: 顶层不是 wrapper —— 停"
    exit 1
}

# ---- 续跑到 write_bitstream ----
puts "INFO: FINISH: ===== impl_1 续跑（到 write_bitstream）====="
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
set pr [get_property PROGRESS [get_runs impl_1]]
puts "INFO: FINISH: impl_1 状态 = $st （进度 $pr）"
if {[string match "*ERROR*" $st] || [string match "*FAILED*" $st] || $pr != "100%"} {
    puts "ERROR: FINISH: impl_1 没跑完 —— 停"
    exit 1
}

# ---- 时序门禁（唯一实现）----
puts "INFO: FINISH: ===== 时序门禁 ====="
source [file join $proj_dir timing_gate.tcl]

# ---- 报告 ----
set bit_file $proj_dir/proj/$proj_name.runs/impl_1/ov5640_m1a_wrapper.bit
if {![file exists $bit_file]} {
    puts "ERROR: FINISH: 找不到 bit 产物"
    exit 1
}
puts "INFO: FINISH: bit = $bit_file"
puts "INFO: FINISH: ===== 完成 ====="
