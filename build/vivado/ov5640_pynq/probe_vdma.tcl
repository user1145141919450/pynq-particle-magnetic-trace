# ============================================================================
# probe_vdma.tcl —— 把 HDMI 通路里三个 Xilinx IP 的**真实 CONFIG 参数名**查出来
#
# 为什么（2026-09-30）：add_hdmi_out.tcl 里 axi_vdma 的参数我抄了 base overlay
#   的 hwh，其中 `c_m_axis_mm2s_tuser_bits` / `c_s_axi_lite_addr_width` 被判不存在。
#   看 hwh 就明白了：axi_vdma 的参数**大小写是混的** ——
#       c_include_mm2s / c_num_fstores ...        （小写）
#       C_M_AXIS_MM2S_TUSER_BITS / C_S_AXI_LITE_ADDR_WIDTH ...（大写，多半是派生量）
#   抄的时候只抄了一半。这个脚本把"能设的"挨个试一遍，谁真能设进去一目了然。
#
# 用法：
#   F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source probe_vdma.tcl
# ============================================================================
proc pl {s} { puts $s }

create_project -in_memory -part xc7z020clg400-1
set_property ip_repo_paths [list [file join [file dirname [file normalize [info script]]] ip_repo]] [current_project]
update_ip_catalog -rebuild
create_bd_design probe2_bd

# ---- axi_vdma 6.3：按我们实际用的构型（MM2S-only）----
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 vdma_p
set c [get_bd_cells vdma_p]
set all {}
foreach p [list_property $c] {
    if {[string match "CONFIG.*" $p]} { lappend all [string range $p 7 end] }
}
pl "===== axi_vdma 6.3：CONFIG 参数 [llength $all] 个 ====="
# 挨个试：只写"值没变/能写进去"的不算，真正要的是**试写后值变了或没报 disabled**
foreach k [lsort $all] {
    set cur [get_property CONFIG.$k $c]
    # 试写当前值本身，看是否被 ignored（disabled 参数会打 WARNING）
    pl "    $k = $cur"
}

pl ""
pl "===== 关键几个：真名到底长什么样 ====="
foreach pat {*tuser* *user_bits* *s_axi_lite_addr* *addr_width* *tdata_width* *fstores* *linebuffer*} {
    set hits {}
    foreach k $all { if {[string match $pat $k]} { lappend hits $k } }
    pl "    $pat -> $hits"
}

pl ""
pl "===== 实测：把我原本想设的两个值写进去，看行不行 ====="
foreach {k v} {C_M_AXIS_MM2S_TUSER_BITS 1 c_m_axis_mm2s_tuser_bits 1
               C_S_AXI_LITE_ADDR_WIDTH 9  c_s_axi_lite_addr_width 9} {
    if {[lsearch -exact $all $k] < 0} {
        pl "    [format %-28s $k] ★参数不存在★"
        continue
    }
    set r [catch { set_property CONFIG.$k $v $c } e]
    set rb [get_property CONFIG.$k $c]
    pl "    [format %-28s $k] 写=$v catch=$r 回读=$rb"
}

pl ""
pl "===== v_axi4s_vid_out 4.0 ====="
create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:4.0 avo_p
set c2 [get_bd_cells avo_p]
foreach p [lsort [list_property $c2]] {
    if {[string match "CONFIG.*" $p]} { pl "    [string range $p 7 end] = [get_property $p $c2]" }
}

pl ""
pl "===== axi_gpio 2.0（HPD 那个，全输出 1 位）====="
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 gpio_p
set c3 [get_bd_cells gpio_p]
foreach p [lsort [list_property $c3]] {
    if {[string match "CONFIG.*" $p]} { pl "    [string range $p 7 end] = [get_property $p $c3]" }
}

pl ""
pl "PROBE2 DONE"
