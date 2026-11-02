# -*- coding: utf-8 -*-
"""temporal_stab.py — 把"raw 和 map 相隔多久"这**一个**变量单独量出来

为什么要有它：

  vision_diag 的 C 项一直只有 ~47% 的块落在 1 LSB 内（判据要 ≥98%）。
  本地把差值**拆到通道**看，得到：

        通道   相同     +1      -1     平均差
        R     70.9%   15.7%   12.3%   +0.042
        G     63.2%   17.9%   15.4%   +0.020
        B     68.9%   20.3%    9.5%   +0.115

  三通道全是 ±1 LSB，而且 R/G 的 +1 与 -1 几乎对称。**这是随机噪声的签名，
  不是算术口径问题** —— 口径不一致（四舍五入 vs 截断）会是单边的（全 +1）。
  再把三个通道乘起来：0.709 × 0.632 × 0.689 = 30.9% 的块应当三通道同时相同，
  与验收里报的"完全相同 34.25%"吻合。也就是说：黄金模型的算术没问题，
  RTL 的块平均也没问题，差值全部来自**两张不同的帧**。

  而 C 的测法里：raw 在 vision_diag.py:148 抓，map 在 :224 读，中间隔着
  `time.sleep(1.0)`（D 项测帧率），两者相差约 35 帧。传感器 AGC/AWB 在这
  1.2 秒里动一点点，整片像素就整体偏 1，块平均跟着偏 1。

本脚本做的事：raw 抓完**立刻**读 map（不 sleep、不测帧率、不做任何别的事），
  连做 N 轮，最后再来一个"慢轮"（前面真的 sleep 1.2s，复现 vision_diag 的条件）。
  于是本地可以同时算出三条曲线：

    1) 同一轮内  golden(raw_i) vs map_i        —— 紧配对下 C 项的成绩
    2) 相邻两轮  golden(raw_i) vs golden(raw_i+1) —— ~1 帧间隔的纯时间噪声
    3) 紧轮 vs 慢轮 golden(...)                —— 1.2 秒间隔的纯时间噪声

  判读：
    · 若 (1) 明显好于 47%（比如 ≥98%）→ C 的问题是**测法**，RTL 无罪，
      验收脚本改成"紧配对"即可。
    · 若 (2) 就已经很差（~30% 相同）→ 画面/传感器本身在动，缩略图永远对不上
      单张原图；只能让场景静止（固定灯光/盖上镜头/对准静态物）再验收。
    · 若 (3) 远差于 (2) → 时间漂移主导，坐实 AGC/AWB 漂移这个根因。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/temporal_stab.py --n 8
    ... --nobit   不重下 bit（刚跑过 dvp_probe 确认传感器健康时用，健康态要珍惜）

输出（都在 /boot）：
    stab_raw_%d.bin     第 i 张全分辨率原图（1024x720x2 字节）
    stab_map_%d.bin     第 i 张缩略图（128x90 个 uint16，按板端字节序原样）
    stab_summary.txt    每轮的帧号与耗时
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

BW, BH = 128, 90
MAP_BYTES = BW * BH * 2                 # 23,040
MAP_BASE = 0x43B00000
CTRL_BASE = 0x43B10000
BIT = '/boot/ov5640_m1a_wrapper.bit'


def _read_map(mm, mc, tries=6):
    """在**不跨帧**的前提下读一份 map 快照：帧号读前后各一次，相等才算数。

    帧周期约 36ms，读 23KB 只要几毫秒，所以绝大多数情况第一次就过。
    """
    for _ in range(tries):
        a = mc.rd_u32(0x08)
        buf = mm.rd_u8_copy(0, MAP_BYTES)
        b = mc.rd_u32(0x08)
        if a == b:
            return a, buf
    return None, None


def main():
    n = 8
    if '--n' in sys.argv:
        n = int(sys.argv[sys.argv.index('--n') + 1])
    nobit = '--nobit' in sys.argv

    # ---------------------------------------------------------------- 加载
    if nobit:
        print('[加载] --nobit：沿用当前已配置的 PL')
    else:
        from pynq import Bitstream
        Bitstream(BIT).download()       # ★ 不要用 Overlay（本板会段错误）
        print('[加载] bit 已下载（Bitstream 直下）')

    import vision_diag as vd            # 只借它的 _grab_raw（含卡死救流）
    from ball_detect import _make_cap, _init_video

    cap = _make_cap()
    _init_video(cap)
    print('[初始化] 采集链就绪')

    # ---------------------------------------------------------------- 热身
    # AGC/AWB 在上电头几帧是大步长收敛的，那几帧拿去比较毫无意义，先扔掉。
    warm = 0
    for k in range(4):
        try:
            cap.frame(timeout=5.0)
            warm += 1
        except Exception as e:
            print('[热身] 第 %d 帧没来：%s' % (k + 1, str(e)[:100]))
    print('[热身] 扔掉 %d 帧（AGC/AWB 稳定前的值不参与比较）' % warm)

    swap = 1 if getattr(cap, '_swap_decided', False) else 0
    print('[字节序] swap_bytes = %d（沿用驱动的通道相关性判定）' % swap)

    rows = []
    with FastMem(MAP_BASE, 0x8000) as mm, FastMem(CTRL_BASE, 0x1000) as mc:
        mc.wr_u32(0x00, (swap << 2) | 0x02)     # soft_rst 清 sticky 错误位
        time.sleep(0.2)
        mc.wr_u32(0x00, (swap << 2))
        print('[配置] ctrl = 0x%02x' % (swap << 2))

        # -------------------------------------------------- N 个"紧轮"
        print('')
        print('==== 紧轮：raw 抓完立刻读 map，中间不 sleep ====')
        for i in range(n):
            t0 = time.time()
            raw, idx = vd._grab_raw(cap)            # 一有新帧就返回
            t1 = time.time()
            fcnt, buf = _read_map(mm, mc)           # ★ 立刻读，不插任何事
            t2 = time.time()
            if fcnt is None:
                print('★ 第 %d 轮读 map 拿不到一致快照' % i)
                return 1
            raw.tofile('/boot/stab_raw_%d.bin' % i)
            np.frombuffer(bytes(buf), np.uint16).tofile('/boot/stab_map_%d.bin' % i)
            rows.append(('tight', i, idx, fcnt, t0, t1, t2))
            print('[紧 %d] vdma帧号 %-3s  map帧号 %-3d  抓帧 %5.0fms  读图 %5.1fms'
                  % (i, idx, fcnt, (t1 - t0) * 1000, (t2 - t1) * 1000))

        # -------------------------------------------------- 1 个"慢轮"
        # 复现 vision_diag 的真实条件：中间隔着 D 项的 time.sleep(1.0)。
        print('')
        print('==== 慢轮：先 sleep 1.2s 再抓（复现 vision_diag 的 C 条件）====')
        time.sleep(1.2)
        t0 = time.time()
        raw, idx = vd._grab_raw(cap)
        t1 = time.time()
        fcnt, buf = _read_map(mm, mc)
        t2 = time.time()
        if fcnt is None:
            print('★ 慢轮读 map 拿不到一致快照')
            return 1
        raw.tofile('/boot/stab_raw_slow.bin')
        np.frombuffer(bytes(buf), np.uint16).tofile('/boot/stab_map_slow.bin')
        rows.append(('slow', n, idx, fcnt, t0, t1, t2))
        print('[慢] vdma帧号 %-3s  map帧号 %-3d  抓帧 %5.0fms  读图 %5.1fms'
              % (idx, fcnt, (t1 - t0) * 1000, (t2 - t1) * 1000))

    # ---------------------------------------------------------------- 汇总
    tight = [r for r in rows if r[0] == 'tight']
    dt = [(b[4] - a[4]) for a, b in zip(tight, tight[1:])]
    print('')
    print('紧轮之间的间隔(ms)：%s' % ' '.join('%.0f' % (x * 1000) for x in dt))
    if tight:
        print('紧轮 → 慢轮的间隔(ms)：%.0f' % ((rows[-1][4] - tight[-1][4]) * 1000))
        print('（一帧 = %.1f ms，所以紧轮彼此相距约 %.1f 帧）'
              % (1000.0 / 28.0, (sum(dt) / len(dt) * 1000) / (1000.0 / 28.0) if dt else 0))

    with open('/boot/stab_summary.txt', 'w') as f:
        f.write('kind idx vdma_fcnt map_fcnt t_grab_start t_raw_done t_map_done\n')
        for r in rows:
            f.write('%s %d %s %d %.6f %.6f %.6f\n'
                    % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
    print('')
    print('[输出] /boot/stab_raw_*.bin + /boot/stab_map_*.bin + /boot/stab_summary.txt')
    print('       把 stab_*.bin 拉到 PC，用 software/pc/stab_analyze.py 画曲线')
    return 0


if __name__ == '__main__':
    sys.exit(main())
