# -*- coding: utf-8 -*-
"""nblock_check.py — 「每局块数 n=1..9」的 **PL vs 黄金模型逐位一致** 终审（板载）

背景（2026-10-07，规则书 §1"第 n 局 n 块、封顶 9"落地）：PL 的场表 NBLK=9 早就在位
（`v3_bundle_axi.v` 的 blk_tab 按 9 建），游戏每局用
`build_world(seed, b, n_blocks=min(round,9))` 重建场景——"PL 对**任意块数**的场景
都与黄金模型逐位一致"这条契约必须**上板实测**（RTL 是数据驱动的，这是终审口径；
PC 上只能验黄金模型自己，验不了 PL）。

做法：对 n=1..9 各建一场（seed/±0.40/球位固定）→ `scan_bundle(use_pl=True,
arcs=True)` 走 17 路——arcs 路径会在 scan_bundle 里**当场自检**并打印
"[自检] 黄金模型 vs PL 回读：端点 逐位一致（17/17）判定 逐位一致（17/17）"，
本脚本逐 n 检查那一行。

用法（板上；★ 这是 PYNQ 客户端——跑之前必须先停游戏，绝不同时两个 VDMA 客户端）：
  echo xilinx | sudo -S -E bash -lc "cd /home/xilinx/wp && \\
      /usr/local/share/pynq-venv/bin/python3 -u nblock_check.py"
（本脚本**不下载 bit**：PL 里必须已经是我们的 bit——先 pl_probe / load_bit 常规序列。）
"""
import contextlib
import io
import sys
import time

HERE = '/home/xilinx/wp'
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import v3_scan as vs          # noqa: E402
import v3_fixedpoint as V     # noqa: E402

SEED = 7
B = 0.40
POS = (300.0, 230.0)          # 场内球位（与定妆照同款口径）
ANG, SPD = 0.0, 400.0
SCAN_WIN = 8                  # ★ int！scan_bundle 的 win 会进 range()——17 路（±8°）
FAN_HALF = 12.0               # 显示淡扇半宽（只影响画面，不影响判定）
STEPS = 600


def main():
    ok_all = True
    print('nblock_check —— n=1..9 逐位一致终审（seed=%d, b=±%.2f, 球位(%g,%g)）'
          % (SEED, B, POS[0], POS[1]), flush=True)
    for n in range(1, 10):
        w = vs.build_world(SEED, B, n_blocks=n)
        nblk = len(w.blocks)
        sc = dict(fields=[(b.x0, b.y0, b.x1, b.y1, b.b) for b in w.blocks],
                  holes=list(w.holes), x0=POS[0], y0=POS[1], ang0=ANG, sp=SPD,
                  q=1, steps=STEPS, bounce=vs.PL_BOUNCE, side=0, fan_half=FAN_HALF)
        buf = io.StringIO()
        t0 = time.perf_counter()
        try:
            with contextlib.redirect_stdout(buf):
                vs.scan_bundle(w, V, vs._MM_PL, sc, SCAN_WIN, True, True)
            err = ''
        except Exception as e:
            err = str(e)[:160]
        ms = (time.perf_counter() - t0) * 1000.0
        out = buf.getvalue()
        bit_ok = ('逐位一致' in out) and ('不一致' not in out)
        ok = (nblk == n) and bit_ok and not err
        ok_all = ok_all and ok
        if ok:
            print('  n=%2d -> 块数=%d  自检=逐位一致 ✓  (%.0f ms)' % (n, nblk, ms),
                  flush=True)
        else:
            tail = (err or out.strip().replace('\n', ' | '))[-200:]
            print('  n=%2d -> 块数=%d  自检=★不一致 ✗  (%.0f ms)  %s'
                  % (n, nblk, ms, tail), flush=True)
    print('==== nblock_check：%s ====' % ('全过 ✓' if ok_all else '★有失败，见上'),
          flush=True)
    return 0 if ok_all else 1


if __name__ == '__main__':
    sys.exit(main())
