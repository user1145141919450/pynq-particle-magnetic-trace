# vivado-repro-gate —— 可复现构建 + 时序门禁 + IP 基线

> **类型**：校验脚本 + 构建模板
> **适用场景**：使用 Vivado、以 Tcl + RTL + 约束 + IP 仓库为源码真源的工程（尤其带 BD 的 Zynq 设计）
> **来源**：本项目两次“看起来构建成功、实际 bit 里是旧逻辑/丢了 IP”的事故，
> 以及 2026-10-31 的净室全量构建实践。

**复制后必改（`bit_freshness.py`，3 行）**

1. `--built build/impl_1/top.bit` → 你的构建产物路径
2. `--deployed deploy/top.bit` → 你的部署 bit 路径
3. `<bit>.md5` 边车 → 跟你的 bit 一起生成 / 拷贝

---

## 1. 解决什么问题

Vivado 的“成功”有很多种假象：

1. **IPCACHE / OOC 子运行静默复用旧 dcp**：改了 RTL，父级 relaunch 不会重跑子运行；
   日志只留一行 “Using cached IP synthesis design”。症状是**时序过、台架绿、能下板，
   但新 bit 里那块逻辑还是旧的**。
2. **从零重建丢 IP**：BD 脚本漏了某两个 IP，构建 exit=0，`.hwh` 里却没有它们；
   游戏某项功能直接哑掉。
3. **失败仍 exit=0**：IP definition not found、无源引脚、时钟没解出来，Vivado 只打 ERROR
   不返回非零；只看返回码会把失败当成功。
4. **时序违例的 bit 被误下板**：WNS/WHS 为负，功能可能“大部分正常”，现场偶发死机。

本技能把“源码可复现”与“产物可对账”固化成脚本与门禁。

---

## 2. 使用方法

### 2.1 源码树 = 唯一真源

提交/复现只依赖：

```text
vivado/<project>/
  rtl/                  全部 RTL（含 testbench）
  bd/<bd>.tcl           BD 构建脚本（可锁 IP 版本）
  bd/ps7_*_preset.tcl   PS 配置（如适用；逐参数应用）
  constrs/*.xdc         引脚/时钟约束
  ip_repo/              工程自带 IP（版本锁死，无外网依赖）
  build_project.tcl     从零：建工程 + BD + 综合 + 实现 + 门禁 + deploy
  build_after_patch.tcl 改 RTL 后：清 cache/gen、reset 所有 run、重综合/实现
  timing_gate.tcl       WNS/WHS >= 0 门禁（违例 bit 改名 .TIMING_FAILED）
  archive_project.tcl   导出可打开的完整工程 zip
```

`proj/`、`.cache`、`.gen`、`.runs`、`xsim.dir`、`*.wdb` 都不提交，可由脚本重建。

### 2.2 从零构建

```bash
cd vivado/<project>
<vivado> -mode batch -source build_project.tcl
# 先探路（2~5 分钟）：
<vivado> -mode batch -source build_project.tcl -tclargs bd_only
```

### 2.3 改 RTL 后重建（必须走这条）

```bash
<vivado> -mode batch -source build_after_patch.tcl
```

它做四件事：
1. 删除 `proj/<name>.cache` 与 `.gen`（防 IPCACHE 静默复用）；
2. `reset_run` **所有** run（父级 + 每个 OOC 子运行）；
3. 重新综合 + 实现；
4. `source timing_gate.tcl`，违例则把 bit 改名 `.TIMING_FAILED` 并 `exit 1`。

> 只 reset 父级是不够的：BD 里的 module_ref 有独立 OOC 子运行，
> 改过它们的 RTL 后父级 relaunch 不会自动重跑。

### 2.4 构建后一键验收

```bash
python tools/finish_build.py          # 检查 + 更新 deploy
python tools/finish_build.py --check  # 只检查
```

它检查：
- 时序门禁产物在不在（`.TIMING_FAILED` 就是没过）；
- 新 `.hwh` 与 `ip_baseline.json` **逐 IP / 逐地址**对账；
- bit/hwh 拷进 deploy；
- `deploy_check.py --fix`（源 ↔ deploy 一致、地址常量对 hwh）。

### 2.5 2026.x 迁移

