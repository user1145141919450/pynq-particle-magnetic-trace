# ============================================================================
# probe_ip.tcl —— 挂 Digilent IP 仓库后，把三个 IP 的**真实参数名**查出来
#
# 为什么（2026-09-30）：
#   add_hdmi_out.tcl 里我给 v_tc 写的一批 C_GEN_* 参数被 Vivado 判为
#   "Parameter does not exist" —— 而且**只发 CRITICAL WARNING 不抛异常**，
#   我的 hm_set 用 catch 根本抓不到（静默失效的又一例）。
#   教训：参数名不能猜，要从 IP 目录里查。这个脚本就是那个"查"。
#
# 第一版的两个错（别再犯）：
#   · `get_ipdefs <全VLNV>` 匹配的是 **IP 名**不是 VLNV → 全空。要按 VLNV 查得用
#     `-filter {VLNV =~ "*xxx*"}`，或者直接拿 IP 名当 pattern。
#   · 拿 `[lindex $t 0]` 当 VLNV 用，$t 空的时候报 "Please specify VLNV"。
#
# 用法：
#   F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source probe_ip.tcl
# ============================================================================

proc pl {s} { puts $s }

pl "===== 0) 挂 IP 仓库 ====="
create_project -in_memory -part xc7z020clg400-1
set_property ip_repo_paths [list [file join [file dirname [file normalize [info script]]] ip_repo]] [current_project]
update_ip_catalog -rebuild
pl "  ip_repo_paths = [get_property ip_repo_paths [current_project]]"

pl ""
pl "===== 1) 按 VLNV 过滤，看 Digilent 到底进来哪些 ====="
set dg [get_ipdefs -all -quiet -filter {VLNV =~ "*digilent*"}]
foreach d [lsort $dg] { pl "    $d" }
pl "  （共 [llength $dg] 个 Digilent IP）"
pl "  接口类："
foreach d [lsort [get_ipdefs -all -quiet -filter {VLNV =~ "*tmds*"}]] { pl "    $d" }

pl ""
pl "===== 2) ★v_tc 6.2 生成器模式下的真实参数表★ ====="
create_bd_design probe_bd
create_bd_cell -type ip -vlnv xilinx.com:ip:v_tc:6.2 vtc_probe
set cell [get_bd_cells vtc_probe]
pl "  enable_detection  = [get_property CONFIG.enable_detection $cell]"
pl "  enable_generation = [get_property CONFIG.enable_generation $cell]"

pl ""
pl "  ---- 设 enable_detection=false 并回读 ----"
set r [catch { set_property CONFIG.enable_detection false $cell } e]
pl "    catch=$r  回读=[get_property CONFIG.enable_detection $cell]  ($e)"
pl "  ---- 设 enable_generation=true 并回读 ----"
set r [catch { set_property CONFIG.enable_generation true $cell } e]
pl "    catch=$r  回读=[get_property CONFIG.enable_generation $cell]  ($e)"
pl "  ---- 回读 C_GENERATE_EN / C_DETECT_EN ----"
foreach k {C_GENERATE_EN C_DETECT_EN} {
    set r [catch { get_property CONFIG.$k $cell } e]
    pl "    $k catch=$r 值=[expr {$r==0 ? $e : {<不存在>}}]"
}

pl ""
pl "  ★全部 CONFIG.* 参数（权威名单）★"
set all {}
foreach p [list_property $cell] {
    if {[string match "CONFIG.*" $p]} { lappend all [string range $p 7 end] }
}
foreach k [lsort $all] { pl "    $k = [get_property CONFIG.$k $cell]" }
pl "  （共 [llength $all] 个）"

