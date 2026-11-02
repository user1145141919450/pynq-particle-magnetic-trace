# pynq-safe-overlay —— 通用 PYNQ Overlay 安全加载与部署对账

> **类型**：通用 PYNQ Skill（不与本队题目绑定）
> **适用场景 / 平台**：PYNQ-Z2 / Zynq-7000 / Zynq UltraScale+，带 PL overlay、VDMA 或自定义 DMA 的项目
> **来源**：本项目 2026-09~10 的三次真实事故——野 DMA 覆写 CMA、盲目下 bit 导致总线挂死、
> FAT 分区断电丢写。

**复制后必改（每个脚本 3 行）**

| 脚本 | 必改 1 | 必改 2 | 必改 3 |
|---|---|---|---|
| `load_bit_reference.py` | 板端目录 `/home/xilinx/wp` | bit 文件名 `ov5640_m1a_wrapper.bit` | 身份探针 `pl_probe.py` |
| `deploy_check.py` | 部署目录 `vivado/ov5640_pynq/deploy` | `SOURCES` 待部署文件清单 | `ADDR_EXPECT` 地址常量 |

---

## 1. 解决什么问题

PYNQ 项目里，**“进程退出”不等于“硬件停止”**：

- VDMA 是 PL 硬件，脚本崩溃/被 SIGKILL 时不会执行 `atexit`，DMA 会带着**已释放的**
  缓冲地址继续跑；下一个进程分配到同一页，就被覆盖。典型指纹：
  相邻帧一致性 10%、帧率异常、`errs=0x08`、帧索引只有 1。
- 盲目下 bit：PL 里可能正在跑别的流水线（或上次半编程），直接下会导致
  PCFG_INIT 超时 → PL 半编程 → MMIO 挂死总线 → 只能断电。
- 部署漂移：只比 mtime/大小会漏判（NTFS 延迟更新、FAT 2 秒粒度），
  “命令没报错”不代表板上文件是新的。

本技能把这三件事变成**可执行的检查**，而不是靠记性。

---

## 2. 使用方法

### 2.1 下 bit 之前：先问 PL 里是什么

在你的项目里提供一个 `pl_probe` 类探针（读一个只有你的流水线会递增的计数器/ID 寄存器）：

```text
rc=0  -> 我们的流水线在跑（不要下 bit，除非明确要替换）
rc=1  -> 不是我们的（可以下）
rc=9  -> 说不清（不要下）
```

**纪律**：下 bit 只允许通过一个 gated 脚本（参考 `scripts/load_bit_reference.py`），
它内部先 probe、再校验 bit md5、再下、下完再 probe。不要直接 `fpga_manager` 写文件。

### 2.2 起采集/显示之前：查野 DMA

```bash
python3 vdma_state.py          # 只看：DMACR.RS / DMASR.halted / 错误位
python3 vdma_state.py --halt   # 有野 DMA 时先停（只停不重置）
```

判据：

| 状态 | 含义 | 动作 |
|---|---|---|
| `RS=0` 或 `halted=1` | 通道没在跑 | 可以 allocate / load |
| `RS=1` 且 `halted=0` | 有人在跑 | 确认是不是你的客户端；不是就先 `--halt` |
| 读不到寄存器 | 说不清 | 当“未知”，不要硬来 |

> 注意：如果你的游戏/采集进程正持有 VDMA，`RS=1` 是正常的，不要误杀；
> 判据是“**你有没有客户端在跑**”，不是“RS 是否为 1”。

### 2.3 部署之后：逐文件 md5 对账

```bash
python deploy_check.py          # 只检查
python deploy_check.py --fix    # 缺的/过期的拷过去
python deploy_check.py --quiet  # 只在有问题时输出
```

- 判据用**内容 md5**，不用 mtime；
- 根目录默认从脚本位置推导（`DEMO_ROOT` 可覆盖），换机器/解压到别处都能用；
- 退出码：0=完整新鲜；1=缺文件/过期；2=路径不对。

### 2.4 取数据之前：缓存一致性

停掉野 DMA 只解决“谁在写”，另一半是“**CPU 看到的是不是 DDR 里的那份**”。
PL 走 AXI 直接读写 DDR，**不经过 CPU cache**；而 `pynq.allocate()` 默认给的是
**cacheable** 缓冲：

| 方向 | 少做一步 | 症状 |
|---|---|---|
| CPU 写 → PL 读 | 没 `flush()` | PL 读到旧 DDR 内容（“我明明写进去了”） |
| PL 写 → CPU 读 | 没 `invalidate()` | CPU 读到自己 cache 里的旧值（“硬件没输出”） |

症状极具欺骗性：小数据“有时对有时不对”、连读两次结果不同、换块缓冲就好、
改 `cacheable=False` 又变慢 —— 很像硬件不稳定，其实少一次 flush。

`scripts/cma.py` 把这件事变成**忘不掉**的接口：

```python
from cma import PlBuffer

with PlBuffer((720, 1024), 'u8') as buf:   # 退出自动 close
    buf.fill(frame)          # 写数据，内部已 flush
    start_transfer(buf)
    wait_done()
    out = buf.snapshot()     # 读结果，内部已 invalidate，返回副本
```

本队生产采集路径的做法与此一致（`vdma_capture.py` 在返回每帧缓冲前
`bufs[idx].invalidate()`，注释原文“PL 直写 DDR，绕过 CPU 缓存”）。

### 2.5 绝对不要做的事

- 不要同时跑两个 VDMA 客户端（实时预览与抓帧互斥）；
- 不要在 FAT 分区（`/boot`）跑脚本或依赖它保存状态（断电丢写）；
- 不要用 `sudo` 成功来验证口令（缓存 ticket 会续期）；
- 不要在“不知道 PL 状态”时下 bit。

