// ============================================================================
// traj_lane.v —— V3「并行轨迹束」的单条轨迹积分器（**已验证版：一拍一步**）
//
// 状态：本版已通过 `tb_traj_bundle.v` 的两个场景（fan/goal），17 路结果与定点黄金
//       模型 `tools/v3_fixedpoint.py` **逐位一致**。
//       ⚠ 资源：OOC 综合实测 LUT 51.7% / DSP48 77%(170) 且 100MHz 时序不收敛
//         —— 上板前必须瘦身：见 `traj_lane_mc_wip.v`（多周期版，一个乘法器时分
//         复用；目前还有时序 bug 未通过，请继续在它上面做，别动本文件）。
//
// 为什么要有它：engine.py 的 tangent_scan() 是在入射角附近**串行**扫一簇轨迹
//   （源码注释原文："PC 端串行版；上板后由 PL 并行完成"）。V3 就是把这一簇
//   用 N 路并行积分器同时算出来 —— 比赛要展示的"并行性"就在这条线上。
//
// 每步（与 Python 的 step_fixed 一一对应）：
//     ang2 = (ang*ang + 2^19) >>> 20          // Q2.20 平方，回 Q2.20
//     cos  = 2^20 - (ang2 >>> 1)              // 二阶小角：cos ≈ 1 - a²/2
//     sin  = ang                              // |a| <= 0.0125 rad，三阶项可忽略
//     nvx  = ((vx*cos + 2^19) >>> 20) - ((vy*sin + 2^19) >>> 20)
//     nvy  = ((vx*sin + 2^19) >>> 20) + ((vy*cos + 2^19) >>> 20)
//     x   += (nvx*273 + 32768) >>> 16          // 273/2^16 ≈ 1/240 = dt
//     y   += (nvy*273 + 32768) >>> 16
//   所有右移都"加半 LSB 再算术右移" = 四舍五入
//
// 关键设计：**转角由 PS 预先算好写进块表**（ang = OMEGA_SIGN·q·B/K·DT 只依赖 (B,q)）
//   → RTL 里连一次乘法都不用，且天然与黄金模型逐位一致。
//
// 表格位域（PS 打包）：
//   块表：每项 95 位 = {ang[22:0], x0[17:0], y0[17:0], x1[17:0], y1[17:0]}（见下方取位）
//   洞表：每项 40 位 = {en, edge[1:0], target, w[17:0], c[17:0]}
// ============================================================================
`timescale 1ns / 1ps

module traj_lane #(
    parameter integer NBLK = 9
)(
    input  wire        clk,
    input  wire        rstn,

    input  wire        start,
    input  wire [17:0] x0, y0,
    input  wire signed [19:0] vx0, vy0,   // ★ 必须 signed（无符号会让负速度变成巨大正数）
    input  wire [11:0] max_steps,
    input  wire [3:0]  max_bounce,
    input  wire [17:0] ball_r, field_w, field_h,
    input  wire        side,

    input  wire [NBLK*95-1:0] blk_tab,
    input  wire [1:0]  n_holes,
    input  wire [4*40-1:0]    hole_tab,

    output reg  [1:0]  res,              // 0=步数用尽 1=goal 2=撞墙
    output reg  [17:0] ex, ey,
    output reg  [11:0] n_step,
    output reg  [3:0]  n_bounce,
    output reg         done,

    // 轨迹点握手（17 路共用 BRAM 写口 -> 撞车时最低序号优先，其余停等）
    output reg         pt_req,
    input  wire        pt_gnt,
    output reg  [7:0]  pt_we_idx,
    output reg  [35:0] pt_we_data        // {x[17:0], y[17:0]}
);

    // ---- 状态 ----
    reg [17:0] x, y;
    reg signed [19:0] vx, vy;
    reg [11:0] step_i;
    reg [3:0]  bounce_i;
    reg        busy;
    reg [17:0] lastx, lasty;
    reg [7:0]  pt_idx;

    // ---- 块查找（组合；闭区间 + 表中靠前优先，与 engine.Block.contains 一致）----
    integer k;
    reg        found;
    reg signed [22:0] ang_c;
    always @(*) begin
        ang_c = 23'sd0;
        found = 1'b0;
        for (k = 0; k < NBLK; k = k + 1) begin
            if (!found &&
                (x >= blk_tab[k*95 + 54 +: 18]) && (x <= blk_tab[k*95 + 18 +: 18]) &&
                (y >= blk_tab[k*95 + 36 +: 18]) && (y <= blk_tab[k*95 +  0 +: 18])) begin
                ang_c = blk_tab[k*95 + 72 +: 23];
                found = 1'b1;
            end
        end
    end

    // ---- 一步的算术（组合）----
    reg signed [45:0] ang2_r;
    reg signed [22:0] cos_c, sin_c;
    reg signed [42:0] mx1, my1, mx2, my2;
    reg signed [19:0] nvx_c, nvy_c;
    reg signed [38:0] dx_c, dy_c;
    reg [17:0] nx_c, ny_c;
    always @(*) begin
        ang2_r = (ang_c * ang_c + 46'sd524288) >>> 20;      // +2^19 = 半 LSB
        cos_c  = 23'sd1048576 - (ang2_r >>> 1);
        sin_c  = ang_c;
        mx1 = vx * cos_c;  my1 = vy * sin_c;
        mx2 = vx * sin_c;  my2 = vy * cos_c;
        nvx_c = ((mx1 + 43'sd524288) >>> 20) - ((my1 + 43'sd524288) >>> 20);
        nvy_c = ((mx2 + 43'sd524288) >>> 20) + ((my2 + 43'sd524288) >>> 20);
        dx_c  = nvx_c * 20'sd273;
        dy_c  = nvy_c * 20'sd273;
        nx_c  = x + ((dx_c + 39'sd32768) >>> 16);
        ny_c  = y + ((dy_c + 39'sd32768) >>> 16);
    end

    // ---- 边界与洞口（组合）----
    // 顺序严格照 engine.preview：先左右（夹住 x、翻 vx）、再上下（夹住 y、翻 vy）；
    // 洞用**已夹住**的坐标判定，且左右侧的洞优先于上下侧。
    reg [17:0] tx_c, ty_c;
    reg        wall_c, goal_c;
    integer h;
    reg        hole_en;
    reg [17:0] hc, hw;
    always @(*) begin
        tx_c = nx_c;
        ty_c = ny_c;
        wall_c = 1'b0;
        goal_c = 1'b0;
        if (nx_c < ball_r) begin
            wall_c = 1'b1; tx_c = ball_r;
            for (h = 0; h < 4; h = h + 1) begin
                if (!goal_c && (h < n_holes) && hole_tab[h*40 + 39] &&
                    (hole_tab[h*40 + 37 +: 2] == 2'd0) && hole_tab[h*40 + 36]) begin
                    hc = hole_tab[h*40 + 0 +: 18];
                    hw = hole_tab[h*40 + 18 +: 18];
                    if ((ny_c >= hc - (hw >> 1)) && (ny_c <= hc + (hw >> 1))) goal_c = 1'b1;
                end
            end
        end else if (nx_c > field_w - ball_r) begin
            wall_c = 1'b1; tx_c = field_w - ball_r;
            for (h = 0; h < 4; h = h + 1) begin
                if (!goal_c && (h < n_holes) && hole_tab[h*40 + 39] &&
                    (hole_tab[h*40 + 37 +: 2] == 2'd1) && hole_tab[h*40 + 36]) begin
                    hc = hole_tab[h*40 + 0 +: 18];
                    hw = hole_tab[h*40 + 18 +: 18];
                    if ((ny_c >= hc - (hw >> 1)) && (ny_c <= hc + (hw >> 1))) goal_c = 1'b1;
                end
            end
        end
        if (!goal_c) begin
            if (ny_c < ball_r) begin
                wall_c = 1'b1; ty_c = ball_r;
                for (h = 0; h < 4; h = h + 1) begin
                    if (!goal_c && (h < n_holes) && hole_tab[h*40 + 39] &&
                        (hole_tab[h*40 + 37 +: 2] == 2'd2) && hole_tab[h*40 + 36]) begin
                        hc = hole_tab[h*40 + 0 +: 18];
                        hw = hole_tab[h*40 + 18 +: 18];
                        if ((tx_c >= hc - (hw >> 1)) && (tx_c <= hc + (hw >> 1))) goal_c = 1'b1;
                    end
                end
            end else if (ny_c > field_h - ball_r) begin
                wall_c = 1'b1; ty_c = field_h - ball_r;
                for (h = 0; h < 4; h = h + 1) begin
                    if (!goal_c && (h < n_holes) && hole_tab[h*40 + 39] &&
                        (hole_tab[h*40 + 37 +: 2] == 2'd3) && hole_tab[h*40 + 36]) begin
                        hc = hole_tab[h*40 + 0 +: 18];
                        hw = hole_tab[h*40 + 18 +: 18];
                        if ((tx_c >= hc - (hw >> 1)) && (tx_c <= hc + (hw >> 1))) goal_c = 1'b1;
                    end
                end
            end
        end
    end

    // ---- 轨迹点判据：位移平方 > (8px)²（等价 hypot>8，不做开方）----
    reg signed [18:0] ddx, ddy;
    reg signed [41:0] dist2;
    reg               put_pt_c;
    always @(*) begin
        ddx = $signed({1'b0, nx_c}) - $signed({1'b0, lastx});
        ddy = $signed({1'b0, ny_c}) - $signed({1'b0, lasty});
        dist2 = ddx*ddx + ddy*ddy;
        put_pt_c = (dist2 > 42'sd4194304);                  // 64 * 256 * 256
    end

    // ---- 一步一拍 ----
    always @(posedge clk) begin
        if (!rstn) begin
            busy <= 1'b0; done <= 1'b0; res <= 2'd0;
            x <= 18'd0; y <= 18'd0; vx <= 20'sd0; vy <= 20'sd0;
            step_i <= 12'd0; bounce_i <= 4'd0; n_step <= 12'd0; n_bounce <= 4'd0;
            pt_req <= 1'b0; pt_idx <= 8'd0;
            lastx <= 18'd0; lasty <= 18'd0; ex <= 18'd0; ey <= 18'd0;
            ang_c <= 23'sd0;
        end else begin
            if (start) begin
                // 起点 (x0,y0) 由 TB/PS 自己记（它是已知的），lane 不占握手
                x <= x0; y <= y0; vx <= vx0; vy <= vy0;
                step_i <= 12'd0; bounce_i <= 4'd0;
                pt_idx <= 8'd0; lastx <= x0; lasty <= y0;
                busy <= 1'b1; done <= 1'b0; res <= 2'd0; pt_req <= 1'b0;
            end else if (busy) begin
                if (put_pt_c && !pt_gnt) begin
                    pt_req <= 1'b1;                           // 想写点但没轮到 -> 原地等
                end else begin
                    pt_req <= 1'b0;
                    if (step_i >= max_steps) begin            // 步数用尽
                        busy <= 1'b0; done <= 1'b1; res <= 2'd0;
                        ex <= x; ey <= y; n_step <= step_i;
                    end else if (wall_c) begin                // 撞墙 / 进洞
                        busy <= 1'b0; done <= 1'b1;
                        n_step <= step_i + 12'd1;
                        if (goal_c) begin
                            // ★ 进洞：终点用**未夹住**的穿越点（engine.preview 进洞时
                            //   append 的是尚未 clamp 的 (x,y)）
                            res <= 2'd1; ex <= nx_c; ey <= ny_c;
                        end else begin
                            // 撞墙：用夹住后的位置（引擎此时 x=BALL_R / FIELD-BALL_R）
                            res <= 2'd2; ex <= tx_c; ey <= ty_c;
                            n_bounce <= bounce_i + 4'd1;
                        end
                    end else begin
                        x <= nx_c; y <= ny_c; vx <= nvx_c; vy <= nvy_c;
                        step_i <= step_i + 12'd1;
                        if (put_pt_c) begin
                            pt_we_idx <= pt_idx;
                            pt_we_data <= {nx_c, ny_c};
                            pt_idx <= pt_idx + 8'd1;
                            lastx <= nx_c; lasty <= ny_c;
                        end
                    end
                end
            end
        end
    end

endmodule
