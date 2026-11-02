// ============================================================================
// vision_core.v — PL 视觉前处理 V1：8x8 块平均降采样（v2，2026-09-24）
//
// 设计文档：docs/vision_ip_design.md
//
// 干什么：串联在采集链中间（**对链路完全透明**），把 1024x720 的 RGB565 画面
//   按 8x8 块求平均，得到 **128x90 的缩略图**（23,040 字节 = 原数据的 1.6%），
//   写进 BRAM 供 PS 用 mmap 读回。分类识别（红/蓝/黑球、背景差分、连通域）全部
//   留在 PS —— 那边改阈值不用重新综合，且能推广到洞/障碍/传送门。
//
// 为什么这样切：实测 PS（Cortex-A9）全分辨率做一次检测要 423ms（背景差分）
//   / 743ms（霍夫圆），只有 1.3~2.4 fps，而游戏要 30fps；同一算法在 1/64 的
//   缩略图上只要几毫秒。所以 PL 的价值不是"算得快"，是**让 3MB/帧的数据不用搬**。
//
// ── 输入流的形态（实测，不是假设）─────────────────────────────────────────
//   OV5640 配置 DVPHO=0x0400=1024（ov5640_cfg.v:350），RGB565 每像素 2 字节，
//   所以**一行 2048 字节**；PS 侧 VDMA 的 stride 也是 width*2=2048
//   （vdma_capture.py:192），两边一致。720 行（VSIZE=720 —— 曾误用 722，
//   导致 fsize_err + 每帧撕裂 + 17.5fps，是真值扫描出来的）。
//   桥（dvp2axis_bridge）的 m_axis_tdata 是 8 位：tlast 骑在每行最后一个字节上、
//   tuser 骑在每帧第一个字节上（FIFO 里存的就是 {sof, eol, data}）。
//
// ── 字节序（swap_bytes）────────────────────────────────────────────────────
//   OV5640 的 0x4300 决定高/低字节谁先发，实测会上电翻转。字节序不同时 16 位值的
//   位域含义整体重排（不是简单 R<->B 互换），所以不能把原始 16 位累加后"事后解释"，
//   必须先在正确位置拆出 R/G/B 再分别累加。PS 用通道相关性判定后写 swap_bytes。
//
// ── 为什么每通道单独累加（不能直接累加 16 位值）──────────────────────────
//   16 位相加会**跨字段进位**：两个 B=31 的像素相加得 0x003E，右移 6 位后 B 变成 0
//   （真值 31）。所以 R/B 各 11 位（64*31=1984）、G 12 位（64*63=4032），最后各自
//   >>6 再拼回 RGB565。
//
// ── ★刷写时序：v1 的教训，v2 的全部理由★ ────────────────────────────────
//   v1 的做法是"8 行凑满 → 进入 flushing 状态 → 64 拍刷完整行"，并**假设**这 64 拍
//   一定落在行消隐里（注释写着"消隐 ≥2000 拍"）。上板实测：**这个假设是错的**
//   —— stat 的 flush_ovl sticky 位被点亮，说明刷写期间真的有字节到来。那些字节被
//   丢了，且丢的是"半拍"：像素字节配对**整体错位一位**，之后所有像素值都变成相邻
//   两像素字节的混合 → 缩略图与黄金模型相关性 0.05（等于无关）。
//
//   v2 把刷写**折叠进最后一行**，从机制上消灭这个窗口：
//     块行最后一行的像素按列顺序流过（一列 8 像素 = 16 字节），某列的最后那个像素
//     一走完，这一列的 64 个像素就**凑齐了** —— 当拍就能取平均写出去，根本不需要
//     "等一行结束再回头刷"。列按 0,1,2,…,127 顺序完成，所以偶数列传给 half_lo 暂存、
//     奇数列到来时与它拼成一个 32 位字写走（低 16 位=偶列、高 16 位=奇列）。
//     写入散落在整行里（每 16 字节 1 次），**不占用任何额外时间窗口**。
//   结果：不需要 flushing 状态、不需要乒乓缓冲、不丢任何字节、不依赖消隐宽度。
//
// ── ★重配置只在帧边界生效（resync）★ ───────────────────────────────────
//   上电 / soft_rst / 撤销冻结，都会把模块置进 resync 状态：**丢弃输入，直到下一个
//   帧首（tuser）才重新开工**。为什么不能"就地接着数"：复位落在行中间时，字节相位
//   会被锚在随便一个奇偶位上 —— 那一行的像素配对必然错位，还会顺带点亮错误位，
//   看起来像"设计有 bug"。等帧首开工，代价是最多等一帧（36ms），换来的是
//   **绝不产出半帧脏数据、也绝不误报错误位**（PS 侧的 soft_rst 可以随便打）。
//
// ── 自检（stat/stat2 两个 32 位字，健康值是一对常数）─────────────────────
//   stat  = {last_lines[15:0], last_wcnt[15:0]}
//           last_lines = 上一帧行数（tlast 数），应 720 = 0x02D0
//           last_wcnt  = 上一帧写进 map 的字数，应 5760 = 0x1680
//           → **健康值 stat == 0x02D01680**（一个字就证明整条链路对）
//   stat2 = {last_bcnt[10:0], errs[5:0], wcnt[14:0]}
//           last_bcnt = 块行收尾那一刻的字节计数，应 2047 = 0x7FF
//           wcnt      = 本帧实时已写字数（0..5760）
//           errs      = 错误位图（**sticky，只由 soft_rst 清**，一个毛刺都不放过）
//             [0] tlast 落在半个像素上（行字节数为奇数）
//             [1] 块行收尾时 bcnt != 2047（行长度不对）
//             [2] 本帧写字数超过 5760（地址/块行失步）
//             [3] 一帧 tlast 数超过 720（行数不对）
//             [4] 块行收尾时 col != 127（列数不够，行太短）
//             [5] 保留
//           → **健康值 stat2 == 0xFFE01680**
// ============================================================================

