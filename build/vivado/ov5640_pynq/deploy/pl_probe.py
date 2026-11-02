# -*- coding: utf-8 -*-
"""pl_probe.py — 回答一个问题："我们的视觉流水线现在正在跑吗？"

★ 为什么必须有它（2026-09-26 实测，三个坑一起踩出来的）：

  【坑一】往一块**已经跑着我们的采集链**的 PL 里再下 bit，会让 `fpga_manager`
  超时（`TimeoutError: [Errno 110] Connection timed out`，出自
  pynq/pl_server/embedded_device.py 的 `open(self.BS_FPGA_MAN, "w")`），
  PL 被留在**半编程**状态，此后任何 AXI 事务都可能锁死总线、整机拖垮
  （症状：ping 掉包 80%、SSH 无响应、只能断电）。
  最阴的是：上一步脚本退出后 **VDMA 仍在 PL 里继续往 DDR 搬数据**
  （VDMA 是 PL 硬件，不随进程退出而停）—— 所以"跑完一个脚本，再下 bit"
  恰好是最危险的动作。

  【坑二】**断电重启后 PL 并不是空的！** 开机时 BOOT.BIN 会把一个默认
  bitstream 配进 PL，所以 `/sys/class/fpga_manager/fpga0/state` 一直读回
  `operating` —— 拿它判"要不要下 bit"会得出**相反**的结论（本探针 v1 就是这么错的）。
  判断"是不是我们的 bit"只能靠**读我们自己的寄存器**：读得到、且帧计数在涨，
  才是我们的东西在跑。

  【坑三】读一个**不存在的外设**地址会 **SIGBUS**（信号 7，不是 Python 异常，
  try/except 拦不住，进程直接死）。所以下面把这次读放进**子进程**里隔离 ——
  子进程被信号杀死，父进程只看到退出码，照样能给出结论。

  【坑四，2026-09-26 晚补】**SIGSEGV（信号 11）跟 SIGBUS 是两件事，别混**。
  本板有个老毛病：紧跟在一个 PYNQ 进程退出之后起的新进程，`import pynq`
  会**确定性段错误**（rc=139 / SIGSEGV、一行输出都没有）——这跟"我们的 bit
  在不在"**毫无关系**。实测就栽在这上面：第 2 步刚刚证明流水线在跑
  （5760 字全被覆盖、frame_cnt 283->313），紧接着的复查探针却死了两次
  （一次 -7、一次 -11），把一轮已经成功的验收差点判成失败。
  所以下面把信号**分开对待**：
    · SIGBUS(-7)  = 那个地址上没有外设 = 我们的 bit 真的不在  -> 可以下
    · SIGSEGV(-11)= PYNQ 自己崩了 = 什么都说明不了            -> 不下！
  读不出结论时**一律不下 bit**：下错一次要断电重启整机，不下最多重跑一遍脚本。

用法（板上）：python3 /boot/pl_probe.py
  退出码 0 = **我们的流水线正在跑** -> 千万别再下 bit，用 --nobit 接力
  退出码 2 = **我们的 bit 在、但流水线没跑**（传感器没出流/视核卡住）
             -> 下 bit 是安全的（没有活着的 DMA 流量），而且重下还能重掷传感器
                状态的骰子；但也要知道：这种情况**不会** SIGBUS，
                读寄存器的 A/B 项照样能测（只是会报视频流不健康）
  退出码 1 = **我们的 bit 根本不在 PL 里**（SIGBUS 实锤）-> **必须下 bit**：
             既为了把我们的 bit 配进去，也为了写 PYNQ 的全局状态文件（没有它，
             后面 --nobit 的进程连 allocate() 都过不了，会报
             "Overlay is not downloaded"）。
  退出码 9 = **探针没能得出结论**（PYNQ 自己崩了 / 重载后仍崩 / 子进程报 ERR，
             例如**非登录 shell 里 import pynq 失败**）-> **绝对不要下 bit**。
             ★ 2026-09-28 深夜补：这个分支以前会把 "ERR No module named 'pynq'"
               当成 rc=1（bit 不在、可以下）—— 那等于把环境问题误判成"可以下 bit"。
               现在 ERR 一律按未知处理。跑之前请确认用 **bash -lc**（否则 cwd 不在
               /home/xilinx，import pynq 就失败）。
             调用方应当用 --nobit 硬着头皮往下走（用不用得上由后面那步自己报错），
             或者干脆重跑一遍脚本。理由见上面【坑四】：把 -11 当成"bit 不在"
             去下 bit，正是把整机搞挂的那个动作。

  【坑五，2026-09-26 深夜补】**PL 是空的时候，读一次寄存器就能锁死整机** ——
  跟坑二合起来看很有意思：坑二说 `state=operating` 不代表是我们的 bit（会误判成
  "有"），坑五说 `state!=operating` **一定**代表什么都没有（可以放心当"没有"用）。
  实测：断电重启后 `state = unknown`（那一次 BOOT.BIN 连默认 bit 都没配上），
  探针打完 sysfs 那行、刚去读我们的寄存器，SSH 立刻 `Connection reset by peer`，
  整机锁死只能断电。机理：**PL 里有 bit** 时，地址被译码、从机回 DECERR ->
  干净的 SIGBUS（坑三）；**PL 空**时事务出去没有任何从机应答，而 Zynq 互连
  **没有超时** -> 永久挂起。所以本探针现在把 state 检查提到读之前，
  `state != operating` 时**直接下结论 rc=1，跳过那次致命的读**。
"""

