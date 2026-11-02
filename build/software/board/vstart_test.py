# -*- coding: utf-8 -*-
"""vstart_test.py — 视核靠什么才会开工？（不重下 bit，只动 ctrl）

现场记录（2026-09-25 凌晨）：出现过"A/B/D 项全过、C 项 0.1%"的组合，但
`watch_map.py` 一测就发现 **frame_cnt 4 秒不动、map 校验和完全不变** —— 也就是说
视核根本没在跑，A/B 项读到的是**陈旧状态字**。而另一些运行里 frame_cnt 又在涨。
所以关键问题是：**它开不开工，取决于什么？**

RTL 事实（vision_core.v:198-230）：退出 resync 的唯一条件是 `beat && s_axis_tuser`，
而 `beat = s_axis_tvalid & s_axis_tready & ~freeze`；`soft_rst` 与"撤销 freeze"
都会把 resync 重新置 1（等下一个帧首）。所以本脚本按这个顺序试：

    A 基线：什么都不动，看 frame_cnt 涨不涨
    B 脉冲 ctrl[1]（soft_rst）→ 看涨不涨        ← "只有软复位才开工"的话这里会涨
    C 脉冲 ctrl[0]=1→0（冻结/解冻）→ 看涨不涨    ← "解冻才开工"的话这里会涨
    D 脉冲 ctrl[3]（视频域复位）→ 看涨不涨       ← _init_video 用的就是这个

判据：哪一步之后 frame_cnt 开始涨，游戏启动序列就必须包含哪一步。

用法（板上，**不重下 bit**）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/vstart_test.py
"""

import sys
import time

sys.path.insert(0, '/boot')

from fastmem import FastMem

CTRL_BASE = 0x43B10000


def main():
    with FastMem(CTRL_BASE, 0x1000) as mc:
        c0 = mc.rd_u32(0x00)
        print('[初值] ctrl = 0x%02x（freeze=%d soft_rst=%d swap=%d）'
              % (c0, c0 & 1, (c0 >> 1) & 1, (c0 >> 2) & 1))

        def rate(tag, hold=1.2):
            a = mc.rd_u32(0x08)
            time.sleep(hold)
            b = mc.rd_u32(0x08)
            print('  %-34s frame_cnt %5d -> %5d  (%+.0f fps)'
                  % (tag, a, b, (b - a) / hold))
            return b - a

        print('')
        print('==== 逐个试"什么能让视核开工" ====')
        r = rate('A 基线（什么都不动）')
        if r > 0:
            print('  → 视核本来就在跑，A/B 项可以信；那 C 项的问题另找')
            return 0

        mc.wr_u32(0x00, c0 | 0x02)                 # soft_rst = 1
        time.sleep(0.2)
        mc.wr_u32(0x00, c0)
        time.sleep(0.3)
        r = rate('B 脉冲 ctrl[1]（soft_rst）后')
        if r > 0:
            print('  ★ 结论：**只有软复位之后才开工** → 启动序列必须脉冲 soft_rst '
                  '（而且要多等一帧让 resync 撞上帧首）')
            return 0

        mc.wr_u32(0x00, c0 | 0x01)                 # freeze = 1
        time.sleep(0.3)
        mc.wr_u32(0x00, c0)                        # 撤销冻结
        time.sleep(0.3)
        r = rate('C 脉冲 freeze 1->0 后')
        if r > 0:
            print('  ★ 结论：**解冻之后才开工**（resync 由 freeze 撤销触发）')
            return 0

        mc.wr_u32(0x00, c0 | 0x08)                 # 视频域复位 = _init_video 的做法
        time.sleep(0.3)
        mc.wr_u32(0x00, c0)
        time.sleep(0.5)
        r = rate('D 脉冲 ctrl[3]（视频域复位）后')
        if r > 0:
            print('  ★ 结论：**视频域复位之后才开工**（_init_video 用的就是这条）')
            return 0

        print('')
        print('  ★ 四步全都不涨 —— 视核卡在 resync 且帧首没能把它叫醒。')
        print('    下一步要查的是"它到底有没有收到 tuser"：拿探针 w1 的增量对同一窗口，')
        print('    若 w1 在涨而视核不醒 → 病在视核的 resync 退出条件（要改 RTL）。')
        return 1


if __name__ == '__main__':
    sys.exit(main())