module vision_core #(
    parameter integer W      = 1024,        // 行像素数
    parameter integer BLK    = 8,           // 块边长（像素）
    parameter integer ROWS   = 720,         // 行数
    parameter integer CW     = W / BLK,     // 每行块数 = 128
    parameter integer BRWS   = ROWS / BLK,  // 块行数 = 90
    parameter integer AW     = 15           // ★ 地址位宽：这个 BRAM 是**字节地址**
                                            //   （C_ENABLE_32BIT_ADDRESS=1），缩略图占
                                            //   5760 字 × 4 字节 = 23040 字节 -> 15 位。
                                            //   原来是 13（按字地址算的），送字节地址会
                                            //   截掉高 2 位 —— 两个位宽必须匹配。
)(
    input  wire         aclk,
    input  wire         aresetn,            // 低有效

    // ---- AXI-Stream 旁路口（**串联直通，对链路完全透明**）----
    // ★ 为什么不是"只读观察"：在 BD 里用 connect_bd_net 单独去接某个接口的子引脚，
    //   会把该引脚**从原来的接口连接里抢断**（实测 BD 41-1271 + 41-759）——
    //   桥的 m_axis_tready 因此变成无源、被 tie 到 0，整条采集链直接死。
    //   所以做成串联直通：数据/握手原样透传，内部只是顺便看一眼。
    input  wire [7:0]   s_axis_tdata,
    input  wire         s_axis_tvalid,
    output wire         s_axis_tready,
    input  wire         s_axis_tuser,       // 帧首（与第一个字节同拍）
    input  wire         s_axis_tlast,       // 行尾（与最后一个字节同拍）

    output wire [7:0]   m_axis_tdata,
    output wire         m_axis_tvalid,
    input  wire         m_axis_tready,
    output wire         m_axis_tuser,
    output wire         m_axis_tlast,

    // ---- 控制（axi_gpio_vision 输出通道）----
    // ★ 极性约定（沿用本工程 ctrl=0 必须表示"正常运转"）：freeze=0 才是工作态
    input  wire         freeze,             // 1 = 冻结（map 不再更新）；0 = 正常累加
    input  wire         soft_rst,           // 1 = 清位置/累加器/错误位（frame_cnt 保留）
    input  wire         swap_bytes,         // 1 = 高低字节对调后再拆通道

    // ---- 状态 ----
    output reg  [31:0]  frame_cnt,          // 完成帧计数（PS 判"缩略图已刷新"）
    output wire [31:0]  stat,               // 见文件头
    output wire [31:0]  stat2,              // 见文件头

    // ---- map_ram 写口（blk_mem_gen 的 PORTB）----
    output reg          wr_en,
    output reg [AW-1:0] wr_addr,
    output reg [31:0]   wr_data
);

    // ---------------------------------------------------------------- 常量
    // ★ 必须显式位宽：直接写 "row_in_blk == BLK - 1" 是拿 4 位信号与 32 位整数
    // 常量比较，Vivado 2023.2 在实现阶段会报 [Opt 31-67] LUT 输入 I2 悬空而失败
    // （2026-09-23 实测踩到，综合能过、实现才炸）。同宽 localparam 干净利落。
    localparam [3:0]  ROW_LAST  = BLK - 1;              // 7
    localparam [10:0] BCNT_LAST = 2 * W - 1;            // 2047
    localparam [6:0]  COL_LAST  = CW - 1;               // 127
    localparam [6:0]  BRW_LAST  = BRWS - 1;             // 89
    localparam [15:0] ROWS_MAX  = ROWS;                 // 720
    localparam [14:0] WCNT_FULL = (CW * BRWS) / 2;      // 5760

    // ---------------------------------------------------------------- 直通透传
    // 这五行必须无条件成立：freeze/soft_rst 只影响"要不要累加"，绝不影响数据通路
    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tvalid = s_axis_tvalid;
    assign m_axis_tlast  = s_axis_tlast;
    assign m_axis_tuser  = s_axis_tuser;
    assign s_axis_tready = m_axis_tready;

    // ---------------------------------------------------------------- 像素拼装
    reg  [7:0]  b0;                         // 每 2 字节的第 1 个（进 bit[7:0]）
    reg         phase;                      // 0 = 等第 1 字节，1 = 等第 2 字节
    reg  [10:0] bcnt;                       // 行内字节计数 0..2047

    wire        beat   = s_axis_tvalid & s_axis_tready & ~freeze;  // 只统计真被取走的拍
    wire [15:0] raw16  = {s_axis_tdata, b0};             // 小端：先到的字节在低位
    wire [15:0] pix16  = swap_bytes ? {raw16[7:0], raw16[15:8]} : raw16;
    wire [4:0]  pixR   = pix16[15:11];
    wire [5:0]  pixG   = pix16[10:5];
    wire [4:0]  pixB   = pix16[4:0];
    wire [6:0]  col    = bcnt[10:4];                     // 0..127：第几个块

    // ---------------------------------------------------------------- 列累加器
    reg [10:0] sumR [0:CW-1];               // 64*31 = 1984 -> 11 位
    reg [11:0] sumG [0:CW-1];               // 64*63 = 4032 -> 12 位
    reg [10:0] sumB [0:CW-1];

    reg [3:0]  row_in_blk;                  // 0..7：本块行内第几行
    reg [6:0]  blk_row;                     // 0..89：第几个块行
    reg [15:0] line_cnt;                    // 本帧已收到的行数
    reg [15:0] last_lines;                  // 上一帧行数（快照）
    reg [14:0] wcnt;                        // 本帧已写进 map 的字数
    reg [15:0] last_wcnt;                   // 上一帧字数（快照）
    reg [10:0] last_bcnt;                   // 块行收尾那刻的 bcnt
    reg [5:0]  errs;                        // sticky 错误位图
    reg [15:0] half_lo;                     // 偶列暂存（等奇列来拼字）
    reg        frz_d1;                      // freeze 沿检测（撤销冻结时自动重锚）
    // ★ resync：复位（上电 / soft_rst / 撤销冻结）后**不立刻接着数**，而是丢弃输入
    //   直到下一个帧首（tuser）才重新开工。理由：复位落在行中间时，字节相位会被
    //   随便锚到某个奇偶位上 —— 那一行必然错位，还会顺带点亮 errs。等帧首再开工，
    //   代价是最多等一帧（36ms），换来的是"任何重配置都只在帧边界生效"，绝不产出
    //   半帧脏数据，也绝不误报错误位。
    reg        resync;

    integer i;

    // 本拍这个像素加进去之后的值（块行收尾要连本像素一起算，故组合算）
    wire [10:0] incR = sumR[col] + {6'd0, pixR};
    wire [11:0] incG = sumG[col] + {6'd0, pixG};
    wire [10:0] incB = sumB[col] + {6'd0, pixB};
    wire [15:0] pack_inc = {incR[10:6], incG[11:6], incB[10:6]};

    // 块行收尾：本拍就是块行最后一行的最后一个像素（列 127 完成，词已随 pack_inc 写出）
    // ★ 三个门都必须有：tuser 拍不能当像素算；resync 期间整个模块不干活
    wire blk_done = beat & phase & ~s_axis_tuser & ~resync & s_axis_tlast
                    & (row_in_blk == ROW_LAST);
    wire do_acc   = beat & phase & ~s_axis_tuser & ~resync & ~blk_done;

    assign stat  = {last_lines, last_wcnt};
    assign stat2 = {last_bcnt, errs, wcnt};

    always @(posedge aclk) begin
        if (!aresetn) begin
            b0 <= 8'd0; phase <= 1'b0; bcnt <= 11'd0;
            row_in_blk <= 4'd0; blk_row <= 7'd0;
            line_cnt <= 16'd0; last_lines <= 16'd0;
            wcnt <= 15'd0; last_wcnt <= 16'd0;
            last_bcnt <= 11'd0; errs <= 6'd0; half_lo <= 16'd0;
            frz_d1 <= 1'b0; frame_cnt <= 32'd0; resync <= 1'b1;
            wr_en <= 1'b0; wr_addr <= {AW{1'b0}}; wr_data <= 32'd0;
            for (i = 0; i < CW; i = i + 1) begin
                sumR[i] <= 11'd0; sumG[i] <= 12'd0; sumB[i] <= 11'd0;
            end
        end else begin
            wr_en  <= 1'b0;
            frz_d1 <= freeze;

            if (soft_rst) begin
                // 在线复位：位置 + 累加器 + 错误位全清（frame_cnt 保留，PS 靠它判刷新），
                // 并进入 resync —— 等下一个帧首才重新开工，不在行中间硬接
                b0 <= 8'd0; phase <= 1'b0; bcnt <= 11'd0;
                row_in_blk <= 4'd0; blk_row <= 7'd0;
                line_cnt <= 16'd0; wcnt <= 15'd0;
                errs <= 6'd0; half_lo <= 16'd0; resync <= 1'b1;
                for (i = 0; i < CW; i = i + 1) begin
                    sumR[i] <= 11'd0; sumG[i] <= 12'd0; sumB[i] <= 11'd0;
                end
            end else if (~freeze & frz_d1) begin
                // ★ freeze 从 1 变 0（撤销冻结）：流一直在走，旧相位/列号已经没意义。
                //   清干净 + 进 resync，等下一个帧首重新锚定（同类坑踩过一次）
                b0 <= 8'd0; phase <= 1'b0; bcnt <= 11'd0;
                row_in_blk <= 4'd0; blk_row <= 7'd0;
                line_cnt <= 16'd0; wcnt <= 15'd0; half_lo <= 16'd0; resync <= 1'b1;
                for (i = 0; i < CW; i = i + 1) begin
                    sumR[i] <= 11'd0; sumG[i] <= 12'd0; sumB[i] <= 11'd0;
                end
            end else if (beat && s_axis_tuser) begin
                // 帧首：位置 + 累加器全清；这个字节当像素的低字节（phase 置 1）。
                // errs 不在这里清 —— 它是 sticky 的，只由 soft_rst 清，一个毛刺都不放过。
                // resync 到这里结束：从帧首开工，字节相位由构造保证对齐
                b0 <= s_axis_tdata; phase <= 1'b1; bcnt <= 11'd1;
                row_in_blk <= 4'd0; blk_row <= 7'd0;
                line_cnt <= 16'd0; wcnt <= 15'd0; half_lo <= 16'd0;
                resync <= 1'b0;
                for (i = 0; i < CW; i = i + 1) begin
                    sumR[i] <= 11'd0; sumG[i] <= 12'd0; sumB[i] <= 11'd0;
                end
            end else if (beat && !resync) begin
                if (!phase) begin
                    b0 <= s_axis_tdata; phase <= 1'b1;
                    if (s_axis_tlast) begin
                        // 行尾落在半个像素上（行字节数为奇数）。真出现就记账，
                        // 并**立刻把字节位置锚回 0** —— 不让错位顺着后续行蔓延；
                        // 这一行少算半拍，列边界会差一点，但 errs[0] 已经把它点亮，
                        // 不会伪装成"看起来正常"的画面
                        errs[0] <= 1'b1;
                        bcnt   <= 11'd0;
                    end else begin
                        bcnt <= bcnt + 11'd1;
                    end
                end else begin
                    phase <= 1'b0;
                    bcnt  <= s_axis_tlast ? 11'd0 : (bcnt + 11'd1);

                    // ---- 块行最后一行：**列完成的那一刻**写词（散落在整行，不占额外窗口）----
                    // ★ 列完成 = 本列的最后一个像素（一列 8 像素 = 16 字节，末像素第二
                    //   字节的 bcnt[3:0] == 15）。v2 第一版漏了"末像素"这个门，写使能
                    //   退化成**每个像素都写一次**（8 倍）：一帧 8x5760 = 46080 次写入，
                    //   15 位的 wcnt 溢出 → 实测 last_wcnt = 0x3400 = 13312 = 46080-32768、
                    //   errs[2] 常亮、缩略图却被写对了（同地址被反复覆盖，最后一次正好是
                    //   列和）—— **"结果碰巧对"绝不能当验收判据**，是自检把它抓出来的。
                    if (row_in_blk == ROW_LAST && bcnt[3:0] == 4'hF) begin
                        if (col[0]) begin
                            // 奇列：本列 + 上一偶列拼成一个 32 位字
                            // 字地址 = blk_row*64 + col/2 = {blk_row, col[6:1]}（无需加法器）
                            //
                            // ★★ 但送出去的必须是**字节地址**，要乘 4！★★
                            //   板上生成的 IP 参数明写着：C_ENABLE_32BIT_ADDRESS=1、
                            //   C_USE_BYTE_WEB=1、C_ADDRA_WIDTH/C_ADDRB_WIDTH=32 ——
                            //   即"32 位字节地址"模式（给字节寻址总线直连用的），
                            //   送进去的地址会被当作**字节偏移**，IP 内部再右移 2 得字地址。
                            //
                            //   原来送的是字地址 0..5759，被当字节偏移 → 实际字地址
                            //   = wr_addr>>2 = 0..1439 ⇒ **只覆盖前 1440 个字（=5760/4，
                            //   整整 1/4）**，且连续 4 次写落在同一个字上互相覆盖 →
                            //   缩略图内容全乱、与黄金模型 0.1%。
                            //
                            //   怎么抓到的：填图案→**不冻结**→跑 1 秒→读回，量出
                            //   "被覆盖字址 0..1439、幸存 1440..5759 且连续段"（工具
                            //   software/board/bram_write_map2.py）⇒ 1440 = 5760/4
                            //   的 4 倍压缩，与 v1 病历里那句"写地址只覆盖前 1440 个字"
                            //   是**同一个 bug**（当时归到"字节丢了"那条线上、搁下了）。
                            //
                            //   PS 侧本来就没问题：axi_bram_ctrl 送的就是字节地址
                            //   （所以 maprw_test 整图 11520 字读写全对 ✓）。
                            wr_en   <= 1'b1;
                            wr_addr <= {blk_row, col[6:1], 2'b00};   // ×4 = 字节地址
                            wr_data <= {pack_inc, half_lo};
                            wcnt    <= wcnt + 15'd1;
                            if ((wcnt + 15'd1) > WCNT_FULL) errs[2] <= 1'b1;
                        end else begin
                            half_lo <= pack_inc;      // 偶列：暂存，等奇列
                        end
                    end

                    // ---- 行尾（**无条件处理**：tlast 是行的权威边界，与相位无关）----
                    if (s_axis_tlast) begin
                        line_cnt <= line_cnt + 16'd1;
                        if (line_cnt >= ROWS_MAX) errs[3] <= 1'b1;
                        if (row_in_blk == ROW_LAST) begin
                            // 块行收尾
                            last_bcnt <= bcnt;
                            if (bcnt != BCNT_LAST) errs[1] <= 1'b1;
                            if (col  != COL_LAST)  errs[4] <= 1'b1;
                            row_in_blk <= 4'd0;
                            if (blk_row == BRW_LAST) begin
                                blk_row    <= 7'd0;
                                // 本拍刚把 line_cnt/wcnt 加过 1，快照取 +1
                                last_lines <= line_cnt + 16'd1;
                                last_wcnt  <= wcnt + 16'd1;
                                frame_cnt  <= frame_cnt + 32'd1;
                                wcnt       <= 15'd0;
                            end else begin
                                blk_row <= blk_row + 7'd1;
                            end
                        end else begin
                            row_in_blk <= row_in_blk + 4'd1;
                        end
                    end
                end
            end

            // ---- 累加 / 块行收尾清累加器（互斥）----
            // 放在最后：do_acc 与 blk_done 永远不会在同一拍同时成立
            if (do_acc) begin
                sumR[col] <= incR;
                sumG[col] <= incG;
                sumB[col] <= incB;
            end else if (blk_done) begin
                for (i = 0; i < CW; i = i + 1) begin
                    sumR[i] <= 11'd0; sumG[i] <= 12'd0; sumB[i] <= 11'd0;
                end
            end
        end
    end

endmodule
