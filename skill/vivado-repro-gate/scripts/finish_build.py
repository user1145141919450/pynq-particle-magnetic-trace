# -*- coding: utf-8 -*-
"""finish_build.py —— 构建之后**一条命令做完所有验收**（2026-10-31 立）

为什么要有它：
  2026-10-30 那次构建"过了时序门禁、台架全绿"，我就当完成报出去了 —— 实际
  从零重建的 BD **丢了 pe_axi_0 / map_detect_axi_0 两个 IP**（风暴模式、PL 检测
  全哑），而且 10-22 删掉的 ila_0 又复活了。是用户追问"没影响其它功能吧"才发现。
  ⇒ 把"报完成之前必须跑的检查"固化成一条命令，别再靠记性：
     1) 时序门禁产物在不在（.TIMING_FAILED 就是没过）
     2) 新 hwh 与工作版本基线**逐 IP/逐地址**对账（tools/check_ip_baseline.py）
     3) bit/hwh 拷进 deploy 包
     4) deploy_check.py --fix（源↔deploy 一致、bit 是本次产物、地址常量对 hwh）

用法：
    python tools/finish_build.py            # 检查 + 更新 deploy
    python tools/finish_build.py --check    # 只检查，不动 deploy
"""
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PJ = os.path.join(ROOT, "vivado", "ov5640_pynq")
IMPL = os.path.join(PJ, "proj", "ov5640_m1a.runs", "impl_1")
BIT = os.path.join(IMPL, "ov5640_m1a_wrapper.bit")
HWH = os.path.join(PJ, "proj", "ov5640_m1a.gen", "sources_1", "bd",
                   "ov5640_m1a", "hw_handoff", "ov5640_m1a.hwh")
DEPLOY = os.path.join(PJ, "deploy")


def run(desc, args):
    print("--- %s" % desc)
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if line.strip():
            print("    " + line)
    return p.returncode, out


def main(argv):
    check_only = "--check" in argv
    bad = 0

    # 1) 时序门禁产物
    if not os.path.isfile(BIT):
        print("[X] 没有 bit：%s" % BIT)
        if os.path.isfile(BIT + ".TIMING_FAILED"):
            print("    （有 .TIMING_FAILED —— 时序门禁没过，这个构建不能要）")
        return 1
    print("[OK] bit 存在：%s（%.1f MB）" % (os.path.basename(BIT),
                                         os.path.getsize(BIT) / 1e6))
    if os.path.isfile(BIT + ".TIMING_FAILED"):
        print("[X] 同时存在 .TIMING_FAILED —— 先确认哪个是本次的，别下错板")
        bad += 1

    # 2) hwh 与基线对账
    if not os.path.isfile(HWH):
        print("[X] 没有 hwh：%s（generate_target 没跑？）" % HWH)
        return 1
    rc, _ = run("IP/地址基线对账（tools/check_ip_baseline.py）",
                [sys.executable, os.path.join(ROOT, "tools", "check_ip_baseline.py"), HWH])
    if rc != 0:
        bad += 1

    if bad:
        print("\n结论：**不过** —— 别更新 deploy、别上板")
        return 1

    if check_only:
        print("\n结论：检查全过（--check 模式，deploy 未动）")
        return 0

    # 3) 拷进 deploy
    shutil.copy2(BIT, os.path.join(DEPLOY, "ov5640_m1a_wrapper.bit"))
    shutil.copy2(HWH, os.path.join(DEPLOY, "ov5640_m1a_wrapper.hwh"))
    print("--- 已拷进 deploy：bit + hwh")

    # 4) deploy_check --fix
    rc, out = run("deploy_check.py --fix", [sys.executable,
                 os.path.join(ROOT, "software", "pc", "deploy_check.py"), "--fix"])
    if rc != 0 or "可上板" not in out:
        print("\n结论：**deploy 不自洽** —— 看上面的红项")
        return 1

    print("\n结论：全过（时序门禁产物 + IP 基线 + deploy 自洽）→ 可以上板")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
