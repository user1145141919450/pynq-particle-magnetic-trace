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
    // ★ 2026-10-16（粒子引擎复用）：mb_inf=1 时忽略 max_bounce、撞墙一律反弹续走
    //   （真球规则的"弹到出射为止不限次数"）。bundle 里恒接 0 —— 行为一字不变。
    input  wire        mb_inf,
    // ★★ 2026-10-16（粒子引擎复用）：角度按粒子电荷取正/负/零 —— 0=+ang（原样），
    //   1=-ang，2=零。**一块共享的 blk_tab 就此通吃三种电荷**（此前引擎每路一个
    //   855 位三选一 mux + 855 位寄存器，实装报告里就是它把每路 lane 撑到 4~6k
    //   LUT——bundle 里 17 路共享静态表只要 1.26k/路）。bundle 恒接 0 —— 行为不变。
    input  wire [1:0]  ang_sel,
    input  wire [17:0] ball_r, field_w, field_h,
    input  wire        side,

    // ---- ★★ 2026-10-19 布置 PLACE 上 PL：AABB 障碍 + 方形传送门 ----
    //   为控制时序/LUT，PL 侧用**轴对齐包围盒**近似旋转矩形（PS 先算好 AABB，
    //   已含 BALL_R 扩张），传送门用**方形区**近似圆（PS 传 PORTAL_R 作半边长）。
    //   obs_en=0/port_en=0 时下面的逻辑全不动作，旧行为逐位不变。
    input  wire        obs_en,
    input  wire [17:0] obs_x0, obs_y0, obs_x1, obs_y1,
    input  wire        port_en,
    input  wire [17:0] port_x0, port_y0, port_x1, port_y1, port_r,
    // ★★ 2026-10-19 用户定案：障碍 = **旋转矩形**（不再用 AABB 当碰撞体）。
    //   obs_x0..y1 仍保留作**便宜的预过滤**（AABB 都进不去就跳过局部坐标运算）；
    //   真正的碰撞用 cx/cy/ehw/ehh（已扩张 BALL_R）+ ca/sa（Q2.20，cos(-ang)/sin(-ang)）。
    input  wire [17:0] obs_cx, obs_cy, obs_ehw, obs_ehh,
    input  wire signed [22:0] obs_ca, obs_sa,
    // ---- ★★ 2026-10-27 多件：3 障碍 / 2 对门（同构参数组）----
    //   0 号沿用上面的旧端口；这里加 1/2 号障碍与 1 号门对。
    input  wire        obs1_en, obs2_en,
    input  wire [17:0] obs1_x0, obs1_y0, obs1_x1, obs1_y1,
    input  wire [17:0] obs1_cx, obs1_cy, obs1_ehw, obs1_ehh,
    input  wire signed [22:0] obs1_ca, obs1_sa,
    input  wire [17:0] obs2_x0, obs2_y0, obs2_x1, obs2_y1,
    input  wire [17:0] obs2_cx, obs2_cy, obs2_ehw, obs2_ehh,
    input  wire signed [22:0] obs2_ca, obs2_sa,
    input  wire        port1_en,
    input  wire [17:0] port1_x0, port1_y0, port1_x1, port1_y1,

    input  wire [NBLK*95-1:0] blk_tab,
    input  wire [1:0]  n_holes,
    // ★ 洞的上下界**已经算好**（在 traj_bundle 里算一次），这里只寄存。
    //   原先这里收的是原生 hole_tab、每路各自算 center±(width>>1) ——
    //   那两条进位链 + 洞表到 17 路的长走线，就是 5 条最差路径的**全部**内容
    //   （形状与数字见 traj_bundle.v 顶上的注释）。
    input  wire [4*18-1:0] h_lo, h_hi,   // 每个洞的 center∓(width>>1)
    input  wire [4*4-1:0]  h_meta,       // 每洞 {en[1], edge[2], tgt[1]}

    output reg  [1:0]  res,              // 0=步数用尽 1=goal 2=撞墙
    output reg  [17:0] ex, ey,
    // ★ 2026-10-16（粒子引擎复用）：done 时**提交态**的速度（res=0/2 时有效；
    //   res=1 进洞后无意义）。bundle 不用这两个输出 —— 行为一字不变。
    output wire signed [19:0] vx_out, vy_out,
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
                     OP_JUDGE = 4'd8, OP_LOOK = 4'd9, OP_PHYS = 4'd10;

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
    // ★★ 每路本地寄存的"全局常数"（治高扇出长走线，2026-09-28）：
    //    这些参数一次扫描期间不变，PS 是先写参数再写 start，所以寄存一份语义不变；
    //    但比较器只读本地寄存器后，r_fw/r_ballr/r_fh/r_steps 到 17 路的长走线就没了
    //    （实测关键路径 r_fw_reg -> g_lane[12].goal_r_reg，布线占 6.42ns）。
    reg [17:0] br_r, fw_r, fh_r;
    reg [11:0] ms_r;
    // ★★ 2026-10-04 max_bounce 修复（用户需求"弹性边界回弹"）：
    //   max_bounce 原来是**死输入**（声明后再没被使用，撞墙分支直接结束）——实测写 10
    //   能原值回读但行为纹丝不动。现在按黄金模型 lane_fixed 的语义实装：
    //     goal -> 结束 res=1；非 goal 且"本次碰壁后反弹计数 > max_bounce" -> 结束 res=2；
    //     否则 -> 夹到边界 + 反射速度 + 计数 -> **继续积分**（最多再弹几次由 PS 写寄存器定）。
    //   与 Python 的两条独立检查一致：单步内 x/y 都越界（角碰）算 **2** 次碰壁。
    //   逐位契约仍然成立：tb 只比 {res, 终点, 步数, 反弹数}（不含点流）。
    reg [3:0]  mb_r;                      // max_bounce 的寄存版（同 ms_r 契约）
    reg        x_wall_r, y_wall_r;        // 本步撞的是哪面墙（sub0 捕获）
    reg [4:0]  nb_next_r;                 // 本步后反弹计数（含角碰双计；多一位防回绕）
    reg        pt_flag_r;                 // 判定结果的寄存版（打断关键路径）
    reg signed [18:0] ddx_r, ddy_r;
    // ★★ 洞表本地寄存（2026-09-30，治最后 27 条 setup 违例）
    //   与 br_r/fw_r/fh_r 同一个套路、同一条契约（PS 先写表、再写 start，
    //   扫描期间不变）：在 `!busy` 那拍把洞的上下界抄进本地寄存器。
    //   这一步把两样东西同时挪出关键路径 ——
    //     · 洞表到本路的长走线（原先 1.63ns 就花在这根线上，fo=34）
    //     · center±(width>>1) 的两条进位链（本来每步重算一遍，其实是常量）
    //   之后判据侧只剩"比较"。
    reg [17:0] hlo_r [0:3];
    reg [17:0] hhi_r [0:3];
    reg [1:0]  hed_r [0:3];
    reg        hen_r [0:3];
    reg        htg_r [0:3];
    reg [1:0]  nh_r;                      // n_holes 的寄存版
    // ★★ 2026-10-19 布置 PLACE 上 PL：AABB 障碍 + 方形传送门的常量/判定寄存器
    //    与洞表同一条契约：`!busy` 拍跟随输入（PS 先写场景再写 start）。
    // ★★ 2026-10-27 多件：寄存器按件存（障碍 0/1/2、门对 0/1）；
    //   下面用 wire 把"当前件"接回旧名（下游 OP_PHYS 逻辑一字不改）。
    reg        obs_en_0_r, obs_en_1_r, obs_en_2_r;
    reg [17:0] ox0_0_r, oy0_0_r, ox1_0_r, oy1_0_r;
    reg [17:0] ocx_0_r, ocy_0_r, oehw_0_r, oehh_0_r;
    reg signed [22:0] oca_0_r, osa_0_r;
    reg [17:0] ox0_1_r, oy0_1_r, ox1_1_r, oy1_1_r;
    reg [17:0] ocx_1_r, ocy_1_r, oehw_1_r, oehh_1_r;
    reg signed [22:0] oca_1_r, osa_1_r;
    reg [17:0] ox0_2_r, oy0_2_r, ox1_2_r, oy1_2_r;
    reg [17:0] ocx_2_r, ocy_2_r, oehw_2_r, oehh_2_r;
    reg signed [22:0] oca_2_r, osa_2_r;
    reg        port_en_0_r, port_en_1_r;
    reg [17:0] px0_0_r, py0_0_r, px1_0_r, py1_0_r;
    reg [17:0] px0_1_r, py0_1_r, px1_1_r, py1_1_r;
    reg [17:0] pr_r;
    reg [1:0]  ob_idx;
    reg        pt_pair;
    // ---- 当前件选择（组合）：ob_idx/pt_pair 在每步 OP_JUDGE 入口清 0 ----
    wire obs_en_r = (ob_idx == 2'd0) ? obs_en_0_r :
                    (ob_idx == 2'd1) ? obs_en_1_r : obs_en_2_r;
    wire [17:0] ox0_r = (ob_idx == 2'd0) ? ox0_0_r :
                        (ob_idx == 2'd1) ? ox0_1_r : ox0_2_r;
    wire [17:0] oy0_r = (ob_idx == 2'd0) ? oy0_0_r :
                        (ob_idx == 2'd1) ? oy0_1_r : oy0_2_r;
    wire [17:0] ox1_r = (ob_idx == 2'd0) ? ox1_0_r :
                        (ob_idx == 2'd1) ? ox1_1_r : ox1_2_r;
    wire [17:0] oy1_r = (ob_idx == 2'd0) ? oy1_0_r :
                        (ob_idx == 2'd1) ? oy1_1_r : oy1_2_r;
    wire [17:0] ocx_r = (ob_idx == 2'd0) ? ocx_0_r :
                        (ob_idx == 2'd1) ? ocx_1_r : ocx_2_r;
    wire [17:0] ocy_r = (ob_idx == 2'd0) ? ocy_0_r :
                        (ob_idx == 2'd1) ? ocy_1_r : ocy_2_r;
    wire [17:0] oehw_r = (ob_idx == 2'd0) ? oehw_0_r :
                         (ob_idx == 2'd1) ? oehw_1_r : oehw_2_r;
    wire [17:0] oehh_r = (ob_idx == 2'd0) ? oehh_0_r :
                         (ob_idx == 2'd1) ? oehh_1_r : oehh_2_r;
    wire signed [22:0] oca_r = (ob_idx == 2'd0) ? oca_0_r :
                               (ob_idx == 2'd1) ? oca_1_r : oca_2_r;
    wire signed [22:0] osa_r = (ob_idx == 2'd0) ? osa_0_r :
                               (ob_idx == 2'd1) ? osa_1_r : osa_2_r;
    wire port_en_r = pt_pair ? port_en_1_r : port_en_0_r;
    wire [17:0] px0_r = pt_pair ? px0_1_r : px0_0_r;
    wire [17:0] py0_r = pt_pair ? py0_1_r : py0_0_r;
    wire [17:0] px1_r = pt_pair ? px1_1_r : px1_0_r;
    wire [17:0] py1_r = pt_pair ? py1_1_r : py1_0_r;
    reg [6:0]  tp_cd_r;                    // 穿门冷却（步计数；0=可穿）
    reg [17:0] qx_r, qy_r;                 // OP_PHYS 的落点/速度暂存
    reg signed [19:0] qvx_r, qvy_r;
    reg [6:0]  tp_next_r;
    integer hh;
    reg signed [37:0] sx_r, sy_r;      // ddx²/ddy²（各自寄存，别再和加法器挤一拍）       // 位移平方判据也要拆拍（19 位平方+加+比较
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
    // ★ 查表结果寄存（2026-09-28，治最后 10 条违例）：实装报告里 10 条违例**全是同一形状**
    //   ——源 = x_reg/y_reg、终点 = 乘法器 B 操作数 mb。也就是"位置 -> 9 块比较器链 ->
    //   查出的角度 -> 乘法器操作数"挤在一拍。给查表单独一拍（OP_LOOK）即可。
    reg signed [22:0] ang_r;
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
        if (nx_r < br_r) begin
            wall_c = 1'b1; tx_c = br_r;
            for (h = 0; h < 4; h = h + 1)
                if ((h < nh_r) && hen_r[h] && (hed_r[h] == 2'd0) && htg_r[h]
                    && (ny_r >= hlo_r[h]) && (ny_r <= hhi_r[h]))
                    goal_c = 1'b1;
        end else if (nx_r > fw_r) begin
            wall_c = 1'b1; tx_c = fw_r;
            for (h = 0; h < 4; h = h + 1)
                if ((h < nh_r) && hen_r[h] && (hed_r[h] == 2'd1) && htg_r[h]
                    && (ny_r >= hlo_r[h]) && (ny_r <= hhi_r[h]))
                    goal_c = 1'b1;
        end
        if (!goal_c) begin
            if (ny_r < br_r) begin
                wall_c = 1'b1; ty_c = br_r;
                for (h = 0; h < 4; h = h + 1)
                    if ((h < nh_r) && hen_r[h] && (hed_r[h] == 2'd2) && htg_r[h]
                        && (tx_c >= hlo_r[h]) && (tx_c <= hhi_r[h]))
                        goal_c = 1'b1;
            end else if (ny_r > fh_r) begin
                wall_c = 1'b1; ty_c = fh_r;
                for (h = 0; h < 4; h = h + 1)
                    if ((h < nh_r) && hen_r[h] && (hed_r[h] == 2'd3) && htg_r[h]
                        && (tx_c >= hlo_r[h]) && (tx_c <= hhi_r[h]))
                        goal_c = 1'b1;
            end
        end
    end

    // ---- 轨迹点判据 ----
    wire signed [18:0] ddx = $signed({1'b0, nx_r}) - $signed({1'b0, lastx});
    wire signed [18:0] ddy = $signed({1'b0, ny_r}) - $signed({1'b0, lasty});
    wire signed [41:0] dist2 = ddx*ddx + ddy*ddy;
    wire put_pt_c = (dist2 > 42'sd4194304);            // (8px)²

    // ---- ★★ 2026-10-19 布置 PLACE（PL 原生）：障碍 AABB + 传送门 ----
    //   顺序照抄 PS 的 realtime_ball：墙/洞 -> 障碍 -> 传送门。这里只用比较器
    //   （不占用那唯一的乘法器）：
    //     · 障碍 = 轴对齐包围盒（PS 已按旋转矩形算好 AABB 并扩张 BALL_R）：
    //       穿进去 -> 从**最浅的一面**推出 + 反射该轴速度分量；
    //     · 传送门 = 方形区（半边长 = PORTAL_R）：进入 -> 瞬移到配对门中心，
    //       速度不变，置 84 步冷却（0.35s @240Hz）防连续触发。
    //   口径说明：旋转矩形/圆形在 PS 真球物理里仍是精确的；PL 这版是给
    //   **瞄准预览/粒子风暴**看的视觉近似（用户要"看得见障碍/门"）。
    localparam [6:0] TP_CD = 7'd84;
    wire signed [20:0] vx_abs_c = vx[19] ? (21'sd0 - $signed(vx)) : $signed(vx);
    wire signed [20:0] vy_abs_c = vy[19] ? (21'sd0 - $signed(vy)) : $signed(vy);
    wire [18:0] off_c = {1'b0, pr_r} + {1'b0, br_r};   // port_r + ball_r
    // ★★ 2026-10-19 旋转矩形流水（用户定案）：AABB 预过滤 -> 局部坐标
    //   4 个乘法 -> 最浅面推出/世界回投/法线反射（6 个乘法）-> 圆形门 4 个乘法。
    //   共用那唯一的乘法器，按 stage/idx/phase 推进；乘积用 4 个共享寄存器。
    reg [17:0] bx_r, by_r;                 // 本步结束的原始位置
    reg signed [19:0] bvx_r, bvy_r;        // 本步结束的原始速度
    reg        obs_bb_r;                   // AABB 预过滤命中
    reg [6:0]  tp_dec_r;
    reg [2:0]  ph_stage;                   // 0=局部 1=世界 2=速度 3=圆门 4=生效
    reg [1:0]  ph_idx, ph_phase;           // idx 0..3；phase 0=发/1=等/2=收
    reg signed [19:0] dxo_r, dyo_r;
    reg signed [19:0] lx_r, ly_r;
    reg signed [1:0]  sx_r2, sy_r2;
    reg signed [22:0] nx_r2, ny_r2;
    reg signed [19:0] dot_r;
    reg signed [19:0] wx_r2, wy_r2;
    reg signed [19:0] ovx_r, ovy_r;
    reg [17:0] ox_r, oy_r;
    reg        obs_in_r, p_sq0_r, p_sq1_r, hit0_r, hit1_r;
    reg signed [45:0] p0_r, p1_r, p2_r, p3_r;
    // ★ 2026-10-19 传送门落点：off=pr+br 寄存（常量为 scene 期不变），
    //   目标位置与最终选择分两拍（治 br_r -> y_reg 的最差路径）。
    reg [18:0] off_r;
    reg [17:0] ptx_r, pty_r;
    reg [6:0]  pcd_r;
    localparam [24:0] PORTAL_R2 = 25'd21233664;   // (18px·Q10.8)^2 = 4608²

    // ★ 2026-10-19 旋转矩形/圆门的组合中间量（乘法结果寄存器）
    wire signed [45:0] lx_c = (p0_r - p1_r + 46'sd524288) >>> 20;
    wire signed [45:0] ly_c = (p2_r + mreg + 46'sd524288) >>> 20;
    wire signed [45:0] lx_abs_c = (lx_c < 0) ? -lx_c : lx_c;
    wire signed [45:0] ly_abs_c = (ly_c < 0) ? -ly_c : ly_c;
    // ★ 寄存后的局部坐标绝对值（stage6 用；20 位足够，劈开长链）
    wire signed [19:0] lx_abs2 = (lx_r < 0) ? -lx_r : lx_r;
    wire signed [19:0] ly_abs2 = (ly_r < 0) ? -ly_r : ly_r;
    wire signed [45:0] wx_c = (p0_r + p1_r + 46'sd524288) >>> 20;
    wire signed [45:0] wy_c = (-p2_r + mreg + 46'sd524288) >>> 20;
    wire signed [45:0] dot_c = (p0_r + mreg + 46'sd524288) >>> 20;
    // ★ 多件：门在**障碍之后**判定，穿门方向/大小用已反射后的工作速度
    wire signed [19:0] pvx_w = vx;
    wire signed [19:0] pvy_w = vy;
    wire signed [20:0] pvx_abs_c = pvx_w[19] ? (21'sd0 - pvx_w) : pvx_w;
    wire signed [20:0] pvy_abs_c = pvy_w[19] ? (21'sd0 - pvy_w) : pvy_w;
    wire _ph0 = hit0_r && p_sq0_r;          // 进 0 号门 -> 出 1 号门
    wire _ph1 = hit1_r && p_sq1_r;          // 进 1 号门 -> 出 0 号门
    wire [17:0] _txw = _ph0 ? px1_r : px0_r;
    wire [17:0] _tyw = _ph0 ? py1_r : py0_r;
    // ★ 圆形门的**组合**预过滤：同一拍就要拿它决定进不进 stage4（不能用
    //   p_sq0_r 的旧值——那会把触发推迟一步；2026-10-19 tb 实锤）。
    wire psq0_c = port_en_r && (tp_cd_r == 7'd0)
                  && (x >= (px0_r - pr_r)) && (x <= (px0_r + pr_r))
                  && (y >= (py0_r - pr_r)) && (y <= (py0_r + pr_r));
    wire psq1_c = port_en_r && (tp_cd_r == 7'd0)
                  && (x >= (px1_r - pr_r)) && (x <= (px1_r + pr_r))
                  && (y >= (py1_r - pr_r)) && (y <= (py1_r + pr_r));

    // ★ 2026-10-19 最终生效（stage4）：障碍结果 -> 圆门目标（组合选择）
    reg [17:0] fx_c, fy_c;
    reg signed [19:0] fvx_c, fvy_c;
    reg [6:0]  fcd_c;
    always @(*) begin
        fx_c = bx_r; fy_c = by_r; fvx_c = bvx_r; fvy_c = bvy_r;
        fcd_c = tp_dec_r;
        if (obs_in_r) begin
            fx_c = ox_r; fy_c = oy_r; fvx_c = ovx_r; fvy_c = ovy_r;
        end
        if (hit0_r && p_sq0_r) begin
            // 进 0 号门 -> 从 1 号门沿速度轴穿出
            if (pvx_abs_c >= pvy_abs_c) begin
                fx_c = (pvx_w[19]) ? ((px1_r >= off_c[17:0])
                                      ? (px1_r - off_c[17:0]) : br_r)
                                   : (({1'b0, px1_r} + off_c > {1'b0, fw_r})
                                      ? fw_r : (px1_r + off_c[17:0]));
                fy_c = py1_r;
            end else begin
                fx_c = px1_r;
                fy_c = (pvy_w[19]) ? ((py1_r >= off_c[17:0])
                                      ? (py1_r - off_c[17:0]) : br_r)
                                   : (({1'b0, py1_r} + off_c > {1'b0, fh_r})
                                      ? fh_r : (py1_r + off_c[17:0]));
            end
            fcd_c = TP_CD;
        end else if (hit1_r && p_sq1_r) begin
            // 进 1 号门 -> 从 0 号门穿出
            if (pvx_abs_c >= pvy_abs_c) begin
                fx_c = (pvx_w[19]) ? ((px0_r >= off_c[17:0])
                                      ? (px0_r - off_c[17:0]) : br_r)
                                   : (({1'b0, px0_r} + off_c > {1'b0, fw_r})
                                      ? fw_r : (px0_r + off_c[17:0]));
                fy_c = py0_r;
            end else begin
                fx_c = px0_r;
                fy_c = (pvy_w[19]) ? ((py0_r >= off_c[17:0])
                                      ? (py0_r - off_c[17:0]) : br_r)
                                   : (({1'b0, py0_r} + off_c > {1'b0, fh_r})
                                      ? fh_r : (py0_r + off_c[17:0]));
            end
            fcd_c = TP_CD;
        end
    end

    always @(posedge clk) begin
        if (!rstn) begin
            busy <= 1'b0; done_r <= 1'b0; res_r <= 2'd0; op <= 4'd0; sub <= 2'd0;
            x <= 18'd0; y <= 18'd0; vx <= 20'sd0; vy <= 20'sd0;
            step_i <= 12'd0; bounce_i <= 4'd0; ns_r <= 12'd0; nb_r <= 4'd0;
            pt_req <= 1'b0; pt_idx <= 8'd0; lastx <= 18'd0; lasty <= 18'd0;
            nx_r <= 18'd0; ny_r <= 18'd0; ex_r <= 18'd0; ey_r <= 18'd0;
            pt_flag_r <= 1'b0; wall_r <= 1'b0; goal_r <= 1'b0;
            br_r <= 18'd0; fw_r <= 18'd0; fh_r <= 18'd0; ms_r <= 12'd0;
            mb_r <= 4'd0; x_wall_r <= 1'b0; y_wall_r <= 1'b0; nb_next_r <= 5'd0;
            nh_r <= 2'd0;
            obs_en_0_r <= 1'b0; obs_en_1_r <= 1'b0; obs_en_2_r <= 1'b0;
            ox0_0_r <= 18'd0; oy0_0_r <= 18'd0; ox1_0_r <= 18'd0; oy1_0_r <= 18'd0;
            ocx_0_r <= 18'd0; ocy_0_r <= 18'd0; oehw_0_r <= 18'd0; oehh_0_r <= 18'd0;
            oca_0_r <= 23'sd0; osa_0_r <= 23'sd0;
            ox0_1_r <= 18'd0; oy0_1_r <= 18'd0; ox1_1_r <= 18'd0; oy1_1_r <= 18'd0;
            ocx_1_r <= 18'd0; ocy_1_r <= 18'd0; oehw_1_r <= 18'd0; oehh_1_r <= 18'd0;
            oca_1_r <= 23'sd0; osa_1_r <= 23'sd0;
            ox0_2_r <= 18'd0; oy0_2_r <= 18'd0; ox1_2_r <= 18'd0; oy1_2_r <= 18'd0;
            ocx_2_r <= 18'd0; ocy_2_r <= 18'd0; oehw_2_r <= 18'd0; oehh_2_r <= 18'd0;
            oca_2_r <= 23'sd0; osa_2_r <= 23'sd0;
            port_en_0_r <= 1'b0; port_en_1_r <= 1'b0;
            px0_0_r <= 18'd0; py0_0_r <= 18'd0; px1_0_r <= 18'd0; py1_0_r <= 18'd0;
            px0_1_r <= 18'd0; py0_1_r <= 18'd0; px1_1_r <= 18'd0; py1_1_r <= 18'd0;
            ob_idx <= 2'd0; pt_pair <= 1'b0;
            pr_r <= 18'd0; tp_cd_r <= 7'd0; tp_next_r <= 7'd0;
            qx_r <= 18'd0; qy_r <= 18'd0; qvx_r <= 20'sd0; qvy_r <= 20'sd0;
            bx_r <= 18'd0; by_r <= 18'd0; bvx_r <= 20'sd0; bvy_r <= 20'sd0;
            obs_bb_r <= 1'b0; tp_dec_r <= 7'd0;
            ph_stage <= 3'd0; ph_idx <= 2'd0; ph_phase <= 2'd0;
            dxo_r <= 20'sd0; dyo_r <= 20'sd0; lx_r <= 20'sd0; ly_r <= 20'sd0;
            sx_r2 <= 2'sd0; sy_r2 <= 2'sd0; nx_r2 <= 23'sd0; ny_r2 <= 23'sd0;
            dot_r <= 20'sd0; wx_r2 <= 20'sd0; wy_r2 <= 20'sd0;
            ovx_r <= 20'sd0; ovy_r <= 20'sd0; ox_r <= 18'd0; oy_r <= 18'd0;
            obs_in_r <= 1'b0; p_sq0_r <= 1'b0; p_sq1_r <= 1'b0;
            hit0_r <= 1'b0; hit1_r <= 1'b0;
            p0_r <= 46'sd0; p1_r <= 46'sd0; p2_r <= 46'sd0; p3_r <= 46'sd0;
            off_r <= 19'd0; ptx_r <= 18'd0; pty_r <= 18'd0; pcd_r <= 7'd0;
            for (hh = 0; hh < 4; hh = hh + 1) begin
                hlo_r[hh] <= 18'd0; hhi_r[hh] <= 18'd0;
                hed_r[hh] <= 2'd0;  hen_r[hh] <= 1'b0; htg_r[hh] <= 1'b0;
            end
            ddx_r <= 19'sd0; ddy_r <= 19'sd0;
            sx_r <= 38'sd0; sy_r <= 38'sd0;
            tx_r <= 18'd0; ty_r <= 18'd0;
            ma <= 20'sd0; mb <= 23'sd0; ang2_q <= 46'sd0; cos_r <= 23'sd0;
            m_vxc <= 43'sd0; m_vys <= 43'sd0; m_vxs <= 43'sd0; m_vyc <= 43'sd0;
            m_dx <= 39'sd0; m_dy <= 39'sd0; nvx_c <= 20'sd0; nvy_c <= 20'sd0;
            ang_r <= 23'sd0;
        end else if (!busy) begin
            done_r <= 1'b0;
            // 本地常数寄存器：每拍跟随输入（扫描期间输入不变）
            br_r <= ball_r;
            fw_r <= field_w - ball_r;
            fh_r <= field_h - ball_r;
            ms_r <= max_steps;
            mb_r <= max_bounce;          // ★ 2026-10-04：max_bounce 不再是死输入
            // ★ 洞表也常驻一份（与上面三条同一条契约）。`start` 也在这拍生效 ——
            //   所以"启动那一拍抄表"就是**最后一次**抄表的时机，正好。
            nh_r <= n_holes;
            obs_en_0_r <= obs_en;
            ox0_0_r <= obs_x0; oy0_0_r <= obs_y0;
            ox1_0_r <= obs_x1; oy1_0_r <= obs_y1;
            ocx_0_r <= obs_cx; ocy_0_r <= obs_cy;
            oehw_0_r <= obs_ehw; oehh_0_r <= obs_ehh;
            oca_0_r <= obs_ca; osa_0_r <= obs_sa;
            obs_en_1_r <= obs1_en;
            ox0_1_r <= obs1_x0; oy0_1_r <= obs1_y0;
            ox1_1_r <= obs1_x1; oy1_1_r <= obs1_y1;
            ocx_1_r <= obs1_cx; ocy_1_r <= obs1_cy;
            oehw_1_r <= obs1_ehw; oehh_1_r <= obs1_ehh;
            oca_1_r <= obs1_ca; osa_1_r <= obs1_sa;
            obs_en_2_r <= obs2_en;
            ox0_2_r <= obs2_x0; oy0_2_r <= obs2_y0;
            ox1_2_r <= obs2_x1; oy1_2_r <= obs2_y1;
            ocx_2_r <= obs2_cx; ocy_2_r <= obs2_cy;
            oehw_2_r <= obs2_ehw; oehh_2_r <= obs2_ehh;
            oca_2_r <= obs2_ca; osa_2_r <= obs2_sa;
            port_en_0_r <= port_en;
            px0_0_r <= port_x0; py0_0_r <= port_y0;
            px1_0_r <= port_x1; py1_0_r <= port_y1;
            port_en_1_r <= port1_en;
            px0_1_r <= port1_x0; py0_1_r <= port1_y0;
            px1_1_r <= port1_x1; py1_1_r <= port1_y1;
            pr_r <= port_r;
            off_r <= {1'b0, port_r} + {1'b0, ball_r};
            for (hh = 0; hh < 4; hh = hh + 1) begin
                hlo_r[hh] <= h_lo[hh*18 +: 18];
                hhi_r[hh] <= h_hi[hh*18 +: 18];
                hed_r[hh] <= h_meta[hh*4 + 1 +: 2];
                hen_r[hh] <= h_meta[hh*4 + 3];
                htg_r[hh] <= h_meta[hh*4 + 0];
            end
            if (start) begin
                x <= x0; y <= y0; vx <= vx0; vy <= vy0;
                step_i <= 12'd0; bounce_i <= 4'd0;
                pt_idx <= 8'd0; lastx <= x0; lasty <= y0;
                busy <= 1'b1; res_r <= 2'd0; pt_req <= 1'b0;
                tp_cd_r <= 7'd0; tp_next_r <= 7'd0;
                ph_stage <= 3'd0; ph_idx <= 2'd0; ph_phase <= 2'd0;
                op <= OP_LOOK; sub <= 2'd0;
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
                    // ★ 2026-10-04：撞墙的**面**与"本步后反弹计数"也在这拍寄存
                    //   （两条独立判断与黄金模型一致：角碰双墙 = 计 2 次）
                    x_wall_r <= ((nx_r < br_r) || (nx_r > fw_r));
                    y_wall_r <= ((ny_r < br_r) || (ny_r > fh_r));
                    nb_next_r <= {1'b0, bounce_i}
                                 + {4'd0, ((nx_r < br_r) || (nx_r > fw_r))}
                                 + {4'd0, ((ny_r < br_r) || (ny_r > fh_r))};
                    sub <= 2'd1;
                end else if (sub == 2'd1) begin
                    // 第 1 拍：只算两个平方并**寄存**（乘法器综合成 DSP，组合输出很长：
                    // 实装路径 p_1_out -> pt_flag_r_reg 逻辑 6.66ns）
                    sx_r <= ddx_r * ddx_r;
                    sy_r <= ddy_r * ddy_r;
                    sub <= 2'd2;
                end else if (sub == 2'd2) begin
                    // 第 2 拍：只剩"加 + 比较"，短得多
                    pt_flag_r <= ((sx_r + sy_r) > 42'sd4194304);
                    sub <= 2'd3;
                end else begin
                    if (pt_flag_r && !pt_gnt) begin
                        pt_req <= 1'b1;                            // 等授权（停在 sub=1）
                    end else begin
                        pt_req <= 1'b0;
                        if (step_i >= ms_r) begin
                            busy <= 1'b0; done_r <= 1'b1;
                            // ★ 2026-10-04：黄金模型口径——步数用尽时若有反弹史
                            //   （stop_reason 仍是 'wall'）res=2；从未反弹才是 res=0。
                            res_r <= (bounce_i != 4'd0) ? 2'd2 : 2'd0;
                            ex_r <= x; ey_r <= y; ns_r <= step_i;   // ★ 报更新前的位置
                            nb_r <= bounce_i;                       // 累计反弹数（黄金模型口径）
                            op <= 4'd0; sub <= 2'd0;
                        end else if (wall_r) begin
                            // ★★ 2026-10-04 max_bounce 修复：与黄金模型 lane_fixed 逐位一致。
                            //   goal -> 结束 res=1；非 goal 且反弹计数超上限 -> 结束 res=2；
                            //   否则：夹边界 + 反射 + 计数 -> **继续积分**（弹性回弹）。
                            if (goal_r || (!mb_inf && (nb_next_r > {1'b0, mb_r}))) begin
                                busy <= 1'b0; done_r <= 1'b1;
                                ns_r <= step_i + 12'd1;
                                if (goal_r) begin
                                    res_r <= 2'd1; ex_r <= nx_r; ey_r <= ny_r;   // 进洞：未夹住
                                    nb_r <= bounce_i;   // ★ 2026-10-04：反弹后再进洞，
                                                        //   黄金模型报已弹次数（此前恒 0 会不符）
                                end else begin
                                    res_r <= 2'd2; ex_r <= tx_r; ey_r <= ty_r;   // 反弹用尽：夹住后
                                    nb_r <= nb_next_r[3:0];
                                end
                                op <= 4'd0; sub <= 2'd0;
                            end else begin
                                // 反弹继续：位置夹到边界（角碰双墙同时夹）、碰到的分量反射。
                                // ★★ 2026-10-04 修：反射必须翻**本步的新速度**（nvx_c/nvy_c——
                                //   撞墙的就是它），不能翻上一步的旧速度（vx/vy）。零场时两者
                                //   相同、仿真看不出来；**带 B 场**时新速度已被本步转向微调，
                                //   翻旧速度会从第一次反弹起就与黄金模型分道扬镳
                                //   （症状：判定巧合一致、端点大范围不一致——定妆照实测 10/17）。
                                x <= tx_r; y <= ty_r;
                                vx <= x_wall_r ? -nvx_c : nvx_c;
                                vy <= y_wall_r ? -nvy_c : nvy_c;
                                bounce_i <= nb_next_r[3:0];
                                step_i <= step_i + 12'd1;
                                // 轨迹点：黄金模型在反弹步 append 的是**夹后点**；这里沿用
                                // 已有 pt_flag（dist 按夹前点算，属近似）——点流不在任何逐位
                                // 契约里（tb 只比 res/终点/步数/反弹数），近似可接受。
                                if (pt_flag_r) begin
                                    pt_we_idx <= pt_idx;
                                    pt_we_data <= {tx_r, ty_r};
                                    pt_idx <= pt_idx + 8'd1;
                                    lastx <= tx_r; lasty <= ty_r;
                                end
                                tp_dec_r <= (tp_cd_r != 7'd0)
                                            ? (tp_cd_r - 7'd1) : 7'd0;
                                ob_idx <= 2'd0; pt_pair <= 1'b0;
                                op <= OP_PHYS; sub <= 2'd0;
                            end
                        end else begin
                            x <= nx_r; y <= ny_r; vx <= nvx_c; vy <= nvy_c;
                            step_i <= step_i + 12'd1;
                            if (pt_flag_r) begin
                                pt_we_idx <= pt_idx;
                                pt_we_data <= {nx_r, ny_r};
                                pt_idx <= pt_idx + 8'd1;
                                lastx <= nx_r; lasty <= ny_r;
                            end
                            tp_dec_r <= (tp_cd_r != 7'd0)
                                        ? (tp_cd_r - 7'd1) : 7'd0;
                            ob_idx <= 2'd0; pt_pair <= 1'b0;
                            op <= OP_PHYS; sub <= 2'd0;
                        end
                    end
                end
            end else if (op == OP_PHYS) begin
                // ★★ 2026-10-19 旋转矩形 + 圆形门（用户定案）。
                //   ph_stage: 0=预过滤 1=局部坐标 2=世界回投 3=法线反射
                //             4=圆门 5=生效。stage1..4 每乘 3 拍（发/等/收）。
                if (ph_stage == 3'd0) begin
                    bx_r <= x; by_r <= y; bvx_r <= vx; bvy_r <= vy;
                    obs_in_r <= 1'b0;            // ★ 预过滤不命中时别留上一拍的 1
                    hit0_r <= 1'b0; hit1_r <= 1'b0;
                    dxo_r <= $signed({1'b0, x}) - $signed({1'b0, ocx_r});
                    dyo_r <= $signed({1'b0, y}) - $signed({1'b0, ocy_r});
                    ph_idx <= 2'd0; ph_phase <= 2'd0;
                    if (obs_en_r && (x >= ox0_r) && (x <= ox1_r)
                            && (y >= oy0_r) && (y <= oy1_r)) begin
                        ph_stage <= 3'd1;
                    end else begin
                        // ★ 多件：本件没撞（或未启用）-> 先落结果（无动作）
                        //   再决定下一件障碍 / 进传送门
                        ph_stage <= 3'd7;
                    end
                end else if (ph_stage == 3'd1) begin
                    // 局部坐标 4 乘：dx*ca, dy*sa, dx*sa, dy*ca
                    if (ph_phase == 2'd0) begin
                        case (ph_idx)
                        2'd0: begin ma <= dxo_r; mb <= oca_r; end
                        2'd1: begin ma <= dyo_r; mb <= osa_r; end
                        2'd2: begin ma <= dxo_r; mb <= osa_r; end
                        default: begin ma <= dyo_r; mb <= oca_r; end
                        endcase
                        ph_phase <= 2'd1;
                    end else if (ph_phase == 2'd1) begin
                        ph_phase <= 2'd2;
                    end else begin
                        case (ph_idx)
                        2'd0: p0_r <= mreg;
                        2'd1: p1_r <= mreg;
                        2'd2: p2_r <= mreg;
                        default: p3_r <= mreg;
                        endcase
                        if (ph_idx != 2'd3) begin
                            ph_idx <= ph_idx + 2'd1; ph_phase <= 2'd0;
                        end else begin
                            lx_r <= lx_c[19:0]; ly_r <= ly_c[19:0];
                            ph_stage <= 3'd6; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end
                    end
                end else if (ph_stage == 3'd6) begin
                    // ★ 局部坐标收尾（寄存后再判）：劈开 46 位 abs/比较/推出的长链
                    if ((lx_abs2 < {28'd0, oehw_r})
                            && (ly_abs2 < {28'd0, oehh_r})) begin
                        obs_in_r <= 1'b1;
                        if ((oehw_r - lx_abs2[17:0])
                                < (oehh_r - ly_abs2[17:0])) begin
                            lx_r <= (lx_r > 0) ? $signed({1'b0, oehw_r})
                                               : -$signed({1'b0, oehw_r});
                            sx_r2 <= (lx_r > 0) ? 2'sd1 : -2'sd1;
                            sy_r2 <= 2'sd0;
                            nx_r2 <= (lx_r > 0) ? oca_r : -oca_r;
                            ny_r2 <= (lx_r > 0) ? -osa_r : osa_r;
                        end else begin
                            ly_r <= (ly_r > 0) ? $signed({1'b0, oehh_r})
                                               : -$signed({1'b0, oehh_r});
                            sx_r2 <= 2'sd0;
                            sy_r2 <= (ly_r > 0) ? 2'sd1 : -2'sd1;
                            nx_r2 <= (ly_r > 0) ? osa_r : -osa_r;
                            ny_r2 <= (ly_r > 0) ? oca_r : -oca_r;
                        end
                        ph_stage <= 3'd2; ph_idx <= 2'd0; ph_phase <= 2'd0;
                    end else begin
                        obs_in_r <= 1'b0;
                        ph_stage <= 3'd7;
                        ph_idx <= 2'd0; ph_phase <= 2'd0;
                    end
                end else if (ph_stage == 3'd2) begin
                    // 世界回投 4 乘：lx*ca, ly*sa, lx*sa, ly*ca
                    if (ph_phase == 2'd0) begin
                        case (ph_idx)
                        2'd0: begin ma <= lx_r; mb <= oca_r; end
                        2'd1: begin ma <= ly_r; mb <= osa_r; end
                        2'd2: begin ma <= lx_r; mb <= osa_r; end
                        default: begin ma <= ly_r; mb <= oca_r; end
                        endcase
                        ph_phase <= 2'd1;
                    end else if (ph_phase == 2'd1) begin
                        ph_phase <= 2'd2;
                    end else begin
                        case (ph_idx)
                        2'd0: p0_r <= mreg;
                        2'd1: p1_r <= mreg;
                        2'd2: p2_r <= mreg;
                        default: p3_r <= mreg;
                        endcase
                        if (ph_idx != 2'd3) begin
                            ph_idx <= ph_idx + 2'd1; ph_phase <= 2'd0;
                        end else begin
                            wx_r2 <= wx_c[19:0]; wy_r2 <= wy_c[19:0];
                            ox_r <= ocx_r + wx_c[17:0];
                            oy_r <= ocy_r + wy_c[17:0];
                            ph_stage <= 3'd3; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end
                    end
                end else if (ph_stage == 3'd3) begin
                    // 法线反射 4 乘：vx*nx, vy*ny, dot*nx, dot*ny
                    if (ph_phase == 2'd0) begin
                        case (ph_idx)
                        2'd0: begin ma <= vx; mb <= nx_r2; end
                        2'd1: begin ma <= vy; mb <= ny_r2; end
                        2'd2: begin dot_r <= dot_c[19:0];
                                    ma <= dot_c[19:0]; mb <= nx_r2; end
                        default: begin ma <= dot_r; mb <= ny_r2; end
                        endcase
                        ph_phase <= 2'd1;
                    end else if (ph_phase == 2'd1) begin
                        ph_phase <= 2'd2;
                    end else begin
                        case (ph_idx)
                        2'd0: p0_r <= mreg;
                        2'd1: p1_r <= mreg;
                        2'd2: p2_r <= mreg;
                        default: p3_r <= mreg;
                        endcase
                        if (ph_idx != 2'd3) begin
                            ph_idx <= ph_idx + 2'd1; ph_phase <= 2'd0;
                        end else begin
                            ovx_r <= (dot_r < 0)
                                     ? (vx - 2 * ((p2_r + 46'sd524288) >>> 20))
                                     : vx;
                            ovy_r <= (dot_r < 0)
                                     ? (vy - 2 * ((mreg + 46'sd524288) >>> 20))
                                     : vy;
                            ph_stage <= 3'd7; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end
                    end
                end else if (ph_stage == 3'd4) begin
                    // 圆门 4 乘：dx0², dy0², dx1², dy1²（预过滤平方区）
                    if (ph_phase == 2'd0) begin
                        // ★ 多件：每对门先按**当前工作位置/当前对**捕获预过滤
                        p_sq0_r <= psq0_c;
                        p_sq1_r <= psq1_c;
                        if (!(psq0_c || psq1_c)) begin
                            ph_stage <= 3'd5; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end else begin
                            case (ph_idx)
                            2'd0: begin ma <= $signed({1'b0, x}) - $signed({1'b0, px0_r});
                                        mb <= $signed({1'b0, x}) - $signed({1'b0, px0_r}); end
                            2'd1: begin ma <= $signed({1'b0, y}) - $signed({1'b0, py0_r});
                                        mb <= $signed({1'b0, y}) - $signed({1'b0, py0_r}); end
                            2'd2: begin ma <= $signed({1'b0, x}) - $signed({1'b0, px1_r});
                                        mb <= $signed({1'b0, x}) - $signed({1'b0, px1_r}); end
                            default: begin ma <= $signed({1'b0, y}) - $signed({1'b0, py1_r});
                                        mb <= $signed({1'b0, y}) - $signed({1'b0, py1_r}); end
                            endcase
                            ph_phase <= 2'd1;
                        end
                    end else if (ph_phase == 2'd1) begin
                        ph_phase <= 2'd2;
                    end else begin
                        case (ph_idx)
                        2'd0: p0_r <= mreg;
                        2'd1: p1_r <= mreg;
                        2'd2: p2_r <= mreg;
                        default: p3_r <= mreg;
                        endcase
                        if (ph_idx != 2'd3) begin
                            ph_idx <= ph_idx + 2'd1; ph_phase <= 2'd0;
                        end else begin
                            hit0_r <= ((p0_r + p1_r) <= {21'd0, PORTAL_R2});
                            hit1_r <= ((p2_r + mreg) <= {21'd0, PORTAL_R2});
                            ph_stage <= 3'd5; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end
                    end
                end else if (ph_stage == 3'd7) begin
                    // ★★ 多件 stage7：本件障碍结果 -> 工作位置/速度；
                    //   还有下一件（且启用）就带着新位置回到 stage0，否则进门对 0。
                    if (obs_in_r) begin
                        x <= ox_r; y <= oy_r; vx <= ovx_r; vy <= ovy_r;
                    end
                    if (ob_idx < 2'd2
                            && ((ob_idx == 2'd0) ? obs_en_1_r
                                                 : (ob_idx == 2'd1) ? obs_en_2_r
                                                                    : 1'b0)) begin
                        ob_idx <= ob_idx + 2'd1;
                        ph_stage <= 3'd0; ph_idx <= 2'd0; ph_phase <= 2'd0;
                    end else begin
                        pt_pair <= 1'b0;
                        ph_stage <= 3'd4; ph_idx <= 2'd0; ph_phase <= 2'd0;
                    end
                end else begin
                    // ★★ 多件 stage5：门命中 -> 沿速度轴穿出配对门并结束本步；
                    //   未命中 -> 还有第 2 对就继续，否则结束本步（冷却落账）。
                    if (ph_phase == 2'd0) begin
                        if (_ph0 || _ph1) begin
                            if (pvx_abs_c >= pvy_abs_c) begin
                                ptx_r <= (pvx_w[19])
                                        ? ((_txw >= off_r[17:0])
                                           ? (_txw - off_r[17:0]) : br_r)
                                        : (({1'b0, _txw} + off_r > {1'b0, fw_r})
                                           ? fw_r : (_txw + off_r[17:0]));
                                pty_r <= _tyw;
                            end else begin
                                ptx_r <= _txw;
                                pty_r <= (pvy_w[19])
                                        ? ((_tyw >= off_r[17:0])
                                           ? (_tyw - off_r[17:0]) : br_r)
                                        : (({1'b0, _tyw} + off_r > {1'b0, fh_r})
                                           ? fh_r : (_tyw + off_r[17:0]));
                            end
                        end
                        ph_phase <= 2'd1;
                    end else begin
                        if (_ph0 || _ph1) begin
                            x <= ptx_r; y <= pty_r;
                            tp_cd_r <= TP_CD;
                            op <= OP_LOOK; sub <= 2'd0;
                            ph_stage <= 3'd0; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end else if (pt_pair == 1'b0 && port_en_1_r) begin
                            pt_pair <= 1'b1;
                            ph_stage <= 3'd4; ph_idx <= 2'd0; ph_phase <= 2'd0;
                        end else begin
                            tp_cd_r <= tp_dec_r;
                            op <= OP_LOOK; sub <= 2'd0;
                            ph_stage <= 3'd0; ph_idx <= 2'd0; ph_phase <= 2'd0;
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
                // ★★ 2026-10-19：OP_LOOK 必须**明确**跳回 OP_ANG2。原版靠
                //   `op+1` 从 9 空转到 15 再回绕到 0（能跑但每步白烧 18 拍）；
                //   现在 op=10 被 OP_PHYS 占用，不显式回跳会死循环（实锤）。
                op <= (op == OP_LOOK) ? OP_ANG2 : (op + 4'd1);
                sub <= 2'd0;
            end else begin
                if (sub == 2'd0) begin
                    case (op)
                    OP_LOOK: begin
                        ang_r <= (ang_sel == 2'd1) ? -ang_c :
                                 ((ang_sel == 2'd2) ? 23'sd0 : ang_c);
                    end
                    OP_ANG2: begin ma <= ang_r; mb <= ang_r; end
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

    // 提交态速度（见端口注释；组合直通内部寄存器）
    assign vx_out = vx;
    assign vy_out = vy;

endmodule
