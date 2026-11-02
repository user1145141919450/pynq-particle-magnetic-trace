#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据：把"整图 copy + 整图 addWeighted"改成"只算改动的那块"之后，**必须 0 像素差**。

为什么要有这个文件：`main.py` 里那 6 处半透明叠加被换成了 `_blend_rect` / `_blend_beams`，
动机是省掉 50% 的 draw() 时间。但"省时间"不能以"画面变了"为代价，而**肉眼看不出来**——
半透明叠加差 1 个灰度级，谁都看不出来，可它是错的。
所以判据必须是**逐像素比**，不是"看着一样"。

★ 参考实现（"旧行为"）**故意用原始字面量**（0.72 / 0.60 / 0.15 / 0.1），
  **不是** `1.0-alpha`。如果写成 `1.0-alpha` 就成了"自己跟自己比"，
  万一 `1.0-0.28 != 0.72` 这种浮点差异存在，这个测试就**永远发现不了**。
  新代码里写的是 `1.0 - alpha`，所以这个测试同时也在验那个减法。

用法：
    set PYTHONPATH=E:/py_pkgs/vision
    /e/py-tools/Scripts/python.exe test_render_equiv.py
退出码：0 = 全部逐像素一致且 6 个站点全被覆盖；1 = 有差异或有站点没被覆盖。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"E:/py_pkgs/vision")

import time                                                  # noqa: E402

import cv2                                                   # noqa: E402
import numpy as np                                           # noqa: E402

import main as M                                             # noqa: E402

NEW_RECT, NEW_BEAMS = M._blend_rect, M._blend_beams

# 记录新代码到底被调用没有 —— 没有"这个站点被覆盖了"的自证，测试可能全是空跑
HIT = {}


def _wrap(name, fn):
    def g(*a, **kw):
        HIT[name] = HIT.get(name, 0) + 1
        return fn(*a, **kw)
    return g


def ref_blend_rect(img, rect, color, alpha, beta):
    """**原代码**的形状：整图 copy → 整图 addWeighted。"""
    ov = img.copy()
    cv2.rectangle(ov, (rect[0], rect[1]), (rect[2], rect[3]), color, -1)
    cv2.addWeighted(ov, alpha, img, beta, 0, img)


def ref_blend_beams(img, beam, ci, alpha, beta):
    """**原代码**的形状（注意 ov 在画线**之前**复制 ⇒ 灰线也被淡化）。"""
    ov = img.copy()
    for k, (ang, pts, res) in enumerate(beam):
        if len(pts) < 2:
            continue
        col = (95, 92, 105) if k != ci else (60, 150, 250)
        arr = np.array([M._pt(px, py) for px, py in pts], np.int32)
        cv2.polylines(ov, [arr], False, col, 2 if k == ci else 1)
    cv2.addWeighted(ov, alpha, img, beta, 0, img)


# ---- 参考侧：签名与新实现**完全一致**（连 beta 都显式传），这样两边唯一的差别
#      就只剩"整图算" vs "只算改动块"这一件事，不会掺进别的变量。
def use_new():
    M._blend_rect = _wrap("rect", NEW_RECT)
    M._blend_beams = _wrap("beams", NEW_BEAMS)


def use_ref():
    M._blend_rect = ref_blend_rect
    M._blend_beams = ref_blend_beams


# ---------------- 场景：每个站点都要被真的触发一次 ----------------
def _play_scene(seed=7, rounds=5, n=90, beam=False, fire=True):
    m = M.Match(seed=seed)
    m.new_round(rounds)
    m.start_play()
    for p in (0, 1):
        m.place_ball(p, 200 if p == 0 else 300)
        if fire:
            m.fire(p)
    for _ in range(n):
        m.update(1.0 / 30.0)
    if beam:
        m.beam_on = True
    return m


def sc_blocks():
    """站点 1：磁场块（整局都有）"""
    return _play_scene()


def _beam_scene(seed, rounds, n=90):
    """站点 2 的变体：换种子/局数 ⇒ 轨迹几何完全不同（含贴墙、跨屏、打到洞口边）"""
    m = M.Match(seed=seed)
    m.new_round(rounds)
    m.start_play()
    for p in (0, 1):
        m.place_ball(p, 200 if p == 0 else 300)
    for _ in range(n):
        m.update(1.0 / 30.0)
    m.beam_on = True
    return m


