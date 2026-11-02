`timescale 1ns / 1ps
// ============================================================================
// axi_wr_master.v —— 极简 AXI4 写主（只做单笔 32 位写，够画线器用）
//
// 用途：line_draw 画出的像素（24 位 RGB）打包成 32 位字，写到 DDR framebuffer。
//   每笔 = 一次 AXI 写事务（AW+W+B），100MHz 下一笔约 5~10 拍（取决于互联/HP 端口）。
//
// ★ 极简设计（2026-10-30）：
//   · 只用**写通道**（AW/W/B），不做读通道、不做突发（awlen=0）
//   · 一笔写完等 B 响应再接受下一笔
//   · ID 固定 0（vivado 要求 AXI4 接口有 ID 线；这里全 0，bresp 不看 bid）
//   · 不做 outstanding（简单可靠，慢一点但不会出错）
//
// ★★ v2（2026-10-30 深夜）：补齐 AXI4 写通道的**全部标准信号**
//   （awid/awlock/awcache/awprot/bid）—— BD 里以 X_INTERFACE_INFO 声明成
//   aximm 接口时，缺这些端口接口不完整，interconnect 连不上。
// ============================================================================
module axi_wr_master #(
    parameter integer AW  = 32,
    parameter integer DW  = 32,
    parameter integer IDW = 6
)(
    input  wire            clk,
    input  wire            rstn,

    // ---- 用户侧（画线器）----
    input  wire            wr_req,      // 单拍：请求写一笔
    input  wire [AW-1:0]   wr_addr,     // 目标地址（字节地址，4 字节对齐）
    input  wire [DW-1:0]   wr_data,     // 写入数据
    output reg             wr_ack,      // 单拍：这笔写完成（B 响应）
    output reg             wr_busy,     // 有未完成事务

    // ---- AXI4 写地址通道 ----
    output reg  [IDW-1:0]  m_axi_awid,
    output reg  [AW-1:0]   m_axi_awaddr,
    output reg  [7:0]      m_axi_awlen,
    output reg  [2:0]      m_axi_awsize,
    output reg  [1:0]      m_axi_awburst,
    output wire            m_axi_awlock,   // AXI4：1 位，恒 0（普通访问）
    output wire [3:0]      m_axi_awcache,  // 恒 0（设备/非缓存语义由互联定）
    output wire [2:0]      m_axi_awprot,
    output reg             m_axi_awvalid,
    input  wire            m_axi_awready,

    // ---- AXI4 写数据通道 ----
    output reg  [DW-1:0]     m_axi_wdata,
    output reg  [(DW/8)-1:0] m_axi_wstrb,
    output reg               m_axi_wlast,
    output reg               m_axi_wvalid,
    input  wire              m_axi_wready,

    // ---- AXI4 写响应通道 ----
    input  wire [IDW-1:0]  m_axi_bid,
    input  wire [1:0]      m_axi_bresp,
    input  wire            m_axi_bvalid,
    output reg             m_axi_bready
);

    assign m_axi_awlock  = 1'b0;
    assign m_axi_awcache = 4'b0000;
    assign m_axi_awprot  = 3'b000;

    localparam S_IDLE = 2'd0,
               S_RESP = 2'd1;

    reg [1:0] st;

    always @(posedge clk) begin
        if (!rstn) begin
            st <= S_IDLE;
            wr_ack <= 1'b0; wr_busy <= 1'b0;
            m_axi_awid    <= {IDW{1'b0}};
            m_axi_awaddr  <= {AW{1'b0}};
            m_axi_awlen   <= 8'd0;
            m_axi_awsize  <= 3'd2;          // 4 字节
            m_axi_awburst <= 2'b01;         // INCR
            m_axi_awvalid <= 1'b0;
            m_axi_wdata   <= {DW{1'b0}};
            m_axi_wstrb   <= {(DW/8){1'b1}};
            m_axi_wlast   <= 1'b1;
            m_axi_wvalid  <= 1'b0;
            m_axi_bready  <= 1'b0;
        end else begin
            wr_ack <= 1'b0;
            case (st)
                S_IDLE: begin
                    m_axi_awvalid <= 1'b0;
                    m_axi_wvalid  <= 1'b0;
                    m_axi_bready  <= 1'b0;
                    wr_busy <= 1'b0;
                    if (wr_req) begin
                        m_axi_awid    <= {IDW{1'b0}};
                        m_axi_awaddr  <= wr_addr;
                        m_axi_awlen   <= 8'd0;
                        m_axi_awsize  <= 3'd2;
                        m_axi_awburst <= 2'b01;
                        m_axi_awvalid <= 1'b1;
                        m_axi_wdata   <= wr_data;
                        m_axi_wstrb   <= {(DW/8){1'b1}};
                        m_axi_wlast   <= 1'b1;
                        m_axi_wvalid  <= 1'b1;
                        m_axi_bready  <= 1'b1;
                        wr_busy <= 1'b1;
                        st <= S_RESP;
                    end
                end
                S_RESP: begin
                    // AW 和 W 各自握手
                    if (m_axi_awvalid && m_axi_awready)
                        m_axi_awvalid <= 1'b0;
                    if (m_axi_wvalid && m_axi_wready)
                        m_axi_wvalid <= 1'b0;
                    // B 响应（等 AW/W 都发出去后才会来；只看 bvalid）
                    if (m_axi_bvalid && m_axi_bready) begin
                        m_axi_bready <= 1'b0;
                        wr_ack  <= 1'b1;
                        wr_busy <= 1'b0;
                        st <= S_IDLE;
                    end
                end
                default: st <= S_IDLE;
            endcase
        end
    end

endmodule
