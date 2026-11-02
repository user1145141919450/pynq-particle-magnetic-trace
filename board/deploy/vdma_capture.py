# -*- coding: utf-8 -*-
"""vdma_capture.py — PYNQ 端 OV5640 采集驱动（M1a，上板运行）

用 MMIO 直接驱动 PL 里 axi_vdma 的 S2MM 通道（寄存器基址 0x43000000）。
pynq 没有内置 VDMA 驱动类，本模块就是它的替代——也是 skill/ 候选的
"DMA 封装范式"：物理地址缓冲 + 环形帧缓冲 + 状态寄存器读最新完整帧。

寄存器偏移（PG020, axi_vdma v6.3；位号取自 IP 源码 axi_vdma_v6_3_rfs.vhd 的
DMACR_*_BIT / dmasr_i 组装语句，比手册回忆可靠）：
  S2MM_DMACR            0x30   bit0 RS(运行)  bit1 CRCLPRK(循环/驻留)  bit2 RESET
                               bit4 FRMCNTEN  bit6..5 FSYNCSEL(fsync 源选择)
                               bit16..23 IRQThreshold
  S2MM_DMASR            0x34   bit0 Halted  bit1 Idle  bit4..11 错误
                               bit16..23 帧计数（= irqthresh_status，倒计数，见坑 3）
  S2MM_VSIZE            0xA0
  S2MM_HSIZE            0xA4   （单位：字节/行）
  S2MM_FRMDLY_STRIDE    0xA8   （单位：字节；高位 [28:24] 是帧延迟）
  S2MM_START_ADDRESS    0xAC   +0x04×n 共 fstores 个（BD 配 fstores=4）

★ 四个踩过的坑（都表现为"传完 1 帧就停 / 首帧全零 / 一启动就报错"，别重犯）：

  1. 循环模式的位是 **bit1**，不是 bit4。bit4 是 FRMCNTEN（帧计数中断使能），
     "bit4=Cyclic" 是 AXI **DMA** 的约定，别跟 AXI VDMA 搞混。
     且 bit1 上电默认就是 1（见 rfs.vhd 的 DMACR_TAILPTREN 进程复位分支），
     写 DMACR 时必须显式把它带上，否则等于手动关掉循环模式。
     bit2 是 RESET，写 DMACR 时若顺手置 1，等于"启动的同时又复位"。

  2. 启动后第一次读 DMASR 之前，计数器里可能已有非 0 旧值 —— 不能拿它当
     "首帧"（见 frame() 的 _last_count 基准）。

  3. **IRQThreshold（DMACR 高 8 位）上电默认是 1，必须改成 0xFF。**
     DMASR 高 8 位不是"累加了多少帧"，而是 rfs.vhd 里的 ch*_thresh_count ——
     一个**倒计数器**：写阈值后装成阈值，每完成一帧减 1，减到 1 就重装成
     阈值并触发中断。阈值=1 时它每帧都"减到 1 → 重装成 1"，读数**永远停在 1**，
     于是"看计数变化判新帧"的 frame() 永远等不到第二帧。写成 0xFF 就得到
     一个每帧变化一次的 8 位计数，天然可判新帧。

  4. **启动瞬间会中一次 fsize_more_or_sof_late(bit11)+dma_interr(bit4)，是瞬态，不是故障。**
     传感器是自由运行的，PS 启动 VDMA 时通道在半帧中间"武装"。rfs.vhd 的
     SOF_LATE_ERR_PULSE_PROCESS 判定条件是：
         (s2mm_chnl_ready = '0') and (s_axis_s2mm_tvalid = '1') and (tuser = '0')
     即"通道还没准备好，源就在推非 SOF 的数据" —— 一启动必中。等通道进入
     ready 后它自己就清除，不影响后续传帧，但 **DMASR 里的错误位是粘连的
     （W1C：写 1 清除）**。所以驱动必须"记录 + 清除 + 继续"，绝不能一见
     错误位就抛异常（本工程踩过：一次性瞬态把整次采集判了死刑）。
     ★ 注意：坑 4 说的"瞬态"只在**通道真能进数据**时才自愈。若坑 5 的帧同步
     没配好，通道永远进不了 ready，这个"瞬态"会每拍复现、永不消失 ——
     实测表现为错误事件以固定间隔（驱动限流 0.5s）刷 115 次。

  5. ★★ **DMACR[6:5] FSYNCSEL 必须写 2（"10" tuser fsync），默认值 0 会让
     整个通道彻底死锁、一帧都传不出来。** 这是本工程最后卡住的那个根因。

     rfs.vhd:53016 的 S2MM FSYNC_CROSSBAR 原文：
         when "00" => frame_sync_i <= fsync;          -- primary fsync (default)
         when "01" => frame_sync_i <= othrchnl_fsync; -- other channel fsync
         when "10" => frame_sync_i <= tuser_fsync;    -- tuser (used only by s2mm)
         when others => frame_sync_i <= '0';
     而 fsync_src_select 接的是 s2mm_dmacr(6 downto 5)（rfs.vhd:73702），
     s2mm_dmacr 复位值是 (others => '0')（rfs.vhd:73764）→ 默认 "00"。
     本设计只有 S2MM 没有 MM2S（后面做 HDMI 显示链时才恢复 MM2S）：
       "00" primary fsync  没有来源          → 永不脉冲
       "01" othrchnl_fsync 没有对端通道      → 恒 0
       "10" tuser fsync    拿视频流的 SOF 当帧同步 → 单通道采集唯一能用的
     帧同步不脉冲 → chnl_ready 恒 0 → 数据一个字节都进不了 datamover。

     ★ 更正（2026-09-13 第九轮）：这条一度被当成"最后卡住的根因"，**它是错的**。
       本设计 BD 里 C_USE_S2MM_FSYNC=2、DMACR 也一直写的是 2，从没写错过。
       实测把 FSYNCSEL 四档全扫一遍（fsync_sweep.py）**全部不出帧、且四档的
       错误事件次数完全相同** —— 那才是"它不是变量"的直接证据。
       真正卡死的是坑 6。坑 5 只在"FSYNCSEL 确实被写成 0"时成立。

  6. ★★ **参数寄存器必须在 RS=1 之后再写一遍。** 这是本工程真正的最后一个根因，
     也是 8 轮黑盒排查才逼出来的一个**纯顺序问题**（PL 侧一个门都不用改）。

     rfs.vhd:20507 的 VIDEO_PRMTR_UPDATE：
         if(prmry_resetn = '0' or run_stop = '0')then
             prmtr_updt_complete_i <= '0';            -- ★ 通道没在跑 → 恒 0
         elsif(axi2ip_wrce(VSYNC_INDEX) = '1' ...)then
             prmtr_updt_complete_i <= '1';            -- ★ 只在"写 VSIZE"那一拍
         else
             prmtr_updt_complete_i <= '0';            -- ★ 而且只宽一拍

     这个单拍脉冲是**唯一**能清掉 d_tready_before_fsync_clr_flag1 的东西
     （rfs.vhd:72469：该 flag 在 halted 时被强制置 1）。而 flag=1 会卡住
     rfs.vhd:72387 那个门 → tuser 传不成 fsync_out → 73004 行的
     chnl_ready 永远拉不起来 → 72304 行的
         tready = (真tready and chnl_ready) or dummy_tready
     就恒等于 dummy_tready：**数据被收下然后丢掉，一个字节都不落 DDR。**

     原代码顺序是「写 VSIZE → 写 HSIZE/STRIDE → 写地址 → 最后 start()」。
     写 VSIZE 那一刻 run_stop 还是 0，脉冲被第一分支吃掉；start() 之后再没人
     碰过 VSIZE 寄存器 —— 于是整条链永久锁死。

     症状辨识（与坑 5 的差别在这里）：
         坑 5 —— 帧同步源配错，tvalid 根本推不动
         坑 6 —— **tvalid 完全正常**（44.5M/s、30 SOF/s、零溢出），
                 但缓冲全零、帧计数恒 255、DMASR=0x00ff4810 每次清完
                 **立刻原样重现**（因为是每拍重新拉起的活错误，不是残留），
                 而且**把 VSIZE 改成 1 也毫无反应**（chnl_ready 没起来，
                 行数设多少都无所谓——这一刀是区分两个坑的最快判别器）。

     Xilinx 自家的 XAxiVdma_StartWriteFrame 就是这个次序（先 DmaStart，再写
     参数）。本工程照着 BD 配置抄的时候把这一步搞反了。对照实验见
     software/board/param_order.py：同样的寄存器、同样的值，只是挪到 start()
     之后写，帧计数立刻从 255 变成持续递减、缓冲标准差从 0.0 变成 ~70。

PL 侧（BD m1a_capture，2026-09-14 第十二版）：dvp2axis_bridge 8bit 直通
（v_vid_in_axi4s 已删，见桥模块文件头），S2MM genlock 走内部 genlock +
tuser SOF（见坑 5）。OV5640 的 1024x720 RGB565 原始字节流（每行 2048 字节；
**帧高 = 720**（2026-09-23 实测：曾长期按 722 收，多出的 2 行让 VDMA 判帧尺寸不符
→ 丢帧 + 画面撕裂，帧率被压到 17.5fps；VSIZE 拟合扫描 + 行间相干性判据确认 720 为正解，
帧率恢复 ~29ms/帧且行间无断裂）
连续写进 4 帧环形缓冲，PS 侧无需任何同步动作。
PS 读 DMASR 帧计数换算出"最新完整帧"索引，任意时刻读都无撕裂。

RGB565 字节序：OV5640 DVP 先发高字节 R[4:0]G[5:3] 后发低字节 G[2:0]B[4:0]，
DMA 按地址序写入后与 cv2 期望（低字节在前）相反 → to_bgr() 做字节交换。
若上板实测颜色反了/花屏，先试 byte_swap=False（不同批次模块字节序可能不同）。

用法：
    from vdma_capture import S2mmCapture
    cap = S2mmCapture(1024, 720)
    cap.start()
    for i in range(10):
        idx, bgr = cap.bgr_frame()   # np.uint8 (720,1024,3) BGR，cv2 直接用
    cap.stop()

运行：sudo -E python3 vdma_capture.py（自带 __main__ 自检：连拍 10 帧存 PNG）
"""

