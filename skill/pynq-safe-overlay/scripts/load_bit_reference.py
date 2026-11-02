# -*- coding: utf-8 -*-
"""load_bit.py — **下 PL bit 的唯一实现**：先判能不能下，下完立刻读回自检并给结论。

为什么要有它（2026-09-29，用户原话："不要让它每次上板都这不行那不行"）：
  "下 bit" 这件事现在散落在 8 个脚本里各写各的（ball_game / v3_calib / stall_probe /
  bram_write_map2 / pair_offset / frame_mon / dvp_probe / ctrl_selftest ...），
  而且**没有一处在下完立刻验证"PL 里真的是我们的比特"** —— 下飞了要等到后面读寄存器
  SIGBUS、或者半编程态把 AXI 总线挂死（2026-09-26 就是这么把整机搞挂、只能断电的）
  才被发现。顺序还常常是反的：先建 capture（它一上来就写 DMACR/DMASR），
  再判"能不能动手" —— 而开机 150s 那个 base.bin 写入窗口里，那几笔写正好撞在
  编程过程中间。

它把这件事收敛成**一条阶梯**，每一步都给可判读的结论：
  [0] 开机窗口：150s 内**拒绝**下 bit（实测 base.bin 在开机后 ~120s 写一次 PL）
  [1] **下之前先 pl_probe**（铁律）：rc=0 = 我们的流水线正在跑 -> **绝不下**
      （往正在跑的链上下 bit，fpga_manager 会 PCFG_INIT 超时，PL 停在半编程态）；
      rc=9 = 说不清 -> 不下；rc=1 = 我们的 bit 不在 -> 下（这一步正是它授权的）；
      rc=2 = bit 在但没出流 -> 可以重下（顺便重掷一次传感器状态的骰子）
  [2] bit 文件 md5 对账（期望值优先取**跟着 bit 一起走的边车文件** `*.bit.md5`）
  [3] 下 bit（**用 Bitstream().download()，不用 Overlay()** —— 本板已知 Overlay 必段错误）
  [4] 下完**立刻** pl_probe 复查 -> 只有它说"是我们的 bit 了"才算成功

退出码：
  0 = 下完确认 PL 里是我们的 bit（rc=2 也算成功：bit 在、只是还没出流）
  1 = 下完**仍然**不是我们的 bit（下飞了 / 被别的东西换掉了）
  2 = 拒绝下（我们的流水线正在跑，或 md5 对不上）—— 这是**保护性**退出，不是失败
  9 = 说不清 / 没敢下（开机窗口内、pl_probe 未知、读不了文件……）

用法（板上，**要 root + 登录 shell**；xilinx 用户不在 render 组，见 pl_probe 的提示③）：
  echo <board-password> | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u load_bit.py"
  ... load_bit.py --md5 <期望md5>     # 换一份 bit 的期望值
  ... load_bit.py --any               # 跳过 md5 对账（只在明确知道自己在干什么时用）
  ... load_bit.py --force             # 明知流水线在跑也要下（**危险**，一般别用）
"""

import hashlib
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

def _pick_bit():
    """bit 文件在哪：**先认脚本旁边（ext4 的 wp），/boot 只作兜底**。

    ★ 2026-10-01 修：原来写死 '/boot/ov5640_m1a_wrapper.bit'，两个坑叠在一起：
      (a) 本板 /boot 是 FAT，曾断电丢写 => 里面长期停着**旧**镜像（实测板上
          /boot 那份 md5 还是 c1698094…，而本次新 bit 是 5828cda4…）；
      (b) 上面那条 WANT_MD5 常量**同样过期** —— 于是两个"旧"正好对上：md5
          对账**通过**、rc=0、日志全绿，实际却把**没有 HDMI IP 的旧 bit** 写进
          PL。症状是屏幕全黑，而所有软件侧检查都正常。
      =>
      这是本工程"校验器校验了个空气"家族的第五个成员，
      而且是最毒的一个：**校验了 md5，但期望值本身指向了错误的对象**。
      光"两步都做了"不算数，得问"这两步是不是在说同一件事"。
    """
    here = os.path.join(HERE, 'ov5640_m1a_wrapper.bit')
    if os.path.isfile(here):
        return here
    return '/boot/ov5640_m1a_wrapper.bit'


BIT = _pick_bit()

# ★ 期望 md5 的**兜底值**（只有在既没 --md5、也没有边车文件时才用）。
#   它天然会过期 —— 所以正常路径不应该是它，而是下面的边车文件。
WANT_MD5_FALLBACK = '5828cda460a57bc5ad9cac4249ac3673'   # 2026-10-01 时序收敛版
PY = sys.executable

RC_OK = 0
RC_NOT_OURS = 1
RC_REFUSE = 2
RC_UNKNOWN = 9


