# -*- coding: utf-8 -*-
"""frame_analyze.py — 解剖一帧原始字节，定死"颜色为什么不对"

板端 frame_dump.py 存下来的 .bin 是原样的字节流（每像素 2 字节）。
本脚本把可能性一次全搜完，用判据打分，而不是靠眼睛猜：

  ① 字节偏移 0/1/2/3/4  （错位 = 每个像素的 16 位拆到了相邻两个像素上）
  ② 交换/不交换         （高字节在前 or 低字节在前）
  ③ 是不是 YUV422       （偶数位字节=亮度、奇数位字节=色度 —— 本项目踩过一次）

两个判据：
  通道相关性 —— 自然场景 R/G/B 高度相关（正确解码 >0.7，错解 ~0.1）
  色度毛刺   —— |Δ(R-G)| 的均值：正确解码的色差是低频的，错解是高频噪声

用法：
    python frame_analyze.py frame_raw_0.bin [输出前缀]
"""

import os
import sys

sys.path.insert(0, 'E:/py_pkgs/vision')

import cv2
import numpy as np

W, H = 1024, 720            # 720 = 实测帧高（旧 dump 是 722，main 里按文件大小自动推断）


def decode565(b0, b1):
    """把两个字节解成 RGB565 -> BGR 图（手工解，完全可控）

    ★ 缩放必须在 uint16 里做：uint8 * 255 会**先溢出再整除**（旧版 numpy 保持
      uint8），R=31 会被算成 (31*255 mod 256)//31 = 7 —— 整幅图塌成一坨黑。
      这个坑踩过一次，靠"与驱动自己的解码对照"才发现。
    """
    v = b0.astype(np.uint16) | (b1.astype(np.uint16) << 8)
    R = (((v >> 11) & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    G = (((v >> 5) & 0x3F).astype(np.uint16) * 255 // 63).astype(np.uint8)
    B = ((v & 0x1F).astype(np.uint16) * 255 // 31).astype(np.uint8)
    return np.dstack([B, G, R])


def corr_score(bgr):
    """三通道两两相关系数取均值（本项目判据：正确 >0.7）"""
    f = bgr[::4, ::4].astype(np.float32).reshape(-1, 3)
    c = np.corrcoef(f.T)
    return float((c[0, 1] + c[0, 2] + c[1, 2]) / 3.0)


def chroma_spur(bgr):
    """|Δ(R-G)| 与 |Δ(B-G)| 的均值：错解的假彩色跟着边缘剧烈跳"""
    b = bgr.astype(np.int16)
    rg = np.abs(np.diff(b[..., 2] - b[..., 1], axis=1)).mean()
    bg = np.abs(np.diff(b[..., 0] - b[..., 1], axis=1)).mean()
    return float((rg + bg) / 2.0)


def main():
    path = sys.argv[1]
    pref = sys.argv[2] if len(sys.argv) > 2 else 'frame'
    if path.lower().endswith(('.png', '.bmp')):
        # 驱动 _selfcheck 存的 raw 图（2048 宽灰度 PNG，字节无损），可直接当字节流用
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            print(f'[错误] 读不到 {path}')
            return
        flat = img.reshape(-1)
    else:
        flat = np.fromfile(path, dtype=np.uint8)
    H = flat.size // (W * 2)                   # 按文件大小推断帧高，新旧 dump 都能解剖
    print(f'[读入] {path}  {flat.size} 字节  = {W} x {H} 像素（每像素 2 字节）')
    if H not in (720, 722):
        print(f'[警告] 推断出的帧高 {H} 不是常见的 720/722，确认一下文件')

    # ---- 判据 ③：YUV422 的奇偶字节签名
    # ★ 必须跨多行统计：首行可能是残缺行（帧首对齐），只看第 0 行会误判
    rows = flat[:W * H * 2].reshape(H, W * 2)[::16].astype(np.float32)
    he = float(np.abs(np.diff(rows[:, 0::2], axis=1)).mean())
    ho = float(np.abs(np.diff(rows[:, 1::2], axis=1)).mean())
    print(f'\n[YUV422 判据] 跨 {rows.shape[0]} 行统计：'
          f'偶数字节高频 {he:5.2f} / 奇数字节高频 {ho:5.2f}')
    if he > 3 * max(ho, 0.3) or ho > 3 * max(he, 0.3):
        print('  奇偶字节高频差 3 倍以上 —— **但这条判据会误报，不能当结论**：')
        print('    RGB565 低字节在前时，低字节（含 LSB 噪声）天然比高字节高频，')
        print('    签名与 YUYV 完全撞车（本项目两次被它误导过，其中一次误判成 YUV422、')
        print('    真因是驱动的 byte_swap 反了）。**一切以"解码相关性"为准。**')
    else:
        print('  两个字节的高频量级相当 → 不像 YUV422')

    # ---- 判据 ①②：偏移 × 交换 全搜
    print(f'\n{"偏移":>4} {"交换":>4}  通道相关性(越高越对)  色度毛刺(越低越对)')
    rows = []
    need_bytes = W * H * 2
    for off in range(0, 5):
        s = flat[off:off + need_bytes]
        if s.size < need_bytes:                     # 尾部不足就补零（只为让表格完整）
            s = np.concatenate([s, np.zeros(need_bytes - s.size, np.uint8)])
        pr = s.reshape(-1, 2)
        for swap in (False, True):
            b0 = pr[:, 1] if swap else pr[:, 0]
            b1 = pr[:, 0] if swap else pr[:, 1]
            bgr = decode565(b0.reshape(H, W), b1.reshape(H, W))
            cs, sp = corr_score(bgr), chroma_spur(bgr)
            rows.append((off, swap, cs, sp, bgr))
            print(f'{off:>4} {str(swap):>4}   {cs:>8.3f}            {sp:>8.2f}')

    # ---- 候选 ④：按 YUV422(YUYV) 解一遍，跟最好的 RGB565 正面对比
    s = flat[:W * H * 2].reshape(H, W, 2)
    y = s[..., 0].astype(np.float32)                    # 每像素第一字节=Y
    c = s[..., 1].astype(np.float32)                    # 第二字节=U/V 交替
    u = np.repeat(c[:, 0::2], 2, axis=1)[:, :W]
    v = np.repeat(c[:, 1::2], 2, axis=1)[:, :W]
    r = y + 1.402 * (v - 128)
    g = y - 0.344 * (u - 128) - 0.714 * (v - 128)
    b = y + 1.772 * (u - 128)
    yuv_bgr = np.dstack([b, g, r]).clip(0, 255).astype(np.uint8)
    cs, sp = corr_score(yuv_bgr), chroma_spur(yuv_bgr)
    print(f'\n[候选 ④] 按 YUV422(YUYV) 解：通道相关性 {cs:.3f}  色度毛刺 {sp:.2f}')
    cv2.imwrite(f'{pref}_yuyv.png', yuv_bgr)
    if cs > 0.5:
        print('  ★ YUV 解也能得高分 → 必须看图判定（两种解都"像"图时，'
              '以肉眼分辨细节/颜色是否自然为准）')

    best = max(rows, key=lambda r: r[2])
    print(f'\n[结论] 通道相关性最高 = 偏移 {best[0]}、交换 {best[1]}'
          f'（相关性 {best[2]:.3f}，毛刺 {best[3]:.2f}）')
    worst = min(rows, key=lambda r: r[2])
    if best[2] < 0.5:
        print('  ⚠ 所有组合都 <0.5：这不是字节序/错位能解释的，'
              '更像传感器输出格式（YUV）或数据线接错')
    out = f'{pref}_best_off{best[0]}_swap{int(best[1])}.png'
    cv2.imwrite(out, best[4])
    print(f'[输出] 最佳解码已存 {out}')
    out2 = f'{pref}_worst_off{worst[0]}_swap{int(worst[1])}.png'
    cv2.imwrite(out2, worst[4])
    print(f'[输出] 最差解码（对照）已存 {out2}')


if __name__ == '__main__':
    main()
