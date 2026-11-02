#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dump_base_hwh.py —— 把 PYNQ 官方 base overlay 里**视频那条链**的参数和接线 dump 出来。

为什么要它（2026-09-30）：
    我们的 VDMA MM2S 出 32 位流，v_axi4s_vid_out 只要 24 位 —— Vivado 只给一句
    "only lower order bits will be connected" 就放行（假通过？真适配？）。
    base overlay 是**已经跑通**的参考设计：它要是也这么直连，说明"接低位"就是
    官方认可的像素截断（= 帧必须是 32bpp，一拍一像素）；它要是有个中间块，
    说明必须补那个块。**抄参考设计，不猜。**

用法（板子上跑，纯标准库，不需要 pynq）：
    ssh xilinx@192.168.2.99 'python3 -u -' < dump_base_hwh.py
"""
import re
import sys
import xml.etree.ElementTree as ET

HWH = "/home/xilinx/pynq/overlays/base/base.hwh"

WANT = re.compile(r"axi_vdma|v_axi4s_vid_out|v_tc|axi_dynclk|rgb2dvi|"
                  r"pixel_pack|v_proc_ss|xlconstant|axi_gpio", re.I)
PARAM_KEEP = re.compile(r"^C_|^k[A-Z]|^K_|^enable_|^MAX_|^NUM_", re.I)


def main():
    try:
        root = ET.parse(HWH).getroot()
    except Exception as e:
        print("!! 解析失败:", e)
        return 1

    mods = {}
    for m in root.iter("MODULE"):
        full = m.get("FULLNAME") or m.get("INSTANCE") or "?"
        mods[full] = (m.get("VLNV", ""), m)

    print("=" * 78)
    print("1) 视频相关模块清单")
    print("=" * 78)
    hit = {k: v for k, v in mods.items() if WANT.search(v[0])}
    for full, (vlnv, _m) in sorted(hit.items()):
        print(f"   {full:<52} {vlnv}")
    print(f"   （共 {len(mods)} 个模块，命中 {len(hit)} 个）")

    print()
    print("=" * 78)
    print("2) 参数")
    print("=" * 78)
    for full, (vlnv, m) in sorted(hit.items()):
        print(f"\n---- {full}   [{vlnv}]")
        found = False
        for pm in m.iter("PARAMETER"):
            name = pm.get("NAME", "")
            if PARAM_KEEP.search(name):
                print(f"     {name} = {pm.get('VALUE')}")
                found = True
        if not found:
            print("     (无匹配参数)")

    print()
    print("=" * 78)
    print("3) ★接线★ —— 只看有数据/时钟意义的引脚（SIGNAME 相同 = 连在一起）")
    print("=" * 78)
    for full, (vlnv, m) in sorted(hit.items()):
        ports = []
        for p in m.iter("PORT"):
            sig = p.get("SIGNAME") or ""
            nm = p.get("NAME", "")
            # 只留关键脚，不然刷屏
            if re.search(r"tdata|tvalid|tready|tlast|tkeep|tuser|"
                         r"clk|rst|resetn|cek|ce\b|locked|vid_data|vid_pHSync|"
                         r"vid_pVSync|vid_pVDE|TMDS|hpd|gpio", nm, re.I):
                ports.append((nm, p.get("DIR", ""), p.get("LEFT", ""), sig))
        if not ports:
            continue
        print(f"\n---- {full}")
        for nm, d, l, sig in ports:
            w = f"[{int(l)+1}-1:0]" if l not in ("", None) else ""
            print(f"     {nm:<28} {d} {w:<12} sig={sig}")

    print()
    print("=" * 78)
    print("4) raw <NETS> 区（如果上面 SIGNAME 全空，看这里）")
    print("=" * 78)
    raw = open(HWH, encoding="utf-8-sig", errors="replace").read()
    i = raw.find("<NETS")
    if i < 0:
        print("   没有 <NETS> 段")
        print("   root 下所有 tag:", " ".join(sorted({e.tag for e in root.iter()})))
        # 退路：找 <SIGNAME> 或 <BUSINTERFACE> 之类
        j = raw.find("<BUSINTERFACES")
        if j >= 0:
            print("   ---- <BUSINTERFACES> 片段 ----")
            print(raw[j:j + 4000])
    else:
        seg = raw[i:i + 6000]
        for ln in seg.splitlines():
            if WANT.search(ln):
                print("   " + ln.strip()[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
