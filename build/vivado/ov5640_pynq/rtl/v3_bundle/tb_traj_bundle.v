// ============================================================================
// tb_traj_bundle.v —— V3 并行轨迹束的自检测试平台
//
// 向量来自定点黄金模型（tools/v3_dump_tb.py，它已与浮点引擎对拍过）：
//   <case>/blk_tab.mem  块表   （每行 95 bit hex）
//   <case>/hole_tab.mem 洞表   （40 bit）
//   <case>/lane_v.mem   17 路初速度（40 bit = {vx,vy}）
//   <case>/exp.mem      17 路期望结果（54 bit = {res,ex,ey,nstep,nbounce}）
//   <case>/params.mem   x0 y0 max_steps max_bounce ball_r field_w field_h side n_holes
//
// 用法（在工程根目录跑）：
//   xvlog rtl/v3_bundle/traj_lane.v rtl/v3_bundle/traj_bundle.v rtl/v3_bundle/tb_traj_bundle.v
//   xelab -debug typical tb_traj_bundle -s v3sim
//   xsim v3sim -runall -testplusarg CASE=fan      （或 CASE=goal）
//
// 判据：17 路的结果字必须与 exp.mem **逐位相同**；一点不同就报出来是哪一路、哪一位。
// 顺带量出"一簇 17 路扫完用多少周期"（V3 的并行性卖点，100MHz 下换算成微秒）。
// ============================================================================
`timescale 1ns / 1ps

module tb_traj_bundle;
    parameter integer NLANES = 17;
    parameter integer NBLK   = 9;

    reg clk = 1'b0;
    reg rstn = 1'b0;
    always #5 clk = ~clk;                 // 100 MHz

    // ---- 场景参数 ----
    reg [17:0] x0, y0, ball_r, field_w, field_h;
    reg [11:0] max_steps;
    reg [3:0]  max_bounce;
    reg        side;
    reg [1:0]  n_holes;
    reg [NBLK*95-1:0]   blk_tab;
    reg [4*40-1:0]      hole_tab;
    reg [NLANES*40-1:0] lane_v_tab;
    reg        start;

    // ---- ★ 2026-10-27 多件物理（3 障碍 / 2 对门）----
    reg        p_obs_en0, p_obs_en1, p_obs_en2;
    reg [17:0] p_obs0_x0, p_obs0_y0, p_obs0_x1, p_obs0_y1;
    reg [17:0] p_obs0_cx, p_obs0_cy, p_obs0_ehw, p_obs0_ehh;
    reg signed [22:0] p_obs0_ca, p_obs0_sa;
    reg [17:0] p_obs1_x0, p_obs1_y0, p_obs1_x1, p_obs1_y1;
    reg [17:0] p_obs1_cx, p_obs1_cy, p_obs1_ehw, p_obs1_ehh;
    reg signed [22:0] p_obs1_ca, p_obs1_sa;
    reg [17:0] p_obs2_x0, p_obs2_y0, p_obs2_x1, p_obs2_y1;
    reg [17:0] p_obs2_cx, p_obs2_cy, p_obs2_ehw, p_obs2_ehh;
    reg signed [22:0] p_obs2_ca, p_obs2_sa;
    reg        p_port_en0, p_port_en1;
    reg [17:0] p_port0_x0, p_port0_y0, p_port0_x1, p_port0_y1;
    reg [17:0] p_port1_x0, p_port1_y0, p_port1_x1, p_port1_y1;
    reg [17:0] p_port_r;
    reg [31:0] phys_mem [0:42];
    reg        is_phys;

    // ---- DUT ----
    wire        res_we;
    wire [4:0]  res_addr;
    wire [53:0] res_data;
    wire        pt_we;
    wire [4:0]  pt_lane;
    wire [7:0]  pt_idx;
    wire [35:0] pt_data;
    wire        done;

    traj_bundle #(.NLANES(NLANES), .NBLK(NBLK)) dut (
        .clk(clk), .rstn(rstn), .start(start),
        .x0(x0), .y0(y0), .max_steps(max_steps), .max_bounce(max_bounce),
        .ball_r(ball_r), .field_w(field_w), .field_h(field_h),
        .side(side), .n_holes(n_holes),
        .obs_en(p_obs_en0), .obs_x0(p_obs0_x0), .obs_y0(p_obs0_y0),
        .obs_x1(p_obs0_x1), .obs_y1(p_obs0_y1),
        .obs_cx(p_obs0_cx), .obs_cy(p_obs0_cy),
        .obs_ehw(p_obs0_ehw), .obs_ehh(p_obs0_ehh),
        .obs_ca(p_obs0_ca), .obs_sa(p_obs0_sa),
        .obs1_en(p_obs_en1), .obs2_en(p_obs_en2),
        .obs1_x0(p_obs1_x0), .obs1_y0(p_obs1_y0),
        .obs1_x1(p_obs1_x1), .obs1_y1(p_obs1_y1),
        .obs1_cx(p_obs1_cx), .obs1_cy(p_obs1_cy),
        .obs1_ehw(p_obs1_ehw), .obs1_ehh(p_obs1_ehh),
        .obs1_ca(p_obs1_ca), .obs1_sa(p_obs1_sa),
        .obs2_x0(p_obs2_x0), .obs2_y0(p_obs2_y0),
        .obs2_x1(p_obs2_x1), .obs2_y1(p_obs2_y1),
        .obs2_cx(p_obs2_cx), .obs2_cy(p_obs2_cy),
        .obs2_ehw(p_obs2_ehw), .obs2_ehh(p_obs2_ehh),
        .obs2_ca(p_obs2_ca), .obs2_sa(p_obs2_sa),
        .port_en(p_port_en0), .port_x0(p_port0_x0), .port_y0(p_port0_y0),
        .port_x1(p_port0_x1), .port_y1(p_port0_y1), .port_r(p_port_r),
        .port1_en(p_port_en1), .port1_x0(p_port1_x0), .port1_y0(p_port1_y0),
        .port1_x1(p_port1_x1), .port1_y1(p_port1_y1),
        .blk_tab(blk_tab), .hole_tab(hole_tab), .lane_v_tab(lane_v_tab),
        .res_we(res_we), .res_addr(res_addr), .res_data(res_data),
        .pt_we(pt_we), .pt_lane(pt_lane), .pt_idx(pt_idx), .pt_data(pt_data),
        .done(done)
    );

    // ---- 收集结果 ----
    reg [53:0] exp_mem [0:NLANES-1];
    reg [53:0] got     [0:NLANES-1];
    reg        seen    [0:NLANES-1];
    integer    n_pt;
    integer    cyc;

    always @(posedge clk) if (res_we) begin
        got[res_addr]  <= res_data;
        seen[res_addr] <= 1'b1;
    end
    always @(posedge clk) if (pt_we) n_pt = n_pt + 1;

    // ---- 向量 ----
    reg [94:0] blk_mem  [0:NBLK-1];
    reg [39:0] hole_mem [0:3];
    reg [39:0] lanev_mem[0:NLANES-1];
    reg [31:0] param_mem[0:8];
    reg [8*64-1:0] case_name;            // +CASE=fan / goal（只传名字，路径在下面拼）
    reg [8*64-1:0] case_dir;             // sim/v3/<name>
    reg [8*64-1:0] f_blk, f_hole, f_lanev, f_exp, f_param;   // 完整路径（不拼接）

    integer i, j, nbad, ncase;

    task load_and_run;
        begin
            // 复位
            rstn = 1'b0; start = 1'b0;
            repeat (4) @(posedge clk);
            rstn = 1'b1;
            @(posedge clk);

            // 读向量
            // ★ 路径**直接赋完整字符串**，绝不做拼接：Verilog 里 {"sim/v3/","fan"} 是
            //   1024 位，赋给 512 位寄存器时高半被截断 —— 第一版就这么丢的，结果
            //   读到的是 "fan/blk_tab.mem"（不存在）→ 全部 WARNING + 参数全 x。
            $readmemh(f_blk,  blk_mem);
            $readmemh(f_hole, hole_mem);
            $readmemh(f_lanev, lanev_mem);
            $readmemh(f_exp,  exp_mem);
            $readmemh(f_param, param_mem);

            x0         = param_mem[0][17:0];
            y0         = param_mem[1][17:0];
            max_steps  = param_mem[2][11:0];
            max_bounce = param_mem[3][3:0];
            ball_r     = param_mem[4][17:0];
            field_w    = param_mem[5][17:0];
            field_h    = param_mem[6][17:0];
            side       = param_mem[7][0];
            n_holes    = param_mem[8][1:0];

            // ---- ★ 多件物理：默认全 0；phys 档从 phys.mem 读 43 个字 ----
            p_obs_en0 = 0; p_obs_en1 = 0; p_obs_en2 = 0;
            p_obs0_x0=0; p_obs0_y0=0; p_obs0_x1=0; p_obs0_y1=0;
            p_obs0_cx=0; p_obs0_cy=0; p_obs0_ehw=0; p_obs0_ehh=0;
            p_obs0_ca=0; p_obs0_sa=0;
            p_obs1_x0=0; p_obs1_y0=0; p_obs1_x1=0; p_obs1_y1=0;
            p_obs1_cx=0; p_obs1_cy=0; p_obs1_ehw=0; p_obs1_ehh=0;
            p_obs1_ca=0; p_obs1_sa=0;
            p_obs2_x0=0; p_obs2_y0=0; p_obs2_x1=0; p_obs2_y1=0;
            p_obs2_cx=0; p_obs2_cy=0; p_obs2_ehw=0; p_obs2_ehh=0;
            p_obs2_ca=0; p_obs2_sa=0;
            p_port_en0 = 0; p_port_en1 = 0;
            p_port0_x0=0; p_port0_y0=0; p_port0_x1=0; p_port0_y1=0;
            p_port1_x0=0; p_port1_y0=0; p_port1_x1=0; p_port1_y1=0;
            p_port_r = 18 * 256;
            if (is_phys) begin
                $readmemh("sim/v3/phys/phys.mem", phys_mem);
                p_obs_en0 = phys_mem[0][0];
                p_obs0_x0 = phys_mem[1][17:0];  p_obs0_y0 = phys_mem[2][17:0];
                p_obs0_x1 = phys_mem[3][17:0];  p_obs0_y1 = phys_mem[4][17:0];
                p_obs0_cx = phys_mem[5][17:0];  p_obs0_cy = phys_mem[6][17:0];
                p_obs0_ehw = phys_mem[7][17:0]; p_obs0_ehh = phys_mem[8][17:0];
                p_obs0_ca = phys_mem[9][22:0];  p_obs0_sa = phys_mem[10][22:0];
                p_obs_en1 = phys_mem[11][0];
                p_obs1_x0 = phys_mem[12][17:0]; p_obs1_y0 = phys_mem[13][17:0];
                p_obs1_x1 = phys_mem[14][17:0]; p_obs1_y1 = phys_mem[15][17:0];
                p_obs1_cx = phys_mem[16][17:0]; p_obs1_cy = phys_mem[17][17:0];
                p_obs1_ehw = phys_mem[18][17:0]; p_obs1_ehh = phys_mem[19][17:0];
                p_obs1_ca = phys_mem[20][22:0]; p_obs1_sa = phys_mem[21][22:0];
                p_obs_en2 = phys_mem[22][0];
                p_obs2_x0 = phys_mem[23][17:0]; p_obs2_y0 = phys_mem[24][17:0];
                p_obs2_x1 = phys_mem[25][17:0]; p_obs2_y1 = phys_mem[26][17:0];
                p_obs2_cx = phys_mem[27][17:0]; p_obs2_cy = phys_mem[28][17:0];
                p_obs2_ehw = phys_mem[29][17:0]; p_obs2_ehh = phys_mem[30][17:0];
                p_obs2_ca = phys_mem[31][22:0]; p_obs2_sa = phys_mem[32][22:0];
                p_port_en0 = phys_mem[33][0];
                p_port0_x0 = phys_mem[34][17:0]; p_port0_y0 = phys_mem[35][17:0];
                p_port0_x1 = phys_mem[36][17:0]; p_port0_y1 = phys_mem[37][17:0];
                p_port_en1 = phys_mem[38][0];
                p_port1_x0 = phys_mem[39][17:0]; p_port1_y0 = phys_mem[40][17:0];
                p_port1_x1 = phys_mem[41][17:0]; p_port1_y1 = phys_mem[42][17:0];
            end

            blk_tab = 0;
            for (i = 0; i < NBLK; i = i + 1)
                blk_tab[i*95 +: 95] = blk_mem[i];
            hole_tab = 0;
            for (i = 0; i < 4; i = i + 1)
                hole_tab[i*40 +: 40] = hole_mem[i];
            lane_v_tab = 0;
            for (i = 0; i < NLANES; i = i + 1)
                lane_v_tab[i*40 +: 40] = lanev_mem[i];

            for (i = 0; i < NLANES; i = i + 1) begin
                seen[i] = 1'b0; got[i] = 54'd0;
            end
            n_pt = 0; cyc = 0;

            $display("");
            $display("==== case %0s ====  x0=%0d y0=%0d steps=%0d bounce=%0d holes=%0d side=%0d",
                     case_dir, x0, y0, max_steps, max_bounce, n_holes, side);
            @(posedge clk);
            start = 1'b1;
            @(posedge clk);
            start = 1'b0;

            // 计时 + 等 done
            while (!done && cyc < 800000) begin   // ★ 2026-10-22：PLANES 4→2，bounce 档更长
                @(posedge clk);
                cyc = cyc + 1;
            end
            repeat (4) @(posedge clk);        // 让最后一路的结果落进收集寄存器

            if (!done) begin
                $display("  ★ 超时：%0d 周期内没等到 done", cyc);
                // ★ 2026-10-04：死锁诊断——把"还没出结果"的每一路的 FSM 内部状态打出来
                //   （停在哪一 op/sub、步数/反弹数到哪、pt 握手的 req 状态）。靠猜没用，
                //   状态自己说话。（本次 max_bounce 修复首轮就靠它定位）
                // ★ 层级引用必须用**常量下标**（变量下标 xsim 精化不了，实测报
                //   "'u_lane' is not declared under prefix 'g_lane'"）——固定看 13~16 路
                //   （max_bounce 修复首轮卡的是 15 路）。
                $display("     死锁诊断 路0: op=%0d sub=%0d step=%0d nb=%0d req=%b",
                         dut.g_lane[0].u_lane.op, dut.g_lane[0].u_lane.sub,
                         dut.g_lane[0].u_lane.step_i, dut.g_lane[0].u_lane.bounce_i,
                         dut.g_lane[0].u_lane.pt_req);
                $display("     死锁诊断 路1: op=%0d sub=%0d step=%0d nb=%0d req=%b",
                         dut.g_lane[1].u_lane.op, dut.g_lane[1].u_lane.sub,
                         dut.g_lane[1].u_lane.step_i, dut.g_lane[1].u_lane.bounce_i,
                         dut.g_lane[1].u_lane.pt_req);
                $display("     ph 路0: stage=%0d phase=%0d idx=%0d ob=%0d pair=%0d en=%b%b%b%b psq=%b%b",
                         dut.g_lane[0].u_lane.ph_stage,
                         dut.g_lane[0].u_lane.ph_phase,
                         dut.g_lane[0].u_lane.ph_idx,
                         dut.g_lane[0].u_lane.ob_idx,
                         dut.g_lane[0].u_lane.pt_pair,
                         dut.g_lane[0].u_lane.obs_en_0_r,
                         dut.g_lane[0].u_lane.obs_en_1_r,
                         dut.g_lane[0].u_lane.port_en_0_r,
                         dut.g_lane[0].u_lane.port_en_1_r,
                         dut.g_lane[0].u_lane.p_sq0_r,
                         dut.g_lane[0].u_lane.p_sq1_r);
                // ★ 2026-10-22：PLANES 4→2，路2/路3 不存在了；只保留 0/1 两路诊断。
                nbad = nbad + 1;
            end else begin
                $display("  扫完 %0d 路用了 %0d 周期（100MHz 下 = %.1f us），轨迹点 %0d 个",
                         NLANES, cyc, cyc * 0.01, n_pt);
            end

            // 逐位比对
            for (i = 0; i < NLANES; i = i + 1) begin
                if (!seen[i]) begin
                    $display("  ★ 路 %0d 没有结果写出", i);
                    nbad = nbad + 1;
                end else if (got[i] !== exp_mem[i]) begin
                    nbad = nbad + 1;
                    $display("  ★ 路 %0d 不一致：", i);
                    $display("      期望 res=%0d ex=%0d ey=%0d step=%0d bounce=%0d",
                             exp_mem[i][53:52], exp_mem[i][51:34], exp_mem[i][33:16],
                             exp_mem[i][15:4], exp_mem[i][3:0]);
                    $display("      实测 res=%0d ex=%0d ey=%0d step=%0d bounce=%0d  xor=%h",
                             got[i][53:52], got[i][51:34], got[i][33:16],
                             got[i][15:4], got[i][3:0], got[i] ^ exp_mem[i]);
                end else begin
                    $display("  路 %2d  OK  res=%0d 终点(%0d,%0d) 步数=%0d",
                             i, got[i][53:52], got[i][51:34], got[i][33:16], got[i][15:4]);
                end
            end
        end
    endtask

    initial begin
        nbad = 0;
        // ★ 只传名字（如 CASE=fan）：MSYS/Git-Bash 会把带斜杠的实参当路径改写，
        //   传 "sim/v3/fan" 这种会在 xsim 那儿变成非法参数、直接打帮助。
        if (!$value$plusargs("CASE=%s", case_name))
            case_name = "fan";
        is_phys = 1'b0;
        // ★ 2026-09-30：这里原先写的是 case_name[23:0] == "goal" —— 位宽笔误。
        //   case_name 声明成 512 位（第 79 行），字符串落在**低位**，
        //   所以 [23:0] 取出来只有 "oal"（3 个字符 24 位），与 32 位的 "goal"
        //   （零扩展到 0x006F616C）**永远不相等**。字符串比较恒假 ⇒
        //   `CASE=goal` 一直**静默地**跑 fan 的向量：goal 那一档
        //   （撞墙 / 进洞判定）在 xsim 里从来没验过，而它每次都报 PASS。
        //   本工程"静默失效"家族的新成员：报 PASS，PASS 的是另一套向量。
        //   （2026-09-30 就是靠下面 case_dir 那行打印才发现的。）
        //   改 [31:0]；case_dir 会打出来，跑的是哪一套**看得见**。
        if (case_name[31:0] == "goal") begin
            f_blk   = "sim/v3/goal/blk_tab.mem";
            f_hole  = "sim/v3/goal/hole_tab.mem";
            f_lanev = "sim/v3/goal/lane_v.mem";
            f_exp   = "sim/v3/goal/exp.mem";
            f_param = "sim/v3/goal/params.mem";
        // ★ 2026-10-04：比较宽度必须 48 位！"bounce" 是 6 字符 = 48 bit；
        //   [31:0] 只取到 "boun" 永不相等（与 09-30 的 [23:0]/"goal" 同一族的坑，
        //   这次是 case 名指纹门禁当场抓住的）。
        end else if (case_name[47:0] == "bounce") begin
            // ★ 2026-10-04 新增档：max_bounce=2（弹性回弹）逐位验证
            f_blk   = "sim/v3/bounce/blk_tab.mem";
            f_hole  = "sim/v3/bounce/hole_tab.mem";
            f_lanev = "sim/v3/bounce/lane_v.mem";
            f_exp   = "sim/v3/bounce/exp.mem";
            f_param = "sim/v3/bounce/params.mem";
        end else if (case_name[31:0] == "phys") begin
            is_phys = 1'b1;
            f_blk   = "sim/v3/phys/blk_tab.mem";
            f_hole  = "sim/v3/phys/hole_tab.mem";
            f_lanev = "sim/v3/phys/lane_v.mem";
            f_exp   = "sim/v3/phys/exp.mem";
            f_param = "sim/v3/phys/params.mem";
        end else begin
            f_blk   = "sim/v3/fan/blk_tab.mem";
            f_hole  = "sim/v3/fan/hole_tab.mem";
            f_lanev = "sim/v3/fan/lane_v.mem";
            f_exp   = "sim/v3/fan/exp.mem";
            f_param = "sim/v3/fan/params.mem";
        end
        case_dir = f_blk;
        $display("=== V3 并行轨迹束 自检开始（向量目录 %0s）===", case_dir);
        load_and_run;
        $display("");
        if (nbad == 0) $display(">>> PASS：17 路结果与定点黄金模型逐位一致");
        else           $display(">>> FAIL：%0d 处不一致", nbad);
        $finish;
    end

endmodule
