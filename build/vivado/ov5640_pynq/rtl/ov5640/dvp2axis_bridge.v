// ============================================================================
// dvp2axis_bridge.v — DVP → AXI4-Stream 桥（M1a 第十二版，2026-09-14）
//
// 背景（为什么抛弃 v_vid_in_axi4s）：
//   第十一轮 dvp_probe 决定性测量证明传感器每次运行都健康
//   （PCLK=83.3MHz 稳定、vsync=30.00/s、href=722 行/帧、SCCB 304 条全写完、
//   8 根数据线全部见过 1/0、MMCM locked），而 v_vid_in_axi4s 的输出侧每次
//   运行随机落在不同状态（tvalid 0.14M~40M/s、tuser 0.67~30/s、
//   tlast 71~903/s、卡死态 live tlast 恒 1 且 tuser 掉到 2/s —— 冷上电也能
//   复现），健康时也均匀丢 1/20（1944/2048 字节、686/722 行）。IP 内部机制
//   无法观测、无法根治，决定整体替换。
//
// 本模块用 ~150 行 RTL 干 IP 干的全部事：href 门控把 8bit DVP 字节流打包
// 进异步 FIFO，aclk 侧按 AXI4-Stream 吐出；tuser = 每帧第一拍、tlast =
// 每行最后一拍。行为完全透明：不丢拍、不丢行，**单 SOF 由构造保证**
// （vsync 上升沿武装 sof，帧首第一个字用掉）—— IP 的 double-SOF /
// fsize_err / 横向条纹三件套从机制上消失。
//
// 设计要点：
//   ① xpm_fifo_async 8192 深 × 10bit（{sof, eol, data[7:0]}），
//      READ_MODE="fwft"：dout 恒呈头部字、empty=0 即有效 —— 输出侧
//      零延迟、tvalid/tdata/tlast/tuser 与 empty/dout 组合对齐，
//      不用数内部读延迟（这也是为什么选 fwft 而不是 std）。
//   ② 行末 eol：push 用 href 延迟一拍（push = href_d1），
//      eol = href_d1 & ~href —— 行的最后一个字节与 eol 同拍写入，
//      行长度与 href 高电平拍数**严格相等**。
//   ③ 帧首 sof：vsync 上升沿武装；武装后的第一拍 push 用掉 sof 并撤武装。
//      武装期间若整行被丢弃（FIFO 快满），sof 保留到下一行 ——
//      帧只会少一行，永远不丢帧首。
//   ④ 反压不存在：传感器自由运行，FIFO 满时只能**整行丢弃**（行起点查
//      wr_data_count > 4096 就丢这一整行，行内绝不半途停推）。
//      整行丢弃 = 画面少一行、由 eol/sof 自愈；行内丢字节 = 余下所有行
//      串位、坏一帧 —— 宁丢整行不丢字节。阈值取一半深度，覆盖
//      wr_data_count 的 CDC 滞后。
//   ⑤ 溢出/下溢脉冲锁存成 sticky 送 axis_probe（那边 2-FF 同步后进
//      w3 bit10/11），上板后一眼能看出 FIFO 出没出过事。
//   ⑥ 复位：写侧用 vid_rst_ctrl 的 vid_rst（MMCM 已锁 **且** PCLK 跑满
//      2^20 拍才撤）—— 这正是 xpm_fifo_async 要求的"复位保持到两侧时钟
//      都稳定"，第七轮的教训直接变成这里的正确做法。
//   ⑦ 桥替换只动 PL 内部逻辑：摄像头/电位器/HDMI 接线、VDMA、探针地址
//      全部不动（2026-09-14 已向用户确认接线影响表）。
//
// 第十四版（2026-09-15）：写侧账本（事件计数 + 快照 + 选择读口）。
//   背景：第十三版上板"dvp_probe 健康、但桥输出 0.51M/s 水滴流、tuser/tlast
//   全 0"——本桥 RTL 由构造保证 eol 必写（骑在每行最后一个推入拍上），
//   电平观测（w3[31:28]）却抓不到脉冲型事件，矛盾只能进写侧内部对账：
//     c_href  href 高电平拍数（脉冲宽度真相）
//     c_push  实际推入数（wr_en 拍数）
//     c_eolw  eol 写入数（推入且 eol=1）
//     c_vsync vsync 上升沿数（帧数）
//     c_ovf   写侧溢出脉冲数
//   读侧另计 c_rd（弹出数）/ c_eolr（弹出且 dout[8]=1），与 probe w0/w2 交叉
//   验证（probe 的 tlast 计数路径从没在健康传感器下验证过）。
//   dbg_trig 上升沿把全部 7 个计数器锁进影子寄存器（写侧经 2 级同步进 pclk
//   锁存、读侧 aclk 原生锁存，两域锁存点相差 ~2 拍 pclk，对账无碍），PS 经
//   dbg_sel 慢速轮读 7 个 32 位影子 —— 影子静止不变，读多慢都精确。
//   计数器随 rst_pclk（vid_rst）清零：ctrl[3] 脉冲 = 账本从零起记。
// ============================================================================

