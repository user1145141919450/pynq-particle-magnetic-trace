# -*- coding: utf-8 -*-
"""patch_lane_lookup2.py —— 同 patch_lane_lookup 的目的，改成**按行定位**（不靠长字符串匹配）。

理由：上一版用长字符串做 replace，第二处断言就挂了（文件里的空格/隐藏字符对不上）。
按"行里含某关键字"定位稳得多。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'traj_lane.v'
lines = io.open(p, encoding='utf-8').read().split(chr(10))


def find(sub, start=0):
    for i in range(start, len(lines)):
        if sub in lines[i]:
            return i
    raise AssertionError('找不到: %s' % sub)


# 1) 常量表加 OP_LOOK
i = find("P_COS2 = 4'd12;")
lines[i] = lines[i].rstrip().rstrip(';') + ", OP_LOOK = 4'd13;   // 查表单独一拍"

# 2) 声明 ang_r
i = find('reg signed [22:0] ang_c')
lines.insert(i + 1, '    reg signed [22:0] ang_r;   // ★ 查表结果寄存（治"位置->比较器->乘法器"
                    '挤一拍：10 条违例全是这个形状）')

# 3) 复位
i = find('nvx_c <= 20\'sd0;')
lines.insert(i + 1, '            ang_r <= 23\'sd0;')

# 4) 起点：先进 OP_LOOK
i = find('op <= OP_ANG2; sub <= 2\'d0;')
lines[i] = lines[i].replace('OP_ANG2', 'OP_LOOK') + '   // 先给查表一拍'
# 下一处（judge 里的"继续"分支）也换
i2 = find('op <= OP_ANG2; sub <= 2\'d0;', i + 1)
lines[i2] = lines[i2].replace('OP_ANG2', 'OP_LOOK') + '   // 下一步：先查表'

# 5) OP_ANG2 的发操作数改用 ang_r；并加 OP_LOOK 的锁存分支
i = find('OP_ANG2: begin ma <= ang_c; mb <= ang_c; end')
indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
lines[i] = lines[i].replace('ma <= ang_c; mb <= ang_c;', 'ma <= ang_r; mb <= ang_r;')
lines.insert(i, indent + 'OP_LOOK: begin ang_r <= ang_c; end        // ★ 查表结果寄存')

io.open(p, 'w', encoding='utf-8').write(chr(10).join(lines))
print('按行补丁完成：OP_LOOK 已加，OP_ANG2 改用 ang_r')
for i, l in enumerate(lines):
    if 'OP_LOOK' in l or 'ang_r' in l:
        print('  %4d: %s' % (i + 1, l.strip()[:80]))
