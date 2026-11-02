# ============================================================================
# m1a_capture.tcl — OV5640 采集链路 BD（M1a：只采集，不显示）
#
# 移植自旧工程 Lesson10_A_OV5640_VDMA（EBAZ4205/xc7z010，Vivado 2019.2），
# 原始配方：project_1.srcs/sources_1/bd/design_1/hw_handoff/design_1_bd.tcl
#
# 与旧工程的差异（全部经过核实，见 README）：
#   ① 器件 xc7z010 → xc7z020，PS7 改用 PYNQ-Z2 板卡 preset（DDR 32位/512MB）
#   ② 时钟源：旧板用外部 50M 晶振进 clk_wiz；PYNQ-Z2 PL 无独立晶振，
#      且 PYNQ 运行时 overlay 只能拿到启动时已使能的 FCLK0=100MHz，
#      故 clk_wiz 输入改为 FCLK0（100M → 24.02M 传感器域 / 74.24M 留给 M1b）
#   ③ 只保留 S2MM 采集通道（旧工程还有 MM2S+VGA 显示链，M1b 再加）
#   ④ xlconstant 显式指定值（旧脚本依赖默认值，此处明确化避免歧义）
#   ⑤ VDMA fstores 1→4：环形帧缓冲，PS 端可读"最新完整帧"无撕裂
#   ⑥ v_vid_in_axi4s 5.0 无 RGB565 格式（4.0 的 12 在 5.0 里是 Mono/Sensor 8bit）
#      → 8bit 字节流直通 DDR，RGB565 字节序解释放 PS 端
#
# 数据通路（2026-09-14 第十二版：v_vid_in_axi4s 已删，换成自研桥；
# 第十三版：ov5640_top 复位改经 dvp_ctrl 门控 = ctrl[4] 软件修复传感器，
# 桥写侧 4 路内部观测进 axis_probe w3[31:28]）：
#   OV5640 DVP(8bit) ──> dvp2axis_bridge(自研，xpm_fifo_async) ──> axis_probe ──> VDMA S2MM ──> DDR(HP0)
#   ov5640_top 只负责：供电时序 + SCCB 寄存器配置（304 条，自动上电执行，
#   estart 悬空即可，最终输出 1024x720@84MHz pclk）
# ============================================================================

create_bd_design ov5640_m1a

# ============================================================================
# 顶层端口（对应 constrs/ov5640_pynq.xdc 的 PMOD 引脚）
# ============================================================================
set ov5640_data   [create_bd_port -dir I -from 7 -to 0 ov5640_data]
set ov5640_href   [create_bd_port -dir I ov5640_href]
set ov5640_pclk   [create_bd_port -dir I -type clk -freq_hz 84000000 ov5640_pclk]
set ov5640_vsync  [create_bd_port -dir I ov5640_vsync]
set ov5640_xclk   [create_bd_port -dir O ov5640_xclk]
set ov5640_pwdn   [create_bd_port -dir O ov5640_pwdn]
set ov5640_rst_n  [create_bd_port -dir O -type rst ov5640_rst_n]
set ov5640_iic_scl [create_bd_port -dir O ov5640_iic_scl]
set ov5640_iic_sda [create_bd_port -dir IO ov5640_iic_sda]
set led_locked    [create_bd_port -dir O led_locked]

# ============================================================================
# PS7：PYNQ-Z2 板卡 preset（DDR/IO 参数齐全），FCLK0=100MHz，开 HP0/GP0
# ★ 2026-10-30：新机器**没有 pynq-z2 板卡文件**（tul.com.tw:pynq-z2），
#   apply_board_preset 1 用不了 ⇒ 走显式 preset：bd/ps7_pynq_z2_preset.tcl
#   是从 10-16 板上跑通的 BD 备份里抽出的全部 547 个 PCW 参数，等价复刻。
# ============================================================================
create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0
set _use_board 0
if {[info exists ::env(BOARD_REPO)] && $::env(BOARD_REPO) ne ""} { set _use_board 1 }
if {$_use_board} {
    apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
        -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} \
        [get_bd_cells processing_system7_0]
    puts "INFO: BD: use official PYNQ-Z2 board preset (apply_board_preset=1)"
} else {
    apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 \
        -config {make_external "FIXED_IO, DDR" apply_board_preset "0" Master "Disable" Slave "Disable"} \
        [get_bd_cells processing_system7_0]
    source [file join [file dirname [info script]] ps7_pynq_z2_preset.tcl]
    ps7_apply_preset [get_bd_cells processing_system7_0]
}
set_property -dict [list \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100} \
    CONFIG.PCW_FPGA_FCLK0_ENABLE {1} \
    CONFIG.PCW_USE_S_AXI_HP0 {1} \
    CONFIG.PCW_USE_M_AXI_GP0 {1} \
] [get_bd_cells processing_system7_0]

# ============================================================================
# 时钟：FCLK0(100M) 进 MMCM → clk_out1=74.24M(M1b 预留) + clk_out2=24.02M(传感器域)
# MMCM 计算：100M × 49 / 6 = VCO 816.67M；/11.0 = 74.24M；/34 = 24.02M
# ============================================================================
create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clk_wiz_0
# ★★★ 时钟配置大坑（2026-09-13 定位）：
#   这里**只能给 REQUESTED_OUT_FREQ，绝不能给 MMCM_* 显式参数**。
#   之前写 MMCM_CLKFBOUT_MULT_F=49 / CLKOUT0_DIVIDE_F=11 / CLKOUT1_DIVIDE=34 /
#   DIVCLK_DIVIDE=6，Vivado **全部忽略**，退回 IP 默认（MULT=10/DIVCLK=1/DIVIDE=10）
#   → 两个输出都变成 100MHz。后果：clk_sys24m=100MHz → ov5640_xclk=100MHz，
#   远超 OV5640 的 XCLK 规格(6~27MHz)，传感器 PLL 锁不上 → **永远没有 PCLK**、
#   一帧全零后停流；而 SCCB 只是被拖到 195kHz（仍在 I2C 400k 容限内）所以照写不误
#   —— 于是表现为"配置成功但没有数据"，极难反推。
#   教训：BD 里配 clk_wiz 用 REQUESTED_OUT_FREQ 让工具求解；
#   并且**必须验证生成物**（build_project.tcl 已加自动校验，频率不对直接 fail）。
set_property -dict [list \
    CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {74.25} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {24} \
    CONFIG.NUM_OUT_CLKS {2} \
    CONFIG.USE_RESET {false} \
] [get_bd_cells clk_wiz_0]

# ============================================================================
# ov5640_top（模块引用）：只干 供电时序 + SCCB 配置（第十二版起不再打包 DVP，
# 原始 DVP 信号直接进 dvp2axis_bridge —— 见下方"视频入"一节）。
# ov5640_top 的 m_data/m_wr_en 输出悬空不用；estart/ewdata/riic_data 悬空
# （ov5640_cfg 上电后自动顺序执行 304 条寄存器配置）。
# ============================================================================
create_bd_cell -type module -reference ov5640_top ov5640_top_0

