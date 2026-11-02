#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_recommended_tree.py — build the competition-recommended submission tree.

Reads the filtered package produced by make_submission_package.py and lays it
out as the competition guide's recommended root structure:

    src/ sim/ build/ board/ data/ skill/ report/

The report's reproduction paths (build/vivado/ov5640_pynq, board/deploy,
data/evidence, data/docs) match this tree.

Usage:
    python tools/make_recommended_tree.py --out "D:/Desktop/PYNQ_Z2"

The output directory must be empty or absent. Exit codes: 0 ok, 1 check failed,
2 copy error.
"""
import argparse
import datetime
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, 'dist')

IGNORE = shutil.ignore_patterns(
    '__pycache__', '*.pyc', '*.pyo', '.DS_Store', 'Thumbs.db',
    '*.build_*.html', '*.TIMING_FAILED', '*.wdb', '*.jou', '*.pb',
)


def latest_package():
    candidates = []
    for name in os.listdir(DIST):
        p = os.path.join(DIST, name)
        if os.path.isdir(p) and name.startswith('pynq-magnetic-field-demo_submission_'):
            candidates.append(p)
    if not candidates:
        raise SystemExit('ERROR: no filtered package found under dist/')
    return max(candidates, key=os.path.getmtime)


def copy_dir(src, dst, ignore=IGNORE):
    if not os.path.isdir(src):
        raise SystemExit('ERROR: missing source dir: %s' % src)
    shutil.copytree(src, dst, ignore=ignore)


def copy_top_md(src, dst):
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        p = os.path.join(src, name)
        if os.path.isfile(p) and name.lower().endswith('.md'):
            shutil.copy2(p, os.path.join(dst, name))


def copy_game_pc(dst):
    src = os.path.join(ROOT, 'game_pc')
    os.makedirs(dst, exist_ok=True)
    for name in ('engine.py', 'main.py', 'test_render_equiv.py', 'README.md'):
        p = os.path.join(src, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dst, name))


def copy_tb(src_rtl, dst_tb):
    os.makedirs(dst_tb, exist_ok=True)
    count = 0
    for dirpath, _dirs, files in os.walk(src_rtl):
        for name in files:
            if name.startswith('tb_') and name.endswith('.v'):
                shutil.copy2(os.path.join(dirpath, name),
                             os.path.join(dst_tb, name))
                count += 1
    return count


def write_readmes(out):
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8') as f:
        f.write('''# 基于PYNQ的粒子磁迹交互系统——提交包

本包为竞赛要求的**工程包 + 技能包 + 设计报告**三合一，根目录直接采用推荐结构
`src/ sim/ build/ board/ data/ skill/ report/`。

## 目录

| 目录 | 内容 |
|---|---|
| `src/` | 设计源码：`rtl/`（Verilog RTL）、`software/`（PS 源码）、`game_pc/`（PC 物理引擎与工具） |
| `sim/` | 仿真脚本与 `tb/` 下的 13 个 testbench |
| `build/` | 可复现构建快照：`vivado/ov5640_pynq/`（Tcl/BD/IP/RTL/deploy）、`tools/`、`software/`、`game_pc/` |
| `board/` | 上板包 `deploy/`：bit/HWH 与板端运行脚本 |
| `data/` | `docs/`（文档）、`reports/`（综合/实现报告）、`evidence/`（交付与迁移原始 .rpt）、`captures/`（实拍数据） |
| `skill/` | 7 项可复用技能包（赛方 3.3.5.2） |
| `report/` | 设计报告与《大模型协作记录》（PDF/DOCX/Markdown） |

> `build/` 是可执行构建快照，保留完整 `vivado/` 树以便 `build_after_patch.tcl`
> 和 `finish_build.py` 按原路径工作；`src/` 是同一份源码的阅读视图。
> `build/software/`、`build/game_pc/` 是构建验收脚本使用的副本。

## 快速开始

### 构建

```bash
cd build/vivado/ov5640_pynq
<vivado> -mode batch -source build_project.tcl          # 从零构建
# 或改 RTL 后：
<vivado> -mode batch -source build_after_patch.tcl      # 清缓存、重置 OOC
cd ../.. && python build/tools/finish_build.py          # IP 基线 / deploy 对账
```

### 上板

```bash
scp board/deploy/* xilinx@<board-ip>:/home/xilinx/wp/
ssh xilinx@<board-ip>; cd /home/xilinx/wp
PY=/usr/local/share/pynq-venv/bin/python3
$PY wp_health.py; $PY vdma_state.py; $PY pl_probe.py; $PY load_bit.py
./start_demo.sh
```

### 验证

- 仿真：`sim/run_v3_sim.sh`、`sim/run_map_detect_sim.sh`、`sim/tb/` 下 testbench；
- 构建后验收：`python build/tools/finish_build.py`；
- 板上台架：`board/deploy/link_boot_selftest.py`、`v3_shoot.sh --cam`、`line_bench.py`。

## 版本与数据

- 交付 bit（2023.2）：`board/deploy/ov5640_m1a_wrapper.bit`，MD5 `67412907...`；
- 2026.1 迁移 bit：`f7eb6178...`，独立记录在 `data/evidence/*_2026.1.rpt` 与设计报告 §5.2/附录 B；
- 原始时序/资源报告：`data/evidence/`；其他实测报告：`data/reports/`。

## 赛方要求对照

- **3.3.5.2 技能包**：`skill/README.md` 索引 7 项，覆盖提示词工作流、案例模板、校验脚本、踩坑清单；
- **3.3.5.3 设计报告**：`report/design_report.pdf` + `report/llm_collaboration.pdf`；
- **3.3.5.4 目录规范**：根目录即 `src/ sim/ build/ board/ data/ skill/ report/`，文件名 ASCII，中文只在正文。

本包不含 WiFi 口令、令牌或私钥。代码采用 MIT 许可证，见 `LICENSE`。
''')

    with open(os.path.join(out, 'src', 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# src/ — 设计源码\n\n'
                '- `rtl/`：Verilog RTL（相机链路、V3 轨迹束、粒子引擎、PL 检测、画线/帧泵）；\n'
                '- `software/`：板上 Python 与 PC 工具源码；\n'
                '- `game_pc/`：PC 物理引擎、渲染与等价性测试。\n\n'
                '构建时使用 `build/vivado/ov5640_pynq/` 内的同一份 RTL。\n')

    with open(os.path.join(out, 'build', 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# build/ — 可复现构建快照\n\n'
                '从 `build/vivado/ov5640_pynq/` 运行 Vivado Tcl；'
                '构建后在包根执行 `python build/tools/finish_build.py`。\n'
                '`build/software/`、`build/game_pc/` 是构建验收脚本使用的源码副本。\n')

    with open(os.path.join(out, 'board', 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# board/ — 上板包\n\n'
                '`deploy/` 内含交付 bit、HWH 与板端运行脚本；'
                '复制到板端 `/home/xilinx/wp/` 后按设计报告 §5.1 运行。\n')

    with open(os.path.join(out, 'data', 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# data/ — 数据与原始报告\n\n'
                '- `docs/`：构建/迁移、演示、规则等项目文档；\n'
                '- `reports/`：净室构建、板上验收等实测报告；\n'
                '- `evidence/`：交付 bit 与 2026.1 迁移 bit 的原始时序/资源 .rpt；\n'
                '- `captures/`：实拍采集数据。\n')

    with open(os.path.join(out, 'sim', 'README.md'), 'w', encoding='utf-8') as f:
        f.write('# sim/ — 仿真\n\n'
                '`tb/` 含 13 个 testbench；`run_v3_sim.sh` / `run_map_detect_sim.sh` '
                '可直接运行；详细对拍口径见设计报告 §3.1。\n')


def check_tree(out):
    required = [
        'src/rtl/v3_bundle/particle_engine.v',
        'src/software/board/ball_game.py',
        'sim/tb/tb_particle_engine.v',
        'build/vivado/ov5640_pynq/build_project.tcl',
        'build/vivado/ov5640_pynq/rtl/v3_bundle/particle_engine.v',
        'build/tools/finish_build.py',
        'board/deploy/ov5640_m1a_wrapper.bit',
        'data/docs/build_and_migrate_2026.x.md',
        'data/evidence/ov5640_m1a_wrapper_timing_summary_postroute_physopted_2026.1.rpt',
        'skill/README.md',
        'report/design_report.pdf',
        'report/llm_collaboration.pdf',
    ]
    missing = [p for p in required if not os.path.exists(os.path.join(out, p))]
    return missing


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', required=True, help='target directory (must be empty/absent)')
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    pkg = latest_package()

    if os.path.exists(out) and os.listdir(out):
        print('ERROR: output exists and is not empty: %s' % out)
        return 2
    if not os.path.exists(out):
        os.makedirs(out)

    try:
        copy_dir(os.path.join(pkg, 'vivado', 'ov5640_pynq', 'rtl'),
                 os.path.join(out, 'src', 'rtl'))
        copy_dir(os.path.join(pkg, 'software'), os.path.join(out, 'src', 'software'))
        copy_game_pc(os.path.join(out, 'src', 'game_pc'))

        copy_dir(os.path.join(pkg, 'sim'), os.path.join(out, 'sim'))
        n_tb = copy_tb(os.path.join(pkg, 'vivado', 'ov5640_pynq', 'rtl'),
                       os.path.join(out, 'sim', 'tb'))
        print('testbenches: %d' % n_tb)

        copy_dir(os.path.join(pkg, 'vivado', 'ov5640_pynq'),
                 os.path.join(out, 'build', 'vivado', 'ov5640_pynq'))
        copy_dir(os.path.join(pkg, 'tools'), os.path.join(out, 'build', 'tools'))
        me = os.path.join(ROOT, 'tools', 'make_recommended_tree.py')
        if os.path.isfile(me):
            shutil.copy2(me, os.path.join(out, 'build', 'tools', 'make_recommended_tree.py'))
        copy_dir(os.path.join(pkg, 'software'), os.path.join(out, 'build', 'software'))
        copy_game_pc(os.path.join(out, 'build', 'game_pc'))

        copy_dir(os.path.join(pkg, 'vivado', 'ov5640_pynq', 'deploy'),
                 os.path.join(out, 'board', 'deploy'))
        # deploy/ is built from a curated list; vdma_state.py is referenced by
        # the report's board bring-up sequence but is not in that list.
        vdma = os.path.join(pkg, 'software', 'board', 'vdma_state.py')
        if os.path.isfile(vdma):
            shutil.copy2(vdma, os.path.join(out, 'board', 'deploy', 'vdma_state.py'))

        copy_top_md(os.path.join(pkg, 'docs'), os.path.join(out, 'data', 'docs'))
        copy_dir(os.path.join(pkg, 'docs', 'reports'), os.path.join(out, 'data', 'reports'))
        copy_dir(os.path.join(pkg, 'report', 'evidence'), os.path.join(out, 'data', 'evidence'))
        copy_dir(os.path.join(pkg, 'software', 'captures'), os.path.join(out, 'data', 'captures'))

        copy_dir(os.path.join(pkg, 'skill'), os.path.join(out, 'skill'))
        for name in os.listdir(os.path.join(pkg, 'report')):
            if name == 'evidence':
                continue
            src = os.path.join(pkg, 'report', name)
            dst = os.path.join(out, 'report', name)
            if os.path.isdir(src):
                shutil.copytree(src, dst, ignore=IGNORE)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)

        shutil.copy2(os.path.join(pkg, 'LICENSE'), os.path.join(out, 'LICENSE'))
        write_readmes(out)
    except Exception as e:
        print('ERROR: copy failed: %s' % e)
        return 2

    missing = check_tree(out)
    if missing:
        print('RESULT: FAIL — missing:')
        for m in missing:
            print('  ' + m)
        return 1
    print('RESULT: PASS — %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
