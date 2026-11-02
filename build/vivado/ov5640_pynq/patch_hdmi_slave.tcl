# ============================================================================
# patch_hdmi_slave.tcl —— 原地修复 HDMI 画面随机水平偏移（2026-10-02 第十五例）
#
# 对**现有工程的 .bd** 打两处修改（与 bd/add_hdmi_out.tcl 里改的两处一一对应）：
#   ① v_axi4s_vid_out_0.C_VTG_MASTER_SLAVE : 1(Master) -> 0(Slave)
#   ② vtc_out/gen_clken 从恒 1 的 CE 网摘除 -> 改接 v_axi4s_vid_out_0/vtg_ce
# 然后过同款硬门禁 → validate → save。门禁不过就**不保存**、exit 1。
#
# 为什么不跑 build_project.tcl 全量重建：它用 apply_board_preset=1 建 PS7，
# 需要 PYNQ-Z2 板卡文件；本机没有、仓库没有、无外网。而现有工程里
# .bd + 全部 IP 实例（.xci）齐全 ⇒ 原地改是最短路径。（ip_repo_paths 由本脚本
# 改指工程自带 ip_repo/，仅寄存器会话内有效 + 保存到工程属性。）
#
# 用法：vivado.bat -mode batch -source patch_hdmi_slave.tcl
# 退出码：门禁不过/异常 = 非零（但 vivado.bat 的 rc 不可信 —— 看日志关键字）
# ============================================================================

set proj_dir [file dirname [file normalize [info script]]]
set ::fail 0
proc pk_gate {what cond} {
    if {!$cond} { puts "ERROR: PATCH: 门禁失败 —— $what"; set ::fail 1 }
}

open_project $proj_dir/proj/ov5640_m1a.xpr

# IP 仓库改指工程自带的 ip_repo（老机器的 E:/vivado-library 不在）
set local_repo $proj_dir/ip_repo
if {![file exists $local_repo/ip/rgb2dvi/component.xml]} {
    puts "ERROR: PATCH: 本地 ip_repo 不完整（$local_repo）"; exit 1
}
set_property ip_repo_paths [list $local_repo] [current_project]
update_ip_catalog -rebuild
puts "INFO: PATCH: ip_repo_paths -> $local_repo"

open_bd_design [get_files ov5640_m1a.bd]

# ---- 改前快照 ----
set ms0 [get_property CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
set gk0 [get_bd_nets -quiet -of_objects [get_bd_pins vtc_out/gen_clken]]
set vc0 [get_bd_nets -quiet -of_objects [get_bd_pins v_axi4s_vid_out_0/vtg_ce]]
puts "INFO: PATCH: 改前 MASTER_SLAVE=$ms0 gen_clken_net='$gk0' vtg_ce_net='$vc0'"

# ---- ① Master -> Slave ----
set_property CONFIG.C_VTG_MASTER_SLAVE 0 [get_bd_cells v_axi4s_vid_out_0]

# ---- ② gen_clken: 摘出 CE 网 -> 接 vtg_ce ----
if {[llength $gk0] > 0} {
    disconnect_bd_net $gk0 [get_bd_pins vtc_out/gen_clken]
}
connect_bd_net [get_bd_pins v_axi4s_vid_out_0/vtg_ce] [get_bd_pins vtc_out/gen_clken]

# ---- 改后门禁（判定不了 = 不通过）----
set ms1 [get_property -quiet CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
puts "INFO: PATCH: 改后 MASTER_SLAVE=$ms1"
set ok1 0
if {[catch {expr {$ms1 == 0}} ok1]} { set ok1 0; puts "ERROR: PATCH: MASTER_SLAVE='$ms1' 解析不了" }
pk_gate "C_VTG_MASTER_SLAVE != 0（还是 Master）" [expr {$ok1 != 0}]

set vc_pin [get_bd_pins -quiet v_axi4s_vid_out_0/vtg_ce]
set gk_pin [get_bd_pins -quiet vtc_out/gen_clken]
set ce_pin [get_bd_pins -quiet xl_hdmi_ce/dout]
if {[llength $vc_pin] == 0 || [llength $gk_pin] == 0 || [llength $ce_pin] == 0} {
    pk_gate "vtg_ce / gen_clken / CE 三个脚有缺失" 0
} else {
    set n_vc [get_bd_nets -quiet -of_objects $vc_pin]
    set n_gk [get_bd_nets -quiet -of_objects $gk_pin]
    set n_ce [get_bd_nets -quiet -of_objects $ce_pin]
    puts "INFO: PATCH: vtg_ce=$n_vc  gen_clken=$n_gk  CE=$n_ce"
    pk_gate "vtg_ce 与 gen_clken 不同网" \
            [expr {[llength $n_vc] > 0 && [string compare $n_vc $n_gk] == 0}]
    pk_gate "gen_clken 还在 CE 网上（没摘干净）" [expr {[string compare $n_gk $n_ce] != 0}]
    # CE 网其余成员应仍然挂着（别把别的东西一起摘掉了）
    foreach p {v_axi4s_vid_out_0/aclken v_axi4s_vid_out_0/vid_io_out_ce vtc_out/clken vtc_out/s_axi_aclken} {
        set pp [get_bd_pins -quiet $p]
        set np [get_bd_nets -quiet -of_objects $pp]
        pk_gate "$p 被误摘出了 CE 网" [expr {[llength $np] > 0 && [string compare $np $n_ce] == 0}]
    }
}

if {$::fail} {
    puts "ERROR: PATCH: ★门禁没过，**不保存**、保持原样★"
    exit 1
}

validate_bd_design
save_bd_design
puts "INFO: PATCH: BD 已保存（validate + save 完成）"

# 保存后复查一遍（防 save 又变回去）
set ms2 [get_property -quiet CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
puts "INFO: PATCH: 保存后复查 MASTER_SLAVE=$ms2（应为 0）"
puts "INFO: PATCH: ===== 打补丁完成 ====="