# ============================================================================
# 视频入：DVP → AXI4-Stream（2026-09-14 第十二版）
# ★ v_vid_in_axi4s_0 已删。第十一轮 dvp_probe 决定性测量证明传感器每次运行
#   都健康（PCLK 83.3MHz/30fps/722 行/304 条 SCCB 全写完/数据线全活），
#   而 IP 输出侧每次运行随机坏、健康时也均匀丢 1/20 —— 换成自研桥
#   rtl/ov5640/dvp2axis_bridge.v（href 门控打包 + xpm_fifo_async fwft，
#   单 SOF 由构造保证）。行为差异：不再丢 1/20，每行 2048 字节、每帧 722 行。
# ============================================================================
create_bd_cell -type module -reference dvp2axis_bridge dvp2axis_bridge_0
# 极性钉死（教训：BD 会按端口名猜极性，别让它猜 —— 第七轮踩过）
set_property CONFIG.POLARITY {ACTIVE_HIGH} [get_bd_pins dvp2axis_bridge_0/rst_pclk]

# ============================================================================
# VDMA：仅 S2MM 采集通道；genlock mode 2 = 自由运行主模式（自产生 fsync）
# fstores=4：环形 4 帧缓冲，PS 读最新完整帧无撕裂
# ============================================================================
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 axi_vdma_0
set_property -dict [list \
    CONFIG.c_include_mm2s {0} \
    CONFIG.c_include_s2mm {1} \
    CONFIG.c_s2mm_genlock_mode {2} \
    CONFIG.c_s2mm_linebuffer_depth {1024} \
    CONFIG.c_s2mm_max_burst_length {32} \
    CONFIG.c_num_fstores {4} \
] [get_bd_cells axi_vdma_0]

# ★★★ 关于 S2MM 帧同步的大坑（2026-09-13 定位，别再用"改 genlock 参数"去修）：
#   axi_vdma_v6_3_rfs.vhd:53016 的 S2MM FSYNC_CROSSBAR 用 DMACR[6:5] 选帧同步源：
#       "00" primary fsync（复位默认，s2mm_dmacr 复位值是全 0 —— 见 73764 行）
#       "01" othrchnl_fsync（另一通道，本设计没有 MM2S → 恒 0）
#       "10" tuser_fsync   （源码注释原文 "used only by s2mm"）
#   上板实测把 DMACR[6:5] 四档全扫一遍**全部不出帧**，且四档错误事件次数完全
#   相同 → 说明不是选档的问题，而是**三个来源全都从不脉冲**：前两个结构性死亡，
#   只剩 tuser 一个可能。故本版在流路径上插入 axis_probe_0（见下），
#   一边统计 tuser/tlast 到底有没有，一边**用 DVP vsync 合成 SOF**补上。
#   C_S2MM_GENLOCK_MODE 保持 2（与跑通过的旧工程一致）——它只决定路由结构，
#   真正选源的是运行期的 DMACR[6:5]（驱动写 0x00FF0043，FSYNCSEL=2）。

# ============================================================================
# 互连：mem(HP0) 1主1从 / periph(GP0) 1主1从；复位；常量；反相；ILA
# ============================================================================
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_mem_intercon
set_property -dict [list CONFIG.NUM_MI {1} CONFIG.NUM_SI {4}] [get_bd_cells axi_mem_intercon]
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 ps7_0_axi_periph
# NUM_MI=11：VDMA + probe0..6（M00~M07）+ 视觉前处理 V1 的 M08/M09/M10
# （2026-09-23 由 8 改 11；原来写 8 时 M08 根本不存在，connect 才报
#  "No interface pins matched .../M08_AXI"）
# ★ 2026-10-30：画线引擎的 M 口**不在这里手加**——收尾处 apply_bd_automation
#   会自己把 NUM_MI 加一（手搓 M11 + 手写 assign_bd_address 的老路在地址段名上翻过车，
#   见 v3_bundle_axi 那段注释）。
set_property -dict [list CONFIG.NUM_MI {11}] [get_bd_cells ps7_0_axi_periph]
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_0_100M

# tkeep 1bit（8bit 数据），恒 1
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xlconstant_1
set_property -dict [list CONFIG.CONST_VAL {1}] [get_bd_cells xlconstant_1]
# ov5640_top 调试口显式接地：estart=0（SCCB 配置保持自动模式）、ewdata=0，
# 消除 BD 41-759 悬空告警
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xlconstant_2
set_property -dict [list CONFIG.CONST_VAL {0}] [get_bd_cells xlconstant_2]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xlconstant_3
set_property -dict [list CONFIG.CONST_VAL {0} CONFIG.CONST_WIDTH {32}] [get_bd_cells xlconstant_3]

create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 util_vector_logic_0
set_property -dict [list CONFIG.C_OPERATION {not} CONFIG.C_SIZE {1}] [get_bd_cells util_vector_logic_0]

# ★★ 2026-10-22：ILA（JTAG 调试核，占 706 LUT / 1.5 BRAM）已从**保存的 BD** 删除
#   （差距1 检测核加进来后切片差 156 个，演示不需要）。
# ★★ 2026-10-31：**脚本里也删掉了**（原来只从 BD 删）—— 从脚本重建 BD 会把
#   ila_0 又加回来（本机 10-30 全量重建实锤：新 bit 里 ila_0 复活、且旧的
#   pe_axi_0 / map_detect_axi_0 丢失）。要 JTAG 抓 DVP 波形时再手动加回。

# ============================================================================
# DVP 寄存器探针（排障"1 帧全零后停流"用）：dvp_probe 模块计数
# pclk/64、vsync、href、scl、sda 上升沿 + 数据线见过 1/0 锁存 + xclk 翻转数，
# 7 个 32 位字经 axi_gpio 固定地址 0x43A0~0x43A3_0000 给 PS 读。
# 另有 axi_gpio_probe3 通道1 作为**输出**：ctrl 控制字交给 dvp_ctrl，
# 让 PS 可以软件开/关 XCLK、接管 PWDN（判决性实验用，见 rtl/debug/dvp_ctrl.v）
# ============================================================================
create_bd_cell -type module -reference dvp_probe dvp_probe_0
create_bd_cell -type module -reference dvp_ctrl dvp_ctrl_0

