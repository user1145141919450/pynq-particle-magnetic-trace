# ============================================================================
# add_hdmi_out.tcl —— ★HDMI 输出通路（P2 屏幕）★
#
# 由 bd/m1a_capture.tcl 在 **V3 收尾块之前** source（那里有说明为什么不能放最后）。
# 单独成文件：这段两百行，塞进 m1a_capture.tcl 会把"采集链"主线淹掉。
#
# ── 设计来源（2026-09-30 逐项查证，**没有一项是猜的**）──────────────────────
# 拓扑抄 PYNQ 官方 base overlay（已量产、已在跑）：
#     axi_vdma(MM2S 32bit) ──> v_axi4s_vid_out(24bit s_axis) ──> rgb2dvi ──> TMDS
#  ★ 中间**没有**任何适配器。base.hwh 实测：
#       /video/axi_vdma                          C_M_AXIS_MM2S_TDATA_WIDTH = 32
#       .../v_axi4s_vid_out_0                    C_S_AXIS_TDATA_WIDTH      = 24
#       .../v_axi4s_vid_out_0                    C_S_AXIS_VIDEO_FORMAT     = 2 (RGB888)
#    就是 32 位直连 24 位，Vivado 那句
#       "Width mismatch ... Only lower order bits will be connected"
#    是**预期行为不是 bug**：帧按 **32bpp** 存，一拍 = 一像素，低 3 字节 = BGR。
#  ★ 为什么必须 32bpp：PYNQ 的 dma.py:456 在运行时按 mode 写
#       MM2S_HSIZE(0x54)  = width * bytes_per_pixel
#       MM2S_STRIDE(0x58) = mode.stride          MM2S_VSIZE(0x50) = height
#    24bpp 时 HSIZE = width*3，32 位流每行只出 width*3/4 拍 ⇒ 像素数根本对不上。
#    只有 VideoMode(w,h,**32**) 才是"一行 width 拍、一拍一像素"。
#  ★ 还查过两条死路，别再试：
#    · v_axi4s_vid_out 的 C_ADDR_WIDTH 是**行缓冲地址位宽**（10/11/12/13 实测
#      s_axis 恒 24 位），配不出 32 位输入；
#    · C_S_AXIS_VIDEO_FORMAT=5/6 虽然能出 32 位 s_axis，但 vid_data 也变 32 位，
#      又跟 rgb2dvi 的 vid_pData[23:0] 对不上 —— 只是把不匹配挪到出端。
#    · v_pixel_pack 这个 IP **本机没装**（get_ipdefs 返回空），base 里那个
#      pixel_pack 是 hls 版且挂在 **hdmi_in** 上，与输出通路无关。
#
# ── 隔离决策：输出 VDMA 单独走 S_AXI_HP1（自己一条 mem 互连）──────────────
# 不共用 HP0：HP0 上挂着已验收的采集链（V3 那套 17/17 逐位一致就靠它）。
# 共享会引入仲裁/顺序耦合，宁可多花一点逻辑也不动已验证的路径。
#
# ── 资源 ─────────────────────────────────────────────────────────────────
# 加之前：LUT 58.99% / FF 27.93% / BRAM 9.64% / DSP 23.18%，有空间。
# ============================================================================

puts "INFO: HDMI: ===== 开始搭 HDMI 输出通路 ====="

# 门禁总开关（要在 hm_set 用到它之前就存在）
set ::hdmi_fail 0

# 逐参数设。★ 2026-09-30 踩坑后加严 ★
# 第一版只用 catch 兜底，结果**一条都没兜住**：Vivado 对
# `set_property CONFIG.<不存在的参数>` 只发一句 CRITICAL WARNING 就返回，
# **不抛异常** —— 于是 v_tc 那 30 多个参数名（跨版本改了，见下面 1c 的注释）
# 全都没设上，脚本却一路绿着往下走。这就是本工程反复吃亏的"静默失效"。
# 现在改成三步：①参数存在吗 ②设得进去吗 ③读回来对不对 —— 任一步不过就错。
proc hm_set {cell pairs} {
    set c [get_bd_cells -quiet $cell]
    if {[llength $c] == 0} {
        puts "ERROR: HDMI: 单元 $cell 不存在，参数没法设"
        set ::hdmi_fail 1
        return
    }
    set props [list_property $c]
    foreach {k v} $pairs {
        if {[lsearch -exact $props CONFIG.$k] < 0} {
            puts "ERROR: HDMI: $cell 没有 CONFIG.$k 这个参数（★名字写错了 —— \
                  本 IP 的参数名跨 Vivado 版本改过，别照抄老工程）"
            set ::hdmi_fail 1
            continue
        }
        if {[catch { set_property CONFIG.$k $v $c } e]} {
            puts "ERROR: HDMI: $cell 设 CONFIG.$k=$v 失败：$e"
            set ::hdmi_fail 1
            continue
        }
        # 读回：有的参数会规范化写法（true↔1、High↔1、RGB↔2…），
        # 所以只把**明显不同**的报成 WARN，不当门禁（门禁交给各 IP 自己的硬校验）。
        set rb [get_property CONFIG.$k $c]
        if {[string compare $rb $v] != 0} {
            puts "WARN: HDMI: $cell 的 CONFIG.$k 想设 $v，读回是 $rb（可能是规范化写法，核对一下）"
        }
    }
}

# ============================================================================
# 0) PS7：开 S_AXI_HP1（输出 VDMA 的专用 DDR 通道）
# ============================================================================
# ★★ 2026-09-30：这里原来只有一句 set_property 加一句**无条件**的 puts ——
#   又一例"打印成功 ≠ 真成功"（本工程数得出来的第五例）。改成设→验端口在不在。
set_property -dict [list CONFIG.PCW_USE_S_AXI_HP1 {1}] [get_bd_cells processing_system7_0]
if {[llength [get_bd_intf_pins -quiet processing_system7_0/S_AXI_HP1]] == 0} {
    puts "ERROR: HDMI: PCW_USE_S_AXI_HP1 设了但 S_AXI_HP1 口没出来（参数名不对 / 板卡 preset 里没这口）"
    error "HDMI: PS7 没开出 S_AXI_HP1"
}
puts "INFO: HDMI: PS7 已开 S_AXI_HP1（端口实测存在，不是嘴上说开）"

