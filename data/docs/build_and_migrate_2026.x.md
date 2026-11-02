# 源码复现与 Vivado 2026.x 迁移指南（2026-10-31 立）

> 目标：赛方要求“可复现的构建脚本 + 完整工程/源码”。本项目以 **Tcl + RTL + 约束 +
> IP 仓库**为唯一真源，`proj/`（Vivado 构建中间目录）不提交、可随时重建。
> 本文同时给出**提交 2023.2 版本**与**迁移到 2026.1/2026.2** 两条路径。

## 0. 结论先行

- **源码可独立复现**（2023.2 净室实测）：只拷源码、不含 `proj/`，`bd_only` 能建出完整
  BD；PS7 配置与现役工程 **878 个参数逐项一致**（唯一差异是 `preset` 元数据标记）。
- **净室全量构建已通过**（2026-10-31，Vivado 2023.2）：同一套源码在全新目录跑完
  综合+实现+门禁，**WNS +0.112 / WHS +0.006**，bit 正常生成（未改名）；
  证据：`data/reports/cleanroom_build_2026-10-31.txt`。
- **不需要 PYNQ-Z2 板卡文件**也能建：PS7 的 MIO 电压/外设全由
  `bd/ps7_pynq_z2_preset.tcl`（547 参数、3 轮应用）自带；`board_part` 会打 WARN，但不
  影响 PS7 配置。若要消掉 WARN，把板卡文件放进 `board_files` 并设置
  `set_param board.repoPaths`。
- **关键风险**：LUT **74.46%**、WNS **+0.085 ns**，余量很小。换 Vivado 版本重新综合/
  布图，**很可能需要重做时序收敛**（不是功能问题）。处置顺序见 §3.5。
- **提交 2023.2 是允许的**（赛方原话：“提交老版本也可以，但要作出说明”）；说明模板见 §4。

## 1. 源码树 = 唯一真源

提交/复现所需的全部内容：

```
build/vivado/ov5640_pynq/
  rtl/            全部 RTL（含 v3_bundle / map_detect / 粒子引擎 / 帧泵 / 画线）
  bd/m1a_capture.tcl        BD 构建脚本（锁 2023.2 IP 版本，逐字节复现用）
  bd/ps7_pynq_z2_preset.tcl PS7 547 参数（3 轮应用；PS7_STRICT 可硬停）
  constrs/ov5640_pynq.xdc   引脚/时钟约束
  ip_repo/         Digilent axi_dynclk 1.2 + rgb2dvi 1.4（工程自带，无外网依赖）
  build_project.tcl         从零建工程 + BD + 综合 + 实现 + 门禁 + deploy
  build_after_patch.tcl     改 RTL 后的重综合/重实现（清 cache/gen、reset 所有 run）
  timing_gate.tcl           WNS/WHS ≥ 0 门禁（违例 bit 改名 .TIMING_FAILED）
  archive_project.tcl       导出“可打开的完整工程”zip（给“完整工程文件”用）
src/software/board/             板上全部脚本（含 load_bit / start_demo / 台架）
src/software/pc/                推板/对账/部署工具
build/tools/                finish_build / check_ip_baseline / make_latest_ip_bd 等
data/docs/                  设计/事故/台本/本指南
data/reports/               构建/验收原始报告
```

**不提交**：`proj/`、`.Xil`、`.cache/.gen/.runs`、`xsim.dir`、`__pycache__`、
`*.wdb/jou/pb/log`、`.git`、旧备份。提交前跑：

```bash
python build/tools/check_submission_ready.py
```

## 2. 2023.2 复现步骤（提交版基线）

```bash
# 0) 环境：Vivado 2023.2（本机 F:/Xilinx/Vivado2023/Vivado/2023.2）
# 1) 从零构建（BD + 综合 + 实现 + bit + 门禁 + deploy）
cd build/vivado/ov5640_pynq
<vivado> -mode batch -source build_project.tcl
#    只验 BD（2~5 分钟）：... -tclargs bd_only
# 2) 门禁：日志里必须出现 "★时序门禁★ WNS=... WHS=..." 且均 ≥ 0
# 3) 构建后一键验收（IP 基线/deploy 自洽/地址常量）
cd ../.. && python build/tools/finish_build.py
# 4) 导出完整工程 zip（赛方“完整工程文件”）
cd build/vivado/ov5640_pynq
<vivado> -mode batch -source archive_project.tcl -tclargs <输出.zip>
```

