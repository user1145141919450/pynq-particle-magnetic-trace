# -*- coding: utf-8 -*-
"""link_doctor.py — 链路体检：**不进 PYNQ**，mmap 直读视核的计数器，量帧率、发 ctrl[3] 脉冲。

为什么要有它（2026-09-27 第八次上板）：
  拿真球跑那一轮，链路掉进了**"慢速半死"**那一档 —— 启动时 1.8s 只涨 5 帧（2.8fps，
  不是 30fps），12 帧里自愈 9 次，参考检测每帧跑出 34~58 个"圆"、耗时 17 秒
  （花帧的边缘爆炸），连驱动的字节序判定都翻了。而自愈看门狗的判据是
  "frame_cnt 完全不动 >=1.2s"，**看不见"动得很慢"** —— 这一档正是它的盲区。

  这一档下所有比对结论都是假的（地图自检 13%/5.9% 就是这么来的），所以得先有个
  便宜、快、不惊动 PYNQ 的工具把链路**量出来**：
    · 不进 PYNQ = 不碰 zocl、不分配 CMA、不碰 VDMA —— 纯读三个寄存器窗口
    · 只读不写也能量帧率；要救才发 ctrl[3] 脉冲，长度可调（默认 0.3s，与看门狗同款）
    · 板上没有 busybox 的 devmem，所以走 /dev/mem + mmap（复用 fastmem）

用法（板上，要 sudo）：
  python3 /boot/link_doctor.py                 # 量 3s 速率 + 印账本
  python3 /boot/link_doctor.py --watch 10      # 每秒印一行，看速率随时间怎么变
  python3 /boot/link_doctor.py --pulse 2.0     # 脉冲 ctrl[3] 保持 2s，前后各量一次
  python3 /boot/link_doctor.py --pulse 0.3 --watch 10

退出码：
  0 = 速率 >= 20fps（健康）
  1 = 速率 < 20fps（慢速半死那一档）
  9 = 读不到寄存器（我们的 bit 不在 PL 里，或 mmap 失败）
"""

import os
import sys
import time

# ★ 不要把 '/boot' 无脑顶到 sys.path[0]（2026-09-29 板上实锤）：
#   同一套脚本在 /home/xilinx/wp(ext4，**真身**，刚部署的那份) 和 /boot(FAT，**镜像**，
#   可能是五月三日的老文件) 各有一份。这里原来是 `sys.path.insert(0, '/boot')`，
#   于是"import link_doctor"这一下就把 /boot 顶到了最前面 —— 之后**所有** import
#   都先撞上那份旧镜像。实测后果：link_boot_selftest 的 display_swap 一节报
#   `module 'ball_map_detect' has no attribute display_swap_check`，
#   而 wp 那份明明有这个函数（/boot 那份 53873 字节 md5 caa378e4，
#   wp 60764 字节 md5 dca7a78b）。台架验的是"刚部署的代码"，不能被旧镜像盖住。
#   现在：先认自己的目录（wp 里同样有 fastmem.py），自己目录找不到才退回 /boot。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _import_fastmem():
    """优先用**本脚本所在目录**的 fastmem；本目录没有才退回 /boot（见上面的注释）。"""
    try:
        import fastmem
        return fastmem
    except ImportError:
        if '/boot' not in sys.path:
            sys.path.append('/boot')
        import fastmem
        return fastmem

# ★ 位定义以 rtl/debug/dvp_ctrl.v 的 case 译码为准（2026-09-29 核对，别照抄"记忆"；
#   这里原先写的是"bit0=freeze bit1=soft_rst bit2=swap"，那是**视核** ctrl(0x43B10000)
#   的位定义，被抄到传感器域这个字上了 —— 张冠李戴的注释比没有注释更贵）：
#   bit0=xclk_off（1=拉低停钟） bit1=force_pwdn bit3=vid_rst_force（视频域复位）
#   bit4=force_sensor_rst（ov5640_top 重跑上电 + 304 条 SCCB） [9:7]=dbg_sel
VID_BASE = 0x43A30000          # 与 dvp_regs 的 'p3' 是同一个字：静止位 bit0/1/3/4 必须为 0
CTRL_BASE = 0x43B10000         # +0x00 ctrl（同款位）/ +0x08 frame_cnt
STAT_BASE = 0x43B20000         # stat={行数,字数} stat2={last_bcnt, errs, wcnt}
VDMA_BASE = 0x43000000         # S2MM：+0x30 DMACR（bit0 RS）/ +0x34 DMASR

PULSE_BIT = 1 << 3             # ctrl[3]：与自愈看门狗同一个位（写脉冲请用 dvp_regs.pulse）
OK_FPS = 20.0


