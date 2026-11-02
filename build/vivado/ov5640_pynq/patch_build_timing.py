# -*- coding: utf-8 -*-
"""patch_build_timing.py —— 给 build_project.tcl 的实现阶段加时序收敛手段。

背景（2026-09-28）：
  加进 V3 后整体实现 **WNS=-1.028ns**（关键路径 r_fw_reg -> g_lane[12].goal_r_reg，
  布线 6.42ns / 逻辑 4.46ns —— 高扇出长线为主），项目脚本很正确地**拒绝产出 bit**
  （"时序不收敛，拒绝产出 bit"）。V3 单独 OOC 综合是 +0.54ns，进设计布线后不够了。

两手一起上：
  1) RTL 侧已加 `(* MAX_FANOUT = 16 *)`（让工具复制寄存器缩短走线）
  2) 流程侧：开 **post-route phys_opt**（布线后再做复制/重定时）+
     选 Performance_ExtraTimingOpt 策略（更激进的时序优化，代价是编译更久）
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'build_project.tcl'
s = io.open(p, encoding='utf-8').read()

anchor = 'launch_runs impl_1 -to_step write_bitstream -jobs 4'
add = '''# ★ 时序收敛手段（2026-09-28 加 V3 后 WNS=-1.028ns 才补上的）：
#   · Performance_ExtraTimingOpt：更激进地优化时序（编译时间会长一些）
#   · 开 post-route phys_opt：布线后再做**扇出复制/重定时** —— 我们的失败路径
#     正是 r_fw_reg 扇出到 17 路的长走线（布线 6.42ns vs 逻辑 4.46ns）
set impl_run [get_runs impl_1]
catch { set_property strategy Performance_ExtraTimingOpt $impl_run }
catch { set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true $impl_run }
catch { set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE Explore $impl_run }
puts "INFO: 实现策略已设为时序优先（含 post-route phys_opt）"

''' + anchor

if 'Performance_ExtraTimingOpt' not in s:
    assert anchor in s, 'build_project.tcl 里找不到 launch_runs impl_1 锚点'
    s = s.replace(anchor, add, 1)
    io.open(p, 'w', encoding='utf-8').write(s)
    print('build_project.tcl 已加时序收敛设置')
else:
    print('已包含，跳过')