# ============================================================================
# 1) 六个单元
# ============================================================================
# ---- 1a. 输出 VDMA（只做 MM2S：DDR -> 流）----
# ★★ "派生量"陷阱（2026-09-30 第三次试错后才查清，本工程第四次静默/半静默失败）★★
#   hwh 里能看到的一些参数（C_M_AXIS_MM2S_TUSER_BITS / C_S_AXI_LITE_ADDR_WIDTH /
#   C_S_AXIS_TDATA_WIDTH …）**不是可配参数，是算出来的**：它们出现在 hwh 的
#   PARAMETER 列表里，却不在 BD cell 的 `list_property` 里 —— 于是
#   `set_property CONFIG.xxx` 只会打一条 CRITICAL WARNING 然后**什么都不做**。
#   ⇒ 抄 base overlay 的 hwh 时，"能看到"≠"能设"。能用 hwh 对照**值**，
#     但能不能设只能问 `list_property`（probe_vdma.tcl / probe_ip.tcl 干的就是这件事）。
#   本块里值全部对齐 base overlay /video/axi_vdma 的 hwh（它是这块板上跑通的配置）：
#     c_m_axi_mm2s_data_width=32（★不是 IP 默认的 64）/ c_mm2s_genlock_mode=1 /
#     c_mm2s_max_burst_length=32 / c_use_fsync=1 / c_flush_on_fsync=1
#   TUSER_BITS 由 c_mm2s_sof_enable / c_use_fsync 推出来（base 是 1），不用管。
# ★ c_prmry_is_aclk_async 也是**只读**的（`CRITICAL WARNING: [BD 41-737] ... It is
#   read-only`，实测回读恒为 1）。base 是 0、我们是 1，但两边都把三个 aclk 接同一个
#   FCLK_CLK0，实际无差别 —— 已从表里删掉，不再做这个无效动作。
# ★ 输出 VDMA 走**自己的 S_AXI_HP1**（见后面第 6 步）：它和抓图 VDMA 各占一个 HP
#   端口，绝不共用 —— 否则 60Hz 出图会和捕获互相饿死（本工程的老账）。
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma:6.3 axi_vdma_out
hm_set axi_vdma_out [list \
    c_include_mm2s {1} c_include_s2mm {0} \
    c_include_mm2s_dre {0} c_include_mm2s_sf {0} c_include_sg {0} \
    c_include_internal_genlock {1} \
    c_m_axi_mm2s_data_width {32} c_m_axis_mm2s_tdata_width {32} \
    c_mm2s_linebuffer_depth {512} c_mm2s_linebuffer_thresh {4} c_mm2s_max_burst_length {32} \
    c_mm2s_genlock_mode {1} c_mm2s_genlock_num_masters {1} c_mm2s_genlock_repeat_en {0} \
    c_mm2s_sof_enable {1} \
    c_num_fstores {4} c_dynamic_resolution {1} c_enable_vidprmtr_reads {1} \
    c_use_fsync {1} c_flush_on_fsync {1} c_enable_vert_flip {0} \
    c_addr_width {32} c_dlytmr_resolution {125} ]

# ---- 1b. 视频流 -> 视频时序 的转换器（24 位进 / 24 位出）----
# ★★ 2026-10-02 第十五例（HDMI 画面随机水平偏移的根因，改了 C_VTG_MASTER_SLAVE）★★
#   症状：画面被**循环左移**一段（首亮当天≈0，10-02 实测≈960~1024px），跨进程/重启
#         稳定不变、永不自愈；"彩条也错位"排除了游戏/软件层。
#   根因（读 IP 明文仿真模型 v_axi4s_vid_out_v4_0_vl_rfs.v 实锤）：
#     · C_VTG_MASTER_SLAVE=1(Master) ⇒ `VTG_EN = VID_CE` **恒 1** —— core 永远
#       不能暂停 v_tc 的时序发生器，对齐只能靠丢数据（flush），方向不对时
#       **无法纠回**（RTL 注释 "Indicates Early EOL... continue as normal"）；
#       开局相位落在哪就永久停在哪 ⇒ 偏移量 = 初始填充/hysteresis 附近（≈1024）
#       或 0，取决于上电时序掷的骰子。
#     · C_VTG_MASTER_SLAVE=0(Slave) ⇒ VTG_EN 由对齐状态机门控（CALN_* 期间拉低）
#       —— "pause 在 VTG 帧起点 → 等 FIFO 追到流 SOF → 充满 → 精确恢复"，
#       锁相到 S≈0，且**丢锁后能自动重新对齐**（LALN→IDLE→重锁）。
#     · IP tooltip 原文："It is recommended to always select 'Slave' mode. Slave
#       mode has the ability to dynamically adjust the phase difference..."，
#       且 Slave=0 是 IP **默认值**；base overlay 用的也是 1(抄的时候抄了隐患)。
#   ⇒ 改成 Slave(0) + 把 vtg_ce 接到 v_tc 的 gen_clken（见第 6 步）。
#     这两件事必须**成对**做：只改模式不接线 = 暂停信号发不出去 = 白改。
create_bd_cell -type ip -vlnv xilinx.com:ip:v_axi4s_vid_out:4.0 v_axi4s_vid_out_0
hm_set v_axi4s_vid_out_0 [list \
    C_ADDR_WIDTH {11} C_HAS_ASYNC_CLK {1} C_HYSTERESIS_LEVEL {1024} \
    C_VTG_MASTER_SLAVE {0} C_S_AXIS_VIDEO_FORMAT {2} C_S_AXIS_VIDEO_DATA_WIDTH {8} ]

# ---- 1c. 时序发生器（720p60；运行时由 PYNQ 的 _display 按模式重编）----
# ★★ 参数名陷阱（2026-09-30 实测，白跑了两轮 BD）★★
#   我第一版照抄 PYNQ base overlay 的 hwh，用的是 **C_GEN_*** 那套名字，
#   结果 Vivado 2023.2 的 v_tc 6.2 判它们**全都不存在**。真名是：
#       C_GEN_HACTIVE_SIZE      ->  GEN_HACTIVE_SIZE
#       C_GEN_HSYNC_POLARITY=1  ->  GEN_HSYNC_POLARITY=High   （★字符串，不是数字）
#       C_GEN_VIDEO_FORMAT=2    ->  GEN_VIDEO_FORMAT=RGB      （★字符串，不是数字）
#       C_GEN_HBLANK_EN 等      ->  horizontal_blank_generation 等（★小写）
#       C_HAS_AXI4_LITE         ->  HAS_AXI4_LITE
#   而且这套参数名**跨版本改过**（base overlay 是更老的 Vivado 编的），
#   所以"抄一份跑通的 hwh"这条经验在这里**不成立** —— 查 IP 目录才是真的。
#   权威名单来自同目录的 probe_ip.tcl（把 90 个 CONFIG.* 全 dump 出来对比）。
#
# ★ 另一条省事又更稳的路：v_tc 自带 `VIDEO_MODE` 预设，实测默认就是 720p，
#   而且它填的 GEN_HACTIVE_SIZE=1280 / GEN_HFRAME_SIZE=1650 / GEN_HSYNC_START=1390
#   / GEN_HSYNC_END=1430 / GEN_VACTIVE_SIZE=720 / GEN_F0_VFRAME_SIZE=750
#   / GEN_F0_VSYNC_VSTART=724 / VEND=729 **正好就是 720p 该有的值**。
#   ⇒ 只设 VIDEO_MODE，F0/F1 那一堆让它自己算，比手写二十个数字少一半出错机会，
#     再用下面的硬校验把结果钉死（这正是本工程"不猜、只验"的路子）。
create_bd_cell -type ip -vlnv xilinx.com:ip:v_tc:6.2 vtc_out
hm_set vtc_out [list \
    enable_detection {false} enable_generation {true} \
    HAS_AXI4_LITE {true} HAS_INTC_IF {false} frame_syncs {1} \
    VIDEO_MODE {720p} \
    GEN_VIDEO_FORMAT {RGB} GEN_INTERLACED {false} \
    GEN_HSYNC_POLARITY {High} GEN_VSYNC_POLARITY {High} \
    GEN_AVIDEO_POLARITY {High} GEN_HBLANK_POLARITY {High} GEN_VBLANK_POLARITY {High} \
    horizontal_sync_generation {1} vertical_sync_generation {1} \
    horizontal_blank_generation {1} vertical_blank_generation {1} \
    active_video_generation {1} ]

