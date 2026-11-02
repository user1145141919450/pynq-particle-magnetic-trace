# ============================================================================
# probe_pix.tcl —— 专探一件事：**32 位 VDMA 流怎么进 24 位的 v_axi4s_vid_out**
#
# 起因（2026-09-30）：probe_hdmi_ip.tcl 校验通过了，但夹着一条 CRITICAL WARNING：
#     TDATA_NUM_BYTES does not match between video_in(3) and M_AXIS_MM2S(4)
#     Width mismatch ... Only lower order bits will be connected.
#   —— "接了低位就放行" 是 Vivado 的**假通过**：每 4 字节丢 1 字节，图是烂的。
#   PYNQ base 里那条 pixel_unpack 就是干这个的。本探针把三件事问清楚：
#     A) v_axi4s_vid_out 的 C_ADDR_WIDTH 到底能不能配出 32 位输入？
#     B) 它的完整参数表（别再猜名字）
#     C) v_pixel_pack 的 VLNV/参数/引脚位宽（PYNQ base 的正解）
#
# 用法：F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source probe_pix.tcl
# ============================================================================

set here  [file dirname [file normalize [info script]]]
set pdir  $here/probe_pix
file delete -force $pdir
file mkdir $pdir

create_project probe_pix $pdir -part xc7z020clg400-1 -force
create_bd_design pix
current_bd_design pix

puts "=== A) v_axi4s_vid_out：请求 C_ADDR_WIDTH = N，看 s_axis_video_tdata 几位 ==="
set ref ""
foreach cw {8 9 10 11 12 13 14} {
    set n "vo_$cw"
    if {[catch {
        create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:4.0 $n
        set_property -dict [list CONFIG.C_ADDR_WIDTH $cw] [get_bd_cells $n]
        set l [get_property LEFT [get_bd_pins $n/s_axis_video_tdata]]
        set c [get_property CONFIG.C_ADDR_WIDTH [get_bd_cells $n]]
        puts "   请求 $cw -> 实得 [get_property CONFIG.C_ADDR_WIDTH [get_bd_cells $n]], s_axis_video_tdata = [expr {$l+1}] 位"
        if {$ref eq ""} { set ref $n }
    } err]} { puts "   请求 $cw -> 建不出来: [string map {\n { }} $err]" }
}

puts "=== B) 允许值列表（IP 自己说的）==="
if {$ref ne ""} {
    if {[catch { puts "   C_ADDR_WIDTH 允许: [list_property_value CONFIG.C_ADDR_WIDTH [get_bd_cells $ref]]" } e]} { puts "   (取不到允许值: $e)" }
}

puts "=== C) v_axi4s_vid_out 全参数（以 vo_11 为例）==="
if {$ref ne ""} {
    foreach p [lsort [list_property [get_bd_cells $ref]]] {
        if {[string match "CONFIG.*" $p]} { puts "   $p = [get_property $p [get_bd_cells $ref]]" }
    }
    puts "   ---- 引脚 ----"
    foreach pin [lsort [get_bd_pins -of_objects [get_bd_cells $ref]]] {
        set d [get_property DIR $pin]; set l [get_property LEFT $pin]
        if {$l ne "" && $l > 0} { puts "   [get_property NAME $pin] dir=$d \[[expr {$l+1}-1:0\]" } \
        else { puts "   [get_property NAME $pin] dir=$d" }
    }
}

puts "=== D) v_pixel_pack ==="
set ppvlnv [lindex [get_ipdefs -all *v_pixel_pack*] 0]
puts "   ipdef = $ppvlnv"
if {$ppvlnv ne ""} {
    if {[catch {
        create_bd_cell -type ip -vlnv $ppvlnv pp
        foreach p [lsort [list_property [get_bd_cells pp]]] {
            if {[string match "CONFIG.*" $p]} { puts "   $p = [get_property $p [get_bd_cells pp]]" }
        }
        puts "   ---- 引脚 ----"
        foreach pin [lsort [get_bd_pins -of_objects [get_bd_cells pp]]] {
            set d [get_property DIR $pin]; set l [get_property LEFT $pin]
            if {$l ne "" && $l > 0} { puts "   [get_property NAME $pin] dir=$d \[[expr {$l+1}-1:0\]" } \
            else { puts "   [get_property NAME $pin] dir=$d" }
        }
        puts "   ---- 接口 ----"
        foreach i [get_bd_intf_pins -of_objects [get_bd_cells pp]] {
            puts "   [get_property NAME $i] mode=[get_property MODE $i] vlnv=[get_property VLNV $i]"
        }
        puts "   ---- 所有 CONFIG 允许值 ----"
        foreach p [lsort [list_property [get_bd_cells pp]]] {
            if {[string match "CONFIG.*" $p]} {
                if {[catch { set av [list_property_value $p [get_bd_cells pp]] } e]} { set av "(n/a)" }
                puts "   $p 允许: $av"
            }
        }
    } err]} { puts "   建不出来: [string map {\n { }} $err]" }
}

puts "=== E) 真接一遍：VDMA(32) -> pixel_pack -> v_axi4s_vid_out(24) ==="
if {[catch {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 vdma
    set_property -dict [list CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {0} \
        CONFIG.c_m_axi_mm2s_data_width {32} CONFIG.c_m_axis_mm2s_tdata_width {32} \
        CONFIG.c_mm2s_linebuffer_depth {512} CONFIG.c_mm2s_max_burst_length {32}] [get_bd_cells vdma]
    connect_bd_intf_net [get_bd_intf_pins vdma/M_AXIS_MM2S] [get_bd_intf_pins pp/vid_in]
    puts "   VDMA -> pp/vid_in 接上了"
} err]} { puts "   接线失败（引脚名可能不同）: [string map {\n { }} $err]" }

puts "=== F) v_proc_ss / color_convert 在 base 里是怎么配的（只问 ipdef 有没有） ==="
puts "   [get_ipdefs -all *v_proc_ss*]"
puts "DONE"
