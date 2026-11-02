# 基于PYNQ的粒子磁迹交互系统——提交包

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