# ★硬校验：720p 的八个关键数字必须真是这几个 —— 参数名/预设写错都在这里现形
foreach {k want} {
    GEN_HACTIVE_SIZE 1280 GEN_HFRAME_SIZE 1650 GEN_HSYNC_START 1390 GEN_HSYNC_END 1430
    GEN_VACTIVE_SIZE 720  GEN_F0_VFRAME_SIZE 750
    GEN_F0_VSYNC_VSTART 724 GEN_F0_VSYNC_VEND 729
} {
    set got [get_property CONFIG.$k [get_bd_cells vtc_out]]
    if {[string compare $got $want] != 0} {
        puts "ERROR: HDMI: vtc_out 的 $k = $got，要的是 $want —— 720p 没配成"
        set ::hdmi_fail 1
    }
}
puts "INFO: HDMI: vtc_out 已锁定 720p（八个关键时长逐个校验）"

# ---- 1d. 像素时钟发生器（Digilent；参考钟 = FCLK0 100MHz）----
# ★ kRefClkFreqHz 必须**显式**设成 100000000：IP 靠这个数反算 MMCM 的
#   M/D/O 分频比去生成像素钟。默认值恰好也是 100MHz，但那是"碰巧对"，
#   一旦 FCLK0 改频/换板就会静默算错（PLL 照样锁、就是分辨率不对）。
# ★ 上板验真把手：AXI 偏移 0x20 是**只读**的参考频率寄存器（axi_dynclk_S00_AXI.vhd
#   里 `slv_reg8 := kRefClkFreqHz`），读回来是 100000000 才说明映射没认错。
create_bd_cell -type ip -vlnv digilentinc.com:ip:axi_dynclk:1.2 axi_dynclk
hm_set axi_dynclk [list kRefClkFreqHz {100000000}]

# ---- 1e. TMDS 编码 + 串化（Digilent）----
# kGenerateSerialClk=false ⇒ 串行钟由 axi_dynclk/PXL_CLK_5X_O 提供，
# 此时 kClkRange 是**禁用参数**（写了只会得到一条 disabled parameter 警告）。
create_bd_cell -type ip -vlnv digilentinc.com:ip:rgb2dvi:1.4 rgb2dvi_0
hm_set rgb2dvi_0 [list kGenerateSerialClk {false} kRstActiveHigh {false}]

# ---- 1f. HPD（热插拔检测线）----
# ★ 板文件实测（part0_pins.xml + board.xml）：
#     pin index 87 = hdmi_tx_hpd = **R19**，iostandard LVCMOS33；
#     接口 hdmi_out_hpd_led = gpio_rtl:1.0，TRI_O/TRI_I/TRI_T 三个都映射到 hdmi_tx_hpd，
#     其中 TRI_O dir=out。⇒ 这是**三态能力**的线，板文件预设按输出用（output_1bit_preset）。
#   （顺带纠一个坑：board.xml 里 part0_hdmi_out_hpd_led 那条 connection 写的
#     c1_st_index=97，而 97 其实是 hdmi_in_ddc_sda —— 板文件自己串行了。
#     我们不信那个 index，只信接口 port_map 给的 hdmi_tx_hpd→R19。）
# ★ 默认值**对齐 base overlay**：C_DOUT_DEFAULT = 0x00000000（不是 1）。
#   我上一版擅自写成 1，属于"没根据的擅改" —— base 这台机器人在同一块板子上
#   是跑通的，它用 0 我们就用 0；要拉高由软件在 start() 里**显式**写并打印回读，
#   这样"到底拉没拉高"是日志里看得见的事，而不是藏在配置里。
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 hdmi_out_hpd_video
hm_set hdmi_out_hpd_video [list \
    C_ALL_OUTPUTS {1} C_GPIO_WIDTH {1} C_INTERRUPT_PRESENT {0} C_DOUT_DEFAULT {0x00000000} ]

puts "INFO: HDMI: 6 个单元建完"

# ============================================================================
# 2) 顶层端口
# ============================================================================
create_bd_intf_port -mode Master -vlnv digilentinc.com:interface:tmds_rtl:1.0 TMDS_out
create_bd_port -dir O hdmi_out_hpd

# ============================================================================
# 3) 常量（CE / 零）
# ============================================================================
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xl_hdmi_ce
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] [get_bd_cells xl_hdmi_ce]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xl_hdmi_zero
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {0}] [get_bd_cells xl_hdmi_zero]
# ★ 2026-10-01 第十一例（**HDMI 黑屏的真根因**）：
#   rst_pixel 的 aux_reset_in 是**低有效**（C_AUX_RESET_HIGH 只读=0），
#   恒喂 0 就等于**辅助复位一直按着** ⇒ peripheral_aresetn 永远不释放
#   ⇒ vtc_out/resetn 被钉死 ⇒ v_tc 不产时序 ⇒ 下游 VDMA 0 帧、黑屏。
#   所以必须有一个**恒定 1** 的常量专门喂它（细节见下面 aux_reset_in 连接处）。
create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 xl_hdmi_one
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] [get_bd_cells xl_hdmi_one]

# ============================================================================
# 4) 视频接口连接
# ============================================================================
connect_bd_intf_net [get_bd_intf_pins axi_vdma_out/M_AXIS_MM2S]     [get_bd_intf_pins v_axi4s_vid_out_0/video_in]
connect_bd_intf_net [get_bd_intf_pins vtc_out/vtiming_out]          [get_bd_intf_pins v_axi4s_vid_out_0/vtiming_in]
connect_bd_intf_net [get_bd_intf_pins v_axi4s_vid_out_0/vid_io_out] [get_bd_intf_pins rgb2dvi_0/RGB]
# ★★ 第七例静默失败（2026-09-30）：顶层**接口口**必须用 `get_bd_intf_ports`。
#   原来写的是 `[get_bd_intf_pins TMDS_out]` —— 这么写 Vivado **不报错**，
#   但生成的是**一条只有 rgb2dvi 一头的孤网** `/rgb2dvi_0_TMDS`，
#   顶层口根本没接上（第 10 步的门禁 + 拓扑打印把它抓出来了：
#   `/rgb2dvi_0_TMDS -> /rgb2dvi_0/TMDS` 只有一头，且列表里完全没有 TMDS_out）。
#   症状会一路藏到上板：显示器全黑、综合时序全绿。
set _tmds_port [get_bd_intf_ports -quiet TMDS_out]
if {[llength $_tmds_port] == 0} {
    puts "ERROR: HDMI: 取不到顶层接口口 TMDS_out"
    error "HDMI: TMDS_out 顶层口不存在"
}
connect_bd_intf_net [get_bd_intf_pins rgb2dvi_0/TMDS] $_tmds_port
puts "INFO: HDMI: 视频接口连完（VDMA -> vid_out -> rgb2dvi -> TMDS）"

