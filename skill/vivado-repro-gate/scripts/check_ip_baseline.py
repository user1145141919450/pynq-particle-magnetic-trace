# -*- coding: utf-8 -*-
"""check_ip_baseline.py —— 把新生成的 hwh 与「工作版本基线」逐 IP/逐地址对账。

为什么要有它（2026-10-31，血的教训）：
  从零重建 BD 时，两个由**补丁脚本**加进去的 IP（pe_axi_0 / map_detect_axi_0）
  悄悄消失了 —— 而构建、时序门禁、台架**全绿**，只有功能是哑的
  （风暴模式 / MAPDET_PL 都没了）。是用户一句"没影响其它功能吧"才逼着去对账。

判定（全部必须满足，否则 rc=1）：
  1) 基线里的每个 IP 都必须在（除非显式列进 allow_missing）
  2) 每个 IP 的 BASE/HIGH 必须与基线完全相同
  3) 新出现的 IP 必须在 allow_new 里登记（"凭空多出来的 IP"同样要人看一眼）

用法：
    python tools/check_ip_baseline.py [hwh] [--json]
默认 hwh = vivado/ov5640_pynq/proj/ov5640_m1a.gen/.../handoff/ov5640_m1a.hwh
"""
import io
import json
import os
import sys
import xml.etree.ElementTree as ET

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HWH = os.path.join(
    ROOT, "vivado", "ov5640_pynq", "proj", "ov5640_m1a.gen", "sources_1", "bd",
    "ov5640_m1a", "hw_handoff", "ov5640_m1a.hwh")
BASELINE = os.path.join(ROOT, "vivado", "ov5640_pynq", "ip_baseline.json")


def load_hwh(path):
    t = ET.parse(path)
    out = {}
    for e in t.iter("MEMRANGE"):
        inst = e.get("INSTANCE")
        if inst in ("External_Ports", "ov5640_m1a_imp"):
            continue
        out[inst] = (e.get("BASEVALUE"), e.get("HIGHVALUE"))
    return out


def main(argv):
    hwh = DEFAULT_HWH
    as_json = "--json" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]
    if args:
        hwh = args[0]
    if not os.path.isfile(hwh):
        print("ERROR: 找不到 hwh：%s" % hwh)
        return 1
    if not os.path.isfile(BASELINE):
        print("ERROR: 找不到基线：%s" % BASELINE)
        return 1

    bl = json.load(io.open(BASELINE, encoding="utf-8"))
    base = bl.get("baseline", {})
    allow_new = bl.get("allow_new", {})
    allow_missing = bl.get("allow_missing", {})
    now = load_hwh(hwh)

    bad = []
    for inst, v in sorted(base.items()):
        if inst not in now:
            if inst in allow_missing:
                continue
            bad.append("缺失 IP：%s（基线 %s）—— 从零重建把它弄丢了？" % (inst, v["base"]))
            continue
        b, h = now[inst]
        if b != v["base"] or h != v["high"]:
            bad.append("地址变了：%s 基线 %s..%s / 现在 %s..%s"
                       % (inst, v["base"], v["high"], b, h))
    for inst in sorted(set(now) - set(base)):
        if inst not in allow_new:
            bad.append("多出未登记的 IP：%s @ %s（确认无误后写进 ip_baseline.json 的 allow_new）"
                       % (inst, now[inst][0]))

    if as_json:
        print(json.dumps({"ok": not bad, "bad": bad, "n_base": len(base), "n_now": len(now)},
                         ensure_ascii=False))
    else:
        print("基线 %d 个 IP / 当前 %d 个" % (len(base), len(now)))
        if bad:
            for b in bad:
                print("  [X] " + b)
            print("结论：**不一致** —— 这个 bit 不能上板")
        else:
            added = sorted(set(now) - set(base))
            print("  [OK] 基线 IP 全在、地址全同")
            if added:
                print("  [OK] 新增（已登记）：%s" % ", ".join(added))
            print("结论：与工作版本一致（只多登记过的东西）")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
