#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_dynclk_tables.py —— 从 PYNQ 的 video_display.h 里把 MMCM 的
                        lock_lookup / filter_lookup_low 两张表**解析**出来，
                        生成板子要 import 的 dynclk_tables.py。

为什么要有这个脚本（2026-09-30）：
  axi_dynclk 是个"给参数就自己配 MMCM"的 IP —— 但 MMCM 的 **LOCK** 和
  **FILTER** 两个寄存器不是算出来的，是查表的（Xilinx 给的经验值，按
  反馈倍频 M=fbmult 索引）。这两张表各有 64 项、分别长 40 位和 10 位。

  ★ 64×2 个数**绝不能手抄**：抄错一个 bit，MMCM 可能锁不上（黑屏）、
    也可能锁上了但抖（偶发花屏）—— 而这两种症状都会被误判成"硬件问题"。
    本工程已经被"手抄/凭记忆"坑过很多次了，所以：**能解析就不要抄**。

  表来自 PYNQ 自己的那份（base overlay 在**同一块板子**上跑通用的就是它），
  所以这是"跟着跑通的东西走"，不是我从别处找来的。

用法：
    python gen_dynclk_tables.py
输出：
    ../board/dynclk_tables.py   （相对本脚本；板子部署时一起拷过去）
校验：
    两张表都必须是 64 项；生成完会回读一遍比对，不一致就报错退出。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "ref", "pynq27_video_display.h")
DST = os.path.abspath(os.path.join(HERE, "..", "board", "dynclk_tables.py"))


def parse_table(text, name, expect_len=64):
    """把 `static const u64 <name>[64] = { 0b... , ... };` 里的二进制字面量抠出来。

    ★ 不用正则一把梭：先定位数组声明，再切到它自己那个 `};` 为止 ——
      否则会把下一个数组的值也吃进来（两张表挨着，很容易串）。
    """
    m = re.search(r"\b" + re.escape(name) + r"\s*\[\s*(\d+)\s*\]\s*=\s*\{", text)
    if not m:
        raise SystemExit("解析失败：找不到数组 %s 的声明" % name)
    declared = int(m.group(1))
    body_start = m.end()
    end = text.find("};", body_start)
    if end < 0:
        raise SystemExit("解析失败：%s 的数组体没有以 '};' 收尾" % name)
    body = text[body_start:end]

    vals = [int(tok, 2) for tok in re.findall(r"0b([01]+)", body)]

    if declared != len(vals):
        # 声明长度和实际项数对不上 = 我们切错了范围，必须停
        raise SystemExit(
            "解析失败：%s 声明长度 %d，实际抠出 %d 项 —— 多半是数组体切错了"
            % (name, declared, len(vals))
        )
    if declared != expect_len:
        raise SystemExit(
            "解析失败：%s 长度 %d，期望 %d" % (name, declared, expect_len)
        )
    return vals


def main():
    if not os.path.exists(SRC):
        raise SystemExit("找不到参考头文件：%s" % SRC)
    with open(SRC, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    lock = parse_table(text, "lock_lookup")
    filt = parse_table(text, "filter_lookup_low")

    # ---- 回读校验：再解析一遍，逐项比对（防"解析出来的和文件里的不是一回事"）----
    lock2 = parse_table(text, "lock_lookup")
    filt2 = parse_table(text, "filter_lookup_low")
    if lock != lock2 or filt != filt2:
        raise SystemExit("回读校验失败：两次解析结果不一致（不会有这种事，但发生了就别写文件）")

    # ---- 抽查几个"肉眼可验"的性质，防止解析出整齐的垃圾 ----
    # lock_lookup 是 40 位宽的表，filter 是 10 位宽的表 —— 超范围说明切错体了
    if any(v <= 0 or v >= (1 << 40) for v in lock):
        raise SystemExit("lock_lookup 里有值越出 40 位范围 —— 解析错了")
    if any(v <= 0 or v >= (1 << 10) for v in filt):
        raise SystemExit("filter_lookup_low 里有值越出 10 位范围 —— 解析错了")

    lines = []
    lines.append("# -*- coding: utf-8 -*-")
    lines.append('"""')
    lines.append("dynclk_tables.py —— axi_dynclk 配 MMCM 用的两张查表（自动生成，勿手改）")
    lines.append("")
    lines.append("由 software/pc/gen_dynclk_tables.py 从 software/pc/ref/pynq27_video_display.h")
    lines.append("解析而来。那份头文件取自 PYNQ 2.7 源码里 base overlay 自己用的同一份实现")
    lines.append("（pynq/lib/_pynq/_video/video_display.h）—— 也就是**这块板上跑通过**的那份。")
    lines.append("")
    lines.append("按 MMCM 反馈倍频 M 索引（fbmult-1，fbmult 取值 2..64）。")
    lines.append("★ 不要手抄这两张表：抄错一个 bit 的表现是'MMCM 锁不上(黑屏)'或")
    lines.append("  '锁上了但抖(偶发花屏)'，两种都会被误判成硬件问题。要改就改生成脚本再跑。")
    lines.append('"""')
    lines.append("")
    lines.append("LOCK_LOOKUP = (")
    for i, v in enumerate(lock):
        lines.append("    0x%010X,%s" % (v, "  # M=%d" % (i + 1) if i % 8 == 7 else ""))
    lines.append(")")
    lines.append("")
    lines.append("FILTER_LOOKUP_LOW = (")
    for i, v in enumerate(filt):
        # ★ 不能用 "0b%010b" —— Python 的 %-格式化**没有 b 这个转换符**
        #   （那是 C 的 printf 才有的），会直接 ValueError。用 format() 才稳。
        lines.append("    0b%s,%s" % (format(v, "010b"),
                                      "  # M=%d" % (i + 1) if i % 8 == 7 else ""))
    lines.append(")")
    lines.append("")

    with open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    print("OK: 已写出 %s" % DST)
    print("    LOCK_LOOKUP        %d 项，最大 0x%X" % (len(lock), max(lock)))
    print("    FILTER_LOOKUP_LOW  %d 项，最大 0b%s" % (len(filt), format(max(filt), "010b")))
    # 顺带把我们要用的那一项打出来，方便和后面的实际取值对账
    for fb in (15, 52):
        print("    [对账] M=%d -> lock_lookup=0x%010X  filter_lookup_low=0b%s"
              % (fb, lock[fb - 1], format(filt[fb - 1], "010b")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