# ============================================================================
# 5) 时钟：FCLK0 供 AXI/参考；dynclk 的像素钟供 vid_out 出端 + vtc
# ============================================================================
# 接到**已有的** FCLK0 网上（把已连的 pin 当锚点，新脚并入同一条网）
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins processing_system7_0/S_AXI_HP1_ACLK] \
    [get_bd_pins axi_vdma_out/s_axi_lite_aclk] \
    [get_bd_pins axi_vdma_out/m_axi_mm2s_aclk] \
    [get_bd_pins axi_vdma_out/m_axis_mm2s_aclk] \
    [get_bd_pins v_axi4s_vid_out_0/aclk] \
    [get_bd_pins vtc_out/s_axi_aclk] \
    [get_bd_pins axi_dynclk/REF_CLK_I] \
    [get_bd_pins axi_dynclk/s_axi_lite_aclk] \
    [get_bd_pins hdmi_out_hpd_video/s_axi_aclk]

# ★ 像素钟：同时进 rgb2dvi/PixelClk、vid_out/vid_io_out_clk、vtc_out/clk
#   —— 时序发生器必须跟视频输出同一个钟，否则 hsync/vsync 和像素对不上。
connect_bd_net [get_bd_pins axi_dynclk/PXL_CLK_O] \
    [get_bd_pins rgb2dvi_0/PixelClk] \
    [get_bd_pins v_axi4s_vid_out_0/vid_io_out_clk] \
    [get_bd_pins vtc_out/clk]
connect_bd_net [get_bd_pins axi_dynclk/PXL_CLK_5X_O] [get_bd_pins rgb2dvi_0/SerialClk]
# MMCM 锁定才放开 rgb2dvi —— base 就是这么接的
connect_bd_net [get_bd_pins axi_dynclk/LOCKED_O]     [get_bd_pins rgb2dvi_0/aRst_n]

# ============================================================================
# 6) 复位 / 使能
# ============================================================================
# ---- 像素域复位同步器（vtc_out/resetn 必须在像素钟域里释放）----
# ★★ 2026-09-30 第九例：这次工具**喊了**，差点被我当噪音放过去 ★★
#   第一次门禁全过的那跑里 Vivado 报了：
#     CRITICAL WARNING: [BD 41-1343] Reset pin /vtc_out/resetn
#       (associated clock /vtc_out/clk) is connected to reset source
#       /rst_ps7_0_100M/peripheral_aresetn (synchronous to FCLK_CLK0)
#   翻译：vtc_out/resetn 属于**像素钟域**，却被 100MHz 域的复位直接驱动。
#   要命的是这两个域在 XDC 里已被声明为 asynchronous ⇒
#   **时序引擎根本不查这条路径**，等于一个完全没约束的复位跨域。
#   复位释放沿落在像素钟的任意相位上，可能把 vtc 的内部状态机停在半路 ——
#   症状是偶发花屏/黑屏（"偶发"正是这类问题最难查的样子）。
#   修法 = 像素域加一级 proc_sys_reset 当复位同步器：
#     slowest_sync_clk ← PXL_CLK_O（像素钟）
#     ext_reset_in    ← peripheral_aresetn（100MHz 域的源复位）
#     dcm_locked      ← axi_dynclk/LOCKED_O
#   ★ 接 dcm_locked 这一笔顺手把"上电顺序"也解决了：
#     dynclk 没使能时像素钟是停的、LOCKED_O=0 ⇒ vtc 一直被按在复位里；
#     软件使能 dynclk、MMCM 锁定后，复位才**同步着**释放。
#     将来软件改分辨率（停钟→改参→起钟）也天然重新同步一次。
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pixel
# ★ 为什么**不设 C_EXT_RESET_HIGH**：BD 里它是**只读**的，试过：
#     CRITICAL WARNING: [BD 41-737] Cannot set the parameter C_EXT_RESET_HIGH
#                        on /rst_pixel. It is read-only.
#   那就别跟它较劲 —— **改喂一个天然高有效的复位源**，让这个只读默认值 1 正好是对的：
#     rst_ps7_0_100M/peripheral_aresetn  ← 低有效；配只读默认值 1 = **极性反了** ✗
#     rst_ps7_0_100M/peripheral_reset    ← 高有效；配只读默认值 1 = **正好** ✓
#   （这两个是同一个 proc_sys_reset 的互补输出，功能等价，只是极性写法不同。）
#   ★ 实证（上一跑）：喂 aresetn 时回读 C_EXT_RESET_HIGH=1，
#     即"把低有效当高有效" ⇒ vtc 被**反着**复位：正常跑的时候反倒一直按着（黑屏），
#     上电复位期间倒是放开的。典型的"显示器全黑、综合时序全绿"哑巴故障。
#   ★★ 教训：只读参数不要去"设"，要**换一个让它天然正确的接法**，
#      再用回读把它钉死（下面的实测门禁）。
set _prst [get_bd_pins -quiet rst_ps7_0_100M/peripheral_reset]
if {[llength $_prst] == 0} {
    puts "ERROR: HDMI: rst_ps7_0_100M/peripheral_reset 这个脚不存在"
    puts "ERROR: HDMI:   （换高有效源这条路走不通，就得改用 util_vector_logic 反相）"
    error "HDMI: 找不到高有效的 peripheral_reset"
}
connect_bd_net [get_bd_pins axi_dynclk/PXL_CLK_O]        [get_bd_pins rst_pixel/slowest_sync_clk]
connect_bd_net $_prst                                     [get_bd_pins rst_pixel/ext_reset_in]
connect_bd_net [get_bd_pins axi_dynclk/LOCKED_O]         [get_bd_pins rst_pixel/dcm_locked]
# ★★ 2026-10-01 ★★ 黑屏真根因就在这两行：
#   rst_pixel 的 C_AUX_RESET_HIGH 是**只读的 0 = 低有效**（改不了，见上面第十例），
#   低有效的 aux_reset_in **喂 0 = 辅助复位恒按着** ⇒ 内部 asr_d1≡1 ⇒
#     lpf_int <= lpf_exr or lpf_asr or srl_time_out or not Dcm_locked  恒 1
#   ⇒ seq_clr 恒 0 ⇒ 复位序列计数器不前进 ⇒ **peripheral_aresetn 永远是 0**。
#   而 peripheral_aresetn 的唯一消费者就是 `vtc_out/resetn`（网表逐条查过）⇒
#   v_tc 被钉在复位里 ⇒ 不产 720p 时序 ⇒ v_axi4s_vid_out 收不到有效视频
#   ⇒ 从不拉 AXI-Stream ⇒ MM2S 表现为"RS=1、不 halted、不 idle、无错、0 帧"。
#   ★ 实证（同工程对照）：**工作正常**的 rst_ps7_0_100M 网表里 aux_reset_in 接 1'b1；
#     我们的 rst_pixel 接的是常量 0 —— 同 IP 同参数，唯一差别就是这根脚。
#   ★ RTL 实锤：proc_sys_reset_v5_0_vh_rfs.vhd
#       :1031  ACTIVE_LOW_AUX: asr_d1 <= not Auxiliary_System_Reset;
#       :929   if asr_and='1' then lpf_asr <= '1';   （粘滞，放不掉）
#       :897   lpf_int <= lpf_exr or lpf_asr or srl_time_out or not Dcm_locked;
#   ⇒ 喂一个**常数 1**（新增的 xl_hdmi_one）；mb_debug_sys_rst 是**高有效**，仍喂 0。
connect_bd_net [get_bd_pins xl_hdmi_one/dout]  [get_bd_pins rst_pixel/aux_reset_in]
connect_bd_net [get_bd_pins xl_hdmi_zero/dout] [get_bd_pins rst_pixel/mb_debug_sys_rst]

