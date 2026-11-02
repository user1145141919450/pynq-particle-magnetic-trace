# ============================================================================
# ov5640_pynq.xdc — PYNQ-Z2 PMOD 引脚约束（OV5640 DVP 模块）
#
# 引脚取自板卡文件（权威来源）：
#   F:/Xilinx2022/Vivado/2023.2/data/boards/board_files/pynq-z2/A.0/part0_pins.xml
# 接线分配：
#   JA = 数据 D[7:0]（JA1..JA4, JA7..JA10）
#   JB = 控制：JB1=pclk JB2=href JB3=vsync JB4=xclk
#        JB7=rst_n JB8=pwdn JB9=SCCB scl JB10=SCCB sda
# 传感器模块供电：PMOD 3.3V（OV5640 模块板载 LDO，18pin DVP 模块已验证可跑）
# ============================================================================

# ---- 数据总线（JA）----
set_property PACKAGE_PIN Y18 [get_ports {ov5640_data[0]}]
set_property PACKAGE_PIN Y19 [get_ports {ov5640_data[1]}]
set_property PACKAGE_PIN Y16 [get_ports {ov5640_data[2]}]
set_property PACKAGE_PIN Y17 [get_ports {ov5640_data[3]}]
set_property PACKAGE_PIN U18 [get_ports {ov5640_data[4]}]
set_property PACKAGE_PIN U19 [get_ports {ov5640_data[5]}]
set_property PACKAGE_PIN W18 [get_ports {ov5640_data[6]}]
set_property PACKAGE_PIN W19 [get_ports {ov5640_data[7]}]

# ---- 控制与时序（JB）----
set_property PACKAGE_PIN W14 [get_ports ov5640_pclk]
set_property PACKAGE_PIN Y14 [get_ports ov5640_href]
set_property PACKAGE_PIN T11 [get_ports ov5640_vsync]
set_property PACKAGE_PIN T10 [get_ports ov5640_xclk]
set_property PACKAGE_PIN V16 [get_ports ov5640_rst_n]
set_property PACKAGE_PIN W16 [get_ports ov5640_pwdn]
set_property PACKAGE_PIN V12 [get_ports ov5640_iic_scl]
set_property PACKAGE_PIN W13 [get_ports ov5640_iic_sda]

# ---- 调试 LED：MMCM 锁定指示（LD0，高有效）----
set_property PACKAGE_PIN R14 [get_ports led_locked]

# ---- 电平标准：全 LVCMOS33（PMOD 电平）----
set_property IOSTANDARD LVCMOS33 [get_ports {ov5640_data[*]}]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_pclk]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_href]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_vsync]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_xclk]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_rst_n]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_pwdn]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_iic_scl]
set_property IOSTANDARD LVCMOS33 [get_ports ov5640_iic_sda]
set_property IOSTANDARD LVCMOS33 [get_ports led_locked]

# ---- SCCB 双向线：FPGA 侧上拉（模块板通常也有，并联无害）----
set_property PULLUP true [get_ports ov5640_iic_sda]

# ---- 关键：传感器 pclk 从普通 IO 进 PL，无专用时钟走线，必须关掉检查 ----
# （旧工程 EBAZ4205 同样处理；pclk 84MHz，IBUF 后进异步 FIFO 逻辑）
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets ov5640_pclk_IBUF]

# pclk 约束（供时序分析；OV5640 最终配置 1024x720@84MHz DVP）
create_clock -period 11.905 -name ov5640_pclk [get_ports ov5640_pclk]

