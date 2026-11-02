# ============================================================================
# build_after_patch.tcl —— 补丁后的综合+实现（2026-10-02，HDMI 相位修复）
#
# 流程（按 timing_gate.tcl 头部推荐的正确顺序）：
#   1) open project（ip_repo 改指本地）
#   2) launch_runs synth_1          （无 .dcp 缓存 ⇒ 全部重综合）
#   3) launch_runs impl_1 -to_step route_design
#   4) source timing_gate.tcl       （WNS/WHS 必须 >= 0；违例自动改名+TIMING_FAILED）
#   5) launch_runs impl_1 -to_step write_bitstream   （只有过了才到这）
#   6) 打印最终状态 + bit 的 md5（和 deploy 里现役的对比用）
#
# 不做部署动作（deploy/ 的更新走 software/pc/deploy_check.py --fix，另行决定）。
# ============================================================================

set proj_dir [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a

# ★★ 2026-10-04 默认全新（血的教训，见文件尾注）：清 IP 缓存 + .gen ——
#   IPCACHE 会**静默复用 module_ref 的旧综合 dcp**：改 RTL 不会使缓存键失效、
#   reset_run 清不掉、日志只留一行 "Using cached IP synthesis design"。曾导致
#   "新 bit 里 V3 还是旧逻辑、时序门禁/md5/pl_probe 全绿"的事故（上板定妆照
#   17/17 不符才现形）。清掉后 generate_target 重生成 .gen（下面的缺 wrapper
#   自动补生成），整棵树全新综合——**改过 rtl/ 之后必须走这条路**。
file delete -force [file join $proj_dir proj $proj_name.cache]
file delete -force [file join $proj_dir proj $proj_name.gen]

open_project $proj_dir/proj/$proj_name.xpr
set_property ip_repo_paths [list $proj_dir/ip_repo] [current_project]
puts "INFO: BUILD: 工程已开，ip_repo -> $proj_dir/ip_repo"

# ---- 1b. ★顶层修复★（2026-10-02 换机踩坑）----
#   交接包把 BD wrapper（ov5640_m1a_wrapper.v，在 .gen 里）剔除了 ⇒ xpr 的
#   TopModule 成了 ov5640_top ⇒ 第一次构建 **综合的是那个采集小模块**
#   （18 秒"完成"、dcp 名字不对才被发现）。这里重新挂 wrapper + 显式钉顶层 +
#   两层门禁（设完查属性；综合后查 dcp 文件名必须叫 wrapper）。saved。
set wrapper $proj_dir/proj/$proj_name.gen/sources_1/bd/ov5640_m1a/hdl/ov5640_m1a_wrapper.v
# ★★ 2026-10-04：.gen 被清过（治 IPCACHE 旧 dcp 时连 .gen 一起清了）⇒ 这里先补生成。
#   注意 generate_target all 会把 BD 产物连同**全部 IP 的输出**重新生成——正是我们要的
#   "整棵树全新"（与清 .cache 配套）。
if {![file exists $wrapper]} {
    puts "INFO: BUILD: .gen 缺失 -> generate_target all 重新生成 BD 产物（清缓存后的正常一步）..."
    if {[catch {generate_target all [get_files ov5640_m1a.bd]} _ge]} {
        puts "ERROR: BUILD: generate_target 失败：$_ge"
        exit 1
    }
}
if {![file exists $wrapper]} {
    puts "ERROR: BUILD: 找不到 BD wrapper $wrapper（generate_target 没生成？）—— 停"
    exit 1
}
if {[llength [get_files -quiet *ov5640_m1a_wrapper.v]] == 0} {
    add_files -norecurse $wrapper
    puts "INFO: BUILD: wrapper 已加进工程"
}
update_compile_order -fileset sources_1
set_property top ov5640_m1a_wrapper [current_fileset]
update_compile_order -fileset sources_1
set top_now [get_property top [current_fileset]]
puts "INFO: BUILD: 顶层 = $top_now（必须是 ov5640_m1a_wrapper）"
if {$top_now ne "ov5640_m1a_wrapper"} {
    puts "ERROR: BUILD: 顶层没设成 wrapper（现在是 $top_now）—— 停，绝不带着错顶层综合"
    exit 1
}
# ★ save_project 在 2023.2 批量模式里不是独立命令（报 Missing value for 'name'）
#   ⇒ 容错即可：本脚本**幂等**（每次重设顶层）；launch_runs 自己会把工程状态落盘。
if {[catch {save_project} _se]} {
    puts "WARN: BUILD: save_project 不可用（$_se）—— 没关系，顶层每次都会重设"
}

# ★ 交接包里 impl/synth 的 .dcp 被剔除 ⇒ 运行目录里有"完成"标记但产物不存在，
#   launch_runs 会拒绝（needs to be reset）。先重置（清掉假完成态），再跑。
#   （上一轮已经把错顶层的结果写进 runs 了，这里一并清掉。）
# ★★ 2026-10-04 实锤（血的教训）：**只 reset 父级是不够的**！！
#   BD 里的 module_ref（v3_bundle_axi / vision_core / ov5640_top 等）有**独立 OOC
#   子运行**。改了它们的 RTL 之后，父级 relaunch **不会**自动重跑子运行——会
#   **静默复用子运行的旧 dcp**。症状：一切"绿"（时序过、md5 对、能下板），但新
#   bit 里的那块逻辑还是旧的（本次：max_bounce 修复没进 bit，板上实测 17/17 全
#   不符才发现）。⇒ 改过任何 RTL 后，**把所有 run 全部 reset**，让整棵树全新综合。
puts "INFO: BUILD: 重置全部 run（父级 + OOC 子运行；只 reset 父级会静默复旧 dcp）"
foreach _r [get_runs] {
    puts "INFO: BUILD:   reset_run $_r"
    reset_run $_r
}

# ---- 2. 综合 ----
puts "INFO: BUILD: ===== synth_1 开始 ====="
# ★ 2026-10-20：-jobs 4 → 2。本机 16GB，加入 map_detect 后 OOC 综合任务变多，
#   4 路并行实测 OOM（map_detect_axi / auto_pc_0 都报 "out of memory"）。
launch_runs synth_1 -jobs 2
wait_on_run synth_1
set st [get_property STATUS [get_runs synth_1]]
set pr [get_property PROGRESS [get_runs synth_1]]
puts "INFO: BUILD: synth_1 状态 = $st （进度 $pr）"
if {[string match "*ERROR*" $st] || $pr != "100%"} {
    puts "ERROR: BUILD: synth_1 没成功（$st）—— 停"
    exit 1
}
# ★门禁：综合产出的顶层 dcp 必须叫 wrapper（防"顶层被自动换掉"复发）
if {![file exists $proj_dir/proj/$proj_name.runs/synth_1/ov5640_m1a_wrapper.dcp]} {
    puts "ERROR: BUILD: synth_1 产物不是 ov5640_m1a_wrapper.dcp —— 顶层又被换了，停"
    exit 1
}
puts "INFO: BUILD: synth dcp = ov5640_m1a_wrapper.dcp ✓"

# ---- 3. 实现到 write_bitstream ----
# ★ 顺序实证（2026-10-03）：文档理想顺序"route→门禁→bit"走不通 ——
#   用 -to_step route_design 暂停的 run，`open_run impl_1` 会被拒
#   （"Run 'impl_1' has not been launched"，2026-10-02 实踩）。
#   项目里久经考验的顺序（rerun_impl.tcl 第 164-178 行）：**先跑完
#   write_bitstream，再 open_run 过门禁**；门禁自带"违例改名 .TIMING_FAILED"
#   的兜底。就按这个来。
puts "INFO: BUILD: ===== impl_1 (到 write_bitstream) 开始 ====="
# ★ 2026-10-22：删掉 JTAG 调试核 ila_0（706 LUT / 1.5 BRAM）后切片够了；
#   实现指令退回**时序优先**的基线（ExploreArea 虽然装得下，但 WNS -2.6 崩了）。
set_property STEPS.OPT_DESIGN.ARGS.DIRECTIVE Default [get_runs impl_1]
# ★★ 2026-10-31：v2.1 帧泵首次全量构建 ExtraTimingOpt 布图 WNS -0.049；
#   只重跑实现换 ExtraPostPlacementOpt 后 +0.085 过门禁 —— 固化这条赢家配方。
# ★ 2026-11-01：16 粒改用 ExtraNetDelay_high（ExtraPostPlacementOpt 布图拥塞 −1.035 → +0.094）
set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE ExtraNetDelay_high [get_runs impl_1]
set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE NoTimingRelaxation [get_runs impl_1]
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore [get_runs impl_1]
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
set pr [get_property PROGRESS [get_runs impl_1]]
puts "INFO: BUILD: impl_1 状态 = $st （进度 $pr）"
if {[string match "*ERROR*" $st] || [string match "*FAILED*" $st] || $pr != "100%"} {
    puts "ERROR: BUILD: impl_1 没跑完（$st / $pr）—— 停"
    exit 1
}
if {![file exists $proj_dir/proj/$proj_name.runs/impl_1/ov5640_m1a_wrapper_routed.dcp]} {
    puts "ERROR: BUILD: impl_1 没有 ov5640_m1a_wrapper_routed.dcp —— 顶层不对，停"
    exit 1
}

# ---- 4. 时序门禁（唯一实现，本文件与 rerun_impl.tcl 共用同一份）----
puts "INFO: BUILD: ===== 时序门禁 ====="
source [file join $proj_dir timing_gate.tcl]

# ---- 6. 记录产物 ----
set bit_file $proj_dir/proj/$proj_name.runs/impl_1/ov5640_m1a_wrapper.bit
if {[file exists $bit_file]} {
    puts "INFO: BUILD: bit 产物 = $bit_file"
} else {
    puts "ERROR: BUILD: 找不到 bit 产物 $bit_file"
    exit 1
}
puts "INFO: BUILD: ===== 全部完成 ====="
