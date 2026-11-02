# -*- coding: utf-8 -*-
"""stab_analyze.py — 把 temporal_stab.py 抓下来的帧拆开，分离"时间噪声"与"算术误差"

输入（temporal_stab.py 在板上生成的，拉到 PC）：
    stab_raw_0.bin ... stab_raw_{n-1}.bin   紧轮的原图
    stab_raw_slow.bin                       慢轮（前置 sleep 1.2s）的原图
    stab_map_0.bin ... stab_map_{n-1}.bin   与上面一一对应的缩略图
    stab_map_slow.bin

本脚本算三张矩阵，一张比一张关键：

  [矩阵 1] golden(raw_i) vs golden(raw_j)   ← **纯时间噪声**（板上 RTL 完全不参与）
           这是"同样的算术、同样的口径、只换帧"的差值。
           ★ 如果这张矩阵的对角线之外就已经只有 ~30% 相同，那么 map 永远对不上
             单张 raw —— 与 RTL 无关，C 项就永远不可能过，除非画面静止。

  [矩阵 2] map_i vs map_j                   ← 板端输出自身的时间稳定性
           与矩阵 1 同量级 → 板端忠实地反映了输入的抖动。

  [矩阵 3] golden(raw_i) vs map_j  (全交叉) ← 找真正的配对
           对角线（i==j）就是"紧配对"的 C 项成绩。
           若某个偏移 k 上的 golden(raw_i) vs map_{i+k} 明显更好，说明板端流水线
           深度是 k 帧，验收脚本应该按这个偏移配对。

判读标准（与 vision_diag 的验收口径一致：完全相同率、以及 ≤1LSB 率）：
    紧配对 ≈ 98%+   → RTL 无罪，改测法
    紧配对 ≈ 慢轮    → 时间不是主因，回到 RTL 找结构问题
    慢轮 << 紧配对   → 坐实时间漂移（AGC/AWB）

用法（PC，Git Bash）：
    /c/Python314/python software/pc/stab_analyze.py [目录]      # 默认 ../../stab
"""

import os
import sys

sys.path.insert(0, 'E:/py_pkgs/vision')                 # numpy 2.5.3（C 盘的小环境）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import vision_golden as vg

BW, BH = 128, 90


def load_raw(path):
    a = np.fromfile(path, np.uint8)
    if a.size < 1024 * 720 * 2:
        raise IOError('%s 只有 %d 字节，不像一张原图' % (path, a.size))
    return a


def load_map(path):
    return np.frombuffer(open(path, 'rb').read(), np.uint16).reshape(BH, BW)


def golden(path, swap):
    pix = vg.pixels_from_raw(load_raw(path))
    if swap:
        pix = vg.swap16(pix)
    return vg.block_avg(pix)


def pct(a, b, tol=0):
    """三通道**同时** |Δ| <= tol 的块比例（%）。tol=0 即三通道全等。

    ★ 不能拿打包值直接相减：RGB565 里 R 的 1 LSB = 2048、G = 32、B = 1，
      打包口径实际上只允许 R/G 一位不差 —— 那会把"块平均对不对"测成
      "这张画面的 R 平均值能不能一比特不差"，动态场景下永远过不了。
      详见 vision_golden.compare 的说明。
    """
    bad = None
    for sh, msk in ((11, 0x1F), (5, 0x3F), (0, 0x1F)):
        d = np.abs(((a >> sh) & msk).astype(np.int32) -
                   ((b >> sh) & msk).astype(np.int32)) > tol
        bad = d if bad is None else (bad | d)
    return float((~bad).mean()) * 100.0


