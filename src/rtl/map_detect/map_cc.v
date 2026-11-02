// ============================================================================
// map_cc.v —— 差距 1 第二级：形态学 OPEN + 8 连通 CCL + 候选统计
//
// 输入：11520 个像素的 3 位掩码流（{black,blue,red}，raster 序，1 像素/拍）。
// 输出：候选数组 cand[0..MAX_CAND-1]（cls/area/Σx/Σy），n_cand。
//
// 全流程（一帧）：
//   1) RX：掩码流入 mask_buf[0..11519]；
//   2) 形态学 OPEN（3x3 全 1）：腐蚀 -> 膨胀，**逐行**用 3 条 line buffer 算；
//      OpenCV 边界口径：腐蚀边界=1、膨胀边界=0（方案 §A）；
//   3) 三色依次 8 连通 CCL：单遍扫描 + union-find（W/NW/N/NE 四邻居，
//      正是 8 连通的"已扫描邻居"集合），统计 area/Σx/Σy；
//   4) 扫完把非根统计合并到根，再按 area∈[MIN_AREA,MAX_AREA] 输出候选。
//
// 算法口径已用 Python 原型与 cv2.connectedComponentsWithStats 对拍：
//   本快照 open 后 红 1 / 蓝 3 / 黑 5 个连通域，面积与 Σx/Σy 逐值相等。
//
// 周期预算（100MHz）：RX 11.5k + 形态 2x~23k + CCL 3x~60k ≈ 250k ≈ 2.5ms；
// 相机 15fps（66ms/帧）下余量充足。
// 资源预算：3 块 11520x3 缓冲（~3 BRAM36）+ stats 4096x55（~6.3 BRAM36）
//          + parent 4096x12（~1.4 BRAM36）+ 控制/比较逻辑（~2-4k LUT）。
//
// ★ 存储器写法约定（为了推断 BRAM，而不是爆 FF/LUTRAM）：
//   par/stats 每个时钟最多**一读一写**；union 的多个父指针分 4 拍写
//   （S_C_UNION），归约时两个 stats 分两拍读（S_C_MR2）。
// ============================================================================
`timescale 1ns / 1ps

module map_cc #(
    parameter integer BW        = 128,
    parameter integer BH        = 90,
    parameter integer NPIX      = BW * BH,   // 11520
    parameter integer MAX_CAND  = 32,
    parameter integer MIN_AREA  = 4,
    parameter integer MAX_AREA  = 6031,
    parameter integer NLAB      = 4096,      // 12 位标签
    parameter integer MAXFIND   = 32         // find 链上限（防死循环）
)(
    input  wire        clk,
    input  wire        rstn,

    input  wire        start,          // 1 拍：开始接收新一帧掩码
    input  wire        in_valid,
    input  wire [2:0]  in_mask,        // {black,blue,red}

    output reg         busy,
    output reg         done,           // 1 拍
    output reg  [5:0]  n_cand,
    output reg         cand_ovf,       // 候选数超过 MAX_CAND
    output reg         label_ovf,      // 标签数超过 NLAB-1
    input  wire [5:0]  cand_addr,
    output reg  [56:0] cand_data       // {cls[1:0],area[13:0],sx[20:0],sy[19:0]}
);

    localparam [5:0] S_IDLE = 5'd0, S_RX = 5'd1,
                     S_M_PRE = 5'd2, S_M_LD = 5'd3, S_M_CALC = 5'd4,
                     S_M_NEXT = 5'd5,
                     S_C_CLR = 5'd6, S_C_READ = 5'd7, S_C_EVAL = 5'd8,
                     S_C_UNION = 5'd9, S_C_STAT_R = 5'd10, S_C_STAT_W = 5'd11,
                     S_C_RES = 5'd12, S_C_RSTEP = 5'd13, S_C_RCHK = 5'd14,
                     S_C_MR2 = 5'd15, S_C_MERGE = 5'd16,
                     S_C_OUT_R = 5'd17, S_C_OUT_C = 5'd18, S_C_OUT_W = 5'd19,
                     // 单读口等待拍（见 par/stats 的读口注释）
                     S_C_RSTEP2 = 5'd20, S_C_RW1 = 5'd21, S_C_RW2 = 5'd22,
                     S_C_RW3 = 5'd23, S_C_STAT_RW = 5'd24,
                     S_C_OUT_R2 = 5'd25, S_C_OUT_RW = 5'd26,
                     S_C_EVAL2 = 5'd27, S_C_EVAL3 = 5'd28, S_C_EVAL4 = 5'd29,
                     // 形态学地址/写口流水（2026-10-22 走线拆分）
                     S_M_LDR = 6'd30, S_M_LDW = 6'd31, S_M_CALCW = 6'd32;

    localparam [11:0] L_NONE = 12'd0;

    // ---------------------------------------------------------------- 缓存
    // ★ 2026-10-22 板上构建实锤：不加属性时 Vivado 把下面这些大数组全推断成
    //   **分布式 RAM**（24,512 个 LUTRAM，器件只有 17,400）→ place 前 DRC 拦死。
    //   强制 block：三色缓存 3×34.5k bit + stats 225k bit + par 49k bit ≈ 12 BRAM36。
    (* ram_style = "block" *) reg [2:0] mask_buf [0:NPIX-1];
    (* ram_style = "block" *) reg [2:0] ero_buf  [0:NPIX-1];
    (* ram_style = "block" *) reg [2:0] open_buf [0:NPIX-1];

    // 3 条 line buffer（每行 BW 个 3 位像素）
    reg [2:0] lb [0:2][0:BW-1];

    // CCL 行标签（双行，parity 轮换）
    reg [11:0] lab_row [0:1][0:BW-1];

    // union-find + 统计（stats={sy[19:0],sx[20:0],area[13:0]} = 55 位）
    (* ram_style = "block" *) reg [11:0] par   [0:NLAB-1];
    (* ram_style = "block" *) reg [54:0] stats [0:NLAB-1];
    // ★ 单一写口控制（组合选通）：多个状态分支直接写数组会被判"多写口"→LUTRAM
    reg        par_we, st_we;
    reg [11:0] par_wa, par_wd, st_wa;
    reg [54:0] st_wd;

    // 候选
    reg [56:0] cand [0:MAX_CAND-1];

    // ---------------------------------------------------------------- 控制
    reg [5:0]  st;
    reg        m_pass;          // 0=腐蚀 1=膨胀
    reg [6:0]  m_y, m_x;
    reg [1:0]  sel0, selp, selm;
    reg [8:0]  pre_i;
    reg [13:0] rx_i;
    // 形态学流水：读/写地址与数据先寄存，路径不再从 FSM 直接拉到 BRAM/line buffer
    reg [13:0] m_ra, m_wa;
    reg [6:0]  lb_wa;
    reg [1:0]  lb_sel;
    reg [2:0]  src_rd, mor_r;

    reg [13:0] scan_p;
    reg [6:0]  scan_x, scan_y;
    reg [2:0]  scan_m;
    reg        cur_par;
    wire       prev_par = ~cur_par;
    reg [11:0] new_label;
    reg [4:0]  ccl_c;
    reg [11:0] clr_i;

    reg [11:0] L_save;
    reg [11:0] nb_min;
    // ★ 2026-10-22 时序：CCL 扫描拆三级流水（读邻居→求最小→决策）
    reg [11:0] nb_w_r, nb_nw_r, nb_n_r, nb_ne_r, nb_min_r;
    reg [11:0] m01_r, m23_r;
    reg [11:0] nb_min2;
    reg [11:0] u0, u1, u2, u3;
    reg [1:0]  union_i;

    reg [11:0] res_l;
    reg [11:0] find_cur, find_steps, root_save, par_rd;
    reg [11:0] pa_ra, st_ra;          // par/stats 的唯一读地址
    reg [54:0] stat_rd, st_r, st_l;

    reg [11:0] out_l;

    // 当前扫描像素的邻居标签（组合）
    wire [11:0] nb_w  = (scan_x == 7'd0)  ? L_NONE : lab_row[cur_par][scan_x - 7'd1];
    wire [11:0] nb_nw = (scan_x == 7'd0)  ? L_NONE : lab_row[prev_par][scan_x - 7'd1];
    wire [11:0] nb_n  = lab_row[prev_par][scan_x];
    wire [11:0] nb_ne = (scan_x == 7'd127) ? L_NONE : lab_row[prev_par][scan_x + 7'd1];

    always @(*) begin
        nb_min = 12'hFFF;
        if (nb_w  != L_NONE && nb_w  < nb_min) nb_min = nb_w;
        if (nb_nw != L_NONE && nb_nw < nb_min) nb_min = nb_nw;
        if (nb_n  != L_NONE && nb_n  < nb_min) nb_min = nb_n;
        if (nb_ne != L_NONE && nb_ne < nb_min) nb_min = nb_ne;
        if (nb_min == 12'hFFF) nb_min = L_NONE;
    end

    always @(*) begin
        nb_min2 = 12'hFFF;
        if (nb_w_r  != L_NONE && nb_w_r  < nb_min2) nb_min2 = nb_w_r;
        if (nb_nw_r != L_NONE && nb_nw_r < nb_min2) nb_min2 = nb_nw_r;
        if (nb_n_r  != L_NONE && nb_n_r  < nb_min2) nb_min2 = nb_n_r;
        if (nb_ne_r != L_NONE && nb_ne_r < nb_min2) nb_min2 = nb_ne_r;
        if (nb_min2 == 12'hFFF) nb_min2 = L_NONE;
    end

    // 形态学 3x3 窗口（组合读 line buffer；越界按当前 pass 的边界值）
    wire [2:0] m_border = (m_pass == 1'b0) ? 3'b111 : 3'b000;
    reg  [2:0] mor_val;
    integer dyi, dxi;
    reg  [1:0] sel_dy;
    reg  [7:0] col_dx;
    always @(*) begin
        mor_val = m_border;
        for (dyi = -1; dyi <= 1; dyi = dyi + 1) begin
            for (dxi = -1; dxi <= 1; dxi = dxi + 1) begin
                sel_dy = (dyi == -1) ? selm : ((dyi == 0) ? sel0 : selp);
                col_dx = m_x + dxi;
                if ((m_x + dxi) >= 0 && (m_x + dxi) < BW) begin
                    if (m_pass == 1'b0)
                        mor_val = mor_val & lb[sel_dy][col_dx[6:0]];
                    else
                        mor_val = mor_val | lb[sel_dy][col_dx[6:0]];
                end
            end
        end
    end

    function [11:0] f_min2; input [11:0] a; input [11:0] b;
        begin
            if (a == L_NONE)      f_min2 = b;
            else if (b == L_NONE) f_min2 = a;
            else                  f_min2 = (a < b) ? a : b;
        end
    endfunction

    // stats 加法（字段独立，不会跨字段进位：总数 <= 全图）
    function [54:0] stat_add(input [54:0] a, input [54:0] b);
        reg [14:0] ar; reg [21:0] sx; reg [20:0] sy;
        begin
            ar = {1'b0, a[13:0]} + {1'b0, b[13:0]};
            sx = {1'b0, a[34:14]} + {1'b0, b[34:14]};
            sy = {1'b0, a[54:35]} + {1'b0, b[54:35]};
            stat_add = {sy[19:0], sx[20:0], ar[13:0]};
        end
    endfunction

    wire [54:0] stat_inc = {13'd0, scan_y, 14'd0, scan_x, 14'd1};

    // 候选读口（组合）
    always @(*) begin
        cand_data = cand[cand_addr];
    end

    integer ii;

    // 扫描推进（背景像素 / 统计写完后调用）
    task adv_scan;
        begin
            if (scan_x == BW - 1) begin
                scan_x <= 7'd0;
                if (scan_y == BH - 1) begin
                    res_l <= 12'd1;
                    st <= S_C_RES;
                end else begin
                    scan_y <= scan_y + 7'd1;
                    scan_p <= scan_p + 14'd1;
                    cur_par <= ~cur_par;
                    st <= S_C_READ;
                end
            end else begin
                scan_x <= scan_x + 7'd1;
                scan_p <= scan_p + 14'd1;
                st <= S_C_READ;
            end
        end
    endtask

    // 归约结束 -> 输出 / 下一色
    task adv_out;
        begin
            if (out_l >= new_label) begin
                if (ccl_c == 5'd2) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    st <= S_IDLE;
                end else begin
                    ccl_c <= ccl_c + 5'd1;
                    clr_i <= 12'd0;
                    st <= S_C_CLR;
                end
            end else begin
                out_l <= out_l + 12'd1;
                st <= S_C_OUT_R;
            end
        end
    endtask

    // ★★ BRAM 推断的两个硬前提：
    //   ① 只有一个读地址（pa_ra/st_ra）—— 多个读地址会退回 LUTRAM 并复制多份；
    //   ② 只有一个写口 —— 按状态组合选出 we/wa/wd，整块存储器只有一条
    //      `if (we) mem[wa] <= wd;` 写语句。原版在 CLR/EVAL/UNION/RCHK 里
    //      各写一次 par、在 CLR/STAT_W/MERGE 各写一次 stats，Vivado 判
    //      "多写口"→ ram_style=block infeasible → LUTRAM（24,512 个）。
    always @(*) begin
        par_we = 1'b0; par_wa = 12'd0; par_wd = 12'd0;
        st_we  = 1'b0; st_wa  = 12'd0; st_wd  = 55'd0;
        case (st)
        S_C_CLR: begin
            par_we = 1'b1; par_wa = clr_i; par_wd = 12'd0;
            st_we  = 1'b1; st_wa  = clr_i; st_wd  = 55'd0;
        end
        S_C_EVAL4: begin
            if ((nb_min_r == L_NONE) && (new_label < NLAB)) begin
                par_we = 1'b1; par_wa = new_label; par_wd = new_label;
            end
        end
        S_C_UNION: begin
            case (union_i)
            2'd0: if (u0 != L_NONE) begin par_we=1'b1; par_wa=u0; par_wd=L_save; end
            2'd1: if (u1 != L_NONE) begin par_we=1'b1; par_wa=u1; par_wd=L_save; end
            2'd2: if (u2 != L_NONE) begin par_we=1'b1; par_wa=u2; par_wd=L_save; end
            default: if (u3 != L_NONE) begin par_we=1'b1; par_wa=u3; par_wd=L_save; end
            endcase
        end
        S_C_RCHK: begin
            if ((par_rd == find_cur || find_steps >= MAXFIND) &&
                (find_cur != res_l)) begin
                par_we = 1'b1; par_wa = res_l; par_wd = find_cur;
            end
        end
        S_C_STAT_W: begin
            if (L_save != L_NONE) begin
                st_we = 1'b1; st_wa = L_save; st_wd = stat_add(stat_rd, stat_inc);
            end
        end
        S_C_MERGE: begin
            st_we = 1'b1; st_wa = root_save; st_wd = stat_add(st_r, st_l);
        end
        default: ;
        endcase
    end

    always @(posedge clk) begin
        if (!rstn) begin
            st <= S_IDLE; busy <= 1'b0; done <= 1'b0;
            n_cand <= 6'd0; cand_ovf <= 1'b0; label_ovf <= 1'b0;
            m_pass <= 1'b0; m_y <= 7'd0; m_x <= 7'd0; pre_i <= 9'd0;
            sel0 <= 2'd0; selp <= 2'd1; selm <= 2'd2;
            m_ra <= 14'd0; m_wa <= 14'd0; lb_wa <= 7'd0; lb_sel <= 2'd0;
            src_rd <= 3'd0; mor_r <= 3'd0;
            rx_i <= 14'd0; scan_p <= 14'd0; scan_x <= 7'd0; scan_y <= 7'd0;
            scan_m <= 3'd0; cur_par <= 1'b0; new_label <= 12'd1;
            ccl_c <= 5'd0; clr_i <= 12'd0; L_save <= 12'd0;
            u0 <= 12'd0; u1 <= 12'd0; u2 <= 12'd0; u3 <= 12'd0; union_i <= 2'd0;
            nb_w_r <= 12'd0; nb_nw_r <= 12'd0; nb_n_r <= 12'd0; nb_ne_r <= 12'd0;
            nb_min_r <= 12'd0; m01_r <= 12'd0; m23_r <= 12'd0;
            res_l <= 12'd0; find_cur <= 12'd0; find_steps <= 12'd0;
            root_save <= 12'd0;   // par_rd/stat_rd 由共享读块每拍驱动，复位块不许再赋值
            pa_ra <= 12'd0; st_ra <= 12'd0;
            st_r <= 55'd0; st_l <= 55'd0;
            out_l <= 12'd0;
            for (ii = 0; ii < MAX_CAND; ii = ii + 1) cand[ii] <= 57'd0;
        end else begin
            done <= 1'b0;
            // ★ 读和写必须在**同一个 always 块**里，Vivado 才肯把它们合成
            //   一个简单双口 BRAM（一读一写）；分两个进程会退回 LUTRAM。
            par_rd  <= par[pa_ra];
            stat_rd <= stats[st_ra];
            if (par_we) par[par_wa]  <= par_wd;    // 唯一写语句
            if (st_we)  stats[st_wa] <= st_wd;     // 唯一写语句

            case (st)
            // ============================================================ RX
            S_IDLE: begin
                busy <= 1'b0;
                if (start) begin
                    busy <= 1'b1;
                    rx_i <= 14'd0;
                    n_cand <= 6'd0; cand_ovf <= 1'b0; label_ovf <= 1'b0;
                    ccl_c <= 5'd0;
                    m_pass <= 1'b0;
                    m_y <= 7'd0; m_x <= 7'd0; pre_i <= 9'd0;
                    sel0 <= 2'd0; selp <= 2'd1; selm <= 2'd2;
                    st <= S_RX;
                end
            end
            S_RX: begin
                if (in_valid) begin
                    mask_buf[rx_i] <= in_mask;
                    if (rx_i == NPIX - 1) begin
                        pre_i <= 9'd0;
                        st <= S_M_PRE;
                    end else begin
                        rx_i <= rx_i + 14'd1;
                    end
                end
            end
            // ====================================================== 形态学
            S_M_PRE: begin
                // lb[0] 装**第 0 行真值**（y=0 计算时 sel0 就是它）；
                // lb[2] 装边界（y=-1）；lb[1] 先填边界，y=0 的 S_M_LD 会用第 1 行覆盖。
                // （第一版三块全填边界 —— 第 0 行整行丢失，黑色大域差 1~2 像素。）
                if (pre_i[8:7] == 2'd0) begin
                    if (m_pass == 1'b0)
                        lb[2'd0][pre_i[6:0]] <= mask_buf[pre_i[6:0]];
                    else
                        lb[2'd0][pre_i[6:0]] <= ero_buf[pre_i[6:0]];
                end else begin
                    lb[pre_i[8:7]][pre_i[6:0]] <= m_border;
                end
                if (pre_i == 9'd383) begin
                    pre_i <= 9'd0; m_y <= 7'd0; m_x <= 7'd0;
                    sel0 <= 2'd0; selp <= 2'd1; selm <= 2'd2;
                    st <= S_M_LD;
                end else begin
                    pre_i <= pre_i + 9'd1;
                end
            end
            S_M_LD: begin
                // 级 1：只寄存源地址 + line buffer 写地址（断开 FSM→BRAM 的走线）
                m_ra   <= (m_y + 7'd1) * BW + m_x;
                lb_sel <= selp;
                lb_wa  <= m_x;
                st <= S_M_LDR;
            end
            S_M_LDR: begin
                // 级 2：同步读源缓冲（BRAM 输出寄存）
                if (m_y == BH - 1)
                    src_rd <= m_border;
                else if (m_pass == 1'b0)
                    src_rd <= mask_buf[m_ra];
                else
                    src_rd <= ero_buf[m_ra];
                st <= S_M_LDW;
            end
            S_M_LDW: begin
                // 级 3：用寄存后的地址/数据写 line buffer
                lb[lb_sel][lb_wa] <= src_rd;
                if (m_x == BW - 1) begin
                    m_x <= 7'd0;
                    st <= S_M_CALC;
                end else begin
                    m_x <= m_x + 7'd1;
                    st <= S_M_LD;
                end
            end
            S_M_CALC: begin
                // 级 1：3x3 结果 + 目标地址寄存
                mor_r <= mor_val;
                m_wa  <= m_y * BW + m_x;
                st <= S_M_CALCW;
            end
            S_M_CALCW: begin
                // 级 2：写目标缓冲（地址/数据都来自寄存器）
                if (m_pass == 1'b0)
                    ero_buf[m_wa] <= mor_r;
                else
                    open_buf[m_wa] <= mor_r;
                if (m_x == BW - 1) begin
                    m_x <= 7'd0;
                    st <= S_M_NEXT;
                end else begin
                    m_x <= m_x + 7'd1;
                    st <= S_M_CALC;
                end
            end
            S_M_NEXT: begin
                selm <= sel0;
                sel0 <= selp;
                selp <= (selp == 2'd2) ? 2'd0 : (selp + 2'd1);
                if (m_y == BH - 1) begin
                    if (m_pass == 1'b0) begin
                        m_pass <= 1'b1;
                        m_y <= 7'd0; pre_i <= 9'd0;
                        st <= S_M_PRE;
                    end else begin
                        clr_i <= 12'd0;
                        st <= S_C_CLR;
                    end
                end else begin
                    m_y <= m_y + 7'd1;
                    st <= S_M_LD;
                end
            end
            // ========================================================= CCL
            S_C_CLR: begin
                if (clr_i < 12'd256)
                    lab_row[clr_i[8]][clr_i[7:0]] <= 12'd0;
                if (clr_i == NLAB - 1) begin
                    new_label <= 12'd1;
                    cur_par <= 1'b0;
                    scan_p <= 14'd0; scan_x <= 7'd0; scan_y <= 7'd0;
                    st <= S_C_READ;
                end else begin
                    clr_i <= clr_i + 12'd1;
                end
            end
            S_C_READ: begin
                scan_m <= open_buf[scan_p];
                st <= S_C_EVAL;
            end
            S_C_EVAL: begin
                if (!scan_m[ccl_c]) begin
                    lab_row[cur_par][scan_x] <= 12'd0;
                    adv_scan;
                end else begin
                    // 第 1 级：只把四个邻居标签锁进寄存器（断开 128 路读 mux）
                    nb_w_r  <= nb_w;
                    nb_nw_r <= nb_nw;
                    nb_n_r  <= nb_n;
                    nb_ne_r <= nb_ne;
                    st <= S_C_EVAL2;
                end
            end
            S_C_EVAL2: begin
                // 第 2 级：两两最小值（各 1 个 12 位比较器，并行）
                m01_r <= f_min2(nb_w_r,  nb_nw_r);
                m23_r <= f_min2(nb_n_r,  nb_ne_r);
                st <= S_C_EVAL3;
            end
            S_C_EVAL3: begin
                // 第 3 级：两两结果再取最小
                nb_min_r <= f_min2(m01_r, m23_r);
                st <= S_C_EVAL4;
            end
            S_C_EVAL4: begin
                // 第 4 级：决策 + 写回
                if (nb_min_r == L_NONE) begin
                    if (new_label < NLAB) begin
                        L_save <= new_label;
                        lab_row[cur_par][scan_x] <= new_label;
                        new_label <= new_label + 12'd1;
                    end else begin
                        label_ovf <= 1'b1;
                        L_save <= L_NONE;
                        lab_row[cur_par][scan_x] <= L_NONE;
                    end
                    st <= S_C_STAT_R;
                end else begin
                    L_save <= nb_min_r;
                    lab_row[cur_par][scan_x] <= nb_min_r;
                    u0 <= (nb_w_r  != L_NONE && nb_w_r  != nb_min_r) ? nb_w_r  : L_NONE;
                    u1 <= (nb_nw_r != L_NONE && nb_nw_r != nb_min_r) ? nb_nw_r : L_NONE;
                    u2 <= (nb_n_r  != L_NONE && nb_n_r  != nb_min_r) ? nb_n_r  : L_NONE;
                    u3 <= (nb_ne_r != L_NONE && nb_ne_r != nb_min_r) ? nb_ne_r : L_NONE;
                    union_i <= 2'd0;
                    st <= S_C_UNION;
                end
            end
            S_C_UNION: begin
                // 父指针写由组合写口 + 唯一写语句完成；这里只推进拍数
                if (union_i == 2'd3)
                    st <= S_C_STAT_R;
                else
                    union_i <= union_i + 2'd1;
            end
            S_C_STAT_R: begin
                st_ra <= L_save;
                st <= S_C_STAT_RW;
            end
            S_C_STAT_RW: begin
                st <= S_C_STAT_W;
            end
            S_C_STAT_W: begin
                adv_scan;
            end
            // ================================================ 归约（找根）
            S_C_RES: begin
                if (res_l >= new_label) begin
                    out_l <= 12'd1;
                    st <= S_C_OUT_R;
                end else begin
                    find_cur <= res_l;
                    find_steps <= 12'd0;
                    st <= S_C_RSTEP;
                end
            end
            S_C_RSTEP: begin
                pa_ra <= find_cur;
                st <= S_C_RSTEP2;
            end
            S_C_RSTEP2: begin
                st <= S_C_RCHK;
            end
            S_C_RCHK: begin
                if (par_rd == find_cur || find_steps >= MAXFIND) begin
                    root_save <= find_cur;
                    if (find_cur != res_l) begin
                        st_ra <= find_cur;           // 路径压缩写由组合写口完成
                        st <= S_C_RW1;
                    end else begin
                        res_l <= res_l + 12'd1;
                        st <= S_C_RES;
                    end
                end else begin
                    find_cur <= par_rd;
                    find_steps <= find_steps + 12'd1;
                    st <= S_C_RSTEP;
                end
            end
            S_C_RW1: begin
                st_ra <= res_l;
                st <= S_C_RW2;
            end
            S_C_RW2: begin
                st_r <= stat_rd;      // stats[find_cur]
                st <= S_C_RW3;
            end
            S_C_RW3: begin
                st_l <= stat_rd;      // stats[res_l]
                st <= S_C_MERGE;
            end
            S_C_MERGE: begin
                res_l <= res_l + 12'd1;
                st <= S_C_RES;
            end
            // ===================================================== 候选输出
            S_C_OUT_R: begin
                pa_ra <= out_l;
                st <= S_C_OUT_R2;
            end
            S_C_OUT_R2: begin
                st <= S_C_OUT_C;
            end
            S_C_OUT_C: begin
                if (par_rd == out_l) begin
                    st_ra <= out_l;
                    st <= S_C_OUT_RW;
                end else begin
                    adv_out;
                end
            end
            S_C_OUT_RW: begin
                st <= S_C_OUT_W;
            end
            S_C_OUT_W: begin
                if (stat_rd[13:0] >= MIN_AREA && stat_rd[13:0] <= MAX_AREA) begin
                    if (n_cand < MAX_CAND) begin
                        cand[n_cand] <= {ccl_c[1:0], stat_rd[13:0],
                                         stat_rd[34:14], stat_rd[54:35]};
                        n_cand <= n_cand + 6'd1;
                        if (n_cand == MAX_CAND - 1)
                            cand_ovf <= 1'b1;
                    end else begin
                        cand_ovf <= 1'b1;
                    end
                end
                adv_out;
            end
            default: st <= S_IDLE;
            endcase
        end
    end

endmodule
