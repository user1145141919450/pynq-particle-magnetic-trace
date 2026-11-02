# ov5640_pynq — PYNQ-Z2 OV5640 采集工程（M1a）

磁场演示装置 FPGA 部分的第一步：把 OV5640 DVP 摄像头接到 PYNQ-Z2，PL 采集进 DDR，
PS 端 Python 直接读帧。移植自 EBAZ4205 旧工程（Lesson10_A_OV5640_VDMA，Vivado 2019.2，
已在旧板跑通显示），全部 RTL 未改动。

## 目录

```
ov5640_pynq/
├── build_project.tcl     # 一键构建（bd_only 参数可只生成 BD）
├── bd/m1a_capture.tcl    # BD 配方（全部连线注释齐全）
├── constrs/ov5640_pynq.xdc  # PMOD 引脚约束
├── rtl/ov5640/           # 旧工程 RTL 原样拷贝（top/data/cfg/iic/power_ctrl）
├── rtl/cmos_data.v       # 旧工程备件，M1a 未用
├── rtl/vga_data.v        # M1b 显示链用
└── proj/                 # 构建产物（脚本生成）
```

## 构建

```bash
# 只生成 BD 验证脚本（约 2~5 分钟）
F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source build_project.tcl -tclargs bd_only
# 全流程（约 15~30 分钟）
F:/Xilinx2022/Vivado/2023.2/bin/vivado.bat -mode batch -source build_project.tcl
```

产物（构建脚本自动打包到 `deploy/`，bit + hwh 同名 + vdma_capture.py 驱动，整目录拷上板即可）：
- `proj/ov5640_m1a.runs/impl_1/ov5640_m1a_wrapper.bit`（4.0MB，2026-09-11 构建，时序全收敛）
- `proj/ov5640_m1a.gen/.../hw_handoff/ov5640_m1a.hwh`（→ deploy/ov5640_m1a_wrapper.hwh，PYNQ Overlay 加载要求与 bit 同名同目录）

## 与旧工程的关键差异（移植决策记录）

| 项目 | 旧工程 (EBAZ4205) | 本工程 (PYNQ-Z2) | 原因 |
|---|---|---|---|
| 器件 | xc7z010clg400-1 | xc7z020clg400-1 | 板载器件 |
| PS7 | 16位 DDR 手动配参 | 板卡 preset（32位/512MB） | 板卡文件已装，preset 齐全 |
| clk_wiz 输入 | 外部 50M 晶振 | **PS FCLK0 = 100MHz** | PYNQ-Z2 PL 无独立晶振；PYNQ 运行时 overlay 只能拿到启动已使能的 FCLK0（100M），FCLK1+ 无效 |
| 显示链 | MM2S + v_tc + VGA 输出 | **去掉（M1b 再加 HDMI）** | M1a 只采集 |
| VDMA fstores | 1 | 4 | 环形 4 帧缓冲，PS 读最新完整帧无撕裂 |
| xlconstant | 依赖默认值（值不明） | 显式 CONST_VAL=1 / tkeep=1 | 旧 hw_handoff 脚本未记录这些值，明确化 |
| 像素打包 | v_vid_in_axi4s 4.0 format 12=RGB565 打包成 16bit | **5.0 无 RGB565 格式，12=Mono/Sensor 8bit 直通** | 字节流原样进 DDR，RGB565 字节序解释放 PS 端（架构更干净） |

**沿用的旧工程验证结论**（不再改动）：
- DVP 原始 8bit 直连 `v_vid_in_axi4s`（不走 ov5640_top 的 m_data 打包路径）；
- ov5640_top 只做供电时序 + SCCB 配置，clk_sys50m/clk_sys24m 都接 24.02MHz
  （power_ctrl 计数在 24M 下延时更长，符合传感器手册下限）；
- SCCB 配置自动执行：ov5640_cfg 内部状态机自启动 304 条寄存器，
  estart 悬空即可；最终输出 **1024×720，pclk 84MHz**（与旧 v_tc HACTIVE=1024 吻合）；
- href 兼作 vid_active_video、!href 兼作 vid_hsync（DVP 无 hsync 的惯用处理）；
- MMCM 锁定直接作为传感器域复位释放条件（!locked → vid_io_in_reset）。

**上板踩坑记录**（2026-09-11 首次自检）：
- 串口打印乱码 = SSCOM 未切 UTF-8（GBK 解码 UTF-8 输出），不影响功能；
- pynq Overlay 解析本工程 hwh 报 `s_axis_s2mm_tkeep cannot be found`——pynq 的
  hwh 解析器对 axi_vdma 6.3 的 S_AXIS_S2MM 端口结构不兼容，**无害**：
  run_selfcheck.py 自动退化为 Bitstream 直下（驱动走裸 MMIO，不依赖 hwh）；
- 驱动 cv2 坑：`COLOR_BGR5652BGR` 要求输入 **CV_8UC2 双通道**（低/高字节分通道），
  不是 uint16 单通道——to_bgr() 用 `raw.reshape(h, w, 2)` + `[..., ::-1]` 字节交换；
- 实测：bit 加载 20s 后首帧 0.4s 内即到（SCCB 自动配置+DVP+VDMA 全链路正常）。
- 二轮自检（0923 起）：首帧全零（均值/标准差=0），此后 FrameCount 停在 1、
  DMACR 读回 0x00010002（RS 被清 + Reset 置位，非我们写入的 0x0005）
  → 判断传感器只吐 1 帧默认数据后停流，DMA 侧无任何错误位。
  嫌疑排序：SDA/SCL 交叉（SCCB 没配置进传感器）> STB 悬空 > 供电不足；
  驱动两处超时已放宽到 60s，区分"停流"与"极慢流"。
- SDA/SCL 对调后现象完全复现 → 排除"两根线交叉"。
- 探针版工程（dvp_probe，2026-09-12）：采集链不动，另加 rtl/debug/dvp_probe.v
  （pclk/64、vsync、href、scl、sda 上升沿计数 + 数据线见过 1/0 锁存 + MMCM locked）
  经 3× axi_gpio 双通道输入挂 PS，固定地址 0x43A0/43A1/43A2_0000，
  PS 端 dvp_probe.py 直读判读（无需 JTAG）。
