// ============================================================================
// dvp_probe.v — DVP 探针（M1a 上板排障专用）：把传感器域信号活动量变成寄存器
//
// 背景：自检发现传感器 I2C 应答正常、供电 3.29V 正常，但 PCLK 从头到尾没启动
// （20s 监视增量全 0），vsync/href 各只在上电出过一次。剩下两个嫌疑：
//   ① XCLK 有没有真正到传感器？   ② PWDN/RST 实际电平对不对？
// 本模块回答：
//   w0 = pclk/64 上升沿计数  （v1）
//   w1 = vsync 上升沿计数    w2 = href 上升沿计数
//   w3 = scl 上升沿计数      w4 = sda 上升沿计数
//   w5 = {12'b0, rst_n电平, pwdn电平, xclk电平, 见过0[7:0], 见过1[7:0], locked}
//        ↑ 位域：bit0=locked, [8:1]=见过1, [16:9]=见过0, [17]=xclk, [18]=pwdn, [19]=rst_n
//   w6 = xclk 翻转载数（相邻两拍不同即算，20ms 内就能看出活没活）   ← v2 新增
//   ↑ xclk/pwdn/rst_n 三根控制线的**板上实际电平**（FPGA 出脚处）直接可读，
//     用来验证 power_ctrl 稳态是否真的是 pwdn=0 / rst_n=1。
//
// 结构：所有计数器跑在 axi_clk(100MHz) 域，传感器域信号 2-FF 同步后取上升沿；
//   pclk(84MHz) 太快不能直接同步 → 传感器域先 /64 分频再同步；
//   "见过 1/0"锁存器在 pclk 域置位（pclk 停了也不丢已存内容），同步到 axi 域。
// 输出经 4 个 axi_gpio 挂 PS，地址固定（见 bd/m1a_capture.tcl）：
//   probe0: w0,w1 / probe1: w2,w3 / probe2: w4,w5 / probe3: w6 + 输出控制
// ============================================================================
module dvp_probe (
    input  wire        axi_clk,     // 100MHz（PS FCLK0）
    input  wire        pclk,        // 传感器像素时钟（84MHz，可能已停）
    input  wire        vsync,       // DVP 场同步
    input  wire        href,        // DVP 行有效
    input  wire        scl,         // SCCB 时钟线（板级信号）
    input  wire        sda,         // SCCB 数据线（板级信号）
    input  wire [ 7:0] data,        // DVP 数据线
    input  wire        xclk,        // 传感器主时钟（FPGA 输出到模块，v2 观测用）
    input  wire        pwdn,        // 传感器 PWDN（板上出脚处电平）
    input  wire        rst_n,       // 传感器 RST_N（板上出脚处电平）
    input  wire        locked,     // MMCM 锁定
    output wire [31:0] w0, w1, w2, w3, w4, w5, w6
);

    // ---- pclk 域：/64 分频 + 数据线"见过"锁存 ----
    reg [5:0] div     = 6'd0;
    reg [7:0] seen1_p = 8'h00;
    reg [7:0] seen0_p = 8'h00;
    always @(posedge pclk) begin
        div     <= div + 6'd1;
        seen1_p <= seen1_p | data;      // 该位见过 1 则永久置 1
        seen0_p <= seen0_p | ~data;     // 该位见过 0 则永久置 1
    end

    // ---- 同步进 axi 域（2 级防亚稳态 + 1 级做边沿检测）----
    reg vs_s1, vs_s2, vs_s3;
    reg hr_s1, hr_s2, hr_s3;
    reg sc_s1, sc_s2, sc_s3;
    reg sd_s1, sd_s2, sd_s3;
    reg pd_s1, pd_s2, pd_s3;
    reg xc_s1, xc_s2;                   // xclk：统计翻转 + 取电平（xc_s2 即电平）
    reg pw_s1, pw_s2;                   // pwdn 电平
    reg rs_s1, rs_s2;                   // rst_n 电平
    reg [7:0] s1_s1, s1_s2;
    reg [7:0] s0_s1, s0_s2;
    reg lk_s1, lk_s2;
    always @(posedge axi_clk) begin
        {vs_s1, hr_s1, sc_s1, sd_s1, pd_s1} <= {vsync, href, scl, sda, div[5]};
        {vs_s2, hr_s2, sc_s2, sd_s2, pd_s2} <= {vs_s1, hr_s1, sc_s1, sd_s1, pd_s1};
        {vs_s3, hr_s3, sc_s3, sd_s3, pd_s3} <= {vs_s2, hr_s2, sc_s2, sd_s2, pd_s2};
        {xc_s1, xc_s2} <= {xclk, xc_s1};
        {pw_s1, pw_s2} <= {pwdn, pw_s1};
        {rs_s1, rs_s2} <= {rst_n, rs_s1};
        s1_s1 <= seen1_p;  s1_s2 <= s1_s1;
        s0_s1 <= seen0_p;  s0_s2 <= s0_s1;
        {lk_s1, lk_s2} <= {locked, lk_s1};
    end

    // ---- axi 域计数器 ----
    reg [31:0] cnt_pclk = 32'd0;
    reg [31:0] cnt_vs   = 32'd0;
    reg [31:0] cnt_hr   = 32'd0;
    reg [31:0] cnt_scl  = 32'd0;
    reg [31:0] cnt_sda  = 32'd0;
    reg [31:0] cnt_xclk = 32'd0;        // xclk 翻转次数：24MHz 时每秒约 4.8e7（32 位够）
    always @(posedge axi_clk) begin
        if (pd_s2 & ~pd_s3) cnt_pclk <= cnt_pclk + 32'd1;
        if (vs_s2 & ~vs_s3) cnt_vs   <= cnt_vs   + 32'd1;
        if (hr_s2 & ~hr_s3) cnt_hr   <= cnt_hr   + 32'd1;
        if (sc_s2 & ~sc_s3) cnt_scl  <= cnt_scl  + 32'd1;
        if (sd_s2 & ~sd_s3) cnt_sda  <= cnt_sda  + 32'd1;
        if (xc_s1 ^ xc_s2)  cnt_xclk <= cnt_xclk + 32'd1;
    end

    assign w0 = cnt_pclk;
    assign w1 = cnt_vs;
    assign w2 = cnt_hr;
    assign w3 = cnt_scl;
    assign w4 = cnt_sda;
    // bit0=locked, [8:1]=见过1, [16:9]=见过0, [17]=xclk电平, [18]=pwdn, [19]=rst_n
    assign w5 = {12'b0, rs_s2, pw_s2, xc_s2, s0_s2, s1_s2, lk_s2};
    assign w6 = cnt_xclk;

endmodule