def _read_sccb(mp0, mp1, mp2):
    """读传感器域探针：pclk64 / vsync / href / scl 上升沿 / sda 电平字。

    ★ 为什么需要它（2026-09-27 傍晚）：视核卡住（frame_cnt 恒 0、账本行长度
      1223 而非 2047 = 一行只有 2048 字节里的 1223）说明**传感器时序不对**；
      而 ctrl[4] 重跑 SCCB 又没效果 —— 那就必须分清是"SCCB 根本没在跑"
      （PL 的 ov5640_cfg 状态机卡住，得重配 PL）还是"跑完了但传感器没接受"
      （I2C/传感器侧问题）。scl 上升沿计数就是那把尺子：它在动 = cfg 在跑。
    """
    return (mp0.rd_u32(0x00), mp0.rd_u32(0x08), mp1.rd_u32(0x00), mp1.rd_u32(0x08),
            mp2.rd_u32(0x08))


def _fmt_ledger(st, st2):
    return ('行数=%d 字数=%d 行长度=%d errs=0x%02X'
            % ((st >> 16) & 0xFFFF, st & 0xFFFF, (st2 >> 21) & 0x7FF, (st2 >> 15) & 0x3F))


def measure(mc, sec):
    """量 sec 秒内的帧率（用 frame_cnt 差值）。"""
    t0 = time.time()
    c0 = mc.rd_u32(0x08)
    time.sleep(sec)
    c1 = mc.rd_u32(0x08)
    dt = time.time() - t0
    return (c1 - c0) / dt, c1 - c0, dt


