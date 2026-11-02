# -*- coding: utf-8 -*-
"""vsize_fit.py — 拟合 VDMA 该按多少行收帧（VSIZE 真值）

为什么需要这个：桥的输出是每场 ~718~720 行 eol，而驱动一直按 **722** 行收，
差的这几行会让 VDMA 判"帧尺寸不符"(fsize_err) 把整帧丢掉 —— 实测约 1/3 帧被丢
→ 17.6fps 而不是 30fps。

为什么要扫而不是算：桥内部的账本（c_eolw/c_vsync）只给出"窗口内总数/帧数"，
受窗口两端**半帧**影响，算出来的每场行数在 715~726 之间晃，不足以分辨
718/719/720。而"帧尺寸不符"是 VDMA 自己判的、非黑即白 —— 直接扫它最可靠。

用法（板上，bit 已下过即可）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/vsize_fit.py [每档帧数]
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from pynq import MMIO
from vdma_capture import S2mmCapture

W = 1024
CAND = (722, 723, 721, 720, 719, 718, 717)      # 722/723 是现状与对照


def one(vsize, nframe):
    cap = S2mmCapture(W, vsize)
    cap.start()
    # 与 run_selfcheck / live_stream 同款：ctrl[3] 脉冲让"sink 先就绪、源后开"
    mm3 = MMIO(0x43A30000, 0x10000)
    c0 = mm3.read(0x00, 4)
    mm3.write(0x00, c0 | (1 << 3))
    time.sleep(0.3)
    mm3.write(0x00, c0)
    time.sleep(0.5)
    try:
        cap.clear_errors()
    except Exception:
        pass
    time.sleep(0.30)

    t0, ts, got = time.time(), [], 0
    while got < nframe and time.time() - t0 < 25:
        t1 = time.time()
        try:
            cap.frame(timeout=3.0)
        except Exception as e:
            print('    抓帧异常：%s' % str(e)[:90])
            break
        ts.append(time.time() - t1)
        got += 1
    dts = np.array(ts) * 1000.0
    nerr = len(cap.errors)
    # 丢掉第一个（启动瞬态那帧往往慢），看稳态
    steady = dts[1:] if len(dts) > 1 else dts
    print('  VSIZE=%-4d 抓到 %2d/%d 帧   平均 %6.0f ms/帧（%.1f fps）   '
          '稳态 %6.0f ms（%.1f fps）   错误事件 %d 次'
          % (vsize, got, nframe, dts.mean() if len(dts) else 0,
             (1000.0 / dts.mean() if len(dts) else 0),
             steady.mean() if len(steady) else 0,
             (1000.0 / steady.mean() if len(steady) else 0), nerr))
    cap.stop()
    return got, nerr, float(steady.mean()) if len(steady) else 1e9


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print('[拟合] 每档抓 %d 帧，看谁零错误、谁最快' % n)
    res = {}
    for v in CAND:
        print('--- VSIZE=%d ---' % v)
        try:
            res[v] = one(v, n)
        except Exception as e:
            print('  该档失败：%s' % str(e)[:120])
    print('')
    print('==== 汇总（错误少优先，其次稳态度快）====')
    ok = [(v, r) for v, r in res.items() if r[1] == 0]
    for v, r in sorted(res.items(), key=lambda kv: (kv[1][1], kv[1][2])):
        flag = '  ← 零错误' if r[1] == 0 else ''
        print('  VSIZE=%-4d 错误 %2d 次   稳态 %6.1f ms/帧%s' % (v, r[1], r[2], flag))
    if ok:
        best = min(ok, key=lambda kv: kv[1][2])
        print('')
        print('★ 建议 VSIZE = %d（零错误且最快：%.1f ms/帧 = %.1f fps）'
              % (best[0], best[1][2], 1000.0 / best[1][2]))
        print('  改法：vdma_capture.py 的默认 VSIZE（或调用处传的 height）改成这个值')
    else:
        print('')
        print('★ 没有任何一档零错误 —— 说明不只是 VSIZE 的问题，'
              '行数本身可能在抖（下一步量每帧行数的分布）')


if __name__ == '__main__':
    main()
