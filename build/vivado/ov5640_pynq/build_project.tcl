# ============================================================================
# build_project.tcl — PYNQ-Z2 OV5640 采集工程 (M1a) 一键构建脚本
#
# 用法（Git Bash 或 cmd 均可）：
#   仅生成 BD 验证脚本（快，约 2~5 分钟）：
#     F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source build_project.tcl -tclargs bd_only
#   全流程（BD + 综合 + 实现 + bit，约 15~30 分钟）：
#     F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source build_project.tcl
#
# 工程定位：xc7z020clg400-1 + PYNQ-Z2 板卡文件（PS7 用板卡 preset）
# 目录结构：
#   rtl/ov5640/   OV5640 全套 RTL（自旧工程 Lesson10_A_OV5640_VDMA 移植，未改动）
#   bd/m1a_capture.tcl   BD 脚本（本脚本第 2 步调用）
#   constrs/ov5640_pynq.xdc   PMOD 引脚约束
#   proj/          生成的 Vivado 工程
# ============================================================================

set proj_dir  [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a
set build_target all
if {[llength $argv] > 0} { set build_target [lindex $argv 0] }

# ★ 2026-11-01：2026.x 迁移用——PYNQ-Z2 板卡文件目录（board.repoPaths）。
#   2026.1 的 PS7 IP 需要板卡定义才能接受 MIO bank 1 = 1.8V（PYNQ-Z2 以太网）；
#   不设 BOARD_REPO 时行为与 2023.2 完全一致（不动）。
if {[info exists ::env(BOARD_REPO)] && $::env(BOARD_REPO) ne ""} {
    set_param board.repoPaths [split $::env(BOARD_REPO) ";"]
    puts "INFO: board.repoPaths = [get_param board.repoPaths]"
}

# ---- 1. 建工程 ----
create_project $proj_name $proj_dir/proj -part xc7z020clg400-1 -force
# 挂 PYNQ-Z2 板卡文件（给 PS7 提供 DDR/UART 等 preset；板卡文件在
# F:/Xilinx2022/Vivado/2023.2/data/boards/board_files/pynq-z2/A.0，已装好）
if {[catch {set_property board_part tul.com.tw:pynq-z2:part0:1.0 [current_project]} bp]} {
    puts "WARN: board_part 设置失败（PS7 preset 可能不可用）: $bp"
} else {
    puts "INFO: board_part = [get_property board_part [current_project]]"
}

# ---- 1b. ★Digilent IP 仓库★（HDMI 输出通路要的 axi_dynclk / rgb2dvi）----
# 起因（2026-09-30）：这两个 IP **不在 Vivado 标准安装里**（data/ip 下只有
#   xilinx / xpm / partners / interfaces ...，没有 digilentinc.com），
#   所以 create_bd_cell 会直接报 "IP definition not found for VLNV"。
# PYNQ-Z2 的 HDMI 输出必须用 Digilent 的 rgb2dvi 做 TMDS 编码 —— 板文件
#   board.xml 里 "hdmi_out" 接口的 preferred_ip 写的就是它，不是我挑的。
# 仓库 = Digilent/vivado-library（GitHub 在**本机不可达**，走 gitee 镜像克隆），
#   里面 ip/axi_dynclk:1.2 + ip/rgb2dvi:1.4 + if/tmds_v1_0（tmds / tmds_rtl 接口定义）。
# ★ 必须放在 source BD 脚本**之前**，否则 IP 还没进目录。
# ★ 老毛病提醒：上面那句 "IP definition not found" 是 ERROR，但 **vivado.bat
#   照样返回 exit=0** —— 所以这里挂完立刻回查一遍，见下面的硬门禁。
# ★ 2026-10-30：优先用**工程自带**的 ip_repo（换机后老机器的 E:/vivado-library 不在；
#   ip_repo 里的 axi_dynclk/rgb2dvi 是当年从那个仓库拷进来的同一份）。
set digilent_repo [file join $proj_dir ip_repo]
# ★ 2026-10-31：去掉本机绝对路径。工程自带 ip_repo 是首选；若缺失，用环境变量
#   DIGILENT_REPO 指到 Digilent vivado-library 克隆目录（任何路径都行）。
if {![file exists $digilent_repo/ip/rgb2dvi/component.xml]
        && [info exists ::env(DIGILENT_REPO)] && $::env(DIGILENT_REPO) ne ""} {
    set digilent_repo $::env(DIGILENT_REPO)
}
if {![file exists $digilent_repo/ip/rgb2dvi/component.xml]} {
    puts "ERROR: 找不到 Digilent IP 仓库（工程 ip_repo 里没有 rgb2dvi）：$digilent_repo"
    puts "ERROR:   HDMI 输出通路需要它（axi_dynclk + rgb2dvi）。获取："
    puts "ERROR:   git clone --depth 1 https://gitee.com/voiue/vivado-library.git <任意目录>"
    puts "ERROR:   然后 set DIGILENT_REPO=<该目录> 再跑本脚本。"
    exit 1
}
set_property ip_repo_paths [list $digilent_repo] [current_project]
update_ip_catalog -rebuild

# ★硬门禁：仓库"挂了"不等于"目录里能查到"。查不到立刻停，别把没 IP 的 BD 带下去。
foreach vlnv {digilentinc.com:ip:axi_dynclk:1.2 digilentinc.com:ip:rgb2dvi:1.4} {
    if {[llength [get_ipdefs -all -quiet -filter "VLNV =~ \"$vlnv\""]] == 0} {
        puts "ERROR: IP 目录里查不到 $vlnv —— 仓库挂了但 update_ip_catalog 没吃到"
        exit 1
    }
}
puts "INFO: Digilent IP 仓库就位（axi_dynclk 1.2 + rgb2dvi 1.4）-> $digilent_repo"

# ---- 1c. ★HDMI 驱动文件的前置门禁★（2026-09-30）----
# 部署包的拷贝在第 6 步（构建最末）。要是那时候才发现文件不在，
# **一小时的综合已经白跑了**。所以把"在不在"挪到这里先查，
# 拷贝还是留在第 6 步 —— 早失败，晚拷贝。
foreach s {hdmi_out.py dynclk_tables.py} {
    if {![file exists $proj_dir/../../software/board/$s]} {
        puts "ERROR: 部署包要的 $s 不在 software/board/ —— HDMI 驱动上不了板，先别跑综合"
        puts "ERROR:   dynclk_tables.py 是**生成**的：cd software/pc && python gen_dynclk_tables.py"
        exit 1
    }
}
puts "INFO: HDMI 驱动文件就位（hdmi_out.py + dynclk_tables.py）"

# ---- 2. 加 RTL 源（BD 里模块引用的定义必须先入工程）----
add_files [glob -nocomplain $proj_dir/rtl/ov5640/*.v]
add_files [glob -nocomplain $proj_dir/rtl/debug/*.v]

# V3 并行轨迹束（**显式列三份**，不能用 glob —— 目录里还有 tb_*.v 和旧版
# traj_lane_1clock_verified.v，glob 会把同名模块 traj_lane 一起拉进来冲突）
add_files -norecurse [list \
    $proj_dir/rtl/v3_bundle/traj_lane.v \
    $proj_dir/rtl/v3_bundle/traj_bundle.v \
    $proj_dir/rtl/v3_bundle/v3_bundle_axi.v ]
puts "INFO: V3 并行轨迹束 RTL 已加入工程"
# ★ 2026-10-30：PL 画线引擎（轨迹渲染下移）—— 画线核 + AXI 写主 + 控制口
add_files -norecurse [list \
    $proj_dir/rtl/v3_bundle/line_draw.v \
    $proj_dir/rtl/v3_bundle/axi_wr_master.v \
    $proj_dir/rtl/v3_bundle/line_engine.v \
    $proj_dir/rtl/v3_bundle/line_ctrl_axi.v \
    $proj_dir/rtl/v3_bundle/line_engine_top.v \
    $proj_dir/rtl/v3_bundle/frame_pump.v ]
puts "INFO: PL 画线引擎 RTL 已加入工程"
# ★★ 2026-10-31：粒子引擎（pe_axi）与 PL 检测（map_detect_axi）的 RTL 也必须在工程里
#   —— 它们一直是由补丁脚本往**已有工程**加的，从零重建时 BD 引用不到模块，
#   10-30 的构建就是这样把它们弄丢的（新旧 hwh 对账发现）。显式列，别用 glob
#   （map_detect/ 里有 tb_*.v，v3_bundle/ 里有同名模块的旧版）。
add_files -norecurse [list \
    $proj_dir/rtl/v3_bundle/particle_engine.v \
    $proj_dir/rtl/v3_bundle/pe_axi.v \
    $proj_dir/rtl/map_detect/map_detect_core.v \
    $proj_dir/rtl/map_detect/map_cc.v \
    $proj_dir/rtl/map_detect/map_detect_axi.v ]
puts "INFO: 粒子引擎 + PL 检测 RTL 已加入工程"
puts "INFO: RTL 文件 [llength [glob -nocomplain $proj_dir/rtl/ov5640/*.v]] 个已加入"

# ---- 3. 生成 BD ----
# ★ 2026.2 迁移：BD_TCL 环境变量可选（默认 2023.2 锁版本脚本）
#   2026.2：先 `python tools/make_latest_ip_bd.py` 生成 bd/m1a_capture_latest.tcl，
#   再 `BD_TCL=m1a_capture_latest.tcl PS7_STRICT=1` 跑本脚本。
set bd_tcl "m1a_capture.tcl"
if {[info exists ::env(BD_TCL)] && $::env(BD_TCL) ne ""} {
    set bd_tcl $::env(BD_TCL)
}
puts "INFO: BD 脚本 = bd/$bd_tcl"
source [file join $proj_dir bd $bd_tcl]

# ---- 3b. ★BD 自检★ ----
# 起因（2026-09-23）：BD 脚本里 blk_mem_gen 的 Memory_Type 写了 Vivado 2023.2
# 不认的值，脚本中途报错退出，**但 vivado.bat 仍返回 exit=0**，命令行看着像成功。
# 这种"静默半成品"最容易把坏 BD 带进综合。故硬性检查关键单元是否真建出来了。
foreach c {vision_core_0 axi_bram_ctrl_vision blk_mem_gen_vision axi_gpio_vision axi_gpio_vstat \
           axi_vdma_out v_axi4s_vid_out_0 vtc_out axi_dynclk rgb2dvi_0 hdmi_out_hpd_video axi_mem_intercon_out} {
    if {[llength [get_bd_cells -quiet $c]] == 0} {
        puts "ERROR: BD 自检失败 —— $c 没建出来（BD 脚本中途报错了，翻上面的日志）"
        exit 1
    }
}
puts "INFO: BD 自检通过（视觉前处理 V1 的 5 个单元都在）"

# ---- 3b-2. ★无源引脚门禁★ ----
# 起因（2026-09-23/24）：本工程反复被同一类问题坑 —— **引脚没接驱动/没被接走，
# Vivado 只给 WARNING，合成照过，上板才表现为"读回恒 0 / 恒常数"**，而且看不出
# 是接线问题还是逻辑问题。这里把"关键引脚必须真有网络"变成硬门禁：
#   · vision_core 的每个输入都必须有驱动（否则 freeze 恒 0 之类会静默失效）
#   · 两个状态字 stat/stat2 必须分别接到 gpio 的不同输入引脚（不能漏接、不能短路）
foreach pn {aclk aresetn freeze soft_rst swap_bytes} {
    set p [get_bd_pins -quiet vision_core_0/$pn]
    if {[llength $p] == 0} {
        puts "ERROR: 无源引脚门禁失败 —— vision_core_0/$pn 这个引脚根本不存在"
        puts "ERROR:   （端口名改了但 BD 没跟着改？）"
        exit 1
    }
    if {[llength [get_bd_nets -quiet -of_objects $p]] == 0} {
        puts "ERROR: 无源引脚门禁失败 —— vision_core_0/$pn 没接任何网络"
        puts "ERROR:   （悬空输入会静默失效，合成不会报错）"
        exit 1
    }
}
# ★ 流接口要连**接口引脚**（vision_core_0/s_axis 本身），查**接口网**。
#   本门禁第一版拿子引脚（s_axis_tdata）去查 get_bd_nets / get_bd_intf_nets，
#   两种都返回空 —— 接口成员的连接不挂在标量子引脚上，于是把接好的流转接
#   误判成"悬空"，白跑一轮 BD（2026-09-24 实测）。
foreach ip {s_axis m_axis} {
    set p [get_bd_intf_pins -quiet vision_core_0/$ip]
    if {[llength $p] == 0} {
        puts "ERROR: 无源引脚门禁失败 —— vision_core_0/$ip 这个接口引脚不存在"
        exit 1
    }
    if {[llength [get_bd_intf_nets -quiet -of_objects $p]] == 0} {
        puts "ERROR: 无源引脚门禁失败 —— vision_core_0/$ip 没接任何接口网"
        puts "ERROR:   （采集链会断在这里，且可能只报 WARNING）"
        exit 1
    }
}
set n_stat  [get_bd_nets -quiet -of_objects [get_bd_pins axi_gpio_vstat/gpio_io_i]]
set n_stat2 [get_bd_nets -quiet -of_objects [get_bd_pins axi_gpio_vstat/gpio2_io_i]]
if {[llength $n_stat] == 0 || [llength $n_stat2] == 0} {
    puts "ERROR: 无源引脚门禁失败 —— axi_gpio_vstat 的 stat/stat2 输入没接全"
    exit 1
}
if {[string compare $n_stat $n_stat2] == 0} {
    puts "ERROR: 无源引脚门禁失败 —— stat 与 stat2 接到同一条网上（短路了）"
    exit 1
}
puts "INFO: 无源引脚门禁通过（vision_core 输入全有驱动；stat/stat2 各自独立接线）"

# ---- 3c. ★采集链自检★ ----
# 起因（2026-09-23）：想在 BD 里"只读观察"采集流，于是用 connect_bd_net 单独去接
# 桥的 m_axis_* 子引脚 —— 结果把这些引脚从原接口连接里**抢断**了（BD 41-1271），
# 桥的 m_axis_tready 变成无源、被 tie 到 0（BD 41-759），整条采集链会死。
# **这类问题只是 CRITICAL WARNING，不会让构建失败** —— 差点烧出一块砖。
# 故把"采集链接口是否真的连成一条"变成硬门禁。
foreach pr {
    {dvp2axis_bridge_0/m_axis vision_core_0/s_axis}
    {vision_core_0/m_axis axis_probe_0/s_axis}
    {axis_probe_0/m_axis axi_vdma_0/S_AXIS_S2MM}
} {
    set a [get_bd_intf_pins [lindex $pr 0]]
    set b [get_bd_intf_pins [lindex $pr 1]]
    if {[llength $a] == 0 || [llength $b] == 0} {
        puts "ERROR: 采集链自检失败 —— 引脚不存在：[lindex $pr 0] <-> [lindex $pr 1]"
        exit 1
    }
    set na [get_bd_intf_nets -quiet -of_objects $a]
    set nb [get_bd_intf_nets -quiet -of_objects $b]
    if {[llength $na] == 0 || [llength $nb] == 0 || [string compare $na $nb] != 0} {
        puts "ERROR: 采集链自检失败 —— [lindex $pr 0] 与 [lindex $pr 1] 不在同一条接口网上"
        puts "ERROR:   多半是被某条 connect_bd_net 抢断了（查 BD 41-1271/41-759 警告）"
        exit 1
    }
}
puts "INFO: 采集链自检通过（桥 → vision_core → axis_probe → VDMA 连成一条）"

# ---- 4. wrapper + 顶层 + 约束 ----
make_wrapper -files [get_files $proj_dir/proj/$proj_name.srcs/sources_1/bd/ov5640_m1a/ov5640_m1a.bd] -top
add_files -norecurse $proj_dir/proj/$proj_name.srcs/sources_1/bd/ov5640_m1a/hdl/ov5640_m1a_wrapper.v
add_files -fileset constrs_1 $proj_dir/constrs/ov5640_pynq.xdc
set_property top ov5640_m1a_wrapper [current_fileset]
update_compile_order -fileset sources_1

puts "INFO: BD + wrapper + 约束就绪，工程保存在 $proj_dir/proj/$proj_name.xpr"
if {$build_target eq "bd_only"} {
    puts "DONE (bd_only)：请打开工程检查 BD 后再跑全流程"
    exit
}

# ---- 5. 生成 BD 输出产物 → 综合 → 实现 → bit ----
generate_target all [get_files $proj_dir/proj/$proj_name.srcs/sources_1/bd/ov5640_m1a/ov5640_m1a.bd]

# ---- 5b. ★时钟自检★：验证 clk_wiz 求解出的频率真的是我们要的 ----
# 起因：2026-09-13 查出 clk_wiz 的 MMCM_* 显式参数被 Vivado 静默忽略、退回默认
# 100MHz，导致 XCLK 超规格、传感器 PLL 锁不上、采集链卡死在"一帧全零"。
# 这种事构建日志里毫无痕迹，只能查生成物。故在此硬性校验，不对就 fail。
set cwgen $proj_dir/proj/$proj_name.gen/sources_1/bd/ov5640_m1a/ip/ov5640_m1a_clk_wiz_0_0
set cwv [glob -nocomplain $cwgen/ov5640_m1a_clk_wiz_0_0.v]
if {[llength $cwv] == 0} { puts "ERROR: 找不到 clk_wiz 生成文件，无法做时钟自检"; exit 1 }
set fp [open [lindex $cwv 0] r]; set cwtxt [read $fp]; close $fp
regexp {clk_out1__([0-9.]+)} $cwtxt -> cwf1
regexp {clk_out2__([0-9.]+)} $cwtxt -> cwf2
puts "INFO: ★时钟自检★ clk_wiz 实际求解：clk_out1=${cwf1}MHz(M1b 像素钟) clk_out2=${cwf2}MHz(传感器域)"
if {[expr {abs($cwf2 - 24.0)}] > 0.5} {
    puts "ERROR: clk_out2 必须≈24MHz（传感器 XCLK，规格 6~27MHz），实际 ${cwf2}MHz。"
    puts "ERROR: 检查 bd/m1a_capture.tcl 里 clk_wiz 是否只用了 CLKOUT*_REQUESTED_OUT_FREQ。"
    exit 1
}
if {[expr {abs($cwf1 - 74.25)}] > 1.0} {
    puts "ERROR: clk_out1 必须≈74.25MHz（M1b HDMI 像素钟），实际 ${cwf1}MHz。"
    exit 1
}
puts "INFO: 时钟自检通过"

# ---- 1e. ★XDC 白名单预检★（综合前、纯读文件、秒级；起因见 xdc 顶部那段血泪教训）----
# XDC 是**白名单**解释器：proc/if/catch 这类非约束命令只会发一条 CRITICAL WARNING
# 就被丢掉，被它包住的约束**全部静默失效**。上一次就是这么把 9 个 HDMI 脚丢光的
# （8 个 TMDS 被工具随手丢到阿duino/RPi 脚上），白跑 5 小时才在上板前发现。
# 这里跑综合之前先按**文本**把这类命令和 9 条引脚约束都对一遍。
set _xdcf $proj_dir/constrs/ov5640_pynq.xdc
set _fh [open $_xdcf r]; set _xdc [read $_fh]; close $_fh
set _bad {}
foreach _w {proc if catch foreach while for switch error} {
    if {[regexp -line "^\\s*$_w\\s" $_xdc]} { lappend _bad $_w }
}
if {[llength $_bad] > 0} {
    puts "ERROR: XDC 里用了白名单外的命令：$_bad"
    puts "ERROR:   它们会被**丢掉且只发 CRITICAL WARNING**，被包住的约束静默失效 -> 禁跑。"
    puts "ERROR:   请把它们搬到本脚本（build_project.tcl 是真的 Tcl 解释器）里。"
    exit 1
}
set _npin 0
foreach _p {L16 L17 K17 K19 J18 K18 J19 H18 R19} {
    if {[regexp "PACKAGE_PIN $_p " $_xdc]} { incr _npin }
}
if {$_npin != 9} {
    puts "ERROR: XDC 里只找到 $_npin/9 条 HDMI 引脚约束（应是 L16 L17 K17 K19 J18 K18 J19 H18 R19）"
    exit 1
}
# ★ 电平标准也要数：8 个 TMDS 脚必须 TMDS_33、HPD 必须 LVCMOS33。
#   2026-09-30 上午的教训：光有"9 条引脚约束"不等于对 —— 我把 8 个 TMDS 脚的电平
#   写成了 LVCMOS33（照抄 part0_pins.xml 的提示字段），placer 直接报 8 条 Place 30-64
#   拒掉。所以这里**数字段**，把"脚位数对了但电平写错"这种半成品挡在综合之前。
set _nt33 0
foreach _p {L16 L17 K17 K19 J18 K18 J19 H18} {
    if {[regexp "PACKAGE_PIN $_p IOSTANDARD TMDS_33" $_xdc]} { incr _nt33 }
}
if {$_nt33 != 8} {
    puts "ERROR: XDC 里只有 $_nt33/8 个 TMDS 脚写了 IOSTANDARD TMDS_33。"
    puts "ERROR:   TMDS 口是**差分**的（rgb2dvi 的 OBUFDS 把 IOSTANDARD 写死成 TMDS_33，"
    # ★ 注意 \[ \] 的转义：不转义的话 `[Place 30-64]` 会被当命令替换执行，
    #   于是**这条报错自己会崩**（invalid command name "Place"）—— 见 tcl_puts_bracket_check.py
    puts "ERROR:   没有单端选项）—— 写 LVCMOS33 会被 placer 用 \[Place 30-64\] 打回。"
    puts "ERROR:   只有 hdmi_out_hpd\[0\] 是 LVCMOS33。"
    exit 1
}
if {![regexp "PACKAGE_PIN R19 IOSTANDARD LVCMOS33" $_xdc]} {
    puts "ERROR: XDC 里 hdmi_out_hpd\[0\]（R19）不是 LVCMOS33 —— 它是普通状态线，不该跟着走 TMDS_33"
    exit 1
}
puts "INFO: XDC 预检通过（无白名单外命令；9/9 条引脚约束在位；8×TMDS_33 + 1×LVCMOS33 电平正确）"

# ★ 2026-10-31：本机 16GB，-jobs 4 在带 map_detect/粒子引擎的 OOC 子运行上会 OOM
#   （build_after_patch.tcl 早已改 2）。统一 -jobs 2，避免换机器的内存差异导致假失败。
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { puts "ERROR: 综合失败"; exit 1 }

# ---- 1f. ★HDMI 引脚门禁★（综合后、实现前，查**网表里的真身**）----
# 上面的文本预检只证明"XDC 写了"，证明不了"工具真把脚约束上了"：口名写错、或
# get_ports 匹配不到，都只发一条 WARNING 就过去（本工程被"静默失效"坑过太多次）。
# 所以这里打开综合结果，逐个口回读 PACKAGE_PIN，缺一个就 error 不往实现走。
# ★ 自校准：先查 led_locked —— 它由**同一个 XDC** 约束。若它也是空的，说明这份 XDC
#   在综合阶段压根没被应用（那是另一类问题），此时**不误报**成"HDMI 引脚错了"。
open_run synth_1
set _ledpin [get_property -quiet PACKAGE_PIN [get_ports -quiet led_locked]]
if {$_ledpin eq ""} {
    puts "WARNING: HDMI 门禁：led_locked 也没有 PACKAGE_PIN —— 这份综合结果里 XDC 没生效，"
    puts "WARNING:   门禁无法判定，跳过（**不是**说 HDMI 引脚没问题，实现后必须人工看 io_placed.rpt）"
} else {
    puts "INFO: HDMI 门禁自校准 OK（led_locked -> $_ledpin，证明 XDC 应用了）"
    # ★ 名字带中括号的必须写在 { } 里，否则 `[0]` 会被 Tcl 当命令替换执行
    # ★★ 每个口查**两件**事：PACKAGE_PIN **和** IOSTANDARD。
    #   2026-09-30 上午的教训：第一版门禁**只查了 PACKAGE_PIN**，于是它高高兴兴放行了一份
    #   "9 个脚全对、但 8 个 TMDS 脚电平写成 LVCMOS33"的设计 —— placer 立刻用 8 条
    #   `[Place 30-64] ... is differential, but has single-ended IOStandard` 打回来。
    #   门禁少查一个字段 = 自己留一个静默缺口。**约束里有几件事，就查几件事。**
    set _pairs {
        {TMDS_out_clk_p}     L16 TMDS_33
        {TMDS_out_clk_n}     L17 TMDS_33
        {TMDS_out_data_p[0]} K17 TMDS_33
        {TMDS_out_data_p[1]} K19 TMDS_33
        {TMDS_out_data_p[2]} J18 TMDS_33
        {TMDS_out_data_n[0]} K18 TMDS_33
        {TMDS_out_data_n[1]} J19 TMDS_33
        {TMDS_out_data_n[2]} H18 TMDS_33
        {hdmi_out_hpd[0]}    R19 LVCMOS33
    }
    set _fail 0
    foreach {_nm _pk _pi} $_pairs {
        set _ps [get_ports -quiet $_nm]
        if {[llength $_ps] == 0} {
            puts "ERROR: HDMI 门禁：顶层口 $_nm **不存在**（名字变了？以 wrapper 为准）"
            incr _fail ; continue
        }
        set _got [get_property -quiet PACKAGE_PIN $_ps]
        set _gi  [get_property -quiet IOSTANDARD $_ps]
        if {$_got ne $_pk} {
            puts "ERROR: HDMI 门禁：$_nm 的 PACKAGE_PIN=$_got，期望 $_pk（没约束上=工具自己乱放=上板全黑）"
            incr _fail
        } elseif {$_gi ne $_pi} {
            puts "ERROR: HDMI 门禁：$_nm 的 IOSTANDARD=$_gi，期望 $_pi（脚对、电平错 ⇒ placer 直接打回）"
            incr _fail
        } else {
            puts "  OK   $_nm -> $_pk  ($_gi)"
        }
    }
    if {$_fail > 0} {
        puts "ERROR: HDMI 引脚门禁不过（$_fail 条）—— 现在停下，别浪费几小时做实现。"
        exit 1
    }
}
# ★ 顺手把最凶的几根**高扇出网**打出来（只诊断、不拦）。
#   上次实测 `v3_bundle/g_lane[16].u_lane/SR[0]` 一根 LUT 驱动 **12671** 个负载 ——
#   这是布线拥塞的元凶，也是上一版布线越走越差的背景原因。早看见早决定。
puts "INFO: 以下是最凶的高扇出网（>1000 就值得警惕）："
catch { report_high_fanout_nets -max_nets 8 -timing }
close_design
# ★ 时序收敛手段（2026-09-28 加 V3 后 WNS=-1.028ns 才补上的）：
#   · Performance_ExtraTimingOpt：更激进地优化时序（编译时间会长一些）
#   · 开 post-route phys_opt：布线后再做**扇出复制/重定时** —— 失败路径里有
#     扇出到 17 路的长走线（布线 6.42ns vs 逻辑 4.46ns）
set impl_run [get_runs impl_1]
# ★ 2026-09-30 第六轮：上一版用 Congestion_SpreadLogic_high 跑了 **4小时44分**还没收敛，
#   而且**越走越差**：布线中间 WNS 0.171 -> -0.520 -> -0.728、TNS 0 -> -27 -> -71.6。
#   拿它的 placed checkpoint 实测复盘（probe_hdmi2）才看清：
#     · 那个策略 = place AltSpreadLogic_high + route AlternateCLBRouting；
#       摊开逻辑让每根线都变长 => setup 变差，是**指令把好局毁掉的**；
#     · 摆完那一刻其实只有 WNS=-0.092 / WHS=-0.332（差一点点就收敛）。
#   => 换回**时序优先**：Performance_ExtraTimingOpt；phys_opt 保留（hold 要靠它修）。
catch { set_property strategy Performance_ExtraTimingOpt $impl_run }
# ★★ 2026-10-31：本工程 74.5% LUT、时序余量很紧。v2.1 帧泵首次全量构建用
#   ExtraTimingOpt 布图 WNS -0.049（仅因布图漂移），**只重跑实现**换
#   ExtraPostPlacementOpt 后 WNS +0.085 过门禁。这里把赢家配方固化进从零构建：
#   （若换 Vivado 版本后该指令不存在，catch 会吞掉，下面“对账”会打出实际值）
# ★ 2026-11-01：16 粒（LUT 79.6%）用 ExtraPostPlacementOpt 布图拥塞 WNS −1.035；
#   同一 netlist 只重跑实现换 ExtraNetDelay_high → +0.094（TNS 0）。固化这条配方。
catch { set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE ExtraNetDelay_high $impl_run }
catch { set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE NoTimingRelaxation $impl_run }
catch { set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true $impl_run }
catch { set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore $impl_run }
puts "INFO: 实现策略已设为时序优先（ExtraPostPlacementOpt + post-route phys_opt）"
# ★ 回读对账：上面三条都包着 catch{}，参数名写错会被**静默吞掉**、日志毫无痕迹
#   （这是本工程"静默失效"家族的第 N 个变种）—— 所以把实际生效值打出来。
puts "INFO: 实现策略对账：strategy=[get_property -quiet strategy $impl_run]"
puts "INFO:   对账 place=[get_property -quiet STEPS.PLACE_DESIGN.ARGS.DIRECTIVE $impl_run] route=[get_property -quiet STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE $impl_run] postroute_physopt=[get_property -quiet STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED $impl_run]"

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { puts "ERROR: 实现失败"; exit 1 }

# ---- 5c. ★时序门禁★：实现跑完必须真的收敛，不能只看 PROGRESS ----
# 起因：2026-09-13 clk_wiz 频率改对后出现 6 条 CDC 违例（WNS=-3.289），
# 但 PROGRESS 仍是 100%、脚本 EXIT=0 —— 带着违例的 bit 差点就被拿去上板。
#
# ★★ 2026-09-30 审计：这条门禁原先**在说谎**。它在第 5 步
#   `launch_runs impl_1 -to_step write_bitstream` **之后**才跑，所以它报
#   "拒绝产出 bit" 的时候，`impl_1/*.bit` 已经躺在磁盘上了。任何按
#   `glob impl_1/*.bit` 找码流的人（或脚本）都会拿到一个**时序不收敛**的码流
#   —— 而门禁的报错反而让人以为它不存在。
#   不是理论风险，09-30 那趟当场兑现：WNS=-0.202 被拒，而
#   `impl_1/ov5640_m1a_wrapper.bit` 时间戳 13:46 好端端在那儿（md5 89063f83…）。
#   修法：失败分支**真的**让那个 bit 不可用（改名），并且说实话。
#   （更结构性的修法是拆两段：route 完查一次、过了再 write_bitstream。
#     改动大，先记着 —— 见 E:/tmp_pkgs/pending_build_gate_fix.md。）
# ★ 2026-10-01：门禁本体已挪到 timing_gate.tcl —— 因为现在有**两个入口**
#   会跑实现（本脚本，和只重跑实现的 rerun_impl.tcl）。抄两份一定会漂。
#   本脚本的变量名 proj_dir / proj_name 正好就是它要的，直接 source。
source [file join $proj_dir timing_gate.tcl]

set bit [glob $proj_dir/proj/$proj_name.runs/impl_1/*.bit]
puts "DONE: bit 已生成 -> $bit"

# ---- 6. 部署包：bit + hwh（同名同目录，PYNQ Overlay 加载要求）+ 驱动 ----
file mkdir $proj_dir/deploy
file copy -force $bit $proj_dir/deploy/
set hwh_src [glob $proj_dir/proj/$proj_name.gen/sources_1/bd/ov5640_m1a/hw_handoff/*.hwh]
file copy -force $hwh_src $proj_dir/deploy/ov5640_m1a_wrapper.hwh
# ★★ 2026-09-30：改成**扫目录**，不再手写清单。
#   起因：原来那是一份**显式清单**，于是它会**悄悄过期** —— 查出来 deploy/ 里
#   **一个 .sh 都没有**（game_v3.sh / v3_shoot.sh / hdmi_shot.sh / v3_wifi.sh 全缺），
#   09-28 之后新增的 v3_*.py 也几乎全缺（上次刷新是 09-25 的构建）。
#   这种"漏拷几个文件"和本工程其他坑一个性质：**不报错、不中断，等上板才发现**。
#   现在按扩展名扫 board 目录：*.py（工具）+ *.sh（上板一条命令）+ *.bin（熔断测试用的
#   真帧夹具 fx_map_clean/torn，frame_sane_map 的锚点就来自它们）。
set _depsrc $proj_dir/../../software/board
set _copied {}
foreach _f [lsort [concat [glob -nocomplain $_depsrc/*.py] \
                          [glob -nocomplain $_depsrc/*.sh] \
                          [glob -nocomplain $_depsrc/*.bin]]] {
    file copy -force $_f $proj_dir/deploy/
    lappend _copied [file tail $_f]
}
puts "INFO: 部署包 += [llength $_copied] 个 board 文件（*.py + *.sh + *.bin）"
# 黄金模型（PC 侧）也一起上板：vision_diag.py 直接 import 它来做 C) 项验收，
# 保证"8x8 块平均"的整数口径**只有一份定义**（PC 校验与板端验收用同一段代码，
# 不会出现"两边各写一遍、悄悄差 1 LSB"的经典坑）
file copy -force $proj_dir/../../software/pc/vision_golden.py $proj_dir/deploy/
# ★ HDMI 驱动那对（hdmi_out.py + dynclk_tables.py）由上面扫目录一并带上，
#   不用再单独列 —— 但**必须成对存在**（hdmi_out.py 里 `from dynclk_tables import ...`），
#   这一点由第 1c 步的前置存在性门禁守着，缺一个就在综合前 fail。
# ★ 上板要 chmod +x 的脚本，这里**打出来**，省得以后又靠猜（或又漏）。
set _shs {}
foreach _f [glob -nocomplain $proj_dir/deploy/*.sh] { lappend _shs [file tail $_f] }
puts "INFO: 上板后需 chmod +x ：$_shs"
puts "DONE: 部署包就绪 -> $proj_dir/deploy/（bit + hwh + [llength $_copied] 个 board 文件 + vision_golden.py）"