- **CDC 时序坑**：探针的 pclk 域→axi 域 2-FF 同步链被工具按最坏相位对齐分析，
  报 17 条 -4.7ns 违例（第一版构建）→ XDC 加
  `set_clock_groups -asynchronous -group [get_clocks ov5640_pclk] -group [get_clocks clk_fpga_0]`
  后全收敛（WNS=2.813）。教训：**任何跨时钟域同步链都要配异步时钟组约束**，
  同步器本身不豁免时序分析。第二版构建 + 部署打包路径 bug（../software→../../software）已修。
- **探针 v1 上板判读（2026-09-13）**：`scl=11249` ≈ 304 寄存器 × 27 SCL —— **SCCB 配置表
  已完整写入，传感器 I2C 全程应答**；时间线也对得上（46.9kHz IIC → 304 reg ≈ 160ms，
  与"上电后 0.24s 出首帧"吻合）。但 `pclk/64` 20 秒增量**全程为 0**（不是"跑后停"，
  是从未启动），vsync/href 各只在上电闪过 1 次，数据线见过1/见过0 全 0。
  万用表实测模块 3V3-GND = 3.29V → **供电排除**。
  ⇒ 剩两个嫌疑：**XCLK 没真正到传感器**，或 **PWDN/RST 实际电平不对**。
- **探针 v2（2026-09-13）**：新增 `rtl/debug/dvp_ctrl.v`，把 XCLK 门控和 PWDN 的
  控制权交给 PS 软件（ctrl 字 bit0=xclk_en、bit1=force_pwdn，经 axi_gpio_probe3 通道1
  写入，地址 0x43A30000）；dvp_probe 增加 `xclk` 输入与 w6=翻转载数（读 0x43A30008）。
  判决性实验：**关/开 XCLK，看 PCLK 跟不跟着变** —— 跟 = XCLK 确实到了传感器
  （问题在配置）；不跟 = XCLK 没到 / 传感器没用它（查线、PWDN、RST）。
  上板脚本 `probe_watch.py [xclk]`。ctrl 默认值（读回 0 时 xclk 开、pwdn 随 pl）
  与 v1 行为完全一致，不影响原采集链。w5 同时带出 xclk/pwdn/rst_n 三根线的
  **板上实际电平**（bit17/18/19），可直接核对 power_ctrl 稳态是否真是 pwdn=0/rst_n=1。
- ★★★ **根因（2026-09-13 探针 v2 上板后定位）：clk_wiz 的显式 MMCM 参数被静默忽略，
  传感器域实际跑在 100MHz**。BD 脚本里写的是 `MMCM_CLKFBOUT_MULT_F=49 /
  MMCM_CLKOUT0_DIVIDE_F=11 / MMCM_CLKOUT1_DIVIDE=34 / MMCM_DIVCLK_DIVIDE=6`
  （意图：clk_out1=74.24M、clk_out2=24.02M），但生成物里是
  `DIVCLK_DIVIDE=1 / CLKFBOUT_MULT_F=10.000 / CLKOUT0_DIVIDE_F=10.000 /
  CLKOUT1_DIVIDE=10` —— **全部退回 IP 默认值，两个输出都成了 100MHz**。
  后果链：`clk_sys24m`(=clk_out2)=100MHz → `assign ov5640_xclk = clk_sys24m`
  → **XCLK 输出 100MHz，远超 OV5640 规格 6~27MHz → 传感器内部 PLL 锁不上
  → 从来没有过 PCLK**（探针实测 pclk/64 增量恒 0）；而 SCCB 只是被拖慢到
  100MHz/512 ≈ 195kHz（仍在 I2C 400k 容限内）→ **配置照写不误、传感器照常 ACK**
  → 表现为"配置完全成功、却一帧全零后停流"，从 v1 起就一直误判方向。
  另注：power_ctrl 的 6/2/21ms 延时在 100MHz 下缩成 3/1/10.5ms，也偏离设计意图。
  **修法：BD 里配 clk_wiz 只能给 `CLKOUT*_REQUESTED_OUT_FREQ`（让工具求解），
  绝不要给 `MMCM_*` 显式参数**；且**必须验证生成物**
  （`ip/*_clk_wiz_0_0/*_clk_wiz_0_0.v` 里的 `// clk_out1__74.25000__` 注释行）。
  `build_project.tcl` 第 5b 步已加硬性自检：clk_out2 必须≈24MHz、clk_out1 必须
  ≈74.25MHz，否则直接 fail —— 这类错误在构建日志里**毫无痕迹**，只能靠查生成物。
- **探针本身的一个观测盲区（记录备查）**：w6 用 100MHz 采样统计 xclk 翻转，
  若 xclk 恰好等于采样时钟（如本次的 100MHz 同名同源时钟），相位锁死 →
  相邻两拍永远相同 → `cnt_xclk` 恒 0，**看起来像"没输出"**。
  本次正是靠这一点反推出 XCLK 不是 24MHz；但也说明该计数器只适合测远低于
  采样率的时钟，不能单独用来判"有/无"。
- **极性坑（v2 首版踩过，已修）**：ctrl 位最初定义成 `bit0 = xclk_en（1=开）`，
  但 **axi_gpio 输出寄存器上电/复位值是 0** → 烧完 bit 直接就把 XCLK 掐死了，
  与 v1 行为相反、还会让判决实验自证失败。规则：**凡 PS 可控的通路，默认态（0）
  必须等于"正常工作"**。改为 `bit0 = xclk_off（1=停钟）`，0 即常开。
- **修钟后二轮上板（2026-09-13 15:17，新 bit）**：XCLK 实测 **24.09MHz**（每 0.2s
  约 9.64e6 次翻转 ÷2）、SCCB 配置耗时从 ~0.1s 变成 **~0.6s**（IIC 从 195kHz 回到
  应有的 ~19kHz 量级）—— **时钟根因确认修复**。但 `pclk/64` 仍恒 0、`vsync=1 href=1`、
  数据线见过1/见过0 全 0。
