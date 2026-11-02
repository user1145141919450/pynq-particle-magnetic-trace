// ============================================================================
// map_detect_core.v —— 差距 1：把缩略图的**颜色分类/检测**从 PS 挪进 PL。
//   v0：tap→V 中位→HSV→三色掩码计数；
//   v1：掩码流送 map_cc（3x3 OPEN + 8 连通 CCL + 候选统计），done 等 CCL 完成。
//   （2026-10-20：v0 的 S_P2 分支里 `st <= S_P2` 会覆盖收尾的 st<=S_CCW —— 已修，
//     否则处理完一帧就回不到 IDLE，第二帧永远不会处理。）
//
// 输入：vision_core 的 map 写口抽头（wr_en / wr_addr 字节地址 / wr_data 32 位，
//       低 16 = 偶列块、高 16 = 奇列块，均为 RGB565）。
// 输出：V 中位、vhi、红/蓝/黑三色掩码像素计数（第一版；连通域/候选下一版）。
//
// 流程（每帧末触发，处理与下一帧写入解耦）：
//   1) 抽头写 map_wr[0..5759]；
//   2) 末字（word 5759）→ 帧末；把 map_wr 复制到 map_eng（防下一帧覆盖）；
//   3) P1：11520 像素的 V=max(R,G,B) 直方图（256 bin）；
//   4) 中位：cumsum 到 N/2（与 PS `searchsorted(cumsum,N/2)` 同口径）→ med/vhi；
//   5) P2：逐像素 565→888→S/H（整数运算，逐位可复现）→ 三色掩码计数。
//
// 与 PS 的对照基准（2026-10-20 map_snap_gap1.bin，frame_cnt=15648）：
//   med=132 vhi=72 red=21 blue=115 black=716
//   （PS 用 cv2 的 HSV 定点实现；本模块用数学精确整数式，在**这张真实快照**
//     上三色掩码逐像素完全一致；S 有 196 个像素 ±1，但都远离 50/110 阈值。）
//
// 口径（照抄 ball_map_detect.detect_map，:395-407）：
//   v_thr = max(70, 0.55*medianV); vhi = ceil(v_thr)-1
//   red  = (H<=12 | H>=165) & S>=50 & V>=35
//   blue = 85<=H<=135       & S>=50 & V>=35
//   black= V<=vhi           & S<=110
// ============================================================================
`timescale 1ns / 1ps

module map_detect_core #(
    parameter integer NW   = 5760,     // 缩略图 32 位字数（128x90 / 2）
    parameter integer NBIN = 256,      // V 直方图 bin 数
    // 分类阈值（与 PS 默认口径一致；日后由 AXI 配置覆盖）
    parameter integer THR_S     = 50,  // 红/蓝掩码 S 下限
    parameter integer THR_V     = 35,  // 红/蓝掩码 V 下限
    parameter integer THR_BS    = 110, // 黑掩码 S 上限
    parameter integer DEBUG     = 0    // 1 = 输出逐像素 H/S/V 调试口
)(
    input  wire        clk,
    input  wire        rstn,

    // ---- vision_core map 写口抽头 ----
    input  wire        wr_en,
    input  wire [14:0] wr_addr,        // **字节地址**（低 2 位恒 0）
    input  wire [31:0] wr_data,

    // ---- 结果（第一版：直连寄存器）----
    output reg  [7:0]  med,            // V 中位
    output reg  [7:0]  vhi,            // ceil(max(70,0.55*med))-1
    output reg  [15:0] cnt_red,
    output reg  [15:0] cnt_blue,
    output reg  [15:0] cnt_black,
    output reg  [31:0] frame_seq,      // 已处理帧计数
    output reg         busy,
    output reg         done,           // 一帧处理完的 1 拍脉冲
    output reg         ovf,            // sticky：处理期间又来了帧末（丢帧）
    // ---- v1：形态学+CCL 候选（接 map_cc）----
    output wire [5:0]  n_cand,
    output wire        cand_ovf,
    output wire        label_ovf,
    input  wire [5:0]  cand_addr,
    output wire [56:0] cand_data,

    // ---- 调试口（DEBUG=1 时有效）：逐像素 H/S/V ----
    output reg         dbg_we,
    output reg [13:0]  dbg_addr,
    output reg [23:0]  dbg_data
);

    localparam integer NPIX = NW * 2;       // 11520
    localparam integer HALF = NPIX / 2;     // 5760

    // ---------------------------------------------------------------- 帧缓存
    // map_wr：抽头持续写入；map_eng：帧末快照，处理期间不再变。
    // 两块都按同步读推断 BRAM（5760 x 32 ≈ 6 BRAM36 each）。
    reg [31:0] map_wr  [0:NW-1];
    reg [31:0] map_eng [0:NW-1];

    always @(posedge clk) begin
        if (wr_en)
            map_wr[wr_addr[14:2]] <= wr_data;
    end

    wire eof_w = wr_en && (wr_addr[14:2] == (NW - 1));

    // ---------------------------------------------------------------- 倒数表
    // S = round(d*255/V)   = (d*T[V] + 2^25) >> 26, T[V]=ceil(2^26/V)
    // H = round(|delta|*30/d) 同式，T[d]=ceil(2^26/d)
    // K=26 已离线验证：对本设计全部取值（S: n<=65025，H: n<=7650）逐值精确。
    // ★ 表宽 27 位：T[1]=2^26 本身就需要 27 位（26 位最大只有 2^26-1）。
    // ★ 2026-10-22：S 和 H 的倒数表**完全相同**（都是 ceil(2^26/x)），合并成一张。
    //   大表强制 block RAM（全设计切片只差 143 个；LUTRAM 能省一点是一点）。
    (* ram_style = "block" *) reg [26:0] sdiv_tab [0:255];
    (* ram_style = "block" *) reg [7:0]  vhi_tab  [0:255];

    integer ii;
    initial begin
        sdiv_tab[0] = 27'd0;
        for (ii = 1; ii < 256; ii = ii + 1)
            sdiv_tab[ii] = ((32'd1 << 26) + ii - 1) / ii;
        for (ii = 0; ii < 256; ii = ii + 1) begin
            if (55 * ii < 7000)
                vhi_tab[ii] = 8'd69;
            else
                vhi_tab[ii] = ((55 * ii + 99) / 100) - 1;
        end
    end

    // ---------------------------------------------------------------- 直方图
    // ★ 2026-10-22 时序实锤：不加属性时 hist 被综合成 256 路 LUTRAM 大 mux +
    //   进位链，是 map_detect 的最差路径（-2.03ns）。强制 BRAM 后，读地址/
    //   写数据路径都短；中位数的组合读也改成同步读（多一拍，可忽略）。
    (* ram_style = "block" *) reg [13:0] hist [0:NBIN-1];

    // ---------------------------------------------------------------- FSM
    localparam [3:0] S_IDLE = 4'd0, S_CPR = 4'd1, S_CPW  = 4'd2,
                     S_P1R  = 4'd3, S_P1W0 = 4'd4, S_P1W1 = 4'd5,
                     S_P1X0 = 4'd11, S_P1X1 = 4'd12,
                     S_P1W2 = 4'd13, S_MD   = 4'd14,
                     S_MR   = 4'd6, S_MC  = 4'd9,
                     S_HCLR = 4'd10,
                     S_P2   = 4'd7, S_CCW = 4'd8;

    reg [3:0]  st;
    reg        cc_start;
    wire       cc_busy, cc_done;
    reg [12:0] cp_i, wi;          // 字索引（0..5759）
    reg [31:0] cp_rd, wrd;
    reg [7:0]  m_idx;
    reg [7:0]  hist_i;            // 直方图逐拍清零计数
    reg [14:0] cum;
    reg [13:0] h_rd;              // 直方图单读口数据
    reg [7:0]  h_ra;              // 单读口地址
    reg [7:0]  h_idx;             // 直方图写地址
    reg        h_we;              // 单一写口控制（组合选通）
    reg [7:0]  h_wa;
    reg [13:0] h_wd;

    // ---------------------------------------------------------------- P2 流水
    // 级 0：发地址  级 1：解码  级 2：乘法  级 3：取整/判色/计数
    reg [13:0] pix_i;             // 正在发地址的像素
    reg        p2_issuing;

    reg [31:0] word_r;
    reg [13:0] pix_r;
    reg        vld_r;
    // ★ 2026-10-22 时序：BRAM 输出后再寄存一拍，断开 map_eng→HSV 解码的长链
    reg [31:0] word_d;
    reg [13:0] pix_d;
    reg        vld_d;

    reg [7:0]  v_a, d_a, dabs_a, base_a;
    reg        sgn_a;
    reg [13:0] pix_a;
    reg        vld_a;
    reg [26:0] sdiv_b, hdiv_b;

    // S 的分子是 d*255（<=65025），H 的分子是 |delta|*30（<=7650）
    wire [15:0] n_s = {8'd0, d_a}    * 8'd255;
    wire [12:0] n_h = {5'd0, dabs_a} * 8'd30;
    reg [42:0] smul_c, hmul_c;
    reg [7:0]  v_c;
    reg [7:0]  base_c;
    reg        sgn_c;
    reg [13:0] pix_c;
    reg        vld_c;

    // 565 -> 888（与 PS 的 <<3/<<2 同口径）
    function [7:0] f_r; input [15:0] p; begin f_r = {p[15:11], 3'b000}; end endfunction
    function [7:0] f_g; input [15:0] p; begin f_g = {p[10:5],  2'b00};  end endfunction
    function [7:0] f_b; input [15:0] p; begin f_b = {p[4:0],   3'b000}; end endfunction
    function [7:0] f_max3; input [7:0] a; input [7:0] b; input [7:0] c;
        begin f_max3 = (a >= b) ? ((a >= c) ? a : c) : ((b >= c) ? b : c); end
    endfunction
    function [7:0] f_min3; input [7:0] a; input [7:0] b; input [7:0] c;
        begin f_min3 = (a <= b) ? ((a <= c) ? a : c) : ((b <= c) ? b : c); end
    endfunction

    // ---------------- 级 1：解码（word_r 有效）
    wire [15:0] wsel = pix_d[0] ? word_d[31:16] : word_d[15:0];
    wire [7:0]  dr = f_r(wsel);
    wire [7:0]  dg = f_g(wsel);
    wire [7:0]  db = f_b(wsel);
    wire [7:0]  dv = f_max3(dr, dg, db);
    wire [7:0]  dm = f_min3(dr, dg, db);
    wire [7:0]  dd = dv - dm;
    wire [1:0]  psec = (dv == dr) ? 2'd0 : ((dv == dg) ? 2'd1 : 2'd2);
    reg  signed [8:0] dlt;
    always @(*) begin
        case (psec)
            2'd0:    dlt = $signed({1'b0, dg}) - $signed({1'b0, db});
            2'd1:    dlt = $signed({1'b0, db}) - $signed({1'b0, dr});
            default: dlt = $signed({1'b0, dr}) - $signed({1'b0, dg});
        endcase
    end
    wire [7:0] dabs = dlt[8] ? (~dlt[7:0] + 8'd1) : dlt[7:0];
    wire [7:0] dbase = (psec == 2'd0) ? 8'd0 : ((psec == 2'd1) ? 8'd60 : 8'd120);

    // ---------------- 级 3：取整/掩码
    wire [7:0]  s_val = (smul_c + (43'd1 << 25)) >> 26;
    wire [7:0]  h_abs = (hmul_c + (43'd1 << 25)) >> 26;
    wire signed [9:0] h_add = sgn_c ? ($signed({2'b00, base_c}) - $signed({2'b00, h_abs}))
                                   : ($signed({2'b00, base_c}) + $signed({2'b00, h_abs}));
    wire [8:0]  h_wrap = h_add[9] ? (h_add + 10'sd180) : h_add;
    wire [7:0]  h_val = h_wrap[7:0];
    wire        m_red   = ((h_val <= 8'd12) || (h_val >= 8'd165)) &&
                          (s_val >= THR_S) && (v_c >= THR_V);
    wire        m_blue  = (h_val >= 8'd85) && (h_val <= 8'd135) &&
                          (s_val >= THR_S) && (v_c >= THR_V);
    wire        m_black = (v_c <= vhi) && (s_val <= THR_BS);

    // ---- v1：掩码流直接送 map_cc（起点=进入 P2 时的一拍）----
    map_cc u_cc (
        .clk(clk), .rstn(rstn),
        .start(cc_start),
        .in_valid(vld_c && (st == S_P2)),
        .in_mask({m_black, m_blue, m_red}),
        .busy(cc_busy), .done(cc_done),
        .n_cand(n_cand), .cand_ovf(cand_ovf), .label_ovf(label_ovf),
        .cand_addr(cand_addr), .cand_data(cand_data)
    );

    // ★★ 直方图 BRAM 推断的两个硬前提：
    //   ① 单读口（h_ra/h_rd，P1 与中位数共用）；
    //   ② 单写口（h_we/h_wa/h_wd 组合选通，整块只有一条写语句）。
    always @(*) begin
        h_we = 1'b0; h_wa = 8'd0; h_wd = 14'd0;
        case (st)
        S_HCLR:  begin h_we = 1'b1; h_wa = hist_i; h_wd = 14'd0; end
        S_P1X0,
        S_P1X1:  begin h_we = 1'b1; h_wa = h_idx;  h_wd = h_rd + 14'd1; end
        default: ;
        endcase
    end

    always @(posedge clk) begin
        if (!rstn) begin
            st <= S_IDLE; busy <= 1'b0; done <= 1'b0; ovf <= 1'b0;
            cc_start <= 1'b0;
            frame_seq <= 32'd0;
            med <= 8'd0; vhi <= 8'd0;
            cnt_red <= 16'd0; cnt_blue <= 16'd0; cnt_black <= 16'd0;
            cp_i <= 13'd0; wi <= 13'd0; cp_rd <= 32'd0; wrd <= 32'd0;
            m_idx <= 8'd0; cum <= 15'd0;   // h_rd 由共享读块每拍驱动，复位块不许再赋值
            h_ra <= 8'd0; h_idx <= 8'd0;
            pix_i <= 14'd0; p2_issuing <= 1'b0;
            word_r <= 32'd0; pix_r <= 14'd0; vld_r <= 1'b0;
            word_d <= 32'd0; pix_d <= 14'd0; vld_d <= 1'b0;
            v_a <= 8'd0; d_a <= 8'd0; dabs_a <= 8'd0; base_a <= 8'd0;
            sgn_a <= 1'b0; pix_a <= 14'd0; vld_a <= 1'b0;
            sdiv_b <= 27'd0; hdiv_b <= 27'd0;
            smul_c <= 43'd0; hmul_c <= 43'd0; v_c <= 8'd0; base_c <= 8'd0;
            sgn_c <= 1'b0; pix_c <= 14'd0; vld_c <= 1'b0;
            dbg_we <= 1'b0; dbg_addr <= 14'd0; dbg_data <= 24'd0;
            hist_i <= 8'd0;
        end else begin
            done <= 1'b0;
            dbg_we <= 1'b0;
            cc_start <= 1'b0;
            // ★ 直方图读与写在同一个 always 块（BRAM 推断前提）
            h_rd <= hist[h_ra];
            if (h_we) hist[h_wa] <= h_wd;      // 唯一写语句
            // 处理期间又来帧末：这一帧不处理，记 sticky（调用方可查）
            if (eof_w && (st != S_IDLE))
                ovf <= 1'b1;

            // ===================== P2 流水线（与主 FSM 并行，仅 S_P2 时推进）=====
            if (st == S_P2) begin
                // 级 0 -> 级 1
                word_r <= map_eng[pix_i[13:1]];
                pix_r  <= pix_i;
                vld_r  <= p2_issuing;
                word_d <= word_r;
                pix_d  <= pix_r;
                vld_d  <= vld_r;
                if (p2_issuing) begin
                    if (pix_i == NPIX - 1)
                        p2_issuing <= 1'b0;
                    else
                        pix_i <= pix_i + 14'd1;
                end

                // 级 1 -> 级 2（解码 + 同步 ROM 读）
                vld_a  <= vld_d;
                pix_a  <= pix_d;
                v_a    <= dv;
                d_a    <= dd;
                dabs_a <= dabs;
                base_a <= dbase;
                sgn_a  <= dlt[8];
                if (vld_d) begin
                    sdiv_b <= sdiv_tab[dv];
                    hdiv_b <= sdiv_tab[dd];   // 与 S 共用同一张表
                end

                // 级 2 -> 级 3（乘法）
                vld_c   <= vld_a;
                pix_c   <= pix_a;
                v_c     <= v_a;
                base_c  <= base_a;
                sgn_c   <= sgn_a;
                smul_c  <= n_s * sdiv_b;
                hmul_c  <= n_h * hdiv_b;
            end

            // 级 3 -> 计数（每拍一个像素；计数与分类互斥性无关，分别累加）
            if (vld_c && (st == S_P2)) begin
                if (m_red)   cnt_red   <= cnt_red   + 16'd1;
                if (m_blue)  cnt_blue  <= cnt_blue  + 16'd1;
                if (m_black) cnt_black <= cnt_black + 16'd1;
                if (DEBUG != 0) begin
                    dbg_we   <= 1'b1;
                    dbg_addr <= pix_c;
                    dbg_data <= {h_val, s_val, v_c};
                end
                if (pix_c == NPIX - 1) begin
                    busy <= 1'b1;        // 掩码流结束，但 CCL 还没算完
                    st   <= S_CCW;
                end
            end

            // ===================== 主 FSM =====================
            case (st)
            S_IDLE: begin
                busy <= 1'b0;
                if (eof_w) begin
                    busy <= 1'b1;
                    frame_seq <= frame_seq + 32'd1;
                    cp_i <= 13'd0;
                    hist_i <= 8'd0;
                    cnt_red <= 16'd0; cnt_blue <= 16'd0; cnt_black <= 16'd0;
                    st <= S_HCLR;
                end
            end
            // ---- 直方图逐拍清零（★ 一拍 for 循环清 256 项会让 BRAM 推断失败）----
            S_HCLR: begin
                if (hist_i == 8'd255) begin
                    cp_i <= 13'd0;
                    st <= S_CPR;
                end else begin
                    hist_i <= hist_i + 8'd1;
                end
            end
            // ---- 帧末复制 map_wr -> map_eng（同步读，2 拍/字）----
            S_CPR: begin
                cp_rd <= map_wr[cp_i];
                st <= S_CPW;
            end
            S_CPW: begin
                map_eng[cp_i] <= cp_rd;
                if (cp_i == NW - 1) begin
                    wi <= 13'd0;
                    st <= S_P1R;
                end else begin
                    cp_i <= cp_i + 13'd1;
                    st <= S_CPR;
                end
            end
            // ---- P1：V 直方图（每个 32 位字 = 2 个像素；低 16 偶列，高 16 奇列）----
            // 每像素 2 拍：先设读地址 h_ra，下一拍用 h_rd+1 写回（单读口）。
            S_P1R: begin
                wrd <= map_eng[wi];
                st <= S_P1W0;
            end
            S_P1W0: begin
                h_ra  <= f_max3(f_r(wrd[15:0]), f_g(wrd[15:0]), f_b(wrd[15:0]));
                h_idx <= f_max3(f_r(wrd[15:0]), f_g(wrd[15:0]), f_b(wrd[15:0]));
                st <= S_P1W1;
            end
            S_P1W1: begin
                st <= S_P1X0;               // 等单读口把 hist[vlow] 取回
            end
            S_P1X0: begin
                h_ra  <= f_max3(f_r(wrd[31:16]), f_g(wrd[31:16]), f_b(wrd[31:16]));
                h_idx <= f_max3(f_r(wrd[31:16]), f_g(wrd[31:16]), f_b(wrd[31:16]));
                st <= S_P1W2;
            end
            S_P1W2: begin
                st <= S_P1X1;               // 等单读口把 hist[vhigh] 取回
            end
            S_P1X1: begin
                if (wi == NW - 1) begin
                    m_idx <= 8'd0; cum <= 15'd0;
                    st <= S_MR;
                end else begin
                    wi <= wi + 13'd1;
                    st <= S_P1R;
                end
            end
            // ---- 中位：第一个 cum >= N/2 的 bin ----
            S_MR: begin
                h_ra <= m_idx;              // 单读口地址
                st <= S_MC;
            end
            S_MC: begin
                st <= S_MD;                 // 等单读口把 hist[m_idx] 取回
            end
            S_MD: begin
                if ({1'b0, cum} + {1'b0, h_rd} >= HALF) begin
                    med <= m_idx;
                    vhi <= vhi_tab[m_idx];
                    pix_i <= 14'd0;
                    p2_issuing <= 1'b1;
                    vld_r <= 1'b0; vld_a <= 1'b0; vld_c <= 1'b0;
                    cc_start <= 1'b1;
                    st <= S_P2;
                end else begin
                    cum <= cum + h_rd;
                    m_idx <= m_idx + 8'd1;
                    st <= S_MR;
                end
            end
            S_P2: begin
                // 流水线已在上面的并行块里推进；这里**不要写 st <= S_P2** ——
                // 非阻塞赋值按源码顺序最后胜出，会把上面收尾时的 st<=S_CCW
                // 覆盖掉（v0 就因此停在 S_P2 回不到 IDLE，第二帧永远不会处理）。
                busy <= busy;
            end
            S_CCW: begin
                // 等 map_cc（形态学+CCL）完成；done 在这一拍之后
                if (cc_done) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    st   <= S_IDLE;
                end
            end
            default: st <= S_IDLE;
            endcase
        end
    end

endmodule
