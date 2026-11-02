`timescale 1ns / 1ps
// ============================================================================
// line_engine.v —— 轨迹画线上层（line_draw + 1 级缓冲 + axi_wr_master）
//
// 输入：一条线（端点 + 颜色）+ 目标 framebuffer 基地址
// 输出：AXI4 写（把像素写进 DDR framebuffer）
//
// ★ 像素格式：framebuffer 是 32 位/像素，小端内存排布 [G, B, R, 0]
//   ⇒ 32 位字的取值 = (R<<16)|(B<<8)|G（hdmi_out.FramePump 的打包口径，别改）。
//   line_draw 给的是 24 位 {B,G,R} = {B[23:16], G[15:8], R[7:0]}，这里换序：
//     word = {8'h00, R, B, G} = {8'h00, color[7:0], color[23:16], color[15:8]}
//
// ★★ v2（2026-10-30 深夜）：像素流改成 **1 级 skid 缓冲 + valid/ready**
//   v1 的「ld_we 来了若写主空闲就直接发」有两个丢像素窗口（见 line_draw 头注释）。
//   现在：画线核 → buf（valid/ready）→ 写主（wr_req 单拍）。协议上不丢不重。
//
// ★ cmd_done 是**粘滞**的（保持到下一次 start）：PS 用 AXI 轮询 STATUS 的间隔
//   远大于 1 拍，脉冲式 done 会被漏掉。
// ============================================================================
module line_engine #(
    parameter integer FB_W = 1280,
    parameter integer FB_H = 720,
    parameter integer IDW  = 6
)(
    input  wire        clk,
    input  wire        rstn,

    // ---- 命令（PS 写寄存器触发）----
    input  wire        cmd_start,      // 单拍：启动一条线（忙时忽略）
    input  wire [17:0] cmd_x0, cmd_y0, cmd_x1, cmd_y1,
    input  wire [23:0] cmd_color,      // {B,G,R}
    input  wire [31:0] fb_base,        // framebuffer 物理基地址（字节）
    output wire        cmd_busy,
    output wire        cmd_done,       // 粘滞：整条线画完（所有像素写完）；下次 start 清

    // ---- AXI4 写 ----
    output wire [IDW-1:0] m_axi_awid,
    output wire [31:0] m_axi_awaddr,
    output wire [7:0]  m_axi_awlen,
    output wire [2:0]  m_axi_awsize,
    output wire [1:0]  m_axi_awburst,
    output wire        m_axi_awlock,
    output wire [3:0]  m_axi_awcache,
    output wire [2:0]  m_axi_awprot,
    output wire        m_axi_awvalid,
    input  wire        m_axi_awready,
    output wire [31:0] m_axi_wdata,
    output wire [3:0]  m_axi_wstrb,
    output wire        m_axi_wlast,
    output wire        m_axi_wvalid,
    input  wire        m_axi_wready,
    input  wire [IDW-1:0] m_axi_bid,
    input  wire [1:0]  m_axi_bresp,
    input  wire        m_axi_bvalid,
    output wire        m_axi_bready
);

    // ---- 画线核 ----
    wire        ld_valid, ld_ready;
    wire [20:0] ld_addr;
    wire [23:0] ld_data;
    wire        ld_busy, ld_done;

    // ---- 写主 ----
    wire        wr_ack, wr_busy;
    reg         wr_req;
    reg  [31:0] wr_addr, wr_data;

    // ---- 1 级缓冲（skid）----
    reg         buf_valid;
    reg  [20:0] buf_addr;
    reg  [23:0] buf_data;
    reg         issued;        // 写事务在途（wr_req 起到 B 响应止）
    reg         drawn;         // 画线核已把最后一个像素交出来
    reg         done_r;        // 粘滞 done

    assign ld_ready = !buf_valid;          // 缓冲空才收

    line_draw #(.FB_W(FB_W), .FB_H(FB_H)) u_draw (
        .clk(clk), .rstn(rstn),
        .start(cmd_start),
        .x0(cmd_x0), .y0(cmd_y0), .x1(cmd_x1), .y1(cmd_y1),
        .color(cmd_color),
        .fb_valid(ld_valid), .fb_addr(ld_addr), .fb_data(ld_data),
        .fb_ready(ld_ready),
        .busy(ld_busy), .done(ld_done)
    );

    // 像素 → AXI 字（framebuffer 口径 (R<<16)|(B<<8)|G）
    wire [31:0] px_addr = fb_base + {9'd0, buf_addr, 2'b00};
    wire [31:0] px_data = {8'h00, buf_data[7:0], buf_data[23:16], buf_data[15:8]};

    axi_wr_master #(.AW(32), .DW(32), .IDW(IDW)) u_axi (
        .clk(clk), .rstn(rstn),
        .wr_req(wr_req), .wr_addr(wr_addr), .wr_data(wr_data),
        .wr_ack(wr_ack), .wr_busy(wr_busy),
        .m_axi_awid(m_axi_awid),
        .m_axi_awaddr(m_axi_awaddr), .m_axi_awlen(m_axi_awlen),
        .m_axi_awsize(m_axi_awsize), .m_axi_awburst(m_axi_awburst),
        .m_axi_awlock(m_axi_awlock), .m_axi_awcache(m_axi_awcache),
        .m_axi_awprot(m_axi_awprot),
        .m_axi_awvalid(m_axi_awvalid), .m_axi_awready(m_axi_awready),
        .m_axi_wdata(m_axi_wdata), .m_axi_wstrb(m_axi_wstrb),
        .m_axi_wlast(m_axi_wlast), .m_axi_wvalid(m_axi_wvalid),
        .m_axi_wready(m_axi_wready),
        .m_axi_bid(m_axi_bid), .m_axi_bresp(m_axi_bresp),
        .m_axi_bvalid(m_axi_bvalid), .m_axi_bready(m_axi_bready)
    );

    always @(posedge clk) begin
        if (!rstn) begin
            wr_req   <= 1'b0;
            wr_addr  <= 32'd0;
            wr_data  <= 32'd0;
            buf_valid<= 1'b0;
            buf_addr <= 21'd0;
            buf_data <= 24'd0;
            issued   <= 1'b0;
            drawn    <= 1'b0;
            done_r   <= 1'b0;
        end else begin
            wr_req <= 1'b0;

            // ---- 新命令：清完成态（line_draw 只在 IDLE 接受 start；忙时 start 被无视）----
            //   ★ 必须 else-if：否则同拍「上一线的 drawn 还是 1」会把 done_r 又置回去
            //     （2026-10-30 深夜仿真抓到：新线刚起 i=2 时 DONE/ BUSY 同 1）。
            if (cmd_start && !ld_busy) begin
                done_r <= 1'b0;
                drawn  <= 1'b0;
            end else if (ld_done) begin
                drawn <= 1'b1;
            end

            // ---- 画线核 → 缓冲 ----
            if (ld_valid && ld_ready) begin
                buf_valid <= 1'b1;
                buf_addr  <= ld_addr;
                buf_data  <= ld_data;
            end

            // ---- 缓冲 → 写主（一次一笔，等 B 响应再发下一笔）----
            if (buf_valid && !wr_busy && !issued) begin
                wr_req    <= 1'b1;
                wr_addr   <= px_addr;
                wr_data   <= px_data;
                buf_valid <= 1'b0;
                issued    <= 1'b1;
            end

            // ---- 完成态 ----
            if (wr_ack)
                issued <= 1'b0;
            // 「最后一点已交出」且「缓冲空、无在途、画线核空闲、本拍没有正在捕获的像素」
            if (!(cmd_start && !ld_busy) && (drawn || ld_done) &&
                !buf_valid && !issued && !ld_busy && !(ld_valid && ld_ready))
                done_r <= 1'b1;
        end
    end

    assign cmd_busy = ld_busy || buf_valid || issued;
    assign cmd_done = done_r;

endmodule