- **★ 从上面这组数据能反推出的硬结论**：`见过1`/`见过0` 两个锁存器是 **pclk 触发**的
  （`always @(posedge pclk) seen1_p <= seen1_p | data`）——只要 pclk 有过**一个**上升沿，
  哪怕数据线全 0，`见过0` 也会立刻变 `0xFF`。两者恒 0 ⇒ **PCLK 一次都没到过 FPGA 引脚**。
  故不是"采集链丢数据"，而是 **PCLK 从未产生/从未到达**，问题在传感器侧而非 FPGA 侧。
- **传感器侧证据的空白（重要）**：`ov5640_cfg` 的 FSM 是**盲写**、不检查 ACK，
  `scl=11249` 只证明 FPGA 自己翻了 11249 次 SCL，**完全不证明传感器收到了任何东西**。
  加上此前 XCLK 一直是坏的，这套接线**从未被真正验证过**。所以"配置成功"这条
  假设必须推翻重来，"传感器是否活着、是否在应答"是当前最大的未知。
- **判决实验①：模块 STB 脚（已做，排除）**：STB 接 GND、接 3V3 **两种接法 `pclk/64`
  增量均仍为 0** → STB 与故障无关，排除。
- **判决实验②：万用表读模块端**：`pin_toggle.py`（软开关周期翻转 XCLK/PWDN）跑完后，
  模块端 **XCLK 恒 3.3V、PWDN 恒 0V，随开关毫无变化**。两种解释、修法相反：
  (A) ctrl 写根本没生效（dvp_ctrl/BD 连线 bug）→ 引脚本来就没动；
  (B) ctrl 生效、引脚确实在动 → 线没通 / 接错脚。
  注：24MHz 50% 方波用直流档量应是 ≈1.65V 平均值，**读到整 3.3V 本身就提示该脚没被驱动**。
- ★ **`ctrl_selftest.py`（新写，纯软件闭环、不用万用表）—— 用探针自己当示波器**：
  T1 ctrl=0 测 w6≈24MHz / T2 ctrl=1 停钟看 w6 是否掉 0 / T3 停钟后 xclk 电平位应恒 0 /
  T4 重开 w6 应回来 / T5 ctrl=2 强制 PWDN 看 w5 bit18 是否读回 1 / T6 放开应读回 0。
  **上板结果 6/6 全过**（T1=24.02MHz、T2 从 974.7 万掉到 0、T5 强制 PWDN 读回 1、T6 读回 0）
  ⇒ **PS→ctrl→引脚 全链路证明有效，dvp_ctrl 与 BD 连线无 bug，FPGA 侧彻底洗清**。
  推理链闭合：FPGA 引脚在按命令动 + 模块端恒 3.3V/0V 不动
  ⇒ **故障 100% 在 FPGA 引脚与模块之间（线 / 插孔 / 接错脚）**，与传感器、SCCB、配置表均无关。
- ★★★ **真正的根因：控制线插错了 PMOD 孔位（2026-09-13 16:48）**。
  从 09-11 首帧全零起，历经"1 帧全零停流 → 探针 v1/v2 → clk_wiz 100MHz 根因 → STB 实验
  → ctrl 自检"整整三天，**最后一条才是真凶**：PYNQ-Z2 排针无可读丝印 + Pmod 2×6 编号
  反直觉（Digilent 自标 non-standard）→ **8 根控制线整体错位** → 模块的
  XVCLK/PWDN/RST 实际全部**悬空** → 从无 PCLK。此前"模块端 XCLK 恒 3.3V、PWDN 恒 0V
  且不随开关变化"正是**悬空**的典型特征（被模块自身上拉/下拉钳住），当时判断正确。
  按正确排布重接后，`probe_watch.py` 一次跑出全部正常读数：
  **`pclk/64 ≈ 260300 / 0.2s`（= 83.3MHz，配置目标 84MHz）**、
  **`vsync = 6 / 0.2s`（= 30fps）**、**`href = 4337 / 0.2s`（≈723 行/帧，对应 720 行）**、
  **数据线「见过1=0xFF 见过0=0xFF」**（8 位全见过高与低 = 真实图像数据）、
  `scl=11249`（SCCB 304 条完整写入）、`locked=1 xclk=1 pwdn=0 rst_n=1`。
  **⇒ 传感器完全正常、配置表正确、RTL 正确、采集链全通。**
- **复盘教训**：① **先验证物理连接再看芯片** —— 大量时间花在时钟、配置表、STB、传感器
  本体上，而最容易错、最该先排除的是接线；② **"FPGA 侧自检通过 + 模块侧无反应"这个组合
  一旦出现，就是接线问题**，应立即上探针定位，而不是继续怀疑 RTL；
  ③ **没有丝印的排针必须用电信号定位**（`find_pins.py` 的做法），不能靠推测编号约定。

**上板踩坑记录（第二轮：VDMA 寄存器，2026-09-13 接线修好后暴露）**：

- ★★★ **坑 A：`S2MM_DMACR` 的循环位是 bit1，不是 bit4**。
  症状：`run_selfcheck.py` 报"首帧 ok 但均值/标准差 0.0"，随后
  `60s 内无新帧（FrameCount 停在 1）`、`halted=True err=False DMACR=0x00010002`。
  **"传完 1 帧就停 + 无错误位 + RS 被清"就是非循环模式的教科书症状**
  （循环未开 → 传完一帧自动清 RS、置 Halted）。
  权威来源＝IP 自带源码 `F:/Xilinx2022/Vivado/2023.2/data/ip/xilinx/axi_vdma_v6_3/hdl/
  axi_vdma_v6_3_rfs.vhd` 的 `DMACR_*_BIT` 常量（10675 行起）：
  `RS=0 / CRCLPRK=1 / RESET=2 / SYNCEN=3 / FRMCNTEN=4 / IRQTHRESH=16..23`。
  **踩坑根因＝把 AXI VDMA 记成了 AXI DMA**：`bit4=Cyclic` 是 AXI **DMA** 的约定
  （DMA：bit0 RS / bit2 Reset / bit4 Cyclic），AXI **VDMA** 完全不是这套。
  旧代码 `write(S2MM_DMACR, 0x0005)` 注释写着"bit2 Circular"——实际是 **RS + RESET**，
  等于"启动的同时又复位"，且整字写入把 bit1 循环位一起覆盖成 0。
  **正确值 `0x0003` = RS | CRCLPRK**，绝不能带 bit2。
  旁证：BSP 驱动头 `Vitis/.../drivers/axivdma_v6_11/src/xaxivdma_hw.h` 的
  `XAXIVDMA_CR_RESET_MASK=0x4`、`XAXIVDMA_CR_TAIL_EN_MASK=0x2`
  （"Tail ptr enable **or Park**"，S2MM 侧就是循环位）。
