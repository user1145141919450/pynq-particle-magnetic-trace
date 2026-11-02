# -*- coding: utf-8 -*-
"""板上验证真实 screen_out 驱动（不是测试直发）。"""
import sys
sys.path.insert(0, '/home/xilinx/wp')
import screen_out
import match_flow

m = match_flow.MatchFlow()
m.phase = 'PLAY'
m.timer = 10.0
screen_out.update(m, 20.0, 1200.0, 1, -15.0, 1000.0, 2)
print('sent')
