// ============================================================================
// dvp_ctrl.v — 传感器控制线接管（M1a 排障专用，探针 v2 → 第十三版）
//
// 背景：探针 v1 已确认"传感器 I2C 应答正常、供电 3.29V 正常、但 PCLK 从头到尾
// 没启动"，而 XCLK 死活未知。本模块把 XCLK 和 PWDN 的控制权交给 PS 软件，
// 做判决性实验：
//   ① ctrl[0] 开/关 XCLK（24MHz），看传感器 PLL(→PCLK) 跟不跟着变
//      ——跟 = XCLK 确实到传感器了；不跟 = XCLK 没到 / 传感器没用它
//   ② ctrl[1] 拉高 PWDN 让传感器重新走一遍上电时序
//
// 第十三版（2026-09-14）新增：
//   ③ ctrl[4] = force_sensor_rst：1 = 按住传感器复位（s_rst_n 拉低），
//      放开后 ov5640_top 重跑完整上电时序（PWDN 6ms→RST 2ms→21ms，24MHz 下
//      实际更长）+ 304 条 SCCB 配置；**同时**经 vid_rst_force 按住视频域
//      复位（桥的 xpm FIFO 一并复位 —— 不这样做，坏状态攒下的旧垃圾字节
//      堵在 FIFO 头部，新帧的 SOF 永远到不了 VDMA，修复就是假的）。
//
//      ★ 为什么需要它：第十二版上板发现传感器状态逐次下载随机（同一天
//        1/5 健康，其余 pclk 在跑但 vsync 永不来）。每次下载 bit 都会重跑
//        上电时序 = 重新掷一次骰子；ctrl[4] 让 PS **不重烧 bit 也能重新掷**，
//        掷到健康为止。上电时序落临界态是接线/电源接触层面的问题，软件
//        修复是比赛现场不搬硬件的保底手段（详见 README 第十三轮）。
//
// ctrl 位定义（PS 经 axi_gpio_probe3 通道1 写入）：
//   bit0 = xclk_off         1=拉低停钟，0=输出 24MHz    ← 默认 0
//   bit1 = force_pwdn       1=强制传感器掉电，0=随 pl   ← 默认 0
//   bit3 = vid_rst_force    1=强制复位视频域，0=正常    ← 默认 0（见 vid_rst_ctrl.v）
//   bit4 = force_sensor_rst 1=按住传感器复位重跑上电+SCCB，0=正常 ← 默认 0
//   （bit2 跳过：本工程早前的实验脚本按"bit2 保留"写过，不占用）
//   bit6 = dbg_trig         0→1 上升沿触发桥账本快照   ← 默认 0
//   bit9..7 = dbg_sel       选桥账本影子寄存器 0..6    ← 默认 0
//   其余位保留
//
// 第十四版（2026-09-15）：bit6/bit9..7 直通输出（dbg_trig/dbg_sel），给
// dvp2axis_bridge 的写侧账本用。ctrl 本身是 axi_gpio 寄存器输出（axi_clk 域），
// 与桥的 aclk 同域，组合直通即可，同步由桥内部做。
//
// ★ 极性约定：axi_gpio 输出寄存器上电/复位值为 0，所以**必须让 ctrl=0 对应
//   "正常工作"**（XCLK 开、PWDN 随 pl、传感器不复位），否则烧完 bit 就把
//   采集链掐死了。故 bit0 定义为"停钟"而非"开钟"——这是踩过一次的坑
//   （v2 首版写反，已修）。
//
// 兼容性：ctrl=0（默认）时 xclk 常开、pwdn 随 pl、rst_n_out = locked 直通，
// 与第十二版行为逐位一致。
// ============================================================================
module dvp_ctrl (
    input  wire axi_clk,          // PS FCLK0 100MHz（ctrl 寄存器写在此域）
    input  wire clk24,            // 传感器主时钟 24.02MHz（来自 clk_wiz clk_out2）
    input  wire [31:0] ctrl,      // PS 写入的控制字
    input  wire pl_pwdn,          // 原 pl 逻辑：power_ctrl 输出（保持默认行为）
    input  wire locked,           // MMCM 锁定（原直连 ov5640_top/s_rst_n）

    output wire xclk_out,         // → 传感器 XCLK（受 ctrl[0] 门控）
    output wire sensor_pwdn,      // → 传感器 PWDN（默认跟 pl；ctrl[1]=1 强制拉高）
    output wire vid_rst_force,    // → vid_rst_ctrl.force_rst（ctrl[3] | ctrl[4]）
    output wire rst_n_out,        // → ov5640_top.s_rst_n（locked 经 ctrl[4] 门控）
    output wire dbg_trig,         // → 桥 dbg_trig（ctrl[6] 快照触发，第十四版）
    output wire [2:0] dbg_sel     // → 桥 dbg_sel（ctrl[9:7] 影子选择，第十四版）
);

    // ctrl 从 axi_clk 域同步到 clk24 域（4 根控制位各 2 级）
    reg xclk_off_s1, xclk_off_s2;
    reg pwdn_s1, pwdn_s2;
    reg pl_s1, pl_s2;
    reg sr_s1, sr_s2;
    always @(posedge clk24) begin
        {xclk_off_s1, xclk_off_s2} <= {ctrl[0], xclk_off_s1};
        {pwdn_s1, pwdn_s2}         <= {ctrl[1], pwdn_s1};
        {pl_s1, pl_s2}             <= {pl_pwdn, pl_s1};
        {sr_s1, sr_s2}             <= {ctrl[4], sr_s1};
    end

    // XCLK 门控：ctrl=0（默认）→ 常开，输出 24MHz；
    //            ctrl[0]=1 → 输出恒定低（静止电平，传感器不会当成时钟）
    assign xclk_out    = xclk_off_s2 ? 1'b0 : clk24;

    // PWDN：默认（ctrl[1]=0）跟 pl 原逻辑走，与 v1 完全一致；
    //       软件接管时强制拉高，让传感器重新走一遍上电时序
    assign sensor_pwdn = pwdn_s2 ? 1'b1 : pl_s2;

    // 传感器复位：默认（ctrl[4]=0）= locked 直通，与第十二版逐位一致。
    // ctrl[4]=1 按住复位；power_ctrl 复位态天然输出 PWDN=1/RST=0（掉电），
    // 放开后重跑完整时序 + SCCB 配置。
    // locked 本就是异步复位用法（ov5640_top 里 negedge s_rst_n），保持异步。
    assign rst_n_out    = locked & ~sr_s2;

    // 视频域强制复位：ctrl[3]（纯复位桥，判决实验用）| ctrl[4]（传感器重跑
    // 必须带桥复位，否则旧垃圾堵 FIFO、新 SOF 到不了 VDMA）。
    // 组合直通出去，**同步在 vid_rst_ctrl 里做**（那边在 pclk 域有 2 级
    // 同步器）。这里不再重复打拍，避免两级串联引入额外的 CDC 路径。
    // ctrl 本身是 axi_gpio 的寄存器输出，源端是干净的。
    assign vid_rst_force = ctrl[3] | ctrl[4];

    // 第十四版：桥账本控制直通（axi_clk 域 → 桥 aclk 域，同域）
    assign dbg_trig = ctrl[6];
    assign dbg_sel  = ctrl[9:7];

endmodule
