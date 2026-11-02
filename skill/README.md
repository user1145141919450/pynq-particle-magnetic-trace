# skill/ —— 可复用技能包

> 对应赛方 2026 选题指南 3.3.5.2「技能包」。
> 本目录把项目开发过程中**真实踩过的坑**提炼成陌生队伍可以直接复用的提示词、脚本、
> 模板与清单。每一项都写明：**适用范围、使用方法、失效条件、已验证效果、来源失败**。

## 0. 怎么用

1. 先读本页的索引表，按你的题目类型选 1~3 项；
2. 进入对应目录读 `README.md`，按“使用方法”操作；
3. `scripts/` 里的脚本是**参考实现**：路径、寄存器名、bit 名需要按你的工程替换；
4. `case-templates/` 可直接复制为你的报告/验证矩阵/提交检查表；
5. `pitfalls/README.md` 建议在项目开始时通读一遍，能省掉最贵的一类时间；
6. **不用板子先验判据**：四个脚本自带 `--selftest`，只需要 Python 3
   （不要 numpy、不要 PYNQ），一分钟内能跑完：

```bash
python pynq-safe-overlay/scripts/cma.py                  --selftest   # 5 条：fill 必 flush、snapshot 必 invalidate
python axi-regmap/scripts/regmap.py                      --selftest   # 6 条：含"卡死位必须被抓到"
python vivado-repro-gate/scripts/bit_freshness.py        --selftest   # 5 条：旧边车/旧产物必须被拦
python golden-hdl-verify/scripts/golden_compare.py       --selftest   # 7 条：差异/容差/进制/字段数/记录数
```

**安全**：本目录不含 WiFi 口令、令牌或私钥。`pynq-safe-overlay/scripts/load_bit_reference.py`
中的 `xilinx` 是 PYNQ 官方默认口令的示例；复用时请改为 `<board-password>` 或环境变量。

---

## 1. 技能索引

| 技能 | 类型 | 适用范围 | 已验证效果（本项目） | 失效条件 |
|---|---|---|---|---|
| [`pynq-safe-overlay`](pynq-safe-overlay/README.md) | 通用 PYNQ Skill（加分项） | 任何 PYNQ/Zynq + PL overlay + DMA 的项目 | 相机约15 fps（实测14.7~15）、scl 0 错；多次救回野 DMA；deploy 44 项 md5 全一致；16 粒 bit 探针 P1–P6 全过 | PL 无可读身份探针；无 `/dev/mem` 权限；多客户端同时抢 DMA |
| [`axi-regmap`](axi-regmap/README.md) | 通用 PYNQ Skill（加分项） | 自己写 AXI-Lite 寄存器核的项目 | 自检 6/6；地址常量 9 条与 `.hwh` 逐 IP 对账；16 粒 P6 靠位域读判定 | 寄存器有读副作用；不是 AXI-Lite 接口 |
| [`vivado-repro-gate`](vivado-repro-gate/README.md) | 校验脚本 + 构建模板 | 有 BD/RTL 的 Vivado 工程，要求源码可复现 | 净室构建 WNS +0.112；16 粒重建 +0.094（零失败端点）；2026.1 迁移 +0.029（bit `f7eb6178`）；拦住“丢 IP/复活 ILA” | 工程依赖 GUI 手工状态；IP 不在仓库；无基线文件 |
| [`golden-hdl-verify`](golden-hdl-verify/README.md) | 校验脚本 + 案例模板 | 定点算法 RTL（物理/视觉/渲染） | 17/17、2000 子步、9/9 候选、3523 像素、0/921600、`tb_pe_axi` 13/13 | 浮点/随机/无确定参考；时序竞争未建模 |
| [`llm-agent-workflow`](llm-agent-workflow/README.md) | 提示词工作流 | 大模型辅助 FPGA/PYNQ 开发 | 18 个会话、8 个自我纠错案例 | 无版本控制/无测试门禁；涉及现场物理动作；凭据 |
| [`pitfalls`](pitfalls/README.md) | 踩坑清单 | 所有 FPGA/PYNQ 项目 | **31 条**（构建 6 / DMA 5 / 视频 7 / 部署 6 / 协作 4 / 现场 3），每条带指纹数字 | 仅为本项目经验，需按平台裁剪 |
| [`case-templates`](case-templates/README.md) | 案例模板 | 设计报告、验证矩阵、提交检查 | 本报告与提交清单按此模板整理 | 赛方模板更新时需同步 |

**对照赛方 3.3.5.2 的四类，本包的覆盖关系**：

