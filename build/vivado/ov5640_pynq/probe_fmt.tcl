# ============================================================================
# probe_fmt.tcl —— 只问一件事：v_axi4s_vid_out 的输入**能不能是 32 位**？
#
# 背景（2026-09-30 实测）：
#   · C_ADDR_WIDTH = 10/11/12/13 全都得到 s_axis_video_tdata = 24 位（行缓冲参数，无关）
#   · VDMA MM2S 出的是 32 位流（base.hwh: C_M_AXIS_MM2S_TDATA_WIDTH=32）
#   ⇒ 要么 C_S_AXIS_VIDEO_FORMAT / C_S_AXIS_VIDEO_DATA_WIDTH 能配出 32 位，
#     要么就只能"接低位"（Vivado 会 WARNING: only lower order bits will be connected）。
#
# ★ 上一版 probe_pix.tcl 死在 C 段的引脚循环里（我的 Tcl 括号 bug：字符串里嵌 \[[expr …]）。
#   这版**不再往字符串里塞方括号**，先 set 到变量再拼，两个问题分开问。
#
# 用法：F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source probe_fmt.tcl
# ============================================================================

set here [file dirname [file normalize [info script]]]
set pdir $here/probe_fmt
file delete -force $pdir
file mkdir $pdir
create_project probe_fmt $pdir -part xc7z020clg400-1 -force
create_bd_design fmt
current_bd_design fmt

create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:4.0 v_ref
set c [get_bd_cells v_ref]

puts "=== 1) 三个格式参数的**允许值**（IP 自己说的，不是我猜的）==="
foreach p {CONFIG.C_S_AXIS_VIDEO_FORMAT CONFIG.C_S_AXIS_VIDEO_DATA_WIDTH \
           CONFIG.C_NATIVE_COMPONENT_WIDTH CONFIG.C_PIXELS_PER_CLOCK} {
    if {[catch { set av [list_property_value $p $c] } e]} { set av "(取不到: $e)" }
    puts "   $p 当前=[get_property $p $c]  允许=$av"
}

puts ""
puts "=== 2) 扫一遍组合，看哪种能出 32 位输入 ==="
# format 允许值通常 0..N；data_width 常见 8/10/12/16；每分量位数 x 分量数 = 输入位宽
set i 0
foreach fmt {0 1 2 3 4 5 6} {
    foreach dw {8 10 12 16} {
        set n "v_${fmt}_${dw}"
        set err [catch {
            create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:4.0 $n
            set_property -dict [list CONFIG.C_S_AXIS_VIDEO_FORMAT $fmt \
                                     CONFIG.C_S_AXIS_VIDEO_DATA_WIDTH $dw] [get_bd_cells $n]
        } emsg]
        if {$err} {
            # 组合非法是正常的，不刷屏
            catch { delete_bd_objs [get_bd_cells $n] }
            continue
        }
        set cc [get_bd_cells $n]
        set gf [get_property CONFIG.C_S_AXIS_VIDEO_FORMAT $cc]
        set gd [get_property CONFIG.C_S_AXIS_VIDEO_DATA_WIDTH $cc]
        set l  [get_property LEFT [get_bd_pins $n/s_axis_video_tdata]]
        set bits [expr {$l + 1}]
        set vl [get_property LEFT [get_bd_pins $n/vid_data]]
        set vbits [expr {$vl + 1}]
        puts "   format=$fmt data_width=$dw -> 实得 format=$gf dw=$gd  s_axis=[format %3d $bits]位  vid_data=$vbits 位"
        incr i
    }
}
puts "   （合法的组合共 $i 种）"

puts ""
puts "=== 3) 32→24 直连到底怎么接的（实证，不靠猜）==="
if {[catch {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 vd
    set_property -dict [list CONFIG.c_include_mm2s {1} CONFIG.c_include_s2mm {0} \
        CONFIG.c_m_axi_mm2s_data_width {32} CONFIG.c_m_axis_mm2s_tdata_width {32}] [get_bd_cells vd]
    create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:4.0 v24
    connect_bd_intf_net [get_bd_intf_pins vd/M_AXIS_MM2S] [get_bd_intf_pins v24/video_in]
    puts "   两边的 tdata 位宽：VDMA=[expr {[get_property LEFT [get_bd_pins vd/m_axis_mm2s_tdata]]+1}]  vid_out=[expr {[get_property LEFT [get_bd_pins v24/s_axis_video_tdata]]+1}]"
    puts "   两边 tkeep 位宽：VDMA=[expr {[get_property LEFT [get_bd_pins vd/m_axis_mm2s_tkeep]]+1}]  vid_out=[expr {[get_property LEFT [get_bd_pins v24/s_axis_video_tkeep]]+1}]"
    puts "   ---- 试着看引脚上挂的 net（Vivado 自己怎么接的）----"
    foreach pin [get_bd_pins -of_objects [get_bd_cells v24]] {
        set pn [get_property NAME $pin]
        if {![string match "*tdata*" $pn] && ![string match "*tkeep*" $pn]} { continue }
        set net [get_bd_nets -quiet -of_objects $pin]
        puts "   $pn  net=$net"
    }
} e]} { puts "   直连测试失败: [string map {\n { }} $e]" }

puts ""
puts "=== 4) v_pixel_pack 在不在（PYNQ 老版本用的那个宽度适配器）==="
set pp [get_ipdefs -all *v_pixel_pack*]
puts "   get_ipdefs *v_pixel_pack* -> $pp"
puts "DONE"
