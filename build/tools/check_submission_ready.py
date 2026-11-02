# -*- coding: utf-8 -*-
"""check_submission_ready.py —— 提交源码前的自检（2026-10-31 立）

检查四件事（任何 ERROR 都不该提交）：
  1. 必需文件在不在（RTL/BD/约束/IP 仓库/构建 Tcl/时序门禁/板上驱动）
  2. 有没有把**可再生成的重物**打进去（proj/、.cache/.gen/.runs、xsim.dir、
     __pycache__、*.wdb/jou/pb、*.TIMING_FAILED、.git）
  3. 可执行代码里有没有**本机绝对路径**（D:/ F:/ C:/ —— 换机器就炸；
     注释里的路径只 WARN，文档用途）
  4. 有没有疑似真实凭据（github_pat_/ghp_/AKIA/sk-…）——发现就 ERROR 并停

用法（仓库根或解压后的交接包根）：
    python tools/check_submission_ready.py [根目录]
退出码：0=全过 / 1=有 ERROR / 2=发现疑似凭据（必须先人工处理）
"""
import os
import re
import sys
try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

REQUIRED = [
    'CLAUDE.md', 'HANDOFF.md',
    'vivado/ov5640_pynq/build_project.tcl',
    'vivado/ov5640_pynq/build_after_patch.tcl',
    'vivado/ov5640_pynq/timing_gate.tcl',
    'vivado/ov5640_pynq/bd/m1a_capture.tcl',
    'vivado/ov5640_pynq/bd/ps7_pynq_z2_preset.tcl',
    'vivado/ov5640_pynq/constrs/ov5640_pynq.xdc',
    'vivado/ov5640_pynq/ip_repo/ip/rgb2dvi/component.xml',
    'vivado/ov5640_pynq/ip_repo/ip/axi_dynclk/component.xml',
    'vivado/ov5640_pynq/rtl/v3_bundle/v3_bundle_axi.v',
    'vivado/ov5640_pynq/rtl/v3_bundle/pe_axi.v',
    'vivado/ov5640_pynq/rtl/v3_bundle/frame_pump.v',
    'vivado/ov5640_pynq/rtl/map_detect/map_detect_core.v',
    'software/board/ball_game.py',
    'software/board/hdmi_out.py',
    'software/board/dynclk_tables.py',
    'software/board/load_bit.py',
    'software/board/start_demo.sh',
]
FORBID_DIRS = {'proj', '.git', 'xsim.dir', '__pycache__', '.Xil',
               'handoff-context', 'logs'}
# 辅助/遗留 PC 脚本：绝对路径只 WARN（不影响 FPGA 构建/上板）；构建与板上代码仍算 ERROR
AUX_PREFIXES = ('game_pc/', 'software/pc/', 'tools/',
                'software/closed_loop.py', 'software/live_demo.py',
                'software/test_')
FORBID_SUFFIX = ('.wdb', '.jou', '.pb', '.pyc', '.TIMING_FAILED')
TEXT_EXT = ('.tcl', '.v', '.sv', '.vh', '.py', '.sh', '.xdc', '.md', '.txt')
ABS = re.compile(r'(?<![A-Za-z0-9_])[A-Za-z]:[\\/]')
CRED = [
    re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
    re.compile(r'\bghp_[A-Za-z0-9]{20,}'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bsk-[A-Za-z0-9]{24,}'),
]


def main(argv):
    root = argv[1] if len(argv) > 1 else os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    print('检查根目录：%s' % root)
    err = warn = 0
    print('\n[1] 必需文件')
    for rel in REQUIRED:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            print('  [OK]   %s' % rel)
        else:
            print('  [ERR]  缺 %s' % rel)
            err += 1
    print('\n[2] 不该出现的重物/缓存')
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ('.git',)]
        for d in list(dns):
            if d in FORBID_DIRS:
                print('  [ERR]  目录 %s' % os.path.relpath(
                    os.path.join(dp, d), root))
                err += 1
                dns.remove(d)
        for fn in fns:
            if fn.lower().endswith(FORBID_SUFFIX):
                print('  [ERR]  文件 %s' % os.path.relpath(
                    os.path.join(dp, fn), root))
                err += 1
    print('\n[3] 代码里的本机绝对路径（注释只 WARN）')
    cred_hit = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in FORBID_DIRS]
        for fn in fns:
            if not fn.lower().endswith(TEXT_EXT):
                continue
            p = os.path.join(dp, fn)
            if os.path.getsize(p) > 2 * 1024 * 1024:
                continue
            try:
                lines = open(p, encoding='utf-8', errors='replace')
            except Exception:
                continue
            for ln, line in enumerate(lines, 1):
                for cr in CRED:
                    if cr.search(line):
                        print('  [CRED] %s:%d 疑似凭据' % (
                            os.path.relpath(p, root), ln))
                        cred_hit = 1
                m = ABS.search(line)
                if not m:
                    continue
                _seg = (line[m.start():m.start() + 12].lower()
                        .replace('\\', '/'))
                if _seg.startswith('c:/windows'):
                    continue
                rel = os.path.relpath(p, root).replace('\\', '/')
                _aux = any(rel.startswith(a) for a in AUX_PREFIXES)
                if (_aux or line.lstrip().startswith(('#', ';', '//'))
                        or os.path.splitext(p)[1].lower() in ('.md', '.txt')):
                    print('  [WARN] %s:%d 文档/辅助脚本含绝对路径' % (rel, ln))
                    warn += 1
                else:
                    print('  [ERR]  %s:%d 代码含绝对路径：%s' % (
                        rel, ln, line.strip()[:90]))
                    err += 1
    print('\n[4] 凭据扫描：%s' % ('发现疑似凭据！' if cred_hit else '无'))
    print('\n==== 汇总：ERROR %d / WARN %d ====' % (err, warn))
    if cred_hit:
        print('★ 发现疑似真实凭据：停下，人工确认为假/删除后再提交。')
        return 2
    if err:
        print('★ 有 ERROR，别提交。')
        return 1
    print('★ 源码树自检通过（可提交）。')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
