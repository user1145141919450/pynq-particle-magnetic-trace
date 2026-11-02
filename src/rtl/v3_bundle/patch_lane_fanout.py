# -*- coding: utf-8 -*-
"""patch_lane_fanout.py —— 治 V3 的时序高扇出：把每路用到的"全局常数"在 lane 内部寄存一份。

为什么（2026-09-28，实装时序报告的失败路径）：
  关键路径 = `r_fw_reg -> g_lane[12].goal_r_reg`，**布线 6.42ns / 逻辑 4.46ns**。
  r_fw/r_ballr/r_fh/r_steps 每个都要扇出到 **17 路**的边界比较器，走线又长又挤。
  加 `MAX_FANOUT` + phys_opt 后 WNS 只从 -1.028 改到 -0.471ns —— 还不够。

做法（语义**完全不变**：这些参数在一次扫描期间是常量，PS 是"先写参数、后写 start"）：
  每路内部寄存 br_r / fw_r / fh_r / ms_r 四个值，比较器只读**本地寄存器** ->
  跨 lane 的长走线消失，只剩本地的短连线。
  然后**必须重跑 AXI 级 TB**：判据是逐位一致，语义若被改坏会立刻被抓出来。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'traj_lane.v'
s = io.open(p, encoding='utf-8').read()

# 1) 声明本地常数寄存器
old_decl = '    reg [17:0] nx_r, ny_r;'
new_decl = '''    reg [17:0] nx_r, ny_r;
    // ★★ 每路本地寄存的"全局常数"（治高扇出长走线，2026-09-28）：
    //    这些参数一次扫描期间不变，PS 是先写参数再写 start，所以寄存一份语义不变；
    //    但比较器只读本地寄存器后，r_fw/r_ballr/r_fh/r_steps 到 17 路的长走线就没了
    //    （实测关键路径 r_fw_reg -> g_lane[12].goal_r_reg，布线占 6.42ns）。
    reg [17:0] br_r, fw_r, fh_r;
    reg [11:0] ms_r;'''
assert old_decl in s
s = s.replace(old_decl, new_decl, 1)

# 2) 复位初值
old_rst = "            pt_flag_r <= 1'b0; wall_r <= 1'b0; goal_r <= 1'b0;"
new_rst = ("            pt_flag_r <= 1'b0; wall_r <= 1'b0; goal_r <= 1'b0;\n"
           "            br_r <= 18'd0; fw_r <= 18'd0; fh_r <= 18'd0; ms_r <= 12'd0;")
assert old_rst in s
s = s.replace(old_rst, new_rst, 1)

# 3) 每拍更新（输入在扫描期间稳定；写 start 之前就已经写好了参数）
old_start = """            if (start) begin
                x <= x0; y <= y0; vx <= vx0; vy <= vy0;"""
new_start = """            // 本地常数寄存器：每拍跟随输入（扫描期间输入不变）
            br_r <= ball_r;
            fw_r <= field_w - ball_r;
            fh_r <= field_h - ball_r;
            ms_r <= max_steps;
            if (start) begin
                x <= x0; y <= y0; vx <= vx0; vy <= vy0;"""
assert old_start in s
s = s.replace(old_start, new_start, 1)

# 4) 比较器改用本地寄存器
reps = [
    ('if (nx_r < ball_r) begin',        'if (nx_r < br_r) begin'),
    ('tx_c = ball_r;',                  'tx_c = br_r;'),
    ('end else if (nx_r > field_w - ball_r) begin', 'end else if (nx_r > fw_r) begin'),
    ('tx_c = field_w - ball_r;',        'tx_c = fw_r;'),
    ('if (ny_r < ball_r) begin',        'if (ny_r < br_r) begin'),
    ('ty_c = ball_r;',                  'ty_c = br_r;'),
    ('end else if (ny_r > field_h - ball_r) begin', 'end else if (ny_r > fh_r) begin'),
    ('ty_c = field_h - ball_r;',        'ty_c = fh_r;'),
    ('if (step_i >= max_steps) begin',  'if (step_i >= ms_r) begin'),
]
n = 0
for old, new in reps:
    if old in s:
        s = s.replace(old, new, 1)
        n += 1
    else:
        print('跳过（找不到）: %s' % old[:50])

io.open(p, 'w', encoding='utf-8').write(s)
print('traj_lane.v 已改：声明+复位+更新+%d 处比较器' % n)
