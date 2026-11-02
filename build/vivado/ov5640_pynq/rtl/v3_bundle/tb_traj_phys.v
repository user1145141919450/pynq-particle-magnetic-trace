// ============================================================================
// tb_traj_phys.v —— 2026-10-19 布置 PLACE（PL 原生）单 lane 自检：
//   ① 无物件基线（与旧行为逐位一致）
//   ② AABB 障碍反弹（轴对齐盒，最浅面推出 + 反射）
//   ③ 方形传送门（进入 -> 瞬移到配对门中心，冷却 84 步）
//
// 期望值来自 tools/v3_fixedpoint.py 的 lane_fixed（同一套整数语义）：
//   ① x=433.594 y=270.000
//   ② x=166.562 y=270.000
//   ③ x=650.195 y=270.000
// 场景：B=0（blk_tab 全 0）、起点 (100,270)、速度 400 px/s 向右、200 步。
// ============================================================================
`timescale 1ns / 1ps

module tb_traj_phys;
    localparam integer NBLK = 9;

    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    reg start = 0;
    reg [17:0] x0 = 18'd25600, y0 = 18'd69120;      // (100,270) Q10.8
    reg signed [19:0] vx0 = 20'sd102400, vy0 = 20'sd0; // 400 px/s Q11.8
    reg [11:0] max_steps = 12'd200;
    reg [3:0]  max_bounce = 4'd6;
    reg        mb_inf = 1'b0;
    reg [1:0]  ang_sel = 2'd0;
    reg [17:0] ball_r = 18'd2816;                    // 11 px
    reg [17:0] field_w = 18'd245760, field_h = 18'd138240; // 960x540
    reg        side = 1'b0;
    reg [NBLK*95-1:0] blk_tab = 0;
    reg [1:0]  n_holes = 0;
    reg [4*18-1:0] h_lo = 0, h_hi = 0;
    reg [4*4-1:0]  h_meta = 0;

    reg        obs_en = 0;
    reg [17:0] obs_x0 = 0, obs_y0 = 0, obs_x1 = 0, obs_y1 = 0;
    reg [17:0] obs_cx = 0, obs_cy = 0, obs_ehw = 0, obs_ehh = 0;
    reg signed [22:0] obs_ca = 0, obs_sa = 0;
    reg        port_en = 0;
    reg [17:0] port_x0 = 0, port_y0 = 0, port_x1 = 0, port_y1 = 0, port_r = 0;

    wire [1:0]  res;
    wire [17:0] ex, ey;
    wire signed [19:0] vx_out, vy_out;
    wire [11:0] n_step;
    wire [3:0]  n_bounce;
    wire        done;
    wire        pt_req;
    wire [7:0]  pt_we_idx;
    wire [35:0] pt_we_data;

    traj_lane #(.NBLK(NBLK)) dut (
        .clk(clk), .rstn(rstn),
        .start(start), .x0(x0), .y0(y0), .vx0(vx0), .vy0(vy0),
        .max_steps(max_steps), .max_bounce(max_bounce), .mb_inf(mb_inf),
        .ang_sel(ang_sel),
        .ball_r(ball_r), .field_w(field_w), .field_h(field_h), .side(side),
        .obs_en(obs_en),
        .obs_x0(obs_x0), .obs_y0(obs_y0), .obs_x1(obs_x1), .obs_y1(obs_y1),
        .obs_cx(obs_cx), .obs_cy(obs_cy),
        .obs_ehw(obs_ehw), .obs_ehh(obs_ehh),
        .obs_ca(obs_ca), .obs_sa(obs_sa),
        .obs1_en(1'b0), .obs2_en(1'b0),
        .obs1_x0(18'd0), .obs1_y0(18'd0), .obs1_x1(18'd0), .obs1_y1(18'd0),
        .obs1_cx(18'd0), .obs1_cy(18'd0), .obs1_ehw(18'd0), .obs1_ehh(18'd0),
        .obs1_ca(23'sd0), .obs1_sa(23'sd0),
        .obs2_x0(18'd0), .obs2_y0(18'd0), .obs2_x1(18'd0), .obs2_y1(18'd0),
        .obs2_cx(18'd0), .obs2_cy(18'd0), .obs2_ehw(18'd0), .obs2_ehh(18'd0),
        .obs2_ca(23'sd0), .obs2_sa(23'sd0),
        .port_en(port_en),
        .port_x0(port_x0), .port_y0(port_y0),
        .port_x1(port_x1), .port_y1(port_y1), .port_r(port_r),
        .port1_en(1'b0),
        .port1_x0(18'd0), .port1_y0(18'd0),
        .port1_x1(18'd0), .port1_y1(18'd0),
        .blk_tab(blk_tab), .n_holes(n_holes),
        .h_lo(h_lo), .h_hi(h_hi), .h_meta(h_meta),
        .res(res), .ex(ex), .ey(ey), .vx_out(vx_out), .vy_out(vy_out),
        .n_step(n_step), .n_bounce(n_bounce), .done(done),
        .pt_req(pt_req), .pt_gnt(1'b1),
        .pt_we_idx(pt_we_idx), .pt_we_data(pt_we_data)
    );

    integer errors = 0;
    integer t;

    // 调试：碰撞窗口内打印 OP_PHYS 入口的 x/vx
    always @(posedge clk) begin
        if (dut.busy && (dut.op == 4'd10) && (dut.ph_stage == 3'd0)
                && (dut.ph_phase == 2'd0)
                && (dut.step_i >= 12'd111) && (dut.step_i <= 12'd116))
            $display("DBG-in step=%0d x=%0d vx=%0d", dut.step_i, dut.x, dut.vx);
        if (dut.busy && (dut.op == 4'd10) && (dut.ph_stage == 3'd5)
                && (dut.step_i >= 12'd111) && (dut.step_i <= 12'd116))
            $display("DBG-out step=%0d fx=%0d fvx=%0d obs_in=%b",
                     dut.step_i, dut.fx_c, dut.fvx_c, dut.obs_in_r);
        if (dut.busy && (dut.op == 4'd10) && (dut.ph_stage == 3'd5)
                && dut.hit0_r)
            $display("DBG-TP step=%0d x=%0d fx=%0d", dut.step_i, dut.x, dut.fx_c);
        if (dut.busy && (dut.op == 4'd10) && (dut.ph_stage == 3'd0)
                && (dut.ph_phase == 2'd0)
                && (dut.step_i >= 12'd48) && (dut.step_i <= 12'd52))
            $display("DBG-P0 step=%0d x=%0d psq0=%b psq1=%b",
                     dut.step_i, dut.x, dut.p_sq0_r, dut.p_sq1_r);
        if (dut.busy && (dut.op == 4'd10) && (dut.ph_stage == 3'd5)
                && (dut.step_i >= 12'd48) && (dut.step_i <= 12'd52))
            $display("DBG-P5 step=%0d x=%0d hit0=%b p0=%0d p1=%0d",
                     dut.step_i, dut.x, dut.hit0_r, dut.p0_r, dut.p1_r);
    end

    task run_case(input [8*12-1:0] name,
                  input [17:0] exp_x, input [17:0] exp_y,
                  input [11:0] exp_n, input [3:0] exp_b);
        begin
            @(negedge clk);
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;
            t = 0;
            while (!done && t < 200000) begin
                @(posedge clk); t = t + 1;
                if (t % 5000 == 0)
                    $display("  t=%0d busy=%b op=%0d sub=%0d step=%0d done=%b",
                             t, dut.busy, dut.op, dut.sub, dut.step_i, dut.done_r);
            end
            if (!done) begin
                $display("FAIL %0s: done timeout (busy=%b op=%0d sub=%0d step=%0d pt_req=%b)",
                         name, dut.busy, dut.op, dut.sub, dut.step_i, dut.pt_req);
                errors = errors + 1;
            end else if (ex !== exp_x || ey !== exp_y || res !== 2'd0
                         || n_step !== exp_n || n_bounce !== exp_b) begin
                $display("FAIL %0s: got ex=%0d ey=%0d res=%0d n=%0d b=%0d | exp ex=%0d ey=%0d n=%0d b=%0d",
                         name, ex, ey, res, n_step, n_bounce,
                         exp_x, exp_y, exp_n, exp_b);
                errors = errors + 1;
            end else begin
                $display("PASS %0s: ex=%0d ey=%0d n=%0d b=%0d",
                         name, ex, ey, n_step, n_bounce);
            end
            // 等 done 落下
            @(posedge clk);
        end
    endtask

    initial begin
        repeat (4) @(posedge clk);
        rstn = 1'b1;
        repeat (4) @(posedge clk);

        // ① 基线：无物件
        obs_en = 0; port_en = 0;
        run_case("base", 18'd111000, 18'd69120, 12'd200, 4'd0);

        // ② 旋转矩形障碍：中心 (310,270)、半宽 10、半高 70、角 0°
        //    （扩张 BALL_R 后 ehw=21/ehh=81；AABB 289..331 / 189..351）
        obs_en = 1;
        obs_x0 = 18'd73984; obs_y0 = 18'd48384;   // 289 / 189 Q10.8
        obs_x1 = 18'd84736; obs_y1 = 18'd89856;   // 331 / 351
        obs_cx = 18'd79360; obs_cy = 18'd69120;   // 310 / 270
        obs_ehw = 18'd5376; obs_ehh = 18'd20736;  // 21 / 81
        obs_ca = 23'sd1048576; obs_sa = 23'sd0;   // cos0 / sin0 Q2.20
        port_en = 0;
        run_case("obs", 18'd37262, 18'd69120, 12'd200, 4'd0);

        // ③ 传送门：(200,270)->(400,270)，半边长 18
        obs_en = 0; port_en = 1;
        port_x0 = 18'd51200;  port_y0 = 18'd69120;
        port_x1 = 18'd102400; port_y1 = 18'd69120;
        port_r  = 18'd4608;
        run_case("port", 18'd173874, 18'd69120, 12'd200, 4'd0);

        if (errors == 0) $display("TB_TRAJ_PHYS: ALL PASS");
        else             $display("TB_TRAJ_PHYS: %0d FAIL", errors);
        $finish;
    end
endmodule