**期望**：门禁通过（现役参考 WNS +0.085 / WHS +0.017）；资源
LUT 74.46% / FF 38.91% / BRAM 34.64% / DSP 9.55%。**bit 的 md5 不要求逐字节相同**
（布图随机），判据是门禁 + 上板功能台架（§3.6）。

改 RTL 后重建（**必须**走这条，避免 IPCACHE/OOC 静默复用旧逻辑）：

```bash
<vivado> -mode batch -source build_after_patch.tcl
```

## 3. 迁移到 2026.1 / 2026.2

> 两个版本流程相同；以赛方指定版本为准，并在提交说明里写清楚实际使用的版本。

### 3.1 先冻结 2023.2 基线
- 现役 bit `db96e197…` + `data/reports/` 两份原始报告 + 上板台架结果（§3.6）。
- 迁移失败时这是**可提交、可演示的保底**。

### 3.2 方案 A（推荐先试）：升级工程
1. 用 **2023.2** 先建好工程（§2，或直接用 `archive_project` 导出的 zip）。
2. 用 **2026.x** 打开该工程（会提示工程升级，选“自动升级”）。
3. `Report IP Status` → **Upgrade IP**（Xilinx IP 升到新版；Digilent 两个 IP 在
   `ip_repo` 里，版本锁 1.2/1.4，不动）。
4. 重新 `synth/impl`（`build_after_patch.tcl` 的流程），过 `timing_gate.tcl`。
5. 上板功能台架（§3.6）。

> 注意：升级后的 `proj/` **不要**当成源码提交；源码仍是 Tcl + RTL。

### 3.3 方案 B：纯源码 + 最新 IP（无旧工程）
```bash
python build/tools/make_latest_ip_bd.py     # 生成 bd/m1a_capture_latest.tcl（Xilinx IP 不锁版本）
cd build/vivado/ov5640_pynq
BD_TCL=m1a_capture_latest.tcl PS7_STRICT=1 \
  <vivado> -mode batch -source build_project.tcl -tclargs bd_only
```
- `PS7_STRICT=1`：PS7 参数有任何一项设不上就**硬停**并列出名字（新版 IP 增删参数时，
  这是发现差异的最快方式）。按列表更新 `bd/ps7_pynq_z2_preset.tcl`（删掉的去掉、
  改名的按新版手册补），再跑全流程。
- 局部代码里若 `ExtraPostPlacementOpt` 这类指令名在 2026.x 不存在，`build_project.tcl`
  的 `catch` 会吞掉，但脚本会打印“实现策略对账”的实际值 —— **看这行**。

### 3.4 方案 C：最坏情况
新版 IP 行为差异导致 BD 连接/时序异常 → 回到方案 A（2023.2 建工程、2026.x 升级），
或在新版里按 BD 脚本手工核对连接（`build_project.tcl` 的“无源引脚/采集链/时钟自检”
三道硬门禁会兜底）。

### 3.5 2026.x 时序过不了时的处置顺序（**不许放宽门禁**）
1. 确认实际生效指令：place=`ExtraPostPlacementOpt`、route=`NoTimingRelaxation`、
   post-route phys_opt=`Explore`（日志“实现策略对账”）。
2. 只重跑实现（沿用 synth dcp）再试一次 —— 布图随机性，换一次可能就过。
3. 看最差路径：
   - `pe_axi b_ang/b_x0/b_y0` 扇出 → 已有 `MAX_FANOUT=16`；
   - `vision_core bcnt→sumG` / `map_detect mask_buf` 布线 → **布图问题**，
     换实现指令/重布，别动功能 RTL；
   - 帧泵内部 → 看 OOC 报告（`ooc_frame_pump.tcl`）。
4. 仍不过：提交 **2023.2 版本 + 说明**（§4）。赛方允许，且这是已验证的基线。

