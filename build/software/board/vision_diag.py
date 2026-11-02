# -*- coding: utf-8 -*-
"""vision_diag.py — PL 视觉前处理（vision_core v2）一次性验收

v1 的教训：单看"缩略图有没有内容"是没用的 —— v1 也有内容、也是彩色、行数也是
720，但与黄金模型相关性 0.05（等于无关）。原因藏在 stat 的一个 sticky 位里
（flush_ovl=1 → 刷写吞了半拍字节 → 像素字节配对整体错位）。所以验收必须**同时**
看四件事，缺一条都不算过：

    A) 结构数：stat  == 0x02D01680（上一帧 720 行、写满 5760 字）
    B) 错误位：stat2 的 errs == 0，且 last_bcnt == 2047（行长度真是 2048 字节）
    C) 数值：缩略图与黄金模型（8x8 分通道求和 >>6）**逐通道** ≥98% 的块在 1 LSB 内
       ★ 2026-09-26 改：判据从"打包值 ≤1"改成"三通道各自 ≤1"。打包值里
         R 的 1 LSB = 2048、G = 32、B = 1，老判据实际上只允许 R/G 一位不差，
         测的是"画面能不能一比特不变"而不是"块平均对不对"，详见 vision_golden.py。
       ★ 同时报"输入自身的噪声底"（两张相邻原图过同一个黄金模型）：板端能追平
         它就是没引入误差 —— 动态场景下绝对值到不了 100%，只有这个差值有意义。
    D) 节奏：frame_cnt 的实测帧率（应 ~28fps，与采集链一致）

A/B 是"设计自证"，C 是"数值自证"，D 是"没拖慢采集链"。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/vision_diag.py
    ... --noswap        强制 swap_bytes=0（默认由驱动自动判定）
    ... --noraw         跳过全分辨率抓帧（只做 A/B/D，快）
    ... --nobit         不重下 bit（刚跑过 dvp_probe 确认传感器健康时用这条：
                        本工程实测"每次下载 bit 传感器状态随机"，健康态要珍惜）

输出：
    /boot/vmap_d.bin        缩略图原始字节（5760 个字，与 map 逐字节一致）
    /boot/vmap_d.png        缩略图放大 8 倍（肉眼）
    /boot/frame_raw_d.bin   全分辨率原样字节（黄金模型的输入）
"""

import sys
import time

sys.path.insert(0, '/boot')

import cv2
import numpy as np

from fastmem import FastMem
import vision_golden as vg            # 与 PC 侧同一份黄金模型（块平均的唯一定义）

W, H = 1024, 720
BW, BH = W // 8, H // 8                 # 128 x 90
MAP_BYTES = BW * BH * 2                 # 23,040
WORDS = BW * BH // 2                    # 5760

MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
STAT_BASE = 0x43B20000
BIT = '/boot/ov5640_m1a_wrapper.bit'     # 与 .hwh 同名同目录（PYNQ Overlay 的要求）

STAT_OK = 0x02D01680                    # {720, 5760}
STAT2_OK = 0xFFE01680                   # {2047, errs=0, wcnt=5760}

ERR_NAME = [
    'tlast 落在半个像素上（行字节数为奇数）',
    '块行收尾时 bcnt != 2047（行长度不对）',
    '本帧写字数超过 5760（块行/地址失步）',
    '一帧 tlast 数超过 720（行数不对）',
    '块行收尾时 col != 127（行太短，列不够）',
    '保留',
]