# ============================================================================
# 视频域复位控制器 vid_rst_ctrl（2026-09-13，第七版）★ 本版修的根因 ★
#
# 原来 vid_io_in_reset = ~clk_wiz_0/locked，即 MMCM 锁定（上电约 100µs）就撤复位；
# 而 OV5640 的 PCLK 要等 I2C 配完内部 PLL 才出来（上电约 600ms）。
# 复位比它要复位的那一侧时钟早撤 0.6 秒 → IP 内部异步 FIFO 上电初始化非法
# → 溢出 → 而 IP 源码里 `VID_RESET = vid_reset | fifo_overflow_from_coupler`
# → **视频域被永久按死**（tvalid 恒 0、tuser 恒 0、tlast 卡在高，正是上板量到的）。
#
# 本模块把撤复位条件改成"MMCM 已锁 且 PCLK 已稳定跑满 2^20 拍（24MHz≈43.7ms）"，
# 使复位撤除时两侧时钟都在运行 —— 这才是异步 FIFO 期望的上电次序。
# 另引出 ctrl[3] 做软件强制复位，便于不重烧 bit 再复位一次。
# 详见 rtl/debug/vid_rst_ctrl.v 文件头。
# ============================================================================
create_bd_cell -type module -reference vid_rst_ctrl vid_rst_ctrl_0
# ★ BD 会按端口名自动推断极性：看到 "vid_rst" 猜成 ACTIVE_LOW，与 IP 的
#   vid_io_in_reset(ACTIVE_HIGH) 冲突 → BD 41-238 直接报错停流程。
#   我们的 vid_rst 确实是高有效（1 = 复位），显式钉死，别让它猜。
set_property CONFIG.POLARITY {ACTIVE_HIGH} [get_bd_pins vid_rst_ctrl_0/vid_rst]
# probe0~2：双通道全输入，承载 w0~w5
for {set i 0} {$i < 3} {incr i} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_probe${i}
    set_property -dict [list \
        CONFIG.C_GPIO_WIDTH {32} \
        CONFIG.C_ALL_INPUTS {1} \
        CONFIG.C_IS_DUAL {1} \
        CONFIG.C_GPIO2_WIDTH {32} \
        CONFIG.C_ALL_INPUTS_2 {1} \
    ] [get_bd_cells axi_gpio_probe${i}]
}
# probe3：通道1 = 输出（ctrl 控制字 → dvp_ctrl），通道2 = 输入（w6 = xclk 翻转数）
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_probe3
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] [get_bd_cells axi_gpio_probe3]

# ============================================================================
# 流侧探针 axis_probe（2026-09-14 第十二版）：插在桥 → VDMA 的 AXI-Stream
# 中间，纯直通 + 统计（不再合成 SOF —— 桥由构造保证单 SOF，相或反而可能
# 造出双 SOF）。统计 tvalid 拍数 / tuser 上升沿数 / tlast 上升沿数 +
# "见过没/当前电平"状态字 + 桥的溢出/下溢 sticky，经 axi_gpio_probe4/5
# 给 PS 读。这是验证桥行为的第一手观测（VDMA 侧的报错只能反推）。
# 探针字地址：probe4=0x43A40000（w0 tvalid 拍数 / w1 tuser 边沿数）、
#             probe5=0x43A50000（w2 tlast 边沿数 / w3 状态字）
# ============================================================================
create_bd_cell -type module -reference axis_probe axis_probe_0
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_probe4
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] [get_bd_cells axi_gpio_probe4]
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_probe5
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] [get_bd_cells axi_gpio_probe5]

# probe6：单通道全输入，承载桥账本的影子读口（第十四版，2026-09-15）。
# 地址 0x43A60000；桥内 7 个 32 位影子按 dbg_sel 选择，PS 只读这一个寄存器。
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_probe6
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
] [get_bd_cells axi_gpio_probe6]

# ============================================================================
# PL 视觉前处理 V1（2026-09-23）—— vision_core：8x8 块平均降采样
# 设计文档 docs/vision_ip_design.md。
# 纯观察者：只把采集流的 axis 信号接成输入，**不产生 tready、不改数据**，
# 对已经调通的采集链零影响。产出 128x90 RGB565 缩略图（23,040 B = 原数据的
# 1.6%）写进 BRAM，PS 用 mmap(/dev/mem)+numpy 一次读回（实测 23KB 拷贝 1.05ms）。
# 地址：map 0x43B00000（axi_bram_ctrl 32KB）、ctrl/frame_cnt 0x43B10000、
#       stat 0x43B20000。
# ctrl 位（极性沿用本工程约定：ctrl=0 就是"正常运转"）：
#   ctrl[0] freeze     1=冻结（默认 0=正常累加）
#   ctrl[1] soft_rst   1=在线清累加器（默认 0）
#   ctrl[2] swap_bytes 1=高低字节对调后再拆通道（默认 0）
#   （字节序由 PS 用通道相关性判定后写进来；PL 不猜）
# ============================================================================
create_bd_cell -type module -reference vision_core vision_core_0

create_bd_cell -type ip -vlnv xilinx.com:ip:axi_bram_ctrl:4.1 axi_bram_ctrl_vision
# ★ SINGLE_PORT_BRAM 必须 = 1（控制器只用 PORTA 做读写）。
#   踩过：设 0（双口模式）时 axi_bram_ctrl **自己也要用两个口**（PORTA 写、PORTB 读），
#   而我拿 PORTB 接了自研逻辑 → 控制器的读口 bram_rddata_b 被 tie 成常数 0、
#   addr_b/wrdata_b/we_b 直接悬空 → PS 的写进不去、读回恒为常数。
#   这种"输出悬空"既不报错、validate 也过，只有上板才现形（已由 bram_test.py 定位）。
#   设成 1 之后：控制器独占 PORTA 读写，PORTB 完整留给 vision_core 写。
set_property -dict [list \
    CONFIG.DATA_WIDTH {32} \
    CONFIG.SINGLE_PORT_BRAM {1} \
    CONFIG.PROTOCOL {AXI4LITE} \
    CONFIG.ECC_TYPE {0} \
] [get_bd_cells axi_bram_ctrl_vision]

# 真双口 RAM：PORTA 挂 BRAM 控制器（PS 读），PORTB 留给 vision_core 写。
# ★ 不能用 Memory_Type=BRAM_Controller —— Vivado 2023.2 的 blk_mem_gen 只认
#   Single_Port_RAM / True_Dual_Port_RAM / ROM 这几种，写 BRAM_Controller 会
#   直接报 "unknown 'Memory_Type' value" 并让整个 BD 脚本失败（踩过）。
create_bd_cell -type ip -vlnv xilinx.com:ip:blk_mem_gen:8.4 blk_mem_gen_vision
set_property -dict [list \
    CONFIG.Memory_Type {True_Dual_Port_RAM} \
    CONFIG.Write_Width_A {32} \
    CONFIG.Write_Depth_A {8192} \
    CONFIG.Read_Width_A {32} \
    CONFIG.Write_Width_B {32} \
    CONFIG.Read_Width_B {32} \
    CONFIG.Register_PortA_Output_of_Memory_Primitives {false} \
    CONFIG.Register_PortB_Output_of_Memory_Primitives {false} \
    CONFIG.Use_RSTA_Pin {false} \
    CONFIG.Use_RSTB_Pin {false} \
] [get_bd_cells blk_mem_gen_vision]

# ctrl 三个位切片
foreach {nm idx} {xlslice_v_freeze 0 xlslice_v_srst 1 xlslice_v_swap 2} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 $nm
    set_property -dict [list \
        CONFIG.DIN_WIDTH {32} \
        CONFIG.DIN_FROM $idx \
        CONFIG.DIN_TO $idx \
        CONFIG.DOUT_WIDTH {1} \
    ] [get_bd_cells $nm]
}

# 通道1 = 输出（ctrl 控制字），通道2 = 输入（frame_cnt）
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_vision
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_OUTPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] [get_bd_cells axi_gpio_vision]

