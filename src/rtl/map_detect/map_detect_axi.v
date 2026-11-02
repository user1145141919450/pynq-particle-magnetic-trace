// ============================================================================
// map_detect_axi.v —— 差距 1（PL 检测）的 AXI4-Lite 封装（v1：掩码+形态学+CCL）
//
// 骨架照抄 pe_axi.v（同一写通道/两相读通道）。
//
// 数据通路：vision_core 的 map 写口（wr_en/wr_addr/wr_data）在 BD 里**并联**到
// 本模块 tap_*；本模块内部有一份与 map_ram 同口径的 5760x32 副本，帧末处理。
// 因此 tap 是"只读观察"，不改采集链。
//
// 寄存器图（字地址 = addr[11:2]）：
//   0x000 W CTRL   [0]=en（0=核心保持复位） [1]=soft_rst（电平，1 有效）
//                  [2]=clr_done（W1C）
//   0x000 R STATUS [0]=busy [1]=done_sticky [2]=ovf [3]=en [15:8]=n_cand
//   0x004 R SEQ    frame_seq（已处理帧数）
//   0x008 R MEDVHI {16'd0, vhi[7:0], med[7:0]}
//   0x00C R CNT_RED
//   0x010 R CNT_BLUE
//   0x014 R CNT_BLACK
//   0x018 R N_CAND {24'd0,label_ovf,cand_ovf,n_cand[5:0]}
//   0x01C R VERSION（0x0002_0000 = v1，含形态学+CCL）
//   0x020 W CAND_IDX  [5:0] 选候选（读 0x024/0x028 前先写）
//   0x024 R CAND_W0   cand_data[31:0]
//   0x028 R CAND_W1   {7'd0, cand_data[56:32]}
//        cand_data = {cls[1:0],area[13:0],sx[20:0],sy[19:0]}
//
// 地址：BD 里分配 0x40002000（4KB）。
// ============================================================================
`timescale 1ns / 1ps

module map_detect_axi (
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 100000000, PHASE 0.0" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    input  wire        s_axi_aresetn,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi, PROTOCOL AXI4LITE, ADDR_WIDTH 12, DATA_WIDTH 32, READ_WRITE_MODE READ_WRITE, HAS_BURST 0, HAS_LOCK 0, HAS_PROT 1, HAS_CACHE 0, HAS_QOS 0, HAS_REGION 0, FREQ_HZ 100000000, ID_WIDTH 0, AWUSER_WIDTH 0, ARUSER_WIDTH 0, WUSER_WIDTH 0, RUSER_WIDTH 0, BUSER_WIDTH 0, SUPPORTS_NARROW_BURST 0, MAX_BURST_LENGTH 1, NUM_READ_OUTSTANDING 2, NUM_WRITE_OUTSTANDING 2" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWADDR" *)    input  wire [11:0] s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWPROT" *)    input  wire [2:0]  s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWVALID" *)   input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi AWREADY" *)   output reg         s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WDATA" *)     input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WSTRB" *)     input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WVALID" *)    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi WREADY" *)    output reg         s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BRESP" *)     output reg  [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BVALID" *)    output reg         s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi BREADY" *)    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARADDR" *)    input  wire [11:0] s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARPROT" *)    input  wire [2:0]  s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARVALID" *)   input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi ARREADY" *)   output reg         s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)     output reg  [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)     output reg  [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)    output reg         s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)    input  wire        s_axi_rready,

    // ---- vision_core map 写口抽头（BD 中和 map_ram 的写口并联）----
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME tap, FREQ_HZ 100000000, PHASE 0.0" *)
    input  wire        tap_wr_en,
    input  wire [14:0] tap_wr_addr,
    input  wire [31:0] tap_wr_data,

    // ---- 调试：当前帧序号（给 ILA/诊断用；不用可悬空）----
    output wire [31:0] dbg_frame_seq
);

    reg [31:0] r_ctrl;
    reg [5:0]  r_cand_idx;
    reg        done_sticky;
    reg        core_rstn;
    reg        wr_clr_done;   // CTRL[2] W1C（写通道里产生）

    wire        core_busy, core_done, core_ovf;
    wire [7:0]  med, vhi;
    wire [15:0] cnt_red, cnt_blue, cnt_black;
    wire [31:0] frame_seq;
    wire [5:0]  core_n_cand;
    wire        core_cand_ovf, core_label_ovf;
    wire [56:0] core_cand_data;

    // en=0 或 soft_rst=1 时核心保持复位
    always @(posedge s_axi_aclk)
        core_rstn <= s_axi_aresetn && r_ctrl[0] && !r_ctrl[1];

    map_detect_core u_core (
        .clk(s_axi_aclk),
        .rstn(core_rstn),
        .wr_en(tap_wr_en),
        .wr_addr(tap_wr_addr),
        .wr_data(tap_wr_data),
        .med(med),
        .vhi(vhi),
        .cnt_red(cnt_red),
        .cnt_blue(cnt_blue),
        .cnt_black(cnt_black),
        .frame_seq(frame_seq),
        .busy(core_busy),
        .done(core_done),
        .ovf(core_ovf),
        .n_cand(core_n_cand),
        .cand_ovf(core_cand_ovf),
        .label_ovf(core_label_ovf),
        .cand_addr(r_cand_idx),
        .cand_data(core_cand_data),
        .dbg_we(),
        .dbg_addr(),
        .dbg_data()
    );

    assign dbg_frame_seq = frame_seq;

    // done 粘滞位（CTRL[2] W1C）
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn)
            done_sticky <= 1'b0;
        else if (core_done)
            done_sticky <= 1'b1;
        else if (wr_clr_done)
            done_sticky <= 1'b0;
    end

    // ---------------- 写通道（照抄 pe_axi）----------------
    reg aw_lat, w_lat;
    reg [11:0] aw_addr_l;
    reg [31:0] w_data_l;
    wire wr_go = ((s_axi_awvalid && s_axi_awready) || aw_lat) &&
                 ((s_axi_wvalid  && s_axi_wready)  || w_lat) && !s_axi_bvalid;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_awready <= 1'b0; s_axi_wready <= 1'b0;
            s_axi_bvalid <= 1'b0; s_axi_bresp <= 2'b00;
            aw_lat <= 1'b0; w_lat <= 1'b0;
            r_ctrl <= 32'd0;
            r_cand_idx <= 6'd0;
            wr_clr_done <= 1'b0;
        end else begin
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
            wr_clr_done <= 1'b0;
            if (s_axi_awvalid && !aw_lat) begin
                s_axi_awready <= 1'b1;
                aw_addr_l <= s_axi_awaddr;
                aw_lat <= 1'b1;
            end
            if (s_axi_wvalid && !w_lat) begin
                s_axi_wready <= 1'b1;
                w_data_l <= s_axi_wdata;
                w_lat <= 1'b1;
            end
            if (wr_go) begin
                case (aw_addr_l[7:2])
                6'h00: begin
                    // ★ 脉冲副作用一律数据门控（硅级写通道每笔会执行两次的教训）
                    r_ctrl <= w_data_l;
                    if (w_data_l[2]) wr_clr_done <= 1'b1;
                end
                6'h08: r_cand_idx <= w_data_l[5:0];   // 0x020 选候选
                default: ;
                endcase
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;
                aw_lat <= 1'b0;
                w_lat  <= 1'b0;
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
        end
    end

    // ---------------- 读通道（两相）----------------
    reg [11:0] ar_addr_l;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_arready <= 1'b0; s_axi_rvalid <= 1'b0;
            s_axi_rresp <= 2'b00; s_axi_rdata <= 32'd0;
            ar_addr_l <= 12'd0;
        end else begin
            if (s_axi_arvalid && !s_axi_rvalid && !s_axi_arready) begin
                s_axi_arready <= 1'b1;
                ar_addr_l <= s_axi_araddr;
            end else if (s_axi_arready && !s_axi_rvalid) begin
                s_axi_arready <= 1'b0;
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;
                case (ar_addr_l[7:2])
                6'h00: s_axi_rdata <= {28'd0,
                                       r_ctrl[0], core_ovf, done_sticky, core_busy};
                6'h01: s_axi_rdata <= frame_seq;
                6'h02: s_axi_rdata <= {16'd0, vhi, med};
                6'h03: s_axi_rdata <= {16'd0, cnt_red};
                6'h04: s_axi_rdata <= {16'd0, cnt_blue};
                6'h05: s_axi_rdata <= {16'd0, cnt_black};
                6'h06: s_axi_rdata <= {24'd0, core_label_ovf,
                                       core_cand_ovf, core_n_cand};  // N_CAND
                6'h07: s_axi_rdata <= 32'h0002_0000;      // VERSION v1
                6'h08: s_axi_rdata <= {26'd0, r_cand_idx};      // CAND_IDX 回读
                6'h09: s_axi_rdata <= core_cand_data[31:0];     // CAND_W0
                6'h0A: s_axi_rdata <= {7'd0, core_cand_data[56:32]}; // CAND_W1
                default: s_axi_rdata <= 32'h0;
                endcase
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule
