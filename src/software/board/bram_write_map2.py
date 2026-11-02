# -*- coding: utf-8 -*-
"""bram_write_map2.py — 测出视核**究竟写了哪些字**（不冻结版，绕开 freeze 陷阱）

与 `bram_write_map.py` 的区别（很关键）：那一版要把视核**冻结**再填图案，但"撤销冻结"
会触发视核 resync（等下一个帧首才开工），实测会出现"视核整段没在跑 → 图案 100% 幸存"
的假结果（2026-09-25 凌晨踩过）。**本版全程不冻结**：

    1. 记下地图当前的校验和
    2. 往整张 map 填一个显眼图案（0xDEADxxxx）
    3. 让流自己跑 ~1 秒（视核每帧重写 5760 个字，30 帧/秒）
    4. 读回：**还留着图案的字 = 视核没写过的字**

判读：
    - 图案 0 幸存 → 5760 个字全被覆盖 → 地址覆盖完整，"1/4 之谜"在别处
    - 图案在某段连续区间幸存 → **地址覆盖只到那里**（地址/计数器问题）
      → 报告幸存字址范围、连续段与否、以及边界落在第几块行（每 64 字 = 1 块行）

★ 前提：视核此刻必须在跑。所以先打一个存活探针（frame_cnt 两次读数），
  不涨就直接报"视核没在跑，本次测量无效" —— 不能又交一个假结果。

用法（板上）：
    sudo -E PYTHONIOENCODING=utf-8 python3 /boot/bram_write_map2.py
"""

import sys
import time

sys.path.insert(0, '/boot')

import numpy as np

from fastmem import FastMem

MAP_BASE = 0x43B00000
MAP_SIZE = 0x8000
CTRL_BASE = 0x43B10000
NW = 5760                      # 缩略图占用的**32 位字**数（90 块行 x 64 字）
NU16 = NW * 2                  # 按 16 位字算是 11520


def main():
    # 必须把 VDMA 跑起来（否则流不走，视核根本不会写 —— 那测出来的是"没在跑"的假象）。
    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')
    # 标准启动序列（照抄 live_stream / run_selfcheck）：建驱动 → ctrl[3] 脉冲 →
    # 清瞬态错误 → **立刻**抓一帧。少任何一步都会"之后再也收不到帧"（老坑）。
    from ball_detect import _make_cap, _init_video
    cap = _make_cap()
    _init_video(cap)
    print('[启动] 采集链已按标准顺序起来')

    with FastMem(MAP_BASE, MAP_SIZE) as m, FastMem(CTRL_BASE, 0x1000) as mc:
        print('[配置] ctrl=0x%02x' % mc.rd_u32(0x00))

        # ---- 0) 存活探针：视核在跑吗？ ----
        a = mc.rd_u32(0x08)
        time.sleep(0.5)
        b = mc.rd_u32(0x08)
        print('[存活] frame_cnt %d -> %d（%+.1f fps）' % (a, b, (b - a) / 0.5))
        if b == a:
            print('  ★ 视核没在跑 —— 本次测量无效，先解决"视核为什么不跑"')
            print('    （判据见 watch_map.py / vstart_test.py；这版脚本不会再交假结果）')
            return 1

        # ---- 1) 填图案 ----
        pat = (0xDEAD0000 | np.arange(NW, dtype=np.uint32)).astype(np.uint32)
        m.view_u32(0, NW)[:] = pat
        back = m.view_u32(0, NW).copy()
        print('[填充] 写入 %d 个 32 位字，立即读回一致 %d' % (NW, int((back == pat).sum())))

        # ---- 2) 让流自己跑（不冻结！） ----
        t0 = time.time()
        time.sleep(1.0)
        c = mc.rd_u32(0x08)
        print('[跑帧] 1 秒内 frame_cnt %d -> %d（涨了才说明真在写）' % (b, c))
        if c == b:
            print('  ★ 这一秒里视核没完成任何帧 —— 结果不可信')
            return 1

        # ---- 3) 读回看谁被覆盖 ----
        now = m.view_u32(0, NW).copy()
        survived = (now == pat)
        cov = np.nonzero(~survived)[0]
        print('')
        print('[结果] 图案幸存 %d 字 / %d（幸存 = 视核没写）' % (int(survived.sum()), NW))
        if not survived.any():
            print('  => 5760 个字**全被覆盖**：地址覆盖完整 ✓ '
                  '"只写了前 1/4"的现象在别处（值/相位），不是地址问题')
            return 0
        if survived.all():
            print('  => 一个字都没写（视核没跑或写入被禁）')
            return 1
        print('  => 部分覆盖。被覆盖字址 %d .. %d（共 %d）'
              % (cov.min(), cov.max(), cov.size))
        idx = np.nonzero(survived)[0]
        cont = (idx.max() - idx.min() + 1) == len(idx)
        print('     幸存字址 %d .. %d，连续段: %s' % (idx.min(), idx.max(), '是' if cont else '否'))
        # 边界落在第几块行（每 64 字 = 1 块行）
        print('     → 覆盖到的最后一块行 ≈ %d（共 90 块行）' % (cov.max() // 64))
        # 逐块行的覆盖情况
        print('     每 10 块行的覆盖字数（满 = 64*10 = 640）：')
        for r0 in range(0, 90, 10):
            seg = cov[(cov >= r0 * 64) & (cov < min(r0 + 10, 90) * 64)]
            print('       块行 %2d~%2d: %4d' % (r0, min(r0 + 9, 89), seg.size))
        return 0


if __name__ == '__main__':
    sys.exit(main())