# stat/stat2 双通道输入（v2 加了第二个 32 位状态字）：
#   0x43B20000 = stat  {last_lines[15:0], last_wcnt[15:0]}  健康值 0x02D01680
#   0x43B20008 = stat2 {last_bcnt[10:0], errs[5:0], wcnt[14:0]}  健康值 0xFFE01680
# ★ 两路都是输入：C_ALL_INPUTS + C_IS_DUAL + C_ALL_INPUTS_2 三者同时给才行。
#   v1 只有单通道，v2 要读 stat2，就地把通道 2 配成输入（省一个 gpio IP）。
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_vstat
set_property -dict [list \
    CONFIG.C_GPIO_WIDTH {32} \
    CONFIG.C_ALL_INPUTS {1} \
    CONFIG.C_IS_DUAL {1} \
    CONFIG.C_GPIO2_WIDTH {32} \
    CONFIG.C_ALL_INPUTS_2 {1} \
] [get_bd_cells axi_gpio_vstat]

# ============================================================================
# ★ 2026-10-30：PL 画线引擎（轨迹渲染下移）
#   AXI4-Lite 从（PS 下发命令）+ AXI4 写主（写 DDR framebuffer）
# ============================================================================
create_bd_cell -type module -reference line_engine_top line_engine_top_0

# ---- AXI 接口连接 ----
connect_bd_intf_net [get_bd_intf_pins axi_mem_intercon/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP0]
connect_bd_intf_net [get_bd_intf_pins axi_vdma_0/M_AXI_S2MM] [get_bd_intf_pins axi_mem_intercon/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins ps7_0_axi_periph/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M00_AXI] [get_bd_intf_pins axi_vdma_0/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M01_AXI] [get_bd_intf_pins axi_gpio_probe0/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M02_AXI] [get_bd_intf_pins axi_gpio_probe1/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M03_AXI] [get_bd_intf_pins axi_gpio_probe2/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M04_AXI] [get_bd_intf_pins axi_gpio_probe3/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M05_AXI] [get_bd_intf_pins axi_gpio_probe4/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M06_AXI] [get_bd_intf_pins axi_gpio_probe5/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M07_AXI] [get_bd_intf_pins axi_gpio_probe6/S_AXI]
# 视觉前处理 V1：M08=BRAM 读口、M09=ctrl/frame_cnt、M10=stat
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M08_AXI] [get_bd_intf_pins axi_bram_ctrl_vision/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M09_AXI] [get_bd_intf_pins axi_gpio_vision/S_AXI]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/M10_AXI] [get_bd_intf_pins axi_gpio_vstat/S_AXI]
# ★ 2026-10-30：画线引擎的 AXI4 写主 → axi_mem_intercon/S01_AXI（和 VDMA 共用 HP0）
#   它的 AXI4-Lite 命令口（s_axi）留到收尾处 apply_bd_automation 自动接
#   （老路手接 M11 + 手写 assign_bd_address 在地址段名上翻过车，见 v3_bundle_axi 注释）。
connect_bd_intf_net [get_bd_intf_pins line_engine_top_0/m_axi] [get_bd_intf_pins axi_mem_intercon/S01_AXI]

# 采集链：桥 → **vision_core 串联直通** → axis_probe → VDMA S2MM
# ★ 必须走接口连接（connect_bd_intf_net）。2026-09-23 踩过：想"只读观察"而用
#   connect_bd_net 单独接桥的 m_axis_* 子引脚，会把这些引脚**从原接口连接里抢断**
#   （BD 41-1271），桥的 m_axis_tready 变成无源、被 tie 到 0 → 整条链路死。
connect_bd_intf_net [get_bd_intf_pins dvp2axis_bridge_0/m_axis] [get_bd_intf_pins vision_core_0/s_axis]
connect_bd_intf_net [get_bd_intf_pins vision_core_0/m_axis] [get_bd_intf_pins axis_probe_0/s_axis]
connect_bd_intf_net [get_bd_intf_pins axis_probe_0/m_axis] [get_bd_intf_pins axi_vdma_0/S_AXIS_S2MM]

# ============================================================================
# 时钟连接：FCLK0(100M) 驱动全部 AXI 时钟域 + clk_wiz 输入
# ============================================================================
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins clk_wiz_0/clk_in1] \
    [get_bd_pins rst_ps7_0_100M/slowest_sync_clk] \
    [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK] \
    [get_bd_pins processing_system7_0/S_AXI_HP0_ACLK] \
    [get_bd_pins axi_mem_intercon/ACLK] \
    [get_bd_pins axi_mem_intercon/M00_ACLK] \
    [get_bd_pins axi_mem_intercon/S00_ACLK] \
    [get_bd_pins ps7_0_axi_periph/ACLK] \
    [get_bd_pins ps7_0_axi_periph/M00_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M01_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M02_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M03_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M04_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M05_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M06_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M07_ACLK] \
    [get_bd_pins ps7_0_axi_periph/S00_ACLK] \
    [get_bd_pins axi_vdma_0/s_axi_lite_aclk] \
    [get_bd_pins axi_vdma_0/m_axi_s2mm_aclk] \
    [get_bd_pins axi_vdma_0/s_axis_s2mm_aclk] \
    [get_bd_pins dvp2axis_bridge_0/aclk] \
    [get_bd_pins axi_gpio_probe0/s_axi_aclk] \
    [get_bd_pins axi_gpio_probe1/s_axi_aclk] \
    [get_bd_pins axi_gpio_probe2/s_axi_aclk] \
    [get_bd_pins axi_gpio_probe3/s_axi_aclk] \
    [get_bd_pins axi_gpio_probe4/s_axi_aclk] \
    [get_bd_pins axi_gpio_probe5/s_axi_aclk] \
    [get_bd_pins axi_gpio_probe6/s_axi_aclk] \
    [get_bd_pins axis_probe_0/aclk] \
    [get_bd_pins dvp_probe_0/axi_clk] \
    [get_bd_pins dvp_ctrl_0/axi_clk] \
    [get_bd_pins ps7_0_axi_periph/M08_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M09_ACLK] \
    [get_bd_pins ps7_0_axi_periph/M10_ACLK] \
    [get_bd_pins vision_core_0/aclk] \
    [get_bd_pins axi_bram_ctrl_vision/s_axi_aclk] \
    [get_bd_pins blk_mem_gen_vision/clka] \
    [get_bd_pins blk_mem_gen_vision/clkb] \
    [get_bd_pins axi_gpio_vision/s_axi_aclk] \
    [get_bd_pins axi_gpio_vstat/s_axi_aclk] \
    [get_bd_pins axi_mem_intercon/S01_ACLK]