- ★ **坑 B：`_last_count = None` 导致首帧假阳性**。`frame()` 判据是
  `count != _last_count`，初值 None 时**第一次调用必然成立** → 立刻返回一块从没被 DMA
  写过的 `allocate()` 全零内存。所谓"首帧 ok"是假的，**全零帧根本不是数据通路问题**。
  修法：`start()` 里先读当前计数当基准。
- ★ **坑 C：错误位掩码 `0x0E` 是错的**。`XAXIVDMA_SR_ERR_ALL_MASK = 0x00000FF0`（bit4~11）；
  低位里 **bit1 是 IDLE，是正常状态**。用 `0x0E` 会在通道进入正常空闲时误报"VDMA 报错"。
  DMASR 位：`bit0 Halted / bit1 Idle / bit4..11 错误 / bit16..23 帧计数`。
- ★★★ **坑 D：`DMASR[23:16]` 不是"累加了多少帧"，而是倒计数器**。
  即 rfs.vhd 里的 `ch*_thresh_count`（源码 8485-8515）：复位=1；写阈值后=阈值；
  每完成一帧减 1；**减到 1 就重装成阈值并触发中断**。上电 `IRQThreshold=1`
  → 每帧都是"减到 1 → 重装成 1" → **读数永远停在 1**，靠"计数变化判新帧"的 `frame()`
  永远等不到第二帧。修法：`DMACR = RS | CRCLPRK | (0xFF<<16)` = **`0x00FF0003`**。
- ★ **坑 E：`fsize_more_or_sof_late`(bit11) 的精确触发条件**（源码 72426
  `SOF_LATE_ERR_PULSE_PROCESS`）：`(chnl_ready='0') and (tvalid='1') and (tuser='0')`
  ——"通道还没准备好，源就在推非 SOF 的数据"。传感器自由运行、PS 在 sleep 后才启动
  VDMA → 通道在半帧中间武装 → **启动瞬间必中一次**。它是瞬态，但 **DMASR 错误位是
  粘连的 W1C（写 1 清）** → 驱动必须"记录 + 清除 + 继续"，**绝不能一见错误位就抛异常**
  （本工程踩过：一次性瞬态把整次采集判了死刑）。
  ★ 注意：坑 E 的"瞬态"**只在通道真能进数据时才自愈**；帧同步没配好时它会每拍复现、
  永不消失 —— 表现为错误事件以固定间隔（被驱动限流 0.5s）刷屏。
- ✗ **被数据否定的假设：改 `DMACR[6:5]` FSYNCSEL 档位能解死锁**。
  依据 rfs.vhd:53016 的 S2MM `FSYNC_CROSSBAR` 原文：
  `"00" => fsync`（primary，复位默认）/ `"01" => othrchnl_fsync` / `"10" => tuser_fsync`
  （源码注释原文 **"used only by s2mm"**）/ others => '0'；
  `fsync_src_select` 接 `s2mm_dmacr(6 downto 5)`（73702 行），`s2mm_dmacr` 复位值
  `(others => '0')`（73764 行）。本设计没有 MM2S ⇒ `"00"`/`"01"` 结构性死亡，
  故怀疑 `"10"` 是缺失的那一档。**`fsync_sweep.py` 上板把四档全扫一遍：全部不出帧，
  且四档的错误事件次数完全相同（均 75 次 / 2.5s）** ⇒ **fsync 选档不是变量**，
  真正的问题在更上游：**源侧到底有没有给出 tuser**。
  ★ 教训：**当两个"截然不同"的配置给出逐位相同的症状时，变量就不是你以为的那个**。
- ★ **流侧探针 `rtl/debug/axis_probe.v`（2026-09-13 本轮）**：插在
  `v_vid_in_axi4s → VDMA` 的 AXI-Stream 正中间，直通的同时统计
  `tvalid` 拍数 / **`tuser` 上升沿数** / **`tlast` 上升沿数** + "见过没 / 当前电平"
  状态字，经 `axi_gpio_probe4/5`（0x43A40000 / 0x43A50000）给 PS 读 ——
  这是**第一次能直接量到"源到底给没给帧首标志"**，之前全靠 VDMA 错误位反推。
  同时**用 DVP vsync 同步过时钟域后合成一个 `tuser`，与源 `tuser` 相或**：
  源自己给 SOF 时那一拍两信号同时为 1、相或仍是单脉冲（**不会产生重复 SOF**）；
  源不给则由 vsync 补上。两条路自动收敛 ⇒ **不需要 PS 开关**，也就没有
  "默认态必须等于正常工作"的极性陷阱（见上文的 ctrl 坑）。
  上板脚本 `stream_probe.py`：先量三个增量并判读，再自动跑一遍完整采集自检出 PNG。

---

## ★★ 第七轮（2026-09-13）：源侧为什么彻底死透 —— 复位比时钟早撤 0.6 秒

### 上板量到的事实

流侧探针（第六轮加的 `axis_probe`）首次直接量到源侧输出，2 秒内：

```
tvalid +0    tuser +0    tlast +0
w3 = 0x0b6d02ad
  live: tvalid=0  tlast=1(卡在高)  tuser=0  tready=1  vsync 正常
  latched: 见过tvalid=1  见过tuser=0  见过tlast=1  见过tready=1
  vsync 脉冲数 = 2925 / 约98s = 29.8Hz
```