import numpy as np

VDMA_BASE = 0x43000000
S2MM_DMACR = 0x30
S2MM_DMASR = 0x34
S2MM_VSIZE = 0xA0
S2MM_HSIZE = 0xA4
S2MM_STRIDE = 0xA8
S2MM_START = 0xAC

# DMACR 位（对应 rfs.vhd 的 DMACR_*_BIT）
CR_RS = 0x00000001        # Run/Stop
CR_CRCLPRK = 0x00000002   # Circular Buffer / Park Enable ← 循环采集靠它
CR_RESET = 0x00000004     # Soft Reset（自清）
CR_IRQTHRESH_SHIFT = 16   # DMACR[23:16] = IRQThreshold
CR_IRQTHRESH = 0xFF       # ★ 必须非 1，见模块注释坑 3
CR_FSYNCSEL_SHIFT = 5     # DMACR[6:5] = fsync 源选择，★ 见模块注释坑 5
CR_FSYNCSEL = 2           # "10" = tuser fsync（单 S2MM 通道专用）

# DMASR 位（对应 rfs.vhd 的 dmasr_i 组装）
SR_HALTED = 0x00000001
SR_IDLE = 0x00000002
SR_ERR_MASK = 0x00000FF0  # XAXIVDMA_SR_ERR_ALL_MASK：bit4..11
SR_FRMCNT_SHIFT = 16      # ★ 其实是倒计数，见模块注释坑 3

# 错误位名字（打印时逐位解读，别只丢一个 hex 让人猜）
SR_ERR_NAME = (
    (4, 'dma_interr', 'datamover 内部错（多与启动瞬态同时出现）'),
    (5, 'dma_slverr', 'AXI 从机响应错误'),
    (6, 'dma_decerr', 'AXI 解码错误'),
    (7, 'fsize_err', '帧尺寸不符 —— HSIZE/VSIZE 或流结构不对'),
    (8, 'lsize_err', '行尺寸不符 —— HSIZE/STRIDE 不对'),
    (9, 'sg_slverr', 'SG 从机错误'),
    (10, 'sg_decerr', 'SG 解码错误'),
    (11, 'fsize_more_or_sof_late', '帧超长或 SOF 迟到（启动瞬态 / 流结构不对）'),
)


