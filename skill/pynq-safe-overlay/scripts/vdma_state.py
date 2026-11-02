# -*- coding: utf-8 -*-
"""vdma_state.py — 看一眼 VDMA 的 S2MM 通道现在是什么状态；`--halt` 顺手停掉它。

★ 为什么需要它（2026-09-26 第二十版，第 5 轮验收现场）：

  脚本**崩着退出**（段错误 / SIGKILL）时不会执行 atexit，而 VDMA 是 PL 硬件、
  也不随进程退出而停 —— 它会带着**已经释放的**缓冲地址继续跑，把下一个进程刚
  allocate 到的 CMA 页覆盖掉。第 5 轮验收的指纹就是这么来的：
    帧索引只有 1、相邻两帧一致性 10%（噪声底也 10%）、帧率 3fps、errs=0x08。

  所以在**下 bit 之前**和**起新采集之前**先跑一次本脚本，确认"没有野 DMA"：
  VDMA 的 DMACR/DMASR 是在 PL 里的，**不随进程退出清零**，读它就是读"上一轮
  留下的真实状态"，这是唯一能看见野 DMA 的窗口。

用法（板上）：
  python3 /home/xilinx/wp/vdma_state.py            # 只看
  python3 /home/xilinx/wp/vdma_state.py --halt     # 看 + 停（DMACR=0，安全：只停不重置）

退出码：
  0 = 通道没在跑（DMACR.RS=0，或 DMASR.halted=1）—— 下 bit / 起采集都安全
  1 = 通道还在跑（RS=1 且没 halted）—— 有野 DMA 的风险，先 --halt 再干别的
  9 = 没有结论（PYNQ 自己崩了 / 读不到寄存器）—— 当"不知道"，别硬来
"""

import sys

RC_STOPPED = 0
RC_RUNNING = 1
RC_UNKNOWN = 9


def main():
    sys.path.insert(0, '/home/xilinx/wp')
    try:
        from pynq import MMIO
        from vdma_capture import (VDMA_BASE, S2MM_DMACR, S2MM_DMASR,
                                  CR_RS, SR_HALTED, SR_ERR_MASK,
                                  SR_FRMCNT_SHIFT, decode_sr)
    except Exception as e:
        print('[VDMA] 取常量失败（%s: %s）—— 结论：未知' % (type(e).__name__, str(e)[:70]))
        return RC_UNKNOWN

    halt = '--halt' in sys.argv
    try:
        m = MMIO(VDMA_BASE, 0x1000)
        cr = m.read(S2MM_DMACR)
        sr = m.read(S2MM_DMASR)
    except Exception as e:
        print('[VDMA] 读寄存器失败（%s: %s）—— 结论：未知' % (type(e).__name__, str(e)[:70]))
        return RC_UNKNOWN

    print('[VDMA] DMACR=0x%08X（RS=%d）  DMASR=0x%08X'
          % (cr, 1 if cr & CR_RS else 0, sr))
    print('[VDMA] halted=%d idle=%d 阈值倒计数=%d 错误位=0x%02X'
          % (1 if sr & SR_HALTED else 0, 1 if sr & 2 else 0,
             (sr >> SR_FRMCNT_SHIFT) & 0xFF, (sr & SR_ERR_MASK) >> 4))
    print('[VDMA] 状态解读：%s' % decode_sr(sr))
    # ★ 错误位是 sticky（W1C）：它亮着只说明"曾经错过"，不说明现在还在错。
    #   所以判"有没有野 DMA"只看 RS/halted，不看错误位。

    if halt:
        try:
            m.write(S2MM_DMACR, 0x0000)          # 只清 RS，不写 RESET
            cr = m.read(S2MM_DMACR)
            print('[VDMA] 已停（DMACR=0x%08X，RS=%d）' % (cr, 1 if cr & CR_RS else 0))
        except Exception as e:
            print('[VDMA] ★ 停失败（%s: %s）' % (type(e).__name__, str(e)[:70]))
            return RC_UNKNOWN

    if (cr & CR_RS) and not (sr & SR_HALTED):
        print('[VDMA] => 通道**还在跑**：上一轮可能有野 DMA，先 --halt 再下 bit/起采集'
              '（exit %d）' % RC_RUNNING)
        return RC_RUNNING
    print('[VDMA] => 通道没在跑，下 bit / 起采集都安全（exit %d）' % RC_STOPPED)
    return RC_STOPPED


if __name__ == '__main__':
    sys.exit(main())
