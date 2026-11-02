# evidence/ —— 16 粒交付 bit 的原始报告

本目录是 **2026-11-01 16 粒交付 bit `67412907`** 的原始综合/实现报告，Vivado 2023.2 直接产出，未修改。

| 文件 | 内容 |
|---|---|
| `ov5640_m1a_wrapper_timing_summary_postroute_physopted_2026-11-01.rpt` | post-route phys_opt 时序：**WNS +0.094 ns / WHS +0.013 ns，0 失效端点** |
| `ov5640_m1a_wrapper_utilization_placed_2026-11-01.rpt` | placed 资源：**LUT 42,347（79.60%）、FF 42,101（39.57%）、BRAM 48.5（34.64%）、DSP 21（9.55%）** |

## 版本说明

- **交付 bit**：`674129074bf711d273f30f2ee74aa449`（2026-11-01，16 粒，`NPART=16`）；
- **首跑** WNS −1.035 / 1287 失败端点，原因是布图拥塞；只重跑实现、place 换 `ExtraNetDelay_high` 后 +0.094；
- **10-31 基线**：`db96e197f9f4cffb3c42061a9cda15a0`（8 粒基线，WNS +0.085 / LUT 74.46）。
  它的原始报告仍在 `docs/reports/`，标注“基线（非现役）”；**数字不改**，只作对照。

这样评委能看到：10-31 是基线数据来源，11-01 是最终交付版本，两边各自的证据分开。

> 板级 P1–P6 / 488/488 的结果来自 `HANDOFF.md` 2026-11-01 条目；板级原始日志未随包提交，未编造。


## 2026.1 迁移验证（2026-11-02）

| 文件 | 内容 |
|---|---|
| `ov5640_m1a_wrapper_timing_summary_postroute_physopted_2026.1.rpt` | **WNS +0.029 / WHS +0.017，0 失效端点** |
| `ov5640_m1a_wrapper_utilization_placed_2026.1.rpt` | **LUT 42,501（79.89%）、FF 42,316（39.77%）、BRAM 48.5（34.64%）** |

- bit `f7eb6178016684685cf141b0dbf13b55`（4,045,773 B）；板上 P1–P6、488/488、v3 17/17（6.36ms vs 1555.8ms ≈245×）。
- 2023.2 净室最终源码：WNS +0.063 / WHS +0.013，bit `df39735b`。
- 注意：`_cleanroom_2026_1/docs/reports/` 里有两份旧拷贝（+0.085/74.46），不是 2026.1 的产物；以本目录从 `proj/impl_1` 拷出的两份为准。
