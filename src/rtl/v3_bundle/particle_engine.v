// ============================================================================
// particle_engine.v —— ★★ 2026-10-16 粒子引擎（档3）：**多粒子持续并行物理**
//
// 把"球飞起来的物理"从 PS Python 搬进 PL：最多 64 颗粒子常驻硬件，每次 run
// 推进 N 个子步（1/240s 一步）：磁偏转 / 墙体镜面反射 / 洞口出射 /
// **同号弹性碰撞** / **异号湮灭** —— 全部硬件解算；PS 只下发指令、读回状态事件。
//
// 与既有逐位契约的关系：
//   · 单粒子步进 = **复用 `traj_lane`**（已被 tb 逐位钉死 = 黄金模型 lane_fixed）。
//     一粒一步 = max_steps=1 + mb_inf=1：第一步完整提交（含墙反弹/进洞），
//     第二步只判"步数用尽"收尾（JUDGE 里步数检查在墙判之前；ms=0 一步都不提交）。
//   · 碰撞解算 = `tools/pe_fix.py` 的**整数域逐位镜像**（Q20 截断除法、推出一步
//     右移 21、投影 Q8、位置/速度事后夹紧——取整与夹紧顺序 RTL 与镜像一字不差）。
//
// 吞吐实账（100MHz，实装 16 粒/KLANES=1）：16 轮 × 54 拍 + 碰撞相 ≈ 1.7k 拍/子步
//   ≈ 17µs；游戏一帧 80 子步 ≈ 1.4ms。PS Python 同量约 60ms 且随粒数崩塌。
// ============================================================================
`timescale 1ns / 1ps

module particle_engine #(
    // ★★ 2026-10-16 实装瘦身（首版 64 粒/4 路把 xc7z020 撑爆：69.6k/53.2k LUT）——
    //   64→32、4→1 路；★ 2026-11-01 用户定案：恢复 **16 粒**（PE=16 并行物理）。
    //   实时吞吐仍大把富余（16 轮×~54 拍 + 碰撞 ≈ 1.7k 拍/子步 ≈ 17µs，
    //   一帧 80 子步 ≈ 1.4ms）。要再扩容得先把粒子阵列挪进 BRAM。
    parameter integer NPART  = 16,
    parameter integer NBLK   = 9,
    parameter integer KLANES = 1
)(
    input  wire        clk,
    input  wire        rstn,

    input  wire        run_pulse,
    input  wire [15:0] run_n,
    input  wire        stop,
    input  wire        clear,
    output reg         busy,
    output reg  [6:0]  n_alive,
    output reg         paused,

    input  wire [17:0] ball_r, field_w, field_h,
    input  wire [1:0]  n_holes,
    input  wire [NBLK*95-1:0] blk_tab,
    input  wire [4*40-1:0]    hole_tab,

    // ★★ 2026-10-19 布置 PLACE（PL 原生）：AABB 障碍 + 方形传送门
    //   （与 traj_lane 同口径；风暴里粒子也能撞条/穿门）
    input  wire        obs_en,
    input  wire [17:0] obs_x0, obs_y0, obs_x1, obs_y1,
    input  wire [17:0] obs_cx, obs_cy, obs_ehw, obs_ehh,
    input  wire signed [22:0] obs_ca, obs_sa,
    input  wire        port_en,
    input  wire [17:0] port_x0, port_y0, port_x1, port_y1, port_r,

    input  wire        spawn_pulse,
    input  wire [5:0]  spawn_idx,
    input  wire [17:0] spawn_x, spawn_y,
    input  wire signed [19:0] spawn_vx, spawn_vy,
    input  wire [1:0]  spawn_chg,
    input  wire [1:0]  spawn_who,

    input  wire [5:0]  rd_idx,
    output reg  [17:0] rd_x, rd_y,
    output reg  signed [19:0] rd_vx, rd_vy,
    output reg  [1:0]  rd_chg, rd_who,
    output reg         rd_alive,

    output reg  [4:0]  ev_count,
    input  wire        ev_pop,
    output reg  [13:0] ev_data,
    output reg         ev_empty,

    output reg  [15:0] st_substeps, st_goal, st_annihilate, st_collide,
    output reg  [15:0] st_bounce, st_evdrop
);
    localparam EV_GOAL = 2'd1, EV_ANNIH = 2'd2;

    integer zi, ck;

    // ---------------- 粒子阵列 ----------------
    reg [17:0] p_x  [0:NPART-1];
    reg [17:0] p_y  [0:NPART-1];
    reg signed [19:0] p_vx [0:NPART-1];
    reg signed [19:0] p_vy [0:NPART-1];
    reg [1:0]  p_chg[0:NPART-1];
    reg [1:0]  p_who[0:NPART-1];
    reg        p_ok [0:NPART-1];

    // ---------------- 辅助函数 ----------------
    function [1:0] edge_of;
        input [17:0] ex;
        input [17:0] ey;
        begin
            // ★ 2026-10-17 板上实锤修：出射位置 ex=nx_r ∈ (fw_r, fw_r+一步]，
            //   **永远够不到 field_w** —— 原来比 `ex >= field_w` 使右洞出射全落到
            //   else=bottom（tb 的 EV=0x01D5 一直是 edge=3，只是没人断言过）。
            //   正确判据：越过**夹紧墙** fw_r = field_w - ball_r 即为该边。
            if (ex < ball_r)                edge_of = 2'd0;   // left（nx_r < br_r）
            else if (ex > field_w - ball_r) edge_of = 2'd1;   // right（nx_r > fw_r）
            else if (ey < ball_r)           edge_of = 2'd2;   // top
            else                            edge_of = 2'd3;   // bottom
        end
    endfunction

    function [17:0] clampx;
        input signed [24:0] v;
        begin
            if (v < 0)                             clampx = 18'd0;
            else if (v > $signed({7'd0, field_w})) clampx = field_w;
            else                                   clampx = v[17:0];
        end
    endfunction

    function [17:0] clampy;
        input signed [24:0] v;
        begin
            if (v < 0)                             clampy = 18'd0;
            else if (v > $signed({7'd0, field_h})) clampy = field_h;
            else                                   clampy = v[17:0];
        end
    endfunction

    function signed [19:0] clampv;                 // 速度夹 signed 20 位（镜像同款）
        input signed [24:0] v;
        begin
            if (v > 25'sd524287)        clampv = 20'sd524287;
            else if (v < -25'sd524288)  clampv = -20'sd524288;
            else                        clampv = v[19:0];
        end
    endfunction

    // ★ 电荷语义改由 lane 的 ang_sel（+角/-角/零）承担 —— 一张共享表通吃，
    //   每路一个 855 位三选一 mux 的时代结束（那是首版每路 4~6k LUT 的元凶）。

    // ---------------- 洞表 -> 上下界 ----------------
    wire [4*18-1:0] h_lo, h_hi;
    wire [4*4-1:0]  h_meta;
    genvar gh;
    generate
        for (gh = 0; gh < 4; gh = gh + 1) begin : g_hole
            assign h_lo[gh*18 +: 18] = hole_tab[gh*40+0  +: 18] - (hole_tab[gh*40+18 +: 18] >> 1);
            assign h_hi[gh*18 +: 18] = hole_tab[gh*40+0  +: 18] + (hole_tab[gh*40+18 +: 18] >> 1);
            assign h_meta[gh*4 +: 4] = {hole_tab[gh*40+39],
                                        hole_tab[gh*40+37 +: 2],
                                        hole_tab[gh*40+36]};
        end
    endgenerate

    // ---------------- KLANES 个复用 lane ----------------
    wire        l_done [0:KLANES-1];
    wire [1:0]  l_res  [0:KLANES-1];
    wire [17:0] l_ex   [0:KLANES-1];
    wire [17:0] l_ey   [0:KLANES-1];
    wire signed [19:0] l_vxo [0:KLANES-1];
    wire signed [19:0] l_vyo [0:KLANES-1];

    reg        d_start [0:KLANES-1];
    reg [17:0] d_x0 [0:KLANES-1], d_y0 [0:KLANES-1];
    reg signed [19:0] d_vx0 [0:KLANES-1], d_vy0 [0:KLANES-1];
    reg [1:0]  d_angsel [0:KLANES-1];

    genvar gk;
    generate
        for (gk = 0; gk < KLANES; gk = gk + 1) begin : g_lane
            traj_lane #(.NBLK(NBLK)) u_lane (
                .clk(clk), .rstn(rstn), .start(d_start[gk]),
                .x0(d_x0[gk]), .y0(d_y0[gk]),
                .vx0(d_vx0[gk]), .vy0(d_vy0[gk]),
                .max_steps(12'd1),        // ★ 一粒一步：完整一步 + 收尾
                .max_bounce(4'd0),
                .mb_inf(1'b1),            // ★ 撞墙一律反弹续走（真球规则）
                .ball_r(ball_r), .field_w(field_w), .field_h(field_h),
                .side(1'b0),
                .obs_en(obs_en),
                .obs_x0(obs_x0), .obs_y0(obs_y0),
                .obs_x1(obs_x1), .obs_y1(obs_y1),
                .obs_cx(obs_cx), .obs_cy(obs_cy),
                .obs_ehw(obs_ehw), .obs_ehh(obs_ehh),
                .obs_ca(obs_ca), .obs_sa(obs_sa),
                // ★ 2026-10-27 多件：PE 只用单套障碍/门，新增的 1/2 号套件显式接 0
                //   （悬空会让 traj_lane 里的 3:1/2:1 mux 不被常量折叠，挤爆时序）
                .obs1_en(1'b0), .obs2_en(1'b0),
                .obs1_x0(18'd0), .obs1_y0(18'd0),
                .obs1_x1(18'd0), .obs1_y1(18'd0),
                .obs1_cx(18'd0), .obs1_cy(18'd0),
                .obs1_ehw(18'd0), .obs1_ehh(18'd0),
                .obs1_ca(23'sd0), .obs1_sa(23'sd0),
                .obs2_x0(18'd0), .obs2_y0(18'd0),
                .obs2_x1(18'd0), .obs2_y1(18'd0),
                .obs2_cx(18'd0), .obs2_cy(18'd0),
                .obs2_ehw(18'd0), .obs2_ehh(18'd0),
                .obs2_ca(23'sd0), .obs2_sa(23'sd0),
                .port_en(port_en),
                .port_x0(port_x0), .port_y0(port_y0),
                .port_x1(port_x1), .port_y1(port_y1), .port_r(port_r),
                .port1_en(1'b0),
                .port1_x0(18'd0), .port1_y0(18'd0),
                .port1_x1(18'd0), .port1_y1(18'd0),
                .blk_tab(blk_tab), .ang_sel(d_angsel[gk]), .n_holes(n_holes),
                .h_lo(h_lo), .h_hi(h_hi), .h_meta(h_meta),
                .res(l_res[gk]), .ex(l_ex[gk]), .ey(l_ey[gk]),
                .vx_out(l_vxo[gk]), .vy_out(l_vyo[gk]),
                .n_step(), .n_bounce(),
                .done(l_done[gk]),
                .pt_req(), .pt_gnt(1'b1),
                .pt_we_idx(), .pt_we_data());
        end
    endgenerate

    // ---------------- 事件 FIFO ----------------
    reg [13:0] ev_mem [0:15];
    reg [3:0]  ev_wp, ev_rp;

    // ---------------- 状态 ----------------
    localparam S_IDLE = 3'd0, S_LAUNCH = 3'd1, S_WAIT = 3'd2,
               S_COLL   = 3'd3, S_NEXT = 3'd4, S_HOLD = 3'd5;

    // ★ 2026-10-17 时序修复（两轮）：碰撞相 6 态 -> 11 态流水，数学逐位不变。
    //   第一轮修 WNS -18.078ns（"读阵列 -> 两次级联乘法 -> 夹紧 -> 写回"压一拍）；
    //   第二轮修 WNS -0.200ns（阵列读 mux -> 相减 -> DSP CE 一拍仍太长：再插一级
    //   "读寄存"，AABB 用寄存值判）。
    localparam C_IDLE = 4'd0, C_SEEK = 4'd1, C_CHK = 4'd2, C_CHK2 = 4'd3,
               C_CHK3 = 4'd4, C_CHK4 = 4'd5, C_SQ = 4'd6, C_DIV = 4'd7,
               C_APPLY1 = 4'd8, C_APPLY2 = 4'd9, C_APPLY3 = 4'd10;

    reg [17:0] pxi_r, pyi_r, pxj_r, pyj_r;   // 第 0 段：阵列读寄存

    reg [2:0]  st;
    reg [15:0] run_left;
    reg [6:0]  base;
    reg [6:0]  l_pidx [0:KLANES-1];
    reg        l_live [0:KLANES-1];

    reg [3:0]  cs;
    // ★ 7 位（0..127）：6 位装不下 64，`ci >= NPART` 会永远假 → 死循环（10-16 实测卡死）
    reg [6:0]  ci, cj;
    // 开方
    reg [42:0] sq_n, sq_bit, sq_res;
    reg        sq_pre;          // 开方阶段1（定标）标志
    reg [12:0] d_root;
    // 除法（共享：两轮，dv_which 0=x 1=y）
    reg [42:0] dv_num, dv_rem, dv_q;
    reg [12:0] dv_den;
    reg [5:0]  dv_i;
    reg        dv_neg, dv_which;
    reg signed [23:0] nx_r, ny_r;
    reg [12:0] ov_r;

    // ---------------- 碰撞相流水寄存（★ 2026-10-17 WNS -18.078ns 修复）----------------
    //   原设计把"读阵列 mux -> 级联两次乘法 -> 移位 -> 夹紧 -> 变址写回"全压在一拍，
    //   实测最差路径 p_vx_reg[20][11] -> p_vx_reg[...][..] = -18ns、全设计 29k 违例端点。
    //   分解（数学逐位不变，运算种类/顺序/截断/夹紧全照旧，只是中间值多了寄存器）：
    //     C_CHK   : 寄存 dxs/dys（AABB 预筛仍是浅组合段）
    //     C_CHK2  : d2 = dxs²+dys²（DSP 从寄存操作数出发）-> 寄存
    //     C_CHK3  : d2 比较 + 电荷判定（全部用寄存值）
    //     C_DIV   : 末拍顺带寄存 8 个粒子分量（阵列 mux -> FF，浅）
    //     C_APPLY1: va/vb 投影 + adjx/adjy（DSP 从寄存操作数 -> FF）
    //     C_APPLY2: dv = vb-va；dxn/dyn = dv*n（一拍 DSP -> FF）
    //     C_APPLY3: 夹紧 + 变址写回（FF -> 加减 -> 夹紧 -> FF，浅）
    reg signed [20:0] dxs_r, dys_r;
    reg signed [42:0] d2_r;
    reg [17:0] ap_xi, ap_yi, ap_xj, ap_yj;
    reg signed [19:0] ap_vxi, ap_vyi, ap_vxj, ap_vyj;
    reg signed [23:0] va_r, vb_r;
    reg signed [23:0] dxn_r, dyn_r, adjx_r, adjy_r;
    // 出生寄存（★ 2026-10-17 幽灵写修复：one-hot 写使能直接由 FF 出）
    reg [NPART-1:0] sp_sel;
    reg [17:0] sp_x_r, sp_y_r;
    reg signed [19:0] sp_vx_r, sp_vy_r;
    reg [1:0] sp_chg_r, sp_who_r;
    // p_ok 的就绪向量（nl 单次 +1 用）
    wire [NPART-1:0] p_ok_v;
    genvar gp;
    generate
        for (gp = 0; gp < NPART; gp = gp + 1) begin : g_pokv
            assign p_ok_v[gp] = p_ok[gp];
        end
    endgenerate

    wire signed [20:0] dxs_rw = $signed({3'b0, pxj_r}) - $signed({3'b0, pxi_r});
    wire signed [20:0] dys_rw = $signed({3'b0, pyj_r}) - $signed({3'b0, pyi_r});
    wire signed [42:0] d2_w  = dxs_r * dxs_r + dys_r * dys_r;      // 从寄存分量算
    wire signed [42:0] dxs_abs_r = dxs_r[20] ? (~dxs_r + 21'sd1) : dxs_r;
    wire signed [42:0] dys_abs_r = dys_r[20] ? (~dys_r + 21'sd1) : dys_r;
    // 推出量：nx*ov >> 21（nx_r/ov_r 是寄存器，一拍 DSP -> FF）
    wire signed [36:0] nx_ov_c = nx_r * $signed({1'b0, ov_r});
    wire signed [36:0] ny_ov_c = ny_r * $signed({1'b0, ov_r});
    wire signed [23:0] adjx_c = nx_ov_c >>> 21;
    wire signed [23:0] adjy_c = ny_ov_c >>> 21;
    // 投影：va/vb = (v·n)>>20（ap_* 是 C_DIV 末拍寄存的粒子分量）；dv = vb-va；
    // 回写 v ± (dv*n)>>20
    wire signed [43:0] avx_nx_c = ap_vxi * nx_r;
    wire signed [43:0] avy_ny_c = ap_vyi * ny_r;
    wire signed [43:0] bvx_nx_c = ap_vxj * nx_r;
    wire signed [43:0] bvy_ny_c = ap_vyj * ny_r;
    wire signed [23:0] va_c = (avx_nx_c >>> 20) + (avy_ny_c >>> 20);
    wire signed [23:0] vb_c = (bvx_nx_c >>> 20) + (bvy_ny_c >>> 20);
    wire signed [23:0] dv_c = vb_r - va_r;                          // 寄存值相减
    wire signed [47:0] dv_nx_c = dv_c * nx_r;
    wire signed [47:0] dv_ny_c = dv_c * ny_r;
    wire signed [23:0] dxn_c = dv_nx_c >>> 20;
    wire signed [23:0] dyn_c = dv_ny_c >>> 20;

    // ---------------- 读回 ----------------
    always @(*) begin
        rd_x = p_x[rd_idx]; rd_y = p_y[rd_idx];
        rd_vx = p_vx[rd_idx]; rd_vy = p_vy[rd_idx];
        rd_chg = p_chg[rd_idx]; rd_who = p_who[rd_idx];
        rd_alive = p_ok[rd_idx];
    end

    // ---------------------------------------------------------------------
    integer nl;
    reg lanes_idle;         // ★ 2026-10-17：S_WAIT 的"全部 lane 完成"判据（按参数循环，
                            //   原来写死 l_live[0..3]/l_done[0..3]——KLANES=1 下越界编译不过）
    always @(posedge clk) begin
        if (!rstn) begin
            st <= S_IDLE; run_left <= 16'd0; base <= 7'd0;
            busy <= 1'b0; paused <= 1'b0; cs <= C_IDLE; ci <= 6'd0; cj <= 6'd1;
            ev_wp <= 4'd0; ev_rp <= 4'd0; ev_count <= 5'd0; ev_empty <= 1'b1;
            ev_data <= 14'd0;
            st_substeps <= 16'd0; st_goal <= 16'd0; st_annihilate <= 16'd0;
            st_collide <= 16'd0; st_bounce <= 16'd0; st_evdrop <= 16'd0;
            n_alive <= 7'd0;
            sq_n <= 43'd0; sq_bit <= 43'd0; sq_res <= 43'd0; sq_pre <= 1'b0;
            d_root <= 13'd0;
            dv_num <= 43'd0; dv_rem <= 43'd0; dv_q <= 43'd0;
            dv_den <= 13'd0; dv_i <= 6'd0; dv_neg <= 1'b0; dv_which <= 1'b0;
            nx_r <= 24'sd0; ny_r <= 24'sd0; ov_r <= 13'd0;
            dxs_r <= 21'sd0; dys_r <= 21'sd0; d2_r <= 43'sd0;
            pxi_r <= 18'd0; pyi_r <= 18'd0; pxj_r <= 18'd0; pyj_r <= 18'd0;
            ap_xi <= 18'd0; ap_yi <= 18'd0; ap_xj <= 18'd0; ap_yj <= 18'd0;
            ap_vxi <= 20'sd0; ap_vyi <= 20'sd0; ap_vxj <= 20'sd0; ap_vyj <= 20'sd0;
            va_r <= 24'sd0; vb_r <= 24'sd0;
            dxn_r <= 24'sd0; dyn_r <= 24'sd0; adjx_r <= 24'sd0; adjy_r <= 24'sd0;
            sp_sel <= {NPART{1'b0}};
            sp_x_r <= 18'd0; sp_y_r <= 18'd0;
            sp_vx_r <= 20'sd0; sp_vy_r <= 20'sd0;
            sp_chg_r <= 2'd0; sp_who_r <= 2'd0;
            for (zi = 0; zi < NPART; zi = zi + 1) begin
                p_x[zi] <= 18'd0; p_y[zi] <= 18'd0;
                p_vx[zi] <= 20'sd0; p_vy[zi] <= 20'sd0;
                p_chg[zi] <= 2'd0; p_who[zi] <= 2'd0; p_ok[zi] <= 1'b0;
            end
            for (zi = 0; zi < KLANES; zi = zi + 1) begin
                d_start[zi] <= 1'b0; d_x0[zi] <= 18'd0; d_y0[zi] <= 18'd0;
                d_vx0[zi] <= 20'sd0; d_vy0[zi] <= 20'sd0; d_angsel[zi] <= 2'd0;
                l_pidx[zi] <= 7'd0; l_live[zi] <= 1'b0;
            end
            for (zi = 0; zi < 16; zi = zi + 1)
                ev_mem[zi] <= 14'd0;
        end else begin
            nl = 0;
            // ★ 2026-10-17：原来写死 d_start[0..3] —— KLANES=1 时这些下标**越界**
            //   （数组只有 1 项，xvlog 直接编译不过）。改按参数循环。
            for (ck = 0; ck < KLANES; ck = ck + 1)
                d_start[ck] <= 1'b0;

            // ---- 出生（★ 2026-10-17 板上"幽灵写"修复）----
            //   实况：任何 idx≠0 的出生都会把"槽 0"的 x/y/vx/vy/alive 半写一遍
            //   （who/chg 不写）——与事务间隔无关、确定性复现；而 RTL 仿真与**综合
            //   网表门级仿真都干净** ⇒ 判为硅级毛刺（写使能锥贴着时序极限）。
            //   修法：引擎侧先**寄存一拍**——one-hot `sp_sel` + 数据寄存器；各槽的
            //   写使能直接由 FF 输出（sp_sel[zi]），不再有"spawn_idx 比较器 -> 宽
            //   写使能扇出"的长组合锥。出生整体晚一拍生效（驱动侧毫秒级等待，无感）。
            //   越界防护不变：6 位 idx 可能超出 NPART（8/16/32），直接比整数参数。
            sp_sel <= {NPART{1'b0}};
            if (spawn_pulse && (spawn_idx < NPART)) begin
                sp_sel[spawn_idx] <= 1'b1;
                sp_x_r <= spawn_x;   sp_y_r <= spawn_y;
                sp_vx_r <= spawn_vx; sp_vy_r <= spawn_vy;
                sp_chg_r <= spawn_chg; sp_who_r <= spawn_who;
            end
            for (zi = 0; zi < NPART; zi = zi + 1) begin
                if (sp_sel[zi]) begin
                    p_x[zi] <= sp_x_r;   p_y[zi] <= sp_y_r;
                    p_vx[zi] <= sp_vx_r; p_vy[zi] <= sp_vy_r;
                    p_chg[zi] <= sp_chg_r; p_who[zi] <= sp_who_r;
                    p_ok[zi] <= 1'b1;
                end
            end
            // ★ 2026-10-17 v6 实测修正：别在 32 路循环里做 `if(!p_ok[zi]) nl=nl+1`
            //   ——综合成了 32 项条件加法链（sp_sel -> nl，8.5ns 走线，WNS -1.85）。
            //   sp_sel 是 one-hot（至多一位）⇒ 或归约后单次 +1 即可，语义完全等价。
            if (|(sp_sel & ~p_ok_v))
                nl = nl + 1;
            // ---- 清场 ----
            if (clear) begin
                for (zi = 0; zi < NPART; zi = zi + 1)
                    p_ok[zi] <= 1'b0;
                st_substeps <= 16'd0; st_goal <= 16'd0;
                st_annihilate <= 16'd0; st_collide <= 16'd0;
                st_bounce <= 16'd0; st_evdrop <= 16'd0;
            end

            // ---- 事件 FIFO 读口 ----
            if (ev_pop && (ev_count != 5'd0)) begin
                ev_data <= ev_mem[ev_rp];
                ev_rp <= ev_rp + 4'd1;
                ev_count <= ev_count - 5'd1;
                ev_empty <= (ev_count == 5'd1);
            end

            // ---- 主状态机 ----
            case (st)
            S_IDLE: begin
                busy <= 1'b0;
                if (run_pulse && (run_n != 16'd0)) begin
                    run_left <= run_n; base <= 7'd0;
                    busy <= 1'b1; paused <= 1'b0;
                    st <= S_LAUNCH;
                end
            end

            S_LAUNCH: begin
                for (ck = 0; ck < KLANES; ck = ck + 1) begin
                    if (((base + ck) < NPART) && p_ok[base + ck]) begin
                        l_pidx[ck] <= base + ck;
                        l_live[ck] <= 1'b1;
                        d_x0[ck] <= p_x[base + ck];
                        d_y0[ck] <= p_y[base + ck];
                        d_vx0[ck] <= p_vx[base + ck];
                        d_vy0[ck] <= p_vy[base + ck];
                        d_angsel[ck] <= (p_chg[base + ck] == 2'b01) ? 2'd0 :
                                        ((p_chg[base + ck] == 2'b11) ? 2'd1
                                                                     : 2'd2);
                        d_start[ck] <= 1'b1;
                    end else begin
                        l_live[ck] <= 1'b0;
                    end
                end
                st <= S_WAIT;
            end

            S_WAIT: begin
                lanes_idle = 1'b1;
                for (ck = 0; ck < KLANES; ck = ck + 1)
                    if (l_live[ck] && !l_done[ck]) lanes_idle = 1'b0;
                if (lanes_idle) begin
                    for (ck = 0; ck < KLANES; ck = ck + 1) begin
                        if (l_live[ck]) begin
                            if (l_res[ck] == 2'd1) begin
                                p_ok[l_pidx[ck]] <= 1'b0;
                                st_goal <= st_goal + 16'd1;
                                nl = nl - 1;
                                if (ev_count != 5'd15) begin
                                    ev_mem[ev_wp] <= {l_pidx[ck][5:0],
                                                      edge_of(l_ex[ck], l_ey[ck]),
                                                      p_who[l_pidx[ck][5:0]],
                                                      p_chg[l_pidx[ck][5:0]],
                                                      EV_GOAL};
                                    ev_wp <= ev_wp + 4'd1;
                                    ev_count <= ev_count + 5'd1;
                                    ev_empty <= 1'b0;
                                end else begin
                                    st_evdrop <= st_evdrop + 16'd1;
                                end
                            end else begin
                                p_x[l_pidx[ck]] <= l_ex[ck];
                                p_y[l_pidx[ck]] <= l_ey[ck];
                                p_vx[l_pidx[ck]] <= l_vxo[ck];
                                p_vy[l_pidx[ck]] <= l_vyo[ck];
                                if (l_res[ck] == 2'd2)
                                    st_bounce <= st_bounce + 16'd1;
                            end
                        end
                    end
                    if ((base + KLANES) >= NPART) begin
                        if (n_alive >= 7'd2) begin
                            cs <= C_SEEK; ci <= 7'd0; cj <= 7'd1;
                            st <= S_COLL;
                        end else begin
                            // ★ 场上不足 2 颗：碰撞相整段跳过（空场扫描 2016 对纯浪费）
                            st <= S_NEXT;
                        end
                    end else begin
                        base <= base + KLANES[6:0];
                        st <= S_LAUNCH;
                    end
                end
            end

            // ---------------- 碰撞相 ----------------
            S_COLL: begin
                case (cs)
                C_IDLE: begin
                    ci <= 6'd0; cj <= 6'd1; cs <= C_SEEK;
                end
                C_SEEK: begin
                    if (ci >= NPART) begin
                        cs <= C_IDLE;
                        st <= S_NEXT;
                    end else if (cj >= NPART) begin
                        ci <= ci + 7'd1;
                        cj <= ci + 7'd2;
                    end else if (p_ok[ci] && p_ok[cj]) begin
                        cs <= C_CHK;
                    end else begin
                        cj <= cj + 7'd1;
                    end
                end
                C_CHK: begin
                    // 第 0 段：阵列读寄存（mux -> FF）—— 治"ci -> mux -> 相减 -> DSP CE"
                    //   一拍 9.5ns 的 -0.2ns 违例（7 个端点全是这条锥）
                    pxi_r <= p_x[ci]; pyi_r <= p_y[ci];
                    pxj_r <= p_x[cj]; pyj_r <= p_y[cj];
                    cs <= C_CHK2;
                end
                C_CHK2: begin
                    // 第 1 段：AABB 预筛（寄存值相减 + 比较，浅）+ 距离分量寄存
                    if ((dxs_rw >= 21'sd5632) || (dxs_rw <= -21'sd5632) ||
                        (dys_rw >= 21'sd5632) || (dys_rw <= -21'sd5632)) begin
                        cj <= cj + 6'd1;
                        cs <= C_SEEK;
                    end else begin
                        dxs_r <= dxs_rw;
                        dys_r <= dys_rw;
                        cs <= C_CHK3;
                    end
                end
                C_CHK3: begin
                    // 第 2 段：d² = dxs²+dys²（DSP 从寄存操作数出发，结果寄存）
                    d2_r <= d2_w;
                    cs <= C_CHK4;
                end
                C_CHK4: begin
                    // 第 3 段：d² 判定 + 电荷判定（全部用寄存值）
                    if ((d2_r >= 43'sd31719424) || (d2_r == 43'd0)) begin
                        cj <= cj + 6'd1;
                        cs <= C_SEEK;
                    end else if (((p_chg[ci] == 2'b01) && (p_chg[cj] == 2'b11)) ||
                                 ((p_chg[ci] == 2'b11) && (p_chg[cj] == 2'b01))) begin
                        p_ok[ci] <= 1'b0; p_ok[cj] <= 1'b0;
                        st_annihilate <= st_annihilate + 16'd1;
                        nl = nl - 2;
                        if (ev_count <= 5'd13) begin
                            ev_mem[ev_wp] <= {ci, 2'd0, p_who[ci], p_chg[ci], EV_ANNIH};
                            ev_mem[ev_wp + 4'd1] <= {cj, 2'd0, p_who[cj], p_chg[cj], EV_ANNIH};
                            ev_wp <= ev_wp + 4'd2;
                            ev_count <= ev_count + 5'd2;
                            ev_empty <= 1'b0;
                        end else begin
                            st_evdrop <= st_evdrop + 16'd1;
                        end
                        cj <= cj + 6'd1;
                        cs <= C_SEEK;
                    end else begin
                        sq_n <= d2_r; sq_res <= 43'd0;
                        sq_bit <= 43'd1 << 40; sq_pre <= 1'b1;
                        cs <= C_SQ;
                    end
                end
                C_SQ: begin
                    // ★ 两段式（10-16 实测：定标与主步混在一起会在扣减后误判再降位、
                    //   根差 ~1%——Python 复刻两段式 20011 例零错后才改的这里）
                    if (sq_pre) begin
                        if ((sq_bit > sq_n) && (sq_bit != 43'd0))
                            sq_bit <= sq_bit >> 2;
                        else
                            sq_pre <= 1'b0;
                    end else if (sq_bit == 43'd0) begin
                        d_root <= sq_res[12:0];
                        ov_r <= 13'd5632 - sq_res[12:0];
                        dv_den <= sq_res[12:0];
                        dv_num <= (dxs_abs_r << 20);
                        dv_rem <= 43'd0; dv_q <= 43'd0; dv_i <= 6'd43;   // ★ 满宽 43 拍（39 拍=只吃高 39 位、结果差 16 倍，10-16 实测）
                        dv_neg <= dxs_r[20]; dv_which <= 1'b0;
                        cs <= C_DIV;
                    end else begin
                        if ((sq_res + sq_bit) <= sq_n) begin
                            sq_n <= sq_n - (sq_res + sq_bit);
                            sq_res <= (sq_res >> 1) + sq_bit;
                        end else begin
                            sq_res <= sq_res >> 1;
                        end
                        sq_bit <= sq_bit >> 2;
                    end
                end
                C_DIV: begin
                    if (dv_i == 6'd0) begin
                        if (dv_which == 1'b0) begin
                            nx_r <= dv_neg ? -$signed({1'b0, dv_q[20:0]})
                                           : $signed({1'b0, dv_q[20:0]});
                            dv_num <= (dys_abs_r << 20);
                            dv_rem <= 43'd0; dv_q <= 43'd0; dv_i <= 6'd43;   // ★ 满宽 43 拍（39 拍=只吃高 39 位、结果差 16 倍，10-16 实测）
                            dv_neg <= dys_r[20]; dv_which <= 1'b1;
                        end else begin
                            ny_r <= dv_neg ? -$signed({1'b0, dv_q[20:0]})
                                           : $signed({1'b0, dv_q[20:0]});
                            // ★ 2026-10-17：除法末拍顺带寄存写回所需的 8 个粒子分量
                            //   （C_SQ/C_DIV 期间无人写 p_*，值稳定；mux -> FF 浅）
                            ap_xi <= p_x[ci]; ap_yi <= p_y[ci];
                            ap_xj <= p_x[cj]; ap_yj <= p_y[cj];
                            ap_vxi <= p_vx[ci]; ap_vyi <= p_vy[ci];
                            ap_vxj <= p_vx[cj]; ap_vyj <= p_vy[cj];
                            cs <= C_APPLY1;
                        end
                    end else begin
                        if ({dv_rem[41:0], dv_num[42]} >= {30'd0, dv_den}) begin
                            dv_rem <= {dv_rem[41:0], dv_num[42]} - {30'd0, dv_den};
                            dv_q <= {dv_q[41:0], 1'b1};
                        end else begin
                            dv_rem <= {dv_rem[41:0], dv_num[42]};
                            dv_q <= {dv_q[41:0], 1'b0};
                        end
                        dv_num <= dv_num << 1;
                        dv_i <= dv_i - 6'd1;
                    end
                end
                C_APPLY1: begin
                    // 投影量 va/vb 与推出量 adjx/adjy（DSP 从寄存操作数出发 -> FF）
                    va_r <= va_c;
                    vb_r <= vb_c;
                    adjx_r <= adjx_c;
                    adjy_r <= adjy_c;
                    cs <= C_APPLY2;
                end
                C_APPLY2: begin
                    // dv = vb-va（寄存值相减）；法向速度差 dxn/dyn = (dv*n)>>20
                    dxn_r <= dxn_c;
                    dyn_r <= dyn_c;
                    cs <= C_APPLY3;
                end
                C_APPLY3: begin
                    // 推出 + 夹紧 + 弹性速度回写（组合运算是镜像 pe_fix 的逐位翻版）
                    p_x[ci] <= clampx($signed({4'b0, ap_xi}) - adjx_r);
                    p_y[ci] <= clampy($signed({4'b0, ap_yi}) - adjy_r);
                    p_x[cj] <= clampx($signed({4'b0, ap_xj}) + adjx_r);
                    p_y[cj] <= clampy($signed({4'b0, ap_yj}) + adjy_r);
                    // ★ 符号扩展必须复制最高位（{5'b0,v} 会把负速度零扩展成巨正数——
                    //   10-16 实测 T4 速度爆到 2000px/s 的根因）
                    p_vx[ci] <= clampv($signed({{5{ap_vxi[19]}}, ap_vxi}) + dxn_r);
                    p_vy[ci] <= clampv($signed({{5{ap_vyi[19]}}, ap_vyi}) + dyn_r);
                    p_vx[cj] <= clampv($signed({{5{ap_vxj[19]}}, ap_vxj}) - dxn_r);
                    p_vy[cj] <= clampv($signed({{5{ap_vyj[19]}}, ap_vyj}) - dyn_r);
                    st_collide <= st_collide + 16'd1;
                    cj <= cj + 6'd1;
                    cs <= C_SEEK;
                end
                default: cs <= C_IDLE;
                endcase
            end

            S_NEXT: begin
                st_substeps <= st_substeps + 16'd1;
                if (run_left > 16'd1) begin
                    run_left <= run_left - 16'd1;
                    base <= 7'd0;
                    if (stop) begin
                        paused <= 1'b1;
                        st <= S_HOLD;
                    end else begin
                        st <= S_LAUNCH;
                    end
                end else begin
                    run_left <= 16'd0;
                    busy <= 1'b0;
                    st <= S_IDLE;
                end
            end
            S_HOLD: begin
                paused <= 1'b1;
                if (run_pulse && (run_n != 16'd0)) begin
                    run_left <= run_n; base <= 7'd0;
                    paused <= 1'b0; st <= S_LAUNCH;
                end
            end
            endcase

            if (clear)
                n_alive <= 7'd0;
            else
                n_alive <= n_alive + nl[6:0];
        end
    end

endmodule
