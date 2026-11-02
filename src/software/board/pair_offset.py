# -*- coding: utf-8 -*-
"""pair_offset.py — 连抓几帧，逐帧量"像素网格偏了几个字节"

为什么要有这个：map_period.py 用"平均水平梯度"证明抓回的整帧整体偏了**一个字节**
（偏移 0 梯度 7332 = 噪点；偏移 1 梯度 648 = 真画面）。但"偏移量是不是每帧都变"
决定了病因：

  每帧固定偏 1 字节 → SOF 放置是确定性地早/晚一个字节（改采样沿或整体挪一位）
  **每帧偏移不同    → 帧首落在行中间、且落点随帧变化**（push 被 dropping/wr_rst_busy
                       挡住的时机不同）→ 正是第十五版"帧首只在行首转正"要治的病

判据：看 3 帧的最佳偏移是否一致、以及 offset=0 与最佳值差多少。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/pair_offset.py [--n 3]
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

W, H = 1024, 720


def best_offset(raw):
    """对行内字节偏移 0..3 各算平均 |水平梯度|，返回 (最佳偏移, 各偏移梯度列表)"""
    a = np.asarray(raw, np.uint8).reshape(H, W * 2)
    grads = []
    for off in range(4):
        if off == 0:
            seg = a
        else:
            seg = np.empty_like(a)
            seg[:, :W * 2 - off] = a[:, off:]
            seg[:, W * 2 - off:] = 0
        pix = (seg[:, 0::2].astype(np.int32) | (seg[:, 1::2].astype(np.int32) << 8))
        grads.append(float(np.abs(np.diff(pix, axis=1)).mean()))
    return int(np.argmin(grads)), grads


def main():
    n = 3
    for i, a in enumerate(sys.argv):
        if a == '--n' and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])

    # 先下 bit（用 Bitstream 直下，不用 Overlay —— 后者在本板 hwh 上会段错误，
    # 且 SIGSEGV 不是 Python 异常，try/except 拦不住，见 stream_ledger.py 的注释）
    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')

    from ball_detect import _make_cap, _init_video
    cap = _make_cap()
    _init_video(cap)

    print('')
    print('==== 连抓 %d 帧，逐帧量像素网格偏移 ====' % n)
    offs = []
    for k in range(n):
        try:
            idx, raw = cap.frame(timeout=3.0)
        except Exception as e:
            print('  帧 %d ✗ %s' % (k + 1, str(e)[:110]))
            continue
        off, grads = best_offset(raw)
        offs.append(off)
        print('  帧 %d (buf%d)：最佳偏移 = %d   [偏移0..3 梯度] %s'
              % (k + 1, idx, off, ' '.join('%6.0f' % g for g in grads)))
        time.sleep(0.4)

    print('')
    if not offs:
        print('  一帧都没抓到 —— 先解决采集')
    elif len(set(offs)) == 1:
        print('  三帧偏移**完全一致**（都是 %d）→ 确定性偏差：'
              '帧首系统性地早/晚一个字节（改采样沿或整体挪一位）' % offs[0])
        print('  注意：即使一致，只要板端 map 与黄金模型用的是**同一个**帧首，'
              'C) 项本该照样对得上 —— 对不上就说明两次抓取之间偏移变了。')
    else:
        print('  ★ 帧间偏移**不一致**：%s → 帧首落点随帧变化（落在行中间），'
              '正是"帧首只在行首转正"要治的病' % offs)
    return 0


if __name__ == '__main__':
    sys.exit(main())