---

## 3. 脚本说明

| 文件 | 用途 | 需要改什么 |
|---|---|---|
| `scripts/vdma_state.py` | 读 VDMA S2MM 状态，`--halt` 停通道 | `VDMA_BASE`、寄存器常量从你的 VDMA 驱动导入 |
| `scripts/deploy_check.py` | 源 ↔ 部署包 md5 对账 | `ROOT`、`SOURCES`/`ADDR_EXPECT` 清单 |
| `scripts/load_bit_reference.py` | gated 下 bit 参考实现（probe → md5 → load → probe） | bit 文件名、md5 sidecar、`pl_probe` 路径 |
| `scripts/cma.py` | CMA 缓冲的 flush / invalidate 封装（`PlBuffer`） | 无（不依赖 PYNQ 常量） |

> 这三个脚本在本项目里是生产脚本；复制到新项目时，先把常量/路径改成你的，
> 再跑一次“只读模式”（不带 `--fix` / `--halt`）确认判断正确。

---

## 4. 失效条件

| 条件 | 后果 | 替代做法 |
|---|---|---|
| PL 没有可读身份/帧计数探针 | `pl_probe` 永远 rc=9 | 至少做一个只读 ID 寄存器；否则只能靠“断电冷启动” |
| 没有 `/dev/mem` 权限 / 用了 XRT | `fastmem` 映射失败 | 用 PYNQ `MMIO`/`pynq.Overlay`，但性能会下降 |
| 多个 DMA 客户端并发 | 互相覆写缓冲 | 加文件锁/状态文件；预览与抓帧互斥 |
| 板卡不是 PYNQ 镜像 | venv/路径不同 | 改 `PYTHONPATH` 与设备树 |
| 用 mtime 判断新旧 | 偶发漏判 | 一律用内容 md5 / SHA256 |
| 用 `/dev/mem` mmap 直接访问（非缓存映射） | flush/invalidate 是空操作 | 可以不套 `PlBuffer`；但 `pynq.allocate()` 的缓冲仍需 |

---

## 5. 已验证效果（本项目）

- 相机链路 **约15 fps（实测14.7~15）、scl 0 错**（2026-10-30 旧 bit `949911fb`；现役 16 粒 bit `67412907`）；多轮上板/断电/重下 bit 后仍稳定；
- 野 DMA 指纹（10% 一致性、`errs=0x08`）多次被 `vdma_state.py` 在起采集前发现并 halt；
- `deploy_check.py` 维护 **44 条源文件 + 9 条地址常量**（`SOURCES` / `ADDR_EXPECT`），
  多次“44 项全一致、可上板”；
- `link_boot_selftest.py` 把七类链路故障做成台架：**PC 489/489**、**板上 488/488**
  （两侧项数相差 1，各自 0 失败；口径以各自的原始日志为准）；
- **16 粒 bit `67412907` 上板**（2026-11-01）：`load_bit.py` rc=0，`particle_pl.py probe`
  **P1–P6 全过**——P6 实测 16 颗全存活、`idx=16` 被越界防护拒；板上台架 488/488；
  部署包 5 文件 md5 前后对账全一致；
- `cma.py` 的调用顺序在 PC 上自检 **5/5**；同一套 flush/invalidate 用法已在生产采集
  路径 `vdma_capture.py` 上板长期使用（通用封装本身未单独上板，见下）；
- 证据：`docs/reports/onboard_acceptance_2026-10-31.txt`、`docs/reports/pc_selftest_2026-10-31.txt`；
  16 粒那一组的上板结果来自项目 2026-11-01 的交接记录（板级原始日志未随提交包，
  口径见 `report/design_report.md` 的说明）。

---

## 6. 来源失败（为什么值得做）

1. **2026-09-26 野 DMA**：脚本段错误退出，VDMA 继续跑，下一进程的 CMA 被覆盖；
   现象是“相邻帧一致性 10%、帧率 3 fps、errs=0x08”，排查花了多轮。
2. **2026-10 盲目下 bit**：PL 里还有半编程状态，直接下载导致 PCFG_INIT 超时 →
   MMIO 挂死 → 只能断电。此后规定“下 bit 只允许 load_bit.py，先 pl_probe”。
3. **部署漂移**：deploy 清单手写过期，`.sh` 全缺却无人报错；改用扫目录 + md5 对账。
4. **mtime 坑**：NTFS 延迟更新目录项、FAT 粒度 2 秒 → 偶发漏判；
   此后所有“是否最新”的判据都改成内容 md5。
5. **缓存一致性（观察，非事故）**：本队采集路径一直用 `pynq.allocate()` 的 CMA 缓冲，
   读帧前必须 `invalidate()`（`vdma_capture.py` 里那句注释“PL 直写 DDR，绕过 CPU 缓存”）。
   我们没有把它踩成事故，但它是“读出来是旧数据”这类问题的唯一解释；
   通用封装见 `scripts/cma.py`。

---

## 7. 迁移到别的题目

1. 复制本目录到新项目；
2. 把 `pl_probe` 换成你的“身份寄存器”（哪怕只是一个常量 ID）；
3. 把 `vdma_state.py` 的基地址/寄存器换成你的 DMA；
4. 把 `deploy_check.py` 的清单换成你的板上文件；
5. 先跑只读模式，确认判断与实际一致，再接入自动化流程。

这套方法对“图像采集 + 显示 + 自定义加速器”的 PYNQ 项目几乎零改动可用。