# ---------------------------------------------------------------- 掉链自愈看门狗
# ★ 第十七版（2026-09-26 深夜）：治"链路跑着跑着自己死掉"
#
# 现场（**寄存器探针实测，不是推理**）：传感器侧完全健康 —— pclk 84.16MHz、
# vsync 30/s、href 21641/s、8 根数据线都在翻转、XCLK 24MHz 在跑、locked=1；
# 可流侧 tvalid/tuser/tlast **全是 0**，桥账本影子 7 读出 `full=1 且 empty=1`
# 且水位 0 —— 异步 FIFO 把自己锁在"两域分裂"的非法态里（写侧溢出 sticky 点亮、
# 读侧永远读到空），于是一比特都到不了 VDMA，frame_cnt 冻死。
#
# 这不是新病：vid_rst_ctrl.v 文件头记着 2026-09-13 量到过一模一样的症状
# （传感器健康、流侧全 0、溢出、永久锁死），第七版的治法就是本文件
# _init_video 末尾那一发 **ctrl[3] 脉冲**（复位保持到 PCLK 数满 2^20 拍才撤，
# 即 xpm_fifo 要求的合法上电次序）。问题是那一发只在**启动时**按一下，
# 而链路是**跑着跑着**才卡死的 —— 卡死之后没人再按。
#
# 本看门狗补的就是"之后也没人按"这一段：拿 frame_cnt 当心跳，卡住超过
# STALL_LIMIT_S 就自动重按一发 ctrl[3]。
# ★ 实测有效（2026-09-26 深夜，直接在卡死现场验的）：按一下 -> full/empty 双双
#   归零、水位回到十几、frame_cnt 从 1192 一路涨到 1598、tvalid 回到 44.3M/s。
#
# ★ 三条安全线：
#   1) **不跟"故意冻结"抢**：0x43B10000 的 ctrl[0]（freeze）为 1 时一律不插手 ——
#      那是脚本在故意冻住画面量东西（bram_write_map2 就干这个），不是掉链。
#   2) **写控制字一律读-改-写**：0x43A30000 那个字里还有 bit0 xclk_off / bit1 pwdn /
#      bit4 force_sensor_rst / [9:7] dbg_sel（桥账本影子选择器）。直接写常数
#      会把 XCLK 关掉、或把探针指针拨走。
#   3) 后台 daemon 线程 + 吞掉一切异常：看门狗只许帮忙，绝不许把主脚本弄崩。
STALL_LIMIT_S = 1.2
_WD_POLL_S = 0.25
# ★ 救援阶梯（2026-09-27 加，2026-09-29 重做判据）。
#   老判据是"30 秒窗口内卡了 2 次就升级到断电重上电"—— 这是**错的**：
#   "卡了两次"跟"脉冲不管用"是两回事，链路一晚上本来就会卡好几次，
#   于是看门狗会一路升级、把传感器按来按去，自己制造不稳定。
#   新判据：升级的唯一依据是**有据可依的失败** ——
#   "上一次动作是脉冲（而不是升级），结果它又卡了"，连续 _WD_ESCALATE_N 次才升级。
#   另外三条纪律：动作之间限频（_WD_MIN_GAP_S）、升级后有静默期（传感器要 ~1s 出流）、
#   升级次数封顶（_WD_MAX_ESC，到顶就放弃自愈只报告 —— 连败必须放弃，不能自锁）。
_WD_WINDOW_S = 30.0            # 统计窗口
_WD_ESCALATE_N = 2             # "脉冲之后又卡"累计 2 次 -> 升级
_WD_PWR_COOLDOWN_S = 20.0      # 升级之后静默 20s，等它把 SCCB 跑完出流再谈别的
_WD_MIN_GAP_S = 5.0            # 任意两次自愈动作之间至少 5s（限频硬线）
_WD_MAX_ESC = 3                # 升级上限；到了就放弃自愈
_wd_thread = None
_CTRL_GUARD = None          # dvp_regs.CtrlGuard（惰性）
_CTRL_GUARD_TRIED = False
WD_STAT = {'n_recover': 0, 't_last_recover': 0.0, 'stalled_total': 0.0,
           'n_pwr': 0, 'n_esc': 0, 'pulse_fails': 0, 't_win_start': 0.0,
           't_last_pwr': 0.0, 't_last_action': 0.0, 'last_kind': '',
           'gave_up': False, 'reported_giveup': False}


class WDDecider:
    """看门狗"这一拍该干什么"的**纯逻辑**：不碰寄存器、不睡、不打印。

    为什么单独抽出来：这段判据正是"自愈自己制造不稳定"的发源地（2026-09-27
    老判据在 30s 窗口里卡两次就升级，一晚上能把传感器按来按去十几次），而它
    埋在 MMIO 线程里就永远没法离线复盘。抽出来以后，台架可以拿假时钟把一条
    时间线整条喂进去，把"什么时候脉冲 / 什么时候升级 / 什么时候放弃"钉死。

    ★ 状态就寄存在 WD_STAT 里（诊断脚本读的就是它），**不另起一份** ——
      两份状态 = 迟早分叉，这是本工程最贵的一类 bug。

    ★★ 顺手修掉一个真 bug：老代码的 `gave_up` 字段**从来没有任何地方置位**
      （三处升级判据都只是"不再升级"，然后 fall through 去脉冲），所以
      升级用满之后它会**永远脉冲下去** —— "连败必须放弃"这条纪律名存实亡。
    """

    def __init__(self, st=None):
        self.st = WD_STAT if st is None else st

    def decide(self, now):
        """按 now（time.time() 口径）返回这一拍的动作。

        'skip'   什么都不做（限频期 / 升级后的静默期 / 已放弃）
        'pulse'  脉冲 ctrl[3] 复位视频域（第一线，便宜、无损）
        'power'  升级：传感器断电重上电（有据可依才升级，且限次）
        'giveup' 连败到头：只报告，之后永远 skip（返回且只返回一次）
        """
        st = self.st
        if st['gave_up']:
            return 'skip'                       # 已放弃：不再动手，也不再刷屏
        if st['last_kind'] and now - st['t_last_action'] < _WD_MIN_GAP_S:
            return 'skip'                       # 限频硬线
        if (st['last_kind'] == 'power'
                and now - st['t_last_pwr'] < _WD_PWR_COOLDOWN_S):
            # 升级后的**真静默期**：传感器正在重跑上电时序 + 304 条 SCCB，
            # 这时候连脉冲都别打（打的就是它刚起来的视频域）。
            # ★ 老代码只拿这个 20s 卡"下一次升级"，脉冲照打 —— 注释和代码对不上，
            #   台架把时间线一跑就露馅了。改成真静默之后，两次升级之间天然 >=20+5+5s。
            return 'skip'
        if now - st['t_win_start'] > _WD_WINDOW_S:
            st['t_win_start'] = now             # 开新窗口：失败计数从零开始
            st['pulse_fails'] = 0               # （只会推迟升级，不会加速 —— 安全方向）
        if st['last_kind'] == 'pulse':
            st['pulse_fails'] += 1              # 有据可依：脉冲过、现在又卡了
        if st['pulse_fails'] >= _WD_ESCALATE_N:
            if st['n_esc'] >= _WD_MAX_ESC:      # 升级次数用满 -> 放弃（不能自锁）
                st['gave_up'] = True
                st['reported_giveup'] = True
                st['t_last_action'] = now
                return 'giveup'
            st['n_esc'] += 1
            st['t_last_pwr'] = now
            st['pulse_fails'] = 0
            st['last_kind'] = 'power'
            st['t_last_action'] = now
            return 'power'
        st['last_kind'] = 'pulse'
        st['t_last_action'] = now
        return 'pulse'