# ★★ 极性实测门禁（2026-09-30 第十例静默失败的候选）★★
#   接的是**高有效**的 peripheral_reset ⇒ 只读的 C_EXT_RESET_HIGH 必须是 **1**。
#   回读是 0 就说明工具把它当低有效了 = 又反了，当场停，绝不带着黑屏风险往下走。
set _erh [get_property CONFIG.C_EXT_RESET_HIGH [get_bd_cells rst_pixel]]
puts "INFO: HDMI: rst_pixel 的 C_EXT_RESET_HIGH 回读 = $_erh（接高有效源 peripheral_reset，应为 1）"
if {$_erh != 1} {
    puts "ERROR: HDMI: rst_pixel 的 ext_reset_in 被当成**低有效**（回读=$_erh），"
    puts "ERROR: HDMI:   而我们接的 peripheral_reset 是**高有效** —— 极性反了，"
    puts "ERROR: HDMI:   vtc 会被反着复位（正常运行时反而一直复位=黑屏）。"
    puts "ERROR: HDMI:   修法：在中间串一个 util_vector_logic 反相。"
    error "HDMI: rst_pixel 复位极性反了"
}

# ★ 注意：这里**不再**有 vtc_out/resetn —— 它改吃像素域的 rst_pixel/peripheral_aresetn。
#   vtc_out/s_axi_aresetn 仍留在 100MHz 域（它本来就是 AXI 侧的复位），这条没毛病。
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins axi_vdma_out/axi_resetn] \
    [get_bd_pins v_axi4s_vid_out_0/aresetn] \
    [get_bd_pins vtc_out/s_axi_aresetn] \
    [get_bd_pins axi_dynclk/s_axi_lite_aresetn] \
    [get_bd_pins hdmi_out_hpd_video/s_axi_aresetn]
connect_bd_net [get_bd_pins rst_pixel/peripheral_aresetn] [get_bd_pins vtc_out/resetn]

# CE 恒 1；vid_io_out_reset / fid 恒 0（像素域没有独立复位，base 同样处理）
# ★★ 2026-10-02 第十五例（第二处）：**vtc_out/gen_clken 不再挂在这条恒 1 网上** ★★
#   原来 gen_clken 恒 1 = 时序发生器永不暂停 —— 配合下面新接的 vtg_ce，
#   把"发生器时钟使能"交给 vid_out 的对齐状态机（官方参考设计的标准接法：
#   vtg_ce(vid_out 输出) -> gen_clken(v_tc 输入)）。只把 gen_clken 从这里拿走，
#   其余照旧。门禁在文件后段（同网判定 + 不许回到 CE 网）。
connect_bd_net [get_bd_pins xl_hdmi_ce/dout] \
    [get_bd_pins v_axi4s_vid_out_0/aclken] \
    [get_bd_pins v_axi4s_vid_out_0/vid_io_out_ce] \
    [get_bd_pins vtc_out/clken] \
    [get_bd_pins vtc_out/s_axi_aclken]
# ★ 对齐握手：vid_out 的 vtg_ce 输出 -> v_tc 的发生器时钟使能（Slave 模式的相位
#   调整手段；两个脚都在 PXL_CLK_O 像素钟域，无跨域问题）。
connect_bd_net [get_bd_pins v_axi4s_vid_out_0/vtg_ce] \
    [get_bd_pins vtc_out/gen_clken]
connect_bd_net [get_bd_pins xl_hdmi_zero/dout] \
    [get_bd_pins v_axi4s_vid_out_0/vid_io_out_reset] \
    [get_bd_pins v_axi4s_vid_out_0/fid]

connect_bd_net [get_bd_pins hdmi_out_hpd_video/gpio_io_o] [get_bd_pins hdmi_out_hpd]

# ============================================================================
# 7) AXI-Lite：往已有的 ps7_0_axi_periph 上再加 4 个 MI
# ============================================================================
# ★ 不写死 M11..M14：读当前 NUM_MI 再追加，这样**谁在前谁在后都不会错**
#   （V3 的 apply_bd_automation 收尾时也会自己追加一个 MI）。
# ★ 后缀 `_AXI` 不能少：MI 引脚的真名是 M00_AXI/M01_AXI/…（见 m1a_capture.tcl
#   里 M00_AXI~M10_AXI 的既有写法）。第一版我写成 "M11"，Vivado 回
#   "No interface pins matched 'get_bd_intf_pins ps7_0_axi_periph/M11'"。
#   ——底下的 **_ACLK / _ARESETN** 同理，所以这里保留不带 _AXI 的基名单独一份。
set nm [get_property CONFIG.NUM_MI [get_bd_cells ps7_0_axi_periph]]
set m_vdma [format "M%02d" $nm]
set m_vtc  [format "M%02d" [expr {$nm + 1}]]
set m_clk  [format "M%02d" [expr {$nm + 2}]]
set m_hpd  [format "M%02d" [expr {$nm + 3}]]
puts "INFO: HDMI: ps7_0_axi_periph NUM_MI $nm -> [expr {$nm + 4}]（新增 $m_vdma/$m_vtc/$m_clk/$m_hpd）"
set_property -dict [list CONFIG.NUM_MI [expr {$nm + 4}]] [get_bd_cells ps7_0_axi_periph]

connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/${m_vdma}_AXI] [get_bd_intf_pins axi_vdma_out/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/${m_vtc}_AXI]  [get_bd_intf_pins vtc_out/ctrl]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/${m_clk}_AXI]  [get_bd_intf_pins axi_dynclk/S_AXI_LITE]
connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/${m_hpd}_AXI]  [get_bd_intf_pins hdmi_out_hpd_video/S_AXI]

