// ============================================================================
// axis_probe.v — AXI-Stream 直通 + 流侧探针（M1a 第十二版 → 第十三版，2026-09-14）
//
// 历史（第十一版之前）：本模块一度同时干三件事 —— ①统计源侧流、
// ②用 DVP vsync 合成 SOF 与源 tuser 相或（因为 v_vid_in_axi4s 的 tuser
// 不可信）、③观测 IP 的 overflow/underflow/vid_reset。
//
// ★ 第十二版（dvp2axis_bridge 替换 v_vid_in_axi4s 之后）：
//   源从"IP"换成自研桥。桥**由构造保证**帧首单 SOF、行尾单 EOL，
//   vsync 合成 SOF 的权宜之计删除（相或反而可能造出双 SOF ——
//   桥的 sof 与 vsync 边沿相位差一两拍就会一帧两个 tuser）。
//   本模块回归纯职能：**直通 + 统计**。四路数据全 wire，不改任何一比特。
//
// 探针字（PS 侧地址 0x43A40000 / 0x43A50000，见 bd/m1a_capture.tcl）：
//   w0  源 tvalid 拍数（自由计数，32 位；44.4M/s 时约 96s 回绕）
//   w1  源 tuser 上升沿数  ← ★ 帧首 SOF：健康时以 ~30/s 增长
//   w2  源 tlast 上升沿数  ← ★ 行尾 EOL：健康时以 ~21660/s 增长
//   w3  状态字：
//         bit0 见过 tvalid   bit1 见过 tuser   bit2 见过 tlast  bit3 见过 tready
//         bit4 live tvalid   bit5 live tlast   bit6 live tuser  bit7 live tready
//         bit8  0（原 live vsync，第十二版移除）
//         bit9  0（原 sof_armed，第十二版移除）
//         ★ bit10 live 桥写侧溢出（br_ovf，2-FF 同步后）
//         ★ bit11 live 桥读侧下溢（br_unf）
//         ★ bit12 0（原 vid_io_in_reset 观测，IP 已删）
//         ★ bit13 见过溢出  bit14 见过下溢
//         ★ bit15 真送给 VDMA 的 m_axis_tuser 电平（第十二版起 m_axis_tuser
//                 就是 s_axis_tuser 的直通，两路观察不再有区别）
//         ★★ 第十三版 [31:28] 桥写侧内部观测（pclk 域，2-FF 同步后）：
//              bit31 sof_armed（vsync 已武装帧首、还没用掉）
//              bit30 dropping（整行丢弃中）
//              bit29 wr_rst_busy（FIFO 写侧复位忙）
//              bit28 rst_pclk 电平（视频域复位撤了没）
//            上板发现传感器逐次下载随机（1/5 健康），坏状态 tuser/tlast 全 0；
//            这 4 位把坏状态解剖到写侧内部（见 dvp2axis_bridge.v 文件头）。
//         [27:16] 0
//
// ★ 两条通用教训（写给以后，也包括我自己）：
//     1) **错误位消失不等于问题解决。** 第十轮第一版删掉相或之后，DMASR 的
//        bit11 确实灭了 —— 但那是因为流根本没跑到会触发它的地方。拿它当
//        "修好了"的证据是错的。判断有没有修好，看**速率和比值**。
//     2) 动手删一个"看起来多余"的信号之前，先确认它不是承重的
//        （sof_armed 当时就是承重的；今天它随 IP 一起退役，是因为
//        源头换成了自己会产 SOF 的桥，而不是"它多余"）。
// ============================================================================

