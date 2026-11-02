#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy_check.py —— 校验 `vivado/.../proj/deploy/` 这个上板包是否**完整且新鲜**。

为什么要有这个工具（不是重复劳动）：
  deploy 包的清单原来是**手写**在 build_project.tcl 里的，于是它悄悄过期了：
  一个 `.sh` 都没有（hdmi_shot.sh / v3_shoot.sh / game_v3.sh / v3_wifi.sh 全缺），
  09-28 之后新增的 v3_*.py 也几乎全缺 —— 而**没有任何地方会报错**。
  现在 build_project.tcl 改成扫目录了，但那也要等构建**跑到最后**才生效；
  构建中途改脚本、只看 mtime、断电丢写……都出过事。
  ⇒ 工具的价值在于：**任何时候都能独立地问一句"这个包到底全不全"**。

★ 判据用**内容 md5**，不用 mtime。
  踩过的坑：NTFS 会延迟更新目录项时间戳、FAT 粒度 2 秒 ⇒ mtime 比大小/比时间
  会**偶发**漏判。偶发失败不许用"复现不出来"放过。
  （这条是 2026-09-30 在 /dev/shm 状态文件上花了一个小时学到的，同类。）

用法：
    python deploy_check.py              # 只检查，打印报告（默认）
    python deploy_check.py --fix        # 缺的/过期的**拷过去**
    python deploy_check.py --quiet      # 只在有问题时打印

退出码：0 = 包完整新鲜；1 = 缺文件或过期；2 = 路径不对（构建还没跑？）

★ 中文在这个控制台里正常；若你是在 Git Bash 里跑而看到乱码，
  那是 Bash 把 GBK 字节当 UTF-8 显示，**不是脚本的问题** —— 加一句
  `PYTHONIOENCODING=utf-8 python deploy_check.py` 就正常。