# ★ 新 MI 的时钟/复位**必须显式接**：NUM_MI 变大后 Vivado 不会自动补，
#   漏接只是 WARNING，上板表现是"寄存器读回恒 0"——正是本工程反复踩的那类坑。
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins ps7_0_axi_periph/${m_vdma}_ACLK] \
    [get_bd_pins ps7_0_axi_periph/${m_vtc}_ACLK] \
    [get_bd_pins ps7_0_axi_periph/${m_clk}_ACLK] \
    [get_bd_pins ps7_0_axi_periph/${m_hpd}_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins ps7_0_axi_periph/${m_vdma}_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/${m_vtc}_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/${m_clk}_ARESETN] \
    [get_bd_pins ps7_0_axi_periph/${m_hpd}_ARESETN]

# ============================================================================
# 8) 输出 VDMA 的 DDR 通道：自己的 mem 互连 -> S_AXI_HP1
# ============================================================================
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_mem_intercon_out
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells axi_mem_intercon_out]
connect_bd_intf_net [get_bd_intf_pins axi_vdma_out/M_AXI_MM2S]     [get_bd_intf_pins axi_mem_intercon_out/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_mem_intercon_out/M00_AXI] [get_bd_intf_pins processing_system7_0/S_AXI_HP1]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins axi_mem_intercon_out/ACLK] \
    [get_bd_pins axi_mem_intercon_out/S00_ACLK] \
    [get_bd_pins axi_mem_intercon_out/M00_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins axi_mem_intercon_out/ARESETN] \
    [get_bd_pins axi_mem_intercon_out/S00_ARESETN] \
    [get_bd_pins axi_mem_intercon_out/M00_ARESETN]
puts "INFO: HDMI: 输出 VDMA 走 S_AXI_HP1（独立于采集链的 HP0）"

# ============================================================================
# 9) 地址分配
# ============================================================================
# axi_dynclk 故意挑 0x43C10000 —— 和 PYNQ base 里的地址一模一样（少一个变量）
# ★ 2026-09-30：第一版把 segment 名字写死成 `<接口>/Reg`，结果报
#   `[BD 5-432] A peripheral must be specified when '-offset and -range' option
#   exists` —— 四个里有名字取空的（`get_bd_addr_segs` 返回空 list 时命令就长这样，
#   而且**不说是哪一个**，这正是本工程最讨厌的静默型报错）。
#   ⇒ 改成：从接口引脚反查它底下的地址段，**每个都打出来**，取不到就当场报错退出。
#     名字是 IP 给的，不是我们猜的。
foreach {off cell ifname} {
    0x43C00000 axi_vdma_out          S_AXI_LITE
    0x43C10000 axi_dynclk            S_AXI_LITE
    0x43C20000 vtc_out               ctrl
    0x43C30000 hdmi_out_hpd_video    S_AXI
} {
    set segs [get_bd_addr_segs -quiet -of_objects [get_bd_intf_pins $cell/$ifname]]
    puts "INFO: HDMI: $cell/$ifname 底下的地址段 -> $segs"
    if {[llength $segs] == 0} {
        puts "ERROR: HDMI: $cell/$ifname 底下取不到地址段（接口名写错？IP 没这个接口？）"
        error "HDMI: $cell 的地址段缺失"
    }
    if {[llength $segs] > 1} {
        puts "WARN: HDMI: $cell/$ifname 底下有 [llength $segs] 个地址段，取第一个 [lindex $segs 0]"
    }
    assign_bd_address -offset $off -range 0x00010000 \
        -target_address_space [get_bd_addr_spaces processing_system7_0/Data] \
        [lindex $segs 0] -force
}
# ---- 9b. 输出 VDMA 的 DDR 段（HP1）----
# ★ 2026-09-30：第一版照 HP0 的样子写死 `processing_system7_0/S_AXI_HP1/HP1_DDR_LOWOCM`，
#   结果 Vivado 报 `[BD 5-432] A peripheral must be specified when '-offset and -range'
#   option exists` —— 那个坐标取空了（HP0 那条同名写法则能用）。
#   ⇒ 名字不猜，把 HP1 底下**实际有**的段全打出来，按"DDR"挑一个；挑不着就**报错退出**，
#     绝不跳过（跳过 = 输出 VDMA 没有 DDR 可写，综合照过、上板黑屏）。
#   同时把 axi_vdma_out 自己的地址空间也打出来，好确认 Data_MM2S 这个名字对不对。
puts "INFO: HDMI: axi_vdma_out 的地址空间 -> [get_bd_addr_spaces -quiet -of_objects [get_bd_cells axi_vdma_out]]"
set hp1_seg ""
foreach s [get_bd_addr_segs -quiet -of_objects [get_bd_intf_pins processing_system7_0/S_AXI_HP1]] {
    set nm [get_property NAME $s]
    puts "INFO: HDMI: HP1 下的地址段：$s"
    if {$hp1_seg eq "" && [string match "*DDR*" $nm]} { set hp1_seg $s }
}
if {$hp1_seg eq ""} {
    puts "ERROR: HDMI: S_AXI_HP1 底下找不到名字含 DDR 的地址段 —— 输出 VDMA 没地方取帧"
    error "HDMI: HP1 的 DDR 地址段缺失"
}
set mm2s_as [get_bd_addr_spaces -quiet axi_vdma_out/Data_MM2S]
if {[llength $mm2s_as] == 0} {
    puts "ERROR: HDMI: 找不到 axi_vdma_out/Data_MM2S 这个地址空间"
    error "HDMI: MM2S 地址空间缺失"
}
puts "INFO: HDMI: HP1 用的 DDR 段 = $hp1_seg  目标地址空间 = $mm2s_as"
assign_bd_address -offset 0x00000000 -range 0x20000000 \
    -target_address_space $mm2s_as $hp1_seg -force
puts "INFO: HDMI: 地址：vdma 0x43C00000 / dynclk 0x43C10000 / vtc 0x43C20000 / hpd 0x43C30000"

# ============================================================================
# 10) ★硬门禁★（本工程的规矩：悬空引脚只是 WARNING，合成照过，上板才现形）
# ============================================================================
# ★ 这里**不能**再 `set hdmi_fail 0`：hm_set 在 1a~1f 阶段（本块之前）就可能已经
#   记过失败了，清零会把它们抹掉 —— 那正是"静默失效"的复发方式。
#   全局初值在文件最上面设过一次，就够了。
proc hm_gate {what cond} {
    if {!$cond} { puts "ERROR: HDMI: 门禁失败 —— $what"; set ::hdmi_fail 1 }
}

foreach c {axi_vdma_out v_axi4s_vid_out_0 vtc_out axi_dynclk rgb2dvi_0 hdmi_out_hpd_video rst_pixel} {
    hm_gate "单元 $c 没建出来" [expr {[llength [get_bd_cells -quiet $c]] > 0}]
}