module axis_probe (
    input  wire        aclk,
    input  wire        aresetn,

    // 桥的诊断 sticky（慢速电平；ovf 生在 pclk 域，本模块 2-FF 同步）
    input  wire        br_ovf,
    input  wire        br_unf,

    // 桥写侧内部观测（第十三版，pclk 域慢速电平；本模块 2-FF 同步进 w3[31:28]）
    input  wire        br_sof_armed,
    input  wire        br_dropping,
    input  wire        br_wr_rst_busy,
    input  wire        br_rst_pclk,

    // 从 dvp2axis_bridge 来（AXI4-Stream Video，8bit 单分量）
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tlast,
    input  wire        s_axis_tuser,

    // 去 axi_vdma 的 S_AXIS_S2MM
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire [7:0]  m_axis_tdata,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,

    // 探针字，接 axi_gpio 输入通道
    output reg  [31:0] w0,
    output reg  [31:0] w1,
    output reg  [31:0] w2,
    output reg  [31:0] w3
);

    // ------------------------------------------------------------------
    // 直通：五个信号纯 wire，不引入任何延迟/寄存器（第十二版：tuser 也直通）
    // ------------------------------------------------------------------
    assign s_axis_tready = m_axis_tready;
    assign m_axis_tvalid = s_axis_tvalid;
    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tlast  = s_axis_tlast;
    assign m_axis_tuser  = s_axis_tuser;

    wire beat = s_axis_tvalid & m_axis_tready;   // 本拍发生了一次成功握手

    // br_ovf/br_unf 是 sticky 电平，只读电平不做边沿判定；2-FF 同步
    // （br_unf 本就在 aclk 域，再同步两级无害且统一处理）
    reg ovf_s1, ovf_s2, unf_s1, unf_s2;
    always @(posedge aclk) begin
        ovf_s1 <= br_ovf;
        ovf_s2 <= ovf_s1;
        unf_s1 <= br_unf;
        unf_s2 <= unf_s1;
    end

    // 桥写侧观测（第十三版）：pclk 域慢速电平，2-FF 同步进 aclk 域。
    // aclk(100M) 快于 pclk(83.3M)，对慢速电平采样安全；偶尔的采样混叠
    // 无碍 —— 观测的是"电平段"不是精确边沿。
    reg sa_s1, sa_s2, dr_s1, dr_s2, wb_s1, wb_s2, rs_s1, rs_s2;
    always @(posedge aclk) begin
        {sa_s1, sa_s2} <= {br_sof_armed,   sa_s1};
        {dr_s1, dr_s2} <= {br_dropping,    dr_s1};
        {wb_s1, wb_s2} <= {br_wr_rst_busy, wb_s1};
        {rs_s1, rs_s2} <= {br_rst_pclk,    rs_s1};
    end

    // ------------------------------------------------------------------
    // 探针统计
    // ------------------------------------------------------------------
    reg tu_d, tl_d;
    always @(posedge aclk) begin
        tu_d <= s_axis_tuser;
        tl_d <= s_axis_tlast;
    end
    wire tu_rise = s_axis_tuser & ~tu_d;
    wire tl_rise = s_axis_tlast  & ~tl_d;

    reg seen_tvalid, seen_tuser, seen_tlast, seen_tready;
    reg seen_ovf, seen_unf;

    always @(posedge aclk) begin
        if (!aresetn) begin
            w0          <= 32'd0;
            w1          <= 32'd0;
            w2          <= 32'd0;
            w3          <= 32'd0;
            seen_tvalid <= 1'b0;
            seen_tuser  <= 1'b0;
            seen_tlast  <= 1'b0;
            seen_tready <= 1'b0;
            seen_ovf    <= 1'b0;
            seen_unf    <= 1'b0;
        end else begin
            if (s_axis_tvalid) w0 <= w0 + 32'd1;   // tvalid 拍数
            if (tu_rise)       w1 <= w1 + 32'd1;   // tuser 上升沿数（帧数）★
            if (tl_rise)       w2 <= w2 + 32'd1;   // tlast 上升沿数（行数）★

            if (s_axis_tvalid) seen_tvalid <= 1'b1;
            if (s_axis_tuser)  seen_tuser  <= 1'b1;
            if (s_axis_tlast)  seen_tlast  <= 1'b1;
            if (m_axis_tready) seen_tready <= 1'b1;
            if (ovf_s2)        seen_ovf    <= 1'b1;
            if (unf_s2)        seen_unf    <= 1'b1;

            w3 <= { sa_s2,               // [31] 桥写侧 sof_armed（第十三版）
                    dr_s2,               // [30] 桥写侧 dropping
                    wb_s2,               // [29] 桥写侧 wr_rst_busy
                    rs_s2,               // [28] 桥写侧 rst_pclk 电平
                    12'd0,               // [27:16] 保留
                    m_axis_tuser,        // [15] 真送给 VDMA 的那路 tuser
                    seen_unf,            // [14] 见过桥读侧下溢
                    seen_ovf,            // [13] 见过桥写侧溢出
                    1'b0,                // [12] 原 vid_io_in_reset，已移除
                    unf_s2,              // [11] live 桥读侧下溢
                    ovf_s2,              // [10] live 桥写侧溢出
                    1'b0,                // [9]  原 sof_armed，已移除
                    1'b0,                // [8]  原 live vsync，已移除
                    m_axis_tready,       // [7]  live tready
                    s_axis_tuser,        // [6]  live tuser
                    s_axis_tlast,        // [5]  live tlast
                    s_axis_tvalid,       // [4]  live tvalid
                    seen_tready,         // [3]
                    seen_tlast,          // [2]
                    seen_tuser,          // [1]
                    seen_tvalid };       // [0]
        end
    end

endmodule