# ---- 跨时钟域：三个真正独立的时钟域，两组间的跨越全部走 2-FF 同步链 ----
#   ① ov5640_pclk  = 传感器像素钟（外部输入，84MHz）   ② clk_out2 = 传感器控制域（24MHz）
#   ③ clk_fpga_0   = PS AXI 域（100MHz）
# 2026-09-13：clk_out2 频率从（被误配的）100MHz 改回 24MHz 后，它与 clk_fpga_0 变成
# 100:24.02 的非整数比 —— 工具会在很长的公共周期里挑到两条沿只差 0.204ns 的"最紧对"，
# 于是把 dvp_ctrl/dvp_probe 的 2-FF 同步器首级判成 6 条违例（WNS=-3.289）。
# 同步器首级本来就会亚稳态、不该做时序分析 → 必须声明为异步时钟组。
# 已逐条核对：两组间除 2-FF 同步器外，只有 MMCM locked 作异步复位用，无真实单拍路径。
set_clock_groups -asynchronous \
    -group [get_clocks ov5640_pclk] \
    -group [get_clocks clk_fpga_0] \
    -group [get_clocks clk_out2_ov5640_m1a_clk_wiz_0_0]

# ============================================================================
# HDMI 输出（P2，2026-09-30 加）
# ============================================================================
# 物理脚：clk L16/L17；data_p 0/1/2 = K17/K19/J18；data_n 0/1/2 = K18/J19/H18；hpd R19
#
# ★★★ 电平标准 = **TMDS_33**（8 个 TMDS 脚），**只有 HPD 是 LVCMOS33**。
#   2026-09-30 上午踩的坑：我照抄 part0_pins.xml 的 `iostandard="LVCMOS33"` 全写了 LVCMOS33，
#   结果 place 直接报 8 条 `[Place 30-64] Illegal IOStandard ... is differential,
#   but has single-ended IOStandard value LVCMOS33`，place 失败（好在只花 2.5 分钟）。
#   **谁对谁错，证据链如下（别再照抄那个字段）**：
#     · Digilent rgb2dvi 的 `src/OutputSERDES.vhd:82-85`：`OBUFDS generic map
#       (IOSTANDARD => "TMDS_33")` —— **写死在 IP 里，没有 single-ended 的 generic**。
#       4 个通道（1 时钟 + 3 数据）全走这个 OBUFDS ⇒ **IP 不可能输出单端**。
#     · `board.xml` 里 `hdmi_out` 接口的类型就是 `digilentinc.com:interface:tmds_rtl:1.0`
#       （CLK_P/CLK_N/DATA_P/DATA_N），**diff 由板文件自己定义**；
#       而且同一个 board.xml 把 `rgb2dvi` 写成这个接口的 `preferred_ip`
#       —— Digilent 自己不会给自家板子配一个电平不兼容的 IP。
#     · `board.xml` 里**完全没有 iostandard 字段**（grep 零命中）；
#       `preset.xml` 也没有。**唯一说 LVCMOS33 的就是 part0_pins.xml 这一个字段**，
#       那是板感知流程用的"默认提示值"，**管不住 IP 内部写死的 OBUFDS**。
#     · 对比：part0_pins.xml 里 TMDS_**IN** 那组（N18/P19/V20/T20/N20/W20/U20/P20）写的是 TMDS_33。
#   ⇒ 冲突时以 **IP 内部实际的 buffer** 为准（placer 也是按它判的），不是 pins 文件的提示字段。
#
# ★★★ 血泪教训（2026-09-30，第一次 HDMI 综合）：这里**绝不能写 proc / if / catch**。
#   XDC 是个**白名单**解释器 —— 非约束命令它只发一条
#     CRITICAL WARNING: [Designutils 20-1307] Command 'proc' is not supported ...
#   然后**把这条命令丢掉**：不是 ERROR、不中断、综合/实现照跑到底。
#   我原本在这里写了个 `hm_pin` proc（想"口找不到就 error，绝不静默"），
#   结果 9 条 `hm_pin` 调用**一条都没执行** → 8 个 TMDS 脚被工具随手丢到
#   阿duino/RPi/音频脚上（实测 T5/U5/V6/V10/V11/W6/Y12/Y13）、HPD 丢到没人用的
#   J15，日志里只有 CRITICAL WARNING —— 上板效果是**全黑，且无声无息**。
#   → 规矩：XDC 里**只放约束命令**（set_property / set_clock_groups / create_clock …）；
#     proc / if / 门禁一律放 build_project.tcl（那里是真 Tcl 解释器）。
#     "9 个脚到底约束上没有"由 build_project.tcl 综合后的 **HDMI 引脚门禁** 负责验，
#     缺一个就 error 退出（门禁写在那里才算数）。
#
# ★ 另一个坑：`get_ports X` 在 X **不存在**时只发 WARNING(Vivado 12-584)就什么都不做。
#   这是"静默失效"的另一半 —— 现在由上面那道门禁兜住（缺口 = error）。
#   HPD 的真名是 `hdmi_out_hpd[0]`（BD 的 1 位口在 wrapper 里是 [0:0]，**带下标**），
#   这个不是猜的，是从跑完的实现里 probe 出来的。
# ★★ 带中括号的口名必须写在 { } 里：`get_ports TMDS_out_data_p[0]` 里的
#    `[0]` 会被 Tcl 当成**命令替换**去执行（报一个莫名其妙的错）。
#    这些名字是从 **跑完的实现里** 读出来的（probe 已核对，见 2026-09-30 记录）：
#    注意 HPD 是 `hdmi_out_hpd[0]`（BD 的 1 位口在 wrapper 里是 [0:0]，带下标！）
set_property -dict {PACKAGE_PIN L16 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_clk_p}]
set_property -dict {PACKAGE_PIN L17 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_clk_n}]
set_property -dict {PACKAGE_PIN K17 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_data_p[0]}]
set_property -dict {PACKAGE_PIN K19 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_data_p[1]}]
set_property -dict {PACKAGE_PIN J18 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_data_p[2]}]
set_property -dict {PACKAGE_PIN K18 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_data_n[0]}]
set_property -dict {PACKAGE_PIN J19 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_data_n[1]}]
set_property -dict {PACKAGE_PIN H18 IOSTANDARD TMDS_33}  [get_ports {TMDS_out_data_n[2]}]
# HPD 是普通的 1 位状态线（热插拔检测），不是差分 —— 这个**确实是** LVCMOS33。
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports {hdmi_out_hpd[0]}]