import os
import subprocess
import sys
import time

CTRL_BASE = 0x43B10000          # ctrl[0]=freeze [1]=soft_rst [2]=swap；+0x08 = 帧计数
FPGA_STATE = '/sys/class/fpga_manager/fpga0/state'

RC_RUNNING = 0                  # 我们的流水线在跑 -> 别下 bit
RC_NO_BIT = 1                   # 我们的 bit 不在 PL 里 -> 必须下 bit（否则 SIGBUS）
RC_IDLE = 2                     # bit 在但没跑 -> 下 bit 安全（也顺便重掷骰子）
RC_UNKNOWN = 9                  # 什么都没测出来 -> **绝不下 bit**

SIGBUS = -7
SIGSEGV = -11


def _child_read():
    """子进程入口：只做危险的那一次寄存器读，结论打在 stdout 上"""
    try:
        from pynq import MMIO
        mc = MMIO(CTRL_BASE, 0x1000)
        c0 = mc.read(0x08)
        time.sleep(0.4)
        c1 = mc.read(0x08)
        print('READ %d %d' % (c0, c1))
    except Exception as e:
        print('ERR %s' % str(e)[:120])
    sys.stdout.flush()
    return 0


def _run_child():
    """跑一次隔离子进程，返回 (rc, 输出字符串)。超时当 rc=+1 处理。"""
    try:
        r = subprocess.run([sys.executable, '-u', os.path.abspath(__file__), '--child'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
    except subprocess.TimeoutExpired:
        return None, ''
    return r.returncode, r.stdout.decode('utf-8', 'replace').strip()


def _verdict(rc, out):
    """把一次子进程的结果翻译成结论。返回 (退出码, 是否已经可以下结论)。"""
    if rc is None:
        print('[PL] 读寄存器**超时**（子进程挂了 45s 以上）')
        print('     -> 总线没回应，最可能是我们的 bit 不在（exit %d）' % RC_NO_BIT)
        print('     -> 也可能是上一轮采集链还在跑而未响应；两种情况都**先别再读**了')
        return RC_NO_BIT, True

    if rc == SIGBUS:
        print('[PL] 子进程被 **SIGBUS** 杀死（rc=%d）—— 那个地址上没有外设' % rc)
        print('     -> **我们的 bit 不在 PL 里**（现在跑的多半是开机默认 bit）')
        print('     -> 必须下 bit，否则后面一读寄存器就 SIGBUS（exit %d）' % RC_NO_BIT)
        return RC_NO_BIT, True

    if rc != 0:
        what = 'SIGSEGV' if rc == SIGSEGV else '信号 %d' % (-rc)
        print('[PL] 子进程被 **%s** 杀死（rc=%d）' % (what, rc))
        if not out:
            print('     -> 它**一个字都没打出来**，说明死在 `import pynq` 或 `MMIO()` 里，')
            print('        **没走到读寄存器那一步** —— 这是本板的老毛病（zocl 相关），')
            print('        它跟"我们的 bit 在不在"**毫无关系**，不能当证据用。')
        return RC_UNKNOWN, False

    if out.startswith('READ'):
        _, a, b = out.split()
        a, b = int(a), int(b)
        print('[PL] 我们的帧计数读到了：%d -> %d（0.4s）' % (a, b))
        if a != b:
            print('[PL] **我们的流水线正在跑** -> 千万别再下 bit（exit %d）' % RC_RUNNING)
            return RC_RUNNING, True
        print('[PL] **我们的 bit 在**（寄存器读得通、没有 SIGBUS），但帧计数不动')
        print('     -> 传感器没出流 / 视核卡住。此时下 bit 是安全的（没有活着的')
        print('        DMA 流量在跟 fpga_manager 抢 PL），而且重下还能顺手重掷一次')
        print('        传感器状态的骰子；但也知道：**这种状态读寄存器不会 SIGBUS**，')
        print('        A/B 项照样能测（只是会报视频流不健康）（exit %d）' % RC_IDLE)
        return RC_IDLE, True

    if out.startswith('ERR'):
        # ★★ 2026-09-28 深夜实锤的坑：子进程**在 import 阶段就失败**时也走这条路
        #    （实测 "ERR No module named 'pynq'" —— 调用方用了**非登录 shell**，
        #      cwd 不在 /home/xilinx，import pynq 直接找不到）。
        #    老代码把这种"什么都没测出来"当成 rc=1（bit 不在，可以下），于是
        #    **一次环境问题被误判成"可以下 bit"** —— 正是本文档第一条铁律要防的动作。
        #    现在按文档自己的规矩办：**读不出结论时一律不下 bit**（rc=9）。
        print('[PL] 子进程自己报了错：%s' % out)
        print('     -> 它**没能走到"读寄存器"那一步**（import/env 问题），跟"我们的 bit'
              ' 在不在"**毫无关系**')
        print('     -> 结论：**未知**，绝对不要下 bit（exit %d）' % RC_UNKNOWN)
        print('        两个已知成因，按顺序排查：')
        print('        ① **解释器选错**（2026-09-28 深夜实锤）：本板有两个 python3，'
              '只有')
        print('           /usr/local/share/pynq-venv/bin/python3 里**有** pynq；'
              '/usr/bin/python3 是')
        print('           系统 python，**没有** pynq。调用方若写死 /usr/bin/python3 就'
              '会踩这里。')
        print('           修法：PY=/usr/local/share/pynq-venv/bin/python3')
        print('        ② **非登录 shell**：cwd 不在 /home/xilinx，import pynq 也会'
              '失败。修法：bash -lc')
        print('        ③ **身份不够**（2026-09-29 实测）：本板的 xilinx 用户**不在**'
              'render/video 组')
        print('           （id 只有 1000/4/27），而 /dev/dri/renderD128 是 root:render 660')
        print("           -> 以 xilinx 跑就报 `Could not open device with index '0'`。")
        print('           反过来说：root 不带登录 shell 会报 `No devices found`。')
        print('           ⇒ 正确姿势是**两个都要**：'
              'echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && <venv python> pl_probe.py"')
        print('        （探针的子进程用 sys.executable，所以父进程的解释器就是它用的）')
        return RC_UNKNOWN, True

    print('[PL] 读寄存器没成功（输出没见过）：%s' % out)
    print('     -> 没拿到结论，**按未知处理（绝不下 bit）**（exit %d）' % RC_UNKNOWN)
    return RC_UNKNOWN, True


def _reload_zocl():
    """`modprobe -r zocl && modprobe zocl` —— 本板 import pynq 段错误的已知缓解手段。
    只在已经是 root 时才试（否则 modprobe 会要密码、可能挂住）。"""
    if os.geteuid() != 0:
        print('[PL] （不是 root，跳过 zocl 重载）')
        return False
    try:
        subprocess.run(['modprobe', '-r', 'zocl'], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['modprobe', 'zocl'], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('[PL] 已 modprobe -r/-a zocl（缓解 import pynq 段错误），重试一次')
        return True
    except Exception as e:
        print('[PL] zocl 重载失败：%s' % str(e)[:80])
        return False


def main():
    if '--child' in sys.argv:
        return _child_read()

    # ---------------------------------------------------------- ① sysfs（仅供参考）
    # 纯文件读，永远不会 SIGBUS。但注意：它只会告诉你"有个 bitstream 配进去了"，
    # 开机默认 bit 也算 —— **绝不能拿它当"是我们的 bit"的依据**（坑二）。
    try:
        state = open(FPGA_STATE).read().strip()
    except Exception as e:
        state = '读不到(%s)' % str(e)[:40]
    print('[PL] fpga_manager state = %s（仅供参考：开机默认 bit 也是 operating）' % state)

    # ---------------------------------------------------------- ①.5 空 PL 短路
    # ★ 第五个坑（2026-09-26 深夜，两次挂机换来的）：**PL 里什么都没有的时候，
    #   读一次寄存器就能把整机读死。**
    #   实测：刚断电重启，`state = unknown`（那一次 BOOT.BIN 连默认 bit 都没配上），
    #   探针打出上面那行 sysfs（纯文件读，安全）之后、刚一去读我们的寄存器，
    #   SSH 立刻 `Connection reset by peer` —— 一条没有从机应答的 AXI 事务把整个
    #   SoC 锁死，只能断电重启。两次挂机是同一个机理。
    #   为什么有时 SIGBUS 有时挂死：**PL 里有 bit 时**，地址会被译码、从机回
    #   DECERR -> 干净的 SIGBUS；**PL 空时**，事务出去没有任何人应答，而 Zynq 的
    #   互连**没有超时机制** -> 挂死。所以"读一下问问看"在 PL 空的时候是致命的。
    #   ⇒ 结论可以**在读之前**就下，而且必须提前下：state 不是 operating
    #     = 什么都没配 = 我们的 bit 必然不在 = 必须下 bit（rc=1）。
    #     （state 只在成功下载之后才变 operating，所以"链路在跑"必然意味着
    #       state=operating —— 反向用这个判据是安全的。）
    if state != 'operating':
        print('[PL] state = %s 不是 operating —— **PL 里没有 bitstream**' % state)
        print('     -> 我们的 bit 必然不在（本板 state 只在成功下载后才变 operating）')
        print('     -> 而且此时**绝不能读寄存器**：PL 空时 AXI 事务没有从机应答，')
        print('        Zynq 互连没有超时，一条读就能把整机锁死（实测两次，只能断电）')
        print('     -> 必须下 bit，**跳过下面那次致命的读**（exit %d）' % RC_NO_BIT)
        return RC_NO_BIT

    # ---------------------------------------------------------- ② 隔离读我们的寄存器
    rc, out = _run_child()
    code, settled = _verdict(rc, out)
    if settled:
        return code

    # 走到这里 = 子进程崩了、且什么都没测出来。先试着把 zocl 重载一下再来一次 ——
    # 但**只重试一次**，重试完还是没结论就认输（不是"bit 不在"，是"不知道"）。
    if not _reload_zocl():
        print('[PL] 没法重载 -> 结论：**未知**（exit %d，绝对不要下 bit）' % RC_UNKNOWN)
        return RC_UNKNOWN
    rc, out = _run_child()
    code, settled = _verdict(rc, out)
    if settled:
        return code

    print('[PL] 重载 zocl 之后**还是**没有结论 -> 结论：**未知**')
    print('     -> **绝对不要下 bit**（把 -11 当"bit 不在"去下，正是搞挂整机的动作）。')
    print('     -> 正确做法：用 --nobit 接着跑（用得上的话），或者重跑一遍脚本。')
    print('     -> exit %d' % RC_UNKNOWN)
    return RC_UNKNOWN


if __name__ == '__main__':
    sys.exit(main())