def main():
    FastMem = _import_fastmem().FastMem

    watch = 0
    pulse = None
    halt = '--halt' in sys.argv
    for i, a in enumerate(sys.argv):
        if a == '--watch' and i + 1 < len(sys.argv):
            watch = int(sys.argv[i + 1])
        if a == '--pulse' and i + 1 < len(sys.argv):
            pulse = float(sys.argv[i + 1])

    try:
        md = FastMem(VDMA_BASE, 0x1000)      # VDMA S2MM：查野 DMA + 停掉它
        mp0 = FastMem(0x43A00000, 0x1000)    # 传感器域探针：pclk64 / vsync
        mp1 = FastMem(0x43A10000, 0x1000)    # href / scl
        mp2 = FastMem(0x43A20000, 0x1000)    # sda / 电平字
        mv = FastMem(VID_BASE, 0x1000)
        mc = FastMem(CTRL_BASE, 0x1000)
        ms = FastMem(STAT_BASE, 0x1000)
        # 救援动作（放开静止位 / 脉冲 ctrl[4]）统一交给 dvp_regs —— 那边是唯一实现，
        # 也是离线台架整条跑过的（try/finally + 读回自检俱在）。
        import dvp_regs
        dmem = dvp_regs.open_regs()
    except Exception as e:
        print('[链路] ★ mmap 失败（%s: %s）—— 我们的 bit 多半不在 PL 里'
              % (type(e).__name__, str(e)[:80]))
        return 9

    try:
        # ★ 野 DMA 检查（不进 PYNQ，所以 zocl 崩了它照样能跑 —— 2026-09-27 加的）：
        #   脚本被 SIGTERM/SIGKILL 杀时不会执行 atexit，而 VDMA 是 PL 硬件、不随进程
        #   退出而停 —— 它会带着**已释放的**缓冲地址继续跑，把下一个进程的 CMA 页
        #   覆盖掉。DMACR/DMASR 在 PL 里不随进程清零，读它就是读"上一轮留下的真状态"。
        cr = md.rd_u32(0x30)
        sr = md.rd_u32(0x34)
        print('[VDMA] DMACR=0x%08X（RS=%d） DMASR=0x%08X halted=%d errs=0x%02X'
              % (cr, cr & 1, sr, sr & 1, (sr >> 4) & 0xFF))
        if halt and (cr & 1):
            md.wr_u32(0x30, 0x0000)          # 只清 RS，不写 RESET
            print('[VDMA] 有野 DMA -> 已停（DMACR=0x%08X）' % md.rd_u32(0x30))
        elif cr & 1:
            print('[VDMA] ★ 通道还在跑 = 可能有野 DMA（加 --halt 停掉它再起采集）')
        else:
            print('[VDMA] 通道没在跑，起采集安全')

        # ★ 传感器电源/时钟位体检（2026-09-27 教训）：ctrl 的 bit0/bit1 = 停 xclk / pwdn。
        #   救援序列（断电重上电）如果**中途被打断**（进程被杀/跑完退出），寄存器会留在
        #   "断电"那一步 —— 之后所有"链路死了"的现象其实都是**自己把传感器关了**，
        #   实测 vid_ctrl=0x00000003 就是这么来的。--sensor-on 一键恢复。
        # ★★ 2026-09-29 重构：这里原来有**两份**几乎一样的实现（第二份没有 try/finally、
        #   --sensor-on 那支还漏了 ctrl[4] 重跑 SCCB，而且它排在最后 = 它说了算）。
        #   同一件事三份实现（加 dvp_regs 那份）= 迟早分叉 —— 已删，统一走 dvp_regs
        #   （纯 /dev/mem，就是离线台架整条跑过的那条路）。
        v0 = mv.rd_u32(0x00)
        if '--sensor-cycle' in sys.argv:
            # ★ 完整断电重上电（时序给足）——被"救援序列半途打断"之后，传感器可能停在
            #   **几何变了**的状态（帧比 720 行短）。只放开 bit0/1 不够，必须：
            #   断电(1s) -> 放开(等内部 POR) -> ctrl[4] 重跑整串 SCCB -> 等 PLL 锁出流。
            print('[电源] 完整断电重上电：pwdn+停钟 1.0s -> 放开 -> ctrl[4] 重跑 SCCB')
            dvp_regs.power_cycle(dmem, hold_s=1.0, quiet=True)
            print('[电源] 已放开，等 PLL 锁 + SCCB 重跑（2.0s）...')
            time.sleep(2.0)
            v0 = mv.rd_u32(0x00)
        held = v0 & dvp_regs.IDLE_MASK
        if not held:
            print('[电源] 传感器电源/时钟位正常（vid_ctrl=0x%08X）' % v0)
        else:
            if held & (dvp_regs.BIT_XCLK_OFF | dvp_regs.BIT_PWDN):
                print('[电源] ★ vid_ctrl=0x%08X：bit0/bit1 举着 = 传感器被按住断电/停钟了' % v0)
            else:
                print('[电源] ★ vid_ctrl=0x%08X：复位位举着（bit3/bit4）= 复位被锁住了' % v0)
            if '--sensor-on' in sys.argv or halt:
                ok, det = dvp_regs.unlatch(dmem, quiet=True)
                print('[电源] 已放开（ctrl=0x%08X）%s'
                      % (mv.rd_u32(0x00), '' if ok else '  ★ 读回不干净：%s' % det))
                # ★ 光上电不够：传感器掉电后**必须重跑一遍 SCCB 配置**才会出流。
                #   ctrl[4] = force_sensor_rst 就是让 PL 的 ov5640_cfg 重发那一串寄存器。
                #   （实测：只放开 bit0/1 -> 依然 0 fps；补上这一发 -> 出流。）
                time.sleep(0.6)
                dvp_regs.reinit_sensor(dmem, quiet=True)
                print('[电源] 已补发传感器复位（ctrl[4]），ov5640_cfg 会重跑 SCCB')
                time.sleep(1.0)
            else:
                print('[电源]   （加 --sensor-on 一键放开）')
        a = _read_sccb(mp0, mp1, mp2)
        time.sleep(2.0)
        b = _read_sccb(mp0, mp1, mp2)
        print('[传感器] pclk64 +%d  vsync +%d  href +%d  scl(2s) +%d  locked=%d pwdn=%d rst_n=%d'
              % (b[0] - a[0], b[1] - a[1], b[2] - a[2], b[3] - a[3],
                 b[4] & 1, (b[4] >> 18) & 1, (b[4] >> 19) & 1))
        print('[账本] %s' % _fmt_ledger(ms.rd_u32(0x00), ms.rd_u32(0x08)))
        print('[控制] vid_ctrl=0x%08X  map_ctrl=0x%08X'
              % (mv.rd_u32(0x00), mc.rd_u32(0x00)))

        fps, dn, dt = measure(mc, 3.0)
        print('[速率] %.1fs 内 %d 帧 = %.1f fps%s'
              % (dt, dn, fps, '' if fps >= OK_FPS else '   ★ 慢速半死那一档'))

        if pulse is not None:
            print('[脉冲] ctrl[3] 拉高保持 %.2fs（当前 ctrl=0x%08X）...'
                  % (pulse, mv.rd_u32(0x00)))
            # ★ 走 dvp_regs.pulse（try/finally + 只清这一位）：本脚本是"板子不舒服时
            #   第一个被叫来的"，而它原来这三行裸写——中途被打断就把 bit3 锁住
            #   （视频域永远复位 = 永远不出帧），下一个工具再来看见的就是"链路死了"。
            dvp_regs.pulse(dmem, dvp_regs.BIT_VID_RST, hold_ms=pulse * 1000.0, quiet=True)
            time.sleep(0.4)
            fps2, dn2, dt2 = measure(mc, 3.0)
            print('[速率] 脉冲后：%.1fs 内 %d 帧 = %.1f fps%s'
                  % (dt2, dn2, fps2, '  √ 救回来了' if fps2 >= OK_FPS else '  ★ 还是慢'))
            fps = fps2

        if watch:
            print('[观察] 每秒一行，共 %ds：' % watch)
            for i in range(watch):
                f, d, _ = measure(mc, 1.0)
                print('   t=%2ds  %5.1f fps  (frame_cnt 涨 %d)' % (i + 1, f, d))
                sys.stdout.flush()

    finally:
        dmem.close()
        md.close()
        mp0.close()
        mp1.close()
        mp2.close()
        mv.close()
        mc.close()
        ms.close()

    print('[链路] => %s（exit %d）'
          % ('健康' if fps >= OK_FPS else '不健康：慢速半死那一档，先救链路再谈比对',
             0 if fps >= OK_FPS else 1))
    return 0 if fps >= OK_FPS else 1


if __name__ == '__main__':
    sys.exit(main())