def _ctrl_guard():
    """惰性拿一份 dvp_regs.CtrlGuard（拿不到就返回 None —— 不挡任何事）。

    CtrlGuard 一构造就挂 atexit + SIGTERM/SIGINT 兜底：任何退出路径都会把
    ctrl 的**静止位**（bit0 xclk_off / bit1 pwdn / bit3 vid_rst / bit4 sensor_rst）
    清掉。这就是 2026-09-29 那次"电源位锁死 40 秒"的正面修复 —— 以前这几个位没人管。
    """
    global _CTRL_GUARD
    if _CTRL_GUARD is None and not _CTRL_GUARD_TRIED:
        _CTRL_GUARD_TRIED = True
        try:
            import dvp_regs
            _CTRL_GUARD = dvp_regs.CtrlGuard(dvp_regs.open_regs(), verbose=False)
        except Exception as e:
            print('[自愈] （提示）退出兜底没装上（%r）—— 启动时的 link_boot 还会兜一层'
                  % (e,), flush=True)
            _CTRL_GUARD = None
    return _CTRL_GUARD


def _sensor_power_cycle(mm3, v, tag):
    """传感器断电重上电 + 传感器复位 —— 照抄 stall_probe.pulse_power_cycle 的寄存器序列。

    为什么需要它（2026-09-27 整天实测）：链路有两种死法，**脉冲只治得了第一种**：
      · 卡死（frame_cnt 完全不动）              -> ctrl[3] 脉冲有效
      · "慢速半死"/彻底死（几十秒卡一次→完全停） -> 脉冲无效，只有
        传感器断电重上电（本函数）或整机断电才救得回来

    寄存器（按 rtl/debug/dvp_ctrl.v 第 24~31 行，**不凭记忆**）：
      ctrl[0] = xclk_off（1 = 拉低停钟）  ctrl[1] = force_pwdn（1 = 强制掉电）
      ctrl[4] = force_sensor_rst（按住 = ov5640_top 重跑上电 + 304 条 SCCB，
                同时按住视频域复位，垃圾字节不会堵 FIFO）
    ★ 老注释把 bit0/bit1 写反了（写成"bit0=pwdn bit1=xclk"）—— 因为两位都置所以
      行为上没出事，但这种注释就是下一个 bug 的种子，已按 RTL 改正。

    ★★ 2026-09-29 事故与修复 ★★
      老版本是"写 v|0x03 -> sleep 0.5 -> 写 v&~0x03"，**中间没有 try/finally、
      没有 atexit、没有信号处理**。进程只要在这 0.5 秒里死掉，xclk_off/pwdn
      就永远锁着（实测 ctrl=0x383 锁了 40 秒，xclk/pclk 全停）。
      现在：三段全部包在 try/finally 里 —— 异常、SIGTERM、SIGINT 都会走到 finally
      把位放开；另外 _ctrl_guard() 给了 atexit/信号那一层。
      （SIGKILL 和硬崩溃谁都救不了，那种情况由下次启动的 link_boot 兜底。）
    """
    g = _ctrl_guard()
    mem = getattr(g, 'mem', None) if g is not None else None
    if mem is not None:
        try:
            import dvp_regs
            # ★ 真正的实现只有一份：dvp_regs.power_cycle（纯 /dev/mem）。
            #   放在那边的原因是**它能被离线台架整条跑一遍**（假寄存器，不碰硬件），
            #   而 PYNQ 版要 numpy/pynq 才能 import，PC 上跑不动 —— 测试跑不到
            #   的代码等于没测。板上 /dev/mem 一定可用（脚本都是 root），所以
            #   板上走的正是这条被测过的路。
            dvp_regs.power_cycle(mem, quiet=True)
            print('[自愈] >>> %s：已做传感器断电重上电 + 传感器复位，等它重跑上电/SCCB'
                  % (tag,), flush=True)
            time.sleep(0.5)
            return
        except Exception as e:
            print('[自愈] （提示）dvp_regs 版断电重上电没跑成（%r），退回 mm3 版'
                  % (e,), flush=True)
    _power_cycle_mm3(mm3, v)
    print('[自愈] >>> %s：已做传感器断电重上电 + 传感器复位，等它重跑上电/SCCB'
          % (tag,), flush=True)
    time.sleep(0.5)


def _power_cycle_mm3(mm3, v):
    """退回版：PYNQ MMIO 直写的断电重上电（只有 /dev/mem 不可用时才走到这里）。

    序列与 dvp_regs.power_cycle 一致；同样套 try/finally —— 任何异常路径的
    **最后一次写必须是 base**（静止位干净），否则就是 2026-09-29 那个锁死事故。
    """
    base = v & ~0x03                             # 干净基线：0/1 位一定是 0
    try:
        try:
            mm3.write(0x00, base | 0x03)         # 停 xclk + 强制 pwdn
            time.sleep(0.5)
        finally:
            mm3.write(0x00, base)                # ★ 无论如何先放开（再往上抛）
        try:
            time.sleep(0.2)
            mm3.write(0x00, base | (1 << 4))     # 传感器复位脉冲
            time.sleep(0.3)
        finally:
            mm3.write(0x00, base)                # ★ 再放开一次
    except Exception:
        # 最后一道：连 finally 里的写都失败的话，再尽力写一次 base 才往上抛。
        try:
            mm3.write(0x00, base)
        except Exception:
            pass
        raise


def _vid_rst_pulse(mm3, v, tag):
    """第一线自愈：脉冲 ctrl[3]（复位视频域），与 dvp_regs 同一份实现。

    以前这里是三行裸写：置位 -> sleep(0.3) -> 清位。中间抛异常/被 SIGTERM 打断
    就把 bit3 锁住（视频域永远复位，永远不出帧）—— 跟 bit0/bit1 是同一类事故。
    现在优先走 dvp_regs.pulse_vid_rst（try/finally + 能被离线台架跑），
    拿不到 /dev/mem 才退回本地版（同样 try/finally）。
    """
    g = _ctrl_guard()
    mem = getattr(g, 'mem', None) if g is not None else None
    if mem is not None:
        try:
            import dvp_regs
            dvp_regs.pulse_vid_rst(mem, quiet=True)
            print('[自愈] >>> %s：已脉冲 ctrl[3] 复位视频域' % (tag,), flush=True)
            return
        except Exception as e:
            print('[自愈] （提示）dvp_regs 版脉冲没跑成（%r），退回 mm3 版' % (e,), flush=True)
    base = v & ~(1 << 3)
    try:
        mm3.write(0x00, v | (1 << 3))            # ctrl[3] = 强制复位视频域
        time.sleep(0.30)                         # （alu 域要 ~43.7ms 才真撤，见下）
    finally:
        mm3.write(0x00, base)                    # ★ 无论如何先放开，再往上抛
    time.sleep(0.30)
    print('[自愈] >>> %s：已脉冲 ctrl[3] 复位视频域' % (tag,), flush=True)