**判读**：传感器完全健康（30fps 稳定、href/pclk 都在跳），但 `v_vid_in_axi4s`
的输出侧**一拍都不推**。比"没有 SOF"更强 —— 是**源整体死了**。
`tlast` 曾出现过一次然后卡在高，是"死之前推过的最后一个字"。

### 回 IP 源码找到的机制

`v_vid_in_axi4s_v5_0_vl_rfs.v`：

```verilog
wire vid_reset = (C_HAS_ASYNC_CLK) ? vid_io_in_reset : ~aresetn;  // 1975
.VID_RESET (vid_reset | fifo_overflow_from_coupler)              // 2181
// 且复位只能由 vid_io_in_reset 再次脉冲才能解开（2129-2150 行）
```

即 **内部异步 FIFO 一溢出，就反过来把整个视频域按住复位**，而我们还把
`overflow` / `underflow` 两个输出**悬空没接** —— 最关键的证据留在盲区里。

### 真正的缺陷：复位与时钟的上电次序反了

原接法：`vid_io_in_reset = ~clk_wiz_0/locked`。

| 事件 | 时刻 |
|---|---|
| MMCM 锁定（reset 撤除） | 约 **100 µs** |
| OV5640 PCLK 出现（I2C 配完内部 PLL） | 约 **600 ms** |

**复位比它要复位的那一侧时钟早撤 0.6 秒。** 异步 FIFO 的正确上电次序是
"复位保持到**两个**时钟都稳定运行"；我们在写侧时钟还不存在时就撤了复位，
FIFO 指针/标志在无时钟状态下初始化非法 → 写侧一动就判满 → 溢出 → 按上面的链
**永久锁死**。

★ **这一条同时解释了全部四个观测**（tvalid 恒 0 / tuser 恒 0 / tlast 曾出现且卡高 /
vsync 计数完全正常——因为 vsync 走的是独立探针，不经过该 IP）。

### 修法与验证手段

- **新增 `rtl/debug/vid_rst_ctrl.v`**：撤复位条件从"MMCM 已锁"改成
  **"MMCM 已锁 且 PCLK 已稳定跑满 2²⁰ 拍（24MHz ≈ 43.7ms）"** —— 撤复位那一刻
  两侧时钟都在运行。另引出 **`ctrl[3]` 软件强制复位**位（默认 0 = 不复位，
  遵守本工程极性约定），便于不重烧 bit 再复位一次。
- **探针补三个盲区**（`axis_probe` v3，`w3` 的闲置高位正好够用）：
  `bit10` live overflow、`bit11` live underflow、`bit12` live vid_io_in_reset、
  `bit13/14` 见过 overflow/underflow。
- `stream_probe.py` 加了**判决性实验**：先量基线，若源仍死，则用 `ctrl[3]`
  强制复位一次再量 —— 假说若成立，复位后 `tvalid` 应开始涨。

### 本轮踩的构建坑

- **BD 41-238 POLARITY 不匹配**：`create_bd_cell -type module -reference` 引入的
  自定义模块，**BD 会按端口名自动推断极性** —— 看到 `vid_rst` 猜成 `ACTIVE_LOW`，
  与 IP 的 `vid_io_in_reset(ACTIVE_HIGH)` 冲突，`validate_bd_design` 直接报错停流程。
  **修法**：建完 cell 立刻钉死 `set_property CONFIG.POLARITY {ACTIVE_HIGH} [get_bd_pins ...]`。
  ★ 教训：模块引用进 BD 后，**所有带极性语义的端口名都要显式表态**，别让 BD 猜。
- `xvlog.bat -i <file>` 里的 `-i` 是 **include 目录**不是输入文件；误用时它会**静默**
  把该文件当目录吞掉、不报错也不分析 —— 语法检查会假通过。用 `xvlog.bat <file...>`。

- **上板脚本的编码坑（2026-09-13 踩到）**：串口控制台是 **GBK**，板上脚本一律用
  `sudo -E PYTHONIOENCODING=gbk python3` 跑。但 **GBK 覆盖不了全部 Unicode 符号**：
  `✔(U+2714) ✘(U+2718) ⇒(U+21D2) ↔(U+2194)` **都不在 GBK 码表里**，写进 `print()`
  的字符串就会在最关键的那行输出时 `UnicodeEncodeError` 崩掉（本次两次踩中：
  `✔/✘` 崩在自检结果行、`⇒` 会崩在最终结论行 —— 都是跑到底才炸）。
  `→(U+2192) ★(U+2605) ≈(U+2248) —(U+2014)` 是 GBK 内的，可放心用。
  **规则：板上脚本的 `print()` 字符串只用 ASCII + 中文 + 上列安全符号**，
  改完用 `ast` 扫一遍全部字符串常量做 GBK 编码校验（只有 `print` 到的会崩，
  但整体校验更省事）。

**2023.2 踩坑记录**（构建日志见 build_bd_only.log）：
- `v_vid_in_axi4s:4.0` 在 2023.2 对 z020 **不可用**（报 BD 5-216，提示最高 5.0）
  → 换 5.0。核心引脚与 4.0 一致，但**接口名变了**：AXIS 输出接口 m_axis_video → video_out；
  且 **format 枚举变了**：4.0 的 12=RGB565，5.0 的 12=Mono/Sensor（8bit）、无 RGB565
  → 采用 8bit 直通架构（见上表）；
- clk_wiz 若同时给 `CLKOUT*_REQUESTED_OUT_FREQ` 与显式 MMCM 参数，
  Vivado 会把 MMCM 参数标记 disabled 并自动求解（忽略显式值）→ 只留显式 MMCM 参数；
- PSU-1~4 CRITICAL WARNING（DDR 负 DQS 延时）来自 PYNQ-Z2 官方 preset 本身，
  所有用该板卡文件的项目都会出现；对 PYNQ 运行时 overlay 无影响
  （PS7 配置由启动 FSBL 决定，bitstream 里的 PS7 不生效）。

## ★ 第八轮（2026-09-13）：VDMA 寄存器写入顺序 —— 参数必须写在 RS=1 之后

### 症状与证据链

