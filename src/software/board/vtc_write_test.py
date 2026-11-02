# -*- coding: utf-8 -*-
"""vtc_write_test.py —— 对 0x43C20000 做**写**测试（只写，不读）。

为什么要有它（2026-10-01，M12 谜题）：
  到现在为止**所有**对 0x43C20000 的探测都是 `read`，结果全是 SIGBUS。
  但 AXI Crossbar 每个 MI 口有一个我们**从没检查过**的属性：
      M<k>_A00_READ_WRITE_MODE   (READ_WRITE / READ_ONLY / WRITE_ONLY)
  若被配成 WRITE_ONLY，"读不出"就是**正确行为**而不是故障 ——
  这一下就能解释"15 格活、唯独它死"却又"静态产物全对"。

判据（一次实验分清三件事）：
  · 写也 SIGBUS   -> 这一格读写都没译码 => 译码项真的缺了（要重建）
  · 写成功        -> 是只写/半配 => 方向完全变了，去查 READ_WRITE_MODE
  · 写成功且 rc=0 -> 顺带说明该地址**有从机应答**，不是空洞

★ 安全性（写比读危险，所以逐条交代）：
  · 写的是 **偏移 0x10 = v_tc 的 VER 寄存器，只读**。对只读寄存器写，AXI4-Lite 从机
    按惯例返回 OKAY 并**忽略**，无副作用。
  · 不写 0x00(SW_RST)：虽然它写 1 才复位、写 0 无害，但没必要冒险。
  · 已知 0x43C20000 的读返回 **DECERR**（干净 SIGBUS）。AXI 的读/写译码是同一套
    组合逻辑，所以写**不可能**被路由到别的从机上 —— 不存在"误写别人寄存器"。
  · 仍放在**独立子进程**里：SIGBUS 是信号不是异常，try/except 拦不住。

用法（板上，root + 登录 shell）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u vtc_write_test.py"

退出码：0 = 写成功；1 = 写 SIGBUS；9 = 前提不满足（没敢碰）
"""

import os
import subprocess
import sys

FPGA_STATE = '/sys/class/fpga_manager/fpga0/state'
BASE = 0x43C20000          # vtc_out (M12) 槽
OFF = 0x10                 # v_tc VER，只读寄存器
PATTERN = 0xAAAAAAAA       # 一个明显不会被误认成"真实配置"的图案


def child():
    """子进程入口：**只做写**，一次读都不做（读会先 SIGBUS 杀掉进程）。"""
    from pynq import MMIO
    m = MMIO(BASE, 0x10000)
    m.write(OFF, PATTERN)
    sys.stdout.write('WROTE 0x%08X to 0x%08X+0x%X\n' % (PATTERN, BASE, OFF))
    sys.stdout.flush()
    return 0


def main(argv):
    if '--child' in argv:
        return child()

    try:
        state = open(FPGA_STATE).read().strip()
    except Exception as e:
        state = '读不到(%s)' % str(e)[:40]
    print('[wt] fpga_manager state = %s' % state)
    if state != 'operating':
        print('[wt] ★ 不是 operating = PL 里没有 bit = 一个寄存器都不碰')
        print('[wt]   （PL 空时 AXI 事务没人应答，Zynq 互连没有超时 -> 整机挂死）')
        return 9

    print('[wt] 目标 0x%08X + 0x%X  (v_tc VER，只读；对本地址写无副作用)' % (BASE, OFF))
    print('[wt] 在隔离子进程里只做一次写 —— 读会先 SIGBUS，所以本脚本不读')
    try:
        r = subprocess.run(
            [sys.executable, '-u', os.path.abspath(__file__), '--child'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    except subprocess.TimeoutExpired:
        print('[wt] 子进程 30s 没回 —— 挂住了，**先别再碰这个地址**')
        return 9

    out = r.stdout.decode('utf-8', 'replace').strip()
    if r.returncode == 0:
        print('[wt] rc=0  %s' % out)
        print('')
        print('[wt] ★★ 结论：**写进去了** —— 这个地址上有从机应答。')
        print('[wt]    那"读 SIGBUS"就不是空洞，而是这格被配成了**只写**（或读通路坏了）。')
        print('[wt]    下一步查 BD 里 M12 的 READ_WRITE_MODE，**不用重建**就能修。')
        return 0
    if r.returncode == -7:
        print('[wt] rc=-7 SIGBUS  %s' % out)
        print('')
        print('[wt] ★★ 结论：**写也是 SIGBUS** —— 这格的读写都没译码。')
        print('[wt]    => 不是只写/只读问题，是 M12 的译码项在硅片里真的不存在。')
        print('[wt]    => 只能重建（BD 重新生成 + 重综合 + 重实现）。')
        return 1
    print('[wt] rc=%d  %s' % (r.returncode, out))
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