# ============================================================================
# 复位连接
# ============================================================================
connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_0_100M/ext_reset_in]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins axi_mem_intercon/ARESETN] \
    [get_bd_pins axi_mem_intercon/M00_ARESETN] \
    [get_bd_pins axi_mem_intercon/S00_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M00_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M01_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M02_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M03_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M04_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M05_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M06_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M07_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/S00_ARESETN] \
    [get_bd_pins axi_vdma_0/axi_resetn] \
    [get_bd_pins dvp2axis_bridge_0/aresetn] \
    [get_bd_pins axi_gpio_probe0/s_axi_aresetn] \
    [get_bd_pins axi_gpio_probe1/s_axi_aresetn] \
    [get_bd_pins axi_gpio_probe2/s_axi_aresetn] \
    [get_bd_pins axi_gpio_probe3/s_axi_aresetn] \
    [get_bd_pins axi_gpio_probe4/s_axi_aresetn] \
    [get_bd_pins axi_gpio_probe5/s_axi_aresetn] \
    [get_bd_pins axi_gpio_probe6/s_axi_aresetn] \
    [get_bd_pins axis_probe_0/aresetn] \
    [get_bd_pins ps7_0_axi_periph/M08_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M09_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/M10_ARESETN] \
    [get_bd_pins vision_core_0/aresetn] \
    [get_bd_pins axi_bram_ctrl_vision/s_axi_aresetn] \
    [get_bd_pins axi_gpio_vision/s_axi_aresetn] \
    [get_bd_pins axi_gpio_vstat/s_axi_aresetn] \
    [get_bd_pins axi_mem_intercon/S01_ARESETN]

# ============================================================================
# 传感器域：24.02M → ov5640_top（xclk 直通输出给传感器；sys50m 也接 24M，
# 与旧工程一致——power_ctrl 计数在 24M 下延时更长，更保险）
# ============================================================================
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] \
    [get_bd_pins ov5640_top_0/clk_sys24m] \
    [get_bd_pins ov5640_top_0/clk_sys50m]
# MMCM 锁定 → 板载 LED0 + vid_rst_ctrl 锁定标志 + dvp_ctrl（第十三版：
# ov5640_top 复位改经 dvp_ctrl 门控 —— PS 用 ctrl[4] 重跑上电时序+SCCB
# 来修复"逐次下载随机坏"的传感器，详见 rtl/debug/dvp_ctrl.v 文件头）
# 注意：vid_io 域复位**不再**直接用 ~locked，改经 vid_rst_ctrl（见下）——
# 因为 ~locked 会在传感器 PCLK 出现前 0.6 秒就撤复位，把 IP 内部 FIFO 搞死。
connect_bd_net [get_bd_pins clk_wiz_0/locked] \
    [get_bd_pins dvp_ctrl_0/locked] \
    [get_bd_pins util_vector_logic_0/Op1] \
    [get_bd_ports led_locked]
# 传感器复位输出：locked 经 ctrl[4] 门控后给 ov5640_top（默认直通，逐位兼容）
connect_bd_net [get_bd_pins dvp_ctrl_0/rst_n_out] [get_bd_pins ov5640_top_0/s_rst_n]
# util_vector_logic_0/Res = ~locked，作为"MMCM 未锁"标志送进 vid_rst_ctrl
connect_bd_net [get_bd_pins util_vector_logic_0/Res] \
    [get_bd_pins vid_rst_ctrl_0/locked_n]
# ★ 视频域复位最终来源：MMCM 已锁 且 PCLK 已稳定跑满 43.7ms 才撤
# 第十二版：直接进桥的写侧复位（xpm_fifo_async 要求的"两侧时钟都稳定才撤"）
connect_bd_net [get_bd_pins vid_rst_ctrl_0/vid_rst] \
    [get_bd_pins dvp2axis_bridge_0/rst_pclk]

# ============================================================================
# DVP 信号：原始 8bit 直连 dvp2axis_bridge（2026-09-14 第十二版）
# ============================================================================
connect_bd_net [get_bd_ports ov5640_data] \
    [get_bd_pins ov5640_top_0/ov5640_data] \
    [get_bd_pins dvp2axis_bridge_0/data]
connect_bd_net [get_bd_ports ov5640_pclk] \
    [get_bd_pins ov5640_top_0/ov5640_pclk] \
    [get_bd_pins dvp2axis_bridge_0/pclk] \
    [get_bd_pins vid_rst_ctrl_0/pclk]
connect_bd_net [get_bd_ports ov5640_href] \
    [get_bd_pins ov5640_top_0/ov5640_href] \
    [get_bd_pins dvp2axis_bridge_0/href]
connect_bd_net [get_bd_ports ov5640_vsync] \
    [get_bd_pins ov5640_top_0/ov5640_vsync] \
    [get_bd_pins dvp2axis_bridge_0/vsync]

# 注意：ov5640_xclk / ov5640_pwdn 已改经 dvp_ctrl 出脚（见下方"控制线接管"），
# 此处不再直连；顶层端口仍是同一对 PMOD 脚位，接线不变。
# ov5640_top_0/ov5640_xclk 因此悬空（XCLK 由 clk_wiz clk_out2 经 dvp_ctrl 门控输出）。
connect_bd_net [get_bd_ports ov5640_rst_n]    [get_bd_pins ov5640_top_0/ov5640_rst_n]
connect_bd_net [get_bd_ports ov5640_iic_scl]  [get_bd_pins ov5640_top_0/ov5640_iic_scl]
connect_bd_net [get_bd_ports ov5640_iic_sda]  [get_bd_pins ov5640_top_0/ov5640_iic_sda]

# ============================================================================
# tkeep=1'b1（8bit 数据）。（aclken/axis_enable/vid_io_in_ce 随 IP 一起删除）
# ============================================================================
connect_bd_net [get_bd_pins xlconstant_1/dout] [get_bd_pins axi_vdma_0/s_axis_s2mm_tkeep]
# ov5640_top 调试口接地（SCCB 自动配置模式）
connect_bd_net [get_bd_pins xlconstant_2/dout] [get_bd_pins ov5640_top_0/estart]
connect_bd_net [get_bd_pins xlconstant_3/dout] [get_bd_pins ov5640_top_0/ewdata]

# ============================================================================
# 探针信号连接（全部只读观测，不改变采集链行为）
# ============================================================================
connect_bd_net [get_bd_pins dvp_probe_0/pclk]    [get_bd_ports ov5640_pclk]
connect_bd_net [get_bd_pins dvp_probe_0/vsync]   [get_bd_ports ov5640_vsync]
connect_bd_net [get_bd_pins dvp_probe_0/href]    [get_bd_ports ov5640_href]
connect_bd_net [get_bd_pins dvp_probe_0/data]    [get_bd_ports ov5640_data]
connect_bd_net [get_bd_pins dvp_probe_0/scl]     [get_bd_ports ov5640_iic_scl]
connect_bd_net [get_bd_pins dvp_probe_0/sda]     [get_bd_ports ov5640_iic_sda]
connect_bd_net [get_bd_pins dvp_probe_0/locked]  [get_bd_pins clk_wiz_0/locked]
# 直接观测传感器 XCLK 同一点（板上该 net 出脚到模块，探头看的是真实输出）
connect_bd_net [get_bd_pins dvp_probe_0/xclk]    [get_bd_ports ov5640_xclk]
# 三根控制线的板上出脚电平：验证 power_ctrl 稳态是否真是 pwdn=0 / rst_n=1
connect_bd_net [get_bd_pins dvp_probe_0/pwdn]    [get_bd_ports ov5640_pwdn]
connect_bd_net [get_bd_pins dvp_probe_0/rst_n]   [get_bd_ports ov5640_rst_n]

