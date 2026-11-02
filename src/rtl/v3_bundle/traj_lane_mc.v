// ============================================================================
// traj_lane_mc.v —— V3「并行轨迹束」的单条轨迹积分器（**多周期微操作版 v2**）
//
// 为什么不能"一拍走一步"（2026-09-27 OOC 综合实测）：
//   全组合数据通路（5 个乘法器 + 9 块×4 比较器 + 洞判定，×17 路）综合出来
//   **LUT 51.7% / DSP48 77%(170) 且 100MHz 时序不收敛** —— 塞不进现有采集链。
//   本版：全 lane 只留**一个**乘法器，一步拆成 9 个微操作 × 3 拍 = 27 拍，
//   2000 步 ≈ 540µs（仍比 PC 串行 1276ms 快 ~2400 倍）。
//
// ★ 算法与定点黄金模型 `tools/v3_fixedpoint.py` **逐位一致**（硬要求）：
//   每个乘法是"发操作数 -> 等一拍 -> 收乘积"三拍 —— **收到的时候乘积一定已落进 mreg**。
//   前两版就错在这条上（同一拍既赋值又读派生量；或按"+1 拍"收）。
//   `tb_traj_bundle.v` 逐位比对（res/终点/步数/反弹数），不是"差不多"。
//
// 9 个微操作（每步一轮）：
//   op0: ang*ang        -> ang2_q，cos = 2^20 - ang2/2
//   op1: vx*cos         -> m_vxc
//   op2: vy*sin(=ang)   -> m_vys
//   op3: vx*sin         -> m_vxs（同时寄存 cos_r 给 op4）
//   op4: vy*cos         -> m_vyc
//   op5: nvx*273        -> m_dx
//   op6: nvy*273        -> m_dy
//   op7: 位置更新        nx = x + (dx+H)>>16，ny = y + (dy+H)>>16
//   op8: 边界/洞判定 + 轨迹点握手 + 记账输出
//   （H = 半 LSB 四舍五入；右移一律算术右移，与 Python 的 >> 同语义）
//
// 格式：x,y 18bit 无符号 Q10.8 | vx,vy 20bit **有符号** Q11.8 | ang 23bit 有符号 Q2.20
//       块边界闭区间 + 表中靠前优先（与 engine.Block.contains 一致）
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

    output reg         pt_req,
    input  wire        pt_gnt,
    output reg  [7:0]  pt_we_idx,
    output reg  [35:0] pt_we_data
);

    localparam [3:0] OP_ANG2 = 4'd0, OP_VXC = 4'd1, OP_VYS = 4'd2, OP_VXS = 4'd3,
                     OP_VYC  = 4'd4, OP_DX  = 4'd5, OP_DY  = 4'd6, OP_UPD = 4'd7,
                     OP_JUDGE = 4'd8;

    reg        busy;
    reg [3:0]  op;
    reg [1:0]  sub;                     // 0=发操作数 1=等 2=收

    reg [17:0] x, y;
    reg signed [19:0] vx, vy;
    reg [11:0] step_i;
    reg [3:0]  bounce_i;
    reg [17:0] lastx, lasty;
    reg [7:0]  pt_idx;
    reg [17:0] nx_r, ny_r;
    reg        pt_flag_r;                 // 判定结果的寄存版（打断关键路径）
    reg signed [18:0] ddx_r, ddy_r;       // 位移平方判据也要拆拍（19 位平方+加+比较
                                          // 挤在一拍实测差 1.58ns）
    reg        wall_r, goal_r;
    reg [17:0] tx_r, ty_r;
    reg [1:0]  res_r;
    reg [3:0]  nb_r;
    reg [11:0] ns_r;
    reg [17:0] ex_r, ey_r;
    reg        done_r;

    // ---- 块查找（组合；闭区间 + 靠前优先）----
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

    // ---- 唯一的乘法器 ----
    reg signed [19:0] ma;
    reg signed [22:0] mb;
    reg signed [45:0] mreg;
    always @(posedge clk) mreg <= ma * mb;

    reg signed [45:0] ang2_q;
    wire signed [22:0] cos_w = 23'sd1048576 - ((ang2_q[45:20]) >>> 1);
    reg  signed [22:0] cos_r;
    wire signed [22:0] sin_w = ang_c;

    reg signed [42:0] m_vxc, m_vys, m_vxs, m_vyc;
    reg signed [38:0] m_dx, m_dy;
    reg signed [19:0] nvx_c, nvy_c;

    wire signed [23:0] nvx_w = ((m_vxc + 43'sd524288) >>> 20) - ((m_vys + 43'sd524288) >>> 20);
    wire signed [23:0] nvy_w = ((m_vxs + 43'sd524288) >>> 20) + ((m_vyc + 43'sd524288) >>> 20);

    // ---- 边界与洞（组合，用 nx_r/ny_r）----
    reg [17:0] tx_c, ty_c;
    reg        wall_c, goal_c;
    integer h;
    always @(*) begin
        tx_c = nx_r;
        ty_c = ny_r;
        wall_c = 1'b0;
        goal_c = 1'b0;
        if (nx_r < ball_r) begin
            wall_c = 1'b1; tx_c = ball_r;
            for (h = 0; h < 4; h = h + 1)
                if ((h < n_holes) && hole_tab[h*40+39] && (hole_tab[h*40+37 +: 2] == 2'd0)
                    && hole_tab[h*40+36]
                    && (ny_r >= hole_tab[h*40+0 +: 18] - (hole_tab[h*40+18 +: 18] >> 1))
                    && (ny_r <= hole_tab[h*40+0 +: 18] + (hole_tab[h*40+18 +: 18] >> 1)))
                    goal_c = 1'b1;
        end else if (nx_r > field_w - ball_r) begin
            wall_c = 1'b1; tx_c = field_w - ball_r;
            for (h = 0; h < 4; h = h + 1)
                if ((h < n_holes) && hole_tab[h*40+39] && (hole_tab[h*40+37 +: 2] == 2'd1)
                    && hole_tab[h*40+36]
                    && (ny_r >= hole_tab[h*40+0 +: 18] - (hole_tab[h*40+18 +: 18] >> 1))
                    && (ny_r <= hole_tab[h*40+0 +: 18] + (hole_tab[h*40+18 +: 18] >> 1)))
                    goal_c = 1'b1;
        end
        if (!goal_c) begin
            if (ny_r < ball_r) begin
                wall_c = 1'b1; ty_c = ball_r;
                for (h = 0; h < 4; h = h + 1)
                    if ((h < n_holes) && hole_tab[h*40+39] && (hole_tab[h*40+37 +: 2] == 2'd2)
                        && hole_tab[h*40+36]
                        && (tx_c >= hole_tab[h*40+0 +: 18] - (hole_tab[h*40+18 +: 18] >> 1))
                        && (tx_c <= hole_tab[h*40+0 +: 18] + (hole_tab[h*40+18 +: 18] >> 1)))
                        goal_c = 1'b1;
            end else if (ny_r > field_h - ball_r) begin
                wall_c = 1'b1; ty_c = field_h - ball_r;
                for (h = 0; h < 4; h = h + 1)
                    if ((h < n_holes) && hole_tab[h*40+39] && (hole_tab[h*40+37 +: 2] == 2'd3)
                        && hole_tab[h*40+36]
                        && (tx_c >= hole_tab[h*40+0 +: 18] - (hole_tab[h*40+18 +: 18] >> 1))
                        && (tx_c <= hole_tab[h*40+0 +: 18] + (hole_tab[h*40+18 +: 18] >> 1)))
                        goal_c = 1'b1;
            end
        end
    end

    // ---- 轨迹点判据 ----
    wire signed [18:0] ddx = $signed({1'b0, nx_r}) - $signed({1'b0, lastx});
    wire signed [18:0] ddy = $signed({1'b0, ny_r}) - $signed({1'b0, lasty});
    wire signed [41:0] dist2 = ddx*ddx + ddy*ddy;
    wire put_pt_c = (dist2 > 42'sd4194304);            // (8px)²

    always @(posedge clk) begin
        if (!rstn) begin
            busy <= 1'b0; done_r <= 1'b0; res_r <= 2'd0; op <= 4'd0; sub <= 2'd0;
            x <= 18'd0; y <= 18'd0; vx <= 20'sd0; vy <= 20'sd0;
            step_i <= 12'd0; bounce_i <= 4'd0; ns_r <= 12'd0; nb_r <= 4'd0;
            pt_req <= 1'b0; pt_idx <= 8'd0; lastx <= 18'd0; lasty <= 18'd0;
            nx_r <= 18'd0; ny_r <= 18'd0; ex_r <= 18'd0; ey_r <= 18'd0;
            pt_flag_r <= 1'b0; wall_r <= 1'b0; goal_r <= 1'b0;
            ddx_r <= 19'sd0; ddy_r <= 19'sd0;
            tx_r <= 18'd0; ty_r <= 18'd0;
            ma <= 20'sd0; mb <= 23'sd0; ang2_q <= 46'sd0; cos_r <= 23'sd0;
            m_vxc <= 43'sd0; m_vys <= 43'sd0; m_vxs <= 43'sd0; m_vyc <= 43'sd0;
            m_dx <= 39'sd0; m_dy <= 39'sd0; nvx_c <= 20'sd0; nvy_c <= 20'sd0;
        end else if (!busy) begin
            done_r <= 1'b0;
            if (start) begin
                x <= x0; y <= y0; vx <= vx0; vy <= vy0;
                step_i <= 12'd0; bounce_i <= 4'd0;
                pt_idx <= 8'd0; lastx <= x0; lasty <= y0;
                busy <= 1'b1; res_r <= 2'd0; pt_req <= 1'b0;
                op <= OP_ANG2; sub <= 2'd0;
            end
        end else begin
            if (op == OP_JUDGE) begin
                if (sub == 2'd0) begin
                    // ★ 判定结果先**寄存**再动作（实测：那坨组合逻辑直接驱动寄存器使能时
                    //   关键路径 21 级、@100MHz 差 3.8ns，Source=ny_r_reg/Dest=vx_reg/CE）。
                    //   这里第 0 拍寄存边界/洞与位移差分。
                    ddx_r <= ddx;
                    ddy_r <= ddy;
                    wall_r <= wall_c;
                    goal_r <= goal_c;
                    tx_r <= tx_c;
                    ty_r <= ty_c;
                    sub <= 2'd1;
                end else if (sub == 2'd1) begin
                    // 第 1 拍专门算"位移平方 > (8px)²"（两个 19 位平方 + 加 + 比较，
                    // 挤在一拍仍差 1.58ns，所以单独占一拍）
                    pt_flag_r <= ((ddx_r * ddx_r + ddy_r * ddy_r) > 42'sd4194304);
                    sub <= 2'd2;
                end else begin
                    if (pt_flag_r && !pt_gnt) begin
                        pt_req <= 1'b1;                            // 等授权（停在 sub=1）
                    end else begin
                        pt_req <= 1'b0;
                        if (step_i >= max_steps) begin
                            busy <= 1'b0; done_r <= 1'b1; res_r <= 2'd0;
                            ex_r <= x; ey_r <= y; ns_r <= step_i;   // ★ 报更新前的位置
                            op <= 4'd0; sub <= 2'd0;
                        end else if (wall_r) begin
                            busy <= 1'b0; done_r <= 1'b1;
                            ns_r <= step_i + 12'd1;
                            if (goal_r) begin
                                res_r <= 2'd1; ex_r <= nx_r; ey_r <= ny_r;   // 进洞：未夹住
                            end else begin
                                res_r <= 2'd2; ex_r <= tx_r; ey_r <= ty_r;   // 撞墙：夹住后
                                nb_r <= bounce_i + 4'd1;
                            end
                            op <= 4'd0; sub <= 2'd0;
                        end else begin
                            x <= nx_r; y <= ny_r; vx <= nvx_c; vy <= nvy_c;
                            step_i <= step_i + 12'd1;
                            if (pt_flag_r) begin
                                pt_we_idx <= pt_idx;
                                pt_we_data <= {nx_r, ny_r};
                                pt_idx <= pt_idx + 8'd1;
                                lastx <= nx_r; lasty <= ny_r;
                            end
                            op <= OP_ANG2; sub <= 2'd0;
                        end
                    end
                end
            end else if (sub == 2'd2) begin
                // 收：这一拍乘积一定在 mreg 里（两拍前发的操作数）
                case (op)
                OP_ANG2: ang2_q <= mreg;
                OP_VXC : m_vxc  <= mreg;
                OP_VYS : m_vys  <= mreg;
                OP_VXS : m_vxs  <= mreg;
                OP_VYC : m_vyc  <= mreg;
                OP_DX  : m_dx   <= mreg;
                OP_DY  : m_dy   <= mreg;          // ★ 只收，**不要在这里算 ny**：
                                                  //   m_dy 是同一拍被赋值的（非阻塞），
                                                  //   这里读到的还是上一步的 dy —— 实测
                                                  //   症状就是"x 精确、y 系统性滞后"。
                OP_UPD : begin
                             nx_r <= x + ((m_dx + 39'sd32768) >>> 16);
                             ny_r <= y + ((m_dy + 39'sd32768) >>> 16);
                         end
                default: ;
                endcase
                op <= op + 4'd1;
                sub <= 2'd0;
            end else begin
                if (sub == 2'd0) begin
                    case (op)
                    OP_ANG2: begin ma <= ang_c; mb <= ang_c; end
                    OP_VXC : begin ma <= vx;    mb <= cos_w; end
                    OP_VYS : begin ma <= vy;    mb <= sin_w; end
                    OP_VXS : begin cos_r <= cos_w; ma <= vx; mb <= sin_w; end
                    OP_VYC : begin ma <= vy;    mb <= cos_r; end
                    OP_DX  : begin
                             nvx_c <= nvx_w;
                             ma <= nvx_w; mb <= 23'sd273;
                         end
                    OP_DY  : begin
                             nvy_c <= nvy_w;
                             ma <= nvy_w; mb <= 23'sd273;
                         end
                    default: ;
                    endcase
                end
                sub <= sub + 2'd1;
            end
        end
    end

    always @(*) begin
        res = res_r; ex = ex_r; ey = ey_r; n_step = ns_r; n_bounce = nb_r;
        done = done_r;
    end

endmodule
