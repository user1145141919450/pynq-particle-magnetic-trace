# -*- coding: utf-8 -*-
"""patch_lane_pt.py —— 把"轨迹点判据"的平方运算拆到单独一拍，治最后一条违例路径。

实装时序报告（2026-09-28 第三轮）：
  关键路径 = g_lane[0].u_lane/**p_1_out（DSP48 乘法输出）** -> **pt_flag_r_reg/D**
  数据路径 10.217ns（逻辑 6.658 + 布线 3.559）
  —— 即"ddx²+ddy² 与比较"全挤在 op8 的同一拍：两个乘法器（综合成 DSP）组合输出
  还要过加法器和比较器。拆成两拍即可：先把两个平方**寄存**，下一拍再加+比较。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'traj_lane.v'
s = io.open(p, encoding='utf-8').read()

# 1) 声明平方寄存器
old = '    reg signed [18:0] ddx_r, ddy_r;'
new = ('    reg signed [18:0] ddx_r, ddy_r;\n'
       '    reg signed [37:0] sx_r, sy_r;      // ddx²/ddy²（各自寄存，别再和加法器挤一拍）')
assert old in s
s = s.replace(old, new, 1)

# 2) 复位
old = "            ddx_r <= 19'sd0; ddy_r <= 19'sd0;"
new = ("            ddx_r <= 19'sd0; ddy_r <= 19'sd0;\n"
       "            sx_r <= 38'sd0; sy_r <= 38'sd0;")
assert old in s
s = s.replace(old, new, 1)

# 3) op8 从 3 拍扩到 4 拍：sub=1 只算平方，sub=2 才加+比较，sub=3 才动作
old = """                end else if (sub == 2'd1) begin
                    // 第 1 拍专门算"位移平方 > (8px)²"（两个 19 位平方 + 加 + 比较，
                    // 挤在一拍仍差 1.58ns，所以单独占一拍）
                    pt_flag_r <= ((ddx_r * ddx_r + ddy_r * ddy_r) > 42'sd4194304);
                    sub <= 2'd2;
                end else begin"""
new = """                end else if (sub == 2'd1) begin
                    // 第 1 拍：只算两个平方并**寄存**（乘法器综合成 DSP，组合输出很长：
                    // 实装路径 p_1_out -> pt_flag_r_reg 逻辑 6.66ns）
                    sx_r <= ddx_r * ddx_r;
                    sy_r <= ddy_r * ddy_r;
                    sub <= 2'd2;
                end else if (sub == 2'd2) begin
                    // 第 2 拍：只剩"加 + 比较"，短得多
                    pt_flag_r <= ((sx_r + sy_r) > 42'sd4194304);
                    sub <= 2'd3;
                end else begin"""
assert old in s
s = s.replace(old, new, 1)

io.open(p, 'w', encoding='utf-8').write(s)
print('traj_lane.v 已改：轨迹点判据拆成两拍（op8 三拍 -> 四拍）')
