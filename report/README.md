# report/ —— 设计报告与大模型协作记录

本目录对应赛方 3.3.5.3「设计报告」与 3.3.3.1「大模型协作记录」的要求。

| 文件 | 内容 |
|---|---|
| `design_report.md` | 主设计报告：5 部分、20 页、7 组图（图 6 分 a/b）；16 粒交付 bit 数据 |
| `llm_collaboration.md` | 大模型协作记录：提示词、模型回答、自我纠错轨迹、智能体工作流、脱敏声明 |
| `figures/system_architecture.svg` | 系统框图（图 2） |
| `photos/` | 报告用图与原始照片（清单见 `photos/README.md`） |
| `evidence/` | 16 粒交付 bit `67412907` 的 2026-11-01 原始综合/实现报告 |

## 如何阅读

- 评委/队友：先读 `design_report.md` 摘要与第 1、2、3 章；
- 想复现：直接跳到第 5 章，配合 `data/docs/build_and_migrate_2026.x.md`；
- 想了解协作过程：读 `llm_collaboration.md` 第 3 节的案例；
- 想看原始数据：`data/reports/` 下的 `.rpt` / `.txt` 原始文件。

## 证据与数据版本

- **交付 bit**：`674129074bf711d273f30f2ee74aa449`（2026-11-01，16 粒，Vivado 2023.2）；
- **交付 bit 时序/资源**：WNS +0.094 ns / WHS +0.013 ns；LUT 79.60 % / FF 39.57 % / BRAM 34.64 % / DSP 9.55 %；原始 `.rpt` 在 `data/evidence/`；
- **2026.1 迁移验证**：bit `f7eb6178`，WNS +0.029 / WHS +0.017，LUT 79.89%；板上 P1–P6、488/488、v3 17/17（6.36ms vs 1555.8ms ≈245×）；原始报告为 `data/evidence/ov5640_m1a_wrapper_timing_summary_postroute_physopted_2026.1.rpt` 与 `data/evidence/ov5640_m1a_wrapper_utilization_placed_2026.1.rpt`；
- **10-31 基线**：`db96e197f9f4cffb3c42061a9cda15a0`（8 粒基线，WNS +0.085 / LUT 74.46 %）作为对照；原始 `.rpt` 在 `data/reports/`，标注“基线（非现役）”；
- PC 台架：489/489；板上：488/488；V3 17/17；PE P1–P6；PL 检测 8/8；帧泵 0/921600；画线 3523 像素；
- 粒子引擎：**16 粒**（RTL `NPART=16`），已上板（P1–P6，bit `67412907`）。

## 图表源文件

`figures/*.svg` 为手写 SVG（无外部依赖），可用浏览器、VSCode、Inkscape 打开；
导出 PDF/PNG 时可保持矢量或 2× 位图。图中标注为英文，正文为中文（符合赛方目录/文件名规范）。

## 重新生成

报告为 Markdown 源文件，可直接提交；本目录已附 PDF：

```bash
# Windows：Markdown -> 带封面的 HTML -> DOCX + PDF（Word COM）
python tools/md_to_pdf.py report/design_report.md
python tools/md_to_pdf.py report/llm_collaboration.md --subtitle "Claude Code 协作过程"
```

- 工具：`tools/md_to_pdf.py`（无第三方 Python 依赖；优先 Word COM，回退 Edge headless）；
- 输出：A4、封面、章节分页、页眉页脚、表格边框/表头底色、代码块；
- 同时生成 `.docx`，方便在 Word 里继续改；`.html` 是中间文件，不随提交包发布。

建议提交 `report/design_report.pdf` 与 `report/llm_collaboration.pdf` 各一份，
`.docx` 与 Markdown 源文件同时保留，便于评委核查和后续修改。