connect_bd_net [get_bd_pins axi_gpio_probe0/gpio_io_i]  [get_bd_pins dvp_probe_0/w0]
connect_bd_net [get_bd_pins axi_gpio_probe0/gpio2_io_i] [get_bd_pins dvp_probe_0/w1]
connect_bd_net [get_bd_pins axi_gpio_probe1/gpio_io_i]  [get_bd_pins dvp_probe_0/w2]
connect_bd_net [get_bd_pins axi_gpio_probe1/gpio2_io_i] [get_bd_pins dvp_probe_0/w3]
connect_bd_net [get_bd_pins axi_gpio_probe2/gpio_io_i]  [get_bd_pins dvp_probe_0/w4]
connect_bd_net [get_bd_pins axi_gpio_probe2/gpio2_io_i] [get_bd_pins dvp_probe_0/w5]
connect_bd_net [get_bd_pins axi_gpio_probe3/gpio2_io_i] [get_bd_pins dvp_probe_0/w6]

# 桥的溢出/下溢 sticky 接进探针（w3 bit10/11）：FIFO 是否出过事的直接证据
connect_bd_net [get_bd_pins dvp2axis_bridge_0/ovf_sticky] [get_bd_pins axis_probe_0/br_ovf]
connect_bd_net [get_bd_pins dvp2axis_bridge_0/unf_sticky] [get_bd_pins axis_probe_0/br_unf]
# 桥写侧内部观测（第十三版，w3 bit31/30/29/28）：坏状态解剖到写侧内部
connect_bd_net [get_bd_pins dvp2axis_bridge_0/sof_armed_obs]   [get_bd_pins axis_probe_0/br_sof_armed]
connect_bd_net [get_bd_pins dvp2axis_bridge_0/dropping_obs]    [get_bd_pins axis_probe_0/br_dropping]
connect_bd_net [get_bd_pins dvp2axis_bridge_0/wr_rst_busy_obs] [get_bd_pins axis_probe_0/br_wr_rst_busy]
connect_bd_net [get_bd_pins dvp2axis_bridge_0/rst_obs]         [get_bd_pins axis_probe_0/br_rst_pclk]

# 流侧探针字（w0 tvalid 拍数 / w1 tuser 边沿数 / w2 tlast 边沿数 / w3 状态字）
connect_bd_net [get_bd_pins axi_gpio_probe4/gpio_io_i]  [get_bd_pins axis_probe_0/w0]
connect_bd_net [get_bd_pins axi_gpio_probe4/gpio2_io_i] [get_bd_pins axis_probe_0/w1]
connect_bd_net [get_bd_pins axi_gpio_probe5/gpio_io_i]  [get_bd_pins axis_probe_0/w2]
connect_bd_net [get_bd_pins axi_gpio_probe5/gpio2_io_i] [get_bd_pins axis_probe_0/w3]

# ---- 控制线接管：ov5640_top 的 xclk/pwdn 改经 dvp_ctrl 再出脚 ----
connect_bd_net [get_bd_pins dvp_ctrl_0/clk24]     [get_bd_pins clk_wiz_0/clk_out2]
connect_bd_net [get_bd_pins dvp_ctrl_0/pl_pwdn]   [get_bd_pins ov5640_top_0/ov5640_pwdn]
connect_bd_net [get_bd_pins dvp_ctrl_0/ctrl]      [get_bd_pins axi_gpio_probe3/gpio_io_o]
connect_bd_net [get_bd_pins dvp_ctrl_0/xclk_out]  [get_bd_ports ov5640_xclk]
connect_bd_net [get_bd_pins dvp_ctrl_0/sensor_pwdn] [get_bd_ports ov5640_pwdn]
# vid_rst_force = ctrl[3] | ctrl[4]（ctrl[3] 纯复位桥；ctrl[4] 传感器重跑
# 必须带桥复位。ctrl=0 默认"不复位"，符合本工程极性约定）
connect_bd_net [get_bd_pins dvp_ctrl_0/vid_rst_force] [get_bd_pins vid_rst_ctrl_0/force_rst]
# 第十四版：桥账本控制与读口（ctrl[6] 快照触发、ctrl[9:7] 影子选择，
# 经 dvp_ctrl 直通；影子值进 probe6 给 PS 读，地址 0x43A60000）
connect_bd_net [get_bd_pins dvp_ctrl_0/dbg_trig] [get_bd_pins dvp2axis_bridge_0/dbg_trig]
connect_bd_net [get_bd_pins dvp_ctrl_0/dbg_sel]  [get_bd_pins dvp2axis_bridge_0/dbg_sel]
connect_bd_net [get_bd_pins dvp2axis_bridge_0/dbg_data] [get_bd_pins axi_gpio_probe6/gpio_io_i]

# ============================================================================
# 视觉前处理 V1 连接（全部只读观测，不改变采集链行为）
# ============================================================================
# BRAM 控制器 ↔ BRAM PORTA；vision_core 写 PORTB
connect_bd_intf_net [get_bd_intf_pins axi_bram_ctrl_vision/BRAM_PORTA] \
    [get_bd_intf_pins blk_mem_gen_vision/BRAM_PORTA]
# ★ blk_mem_gen 的 B 口引脚在 BD 里到底叫什么，不同版本/配置下可能是
#   web/dinb/addrb（IP 原生名）也可能是 BRAM_PORTB_we/... （接口前缀名）。
#   与其猜，不如把候选名挨个试一遍，谁在就连谁，并把用到的名字打进日志。
#   （踩过：写 BRAM_PORTB_we 报 "No pins matched"，白跑一轮 4 分钟构建）
proc conn_cand {cands target} {
    foreach c $cands {
        set p [get_bd_pins -quiet $c]
        if {[llength $p] > 0} {
            connect_bd_net $p [get_bd_pins $target]
            puts "INFO: 视觉前处理连接 $c -> $target"
            return 1
        }
    }
    puts "ERROR: 候选引脚名都不存在：$cands（目标 $target）"
    return 0
}
conn_cand {blk_mem_gen_vision/web blk_mem_gen_vision/BRAM_PORTB_we blk_mem_gen_vision/we_b} \
    vision_core_0/wr_en
conn_cand {blk_mem_gen_vision/addrb blk_mem_gen_vision/BRAM_PORTB_addr blk_mem_gen_vision/addr_b} \
    vision_core_0/wr_addr
conn_cand {blk_mem_gen_vision/dinb blk_mem_gen_vision/BRAM_PORTB_din blk_mem_gen_vision/din_b} \
    vision_core_0/wr_data
# B 口使能：blk_mem_gen 默认 Enable_B=Use_ENB_Pin（有 enb 脚），**不接就永远写不进去**。
# 接常 1；若该 IP 被配成 Always_Enabled（没有 enb 脚）则跳过，属正常。
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xlconstant_v_enb
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] \
    [get_bd_cells xlconstant_v_enb]
set v_enb [get_bd_pins -quiet blk_mem_gen_vision/enb]
if {[llength $v_enb] == 0} { set v_enb [get_bd_pins -quiet blk_mem_gen_vision/BRAM_PORTB_en] }
if {[llength $v_enb] > 0} {
    connect_bd_net $v_enb [get_bd_pins xlconstant_v_enb/dout]
    puts "INFO: 视觉前处理 B 口 enb 已接常 1（$v_enb）"
} else {
    puts "INFO: 视觉前处理 BRAM 没有 enb 引脚（Enable_B=Always_Enabled），无需接"
}