"""
import hashlib
import os
import shutil
import sys

sys.stdout.reconfigure(errors="replace")

# ★ 2026-10-31：不再写死本机路径。默认 = 本文件上三级（software/pc/ -> 仓库根）；
#   换机器/解压到别处都能直接用；特殊布局可用环境变量 DEMO_ROOT 覆盖。
ROOT = (os.environ.get("DEMO_ROOT")
        or os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
# BD 生成的硬件交接文件（★ 2023.2 的 .hwh 是 EDKSYSTEM **XML**，不是 JSON；带 UTF-8 BOM）
HWH = os.path.join(ROOT, "vivado", "ov5640_pynq", "proj", "ov5640_m1a.gen",
                   "sources_1", "bd", "ov5640_m1a", "hw_handoff", "ov5640_m1a.hwh")
# (源文件, 常量名, hwh 里的 IP 实例名) —— 驱动写死的地址必须与 BD 实际分配一致
ADDR_EXPECT = [
    ("hdmi_out.py", "BASE_VDMA",   "axi_vdma_out"),
    ("hdmi_out.py", "BASE_DYNCLK", "axi_dynclk"),
    ("hdmi_out.py", "BASE_VTC",    "vtc_out"),
    ("hdmi_out.py", "BASE_HPD",    "hdmi_out_hpd_video"),
    # ★ 2026-10-30：PL 画线引擎（line_engine_top_0）命令口，BD 里钉在 0x43B30000
    ("line_pl.py",  "BASE",        "line_engine_top_0"),
    # ★★ 2026-10-31：这三个是**从零重建 BD 时最容易丢**的（2026-10-30 就真丢过：
    #   主 BD 脚本里没有 pe_axi/map_detect，重建后它们消失、地址被顶掉——
    #   症状是风暴模式/PL 检测全哑，而且构建/时序/台架全绿，根本发现不了）。
    #   放进这张表：以后任何重建只要丢了它们，deploy_check 立刻报红。
    ("v3_pl.py",         "BASE",  "v3_bundle_axi_0"),
    ("particle_pl.py",   "BASE",  "pe_axi_0"),
    ("map_detect_pl.py", "BASE",  "map_detect_axi_0"),
    # ★ 2026-10-31：PL 帧泵命令口（0x43B40000）
    ("frame_pump_pl.py", "BASE",  "frame_pump_0"),
]
# ★ 注意：是 vivado/ov5640_pynq/deploy，**不是** proj/deploy ——
#   build_project.tcl 里 `set proj_dir [file dirname [info script]]` 取的是
#   **脚本自己所在的目录**（vivado/ov5640_pynq），不是 .xpr 所在的 proj/。
#   （第一版我就写成了 proj/deploy，脚本直接报"路径不对"。）
DEPLOY = os.path.join(ROOT, "vivado", "ov5640_pynq", "deploy")
BOARD = os.path.join(ROOT, "software", "board")

# 这些是**上游**：deploy 包里必须有它们的一份内容相同的拷贝。
# 逐条写明"为什么必须在"，否则以后没人敢删也不敢留。
SOURCES = [
    # (源路径, 上板后为什么需要它)
    (os.path.join(BOARD, "hdmi_out.py"),        "HDMI 直出驱动（P2 的核心）"),
    (os.path.join(BOARD, "dynclk_tables.py"),   "hdmi_out.py 要 import 它，**必须同目录**"),
    (os.path.join(BOARD, "hdmi_shot.sh"),       "一条命令验屏幕（现场脚本）"),
    (os.path.join(BOARD, "realtime_ball.py"),   "★ 真球实时模拟（2026-10-05 规则订正：ball_game 要 import 它）"),
    (os.path.join(BOARD, "ball_tracker.py"),    "★ 多目标追踪（2026-10-05：多颗实物球各自被追踪；ball_game 要 import 它）"),
    (os.path.join(BOARD, "vtc_after_clk_test.py"),
     "判决实验：0x43C20000 的 SIGBUS 是'整格没译码'还是'被像素钟复位钉住'"
     "（2026-10-01 M12 根因；冷启动时 v_tc 读一下就 SIGBUS，本脚本一次分清）"),
    (os.path.join(BOARD, "v3_shoot.sh"),        "一条命令出定妆照"),
    (os.path.join(BOARD, "game_v3.sh"),         "游戏循环入口"),
    (os.path.join(BOARD, "v3_wifi.sh"),         "WiFi 一键配置"),
    (os.path.join(BOARD, "v3_remote.sh"),       "手机/键盘状态服务"),
    (os.path.join(BOARD, "v3_input.py"),        "输入层（手机网页 + 有线键盘共用）"),
    (os.path.join(BOARD, "v3_scan.py"),         "PL 轨迹束扫描 + 黄金模型自检"),
    (os.path.join(BOARD, "v3_pl.py"),
     "★ 2026-10-19 补进：V3 束 PL 访问层（open_v3 / write_scene / read_results，"
     "寄存器图只此一份）——v3_scan 与游戏的 PL 预测都 import 它。此前一直是"
     "'额外的文件'（没人对账，板上靠手推）"),
    # ★ 2026-10-04 补进：v3_scan / render_dashed 的核心依赖，此前一直是"额外的文件"
    #   （没人对账——和 vdma_capture 那次同一类隐患）。各自的上游在 tools/ 与 game_pc/。
    (os.path.join(ROOT, "tools", "v3_fixedpoint.py"),
     "定点黄金模型（lane_fixed —— v3_scan / render_dashed / 自检 全依赖它）"),
    (os.path.join(ROOT, "game_pc", "engine.py"),
     "物理引擎/场区（World/Blocks/Holes —— v3_scan 与游戏同源）"),
    (os.path.join(BOARD, "ball_game.py"),       "游戏主循环"),
    (os.path.join(BOARD, "line_pl.py"),
     "★ 2026-10-30：PL 画线引擎的 PS 驱动（line_engine_top 0x43B30000；"
     "fastmem 单字 MMIO，寄存器图只此一份）——上板验证要用它"),
    (os.path.join(BOARD, "frame_pump_pl.py"),
     "★ 2026-10-31：PL 帧泵驱动（透视矫正+打包写显存，命令口 0x43B40000）"),
    (os.path.join(BOARD, "frame_pump_bench.py"),
     "★ 2026-10-31：PL 帧泵上板台架（两档映射逐像素对比 + 计速）"),
    (os.path.join(BOARD, "line_bench.py"),
     "★ 2026-10-30：PL 画线上板台架（不碰 VDMA：自己 allocate 1280x720，"
     "PL 画完 PS 读回逐点核对 + 与 cv2.polylines 对比计速）"),
    (os.path.join(BOARD, "pv_worker.py"),
     "★ 2026-10-29 瞄准精确虚线的独立后台计算进程（纯 Python，不碰 PYNQ；"
     "ball_game 用 subprocess 起它，stdin/stdout 每行一个 JSON）"),
    (os.path.join(BOARD, "screen_out.py"),
     "★ 2026-10-24 串口屏状态输出（TJC 115200/GBK，页面 index；--screen 时 import）"),
    (os.path.join(BOARD, "match_flow.py"),
     "★ M2.2 局状态机（2026-10-06：10 局制/弹药/计分/结算清场；规则书 v4 移植）。"
     "ball_game --match 与 game_v3.sh MATCH=1 的硬门槛都 import 它，必须对账"),
    (os.path.join(BOARD, "hole_adapt.py"),
     "★ M2.4 洞口自适应（2026-10-07：30s 无进球 -> 洞口朝该侧落点热点移动一小步；"
     "规则书 §8 / engine.adapt_holes 移植）。ball_game 的 'hole_due' 接线 import 它"),
    (os.path.join(BOARD, "ball_map_detect.py"), "缩略图找球（检测主路径）"),
    (os.path.join(BOARD, "ball_detect.py"),     "原图找球/背景差分"),
    (os.path.join(BOARD, "vdma_capture.py"),
     "S2MM 采集 + 掉链自愈看门狗（WD_STAT/WDDecider）。★ 2026-10-03 补进 SOURCES ——"
     "它一直是'额外的文件'（不在清单=没人对账），deploy 里悄悄留了个**没有 "
     "pulse_fails 键的旧版**，上板跑台架 wd_timeline 场景时 KeyError 才现形"
     "（PC 端那场景会跳过，所以 PC 全绿掩盖了它）。ball_detect 依赖它，必须对账"),
    (os.path.join(BOARD, "link_boot.py"),       "链路守护"),
    (os.path.join(BOARD, "load_bit.py"),        "**下 bit 的唯一实现**（别绕过它）"),
    (os.path.join(BOARD, "pl_probe.py"),        "下 bit 前的授权探针"),
    (os.path.join(BOARD, "link_doctor.py"),     "链路诊断/救援"),
    (os.path.join(BOARD, "link_boot_selftest.py"),
     "全台架自检（PC 与板上同一套：链路/输入层/坏帧熔断/场景门禁/"
     "ball_game AST 静态检查）。★ 2026-10-01 深夜补进 SOURCES —— 它一直只在板"
     "上（手推的），push_files.sh 推不了它，改键位那次差点漏掉后面又多了发射模式"),
    (os.path.join(BOARD, "usb_diag.py"),        "键盘/USB-A 口供电诊断（read/vbus/watch —— "
                                                "直读写 ULPI 的 OTG_CTRL 与 DEBUG 线状态）"),
    (os.path.join(BOARD, "zh_font_subset.ttf"),
     "图上中文的子集化字体（v3_scan.render_dashed 用；164 字形 25KB）"),
    (os.path.join(BOARD, "wp_health.py"),       "开工前文件体检（走读 + import 探针 + dmesg）"
                                                "—— 2026-10-01 深夜 'import pynq 段错误'"
                                                "事故之后，开工第一步先跑它"),
    (os.path.join(BOARD, "particle_pl.py"),     "★ 2026-10-16 粒子引擎驱动 + 上板探针"
                                                "（probe：T0/P2 逐位/P3 进洞/P4 湮灭/P5 弹性）"),
    (os.path.join(BOARD, "phys_objects.py"),    "★ 2026-10-19 实物识别（黑长条=障碍/"
                                                "绿椭圆=传送门/黑球=洞口标记；ball_game import）"),
    (os.path.join(BOARD, "map_detect_pl.py"),
     "★ 2026-10-20 差距1 PL 检测 v0 驱动/上板探针（probe：VERSION/SEQ/med/vhi/"
     "三色掩码计数；watch 看 PL 处理帧率）"),
    (os.path.join(BOARD, "map_detect_compare.py"),
     "★ 2026-10-20 PL 检测 v0 的 PS 同帧对拍（缩略图快照 + cv2 参考，只读）"),
    (os.path.join(BOARD, "map_detect_candidates.py"),
     "★ 2026-10-20 PL 检测 v1 上板验收（PL 候选统计 vs PS CCL + 混合口径 vs detect_map）"),
    (os.path.join(BOARD, "nblock_check.py"),
     "★ 每局块数 n=1..9 的 PL vs 黄金模型逐位一致终审（2026-10-07 M2.5；"
     "改过 RTL/建场后上板跑一遍，游戏必须先停——它是 PYNQ 客户端）"),
    # 夹具：坏帧熔断与场景门禁的自检要用
    (os.path.join(BOARD, "fx_map_clean.bin"),   "干净帧夹具（熔断自检用）"),
    (os.path.join(BOARD, "fx_map_torn.bin"),    "撕裂帧夹具（熔断自检用）"),
    # PC 侧黄金模型：板上 --arcs 自检要跟它逐位对比
    (os.path.join(ROOT, "software", "pc", "vision_golden.py"), "黄金模型（逐位一致性的参照）"),
]


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def refresh_bit_sidecar():
    """把 deploy 里 .bit 的当前 md5 写进边车 `*.bit.md5`。

    ★ 2026-10-01 补：load_bit.py 的期望值**优先取边车文件**（"bit 走到哪它跟
      到哪"，见它 _expected_md5 的说明）。可这之前**没人生成它** —— deploy 里
      那份一直停在旧 bit（5828cda4…）上，谁不用 --md5 谁就被对账拦下。
      --fix 顺手刷新它（只在内容变了时才写，会打印一行）。
      格式照 md5sum：`<hex> *<文件名>`（load_bit 只取第一段）。
    """
    for name in sorted(os.listdir(DEPLOY)):
        if not name.endswith(".bit"):
            continue
        p = os.path.join(DEPLOY, name)
        side = p + ".md5"
        line = "%s *%s\n" % (md5(p), name)
        try:
            old = open(side).read() if os.path.isfile(side) else None
        except Exception:
            old = None
        if old != line:
            with open(side, "w") as f:
                f.write(line)
            print("  ★ 边车已刷新：%s" % side)
            print("      %s" % line.strip())


def main():
    fix = "--fix" in sys.argv
    quiet = "--quiet" in sys.argv

    if not os.path.isdir(DEPLOY):
        print("路径不对：%s 不存在" % DEPLOY)
        print("  -> 构建还没跑到部署那一步，或者 proj_dir 变了。")
        return 2

    missing, stale, ok = [], [], []
    for src, why in SOURCES:
        name = os.path.basename(src)
        dst = os.path.join(DEPLOY, name)
        if not os.path.isfile(src):
            missing.append((name, "**上游源文件不存在**：" + src, why))
            continue
        if not os.path.isfile(dst):
            missing.append((name, "deploy 包里没有", why))
            continue
        if md5(src) != md5(dst):
            stale.append((name, "内容不一致（deploy 里的是旧的）", why))
            continue
        ok.append(name)

    # 只有 .py/.sh/.bin 会被扫进包；多出来的东西不算错，但打出来给人看
    have = set(os.listdir(DEPLOY))
    extra = sorted(n for n in have
                   if n not in {os.path.basename(s) for s, _ in SOURCES}
                   and not n.endswith(".hwh"))

    bad = missing + stale
    if fix:
        # ★ 注意：--fix 的两件事**不等价出现** —— 驱动文件没事的时候 bad 是空的，
        #   但边车照样要刷（它不属于 SOURCES，从来不进 bad）。所以这里不能只挂在
        #   `bad and fix` 上（第一版就是这么写的，结果 --fix 跑完边车纹丝不动）。
        if bad:
            n = 0
            for name, _, _ in bad:
                src = next(s for s, _ in SOURCES if os.path.basename(s) == name)
                if os.path.isfile(src):
                    shutil.copy2(src, DEPLOY)
                    n += 1
            print("已拷入 %d 个文件。**重跑一次确认**（不要凭'我修过了'就上板）：" % n)
            print()
        refresh_bit_sidecar()
        sys.argv = [a for a in sys.argv if a != "--fix"]
        return main()

    if not quiet or bad:
        print("=" * 72)
        print("  deploy 包体检：%s" % DEPLOY)
        print("=" * 72)
        print("  源目录：%s" % BOARD)
        print()
        print("  ✓ 内容一致        %2d 个" % len(ok))
        for name, _, _ in missing:
            print("  ✗ 缺失            %s" % name)
        for name, _, _ in stale:
            print("  ! 内容过期        %s" % name)
        if extra:
            print("  · 额外的文件      %s" % ", ".join(extra))
        print()
        if bad:
            print("  ---- 缺/过期的逐条理由 ----")
            for name, what, why in missing + stale:
                print("   %-24s %s" % (name, what))
                print("   %-24s   为什么必须有：%s" % ("", why))
            print()
            print("  修法：python deploy_check.py --fix")
        else:
            print("  包完整、且每个文件的内容都与源一致。")
        print()

    # ---- 上板前的两条硬不变量（脚本能查，且**必须**查）----
    print("  ---- 上板前的不变量 ----")
    n_ok = True
    p_hdmi = os.path.join(DEPLOY, "hdmi_out.py")
    p_tab = os.path.join(DEPLOY, "dynclk_tables.py")
    if os.path.isfile(p_hdmi) and not os.path.isfile(p_tab):
        print("  ✗ dynclk_tables.py 不在 -- hdmi_out.py 会 import 失败（必须同目录）")
        n_ok = False
    else:
        print("  ✓ hdmi_out.py 与 dynclk_tables.py 同目录")

    bits = sorted(n for n in have if n.endswith(".bit"))
    if not bits:
        print("  ✗ 包里没有 .bit -- 构建没跑到写码流，或者写失败了")
        n_ok = False
    else:
        # ★ 光"有 .bit"不够 —— 还必须是**本次实现的产物**。
        #   2026-09-30 就撞上过：包里的 bit 是 09-25 那次构建的（那时**还没有 HDMI 的
        #   IP**），而"包完整"这个结论会让人放心地把它下到板上，然后在屏幕上什么也看不到，
        #   再花一晚去查引脚/时钟。判据：与 impl_1 的输出**逐字节相同**（比 md5）。
        impl_bit = os.path.join(ROOT, "vivado", "ov5640_pynq", "proj",
                                "ov5640_m1a.runs", "impl_1",
                                "ov5640_m1a_wrapper.bit")
        impl_md5 = md5(impl_bit) if os.path.isfile(impl_bit) else None
        for b in bits:
            p = os.path.join(DEPLOY, b)
            d_md5 = md5(p)
            if impl_md5 is None:
                print("  ? %s  (%d 字节, md5=%s)" % (b, os.path.getsize(p), d_md5))
                print("     impl_1 里没有对应产物 -> **无法判定这个 bit 是不是本次的**")
                print("     （构建没跑到写码流。别下板，先看 build 日志的 DONE 标记）")
                n_ok = False
            elif d_md5 != impl_md5:
                print("  ✗ %s  **不是本次实现的产物**（与 impl_1 内容不一致）" % b)
                print("     deploy 里 : %s" % d_md5)
                print("     impl_1 里 : %s  <- 这个才是新的" % impl_md5)
                print("     -> 症状会是『下板了但屏幕全黑』，而所有软件侧检查都正常")
                n_ok = False
            else:
                import time as _t
                mt = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(os.path.getmtime(p)))
                print("  ✓ %s  == impl_1 的产物，是本次的（%d 字节, %s）"
                      % (b, os.path.getsize(p), mt))
                print("     md5=%s" % d_md5)
                print("     上板对账用上面这个 md5（load_bit.py 会拿它比对板上的）")

    # ---- 不变量 3：驱动里**写死的基地址**必须与 BD 实际分配的一致 ----
    # 为什么值得单独查：`hdmi_out.py` 把四个地址写成常量（0x43C00000 起）。
    # 一旦 BD 改了（增删 IP、改地址段），常量就**悄悄**失效 —— 症状是上板第一秒
    # `selftest` 就失败，然后你会以为是 bit 没下对/硬件坏了，白烧一个上板窗口。
    # 2026-09-30 手工核过一次（四个全中，`axi_vdma_out`/`axi_dynclk`/`vtc_out`/
    # `hdmi_out_hpd_video`）；现在把它自动化，免得下次 BD 一变又要靠人去记。
    if os.path.isfile(HWH):
        import re
        import xml.etree.ElementTree as ET
        try:
            root = ET.parse(HWH).getroot()
        except Exception as e:
            print("  ✗ 解析 hwh 失败（%s）-> 地址这一项**判定不了**，别当成通过" % e)
            n_ok = False
            root = None
        if root is not None:
            where = {}
            for mr in root.iter("MEMRANGE"):
                inst, base = mr.get("INSTANCE"), mr.get("BASEVALUE")
                if inst and base:
                    where.setdefault(inst, []).append(int(base, 16))
            for fname, const, inst in ADDR_EXPECT:
                src = os.path.join(BOARD, fname)
                if not os.path.isfile(src):
                    continue
                m = re.search(r"^%s\s*=\s*(0x[0-9A-Fa-f]+)" % const,
                              open(src, encoding="utf-8", errors="replace").read(),
                              re.M)
                if not m:
                    print("  ? %s:%s 找不到这个常量（改名了？）" % (fname, const))
                    n_ok = False
                    continue
                got = int(m.group(1), 16)
                exp = where.get(inst)
                if not exp:
                    print("  ✗ %s:%s=0x%08X，但 hwh 里**没有** %s 这个 IP"
                          % (fname, const, got, inst))
                    n_ok = False
                elif got not in exp:
                    print("  ✗ %s:%s=0x%08X，而 hwh 里 %s 在 %s"
                          % (fname, const, got, inst,
                             "/".join("0x%08X" % x for x in exp)))
                    n_ok = False
                else:
                    print("  ✓ %s:%s=0x%08X == hwh 的 %s" % (fname, const, got, inst))
    else:
        print("  ? 找不到 hwh（%s）-> **地址一致性判定不了**" % HWH)
        print("     （BD 还没生成过？构建没跑到？）别把这当成通过。")
        n_ok = False

    shs = sorted(n for n in have if n.endswith(".sh"))
    if shs:
        print("  · 上板后先 chmod +x ：" + " ".join(shs))

    print()
    if bad:
        print("结论：包**不完整**（%d 个缺/过期）-> 别上板，先 --fix" % len(bad))
        return 1
    if not n_ok:
        print("结论：文件都在，但上板前的不变量没过 -> 见上面 ✗")
        return 1
    print("结论：deploy 包可上板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
