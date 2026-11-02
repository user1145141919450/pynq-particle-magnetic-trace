// ============================================================================
// tb_particle_engine.v —— 粒子引擎集成测试台（2026-10-16）
//
// 场景（固定常量，期望值由 tools/pe_tb.py 用黄金模型 lane_fixed + pe_fix 生成）：
//   T0 出生读回   T1 进洞（1600px/s+右洞c=178，期望 129 步 goal/edge=right）
//   T2 2000 步无洞（逐位期望终点 216171,2930）
//   T3 异号湮灭   T4 同号弹性
// 输出格式 "T# name=val ..." 由 pe_tb.py 解析比对。
// ============================================================================
`timescale 1ns / 1ps

module tb_particle_engine;
    reg clk = 0;
    always #5 clk = ~clk;               // 100MHz
    reg rstn = 0;

    reg         run_pulse = 0;
    reg  [15:0] run_n = 0;
    reg         stop = 0;
    reg         clear = 0;
    wire        busy, paused;
    wire [6:0]  n_alive;

    reg  [17:0] ball_r = 18'd2816, field_w = 18'd245760, field_h = 18'd138240;
    reg  [1:0]  n_holes = 2'd0;
    reg  [9*95-1:0] blk_tab = 855'd0;
    reg  [4*40-1:0] hole_tab = 160'd0;

    reg         spawn_pulse = 0;
    reg  [5:0]  spawn_idx = 0;
    reg  [17:0] spawn_x = 0, spawn_y = 0;
    reg  signed [19:0] spawn_vx = 0, spawn_vy = 0;
    reg  [1:0]  spawn_chg = 0, spawn_who = 0;

    reg  [5:0]  rd_idx = 0;
    wire [17:0] rd_x, rd_y;
    wire signed [19:0] rd_vx, rd_vy;
    wire [1:0]  rd_chg, rd_who;
    wire        rd_alive;

    wire [4:0]  ev_count;
    reg         ev_pop = 0;
    wire [13:0] ev_data;
    wire        ev_empty;

    wire [15:0] st_substeps, st_goal, st_annihilate, st_collide, st_bounce,
                st_evdrop;

    // ★ 2026-10-17：参数改为**实建配置**（此前 tb 用 64/4 覆盖）；
    //   2026-11-01 实建再改为 16/1（用户定案 16 粒引擎），台架同步。
    particle_engine #(.NPART(16), .NBLK(9), .KLANES(1)) dut (
        .clk(clk), .rstn(rstn),
        .run_pulse(run_pulse), .run_n(run_n), .stop(stop), .clear(clear),
        .busy(busy), .n_alive(n_alive), .paused(paused),
        .ball_r(ball_r), .field_w(field_w), .field_h(field_h),
        .n_holes(n_holes), .blk_tab(blk_tab), .hole_tab(hole_tab),
        .obs_en(1'b0), .obs_x0(18'd0), .obs_y0(18'd0),
        .obs_x1(18'd0), .obs_y1(18'd0),
        .obs_cx(18'd0), .obs_cy(18'd0),
        .obs_ehw(18'd0), .obs_ehh(18'd0),
        .obs_ca(23'sd0), .obs_sa(23'sd0),
        .port_en(1'b0), .port_x0(18'd0), .port_y0(18'd0),
        .port_x1(18'd0), .port_y1(18'd0), .port_r(18'd0),
        .spawn_pulse(spawn_pulse), .spawn_idx(spawn_idx),
        .spawn_x(spawn_x), .spawn_y(spawn_y),
        .spawn_vx(spawn_vx), .spawn_vy(spawn_vy),
        .spawn_chg(spawn_chg), .spawn_who(spawn_who),
        .rd_idx(rd_idx), .rd_x(rd_x), .rd_y(rd_y), .rd_vx(rd_vx), .rd_vy(rd_vy),
        .rd_chg(rd_chg), .rd_who(rd_who), .rd_alive(rd_alive),
        .ev_count(ev_count), .ev_pop(ev_pop), .ev_data(ev_data),
        .ev_empty(ev_empty),
        .st_substeps(st_substeps), .st_goal(st_goal),
        .st_annihilate(st_annihilate), .st_collide(st_collide),
        .st_bounce(st_bounce), .st_evdrop(st_evdrop)
    );

    // 整场一块 B=+0.4：ang = -1748 (Q2.20) = 23'h7FF92C
    // blk_tab 字 = {ang[22:0], x0, y0, x1, y1}
    initial begin
        blk_tab[0*95 +: 95] = {23'h7FF92C, 18'd0, 18'd0,
                               18'd245760, 18'd138240};
    end

    // 看门狗（20ms 仿真上限）
    initial begin
        #400_000_000;
        $display("TB-TIMEOUT");
        $finish;
    end

    task do_spawn;
        input [5:0] idx;
        input [17:0] x, y;
        input signed [19:0] vx, vy;
        input [1:0] chg, who;
        begin
            @(posedge clk);
            spawn_idx <= idx; spawn_x <= x; spawn_y <= y;
            spawn_vx <= vx; spawn_vy <= vy;
            spawn_chg <= chg; spawn_who <= who;
            spawn_pulse <= 1'b1;
            @(posedge clk);
            spawn_pulse <= 1'b0;
            @(posedge clk);
        end
    endtask

    task do_clear;
        begin
            @(posedge clk);
            clear <= 1'b1;
            @(posedge clk);
            clear <= 1'b0;
            @(posedge clk);
        end
    endtask

    task do_run;
        input [15:0] n;
        begin
            @(posedge clk);
            run_n <= n; run_pulse <= 1'b1;
            @(posedge clk);
            run_pulse <= 1'b0;
            wait (busy);
            wait (!busy);
            @(posedge clk);
        end
    endtask

    task disp_ev;                       // 弹一条事件并打印
        begin
            if (ev_count != 5'd0) begin
                @(posedge clk);
                ev_pop <= 1'b1;
                @(posedge clk);
                ev_pop <= 1'b0;
                @(posedge clk);
                $display("EV data=%04x count_after=%0d", ev_data, ev_count);
            end
        end
    endtask

    integer k;
    initial begin
        rstn = 0;
        repeat (5) @(posedge clk);
        rstn = 1;
        repeat (3) @(posedge clk);

        // ---- T0 出生读回 ----
        do_spawn(6'd0, 18'd25600, 18'd69120, 20'sd102400, 20'sd0,
                 2'b01, 2'b01);
        rd_idx = 6'd0;
        @(posedge clk);
        $display("T0 x=%0d y=%0d vx=%0d vy=%0d chg=%0d who=%0d alive=%0d",
                 rd_x, rd_y, rd_vx, rd_vy, rd_chg, rd_who, rd_alive);

        // ---- T2 2000 步无洞（逐位终点）----
        do_run(16'd2000);
        rd_idx = 6'd0;
        @(posedge clk);
        $display("T2 x=%0d y=%0d vx=%0d vy=%0d alive=%0d sub=%0d bounce=%0d",
                 rd_x, rd_y, rd_vx, rd_vy, rd_alive, st_substeps, st_bounce);

        // ---- T1 进洞（1600px/s + 右洞 c=178：期望 129 步 goal）----
        do_clear();
        hole_tab[0*40 +: 40] = {1'b1, 2'd1, 1'b1, 18'd11264, 18'd45568};
        n_holes = 2'd1;
        do_spawn(6'd1, 18'd25600, 18'd69120, 20'sd409600, 20'sd0,
                 2'b01, 2'b01);
        do_run(16'd400);
        $display("T1 goal=%0d alive0=%0d alive1=%0d ev=%0d aerrs=%0d errs=0",
                 st_goal, rd_alive, n_alive, ev_count, st_annihilate);
        rd_idx = 6'd1;
        @(posedge clk);
        $display("T1x p1_alive=%0d", rd_alive);
        disp_ev();

        // ---- T3 异号湮灭 ----
        do_clear();
        hole_tab = 160'd0; n_holes = 2'd0;
        do_spawn(6'd0, 18'd76800, 18'd69120, 20'sd102400, 20'sd0, 2'b01, 2'b01);
        do_spawn(6'd1, 18'd78080, 18'd69120, -20'sd102400, 20'sd0, 2'b11, 2'b10);
        do_run(16'd2);
        $display("T3 alive=%0d annih=%0d ev=%0d", n_alive, st_annihilate,
                 ev_count);
        disp_ev();
        disp_ev();

        // ---- T4 同号弹性 ----
        do_clear();
        do_spawn(6'd0, 18'd76800, 18'd69120, 20'sd102400, 20'sd0, 2'b01, 2'b01);
        do_spawn(6'd1, 18'd101120, 18'd69120, -20'sd102400, 20'sd0, 2'b01, 2'b10);
        do_run(16'd40);
        rd_idx = 6'd0; @(posedge clk);
        $display("T4a x=%0d vx=%0d alive=%0d", rd_x, rd_vx, rd_alive);
        rd_idx = 6'd1; @(posedge clk);
        $display("T4b x=%0d vx=%0d alive=%0d", rd_x, rd_vx, rd_alive);
        $display("T4 collide=%0d alive=%0d", st_collide, n_alive);

        $display("TB-DONE");
        $finish;
    end

endmodule