module dvp2axis_bridge (
    // ---- 传感器域（pclk，实测 83.3MHz）----
    input  wire        pclk,
    input  wire        rst_pclk,       // 高有效；来自 vid_rst_ctrl（撤复位条件见⑥）
    input  wire        href,           // 行有效（高电平期间为有效字节）
    input  wire        vsync,          // 场同步（上升沿 = 帧首）
    input  wire [7:0]  data,           // DVP 8bit 字节流

    // ---- AXI 域（aclk = 100MHz）----
    input  wire        aclk,
    input  wire        aresetn,        // 低有效（外设标准复位）

    // ---- AXI4-Stream 输出（→ axis_probe → VDMA S2MM）----
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire [7:0]  m_axis_tdata,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,

    // ---- 诊断 sticky（慢速电平，进 axis_probe w3 bit10/11）----
    output wire        ovf_sticky,     // 写侧溢出发生过（pclk 域）
    output wire        unf_sticky,     // 读侧下溢发生过（aclk 域）

    // ---- 写侧观测（第十三版：慢速电平直连，axis_probe 2-FF 同步进 w3[31:28]）----
    // 上板发现传感器状态逐次下载随机（1/5 健康），坏状态 tuser/tlast 全 0。
    // 这 4 根让坏状态也能被解剖：sof 武装了没 / 在不在丢行 / FIFO 复位忙不忙 /
    // 视频域复位撤了没 —— 坏在哪一侧一眼定位，不再隔着 FIFO 猜。
    output wire        sof_armed_obs,   // vsync 已武装帧首、还没被用掉
    output wire        dropping_obs,    // 整行丢弃中（写侧水位过半）
    output wire        wr_rst_busy_obs, // FIFO 写侧复位忙
    output wire        rst_obs,         // rst_pclk 电平（视频域还压着复位？）

    // ---- 第十四版 账本观测：事件计数 + 快照 + 选择读口（见文件头/文件尾）----
    input  wire        dbg_trig,        // aclk 域：快照触发（上升沿锁存全部计数器）
    input  wire [2:0]  dbg_sel,         // aclk 域：0..6 选影子寄存器
    output wire [31:0] dbg_data         // aclk 域：被选中的影子值
);

    // ------------------------------------------------------------------
    // 写侧（pclk 域）
    // push 用 href 延迟一拍：时间 t 推的是 t-1 拍采的字节；
    // eol = href(t-1)=1 且 href(t)=0 —— 行的最后一个字节与 eol 同拍入队。
    // ------------------------------------------------------------------
    reg  vsync_d;
    wire vsync_rise = vsync & ~vsync_d;

    reg  href_d;
    reg  [7:0] data_d;
    wire line_start = href & ~href_d;   // href 上升沿（行起点，丢行决策点）
    wire eol        = href_d & ~href;   // 行尾标志，骑在最后字节上

    reg  sof_pending;                   // vsync 上升沿武装："欠着一次帧首"
    reg  sof_armed;                     // 只在行首转正；该行第一个字把它用掉
    reg  dropping;                      // 本行整行丢弃中

    wire push = href_d & ~dropping & ~wr_rst_busy;

    always @(posedge pclk) begin
        vsync_d <= vsync;
        href_d  <= href;
        data_d  <= data;
        if (rst_pclk) begin
            sof_pending <= 1'b0;
            sof_armed   <= 1'b0;
            dropping  <= 1'b0;
        end else begin
            // ★★ 帧首只在**行首**转正（第十五版定稿）★★
            //
            // 老写法是"vsync 上升沿武装、下一个 push 用掉"。上板实测证明那是错的：
            // push 会被 dropping / wr_rst_busy 挡住，一旦挡住，这个"下一个 push"
            // 就落在**某一行中间** —— 帧首落到行中 = 整帧像素网格偏移若干个字节
            // （奇偶随机，每帧还不一样）。
            //
            // 怎么测出来的：把抓回的整帧按"行内字节偏移 0..3"各配一次像素，算平均
            // 水平梯度（真画面相邻像素相关 → 梯度小；配错字节 → 噪点 → 梯度大）：
            //     偏移 0：7332（噪点）   偏移 1：648（真画面）
            // → 网格整体偏了**整整一个字节 = 半个像素**，正是 RTL 里 errs bit0
            //   "tlast 落在半个像素上"这个名字的由来。同一时刻的 map 与 raw 帧因为
            //   偏移量不同而互相对不上（黄金模型对比 0.2% 的直接原因）。
            //
            // 改法：vsync 上升沿只**记账**（sof_pending=1），到行首（line_start）才
            // 转正成 sof_armed；该行若被丢行则继续欠着（pending 不清），直到某一行
            // 真的被推出去 —— 于是帧首**必然**骑在某一行的第一个字节上。
            // 顺带把"复位/丢行导致相位被锚在随便一个奇偶位"这条老病根一起除了。
            if (vsync_rise)
                sof_pending <= 1'b1;                        // 武装（只记账）
            if (line_start) begin
                if (sof_pending) begin
                    sof_armed   <= ~dropping;               // 行首转正
                    sof_pending <= dropping;                // 该行被丢则继续欠着
                end
            end else if (push) begin
                sof_armed <= 1'b0;                          // 帧首第一个字用掉 SOF
            end
            if (line_start)
                // ★★ 行起点水位判据：普通行 4096、SOF 行 6144（第十五版定稿）★★
                //
                // 两条要求必须**同时**满足，而它们是互相拉扯的 —— 这就是为什么
                // 前面两版各错一半（都上板实测过）：
                //
                //   要求① **永不溢出**：溢出会丢**字**（FIFO 是 10 位宽，一次丢
                //     {sof,eol,data} 一个整体），行内丢字 → 该行字节数变奇数 →
                //     之后所有像素**字节配对整体错一位** → 列完成条件 bcnt[3:0]==15
                //     再也对不上 → 写入停住 → map 冻在断点之前。
                //     实测症状：errs 亮 bit0、map 只有前 1/4 有内容、与黄金模型 0.1%。
                //     （第一版强行放行 SOF 行 = 违反了这条 → 就是上面那组症状。）
                //   要求② **帧首必须送得到**：VDMA 是 tuser 同步（FSYNCSEL=2，
                //     没 tuser 就不启动帧、**不启动帧就不取数**）。而帧间空隙里桥
                //     还在推（44M 字/秒），水位必然爬到 4096 以上；若 SOF 行也按
                //     4096 判据丢，就再也没有帧首 → VDMA 永不取数 → 水位永不下降
                //     → 死锁。实测：每场只剩 90/720 行、tuser 3.75/s、12 秒 45 帧
                //     = 45 个 tuser（一 SOF 一帧，帧率被 SOF 率锁死）。
                //     （第二版把阈值整体抬到 6144 = 普通行也让位 → 水位更容易越过
                //      6144 → 连 SOF 行一起丢 → 又锁死，实测每场只剩 2.1 行。）
                //
                // 同时满足的唯一取法：**两种行各留一整行（2048）的余量**，
                // 于是"放行就绝不溢出"成为恒等式：
                //   ≤4096 起推，整行推完最多到 6144 ≤ 8192 ✓
                //   ≤6144 起推，整行推完最多到 8192 = 8192 ✓（刚好满，不溢出）
                // 于是 SOF 行在水位 4096~6144 这段"帧间空隙必然进到"的区间里
                // **既送得到、又不丢字节** —— 死锁的那个窗口被这两条一起堵掉。
                //
                // 为什么不再需要"无条件强行放行"：SOF 行现在有一档独立放宽的阈值，
                // 它本身就是"能送到就不丢"；而无条件放行会在水位接近满时硬推整行，
                // 那正是要求①要禁的事。
                // 残留的极端情形（水位 >6144 时恰逢 SOF 行）由 PS 侧兜底：
                // 驱动起停顺序里本来就有 ctrl[3] 脉冲（见 vdma_capture 的 _init_video）。
                dropping <= (wr_data_count > (sof_armed ? 14'd6144 : 14'd4096));
            else if (!href)
                dropping <= 1'b0;       // 行结束即解除
        end
    end

    // ★ 必须显式声明！否则 Verilog 隐式网络规则会把 din 当成 1 位线，
    //   10 位的 {sof,eol,data} 只有最低位进得了 FIFO —— 标志位全丢、数据只剩 D0。
    //   （这个 bug 让第 12~14 轮"传感器健康但桥没帧头行尾"的悖论白查了三天）
    wire        wr_en;
    wire [9:0]  din;

    assign wr_en = push;
    assign din   = {sof_armed, eol, data_d};    // 10bit：{sof, eol, data}

    // ------------------------------------------------------------------
    // ★★ FIFO 水位看门狗（第十六版）：治"丢唤醒死锁" ★★
    //
    // 死锁链（2026-09-25 凌晨实测复现、一次解释全部现象）：
    //   消费者缺席（驱动进程一退出就 stop()，VDMA 停）→ FIFO 灌到接近满
    //   → 水位 >6144 → dropping 挡住所有行（SOF 行也挡，因为放不下）
    //   → 没有 push → **sof_armed 永远举着**（它只由 push 清）→ 帧首送不出去
    //   → VDMA 是 tuser 同步：**没 tuser 不启动帧、不启动帧不取数** → 水位永不下降 ⇒ 自锁
    //   → AXI 域 tvalid/tuser/tlast 全 0；vision_core 收不到数据卡在 resync；
    //     frame_cnt 冻结、map 变陈旧；而 stat/errs 还留着上次成功运行的快照
    //     → A/B/D 项读到旧值"全过"、C 项拿陈旧 map 比新 raw → 假通过 + 0.1%
    //
    // 为什么"调阈值"治不了：读侧要靠帧首才取数，而帧首必须穿过一条被读侧堵死的
    //   通道 —— 这是**丢唤醒（lost wakeup）**，调水位只能推迟它（分裂阈值版实测
    //   只是把死锁从 2 帧推迟到 2~3 帧：SOF 行每轮把水位顶高 2048，很快就到 8192）。
    //
    // 解法（标准做法）：**水位持续过高就自动复位 FIFO 一次**。复位后水位归零，
    //   帧首立刻送得出去，VDMA 拿到帧首即开始取数 —— 全自愈，不需要 PS 参与。
    //   代价：被中断的那一帧作废；且因为帧首现在**只在行首转正**（见上），
    //   复位后的下一个帧首仍然骑在行首 → 相位不会被锚坏，只丢一帧内容。
    //
    // 阈值取 5ms（83.3MHz 下 416000 拍）：健康时水位 ≤6144，一有超限说明读侧真停了；
    //   取长一点也不会误伤"PS 抢 DDR 带宽"那种短暂反压（那种情况水位会自己回落，
    //   计数器跟着清零）。
    // 复位脉冲宽度取 8 拍 pclk（≈96ns > 1 个 aclk 周期）：XPM 的异步复位同步器在
    //   两个时钟域里都必须稳稳看见，脉冲太短会出现"一个域看见、另一个没看见"。
    localparam [19:0] WDT_MAX = 20'd416000;
    // ★ 这里的声明必须放在用它的地方**之前**：本文件第 12~14 轮踩过"隐式网络规则
    //   把 10 位 din 当成 1 位线"的坑（见下方 din 的注释）。FIFO 那组的声明在文件
    //   后面，所以水位这条线在这儿先显式声明一次，后面那组里就不重复声明了。
    wire [13:0] wr_data_count;
    reg  [19:0] wdt_cnt;
    reg  [3:0]  wdt_pulse;
    reg         wdt_lat;                    // 粘滞：触发过就置 1（7 号影子可见）
    wire        wdt_hot = (wr_data_count > 14'd6144);

    always @(posedge pclk) begin
        if (rst_pclk) begin
            wdt_cnt   <= 20'd0;
            wdt_pulse <= 4'd0;
            wdt_lat   <= 1'b0;
        end else if (wdt_pulse != 4'd0) begin
            wdt_pulse <= wdt_pulse - 4'd1;  // 复位脉冲进行中
            wdt_cnt   <= 20'd0;
        end else if (wdt_hot) begin
            if (wdt_cnt == WDT_MAX) begin
                wdt_pulse <= 4'd8;          // ★ 触发：按住 8 拍
                wdt_lat   <= 1'b1;
            end else begin
                wdt_cnt <= wdt_cnt + 20'd1;
            end
        end else begin
            wdt_cnt <= 20'd0;               // 水位正常 → 计数清零，绝不误伤
        end
    end

    // FIFO 的复位 = 上电/软件复位 或 看门狗脉冲（只复位 FIFO，不动视频域 ——
    // 视频域整块复位要重新鉴定 43.7ms，代价大且没必要）
    wire fifo_rst = rst_pclk | (wdt_pulse != 4'd0);

    // ------------------------------------------------------------------
    // 异步 FIFO（83.3MHz → 100MHz）
    // USE_ADV_FEATURES="0005"：bit0=overflow、bit2=wr_data_count
    //   （位表权威来源：xpm_fifo.sv 206-216 行 EN_OF/EN_WDC）
    // 读侧 fwft：dout 恒为头部字、empty=0 即有效，见文件头①。
    // ------------------------------------------------------------------
    wire [9:0]  dout;
    wire        empty;
    wire        overflow;
    wire        underflow;
    wire        wr_rst_busy;
    wire        full_w;                          // 第十五版：接到 7 号影子（原来悬空）
    // （wr_data_count 已在看门狗那一段显式声明过，这里不重复 —— 同一根线不能声明两次）

    xpm_fifo_async #(
        .CDC_SYNC_STAGES(4),
        .DOUT_RESET_VALUE("0"),
        .ECC_MODE("no_ecc"),
        .FIFO_MEMORY_TYPE("auto"),
        .FIFO_READ_LATENCY(0),
        .FIFO_WRITE_DEPTH(8192),
        .FULL_RESET_VALUE(0),
        .PROG_EMPTY_THRESH(10),
        .PROG_FULL_THRESH(10),
        .RD_DATA_COUNT_WIDTH(1),
        .READ_DATA_WIDTH(10),
        .READ_MODE("fwft"),
        .RELATED_CLOCKS(0),
        .SIM_ASSERT_CHK(0),
        .USE_ADV_FEATURES("0005"),
        .WAKEUP_TIME(0),
        .WRITE_DATA_WIDTH(10),
        .WR_DATA_COUNT_WIDTH(14)
    ) u_fifo (
        .sleep(1'b0),
        .rst(fifo_rst),                     // 第十六版：= rst_pclk | 看门狗脉冲（见上）
        .wr_clk(pclk),
        .wr_en(wr_en),
        .din(din),
        .full(full_w),
        .prog_full(),
        .wr_data_count(wr_data_count),
        .overflow(overflow),
        .wr_rst_busy(wr_rst_busy),
        .almost_full(),
        .wr_ack(),
        .rd_clk(aclk),
        .rd_en(rd_en),
        .dout(dout),
        .empty(empty),
        .prog_empty(),
        .rd_data_count(),
        .underflow(underflow),
        .rd_rst_busy(rd_rst_busy),
        .almost_empty(),
        .data_valid(),
        .injectsbiterr(1'b0),
        .injectdbiterr(1'b0),
        .sbiterr(),
        .dbiterr()
    );

    // ------------------------------------------------------------------
    // 读侧（aclk 域）：fwft 直通，全组合对齐，零延迟。
    // rd_en 只被 tready/empty 门控 —— 结构上不可能下溢。
    // rd_rst_busy 一并门控：复位收尾期间不推数。
    // ------------------------------------------------------------------
    wire         rd_en;                        // 同样显式声明（1 位，但别留给隐式规则）
    assign rd_en         = ~empty & ~rd_rst_busy & m_axis_tready;
    assign m_axis_tvalid = ~empty & ~rd_rst_busy;
    assign m_axis_tdata  = dout[7:0];
    assign m_axis_tlast  = dout[8];
    assign m_axis_tuser  = dout[9];

    // ------------------------------------------------------------------
    // 诊断 sticky：溢出/下溢脉冲锁存（只置不除，重配置清）。
    // 慢速电平，2-FF 同步在 axis_probe 里做。
    // ------------------------------------------------------------------
    reg ovf_lat;
    reg unf_lat;
    always @(posedge pclk) begin
        if (rst_pclk)      ovf_lat <= 1'b0;
        else if (overflow) ovf_lat <= 1'b1;
    end
    always @(posedge aclk) begin
        if (!aresetn)       unf_lat <= 1'b0;
        else if (underflow) unf_lat <= 1'b1;
    end
    assign ovf_sticky = ovf_lat;
    assign unf_sticky = unf_lat;

    // 写侧观测直连（axis_probe 里同步，这里不再打拍）
    assign sof_armed_obs   = sof_armed;
    assign dropping_obs    = dropping;
    assign wr_rst_busy_obs = wr_rst_busy;
    assign rst_obs         = rst_pclk;

    // ------------------------------------------------------------------
    // 第十四版 账本：写侧事件计数（pclk 域，随 rst_pclk 清零）
    // 全部是**脉冲型事件**——第十三版电平观测抓不到的东西，这里逐拍记账。
    // 健康账本（每场）：href 高 ≈722 行×~1024 拍、push 与之相等（减丢行）、
    // eolw=722、vsync=1、ovf=0。谁对不上，病灶就在谁那一段。
    // ------------------------------------------------------------------
    reg [31:0] c_href, c_push, c_eolw, c_vsync, c_ovf;
    always @(posedge pclk) begin
        if (rst_pclk) begin
            c_href  <= 32'd0;
            c_push  <= 32'd0;
            c_eolw  <= 32'd0;
            c_vsync <= 32'd0;
            c_ovf   <= 32'd0;
        end else begin
            if (href)                c_href  <= c_href  + 32'd1;   // 0 号影子
            if (push)                c_push  <= c_push  + 32'd1;   // 1 号影子
            if (push & eol)          c_eolw  <= c_eolw  + 32'd1;   // 2 号影子
            if (vsync_rise)          c_vsync <= c_vsync + 32'd1;   // 3 号影子
            if (overflow)            c_ovf   <= c_ovf   + 32'd1;   // 4 号影子
        end
    end

    // ------------------------------------------------------------------
    // 写侧快照：dbg_trig（aclk 域）2 级同步进 pclk，上升沿把 5 个计数器
    // 锁进影子。影子只在快照瞬间变化 → 之后 2-FF 同步到 aclk 无风险。
    // ------------------------------------------------------------------
    reg        trig_s1, trig_s2;
    reg [31:0] sh_href, sh_push, sh_eolw, sh_vsync, sh_ovf;
    always @(posedge pclk) begin
        if (rst_pclk) begin
            trig_s1 <= 1'b0; trig_s2 <= 1'b0;
            sh_href <= 32'd0; sh_push <= 32'd0; sh_eolw <= 32'd0;
            sh_vsync <= 32'd0; sh_ovf <= 32'd0;
        end else begin
            trig_s1 <= dbg_trig;
            trig_s2 <= trig_s1;
            if (trig_s1 & ~trig_s2) begin
                sh_href  <= c_href;
                sh_push  <= c_push;
                sh_eolw  <= c_eolw;
                sh_vsync <= c_vsync;
                sh_ovf   <= c_ovf;
            end
        end
    end

    // 写侧影子 → aclk 域（2 级同步，快照间隔 ≫ CDC 延迟，无需格雷码）
    reg [31:0] sh_href_a1,  sh_href_a2;
    reg [31:0] sh_push_a1,  sh_push_a2;
    reg [31:0] sh_eolw_a1,  sh_eolw_a2;
    reg [31:0] sh_vsync_a1, sh_vsync_a2;
    reg [31:0] sh_ovf_a1,   sh_ovf_a2;
    always @(posedge aclk) begin
        {sh_href_a1,  sh_href_a2}  <= {sh_href,  sh_href_a1};
        {sh_push_a1,  sh_push_a2}  <= {sh_push,  sh_push_a1};
        {sh_eolw_a1,  sh_eolw_a2}  <= {sh_eolw,  sh_eolw_a1};
        {sh_vsync_a1, sh_vsync_a2} <= {sh_vsync, sh_vsync_a1};
        {sh_ovf_a1,   sh_ovf_a2}   <= {sh_ovf,   sh_ovf_a1};
    end

    // ------------------------------------------------------------------
    // 读侧账本 + 快照（aclk 域原生）：弹出数 / 弹出且 eol=1 数。
    // 与 probe 的 w0(tvalid 拍)/w2(tlast 沿) 独立对账 —— probe 的 tlast
    // 计数路径从没在健康传感器下验证过，这两路是它的裁判。
    // ------------------------------------------------------------------
    reg [31:0] c_rd, c_eolr;
    reg        trig_d;
    reg [31:0] sh_rd, sh_eolr;
    always @(posedge aclk) begin
        if (!aresetn) begin
            c_rd   <= 32'd0;
            c_eolr <= 32'd0;
            trig_d <= 1'b0;
            sh_rd  <= 32'd0;
            sh_eolr<= 32'd0;
        end else begin
            if (rd_en)          c_rd   <= c_rd   + 32'd1;           // 5 号影子
            if (rd_en & dout[8]) c_eolr <= c_eolr + 32'd1;          // 6 号影子
            trig_d <= dbg_trig;
            if (dbg_trig & ~trig_d) begin
                sh_rd   <= c_rd;
                sh_eolr <= c_eolr;
            end
        end
    end

    // ------------------------------------------------------------------
    // 选择读口：7 个影子寄存器按 dbg_sel 选一，PS 经 axi_gpio_probe6 读。
    // 数组开到 8：sel=7 越界读会出 X，7 号槽恒 0 兜底。
    // ------------------------------------------------------------------
    // 7 号影子（第十五版）：**FIFO 内部状态打包，实时电平、不走快照**。
    // 为什么需要它：0~6 号都是"事件计数"，看不出**水位**。而丢行机制的判据正是
    // 水位（wr_data_count > 4096），2026-09-24 的死锁就是水位解释的。有了这个字，
    // "水位到底多少"从推理变成读数：
    //   [13:0]  wr_data_count（写侧占用；>4096 = 丢行风暴，接近 8192 = 快满了）
    //   [14]    full（写侧判满）
    //   [15]    wr_rst_busy
    //   [16]    empty（读侧判空）  ★ 和 [14] 同时为 1 = 读写两域分裂（死锁特征）
    //   [17]    rd_rst_busy
    //   [18]    sof_armed（=1 且长时间不落 = 本场还没送出帧首 → VDMA 不启动）
    //   [19]    dropping
    // pclk 域的多位信号（wr_data_count）只做了 2FF —— 慢速诊断读它，读数偶尔追不上
    // 没关系，这里要看的是量级和"空/满"这种 1 位电平，不追求逐拍精确。
    reg [13:0] wdc_a1, wdc_a2;
    reg        ful_a1, ful_a2, wrb_a1, wrb_a2, sua_a1, sua_a2, drp_a1, drp_a2;
    always @(posedge aclk) begin
        wdc_a1 <= wr_data_count;  wdc_a2 <= wdc_a1;
        ful_a1 <= full_w;         ful_a2 <= ful_a1;
        wrb_a1 <= wr_rst_busy;    wrb_a2 <= wrb_a1;
        sua_a1 <= sof_armed;      sua_a2 <= sua_a1;
        drp_a1 <= dropping;       drp_a2 <= drp_a1;
    end

    reg [31:0] dbg_sh [0:7];
    always @(posedge aclk) begin
        dbg_sh[0] <= sh_href_a2;
        dbg_sh[1] <= sh_push_a2;
        dbg_sh[2] <= sh_eolw_a2;
        dbg_sh[3] <= sh_vsync_a2;
        dbg_sh[4] <= sh_ovf_a2;
        dbg_sh[5] <= sh_rd;
        dbg_sh[6] <= sh_eolr;
        dbg_sh[7] <= {wdt_lat, 11'd0, drp_a2, sua_a2, rd_rst_busy, empty,
                      wrb_a2, ful_a2, wdc_a2};    // [31] = 看门狗触发过（粘滞）
    end
    assign dbg_data = dbg_sh[dbg_sel];

endmodule