def _stall_watchdog_loop():
    import time
    from pynq import MMIO
    try:
        mmc = MMIO(0x43B10000, 0x1000)     # +0x00 ctrl(bit0 freeze) / +0x08 frame_cnt
        mm3 = MMIO(0x43A30000, 0x1000)     # dvp_ctrl 控制字
    except Exception:
        return                              # 探针不在也不该挡住采集
    fc = mmc.read(0x08)
    t_fc = time.time()
    dec = WDDecider()                       # 决策逻辑（纯函数式，离线台架能整条驱动）
    while True:
        try:
            time.sleep(_WD_POLL_S)
            cur = mmc.read(0x08)
            if cur != fc:
                fc, t_fc = cur, time.time()
                continue
            if time.time() - t_fc < STALL_LIMIT_S:
                continue
            if mmc.read(0x00) & 0x01:       # 软件故意冻结 -> 不是掉链，不插手
                t_fc = time.time()
                continue
            stalled = time.time() - t_fc
            st = dec.st
            act = dec.decide(time.time())
            if act == 'skip':
                continue                    # 限频期/已放弃：不动链路，t_fc 也不重置
            if act == 'giveup':
                # 连败到头：只报告一次，之后永远 skip（老代码在这里会永远脉冲下去）
                st['stalled_total'] += stalled
                fc, t_fc = cur, time.time()  # 静默：不再反复刷屏
                print('[自愈] 已放弃自愈（升级过 %d 次仍然卡）：**不再动链路**。'
                      '人工处理 = 整机断电 / 重下 bit，然后跑 link_doctor 复查'
                      % (st['n_esc'],), flush=True)
                continue
            v = mm3.read(0x00)
            if act == 'power':
                _sensor_power_cycle(mm3, v, '第 %d 次自愈（脉冲连续 %d 次不管用）'
                                    % (st['n_recover'] + 1, _WD_ESCALATE_N))
                st['n_pwr'] += 1
                why = '已升级：传感器断电重上电'
            else:
                _vid_rst_pulse(mm3, v, '第 %d 次自愈' % (st['n_recover'] + 1,))
                why = '已脉冲 ctrl[3] 复位视频域（脉冲失败 %d/%d）' % (
                    st['pulse_fails'], _WD_ESCALATE_N)
            st['n_recover'] += 1
            st['t_last_recover'] = time.time()
            st['stalled_total'] += stalled
            # ★ flush=True 不是为了好看：这行是**判断"自愈发生在哪一步之后"的唯一
            #   证据**。不给 flush 的话它会留在缓冲区里，等进程退出才吐出来，日志里
            #   就跑到别的行后面去了 —— 2026-09-26 第 4 轮验收就因此把"D 项为什么量到
            #   0.0 fps"看了半天（自愈的日志看起来在 D 之后，其实是 D 之前就卡了）。
            print('[自愈] 链路卡死（frame_cnt 停在 %d，约 %.1fs）-> %s（第 %d 次；'
                  '累计升级 %d/%d）'
                  % (cur, stalled, why, st['n_recover'], st['n_esc'], _WD_MAX_ESC),
                  flush=True)
            fc, t_fc = mmc.read(0x08), time.time()
        except Exception as e:
            # ★ 吞异常可以（看门狗不许把主脚本弄崩），但**必须留话**：
            #   2026-09-29 台架第一次跑就发现"自愈里漏参数 -> TypeError ->
            #   被这里吞掉 -> 自愈静默失效"。静默失败最贵，所以改成"吞 + 报一次"。
            key = '%s: %s' % (type(e).__name__, e)
            if WD_STAT.get('last_err') != key:
                WD_STAT['last_err'] = key
                WD_STAT['n_err'] = WD_STAT.get('n_err', 0) + 1
                print('[自愈] !! 自愈动作抛了异常（第 %d 次）：%s —— 这一次没救成；'
                      '同样的异常不再刷屏' % (WD_STAT['n_err'], key), flush=True)
            time.sleep(_WD_POLL_S)


def start_stall_watchdog():
    """起一次即可（幂等）。_init_video 会自动调用，脚本不用管。"""
    global _wd_thread
    if _wd_thread is not None and _wd_thread.is_alive():
        return _wd_thread
    import threading
    _wd_thread = threading.Thread(target=_stall_watchdog_loop,
                                  name='stall-watchdog', daemon=True)
    _wd_thread.start()
    return _wd_thread


def decode_sr(sr):
    """把 DMASR 原始值翻成人话：错误位逐个列名，无错误则说明当前状态。"""
    errs = [f'bit{b}({n}: {d})' for b, n, d in SR_ERR_NAME if sr & (1 << b)]
    if errs:
        return ' | '.join(errs)
    return '无错误位（bit0 halted=%d bit1 idle=%d）' % (sr & 1, (sr >> 1) & 1)


