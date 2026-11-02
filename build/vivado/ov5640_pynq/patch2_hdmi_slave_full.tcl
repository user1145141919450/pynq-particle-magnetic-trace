# ============================================================================
# patch2_hdmi_slave_full.tcl —— HDMI 相位修复·完整版（2026-10-02）
#
# 在 patch_hdmi_slave.tcl 基础上补上"locked IP"的处理：
#   交接后 .gen/.cache 缺失 ⇒ 打开工程时 15 个 IP 处于 locked 状态 ⇒
#   validate_bd_design 被拒（[BD 5-336]）。标准解法：
#     ① 报告锁定原因（report_ip_status 写文件留档）
#     ② upgrade_ip（对版本漂移的 IP）
#     ③ generate_target all（重新生成 BD + 全部 IP 输出 —— 这本来综合前
#        也要做，一步不亏）
#   然后才 validate + save。
#
# 修改本体（与 bd/add_hdmi_out.tcl 的三处改动一一对应）：
#   ① C_VTG_MASTER_SLAVE 1 -> 0
#   ② gen_clken 从恒 1 CE 网 -> vtg_ce
#   ③ 就地门禁（同网/未回挂/参数回读）
#
# 全程日志：本目录 patch2_hdmi_slave_*.log（命令行重定向）
# ============================================================================

set proj_dir [file dirname [file normalize [info script]]]
set ::fail 0
proc pk_gate {what cond} {
    if {!$cond} { puts "ERROR: PATCH2: 门禁失败 —— $what"; set ::fail 1 }
}

open_project $proj_dir/proj/ov5640_m1a.xpr
set_property ip_repo_paths [list $proj_dir/ip_repo] [current_project]
update_ip_catalog -rebuild
open_bd_design [get_files ov5640_m1a.bd]

# ---- 0. 锁定报告（留档）----
report_ip_status -file $proj_dir/ip_status_20261002.rpt
puts "INFO: PATCH2: ip_status 报告已写 $proj_dir/ip_status_20261002.rpt"
set nlocked 0
foreach ip [get_ips -quiet] {
    set lk [get_property -quiet IS_LOCKED $ip]
    if {$lk == 1 || $lk eq "true" || $lk eq "TRUE"} {
        incr nlocked
        puts "INFO: PATCH2: LOCKED -> $ip"
    }
}
puts "INFO: PATCH2: 共 $nlocked 个 IP 处于 locked"

# ---- 1. 解锁尝试：upgrade_ip（版本漂移类锁定）----
if {$nlocked > 0} {
    if {[catch {upgrade_ip -quiet [get_ips -quiet]} e]} {
        puts "WARN: PATCH2: upgrade_ip 报错（可能是非版本类锁定）：$e"
    }
}

# ---- 2. 修改本体 ----
set ms0 [get_property CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
set gk0 [get_bd_nets -quiet -of_objects [get_bd_pins vtc_out/gen_clken]]
puts "INFO: PATCH2: 改前 MASTER_SLAVE=$ms0 gen_clken_net='$gk0'"

set_property CONFIG.C_VTG_MASTER_SLAVE 0 [get_bd_cells v_axi4s_vid_out_0]
if {[llength $gk0] > 0} {
    disconnect_bd_net $gk0 [get_bd_pins vtc_out/gen_clken]
}
connect_bd_net [get_bd_pins v_axi4s_vid_out_0/vtg_ce] [get_bd_pins vtc_out/gen_clken]

# ---- 3. 重新生成（解锁 + 产出新网表）----
puts "INFO: PATCH2: generate_target all 开始（第一次会比较久）..."
if {[catch {generate_target all [get_files ov5640_m1a.bd]} e]} {
    puts "ERROR: PATCH2: generate_target 失败：$e"
    exit 1
}
puts "INFO: PATCH2: generate_target all 完成"

# ---- 4. 改后门禁（generation 之后再验一遍）----
set ms1 [get_property -quiet CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
set ok1 0
if {[catch {expr {$ms1 == 0}} ok1]} { set ok1 0 }
pk_gate "C_VTG_MASTER_SLAVE != 0（回读=$ms1）" [expr {$ok1 != 0}]

set vc_pin [get_bd_pins -quiet v_axi4s_vid_out_0/vtg_ce]
set gk_pin [get_bd_pins -quiet vtc_out/gen_clken]
set ce_pin [get_bd_pins -quiet xl_hdmi_ce/dout]
if {[llength $vc_pin] == 0 || [llength $gk_pin] == 0 || [llength $ce_pin] == 0} {
    pk_gate "vtg_ce / gen_clken / CE 三个脚有缺失" 0
} else {
    set n_vc [get_bd_nets -quiet -of_objects $vc_pin]
    set n_gk [get_bd_nets -quiet -of_objects $gk_pin]
    set n_ce [get_bd_nets -quiet -of_objects $ce_pin]
    puts "INFO: PATCH2: vtg_ce=$n_vc  gen_clken=$n_gk  CE=$n_ce"
    pk_gate "vtg_ce 与 gen_clken 不同网" \
            [expr {[llength $n_vc] > 0 && [string compare $n_vc $n_gk] == 0}]
    pk_gate "gen_clken 还在 CE 网上" [expr {[string compare $n_gk $n_ce] != 0}]
    foreach p {v_axi4s_vid_out_0/aclken v_axi4s_vid_out_0/vid_io_out_ce vtc_out/clken vtc_out/s_axi_aclken} {
        set pp [get_bd_pins -quiet $p]
        set np [get_bd_nets -quiet -of_objects $pp]
        pk_gate "$p 被误摘出了 CE 网" [expr {[llength $np] > 0 && [string compare $np $n_ce] == 0}]
    }
}
if {$::fail} {
    puts "ERROR: PATCH2: ★门禁没过，**不保存**★"
    exit 1
}

# ---- 5. validate + save ----
validate_bd_design
save_bd_design
puts "INFO: PATCH2: BD 已保存"

set ms2 [get_property -quiet CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
puts "INFO: PATCH2: 保存后复查 MASTER_SLAVE=$ms2（应为 0）"
puts "INFO: PATCH2: ===== 完成 ====="
