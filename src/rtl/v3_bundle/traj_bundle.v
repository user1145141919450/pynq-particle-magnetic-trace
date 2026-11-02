// ============================================================================
// traj_bundle.v —— V3「并行轨迹束」（v2 2026-10-17：物理 lane 时间复用）
//
// 结构（v2）：
//   · NLANES(=17) 条**逻辑射线** × PLANES(=4) 个物理 traj_lane：
//     射线 0..16 按「空闲即派发」动态调度上 lane（一条完成立刻派下一条）；
//     每条射线完成即把 {res,ex,ey,step,bounce} 写到 res_addr = **逻辑射线号**
//     的结果口 —— 对 AXI 层与 17 路静态并行**同构**（17 个结果槽、done/
//     STATUS 语义、寄存器图一字不动），唯一区别是一簇总耗时 ≈ ceil(17/4) 倍
//     单射线（AXI 往返开销仍是总量主体，量级不变）。
//   · 为什么改（2026-10-16 实装教训）：17 路静态并行 = 17×1.26k LUT，
//     粒子引擎入 BD 后全设计 53.8k / 器件 53.2k —— place 前 DRC 直接拦死。
//     4 路复用立省 ~16k LUT。**判定/端点逐位契约不变** —— tb 三档向量
//     （fan/goal/bounce）只比 AXI 结果，逐位回归。
//   · 轨迹点口（pt_*）保留但封装层不用（v3_bundle_axi 里悬空、综合自动修剪）——
//     维持旧接口签名。
//
// 契约（承 v1，调度器新增两条）：
//   · 各 lane 的本地常数寄存（br_r/fw_r/hlo_r…）在 !busy 拍跟随输入 ⇒ 调度器
//     **先寄存射线初速度（lane_vx/vy，一拍）再下一拍打 start**，保证 lane 采样
//     时输入稳定（同 v1 "PS 先写参数再 start" 的老契约）。
//   · 结果记账：lane done 打一拍（lane_done_d1）后捕获 —— lane 的 done 与结果
//     寄存器同拍更新，直接拿 done 会取到旧值（v1 注释同款）。结果在队
//     （s_dnq）的 lane 不再派发新射线 ⇒ 同一 lane 不可能压两条结果。
// ============================================================================
`timescale 1ns / 1ps

module traj_bundle #(
    parameter integer NLANES = 17,   // 逻辑射线数（AXI 侧不变）
    parameter integer PLANES = 2,    // 物理 lane 数（2026-10-22 4→2：为差距1腾 LUT；17 路仍逐位一致，仅约 2× 慢）
    parameter integer NBLK   = 9
)(
    input  wire        clk,
    input  wire        rstn,

    // ---- 启动与场景参数 ----
    input  wire        start,            // 单拍脉冲：启动一簇（调度器自 ray0 起派发）
    input  wire [17:0] x0, y0,
    input  wire [11:0] max_steps,
    input  wire [3:0]  max_bounce,
    input  wire [17:0] ball_r, field_w, field_h,
    input  wire        side,
    input  wire [1:0]  n_holes,

    // ---- ★★ 2026-10-19 布置 PLACE（PL 原生）：AABB 障碍 + 方形传送门 ----
    input  wire        obs_en,
    input  wire [17:0] obs_x0, obs_y0, obs_x1, obs_y1,
    input  wire [17:0] obs_cx, obs_cy, obs_ehw, obs_ehh,
    input  wire signed [22:0] obs_ca, obs_sa,
    input  wire        port_en,
    input  wire [17:0] port_x0, port_y0, port_x1, port_y1, port_r,
    // ---- ★★ 2026-10-27 多件：3 障碍 / 2 对门（0 号沿用上面）----
    input  wire        obs1_en, obs2_en,
    input  wire [17:0] obs1_x0, obs1_y0, obs1_x1, obs1_y1,
    input  wire [17:0] obs1_cx, obs1_cy, obs1_ehw, obs1_ehh,
    input  wire signed [22:0] obs1_ca, obs1_sa,
    input  wire [17:0] obs2_x0, obs2_y0, obs2_x1, obs2_y1,
    input  wire [17:0] obs2_cx, obs2_cy, obs2_ehw, obs2_ehh,
    input  wire signed [22:0] obs2_ca, obs2_sa,
    input  wire        port1_en,
    input  wire [17:0] port1_x0, port1_y0, port1_x1, port1_y1,

    // ---- 表格（PS 写）----
    input  wire [NBLK*95-1:0]            blk_tab,
    input  wire [4*40-1:0]               hole_tab,
    input  wire [NLANES*40-1:0]          lane_v_tab,   // 每路 {vx[19:0], vy[19:0]}

    // ---- 结果写出口（-> 封装层 lane_res 寄存器组）----
    output reg         res_we,
    output reg  [4:0]  res_addr,                 // = 逻辑射线号 0..NLANES-1
    output reg  [53:0] res_data,

    // ---- 轨迹点写出口（封装层未用；保接口）----
    output reg         pt_we,
    output reg  [4:0]  pt_lane,                  // = 逻辑射线号
    output reg  [7:0]  pt_idx,
    output reg  [35:0] pt_data,

    output reg         done              // 全部 NLANES 条射线结果写完 = 一簇扫完
);

    // ---- ★★ 洞边界预算（2026-09-30，v1 起沿用）：把"扫描期间不变量"从关键路径上摘下来 ----
    //   在**这里算一次**（4 个洞 × 2 个加法器），各 lane 只寄存算好的上下界 ⇒
    //   判据侧只剩"比较"。语义与 lane 里 br_r/fw_r/fh_r 同一条契约。
    wire [4*18-1:0] h_lo, h_hi;        // 每个洞的 center∓(width>>1)
    wire [4*4-1:0]  h_meta;            // 每洞 {en[1], edge[2], tgt[1]}
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

    // ---- 射线初速度解包（lane_v_tab -> 每射线 40 位 {vx,vy}）----
    wire [39:0] ray_v [0:NLANES-1];
    genvar gv;
    generate
        for (gv = 0; gv < NLANES; gv = gv + 1) begin : g_rv
            assign ray_v[gv] = lane_v_tab[gv*40 +: 40];
        end
    endgenerate

    // ---- PLANES 个物理 lane ----
    wire [PLANES-1:0] lane_done;
    wire [PLANES-1:0] lane_pt_req;
    reg  [PLANES-1:0] lane_pt_gnt;
    wire [7:0]        lane_pt_idx [0:PLANES-1];
    wire [35:0]       lane_pd     [0:PLANES-1];
    wire [1:0]        lane_res_w  [0:PLANES-1];
    wire [17:0]       lane_ex_w   [0:PLANES-1];
    wire [17:0]       lane_ey_w   [0:PLANES-1];
    wire [11:0]       lane_ns_w   [0:PLANES-1];
    wire [3:0]        lane_nb_w   [0:PLANES-1];

    reg               lane_start [0:PLANES-1];   // 调度器打出的 start 脉冲
    reg signed [19:0] lane_vx    [0:PLANES-1];   // 已寄存的射线初速度（先于 start 一拍）
    reg signed [19:0] lane_vy    [0:PLANES-1];

    genvar i;
    generate
        for (i = 0; i < PLANES; i = i + 1) begin : g_lane
            traj_lane #(.NBLK(NBLK)) u_lane (
                .clk(clk), .rstn(rstn), .start(lane_start[i]),
                .x0(x0), .y0(y0),
                .vx0(lane_vx[i]), .vy0(lane_vy[i]),
                .max_steps(max_steps), .max_bounce(max_bounce),
                .mb_inf(1'b0),   // bundle 恒 0（真球规则只在粒子引擎里用）
                .ang_sel(2'd0),  // bundle 恒 +ang（行为与 v1 一字不变）
                .ball_r(ball_r), .field_w(field_w), .field_h(field_h),
                .side(side),
                .obs_en(obs_en),
                .obs_x0(obs_x0), .obs_y0(obs_y0),
                .obs_x1(obs_x1), .obs_y1(obs_y1),
                .obs_cx(obs_cx), .obs_cy(obs_cy),
                .obs_ehw(obs_ehw), .obs_ehh(obs_ehh),
                .obs_ca(obs_ca), .obs_sa(obs_sa),
                .obs1_en(obs1_en), .obs2_en(obs2_en),
                .obs1_x0(obs1_x0), .obs1_y0(obs1_y0),
                .obs1_x1(obs1_x1), .obs1_y1(obs1_y1),
                .obs1_cx(obs1_cx), .obs1_cy(obs1_cy),
                .obs1_ehw(obs1_ehw), .obs1_ehh(obs1_ehh),
                .obs1_ca(obs1_ca), .obs1_sa(obs1_sa),
                .obs2_x0(obs2_x0), .obs2_y0(obs2_y0),
                .obs2_x1(obs2_x1), .obs2_y1(obs2_y1),
                .obs2_cx(obs2_cx), .obs2_cy(obs2_cy),
                .obs2_ehw(obs2_ehw), .obs2_ehh(obs2_ehh),
                .obs2_ca(obs2_ca), .obs2_sa(obs2_sa),
                .port_en(port_en),
                .port_x0(port_x0), .port_y0(port_y0),
                .port_x1(port_x1), .port_y1(port_y1), .port_r(port_r),
                .port1_en(port1_en),
                .port1_x0(port1_x0), .port1_y0(port1_y0),
                .port1_x1(port1_x1), .port1_y1(port1_y1),
                .blk_tab(blk_tab), .n_holes(n_holes),
                .h_lo(h_lo), .h_hi(h_hi), .h_meta(h_meta),
                .res(lane_res_w[i]), .ex(lane_ex_w[i]), .ey(lane_ey_w[i]),
                .vx_out(), .vy_out(),          // bundle 不用提交态速度（悬空）
                .n_step(lane_ns_w[i]), .n_bounce(lane_nb_w[i]),
                .done(lane_done[i]),
                .pt_req(lane_pt_req[i]), .pt_gnt(lane_pt_gnt[i]),
                .pt_we_idx(lane_pt_idx[i]), .pt_we_data(lane_pd[i])
            );
        end
    endgenerate

    // ---- 调度器状态 ----
    reg [4:0]  ray_next;                 // 下一条待派发射线（0..NLANES）
    reg [4:0]  ray_written;              // 已写出结果的射线数
    reg        active;                   // 一簇进行中

    reg [4:0]  s_ray   [0:PLANES-1];     // lane 当前射线号（空闲 = 5'd31）
    reg        s_busy  [0:PLANES-1];     // 占用（含"参数已寄存、start 已打/待打"）
    reg        s_st    [0:PLANES-1];     // 待打 start 脉冲（参数寄存后一拍）
    reg        s_dnq   [0:PLANES-1];     // 结果在队（待写出）
    reg [4:0]  s_dqray [0:PLANES-1];     // 在队结果对应的射线号
    reg [53:0] s_res   [0:PLANES-1];     // 在队结果 {res,ex,ey,ns,nb}

    reg [PLANES-1:0] lane_done_d1;

    integer p, q;
    reg        sel_valid;                // 派发候选：最低空闲 lane
    integer    sel_lane;
    reg        dq_valid;                 // 写出候选：最低在队 lane
    integer    sel_dq;
    always @(*) begin
        sel_valid = 1'b0;
        sel_lane  = 0;
        for (p = PLANES - 1; p >= 0; p = p - 1) begin
            if (!s_busy[p] && !s_dnq[p]) begin   // 结果未写出的 lane 不再派发
                sel_valid = 1'b1;
                sel_lane  = p;                   // 从高往低扫 -> 最低号最后写，胜出
            end
        end
        dq_valid = 1'b0;
        sel_dq   = 0;
        for (p = PLANES - 1; p >= 0; p = p - 1) begin
            if (s_dnq[p]) begin
                dq_valid = 1'b1;
                sel_dq   = p;
            end
        end
    end

    always @(posedge clk) begin
        if (!rstn) begin
            ray_next <= 5'd0; ray_written <= 5'd0; active <= 1'b0;
            done <= 1'b0; res_we <= 1'b0; res_addr <= 5'd0; res_data <= 54'd0;
            lane_done_d1 <= {PLANES{1'b0}};
            pt_we <= 1'b0; pt_lane <= 5'd0; pt_idx <= 8'd0; pt_data <= 36'd0;
            lane_pt_gnt <= {PLANES{1'b0}};
            for (p = 0; p < PLANES; p = p + 1) begin
                s_ray[p] <= 5'd31; s_busy[p] <= 1'b0; s_st[p] <= 1'b0;
                s_dnq[p] <= 1'b0; s_dqray[p] <= 5'd0; s_res[p] <= 54'd0;
                lane_start[p] <= 1'b0; lane_vx[p] <= 20'sd0; lane_vy[p] <= 20'sd0;
            end
        end else begin
            lane_done_d1 <= lane_done;

            // ---- 轨迹点仲裁：最低序号优先（v1 逻辑照搬，宽度 = PLANES）----
            //   该输出封装层未用 -> 综合自动修剪；保留只为接口/调试一致。
            lane_pt_gnt <= {PLANES{1'b0}};
            pt_we <= 1'b0; pt_lane <= 5'd0; pt_idx <= 8'd0; pt_data <= 36'd0;
            for (q = PLANES - 1; q >= 0; q = q - 1) begin
                if (lane_pt_req[q]) begin
                    lane_pt_gnt <= {PLANES{1'b0}};
                    lane_pt_gnt[q] <= 1'b1;
                    pt_we  <= 1'b1;
                    pt_lane <= s_ray[q];
                    pt_idx  <= lane_pt_idx[q];
                    pt_data <= lane_pd[q];
                end
            end

            // ---- 一簇启动 ----
            if (start) begin
                ray_next    <= 5'd0;
                ray_written <= 5'd0;
                active      <= 1'b1;
                done        <= 1'b0;
                for (p = 0; p < PLANES; p = p + 1) begin
                    s_busy[p] <= 1'b0; s_st[p] <= 1'b0; s_dnq[p] <= 1'b0;
                    s_ray[p]  <= 5'd31;
                    lane_start[p] <= 1'b0;
                end
            end
            // ---- 派发：空闲即派（每拍一条；参数寄存与 start 分两拍，见文件头契约）----
            else begin
                if (active && (ray_next < NLANES[4:0]) && sel_valid) begin
                    s_busy[sel_lane] <= 1'b1;
                    s_ray[sel_lane]  <= ray_next;
                    s_st[sel_lane]   <= 1'b1;
                    lane_vx[sel_lane] <= ray_v[ray_next][39:20];
                    lane_vy[sel_lane] <= ray_v[ray_next][19:0];
                    ray_next <= ray_next + 5'd1;
                end
            end
            // ---- start 脉冲（寄存参数后一拍打给对应 lane）----
            for (p = 0; p < PLANES; p = p + 1) begin
                if (s_st[p]) begin
                    lane_start[p] <= 1'b1;
                    s_st[p] <= 1'b0;
                end else begin
                    lane_start[p] <= 1'b0;
                end
            end

            // ---- 完成捕获（done 打一拍后取结果 —— 见文件头契约）----
            for (p = 0; p < PLANES; p = p + 1) begin
                if (lane_done_d1[p] && !start) begin
                    s_dnq[p]   <= 1'b1;
                    s_dqray[p] <= s_ray[p];
                    s_res[p]   <= {lane_res_w[p], lane_ex_w[p], lane_ey_w[p],
                                   lane_ns_w[p], lane_nb_w[p]};
                    s_busy[p]  <= 1'b0;
                    s_ray[p]   <= 5'd31;
                end
            end

            // ---- 结果写出（每拍一条；res_addr = 逻辑射线号）----
            res_we <= dq_valid && !start;
            if (dq_valid && !start) begin
                res_addr <= s_dqray[sel_dq];
                res_data <= s_res[sel_dq];
                s_dnq[sel_dq] <= 1'b0;
                ray_written <= ray_written + 5'd1;
            end

            // ---- 收工判据：17 条全部派发且全部写出 ----
            //   ★ 必须 !start：启动那一拍 ray_next/ray_written 还是**上一簇的 17/17**，
            //     不算门就会把 done 又置 1（tb 轮询会误判"立刻完成"读到空结果）。
            if (!start)
                done <= active && (ray_next == NLANES[4:0]) &&
                        (ray_written == NLANES[4:0]);
        end
    end

endmodule