# ★ 像素域复位必须真的分开了（否则 BD 41-1343 那条 CRITICAL WARNING 会再来）
#   判据不是"有没有接网"，而是"接的是不是对的网"：
#     vtc_out/resetn       必须 = rst_pixel/peripheral_aresetn（像素域）
#     vtc_out/s_axi_aresetn 必须 = rst_ps7_0_100M/peripheral_aresetn（AXI 域）
proc hm_same_net_obj {a b} {
    set na [get_bd_nets -quiet -of_objects $a]
    set nb [get_bd_nets -quiet -of_objects $b]
    return [expr {[llength $na] > 0 && [string compare $na $nb] == 0}]
}
foreach {pin ref want} {
    vtc_out/resetn       rst_pixel/peripheral_aresetn          rst_pixel
    vtc_out/s_axi_aresetn rst_ps7_0_100M/peripheral_aresetn    rst_ps7_0_100M
} {
    set pp [get_bd_pins -quiet $pin]
    set rp [get_bd_pins -quiet $ref]
    if {[llength $pp] == 0 || [llength $rp] == 0} {
        hm_gate "$pin 或 $ref 不存在" 0
    } else {
        hm_gate "$pin 没接到 $want/peripheral_aresetn 上（复位域混了）" [hm_same_net_obj $pp $rp]
    }
}
# 像素域复位同步器的三根输入都不能悬空
foreach spec {rst_pixel/slowest_sync_clk rst_pixel/ext_reset_in rst_pixel/dcm_locked rst_pixel/peripheral_aresetn} {
    set p [get_bd_pins -quiet $spec]
    hm_gate "rst_pixel：$spec 不存在" [expr {[llength $p] > 0}]
    hm_gate "rst_pixel：$spec 悬空（复位同步器会失效）" [expr {[llength [get_bd_nets -quiet -of_objects $p]] > 0}]
}
# ★★ aux_reset_in 电平门禁（2026-10-01 第十一例，**黑屏的真根因**）★★
#   C_AUX_RESET_HIGH=0 ⇒ 低有效 ⇒ **必须喂 1**（喂 0 = 辅助复位恒按着，
#   peripheral_aresetn 永远不释放 ⇒ vtc_out 永远在复位 ⇒ 黑屏 + VDMA 0 帧）。
#   "接上网了"不够 —— 要钉死"接的是那个**恒定 1**的网，而且它读回来真的是 1"。
hm_gate "rst_pixel/aux_reset_in 没接在 xl_hdmi_one（恒定 1）上 —— 低有效脚接 0 会把 vtc 按死在复位" \
        [hm_same_net_obj [get_bd_pins -quiet rst_pixel/aux_reset_in] [get_bd_pins -quiet xl_hdmi_one/dout]]
hm_gate "rst_pixel/mb_debug_sys_rst 没接在 xl_hdmi_zero（恒定 0）上 —— 它是高有效，接 1 会一直复位" \
        [hm_same_net_obj [get_bd_pins -quiet rst_pixel/mb_debug_sys_rst] [get_bd_pins -quiet xl_hdmi_zero/dout]]
set _cv [get_property -quiet CONFIG.CONST_VAL [get_bd_cells xl_hdmi_one]]
puts "INFO: HDMI: xl_hdmi_one 的 CONST_VAL 回读 = '$_cv'（必须是 1）"
# ★ 解析不了就**按不通过**处理（判定不了 ≠ 通过 —— 本工程的老规矩）
set _cvok 0
if {[catch {expr {$_cv == 1}} _cvok]} {
    set _cvok 0
    puts "ERROR: HDMI: xl_hdmi_one 的 CONST_VAL='$_cv' 解析不了 —— 判定不了，按不通过算"
}
hm_gate "xl_hdmi_one 的 CONST_VAL='$_cv' 不是 1" [expr {$_cvok != 0}]
# ★★ 相位对齐门禁（2026-10-02 第十五例，画面随机水平偏移的根因）★★
#   1) C_VTG_MASTER_SLAVE 必须是 0（Slave）—— Master 时 vtg_ce 恒 1、
#      相位永远不可调（上板表现：固定偏移、永不自愈）。
#   2) vtg_ce 与 gen_clken 必须在**同一条网**上（Slave 的暂停信号通路）。
#   3) gen_clken **不许**回到恒 1 的 CE 网上（又挂回去 = 通路失效、白改）。
#   判定不了 = 不通过（本工程老规矩）。
set _ms [get_property -quiet CONFIG.C_VTG_MASTER_SLAVE [get_bd_cells v_axi4s_vid_out_0]]
puts "INFO: HDMI: C_VTG_MASTER_SLAVE 回读 = '$_ms'（必须是 0 = Slave）"
set _msok 0
if {[catch {expr {$_ms == 0}} _msok]} {
    set _msok 0
    puts "ERROR: HDMI: C_VTG_MASTER_SLAVE='$_ms' 解析不了 —— 判定不了，按不通过算"
}
hm_gate "C_VTG_MASTER_SLAVE 不是 0（Master 模式相位不可调）" [expr {$_msok != 0}]
set _vc_pin [get_bd_pins -quiet v_axi4s_vid_out_0/vtg_ce]
set _gk_pin [get_bd_pins -quiet vtc_out/gen_clken]
if {[llength $_vc_pin] == 0 || [llength $_gk_pin] == 0} {
    hm_gate "vtg_ce 或 gen_clken 引脚不存在（端口名变了？）" 0
} else {
    hm_gate "vtg_ce 没接到 gen_clken（Slave 的暂停信号发不出去）" \
            [hm_same_net_obj $_vc_pin $_gk_pin]
    set _ce_pin [get_bd_pins -quiet xl_hdmi_ce/dout]
    if {[llength $_ce_pin] > 0} {
        hm_gate "gen_clken 又回到了恒 1 的 CE 网上（应已摘除）" \
                [expr {![hm_same_net_obj $_gk_pin $_ce_pin]}]
    }
}
# 关键输入脚必须真有网
foreach spec {
    {axi_vdma_out/axi_resetn} {axi_vdma_out/m_axis_mm2s_aclk}
    {v_axi4s_vid_out_0/aclk} {v_axi4s_vid_out_0/vid_io_out_clk} {v_axi4s_vid_out_0/aresetn}
    {vtc_out/clk} {vtc_out/s_axi_aclk} {vtc_out/resetn}
    {axi_dynclk/REF_CLK_I} {axi_dynclk/s_axi_lite_aclk}
    {rgb2dvi_0/PixelClk} {rgb2dvi_0/SerialClk}
} {
    set p [get_bd_pins -quiet $spec]
    hm_gate "$spec 这个引脚不存在" [expr {[llength $p] > 0}]
    hm_gate "$spec 悬空（没接任何网）" [expr {[llength [get_bd_nets -quiet -of_objects $p]] > 0}]
}
# 新 MI 的时钟/复位必须接上（漏接=寄存器读回恒 0）
foreach mi [list $m_vdma $m_vtc $m_clk $m_hpd] {
    foreach suf {ACLK ARESETN} {
        set p [get_bd_pins -quiet ps7_0_axi_periph/${mi}_${suf}]
        hm_gate "ps7_0_axi_periph/${mi}_${suf} 不存在" [expr {[llength $p] > 0}]
        hm_gate "ps7_0_axi_periph/${mi}_${suf} 悬空" [expr {[llength [get_bd_nets -quiet -of_objects $p]] > 0}]
    }
}
# 视频链必须连成一条（接口网）
# ★★ 2026-09-30：TMDS 那一对**不能按"同一条网"判**（第一版就是这么冤枉的）★★
#   原因在接口定义里写着（vivado-library/if/tmds_v1_0/）：
#     · rgb2dvi 的 TMDS 脚是总线接口 digilentinc.com:interface:tmds:1.0
#     · 顶层口 TMDS_out 是 digilentinc.com:interface:tmds_rtl:1.0
#     · tmds_rtl.xml 里 busType=tmds:1.0 —— 后者是前者的 **RTL 抽象**，同一族
#   ⇒ 直连合法（那行 connect 也确实没报错），但 Vivado 在这中间**可能插一个
#     接口适配器单元**，这一段就变成**两条网**；按"同一条网"判就误报了。
#   改法：内部三段（VDMA→vid_out→rgb2dvi 的 RGB）仍按同一条网严判；
#         TMDS→顶层口 允许"同网"或"经恰好一个中间单元相连"；
#         并且**无论成败都先把接口网拓扑打出来** —— 下次再出这类问题，
#         日志自己就能说明白，不用再猜第四回。
# ★ 这个打印**必须同时列 pin 和 port**：`get_bd_intf_pins -of_objects <net>`
#   **不会**返回顶层接口口（port）。上一版只印 pin，于是 `/rgb2dvi_0_TMDS`
#   看着像孤网 —— 差点让我把一条**已经接对**的线又改坏一次。
#   注意反过来的坑同样存在：`/processing_system7_0_DDR`、`_FIXED_IO` 那两条
#   同理，它们看着也"只有一头"，其实另一头是顶层口。
puts "INFO: HDMI: ---- 接口网拓扑（pin 与顶层 port 分列；只印 pin 会漏顶层口）----"
foreach n [get_bd_intf_nets] {
    puts "INFO: HDMI:   $n"
    puts "INFO: HDMI:       pin  : [get_bd_intf_pins  -quiet -of_objects $n]"
    puts "INFO: HDMI:       port : [get_bd_intf_ports -quiet -of_objects $n]"
}
proc hm_same_intf_net_obj {a b} {
    set na [get_bd_intf_nets -quiet -of_objects $a]
    set nb [get_bd_intf_nets -quiet -of_objects $b]
    return [expr {[llength $na] > 0 && [string compare $na $nb] == 0}]
}

