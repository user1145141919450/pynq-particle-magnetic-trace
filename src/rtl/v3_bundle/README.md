# V3「并行轨迹束」—— PL 侧的轨迹积分器

> 对应 `docs/vision_ip_design.md` 里程碑表的 **V3**：把 `game_pc/engine.py` 的
> `tangent_scan()`（源码注释原文："PC 端串行版；上板后由 PL 并行完成"）
> 用 N 路并行积分器一次算完 —— 一簇 17 条（入射角 ±8°、1° 步进）。

## 文件

| 文件 | 说明 |
|---|---|
| `traj_lane.v` | 单条轨迹积分器。**已通过逐位验证**（一拍一步版） |
| `traj_bundle.v` | 17 路 + 轨迹点仲裁（最低序号优先）+ 结果写出口 |
| `tb_traj_bundle.v` | 自检测试平台：读向量 → 跑 → **与黄金模型逐位比对** |
| `synth_v3.tcl` | OOC 综合（出资源/时序报告，不建工程、不动现有 BD） |
| `traj_lane_mc_wip.v` | 多周期瘦身版（**未通过**，见「下一步」） |
| `v3_util.rpt` / `v3_timing.rpt` | 综合报告 |

黄金模型在 `tools/v3_fixedpoint.py`，测试向量导出器是 `tools/v3_dump_tb.py`。

## 怎么重跑验证

在**工程根目录**（`E:\pynq-magnetic-field-demo`）跑：

```bash
# 1) 先生成/更新向量（可选，向量已在 sim/v3/ 下）
PYTHONPATH="E:/py_pkgs/vision" python tools/v3_dump_tb.py sim/v3

# 2) 编译 + 精化 + 跑（fan 场景；TB 默认读 sim/v3/fan）
V="F:/Xilinx2022/Vivado/2023.2/bin"
$V/xvlog.bat vivado/ov5640_pynq/rtl/v3_bundle/traj_lane.v \
              vivado/ov5640_pynq/rtl/v3_bundle/traj_bundle.v \
              vivado/ov5640_pynq/rtl/v3_bundle/tb_traj_bundle.v
$V/xelab.bat -debug typical tb_traj_bundle -s v3sim
$V/xsim.bat v3sim -R          # 注意：是 -R（跑到底），不是 -runall

# 3) goal 场景：xsim 的 -testplusarg 在 MSYS 环境里传不进去，
#    直接把 goal 的向量覆盖到 fan 目录再跑，跑完还原
cd sim/v3 && mkdir -p _bak && cp fan/*.mem _bak/ && cp goal/*.mem fan/
cd ../.. && $V/xsim.bat v3sim -R
cd sim/v3 && cp _bak/*.mem fan/ && rm -rf _bak
```

## 当前状态（2026-09-27 深夜，B 任务完成 ✅）

```
fan  场景（17 路 × 120 步）->  PASS：17 路结果与定点黄金模型逐位一致   3310 周期 = 33.1 µs @100MHz
goal 场景（17 路 × ~510 步）-> PASS：17 路结果与定点黄金模型逐位一致  13022 周期 = 130.2 µs @100MHz
```

**资源与时序（OOC 综合，100MHz 约束）—— 上板前的那道坎已经过了**：

| 资源 | 一拍一步版（旧） | **多周期版（现在）** |
|---|---|---|
| DSP48 | 170（**77%**）| **51（23%）** |
| LUT | 27,501（51.7%）| 22,570（42.4%）|
| FF | 4,291（4.0%）| 10,836（10.2%）|
| 时序 | **WNS 不收敛** | **MET（+0.54ns）** ✅ |

改法：`traj_lane.v`（= `traj_lane_mc.v`）把每路做成**一个乘法器 + 三拍微操作**
（发操作数 / 等 / 收），一步 9 个微操作 ≈ 33 拍。旧的一拍一步版留在
`traj_lane_1clock_verified.v`（也能跑，只是装不进去）。

**★ 这次重构踩到的三条"Verilog 时序陷阱"（都写进注释了）**：
1. **发操作数 -> 收乘积要隔两拍**：`ma<=A; mb<=B;` 下一个沿才进乘法器、乘积再下一个沿
   才落 `mreg`。按"+1 拍"收会每项都收错位置（实测 `m_vys` 里躺着 `vx*cos`、`m_dx=0`）。
2. **同一拍里别既赋值又读派生量**：`m_dy <= mreg` 与"用 m_dy 算 ny"放同一拍，读到的是
   上一步的值（症状：x 精确、y 系统性滞后 1.39px）。
3. **组合判定别直接驱动寄存器使能/数据**：边界+洞+轨迹点那一坨（21 级逻辑）直接驱动
   `vx` 的 CE 时 @100MHz 差 3.8ns；把判定结果**寄存一拍**再动作，立刻收敛
   （轨迹点判据那两个 19 位平方还要再单独占一拍，之前差 1.58ns）。

## 下一步

1. **AXI-Lite 封装 + 进 BD**（上板）：现在只有寄存器/BRAM 裸接口，要包一层 s_axi
   （或直接用 axi_bram_ctrl 读结果，参考 V1 的 map 回读套路）
2. **PS 侧对接**：`v3_scan.py` 里现在跑的是 Python 版扫描 —— 上板后换成"写参数 →
   启动 → 读结果 BRAM"，就能做"PS 串行 vs PL 并行"的实测比对了
3. NLANES 可按需要缩放（17 路是 ±8°；9 路 = ±4° 更省资源）
