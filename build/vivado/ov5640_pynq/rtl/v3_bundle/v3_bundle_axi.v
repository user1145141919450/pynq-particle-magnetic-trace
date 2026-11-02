// ============================================================================
// v3_bundle_axi.v —— V3 并行轨迹束的 **AXI4-Lite 封装**（PS 写参数 / 读结果）
//
// 为什么要有它（2026-09-27 用户点破：仿真过 ≠ 上板过）：
//   `traj_bundle` 只有裸寄存器/写口，PS 摸不到；套一层 AXI4-Lite 才能挂进 BD、
//   被 PYNQ 用 MMIO 读写 —— 这样才谈得上"实测与参考比对"（比赛硬要求）。
//
// 寄存器图（**12 位字节地址 = 4KB**；字地址 = addr[11:2]）：
//   0x000 CTRL    [0]=start(自清)
//   0x004 STATUS  [0]=done [1]=busy [15:8]=一簇耗时周期数低 8 位
//   0x010 X0(Q10.8)   0x014 Y0      0x018 MAX_STEPS   0x01C MAX_BOUNCE
//   0x020 BALL_R      0x024 FIELD_W 0x028 FIELD_H     0x02C {n_holes[1:0],side}
//   0x100 + i*12 (i=0..8)   块表 i：+0=ang[22:0]  +4={x0[17:0],y0[17:0]}
//                                     +8={x1[17:0],y1[17:0]}
//   0x200 + h*8  (h=0..3)   洞表 h：+0={w[17:0],c[17:0]}  +4={en,edge[1:0],target}
//   0x300 + i*8  (i=0..16)  每路初速度：+0=vx[19:0]  +4=vy[19:0]（有符号）
//   0x400 + i*8  (i=0..16)  **结果读回**：+0={res[1:0], ex[17:0], ey[11:0]}
//                                        +4={ey[17:12], n_step[11:0], n_bounce[3:0]}
// ============================================================================
`timescale 1ns / 1ps

module v3_bundle_axi #(
    parameter integer NLANES = 17,
    parameter integer NBLK   = 9
)(
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aclk, ASSOCIATED_BUSIF s_axi, ASSOCIATED_RESET s_axi_aresetn, FREQ_HZ 100000000, PHASE 0.0" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME s_axi_aresetn, POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    input  wire        s_axi_aresetn,
    // ★★ 必须把这些离散信号声明成 **AXI 接口**（X_INTERFACE 属性），否则 Vivado 只看到
    //    一堆单根线：`connect_bd_intf_net .../M11_AXI` 连不上、地址段也不存在
    //    （实测报 "A peripheral must be specified when '-offset and -range' option exists"）。
    //    属性块的骨架照 Xilinx 标准的 aximm 接口写。
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
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)    input  wire        s_axi_rready
);

    // ---------------- 参数寄存器 ----------------
    // ★★ MAX_FANOUT：这几个参数寄存器每个都要扇出到 **17 路的比较器**（场宽/球径/步数…），
    //    实装布线后关键路径是 r_fw_reg -> g_lane[12].goal_r_reg，Slack -1.028ns，
    //    其中**布线 6.42ns / 逻辑 4.46ns** —— 瓶颈是那根长线不是逻辑。让工具自动
    //    复制寄存器（replication）把走线缩短，一行属性即可。
    reg [31:0] r_ctrl, r_status;
    (* MAX_FANOUT = 16 *) reg [31:0] r_x0, r_y0;
    (* MAX_FANOUT = 16 *) reg [31:0] r_steps, r_bounce;
    (* MAX_FANOUT = 16 *) reg [31:0] r_ballr, r_fw, r_fh, r_misc;
    // ★★ 2026-10-19 布置 PLACE（PL 原生）：AABB 障碍 + 方形传送门（0x030~0x058）
    reg [31:0] r_obs_en, r_obs_x0, r_obs_y0, r_obs_x1, r_obs_y1;
    reg [31:0] r_port_en, r_port_x0, r_port_y0, r_port_x1, r_port_y1, r_port_r;
    // ★ 2026-10-19 旋转矩形障碍：中心/扩张半宽高/cos(-ang)/sin(-ang) Q2.20
    reg [31:0] r_obs_cx, r_obs_cy, r_obs_ehw, r_obs_ehh, r_obs_ca, r_obs_sa;
    // ★★ 2026-10-27 多件：1/2 号障碍（0x074-0x0CB）+ 1 号门对（0x0CC-0x0DF）
    reg [31:0] r_obs1_en, r_obs1_x0, r_obs1_y0, r_obs1_x1, r_obs1_y1;
    reg [31:0] r_obs1_cx, r_obs1_cy, r_obs1_ehw, r_obs1_ehh, r_obs1_ca, r_obs1_sa;
    reg [31:0] r_obs2_en, r_obs2_x0, r_obs2_y0, r_obs2_x1, r_obs2_y1;
    reg [31:0] r_obs2_cx, r_obs2_cy, r_obs2_ehw, r_obs2_ehh, r_obs2_ca, r_obs2_sa;
    reg [31:0] r_port1_en, r_port1_x0, r_port1_y0, r_port1_x1, r_port1_y1;
    reg [22:0] b_ang  [0:NBLK-1];
    reg [17:0] b_x0   [0:NBLK-1];
    reg [17:0] b_y0   [0:NBLK-1];
    reg [17:0] b_x1   [0:NBLK-1];
    reg [17:0] b_y1   [0:NBLK-1];
    reg [17:0] h_c    [0:3];
    reg [17:0] h_w    [0:3];
    reg        h_en   [0:3];
    reg [1:0]  h_edge [0:3];
    reg        h_tgt  [0:3];
    reg [19:0] l_vx   [0:NLANES-1];
    reg [19:0] l_vy   [0:NLANES-1];
    reg [53:0] lane_res [0:NLANES-1];

    // ---------------- 拼表 -> DUT ----------------
    reg  start_pulse;
    wire bundle_done;
    reg [NBLK*95-1:0]   blk_tab;
    reg [4*40-1:0]      hole_tab;
    reg [NLANES*40-1:0] lane_v_tab;
    integer i;
    always @(*) begin
        blk_tab = 0;
        hole_tab = 0;
        lane_v_tab = 0;
        for (i = 0; i < NBLK; i = i + 1)
            blk_tab[i*95 +: 95] = {b_ang[i], b_x0[i], b_y0[i], b_x1[i], b_y1[i]};
        for (i = 0; i < 4; i = i + 1)
            hole_tab[i*40 +: 40] = {h_en[i], h_edge[i], h_tgt[i], h_w[i], h_c[i]};
        for (i = 0; i < NLANES; i = i + 1)
            lane_v_tab[i*40 +: 40] = {l_vx[i], l_vy[i]};
    end

    wire        res_we;
    wire [4:0]  res_addr;
    wire [53:0] res_data;
    wire        pt_we;
    wire [4:0]  pt_lane;
    wire [7:0]  pt_idx;
    wire [35:0] pt_data;

    traj_bundle #(.NLANES(NLANES), .NBLK(NBLK)) u_bundle (
        .clk(s_axi_aclk), .rstn(s_axi_aresetn),
        .start(start_pulse),
        .x0(r_x0[17:0]), .y0(r_y0[17:0]),
        .max_steps(r_steps[11:0]), .max_bounce(r_bounce[3:0]),
        .ball_r(r_ballr[17:0]), .field_w(r_fw[17:0]), .field_h(r_fh[17:0]),
        .side(r_misc[0]), .n_holes(r_misc[3:2]),
        .obs_en(r_obs_en[0]),
        .obs_x0(r_obs_x0[17:0]), .obs_y0(r_obs_y0[17:0]),
        .obs_x1(r_obs_x1[17:0]), .obs_y1(r_obs_y1[17:0]),
        .obs_cx(r_obs_cx[17:0]), .obs_cy(r_obs_cy[17:0]),
        .obs_ehw(r_obs_ehw[17:0]), .obs_ehh(r_obs_ehh[17:0]),
        .obs_ca(r_obs_ca[22:0]), .obs_sa(r_obs_sa[22:0]),
        .obs1_en(r_obs1_en[0]), .obs2_en(r_obs2_en[0]),
        .obs1_x0(r_obs1_x0[17:0]), .obs1_y0(r_obs1_y0[17:0]),
        .obs1_x1(r_obs1_x1[17:0]), .obs1_y1(r_obs1_y1[17:0]),
        .obs1_cx(r_obs1_cx[17:0]), .obs1_cy(r_obs1_cy[17:0]),
        .obs1_ehw(r_obs1_ehw[17:0]), .obs1_ehh(r_obs1_ehh[17:0]),
        .obs1_ca(r_obs1_ca[22:0]), .obs1_sa(r_obs1_sa[22:0]),
        .obs2_x0(r_obs2_x0[17:0]), .obs2_y0(r_obs2_y0[17:0]),
        .obs2_x1(r_obs2_x1[17:0]), .obs2_y1(r_obs2_y1[17:0]),
        .obs2_cx(r_obs2_cx[17:0]), .obs2_cy(r_obs2_cy[17:0]),
        .obs2_ehw(r_obs2_ehw[17:0]), .obs2_ehh(r_obs2_ehh[17:0]),
        .obs2_ca(r_obs2_ca[22:0]), .obs2_sa(r_obs2_sa[22:0]),
        .port_en(r_port_en[0]),
        .port_x0(r_port_x0[17:0]), .port_y0(r_port_y0[17:0]),
        .port_x1(r_port_x1[17:0]), .port_y1(r_port_y1[17:0]),
        .port_r(r_port_r[17:0]),
        .port1_en(r_port1_en[0]),
        .port1_x0(r_port1_x0[17:0]), .port1_y0(r_port1_y0[17:0]),
        .port1_x1(r_port1_x1[17:0]), .port1_y1(r_port1_y1[17:0]),
        .blk_tab(blk_tab), .hole_tab(hole_tab), .lane_v_tab(lane_v_tab),
        .res_we(res_we), .res_addr(res_addr), .res_data(res_data),
        .pt_we(pt_we), .pt_lane(pt_lane), .pt_idx(pt_idx), .pt_data(pt_data),
        .done(bundle_done)
    );

    // ---------------- 状态/周期计数 ----------------
    reg [31:0] cyc;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            cyc <= 32'd0;
            r_status <= 32'd0;
        end else begin
            if (start_pulse)      cyc <= 32'd0;
            else if (!bundle_done) cyc <= cyc + 32'd1;
            r_status[0]     <= bundle_done;
            r_status[1]     <= start_pulse | ((cyc != 0) && !bundle_done);
            r_status[15:8]  <= cyc[7:0];
        end
    end

    always @(posedge s_axi_aclk)
        if (res_we) lane_res[res_addr] <= res_data;

    // ---------------- 写通道（aw/w 各自握手，随后回一个 b）----------------
    reg aw_lat, w_lat;
    reg [11:0] aw_addr_l;
    reg [31:0] w_data_l;
    wire wr_go = ((s_axi_awvalid && s_axi_awready) || aw_lat) &&
                 ((s_axi_wvalid  && s_axi_wready)  || w_lat) && !s_axi_bvalid;
    integer bi, hi, li;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_awready <= 1'b0; s_axi_wready <= 1'b0;
            s_axi_bvalid <= 1'b0; s_axi_bresp <= 2'b00;
            aw_lat <= 1'b0; w_lat <= 1'b0;
            start_pulse <= 1'b0; r_ctrl <= 32'd0;
            r_x0 <= 32'd0; r_y0 <= 32'd0; r_steps <= 32'd96; r_bounce <= 32'd0;
            r_ballr <= 32'd2816; r_fw <= 32'd245760; r_fh <= 32'd138240; r_misc <= 32'd0;
            r_obs_en <= 32'd0; r_obs_x0 <= 32'd0; r_obs_y0 <= 32'd0;
            r_obs_x1 <= 32'd0; r_obs_y1 <= 32'd0;
            r_obs_cx <= 32'd0; r_obs_cy <= 32'd0;
            r_obs_ehw <= 32'd0; r_obs_ehh <= 32'd0;
            r_obs_ca <= 32'd0; r_obs_sa <= 32'd0;
            r_obs1_en <= 32'd0; r_obs1_x0 <= 32'd0; r_obs1_y0 <= 32'd0;
            r_obs1_x1 <= 32'd0; r_obs1_y1 <= 32'd0;
            r_obs1_cx <= 32'd0; r_obs1_cy <= 32'd0;
            r_obs1_ehw <= 32'd0; r_obs1_ehh <= 32'd0;
            r_obs1_ca <= 32'd0; r_obs1_sa <= 32'd0;
            r_obs2_en <= 32'd0; r_obs2_x0 <= 32'd0; r_obs2_y0 <= 32'd0;
            r_obs2_x1 <= 32'd0; r_obs2_y1 <= 32'd0;
            r_obs2_cx <= 32'd0; r_obs2_cy <= 32'd0;
            r_obs2_ehw <= 32'd0; r_obs2_ehh <= 32'd0;
            r_obs2_ca <= 32'd0; r_obs2_sa <= 32'd0;
            r_port_en <= 32'd0; r_port_x0 <= 32'd0; r_port_y0 <= 32'd0;
            r_port_x1 <= 32'd0; r_port_y1 <= 32'd0; r_port_r <= 32'd4608;
            r_port1_en <= 32'd0; r_port1_x0 <= 32'd0; r_port1_y0 <= 32'd0;
            r_port1_x1 <= 32'd0; r_port1_y1 <= 32'd0;
            for (bi = 0; bi < NBLK; bi = bi + 1) begin
                b_ang[bi] <= 23'd0; b_x0[bi] <= 18'd0; b_y0[bi] <= 18'd0;
                b_x1[bi] <= 18'd0; b_y1[bi] <= 18'd0;
            end
            for (hi = 0; hi < 4; hi = hi + 1) begin
                h_c[hi] <= 18'd0; h_w[hi] <= 18'd0;
                h_en[hi] <= 1'b0; h_edge[hi] <= 2'd0; h_tgt[hi] <= 1'b0;
            end
            for (li = 0; li < NLANES; li = li + 1) begin
                l_vx[li] <= 20'd0; l_vy[li] <= 20'd0;
            end
        end else begin
            start_pulse <= 1'b0;
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
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
                // ---- 低段：标量寄存器 ----
                case (aw_addr_l[11:8])
                4'h0: case (aw_addr_l[7:2])
                      6'h00: begin r_ctrl <= w_data_l; if (w_data_l[0]) start_pulse <= 1'b1; end
                      6'h04: r_x0 <= w_data_l;
                      6'h05: r_y0 <= w_data_l;
                      6'h06: r_steps <= w_data_l;
                      6'h07: r_bounce <= w_data_l;
                      6'h08: r_ballr <= w_data_l;
                      6'h09: r_fw <= w_data_l;
                      6'h0A: r_fh <= w_data_l;
                      6'h0B: r_misc <= w_data_l;
                      // ★ 2026-10-19 布置 PLACE（PL 原生）
                      6'h0C: r_obs_en <= w_data_l;
                      6'h0D: r_obs_x0 <= w_data_l;
                      6'h0E: r_obs_y0 <= w_data_l;
                      6'h0F: r_obs_x1 <= w_data_l;
                      6'h10: r_obs_y1 <= w_data_l;
                      6'h11: r_port_en <= w_data_l;
                      6'h12: r_port_x0 <= w_data_l;
                      6'h13: r_port_y0 <= w_data_l;
                      6'h14: r_port_x1 <= w_data_l;
                      6'h15: r_port_y1 <= w_data_l;
                      6'h16: r_port_r <= w_data_l;
                      // ★ 2026-10-19 旋转矩形障碍：cx/cy/ehw/ehh/ca/sa
                      6'h17: r_obs_cx <= w_data_l;
                      6'h18: r_obs_cy <= w_data_l;
                      6'h19: r_obs_ehw <= w_data_l;
                      6'h1A: r_obs_ehh <= w_data_l;
                      6'h1B: r_obs_ca <= w_data_l;
                      6'h1C: r_obs_sa <= w_data_l;
                      // ★ 2026-10-27 多件：1 号障碍 0x1D-0x27，2 号 0x28-0x32
                      6'h1D: r_obs1_en <= w_data_l;
                      6'h1E: r_obs1_x0 <= w_data_l;
                      6'h1F: r_obs1_y0 <= w_data_l;
                      6'h20: r_obs1_x1 <= w_data_l;
                      6'h21: r_obs1_y1 <= w_data_l;
                      6'h22: r_obs1_cx <= w_data_l;
                      6'h23: r_obs1_cy <= w_data_l;
                      6'h24: r_obs1_ehw <= w_data_l;
                      6'h25: r_obs1_ehh <= w_data_l;
                      6'h26: r_obs1_ca <= w_data_l;
                      6'h27: r_obs1_sa <= w_data_l;
                      6'h28: r_obs2_en <= w_data_l;
                      6'h29: r_obs2_x0 <= w_data_l;
                      6'h2A: r_obs2_y0 <= w_data_l;
                      6'h2B: r_obs2_x1 <= w_data_l;
                      6'h2C: r_obs2_y1 <= w_data_l;
                      6'h2D: r_obs2_cx <= w_data_l;
                      6'h2E: r_obs2_cy <= w_data_l;
                      6'h2F: r_obs2_ehw <= w_data_l;
                      6'h30: r_obs2_ehh <= w_data_l;
                      6'h31: r_obs2_ca <= w_data_l;
                      6'h32: r_obs2_sa <= w_data_l;
                      // ★ 2026-10-27 1 号门对：0x33-0x37（半径共用 0x16）
                      6'h33: r_port1_en <= w_data_l;
                      6'h34: r_port1_x0 <= w_data_l;
                      6'h35: r_port1_y0 <= w_data_l;
                      6'h36: r_port1_x1 <= w_data_l;
                      6'h37: r_port1_y1 <= w_data_l;
                      default: ;
                      endcase
                4'h1: begin                    // 0x100 块表：每块 **5 字**（各占一个 32 位字）
                    bi = aw_addr_l[7:2] / 5;   // ★ 不能把两个 18 位坐标挤进一个字（36 位 ✗
                    if (bi < NBLK) begin       //   被 32 位 AXI 截掉高 4 位，实测 y1 69120->3584）
                        case (aw_addr_l[7:2] % 5)
                        0: b_ang[bi] <= w_data_l[22:0];
                        1: b_x0[bi]  <= w_data_l[17:0];
                        2: b_y0[bi]  <= w_data_l[17:0];
                        3: b_x1[bi]  <= w_data_l[17:0];
                        4: b_y1[bi]  <= w_data_l[17:0];
                        endcase
                    end
                end
                4'h2: begin                    // 0x200 洞表：每洞 **3 字**（c / w / meta）
                    hi = aw_addr_l[7:2] / 3;   // c 与 w 各 18 位，一个字装不下两个
                    if (hi < 4) begin
                        case (aw_addr_l[7:2] % 3)
                        0: h_c[hi]  <= w_data_l[17:0];
                        1: h_w[hi]  <= w_data_l[17:0];
                        2: begin h_en[hi] <= w_data_l[0];
                                 h_edge[hi] <= w_data_l[2:1];
                                 h_tgt[hi]  <= w_data_l[3]; end
                        endcase
                    end
                end
                4'h3: begin                                   // 0x300 每路初速度
                    li = aw_addr_l[7:2] / 2;
                    if (li < NLANES) begin
                        if (aw_addr_l[2] == 1'b0) l_vx[li] <= w_data_l[19:0];
                        else                       l_vy[li] <= w_data_l[19:0];
                    end
                end
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

    // ---------------- 读通道 ----------------
    integer ri;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_arready <= 1'b0; s_axi_rvalid <= 1'b0;
            s_axi_rresp <= 2'b00; s_axi_rdata <= 32'd0;
        end else begin
            if (s_axi_arvalid && !s_axi_rvalid && !s_axi_arready) begin
                s_axi_arready <= 1'b1;
                // ★ 结果区 0x400~0x50F **跨了 [11:8] 段边界**（路 16 的地址是 0x500），
                //   所以它必须用**显式范围**判断，用 case([11:8]) 会把路 16 漏进 default。
                if (s_axi_araddr >= 12'h400 && s_axi_araddr < 12'h400 + NLANES*16) begin
                    ri = (s_axi_araddr - 12'h400) >> 4;
                    case (s_axi_araddr[3:2])
                    2'd0: s_axi_rdata <= {12'd0, lane_res[ri][53:52], lane_res[ri][51:34]};
                    2'd1: s_axi_rdata <= {14'd0, lane_res[ri][33:16]};
                    2'd2: s_axi_rdata <= {20'd0, lane_res[ri][15:4]};
                    2'd3: s_axi_rdata <= {28'd0, lane_res[ri][3:0]};
                    endcase
                    s_axi_rvalid <= 1'b1;
                    s_axi_rresp  <= 2'b00;
                end else begin
                case (s_axi_araddr[11:8])
                4'h0: case (s_axi_araddr[7:2])
                      6'h00: s_axi_rdata <= r_ctrl;
                      6'h01: s_axi_rdata <= r_status;
                      6'h04: s_axi_rdata <= r_x0;
                      6'h05: s_axi_rdata <= r_y0;
                      6'h06: s_axi_rdata <= r_steps;
                      6'h07: s_axi_rdata <= r_bounce;
                      6'h08: s_axi_rdata <= r_ballr;
                      6'h09: s_axi_rdata <= r_fw;
                      6'h0A: s_axi_rdata <= r_fh;
                      6'h0B: s_axi_rdata <= r_misc;
                      6'h0C: s_axi_rdata <= r_obs_en;
                      6'h0D: s_axi_rdata <= r_obs_x0;
                      6'h0E: s_axi_rdata <= r_obs_y0;
                      6'h0F: s_axi_rdata <= r_obs_x1;
                      6'h10: s_axi_rdata <= r_obs_y1;
                      6'h11: s_axi_rdata <= r_port_en;
                      6'h12: s_axi_rdata <= r_port_x0;
                      6'h13: s_axi_rdata <= r_port_y0;
                      6'h14: s_axi_rdata <= r_port_x1;
                      6'h15: s_axi_rdata <= r_port_y1;
                      6'h16: s_axi_rdata <= r_port_r;
                      6'h17: s_axi_rdata <= r_obs_cx;
                      6'h18: s_axi_rdata <= r_obs_cy;
                      6'h19: s_axi_rdata <= r_obs_ehw;
                      6'h1A: s_axi_rdata <= r_obs_ehh;
                      6'h1B: s_axi_rdata <= r_obs_ca;
                      6'h1C: s_axi_rdata <= r_obs_sa;
                      6'h1D: s_axi_rdata <= r_obs1_en;
                      6'h1E: s_axi_rdata <= r_obs1_x0;
                      6'h1F: s_axi_rdata <= r_obs1_y0;
                      6'h20: s_axi_rdata <= r_obs1_x1;
                      6'h21: s_axi_rdata <= r_obs1_y1;
                      6'h22: s_axi_rdata <= r_obs1_cx;
                      6'h23: s_axi_rdata <= r_obs1_cy;
                      6'h24: s_axi_rdata <= r_obs1_ehw;
                      6'h25: s_axi_rdata <= r_obs1_ehh;
                      6'h26: s_axi_rdata <= r_obs1_ca;
                      6'h27: s_axi_rdata <= r_obs1_sa;
                      6'h28: s_axi_rdata <= r_obs2_en;
                      6'h29: s_axi_rdata <= r_obs2_x0;
                      6'h2A: s_axi_rdata <= r_obs2_y0;
                      6'h2B: s_axi_rdata <= r_obs2_x1;
                      6'h2C: s_axi_rdata <= r_obs2_y1;
                      6'h2D: s_axi_rdata <= r_obs2_cx;
                      6'h2E: s_axi_rdata <= r_obs2_cy;
                      6'h2F: s_axi_rdata <= r_obs2_ehw;
                      6'h30: s_axi_rdata <= r_obs2_ehh;
                      6'h31: s_axi_rdata <= r_obs2_ca;
                      6'h32: s_axi_rdata <= r_obs2_sa;
                      6'h33: s_axi_rdata <= r_port1_en;
                      6'h34: s_axi_rdata <= r_port1_x0;
                      6'h35: s_axi_rdata <= r_port1_y0;
                      6'h36: s_axi_rdata <= r_port1_x1;
                      6'h37: s_axi_rdata <= r_port1_y1;
                      default: s_axi_rdata <= 32'h0;
                      endcase
                default: s_axi_rdata <= 32'h0;
                endcase
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;
                end
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
                s_axi_arready <= 1'b0;
            end
        end
    end

endmodule