# ---- 像素时钟域：**实测不需要**单独声明异步（2026-09-30 probe 结论）----
# 这里原来有一整段抓像素钟名字再 set_clock_groups 的代码，外面包着 catch/if ——
# 而 catch 和 if 同样被 XDC 白名单丢掉（见上面那段教训），所以它**从来没生效过**。
# 但"没生效"这次是**无害**的，不是漏网：拿跑完的 placed checkpoint 实测（probe_hdmi2）：
#   · 设计里所有时钟：clk_fpga_0(10ns) / ov5640_pclk(11.905) / clk_out2_...(41.633)
#     / axi_dynclk_PXL_CLK_O(10.000) / mmcm_fbclk_out(10) / I(2.0, 5x 串行钟) …
#   · 最差 setup 路径：clk_fpga_0 -> clk_fpga_0，-0.092ns（只差一点点，不是 CDC 问题）
#   · 最差 hold  路径：axi_dynclk_PXL_CLK_O -> 它自己，-0.332ns（**域内**，不是跨域）
#   · 跨域（clk_fpga_0 <-> 像素钟）**一条违例都没有** → v_axi4s_vid_out 内部那个
#     异步 FIFO 的 CDC 已经被工具当同步路径分析了却没爆，说明它本来就是按同步用的。
# ★ 还有个大前提：dynclk 的 MMCM 上电是**默认分频**，probe 读到像素钟 = 10.000ns(100MHz)、
#   5x 串行钟 = 2.000ns(500MHz)；而运行时我们把它改成 371.25/74.25MHz。
#   10ns < 13.47ns、2.0ns < 2.69ns —— **工具是在比运行时更紧的约束下分析的**，
#   偏保守，方向是对的。所以这里**不加** create_clock 覆盖，保持工具自动派生即可。
# 真名留档：像素钟 `axi_dynclk_PXL_CLK_O`，串行钟在报告里显示为 `I`。