class S2mmCapture:
    """OV5640 → VDMA S2MM 环形采集（8bit 字节流直通）。

    width/height : 像素（当前 PL 配置 1024x720 —— 帧高 720 是实测值，见模块注释，
                   详见模块注释；每像素 2 字节 RGB565）
    num_bufs     : 与 BD 的 c_num_fstores 一致（=4）
    byte_swap    : RGB565 高/低字节序与 cv2 相反时置 True（见模块注释）
    """

    def __init__(self, width, height, num_bufs=4, byte_swap='auto'):
        from pynq import MMIO, allocate
        self.width, self.height, self.num_bufs = width, height, num_bufs
        self.byte_swap = byte_swap
        self._swap_decided = None                  # 'auto' 首次解码后固化
        self._n_frames = 0                         # 复核字节序用的帧计数
        self.stride = width * 2                    # RGB565: 2 字节/像素
        self.mmio = MMIO(VDMA_BASE, 0x10000)
        # ★★ 先复位、后分配（2026-09-26 第二十版）
        # 老顺序是"先 allocate 再 reset"。在上一个进程**崩着退出**的情况下这是致命的：
        # 崩掉的进程不会停 VDMA，PL 里的 S2MM 还在按老地址往老缓冲里写
        # （28fps x 1.47MB/帧）。而新 allocate 拿到的 CMA 页**很可能就是刚释放的那几页**
        # —— 于是刚分配到的缓冲立刻被野 DMA 覆盖，读回来是撕裂/旧数据。
        # 第 5 轮验收的指纹：帧索引只有 1、相邻两帧一致性 10%（噪声底也 10%）、
        # 帧率 3fps、视核账本 errs=0x08（bit3 = 一帧 tlast 数超过 720，行数不对）。
        # 复位在前 = 先把野 DMA 停掉再碰内存。reset() 只写 DMACR，不需要缓冲地址，
        # 所以挪到 allocate 之前完全安全；_program_frame_regs()（要缓冲地址）仍在后面。
        self.reset()
        # 连续物理内存帧缓冲（pynq allocate → CMA，physical_address 直填 VDMA）
        self.bufs = [allocate(shape=(height, self.stride), dtype=np.uint8)
                     for _ in range(num_bufs)]
        self._program_frame_regs()               # 先写一遍；start() 里还会再写一遍（见坑 6）
        self._last_count = None                  # frame() 判"新帧"用
        self._started = False
        self.errors = []                         # [(t, DMASR)] 采集期间见到的错误位

    # ---------------- 低级寄存器操作 ----------------

    def _program_frame_regs(self):
        """把一块帧缓冲的几何 + 地址写进 S2MM 寄存器组。

        ★ 这个动作**必须在 RS=1 之后再调一次**（见模块注释坑 6）：
        写 VSIZE 那一拍会产出 prmtr_updt_complete 单拍脉冲，而它只在
        run_stop=1 时才打得出来 —— 少了它，chnl_ready 永远拉不起来，
        数据会被 dummy tready 静默丢掉。
        """
        self.mmio.write(S2MM_VSIZE, self.height)
        self.mmio.write(S2MM_HSIZE, self.stride)
        self.mmio.write(S2MM_STRIDE, self.stride)
        for i, b in enumerate(self.bufs):
            self.mmio.write(S2MM_START + 4 * i, int(b.physical_address))

    def reset(self):
        """软复位 S2MM 通道，等 RESET 位自清（PG020 要求先清 RS 再置 RESET）。"""
        import time
        self.mmio.write(S2MM_DMACR, 0x0000)          # 先停
        self.mmio.write(S2MM_DMACR, CR_RESET)        # bit2 RESET=1，自清
        t0 = time.time()
        while self.mmio.read(S2MM_DMACR) & CR_RESET:
            if time.time() - t0 > 1.0:
                raise RuntimeError('VDMA S2MM 软复位 1s 未自清（DMACR 见上）')
            time.sleep(0.001)

    def start(self):
        """启动 S2MM：Run + Circular/Park + IRQThreshold=0xFF。

        ★ 必须同时写 bit1(CRCLPRK)。写 DMACR 是整字写入，漏掉 bit1 就等于
        关掉循环模式 —— 通道会传完 1 帧就自己清 RS、置 Halted（本工程踩过）。
        绝不能带 bit2(RESET)，那等于"启动的同时又复位"。
        ★ 高 8 位阈值必须写 0xFF，否则 DMASR 帧计数永远停在 1（见模块注释坑 3）。
        ★ bit6..5 必须写 2（"10" tuser fsync），否则帧同步没有来源（见坑 5）。
        ★★ 写完 DMACR 之后**必须把参数寄存器再写一遍**（见坑 6）—— 这是本工程
        真正的最后一个根因。它的位置不能动：早了 prmtr_updt_complete 脉冲会被
        run_stop=0 吃掉，晚了 VDMA 已经在空转丢数据。
        """
        self._last_count = (self.mmio.read(S2MM_DMASR) >> SR_FRMCNT_SHIFT) & 0xFF
        self.mmio.write(S2MM_DMACR,
                        CR_RS | CR_CRCLPRK | (CR_FSYNCSEL << CR_FSYNCSEL_SHIFT)
                        | (CR_IRQTHRESH << CR_IRQTHRESH_SHIFT))
        self._program_frame_regs()               # ★★ 见坑 6：必须在 RS=1 之后
        self._started = True
        # ★★ 退出前先停 DMA（2026-09-26 第二十版）
        # 为什么必须停：缓冲是 pynq allocate() 出来的 CMA 页，**进程一退就被释放**，
        # 而 PL 里的 VDMA 是硬件、不随进程退出而停 —— 它会继续往那几页写。
        # 实测崩溃指纹（第 5 轮验收第 2 步，本脚本退出时）：
        #     Fatal Python error: Segmentation fault
        #     Current thread ... (most recent call first):
        #       Garbage-collecting
        #       <no Python frame>
        # 就是"GC 释放缓冲 → 底层 ioctl 去 free 一块正在被 DMA 写的内存"这一步崩的
        # （崩在 C 里，所以没有 Python 栈）。它崩了还不算完：野 DMA 会继续污染**下一个**
        # 进程刚分配到的页 —— 第 4 轮验收里那些"紧跟 PYNQ 进程之后 import 就段错误"、
        # 以及第 5 轮 step4 的 3fps/撕裂帧，都是这条链上的。
        # 注册在 start() 里（不是 __init__）：只有真跑起来过才需要停。
        # 注意 atexit **不覆盖 SIGKILL/段错误**，那种情况由 __init__ 的"先复位再分配"兜住。
        try:
            import atexit
            if not getattr(self, '_atexit_done', False):
                self._atexit_done = True
                atexit.register(self._stop_quiet)
        except Exception:
            pass

    def _stop_quiet(self):
        """atexit 专用：退出路径上只许帮忙，不许抛异常（MMIO 可能已被拆）。"""
        try:
            self.stop()
        except Exception:
            pass

    def stop(self):
        self.mmio.write(S2MM_DMACR, 0x0000)
        self._started = False

    def status(self):
        """返回 (DMASR 原始值, 帧计数, halted, 是否出错)。

        ★ 错误位是 bit4..11。别用低位掩码 —— bit1 是 IDLE（空闲是正常状态），
        把它当错误会让通道一进入正常空闲就误报（本工程踩过）。"""
        sr = self.mmio.read(S2MM_DMASR)
        return (sr, (sr >> SR_FRMCNT_SHIFT) & 0xFF,
                bool(sr & SR_HALTED), bool(sr & SR_ERR_MASK))

    def clear_errors(self):
        """清粘连错误位（DMASR 对应位是 W1C，写 1 清零）。返回清掉的原始值。"""
        sr = self.mmio.read(S2MM_DMASR)
        if sr & SR_ERR_MASK:
            self.mmio.write(S2MM_DMASR, sr & SR_ERR_MASK)
        return sr

    # ---------------- 取帧 ----------------

    def frame(self, timeout=2.0):
        """等待一帧**新**帧完成，返回 (帧索引, np.uint8 帧 (height, stride))。

        连续调用时等帧计数变化（新帧），不会重复返回同一块缓冲；
        timeout 内计数不涨 → TimeoutError（消息里带当前计数，便于定位）。

        ★ 见到错误位时**只记录 + 清除 + 继续等**，不抛异常：启动瞬态那一次
        fsize_more_or_sof_late 是必中的（见模块注释坑 4），抛异常等于把整次
        采集判死刑。真出不来帧时由 timeout 兜底报错，错误历史一并带出。"""
        import time
        t0 = time.time()
        while True:
            sr, count, halted, err = self.status()
            if err:
                # 最多每 0.5s 记一次，避免每 10ms 轮询都追加一条
                if not self.errors or (time.time() - self.errors[-1][0]) > 0.5:
                    self.errors.append((time.time(), sr))
                self.clear_errors()                  # W1C 清掉，别让它挡住后续
            if count != self._last_count:
                self._last_count = count
                if count == 0:          # 8 位计数器回绕
                    continue
                idx = (count - 1) % self.num_bufs    # 最新完成帧所在缓冲
                if hasattr(self.bufs[idx], 'invalidate'):
                    self.bufs[idx].invalidate()      # PL 直写 DDR，绕过 CPU 缓存
                return idx, self.bufs[idx]
            if time.time() - t0 > timeout:
                sr, count, halted, err = self.status()
                raise TimeoutError(
                    f'{timeout:.0f}s 内无新帧（帧计数停在 {count}，'
                    f'DMASR=0x{sr:08x} halted={halted} err={err}；'
                    f'期间错误事件 {len(self.errors)} 次）')
            time.sleep(0.01)

    def to_bgr(self, raw):
        """原始字节帧 → BGR（RGB565 双字节 → cv2 转换）。

        字节序实测会变：同一份寄存器配置（0x4300=0x61），两次上电分别呈现
        "低字节在前"与"高字节在前"，所以固定默认值不可靠 → 默认 'auto'：
        首次解码时两种顺序各解一遍，取 R/G/B 三通道相关性高的一侧并固化。
        （正确顺序的相关性 0.83~0.95，错误顺序 ≈0.1，YUYV 误判 ≈0.58，区分度极大。）
        """
        import cv2
        b = raw.reshape(self.height, self.width, 2)   # 通道0=低字节, 通道1=高字节
        swap = self.byte_swap
        if swap == 'auto':
            if self._swap_decided is None:
                d = self._pick_swap(b)             # 判不准返回 None（不固化，等下一帧）
                if d is not None:
                    self._swap_decided = d
                    print('[驱动] 字节序自动判定：%s' % ('需交换' if d else '无需交换'))
            else:
                self._n_frames += 1
                if self._n_frames % 60 == 0:       # 每 60 帧复核一次（自愈 + 翻序检测）
                    self._verify_swap(b)
            swap = bool(self._swap_decided)
        if swap:
            b = b[..., ::-1]
        return cv2.cvtColor(b, cv2.COLOR_BGR5652BGR)

    @staticmethod
    def _score_swap(b8, swap, step=3):
        """给"某种字节序"打相关性分：正确顺序 R/G/B 高度相关 ≈0.93~0.95，
        错误顺序 ≈0.02~0.11 —— 差 50 倍，区分度极大。

        ★ 先抽样(step)再解码：整幅 740k 像素在板上要几十毫秒，抽样到 1/9 足够判。
        """
        import cv2
        import numpy as np
        arr = b8[::step, ::step]
        if swap:
            arr = arr[..., ::-1]
        bgr = cv2.cvtColor(np.ascontiguousarray(arr), cv2.COLOR_BGR5652BGR)
        f = bgr.astype(np.float32).reshape(-1, 3)
        c = np.corrcoef(f.T)
        return float((c[0, 1] + c[0, 2] + c[1, 2]) / 3.0)

    def _pick_swap(self, b8, min_best=0.60, min_margin=0.30):
        """判定字节序。**必须"赢家分数够高 + 与输家差距够大"才认，否则返回 None。**

        ★ 这条门槛是血换来的：ctrl[3] 脉冲之后的第一帧可能是**错位/撕裂帧**，
          那种帧两种顺序都打不出高分，只挑"分高的那个"就会挑错；而挑错一次会被
          固化，之后每一帧都是伪彩（实测：整轮 10 帧全彩虹、整幅图只剩边缘假彩色）。
          区分度不足时返回 None → 调用方换下一帧再判，直到判准为止。
        """
        if float(b8.std()) < 2.0:                  # 空帧/全黑：判不出来
            return None
        s0 = self._score_swap(b8, False)
        s1 = self._score_swap(b8, True)
        best, bs = (True, s1) if s1 > s0 else (False, s0)
        worst = s0 if best else s1
        if bs < min_best or (bs - worst) < min_margin:
            print('[驱动] 字节序暂不判定：本帧两种顺序得分 %.3f / %.3f，区分度不足'
                  '（疑似错位/撕裂帧），等下一帧' % (s0, s1))
            return None
        print('[驱动] 字节序判定依据：不交换 %.3f / 交换 %.3f' % (s0, s1))
        return best

    def _verify_swap(self, b8):
        """复核已固化的字节序：判错能自愈，上电翻序也能发现。

        另一次上电确实会翻字节序（实测 r18 需交换 0.954、今日无需交换 0.931），
        所以"固化"本身有风险；每 60 帧复核一次的代价可以忽略（抽样打分 ~ms 级）。
        """
        if float(b8.std()) < 2.0:
            return
        cur = bool(self._swap_decided)
        s_cur = self._score_swap(b8, cur)
        s_alt = self._score_swap(b8, not cur)
        if s_alt >= 0.60 and (s_alt - s_cur) >= 0.30:
            print('[驱动] ★ 字节序复核：当前顺序 %.3f 明显不如另一种 %.3f → 翻转'
                  % (s_cur, s_alt))
            self._swap_decided = not cur

    def bgr_frame(self, timeout=2.0):
        idx, raw = self.frame(timeout)
        return idx, self.to_bgr(raw)


