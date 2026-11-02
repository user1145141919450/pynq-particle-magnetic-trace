# 综合与实现报告（2026-10-31 基线 + 2026-11-01 16 粒交付）

本目录是 **2026-10-31 基线 bit** 的原始报告 + 摘要。原始报告由 Vivado 2023.2 直接产出，
未做任何修改；数字以原始报告为准。

## 2026-10-31 基线 bit（非现役）

| 项 | 值 |
|---|---|
| Bit | `ov5640_m1a_wrapper.bit`，md5 **`db96e197f9f4cffb3c42061a9cda15a0`** |
| 构建时间 | 2026-10-31 15:36 |
| 器件 | `xc7z020clg400-1`（PYNQ-Z2） |
| 主时钟 | `clk_fpga_0` = 100 MHz（10.000 ns） |
| 源 | git 标签 `backup-2026-10-31-demo`（游戏源码 `a0124ea`） |

> **交付 bit 是 `67412907`（2026-11-01，16 粒）**；其原始报告在 `data/evidence/`。
> 本目录的 10-31 数字仅作基线对照，未改动。

## 时序（原始：`ov5640_m1a_wrapper_timing_summary_postroute_physopted.rpt`）

| WNS | TNS | 失效端点 | WHS | THS | WPWS |
|---|---|---|---|---|---|
| **+0.085 ns** | 0.000 | 0 | **+0.017 ns** | 0.000 | +0.333 ns |

- 门禁口径：WNS ≥ 0 且 WHS ≥ 0（`timing_gate.tcl`；不过则 bit 改名 `.TIMING_FAILED`）
- 说明：**首次全量构建**（place `ExtraTimingOpt`）WNS **−0.049** 被拦；**只重跑实现**
  换 place **`ExtraPostPlacementOpt`**（route `NoTimingRelaxation` + post-route
  phys_opt `Explore`）后 **+0.085**。该配方已固化进 `build_project.tcl` /
  `build_after_patch.tcl`，从零构建按文档走即复现这条路径。

## 资源（原始：`ov5640_m1a_wrapper_utilization_placed.rpt`）

| 资源 | 用量 | 总量 | 占比 |
|---|---|---|---|
| Slice LUTs | 39,613 | 53,200 | **74.46 %** |
| LUT as Memory | 1,939 | 17,400 | 11.14 % |
| Slice Registers | 41,398 | 106,400 | 38.91 % |
| Block RAM Tile | 48.5 | 140 | 34.64 % |
| DSPs | 21 | 220 | 9.55 % |

> LUT 74.5% 是本设计最紧的资源，也是换 Vivado 版本时**时序风险的主要来源**
> （布图稍有变化就可能把 WNS 顶过线）。迁移时先看时序门禁，再看资源。

## 如何重新生成这两份报告

```bash
# 2023.2，包根/build/vivado/ov5640_pynq
<vivado> -mode batch -source build_after_patch.tcl
# 报告位置：
#   proj/ov5640_m1a.runs/impl_1/ov5640_m1a_wrapper_timing_summary_postroute_physopted.rpt
#   proj/ov5640_m1a.runs/impl_1/ov5640_m1a_wrapper_utilization_placed.rpt
# 门禁文本：构建日志里的 "★时序门禁★ WNS=... WHS=..."
```

## 原始实测输出（本目录）

- `onboard_acceptance_2026-10-31.txt`：上板 `load_bit` 复查 + 帧泵台架两档
  0/921600、73.5/70.5 ms/帧（bit db96e197）
- `pc_selftest_2026-10-31.txt`：PC 离线台架 `link_boot_selftest.py` **489/489**
