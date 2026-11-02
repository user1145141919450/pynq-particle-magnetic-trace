// ============================================================================
// vid_rst_ctrl.v — 视频域复位时序修正（M1a 排障，第七版）
//
// ★★ 修的到底是什么 ★★
//
// 2026-09-13 上板量到：传感器完全健康（vsync 29.8Hz 稳定、href/pclk 都在跳），
// 但 v_vid_in_axi4s 的输出侧**一拍数据都不推**：
//     tvalid 2s 内 +0     tuser +0     tlast +0
//     w3=0x0b6d02ad  live tlast=1（卡死在高）、seen_tuser=0、vsync_cnt=2925
//
// 回到 IP 源码（v_vid_in_axi4s_v5_0_vl_rfs.v）找到这条链：
//     wire vid_reset = (C_HAS_ASYNC_CLK) ? vid_io_in_reset : ~aresetn;   // 1975
//     .VID_RESET (vid_reset | fifo_overflow_from_coupler)                // 2181
//     assign vid_vsync_re    = clk_video_vs[0] & ~vid_vsync_dly;         // 2129
//     always @(posedge vid_clk) begin                                    // 2140
//       if (vid_reset)            vid_reset_pulse_cnt <= 16'd0;
//       else if (fifo_overflow_from_coupler || vid_vsync_re)
//                                 vid_reset_pulse_cnt <= {16{1'b1}};
//       else if (vid_reset_pulse_cnt != 0)
//                                 vid_reset_pulse_cnt <= {1'b0, ...};
//     end
// 即：**内部异步 FIFO 一溢出，就会反过来把整个视频域按住复位**，而复位只能由
// `vid_io_in_reset` 再次脉冲才能解开。
//
// 再看我们原来的接法（bd/m1a_capture.tcl 第 311~315 行）：
//     vid_io_in_reset = ~clk_wiz_0/locked
// MMCM 锁定的时刻 —— 上电约 100µs；而 OV5640 的 PCLK 要等 I2C 把它的内部
// PLL 配完才出来 —— 上电约 600ms。**复位比它要复位的那一侧时钟早撤 0.6 秒。**
//
// 异步 FIFO 的正确上电次序是"复位必须保持到**两个**时钟都稳定运行"。
// 我们在写侧时钟还不存在时就撤了复位 → FIFO 指针/标志初始化在无时钟状态下
// 偷偷溜过去 → 写侧一开动就判满 → 溢出 → 按上面的链把视频域永久锁死。
// **这一条同时解释了全部四个观测**（tvalid 恒 0 / tuser 恒 0 / tlast 曾出现
// 一次后卡高 / vsync 计数完全正常——因为 vsync 是我们的独立探针，不经过 IP）。
//
// ★★ 怎么修 ★★
//
// 把"复位撤除"的条件从"MMCM 锁定"改成"MMCM 已锁 **且** PCLK 已经在跑"。
// PCLK 活性用数拍子判定：连续数满 2^20 拍（24MHz 下约 43.7ms）才算稳。
// 于是复位撤除的那一刻，写侧（pclk）和读侧（aclk，早就在跑）都在动，
// FIFO 拿到的是一个合法的初始化时刻。这是 IP 期望的上电次序。
//
// 另留一个 PS 可控的强制复位位（ctrl[3]），万一还有残留的死锁状态，
// 可以在**不重烧 bit** 的情况下从软件再复位一次视频域。
//
// ★ 第十三版（2026-09-14）：force_rst 释放后 alive **重新鉴定**（清零重数
//   2^20 拍）。为什么：第十二版 alive 一旦为 1 永久为 1；而第十三版的
//   ctrl[4]（传感器重跑上电时序）会先按住 force_rst 再松开，松开时传感器
//   还在掉电复位中、PCLK 停摆 —— 若 alive 还是永久 1，复位会在 PCLK 停摆
//   期间被撤，xpm_fifo 又拿到"复位比时钟早撤"的非法初始化（第七轮教训
//   重演）。改为 force 期间清零、释放后重新数满 2^20 拍才撤复位：
//   复位保持到 PCLK 恢复并稳定 —— 无论 PCLK 停多久。
//
// ★ 极性约定（本工程踩过坑的规矩）：axi_gpio 输出寄存器复位值为 0，
//   所以 force_rst 必须是"0 = 不复位 = 正常工作"，ctrl[3]/ctrl[4]=1 才强制复位。
// ============================================================================

module vid_rst_ctrl (
    input  wire pclk,          // 传感器 PCLK（写侧时钟，上电约 600ms 才出现）
    input  wire locked_n,      // ~MMCM locked（高 = MMCM 未锁定，来自 util_vector_logic_0）
    input  wire force_rst,     // PS 软件强制复位（ctrl[3]），高有效
    output wire vid_rst        // → v_vid_in_axi4s_0/vid_io_in_reset（高有效）
);

    // ------------------------------------------------------------------
    // PCLK 活性检测：数满 2^20 拍才算"时钟稳定在跑"
    // 24MHz 下 = 1048576 / 24e6 ≈ 43.7ms
    // 第十三版：fr_s2=1（强制复位中）期间清零并保持 0；fr_s2 释放后从 0
    // 重新数 —— force 每发一次都要重新鉴定 PCLK（含传感器重跑后 PCLK
    // 停摆再恢复的场景，见文件头第十三版注释）。
    // ------------------------------------------------------------------
    reg [19:0] alive_cnt;
    reg        alive;

    always @(posedge pclk) begin
        if (fr_s2) begin
            alive_cnt <= 20'd0;
            alive     <= 1'b0;
        end else if (alive_cnt != 20'hFFFFF) begin
            alive_cnt <= alive_cnt + 20'd1;
            alive     <= 1'b0;
        end else begin
            alive     <= 1'b1;
        end
    end

    // ------------------------------------------------------------------
    // force_rst 来自 aclk（100MHz）域，2 级同步进 pclk 域
    // 它是软件写的电平，慢速、长时间保持，2 级同步足够
    // ------------------------------------------------------------------
    reg fr_s1, fr_s2;
    always @(posedge pclk) begin
        fr_s1 <= force_rst;
        fr_s2 <= fr_s1;
    end

    // ------------------------------------------------------------------
    // 复位合并：任一条件成立就按住视频域
    //   locked_n  = MMCM 没锁           —— 读侧时钟都没了，谈不上工作
    //   ~alive    = PCLK 还没跑够       —— ★ 本版新增，修的就是这个
    //   fr_s2     = 软件强制复位
    // ------------------------------------------------------------------
    assign vid_rst = locked_n | ~alive | fr_s2;

endmodule