### 3.6 任何新版本 bit 上板必跑的功能回归
| 项 | 命令/脚本 | 判据 |
|---|---|---|
| 链路/PL 身份 | `wp_health.py` + `pl_probe.py` | 15fps、errs=0、身份=ours |
| 下 bit | `load_bit.py`（唯一入口） | rc=0/2，复查 rc=0 |
| V3 束逐位 | `v3_shoot.sh --cam` | **17/17**（PL vs PS 逐位） |
| 粒子引擎 | PE 台架 | P1–P5 全过 |
| PL 地图检测 | `map_detect` 台架 | 8/8 同帧一致 |
| PL 画线 | `line_bench.py` | 3523 像素逐笔一致 |
| PL 帧泵 | `frame_pump_bench.py` | 两档 **0/921600**，~72ms/帧 |
| 游戏直播 | `game_v3.sh` | HDMI 正常、≥2.8fps、0 自愈 |

**⑥ 上板回归（2026-11-02 上午，板载实测）**
- `load_bit.py` rc=0（bit `f7eb6178` 上板、md5 边车对账通过、帧计数在动）
- `particle_pl.py probe` **P1–P6 全过**（含 16 粒容量：16 颗全存活、idx=16 被拒）
- 板上台架 **488/488，0 失败**
- `v3_shoot.sh --cam` **17/17 逐位一致**（有效帧 12；PL 6.36ms vs PS 1555.8ms ≈ 245×）
- 游戏直播正常（第 1 局、HDMI 推帧、VDMA 无错误位）
⇒ **2026.1 版本的 bit 可直接交付/演示**：与 2023.2 同一套源码，两个版本各自过时序门禁、
  上板功能一致（2023.2 板上 `67412907` 已验证，2026.1 板上 `f7eb6178` 已验证）。

### 3.7 2026.1 实测记录（2026-11-01，Vivado v2026.1）

> 环境：`F:/Xilinx/Vivado2026/2026.1/Vivado/bin/vivado.bat`；工程源码 = 最终版（16 粒）。
> 结论：**源码 + Tcl 在 2026.1 下可完整复现并过时序门禁**——`bd_only` 零 ERROR；
> 全量构建 **WNS +0.029 / WHS +0.017（TNS 0，零失败端点）**，
> bit md5 `f7eb6178016684685cf141b0dbf13b55`（4,045,773 B；
> LUT 79.89% / FF 39.77% / BRAM 34.64%）。净室日志
> `_cleanroom_2026_1/vivado/ov5640_pynq/_tmp_full2026.log` / `_tmp_impl_retry_2026b.log`。

**① 授权（Enterprise 安装）**
- 本机装的是 `Vivado ML Enterprise` → 没有 license 直接拒绝启动（`vivado -version` 能跑，
  真正开工程才报 "valid license not found"）。
- 免费 `Vivado_Basic_Package` license（AMD 产品授权页生成，绑定 MAC、到 2027.11）可用：
  `XILINXD_LICENSE_FILE=C:\Users\<user>\.Xilinx\Xilinx.lic`（或放默认目录）。
- 旧 license 不行：`Vivado_System_Edition`（feature 名旧）、`Vivado_ML_Enterprise_Edition
  2024.07`（版本上限/过期）。

**② PYNQ-Z2 板卡文件（2026.1 安装不带）**
- 从 XilinxBoardStore 拉：`boards/TUL/pynq-z2/A.0/{board.xml,part0_pins.xml,preset.xml}`
  （https://github.com/Xilinx/XilinxBoardStore）。
- 放到**无空格**目录（2026.1 对带空格 repoPaths 有已知 bug），例如
  `D:/PYNQ_boards/boards/tul.com.tw/pynq-z2/A.0/`。
- 用环境变量 `BOARD_REPO` 驱动（`build_project.tcl` 会 `set_param board.repoPaths`）。

**③ PS7 参数集变化（关键）**
- 新版 PS7 IP 把 MIO bank 电压拆成显式参数：`PCW_PRESET_BANK0_VOLTAGE` /
  `PCW_PRESET_BANK1_VOLTAGE`。PYNQ-Z2 的 **bank1 = 1.8V**（以太网），不设则
  MIO16-27 只接受 3.3V、ENET0 一串参数被当成 disabled 忽略。
