# -*- coding: utf-8 -*-
"""stall_probe.py — 接上板上**已有的全套寄存器探针**，一次把"卡死时谁先死"钉死

★ 为什么要用它（2026-09-26 深夜）：
  frame_mon.py 已经证明：**没人碰它、单进程、链路自己在 ~10s 内死掉**，而且
  "冻结 -> 自己恢复 -> 再冻结"反复出现，最后永久冻结。但 frame_cnt 一个数
  没法回答**是哪一节先死的** —— 传感器停钟？桥的 FIFO 死锁？VDMA 不消费？
  视核不干活？这四种在 frame_cnt 上长得一模一样。

  这块板子上其实早就埋了完整的探针（第十二~十五版排障时加的），只是没人
  把它们**同时**读过。本脚本就是把它们接起来，每秒采一次，看**谁先不动**。

★ 探针全图（地址取自 deploy/ov5640_m1a_wrapper.hwh 的 MEMRANGE，权威）：

  传感器域（100M axi_clk 域计数，信号 2FF 同步过来）：
    probe0 0x43A00000  +00 = pclk/64 上升沿   <- ★ PCLK 活没活（最上游）
                       +08 = vsync 上升沿
    probe1 0x43A10000  +00 = href 上升沿      <- 行有效
                       +08 = scl 上升沿       <- SCCB 时钟有没有在动
    probe2 0x43A20000  +00 = sda 上升沿
                       +08 = 电平字 w5：
                             bit0   locked
                             [8:1]  见过1（数据线 7:0 见过高电平的位）
                             [16:9] 见过0（见过低电平的位）★ 两个都 0xFF = 数据线在翻转
                             bit17  xclk 实际电平   bit18  pwdn 实际电平
                             bit19  rst_n 实际电平
    probe3 0x43A30000  +00 = ctrl 控制字（**可写**，见下）
                       +08 = xclk 翻转载数   <- 20ms 就能看出 XCLK 活没活

  流侧（aclk 域，桥 -> VDMA 之间）：
    probe4 0x43A40000  +00 = tvalid 拍数       <- 桥读侧有没有在出数据
                       +08 = tuser 上升沿数    <- 帧首，健康 ~29.5/s
    probe5 0x43A50000  +00 = tlast 上升沿数    <- 行尾，健康 ~21600/s
                       +08 = 状态字 w3：
                             bit0..3 见过 tvalid/tuser/tlast/tready
                             bit4..7 live tvalid/tlast/tuser/tready
                             bit10 live 桥写侧溢出   bit11 live 桥读侧下溢
                             bit13/14 见过溢出/下溢
                             bit15 真送给 VDMA 的 m_axis_tuser 电平
                             [31:28] 桥写侧内部：sof_armed / dropping /
                                     wr_rst_busy / rst_pclk   ★★ 这四位是钥匙
    probe6 0x43A60000  +00 = 桥账本影子（单通道输入），由 ctrl[9:7]=dbg_sel 选：
                             sel=0 href 拍数   sel=1 push 数   sel=2 带 eol 的 push
                             sel=3 vsync 数    sel=4 溢出数    sel=5 读侧读出数
                             sel=6 读侧 eol 数
                             **sel=7 = FIFO 内部状态，实时不走快照**（第十五版）：
                               [13:0] wr_data_count 水位（>4096 丢行风暴 / 近 8192 快满）
                               [14]   full         [15] wr_rst_busy
                               [16]   empty        [17] rd_rst_busy
                               [18]   sof_armed    [19] dropping
                               **[31]  wdt_lat —— FIFO 水位看门狗触发过（粘滞）**

  视觉域：
    0x43B10000  +00 = ctrl（bit0 freeze / bit1 soft_rst / bit2 swap）
                +08 = frame_cnt  <- 只有**完整帧收尾**才 +1（与 stat 同一拍写入）
    0x43B20000  +00 = stat  = {上一帧行数, 上一帧写字数}  健康 **0x02D01680**
                +08 = stat2 = {last_bcnt[10:0], errs[5:0], 本帧字数[14:0]}

  ★ 注意 frame_cnt 和 stat 是**同一拍**写的（vision_core.v:310-312），所以
    "frame_cnt 在涨但 stat=0" 是不可能的 —— 那只能是**读错了基址**。
    （frame_mon.py 第一版就犯了：stat 要从 0x43B20000 读，不是 0x43B10000+0。）

★ 判读表（谁先停 = 凶手在哪一节）：
    pclk/64 停          -> 传感器**停钟了**（供电/接触/传感器自身）
    pclk 活、href 停    -> 传感器在出时钟但没出行有效（传感器内部状态坏）
    href 活、tuser/tlast 停 -> 桥的写侧/水银位挡了（看 probe5[31:28] + 水位）
    水位顶满 + sof_armed 举着 + wdt_lat=1 -> **FIFO 死锁**（文档里的第十六版病历）
    以上全活、frame_cnt 停 -> 视核没收到完整帧（字节相位/列数不对）
    全活且 frame_cnt 也在涨    -> 链路是好的，"卡死"发生在 PS/读回那一段

★ 恢复动作（**本脚本能自己动手**，不用重烧 bit）：
    --vidrst-at T   在 t=T 秒脉冲 ctrl[3]（强制复位视频域）。
                    ★ 最该先试这一个：vid_rst_ctrl.v 文件头记着 2026-09-13 量到过
                    **一模一样**的症状（传感器健康 vsync/href/pclk 都在跳，流侧
                    tvalid/tuser/tlast 全 0），根因是异步 FIFO 带着非法初值开跑、
                    写侧一开动就判满、溢出、把视频域永久锁死。ctrl[3] 就是为这种
                    "残留死锁"留的软件后门。
    --senrst-at T   脉冲 ctrl[4]（force_sensor_rst）：重跑传感器上电时序 + SCCB，
                    也就是不重烧 bit 再掷一次传感器状态的骰子（第十三版保底手段）。
    --pwr-at T      更狠：ctrl[1] 强制 pwdn + ctrl[0] 停 xclk，停 0.5s 再放开，
                    然后跟着来一发 senrst。
  这两个动作都在**同一个进程、同一条采集链**上做 —— 所以"恢复成功了"这件事
  是干净的：没有新进程、没有 zocl 重载、没有重下 bit 这些混淆项。

用法（板上）：
  sudo -E PYTHONIOENCODING=utf-8 python3 -u /boot/stall_probe.py [--n 150] [--period 1] \
       [--cold | --nobit] [--vidrst-at 60] [--senrst-at 60] [--pwr-at 60]
  ★ 全程只有这一个 PYNQ 客户端（项目硬规矩：绝不同时跑两个 VDMA 客户端）。
  ★★ **断过电就用 --cold**（直接下 bit，不探测）；**本会话里刚跑过链就用 --nobit**
     （绝不下）。说不清才用默认的 pl_probe 探测——它自己也有把板子读挂的可能，
     见下面"两次挂机"的记录。**推荐姿势：刚上电 -> --cold 起一轮长的；之后
     一律 --nobit 接力，再也不下 bit。**
"""

