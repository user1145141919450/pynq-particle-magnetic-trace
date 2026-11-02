# -*- coding: utf-8 -*-
"""patch_lane_lookup.py —— 给"块查表"单独一拍，治最后 10 条违例路径。

实装时序报告（2026-09-28 第四轮）：
  10 条违例**全是同一形状**：源 = g_lane[N].x_reg/y_reg（位置寄存器），
  终点 = g_lane[N].mb[...]/D（**乘法器的 B 操作数**）。
  即"位置 -> 9 块比较器链 -> 查出的角度 -> 乘法器操作数"挤在一拍里。
  （多周期版把乘法器时分复用了，但查表仍是组合的，反而成了瓶颈。）

做法（语义不变：一次步进内位置不变，查表用的就是当前 x/y）：
  新增 OP_LOOK 微操作（3 拍，只在 sub0 干活）：把 ang_c 锁进 ang_r；
  之后 OP_ANG2 发操作数时用 **ang_r**（已寄存）而不是组合的 ang_c。
  于是"位置 -> 比较器 -> ang_r"自成一路（终点是寄存器），
  乘法器操作数那一段变成从 ang_r 出发的短路径。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'traj_lane.v'
s = io.open(p, encoding='utf-8').read()

# 1) 常量 + 声明
old = "                     P_COS2 = 4'd12;"
new = "                     P_COS2 = 4'd12, OP_LOOK = 4'd13;   // OP_LOOK：先把块查表结果寄存一拍"
assert old in s
s = s.replace(old, new, 1)

old = '    reg signed [22:0] ang_c;'
new = ('    reg signed [22:0] ang_c;\n'
       '    reg signed [22:0] ang_r;   // ★ 本步用的查表结果（OP_LOOK 里锁存；'
       '"位置->比较器->ang_r"单独一路，别再和乘法器操作数挤一拍）')
assert old in s
s = s.replace(old, new, 1)

# 2) 复位
old = "            m_dx <= 39'sd0; m_dy <= 39'sd0; nvx_c <= 20'sd0; nvy_c <= 20'sd0;"
new = ("            m_dx <= 39'sd0; m_dy <= 39'sd0; nvx_c <= 20'sd0; nvy_c <= 20'sd0;\n"
       "            ang_r <= 23'sd0;")
assert old in s
s = s.replace(old, new, 1)

# 3) 起点与"继续下一步"都先进 OP_LOOK
old = """                busy <= 1'b1; res_r <= 2'd0; pt_req <= 1'b0;
                op <= OP_ANG2; sub <= 2'd0;
            end
        end else begin"""
new = """                busy <= 1'b1; res_r <= 2'd0; pt_req <= 1'b0;
                op <= OP_LOOK; sub <= 2'd0;      // 先给查表一拍
            end
        end else begin"""
assert old in s
s = s.replace(old, new, 1)

old = """                            op <= OP_ANG2; sub <= 2'd0;
                        end
                    end
                end
            end else if (sub == 2'd2) begin"""
new = """                            op <= OP_LOOK; sub <= 2'd0;   // 下一步：先查表
                        end
                    end
                end
            end else if (sub == 2'd2) begin"""
assert old in s
s = s.replace(old, new, 1)

# 4) OP_LOOK：sub0 锁存查表结果，sub1/2 空转
old = """                if (sub == 2'd0) begin
                    case (op)
                    OP_ANG2: begin ma <= ang_c; mb <= ang_c; end"""
new = """                if (sub == 2'd0) begin
                    case (op)
                    OP_LOOK: begin ang_r <= ang_c; end        // ★ 查表结果寄存
                    OP_ANG2: begin ma <= ang_r; mb <= ang_r; end"""
assert old in s
s = s.replace(old, new, 1)

io.open(p, 'w', encoding='utf-8').write(s)
print('traj_lane.v 已改：新增 OP_LOOK（查表单独一拍），OP_ANG2 改用 ang_r')