```bash
python tools/make_latest_ip_bd.py     # 生成不锁 IP 版本的 BD
cd vivado/<project>
BD_TCL=m1a_capture_latest.tcl PS7_STRICT=1 \
  <vivado> -mode batch -source build_project.tcl -tclargs bd_only
```

`PS7_STRICT=1` 会在 PS7 参数设不上时硬停并列出名字，是最快发现版本差异的方式。
时序不过时的处置顺序：确认实现策略 → 只重跑实现（换布图）→ 看最差路径 →
仍不过就提交已验证的旧版本 + 说明（**不放宽门禁**）。

### 2.6 提交前自检

```bash
python tools/check_submission_ready.py
```

检查必需文件、重物/缓存目录、可执行代码里的本机绝对路径、疑似凭据。
`CLAUDE.md` / `HANDOFF.md` 属**过程文档**（`OPTIONAL_DOCS`）：工作副本里有就报 OK，
提交包里没有只报 `[SKIP]`、不算错。

### 2.7 出 bit 之后：三方 md5 对账

```bash
python scripts/bit_freshness.py --built <impl_1>/top.bit --deployed deploy/top.bit
python scripts/bit_freshness.py --built a.bit --deployed b.bit --stage   # 顺便重写边车
```

判据只有一个：**impl_1 产物 == 部署副本 == 边车 `<name>.bit.md5` 里记的那串**。
三方任一不一致就退出码 1（别上板），缺文件返回 2。**不用 mtime**。

> 真实现场：deploy 目录里同时躺着 `top.bit.md5`（新）与 `top_949911fb.bit.md5`（旧）。
> 只要有人手滑复制了旧边车，`load_bit.py` 就会拿旧 md5 去校验新 bit —— 当场报不一致
> 还算是好的，坏的是反过来：新边车配旧 bit，校验通过、下进去的是旧逻辑。
> 每次出 bit 后跑一次三方对账，这类“文件名对、内容不对”的坑就没了。
>
> **本项目已按这条处理**：旧边车 `ov5640_m1a_wrapper_949911fb.bit.md5` 已从
> `vivado/ov5640_pynq/deploy/` 移出，现在部署包里只剩当前 bit 的那一个边车
> （`ov5640_m1a_wrapper.bit.md5`，内容为 `674129074bf711d273f30f2ee74aa449`）。
> 判据很简单：**部署包里有且只有一个 `.md5`，且它与 bit 的实际 md5 一致**。

---

## 3. 脚本说明

| 文件 | 用途 | 需要改什么 |
|---|---|---|
| `scripts/timing_gate.tcl` | WNS/WHS 门禁；违例 bit 改名 | 工程名/impl 目录 |
| `scripts/finish_build.py` | 时序 + IP 基线 + deploy 一键验收 | `ROOT`、`PJ`、`IMPL`、`HWH`、`DEPLOY` |
| `scripts/check_ip_baseline.py` | `.hwh` 逐 IP/地址对账 | `ip_baseline.json` 路径 |
| `scripts/make_latest_ip_bd.py` | 生成不锁版本的 BD | BD 模板、IP 列表 |
| `scripts/check_submission_ready.py` | 提交前文件/路径/凭据扫描 | `REQUIRED`、`OPTIONAL_DOCS`、`FORBID_DIRS`、`AUX_PREFIXES` |
| `scripts/bit_freshness.py` | impl / deploy / 边车 三方 md5 对账 | 无（路径从命令行给） |

---

## 4. 失效条件

| 条件 | 后果 | 替代做法 |
|---|---|---|
| 工程依赖 GUI 手工状态 | Tcl 重建不完整 | 把每一步写进 Tcl；用 `report_property` 对账 |
| 没有 BD / 纯 RTL | 本技能的 BD 部分不适用 | 只保留 build + timing gate |
| IP 不在仓库、依赖外部版本 | 换机器建不出 | 把 IP 放进 `ip_repo/` 并锁版本 |
| 没有 `ip_baseline.json` | 丢 IP 检查失效 | 首次构建后生成基线，之后每次对账 |
| 用了 `board_part` 但没装板卡文件 | 有 WARN | WARN 不是失败；PS7 可由 preset 自带 |