def md5_of(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _expected_md5(argv):
    """返回 (期望 md5 或 None, 来源说明)。优先级：--md5 > 边车文件 > 内置常量。

    ★ 为什么主路径是**边车文件**而不是常量（2026-10-01）：
      原先期望值靠人把 deploy_check.py 打印的 md5 **手抄**进常量。
      抄的那一刻就把值"冻结"了 —— 下次换 bit 忘了改，就变成"旧 bit 对旧期望"
      （本次事故的根因）。边车文件由生成 bit 的那一步随手写出，
      **bit 走到哪它跟到哪**，不存在"忘了同步"这个自由度。
    """
    for i, a in enumerate(argv):
        if a == '--md5' and i + 1 < len(argv):
            return argv[i + 1], '命令行 --md5'
    side = BIT + '.md5'
    if os.path.isfile(side):
        try:
            with open(side) as f:
                tok = f.read().split()
        except Exception as e:
            return None, '边车文件 %s 读不了（%s）' % (side, e)
        if not tok:
            return None, '边车文件 %s 是空的' % side
        return tok[0].strip().lower(), '边车文件 %s' % side
    return WANT_MD5_FALLBACK, '内置常量（**没有边车文件 —— 这条会过期，快去补 %s**）' % side


def _pl_probe():
    """跑 pl_probe（它自己会把危险的那次读放进子进程隔离）。返回它的退出码。

    ★ 用 sys.executable 跑：调用方用什么解释器（venv 那个带 pynq 的），
      子进程就用什么 —— 这是 pl_probe 文档里点名的第一个坑。
    """
    print('[load] 先 pl_probe（铁律：**下 bit 之前必须先问 PL 里现在是什么**）...')
    try:
        return subprocess.call([PY, '-u', os.path.join(HERE, 'pl_probe.py')])
    except Exception as e:
        print('[load] pl_probe 没跑起来：%r' % (e,))
        return RC_UNKNOWN


def main(argv):
    import link_boot                      # 只用它的开机窗口（纯 /proc 读，不碰 PL）

    force = '--force' in argv

    # ---------------------------------------------------------------- [0] 开机窗口
    inside, up, need = link_boot.boot_window()
    if inside:
        print('[load] 开机才 %.1fs —— 还在 base.bin 写 PL 的窗口里（<%ds），拒绝下 bit'
              % (up if up is not None else -1, link_boot.BOOT_QUIET_S))
        print('[load] （这个窗口里下 bit 会被 base.bin 的编程过程打断 = 半编程态）')
        return RC_UNKNOWN
    print('[load] 开机 %.1fs，已过 base.bin 窗口' % (up if up is not None else -1))

    # ---------------------------------------------------- [1] 下之前先问 PL（铁律）
    rc_probe = _pl_probe()
    if rc_probe == 0 and not force:
        print('[load] ★ 拒绝下 bit：我们的流水线**正在跑**（pl_probe rc=0）')
        print('       往正在跑的链上下 bit -> fpga_manager PCFG_INIT 超时 -> PL 半编程')
        print('       -> 之后任何 AXI 读都可能挂死总线（实测过，只能断电）。')
        print('       要接力请用 --nobit；确实要重下再加 --force。')
        return RC_REFUSE
    if rc_probe == RC_UNKNOWN and not force:
        print('[load] ★ 拒绝下 bit：pl_probe 说不清（rc=9）—— 读不出结论时一律不下')
        return RC_UNKNOWN
    if rc_probe == RC_NOT_OURS:
        print('[load] pl_probe rc=1：我们的 bit 不在 PL 里 -> 下 bit 正是它授权的动作')
    elif rc_probe == 2:
        print('[load] pl_probe rc=2：bit 在但没出流 -> 重下是安全的（还顺带重掷传感器骰子）')
    elif force:
        print('[load] --force：明知 rc=%d 也要下（出了事就是自己按下去了）' % rc_probe)

    # ---------------------------------------------------------------- [2] md5 对账
    # ★ 无论走不走对账，**先把"我在下哪个文件"打出来** —— 本次事故里最贵的一句话
    #   就是这行：日志全绿，却没人看得出下的是 /boot 里的旧 bit。
    print('[load] bit 文件 = %s' % BIT)
    if '--any' not in argv:
        want, src = _expected_md5(argv)
        print('[load] 期望 md5 来自 %s' % src)
        if want is None:
            print('[load] ★ 说不清期望 md5 —— 不下（读不出结论时一律不下）')
            return RC_UNKNOWN
        try:
            got = md5_of(BIT)
        except Exception as e:
            print('[load] 读不了 bit 文件（%s）：%s' % (BIT, e))
            return RC_UNKNOWN
        if got != want:
            print('[load] ★ bit 文件 md5 与期望不符 —— 不下（免得把别的/半截文件写进 PL）')
            print('       文件 = %s' % got)
            print('       期望 = %s（换一份用 --md5，明知故犯用 --any）' % want)
            return RC_REFUSE
        print('[load] bit md5 对账通过：%s' % got)
    else:
        print('[load] --any：跳过 md5 对账（自己知道在下什么）')

    # -------------------------------------------------------------------- [3] 下 bit
    from pynq import Bitstream
    print('[load] 下 bit：%s ...' % BIT)
    t0 = time.time()
    Bitstream(BIT).download()
    print('[load] 已下载（%.1fs）' % (time.time() - t0))
    time.sleep(1.0)                       # 给 fpga_manager 收尾留一点时间

    # --------------------------------------------------------- [4] 下完立刻读回自检
    print('[load] === 下完复查 ===')
    rc2 = _pl_probe()
    if rc2 in (0, 2):
        print('[load] => 成功：PL 里现在是**我们的 bit**（pl_probe rc=%d）' % rc2)
        return RC_OK
    if rc2 == RC_NOT_OURS:
        print('[load] => 失败：下完**仍然**不是我们的 bit（下飞了？）')
        return RC_NOT_OURS
    print('[load] => 说不清：复查 rc=%d（别继续跑采集，先看 pl_probe 的输出）' % rc2)
    return RC_UNKNOWN


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