def _buf_stats(cap):
    """4 个缓冲各自的均值/标准差 —— 一眼看出数据到底落进哪块。"""
    for i, b in enumerate(cap.bufs):
        if hasattr(b, 'invalidate'):
            b.invalidate()                       # PL 直写 DDR，统计前必须失效缓存
        a = np.asarray(b, dtype=np.uint8)
        print(f'[驱动自检]   buf{i} @0x{int(b.physical_address):08x}  '
              f'均值 {a.mean():6.1f}  标准差 {a.std():6.1f}')


def _print_errors(cap):
    """把采集期间见到的错误事件逐条解读出来（粘连位已清，但历史留着）。"""
    if not cap.errors:
        print('[驱动自检] 采集期间未再出现错误位')
        return
    print(f'[驱动自检] 采集期间见到 {len(cap.errors)} 次错误位事件（已逐条清除，未中断采集）：')
    for t, sr in cap.errors[:6]:
        print(f'[驱动自检]   t={t:.3f}s  DMASR=0x{sr:08x}  {decode_sr(sr)}')
    if len(cap.errors) > 6:
        print(f'[驱动自检]   ...（共 {len(cap.errors)} 次，其余略）')


def _selfcheck():
    import cv2
    import time
    cap = S2mmCapture(1024, 720)
    print(f'[驱动自检] {cap.num_bufs} 帧缓冲 @ {[hex(int(b.physical_address)) for b in cap.bufs]}')
    # 启动前快照：DMASR 里若已有的帧计数非 0，说明计数器带旧值，
    # 启动后不能拿它当"首帧"（frame() 已用启动瞬间的计数作基准）
    sr = cap.mmio.read(S2MM_DMASR)
    print(f'[驱动自检] 启动前 DMASR=0x{sr:08x} 帧计数={(sr >> 16) & 0xFF} '
          f'DMACR=0x{cap.mmio.read(S2MM_DMACR):08x}')
    cap.start()
    dr = cap.mmio.read(S2MM_DMACR)
    print(f'[驱动自检] 已启动 DMACR=0x{dr:08x}'
          f'（期望 0x00ff0003 = RS+CRCLPRK+IRQThreshold=0xff）')
    if not dr & CR_CRCLPRK:
        print('[驱动自检] !! 循环位 CRCLPRK(bit1) 没置上，通道会传完 1 帧就停')
    if ((dr >> CR_IRQTHRESH_SHIFT) & 0xFF) in (0, 1):
        print('[驱动自检] !! IRQThreshold 是 0 或 1，DMASR 帧计数会卡住不动，读不到新帧')

    # ★ 次序：**sink 先就绪、源后开**。
    # bit 下载到 cap.start() 之间隔了上百毫秒，而传感器上电就自由运行 ——
    # 这期间推出来的帧没人接，IP 内部 FIFO 一溢出就会把整个视频域按死
    # （`VID_RESET = vid_reset | fifo_overflow_from_coupler`，见 axis_probe.v
    # 头部与 vid_rst_ctrl.v）。所以在 VDMA 就绪之后主动把视频域复位一次、
    # 放出来，让源从干净状态重新开跑。第七轮实测：这个次序下流可以稳跑 5s+
    # 且零溢出；反过来（源先开、sink 后到）会死透。
    try:
        from pynq import MMIO
        mm3 = MMIO(0x43A30000, 0x10000)
        c0 = mm3.read(0x00, 4)
        mm3.write(0x00, c0 | (1 << 3))       # ctrl[3] = 强制复位视频域
        time.sleep(0.3)
        mm3.write(0x00, c0 & ~(1 << 3))      # 撤复位，源重新开跑
        print('[驱动自检] 视频域已按"sink 先就绪、源后开"重启（ctrl[3] 脉冲 0.3s）')
    except Exception as e:                    # 探针 GPIO 不在也不该挡住采集
        print(f'[驱动自检] 视频域重启跳过（{type(e).__name__}: {e}）')

    # ★ 上面那一发只在**启动时**按一次；链路是**跑着跑着**才卡死的（实测：启动后
    #   几秒到几十秒随机卡死，卡住就不再恢复）。所以把同样的动作挂成后台看门狗，
    #   拿 frame_cnt 当心跳，卡住超过 STALL_LIMIT_S 就自动重按一发。见上方注释。
    try:
        start_stall_watchdog()
        print('[驱动自检] 掉链看门狗已挂上（心跳=frame_cnt，卡住 %.1fs 自动脉冲 '
              'ctrl[3] 复位视频域）' % STALL_LIMIT_S)
    except Exception as e:
        print(f'[驱动自检] 掉链看门狗没挂上（{type(e).__name__}: {e}）')

    # ★ 启动瞬态：通道在半帧中间武装，必中一次 fsize_more_or_sof_late。
    # 这里主动等一拍再读一次，把它显式地"看到并清掉"，免得污染后面的判断。
    time.sleep(0.5)
    sr = cap.clear_errors()
    if sr & SR_ERR_MASK:
        print(f'[驱动自检] 启动瞬态错误位 DMASR=0x{sr:08x} -> {decode_sr(sr)}')
        print('[驱动自检] 已清除（W1C）。这是"传感器自由运行、通道半帧中间武装"的'
              '固有现象，不是故障，见模块注释坑 4')
    else:
        print('[驱动自检] 启动 0.3s 内无错误位（DMASR=0x%08x）' % sr)

    # ★ 前 2 帧丢掉：启动时通道武装在半帧中间，首个"完成帧"极可能是滚动的半帧，
    #   拿它判图像质量会冤枉整条数据通路。取第 3 帧（此时相位已稳定）。
    print('[驱动自检] 等待首帧（传感器要跑约 1s 的 SCCB 配置，耐心）...')
    t0 = time.time()
    idx = None
    for k in range(3):
        try:
            idx, bgr = cap.bgr_frame(timeout=60.0)  # 放宽到 60s：区分"停流"与"极慢流"
        except TimeoutError:
            print(f'[驱动自检] 60s 无帧（第 {k + 1} 次尝试）！'
                  '请确认 LD0 亮（MMCM 锁定）且 run_selfcheck.py 已成功下载 bit')
            _print_errors(cap)
            raise
    dt = time.time() - t0
    out = '/boot/vdma_capture_check.png'        # 存 FAT 分区，拔卡 Windows 直接看
    cv2.imwrite(out, bgr)
    print(f'[驱动自检] 第 3 帧 ok（前 2 帧已丢）：idx={idx} 共耗时 {dt:.2f}s，'
          f'尺寸 {bgr.shape[1]}x{bgr.shape[0]}，已存 {out}')
    print(f'[驱动自检] 帧统计：均值 {bgr.mean():.1f} 标准差 {bgr.std():.1f}'
          f'（标准差≈0 = 没有真实图像数据，查 DVP 接线）')
    # 原始字节帧也存一张灰度图：颜色/字节序有疑问时，原图比 BGR 更能说明问题。
    # ★ 必须先 ascontiguousarray 拷成连续内存：cap.bufs[idx] 是 CMA 缓冲区上的
    #   视图，步长不一定连续，直接喂 cv2.imwrite 会静默返回 False、写出 0 字节
    #   文件（本工程踩过：raw 图一直是 0 字节，正是这个原因）。
    raw = np.ascontiguousarray(np.asarray(cap.bufs[idx], dtype=np.uint8))
    if cv2.imwrite('/boot/vdma_capture_raw.png', raw):
        print('[驱动自检] 原始字节帧（未做 BGR 转换，2048 宽灰度）已存 '
              '/boot/vdma_capture_raw.png')
    else:
        print('[驱动自检] !! 原始字节帧存盘失败（cv2.imwrite 返回 False）')

    # 连测 9 帧看帧率；传感器若停流，会在这里暴露（帧计数不涨）
    t0 = time.time()
    n = 0
    while n < 9:
        try:
            cap.frame(timeout=60.0)
            n += 1
        except TimeoutError as e:
            print(f'[驱动自检] 第 {n + 1} 帧起无新帧：{e}')
            break
    dt = time.time() - t0
    if n:
        print(f'[驱动自检] 其后连续 {n} 帧平均 {dt / n * 1000:.0f} ms/帧（~{n / dt:.1f} fps）')
    sr, count, halted, err = cap.status()
    print(f'[驱动自检] DMASR=0x{sr:08x} 帧计数={count} halted={halted} err={err}')
    print(f'[驱动自检] DMACR=0x{cap.mmio.read(S2MM_DMACR):08x}'
          f'（循环模式下 bit0 RS 应仍为 1、bit1 应为 1）')
    if halted and not err:
        print('[驱动自检] !!! halted 且无错误位 = 正常停机，非循环模式的典型症状：'
              '查 DMACR 的 bit1(CRCLPRK) 是否被写掉')
    _print_errors(cap)
    print('[驱动自检] 全部 4 块缓冲的内容（数据到底落进哪块、是否真有图像）：')
    _buf_stats(cap)
    cap.stop()


if __name__ == '__main__':
    _selfcheck()