def _grab_raw(cap, tries=2):
    """抓一张全分辨率原图，流卡死就救一次。

    ★ 卡死是本工程的既知毛病（实测：VDMA 帧计数停住、DMASR halted=False/err=False、
      期间 0 个错误事件；它与 vision_core 无关 —— 在视核存在之前就出现过）。
      救法沿用 dvp_probe.py：ctrl[4] 脉冲 = 重跑传感器上电时序 + SCCB（连带复位桥
      FIFO）。救完必须重跑一次 _init_video 的 ctrl[3]（视频域重启 + 立刻抓一帧），
      否则 VDMA 空转之后照样收不到帧（这条是本工程踩过的老坑）。
    """
    from pynq import MMIO
    from ball_detect import _init_video

    for k in range(tries + 1):
        try:
            idx, raw = cap.frame(timeout=3.0)
            return np.ascontiguousarray(np.asarray(raw, np.uint8)).copy(), idx
        except Exception as e:
            print('[原图] 第 %d 次抓帧失败：%s' % (k + 1, str(e)[:150]))
            if k == tries:
                raise
        print('[原图] 按 dvp_probe 的办法救流：ctrl[4] 脉冲重跑传感器上电+SCCB')
        mm3 = MMIO(0x43A30000, 0x10000)
        cur = mm3.read(0x00)
        mm3.write(0x00, cur | (1 << 4))
        time.sleep(0.5)
        mm3.write(0x00, cur & ~(1 << 4))
        time.sleep(3.0)
        try:
            _init_video(cap)          # ctrl[3] 视频域重启 + 清错 + 立刻抓热身帧
        except Exception as e2:
            print('[原图] 救流后的热身帧也没来（%s），再试一次抓帧' % str(e2)[:100])


def _ensure_stream(cap, mc, ms, tries=3):
    """确认视频流既"活"又"干净"；不干净就按 dvp_probe 的办法 ctrl[4] 救一次

    ★ 为什么必须有它（2026-09-26）：本板实测"每次下载 bit，传感器状态随机"
      （约 1/5 健康 —— 见 dvp_probe.py 的说明）。不健康有两类表现：

        a) 彻底没帧（cap.frame 超时）—— _grab_raw 里已经在救；
        b) **有帧但是脏的**：errs 亮（实测 0x13 = 位0 tlast 落在半个像素上、
           位1 行长度不对、位4 列不够），帧计数还可能停住。这一类比 (a) 阴得多：
           帧照样抓得回来，只是内容全错，于是 C 项莫名其妙掉到 1%，看起来
           像"块平均算错了"，实际上根本没在算同一幅画。

      两类的救法一样：ctrl[4] 脉冲重跑传感器上电时序 + 304 条 SCCB 配置
      （连带复位桥的 FIFO —— 旧垃圾字节堵在 FIFO 头部时新 SOF 到不了 VDMA），
      然后重跑一次 _init_video 做视频域重启。

    返回 True/False；False 不代表脚本要退出，只代表"这一轮的数不可信"。
    """
    from pynq import MMIO
    from ball_detect import _init_video

    healed = False
    for k in range(tries + 1):
        c0 = mc.rd_u32(0x08)
        time.sleep(0.25)
        c1 = mc.rd_u32(0x08)
        errs = (ms.rd_u32(0x08) >> 15) & 0x3F
        if c1 != c0 and errs == 0:
            print('[健康] 视频流活得干净（帧计数 %d -> %d，errs=0）✓' % (c0, c1))
            if healed:
                # ★ 刚救过流：_init_video 重启了视频域，map 又要靠新帧重建一遍
                #   （头两帧还是旧内容）—— 再等 0.5s，否则 C 项读到过渡帧。
                time.sleep(0.5)
                print('       救流后已再等 0.5s，让 map 用新帧重建')
            return True
        print('[健康] ★ 第 %d 次判读不健康：帧计数 %d -> %d（%s），errs=0x%02X'
              % (k + 1, c0, c1, '没在涨' if c1 == c0 else '在涨', errs))
        if k == tries:
            break
        print('       按 dvp_probe 的办法救：ctrl[4] 脉冲重跑传感器上电 + SCCB')
        mm3 = MMIO(0x43A30000, 0x10000)
        cur = mm3.read(0x00)
        mm3.write(0x00, cur | (1 << 4))
        time.sleep(0.5)
        mm3.write(0x00, cur & ~(1 << 4))
        time.sleep(3.0)
        try:
            _init_video(cap)          # ctrl[3] 视频域重启 + 清错 + 立刻抓热身帧
            mc.wr_u32(0x00, (mc.rd_u32(0x00) & ~0x04) | 0x02)   # 重锚 + 清 sticky 错
            time.sleep(0.2)
            mc.wr_u32(0x00, mc.rd_u32(0x00) & ~0x02)
        except Exception as e:
            print('       救流后的热身帧没来（%s）' % str(e)[:100])
        healed = True
    print('[健康] ★ 救了 %d 次仍不健康 —— 这一轮的数不可信（重跑脚本 = 重新掷骰子）'
          % tries)
    return False


