// tb_pe_probe.v —— 最小探针：出生 -> 读内部 -> 一发 run -> 观察 busy/st
`timescale 1ns / 1ps
module tb_pe_probe;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rstn = 0;

    reg run_pulse = 0; reg [15:0] run_n = 0; reg stop = 0; reg clear = 0;
    wire busy, paused; wire [6:0] n_alive;
    reg [17:0] ball_r = 18'd2816, field_w = 18'd245760, field_h = 18'd138240;
    reg [1:0] n_holes = 0;
    reg [9*95-1:0] blk_tab = 855'd0;
    reg [4*40-1:0] hole_tab = 0;
    reg spawn_pulse = 0; reg [5:0] spawn_idx = 0;
    reg [17:0] spawn_x = 0, spawn_y = 0;
    reg signed [19:0] spawn_vx = 0, spawn_vy = 0;
    reg [1:0] spawn_chg = 0, spawn_who = 0;
    reg [5:0] rd_idx = 0;
    wire [17:0] rd_x, rd_y; wire signed [19:0] rd_vx, rd_vy;
    wire [1:0] rd_chg, rd_who; wire rd_alive;
    wire [4:0] ev_count; reg ev_pop = 0; wire [13:0] ev_data; wire ev_empty;
    wire [15:0] st_substeps, st_goal, st_annihilate, st_collide, st_bounce,
                st_evdrop;

    particle_engine #(.NPART(64), .NBLK(9), .KLANES(4)) dut (
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
        .st_bounce(st_bounce), .st_evdrop(st_evdrop));

    initial begin
        blk_tab[0*95 +: 95] = {23'h7FF92C, 18'd0, 18'd0, 18'd245760, 18'd138240};
    end

    integer i;
    initial begin
        rstn = 0;
        repeat (5) @(posedge clk);
        rstn = 1;
        repeat (2) @(posedge clk);

        // 出生脉冲
        @(posedge clk);
        spawn_idx <= 6'd0; spawn_x <= 18'd25600; spawn_y <= 18'd69120;
        spawn_vx <= 20'sd102400; spawn_vy <= 20'sd0;
        spawn_chg <= 2'b01; spawn_who <= 2'b01;
        spawn_pulse <= 1'b1;
        @(posedge clk);
        spawn_pulse <= 1'b0;
        repeat (2) @(posedge clk);
        $display("PROBE1 ok=%0d x=%0d alive=%0d nalive=%0d",
                 dut.p_ok[0], dut.p_x[0], rd_alive, n_alive);

        // 一发 run：3 个子步
        @(posedge clk);
        run_n <= 16'd3; run_pulse <= 1'b1;
        @(posedge clk);
        run_pulse <= 1'b0;
        for (i = 0; i < 120; i = i + 1) begin
            @(posedge clk);
            if (i < 3 || i == 12 || i == 30 || i == 55 || i == 60 || i == 65 || i == 70 || i == 119)
                $display("PROBE2 i=%0d st=%0d busy=%0d cs=%0d base=%0d live=%b done=%b",
                         i, dut.st, busy, dut.cs, dut.base, dut.l_live, dut.l_done);
        end
        rd_idx = 6'd0; @(posedge clk);
        $display("PROBE3 x=%0d y=%0d sub=%0d busy=%0d st=%0d",
                 rd_x, rd_y, st_substeps, busy, dut.st);

        // ---- 碰撞隔离实验：直接摆在"将碰"位（gap=5546<5632），跑 1 子步 ----
        @(posedge clk); clear <= 1'b1; @(posedge clk); clear <= 1'b0;
        @(posedge clk);
        spawn_idx <= 6'd0; spawn_x <= 18'd86187; spawn_y <= 18'd69120;
        spawn_vx <= 20'sd102400; spawn_vy <= 20'sd0;
        spawn_chg <= 2'b01; spawn_who <= 2'b01; spawn_pulse <= 1'b1;
        @(posedge clk);
        spawn_idx <= 6'd1; spawn_x <= 18'd91733; spawn_y <= 18'd69120;
        spawn_vx <= -20'sd102400; spawn_vy <= 20'sd0;
        spawn_chg <= 2'b01; spawn_who <= 2'b10; spawn_pulse <= 1'b1;
        @(posedge clk);
        spawn_pulse <= 1'b0;
        @(posedge clk);
        run_n <= 16'd1; run_pulse <= 1'b1;
        @(posedge clk);
        run_pulse <= 1'b0;
        wait (busy);
        for (i = 0; i < 400; i = i + 1) begin
            @(posedge clk);
            if (dut.cs != 0 || dut.st_collide != 0)
                $display("TR st=%0d cs=%0d ci=%0d cj=%0d coll=%0d ax=%0d bx=%0d avx=%0d bvx=%0d",
                         dut.st, dut.cs, dut.ci, dut.cj, dut.st_collide,
                         dut.p_x[0], dut.p_x[1], dut.p_vx[0], dut.p_vx[1]);
            if (!busy) i = 400;
        end
        rd_idx = 6'd0; @(posedge clk);
        $display("C1 a_x=%0d a_vx=%0d", rd_x, rd_vx);
        rd_idx = 6'd1; @(posedge clk);
        $display("C1 b_x=%0d b_vx=%0d coll=%0d", rd_x, rd_vx, st_collide);
        $display("C1 期望: a_x=87187 a_vx=-102400 b_x=90733 b_vx=102400");
        $finish;
    end
endmodule
