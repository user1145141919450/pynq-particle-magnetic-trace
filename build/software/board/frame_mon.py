# -*- coding: utf-8 -*-
"""frame_mon.py — 只干一件事：下完 bit 之后**不再碰它**，盯着帧计数看它到底怎么死的

★ 为什么要有它（2026-09-26 晚，验收连跑两轮实测）：
  两轮跑出的是**同一个规律** —— 每次刚下完 bit 链路都是健康的：
    · 第 1 轮 step2：28.0 fps、5760/5760 全覆盖；step3：wcnt 到 5696（写入是满的）
    · 第 2 轮 step2：28.0 fps、5760/5760 全覆盖、热身帧 mean 115.5
  然后在一两分钟内**自己烂掉**，而且两轮烂法不一样：
    · 第 1 轮 step4：0.0 fps（帧计数冻结在 565）、行字节数 587 = **奇数**
      （16bpp 的流不可能出现奇数字节 -> 读回来的根本不是那条流）
    · 第 2 轮 step3：wcnt 停在 1613（写入中途停死）；step4：只剩 8.0 fps
  而验收脚本**每一步之间**都会 `zocl_reload`（卸掉再装一次 zocl）+ 新起一个
  PYNQ 进程。所以有两种可能，这个脚本就是来分辨它们的：

    (A) **时间/环境**：链路自己会死 —— 供电跌落（记忆里的首选假说）、发热、
        传感器丢锁。特点是"没人碰它也会死"。
    (B) **被碰死**：`zocl_reload` / 新进程把正在跑的 VDMA 链路搅乱了。
        特点是"只要不碰就一直好"。

  判据：
    · 全程帧计数一直稳定涨（≈28fps）   -> 是 (B)：该改验收脚本的接力方式，
      别再每步都 reload，改成"一次配置、多步共用"
    · 某秒起冻结不动、且不再恢复      -> 是 (A)：凶手在硬件/环境
    · 冻结之后**又自己恢复**           -> 记下时刻。这种"自愈"很重要：它说明
      链路不是硬坏，而是有个可恢复的坏状态（例如传感器重新锁相）

★ 它和 wcnt_poll.py 的分工：后者每 5ms 采一次、`--n 120` 只覆盖 **0.6 秒**，
  是个"快照"；这个是**分钟的尺度**，专看"慢慢死掉"这个过程。

用法（板上）：
  sudo -E PYTHONIOENCODING=utf-8 python3 -u /boot/frame_mon.py [--n 180] [--period 2] [--nobit]
  不带 --nobit = 先下载 bit（= 重掷一次骰子），然后开始盯。
  ★ 全程只有这一个 PYNQ 客户端，绝不能和别的脚本同时跑（项目硬规矩）。

寄存器（与 wcnt_poll.py / bram_write_map2.py 一致）：
  0x43B10000 +0x08 = frame_cnt，+0x00 = stat（上一帧快照）
  0x43B20000 +0x08 = stat2：低 15 位 wcnt / bit15..20 errs / bit21..31 bcnt
"""

import sys
import time

sys.path.insert(0, '/boot')

from pynq import MMIO

CTRL_BASE = 0x43B10000
STAT_BASE = 0x43B20000


def main():
    n = 180
    period = 2.0
    args = sys.argv
    for i, a in enumerate(args):
        if a == '--n' and i + 1 < len(args):
            n = int(args[i + 1])
        if a == '--period' and i + 1 < len(args):
            period = float(args[i + 1])

    # 标准启动顺序（与 wcnt_poll.py 一致，别改）：先 bit，再 _init_video
    if '--nobit' not in args:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载（= 重掷一次骰子）')
    from ball_detect import _make_cap, _init_video
    _init_video(_make_cap())
    print('[启动] 采集链已按标准顺序起来')

    mmv = MMIO(CTRL_BASE, 0x1000)
    mms = MMIO(STAT_BASE, 0x1000)

    print('')
    print('  盯 %d 秒，每 %.1fs 记一次 —— 中途**不碰**任何寄存器，只读' % (n, period))
    print('  %-7s %-11s %-8s %-6s %-7s %-11s'
          % ('t(s)', 'frame_cnt', 'fps', 'errs', 'wcnt', 'stat'))
    print('  （fps 是这一段的**瞬时**速度 —— 看它是"慢慢掉速"还是"一刀切断"，'
          '这能区分发热/供电 与 接触/掉线）')

    t0 = time.time()
    prev = None
    prev_t = t0
    frozen_since = None
    frozen_spans = []      # [(起, 止 or None)]
    n_heal = 0
    n_ok = 0

    while time.time() - t0 < n:
        now = time.time()
        fc = mmv.read(0x08)
        stat = mmv.read(0x00)
        st2 = mms.read(0x08)
        wcnt = st2 & 0x7FFF
        errs = (st2 >> 15) & 0x3F
        fps = (fc - prev) / (now - prev_t) if prev is not None else -1.0

        mark = ''
        if prev is not None and fc == prev:
            if frozen_since is None:
                frozen_since = now
                mark = '  ★ 帧计数**冻结**（从这一刻开始）'
        else:
            if frozen_since is not None:
                n_heal += 1
                frozen_spans.append((frozen_since, now))
                mark = ('  ★ 冻了 %.1fs 后**自己恢复了**（第 %d 次自愈）'
                        % (now - frozen_since, n_heal))
                frozen_since = None
            else:
                n_ok += 1

        print('  %-7.1f %-11d %-8s 0x%02X   %-7d 0x%08X%s'
              % (now - t0, fc,
                 ('%.1f' % fps) if fps >= 0 else '-',
                 errs, wcnt, stat, mark))
        sys.stdout.flush()

        prev, prev_t = fc, now
        time.sleep(period)

    if frozen_since is not None:
        frozen_spans.append((frozen_since, time.time()))

    # ------------------------------------------------------------ 小结
    print('')
    print('=== 小结（盯了 %ds）===' % n)
    if not frozen_spans:
        print('  全程**一次都没冻结**（%d/%d 次采样在涨）' % (n_ok, n_ok))
        print('  -> 链路**自己**是稳的：凶手是步骤之间的折腾 ——')
        print('     `zocl_reload` + 每步新起 PYNQ 进程。该改验收脚本的接力方式了：')
        print('     一次 reload、之后 NoBit 接力，别每步都重来一遍。')
    else:
        total = sum(b - a for a, b in frozen_spans)
        print('  **冻结过 %d 次**，累计 %.1fs' % (len(frozen_spans), total))
        for i, (a, b) in enumerate(frozen_spans):
            print('    第 %d 次：t=%.1fs 起，持续 %.1fs%s'
                  % (i + 1, a - t0, b - a,
                     '（到结束都没恢复）' if b == frozen_spans[-1][1]
                     and frozen_spans[-1][1] >= t0 + n - 2 * period else ''))
        if n_heal:
            print('  -> 有 **%d 次自愈**：不是硬坏，是个可恢复的坏状态。' % n_heal)
            print('     注意"恢复"是靠时间自己好的，还是被下一次读弄好的。')
        else:
            print('  -> **冻住就没再起来** = 时间/环境相关（供电、发热、传感器丢锁），')
            print('     正是记忆里那条"供电边际"假说的表现型。')
            print('     下一步（要人动手）：摸电源适配器/板载稳压温升，')
            print('     换一个 >=12V/3A 的好电源再复跑同一序列。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