def matrix(title, items, fn, tol=0):
    """打印 len(items) x len(items) 的矩阵（单位 %）"""
    print('')
    print('=' * 72)
    print(title)
    print('=' * 72)
    head = '        ' + ''.join('%7s' % nm for nm, _ in items)
    print(head)
    for nm_a, a in items:
        cells = ''.join('%7.1f' % fn(a, b) for _, b in items)
        print('%-8s%s' % (nm_a, cells))


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'stab')
    d = os.path.abspath(d)
    print('目录：%s' % d)
    if not os.path.isdir(d):
        print('★ 目录不存在 —— 先把板上的 stab_*.bin 拉下来')
        return 1

    # ---------------------------------------------------------- 有哪些轮
    idxs = []
    i = 0
    while os.path.exists(os.path.join(d, 'stab_raw_%d.bin' % i)):
        idxs.append(i)
        i += 1
    names = [str(k) for k in idxs]
    paths = [os.path.join(d, 'stab_raw_%d.bin' % k) for k in idxs]
    mpaths = [os.path.join(d, 'stab_map_%d.bin' % k) for k in idxs]
    if os.path.exists(os.path.join(d, 'stab_raw_slow.bin')):
        names.append('slow')
        paths.append(os.path.join(d, 'stab_raw_slow.bin'))
        mpaths.append(os.path.join(d, 'stab_map_slow.bin'))
    if len(idxs) < 2:
        print('★ 少于 2 轮，没法比时间 —— 先跑 temporal_stab.py --n 8')
        return 1
    print('轮次：%s' % ' '.join(names))

    maps = [load_map(p) for p in mpaths]

    # ---------------------------------------------------------- 定字节序
    # 用"紧配对"的吻合率来判：黄金模型该不该 swap16，看哪一个更像板端。
    # ★ 判据用"三通道同时 <=1 LSB"，且**别用第 0 轮** —— 复位后头两帧的 map
    #   还是旧内容（temporal_stab 实测），拿它定字节序会把顺序判反。
    ref = min(2, len(maps) - 1)
    print('')
    print('---- 先定口径：紧配对下 swap=0/1 哪个像（用第 %d 轮，避开复位过渡帧）----' % ref)
    g0s = {s: golden(paths[ref], s) for s in (0, 1)}
    best_swap = max(g0s, key=lambda s: pct(g0s[s], maps[ref], 1))
    for s in (0, 1):
        print('  swap=%d : 三通道同时<=1LSB %5.1f%%   三通道全等 %5.1f%%'
              % (s, pct(g0s[s], maps[ref], 1), pct(g0s[s], maps[ref], 0)))
    print('  → 取 swap=%d（下面全部用它）' % best_swap)

    G = [golden(p, best_swap) for p in paths]

    # ---------------------------------------------------------- 矩阵 1
    matrix('[矩阵 1] 黄金模型 vs 黄金模型 —— **纯时间噪声**，板端 RTL 完全不参与',
           list(zip(names, G)), lambda a, b: pct(a, b, 1))
    print('（对角线必然是 100%；看的是**离对角线一格**就已经掉到多少）')
    print('')
    g_adj = [pct(G[k], G[k + 1], 1) for k in range(len(G) - 1)]
    print('相邻两轮（~1 帧间隔）三通道同时<=1LSB：%s'
          % ' '.join('%.1f%%' % x for x in g_adj))
    print('首轮 vs 末轮（~%d 帧间隔）：%.1f%%' % (len(G) - 1, pct(G[0], G[-1], 1)))

    # ---------------------------------------------------------- 矩阵 2
    matrix('[矩阵 2] map vs map —— 板端输出自身的稳定性',
           list(zip(names, maps)), lambda a, b: pct(a, b, 1))
    m_adj = [pct(maps[k], maps[k + 1], 1) for k in range(len(maps) - 1)]
    print('相邻两轮 map 三通道同时<=1LSB：%s' % ' '.join('%.1f%%' % x for x in m_adj))

    # ---------------------------------------------------------- 矩阵 3
    print('')
    print('=' * 72)
    print('[矩阵 3] 黄金(raw_i) vs map_j —— 全交叉，找真正的配对')
    print('=' * 72)
    head = '        ' + ''.join('%8s' % nm for nm in names)
    print(head + '   ← map_j')
    for nm, g in zip(names, G):
        cells = ''
        for m in maps:
            cells += '%8.1f' % pct(g, m, 1)
        print('%-8s%s' % ('golden ' + nm, cells))
    print('')
    print('★ 对角线 = 紧配对（C 项在正确测法下的成绩）')
    print('  看哪条斜线上的数最大 —— 那个偏移就是板端流水线的真实帧延迟。')

    # ---------------------------------------------------------- 逐通道拆解
    # ★ 用"稳定轮"：复位后头两轮的 map 还是旧内容（矩阵 2 头两行 2.5%/14.7% 就是它），
    #   拿过渡帧做逐通道统计只会得到一堆无意义的 |Δ|>1。
    st = min(3, len(idxs) - 1) if idxs else 0
    g, m = G[st], maps[st]
    print('')
    print('（下面用第 %d 轮：复位后前两轮是过渡帧，不参与统计）' % st)
    bR, bG_, bB = (m >> 11) & 0x1F, (m >> 5) & 0x3F, m & 0x1F
    gR, gG_, gB = (g >> 11) & 0x1F, (g >> 5) & 0x3F, g & 0x1F
    print('')
    print('---- 紧配对（第 0 轮）逐通道差值分布（单位 = 该通道的 1 LSB）----')
    print('%-4s %8s %8s %8s %8s %10s' % ('通道', '相同', '+1', '-1', '|差|>1', '平均差'))
    for nm, bb, gg in (('R', bR, gR), ('G', bG_, gG_), ('B', bB, gB)):
        dd = bb.astype(int) - gg.astype(int)
        print('%-4s %7.1f%% %7.1f%% %7.1f%% %7.1f%% %10.4f'
              % (nm, (dd == 0).mean() * 100, (dd == 1).mean() * 100,
                 (dd == -1).mean() * 100, (np.abs(dd) > 1).mean() * 100, dd.mean()))
    print('（+1 与 -1 对称 = 随机噪声；单边偏 = 算术口径。这一栏是判"谁有罪"的铁证）')

    # ---------------------------------------------------------- 结论
    # ★ 只统计"稳定轮"：第 0、1 轮的 map 是复位过渡帧，混进来会把结论整个拉歪
    #   （实测 map_0 vs 稳定帧只有 2.5%）。
    steady = [k for k in idxs if k >= 2]
    if not steady:
        steady = idxs
    tight = [pct(G[k], maps[k], 1) for k in steady]
    g_st = [pct(G[k], G[k + 1], 1) for k in steady[:-1]]
    m_st = [pct(maps[k], maps[k + 1], 1) for k in steady[:-1]]
    slow_t = pct(G[-1], maps[-1], 1) if len(G) > len(idxs) else None
    print('')
    print('=' * 72)
    print('结论（全部是"三通道同时 <=1 LSB"的比例；只算稳定轮 %s）'
          % ' '.join(str(k) for k in steady))
    print('  紧配对 golden(raw_i) vs map_i     ：%s'
          % ' '.join('%.1f%%' % x for x in tight))
    print('  紧配对的平均                      ：%.1f%%' % float(np.mean(tight)))
    if slow_t is not None:
        print('  慢轮（前置 sleep 1.2s，复现老测法）：%.1f%%' % slow_t)
    tn = float(np.mean(g_st)) if g_st else float('nan')
    print('  输入自身的帧间噪声 golden/golden  ：%.1f%%' % tn)
    print('  板端输出自身的帧间变化 map/map    ：%.1f%%'
          % (float(np.mean(m_st)) if m_st else float('nan')))
    print('')
    t1 = float(np.mean(tight))
    print('  → 板端 vs 黄金 %.1f%%，黄金自己帧间 %.1f%%，差 %+.2f 个百分点'
          % (t1, tn, t1 - tn))
    if t1 >= 98.0 or (tn == tn and t1 >= tn - 0.5):
        print('  ★★ **RTL 无罪**：板端与黄金模型的差，不超过黄金模型自己两张相邻帧')
        print('     之间的差 —— PL 没有引入输入噪声之上的任何额外误差。')
        print('     动态场景（屋里有人）下"绝对 98%"这条线本来就到不了，')
        print('     所以验收要看这个**差值**，不是看绝对值。')
    elif t1 < tn - 3:
        print('  ★ 板端比"黄金自己跟自己"还差 %.1f 个百分点 —— 这才是真正的 PL 误差，'
              % (tn - t1))
        print('    回到 RTL 找结构/算术问题（逐通道那一栏若单边偏 = 算术口径，散布 = 别处）。')
    else:
        print('  ★ 板端与黄金只差一点点：多半是配对还差一两帧，或在复位过渡期读的 map。')
        print('    试着把 map 读在 raw 抓取之前、并等复位后 0.5s 以上。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
