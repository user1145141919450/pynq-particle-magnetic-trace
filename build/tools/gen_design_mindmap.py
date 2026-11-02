# -*- coding: utf-8 -*-
"""gen_design_mindmap.py —— 生成 docs/design_mindmap.svg（工程设计思维导图）

不依赖 graphviz/matplotlib：手写 SVG，浏览器/VSCode 直接打开。
中文用系统字体（Microsoft YaHei / Noto Sans CJK），Windows 上显示正常。
"""
import os
import sys

W, H = 1280, 1260
FONT = "'Microsoft YaHei','Noto Sans CJK SC','PingFang SC',sans-serif"

BRANCHES = [
    ("硬件与视频链路", "#2e7d32", "#e8f5e9", [
        "OV5640 (DVP) → 自研采集桥",
        "vision_core：8×8 块平均 → 128×90 缩略图",
        "VDMA → PS 内存；HDMI 由 PL 直出",
        "输入：键盘 / 鼠标 / 手机 HTTP",
        "MTT 坞：键鼠 + WiFi 三件共存",
    ]),
    ("PL 加速 IP", "#1565c0", "#e3f2fd", [
        "V3 轨迹束：17 逻辑射线 / 2 物理 lane",
        "粒子引擎：16 粒子并行物理（K=1）",
        "差距1 检测：掩码→OPEN→CCL→候选",
        "PL 原生障碍（旋转矩形）/ 圆形门",
        "AXI 地址：0x40000000 / 1000 / 2000",
    ]),
    ("游戏与物理", "#6a1b9a", "#f3e5f5", [
        "10 局双人对战：弹药 / 积分 / 结算",
        "分块磁场 + 反弹 + 洞口自适应",
        "局前商店 / 局中道具（改场 / 加洞）",
        "真球追踪 + 发射模式 + 直播/复盘",
        "HDMI 直出：live2 双缓冲 + 局部推帧",
    ]),
    ("工程保障（铁律）", "#b26a00", "#fff3e0", [
        "时序门禁：改 RTL/BD 必跑 WNS/WHS",
        "仿真对拍：lane / 束 / PE / 检测逐位",
        "台架 355/355（PC 与板同套件）",
        "部署对账：deploy_check + md5",
        "事故档案：野 DMA / 花屏 / 缓存陷阱",
    ]),
]


def tw(s, size):
    """粗略文字宽度：CJK 按 1em，ASCII 按 0.55em。"""
    w = 0.0
    for ch in s:
        w += size * (1.0 if ord(ch) > 127 else 0.55)
    return w


def wrap(s, max_chars):
    if len(s) <= max_chars:
        return [s]
    return [s[:max_chars], s[max_chars:]]


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def rect(x, y, w, h, fill, stroke, rx=10, sw=2):
    return ('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%d" '
            'fill="%s" stroke="%s" stroke-width="%d"/>'
            % (x, y, w, h, rx, fill, stroke, sw))


def text(x, y, s, size, color="#1a1a1a", weight="normal", anchor="start"):
    return ('<text x="%.1f" y="%.1f" font-family="%s" font-size="%d" '
            'fill="%s" font-weight="%s" text-anchor="%s">%s</text>'
            % (x, y, FONT, size, color, weight, anchor, esc(s)))


def edge(x1, y1, x2, y2, color):
    mx = (x1 + x2) / 2.0
    return ('<path d="M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" '
            'fill="none" stroke="%s" stroke-width="2.4" opacity="0.55"/>'
            % (x1, y1, mx, y1, mx, y2, x2, y2, color))


def main(out):
    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (W, H, W, H))
    parts.append('<rect width="100%%" height="100%%" fill="#fbfbfd"/>')
    # 标题
    parts.append(text(60, 62, "基于 PYNQ 的粒子磁迹交互系统 · 工程设计导图", 30,
                      "#14213d", "bold"))
    parts.append(text(60, 94, "Zynq-7020 / PYNQ-Z2 ｜ 相机看球 → PL 并行物理 → HDMI 实时叠加",
                      17, "#555"))
    parts.append(text(60, 120, "2026-10-23 · 差距 1 上板验收 / 差距 2 流畅化（快晃不闪）",
                      15, "#777"))

    # 根节点
    root = "基于 PYNQ 的粒子磁迹交互系统"
    rw, rh = 230, 84
    rx, ry = 70, 600
    parts.append(rect(rx, ry, rw, rh, "#14213d", "#14213d", 18, 2))
    parts.append(text(rx + rw / 2, ry + 36, root, 20, "#ffffff", "bold", "middle"))
    parts.append(text(rx + rw / 2, ry + 62, "PS + PL 协同", 14, "#c9d6ea",
                      "normal", "middle"))

    branch_x, branch_w = 400, 240
    leaf_x = 730
    for i, (bname, col, light, leaves) in enumerate(BRANCHES):
        by = 175 + i * 275
        # 分支节点
        parts.append(rect(branch_x, by - 34, branch_w, 68, light, col, 14, 2.5))
        parts.append(text(branch_x + branch_w / 2, by + 7, bname, 18, col,
                          "bold", "middle"))
        parts.append(edge(rx + rw, ry + rh / 2, branch_x, by, col))

        # 叶子
        n = len(leaves)
        leaf_h, gap = 46, 10
        total = n * leaf_h + (n - 1) * gap
        y0 = by - total / 2
        for j, leaf in enumerate(leaves):
            ly = y0 + j * (leaf_h + gap)
            lines = wrap(leaf, 20)
            lw = max(tw(s, 15) for s in lines) + 34
            parts.append(rect(leaf_x, ly, lw, leaf_h, "#ffffff", col, 10, 1.4))
            parts.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s"/>'
                         % (leaf_x + 15, ly + leaf_h / 2, col))
            ty = ly + (leaf_h - 18 * len(lines)) / 2 + 14
            for k, s in enumerate(lines):
                parts.append(text(leaf_x + 28, ty + k * 19, s, 15, "#222"))
            parts.append(edge(branch_x + branch_w, by, leaf_x, ly + leaf_h / 2, col))

    # 底部说明
    parts.append(text(70, H - 46, "读法：从左到右四层——平台 → 四条主线（硬件 / PL IP / 游戏 / 工程保障）→ 关键落点。",
                      14, "#666"))
    parts.append(text(70, H - 24, "差距1：缩略图→掩码→OPEN→CCL→候选→PS复判；差距2：live2双缓冲+160x90差分局部推帧（~3fps，原8~12fps未达）。",
                      14, "#666"))
    parts.append("</svg>")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "docs", "design_mindmap.svg")
    main(out)