# （采集流的接入已在上面用 connect_bd_intf_net 做了串联直通，此处不再单接子引脚）

# ctrl 分发 + 状态回收
connect_bd_net [get_bd_pins axi_gpio_vision/gpio_io_o] \
    [get_bd_pins xlslice_v_freeze/Din] \
    [get_bd_pins xlslice_v_srst/Din] \
    [get_bd_pins xlslice_v_swap/Din]
connect_bd_net [get_bd_pins xlslice_v_freeze/Dout] [get_bd_pins vision_core_0/freeze]
connect_bd_net [get_bd_pins xlslice_v_srst/Dout]   [get_bd_pins vision_core_0/soft_rst]
connect_bd_net [get_bd_pins xlslice_v_swap/Dout]   [get_bd_pins vision_core_0/swap_bytes]
connect_bd_net [get_bd_pins vision_core_0/frame_cnt] [get_bd_pins axi_gpio_vision/gpio2_io_i]
connect_bd_net [get_bd_pins vision_core_0/stat]      [get_bd_pins axi_gpio_vstat/gpio_io_i]
# ★ 硬门禁：stat2 必须真的接上。BD 里少接一根线只会变成 WARNING，
#   合成照过、上板读回恒 0 —— v1 就是被这类"静默无源引脚"坑了一整天。
if {[llength [get_bd_pins -quiet axi_gpio_vstat/gpio2_io_i]] == 0} {
    puts "ERROR: axi_gpio_vstat 没有 gpio2_io_i 引脚（C_IS_DUAL/C_ALL_INPUTS_2 没生效？）"
    exit 1
}
connect_bd_net [get_bd_pins vision_core_0/stat2]     [get_bd_pins axi_gpio_vstat/gpio2_io_i]
puts "INFO: 视觉前处理 stat2 -> axi_gpio_vstat/gpio2_io_i 已连接（读 0x43B20008）"

# ============================================================================
# 地址映射（沿用旧工程数值：VDMA 寄存器 0x43000000；探针 0x43A0/1/2_0000）
# ============================================================================
assign_bd_address -offset 0x43000000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_vdma_0/S_AXI_LITE/Reg] -force
assign_bd_address -offset 0x43A00000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe0/S_AXI/Reg] -force
assign_bd_address -offset 0x43A10000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe1/S_AXI/Reg] -force
assign_bd_address -offset 0x43A20000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe2/S_AXI/Reg] -force
assign_bd_address -offset 0x43A30000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe3/S_AXI/Reg] -force
assign_bd_address -offset 0x43A40000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe4/S_AXI/Reg] -force
assign_bd_address -offset 0x43A50000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe5/S_AXI/Reg] -force
assign_bd_address -offset 0x43A60000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_probe6/S_AXI/Reg] -force
assign_bd_address -offset 0x00000000 -range 0x20000000 \
    -target_address_space [get_bd_addr_spaces axi_vdma_0/Data_S2MM] \
    [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force

# ---- 视觉前处理 V1（0x43B0_0000 起）----
# map：32KB 窗口（BRAM 实配 32KB，缩略图占 23,040 B；PS 用 mmap 读）
assign_bd_address -offset 0x43B00000 -range 0x00008000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_bram_ctrl_vision/S_AXI/Mem0] -force
assign_bd_address -offset 0x43B10000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_vision/S_AXI/Reg] -force
assign_bd_address -offset 0x43B20000 -range 0x00010000 \
    -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
    [get_bd_addr_segs axi_gpio_vstat/S_AXI/Reg] -force
# ★ 2026-10-30：画线引擎命令口（64 字节：x0/y0/x1/y1/color/fb_base/ctrl/status）
# ★ 画线引擎命令口的地址在收尾处 apply_bd_automation 之后统一分配（见文件末尾）

# ============================================================================
# ---- HDMI 输出通路（P2 屏幕）：见 bd/add_hdmi_out.tcl ----
# ★ 必须放在 **V3 收尾块之前**：
#   · V3 用 apply_bd_automation 接进 ps7_0_axi_periph，会自己追加一个 MI 并连
#     FCLK_RESET0_N -> rst_ps7_0_100M/ext_reset_in —— 它必须在最后（见下面的说明）；
#   · 这段自己追加 4 个 MI（编号从当前 NUM_MI 算，不写死），所以先后都不冲突，
#     但放这里能让"地址分配"和别的设备挨着，读起来清楚。
#   · 地址用 0x43C0_0000 起，与 V3 自动分配的地址段（0x4000_0000）不撞。
# ============================================================================
source [file join [file dirname [info script]] add_hdmi_out.tcl]

# ---- V3 并行轨迹束：PS 写扫描参数、启动、读回 17 路结果 ----
# ★ 用 **axi4 自动化**接进现有互联（intc_ip 指过去）：它会自己加 MI 端口、连时钟/复位、
#   分配地址段。手搓 M11 + 手写 assign_bd_address 那条路实测在地址段名上就死了
#   （"A peripheral must be specified ..."），别再手搓。
# ★★ 必须放在**收尾处**：自动化会连 FCLK_RESET0_N -> rst_ps7_0_100M/ext_reset_in，
#   而脚本前面自己也要连这一对 —— 放中途就会撞 "already connected" 报 [BD 5-4]。
create_bd_cell -type module -reference v3_bundle_axi v3_bundle_axi_0
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/v3_bundle_axi_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins v3_bundle_axi_0/s_axi]

# ---- 画线引擎（line_engine_top_0）的 AXI4-Lite 命令口：同样走自动化 ----
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/line_engine_top_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins line_engine_top_0/s_axi]

# 地址钉到 0x43B30000（自动化默认会给 0x40000000 附近的空闲段；这里显式改，
# PS 侧软件按这个常量写死。段对象按实例取，不猜 reg0/Reg 的名字）。
set _le_seg [get_bd_addr_segs -of_objects [get_bd_cells line_engine_top_0]]
if {[llength $_le_seg] > 0} {
    assign_bd_address -offset 0x43B30000 -range 0x00010000 \
        -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
        $_le_seg -force
    puts "INFO: 画线引擎命令口 → 0x43B30000（段 [get_property NAME $_le_seg]）"
} else {
    puts "WARN: 找不到 line_engine_top_0 的地址段，沿用自动分配地址"
}

