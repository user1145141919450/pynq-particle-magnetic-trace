# ============================================================================
# probe_slave_fix.tcl —— 只读探针：验证本机能打开现有工程 + 查看 HDMI 相位修复
#                       相关的 BD 现状（2026-10-02，换机后第一次动 Vivado）
#
# 背景（为什么走"原地改 BD"而不是 build_project.tcl 全量重建）：
#   build_project.tcl 第 46-48 行用 `apply_board_preset "1"` 建 PS7 —— 这需要
#   **PYNQ-Z2 的板卡文件**（tul.com.tw:pynq-z2）。本机 Vivado 没装它、仓库里
#   也没有、且无外网 ⇒ 全量重建不可行。
#   而现有 proj/ 里 .bd + 全部 40 个 IP 实例（.xci）都在 ⇒ **原地改 BD** 可行。
#   （ip_repo_paths 原指向老机器的 E:/vivado-library，这里改指工程自带的
#     ip_repo/ —— 里面有 axi_dynclk 1.2 + rgb2dvi 1.4 + tmds 接口定义。）
#
# 本脚本**不改任何东西、不保存工程**（ip_repo_paths 的修改不落盘），只打印。
# ============================================================================

set proj_dir [file dirname [file normalize [info script]]]
puts "INFO: proj_dir = $proj_dir"

open_project $proj_dir/proj/ov5640_m1a.xpr

puts "INFO: board_part    = [get_property board_part [current_project]]"
puts "INFO: ip_repo_paths = [get_property ip_repo_paths [current_project]]"

# 把 IP 仓库路径改指本工程自带的 ip_repo（不保存，仅本次会话有效）
set local_repo $proj_dir/ip_repo
if {[file exists $local_repo/ip/rgb2dvi/component.xml]} {
    set_property ip_repo_paths [list $local_repo] [current_project]
    update_ip_catalog -rebuild
    puts "INFO: ip_repo_paths 已改指 $local_repo 并 update_ip_catalog -rebuild"
    foreach vlnv {digilentinc.com:ip:axi_dynclk:1.2 digilentinc.com:ip:rgb2dvi:1.4} {
        set n [llength [get_ipdefs -all -quiet -filter "VLNV =~ \"$vlnv\""]]
        puts "INFO: 目录里 $vlnv -> [expr {$n > 0 ? "在" : "★不在★"}]"
    }
} else {
    puts "WARN: 本地 ip_repo 不完整：$local_repo/ip/rgb2dvi/component.xml 不存在"
}

# 打开 BD，打印修复相关的现状
set bd_file [get_files ov5640_m1a.bd]
puts "INFO: bd_file = $bd_file"
open_bd_design $bd_file

set ms [get_property CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
puts "INFO: v_axi4s_vid_out_0.C_VTG_MASTER_SLAVE = $ms （1=Master 是错误的现状，0=Slave 是目标）"

set ce_net [get_bd_nets -quiet -of_objects [get_bd_pins xl_hdmi_ce/dout]]
set gk_net [get_bd_nets -quiet -of_objects [get_bd_pins vtc_out/gen_clken]]
set vc_net [get_bd_nets -quiet -of_objects [get_bd_pins v_axi4s_vid_out_0/vtg_ce]]
puts "INFO: xl_hdmi_ce/dout 网      = $ce_net"
puts "INFO: vtc_out/gen_clken 网    = $gk_net （现状应=CE 网；目标=vtg_ce 网）"
puts "INFO: v_axi4s_vid_out_0/vtg_ce 网 = $vc_net （现状应为空=悬空）"

# vtg_ce 脚存在性（防版本差异）
set vc_pin [get_bd_pins -quiet v_axi4s_vid_out_0/vtg_ce]
puts "INFO: vtg_ce 脚存在: [expr {[llength $vc_pin] > 0}]"

puts "INFO: ===== 探针结束（没有改任何东西，没有保存）====="