pl ""
pl "===== 3) 我要用的那批参数逐个验：存不存在 + 设进去能不能读回 ====="
set want {C_GEN_HACTIVE_SIZE C_GEN_HFRAME_SIZE C_GEN_HSYNC_START C_GEN_HSYNC_END
          C_GEN_VACTIVE_SIZE C_GEN_VFRAME_SIZE C_GEN_HSYNC_POLARITY C_GEN_VSYNC_POLARITY
          C_GEN_AVIDEO_POLARITY C_GEN_HBLANK_POLARITY C_GEN_VBLANK_POLARITY
          C_GEN_AVIDEO_EN C_GEN_ACHROMA_EN C_GEN_CPARITY C_GEN_AUTO_SWITCH
          C_GEN_VIDEO_FORMAT C_GEN_INTERLACED C_GEN_HBLANK_EN C_GEN_HSYNC_EN
          C_GEN_VBLANK_EN C_GEN_VSYNC_EN C_GEN_F0_VSYNC_VSTART C_GEN_F0_VSYNC_VEND
          C_GEN_F0_VSYNC_HSTART C_GEN_F0_VSYNC_HEND C_GEN_F0_VBLANK_HSTART
          C_GEN_F0_VBLANK_HEND C_GEN_F0_VFRAME_SIZE C_GEN_F1_VSYNC_VSTART
          C_GEN_F1_VSYNC_VEND C_GEN_F1_VSYNC_HSTART C_GEN_F1_VSYNC_HEND
          C_GEN_F1_VFRAME_SIZE C_GEN_F1_VBLANK_HSTART C_GEN_F1_VBLANK_HEND
          C_GEN_FIELDID_EN C_GEN_ACHROMA_POLARITY
          C_HAS_AXI4_LITE C_HAS_INTC_IF C_NUM_FSYNCS C_INTERLACE_EN C_VID_PPC
          C_MAX_LINES C_MAX_PIXELS C_SYNC_EN C_ARBITRARY_RES_EN}
set miss {}; set good {}
foreach k $want {
    if {[lsearch -exact $all $k] < 0} { lappend miss $k } else { lappend good $k }
}
pl "  存在 [llength $good] 个 / 共 [llength $want]"
if {[llength $miss] > 0} {
    pl "  ★不存在 [llength $miss] 个："
    foreach k $miss { pl "      $k" }
} else { pl "  ★全部存在★" }

pl ""
pl "===== 4) 实测：把 720p 那套值写进去，逐个回读 ====="
# 值抄 base overlay 的 vtc_in（跑通的 1280x720）
foreach {k v} {
    C_GEN_HACTIVE_SIZE 1280  C_GEN_HFRAME_SIZE 1650
    C_GEN_HSYNC_START 1390   C_GEN_HSYNC_END 1430
    C_GEN_VACTIVE_SIZE 720   C_GEN_VFRAME_SIZE 750
    C_GEN_F0_VSYNC_VSTART 724 C_GEN_F0_VSYNC_VEND 729
    C_GEN_F0_VBLANK_HSTART 1390 C_GEN_F0_VBLANK_HEND 1390
    C_GEN_HBLANK_EN 1 C_GEN_HSYNC_EN 1 C_GEN_VBLANK_EN 1 C_GEN_VSYNC_EN 1
} {
    if {[lsearch -exact $all $k] < 0} { pl "    [format %-26s $k] 不存在 -> 跳过"; continue }
    set r [catch { set_property CONFIG.$k $v $cell } e]
    set rb [expr {$r==0 ? [get_property CONFIG.$k $cell] : "N/A"}]
    set ok [expr {$rb eq $v ? "OK " : "★没写进去★"}]
    pl "    [format %-26s $k] 写=$v 回读=$rb  $ok"
}

pl ""
pl "===== 5) axi_dynclk / rgb2dvi 的参数名 ====="
foreach {cellnm vlnv} {dynclk_probe digilentinc.com:ip:axi_dynclk:1.2
                       rgb2dvi_probe digilentinc.com:ip:rgb2dvi:1.4} {
    set r [catch { create_bd_cell -type ip -vlnv $vlnv $cellnm } e]
    if {$r != 0} { pl "  ★建不出 $vlnv : $e"; continue }
    set c [get_bd_cells $cellnm]
    set ks {}
    foreach p [list_property $c] {
        if {[string match "CONFIG.*" $p]} { lappend ks [string range $p 7 end] }
    }
    pl "  ---- $vlnv  （[llength $ks] 个参数）"
    foreach k [lsort $ks] { pl "      $k = [get_property CONFIG.$k $c]" }
}

pl ""
pl "PROBE DONE"
