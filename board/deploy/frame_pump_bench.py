# -*- coding: utf-8 -*-
"""frame_pump_bench.py —— PL 帧泵上板台架（2026-10-31）

不碰 VDMA/相机：自己分配"源图/显存/行表"三块 CMA 缓冲，跑两档映射，
把 PL 输出逐像素与 PS 参考模型（same nearest 口径）对比，并计速。

用法（板上 root）：
    python3 frame_pump_bench.py
"""
import sys
import time

SW, SH = 1024, 720
DW, DH = 1280, 720


def _phys(b):
    p = getattr(b, 'device_address', None)
    return int(p if p is not None else b.physical_address)


def ref_row_table_apply(src, tbl, dw=DW, dh=DH, sw=SW, sh=SH):
    """PS 参考：按行表做最近邻采样（与 RTL 同口径：Q16.8，>>8 算术移位）。"""
    import numpy as np
    out = np.zeros((dh, dw), np.uint32)
    t = tbl.reshape(dh, 4).astype(np.int32)
    xs = np.arange(dw, dtype=np.int64)
    for y in range(dh):
        sx0 = np.int32(t[y, 0]); sy0 = np.int32(t[y, 1])
        dsx = np.int32(t[y, 2]); dsy = np.int32(t[y, 3])
        sx = (sx0 + dsx * xs) >> 8
        sy = (sy0 + dsy * xs) >> 8
        sx = np.clip(sx, 0, sw - 1)
        sy = np.clip(sy, 0, sh - 1)
        out[y] = src[sy, sx]
    return out


def main():
    import numpy as np
    from pynq import allocate
    from frame_pump_pl import FramePumpPL, build_row_table, pack_bgr32

    print('=' * 66)
    print('PL 帧泵上板台架')
    print('=' * 66)
    fp = FramePumpPL(verbose=True)

    src = allocate(shape=(SH, SW), dtype=np.uint32)
    dst = allocate(shape=(DH, DW), dtype=np.uint32)
    tbl = allocate(shape=(DH * 4,), dtype=np.uint32)

    # 源图：可辨认的彩色图案（用 pack_bgr32 的真实打包函数生成）
    img = np.zeros((SH, SW, 3), np.uint8)
    yy, xx = np.mgrid[0:SH, 0:SW]
    img[:, :, 0] = (xx * 255 // SW).astype(np.uint8)      # B
    img[:, :, 1] = (yy * 255 // SH).astype(np.uint8)      # G
    img[:, :, 2] = ((xx + yy) * 255 // (SW + SH)).astype(np.uint8)  # R
    src[:] = pack_bgr32(img)
    try:
        src.flush()
    except Exception:
        pass
    print('源图 1024x720 已打包（%.1f MB）' % (SW * SH * 4 / 1e6))

    ok = bad = 0

    def check(name, cond, extra=''):
        nonlocal ok, bad
        if cond:
            ok += 1
            print('  [OK]   %s %s' % (name, extra))
        else:
            bad += 1
            print('  [FAIL] %s %s' % (name, extra))

    # ---- 档①：水平 1.25 倍放大（sx = x*1024/1280, sy = y）----
    t1 = np.zeros((DH, 4), np.int32)
    t1[:, 0] = 0
    t1[:, 1] = (np.arange(DH) * 256).astype(np.int32)
    # ★ 取 204 而不是 205：205 在最后一列会算到 1024（越界一格），
    #   RTL 不做钳位 -> 两档对不上
    t1[:, 2] = 204
    t1[:, 3] = 0
    tbl[:] = t1.reshape(-1).astype(np.uint32)
    try:
        tbl.flush()
    except Exception:
        pass
    fp.setup(src, dst, tbl, SW, SH, DW, DH)
    dst[:] = 0
    try:
        dst.flush()
    except Exception:
        pass
    ms = fp.start_wait()
    try:
        dst.invalidate()
    except Exception:
        pass
    got = dst.copy()
    exp = ref_row_table_apply(np.asarray(src), np.asarray(tbl))
    nd = int((got != exp).sum())
    check('档① 1.25x 水平放大：逐像素一致', nd == 0, '不符 %d / %d' % (nd, DW * DH))
    print('      耗时 %.1f ms/帧' % ms)

    # ---- 档②：真实标定行表（有 v3_calib 才跑）----
    try:
        import v3_scan
        Hm = v3_scan.load_calib(verbose=False)
        if Hm is not None:
            t2 = build_row_table(Hm, DW, DH, 0.03, 0.012, SW, SH)
            tbl[:] = t2
            try:
                tbl.flush()
            except Exception:
                pass
            dst[:] = 0
            try:
                dst.flush()
            except Exception:
                pass
            ms2 = fp.start_wait()
            try:
                dst.invalidate()
            except Exception:
                pass
            got2 = dst.copy()
            exp2 = ref_row_table_apply(np.asarray(src), np.asarray(tbl))
            nd2 = int((got2 != exp2).sum())
            check('档② 真实标定透视行表：逐像素一致', nd2 == 0,
                  '不符 %d / %d' % (nd2, DW * DH))
            print('      耗时 %.1f ms/帧' % ms2)
        else:
            print('  [ .. ] 没有 v3_calib.json，跳过档②')
    except Exception as e:
        print('  [ .. ] 档② 跳过：%s' % str(e)[:100])

    print('-' * 66)
    print('通过 %d / 失败 %d' % (ok, bad))
    print('帧泵统计：%s' % dict(n=fp.n, ms_avg=round(fp.ms / max(1, fp.n), 1)))
    fp.close()
    for b in (src, dst, tbl):
        try:
            b.freebuffer()
        except Exception:
            pass
    return 0 if bad == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