import subprocess
import sys
import time

sys.path.insert(0, '/boot')

from pynq import MMIO

P0, P1, P2, P3 = 0x43A00000, 0x43A10000, 0x43A20000, 0x43A30000
P4, P5, P6 = 0x43A40000, 0x43A50000, 0x43A60000
CTRL, VSTAT = 0x43B10000, 0x43B20000

DBG_SEL_SHADOW7 = 7


def _d(a, b):
    """32 位自由计数器求增量，自动处理回绕"""
    return (b - a) & 0xFFFFFFFF


def pl_state():
    """跑项目自带的 pl_probe.py，把它的退出码当结论（**下 bit 前的强制前置**）

    为什么复用子进程而不是把逻辑抄一遍：pl_probe.py 是踩了四个坑之后写出来的
    （SIGBUS 与 SIGSEGV 必须分开、开机默认 bit 会让 state 一直 operating、
    读不存在的外设会 SIGBUS 要放子进程隔离、没结论时一律不下）。抄一遍就等于
    把这四个坑重新踩一遍。这里只要退出码。
    """
    try:
        r = subprocess.run([sys.executable, '-u', '/boot/pl_probe.py'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=200)
    except subprocess.TimeoutExpired:
        print('[加载] pl_probe 子进程 200s 没回来 -> 当"没结论"（不下 bit）')
        return 9, ''
    out = r.stdout.decode('utf-8', 'replace').strip()
    if '[PL]' not in out:
        print('[加载] pl_probe 没跑起来（没有 [PL] 标记）-> 当"没结论"')
        return 9, out
    for ln in out.splitlines():
        if ln.startswith('[PL]'):
            print('  ' + ln)
    # ★ 退出码只认白名单。理由：SSH / 传输层断掉时子进程会返回 **255**，而 255
    #   既不在 (0, 9) 也不在 (1, 2) —— 原样返回的话调用方的 `st in (0, 9)` 判不中，
    #   就会掉进"下载 bit"这条**最危险**的分支：在一个我们其实没看清状态的 PL 上
    #   下 bit。实测过一次：`[PL] state = unknown` 打出来之后连接立刻被板子掐断
    #   （`Connection reset by peer`）—— 正是"有 [PL] 标记、但结论不可信"的情形。
    #   白名单之外一律归 9（没结论、绝不下 bit）。
    if r.returncode not in (0, 1, 2, 9):
        print('[加载] pl_probe 退出码 %d 不在白名单 (0/1/2/9) 里 —— 多半是连接/'
              '传输层断了，结论不可信 -> 当"没结论"' % r.returncode)
        return 9, out
    return r.returncode, out


def sample(m):
    """采一次全部探针，返回一个 dict（全是裸读数）"""
    return {
        'pclk64': m['p0'].read(0x00),
        'vsync':  m['p0'].read(0x08),
        'href':   m['p1'].read(0x00),
        'scl':    m['p1'].read(0x08),
        'sda':    m['p2'].read(0x00),
        'w5':     m['p2'].read(0x08),
        'ctrl':   m['p3'].read(0x00),
        'xclk':   m['p3'].read(0x08),
        'tvalid': m['p4'].read(0x00),
        'tuser':  m['p4'].read(0x08),
        'tlast':  m['p5'].read(0x00),
        'w3':     m['p5'].read(0x08),
        'sh7':    m['p6'].read(0x00),
        'frame_cnt': m['ctrl'].read(0x08),
        'stat':   m['vstat'].read(0x00),
        'stat2':  m['vstat'].read(0x08),
    }


def lines(t, cur, prv, mark):
    """把一个采样点打成 3 行（传感器 / 流侧 / 视觉+水位）"""
    g = lambda k: _d(prv[k], cur[k]) if prv is not None else cur[k]
    w5 = cur['w5']
    sh = cur['sh7']
    w3 = cur['w3']
    st2 = cur['stat2']
    out = []
    out.append('S t=%-5.1f pclk64=%-10d vsync=%-6d href=%-9d xclk=%-9d scl=%-5d sda=%-5d'
               % (t, g('pclk64'), g('vsync'), g('href'), g('xclk'), g('scl'), g('sda')))
    out.append('  L locked=%d xclk=%d pwdn=%d rst_n=%d seen0=0x%02X seen1=0x%02X'
               % (w5 & 1, (w5 >> 17) & 1, (w5 >> 18) & 1, (w5 >> 19) & 1,
                  (w5 >> 9) & 0xFF, (w5 >> 1) & 0xFF))
    out.append('A t=%-5.1f tvalid=%-10d tuser=%-6d tlast=%-8d | w3=0x%08X'
               % (t, g('tvalid'), g('tuser'), g('tlast'), w3))
    out.append('  sa=%d drp=%d wrb=%d rsp=%d | ovf=%d unf=%d seen_ovf=%d seen_unf=%d'
               % ((w3 >> 31) & 1, (w3 >> 30) & 1, (w3 >> 29) & 1, (w3 >> 28) & 1,
                  (w3 >> 10) & 1, (w3 >> 11) & 1, (w3 >> 13) & 1, (w3 >> 14) & 1))
    out.append('V t=%-5.1f fc=%-9d stat=0x%08X wcnt=%-6d errs=0x%02X lastbcnt=%-5d'
               % (t, cur['frame_cnt'], cur['stat'], st2 & 0x7FFF,
                  (st2 >> 15) & 0x3F, (st2 >> 21) & 0x7FF))
    out.append('  fifo wm=%-5d full=%d empty=%d sof_armed=%d dropping=%d wdt_lat=%d%s'
               % (sh & 0x3FFF, (sh >> 14) & 1, (sh >> 16) & 1, (sh >> 18) & 1,
                  (sh >> 19) & 1, (sh >> 31) & 1, mark))
    return out


def write_ctrl(m, value):
    m['p3'].write(0x00, value)
    return value


def pulse_sensor_rst(m, tag):
    """脉冲 ctrl[4]：重跑传感器上电时序 + SCCB（不重烧 bit 的重掷骰子手段）"""
    v = m['p3'].read(0x00)
    print('  >>> [%s] 脉冲 ctrl[4]（force_sensor_rst），ctrl 0x%X -> 0x%X'
          % (tag, v, v | 0x10))
    write_ctrl(m, v | 0x10)
    time.sleep(0.30)
    write_ctrl(m, v & ~0x10)
    print('  >>> [%s] 已放开，等它重跑上电+SCCB' % tag)


def pulse_vid_rst(m, tag):
    """脉冲 ctrl[3]：强制复位视频域（vid_rst_ctrl 的 force_rst）

    ★ 为什么这一发最值得先试：vid_rst_ctrl.v 文件头写着，本板 2026-09-13 量到过
      **一模一样**的症状 —— 传感器健康（vsync/href/pclk 都在跳）但流侧 tvalid/tuser/
      tlast 全是 0，根因是"复位比要复位的那侧时钟早撤 -> 异步 FIFO 带着非法初值
      开跑 -> 写侧一开动就判满 -> 溢出 -> 永久锁死"。第七版的修法是让复位保持到
      PCLK 数满 2^20 拍，并额外留了 ctrl[3] 这个**软件复位**后门。
      这里就是去按那个后门：按下去 = fr_s2=1 复位住；松开后 alive 重新数 43.7ms，
      于是复位**又保持到 PCLK 稳定**才撤 —— 正是 xpm_fifo 要的上电次序。
      （drv 的 _init_video 起链时也按一次，但那只在**启动时**按；中途卡死没人按。）
    """
    v = m['p3'].read(0x00)
    print('  >>> [%s] 按住 ctrl[3] 强制复位视频域，ctrl 0x%X -> 0x%X' % (tag, v, v | 0x08))
    write_ctrl(m, v | 0x08)
    time.sleep(0.30)
    write_ctrl(m, v & ~0x08)
    print('  >>> [%s] 已松开（松开后 alive 重新数 2^20 拍 pclk ≈ 43.7ms 才撤复位）'
          % tag)


def pulse_power_cycle(m, tag):
    """更狠的重掷：强制 pwdn + 停 xclk -> 放开 -> 再来一发传感器复位"""
    v = m['p3'].read(0x00)
    print('  >>> [%s] 强制 pwdn + 停 xclk（ctrl[1]|ctrl[0]），ctrl 0x%X -> 0x%X'
          % (tag, v, v | 0x03))
    write_ctrl(m, v | 0x03)
    time.sleep(0.50)
    write_ctrl(m, v & ~0x03)
    print('  >>> [%s] 已恢复供电/时钟，接着发传感器复位' % tag)
    time.sleep(0.20)
    pulse_sensor_rst(m, tag)


def main():
    n, period = 150.0, 1.0
    senrst_at, pwr_at, vidrst_at = None, None, None
    args = sys.argv
    for i, a in enumerate(args):
        if i + 1 >= len(args):
            continue
        if a == '--n':
            n = float(args[i + 1])
        elif a == '--period':
            period = float(args[i + 1])
        elif a == '--senrst-at':
            senrst_at = float(args[i + 1])
        elif a == '--pwr-at':
            pwr_at = float(args[i + 1])
        elif a == '--vidrst-at':
            vidrst_at = float(args[i + 1])

    # ★ --noinit：**只读、什么都不启动**。用来读"上一个进程退出后留下的现场"。
    #   为什么这个现场值得单独读一次：采集链的消费者（VDMA）是随驱动进程退出而
    #   stop() 的，而桥的 FIFO 还在被传感器灌 —— 这会造出文档里那个**已知的**
    #   "消费者缺席 -> 水位顶满 -> dropping 挡行 -> sof_armed 举着不落 -> 自锁"
    #   死锁。先把它的寄存器长相记下来当**参照签名**，之后真卡死时一比对就知道
    #   是"同一个已知死锁"还是"另一种没见过的死法"。
    #   只读模式连 bit 都不碰（MMIO 读已配置好的 PL 不需要 Overlay 对象，
    #   与 pl_probe.py 同款做法）。
    if '--noinit' not in args:
        if '--nobit' not in args:
            # ★★ 下 bit 是**整机最危险的动作**，2026-09-26 深夜一次挂机换来的规矩：
            #
            #   【挂法一】链路**正跑着**时盲目下 bit -> fpga_manager
            #     `Timeout waiting for PCFG_INIT` / `write init error: 0xffffff92`
            #     -> PL 留在半编程态 -> 之后任何一次 MMIO 读都可能锁死总线
            #     （ping 掉 66%、SSH 无响应、**只能断电重启**）。
            #   【挂法二】刚上电、我们的 bit **不在** PL 里（`fpga_manager state`
            #     是 unknown 而非 operating）时，哪怕只是**跑一次 pl_probe 去读**，
            #     也能把总线读死 —— 2026-09-26 深夜第二次挂机就是这么来的，
            #     那次全程没下 bit。所以"读一下问问看"本身不是无风险动作。
            #
            # ⇒ 判据不是"探针怎么说"，而是"**上一个动作是什么**"：
            #     · 刚断电重启过 -> 我们的 bit 不可能在跑 -> **直接下，不用探**
            #       （这就是 --cold 的用法；此时下 bit 是把 PL 从 base.bin 换成
            #        我们的设计，是**必须**做且唯一安全的一步）
            #     · 本会话里刚跑过采集链 -> **绝不下**，用 --nobit 接力
            #     · 说不清（例如人手动跑、跨会话）-> 才需要 pl_probe 去问，
            #       并接受它自己可能把板子读挂的代价
            if '--cold' in args:
                from pynq import Bitstream
                Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
                print('[加载] --cold：刚上电，直接下 bit（不下反而是错的：'
                      'PL 里是 base.bin，读我们的寄存器会 SIGBUS 甚至读死总线）')
            else:
                st, out = pl_state()
                print('[加载] pl_probe 退出码 %d' % st)
                if st in (0, 9):
                    print('[加载] ==> **不下载 bit**（%s）—— 用 --nobit 接力'
                          % ('流水线正在跑，下 bit 会把整机搞挂' if st == 0
                             else '探针没结论；下错一次要断电重启整机'))
                else:
                    from pynq import Bitstream
                    Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
                    print('[加载] bit 已下载（= 重掷一次骰子）')
        from ball_detect import _make_cap, _init_video
        _init_video(_make_cap())
        print('[启动] 采集链已按标准顺序起来')
    else:
        print('[只读] --noinit：不下载 bit、不启动采集链，只读现场')

    m = {'p0': MMIO(P0, 0x1000), 'p1': MMIO(P1, 0x1000), 'p2': MMIO(P2, 0x1000),
         'p3': MMIO(P3, 0x1000), 'p4': MMIO(P4, 0x1000), 'p5': MMIO(P5, 0x1000),
         'p6': MMIO(P6, 0x1000), 'ctrl': MMIO(CTRL, 0x1000),
         'vstat': MMIO(VSTAT, 0x1000)}

    # ★ 唯一的写动作：把桥账本影子选择器指到 7 号（FIFO 实时状态）。
    #   保留 bits[5:0]（xclk_off/pwdn/vid_rst/sensor_rst，正常全是 0），
    #   清掉 bit6（dbg_trig，影子 7 是实时的、不需要快照）。
    #   之后全程**只读**，直到用户显式要求恢复动作。
    v = m['p3'].read(0x00)
    nv = (v & 0x3F) | (DBG_SEL_SHADOW7 << 7)
    write_ctrl(m, nv)
    print('[探针] dbg_sel 指向 7 号影子（FIFO 实时状态）：ctrl 0x%X -> 0x%X' % (v, nv))
    print('[探针] 影子 7 的位域：[13:0]水位 [14]full [15]wrb [16]empty '
          '[17]rdb [18]sof_armed [19]dropping [31]wdt_lat')

    print('')
    print('  盯 %.0f 秒，每 %.1fs 一次，**中途只读**%s'
          % (n, period,
             ('' if senrst_at is None else '，t=%.0fs 时脉冲传感器复位' % senrst_at)
             + ('' if pwr_at is None else '，t=%.0fs 时做一次断电重上电' % pwr_at)))
    print('  判读：谁先停 = 凶手在哪一节。pclk64 停 = 传感器停钟；'
          'pclk 活而 tuser/tlast 停 = 桥侧；水位顶满 + wdt_lat=1 = FIFO 死锁')
    print('')

    t0 = time.time()
    prv = None
    prev_fc = None
    frozen_since = None
    n_heal = 0
    did_senrst = did_pwr = did_vidrst = False

    while time.time() - t0 < n:
        now = time.time()
        t = now - t0

        if vidrst_at is not None and not did_vidrst and t >= vidrst_at:
            did_vidrst = True
            pulse_vid_rst(m, 't=%.0fs' % t)
            prv = None
            time.sleep(1.0)

        if pwr_at is not None and not did_pwr and t >= pwr_at:
            did_pwr = True
            pulse_power_cycle(m, 't=%.0fs' % t)
            prv = None                      # 动作后重新起算增量，免得看着像"暴增"
            time.sleep(1.0)

        if senrst_at is not None and not did_senrst and t >= senrst_at:
            did_senrst = True
            pulse_sensor_rst(m, 't=%.0fs' % t)
            prv = None
            time.sleep(1.0)

        cur = sample(m)

        mark = ''
        fc = cur['frame_cnt']
        if prev_fc is not None:
            if fc == prev_fc:
                if frozen_since is None:
                    frozen_since = t
                    mark = '   <<< 帧计数冻结（从这一刻开始）'
            elif frozen_since is not None:
                n_heal += 1
                mark = '   <<< 冻了 %.1fs 后自己恢复了（第 %d 次）' % (t - frozen_since, n_heal)
                frozen_since = None

        for ln in lines(t, cur, prv, mark):
            print(ln)
        if mark:
            print('')
        sys.stdout.flush()

        prv = cur
        prev_fc = fc
        time.sleep(max(0.0, period - (time.time() - now)))

    print('')
    print('=== 收工（盯了 %.0fs）===' % (time.time() - t0))
    print('  最后状态：frame_cnt=%d  stat=0x%08X  fifo wm=%d full=%d empty=%d '
          'sof_armed=%d wdt_lat=%d'
          % (cur['frame_cnt'], cur['stat'], cur['sh7'] & 0x3FFF,
             (cur['sh7'] >> 14) & 1, (cur['sh7'] >> 16) & 1,
             (cur['sh7'] >> 18) & 1, (cur['sh7'] >> 31) & 1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
