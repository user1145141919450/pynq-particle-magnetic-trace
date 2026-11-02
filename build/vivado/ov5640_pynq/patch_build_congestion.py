# -*- coding: utf-8 -*-
"""patch_build_congestion.py —— 把实现策略从"时序优先"换成"专治拥塞"。

依据（2026-09-28 第五轮构建日志）：
  布线器明说 `Congestion is preventing the router from routing all nets`，
  且 `Max Cong = 88~91%`（布线资源接近饱和）—— **瓶颈是拥塞不是逻辑深度**。
  时序优先策略（Performance_ExtraTimingOpt）对拥塞无能为力，
  该换成 Congestion_SpreadLogic_high（把逻辑摊开、降局部拥塞）。
  本轮 WNS 已经收敛到 -0.142ns（五轮：-1.028 -> -0.471 -> -0.554 -> -0.393 -> -0.142），
  只差最后 0.14ns，值得用对症的策略再跑一轮。
"""

import io
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
p = 'build_project.tcl'
s = io.open(p, encoding='utf-8').read()

old = "catch { set_property strategy Performance_ExtraTimingOpt $impl_run }"
new = ("# ★ 2026-09-28 第五轮：布线器报拥塞（Max Cong 88~91%），时序优先策略不对症 ->\n"
       "#   换 Congestion_SpreadLogic_high（摊开逻辑降拥塞）；phys_opt 保留。\n"
       "catch { set_property strategy Congestion_SpreadLogic_high $impl_run }")
assert old in s, '找不到策略设置那一行'
s = s.replace(old, new, 1)
io.open(p, 'w', encoding='utf-8').write(s)
print('实现策略已换成 Congestion_SpreadLogic_high')
