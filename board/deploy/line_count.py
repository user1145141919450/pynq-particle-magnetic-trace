# -*- coding: utf-8 -*-
"""line_count.py — 精确量出传感器每帧到底送几个 tlast（M1a 收尾）

用法（串口，PYNQ 板上）：
    sudo -E PYTHONIOENCODING=gbk python3 /boot/line_count.py            # 单独跑：下 bit + 量
    sudo -E PYTHONIOENCODING=gbk python3 /boot/line_count.py noprog     # 接力跑：不下 bit

★ 第十三版推荐：传感器状态逐次下载随机（1/5 健康），下载 = 重新掷骰子。
  上板验证链只掷一次骰子，其余 noprog 接力：
    dvp_probe.py（含 ctrl[4] 自动修复）→ stream_probe.py noprog
    → 本脚本 noprog → run_selfcheck.py noprog

为什么要专门量这个（2026-09-13 第九轮收尾）：

  第九轮把顺序问题修掉之后，M1a 出图了 —— 真实画面，能认出场景。
  但有两个残留症状：
    ① 图像上有横向彩色细条纹（品红/青/绿），右半边密集
    ② 采集期间报 fsize_err（DMASR bit7，"帧尺寸不符"），约 2.5% 的帧中招

  两者是同一件事：**VDMA 按 VSIZE=720 行切帧，而传感器实际送的行数不是 720**，
  于是帧边界缓慢漂移，漂到某一拍就对不上，那一帧的若干行被写错位置。

  之前量出来的"721.4 行/帧"**不可信**，因为它用的是
        (窗口内 tlast 增量) / (窗口内 tuser 增量)
  而这个窗口的两端各切在一帧的中间 —— 分子多算了半帧、分母也少算了，
  720 / 721 / 722 全都能落进误差里。反复出现的 721.4 只是说明边界偏差很稳定。

  本脚本改用**对齐到帧边界**的差分：
    1. 死等 tuser 计数器变化 —— 此刻正好落在某帧的帧首
    2. 记下 tlast 读数 L0
    3. 再死等 N 次 tuser 变化
    4. 记下 tlast 读数 L1
    5. **每帧行数 = (L1 - L0) / N**   ← 两端都在帧首，没有半个帧的偏差

  轮询间隔 0.5ms（一行约 46us），检测总落在边沿之后一点点；但两端偏得一样多，
  相减基本抵消。N=60 时剩余误差约 0.1 行 —— 足够把 720/721/722 分开。

  判读（第十二版桥之后）：
    每帧行数恒为 722     -> VSIZE=722 本来就是对的，M1a 验收
    每帧行数恒为其它整数 -> 把 vdma_capture 里的高度改成那个值即可，一次改对
    每帧行数**在变**     -> 固定 VSIZE 无解，必须改用 SOF/EOF 标帧（要改 PL）

★ 21:57 第一次成功跑完的结果（2026-09-13）——比"不是常数"更糟：

    60 帧共 11979 个 tlast，平均 199.7 行/帧，帧率 30.07 fps（传感器本身是好的）。
    逐帧分布：**0 行 x43**，剩下 17 个间隔散在 528~789 之间。
    DMASR=0x00d74990，错误位 bit4|bit7|bit8|bit11。

  这份数据的重点**不是"行数不等于 720"**，而是：
      **60 个间隔里有 43 个连一个 tlast 都没有。**
  健康的流不可能整帧没有行尾。这个形状说明 tuser 在**成串**出现 ——
  前一个边沿刚被 0.5ms 的轮询捕捉到、下一个紧跟着又来了，那次 wait_edge
  就立刻返回、记下 delta=0。即 **IP 在反复丢同步/复位**（溢出 -> vid_reset
  -> 重新对齐），流本身是**碎的**，不是"整齐地少了几行"。

  所以 [步骤 0] 现在不只看绝对速率，更看**三个速率之间的比值**：
      tvalid/tlast 应恒为 2048（每行 1024px x 2B，HSIZE=2048）
      tlast /tuser 应恒为 722 （每帧行数 = DVPVO 720 + 2 条消隐行；桥不丢行）
  比值不对 = 流结构坏了；比值对而帧数不对 = 才是 "VSIZE 填错"。
  这一步专门用来**避免在流已经碎掉时贸然改 PL 做 SOF/EOF 标帧** ——
  那样改完仍然不通，只是白烧一次 bit。

★ 两个上板时踩到的环境坑（2026-09-13，都不是脚本逻辑问题）：

  1. **PL 没配上 → 一读 MMIO 就 `Bus error`。**
     现象：Bitstream.download() 要花 30s（正常 1~2s），随后在任意 PL 地址上
     报 `Unhandled fault: external abort on non-linefetch (0x018)`，进程直接死。
     注意 PTE 里的物理地址就是那个 GPIO（如 0x43a30xxx）—— 说明映射本身是好的，
     是 PL 侧不应答。这是本板 fpga_manager 的已知抽风。**sudo reboot 后重来。**
     （SIGBUS 不是 Python 异常，脚本里 try/except 抓不住，所以写在文档里。）

  2. **源死了但 tvalid 还在抖 → 别被"有数据在动"骗了。**
     现象：tvalid 只有 ~1.4M/s（健康 ~22M/s）且 tuser 恒 0。
     成因：上一次运行中途崩溃，把 ov5640_top 的 304 条 SCCB 配置打断在半路，
     OV5640 的 SCCB 状态机卡住，之后 FPGA 怎么重载都不认。
     **必须给传感器断电**（拔电源重插）—— sudo reboot 不断传感器电，不够。
     脚本的[步骤 0]会把这个状态直接打出来，不再只报一句"没在出帧"。

★ 第十轮定案（2026-09-13）：**双 SOF**，不是回压、也不是传感器。

  tready_ab.py 的 A 相（VDMA 停着、tready 恒 1）也没能复现出干净流，但那次
  `ctrl[3]` 重启视频域时 sink 是停着的，源从复位出来没有 sink 接数据 ——
  实验设计本身有缺陷，A1 源直接死了、A2 把 IP 顶死（tvalid 100% 占空、
  tready 恒 0 两秒）。**那一相不算数。**

  真正定案靠的是把三处参数摆在一起：

    探针侧    s_axis_tuser = 30.00/s（每轮都量到，IP 本来就给 SOF）
    探针侧    m_axis_tuser = s_axis_tuser | sof_armed   ← 相或，每帧多一个
    VDMA 侧   C_S2MM_SOF_ENABLE = 1   tuser 就是帧同步源
              C_USE_S2MM_FSYNC  = 2   fsync 由 tuser 产生
              C_FLUSH_ON_FSYNC  = 1   收到 fsync 就清内部行缓冲

  sof_armed 在 vsync 那拍置起（早于有效像素），IP 的 tuser 在第一个有效像素
  那拍 —— 差一个消隐期，所以不是"同拍相或仍是单脉冲"，而是**实打实的两个 SOF**。
  第二个落在 chnl_ready=0 期间，触发 rfs.vhd:72423 的 SOF_LATE_ERR_PULSE_PROCESS
  -> d_tready_sof_late=1 -> dummy_tready=1 -> **收下数据原地丢掉**
  （rfs.vhd:70563 s2mm_fsize_more_or_sof_late_s）。

  亏损量级完全吻合：104/2048 字节 = 5.1%、34/720 行 = 4.7%，同一个比例。
  对应的 DMASR 三个错误位 bit7 fsize_err | bit8 lsize_err | bit11
  fsize_more_or_sof_late 也全部对得上 —— bit11 的字面意思就是"SOF 来晚了"。

  修法：删掉 rtl/debug/axis_probe.v 里那个相或（已改，第十轮 bit）。
  sof_armed 本身留着，状态字 bit9 仍在观测它。

  判别要点：**每行字节数 < 2048 且每帧行数 < 720，但两者的亏损比例相同**
  -> 是"数据被均匀丢掉"，不是"源变小了"，也不是"帧结构配错"。
  若哪天又看到这个指纹，先查 SOF 是不是给了两次。
"""