foreach pr {
    {axi_vdma_out/M_AXIS_MM2S v_axi4s_vid_out_0/video_in}
    {vtc_out/vtiming_out v_axi4s_vid_out_0/vtiming_in}
    {v_axi4s_vid_out_0/vid_io_out rgb2dvi_0/RGB}
} {
    set a [lindex $pr 0]; set b [lindex $pr 1]
    set pa [get_bd_intf_pins -quiet $a]
    set pb [get_bd_intf_pins -quiet $b]
    if {[llength $pa] == 0 || [llength $pb] == 0} {
        hm_gate "$a <-> $b 有一头不存在" 0
    } else {
        hm_gate "$a <-> $b 不在同一条接口网上" [hm_same_intf_net_obj $pa $pb]
    }
}

# TMDS -> 顶层口：同网，或经恰好一个中间单元（接口适配器）
# ★ 顶层口一律用 get_bd_intf_ports（用 get_bd_intf_pins 会得到"看着像口但连不上"的东西）
set tmds_a [get_bd_intf_pins -quiet rgb2dvi_0/TMDS]
set tmds_b [get_bd_intf_ports -quiet TMDS_out]
if {[llength $tmds_a] == 0} {
    hm_gate "rgb2dvi_0/TMDS 这个脚不存在" 0
} elseif {[llength $tmds_b] == 0} {
    hm_gate "顶层接口口 TMDS_out 不存在（create_bd_intf_port 没成功？）" 0
} elseif {[hm_same_intf_net_obj $tmds_a $tmds_b]} {
    puts "INFO: HDMI: rgb2dvi_0/TMDS <-> TMDS_out 直连同一条网"
} else {
    set na [get_bd_intf_nets -quiet -of_objects $tmds_a]
    set nb [get_bd_intf_nets -quiet -of_objects $tmds_b]
    set bridge {}
    if {[llength $na] > 0 && [llength $nb] > 0} {
        foreach c [get_bd_cells -quiet] {
            set on_a 0; set on_b 0
            foreach p [get_bd_intf_pins -quiet -of_objects $c] {
                set pn [get_bd_intf_nets -quiet -of_objects $p]
                if {[llength $pn] == 0} { continue }
                if {[string compare $pn $na] == 0} { set on_a 1 }
                if {[string compare $pn $nb] == 0} { set on_b 1 }
            }
            if {$on_a && $on_b} { lappend bridge $c }
        }
    }
    if {[llength $na] == 0 || [llength $nb] == 0} {
        hm_gate "rgb2dvi_0/TMDS <-> TMDS_out 有一头没接网（rgb2dvi 侧网数=[llength $na] 顶层口侧网数=[llength $nb]；孤网就是只连了一头的指纹）" 0
    } elseif {[llength $bridge] == 1} {
        puts "INFO: HDMI: rgb2dvi_0/TMDS <-> TMDS_out 中间隔着 1 个单元 $bridge（接口适配器，正常）"
    } else {
        hm_gate "rgb2dvi_0/TMDS <-> TMDS_out 既不同网、中间单元也不是唯一一个（$bridge）" 0
    }
}

# HPD 普通口必须真接上（漏了的话 HPD 恒 0，某些显示器直接不给画面）
set hpd_io [get_bd_pins -quiet hdmi_out_hpd_video/gpio_io_o]
set hpd_port [get_bd_ports -quiet hdmi_out_hpd]
if {[llength $hpd_io] == 0 || [llength $hpd_port] == 0} {
    hm_gate "HPD 的 gpio_io_o 或顶层口 hdmi_out_hpd 不存在" 0
} else {
    set ni [get_bd_nets -quiet -of_objects $hpd_io]
    set np [get_bd_nets -quiet -of_objects $hpd_port]
    hm_gate "HPD：gpio_io_o 与顶层口 hdmi_out_hpd 不在同一条线上" \
        [expr {[llength $ni] > 0 && [string compare $ni $np] == 0}]
}
# HP1 的地址段必须真分下去（否则 VDMA 读不到 DDR）
set segs [get_bd_addr_segs -quiet -of_objects [get_bd_addr_spaces axi_vdma_out/Data_MM2S]]
hm_gate "输出 VDMA 的地址段没分（HP1 读不到 DDR）" [expr {[llength $segs] > 0}]

if {$hdmi_fail} {
    puts "ERROR: HDMI: ★门禁没过，停止★（上面每条都写了是哪个）"
    error "HDMI 输出通路的 BD 门禁失败"
}
puts "INFO: HDMI: ★门禁全过★ 视频链（3 段严判 + TMDS 段含适配器判法）+ 关键输入脚 + 新 MI 时钟/复位 + HP1 地址段"
puts "INFO: HDMI: ===== HDMI 输出通路搭完 ====="