| 官方类别 | 本包对应项 |
|---|---|
| 提示词工作流 | `llm-agent-workflow`（提示词模板 + 会话生命周期 + 交接流程） |
| 案例模板 | `case-templates`（报告 / 验证矩阵 / 提交检查表，可直接复制改名） |
| 校验脚本 | `vivado-repro-gate`（构建可复现 / 时序门禁 / bit 三方对账）、`golden-hdl-verify`（黄金模型逐位对拍）、`axi-regmap`（寄存器映射与自检） |
| 踩坑清单 | `pitfalls`（31 条，每条含触发 / 症状 / 根因 / 修复 / 预防 / 失效） |
| **通用 PYNQ Skill（单独加分）** | `pynq-safe-overlay`、`axi-regmap` —— 换题目、换板卡仍可用，见下节 |

---

## 2. 通用 PYNQ Skill（单独加分项）

`pynq-safe-overlay` 与 `axi-regmap` 是**不与本队题目绑定**的通用技能：换一个 PYNQ 题目、
换一块 Zynq 板卡，只要涉及“加载 overlay + DMA 采集/显示 + 部署脚本 + 自定义寄存器”，
都可以直接使用：

- **Overlay 安全加载**：先读 PL 身份/帧计数，确认“现在是什么”，再决定是否下 bit；
- **野 DMA 检查**：VDMA 是 PL 硬件，不随进程退出而停；先读 `DMACR.RS` / `DMASR.halted`；
- **缓存一致性**：`pynq.allocate()` 的缓冲写后要 flush、读前要 invalidate（`cma.py`）；
- **部署对账**：用内容 md5 而不是 mtime，逐文件核对“源 ↔ 板上”；
- **寄存器映射**：按名字访问位域、走 1 自检、sticky 完成位（`regmap.py`）；
- **失效保护**：不知道 PL 状态时返回“未知”，宁可停也不盲目下 bit。

这些条目的背后是本项目最贵的三次事故（野 DMA 覆写 CMA、盲目下 bit 导致总线挂死、
FAT 断电丢写），在任何 PYNQ 项目里都会遇到。

---

## 3. 每项技能的“四要素”

按赛方要求，每项都写清：

1. **适用场景**：什么题目、什么平台、什么阶段用；
2. **使用方法**：命令/步骤/需要替换的常量；
3. **失效条件**：什么情况下不能用，误用会怎样；
4. **已验证效果**：在本项目里跑出的具体数字与证据路径。

另外每项都写了 **来源失败**：它是从哪次事故/观察中总结出来的。
这比“最佳实践”四个字更有用——它告诉你为什么值得做。

---

## 4. 目录结构

```
skill/
├── README.md                     # 本页
├── pynq-safe-overlay/            # 通用 PYNQ：加载/野 DMA/缓存一致性/部署对账
│   ├── README.md
│   └── scripts/                  # vdma_state.py / deploy_check.py / cma.py / load_bit 参考实现
├── axi-regmap/                   # 通用 PYNQ：寄存器映射/走 1 自检/sticky 完成位
│   ├── README.md
│   └── scripts/                  # regmap.py
├── vivado-repro-gate/            # 可复现构建 + 时序门禁 + IP 基线 + bit 新鲜度
│   ├── README.md
│   └── scripts/                  # timing_gate.tcl / finish_build.py / check_ip_baseline.py
│                                 # / bit_freshness.py ...
├── golden-hdl-verify/            # 黄金模型逐位对拍
│   ├── README.md
│   ├── scripts/golden_compare.py
│   └── templates/tb_golden_template.v
├── llm-agent-workflow/           # 提示词工作流 / 智能体工作流
│   ├── README.md
│   └── prompts.md
├── pitfalls/                     # 结构化踩坑清单
│   └── README.md
└── case-templates/               # 报告/验证矩阵/提交检查模板
    ├── README.md
    ├── design_report_template.md
    ├── verification_matrix_template.md
    └── submission_checklist_template.md
```

---

## 5. 已知边界

- 这些技能来自**一个真实项目**，不是通用框架；换平台时需要替换寄存器地址、时钟、
  DMA 驱动与板卡初始化；
- `scripts/` 中的路径多为本项目相对路径，复制到新工程后先读一遍再运行；
- 所有“已验证效果”都是本项目的数据，不保证在别的板卡/版本上复现；
- 技能包不替代基础能力：时序约束、AXI 协议、DMA 一致性、定点数设计仍需自己掌握。
- 每项的“已验证效果”都给了原始证据在哪（`docs/reports/`、`report/evidence/`、各 `tb_*.v`），
  照着跑一遍就能自己判断值不值得抄。