import time

GPIO3 = 0x43A30000
GPIO4 = 0x43A40000
GPIO5 = 0x43A50000
GPIO_RANGE = 0x10000

BIT = '/boot/ov5640_m1a_wrapper.bit'
CTRL_VID_RST_BIT = 3

N_FRAMES = 60          # 量多少帧
POLL = 0.0005          # 帧沿检测轮询间隔（秒）


def main():
    import sys
    from pynq import Bitstream, MMIO
    import vdma_capture as vc

    if 'noprog' in sys.argv:
        print('[加载] noprog：跳过 bit 下载，沿用当前已加载的 bit')
        print('[加载]   （勿单独跑 —— 先跑 dvp_probe.py 让它自动修复到健康，再接力本脚本）')
    else:
        Bitstream(BIT).download()
        print('[加载] bitstream 已直下')
        time.sleep(2)

    mm3 = MMIO(GPIO3, GPIO_RANGE)
    mm4 = MMIO(GPIO4, GPIO_RANGE)
    mm5 = MMIO(GPIO5, GPIO_RANGE)

    def w0():   # tvalid 拍数
        return mm4.read(0x00, 4)

    def w1():   # tuser 沿数
        return mm4.read(0x08, 4)

    def w2():   # tlast 沿数
        return mm5.read(0x00, 4)

    # ------------------------------------------------------------------
    print('=' * 70)
    print('[步骤 1] 启动采集链（沿用第九轮验证过的顺序）')
    print('=' * 70)
    cap = vc.S2mmCapture(1024, 720)   # 720 = 实测帧高（曾误用 722）
    cap.start()                                   # ★ start() 内部已按正确顺序重写参数
    c0 = mm3.read(0x00, 4)
    mm3.write(0x00, c0 | (1 << CTRL_VID_RST_BIT))
    time.sleep(0.3)
    mm3.write(0x00, c0 & ~(1 << CTRL_VID_RST_BIT))
    print('    视频域已重启')

    # ------------------------------------------------------------------
    # 步骤 0：先确认"链路是健康的"，再谈测量。
    # ★ 这一段是踩过坑加的（2026-09-13）：脚本第一版只报了一句"源没在出帧"，
    #   但没告诉我们为什么 —— 而"为什么"恰恰是当时最需要的信息。那次是前一次
    #   运行的 bus error 把 ov5640_top 的 SCCB 配置打断在半路，传感器卡住，
    #   表现为 tvalid 以 ~1.4M/s 零碎抖动（健康是 ~22M/s）且 tuser 恒 0。
    #   光看"没在出帧"分不清是"板子没配好""传感器卡住"还是"复位没放开"。
    # ------------------------------------------------------------------
    print()
    print('=' * 70)
    print('[步骤 0] 链路自检（先看源健不健康，再谈测量）')
    print('=' * 70)

    def status_word():
        return mm5.read(0x08, 4)

    def report_health(T=1.5):
        """★ 除了绝对速率，更要看**三个速率之间的比值** ---- 绝对值会随传感器
        模式飘（XCLK 一变全变），比值才是"流结构对不对"的硬指标。
        详见文档里 21:57 那一节：那次 43/60 个间隔一个 tlast 都没有，
        正是"比值不对"而不是"行数不对"。"""
        a0, a1, a2 = w0(), w1(), w2()
        time.sleep(T)
        b0, b1, b2 = w0(), w1(), w2()
        r0 = ((b0 - a0) & 0xFFFFFFFF) / T       # tvalid 拍/秒
        r1 = ((b1 - a1) & 0xFFFFFFFF) / T       # tuser  沿/秒
        r2 = ((b2 - a2) & 0xFFFFFFFF) / T       # tlast  沿/秒
        sw = status_word()
        print(f'    tvalid {r0 / 1e6:8.2f} M/s    tuser {r1:7.2f} /s    '
              f'tlast {r2:9.1f} /s')
        print(f'    状态字 0x{sw:08x}  桥溢出={sw >> 10 & 1} 桥下溢={sw >> 11 & 1} '
              f'见过溢出={sw >> 13 & 1} 见过下溢={sw >> 14 & 1}')
        print(f'                      live: tvalid={sw >> 4 & 1} tlast={sw >> 5 & 1} '
              f'tuser={sw >> 6 & 1} tready={sw >> 7 & 1}')
        bpl = (r0 / r2) if r2 > 0 else 0.0
        rpf = (r2 / r1) if r1 > 0 else 0.0
        print(f'    ★ 每行字节数 = tvalid/tlast = {bpl:9.1f}  （应为 2048 = 1024px x 2B）')
        print(f'    ★ 每帧行数   = tlast /tuser = {rpf:9.1f}  （应为 722 = 720 有效行 + 2 消隐行）')
        return r0, r1, r2, bpl, rpf, sw

    r0, r1, r2, bpl, rpf, sw = report_health()

    # 结构性的毛病 -> 测量无意义，直接停
    fatal = []
    if r1 < 1.0:
        fatal.append('tuser 几乎不出 —— 传感器没在出帧')
    if r2 < 100.0:
        fatal.append('tlast 几乎不出 —— 流里根本没有行尾')
    if r2 > 0 and abs(bpl - 2048) > 64:
        fatal.append(f'每行字节数 {bpl:.0f} != 2048 —— tlast 与 tvalid 不成比例，'
                     f'**流结构是坏的**')
    # ★ 第十轮补的门：行数**量级**不对和行数"对不上 720"是两回事。
    #   1944 字节/行 + 686 行/帧 -> 那是"均匀丢了一点"，本脚本要量的正是它，继续。
    #   2013 字节/行 + 15.2 行/帧 -> 那是流根本没跑起来，比值却照样自洽
    #   （2013*15.2*30 = 0.92M/s，和实测 tvalid 一丝不差），
    #   原来的门只看比值，于是放行了一个正在死掉的链路。
    if r1 >= 1.0 and (r2 / r1) < 100.0:
        fatal.append(f'每帧只有 {r2 / r1:.1f} 行 —— 流根本没跑起来'
                     f'（不是"行数不对"，那是几百行的量级）')

    if fatal:
        print()
        print('    !! 源**不健康**，测量没有意义。逐条：')
        for s in fatal:
            print(f'       - {s}')
        print()
        if (sw >> 13 & 1) or (sw >> 10 & 1):
            print('       ★ 见过/当前 桥写侧溢出=1 —— dvp2axis_bridge 的 FIFO 溢出过。')
            print('         读侧没在取数（VDMA 没 ready）而写侧灌到满；溢出是**结果**')
            print('         不是原因，先查 [步骤 1] 的启动顺序对不对（先 sink 后源）。')
        if r1 < 1.0:
            print('       ★ 传感器没在出帧。第十三版起先跑 dvp_probe.py（ctrl[4] 自动')
            print('         重跑上电时序+SCCB，最多修 3 次）；还不行再断电重插摄像头线')
        if r2 >= 100.0 and abs(bpl - 2048) > 64:
            print('       ★ 若 tvalid 也低（<20M/s）而 tuser 仍是 30/s，则更可能是')
            print('         传感器这次上电起的模式/时钟不对（SCCB 某几条没写进去），')
            print('         可以先 **拔电源** 再试一次，排除偶发。')
        cap.stop()
        return

    print('    链路健康（三个比值都对得上），继续测量')

    # ------------------------------------------------------------------
    print()
    print('=' * 70)
    print(f'[步骤 2] ★ 对齐帧边界，量 {N_FRAMES} 帧的精确行数')
    print('=' * 70)

    def wait_edge(prev, timeout=2.0):
        """死等 tuser 计数器变化，返回 (新计数, 此刻的 tlast 读数)。"""
        t = time.time()
        while True:
            u = w1()
            if u != prev:
                return u, w2()
            if time.time() - t > timeout:
                return None, None
            time.sleep(POLL)

    # 先对齐到第一个帧沿
    u_cur, _ = wait_edge(w1(), timeout=5.0)
    if u_cur is None:
        print('    !! 5s 内没有 tuser 边沿 —— 源没在出帧，检查 LD0 与接线')
        cap.stop()
        return

    # 现在是帧首。取一次基准，然后走 N 帧
    u0 = u_cur
    l0 = w2()
    t_start = time.time()

    deltas = []
    prev_l = l0
    for k in range(N_FRAMES):
        u_cur, l_cur = wait_edge(u_cur)
        if u_cur is None:
            print(f'    !! 第 {k + 1} 帧超时（2s 没有新 tuser 边沿），提前收工')
            break
        # 这一帧的 tlast 增量（上一帧首到本帧首之间有多少个行尾）
        d = (l_cur - prev_l) & 0xFFFFFFFF
        deltas.append(d)
        prev_l = l_cur

    t_elapsed = time.time() - t_start
    if not deltas:
        print('    没量到任何一帧')
        cap.stop()
        return

    total = (prev_l - l0) & 0xFFFFFFFF
    n = len(deltas)
    print(f'    走过 {n} 帧，共 {total} 个 tlast，用时 {t_elapsed:.3f}s')
    print()
    print(f'    ★ 每帧行数 = {total} / {n} = **{total / n:.3f}**')
    print(f'    ★ 帧率     = {n} / {t_elapsed:.3f} = **{n / t_elapsed:.2f} fps**')
    print(f'    ★ 行率     = {total} / {t_elapsed:.3f} = **{total / t_elapsed:.0f} 行/s**')

    # 逐帧分布：这是"是不是常数"的直接证据
    hist = {}
    for d in deltas:
        hist[d] = hist.get(d, 0) + 1
    print()
    print('    逐帧分布（行数 -> 出现次数）：')
    for v in sorted(hist):
        bar = '#' * min(60, hist[v])
        print(f'      {v:>5} 行  x{hist[v]:<4} {bar}')

    # ------------------------------------------------------------------
    print()
    print('=' * 70)
    print('[步骤 3] 同一窗口里 VDMA 完成了多少帧（对照）')
    print('=' * 70)
    sr = cap.mmio.read(vc.S2MM_DMASR)
    fc_end = (sr >> vc.SR_FRMCNT_SHIFT) & 0xFF
    print(f'    DMASR=0x{sr:08x}  阈值倒计数={fc_end}  halted={bool(sr & 1)}  '
          f'err={bool(sr & vc.SR_ERR_MASK)}')
    print(f'    （倒计数每完成一帧减 1；本窗口 {t_elapsed:.2f}s 内的变化量即完成帧数）')
    if sr & vc.SR_ERR_MASK:
        print(f'    错误位：{vc.decode_sr(sr)}')

    print()
    print('=' * 70)
    print('[结论]')
    print('=' * 70)
    avg = total / n
    vals = set(deltas)

    # ★ 成串检测：这一条比"行数是不是常数"更重要（见 [步骤 0] 上方注释）。
    #   健康的流里 tuser 每 33ms 一个，0.5ms 的轮询绝不可能在相邻两次检测之间
    #   又落进一个边沿 -> delta 不可能为 0。delta=0 只能说明 tuser 在成串来。
    n_zero = hist.get(0, 0)
    if n_zero > n * 0.25:
        print(f'    !! {n_zero}/{n} 个间隔里**一个 tlast 都没有** —— 这不是"行数不对"。')
        print('       健康的流不可能整帧没有行尾。这个形状说明 tuser 在**成串**出现')
        print('       （前一个边沿刚被轮询抓到、下一个紧跟着又来了，wait_edge 立刻返回），')
        print('       也就是 IP 在反复丢同步/复位，**流本身是碎的**。')
        print('       先回 [步骤 0] 看 溢出/下溢 与 每行字节数，别急着改 PL 做标帧。')
        print()

    if len(vals) == 1:
        v = deltas[0]
        print(f'    ★★ 每帧行数是**恒定**的 {v} 行。')
        if v == 722:
            print('    ★★★ M1a 采集链**完全正确**。')
            print('    2048 字节/行 + 722 行/帧 + 30.00 fps —— 三个数全部对得上，')
            print('    且第十二版桥不再丢那 1/20 数据（v_vid_in_axi4s 的均匀亏损消失）。')
            print('    再确认一下 [步骤 0] 的三个比值也都对、且无溢出/下溢，')
            print('    M1a 就可以验收，直接进 M1b（HDMI 显示链）。')
            print()
            print('    （若行数=722 但每行字节数仍偏离 2048，或仍有溢出，')
            print('      那是桥的丢行门限在起作用 —— 查 VDMA 启动顺序/HSIZE。）')
        else:
            print(f'    把 vdma_capture 里的高度从 722 改成 {v} 即可。')
            print('    （BD 侧不用动；OV5640 的 DVPVO 是 720，多出来的是消隐行）')
    else:
        print(f'    ★★ 每帧行数**不是常数**：{sorted(vals)}')
        print(f'    平均 {avg:.3f}，最小 {min(deltas)}，最大 {max(deltas)}。')
        print()
        print('    这意味着**固定 VSIZE 无论填多少都会错**，必须改成按 SOF/EOF 标帧。')
        print('    方案：BD 里把 vdma 的 S2MM tuser 位宽改成 2（tuser[0]=SOF,')
        print('    tuser[1]=EOF），axis_probe 在每帧最后一个 tlast 那拍拉 EOF ——')
        print('    帧边界就由流自己标定，与行数无关。这需要重新综合 bit。')

    print()
    cap.stop()


if __name__ == '__main__':
    main()
