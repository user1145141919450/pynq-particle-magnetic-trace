# src/ — 设计源码

- `rtl/`：Verilog RTL（相机链路、V3 轨迹束、粒子引擎、PL 检测、画线/帧泵）；
- `software/`：板上 Python 与 PC 工具源码；
- `game_pc/`：PC 物理引擎、渲染与等价性测试。

构建时使用 `build/vivado/ov5640_pynq/` 内的同一份 RTL。
