`timescale 1ns / 1ps
// ============================================================================
// line_ctrl_axi.v —— line_engine 的 AXI4-Lite 寄存器从口（PS 下发画线命令）
//
// 寄存器映射（字节地址，4 字节对齐；本从口占 64 字节）：
//   0x00  X0      [17:0]  起点 x
//   0x04  Y0      [17:0]  起点 y
//   0x08  X1      [17:0]  终点 x
//   0x0C  Y1      [17:0]  终点 y
//   0x10  COLOR   [23:0]  {B,G,R}（与 cv2 BGR 一致；引擎内部换序成 framebuffer 口径）
//   0x14  FB_BASE [31:0]  framebuffer 物理基地址（字节）
//   0x18  CTRL    [0]     START（写 1 启动一条线，自清；忙时写入被忽略）
//   0x1C  STATUS  [0]=BUSY [1]=DONE（粘滞到下一次 START）
//
// ★ 2026-10-30：极简实现，只支持 32 位单拍读写（PS 用 MMIO 写）。
// ★ 2026-10-30 深夜：补 AWPROT/ARPROT 口（BD 里 aximm 接口声明 HAS_PROT 1 时
//   必须有这两根线，否则接口不完整连不上）。
// ============================================================================
module line_ctrl_axi (
    input  wire        clk,
    input  wire        rstn,
    input  wire        awclk,
    input  wire        awrstn,

    // ---- AXI4-Lite 从（S_AXI）----
    input  wire [5:0]  s_axi_awaddr,
    input  wire [2:0]  s_axi_awprot,
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [5:0]  s_axi_araddr,
    input  wire [2:0]  s_axi_arprot,
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    // ---- 到 line_engine ----
    output wire [17:0] cmd_x0, cmd_y0, cmd_x1, cmd_y1,
    output wire [23:0] cmd_color,
    output wire [31:0] fb_base,
    output wire        cmd_start,      // 单拍
    input  wire        engine_busy,
    input  wire        engine_done
);

    // ---- 写通道 ----
    reg [31:0] r_x0, r_y0, r_x1, r_y1, r_color, r_fb;
    reg        r_start;

    always @(posedge awclk) begin
        if (!awrstn) begin
            s_axi_awready <= 1'b1;
            s_axi_wready  <= 1'b1;
            s_axi_bvalid  <= 1'b0;
            s_axi_bresp   <= 2'b00;
            r_x0 <= 32'd0; r_y0 <= 32'd0; r_x1 <= 32'd0; r_y1 <= 32'd0;
            r_color <= 32'd0; r_fb <= 32'd0; r_start <= 1'b0;
        end else begin
            if (s_axi_bvalid && s_axi_bready)
                s_axi_bvalid <= 1'b0;
            if (s_axi_awvalid && s_axi_awready && s_axi_wvalid && s_axi_wready) begin
                case (s_axi_awaddr[5:2])
                    4'd0: r_x0    <= s_axi_wdata;
                    4'd1: r_y0    <= s_axi_wdata;
                    4'd2: r_x1    <= s_axi_wdata;
                    4'd3: r_y1    <= s_axi_wdata;
                    4'd4: r_color <= s_axi_wdata;
                    4'd5: r_fb    <= s_axi_wdata;
                    4'd6: r_start <= s_axi_wdata[0];
                    default: ;
                endcase
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;
            end else if (r_start) begin
                r_start <= 1'b0;      // 单拍：启动后自清
            end
        end
    end

    // ---- 读通道 ----
    always @(posedge awclk) begin
        if (!awrstn) begin
            s_axi_arready <= 1'b1;
            s_axi_rvalid  <= 1'b0;
            s_axi_rdata   <= 32'd0;
            s_axi_rresp   <= 2'b00;
        end else begin
            if (s_axi_rvalid && s_axi_rready)
                s_axi_rvalid <= 1'b0;
            if (s_axi_arvalid && s_axi_arready) begin
                case (s_axi_araddr[5:2])
                    4'd0: s_axi_rdata <= r_x0;
                    4'd1: s_axi_rdata <= r_y0;
                    4'd2: s_axi_rdata <= r_x1;
                    4'd3: s_axi_rdata <= r_y1;
                    4'd4: s_axi_rdata <= r_color;
                    4'd5: s_axi_rdata <= r_fb;
                    4'd7: s_axi_rdata <= {30'd0, engine_done, engine_busy};
                    default: s_axi_rdata <= 32'd0;
                endcase
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;
            end
        end
    end

    // ---- 命令同步到 engine 时钟域（同一 100MHz，直接给）----
    assign cmd_x0   = r_x0[17:0];
    assign cmd_y0   = r_y0[17:0];
    assign cmd_x1   = r_x1[17:0];
    assign cmd_y1   = r_y1[17:0];
    assign cmd_color= r_color[23:0];
    assign fb_base  = r_fb;
    assign cmd_start= r_start;

    // 未用的 PROT 线：显式忽略（防综合 warning 噪音）
    wire _unused = &{1'b0, s_axi_awprot, s_axi_arprot};

endmodule
