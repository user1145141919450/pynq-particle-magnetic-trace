// ============================================================================
// tb_v3_bundle_axi.v —— AXI4-Lite 封装的**总线级**自检（上板前的最后一道仿真关）
//
// 判据：通过 AXI 写参数/启动、再读回 17 路结果，必须与定点黄金模型的 exp.mem
//       **逐位相同**（和 traj_bundle 那层 TB 同一个判据，但整条总线路径都过一遍）。
//       —— 上板后 PYNQ 走的就是这条路径，这里不验，板上就是盲调。
//
// 用法（工程根目录）：
//   xvlog rtl/v3_bundle/traj_lane.v rtl/v3_bundle/traj_bundle.v \
//         rtl/v3_bundle/v3_bundle_axi.v rtl/v3_bundle/tb_v3_bundle_axi.v
//   xelab -debug typical tb_v3_bundle_axi -s v3axi
//   xsim v3axi -R          （向量目录默认 sim/v3/fan，可用 +CASE=goal）
// ============================================================================
`timescale 1ns / 1ps

module tb_v3_bundle_axi;
    parameter integer NLANES = 17;
    parameter integer NBLK   = 9;

    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    // ---- AXI 信号 ----
    reg  [11:0] awaddr; reg [2:0] awprot; reg awvalid; wire awready;
    reg  [31:0] wdata;  reg [3:0] wstrb;  reg wvalid;  wire wready;
    wire [1:0]  bresp;  wire bvalid;      reg bready;
    reg  [11:0] araddr; reg [2:0] arprot; reg arvalid; wire arready;
    wire [31:0] rdata;  wire [1:0] rresp; wire rvalid; reg rready;

    v3_bundle_axi #(.NLANES(NLANES), .NBLK(NBLK)) dut (
        .s_axi_aclk(clk), .s_axi_aresetn(rstn),
        .s_axi_awaddr(awaddr), .s_axi_awprot(awprot), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arprot(arprot), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready)
    );

    // ---- AXI 读写任务 ----
    task axi_wr(input [11:0] a, input [31:0] d);
        begin
            @(posedge clk); awaddr <= a; awvalid <= 1'b1; wdata <= d; wvalid <= 1'b1; bready <= 1'b1;
            wait (awready || wready);
            @(posedge clk); awvalid <= 1'b0; wvalid <= 1'b0;
            wait (bvalid);
            @(posedge clk); bready <= 1'b0;
        end
    endtask

    task axi_rd(input [11:0] a, output [31:0] d);
        begin
            @(posedge clk); araddr <= a; arvalid <= 1'b1; rready <= 1'b1;
            wait (arready);
            @(posedge clk); arvalid <= 1'b0;
            wait (rvalid);
            d = rdata;
            @(posedge clk); rready <= 1'b0;
        end
    endtask

    // ---- 向量 ----
    reg [94:0] blk_mem  [0:NBLK-1];
    reg [39:0] hole_mem [0:3];
    reg [39:0] lanev_mem[0:NLANES-1];
    reg [31:0] param_mem[0:8];
    reg [53:0] exp_mem  [0:NLANES-1];
    reg [8*64-1:0] case_name, f_blk, f_hole, f_lanev, f_exp, f_param, case_dir;
    integer i, nbad;
    reg [31:0] rd, st;
    reg [17:0] x0, y0, br, fw, fh;
    reg [11:0] stp; reg [3:0] nbo; reg side_; reg [1:0] nh;
    reg [53:0] got;
    reg [31:0] w0, w1, w2, w3;   // 模块级（Verilog 不允许在无名块里声明）

    initial begin
        nbad = 0;
        if (!$value$plusargs("CASE=%s", case_name)) case_name = "fan";
        // ★ 2026-09-30：这里原先写的是 case_name[23:0] == "goal" —— 位宽笔误。
        //   case_name 声明成 512 位（第 67 行），字符串落在**低位**，
        //   所以 [23:0] 取出来只有 "oal"（3 个字符 24 位），与 32 位的 "goal"
        //   （零扩展到 0x006F616C）**永远不相等**。字符串比较恒假 ⇒
        //   `CASE=goal` 一直**静默地**跑 fan 的向量：goal 那一档
        //   （撞墙 / 进洞判定）在 xsim 里从来没验过，而它每次都报 PASS。
        //   本工程"静默失效"家族的新成员：报 PASS，PASS 的是另一套向量。
        //   改 [31:0]；顺带 case_dir 会打出来，跑的是哪一套**看得见**。
        if (case_name[31:0] == "goal") begin
            f_blk = "sim/v3/goal/blk_tab.mem";
            f_hole = "sim/v3/goal/hole_tab.mem";
            f_lanev = "sim/v3/goal/lane_v.mem";
            f_exp = "sim/v3/goal/exp.mem";
            f_param = "sim/v3/goal/params.mem";
            case_dir = "goal";
        // ★ 2026-10-04：比较宽度必须 48 位！"bounce" 是 6 字符 = 48 bit；
        //   [31:0] 只取到 "boun" 永不相等（与 09-30 的 [23:0]/"goal" 同一族的坑，
        //   这次是 case 名指纹门禁当场抓住的）。
        end else if (case_name[47:0] == "bounce") begin
            // ★ 2026-10-04 新增档：max_bounce=2（弹性回弹）逐位验证
            f_blk = "sim/v3/bounce/blk_tab.mem";
            f_hole = "sim/v3/bounce/hole_tab.mem";
            f_lanev = "sim/v3/bounce/lane_v.mem";
            f_exp = "sim/v3/bounce/exp.mem";
            f_param = "sim/v3/bounce/params.mem";
            case_dir = "bounce";
        end else begin
            f_blk = "sim/v3/fan/blk_tab.mem";
            f_hole = "sim/v3/fan/hole_tab.mem";
            f_lanev = "sim/v3/fan/lane_v.mem";
            f_exp = "sim/v3/fan/exp.mem";
            f_param = "sim/v3/fan/params.mem";
            case_dir = "fan";
        end
        $readmemh(f_blk, blk_mem);
        $readmemh(f_hole, hole_mem);
        $readmemh(f_lanev, lanev_mem);
        $readmemh(f_exp, exp_mem);
        $readmemh(f_param, param_mem);

        awvalid = 0; wvalid = 0; bready = 0; arvalid = 0; rready = 0;
        rstn = 0; repeat (5) @(posedge clk); rstn = 1;

        $display("");
        $display("==== AXI 级自检 case=%0s ====", case_dir);
        // 参数
        axi_wr(12'h010, param_mem[0]);            // X0
        axi_wr(12'h014, param_mem[1]);            // Y0
        axi_wr(12'h018, param_mem[2]);            // MAX_STEPS
        axi_wr(12'h01C, param_mem[3]);            // MAX_BOUNCE
        axi_wr(12'h020, param_mem[4]);            // BALL_R
        axi_wr(12'h024, param_mem[5]);            // FIELD_W
        axi_wr(12'h028, param_mem[6]);            // FIELD_H
        axi_wr(12'h02C, (param_mem[8][1:0] << 2) | param_mem[7][0]);   // {n_holes, side}
        // 块表 / 洞表 / 每路初速度
        for (i = 0; i < NBLK; i = i + 1) begin          // 每块 5 字：ang/x0/y0/x1/y1
            axi_wr(12'h100 + i*20 + 0,  {9'd0, blk_mem[i][94:72]});
            axi_wr(12'h100 + i*20 + 4,  {14'd0, blk_mem[i][71:54]});
            axi_wr(12'h100 + i*20 + 8,  {14'd0, blk_mem[i][53:36]});
            axi_wr(12'h100 + i*20 + 12, {14'd0, blk_mem[i][35:18]});
            axi_wr(12'h100 + i*20 + 16, {14'd0, blk_mem[i][17:0]});
        end
        for (i = 0; i < 4; i = i + 1) begin
            axi_wr(12'h200 + i*12 + 0, {14'd0, hole_mem[i][17:0]});               // c
            axi_wr(12'h200 + i*12 + 4, {14'd0, hole_mem[i][35:18]});              // w
            axi_wr(12'h200 + i*12 + 8, {28'd0, hole_mem[i][39], hole_mem[i][38:37],
                                        hole_mem[i][36]});                        // meta
        end
        for (i = 0; i < NLANES; i = i + 1) begin
            axi_wr(12'h300 + i*8 + 0, {12'd0, lanev_mem[i][39:20]});
            axi_wr(12'h300 + i*8 + 4, {12'd0, lanev_mem[i][19:0]});
        end
        // ★ 回读参数自检（写没进去一眼可见；上板前这道必须有）
        axi_rd(12'h010, rd); $display("  [回读] X0=%0d (期望 %0d)", rd[17:0], param_mem[0]);
        axi_rd(12'h014, rd); $display("  [回读] Y0=%0d (期望 %0d)", rd[17:0], param_mem[1]);
        axi_rd(12'h018, rd); $display("  [回读] STEPS=%0d (期望 %0d)", rd[11:0], param_mem[2]);
        axi_rd(12'h020, rd); $display("  [回读] BALL_R=%0d (期望 %0d)", rd[17:0], param_mem[4]);
        axi_rd(12'h024, rd); $display("  [回读] FIELD_W=%0d (期望 %0d)", rd[17:0], param_mem[5]);
        axi_rd(12'h028, rd); $display("  [回读] FIELD_H=%0d (期望 %0d)", rd[17:0], param_mem[6]);
        // ★ 偷看 DUT 内部：表到底有没有传进 traj_bundle（分层引用）
        $display("  [内部] blk_tab[0]   = %h   源 blk_mem[0] = %h", dut.blk_tab[94:0], blk_mem[0]);
        $display("  [内部] lane_v[0]    = %h (期望 %h)", dut.lane_v_tab[39:0], lanev_mem[0]);
        $display("  [内部] lane_v[1]    = %h (期望 %h)", dut.lane_v_tab[79:40], lanev_mem[1]);
        $display("  [内部] hole_tab[0]  = %h", dut.hole_tab[39:0]);
        // 启动
        axi_wr(12'h000, 32'h1);
        // 等 done
        st = 0;
        for (i = 0; i < 200000 && !st[0]; i = i + 1) begin
            axi_rd(12'h004, st);
        end
        if (!st[0]) begin
            $display("  ★ 超时：没等到 done");
            nbad = nbad + 1;
        end else begin
            $display("  扫完 17 路，耗时 %0d 周期（100MHz 下 %.1f us）", st[15:8], st[15:8] * 0.01);
        end
        // 读结果并逐位比对（每路 4 个字：res+ex / ey / steps / bounces）
        for (i = 0; i < NLANES; i = i + 1) begin
            axi_rd(12'h400 + i*16 + 0,  w0);
            axi_rd(12'h400 + i*16 + 4,  w1);
            axi_rd(12'h400 + i*16 + 8,  w2);
            axi_rd(12'h400 + i*16 + 12, w3);
            got = {w0[19:18], w0[17:0], w1[17:0], w2[11:0], w3[3:0]};
            if (got !== exp_mem[i]) begin
                nbad = nbad + 1;
                $display("  ★ 路 %0d 不一致：期望 %h 实测 %h", i, exp_mem[i], got);
            end
        end
        $display("");
        if (nbad == 0) $display(">>> PASS：AXI 写参数/读结果整条路径与黄金模型逐位一致");
        else           $display(">>> FAIL：%0d 处不一致", nbad);
        $finish;
    end

endmodule