- 第七轮修完复位后，每次运行仍报 `DMASR=0x00ff4810`。按 `axi_vdma_v6_3_rfs.vhd:10696-10716`
  的位定义解码：bit4 DMAInternalErr、bit11 FSizeMoreOrSOFLate、bit14 Err_Irq；
  **bit3 Err=0、Halted=0、Idle=0** —— 通道没被错误停住，只是干等着。
- **`vsize_sweep.py` 用两个判别器排掉"行数不对"**：VSIZE 从 720 扫到 730，四个缓冲
  **全部** std=0.0、帧计数**全部**卡在 255；关键是 **VSIZE=1 也纹丝不动**
  ⇒ 失败与行数无关。
- 顺 RTL 逆推到断点（`axi_vdma_v6_3_rfs.vhd` 行号）：
  ```
  73002-73004  chnl_ready <= '1' 只在 s2mm_fsync_out_i='1' 时        ← 唯一能拉起来的地方
  72387        tuser 要传成 fsync_out，须 d_tready_before_fsync_clr_flag1='0'
  72469-72471  该 flag：halted 时置 1，只有 prmtr_updt_complete 能清 0
  20507-20518  VIDEO_PRMTR_UPDATE：if(run_stop='0') -> '0'   ← 通道没跑就恒 0
                                   elsif(写 VSIZE 寄存器) -> '1' ← 只在那一拍
                                   else -> '0'                ← 只有一拍宽
  ```
  → 链断在最后三行：驱动**先**写 VSIZE **后** start()，写的那刻 `run_stop=0`，
  脉冲被第一分支吃掉，之后再没人写过 VSIZE，flag 永远清不掉 → tuser 传不下去 →
  无 fsync_out → `chnl_ready` 恒 0 → `tready=(真tready and chnl_ready) or dummy_tready`
  恒 dummy → **数据收下就丢**。

### 判决实验与修法

- **`param_order.py` 四组 A/B/C/D 对照坐实**（纯 PS 侧，不用重烧 bit）：
  变体1「写参数→start」帧计数=255/std=0.0（与之前每次运行完全一样，对照组）；
  变体2「start→写 VSIZE」=187/76.8 ✓；变体3「start→写 V/H/S」=187/71.5 ✓；
  变体4「start→写全套(含地址)」=187/70.7 ✓，**DMASR=0x00744000 错误位全清**。
  唯一变量就是顺序。
- 修法：`vdma_capture.py` 的 `start()` 在写完 DMACR **之后**再写一次
  VSIZE/HSIZE/STRIDE + 4 个起始地址 —— 这正是 Xilinx 官方 `XAxiVdma_StartWriteFrame`
  的顺序，我们原来写反了。
- **上板结果（21:38）**：第 3 帧 ok，1024x720，均值 110.3 / std 67.1，4 个缓冲全部
  有数据，连拍 9 帧 58ms/帧（≈17.3fps）。PNG 人工查验是**真实画面** ——
  M1a 第一次出图。
- ★ 方法论（可复用到任何"数据没到"的调试）：先造一个能把假说劈开的**极端输入**
  （VSIZE=1 —— 若通路正常就该飞涨），比扫描"合理区间"有效得多；再做
  **只改一个变量的 A/B**（顺序前 vs 后）。

### 残留症状

图像有横向彩色细条纹（品红/青/绿，右半密），约 2.5% 帧报 fsize_err(bit7)。
两者疑似同源：VDMA 按 VSIZE=720 切帧，而源实际行数可能 ≠720，帧边界缓慢漂移。

## ★ 第十轮（2026-09-13）：双 SOF 定案 —— 每帧两个帧首标志，均匀丢 1/20

### 判决性证据

- 把三处参数摆在一起：
  ```
  探针侧    s_axis_tuser = 30.00/s（每轮都量到，IP 本来就给 SOF）
  探针侧    m_axis_tuser = s_axis_tuser | sof_armed   ← 相或，每帧多一个
  VDMA 侧   C_S2MM_SOF_ENABLE=1 / FSYNCSEL=2 / C_FLUSH_ON_FSYNC=1
  ```
  `sof_armed` 在 vsync 那拍置起（早于有效像素），IP 的 tuser 在第一个有效像素那拍 ——
  差一个消隐期，不是"同拍相或仍是单脉冲"，而是**实打实的两个 SOF**。
- 第二个 SOF 落在 `chnl_ready=0` 期间，触发 rfs.vhd:72423 的 `SOF_LATE_ERR_PULSE_PROCESS`
  → `d_tready_sof_late=1` → `dummy_tready=1` → **收下数据原地丢掉**。
- 亏损量级完全吻合：104/2048 字节 = 5.1%、34/720 行 = 4.7%，**同一个比例**。
  DMASR 三个错误位 bit7 fsize_err | bit8 lsize_err | bit11 fsize_more_or_sof_late
  全部对得上。
- 修法：删掉 `axis_probe.v` 里的相或，m_axis_tuser 直通 s_axis_tuser。
- ★ 判别要点：**每行字节数 < 2048 且每帧行数 < 720，但两者的亏损比例相同**
  → 是"数据被均匀丢掉"，不是"源变小了"，也不是"帧结构配错"。若哪天再看到这个指纹，
  先查 SOF 是不是给了两次。
- 配套工具 `line_count.py`（新）：不再用"窗口内 tlast/tuser 增量"（两端各切在帧中间，
  分子多算半帧分母少算 → 720/721/722 全落在误差里，之前的"721.4 行/帧"就是噪声不是
  信号），改为**死等 tuser 计数变化对齐帧首**，走 N=60 帧取 (L1−L0)/N。

## ★ 第十一轮（2026-09-14）：dvp_probe 决定性测量 —— 传感器每次运行都健康，IP 是全部问题

### 上板量到的事实

- `dvp_probe.py` 每次运行：PCLK=**83.3MHz**（650705 pclk/64 per 0.5s，稳）、
  vsync=**30.00/s**、href=**21660/s = 722 行/帧**（DVPVO 720 + 2 条伪行）、
  scl 累计 11249 ≥ 8208（304 条 SCCB 完整发出）、**8 根数据线全部见过 1/0**、
  MMCM locked。
