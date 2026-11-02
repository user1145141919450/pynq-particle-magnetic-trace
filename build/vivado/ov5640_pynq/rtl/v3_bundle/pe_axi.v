// ============================================================================
// pe_axi.v —— 粒子引擎的 AXI4-Lite 封装（PS 写指令 / 读状态，2026-10-16）
//
// 骨架照抄 v3_bundle_axi.v；引擎的粒子读回是**单口**（rd_idx 选择 + 组合输出），
// 因此读通道做成两相：AR 握手拍先按地址算出 rd_idx 寄存，下一拍再装 rdata。
//
// 寄存器图（字地址 = addr[11:2]）：
//   ★★ 2026-10-17 板上实锤订正（旧注释把 STATUS 写成 0x004 —— 驱动照抄后 run() 轮询到
//      的是 n_alive 的 bit0，场上有一颗粒子就永远"busy"；以**译码**为准）：
//   0x000 CTRL      [0]=run(自清) [1]=stop(电平) [2]=clear(自清)        ← 写
//   0x000 STATUS    [0]=busy [1]=paused [2]=ctrl[1] [14:8]=n_alive      ← 读（同址读写双义）
//   0x004 N_ALIVE   {24'd0, n_alive}                                   ← 读（不是 STATUS！）
//   0x008 RUN_N
//   0x00C ST_SUB    0x010 ST_GOAL   0x014 ST_ANNIH   0x018 ST_COLL
//   0x01C ST_BOUNCE 0x020 ST_EVDROP
//   0x024 SPAWN_META 写触发一次出生 {pad[21:0], who[1:0], chg[1:0], idx[5:0]}
//   0x028 SPAWN_X   0x02C SPAWN_Y   0x030 SPAWN_VX   0x034 SPAWN_VY
//   0x038 BALL_R    0x03C FIELD_W   0x040 FIELD_H    0x044 N_HOLES
//   0x048 RD_IDX    直接选粒（0x400 区读的默认对象）
//   0x100 + i*5     块表（同 bundle：ang/x0/y0/x1/y1）
//   0x200 + h*3     洞表（c/w/{en,edge,tgt}）
//   0x400 + i*16   粒子 i 读回：+0=x +4=y +8=vx(符号扩展 32) +12={alive,who,chg,pad,vy}
//   0xE00 EV_COUNT  0xE04 EV_DATA  0xE08 EV_EMPTY（写 0xE00 = 弹事件）
//   ★ 事件区原来放 0x600 —— 与粒子读回区 0x400+64*16（到 0x7FF）撞车，实测读 0。
// ============================================================================
`timescale 1ns / 1ps

module pe_axi #(
    parameter integer NPART = 16,      // ★ 2026-11-01 用户定案：真正的 16 粒并行物理
    parameter integer NBLK  = 9
)(
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
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm1.0 s_axi ARREADY" *)   output reg         s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RDATA" *)     output reg  [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RRESP" *)     output reg  [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RVALID" *)    output reg         s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 s_axi RREADY" *)    input  wire        s_axi_rready
);

    reg [31:0] r_run_n, r_ballr, r_fw, r_fh, r_nholes;
    reg [31:0] r_spx, r_spy, r_spvx, r_spvy, r_spmeta;
    // ★★ 2026-10-19 布置 PLACE（PL 原生）：AABB 障碍 + 方形传送门
    reg [31:0] r_obs_en, r_obs_x0, r_obs_y0, r_obs_x1, r_obs_y1;
    reg [31:0] r_port_en, r_port_x0, r_port_y0, r_port_x1, r_port_y1, r_port_r;
    reg [31:0] r_obs_cx, r_obs_cy, r_obs_ehw, r_obs_ehh, r_obs_ca, r_obs_sa;
    reg [31:0] r_ctrl;                       // [0]=run [1]=stop [2]=clear（电平位）
    reg [5:0]  rd_idx_r;
    // ★★ 2026-10-31：整机时序门禁只差 3ps（WNS -0.003），唯一违例就是
    //   b_x0_reg -> g_lane[0].u_lane/ang_r（逻辑 4.0ns + **布线 5.8ns** = 扇出太长）。
    //   和 2026-09 的 v3_bundle_axi 同一病：让工具**复制寄存器**缩短走线。
    (* MAX_FANOUT = 16 *) reg [22:0] b_ang  [0:NBLK-1];
    (* MAX_FANOUT = 16 *) reg [17:0] b_x0   [0:NBLK-1];
    (* MAX_FANOUT = 16 *) reg [17:0] b_y0   [0:NBLK-1];
    reg [17:0] b_x1   [0:NBLK-1];
    reg [17:0] b_y1   [0:NBLK-1];
    reg [17:0] h_c    [0:3];
    reg [17:0] h_w    [0:3];
    reg        h_en   [0:3];
    reg [1:0]  h_edge [0:3];
    reg        h_tgt  [0:3];

    reg        run_pulse, clear_pulse, spawn_pulse, ev_pop_r;

    wire        busy, paused;
    wire [6:0]  n_alive;
    wire [17:0] rd_x, rd_y;
    wire signed [19:0] rd_vx, rd_vy;
    wire [1:0]  rd_chg, rd_who;
    wire        rd_alive;
    wire [4:0]  ev_count;
    wire [13:0] ev_data;
    wire        ev_empty;
    wire [15:0] st_substeps, st_goal, st_annihilate, st_collide, st_bounce,
                st_evdrop;

    reg [6:0]  rd_idx_eff;                   // 读通道两相：AR 拍算出、下一拍用
    reg [NBLK*95-1:0] blk_tab;
    reg [4*40-1:0]    hole_tab;
    integer bi, hi;
    always @(*) begin
        blk_tab = 0;
        hole_tab = 0;
        for (bi = 0; bi < NBLK; bi = bi + 1)
            blk_tab[bi*95 +: 95] = {b_ang[bi], b_x0[bi], b_y0[bi],
                                    b_x1[bi], b_y1[bi]};
        for (hi = 0; hi < 4; hi = hi + 1)
            hole_tab[hi*40 +: 40] = {h_en[hi], h_edge[hi], h_tgt[hi],
                                     h_w[hi], h_c[hi]};
    end

    // ★★ 2026-10-17 实修：此处原来写死 .KLANES(4) —— 10-16 的"瘦身"提交只把
    //   particle_engine 的**默认值**改成 1，实例化覆盖没动，实建 bit 里仍是 4 路
    //   （OOC 报告实锤：pe_axi 里 4 个 g_lane，共 11.7k LUT）。改回 1 路 = 立省 ~8.5k。
    particle_engine #(.NPART(NPART), .NBLK(NBLK), .KLANES(1)) u_pe (
        .clk(s_axi_aclk), .rstn(s_axi_aresetn),
        .run_pulse(run_pulse), .run_n(r_run_n[15:0]),
        .stop(r_ctrl[1]), .clear(clear_pulse),
        .busy(busy), .n_alive(n_alive), .paused(paused),
        .ball_r(r_ballr[17:0]), .field_w(r_fw[17:0]), .field_h(r_fh[17:0]),
        .n_holes(r_nholes[1:0]), .blk_tab(blk_tab), .hole_tab(hole_tab),
        .obs_en(r_obs_en[0]),
        .obs_x0(r_obs_x0[17:0]), .obs_y0(r_obs_y0[17:0]),
        .obs_x1(r_obs_x1[17:0]), .obs_y1(r_obs_y1[17:0]),
        .obs_cx(r_obs_cx[17:0]), .obs_cy(r_obs_cy[17:0]),
        .obs_ehw(r_obs_ehw[17:0]), .obs_ehh(r_obs_ehh[17:0]),
        .obs_ca(r_obs_ca[22:0]), .obs_sa(r_obs_sa[22:0]),
        .port_en(r_port_en[0]),
        .port_x0(r_port_x0[17:0]), .port_y0(r_port_y0[17:0]),
        .port_x1(r_port_x1[17:0]), .port_y1(r_port_y1[17:0]),
        .port_r(r_port_r[17:0]),
        .spawn_pulse(spawn_pulse), .spawn_idx(r_spmeta[5:0]),
        .spawn_x(r_spx[17:0]), .spawn_y(r_spy[17:0]),
        .spawn_vx(r_spvx[19:0]), .spawn_vy(r_spvy[19:0]),
        .spawn_chg(r_spmeta[7:6]), .spawn_who(r_spmeta[9:8]),
        .rd_idx(rd_idx_eff[5:0]),
        .rd_x(rd_x), .rd_y(rd_y), .rd_vx(rd_vx), .rd_vy(rd_vy),
        .rd_chg(rd_chg), .rd_who(rd_who), .rd_alive(rd_alive),
        .ev_count(ev_count), .ev_pop(ev_pop_r), .ev_data(ev_data),
        .ev_empty(ev_empty),
        .st_substeps(st_substeps), .st_goal(st_goal),
        .st_annihilate(st_annihilate), .st_collide(st_collide),
        .st_bounce(st_bounce), .st_evdrop(st_evdrop));

    // ---------------- 写通道（照抄 bundle 的 aw/w 各自握手 + 单 b）----------------
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
            run_pulse <= 1'b0; clear_pulse <= 1'b0; spawn_pulse <= 1'b0;
            ev_pop_r <= 1'b0;
            r_run_n <= 32'd0; r_ctrl <= 32'd0; rd_idx_r <= 6'd0;
            r_ballr <= 32'd2816; r_fw <= 32'd245760; r_fh <= 32'd138240;
            r_nholes <= 32'd2;
            r_spx <= 32'd0; r_spy <= 32'd0; r_spvx <= 32'd0; r_spvy <= 32'd0;
            r_spmeta <= 32'd0;
            r_obs_en <= 32'd0; r_obs_x0 <= 32'd0; r_obs_y0 <= 32'd0;
            r_obs_x1 <= 32'd0; r_obs_y1 <= 32'd0;
            r_obs_cx <= 32'd0; r_obs_cy <= 32'd0;
            r_obs_ehw <= 32'd0; r_obs_ehh <= 32'd0;
            r_obs_ca <= 32'd0; r_obs_sa <= 32'd0;
            r_port_en <= 32'd0; r_port_x0 <= 32'd0; r_port_y0 <= 32'd0;
            r_port_x1 <= 32'd0; r_port_y1 <= 32'd0; r_port_r <= 32'd4608;
            for (bi = 0; bi < NBLK; bi = bi + 1) begin
                b_ang[bi] <= 23'd0; b_x0[bi] <= 18'd0; b_y0[bi] <= 18'd0;
                b_x1[bi] <= 18'd0; b_y1[bi] <= 18'd0;
            end
            for (hi = 0; hi < 4; hi = hi + 1) begin
                h_c[hi] <= 18'd0; h_w[hi] <= 18'd0;
                h_en[hi] <= 1'b0; h_edge[hi] <= 2'd0; h_tgt[hi] <= 1'b0;
            end
        end else begin
            run_pulse <= 1'b0;
            clear_pulse <= 1'b0;
            spawn_pulse <= 1'b0;
            ev_pop_r <= 1'b0;
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
                case (aw_addr_l[11:8])
                4'h0: case (aw_addr_l[7:2])
                      6'h00: begin
                          r_ctrl <= w_data_l;
                          if (w_data_l[0]) run_pulse <= 1'b1;
                          if (w_data_l[2]) clear_pulse <= 1'b1;
                      end
                      6'h02: r_run_n <= w_data_l;
                      6'h09: begin
                          // SPAWN_META：写即出生一次（先写 X/Y/VX/VY 再写这）
                          // ★★ 2026-10-17 板上实锤（硅级写通道异常）：每次写会被执行
                          //   **两次** —— 第一次 `w_data_l=0`（幽灵），第二次真数据。
                          //   寄存器最终值无恙（真数据后到），但**脉冲副作用**会多出
                          //   一次"meta=0 的出生"（砸槽 0，n_alive +1）。RTL/网表门级
                          //   仿真都复现不了（判为 AW/W 错拍+硅上数据路径毛刺，模型
                          //   WNS 仅 +0.03）。修法：**脉冲一律数据门控** —— 数据为 0
                          //   不产生任何动作（真出生 meta 的 who≠0 ⇒ 数据恒非零）。
                          r_spmeta <= w_data_l;
                          if (w_data_l != 32'd0)
                              spawn_pulse <= 1'b1;
                      end
                      6'h0A: r_spx <= w_data_l;
                      6'h0B: r_spy <= w_data_l;
                      6'h0C: r_spvx <= w_data_l;
                      6'h0D: r_spvy <= w_data_l;
                      6'h0E: r_ballr <= w_data_l;
                      6'h0F: r_fw <= w_data_l;
                      6'h10: r_fh <= w_data_l;
                      6'h11: r_nholes <= w_data_l;
                      6'h12: rd_idx_r <= w_data_l[5:0];
                      // ★ 2026-10-19 布置 PLACE（PL 原生）
                      6'h13: r_obs_en <= w_data_l;
                      6'h14: r_obs_x0 <= w_data_l;
                      6'h15: r_obs_y0 <= w_data_l;
                      6'h16: r_obs_x1 <= w_data_l;
                      6'h17: r_obs_y1 <= w_data_l;
                      6'h18: r_port_en <= w_data_l;
                      6'h19: r_port_x0 <= w_data_l;
                      6'h1A: r_port_y0 <= w_data_l;
                      6'h1B: r_port_x1 <= w_data_l;
                      6'h1C: r_port_y1 <= w_data_l;
                      6'h1D: r_port_r <= w_data_l;
                      6'h1E: r_obs_cx <= w_data_l;
                      6'h1F: r_obs_cy <= w_data_l;
                      6'h20: r_obs_ehw <= w_data_l;
                      6'h21: r_obs_ehh <= w_data_l;
                      6'h22: r_obs_ca <= w_data_l;
                      6'h23: r_obs_sa <= w_data_l;
                      default: ;
                      endcase
                4'h1: begin                    // 0x100 块表：每块 5 字
                    bi = aw_addr_l[7:2] / 5;
                    if (bi < NBLK) begin
                        case (aw_addr_l[7:2] % 5)
                        0: b_ang[bi] <= w_data_l[22:0];
                        1: b_x0[bi]  <= w_data_l[17:0];
                        2: b_y0[bi]  <= w_data_l[17:0];
                        3: b_x1[bi]  <= w_data_l[17:0];
                        4: b_y1[bi]  <= w_data_l[17:0];
                        endcase
                    end
                end
                4'h2: begin                    // 0x200 洞表：每洞 3 字
                    hi = aw_addr_l[7:2] / 3;
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
                4'hE: case (aw_addr_l[7:2])
                      // ★ 数据门控（同上）：幽灵执行（data=0）不许弹事件；驱动写 1。
                      6'h00: if (w_data_l != 32'd0) ev_pop_r <= 1'b1;
                      default: ;
                      endcase
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

    // ---------------- 读通道（两相：AR 拍锁存地址/选粒 -> 下一拍装 rdata）----------------
    integer ri;
    reg [1:0]  rd_wsel;
    reg        rd_is_part;
    reg [11:0] ar_addr_l;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            s_axi_arready <= 1'b0; s_axi_rvalid <= 1'b0;
            s_axi_rresp <= 2'b00; s_axi_rdata <= 32'd0;
            rd_idx_eff <= 7'd0; rd_wsel <= 2'd0; rd_is_part <= 1'b0;
            ar_addr_l <= 12'd0;
        end else begin
            if (s_axi_arvalid && !s_axi_rvalid && !s_axi_arready) begin
                // 相位 1：握手 + 锁存
                s_axi_arready <= 1'b1;
                ar_addr_l <= s_axi_araddr;
                if (s_axi_araddr >= 12'h400 &&
                    s_axi_araddr < 12'h400 + NPART*16) begin
                    ri = (s_axi_araddr - 12'h400) >> 4;
                    rd_idx_eff <= ri[6:0];
                    rd_wsel <= s_axi_araddr[3:2];
                    rd_is_part <= 1'b1;
                end else begin
                    rd_idx_eff <= {1'b0, rd_idx_r};
                    rd_is_part <= 1'b0;
                end
            end else if (s_axi_arready && !s_axi_rvalid) begin
                // 相位 2：rd_idx_eff/地址已稳定，装 rdata
                s_axi_arready <= 1'b0;
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;
                if (rd_is_part) begin
                    case (rd_wsel)
                    2'd0: s_axi_rdata <= {14'd0, rd_x};
                    2'd1: s_axi_rdata <= {14'd0, rd_y};
                    2'd2: s_axi_rdata <= {{12{rd_vx[19]}}, rd_vx};
                    // word3：{pad[6:0], alive, who[1:0], chg[1:0], vy(20 有符号)}
                    2'd3: s_axi_rdata <= {7'd0, rd_alive, rd_who, rd_chg, rd_vy};
                    endcase
                end else begin
                    case (ar_addr_l[11:8])
                    4'h0: case (ar_addr_l[7:2])
                          // STATUS: [0]=busy [1]=paused [2]=stop [15:8]=n_alive
                          6'h00: s_axi_rdata <= {17'd0, n_alive, 5'd0,
                                                 r_ctrl[1], paused, busy};
                          6'h01: s_axi_rdata <= {24'd0, n_alive};
                          6'h02: s_axi_rdata <= r_run_n;
                          6'h03: s_axi_rdata <= {16'd0, st_substeps};
                          6'h04: s_axi_rdata <= {16'd0, st_goal};
                          6'h05: s_axi_rdata <= {16'd0, st_annihilate};
                          6'h06: s_axi_rdata <= {16'd0, st_collide};
                          6'h07: s_axi_rdata <= {16'd0, st_bounce};
                          6'h08: s_axi_rdata <= {16'd0, st_evdrop};
                          6'h09: s_axi_rdata <= r_spmeta;
                          6'h0A: s_axi_rdata <= r_spx;
                          6'h0B: s_axi_rdata <= r_spy;
                          6'h0C: s_axi_rdata <= r_spvx;
                          6'h0D: s_axi_rdata <= r_spvy;
                          6'h0E: s_axi_rdata <= r_ballr;
                          6'h0F: s_axi_rdata <= r_fw;
                          6'h10: s_axi_rdata <= r_fh;
                          6'h11: s_axi_rdata <= r_nholes;
                          6'h12: s_axi_rdata <= {26'd0, rd_idx_r};
                          default: s_axi_rdata <= 32'h0;
                          endcase
                    4'hE: case (ar_addr_l[7:2])
                          6'h00: s_axi_rdata <= {27'd0, ev_count};
                          6'h01: s_axi_rdata <= {18'd0, ev_data};
                          6'h02: s_axi_rdata <= {31'd0, ev_empty};
                          default: s_axi_rdata <= 32'h0;
                          endcase
                    default: s_axi_rdata <= 32'h0;
                    endcase
                end
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule
