// ============================================================================
// frame_pump.v —— PL 帧泵 v1（2026-10-31 下午冲刺）
//
// 【解决什么】PS 每帧做"透视矫正 warpPerspective(~100ms) + 缩放打包(~50ms)"，
//   占 340ms 帧周期的 44%。本模块把这条链整段搬进 PL。
//
// 【数据流】
//   PS  ：相机帧 → 画叠加 → 打包成 32 位(与 framebuffer 同口径 (R<<16)|(B<<8)|G)
//         → 写进 DDR 源缓冲（1024x720x4 = 2.95MB，numpy 三笔切片 ~15ms）
//         → 再写一张行表（720 x 16B）
//   PL  ：逐行读行表 → 逐像素按 (sx0+dsx*x, sy0+dsy*x) gather（最近邻）
//         → 每 16 像素一次 AXI 突发写回 framebuffer
//
// 【为什么行表而不是逐像素 LUT】LUT 3.7MB/帧额外带宽；行表只 11.5KB，
//   行内线性内插近似透视（我们的场区透视很小，误差 <2px）。
//
// 【寄存器（AXI4-Lite，64B）】
//   0x00 SRC_BASE  源图物理地址（32 位/像素）
//   0x04 DST_BASE  framebuffer 物理地址（32 位/像素）
//   0x08 SRC_W     源宽（像素）
//   0x0C SRC_H     源高
//   0x10 DST_W     输出宽（16 的倍数，=1280）
//   0x14 DST_H     输出高（=720）
//   0x18 TBL_BASE  行表物理地址（DST_H x 16B）
//   0x1C CTRL      [0]=START（自清）
//   0x20 STATUS    [0]=BUSY [1]=DONE（粘滞，下次 START 清）
//   0x24 FRAMES    完成帧数
//
// 【行表】每行 16B，Q16.8 有符号 32 位：sx0, sy0, dsx, dsy
//
// ★ v1：Nearest；单笔读流水（一进一出）；写 16 拍突发。
//   板上估算：1280x720 ≈ 92 万像素 × ~6 拍 ≈ 55ms（PS 现状 150ms）。
// ============================================================================
`timescale 1ns / 1ps

module frame_pump #(
    parameter integer IDW = 6,
    // 源图行距 = SRC_W*4，必须是 2 的幂（现场 1024 → 1<<12）；台架用小图会覆盖
    parameter integer SRC_ROW_SHIFT = 12
)(
    input  wire        clk,
    input  wire        rstn,

    // ---------------- AXI4-Lite 控制口 ----------------
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi, PROTOCOL AXI4LITE, ADDR_WIDTH 6, DATA_WIDTH 32, READ_WRITE_MODE READ_WRITE, HAS_BURST 0, HAS_LOCK 0, HAS_PROT 1, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, FREQ_HZ 100000000, ID_WIDTH 0, AWUSER_WIDTH 0, ARUSER_WIDTH 0, WUSER_WIDTH 0, RUSER_WIDTH 0, BUSER_WIDTH 0, SUPPORTS_NARROW_BURST 0, MAX_BURST_LENGTH 1, NUM_READ_OUTSTANDING 2, NUM_WRITE_OUTSTANDING 2" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR" *)    input  wire [5:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWPROT" *)    input  wire [2:0]  s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)   input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)   output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)     input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)     input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)     output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)    input  wire [5:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARPROT" *)    input  wire [2:0]  s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)   input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)   output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)     output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)     output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)    input  wire        s_axi_rready,

    // ---------------- AXI4 读（源图 / 行表）----------------
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME m_rd, PROTOCOL AXI4, ADDR_WIDTH 32, DATA_WIDTH 32, READ_WRITE_MODE READ_ONLY, HAS_BURST 1, HAS_LOCK 1, HAS_PROT 1, HAS_CACHE 1, HAS_QOS 0, HAS_REGION 0, FREQ_HZ 100000000, ID_WIDTH 6, AWUSER_WIDTH 0, WUSER_WIDTH 0, BUSER_WIDTH 0, SUPPORTS_NARROW_BURST 0, MAX_BURST_LENGTH 16, NUM_READ_OUTSTANDING 4, NUM_WRITE_OUTSTANDING 0" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARID" *)       output wire [IDW-1:0] m_rd_arid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARADDR" *)     output reg  [31:0] m_rd_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARLEN" *)      output reg  [7:0]  m_rd_arlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARSIZE" *)     output reg  [2:0]  m_rd_arsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARBURST" *)    output reg  [1:0]  m_rd_arburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARLOCK" *)     output wire        m_rd_arlock,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARCACHE" *)    output wire [3:0]  m_rd_arcache,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARPROT" *)     output wire [2:0]  m_rd_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARVALID" *)    output reg         m_rd_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd ARREADY" *)    input  wire        m_rd_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd RID" *)        input  wire [IDW-1:0] m_rd_rid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd RDATA" *)      input  wire [31:0] m_rd_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd RRESP" *)      input  wire [1:0]  m_rd_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd RLAST" *)      input  wire        m_rd_rlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd RVALID" *)     input  wire        m_rd_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_rd RREADY" *)     output wire        m_rd_rready,

    // ---------------- AXI4 写（framebuffer）----------------
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME m_wr, PROTOCOL AXI4, ADDR_WIDTH 32, DATA_WIDTH 32, READ_WRITE_MODE WRITE_ONLY, HAS_BURST 1, HAS_LOCK 1, HAS_PROT 1, HAS_CACHE 1, HAS_QOS 0, HAS_REGION 0, FREQ_HZ 100000000, ID_WIDTH 6, AWUSER_WIDTH 0, WUSER_WIDTH 0, BUSER_WIDTH 0, SUPPORTS_NARROW_BURST 0, MAX_BURST_LENGTH 16, NUM_READ_OUTSTANDING 0, NUM_WRITE_OUTSTANDING 8" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWID" *)       output wire [IDW-1:0] m_wr_awid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWADDR" *)     output reg  [31:0] m_wr_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWLEN" *)      output reg  [7:0]  m_wr_awlen,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWSIZE" *)     output reg  [2:0]  m_wr_awsize,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWBURST" *)    output reg  [1:0]  m_wr_awburst,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWLOCK" *)     output wire        m_wr_awlock,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWCACHE" *)    output wire [3:0]  m_wr_awcache,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWPROT" *)     output wire [2:0]  m_wr_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWVALID" *)    output reg         m_wr_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr AWREADY" *)    input  wire        m_wr_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm[1.0] m_wr WDATA" *)     output reg  [31:0] m_wr_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr WSTRB" *)      output reg  [3:0]  m_wr_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr WLAST" *)      output reg         m_wr_wlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr WVALID" *)     output reg         m_wr_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr WREADY" *)     input  wire        m_wr_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr BID" *)        input  wire [IDW-1:0] m_wr_bid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr BRESP" *)      input  wire [1:0]  m_wr_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr BVALID" *)     input  wire        m_wr_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 m_wr BREADY" *)     output reg         m_wr_bready
);

    assign m_rd_arid    = {IDW{1'b0}};
    assign m_rd_arlock  = 1'b0;
    assign m_rd_arcache = 4'b0000;
    assign m_rd_arprot  = 3'b000;
    assign m_rd_rready  = 1'b1;
    assign m_wr_awid    = {IDW{1'b0}};
    assign m_wr_awlock  = 1'b0;
    assign m_wr_awcache = 4'b0000;
    assign m_wr_awprot  = 3'b000;

    // ------------------------------------------------------------------
    // AXI4-Lite 控制口
    // ------------------------------------------------------------------
    reg [31:0] r_src, r_dst, r_tbl;
    reg [15:0] r_sw, r_sh, r_dw, r_dh;
    reg        r_start;
    reg        busy, done_r;
    reg [31:0] n_frames;

    assign s_axi_awready = 1'b1;
    assign s_axi_wready  = 1'b1;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_arready = 1'b1;
    assign s_axi_rresp   = 2'b00;

    reg        bvalid_r, rvalid_r;
    reg [31:0] rdata_r;
    assign s_axi_bvalid = bvalid_r;
    assign s_axi_rvalid = rvalid_r;
    assign s_axi_rdata  = rdata_r;

    always @(posedge clk) begin
        if (!rstn) begin
            r_src <= 32'd0; r_dst <= 32'd0; r_tbl <= 32'd0;
            r_sw <= 16'd1024; r_sh <= 16'd720;
            r_dw <= 16'd1280; r_dh <= 16'd720;
            r_start <= 1'b0;
            bvalid_r <= 1'b0; rvalid_r <= 1'b0; rdata_r <= 32'd0;
        end else begin
            if (bvalid_r && s_axi_bready) bvalid_r <= 1'b0;
            if (rvalid_r && s_axi_rready) rvalid_r <= 1'b0;
            if (s_axi_awvalid && s_axi_wvalid) begin
                case (s_axi_awaddr[5:2])
                    4'd0: r_src <= s_axi_wdata;
                    4'd1: r_dst <= s_axi_wdata;
                    4'd2: r_sw  <= s_axi_wdata[15:0];
                    4'd3: r_sh  <= s_axi_wdata[15:0];
                    4'd4: r_dw  <= s_axi_wdata[15:0];
                    4'd5: r_dh  <= s_axi_wdata[15:0];
                    4'd6: r_tbl <= s_axi_wdata;
                    4'd7: r_start <= s_axi_wdata[0];
                    default: ;
                endcase
                bvalid_r <= 1'b1;
            end else if (r_start) begin
                r_start <= 1'b0;
            end
            if (s_axi_arvalid) begin
                case (s_axi_araddr[5:2])
                    4'd0: rdata_r <= r_src;
                    4'd1: rdata_r <= r_dst;
                    4'd2: rdata_r <= {16'd0, r_sw};
                    4'd3: rdata_r <= {16'd0, r_sh};
                    4'd4: rdata_r <= {16'd0, r_dw};
                    4'd5: rdata_r <= {16'd0, r_dh};
                    4'd6: rdata_r <= r_tbl;
                    4'd8: rdata_r <= {30'd0, done_r, busy};
                    4'd9: rdata_r <= n_frames;
                    default: rdata_r <= 32'd0;
                endcase
                rvalid_r <= 1'b1;
            end
        end
    end

    // ------------------------------------------------------------------
    // gather 引擎
    // ------------------------------------------------------------------
    // ★★ 2026-10-31 v2.1：首版上板 306ms/帧（单笔读流水被 DDR 延迟卡死）。
    //   改为 **16 深 outstanding 读 + 32 深采样 FIFO + 写突发解耦**。
    //   五个坑（都已在仿真里踩过，见 HANDOFF 2026-10-31 傍晚节）：
    //   ① 行表 AR 不能算进像素 outstanding（用 ar_tbl 标志区分，不用状态判断）
    //   ② 写突发首拍必须在 **AW 受理时**才拉 wvalid（S_GAT 拉会被通用握手清掉）
    //   ③ 突发数据用 burst_rp 副本（起突发时 fifo_rp 已经 +16）
    //   ④ case 里必须有 S_WD 分支（数据逻辑在全局块）
    //   ⑤ 仿真模型必须 arready=!busy（TB 已改）
    localparam S_IDLE = 4'd0,
               S_TBL  = 4'd1,
               S_TBLR = 4'd2,
               S_GAT  = 4'd3,
               S_WA   = 4'd4,
               S_WD   = 4'd5,
               S_NROW = 4'd7;

    localparam integer MAXOS = 16;

    reg [3:0]  st;
    reg [15:0] oy, ox;
    reg signed [31:0] sxq, syq, dsxq, dsyq;
    reg [1:0]  tbl_beat;
    reg [31:0] tbl_w0, tbl_w1, tbl_w2;
    reg [4:0]  os_cnt;
    reg        ar_pend;
    reg        ar_tbl;                 // 这笔 AR 是行表读
    reg        os_err;                 // 防御：R 多于 AR（下溢）就置位

    reg [31:0] fifo [0:31];
    reg [5:0]  fifo_wp, fifo_rp;
    reg [5:0]  burst_rp;
    reg [3:0]  wb_i;
    reg [15:0] dst_x;
    reg [31:0] dst_row_base;
    reg        w_inflight;

    wire [5:0] fifo_cnt = fifo_wp - fifo_rp;

    wire signed [31:0] px_sx = sxq >>> 8;
    wire signed [31:0] px_sy = syq >>> 8;
    wire [31:0] px_addr = r_src +
        ({px_sy[19:0], {SRC_ROW_SHIFT{1'b0}}} + {px_sx[19:0], 2'd0});

    always @(posedge clk) begin
        if (!rstn) begin
            st <= S_IDLE; busy <= 1'b0; done_r <= 1'b0; n_frames <= 32'd0;
            oy <= 16'd0; ox <= 16'd0;
            sxq <= 32'sd0; syq <= 32'sd0; dsxq <= 32'sd0; dsyq <= 32'sd0;
            m_rd_arvalid <= 1'b0; m_rd_araddr <= 32'd0;
            m_rd_arlen <= 8'd0; m_rd_arsize <= 3'd2; m_rd_arburst <= 2'b01;
            m_wr_awvalid <= 1'b0; m_wr_wvalid <= 1'b0; m_wr_bready <= 1'b0;
            m_wr_wlast <= 1'b0; m_wr_wstrb <= 4'hF; m_wr_wdata <= 32'd0;
            m_wr_awlen <= 8'd0; m_wr_awsize <= 3'd2; m_wr_awburst <= 2'b01;
            m_wr_awaddr <= 32'd0;
            tbl_beat <= 2'd0; wb_i <= 4'd0;
            tbl_w0 <= 32'd0; tbl_w1 <= 32'd0; tbl_w2 <= 32'd0;
            os_cnt <= 5'd0; ar_pend <= 1'b0; ar_tbl <= 1'b0; os_err <= 1'b0;
            fifo_wp <= 6'd0; fifo_rp <= 6'd0; burst_rp <= 6'd0;
            dst_row_base <= 32'd0; dst_x <= 16'd0; w_inflight <= 1'b0;
        end else begin
            // ============ 全局：AR 受理（握手就撤，像素 AR 才计数/推进）============
            if (m_rd_arvalid && m_rd_arready) begin
                m_rd_arvalid <= 1'b0;
                ar_pend <= 1'b0;
                if (!ar_tbl) begin
                    os_cnt <= os_cnt + 5'd1;
                    sxq <= sxq + dsxq;
                    syq <= syq + dsyq;
                    ox  <= ox + 16'd1;
                end
            end

            // ============ 全局：R → FIFO（行表 4 拍不走这里）============
            if (m_rd_rvalid && st != S_TBLR) begin
                fifo[fifo_wp[4:0]] <= m_rd_rdata;
                fifo_wp <= fifo_wp + 6'd1;
                if (os_cnt != 5'd0)
                    os_cnt <= os_cnt - 5'd1;
                else
                    os_err <= 1'b1;        // 防御：不该发生
            end

            // ============ 全局：写通道 ============
            if (m_wr_awvalid && m_wr_awready) m_wr_awvalid <= 1'b0;
            if (m_wr_wvalid && m_wr_wready) m_wr_wvalid <= 1'b0;
            if (m_wr_bvalid && m_wr_bready) begin
                m_wr_bready <= 1'b0;
                w_inflight <= 1'b0;
            end

            // 写数据：S_WD 期间连续 16 拍
            if (st == S_WD && m_wr_wvalid && m_wr_wready) begin
                if (wb_i == 4'd15) begin
                    m_wr_wvalid <= 1'b0;
                    m_wr_wlast  <= 1'b0;
                    st <= S_GAT;
                end else begin
                    wb_i <= wb_i + 4'd1;
                    m_wr_wdata  <= fifo[burst_rp[4:0] + wb_i + 4'd1];
                    m_wr_wlast  <= (wb_i + 4'd1 == 4'd15);
                    m_wr_wvalid <= 1'b1;
                end
            end

            case (st)
                S_IDLE: begin
                    busy <= 1'b0;
                    if (r_start) begin
                        done_r <= 1'b0;
                        busy <= 1'b1;
                        oy <= 16'd0; ox <= 16'd0;
                        dst_row_base <= r_dst; dst_x <= 16'd0;
                        os_cnt <= 5'd0; ar_pend <= 1'b0; ar_tbl <= 1'b0;
                        os_err <= 1'b0;
                        fifo_wp <= 6'd0; fifo_rp <= 6'd0; burst_rp <= 6'd0;
                        w_inflight <= 1'b0;
                        st <= S_TBL;
                    end
                end

                S_TBL: begin
                    m_rd_araddr  <= r_tbl + (({16'd0, oy}) << 4);
                    m_rd_arlen   <= 8'd3;
                    m_rd_arsize  <= 3'd2;
                    m_rd_arburst <= 2'b01;
                    m_rd_arvalid <= 1'b1;
                    ar_tbl <= 1'b1;
                    tbl_beat <= 2'd0;
                    st <= S_TBLR;
                end
                S_TBLR: begin
                    if (m_rd_rvalid) begin
                        if (tbl_beat == 2'd0) tbl_w0 <= m_rd_rdata;
                        if (tbl_beat == 2'd1) tbl_w1 <= m_rd_rdata;
                        if (tbl_beat == 2'd2) tbl_w2 <= m_rd_rdata;
                        tbl_beat <= tbl_beat + 2'd1;
                        if (tbl_beat == 2'd3) begin
                            sxq  <= $signed(tbl_w0);
                            syq  <= $signed(tbl_w1);
                            dsxq <= $signed(tbl_w2);
                            dsyq <= $signed(m_rd_rdata);
                            ox <= 16'd0;
                            ar_tbl <= 1'b0;
                            st <= S_GAT;
                        end
                    end
                end

                S_GAT: begin
                    // ① 发行像素读
                    if (!ar_pend && os_cnt < MAXOS && ox < r_dw) begin
                        m_rd_araddr  <= px_addr;
                        m_rd_arlen   <= 8'd0;
                        m_rd_arsize  <= 3'd2;
                        m_rd_arburst <= 2'b01;
                        m_rd_arvalid <= 1'b1;
                        ar_tbl <= 1'b0;
                        ar_pend <= 1'b1;
                    end
                    // ② 起写突发（wvalid 留到 AW 受理时才拉）
                    if (!w_inflight && fifo_cnt >= 6'd16) begin
                        m_wr_awaddr  <= dst_row_base + ({16'd0, dst_x} << 2);
                        m_wr_awlen   <= 8'd15;
                        m_wr_awsize  <= 3'd2;
                        m_wr_awburst <= 2'b01;
                        m_wr_awvalid <= 1'b1;
                        m_wr_bready  <= 1'b1;
                        m_wr_wdata   <= fifo[fifo_rp[4:0]];
                        m_wr_wlast   <= 1'b0;
                        m_wr_wvalid  <= 1'b0;
                        burst_rp <= fifo_rp;
                        wb_i <= 4'd0;
                        dst_x <= dst_x + 16'd16;
                        fifo_rp <= fifo_rp + 6'd16;
                        w_inflight <= 1'b1;
                        st <= S_WA;
                    end
                    // ③ 行结束
                    if (ox >= r_dw && os_cnt == 5'd0 && !ar_pend
                            && fifo_cnt == 6'd0 && !w_inflight) begin
                        st <= S_NROW;
                    end
                end

                S_WA: begin
                    if (m_wr_awvalid && m_wr_awready) begin
                        m_wr_awvalid <= 1'b0;
                        m_wr_wvalid  <= 1'b1;      // 首拍现在才发
                        st <= S_WD;
                    end
                end

                // ★ 必须有的占位分支（数据在全局块里送）
                S_WD: begin
                end

                S_NROW: begin
                    if (oy + 16'd1 >= r_dh) begin
                        n_frames <= n_frames + 32'd1;
                        done_r <= 1'b1;
                        busy <= 1'b0;
                        st <= S_IDLE;
                    end else begin
                        oy <= oy + 16'd1;
                        dst_row_base <= dst_row_base + {14'd0, r_dw, 2'd0};
                        dst_x <= 16'd0;
                        tbl_beat <= 2'd0;
                        fifo_rp <= fifo_wp;
                        st <= S_TBL;
                    end
                end
                default: st <= S_IDLE;
            endcase
        end
    end

endmodule