- 同一时刻 IP 输出侧**每次运行随机落在一个不同状态**：tvalid 0.14~40 M/s、
  tuser 0.67~30/s、tlast 71~903/s；卡死态 live tlast 恒 1 且 tuser 掉到 2/s ——
  **冷上电也能复现**，排除特定版本/时序偶发。
- IP 即便"健康"时也**均匀丢 1/20**：1944/2048 字节 = 94.9%、686/722 行 = 95.0%，
  机制不可观测（IP 内部行为是黑盒）。
- ★ 推论链：传感器每次运行都健康 + IP 输出每次运行随机坏 ⇒ **问题 100% 在 IP 内部**；
  "SCCB 半截配置"理论死亡；"42MHz PCLK"旧结论错误（83.3MHz 与 XDC 84MHz 声明一致）。
- ★ 方法论：**先测不可疑的一方，把问题钉死到单一组件上**。此前所有轮次都在
  IP 外围打转（接线/时钟/复位/VDMA 参数），因为缺一个"传感器侧独立观测"；
  dvp_probe 提供了它，一轮就把嫌疑范围收窄到 IP 本体。

### 决策：整体替换 IP

第十二版删掉 `v_vid_in_axi4s`，自研 `rtl/ov5640/dvp2axis_bridge.v`（~150 行 RTL 干
IP 的全部事：href 门控打包进 xpm_fifo_async，tuser=帧首、tlast=行尾，单 SOF 由构造
保证）。VSIZE 720→722。设计细节与验证结果见"第十二轮"一节。

## ★ 第十二轮（2026-09-14）：dvp2axis_bridge 自研桥

- 设计要点见 `rtl/ov5640/dvp2axis_bridge.v` 文件头（①fwft 零延迟直通 ②eol 与行尾
  字节同拍 ③sof 由 vsync 武装、整行丢弃时保留 ④FIFO 满时**整行丢弃**、行内绝不
  半途停推 ⑤溢出/下溢 sticky 进探针 ⑥写侧复位 = vid_rst_ctrl 的"两侧时钟都稳定才撤"）。
- 期望上板值：tvalid ≈ **44.4M/s**（2048 字节 × 722 行 × 30fps）、tuser **30/s**、
  tlast **21660/s**、w3 无溢出/下溢、`line_count.py` 恒 **722**。
- **上板第一天的结果（2026-09-14 晚）**：桥**完全透明**，把传感器状态原样暴露了
  出来 —— 而且暴露出一个此前被 IP 的随机性掩盖的**新事实**：
  传感器状态**逐次下载随机**（见第十三轮）。桥本身四轮观测与其输入逐位自洽
  （vsync 没来 → tuser 全 0；href 只来一段垃圾 → 只在复位后突发 push），无一处
  行为偏离设计；桥的验收要在传感器健康的一次运行里完成（期望值不变）。

## ★ 第十三轮（2026-09-14）：传感器逐次下载随机 —— ctrl[4] 软件修复（结果待上板）

### 上板量到的事实（2026-09-14 晚，五次下载）

- 同一块 bit、同一块板，**每次下载后传感器状态随机**：
  21:33 / 21:35 / 21:36 / 21:54 四次下载全部落在坏状态（pclk 在跑、
  **vsync 永不来**、href 每次复位后只来一段 76~85ms 的高电平、随后 60s 无活动），
  21:49 一次下载落在好状态（vsync 30/s、href 21660/s、SCCB 304 条全写完、
  数据线全活，健康了整整 9 秒采样窗口）。
- 加载方式不构成变量：run_selfcheck（Overlay）落在坏状态、dvp_probe（Overlay）
  落在好状态，同一加载路径两种结果 → 排除加载方式因素。
- 第十一轮（前一天）同一块板 100% 健康 —— 差别发生在**昨天拔卡换卡**之后：
  极可能动到了摄像头杜邦线接触（供电/时钟等），上电时序开始落临界态。
- ★ 机制：每次下载 bit 都会重跑 ov5640_top 的 PWDN/RST 上电时序 = **重新掷一次
  骰子**；坏骰子 = 上电时序没落稳（PLL 锁了但流引擎没起来）。坏状态持续 ≥60s
  不恢复（当次骰子永久坏），只能重掷。
- ★ 方法论：**别把熟悉的签名往新问题上套**。坏状态初看像"环境坑 #2（SCCB 半截
  配置）"，但那个坑要求断电才能恢复，而 21:49 那次没有断电就健康了 ——
  判别性测量（dvp_probe）一跑，老判断当场撤回。

### 修复设计（第十三版）

- **ctrl[4] = force_sensor_rst**（`rtl/debug/dvp_ctrl.v`）：按住 ov5640_top 的
  s_rst_n（power_ctrl 复位态天然输出 PWDN=1/RST=0 → 传感器完整掉电重来），
  放开后重跑 PWDN/RST/21ms 时序 + **304 条 SCCB 配置** —— 不重烧 bit 也能重掷骰子。
- **必须连带复位桥的 FIFO**（ctrl[4] 同时置 vid_rst_force）：坏状态攒下的垃圾字节
  堵在 FIFO 头部的话，新帧的 SOF 排在 8192 字节垃圾后面，VDMA 永远收不到 fsync，
  "修复"就是假的。
- **vid_rst_ctrl 重新鉴定**（第十三版）：force 释放后 PCLK 活性计数清零重数
  2^20 拍才撤复位 —— 传感器重跑期间 PCLK 停摆，复位保持到 PCLK 恢复并稳定，
  避免第七轮"复位比时钟早撤"的教训重演。
- **桥写侧内部观测**（w3[31:28]）：sof武装/丢行中/FIFO复位忙/vid复位电平，
  2-FF 同步进 axis_probe —— 再坏也能解剖到写侧内部，不再隔着 FIFO 猜。
- **驱动协议**：`dvp_probe.py` 6s 判读 + ctrl[4] 自动修复（最多 3 次）；
  修好后其余脚本全部 `noprog` 接力（不重下 bit = 不重掷骰子）：
  `dvp_probe.py` → `stream_probe.py noprog` → `line_count.py noprog`
  → `run_selfcheck.py noprog`。

### 待办