# ★ 画线引擎的**写地址空间**必须挂上 HP0 的 DDR 段——否则 validate 报
#   [BD 41-1356] "Slave segment HP0_DDR_LOWOCM is not assigned into address space
#   </line_engine_top_0/m_axi>"，运行时写 DDR 落到未映射空间。
#   apply_bd_automation 一般会自动挂上；这里再兜一层（只缺才补，避免重复分配的警告）。
# ★★ 2026-10-31 实锤：`get_bd_addr_segs -of_objects <space>` 列的是**可分配**的段，
#   不是**已分配**的 —— 原来那个"已挂就不重复分配"的判断永远为真 ⇒ 这一跳被跳过 ⇒
#   S01 没有任何地址段 ⇒ 画线引擎的写全被互联回 DECERR（首上板：done 秒回、
#   一个像素都没落地，/dev/mem 读物理内存全 0）。这里改成**无条件分配**。
# ★ 取**m_axi 这个接口**的地址空间（按 cell 取会把 s_axi 那个也带出来，
#   变成两元素列表 -> assign_bd_address 报 BD 5-309）
set _le_sp [get_bd_addr_spaces -quiet -of_objects [get_bd_intf_pins line_engine_top_0/m_axi]]
if {[llength $_le_sp] == 0} {
    puts "ERROR: 找不到 line_engine_top_0 的地址空间"
} else {
    assign_bd_address -offset 0x00000000 -range 0x20000000 \
        -target_address_space $_le_sp \
        [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
    puts "INFO: line_engine 写地址空间补挂 HP0_DDR_LOWOCM"
    # 门禁：分配完必须真的能看到段，否则这个 bit 下去照样写不进 DDR
    # ★ 2026-11-01（2026.1）：`-addressed` 在新版返回空（实测 dump：段实际已分配，
    #   列出来的是 SEG_processing_system7_0_HP0_DDR_LOWOCM）。去掉 `-addressed` 兼容两版。
    set _le_seg2 [get_bd_addr_segs -quiet -of_objects $_le_sp]
    if {[llength $_le_seg2] == 0} {
        puts "ERROR: 分配后 line_engine 地址空间里仍没有段 —— 这个 bit 不能要"
    } else {
        puts "INFO: line_engine 地址空间 = $_le_seg2"
    }
}

# ============================================================================
# ★★ 2026-10-31 下午：PL 帧泵（把"透视矫正+打包写显存"从 PS 搬进 PL）
#   S_AXI（Lite，命令口）+ m_rd（读源图/行表）+ m_wr（写 framebuffer）
# ============================================================================
create_bd_cell -type module -reference frame_pump frame_pump_0
connect_bd_intf_net [get_bd_intf_pins frame_pump_0/m_rd] [get_bd_intf_pins axi_mem_intercon/S02_AXI]
connect_bd_intf_net [get_bd_intf_pins frame_pump_0/m_wr] [get_bd_intf_pins axi_mem_intercon/S03_AXI]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0]     [get_bd_pins axi_mem_intercon/S02_ACLK]     [get_bd_pins axi_mem_intercon/S03_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn]     [get_bd_pins axi_mem_intercon/S02_ARESETN]     [get_bd_pins axi_mem_intercon/S03_ARESETN]
puts "INFO: frame_pump_0 已接入（m_rd=S02 / m_wr=S03）"

# ============================================================================
# ★★ 2026-10-31：补回 **pe_axi_0（粒子引擎）** 和 **map_detect_axi_0（PL 检测）**
#   这两个当初是用补丁脚本（add_pe_to_bd.tcl / add_map_detect_to_bd.tcl）加进
#   **保存的 BD** 的，从没进过本脚本 —— 本机从零重建 BD 时它们直接消失了
#   （10-30 全量构建实锤：新旧 hwh 逐条对账，其余地址全对，唯独少了这两个 IP，
#    并且 10-22 删掉的 ila_0 又回来了）。
#   现在折进主脚本：重建即完整。地址与 PS 驱动常量一一对应（写死不许漂）：
#     v3_bundle_axi 0x40000000 / pe_axi 0x40001000 / map_detect 0x40002000
# ============================================================================
create_bd_cell -type module -reference pe_axi pe_axi_0
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/pe_axi_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins pe_axi_0/s_axi]
puts "INFO: pe_axi_0（粒子引擎）已接入"

create_bd_cell -type module -reference map_detect_axi map_detect_axi_0
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list \
    Master "/processing_system7_0/M_AXI_GP0" \
    Slave  "/map_detect_axi_0/s_axi" \
    intc_ip "/ps7_0_axi_periph" \
    Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto} \
    ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins map_detect_axi_0/s_axi]
puts "INFO: map_detect_axi_0（PL 检测）已接入"

# tap 并联：vision_core 写侧三根线各加一个汇点（只读观察，不改采集链）
# ★ 必须**引脚对引脚** connect（会并入已有网线）；`connect_bd_net <net> <pin>`
#   在 2023.2 报 BD 41-701（2026-10-20 实测）。
foreach {_src _dst} {wr_en tap_wr_en wr_addr tap_wr_addr wr_data tap_wr_data} {
    connect_bd_net [get_bd_pins vision_core_0/$_src] [get_bd_pins map_detect_axi_0/$_dst]
}
puts "INFO: map_detect tap 已并联（wr_en/wr_addr/wr_data）"

# 三个 IP 的地址显式钉死（自动化按顺序分配，顺序一变地址就漂，PS 驱动是写死的常量）
foreach {_cell _off} {v3_bundle_axi_0 0x40000000 pe_axi_0 0x40001000
                      map_detect_axi_0 0x40002000} {
    set _sg [get_bd_addr_segs -quiet -of_objects [get_bd_cells $_cell]]
    if {[llength $_sg] == 0} {
        puts "ERROR: 找不到 $_cell 的地址段"
    } else {
        assign_bd_address -offset $_off -range 0x00001000 \
            -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
            $_sg -force
        puts "INFO: $_cell 地址钉死 $_off"
    }
}

# ---- PL 帧泵：命令口走自动化（自动加 MI/时钟/复位/地址段）----
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config [list     Master "/processing_system7_0/M_AXI_GP0"     Slave  "/frame_pump_0/s_axi"     intc_ip "/ps7_0_axi_periph"     Clk_master {Auto} Clk_slave {Auto} Clk_xbar {Auto}     ddr_seg {Auto} master_apm {0} ] [get_bd_intf_pins frame_pump_0/s_axi]

set _fp_seg [get_bd_addr_segs -quiet -of_objects [get_bd_cells frame_pump_0]]
if {[llength $_fp_seg] > 0} {
    assign_bd_address -offset 0x43B40000 -range 0x00010000         -target_address_space [get_bd_addr_spaces processing_system7_0/Data]         $_fp_seg -force
    puts "INFO: 帧泵命令口 → 0x43B40000"
} else {
    puts "ERROR: 找不到 frame_pump_0 的地址段"
}

# 两个主口（读/写）都要挂 HP0 的 DDR 段
foreach _pin {m_rd m_wr} {
    set _sp [get_bd_addr_spaces -quiet -of_objects [get_bd_intf_pins frame_pump_0/$_pin]]
    if {[llength $_sp] == 0} {
        puts "ERROR: frame_pump_0/$_pin 没有地址空间"
    } else {
        assign_bd_address -offset 0x00000000 -range 0x20000000             -target_address_space $_sp             [get_bd_addr_segs processing_system7_0/S_AXI_HP0/HP0_DDR_LOWOCM] -force
        puts "INFO: frame_pump $_pin 地址空间已挂 HP0_DDR_LOWOCM"
    }
}

validate_bd_design
save_bd_design
puts "INFO: BD ov5640_m1a 生成并校验完成"