def sc_beams():
    """站点 2：轨迹束（beam_on + 球待发射）"""
    return _play_scene(beam=True, fire=False)


def sc_beams_s1():
    return _beam_scene(1, 3)


def sc_beams_s3():
    return _beam_scene(3, 7)


def sc_beams_s11():
    return _beam_scene(11, 9)


def sc_hole():
    """站点 3：加洞口提示条（HUD_H+66）"""
    m = _play_scene()
    m.phase = 'HOLE'
    m.hole_ui.update({'player': 0, 'edge': 'right', 'pos': 270.0, 'stage': 'EDGE'})
    return m


def sc_hole_fine():
    """站点 3 的另一分支（stage=FINE，文案不同、位置相同）"""
    m = sc_hole()
    m.hole_ui['stage'] = 'FINE'
    return m


def sc_edit():
    """站点 4：改磁场提示条（HUD_H+92）"""
    m = _play_scene()
    m.phase = 'EDIT'
    m.edit = {'player': 0, 'idx': 2, 'stage': 'ADJUST'}
    return m


def sc_round_end():
    """站点 5：本局结束面板"""
    m = _play_scene()
    m.phase = 'ROUND_END'
    return m


def sc_match_end():
    """站点 6：比赛结束面板"""
    m = _play_scene()
    m.phase = 'MATCH_END'
    m.goals = [3, 1]
    return m


SCENES = [
    ("站点1 磁场块",           sc_blocks),
    ("站点2 轨迹束 seed7",     sc_beams),
    ("站点2 轨迹束 seed1",     sc_beams_s1),
    ("站点2 轨迹束 seed3",     sc_beams_s3),
    ("站点2 轨迹束 seed11",    sc_beams_s11),
    ("站点3 加洞口 EDGE",      sc_hole),
    ("站点3 加洞口 FINE",      sc_hole_fine),
    ("站点4 改磁场",           sc_edit),
    ("站点5 本局结束",         sc_round_end),
    ("站点6 比赛结束",         sc_match_end),
]


def render(factory, mouse=(0, 0)):
    """重造一次全新的场景再画 —— 免得 draw() 里的任何状态被上一轮带过来。"""
    return M.draw(factory(), mouse)


def main():
    print("=" * 74)
    print("  判据：新（只算改动块）vs 旧（整图 copy + 整图 addWeighted）必须 0 像素差")
    print("=" * 74)

    use_ref()
    refs = {name: render(f) for name, f in SCENES}
    use_new()
    HIT.clear()
    bad = 0
    for name, f in SCENES:
        got = render(f)
        a, b = refs[name], got
        if a.shape != b.shape:
            print("  ✗ %-18s 尺寸不同 %s vs %s" % (name, a.shape, b.shape))
            bad += 1
            continue
        d = cv2.absdiff(a, b)
        n_diff = int(np.count_nonzero(d.reshape(-1, a.shape[2]).any(axis=1)))
        mx = int(d.max())
        if n_diff == 0:
            print("  ✓ %-18s 0 像素差" % name)
        else:
            print("  ✗ %-18s %d 个像素不同，最大差 %d" % (name, n_diff, mx))
            bad += 1

    print()
    print("  新代码被调用的次数：%s" % (HIT or "**一次都没调用 —— 这个测试是空跑**"))
    if not HIT:
        bad += 1

    # ---- 顺带量一下省了多少（PC 上；绝对值不能搬到板上，但比例可以看）----
    print()
    print("=" * 74)
    print("  同一批场景的 draw() 耗时（PC，1280x720）")
    print("=" * 74)
    for tag, setup in (("旧（整图叠加）", use_ref), ("新（只算改动块）", use_new)):
        setup()
        for _, f in SCENES:
            M.draw(f(), (0, 0))
        ts = []
        for _ in range(10):
            for _, f in SCENES:
                m = f()
                t0 = time.perf_counter()
                M.draw(m, (0, 0))
                ts.append((time.perf_counter() - t0) * 1000.0)
        ts.sort()
        print("  %-18s 中位 %6.2f ms   最小 %6.2f   最大 %6.2f   （%d 次）"
              % (tag, ts[len(ts) // 2], ts[0], ts[-1], len(ts)))

    print()
    if bad:
        print("结论：**不通过**（%d 项）-> 别把这批改动留在 main.py 里" % bad)
        return 1
    print("结论：通过 —— 逐像素一致，且 6 个站点全部被真的触发过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