- 物理层面：断电重插摄像头 16 根杜邦线（重点 JA5(3V3)/JA6(GND) 供电、
  JB1(pclk)/JB4(xclk)）—— 接触不良是掷骰子的根因，软件修复只是保底。
- 上板验证第十三版：坏骰子下 dvp_probe 应能自动修复到健康；
  健康下四连（noprog 接力）出桥的期望值 + 真图。
- 比赛现场：演示前先跑 dvp_probe（含自动修复），修好不动硬件直接演示。

## 接线（PMOD）

### ★ PMOD 2×6 的物理排布（踩坑点，务必先看）

PYNQ-Z2 的 PMOD 排针**看不到可读丝印**，而 Pmod 的编号方式**反直觉**
（Digilent 官方手册里自己标注为 "non-standard"）：

```
上排（左→右）：  1     2     3     4     5(GND)  6(VCC)
下排（左→右）：  7     8     9    10    11(GND) 12(VCC)
```

**即 1 正对 7、2 正对 8、3 正对 9、4 正对 10（上下成列），
不是"上排 1~6、下排接着 7~12"的蛇形。** 电源孔 5/6、11/12
并排落在每一排的同一端。

依据（两个独立来源互证）：
- Digilent 参考手册（Cerebot MX7cK / MX4cK / Nexys4 DDR）：上排 1~4 信号 + 5=GND + 6=VCC，
  下排 7~10 信号 + 11=GND + 12=VCC；
- 某第三方 PYNQ-Z2 引脚页把"接在**上排**的 PMOD"列成 Y18,Y19,Y16,Y17,W14,Y14,T11(+T10) ——
  正好是 JA1~JA4 **接着** JB1~JB4；"下排"列成 U18,U19,W18,W19,V16,W16,V12(+W13) ——
  正好是 JA7~JA10 **接着** JB7~JB10。**与上表完全吻合**。

`software/board/find_pins.py` 可用 FPGA 把孔"喊"出来（零假设定位）：
`ov5640_pwdn`(JB8) 在 0V/3.3V 间慢速翻转、`ov5640_xclk`(JB4) 整根停/开，
拿万用表挨个孔戳 —— **哪个孔跟着动哪个就是它**，JB4 与 JB8 必然同一列，
由此把整排编号定死。

### 信号对照

| 信号 | 引脚 | 信号 | 引脚 |
|---|---|---|---|
| data0 | JA1 Y18 | data1 | JA2 Y19 |
| data2 | JA3 Y16 | data3 | JA4 Y17 |
| data4 | JA7 U18 | data5 | JA8 U19 |
| data6 | JA9 W18 | data7 | JA10 W19 |
| pclk | JB1 W14 | href | JB2 Y14 |
| vsync | JB3 T11 | xclk | JB4 T10 |
| rst_n | JB7 V16 | pwdn | JB8 W16 |
| scl | JB9 V12 | sda | JB10 W13 |

供电：模块 3V3 接 **JA6**、GND 接 **JA5**（**JA5=GND、JA6=3V3，别接反**）；
LD0 亮 = MMCM 锁定。

## 上板验证步骤（M1a）

1. **断电**，按接线表插 OV5640（JA 数据 8 根 + JB 控制 8 根，杜邦线短且等长；
   模块 3V3/GND 接 JA5/JA6；PMOD 方形焊盘 = 1 脚）；
2. SD 卡插 Windows，`deploy/` 全部文件拷到卡根目录（板内即 /boot）；
3. 插回上电，**先看 LD0**：亮 = MMCM 锁定（不亮查供电/杜邦线，别浪费时间等超时）；
4. 串口验证链（第十三版起四连 **noprog 接力**：只第一个脚本下 bit，其余沿用；
   全部 `sudo -E PYTHONIOENCODING=gbk python3 /boot/xxx.py`；
   SSCOM 每次发送前 Ctrl+A 清空输入框）：
   - ① `dvp_probe.py` 传感器健康：PCLK≈83.3MHz、vsync≈30/s、href≈21660/s、
     scl 累计≥8208、8 根数据线全见过 1/0；**没在出帧会自动用 ctrl[4] 重跑
     上电时序+SCCB 修复（最多 3 次）** —— 修好别重下 bit；
   - ② `stream_probe.py noprog` 桥速率：**tvalid≈44.4M/s、tuser≈30/s、
     tlast≈21660/s**、w3 无溢出(bit13)/下溢(bit14) —— 桥的第一手观测，
     VDMA 报错只能反推；w3 bit31-28 是桥写侧内部观测；
   - ③ `line_count.py noprog` 精确行数：**恒 722 行/帧**（720 有效 + 2 伪行）；
   - ④ `run_selfcheck.py noprog` 出图：1024×722 真实画面，存 `/boot/vdma_capture_check.png`
     并报帧率 + 帧均值/标准差；
5. 拔卡在 Windows 看 PNG。排障三件套：
   - 帧标准差 ≈0 → 没有真实图像数据（查 DVP 接线/ILA）；
   - 颜色反了 → 驱动 `byte_swap=False` 重试；
   - 帧内容花屏 → 84M pclk 走 PMOD 偏斜问题，见"已知风险"对策；
   - 5s 超时无帧 → LD0 是否亮、DMASR 错误位、ILA 抓 pclk 域。

## 已知风险（与对策）

- **pclk 84MHz 走 PMOD 普通 IO**（CLOCK_DEDICATED_ROUTE FALSE）：JA 数据 / JB pclk
  跨 PMOD 走线长度差带来偏斜，旧板同方案可行，但 PYNQ 走线更长。若采到花屏：
  ① 降帧率（改 ov5640_cfg 表 PCLK 分频，42M/30fps）；② 数据用 pclk 反沿采样。
- **比赛报告须注明**：官方推荐 Vivado 2025.2/2026.1，本工程用 2023.2
  （全部 TCL 脚本化，可复现）。
- M1b 显示：传感器 1024×720 非标准 HDMI 时序 → 方案 A：ov5640_cfg 表改
  1280×720@30（DVPHO 0x500 + PCLK 分频，4~5 条寄存器）；方案 B：v_tc 输出
  1280×720 拉伸。优先 A。