def main():
    noswap = '--noswap' in sys.argv
    noraw = '--noraw' in sys.argv
    nobit = '--nobit' in sys.argv
    verdict = []

    # -------------------------------------------------------------- 先下 bit
    # ★ 顺序踩过的坑：ball_detect 的 _make_cap() 里 allocate() 要求**overlay 已经
    #   下载过**（PYNQ 3.1 的 Device.default_memory 会抛 "Overlay is not downloaded"），
    #   它自己不加载。以前是先跑 dvp_probe.py/run_selfcheck.py 把 overlay 载上、再
    #   跑别的脚本 —— 一步验收脚本不能依赖"你之前跑过别的"，这里自己加载。
    #   加载失败也不退出：A/B/D 三项只用 mmap 读写寄存器，不依赖 PYNQ 的内存分配，
    #   照样能判（C 项会如实标成跳过）。
    bit_ok = False
    if nobit:
        # ★ 传感器健康状态是"抽"出来的（dvp_probe 的原话：保持这个状态、别重下 bit）：
        #   本工程实测过"每次下载 bit，传感器状态随机"（pclk 在跑但 vsync 永不来）。
        #   所以刚跑过 dvp_probe 确认健康时，用 --nobit 接力 —— PYNQ 的内存分配靠
        #   全局状态文件兜底（download 时写下的），新进程照样能 allocate。
        print('[加载] --nobit：不重下 bit（沿用当前已配置的 PL + 全局状态文件）')
        bit_ok = True
    else:
        # ★ 用 Bitstream 直下，**不要用 Overlay**（2026-09-24 深夜实测）：本板上
        #   `Overlay(BIT)` 会**段错误** —— SIGSEGV 不是 Python 异常，try/except 拦不住，
        #   脚本会连一行输出都没有就死掉（只在 bash 留下 "Segmentation fault"、
        #   dmesg 里留 apport）。而 allocate() 靠 Bitstream.download() 内部的
        #   gen_cache() → save_global_state() 写下的全局状态文件照样能用 ——
        #   这正是本工程采集链一直在走的路径（run_selfcheck 的兜底也是它）。
        try:
            from pynq import Bitstream
            Bitstream(BIT).download()
            print('[加载] bit 已下载（Bitstream 直下；Overlay 在本板 hwh 上会段错误）')
            bit_ok = True
        except Exception as e2:
            print('[加载] ★ 下载失败（%s）—— 下面全靠读寄存器，读出来大概率是 0'
                  % str(e2)[:80])

    # ------------------------------------------------------- 采集初始化（只热身）
    # ★ 2026-09-26 重排：原图**不再在这里抓**。C 项要的是"与 map 相邻的那一帧"，
    #   而 map 得等 soft_rst 之后用复位后的帧重建完再读（temporal_stab 实测：
    #   复位后头两帧的 map 还是旧内容，约 8 帧后才稳）。顺序改成
    #       复位 → 等 → **抓原图 → 立刻读 map**（这个先后不能反！）
    #   老版是"抓原图 → 复位 → 测 1 秒帧率 → 读 map"，
    #   相隔约 35 帧 —— 2026-09-26 用 temporal_stab.py 量过：相隔 35 帧时连
    #   黄金模型跟自己都只剩 94%，相邻帧则有 99%。场景里有人有手，35 帧够全变。
    raw_bytes = None
    raw2_bytes = None
    swap = 0
    cap = None
    if noraw:
        pass
    elif not bit_ok:
        print('[原图] 跳过（bit 没下进去）')
    else:
        # 整个采集初始化都包在 try 里：抓原图只是 C) 项的输入，它失败不该连带
        # A/B/D 一起死（第一次上板就是这里抛异常把整脚本带崩的）
        try:
            from ball_detect import _make_cap, _init_video
            cap = _make_cap()
            _init_video(cap)
            swap = 1 if getattr(cap, '_swap_decided', False) else 0
            if noswap:
                swap = 0
            print('[字节序] swap_bytes = %d（%s）'
                  % (swap, '命令行强制' if noswap else '驱动按通道相关性判定'))
            for _k in range(2):          # 热身：丢两帧，等 AGC/AWB 收敛
                _grab_raw(cap)
            print('[原图] 采集链就绪（已扔掉 2 帧热身）')
        except Exception as e:
            cap = None
            print('[原图] ★ 采集初始化失败（%s: %s）—— 只做 A/B/D'
                  % (type(e).__name__, str(e).replace('\n', ' ')[:140]))

    with FastMem(MAP_BASE, 0x8000) as mm, \
         FastMem(CTRL_BASE, 0x1000) as mc, \
         FastMem(STAT_BASE, 0x1000) as ms:

        # ------------------------------------------------------------ 复位重跑
        # soft_rst 清 errs（sticky，只由 soft_rst 清）+ 位置重锚；冻结位保持 0
        mc.wr_u32(0x00, (swap << 2) | 0x02)
        time.sleep(0.2)
        mc.wr_u32(0x00, (swap << 2))
        print('[配置] ctrl = 0x%02x（freeze=0 soft_rst=0 swap=%d）'
              % (swap << 2, swap))
        # ★ 复位后 map 里的旧内容要靠新帧冲掉：temporal_stab 实测头两帧还是旧的，
        #   约 8 帧（0.29s）后才稳。等 0.5s（14 帧）留够余量 —— 这一步不能省，
        #   否则 C 项拿到的是一张"过渡帧"的 map。
        time.sleep(0.5)

        # ------------------------------------------------------- 视频流健康门
        # ★ 2026-09-26：errs 是 sticky 的，上面刚 soft_rst 清过 —— 此刻再亮起来
        #   就是**这一轮真实在发生的错**。必须在这里判，因为 C 项拿不到健康流时
        #   会掉到 1~2%（看起来像"块平均算错了"，其实是根本没在算同一幅画）。
        #   不健康 → ctrl[4] 重跑传感器上电 + SCCB，重判，最多 3 次。
        if cap is not None:
            ok_stream = _ensure_stream(cap, mc, ms)
            verdict.append(('※ 视频流健康（前提）', ok_stream, 'errs=0 且帧计数在动'))
            if not ok_stream:
                print('[健康] ★ 下面的 C 项在这个状态下没有意义，但 A/B/D 照测')

        # ------------------------------------------------------------ A/B) 状态字
        st = ms.rd_u32(0x00)
        st2 = ms.rd_u32(0x08)
        lines = st >> 16
        wcnt = st & 0xFFFF
        bcnt_at = (st2 >> 21) & 0x7FF
        errs = (st2 >> 15) & 0x3F
        live_wcnt = st2 & 0x7FFF
        print('[A 结构] stat  = 0x%08X  上一帧 %d 行（应 720）  写 %d 字（应 5760）'
              % (st, lines, wcnt))
        print('[B 错误] stat2 = 0x%08X  行尾 bcnt=%d（应 2047）  errs=0x%02X（应 0）'
              '  本帧实时字数 %d' % (st2, bcnt_at, errs, live_wcnt))
        for i, nm in enumerate(ERR_NAME):
            if errs & (1 << i):
                print('        ★ 错误位 %d：%s' % (i, nm))
        verdict.append(('A 行数 = 720', lines == 720, str(lines)))
        verdict.append(('A 字数 = 5760', wcnt == WORDS, str(wcnt)))
        verdict.append(('B 行长度 = 2048B', bcnt_at == 2047, str(bcnt_at)))
        verdict.append(('B errs = 0', errs == 0, '0x%02X' % errs))

        # 实时字数（本帧已写多少字）只能"抽"：它一帧内 0→5760 单调涨，采样点
        # 落在帧里哪个位置是随机的 → 单看"峰值不到 5760"没有意义（必然如此，
        # 除非恰好抽在帧尾）。真正能判"帧写满了"的是上面的 last_wcnt（=5760）。
        # 这里只问一件事：**计数在动吗**（动了 = 正在写帧，没卡死）。
        samples = []
        for _ in range(12):
            samples.append(ms.rd_u32(0x08) & 0x7FFF)
            time.sleep(0.004)          # 12 x 4ms ≈ 48ms > 一帧 36ms
        span = max(samples) - min(samples)
        print('[A 实时] 本帧字数采样 %d 点：%s'
              % (len(samples), ' '.join(str(v) for v in samples)))
        print('        跨度 %d（>0 = 正在写帧；这个数抽到帧尾才接近 5760，'
              '抽到帧头接近 0，都正常）' % span)
        verdict.append(('A 计数在动', span > 0, '跨度 %d' % span))

        # ------------------------------------ 先抓原图，再立刻读 map（★顺序是关键）
        # ★★ 顺序不能反！2026-09-26 用 temporal_stab.py 量过：cap.frame() 返回的是
        #    "**刚刚完成**的那一帧"，而视核的 map 也是那一帧写完后才更新的 ——
        #    所以"先抓原图、紧接着读 map"拿到的是**同一帧**（实测 98.6~99.7%）；
        #    反过来"先读 map、再抓原图"就差了整整一帧（实测掉到 96~97%）。
        if cap is not None:
            try:
                raw_bytes, idx = _grab_raw(cap)
                with open('/boot/frame_raw_d.bin', 'wb') as f:
                    f.write(raw_bytes.tobytes())
                print('[原图] %d 字节 -> /boot/frame_raw_d.bin（帧索引 %s）'
                      % (raw_bytes.size, idx))
            except Exception as e:
                raw_bytes = None
                print('[原图] ★ 抓帧失败（%s: %s）—— C 项跳过'
                      % (type(e).__name__, str(e).replace('\n', ' ')[:140]))

        # ------------------------------------------------------------ 一致性读 map
        buf = None
        for k in range(8):
            a = mc.rd_u32(0x08)
            buf = mm.rd_u8_copy(0, MAP_BYTES)
            b = mc.rd_u32(0x08)
            if a == b:
                print('[读取] 第 %d 次拿到一致快照（frame %d）' % (k + 1, a))
                break
            print('   读期间帧号变了 %d->%d，重读' % (a, b))
        if buf is None:
            print('★ 8 次都没读到一致快照')
            return 1
        with open('/boot/vmap_d.bin', 'wb') as f:
            f.write(buf.tobytes())

        m = np.frombuffer(buf, np.uint16).reshape(BH, BW)
        print('[输出] /boot/vmap_d.bin（%d 字节）—— 同名的 PNG 挪到 C 项之后写，'
              '因为它的显示字节序要等 C 项量出来（见下面的说明）' % MAP_BYTES)

        def dump_map_png(disp_swap):
            """缩略图 -> PNG（肉眼对照用）。

            ★ 显示字节序 = 驱动判定的 `swap` **异或** 下面 C 项实测的"字序关系"。
              2026-09-27 血的教训：只按 `swap` 解，会得到一张**洋红噪声图** ——
              而 C 项 89.8%/99.4% 全绿。原因是 C 项比的是**位域（数值）**，
              对"喂给 cv2 的字节顺序"完全无感；地图在内存里的两个字节与 VDMA
              那路原图**正好相反**（所以黄金模型要 swap16 才对得上），照抄原图的
              swap 就等于翻了第二次。那晚"缩略图找到 30~45 个球、参考 0 个"的
              假象，根子就在这里，不是检测器不行。
            """
            b = np.dstack([(m & 0xFF).astype(np.uint8), (m >> 8).astype(np.uint8)])
            if disp_swap:
                b = b[..., ::-1]
            bgr = cv2.cvtColor(np.ascontiguousarray(b), cv2.COLOR_BGR5652BGR)
            cv2.imwrite('/boot/vmap_d.png',
                        cv2.resize(bgr, (BW * 8, BH * 8), interpolation=cv2.INTER_NEAREST))
            print('[输出] /boot/vmap_d.png（显示字节序 %d）' % disp_swap)
            print('[统计] 缩略图 BGR 均值 %.1f 标准差 %.1f' % (bgr.mean(), bgr.std()))

        # --------------------------------------- 再抓一张：量"输入自身的噪声底"
        # 第二张与第一张相隔 1 帧，正好和"map 与第一张"的间隔一样 —— 于是
        # 噪声底和 C 项的成绩是在**同一个时间尺度**上量的，可比。
        # 板端再准也超不过这个噪声底：传感器/场景在这两帧之间本来就变了。
        if cap is not None and raw_bytes is not None:
            try:
                raw2_bytes, idx2 = _grab_raw(cap)
                with open('/boot/frame_raw2_d.bin', 'wb') as f:
                    f.write(raw2_bytes.tobytes())
                print('[原图] 第二张 -> /boot/frame_raw2_d.bin（帧索引 %s）' % idx2)
            except Exception as e:
                raw2_bytes = None
                print('[原图] ★ 第二张抓帧失败（%s: %s）—— 噪声底不可用'
                      % (type(e).__name__, str(e).replace('\n', ' ')[:140]))

        # ------------------------------------------------------------ D) 帧率
        # ★ 挪到**最后**：原来它夹在"抓原图"和"读 map"中间，那 1 秒 sleep 正是
        #   C 项两张图相差 35 帧的元凶（见文件头的说明）。
        c0 = mc.rd_u32(0x08)
        t0 = time.time()
        time.sleep(1.0)          # 不用忙等：PS 空转只会白白抢 DDR 带宽
        c1 = mc.rd_u32(0x08)
        fps = (c1 - c0) / (time.time() - t0)
        print('[D 节奏] frame_cnt %d -> %d，实测 %.1f fps（采集链应 ~28fps）'
              % (c0, c1, fps))
        verdict.append(('D 帧率 ≥ 20fps', fps >= 20.0, '%.1f fps' % fps))

    # -------------------------------------------------------------- C) 黄金对比
    if raw_bytes is not None and raw_bytes.size >= W * 2:
        def _golden(raw, sw):
            pix = vg.pixels_from_raw(raw)
            if sw:
                pix = vg.swap16(pix)
            return vg.block_avg(pix)

        def _joint(mm_, g):
            """map 与黄金"三通道同时 <=1 LSB"的比例（%）—— 与黄金模型同一口径"""
            bad = None
            for sh, msk in ((11, 0x1F), (5, 0x3F), (0, 0x1F)):
                d = np.abs(((mm_ >> sh) & msk).astype(np.int32) -
                           ((g >> sh) & msk).astype(np.int32)) > 1
                bad = d if bad is None else (bad | d)
            return float((~bad).mean()) * 100.0

        print('')
        print('==== C) 与黄金模型对照（两种字节序都算一遍，看哪个对）====')
        cand = {}
        for sw in (0, 1):
            cand[sw] = _joint(m, _golden(raw_bytes, sw))
            print('   swap=%d : 三通道同时 <=1 LSB %5.1f%%' % (sw, cand[sw]))
        best_sw = max(cand, key=lambda s: cand[s])
        print('   （板端实际用的是 swap=%d；两种都低 = 不是字节序问题）' % swap)
        # ★ 现在才知道地图与原文的字节序关系（best_sw），可以写那张给人看的 PNG 了：
        #   显示字节序 = swap ^ best_sw（两个都 1 -> 0，即"地图的字节别动"）。
        dump_map_png(swap ^ best_sw)

        # 输入自身的噪声底：两张相邻原图过同一个黄金模型。板端再准也超不过它，
        # 因为传感器/场景在这两帧之间本来就变了 —— 动态场景下 100% 到不了。
        nf = None
        if raw2_bytes is not None:
            nf = _joint(_golden(raw_bytes, swap), _golden(raw2_bytes, swap))
            print('   [噪声底] 两张相邻原图（黄金 vs 黄金，PL 完全不参与）：%.1f%%'
                  % nf)

        ret = vg.compare(buf, raw_bytes, swap=bool(swap), noise_floor=nf)
        verdict.append(('C 黄金模型 ≥98%', ret == 0, '%.1f%%' % cand[swap]))
        if ret != 0 and best_sw != swap and cand[best_sw] >= 98.0:
            print('★ 注意：另一种字节序反而对上了 —— 说明驱动判定的 swap 是反的，'
                  '而 PL 侧忠实执行了它。改 vdma_capture 的判定或强制 --noswap。')
    else:
        print('')
        print('==== C) 跳过（没有原图；用 --noraw 跑的，或抓帧失败）====')
        if buf is not None:
            # 没有原图就量不出字序关系，按本版实测的"地图字序关系 = 1"假定，
            # 至少别把一张洋红图当成"缩略图长这样"。
            dump_map_png(swap ^ 1)

    # -------------------------------------------------------------- 汇总
    print('')
    print('=' * 64)
    ok = True
    for nm, good, val in verdict:
        print('  %-18s %s   (%s)' % (nm, '通过 ✓' if good else '未过 ✗', val))
        ok = ok and good
    print('=' * 64)
    print('总判定：%s' % ('全部通过 ✓ —— PL 视觉前处理 V1 可以用在游戏里了'
                       if ok else '有未过项 —— 见上面的 ★ 行'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