---

## 5. 已验证效果（本项目）

- **净室全量构建**：只拷源码、无 `proj/`、无板卡文件，Vivado 2023.2 跑完
  综合+实现+门禁，**WNS +0.112 / WHS +0.006**，bit 正常生成；
- **发现丢 IP**：10-30 从零重建后 `.hwh` 少了 `pe_axi_0` / `map_detect_axi_0`，
  且删掉的 `ila_0` 复活；`finish_build.py` 对账把它拦住；
- **拦住时序违例**：首次帧泵构建 WNS −0.049，bit 被改名 `.TIMING_FAILED`；
  换布图后 +0.085 才允许下板；
- **2026-11-01 16 粒重建（资源顶到边、布图拥塞）**：`NPART` 8→16 后首次全量构建
  **WNS −1.035 / WHS +0.010、1287 个失败端点**，LUT 74.5%→**79.6%**；最差路径
  **布线占 9.03 ns 而逻辑只有 1.7 ns** —— 是布图被挤爆，不是 16 粒逻辑本身。
  门禁拦下后**只重跑实现**（复用 synth dcp）、place 换 `ExtraNetDelay_high`
  （route `NoTimingRelaxation` + post-route phys_opt `Explore`）→
  **WNS +0.094 / WHS +0.013 / TNS 0.000（零失败端点）**；
  bit md5 `67412907`，`finish_build.py` 全过（**19 个 IP 基线地址全同、deploy 44 项一致**），
  当晚 `load_bit.py` 下板 rc=0、探针 P1–P6 全过；
- **2026.x 迁移**：`make_latest_ip_bd.py` + `PS7_STRICT=1` 的探路流程已写成文档
  （`docs/build_and_migrate_2026.x.md`）；**2026.1 已实测全量构建 WNS +0.029 / WHS +0.017**
  （bit `f7eb6178`），并上板 P1–P6、488/488、v3 17/17；
- 证据：`docs/reports/cleanroom_build_2026-10-31.txt`、
  `docs/reports/*timing_summary_postroute_physopted.rpt`、
  `docs/reports/*utilization_placed.rpt`。

---

## 6. 来源失败（为什么值得做）

1. **2026-10-04 IPCACHE 事故**：改了 `max_bounce` 的 RTL，构建“成功”、时序/台架全绿、
   bit md5 也变了，但上板逐位 17/17 全不符——新 bit 里还是旧逻辑。
   根因是 OOC 子运行复用了旧 dcp；此后强制清 `.cache/.gen` + reset 所有 run。
2. **2026-10-30 丢 IP 事故**：从零重建后 BD 少了两个 IP、复活了一个 ILA；构建 exit=0，
   台架也没覆盖“IP 是否存在”。用户问“没影响其它功能吧”才发现；此后固化 `finish_build.py`。
3. **2026-10-31 时序门禁**：帧泵首次构建 WNS −0.049，门禁把 bit 改名；
   只重跑实现换布图后 +0.085。若没有门禁，这个 bit 会被下板并偶发死机。
4. **2026-11-01 加逻辑后的时序悬崖**：16 粒让 LUT 从 74.5% 涨到 79.6%，
   同一份 RTL、同样的策略，WNS 从 +0.085 掉到 **−1.035**、1287 个端点失败。
   教训：**资源贴近 80% 后，时序不再是“逻辑长度”问题而是“布图拥塞”问题**——
   看最差路径的布线占比（9.03 ns vs 逻辑 1.7 ns）就能分清该改逻辑还是该换布图。

---

## 7. 迁移到别的题目

1. 复制 `scripts/` 到你的工程，改工程名/路径；
2. 把你的 BD 构建、约束、IP 仓库整理成 Tcl 真源；
3. 首次成功构建后生成 `ip_baseline.json`；
4. 把 `build_after_patch.tcl` 作为“改 RTL 后唯一入口”；
5. 在 CI 或本地提交前跑 `timing_gate.tcl` + `finish_build.py --check`；
6. 迁移新版本 Vivado 时，先用 `bd_only` + `PS7_STRICT=1` 探路，再全量构建。

这套流程对任何“BD + 自定义 RTL + 需要上板”的 Vivado 工程都适用。