- 旧的手动 547 参数复刻（`ps7_pynq_z2_preset.tcl`）在新版还有一批参数被拒
  （DDR `DQS_TO_CLK_DELAY` 负值、QSPI 单片选、USB0 reset、GPIO MIO 等）。
- ⇒ **有板卡文件时改走官方 `apply_board_preset`**（`m1a_capture.tcl` 里按 `BOARD_REPO`
  分支）；无板卡文件时仍走手动 preset（2023.2 行为不变）。
- `bd/ps7_pynq_z2_preset.tcl` 也加了 bank 电压前置设置（旧版 IP 无此参数 → catch 跳过），
  保证两条路都能用。

**④ BD API 差异**
- `get_bd_addr_segs -addressed -of_objects <space>` 在 2026.1 返回空（段其实已分配）；
  改用不带 `-addressed` 的查询，两版兼容。
- `make_latest_ip_bd.py` 生成的 `m1a_capture_latest.tcl`（Xilinx IP 不锁版本）是 2026.x 入口。

**⑤ 构建命令（2026.1）**
```bash
export XILINXD_LICENSE_FILE="C:/Users/<user>/.Xilinx/Xilinx.lic"
export BOARD_REPO="D:/PYNQ_boards/boards;D:/PYNQ_boards/board_files"
cd build/vivado/ov5640_pynq   # 建议在源码副本（净室）里跑，别动现役 2023.2 工程
BD_TCL=m1a_capture_latest.tcl PS7_STRICT=1 \
  <vivado2026.1> -mode batch -source build_project.tcl -tclargs bd_only   # 先探 BD
# 过了再跑全流程（去掉 -tclargs bd_only）
```
- 时序风险：LUT ~80%，**2026.1 下 PLACE 指令要选 `Explore`**（实测：
  `ExtraNetDelay_high` → WNS −0.150 / 12 端点；`Explore` → **+0.029 / 0 端点**）。
  2023.2 下则是 `ExtraNetDelay_high` 最好——两个版本的甜点不同，过门禁前两种都备着。
- ★ 2026.1 的 impl step 改名：`STEPS.POST_PLACE_PHYS_OPT_DESIGN` → **`STEPS.PHYS_OPT_DESIGN`**
  （旧脚本写 POST_PLACE_PHYS_OPT 会报 "does not have a property"）。实测把该 step 加
  Explore 对上面那版网表**无改善**（布局确定性，结果是 −0.150 不变）。
- 并行注意：**16GB 内存一次只跑一个 Vivado 构建**（两个并行在 impl 阶段 OOM）。

## 4. 提交 2023.2 版本的说明模板

> 本项目在 **Vivado 2023.2** 下完成综合/实现与全量上板验证。该版本下：
> 时序门禁 WNS +0.085 / WHS +0.017（0 失效端点）；资源 LUT 74.46%；
> 上板功能台架（V3 17/17、PE P1–P5、帧泵 0/921600、相机 15fps）全部通过。
> 源码已按 Tcl 真源组织，提供从零构建脚本（`build_project.tcl`）与完整工程归档；
> 2026.x 的迁移路径见 `docs/build_and_migrate_2026.x.md`（本版本 LUT/时序余量小，
> 迁移需重做时序收敛；若赛方要求以 2026.x 构建，按该文档的方案 A/B 执行并复测）。

## 5. 已踩过的“静默失效”坑（复现时必须知道）

1. **IPCACHE / OOC 子运行静默复用旧 dcp**：改 RTL 后必须用 `build_after_patch.tcl`
   （清 `.cache/.gen`、**reset 所有 run**），否则“时序过、台架绿、bit 里是旧逻辑”。
2. **从零重建 BD 可能丢 `pe_axi` / `map_detect`**：`build/tools/check_ip_baseline.py` +
   `ip_baseline.json` 逐 IP/逐地址对账（`finish_build.py` 自动跑）。
3. **Vivado 失败仍 exit=0**（如 IP definition not found 只报 ERROR）：脚本里已有
   硬门禁（IP 目录查询、无源引脚、采集链、时钟求解值、时序），**看门禁不看返回码**。
4. **board_part 警告**不是失败：PS7 由 preset 自带（净室已证）；要消警告就装板卡文件。
