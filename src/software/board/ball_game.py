# -*- coding: utf-8 -*-
"""ball_game.py — V3 原型：**真正的游戏循环**（读缩略图 -> 找球 -> 追轨迹 -> 出状态）。

为什么现在做它（2026-09-27 下午）：V2 已经把"缩略图能找到球、与全分辨率差 <=8px"
验证过了（白纸 16/16 帧、木纹杂背景 10/11 帧），**该往前走了** —— 演示要的是
"每帧知道球在哪、往哪走、画出轨迹"，而不是再调检测器。

它同时是**链路长跑测试**：游戏循环就是"30fps 连续采集"，跑几分钟正好量出
"自愈阶梯（脉冲 -> 传感器断电重上电）能不能把它撑住" —— 演示现场没有人手跑
stall_probe，撑得住才叫能用。

用法（板上）：
  python3 /home/xilinx/wp/ball_game.py --sec 120 [--nobit] [--render 30] [--trail 60]
    --sec S     跑 S 秒（默认 60）
    --render K  每 K 帧存一张叠加图到 /home/xilinx/wp/game_d.png（默认 0=不存）
    --trail N   轨迹保留最近 N 个点（默认 60）
    --no-bg     不用背景差分（默认有背景就自动用）
    --max-r P   球半径上限（px，默认 320）

  ★ P1：V3 实时预测（2026-09-28 深夜加；**不给 --v3 时行为一个字节都不变**）
    --v3 K        每 K 个**有效帧**（真的找到球的那种）拿当前球位喂一次 V3，
                  当场画出那一簇 17 路的判定，覆盖写 --v3-dump（默认 0 = 不预测）
    --v3-seed S   场区种子（默认 7，与定妆照同一套：3 块磁场 + 左右两洞）
    --v3-b B      固定 |B|（默认 0.40，符号隔块翻转）
    --v3-fan F    扇形半宽（默认 12°）
    --v3-steps N  积分步数（默认 600 ≈ 2.5s，够画出射；越大折线越贵）
    --v3-angle A  入射角（默认 0.0° = **右水平**；逆时针为正、360° 一圈；
                  2026-10-28 起是绝对角度，不再是"边法线相对角"）
    --v3-speed V  入射速度（默认 400 px/s；给 0 = 改用跟踪出来的速度）
    --v3-track-angle  入射角也改用跟踪出来的方向（原型模式）
    --v3-no-arcs      只给临界那条画弧线（省 ~150ms/次，图上其余路只剩终点小点）
    --v3-dump F   预测图文件名（默认 v3_live.png，写在工作目录）
    --v3-hdmi     ★ P2：把 V3 每次预测画的图**直接推到板载 HDMI**（接显示器用）。
                  只上屏、不写盘 —— 配 --v3-dump none 就是"零写盘直出"现场档。
                  帧泵启动失败会自己关掉，游戏照跑；**不给它时行为一个字节都不变**。
    --v3-hdmi-live ★ 直播档（2026-10-01 夜加；**必须配 --v3-hdmi**）：屏幕不再是
                  "每次预测换一张仿真图"（stst 换帧一闪一闪、且看不到实拍），改成
                  **单缓冲原地写、DMA 全程不停** —— 每个循环抓一张原图（~15ms），
                  叠上场区边界/洞口/球圈/轨迹直推上屏：**屏幕 = 摄像头实拍 + 跟踪
                  叠加，不闪不黑**。配 --v3-review：每次"这一杆会进洞"（扇形正中
                  那一路 = 球员瞄的角度，判定=goal）-> 屏幕切到该次的轨迹复盘图
                  review 秒，然后自动回直播。不给它时行为一个字节都不变。
    --v3-review S  直播档里"进球复盘图"停留的秒数（默认 6.0；只在 --v3-hdmi-live
                  下生效；给 0 = 关掉复盘）。触发 = 正中那一路 goal 的**上升沿**。
    --track-margin P  ★ 追踪只认场区内的球：候选球心的游戏坐标要落在场区外扩 P
                  像素的框里（默认 20）。场外的大块（手机支架/黑色网线）从此不再
                  抢目标。只在 --v3 开启时生效（要场区+标定）；不开 --v3 行为不变。
    --live-dump F  直播档顺手存实拍叠加图（每 5 秒最多一张，验证用；默认不存）
    --live-fast    直播档推帧缩放换 INTER_NEAREST（板上实测 75ms -> 13.5ms/帧，
                   1.25 倍上采样看不出区别）。fps 不够就翻这一档，别的不动。
    --fire-margin M  ★ 兼容保留（2026-10-28 起不限制球位）：**输入层开着 = 发射
                  模式** —— 平时一帧都不预测；手机按"发射"（键盘空格）才发一杆。
                  **球在红色场区内任意位置都可识别/初始/发射**（球身压着场区即可；
                  喂 PL 仍会夹回安全盒）；方向 = **界面绝对角**（0°=右水平、
                  逆时针为正，360° 一圈）、发射点 = 球的位置。
                  ★★ 2026-10-05 规则订正：发射 = **真球入场**——球留场里飞
                  （镜面反射、多球互撞），弹到出射（进洞）为止，轨迹实时上屏
                  （realtime_ball，与瞄准预览逐位一致）。
   --match        ★ M2.2 局状态机（2026-10-06；规则书 v4 / game_pc/main.py 移植，
                  纯逻辑在 match_flow.py，自检 44/44）：10 局制（第 n 局 n 块）、
                  局前(第 2 局起)→局中→结算 2.6s→下一局、平局加赛；弹药每局
                  3 红 2 蓝（按**实物球颜色**扣，未知色不设限）；进球 +10、本局
                  立即结束；30s 洞口计时（暂停冻结；移动逻辑 M2.4 接）。局前就绪
                  = 双方各按自己的发射键（空格/回车）。**默认关**：不带 --match
                  时本脚本行为一个字节都不变；需要 --v3-input（发射模式）。
  一条命令跑（板上）：./game_v3.sh    # = pl_probe 把关 + 上面这些参数的默认值

退出码：0 正常跑完；9 环境问题（抓不到帧/地图不可信）
"""

import atexit
import json
import math
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, '/boot')
sys.path.insert(0, '/home/xilinx/wp')

import cv2
import numpy as np

# ★ 2026-10-01 深夜（用户三条之③）：发射结果的落点 —— **游戏写、手机读**。
#   serve 的 /shot 端点读的是同一份（v3_input.SHOT_PATH，同一个环境变量）。
_SHOT_PATH = os.environ.get('V3_SHOT_PATH', '/dev/shm/v3_shot.json')
# ★ 2026-10-06（用户："网页应该实时刷新有几个球的序号"）：场上球列表（号+颜色），
#   游戏写、serve 的 /balls 读、手机网页做成**动态选球按钮**。
_BALLS_PATH = os.environ.get('V3_BALLS_PATH', '/dev/shm/v3_balls.json')
# ★ 2026-10-07 M2.3：对局状态文件（手机 /match 读；--match 时每 0.5s 原子写）
_MATCH_PATH = os.environ.get('V3_MATCH_PATH', '/dev/shm/v3_match.json')


def main():
    # ★★ 2026-10-05 深夜（野 DMA 事故实锤后加的保险）：本项目从 ssh 后台任务启动，
    #   **SIGINT 被继承成"忽略"**，现场杀进程只能用 SIGTERM —— 而 TERM 默认"立刻死"，
    #   **不跑 atexit ⇒ 停不了 VDMA ⇒ 野 DMA**（今晚实锤：r13 被 TERM 杀 → r14 一起来
    #   就 segfault、import pynq 都死；用不依赖 PYNQ 的 link_doctor --halt 一停野 DMA，
    #   import 立刻恢复）。把 TERM 接成优雅退出（转 SystemExit），atexit 就能收 DMA。
    import signal as _signal
    try:
        _signal.signal(_signal.SIGTERM, lambda *_a: sys.exit(0))
    except Exception:
        pass
    secs = 60.0
    render_every = 0
    trail_len = 30
    max_r = 120.0                    # ★ 2026-10-23：320→120，拒手的大块团（球实测 19~27 块）
    # ★ P1：V3 实时预测的参数（**默认全关** —— 不带 --v3 时本脚本的行为一个字节都不变）
    v3_every = 0                     # 每 K 个有效帧预测一次（0=关）
    v3_seed, v3_b = 7, 0.40          # 场区：与定妆照同一套（seed=7 -> 3 块磁场 + 左右两洞）
    v3_fan, v3_steps = 12.0, 600     # 扇角半宽 / 积分步数（600 步 ≈ 2.5s，够画出射）
    v3_angle, v3_speed = 0.0, 400.0  # 界面输入值（0°=右水平/逆时针、400 px/s）
    # ★ 2026-10-05 双人：P2 独立的角度/速度（手机网页下半区写的 p2_*）
    v3_angle2, v3_speed2 = 0.0, 400.0
    # ★ 2026-10-05 夜：**选球**（每个玩家控哪颗编号的球；手机网页 [球1/2/3] 可切）
    v3_ball1, v3_ball2 = 1, 2
    # ★★ P2：输入层（手机网页遥控 / 有线键盘）—— 两个源写**同一份状态文件**，这里每帧读。
    #   **默认 None = 完全不开**：不带 --v3-input 时本脚本行为一个字节都不变（铁律）。
    #   特殊值 'auto' = /dev/shm/v3_input.json（v3_input.PATH_DEFAULT）。
    v3_input = None
    v3_hub = None                    # v3_input.InputHub，开了才有
    # ★★ M2.2 局状态机（--match）：默认关（铁律同 --v3-input —— 不带 --match 时
    #   本脚本行为一个字节都不变）。需要输入层（对局靠它读"发射/就绪/选球"）。
    match_on = False
    # ★★ 2026-10-16 粒子引擎（档3，--pe）：**默认关**（铁律同 --v3-input）——
    #   不带 --pe 时本脚本行为一个字节都不变。
    v3_pe = False
    # ★ 2026-10-17 修：'mod' 键原来只在 MATCH=1 时才补（378 行）——**不带 MATCH 跑
    #   沙盒模式时**，_phone_state_tick(_MF['ob'], _MF['mod'], ...) 在 0.5s 节拍处
    #   抛 KeyError: 'mod' **开局即崩**（首现于 r90 风暴试玩；此前所有上板实跑都带
    #   MATCH=1，从没暴露）。初始化就带上 None，函数内 `ob is not None` 门会整段跳过。
    _MF = {'ob': None, 'mod': None, 't': None}  # 局状态机实例 + 计时基准（match_flow.MatchFlow）
    v3_arcs = True                   # 画完整折线（折线由板载 CPU 跑定点黄金模型给，不是 PL）
    v3_dump = 'v3_live.png'
    v3_track_angle = '--v3-track-angle' in sys.argv
    v3_hdmi = '--v3-hdmi' in sys.argv    # ★ P2：HDMI 直出（hdmi_out.FramePump）；默认关
    v3_pump = None                       # 帧泵实例，真开起来才有
    # ★★ 2026-10-01 夜（用户①+③两点）：直播档 / 进球复盘 / 追踪只认场区内
    #   ① `--v3-hdmi-live`：老的 stst 是"每次预测停机换一张图"——屏幕一闪一闪、而且
    #      全程看不到实拍。直播档 = **单缓冲原地写、DMA 全程不停**，每个循环抓一张
    #      原图叠上场区/球/轨迹推上屏：屏幕 = **摄像头实拍 + 跟踪叠加**（不闪不黑）。
    #   ③ `--v3-review S`：这一杆（扇形**正中那一路** = 球员瞄的角度）判定 goal（进洞）
    #      -> 屏幕切到该次的轨迹复盘图 S 秒，然后自动回直播（"进球后的复盘"）。
    #   ② `--track-margin P`：追踪只认**场区外扩 P 游戏像素**内的候选 —— 场外的
    #      手机支架/黑色网线不再靠"面积最大"抢走目标（上一趟 38% 的时间在抢）。
    #   `--live-dump F`：直播档顺手存一张实拍叠加图（每 5s 最多一张，验证用）。
    #   ★ 不给这些旗标时，本脚本行为一个字节都不变（铁律）。
    v3_hdmi_live = '--v3-hdmi-live' in sys.argv
    v3_review = 6.0                      # 复盘图停留秒数（0 = 关掉复盘）
    _review_given = '--v3-review' in sys.argv
    track_margin = 20.0                  # 场区外扩容差（游戏像素）
    # ★ 2026-10-01 深夜（用户三条）：发射模式的门槛 —— 球心离最近那条边 ≤ 这个值
    #   才算"摆在边界上"。70px @ 场区 960x540（实体约 480mm 宽）= 离边 ~35mm。
    fire_margin = 70.0
    live_dump = None                     # 直播档存图路径（None = 不存）
    #   `--live-fast`：推帧缩放换 INTER_NEAREST（板上实测 75ms -> 13.5ms/帧）。
    #   1.25 倍上采样看不出区别；直播档 fps 不够就翻这一档（不改别的任何东西）。
    live_fast = '--live-fast' in sys.argv
    # ★ 2026-10-24：串口屏状态输出（TJC 115200, page index）。
    #   默认关；`--screen`（或 game_v3.sh SCREEN=1）打开。串口没插/掉线不影响游戏。
    screen_on = '--screen' in sys.argv
    # ★ 2026-10-25：HDMI 去 HUD（状态已上串口屏）+ 场区裁剪放大。
    #   `--no-hud` 去掉顶部/底部常驻文字（保留瞬时道具横幅，裁剪后画）；
    #   `--live-crop` 按标定场区 bbox + margin 裁剪再缩放到 1280x720。
    no_hud = '--no-hud' in sys.argv
    live_crop = '--live-crop' in sys.argv
    crop_margin = float(os.environ.get('LIVE_CROP_MARGIN', '0.03'))
    for i, a in enumerate(sys.argv):
        if a == '--sec' and i + 1 < len(sys.argv):
            secs = float(sys.argv[i + 1])
        if a == '--render' and i + 1 < len(sys.argv):
            render_every = int(sys.argv[i + 1])
        if a == '--trail' and i + 1 < len(sys.argv):
            trail_len = int(sys.argv[i + 1])
        if a == '--max-r' and i + 1 < len(sys.argv):
            max_r = float(sys.argv[i + 1])
        if a == '--v3' and i + 1 < len(sys.argv):
            v3_every = int(sys.argv[i + 1])
        if a == '--v3-seed' and i + 1 < len(sys.argv):
            v3_seed = int(sys.argv[i + 1])
        if a == '--v3-b' and i + 1 < len(sys.argv):
            v3_b = float(sys.argv[i + 1])
        if a == '--v3-fan' and i + 1 < len(sys.argv):
            v3_fan = float(sys.argv[i + 1])
        if a == '--v3-steps' and i + 1 < len(sys.argv):
            v3_steps = int(sys.argv[i + 1])
        if a == '--v3-angle' and i + 1 < len(sys.argv):
            v3_angle = float(sys.argv[i + 1])
        if a == '--v3-speed' and i + 1 < len(sys.argv):
            v3_speed = float(sys.argv[i + 1])
        if a == '--v3-dump' and i + 1 < len(sys.argv):
            v3_dump = sys.argv[i + 1]
        if a == '--v3-input' and i + 1 < len(sys.argv):
            v3_input = sys.argv[i + 1]
        if a == '--match':
            match_on = True
        if a == '--pe':
            v3_pe = True
        if a == '--v3-review' and i + 1 < len(sys.argv):
            v3_review = float(sys.argv[i + 1])
        if a == '--track-margin' and i + 1 < len(sys.argv):
            track_margin = float(sys.argv[i + 1])
        if a == '--fire-margin' and i + 1 < len(sys.argv):
            fire_margin = float(sys.argv[i + 1])
        if a == '--live-dump' and i + 1 < len(sys.argv):
            live_dump = sys.argv[i + 1]
    if '--v3-no-arcs' in sys.argv:
        v3_arcs = False
    # ★ 记下**配置值**：循环里连续失败会把 v3_every 清零，汇总时不能拿它当分母
    v3_every_cfg = max(1, v3_every)
    if v3_input is not None and not v3_every:
        # ★ 静默失败最贵：给了 --v3-input 却没开 --v3，输入层是**不会生效**的
        #   （它只喂 V3 预测那一簇）。必须喊出来，不然演示时按半天没人理。
        print('[输入] ★ --v3-input 给了，但 --v3 没开 -> 输入层**不生效**；'
              '加 --v3 6 之类的才有人读它')
        v3_input = None
    if v3_pe and v3_input is None:
        print('[粒子] ★ --pe 给了，但输入层没开（--v3-input）-> 风暴键没有输入源，不生效')
        v3_pe = False
    if match_on and v3_input is None:
        # ★ 静默失败最贵（同族第四例）：对局靠输入层读"发射（=就绪）/选球"，
        #   输入层没开它就没有任何输入源 —— 必须喊出来，不许默默不开。
        print('[对局] ★ --match 给了，但输入层没开（--v3-input）-> 局状态机**不生效**')
        match_on = False
    if v3_hdmi and not v3_every:
        # ★ 同一个坑（静默失败最贵）：HDMI 直出推的是 **V3 每次预测画的那张图**，
        #   没开 --v3 就没人画图 -> 屏幕上只剩帧泵启动时的深灰底，看着像"死机"。
        print('[HDMI] ★ --v3-hdmi 给了，但 --v3 没开 -> HDMI 直出**不生效**；'
              '它推的是 V3 每次预测画的那张图，加 --v3 6 之类的才有人喂它')
        v3_hdmi = False
    if v3_hdmi_live and not v3_hdmi:
        # ★ 静默失败最贵（第 N 次）：直播档只是**帧泵的一个模式**，没有 --v3-hdmi
        #   就没有帧泵，直播档会一声不吭地不生效 —— 现场"屏幕没画面"最难查。
        print('[直播] ★ --v3-hdmi-live 给了，但 --v3-hdmi 没开 -> 直播档**不生效**；'
              '直播档是帧泵的一个模式，两个旗标一起给')
        v3_hdmi_live = False
    if _review_given and not (v3_hdmi and v3_hdmi_live):
        print('[复盘] ★ --v3-review 给了，但没有 --v3-hdmi --v3-hdmi-live -> '
              '复盘**不生效**（它是直播档的伴生件）')

    # ★ 2026-10-24 串口屏：import 失败只关屏、不带走游戏（静默失败最贵）
    _screen_out = None
    if screen_on:
        try:
            import screen_out as _screen_out
            # ★ 2026-10-25：上次进程可能停在加球选择页；开局先切回状态页
            _screen_out.set_page('index')
            print('[屏幕] 串口屏状态输出已开（TJC 115200，页面 index）',
                  flush=True)
        except Exception as _escr:
            print('[屏幕] ★ --screen 给了但 import screen_out 失败：%s'
                  % str(_escr)[:120], flush=True)
            screen_on = False

    if '--bit-only' in sys.argv:
        # 守护脚本用的"只重配 PL"档：下完 bit 就退出（给 game_loop.sh 的第 2 级救援用）
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] 已重配 PL（--bit-only）')
        return 0
    if '--nobit' not in sys.argv:
        from pynq import Bitstream
        Bitstream('/boot/ov5640_m1a_wrapper.bit').download()
        print('[加载] bit 已下载')

    from ball_detect import _make_cap, _init_video
    from fastmem import FastMem
    from ball_map_detect import (MAP_BASE, CTRL_BASE, STAT_BASE, BLK, BH, BW,
                                 read_map, map_to_bgr, map_gate, detect_map,
                                 detect_map_bg, detect_map_from_stats,
                                 _ring_median_fast, raw_to_bgr,
                                 frame_sane_map, load_bg_words, bg_words_to_bgr)
    from vdma_capture import WD_STAT

    # ★ 2026-10-20 差距 1：PL 检测 v1（掩码+形态学+CCL）—— MAPDET_PL=1 时
    #   PL 出候选统计，PS 只做 classify_map 复判（detect_map_from_stats）。
    #   默认关：bit 里没有 map_detect_axi 时开它只会多一条错误日志。
    _plmd = None
    if os.environ.get('MAPDET_PL') == '1' or '--mapdet-pl' in sys.argv:
        try:
            from map_detect_pl import MapDetect, VER_V1
            _plmd = MapDetect()
            _pv = _plmd.read()['version']
            if _pv != VER_V1:
                print('[检测] PL 检测 VERSION=0x%08X（要 v1）-> 关闭 PL 路径' % _pv)
                _plmd.close()
                _plmd = None
            else:
                _plmd.enable()
                print('[检测] PL 路径已开：PL 出候选统计，PS 只复判（classify_map）')
        except Exception as e:
            print('[检测] PL 路径开不了（%s）-> 回退 PS' % str(e)[:80])
            _plmd = None

    # ★ 启动先确认传感器没被"上一轮的救援残留"按在断电状态（bit0/bit1 = pwdn/停 xclk）
    try:
        from pynq import MMIO as _M
        _m = _M(0x43A30000, 0x1000)
        _v = _m.read(0x00)
        if _v & 0x03:
            _m.write(0x00, _v & ~0x03)
            print('[电源] 启动时发现传感器被按住断电（ctrl=0x%08X）-> 已放开' % _v)
            time.sleep(0.8)
    except Exception as e:
        print('[电源] 电源位体检跳过（%s）' % str(e)[:60])
    cap = _make_cap()
    # ★ 场景门禁（2026-09-29 夜）：热身帧均值低于阈值就直接**拒跑**（要硬跑 --allow-dark）。
    #   见 ball_detect._init_video 里那段注释：热身帧均值 91.8 = 镜头对场区、灯亮、球在场；
    #   13.5 = 镜头朝下对着桌面（昨晚白跑两趟的真实原因，当时还被我误判成"光照/曝光"）。
    _init_video(cap, dark_gate=True)
    swap = 1 if getattr(cap, '_swap_decided', False) else 0
    print('[配置] 原图 swap=%d  max_r=%.0fpx  跑 %.0fs' % (swap, max_r, secs))

    # ★★ 背景必须**懒解码**（2026-09-29 凌晨实锤的 bug）：
    #   文件里存的是**原始地图字**（uint16 扁平，11520 个），**不是 BGR 图**。
    #   原来这里是 `bg_map = np.load(BG_MAP)` 直接当图喂给 detect_map_bg
    #   -> `cv2.absdiff((90,128,3), (11520,))` **尺寸不匹配** -> 抛异常
    #   -> 整个 60 秒长跑**当场崩掉**（0 帧 + traceback，今晚白跑一趟）。
    #   正确姿势（v3_scan.py 相机路径一直是这么写的，这里没跟上）：
    #   先 load_bg_words 拿原始字，等**字节序判定 mswap 定下来**再 bg_words_to_bgr
    #   解码 —— 背景存原始字就是为了跟"当时判的字节序"解耦。
    bg_words = None
    bg_map = None
    if '--no-bg' not in sys.argv:
        bg_words = load_bg_words()
        if bg_words is not None:
            print('[背景] 已载入差分背景（%d 个原始字）-> 等字节序定下来再解码'
                  % bg_words.size)
        else:
            print('[背景] 没背景图/格式不对 -> 单帧颜色掩码（先 --bgcap 拍一张更好）')
    else:
        print('[背景] --no-bg：单帧颜色掩码')

    map_max_area = int(np.pi * (max_r / float(BLK)) ** 2 * 1.2)

    # 游戏状态：球在哪、往哪走、轨迹
    trail = []
    n_frames = n_hit = 0
    _FT = []                      # ★ 2026-10-28：最近 30 帧时间戳（算**即时**帧率）
    t_start = time.time()
    t_det = []
    last_render = 0
    outdir = '/home/xilinx/wp' if os.path.isdir('/home/xilinx/wp') else '/boot'

    # ---- ★ P1：V3 实时预测的场区与标定（不带 --v3 就整段跳过，什么都不碰）----
    v3_world = v3_V = v3_H = v3_mod = None
    live_balls = []                              # ★ 真球队列（2026-10-05 规则订正）
    _tracks = []                                 # ★ 2026-10-25：局前也要用（/match 刷新）
    import ball_tracker as _btk                  # ★ 多目标追踪（2026-10-05 用户需求）
    trk = _btk.MultiTracker()
    if v3_every:
        import v3_scan as _vs
        import v3_fixedpoint as _vf
        import realtime_ball as _rtb              # ★ 真球实时模拟（与预览逐位一致）
        import phys_objects as _po                # ★ 实物识别（黑条/绿椭圆/黑球）
        v3_mod, v3_V = _vs, _vf
        v3_world = _norm_holes(
            _vs.build_world(v3_seed, v3_b))    # 与出图脚本同一份 build_world
        v3_world.obstacles = _OBST                    # ★ 布置 PLACE：已摆障碍跨世界回注
        # ★★ 2026-10-04 用户定案（演示模式，"预设轨迹看着能打到就是要打到"）：**两个绿洞都算进**。
        #   洞的 target 原本是两人对战里的语义（对面选手的靶：左=1/右=0），而单人演示 side
        #   恒 0 ⇒ 左洞会被当成**一面墙**（撞上就弹回去，屏幕上完全看不出来）——但左边那个
        #   洞和右边画得一样绿，玩家必然读成"这条线进了"。做法：建完场景把每个洞的 target
        #   都摊成 0（side=0 ⇒ 全部匹配）。**不动 RTL**：v3_pl.write_scene 是先在软件里算好
        #   (target==side) 一位、再写进洞表寄存器的（v3_pl.py 第 80 行），改的是数据不是逻辑。
        for _h in v3_world.holes:
            _h['target'] = 0
        # ★★ 2026-10-16 粒子引擎（档3，--pe）：打开 PL 里的 pe_axi（0x40001000）。
        #   失败不致命（旧 bit 没映射这块——显式 --pe 才走到这，默认路一字不变）。
        if v3_pe:
            try:
                import particle_pl as _ppl
                _PEB['mm'] = _ppl.open_pe()
                _PEB['mod'] = _ppl
                _PEB['on'] = True
                _PEB['scene_key'] = None
                print('[粒子] ★ PL 粒子引擎已打开（BASE=0x40001000；16 粒并行物理；'
                      'B/N 放风暴）')
            except Exception as _epe:
                _PEB['on'] = False
                print('[粒子] ★ 打开失败（%s）—— bit 不是带 pe_axi 的新版？--pe 失效，'
                      '游戏照跑' % str(_epe)[:140])
        v3_H = _vs.load_calib(verbose=True)           # 相机像素 -> 游戏坐标
        print('[V3] 实时预测开启：每 %d 个有效帧一次  seed=%d B=%s  扇±%.0f° %d 步  折线=%s'
              % (v3_every, v3_seed, v3_b, v3_fan, v3_steps,
                 'all(17 路)' if v3_arcs else 'crit(仅临界那条)'))
        print('[V3] 场区：块=%d  洞=%s' % (len(v3_world.blocks),
              [(h['edge'], round(h['c']), h['target']) for h in v3_world.holes]))
        print('[V3] 入射角/速度 = %s'
              % ('**跟踪值**' if (v3_track_angle or v3_speed <= 0) else
                 '按键输入 %.1f°（0=右/逆时针）/ %.0f px/s'
                 '（规则默认；球是摆的不是推的）'
                 % (v3_angle, v3_speed)))
        if v3_H is None:
            print('[V3] ★ 没有标定 -> 球位按"画幅≙场区"折算，近景下会偏；'
                  '先跑 v3_calib.py --shot 更准')
        # ★ 2026-10-01 夜（用户第②点）：追踪只认场区内的球 —— 这里把口径喊出来
        #   （球要放"边界上"，所以容差不是 0；场外的手机支架/网线从此一票否决）
        print('[跟踪] 只认场区内的球：候选球心须落在场区外扩 %.0f 游戏像素内'
              '（--track-margin 可调；场外的大块不再抢目标）' % track_margin)
        if v3_H is None:
            # ★ 实话实说：没有标定时，"画幅≙场区"会把**整个相机画面**都算进场区框里
            #   （s_m=0.75 时相机 1024x720 覆盖游戏 1365x960）—— 场外的大块照样抢目标。
            print('[跟踪] ★ 但没有标定：默认假设下整个画面都落在场区框里 -> 拦不住场外大块；'
                  '要真拦住先跑 v3_calib.py --shot 生成 v3_calib.json')

        # ---- ★★ P2：输入层（手机网页 `v3_input.py serve` / 有线键盘 `v3_input.py key`）----
        #   两个源写**同一份状态文件**，这里每帧 poll 一次（内容没变就 54µs，板上实测）。
        #   ★★ 2026-10-01 深夜（用户①②③三条）：**输入层开着 = 发射模式** ——
        #     平时一帧都不预测（下面的周期预测块被 `v3_hub is None` 挡掉），
        #     手机"发射"/键盘空格写 fire 计数，游戏读它的**上升沿**在循环里发一杆
        #     （见"发射模式"块）。玩法规则见 --fire-margin 的用法注释。
        if v3_input is not None:
            # ★ 2026-10-06（选球写回 bug 的根因）：这里原来只 import 了 InputHub ——
            #   模块名没绑，而局部变量 `v3_input` 存的是**参数串**（如 'auto'）；
            #   选球块里 `v3_input.write_state(...)` 于是撞名 AttributeError 被吞。
            #   现在把 write_state 也显式引进来（选球写回用它，且必须带 path=_p）。
            from v3_input import (InputHub, PATH_DEFAULT as _V3IN_PATH,
                                  write_state as _v3in_write)
            _p = _V3IN_PATH if v3_input in ('auto', '') else v3_input
            # ★★ 2026-10-06（"选球永远锁在1号"的根因·之一）：主循环的**直播存图**
            #   块会复用 `_p` 这个名字（`_p = live_dump…`，每 ~5s 一次）——选球
            #   写回若用 `_p` 当路径，就会被写成 /dev/shm/live.bmp（还合法不报错！）。
            #   专名保存，选球写回一律用 _v3in_path。
            _v3in_path = _p

            # ★★ M2.2 局状态机（--match）：装在输入层罩子里 —— 对局靠输入层读
            #   "发射（=就绪）/选球"，没有输入层它没有任何输入源（上面已拦并喊）。
            if match_on:
                import match_flow as _mflow
                import hole_adapt as _ha      # ★ M2.4 洞口自适应（30s 无进球触发）
                _MF['ob'] = _mflow.MatchFlow()
                _MF['mod'] = _mflow           # 常量来源（_match_snapshot 用；不许再 import）
                print('[对局] ★ 局状态机开（10 局制）：%s'
                      % _MF['ob'].status_line(), flush=True)
                # ★ 2026-10-25 测试种子（**默认关**，正式运行不要设）：
                #   TEST_GIVE_BALLS=2 -> 双方各 2 个加球道具，跳过"打一局攒分买"。
                #   只影响 items['ball']，不碰积分/其它道具。变量名用 _tp，别覆盖
                #   上面的 `_p`（那是输入状态文件路径，后面 InputHub 还要用）。
                try:
                    _tgb = int(os.environ.get('TEST_GIVE_BALLS', '0') or 0)
                except Exception:
                    _tgb = 0
                if _tgb > 0:
                    for _tp in (1, 2):
                        _MF['ob'].items[_tp]['ball'] = max(
                            int(_MF['ob'].items[_tp]['ball']), _tgb)
                    print('[测试] ★ TEST_GIVE_BALLS=%d：双方加球道具各 %d'
                          '（仅测试；正式运行不设这个环境变量）'
                          % (_tgb, _tgb), flush=True)
                # ★ 2026-10-27 测试种子：TEST_GIVE_POINTS=50 -> 双方积分；
                #   只给货币，**商店购买仍然是真买**（买障碍/传送门/加球）。
                try:
                    _tgp = int(os.environ.get('TEST_GIVE_POINTS', '0') or 0)
                except Exception:
                    _tgp = 0
                if _tgp > 0:
                    for _tp in (1, 2):
                        _MF['ob'].points[_tp] = max(
                            int(_MF['ob'].points[_tp]), _tgp)
                    print('[测试] ★ TEST_GIVE_POINTS=%d：双方积分各 %d'
                          '（仅测试；商店购买仍是真买）' % (_tgp, _tgp),
                          flush=True)
                # ★★ 2026-10-29 用户点单：**专用「购买→摆放→识别」测试局**。
                #   TEST_START_PREP=1：强制第 1 局停在 PREP（商店解锁）并把商店
                #   画面直接打开；配合 TEST_GIVE_POINTS 走完整道具流程。
                #   正式运行不设这个环境变量（默认关）。
                try:
                    _tsp = int(os.environ.get('TEST_START_PREP', '0') or 0)
                except Exception:
                    _tsp = 0
                if _tsp > 0:
                    _MF['ob'].phase = 'PREP'
                    _MF['ob'].sub = {1: 'SHOP', 2: 'SHOP'}
                    _MF['ob'].phys_bought = {1: False, 2: False}
                    _MF['ob'].phys_placed = {1: False, 2: False}
                    _MF['ob'].adjust = False
                    _MF['ob'].msg, _MF['ob'].msg_t = (
                        '测试模式：局前商店已解锁（买实体件→立即摆放）', 999.0)
                    _SHOPV['open'] = True
                    # ★★ 2026-10-29 测试局实锤（"买完按使用没反应"）：
                    #   `base_ready` 只在 PLAY 的检测路径里建立；强制 PREP 时
                    #   它永远 False -> `_pplace_enter` 一直回「相机还在稳定中」。
                    #   测试局直接置位（免费基线为空——所有条都算新件，正好测摆放）。
                    _PHYS['t0'] = time.time()
                    _PHYS['base_ready'] = True
                    _PHYS['base_bars'] = []
                    _PHYS['base_greens'] = []
                    print('[测试] ★ TEST_START_PREP=1：第 1 局强制停在 PREP、'
                          '商店画面已打开、识别基线置位（仅测试；购买仍是真买）',
                          flush=True)
                # ★★ 2026-11-01 用户演示用：一次性「残局种子」（默认关）。
                #   DEMO_START_R7=1：开局直接第 7 局、局分/积分 100:100、
                #   P1 红2（无蓝）/ P2 蓝2（无红）；第 8 局起走正常流程。
                try:
                    _r7 = int(os.environ.get('DEMO_START_R7', '0') or 0)
                except Exception:
                    _r7 = 0
                if _r7 > 0:
                    _m = _MF['ob']
                    _m.round = 7
                    _m.goals = {1: 10, 2: 10}        # 局分 = goals×10 = 100:100
                    _m.points = {1: 100, 2: 100}     # 积分 100:100
                    _m.ammo = {1: {1: 2, -1: 0},     # P1 两红
                               2: {1: 0, -1: 2}}     # P2 两蓝
                    _m.timer = 0.0
                    _m.grace = None
                    _m.round_goals = {1: 0, 2: 0}
                    _m.free_hole = {1: 1, 2: 1}
                    _m.items = {p: {k: 0 for k in _mflow.ITEM_KEYS}
                                for p in (1, 2)}
                    _m.phase = 'PLAY'
                    _m.sub = {1: 'READY', 2: 'READY'}
                    _m.phys_bought = {1: False, 2: False}
                    _m.phys_placed = {1: False, 2: False}
                    _m.adjust = False
                    # 清掉构造时留下的"第 1 局开始"提示（手机页会显示 msg），
                    # 避免残局第 7 局开头闪一下第 1 局文案。
                    _m.msg, _m.msg_t = '', 0.0
                    # ★ 关键：MatchFlow() 构造时排了一个 round_start(1) 事件；
                    #   不丢掉的话第一帧会把场界按"第 1 局"（1 块）重建，覆盖残局。
                    try:
                        _m.drain()
                    except Exception:
                        pass
                    # 第 7 局 = 7 块磁场（规则书 §1）；洞口沿用默认（残局不随机）。
                    v3_world = _norm_holes(_vs.build_world(v3_seed, v3_b,
                                                          n_blocks=7))
                    v3_world.obstacles = _OBST
                    for _h in v3_world.holes:
                        _h['target'] = 0
                    print('[演示] ★ DEMO_START_R7=1：残局第 7 局（场界 %d 块），'
                          '局分/积分 100:100，P1 红2 / P2 蓝2（第 8 局起正常）'
                          % len(v3_world.blocks), flush=True)
            # ★ 发射方向 = 边法线 + 输入角 —— 角度/速度**必须由输入层给**。
            #   --v3-track-angle / --v3-speed 0 会把它们改走**跟踪值**（球是静止摆着的，
            #   跟踪速度≈0）—— 那是"按了没反应"一类最难查的故障，这里**强制纠正**并喊出来。
            if v3_track_angle or v3_speed <= 0:
                print('[发射] ★ --v3-track-angle / --v3-speed 0 与发射模式冲突：'
                      '方向必须是"边界法线 + 输入角" -> 强制纠正（track_angle=False、'
                      'speed=%s）' % ('%.0f' % v3_speed if v3_speed > 0 else '400'))
                v3_track_angle = False
                if v3_speed <= 0:
                    v3_speed = 400.0
            v3_hub = InputHub(path=_p, angle=v3_angle, speed=v3_speed)
            _a, _s, _ch = v3_hub.poll()      # 开局先吃一笔：手机可能起游戏**之前**就设好了
            if _ch:
                print('[输入] 开局就吃到一笔（%s 写的）：%.1f° / %.0f px/s'
                      ' —— 盖掉命令行给的 %.1f° / %.0f'
                      % (v3_hub.src, _a, _s, v3_angle, v3_speed))
            v3_angle, v3_speed = _a, _s
            # ★ 发射计数只认**上升沿**：开局把基准对齐到文件里的现值（上一趟残留的
            #   fire=3 也不许被当成"刚点了发射"）。
            # ★ 2026-10-05 双人：开局把**两个玩家**的发射计数都对齐到当前值——
            #   否则文件里的旧计数会被当成"上升沿"，一开局自发一杆
            #   （实锤：r20 开局 P1 自动打了一杆）。last1/last2 = 各自玩家的基准。
            _FIRE['last1'] = v3_hub.fire
            _FIRE['last2'] = v3_hub.fire2
            # ★★ 2026-10-15（板 r76 实锤"幽灵重放"）：同一条纪律要铺到**所有**
            #   计数器——状态文件跨局存活，重启后第一帧会把上一局的按键当**新事件**
            #   （实况：r76 开局自己冒出一个"加洞口→被旧 ESC 取消→旧点位置被忽略"）。
            #   这里把 _MAS / _SEL / _SHOP 的每个键**逐键**对齐到 hub 现值：
            #   只有之后**新发生**的笔才算数（宁可不做，也不做一记莫名其妙的）。
            #   ⚠ 别名：确认计数在 _MAS 里叫 cfmN、在 hub 里叫 buyN——别抄错。
            for _bk, _bv in (('edit1', v3_hub.edit1), ('edit2', v3_hub.edit2),
                             ('hole1', v3_hub.hole1), ('hole2', v3_hub.hole2),
                             ('mup1', v3_hub.mup1), ('mdn1', v3_hub.mdn1),
                             ('mup2', v3_hub.mup2), ('mdn2', v3_hub.mdn2),
                             ('aup1', v3_hub.aup1), ('adn1', v3_hub.adn1),
                             ('aup2', v3_hub.aup2), ('adn2', v3_hub.adn2),
                             ('esc', v3_hub.esc),
                             ('cfm1', v3_hub.buy1), ('cfm2', v3_hub.buy2),
                             ('hopos1', v3_hub.hopos1),
                             ('hopos2', v3_hub.hopos2),
                             # ★ 2026-10-19：风暴计数也要对齐（此前漏了——重启后
                             #   旧计数要么幽灵触发、要么新笔被当旧账）
                             ('pe1', v3_hub.pe1), ('pe2', v3_hub.pe2)):
                _MAS[_bk] = int(_bv or 0)
            for _bk, _bv in (('sup1', v3_hub.sup1), ('sdn1', v3_hub.sdn1),
                             ('sup2', v3_hub.sup2), ('sdn2', v3_hub.sdn2)):
                _SEL[_bk] = int(_bv or 0)
            for _bk, _bv in (('mup1', v3_hub.mup1), ('mdn1', v3_hub.mdn1),
                             ('buy1', v3_hub.buy1), ('mup2', v3_hub.mup2),
                             ('mdn2', v3_hub.mdn2), ('buy2', v3_hub.buy2),
                             ('mbtn', v3_hub.mbtn),
                             ('sopen1', v3_hub.sopen1),
                             ('sopen2', v3_hub.sopen2)):
                _SHOP[_bk] = int(_bv or 0)
            print('[输入] 输入层**开**：%s（手机 serve / 键盘 key 都写这里）' % _p)
            print('[发射] ★★ **发射模式**（输入层开着就是它）—— 用户三条：')
            print('       ① 球摆在**场区边界**上，按"发射"后整条轨迹全是**模拟**'
                  '（PL 判 17 路 + 轨迹图），球不用真的滚；')
            print('       ② 球放在红色场区内**任意位置**都可识别/发射'
                  '（2026-10-28 起取消边界带）；')
            print('       ③ 发射入口：手机网页的大按钮 / 键盘**空格**'
                  '（W/S 调角 ±2°、A/D 调速 ±20、R 复位）。')
            print('       场内任意位置可发：球身压着场区即可（喂 PL 仍会夹回安全盒，'
                  '离边 ≥%.0fpx）；方向=最近边法线+输入角；平时**一帧都不预测**，'
                  '只有按了发射才花那一笔'
                  % (v3_mod.IN_FIELD_MARGIN + v3_mod.BALL_R))
            print('[发射] 结果落在 %s（手机网页 /shot 轮询显示）' % _SHOT_PATH)

        # ---- ★★ P2：HDMI 直出（帧泵 = hdmi_out.FramePump）----
        #   每次预测出图后，把那张图换到板载 HDMI 上（720p60）。换帧走 stst 档
        #   （停机-改地址-重启，与 hdmi_out 自己演示的那条路同一条）。**绝不让屏幕
        #   拖死游戏**：这里启动失败就关掉自己，循环里推帧连败 3 次也关掉自己。
        #   ★ 2026-10-01 夜：`--v3-hdmi-live` 走 live 档（单缓冲原地写，DMA 不停）
        #     —— 上屏的是**实拍+叠加**（每个循环抓一张原图），不是每次预测那张图；
        #     进球复盘图则借同一个泵原地写上去（见循环里的 _REV 状态机）。
        if v3_hdmi:
            try:
                from hdmi_out import FramePump
                v3_pump = FramePump(verbose=False,
                                    interp='nearest' if live_fast else 'linear')
                # ★ 2026-10-23 差距2 第二刀：live2 双缓冲（整帧写后台缓冲 +
                #   Park 指针帧边界换页）。**已上板验收：快晃不闪**，设为默认；
                #   出问题时 `HDMI_DBUF=0` 回退旧的单缓冲 live 档。
                _hdmi_mode = ('stst' if not v3_hdmi_live
                              else ('live' if os.environ.get('HDMI_DBUF', '1') == '0'
                                    else 'live2'))
                # ★★ 2026-10-31：PL 帧泵直接写显存地址 -> 必须**单缓冲**
                _plp_want = os.environ.get('PLPUMP', '0') not in (
                    '0', '', 'false', 'False')
                if _plp_want and v3_hdmi_live:
                    _hdmi_mode = 'live'
                v3_pump.start(mode=_hdmi_mode)
                if _plp_want:
                    try:
                        _plp_init(v3_pump)
                    except Exception as _epi:
                        print('[PL帧泵] 初始化异常（回退 PS）：%s'
                              % str(_epi)[:120], flush=True)
                # ★★ 2026-10-31：SMOOTH=1 -> 球的高频插值叠加（默认关）
                if os.environ.get('SMOOTH', '0') not in ('0', '', 'false', 'False'):
                    try:
                        _sm_start(v3_pump)
                    except Exception as _esm:
                        print('[平滑] 启动失败（不影响游戏）：%s'
                              % str(_esm)[:120], flush=True)
                if v3_hdmi_live:
                    print('[HDMI] 直播档**开**：屏幕=摄像头实拍+场区/球/轨迹叠加，'
                          '%s；缩放=%s'
                          % ('**live2 双缓冲（Park 帧边界换页）**'
                             if _hdmi_mode == 'live2'
                             else '单缓冲原地写、DMA 全程不停（不闪不黑）',
                             'INTER_NEAREST（--live-fast）' if live_fast
                             else 'INTER_LINEAR'))
                    print('       进球复盘：正中那一路判 goal（进洞）-> '
                          '切轨迹复盘图 %.0fs 再回直播%s'
                          % (v3_review, '（--v3-review 0 = 关掉复盘）'
                             if v3_review <= 0 else ''))
                    print('       ★ 提速（2026-10-01 夜）：每 K 帧那次预测平时只读 PL'
                          '判定（轻 tick，~3ms）；折线+渲染那 ~0.6s 只在**真进球'
                          '那一刻**现算一次复盘图')
                else:
                    print('[HDMI] 直出**开**：每次预测的那张图推到 720p60（stst）（%s）'
                          % ('本趟还在写盘 DUMP=%s —— 上屏不需要它，现场档建议'
                             ' --v3-dump none' % v3_dump
                             if (v3_dump and v3_dump.lower() not in ('none', '-'))
                             else '本趟不写盘（--v3-dump none），纯上屏'))
            except Exception as e:
                print('[HDMI] ★ 帧泵启动失败：%s' % str(e)[:160])
                print('       -> HDMI 直出关掉，游戏照跑（屏幕是加分项，不是主线）')
                v3_hdmi, v3_pump = False, None

    with FastMem(MAP_BASE, 0x8000) as mm, \
         FastMem(CTRL_BASE, 0x1000) as mc, \
         FastMem(STAT_BASE, 0x1000) as ms:
        # soft_rst：清 sticky + 让 map 用新帧重建
        mc.wr_u32(0x00, (swap << 2) | 0x02)
        time.sleep(0.2)
        mc.wr_u32(0x00, (swap << 2))
        time.sleep(0.5)

        mswap = None
        fc_prev = t_prev = None
        n_bad = 0
        fc_fresh = None
        t_fresh = time.time()
        t_gate = 0.0
        from pynq import MMIO
        mm3 = MMIO(0x43A30000, 0x10000)
        st = ms.rd_u32(0x00)
        print('[账本] 行数=%d 字数=%d' % ((st >> 16) & 0xFFFF, st & 0xFFFF))

        # ★★ 2026-10-29 改磁场：退出时把进入前的角度/速度写回去
        #   （P1 的 W/S、P2 的 ↑/↓ 在 EDIT 里是"定方向"，不能把对局瞄准参数改了）
        def _edit_do_confirm(m, world, pk):
            _bvx = _edit_apply_value(m, world)
            _ixx = int(m.edit['idx']) + 1
            m.end_edit(confirm=True)
            _MAS['snap'] = None
            _edit_restore()
            print('[道具] P%d 改磁场完成：块 %d B=%+.2f'
                  % (pk, _ixx, _bvx or 0.0), flush=True)
            _item_msg_set('块 %d 磁场已改为 %+.2f' % (_ixx, _bvx or 0.0))
            try:
                _shot_write(dict(t=round(time.time(), 3), ok=True,
                                 why='item', kind='item', seq=0,
                                 msg='P%d 改磁场(块%d B=%+.2f)'
                                     % (pk, _ixx, _bvx or 0.0)),
                            player=pk)
            except Exception:
                pass
            return _bvx

        def _edit_do_stage_confirm(m, world, pk):
            """★ 2026-10-29 用户定案：三步都要确认——选块 -> 方向 -> 数值。"""
            _r = m.edit_confirm_stage()
            _ix = int(m.edit['idx']) + 1
            if _r == 'DIR':
                try:
                    _b2 = float(world.blocks[int(m.edit['idx'])].b)
                except Exception:
                    _b2 = 0.0
                print('[道具] P%d 确认选块 %d（B=%+.2f）——W/S（P2 ↑/↓）定方向，'
                      '方向需再确认' % (pk, _ix, _b2), flush=True)
                _item_msg_set('已选块 %d——W/S（P2 ↑/↓）定方向，再按 %s 确认方向'
                              % (_ix, 'E' if int(pk) == 1 else '小键盘 *'))
            elif _r == 'VALUE':
                _sg = '+' if int(m.edit.get('sign', 1)) >= 0 else '-'
                print('[道具] P%d 确认方向 %s（块 %d）——输入数值，再确认修改'
                      % (pk, _sg, _ix), flush=True)
                _item_msg_set('方向 %s——输入大小（0.10~0.80），再按 %s 确认修改'
                              % (_sg, 'E' if int(pk) == 1 else '小键盘 *'))
            elif _r == 'CONFIRM':
                _edit_do_confirm(m, world, pk)
            return _r

        def _edit_do_back(m):
            """ESC 退一步：VALUE -> DIR -> SELECT；SELECT 再 ESC = 取消。"""
            _st = m.edit_back()
            if _st == 'DIR':
                _item_msg_set('回到方向——W/S（P2 ↑/↓）重定，确认后再输数值')
                print('[道具] ESC 回到方向阶段', flush=True)
            elif _st == 'SELECT':
                _item_msg_set('回到选块——A/D（P2 ←/→）或鼠标，再确认（ESC=取消）')
                print('[道具] ESC 回到选块阶段', flush=True)
            else:
                _edit_do_cancel(m)
            return _st

        def _edit_do_cancel(m):
            for _b2, _v2 in (_MAS.get('snap') or []):
                _b2.b = _v2
            m.end_edit(confirm=False)
            _MAS['snap'] = None
            _edit_restore()
            print('[道具] %s' % m.msg, flush=True)

        def _edit_restore():
            nonlocal v3_angle, v3_speed, v3_angle2, v3_speed2
            try:
                _a0 = _EDC.get('ang')
                if not _a0:
                    return
                v3_angle, v3_speed, v3_angle2, v3_speed2 = _a0
                _EDC['ang'] = None
                if _v3in_path:
                    _v3in_write(player=1, angle=_a0[0], speed=_a0[1],
                                src='game', path=_v3in_path)
                    _v3in_write(player=2, angle=_a0[2], speed=_a0[3],
                                src='game', path=_v3in_path)
            except Exception:
                pass

        # ★★ 游戏循环**不抓原图**（2026-09-27 改，重要）★★
        #   游戏只要 map（23KB），原图那一帧 1.47MB 抓回来根本没用途 —— 而抓它
        #   每帧要 11~14ms（占循环周期 34ms 的三分之一）。VDMA 是**自循环**的
        #   （circular 模式，num_bufs 个缓冲自己轮转），我们不取数它也照样消费，
        #   流水线照跑、map 照新鲜 ✓。于是循环周期从 34ms 压到 ~23ms。
        #   顺带把"野 DMA / CMA 页被覆盖"这一整类风险从游戏路径上摘掉了。
        n_iter = 0
        # ★★ 2026-10-29（测试局实锤）：`ball` 原来只在"这一帧有检测"的分支里
        #   赋值。强制开局停在 PREP 时第 1 帧检测块不跑 -> 后面的 HUD/画圈
        #   直接引用就 UnboundLocalError（屏幕黑）。循环外先给个 None。
        ball = None
        while time.time() - t_start < secs:
            n_iter += 1
            # ★★ P2：每帧读一次输入层。**放在 t0 之前** —— 否则这点开销会混进
            #   "读回 map"的计时口径里（那笔账好不容易才量干净，不能再脏）。
            #   代价 = 读一个 150 字节的文件 + 比字节（板上实测 54µs，30fps 下 0.16%）；
            #   ★ 比的是**内容**不是 mtime —— 时间戳在 NTFS/FAT 上会骗人（漏写，实测过）。
            if v3_hub is not None:
                _a, _s, _ch = v3_hub.poll()
                if _ch:
                    v3_angle, v3_speed = _a, _s
                    print('[输入] %s 改：%.1f° / %.0f px/s'
                          % (v3_hub.src, _a, _s), flush=True)
                # ★ 2026-10-05 双人：P2 每帧同步（便宜），变了自己吱一声
                v3_angle2, v3_speed2 = v3_hub.angle2, v3_hub.speed2
                v3_ball1, v3_ball2 = v3_hub.ball1, v3_hub.ball2
                # ★★ 2026-10-07 修（启动阶段日志刷屏 1.5 万行实锤）：`changed2` 只在
                #   **文件内容变化的那一次 parse** 里被重算；内容没变的快路径直接
                #   return（干净利落），**不会把它清 False** —— 于是"上一次 P2 改过"
                #   之后，这一行会**每帧**重复打（坏帧空转期更是一秒几百行）。
                #   口径修正：只在"文件真的变了"这一拍（_ch=True）才看 changed2。
                if _ch and v3_hub.changed2:
                    print('[输入] P2 改：%.1f° / %.0f px/s'
                          % (v3_angle2, v3_speed2), flush=True)
            # ★ 2026-10-24 串口屏：5Hz 级节流在 screen_out 内部；只发变化字段。
            #   串口异常由 screen_out 自己吞掉并重试，绝不影响主循环。
            # ★ 2026-10-25 加球选择页：页面开着时暂停状态刷新（给触摸事件让路），
            #   每帧 poll 触摸命令；选完扣道具、取消/超时/离开对局不扣。
            if screen_on and _screen_out is not None:
                _tsc0 = time.perf_counter()
                if not _ABALL['on']:
                    _screen_out.update(_MF['ob'], v3_angle, v3_speed, v3_ball1,
                                       v3_angle2, v3_speed2, v3_ball2)
                _evab = _screen_out.poll_event()
                if _evab is not None:
                    if _ABALL['on'] and _evab in ('red', 'blue'):
                        _whoab = int(_ABALL['who'])
                        _oka, _msga = _aball_finish(_MF['ob'], _evab,
                                                    _screen_out)
                        print('[道具] %s' % _msga, flush=True)
                        _item_msg_set(_msga)
                        if _oka:
                            _shot_write(dict(t=round(time.time(), 3),
                                             ok=True, why='item',
                                             kind='item', seq=0, msg=_msga),
                                        player=_whoab)
                    elif _ABALL['on'] and _evab == 'cancel':
                        _msga = _aball_cancel(_screen_out)
                        print('[道具] %s' % _msga, flush=True)
                        _item_msg_set(_msga)
                    # 非选择态收到的事件直接丢弃（防旧触摸串到下一局）
                if _ABALL['on']:
                    _mab = _MF['ob']
                    if (time.time() - float(_ABALL['t']) > ABALL_TIMEOUT
                            or _mab is None or _mab.phase != 'PLAY'
                            or _mab.grace is not None):
                        _msga = _aball_cancel(
                            _screen_out, '加球选择超时/对局已结束（未扣）')
                        print('[道具] %s' % _msga, flush=True)
                        _item_msg_set(_msga)
                    else:
                        # ★ 切页后再补写 t8 + 三个数字；之后数字变了才重发
                        _aball_set_who(_screen_out)
                        _aball_set_count(_mab, _screen_out)
                _ms_add('screen', _tsc0)
            t0 = time.perf_counter()
            buf, ntry, fc = read_map(mm, mc)
            t1 = time.perf_counter()

            # 帧可信度：速率 + 地图字节序自检（第一次定下来之后不再重判）
            bad = False
            if fc_prev is not None and t_prev is not None:
                fps_now = (fc - fc_prev) / max(1e-3, t1 - t_prev)
                if fps_now < 20.0:
                    bad = True
            fc_prev, t_prev = fc, t1
            if (ms.rd_u32(0x00) & 0xFFFF) != 5760:
                bad = True
            if mswap is None and time.time() - t_gate > 0.5:
                # ★ 自检没过就**再抓一帧重试**（限频 0.5s）。第一版只抓一次启动帧，
                #   那一帧要是撕裂的就永远定不下字序 -> 之后每帧都被拒 -> "0 有效帧"
                #   （实测白跑 70 秒）。抓图只在"自检还没过"期间发生，过了就彻底停手
                #   —— 优化（不抓原图）与可靠性两全。
                t_gate = time.time()
                try:
                    rawg, _ig = _grab(cap)
                except Exception:
                    rawg = None
                if rawg is not None:
                    bs, p0, p1 = map_gate(rawg, buf)
                    if bs is not None and max(p0, p1) >= 60.0:
                        mswap = bs
                        print('[地图] 自检 swap=0 %.1f%% / swap=1 %.1f%% -> 用 %d'
                              % (p0, p1, mswap), flush=True)
            if fc != fc_fresh:
                fc_fresh = fc
                t_fresh = time.time()
            # ★ 主循环自己救链路（不能只靠看门狗线程）：卡住的 AXI 读会把看门狗
            #   线程本身阻塞住（实测"停在 1603，约 29.3s" = 它被卡了 29 秒才动手）。
            #   ★★ 但触发必须**按时间**、并且**限频**（第一版按迭代次数判，而这个
            #   循环空转一次只要 1ms → 8 次 = 8ms → 120 秒里把传感器断电重上电按了
            #   **35 次**，它根本没机会稳定 —— 自己把链路按死了）。
            #   ★★ 2026-10-01 现场教训（fire_run5）：1.2s/5s 太灵敏 —— 慢性退化
            #   链路上帧计数每隔 ~10s 就抽一下（1~3s 自恢复），旧阈值每次都按、
            #   按了又不解决问题，11 次断电重上电把屏幕冻成"一卡一卡"。
            #   现在：≥2.5s 没有新的帧计数才算"真卡"（自恢复的抽风放它过去）；
            #   两次救援之间至少隔 12s（让上一次救援真正落定，别连环按）。
            #   判据抽在 `_rescue_due`（台架 rescue_ladder 场景拿时间线钉死）。
            if _rescue_due(time.time(), t_fresh, _RESCUE):
                # ★ 护栏：救援自己抛异常不许把游戏循环带走（照抄看门狗线程的
                #   纪律"吞可以，但必须留话"）。KeyboardInterrupt 不在 Exception
                #   里，Ctrl-C 照常退出 —— 函数里的 finally 负责还原静止位。
                try:
                    _rescue_escalate(mm3)
                except Exception as _er:
                    print('[自愈] ★ 主循环救援抛异常（这一次没救成）：%s'
                          % str(_er)[:140], flush=True)
            if mswap is None or bad:
                continue

            # ★ 背景懒解码：字节序定下来了才解（见上面 load_bg_words 的注释）
            if bg_words is not None and bg_map is None:
                bg_map = bg_words_to_bgr(bg_words, swap ^ mswap)
                print('[背景] 已按当前字节序解码（swap=%d mswap=%d）' % (swap, mswap),
                      flush=True)

            _prep_gate = (_MF['ob'] is not None and _MF['ob'].phase == 'PREP')
            _shop_on = _SHOPV['open'] and _MF['ob'] is not None
            _gate = _shop_on or _prep_gate
            # ★★ 2026-10-28：双方就绪、本局没买实体件 -> 调整界面（状态由
            #   MatchFlow.adjust 驱动；这里同步画面/按键状态并关商店）。
            _adj_sync(_MF['ob'])
            if _ADJ['on']:
                _shop_on = False
            # ★★ 2026-10-28 用户定案：局前可摆放/调整；开局（PLAY）锁定
            _phase = (_MF['ob'].phase if _MF['ob'] is not None else None)
            if _PBUY['on'] and _phase != 'PREP':
                _PBUY['on'] = False              # 离开局前：询问自动作废（道具保留）
            if _phase not in ('PLAY', 'PLACE') and _PHYS.get('lock'):
                _PHYS['lock'] = False
                _PHYS['lock_obs'] = []
                _PHYS['lock_ports'] = []
                _PHYS['lock_holes'] = []
                print('[实物] 局前：障碍/传送门解锁（可摆放/调整）', flush=True)
            # ★ 2026-10-25：开局/离开局前时，实体布置态自动取消（额度不扣）
            if (_PPLACE['on'] and _MF['ob'] is not None
                    and _MF['ob'].phase not in ('PREP', 'PLACE')):
                # ★ 2026-10-28 修：对局中摆放已改成 PLACE 暂停阶段，不能在这里
                #   被"离开局前"取消（此前就是这个守卫把临时摆放秒杀了）。
                _pplace_clear()
                _item_msg_set('当前阶段结束——布置取消（额度未扣）')
                print('[布置] 离开可布置阶段，实体布置自动取消（额度未扣）',
                      flush=True)
            # ★★ 2026-10-25 实体道具布置：局前（商店/入口画面）整段检测本来是
            #   跳过的（要光标丝滑），但摆放态必须看相机——按 0.3s 节拍只跑
            #   实物识别（黑条/绿门/黑球），够判"新件稳定出现了没"。
            if (_PPLACE['on'] or (_gate and _ADJ['on'])) and v3_mod is not None:
                _tp = time.time()
                _det_holder = _PPLACE if _PPLACE['on'] else _ADJ
                if _tp - float(_det_holder.get('t_det') or 0.0) >= PHYS_PLACE_DET:
                    _det_holder['t_det'] = _tp
                    try:
                        # ★★ 2026-10-28 用户实况（"全分辨率识别差、还出假球"）：
                        #   **回缩略图识别**（8x8 平均把木纹/阴影抹掉，稳定），
                        #   摆放态用 loose=True 放宽小门/短条阈值。
                        _mbp = map_to_bgr(buf, swap ^ mswap)
                        _pd = _po.detect(_mbp, loose=True)
                        # ★★ 2026-10-30 用户定案：加洞口**不复用障碍/传送门
                        #   识别链**——只跑专用黑球检测（独立映射/过滤），
                        #   不调 `_phys_update`，不碰 bars/greens/世界/锁定副本。
                        if (_PPLACE.get('on')
                                and _PPLACE.get('kind') == 'hole'):
                            _PPLACE['hballs'] = _hole_balls_from_pd(
                                _pd, v3_mod, v3_H)
                        else:
                            _phys_update(v3_world, _pd, v3_mod,
                                         v3_H, _OBST,
                                         balls=[(getattr(_t, 'x', 0),
                                                 getattr(_t, 'y', 0),
                                                 getattr(_t, 'r', 0))
                                                for _t in _tracks])
                        #   摆放/调整改用**全分辨率原图**识别——缩略图 1 块=8 相机
                        #   像素，一对小绿门只有几像素，形状/面积都在阈值边缘。

                        if _PPLACE['on']:
                            _pplace_check(_MF['ob'], _tp, v3_world)
                            # ★ 对局中临时摆放：识别后把世界恢复成锁定副本
                            #   （新件只在黄框里预览，确认那一刻才写进锁定）
                            if (_PHYS.get('lock')
                                    and _MF['ob'] is not None
                                    and _MF['ob'].phase in ('PLAY', 'PLACE')):
                                _phys_apply_lock(v3_world)

                    except Exception as _epp:
                        _PHYS['nerr'] = int(_PHYS.get('nerr', 0)) + 1
                        if _PHYS['nerr'] <= 3:
                            print('[布置] ★ 实物识别异常（第 %d 次）：%s'
                                  % (_PHYS['nerr'], str(_epp)[:120]),
                                  flush=True)
            # ★★ 2026-10-15 加洞口 v2（用户实况："鼠标大漂移，不好控制"）：HOLE
            #   暂停期**全场冻结**——不抓相机、不检测、不跟踪（球/画面都不动），
            #   大屏换**俯视瞄准图**（合成画面，光标 20+fps 丝滑）。手机 json 的
            #   0.5s 节拍照旧（见下面的 elif）。
            _hole_on = (_MF['ob'] is not None and _MF['ob'].phase == 'HOLE')
            _edit_on = (_MF['ob'] is not None and _MF['ob'].phase == 'EDIT')
            n_frames += 1
            _FT.append(time.time())
            if len(_FT) > 31:
                _FT.pop(0)
            _rawg = None
            # ★★ 2026-10-13 提速二刀（用户："还是卡卡的，不够丝滑"）：**商店画面
            #   开着时**——地图→检测→坏帧熔断→选球/轨迹——**整段不跑**（合成画面
            #   没有消费者；球位店里冻结、开局跟踪器自然重锁）。地图读取与救援
            #   看门狗仍在上方每帧跑（不受影响）；n_frames 提到门外保 fps 账。
            #   ★ 2026-10-13 三刀：**局前（PREP）也算**——入口画面改成合成后同样
            #   不需要相机，进店前的光标一起丝滑（_gate = 店里 or 局前）。
            if not _gate and not _hole_on and not _edit_on:
                mbgr = map_to_bgr(buf, swap ^ mswap)
                # ★ 检测异常不再让整轮长跑崩掉（2026-09-29 凌晨：一次 absdiff 尺寸不匹配
                #   就把 60 秒的跑直接带走 —— 演示现场这种死法最不该有）。
                #   第一次连 traceback 一起打（好定位），之后只打一行；次数进汇总。
                try:
                    if _plmd is not None and bg_map is None:
                        # 差距 1：PL 已做完 掩码->OPEN->8连通；PS 只复判
                        _stats, _covf, _lovf, _pseq = _plmd.read_candidates()
                        if _pseq < 0:
                            raise RuntimeError('PL 候选读不稳定（SEQ 一直变）')
                        _hsv = cv2.cvtColor(mbgr, cv2.COLOR_BGR2HSV)
                        det, _nrej = detect_map_from_stats(_stats, _hsv,
                                                           _ring_median_fast)
                    elif bg_map is not None:
                        det, _nrej = detect_map_bg(mbgr, bg_map, _ring_median_fast,
                                                   max_area=map_max_area)
                    else:
                        det, _nrej = detect_map(mbgr, _ring_median_fast,
                                                max_area=map_max_area)
                except Exception as e:
                    _DET['n_err'] += 1
                    if _DET['n_err'] <= 3:
                        print('[检测] ★ 异常（第 %d 次）：%s' % (_DET['n_err'], str(e)[:140]),
                              flush=True)
                        if _DET['n_err'] == 1:
                            import traceback
                            traceback.print_exc()
                    continue
                t2 = time.perf_counter()
                t_det.append(t2 - t1)
                # ★★ 2026-10-19 实物识别：黑长条=障碍 / 绿椭圆=传送门 / 黑球=洞口标记
                #   （~1-2ms/帧；稳定性 0.4s 才生效，坏帧被跟踪器自然滤掉）
                if v3_mod is not None:
                    _tph0 = time.perf_counter()
                    if _PHYS.get('lock'):
                        # PLAY：障碍/传送门锁定——不再做实物识别（也省一笔）
                        _phys_apply_lock(v3_world)
                    else:
                        try:
                            _phys_update(v3_world, _po.detect(mbgr), v3_mod,
                                         v3_H, _OBST,
                                         balls=[(getattr(_t, 'x', 0),
                                                 getattr(_t, 'y', 0),
                                                 getattr(_t, 'r', 0))
                                                for _t in _tracks])
                            # ★ 第一帧 PLAY：用这一帧的稳定结果锁死，之后不变
                            #   ★ 必须等**免费基线**锁定（base_ready）后再锁：
                            #     否则 world.obstacles 还是空的 -> 锁成 0 根（2026-10-28 实锤）
                            if (_phase == 'PLAY'
                                    and _PHYS.get('base_ready')):
                                _phys_lock(v3_world)
                        except Exception as _eph:
                            _PHYS['nerr'] = int(_PHYS.get('nerr', 0)) + 1
                            if _PHYS['nerr'] <= 3:
                                print('[实物] 识别异常（第 %d 次）：%s'
                                      % (_PHYS['nerr'], str(_eph)[:120]),
                                      flush=True)
                    _ms_add('phys', _tph0)

                # ---- ★ 坏帧熔断（2026-09-29 夜，接屏幕前补的一环）----
                #   昨晚那趟 20fps 的跑里"球"每帧跳 ±600px、颜色报 black —— 那不是球，
                #   是**半死链路/撕裂**送来的垃圾地图。这种帧要是喂进 trail 和 V3：图是乱的、
                #   结论是假的，而且它还是**最贵**的帧（候选越多 classify_map 越慢）。
                #   判据 = 候选数 + 相邻行差两个证据一起看（见 frame_sane_map 的注释）。
                #   ★ 熔断的帧**仍然算有效帧**（循环确实读+检了一帧，fps 账要诚实），
                #     只是它的球不可信：不进球、不进轨迹、不喂 V3。
                _fused = False
                _ok, _why, _rc = frame_sane_map(mbgr, len(det) + _nrej)
                _DET['rc'].append(_rc)
                if len(_DET['rc']) > 500:
                    _DET['rc'].pop(0)
                if not _ok:
                    _fused = True
                    _DET['n_fuse'] += 1
                    if _DET['n_fuse'] <= 3:
                        print('[检测] ★ 坏帧熔断（第 %d 次）：%s -> 这帧不进球/不进 V3'
                              % (_DET['n_fuse'], _why), flush=True)

                # ---- 游戏逻辑：取最大的红/蓝球当"粒子"，算位置/速度/轨迹 ----
                #   ★★ 2026-10-01 夜（用户第②点）：**只认场区内的球**。
                #   上一趟的实测：手机支架（raw 顶部那两条，r≈68~73）靠"面积最大"在
                #   38% 的时间里抢走了目标；它的游戏坐标在场区**外**（=那 113 次"球在
                #   场区外跳过"）。球是要摆在**边界上**的，所以判据不能是"严格在场内"，
                #   而是"在场区外扩 track_margin 像素的框里"（默认 20px 起步，可调）。
                #   折算走 cam_to_game —— 与 V3 判定同一份标定，不可能出现两套映射。
                #   ★ 只在 --v3 开启时过滤（没场区/没标定就无从判"场外"，行为不变）。
                # ★★ 2026-10-05：**多目标追踪**（用户："多颗实物球各自被追踪"）。
                #   老实现"每帧挑面积最大"没有时间关联——两颗球面积一波就来回跳
                #   （实测 ±121px、轨迹锯齿，见 ball_tracker.py 文件头）。现在：
                #   · （场区过滤后的）候选每帧喂 MultiTracker：预测-关联 → 稳定轨迹；
                #   · "主球"（喂发射/速度拟合/HUD 的那颗）= 离最近边最近的**已确认**轨迹；
                #   · 全部轨迹交给直播画面：各自画圈 + 各自拖尾（按颜色）。
                ball = None
                _tracks = []
                _cand = []
                if det and not _fused:
                    _cand = det
                    if v3_mod is not None:
                        _cand, _rej = _track_filter(
                            v3_mod, v3_H, det, track_margin,
                            getattr(v3_world, 'obstacles', None))
                        if not _cand:
                            # 候选全在场区外：这一帧就当"画面里没球"（但账要记下来，
                            # 前 3 次把最大那个候选的坐标喊出来 —— 好核对是不是支架/网线）
                            V3_STAT['n_trk_rej'] += 1
                            if V3_STAT['n_trk_rej'] <= 3 and _rej is not None:
                                _b, (_gx, _gy) = _rej
                                print('[跟踪] 候选 %d 个全在场区外（最大者在游戏 %.0f,%.0f，'
                                      'r=%.0f %s）-> 本帧没球（容差 %.0fpx，--track-margin 调）'
                                      % (len(det), _gx, _gy, _b['r'], _b.get('cls'),
                                         track_margin), flush=True)
                _ttr0 = time.perf_counter()
                _tracks = trk.update(_cand, time.time())      # 空帧也要走（miss 计数）
                # ★ 2026-11-01 用户规则：障碍物本体 = **禁止识别球区**——已经
                #   关联上的轨迹若落在障碍物里（黑条挡住/误跟），从渲染/选球
                #   列表剔除；tracker 内部保留，球出来还能认回原号。
                if (_cand is not None and v3_mod is not None
                        and getattr(v3_world, 'obstacles', None)):
                    _tracks = [
                        _tk for _tk in _tracks
                        if not _in_obstacle_game(
                            *v3_mod.cam_to_game(v3_H, _tk.sx, _tk.sy)[:2],
                            v3_world.obstacles)]
                _ms_add('trk', _ttr0)
                # ★★ 2026-10-07（用户："序号重排时屏幕时有跳动"实锤：贴边球闪断->留位
                #   进出->活球的号被整体推来推去；而玩家高亮/预览/发射都按**号**锚定，
                #   号一换主，玩家的圈就跳到别的球上）：**选球跟着球走**——重排事件
                #   (Track, 旧号, 新号) 里若有玩家正控的号，自动跟到该球的新号；写回
                #   状态文件（手机同步）+ hub/本地同帧跟手（发射块这帧就用新号）。
                if v3_hub is not None:
                    for _to, _on, _nn in trk.take_renumber():
                        for _pk3, _cur3 in ((1, v3_ball1), (2, v3_ball2)):
                            if _on == _cur3:
                                print('[选球] ★ 重排跟随：P%d 控球 %d -> %d（跟着球走）'
                                      % (_pk3, _on, _nn), flush=True)
                                try:
                                    _v3in_write(player=_pk3, ball=_nn, src='game',
                                                path=_v3in_path)
                                except Exception:
                                    pass
                                if _pk3 == 1:
                                    v3_hub.ball1 = _nn
                                    v3_ball1 = _nn
                                else:
                                    v3_hub.ball2 = _nn
                                    v3_ball2 = _nn
                # ★ 2026-10-05 夜：候选清单周期日志（15s 一行）——多球排查时 tail 一眼看全
                if time.time() - _DBG['t'] > 15.0:
                    _DBG['t'] = time.time()
                    print('[检测] 候选 %d 个: %s' % (len(_cand),
                          ' '.join('%s@(%.0f,%.0f)' % (c.get('cls'), c['x'], c['y'])
                                   for c in _cand[:6])), flush=True)
                # ★ 2026-10-06（用户："网页应该实时刷新有几个球的序号"）：把"场上现在
                #   有几号球（含颜色）"写出来给手机网页（动态选球按钮）。0.5s 限频、
                #   原子写、失败不抛（网页顶多少一行列表）。
                # ★★ 2026-10-16：节拍抽成 _phone_state_tick——正常检测路径与 HOLE
                #   冻结路径**共用同一份**（冻结期检测全跳过，只有这个节拍必须活着；
                #   两份实现不许存在——同"门变量只许一个来源"的教训）。
                if time.time() - _BALLS['t'] > 0.5:
                    _phone_state_tick(_MF['ob'], _MF['mod'], v3_world,
                                      _SHOPV['open'], live_balls, _tracks,
                                      v3_mod, v3_H)
                if _tracks:
                    # ★ 2026-10-23：主球优先**红/蓝**已确认轨迹（黑球=洞口标记/手影，
                    #   不该抢主球；否则 V3/轨迹会跟着黑球标记跳到边缘）
                    _conf = ([t for t in _tracks
                              if t.no is not None and t.cls in ('red', 'blue')]
                             or [t for t in _tracks if t.no is not None]
                             or [t for t in _tracks
                                 if t.confirmed and t.cls in ('red', 'blue')]
                             or [t for t in _tracks if t.confirmed] or _tracks)
                    if v3_mod is not None:
                        def _edge_dist(t):
                            _gx, _gy = v3_mod.cam_to_game(v3_H, t.x, t.y)[:2]
                            return min(_gx, v3_mod.FIELD_W - _gx,
                                       _gy, v3_mod.FIELD_H - _gy)
                        _prim = min(_conf, key=_edge_dist)
                    else:
                        _prim = _conf[0]                      # 已按 area 降序
                    ball = dict(x=_prim.sx, y=_prim.sy, r=_prim.sr, cls=_prim.cls)
                    n_hit += 1
                    # ★ 2026-10-23 实况（用户晃手）：手被当成假黑球，跟踪器跳到
                    #   远处 -> 历史轨迹把两次位置连成横跨屏幕的绿线。跳变即清空，
                    #   不让轨迹跨过"换目标"的那一跳。
                    if trail:
                        _jdx = ball['x'] - trail[-1][0]
                        _jdy = ball['y'] - trail[-1][1]
                        if _jdx * _jdx + _jdy * _jdy > 250.0 * 250.0:
                            trail.clear()
                    trail.append((ball['x'], ball['y'], ball['cls'], time.time()))
                    if len(trail) > trail_len:
                        trail.pop(0)

            elif (_hole_on or _edit_on) and time.time() - _BALLS['t'] > 0.5:
                # ★★ 2026-10-16 加洞口 v2：HOLE 冻结期的**最小节拍**——检测/跟踪
                #   全跳过（全场冻结、画面不动，球位用冻结前快照），只保持手机
                #   json 活着（手机俯视图 + 状态栏 0.5s 一刷，不刷就"死了"）。
                _phone_state_tick(_MF['ob'], _MF['mod'], v3_world,
                                  _SHOPV['open'], live_balls, _tracks,
                                  v3_mod, v3_H)

            # ---- ★ 2026-10-06 M2.1：**选球键**（P1=Z/X、P2=,/.；循环控球编号）----
            #   读上升沿（与 fire 同口径，文件重建只对基准）；在**当前场上的号**
            #   里循环（-1=上一颗 / +1=下一颗，回绕）；结果写回状态文件（手机页面
            #   同步显示）+ 本地立即生效；没有号（场上无球）时忽略。
            if v3_hub is not None:
                for _pk, _up, _dn in ((1, v3_hub.sup1, v3_hub.sdn1),
                                      (2, v3_hub.sup2, v3_hub.sdn2)):
                    for _lk, _cnt, _dlt in (('sup%d' % _pk, _up, 1),
                                            ('sdn%d' % _pk, _dn, -1)):
                        if _cnt < _SEL[_lk]:
                            _SEL[_lk] = _cnt            # 文件重建：只对基准
                            continue
                        if _cnt == _SEL[_lk]:
                            continue
                        _SEL[_lk] = _cnt
                        # ★ 2026-10-29：改磁场输数值时，'.' 是小数点
                        if (_MF['ob'] is not None
                                and _MF['ob'].phase == 'EDIT'):
                            continue
                        _nums = sorted(int(t.no) for t in _tracks
                                       if t.no is not None)
                        if not _nums:
                            continue
                        _cur = v3_hub.ball1 if _pk == 1 else v3_hub.ball2
                        _ix = _nums.index(_cur) if _cur in _nums else 0
                        _new = _nums[(_ix + _dlt) % len(_nums)]
                        if _new == _cur:
                            continue
                        try:
                            _v3in_write(player=_pk, ball=_new, src='key',
                                        path=_v3in_path)
                        except Exception as _ew:
                            # ★ 不许静默（2026-10-06 教训：就是静默吞异常让
                            #   "选球永远停在 1"查了半天）——首次大声报，之后安静。
                            if not _SEL.get('warned'):
                                _SEL['warned'] = True
                                print('[选球] ★ 写回状态文件失败（仅提醒一次）：%s'
                                      % str(_ew)[:140], flush=True)
                        # ★★ 2026-10-06（"切到3球发射却不是3球的位置"的根因）：
                        #   发射块读的是**循环开头**拍的快照 `v3_ball1/2`——而"切球→
                        #   马上按发射"间隔常小于一帧（当前 ~650ms/帧），两者落进同
                        #   一帧时，发射还在用帧头的旧号。这里**同帧同步**本地快照。
                        if _pk == 1:
                            v3_hub.ball1 = _new
                            v3_ball1 = _new
                        else:
                            v3_hub.ball2 = _new
                            v3_ball2 = _new
                        print('[选球] P%d 控球 %s -> %s（键盘）'
                              % (_pk, _cur, _new), flush=True)

            # ---- ★★ M2.2 局状态机推进（--match）-----------------------------
            #   30s 洞口计时 / 结算 2.6s→下一局 / 平局加赛 / 暂停超时；事件用
            #   drain() 取走：round_start = **清场**（规则书 §3"本局结束场上剩余
            #   粒子全部清场"）。dt 卡顿后封顶 0.5s（别让一觉醒来跳三局）。
            if _MF['ob'] is not None:
                _mnow = time.time()
                if _MF['t'] is None:
                    _MF['t'] = _mnow
                _mdt = min(0.5, _mnow - _MF['t'])
                _MF['t'] = _mnow
                if _mdt > 0:
                    _MF['ob'].update(_mdt)
                for _mev in _MF['ob'].drain():
                    if _mev[0] == 'round_start':
                        # ★★ 2026-11-01 用户规则订正：**实球跨局保留**（v5 的
                        #   "在飞续局"恢复）——上一局还没出射的球冻结在场上，
                        #   新局从中接着飞（LiveBall.who 记发球人，进球仍算他的分）。
                        #   只清掉已死/出射残影；洞口随机的前视否决仍用 live_balls
                        #   （v5 同一套：在飞球可能正压着新洞位）。
                        _nold = len(live_balls)
                        live_balls[:] = [b for b in live_balls if b.alive]
                        _nkeep = len(live_balls)
                        _STEP['t'] = None               # 真球计时基准同步重置
                        _REV['img'] = None              # 复盘图不留到下一局
                        _REV['until'] = 0.0
                        _REV['prev'] = False
                        for _k in _HITS:                # ★ M2.4：落点账"本局"口径
                            _HITS[_k].clear()
                        # ★★ 2026-10-07（规则书 §1："第 n 局 n 块、封顶 9"）：本局按
                        #   局数**重建磁场分块**——PL 场表 NBLK=9 早就在位（纯软件
                        #   重写场景，不动 RTL）；b_fix=±0.4 隔块交替沿用演示口径，
                        #   seed 固定 = 同 n 的布局可复现。真球物理每帧、PL 预览每次
                        #   扫描、画面都从 v3_world **现取**——三处自动同步。必须在
                        #   洞口随机**之前**重建（新世界的洞才是要随机的对象）。
                        _nb = min(int(_mev[1]), 9)
                        v3_world = _norm_holes(
                            _vs.build_world(v3_seed, v3_b, n_blocks=_nb))
                        v3_world.obstacles = _OBST    # ★ 布置 PLACE：障碍"永久"跨局保留
                        print('[对局] 本局磁场：%d 块（±%.2f 隔块交替）'
                              % (len(v3_world.blocks), v3_b), flush=True)
                        # ★ 用户 2026-10-07："每一局的洞口希望是随机的"（规则书 §2
                        #   本就是"电脑设置本局磁场与洞口"）。掷新位 -> 2 秒前视
                        #   否决（v5 续局的在飞球可能正压着新洞位——被否就保留原值）
                        #   -> 应用并记"醒目动画"（与 30s 挪步同一套视觉）。
                        _flds3 = [(bb.x0, bb.y0, bb.x1, bb.y1, bb.b)
                                  for bb in v3_world.blocks]
                        _rp = _ha.random_positions(
                            v3_world.holes, v3_mod.FIELD_W, v3_mod.FIELD_H,
                            getattr(v3_mod, 'CORNER_R', 0.0))
                        _rj = []
                        for _hi, _nc in _rp:
                            _cand = [dict(h) for h in v3_world.holes]
                            _cand[_hi]['c'] = _nc
                            if _ha.would_score_within(live_balls, _cand, _flds3):
                                continue
                            _old = v3_world.holes[_hi]['c']
                            v3_world.holes[_hi]['c'] = _nc
                            _rj.append((v3_world.holes[_hi]['edge'], _old, _nc))
                        if _rj:
                            _HMOVE['t'] = time.time()
                            _HMOVE['items'] = _rj
                            print('[对局] 本局洞口随机：%s' % '、'.join(
                                '%s %.0f->%.0f' % (e, o, n) for e, o, n in _rj),
                                flush=True)
                        print('[对局] ★★ 第 %d 局开始（续局：保留上一局 %d 颗实球，清掉 %d 颗死球）%s'
                              % (_mev[1], _nkeep, _nold - _nkeep,
                                 '（加赛）' if _mev[2] == 'tiebreak' else ''),
                              flush=True)
                    elif _mev[0] == 'place_timeout':
                        if _PPLACE['on']:
                            _pplace_clear()
                            _item_msg_set('摆放超时（30秒）——已取消（额度未扣）')
                            print('[布置] 对局中摆放超时（30s）——取消（额度未扣）',
                                  flush=True)
                    elif _mev[0] == 'match_end':
                        print('[对局] ★★★ 比赛结束：%s' % _MF['ob'].result_text(),
                              flush=True)
                    elif _mev[0] == 'ammo_done':
                        print('[对局] 双方弹药打光——等场上球落定 %d 秒'
                              '（期间进球照算）' % int(_MF['mod'].GRACE_SECS),
                              flush=True)
                    elif _mev[0] == 'round_over':
                        _nfly = sum(1 for b in live_balls if b.alive)
                        print('[对局] 本局结算%s' % (
                            '；%d 颗球还在飞 -> 下一局原轨迹续飞' % _nfly
                            if _nfly else ''), flush=True)
                    elif _mev[0] == 'hole_due':
                        if _PHYS.get('mark_on'):
                            # ★ 2026-10-19：桌上有黑洞标记 -> 洞位置由实物说了算，
                            #   自适应让位（清落点账，保持"每 30s 重新计"的口径）。
                            print('[对局] 黑洞标记在场：洞自适应让位（不动洞）',
                                  flush=True)
                            for _k5 in _HITS:
                                _HITS[_k5] = []
                            continue
                        # ★★ M2.4（规则书 §8）：30 秒无进球 -> 洞口朝**该侧落点热点**
                        #   移动一小步。计划+边界/重叠约束在 hole_adapt.pick_moves；
                        #   逐条做 **2 秒前视否决**（会让在飞真球直接进洞的移动作废——
                        #   克隆步进，与真球物理逐位同源）。移动 = 改
                        #   `v3_world.holes[i]['c']`：真球每帧吃同一份 dict、PL 预览
                        #   每次扫描都 write_scene 重写洞表、画面直读世界——三处自动
                        #   同步。**无论移没移，本段落点账清空**（engine 同款："本局
                        #   累计"重新从这 30 秒算起）。
                        _flds2 = [(bb.x0, bb.y0, bb.x1, bb.y1, bb.b)
                                  for bb in v3_world.blocks]
                        _n_hits = '/'.join('%s%d' % (_k, len(_HITS[_k]))
                                           for _k in ('left', 'right'))
                        _mv = _ha.pick_moves(v3_world.holes, _HITS,
                                             v3_mod.FIELD_W, v3_mod.FIELD_H,
                                             getattr(v3_mod, 'CORNER_R', 0.0))
                        _moved, _veto = [], 0
                        _flash = []
                        for _hi, _nc in _mv:
                            _cand = [dict(h) for h in v3_world.holes]
                            _cand[_hi]['c'] = _nc
                            if _ha.would_score_within(live_balls, _cand, _flds2):
                                _veto += 1
                                continue
                            _old = v3_world.holes[_hi]['c']
                            v3_world.holes[_hi]['c'] = _nc
                            _moved.append('%s->%.0f' % (v3_world.holes[_hi]['edge'], _nc))
                            _flash.append((v3_world.holes[_hi]['edge'], _old, _nc))
                        if _flash:                      # ★ 醒目动画（3 秒）
                            _HMOVE['t'] = time.time()
                            _HMOVE['items'] = _flash
                        for _k in _HITS:
                            _HITS[_k].clear()
                        print('[对局] 30s 无进球 -> 洞口%s（落点 %s；计划 %d，前视否决 %d）'
                              % ('移动：' + '、'.join(_moved) if _moved else '评估后不动',
                                 _n_hits, len(_mv), _veto), flush=True)
                    elif _mev[0] == 'edit_timeout':
                        # ★ 2026-10-29 三步确认：match_flow 只在 VALUE 阶段自动
                        #   确认（end_edit(confirm=True)）；SELECT/DIR 超时=取消、
                        #   不扣额度——这里只负责把输入的数值落到块。
                        _done2 = bool(_mev[2]) if len(_mev) > 2 else True
                        if _done2:
                            try:
                                if _MF['ob'].edit.get('stage') == 'VALUE':
                                    _bv2 = _edit_apply_value(_MF['ob'], v3_world)
                                    print('[对局] ★ P%d 改磁场超时——按输入值生效 '
                                          'B=%+.2f' % (_mev[1], _bv2 or 0.0),
                                          flush=True)
                            except Exception:
                                pass
                            _item_msg_set('改磁场超时——按输入值生效')
                        else:
                            _item_msg_set('改磁场超时未完成——本次取消（不扣额度）')
                        _MAS['snap'] = None
                        _edit_restore()
                        print('[对局] ★ P%d 改磁场暂停超 30s，%s'
                              '（规则书 §7.1-5）'
                              % (_mev[1], '自动确认恢复' if _done2
                                 else '未到数值阶段 -> 取消，不扣额度'),
                              flush=True)
                    elif _mev[0] == 'hole_timeout':
                        # ★ 2026-10-14（规则书 §7）：加洞口 30s 上限。超时怎么收
                        #   由这里定夺。★★ 2026-10-16 收紧（用户实况："按 ESC 退出
                        #   后也算次数了？"）：**只有"确认过位置"（confirm=True——
                        #   点击过/手机点过）才自动放置（扣额度）**；只是晃了鼠标
                        #   从没点过、或位置不合法 -> 一律取消、**不扣**。
                        #   两种结局都上横幅 + 回手机——此前超时放置是**静默**的，
                        #   "次数怎么没了"根本没处问。
                        _hk2 = _MF['ob']
                        _who2 = _hk2.hole['player']
                        _ed4, _ps4 = (_hk2.hole['edge'], float(_hk2.hole['pos']))
                        _fine2 = (_hk2.hole.get('stage') == 'FINE')
                        if (_HOLEV['confirm'] and _fine2
                                and _hole_valid(v3_world, _ed4, _ps4)):
                            _hole_commit(_hk2, v3_world)
                            _item_msg_set('加洞口超时（30秒）——已自动放置'
                                          '（%s %.0f）' % (_ed4, _ps4))
                            print('[道具] ★ 30s 超时自动放置（P%d %s %.0f）'
                                  % (_who2, _ed4, _ps4), flush=True)
                            _shot_write(dict(t=round(time.time(), 3), ok=True,
                                             why='item', kind='item', seq=0,
                                             msg='P%d 加洞口(%s %.0f)'
                                                 % (_who2, _ed4, _ps4)),
                                        player=_who2)
                        else:
                            _hk2.end_hole(cancel=True)
                            _item_msg_set('加洞口超时（30秒）——已取消，没有扣额度')
                            print('[道具] ★ 30s 超时取消（%s）'
                                  % ('位置放不下' if _fine2
                                     else '只晃了鼠标、没点过位置'), flush=True)
                            _shot_write(dict(t=round(time.time(), 3), ok=True,
                                             why='item', kind='item', seq=0,
                                             msg='P%d 加洞口超时取消（没扣额度）'
                                                 % _who2), player=_who2)
                        _holev_clear()

            # ---- ★★ 2026-10-08 局前选购（道具系统 v1，规则书 §6.2）------------
            #   同键双语义：W/S/↑↓ 局中是角度键、**局前=选单上下**（v3_input 记
            #   p{N}_mup/mdn 计数器）；E / 小键盘*=购买。这里读**上升沿**（计数器
            #   变低=文件重建，只对基准）；结果播报 + 写 shot.json（手机"最近一杆"
            #   可见）。动作本身走纯函数 _shop_apply（台架可测）。
            if _MF['ob'] is not None and v3_hub is not None:
                for _pk, _evs in ((1, (('mup', v3_hub.mup1), ('mdn', v3_hub.mdn1),
                                       ('buy', v3_hub.buy1))),
                                  (2, (('mup', v3_hub.mup2), ('mdn', v3_hub.mdn2),
                                       ('buy', v3_hub.buy2)))):
                    for _act, _cnt in _evs:
                        _k = '%s%d' % (_act, _pk)
                        if _cnt < _SHOP[_k]:
                            _SHOP[_k] = _cnt              # 文件重建：只对基准
                            continue
                        if _cnt == _SHOP[_k]:
                            continue
                        _SHOP[_k] = _cnt
                        # ★★★ 2026-10-14 修（10-13 提速时埋的基准 staleness bug）：
                        #   相位门必须放在**基准更新之后**——否则局中按 E/W 不更新
                        #   基准，计数器居高不下，回到局前第一帧会被当成一次新按键
                        #   **误买/误挪**。这里 _SHOP[_k] 已在上面无条件对齐。
                        if _MF['ob'].phase != 'PREP':
                            continue
                        # ★★ 2026-10-28（第 3 条）：购买询问未答复时，只认
                        #   "买家再按一次购买键"= 立即摆放；其余按键先搁置。
                        if _PBUY['on']:
                            if (_act == 'buy' and _pk == _PBUY['who']
                                    and _cnt > int(_PBUY['b'])):
                                _pbu_resolve(_MF['ob'], True)
                            continue
                        # ★ 2026-10-27 实体布置态：E 走下面 cfm 确认链，W/S 不导航商店
                        #   （这里只消费基准，防 E 同时误买）。
                        if _PPLACE['on']:
                            continue
                        # ★ 2026-10-28 调整界面：E/小键盘* 是「开始本局」，
                        #   走下面 cfm 链；这里只消费基准，不重开商店。
                        if _ADJ['on']:
                            continue
                        # ★ 2026-10-19 布置 PLACE v1：摆放态里 W/S=**旋转**、E=确定
                        #   （E 的消费统一走下面 cfm 链——这里只拦商店动作，防双吃）。
                        if _OBSP['on']:
                            if _OBSP['who'] == _pk and _act in ('mup', 'mdn'):
                                _dsp = (OBS_ANG_STEP if _act == 'mup'
                                        else -OBS_ANG_STEP)
                                _OBSP['ang'] = (_OBSP['ang'] + _dsp) % 180.0
                                _item_msg_set('障碍角度 %.0f°（绿=可放/红=放不下）'
                                              % _OBSP['ang'])
                            continue
                        if not _SHOPV['open']:
                            if _act == 'buy':
                                _SHOPV['open'] = True
                                print('[商店] 进入商店画面（P%d 按了购买键）' % _pk,
                                      flush=True)
                            continue
                        _shopmsg = _shop_apply(_MF['ob'], _pk, _act)
                        if _shopmsg:
                            print('[选购] P%d %s：%s' % (_pk, _act, _shopmsg),
                                  flush=True)
                            _shot_write(dict(t=round(time.time(), 3), ok=True,
                                             why='shop', kind='shop', seq=_cnt,
                                             msg='P%d %s' % (_pk, _shopmsg)),
                                        player=_pk)
                        # ★ 2026-10-28 用户定案（第 3 条）：买完先问要不要现在用
                        if _act == 'buy':
                            _shop_ask_use(_MF['ob'], _pk, _cnt,
                                          getattr(v3_hub, 'esc', 0))
                # ---- ★★ 2026-10-25 实体道具布置：V/小键盘- = 障碍；G/小键盘7 = 传送门 ----
                #   用户定案：摆的是**实体件**（相机识别）。局前买道具 -> 按键进入
                #   摆放 -> 实体黑条/一对绿门稳定识别 -> 扣 1 次并生效（永久）；
                #   ESC 取消不扣。开局就在桌上的实体件是免费基线，不受这个门限制。
                #   再按同一个键 = 退出（不扣额）。
                for _pk, _cnt2, _kind, _kn in (
                        (1, getattr(v3_hub, 'obs1', 0), 'obstacle', 'V'),
                        (2, getattr(v3_hub, 'obs2', 0), 'obstacle', '小键盘-'),
                        (1, getattr(v3_hub, 'ppl1', 0), 'portal', 'G'),
                        (2, getattr(v3_hub, 'ppl2', 0), 'portal', '小键盘7')):
                    _k = ('obs%d' if _kind == 'obstacle' else 'ppl%d') % _pk
                    if _cnt2 < _SHOP[_k]:
                        _SHOP[_k] = _cnt2                   # 文件重建：只对基准
                        continue
                    if _cnt2 == _SHOP[_k]:
                        continue
                    _SHOP[_k] = _cnt2
                    # ★ 2026-10-29：改磁场输数值时，小键盘 7/8 是数字 7/8
                    if (_MF['ob'] is not None
                            and _MF['ob'].phase == 'EDIT'
                            and _MF['ob'].edit.get('stage') == 'VALUE'):
                        continue
                    _cn = '障碍' if _kind == 'obstacle' else '传送门'
                    if _MF['ob'].phase not in ('PREP', 'PLAY'):
                        # ★ 2026-10-28（第 3 条）：对局中也能临时加；只有暂停/
                        #   结算阶段不能摆。
                        print('[布置] P%d 按 %s：当前阶段不能摆放——道具保留'
                              % (_pk, _cn), flush=True)
                        _item_msg_set('当前阶段不能摆放——道具保留')
                        continue
                    if _PPLACE['on']:
                        if _PPLACE['who'] == _pk \
                                and _PPLACE['kind'] == _kind:
                            _pplace_clear()
                            _item_msg_set('已退出布置（额度未扣）')
                            print('[布置] P%d 退出%s摆放（额度未扣）'
                                  % (_pk, _cn), flush=True)
                        continue
                    _okp, _pm = _pplace_enter(_MF['ob'], _pk, _kind)
                    print('[布置] %s' % _pm, flush=True)
                    _item_msg_set(_pm)
                    if _okp:
                        print('[布置] P%d 按 %s 进入%s摆放（实体件识别）'
                              % (_pk, _kn, _cn), flush=True)

                # ---- ★★ 商店 v2：手机「进入商店」键 + 鼠标左键 ------------------
                for _pk, _sc in ((1, getattr(v3_hub, 'sopen1', 0)),
                                 (2, getattr(v3_hub, 'sopen2', 0))):
                    _k = 'sopen%d' % _pk
                    if _sc < _SHOP[_k]:
                        _SHOP[_k] = _sc
                        continue
                    if _sc == _SHOP[_k]:
                        continue
                    _SHOP[_k] = _sc
                    if _MF['ob'].phase == 'PREP' and not _SHOPV['open']:
                        _SHOPV['open'] = True
                        print('[商店] 进入商店画面（手机 P%d 按了"进入商店"）' % _pk,
                              flush=True)
                _mc = int(getattr(v3_hub, 'mbtn', 0) or 0)
                if _mc < _SHOP['mbtn']:
                    _SHOP['mbtn'] = _mc                   # 文件重建：只对基准
                elif _mc > _SHOP['mbtn']:
                    _SHOP['mbtn'] = _mc
                    # ★★ 2026-10-27 实体布置点选：布置态左键先归它（点黑条锁定），
                    #   不消费才走洞口/虚拟障碍/商店。
                    _hit = None
                    if _pbuy_mouse_click(v3_hub.mx, v3_hub.my,
                                         v3_pump.width, v3_pump.height,
                                         _MF['ob']):
                        pass
                    elif _PPLACE['on']:
                        _pplace_mouse_click(v3_hub.mx, v3_hub.my, v3_H,
                                            v3_pump.width, v3_pump.height,
                                            crop_margin)
                    # ★★ 2026-10-15 加洞口 v2：这一笔先给洞口流程（弹窗按钮 /
                    #   定住位置）；没消费（不在 HOLE）才走商店命中框。
                    #   相位互斥（HOLE vs PREP），两边不会打架。
                    elif _edit_mouse_hit(_MF['ob'], v3_hub,
                                         v3_pump.width, v3_pump.height,
                                         v3_world):
                        pass
                    elif _hole_mouse_click(_MF['ob'], v3_hub, v3_pump, v3_H,
                                           v3_mod, v3_world,
                                           _MF['mod']) is None:
                        if not _obsp_mouse_click(v3_hub, v3_world):
                            _hit = _shop_click(v3_hub.mx, v3_hub.my,
                                               _SHOPV['rects'])
                    if _hit == 'ent':
                        if _MF['ob'].phase == 'PREP':
                            _SHOPV['open'] = True
                            print('[商店] 进入商店画面（鼠标点击入口）', flush=True)
                    elif _hit and _hit[0] == 'p' and _SHOPV['open'] \
                            and _MF['ob'].phase == 'PREP':
                        _pk2, _rest = int(_hit[1]), _hit[2:]
                        if _rest.startswith('_it'):
                            if _MF['ob'].sub[_pk2] == 'SHOP':
                                _MF['ob'].sel_item[_pk2] = int(_rest[3:])
                                print('[选购] P%d 点选：%s'
                                      % (_pk2, _MF['ob'].shop_label(_pk2)),
                                      flush=True)
                        elif _rest == '_buy':
                            _msg = _shop_apply(_MF['ob'], _pk2, 'buy')
                            if _msg:
                                print('[选购] P%d 点买：%s' % (_pk2, _msg),
                                      flush=True)
                                _shot_write(dict(t=round(time.time(), 3),
                                                 ok=True, why='shop',
                                                 kind='shop', seq=_mc,
                                                 msg='P%d %s' % (_pk2, _msg)),
                                            player=_pk2)
                            # ★ 2026-10-28（第 3 条）：买完实体件先问要不要现在用
                            _shop_ask_use(_MF['ob'], _pk2,
                                          v3_hub.buy1 if _pk2 == 1
                                          else v3_hub.buy2,
                                          getattr(v3_hub, 'esc', 0))
                        elif _rest == '_done':
                            _MF['ob'].ready(_pk2)
                            _both2 = (_MF['ob'].phase == 'PLAY')
                            _adj3 = bool(getattr(_MF['ob'], 'adjust', False))
                            _why3 = ('双方就绪，开局！' if _both2
                                     else ('双方就绪——进入调整界面' if _adj3
                                           else '等对方就绪'))
                            print('[对局] P%d 点「完成」= 就绪（%s）'
                                  % (_pk2, _why3), flush=True)
                            _shot_write(dict(t=round(time.time(), 3), ok=True,
                                             why='ready', kind='ready',
                                             seq=_mc,
                                             msg=('P%d 已就绪——%s' % (
                                                 _pk2, _why3)),
                                        player=_pk2))
            # ★ 开局（含加赛局）自动关商店画面；非对局/回放也关
            if _SHOPV['open'] and (_MF['ob'] is None
                                   or _MF['ob'].phase != 'PREP'):
                _SHOPV['open'] = False

            # ---- ★★ 发射模式（2026-10-01 深夜，用户①②③三条）----------------
            #   输入层开着就是发射模式：
            #     · 平时**一帧都不预测**（下面那个周期预测块被 `v3_hub is None` 挡掉）；
            #     · 手机"发射"（大按钮）/ 键盘空格 -> 状态文件 fire 计数 +1 ->
            #       这里读**上升沿**发一杆：从**球当前所在的那条边**出发、方向 =
            #       边法线 + 输入角（规则表 v4：入射角以边界法线为 0°）。★★ 2026-10-05
            #       规则订正（用户定案）：**发射 = 真球入场**——球留场里一直飞，撞边界
            #       镜面反射，弹到出射（进洞）为止；多球共存、球球弹性反弹（规则书 §3）。
            #       物理 = realtime_ball，与瞄准预览 lane_fixed **逐位一致**。
            #     · ★★ 2026-10-28 用户定案：取消"区域中间拒绝"与紫色可发射带；
            #       球在红色场区内任意位置都可识别/初始/发射（球身压场区即可），
            #       方向仍 = 最近那条边的法线 + 输入角。
            #   ★ 冷却 1.5s：连点不许把游戏循环打穿（跟踪才是主线）。
            #   ★ 计数**变低**（文件被重建：serve 重启/换路径）= 只把基准对回去，
            #     **绝不**当成一次发射（宁可不发，也不发一杆莫名其妙的）。
            if v3_hub is not None:
                # ★★ 2026-10-05 双人（用户："网页改双人控制"）：两个玩家**各自读
                #   上升沿**（P1=fire / P2=fire2），各自用**自己那颗球**（编号 1/2
                #   的轨迹）+ 自己的角度/速度；冷却、拒因、结果全按玩家分开记。
                for _pk, _fcnt, _pang, _pspd, _pbno in (
                        (1, v3_hub.fire, v3_angle, v3_speed, v3_ball1),
                        (2, v3_hub.fire2, v3_angle2, v3_speed2, v3_ball2)):
                    _lk = 'last%d' % _pk
                    if _fcnt < _FIRE[_lk]:
                        _FIRE[_lk] = _fcnt               # 文件重建：只对基准，不发射
                        continue
                    if _fcnt == _FIRE[_lk]:
                        continue
                    _FIRE[_lk] = _fcnt
                    _now_f = time.time()
                    # ★★ M2.2：局前（PREP）里双方各自的发射键 = **就绪**键
                    #   （规则书 §6.2"双方都按才开局"；空格/回车本来就在手上，
                    #   局前本来也不许发射 —— 语义不冲突）。
                    if _MF['ob'] is not None and _MF['ob'].phase == 'PREP':
                        # ★★ 2026-10-28：调整界面里，发射键 = 「开始本局」
                        #   （但先把手上这次摆放收尾——别把布置态带进对局）
                        if _ADJ['on']:
                            if _PPLACE['on']:
                                _item_msg_set('先完成/退出这次摆放，再开始本局')
                                continue
                            _adj_start(_MF['ob'], _pk, v3_world)
                            continue
                        _MF['ob'].ready(_pk)
                        _both = (_MF['ob'].phase == 'PLAY')
                        _adj2 = bool(getattr(_MF['ob'], 'adjust', False))
                        if _both:
                            _why2 = '双方就绪，开局！'
                        elif _adj2:
                            _why2 = ('双方就绪——调整障碍/传送门后按 '
                                     'E / 小键盘* 开始本局')
                        else:
                            _why2 = ('等对方按「%s」就绪'
                                     % ('空格' if _pk == 1 else '回车'))
                        print('[对局] P%d 就绪（%s）' % (_pk, _why2), flush=True)
                        # ★ 2026-10-07 上板实玩发现（"按了发射没反应"的真身 = 局前里
                        #   发射键变成就绪键、却没有任何**玩家可见**的反馈）：就绪也写
                        #   一笔 shot.json —— 手机"最近一杆"直接显示谁已就绪、等谁。
                        _shot_write(dict(
                            t=round(_now_f, 3), ok=True, why='ready',
                            kind='ready', seq=_FIRE['n_req'],
                            msg=('P%d 已就绪——%s' % (_pk, _why2)),
                            player=_pk))
                        continue
                    if _now_f - _FIRE['t_last%d' % _pk] < 1.5:
                        _FIRE['n_cool'] += 1
                        print('[发射] P%d 距上一杆不足 1.5s -> 忽略这次点击' % _pk,
                              flush=True)
                        continue
                    _FIRE['n_req'] += 1
                    _pt = next((t for t in _tracks
                                if getattr(t, 'no', None) == _pbno), None)
                    _pball = (dict(x=_pt.sx, y=_pt.sy, r=_pt.sr, cls=_pt.cls)
                              if _pt is not None else None)
                    # ★★ M2.2 对局口径（--match）：暂停/结算中不许发；弹药按
                    #   **实物球颜色**（相机判色）扣 —— 没弹药也走同一条拒绝报告路
                    #   （控制台 + shot.json），玩家在手机上能看到为什么。
                    _mchg = (_rtb.charge_of(_pball['cls'])
                             if _pball is not None else 0)
                    if _MF['ob'] is not None:
                        _mok, _mwhy = _MF['ob'].can_fire(_pk, _mchg)
                        if not _mok:
                            _FIRE['n_' + _mwhy] += 1
                            _minfo = {'no_ammo': True,
                                      'red': _MF['ob'].ammo[_pk][1],
                                      'blue': _MF['ob'].ammo[_pk][-1]}
                            _m = ('P%d：' % _pk
                                  + _fire_msg(_mwhy, _minfo, fire_margin))
                            print('[发射] P%d #%d ★ 拒绝（%s）：%s'
                                  % (_pk, _FIRE['n_req'], _mwhy, _m), flush=True)
                            _shot_write(dict(t=round(_now_f, 3), ok=False,
                                             why=_mwhy, msg=_m,
                                             seq=_FIRE['n_req'],
                                             ang=round(_pang, 2),
                                             spd=round(_pspd, 1)),
                                        player=_pk)
                            continue
                    _okf, _why, _info = _fire_gate(v3_mod, v3_H, _pball,
                                                   _pang, fire_margin)
                    if not _okf:
                        _FIRE['n_' + _why] += 1
                        # ★ 2026-10-05 夜（"控球3按发射没反应"）：noball 有两半 ——
                        #   ①场上真没球（老文案对）②场上有球、但没有你选的这个
                        #   编号（文案必须说清"N 号不在、现在场上是几号"，老文案
                        #   还叫人"把球摆到边界上"，对第②种纯误导）。补 ② 的路标。
                        _info2 = _info
                        if _why == 'noball' and _tracks:
                            _info2 = dict(_info)
                            _info2['no_such'] = _pbno
                            _info2['have'] = sorted(int(t.no) for t in _tracks
                                                    if t.no is not None)
                        _m = ('P%d：' % _pk
                              + _fire_msg(_why, _info2, fire_margin))
                        print('[发射] P%d #%d ★ 拒绝（%s）：%s'
                              % (_pk, _FIRE['n_req'], _why, _m), flush=True)
                        _shot_write(dict(t=round(_now_f, 3), ok=False,
                                         why=_why, msg=_m,
                                         seq=_FIRE['n_req'],
                                         ang=round(_pang, 2),
                                         spd=round(_pspd, 1)),
                                    player=_pk)
                    else:
                        _FIRE['t_last%d' % _pk] = _now_f
                        _FIRE['n_ok'] += 1
                        # 发射 = 真球入场（2026-10-05 规则订正；与预览 lane_fixed
                        # 逐位一致，见 realtime_ball.py）
                        _b0 = _rtb.LiveBall(_info['bx'], _info['by'],
                                            _info['ang'], _pspd,
                                            seq=_FIRE['n_req'],
                                            charge=_rtb.charge_of(_pball['cls']),
                                            who=_pk)
                        if _MF['ob'] is not None:
                            _MF['ob'].consume(_pk, _mchg)   # ★ M2.2 按实物球颜色扣弹药
                        live_balls.append(_b0)
                        _LIVE_ST['n_fired'] += 1
                        _m = ('P%d（控球%d）#%d 在%s侧发射，角 %.1f°（0=右/逆时针）：'
                              '**真球已进场**（弹到出射为止；场上 %d 颗）'
                              % (_pk, _pbno, _FIRE['n_req'], _info['edge'],
                                 _info['ang_ui'], len(live_balls)))
                        if _info.get('snap'):
                            _m += '（球贴在线上，起点已自动收进边内）'
                        print('[发射] ★ %s' % _m, flush=True)
                        _shot_write(dict(
                            t=round(_now_f, 3), ok=True, why='', msg=_m,
                            seq=_FIRE['n_req'], edge=_info['edge'],
                            ang=round(_info['ang_ui'], 2),
                            spd=round(_pspd, 1),
                            bx=round(_info['bx'], 1),
                            by=round(_info['by'], 1),
                            d=round(_info['d'], 1), kind='live'),
                            player=_pk)

            # ---- ★★ 2026-10-14 局中道具（规则书 §6.4/§7.1）：改磁场 / 加洞口 ----
            #   发起 Q·小键盘. / C·小键盘+；ESC 取消；确认复用 E/小键盘*（buy 计数
            #   同键双语义：局前=购买、EDIT/HOLE=确认）；调参复用 W/S·↑↓（nav=方向）
            #   与 A/D·←→（nav2=强度/位置）。冻结/禁发射由 match 相位自带。
            if _MF['ob'] is not None and v3_hub is not None:
                _mob2 = _MF['ob']

                def _mas_ev(_k, _cnt):
                    """计数器上升沿（文件重建只对基准）。True=新一笔。

                    ★ 2026-10-25 第二次同款事故（aball1 KeyError 直接把游戏带崩）：
                      这里改用 _MAS.get(_k, 0)，**新增计数器忘了登记也不会崩**
                      （第一笔按 0 基准算上升沿，最多多消费一次，不会死）。
                    """
                    _cur = _MAS.get(_k, 0)
                    if _cnt < _cur:
                        _MAS[_k] = _cnt
                        return False
                    if _cnt == _cur:
                        return False
                    _MAS[_k] = _cnt
                    return True

                # ★ 2026-10-25 局中加球：F / 小键盘8——先打开串口屏选择页，
                #   触摸红/蓝后才扣 1 个「加球」道具（取消/超时不扣）。
                for _pk, _cntab in ((1, getattr(v3_hub, 'aball1', 0)),
                                    (2, getattr(v3_hub, 'aball2', 0))):
                    if not _mas_ev('aball%d' % _pk, _cntab):
                        continue
                    # ★ 2026-10-29：改磁场输数值时，数字 8 会走 aball2 别名
                    #   （小键盘/主键盘 8），别在这时弹"加球只能在对局中"
                    if (_MF['ob'] is not None
                            and _MF['ob'].phase == 'EDIT'
                            and _MF['ob'].edit.get('stage') == 'VALUE'):
                        continue
                    _okab, _msgab = _aball_begin(
                        _mob2, _pk, _screen_out if screen_on else None)
                    print('[道具] %s' % _msgab, flush=True)
                    _item_msg_set(_msgab)
                    if _okab:
                        _shot_write(dict(t=round(time.time(), 3), ok=True,
                                         why='item', kind='item', seq=0,
                                         msg=_msgab), player=_pk)

                for _pk, _evs in ((1, (('edit', v3_hub.edit1),
                                       ('hole', v3_hub.hole1))),
                                  (2, (('edit', v3_hub.edit2),
                                       ('hole', v3_hub.hole2)))):
                    for _act, _cnt in _evs:
                        if not _mas_ev('%s%d' % (_act, _pk), _cnt):
                            continue
                        if _mob2.phase == 'PLAY':
                            if _act == 'edit':
                                _okx, _msgx = _mob2.begin_edit(_pk)
                                if _okx:
                                    _MAS['snap'] = [(b, float(b.b))
                                                    for b in v3_world.blocks]
                                    # ★ 记录角度/速度快照（退出时还原，编辑键
                                    #   不污染对局的瞄准参数）+ 数字键基准
                                    _EDC['ang'] = (v3_angle, v3_speed,
                                                   v3_angle2, v3_speed2)
                                    for _i0 in range(10):
                                        _MAS['nd%d' % _i0] = int(
                                            v3_hub.nd[_i0] or 0)
                                    _MAS['nd7b'] = int(v3_hub.ppl2 or 0)
                                    _MAS['nd8b'] = int(v3_hub.aball2 or 0)
                                    _MAS['ndbk'] = int(v3_hub.nbksp or 0)
                                    _MAS['nddot'] = int(v3_hub.sup2 or 0)
                            else:
                                # ★★ 2026-10-30 用户定案：**加洞口 = 和障碍
                                #   完全同一套布置流程**——进 PLACE 暂停、实时
                                #   相机、0.3s 黑球识别、黄框、E/* 确认、ESC。
                                _okx, _msgx = _pplace_enter(
                                    _MF['ob'], _pk, 'hole')
                            if _msgx:
                                print('[道具] %s' % _msgx, flush=True)
                                _shot_write(dict(t=round(time.time(), 3), ok=True,
                                                 why='item', kind='item', seq=0,
                                                 msg=_msgx), player=_pk)
                        elif (_mob2.phase == 'EDIT'
                              and _mob2.edit['player'] == _pk
                              and _act == 'edit'):
                            # ★ 2026-10-29：EDIT 里这个键 = 小数点（输入数值用）
                            if _mob2.edit['stage'] == 'VALUE':
                                _mob2.edit_type('.')
                                print('[道具] P%d 输入：%s'
                                      % (_pk, _mob2.edit['txt']), flush=True)
                        elif (_mob2.phase == 'HOLE' and _act == 'hole'
                              and _mob2.hole['player'] == _pk):
                            if _HOLEV['mouse']:
                                # ★ 2026-10-16（用户："按 C 没反应"的真案例之一）：
                                #   鼠标接管中按 C —— 切边下一帧就被鼠标跟随覆盖
                                #   （隐形 no-op）。改为：**收回键盘控制**（C/E/ESC
                                #   全套可用）；动一下鼠标 >8px 可再交还鼠标。
                                _HOLEV['mouse'] = False
                                _HOLEV['m0'] = _HOLEV.get('last', (0, 0))
                                _item_msg_set('已切到键盘控制：C 切边 · E 定边/确认'
                                              '（动一下鼠标可交还）')
                            _HOLEV['ball'] = None
                            _e2 = _mob2.hole_cycle_edge()
                            print('[道具] P%d 切边 -> %s' % (_pk, _e2),
                                  flush=True)

                for _pk in (1, 2):
                    _cnt = v3_hub.buy1 if _pk == 1 else v3_hub.buy2
                    if not _mas_ev('cfm%d' % _pk, _cnt):
                        continue
                    # ★★ 2026-10-27 实体布置确认：E/小键盘* = 确定（不自动扣）
                    if _PPLACE['on'] and _PPLACE['who'] == _pk:
                        if _PPLACE.get('confirm'):
                            _okp, _msgp = _pplace_commit_now(_mob2, v3_world)
                            print('[布置] %s' % _msgp, flush=True)
                            if not _okp:
                                _item_msg_set('放不下：' + _msgp)
                        else:
                            _key = _pplace_key(_pk)
                            _item_msg_set('还没识别到新件——把实体件放到场区，'
                                          '黄框出现后按 %s' % _key)
                            print('[布置] P%d %s：还没有待确认的新件'
                                  % (_pk, _key), flush=True)
                        continue
                    # ★★ 2026-10-28 调整界面：E/小键盘* = 「开始本局」
                    if _ADJ['on']:
                        _adj_start(_MF['ob'], _pk, v3_world)
                        continue
                    if _OBSP['on'] and _OBSP['who'] == _pk:
                        # ★ 布置 PLACE v1：E 两段——第一下=定住弹窗；再一下=确定放置。
                        if not _OBSP['confirm']:
                            _OBSP['confirm'] = True
                            _item_msg_set('确认放置这个障碍？（点「确定」或再按 E）')
                            print('[布置] P%d 定住位置——等「确定」（W/S 可继续旋转）'
                                  % _pk, flush=True)
                        else:
                            _ok, _omsg = _obsp_commit(_mob2, v3_world, _OBST)
                            print('[布置] %s' % _omsg, flush=True)
                            _item_msg_set(_omsg if _ok else ('放不下：' + _omsg))
                            if _ok:
                                _shot_write(dict(t=round(time.time(), 3),
                                                 ok=True, why='item',
                                                 kind='item', seq=0,
                                                 msg=_omsg), player=_pk)
                                _obsp_clear()
                    elif _mob2.phase == 'EDIT' and _mob2.edit['player'] == _pk:
                        # ★ 2026-10-29 三步确认：E / P2 小键盘 * 逐步确认
                        _edit_do_stage_confirm(_mob2, v3_world, _pk)
                    elif _mob2.phase == 'HOLE' and _mob2.hole['player'] == _pk:
                        if _mob2.hole['stage'] == 'EDGE':
                            _ed2 = _mob2.hole['edge']
                            _len2 = 960.0 if _ed2 in ('top', 'bottom') else 540.0
                            _mob2.hole_to_fine(_len2 / 2.0)
                            print('[道具] 洞口边=%s 微调中（%s 10px/按；E 放置/ESC 取消）'
                                  % (_ed2, 'W/S' if _ed2 in ('left', 'right')
                                     else 'A/D'), flush=True)
                        else:
                            _ed3, _ps3 = _mob2.hole['edge'], float(_mob2.hole['pos'])
                            if _hole_commit(_mob2, v3_world):
                                _holev_clear()
                                print('[道具] %s（边=%s 位置=%.0f；target=0 演示口径）'
                                      % (_mob2.msg, _ed3, _ps3), flush=True)
                                _shot_write(dict(t=round(time.time(), 3), ok=True,
                                                 why='item', kind='item', seq=0,
                                                 msg='P%d 加洞口(%s %.0f)'
                                                     % (_pk, _ed3, _ps3)),
                                            player=_pk)
                            else:
                                print('[道具] 放不下：%s' % _mob2.msg, flush=True)
                                _item_msg_set(_mob2.msg)

                if _mas_ev('esc', v3_hub.esc):
                    if _PBUY['on']:
                        _pbu_resolve(_MF['ob'], False)
                    elif _ABALL['on']:
                        _msga = _aball_cancel(
                            _screen_out if screen_on else None)
                        print('[道具] ESC %s' % _msga, flush=True)
                        _item_msg_set(_msga)
                    elif _PPLACE['on']:
                        # ★★ 2026-10-29 用户实况（"传送门点选后改不了、卡死"）：
                        #   传送门先清**已点的一对门**，回到重新点选；再按 ESC 退出。
                        if (_PPLACE.get('kind') == 'portal'
                                and _PPLACE.get('pair')):
                            _PPLACE['pair'] = None
                            _PPLACE['cand'] = []
                            _PPLACE['confirm'] = False
                            _PPLACE['status'] = 'waiting'
                            _item_msg_set('已清除点选——重新点两扇绿门'
                                          '（再按 ESC 退出）')
                            print('[布置] ESC 清除已点绿门（继续摆放）',
                                  flush=True)
                        else:
                            # ★★ 2026-10-31 用户实况（"连按 ESC 也退不掉"）：
                            #   原来这里还有一档"候选已识别时先取消候选、再按才退出"——
                            #   但**候选是每 0.3s 实时识别的**（_pplace_check 每次都
                            #   把 confirm 置回 True）⇒ 清掉 0.3s 后又回来，玩家永远
                            #   按不到"退出"那一档（键盘/手机连按都退不掉）。
                            #   改成：ESC **一次到底**，直接退出（额度未扣）。
                            _pplace_clear()
                            _item_msg_set('已退出布置（额度未扣）')
                            print('[布置] ESC 退出实体布置（额度未扣）', flush=True)
                    elif _ADJ['on']:
                        # ★ 2026-10-28：调整界面 ESC -> 回商店（双方重新可买/可调）
                        _MF['ob'].back_to_shop()
                        _ADJ['on'] = False
                        _SHOPV['open'] = True
                        _item_msg_set('回到商店（继续购买/调整）')
                        print('[布置] ESC 退出调整界面 -> 回商店', flush=True)
                    elif _OBSP['on']:
                        # ★ 布置 PLACE v1：ESC 两段——弹窗中=收弹窗回跟随；跟随中=退出。
                        if _OBSP['confirm']:
                            _OBSP['confirm'] = False
                            _item_msg_set('弹窗已取消——继续移动位置（再按 ESC 退出）')
                        else:
                            _obsp_clear()
                            _item_msg_set('已退出布置（额度未扣）')
                    elif _mob2.phase == 'EDIT':
                        # ★ 2026-10-29 三步确认：ESC 每次退一步；选块阶段再 ESC=取消
                        _edit_do_back(_mob2)
                    elif _mob2.phase == 'HOLE':
                        # ★ 加洞口 v2：ESC 两段式——弹窗开着先回跟随，再按才整个取消
                        if _HOLEV['confirm']:
                            _HOLEV['confirm'] = False
                            print('[道具] 已取消确认弹窗——回到跟随（继续挪；'
                                  '再按 ESC = 整个取消）', flush=True)
                        else:
                            _mob2.end_hole(cancel=True)
                            _holev_clear()
                            print('[道具] %s' % _mob2.msg, flush=True)

                if _mob2.phase == 'EDIT' and _mob2.edit['player'] is not None:
                    _pkE = int(_mob2.edit['player'])
                    _ma = _EDC.pop('mouse_act', None)
                    if _ma == 'ok':
                        # 鼠标「确认」= 当前这一步确认（选块/方向/数值）
                        _edit_do_stage_confirm(_mob2, v3_world, _pkE)
                    elif _ma == 'no':
                        _edit_do_cancel(_mob2)
                    # ★★ 2026-10-29 用户实况：切块键（P1 A/D、P2 ←/→）在任何
                    #   阶段都有效——切块即回到选块阶段、需重新确认（不跳过确认）。
                    for _k2, _d2 in (('aup', +1), ('adn', -1)):
                        _cnt2 = getattr(v3_hub, '%s%d' % (_k2, _pkE))
                        if _mas_ev('%s%d' % (_k2, _pkE), _cnt2):
                            if _mob2.edit['stage'] != 'SELECT':
                                _mob2.edit['stage'] = 'SELECT'
                                _mob2.edit['txt'] = ''
                                print('[道具] P%d 切块 -> 回到选块（需重新确认）'
                                      % _pkE, flush=True)
                            _ix2 = _mob2.edit_next_block(
                                len(v3_world.blocks), _d2)
                            if _ix2 is not None:
                                print('[道具] P%d 选块 -> %d（B=%+.2f）'
                                      % (_pkE, _ix2 + 1,
                                         float(v3_world.blocks[_ix2].b)),
                                      flush=True)
                    # P1 W/S、P2 ↑/↓ = 定方向——**只有 DIR 阶段生效**（选块确认后）。
                    #   其它阶段也要消费计数器，否则退出编辑后对局瞄准会多走一格。
                    for _k2, _s2 in (('mup', +1), ('mdn', -1)):
                        _cnt2 = getattr(v3_hub, '%s%d' % (_k2, _pkE))
                        if not _mas_ev('%s%d' % (_k2, _pkE), _cnt2):
                            continue
                        if _mob2.edit['stage'] != 'DIR':
                            if _mob2.edit['stage'] == 'SELECT':
                                _item_msg_set('先确认选块（%s），方向才生效'
                                              % ('E' if _pkE == 1
                                                 else '小键盘 *'))
                            else:
                                _item_msg_set('已在数值阶段——ESC 可回方向')
                            continue
                        _sg2 = _mob2.edit_set_sign(_s2)
                        if _sg2 is not None:
                            print('[道具] P%d 方向 %s（块 %d）——'
                                  '按 %s 确认方向'
                                  % (_pkE, '+' if _sg2 > 0 else '-',
                                     int(_mob2.edit['idx']) + 1,
                                     'E' if _pkE == 1 else '小键盘 *'),
                                  flush=True)
                            _item_msg_set(
                                '方向 %s——按 %s 确认方向（ESC 返回选块）'
                                % ('+（出屏）' if _sg2 > 0 else '-（入屏）',
                                   'E' if _pkE == 1 else '小键盘 *'))
                    # ★ 2026-10-29：数字/退格**所有阶段都消费计数器**——否则
                    #   SELECT/DIR 阶段按过的数字会在进 VALUE 的那帧被"补打"。
                    _vin = (_mob2.edit['stage'] == 'VALUE')
                    for _i2 in range(10):
                        if _mas_ev('nd%d' % _i2,
                                   int(v3_hub.nd[_i2] or 0)) and _vin:
                            _mob2.edit_type(str(_i2))
                    if _mas_ev('nd7b', int(v3_hub.ppl2 or 0)) and _vin:
                        _mob2.edit_type('7')
                    if _mas_ev('nd8b', int(v3_hub.aball2 or 0)) and _vin:
                        _mob2.edit_type('8')
                    if _mas_ev('ndbk', int(v3_hub.nbksp or 0)) and _vin:
                        _mob2.edit_backspace()
                    if _mas_ev('nddot', int(v3_hub.sup2 or 0)) and _vin:
                        _mob2.edit_type('.')
                elif _mob2.phase == 'HOLE' and _mob2.hole['stage'] == 'FINE':
                    _opl = _mob2.hole['player']
                    _pfx = 1 if _opl == 1 else 2
                    _horiz = _mob2.hole['edge'] in ('top', 'bottom')
                    _mvs = (('aup', +10.0), ('adn', -10.0)) if _horiz \
                        else (('mup', +10.0), ('mdn', -10.0))
                    for _k2, _d2 in _mvs:
                        _cnt2 = getattr(v3_hub, '%s%d' % (_k2, _pfx))
                        if _mas_ev('%s%d' % (_k2, _pfx), _cnt2):
                            _hi2 = (960.0 if _horiz else 540.0) - 50.0
                            _np2 = _mob2.hole_adjust(_d2, 50.0, _hi2)
                            print('[道具] 洞口 pos=%.0f（%s）'
                                  % (_np2, '可放' if _hole_valid(
                                      v3_world, _mob2.hole['edge'], _np2)
                                     else '★放不下'), flush=True)

                # ---- ★★ 2026-10-15 加洞口 v2：鼠标跟随（对话5 死前定稿）--------
                #   进入 HOLE 后**鼠标一动即接管**（与进入点差 >8px）：最近边选边、
                #   沿边滑动定位置——每帧从鼠标位置重算 edge+pos（含尺寸换算，见
                #   _hole_from_mouse）。弹窗开着（confirm）时位置定住，这里只记光标。
                #   没动过鼠标 = 不碰状态，老键盘流程原样。离开 HOLE 一律归零。
                # ★ 2026-10-19 布置 PLACE v1：摆放态鼠标跟随（直映射——与
                #   _build_obsp_view 的画法成对：1:1 居中，偏移 (w-960)/2,(h-540)/2；
                #   与 _hole_side_from_canvas 同款口径）。一动才接管。
                if _OBSP['on'] and _MF['ob'] is not None \
                        and _MF['ob'].phase == 'PREP' and v3_hub is not None:
                    _mx4, _my4 = int(v3_hub.mx), int(v3_hub.my)
                    if (not _OBSP['mouse']
                            and (abs(_mx4 - _OBSP['m0'][0]) > 8
                                 or abs(_my4 - _OBSP['m0'][1]) > 8)):
                        _OBSP['mouse'] = True
                    if _OBSP['mouse'] and not _OBSP['confirm']:
                        _ox4 = (int(v3_pump.width) - 960) // 2
                        _oy4 = (int(v3_pump.height) - 540) // 2
                        _OBSP['cx'] = max(0.0, min(960.0, float(_mx4 - _ox4)))
                        _OBSP['cy'] = max(0.0, min(540.0, float(_my4 - _oy4)))
                if (_mob2.phase == 'HOLE'
                        and _HOLEV['who'] == _mob2.hole['player']):
                    _HOLEV['last'] = (int(v3_hub.mx), int(v3_hub.my))
                    if v3_pump is not None:
                        _HOLEV['pw'], _HOLEV['ph'] = v3_pump.width, v3_pump.height
                        if not _HOLEV['confirm']:
                            if not _HOLEV['mouse']:
                                _dvh = (abs(_HOLEV['last'][0] - _HOLEV['m0'][0])
                                        + abs(_HOLEV['last'][1] - _HOLEV['m0'][1]))
                                if _dvh > 8:
                                    _HOLEV['mouse'] = True
                                    _HOLEV['ball'] = None
                                    print('[道具] P%d 加洞口：鼠标接管——'
                                          '移动鼠标选边/滑动，左键点住→弹窗，'
                                          'E 确定 ESC 取消'
                                          % _mob2.hole['player'], flush=True)
                            if _HOLEV['mouse']:
                                try:
                                    # ★ 2026-10-16：瞄准图 -> 直映射（用户实况
                                    #   "左边一部分吸附不到"的根因就是这里还在走
                                    #   相机标定链）
                                    _ed6, _ps6 = _hole_side_from_canvas(
                                        _HOLEV['last'][0], _HOLEV['last'][1],
                                        v3_pump.width, v3_pump.height)
                                    if (_ed6 != _mob2.hole['edge']
                                            or abs(_ps6 - float(_mob2.hole['pos']))
                                            > 0.5
                                            or _mob2.hole['stage'] != 'FINE'):
                                        _mob2.hole_set_pos(_ed6, _ps6)
                                except Exception:
                                    pass
                else:
                    if (_HOLEV['who'] is not None or _HOLEV['confirm']
                            or _HOLEV['mouse']):
                        _holev_clear()

                # ---- ★★ 2026-10-15 加洞口 v2：手机俯视图点位置（游戏坐标直达）--
                #   数据源 = v3_input 的 p{N}_hopos 计数 + p{N}_hx/hy（serve 的
                #   /hole?act=pos 写）——与键盘/鼠标同一套"读数上升沿 + 文件重建
                #   只对基准"的纪律（_mas_ev 同款；键名已进 _MAS 表）。
                for _pkh in (1, 2):
                    _cnth = int(getattr(v3_hub, 'hopos%d' % _pkh, 0) or 0)
                    if not _mas_ev('hopos%d' % _pkh, _cnth):
                        continue
                    if _mob2.phase == 'HOLE' and _HOLEV['who'] == _pkh:
                        _gxh = float(getattr(v3_hub, 'hx%d' % _pkh, 0.0) or 0.0)
                        _gyh = float(getattr(v3_hub, 'hy%d' % _pkh, 0.0) or 0.0)
                        _HOLEV['ball'] = None
                        _edh, _psh = _hole_from_game(_gxh, _gyh)
                        _hole_apply_pos(_mob2, _edh, _psh, _MF['mod'],
                                        v3_world, src='手机点位置')
                    else:
                        print('[道具] 手机点了位置，但不在加洞口（忽略）',
                              flush=True)

                # ---- ★★ 2026-10-16 粒子引擎（--pe）：B=P1 / N=P2 放一簇粒子 ----
                for _pkp, _pcn in ((1, v3_ball1), (2, v3_ball2)):
                    _cntp = int(getattr(v3_hub, 'pe%d' % _pkp, 0) or 0)
                    if not _mas_ev('pe%d' % _pkp, _cntp):
                        continue
                    if not _PEB['on']:
                        print('[粒子] B/N：引擎没开（加 --pe 且用带 pe_axi 的 bit）',
                              flush=True)
                        continue
                    _src = None
                    try:
                        _ptp = next((t for t in (_tracks or [])
                                     if getattr(t, 'no', None) == _pcn), None)
                        if _ptp is not None and v3_mod is not None:
                            _src = v3_mod.cam_to_game(v3_H, _ptp.sx, _ptp.sy)[:2]
                    except Exception:
                        _src = None
                    if _src is None:            # 没跟踪到球 -> 本侧半场中点兜底
                        _src = (200.0, 270.0) if _pkp == 1 else (760.0, 270.0)
                    # ★ 2026-10-19 实况：双方控同一颗球时，红蓝风暴同点生成会
                    #   瞬间互相湮灭（看起来"按 BN 没反应"）→ 按玩家左右错开。
                    _sx2 = float(_src[0]) + (-60.0 if _pkp == 1 else 60.0)
                    _sy2 = float(_src[1]) + (-30.0 if _pkp == 1 else 30.0)
                    # ★ 2026-10-19 观感：球贴边时风暴几帧就出洞 → 压进场区内部
                    _sx2 = max(120.0, min(840.0, _sx2))
                    _sy2 = max(90.0, min(450.0, _sy2))
                    _pe_spawn_storm(_PEB, _sx2, _sy2,
                                    n=4, chg=(1 if _pkp == 1 else -1))
                    print('[粒子] P%d 风暴：4 颗从 (%.0f,%.0f) 入场'
                          '（PL 并行物理）' % (_pkp, _sx2, _sy2),
                          flush=True)


            #   球位/速度来自**真跟踪**（trail 最近 6 点线性拟合），场区来自 seed，
            #   判定由 PL 真硬件给 —— 这就是"PL 12ms 算完一簇"的现场证据。
            #   ★ 代价要记账：一次预测 = PL AXI + 板载 CPU 画折线 + 渲染写盘，
            #     明细进 V3_STAT，长跑汇总里摊到每帧（评委问"拖慢了多少"有数）。
            #   ★★ 发射模式下（v3_hub 非空）这整块**不跑** —— 预测只在按发射那一刻
            #     发生（用户①②：球从边界发射、后面的全是模拟；平时不许自己预测）。
            if (v3_every and v3_hub is None and ball is not None
                    and n_frames % v3_every == 0):
                _smp = [(t_, x_, y_) for (x_, y_, _c, t_) in trail[-v3_mod.TRACK_N:]]
                _vp = v3_mod.fit_velocity(_smp)
                if _vp is None:
                    V3_STAT['n_skip'] += 1          # 轨迹点不够（刚开局/刚被遮挡）
                else:
                    try:
                        _st = _v3_live(v3_world, v3_V, v3_H,
                                       (ball['x'], ball['y']), _vp,
                                       # ★ 尾巴用**整条 trail**（最多 60 点，看得见），
                                       #   拟合只用最近 6 点（_smp，噪声小）—— 别混用
                                       [(p[0], p[1]) for p in trail[-30:]],
                                       v3_mod.BALL_R,
                                       outdir, v3_dump,
                                       ang_in=v3_angle, spd_in=v3_speed,
                                       charge=_rtb.charge_of(ball.get('cls')),
                                       fan_half=v3_fan, steps=v3_steps,
                                       arcs=v3_arcs, track_angle=v3_track_angle,
                                       quiet=(V3_STAT['n'] > 0),
                                       # ★ 2026-10-01 夜提速：直播档平时**不出图**
                                       #   （轻 tick，只读 PL 判定 ~3ms）；那张仿真图
                                       #   只在"真进球"那一刻现算（见下面的复盘触发）。
                                       #   stst 档照旧：每次预测都出图（want_img=True）。
                                       want_img=(not v3_hdmi_live))
                        if _st.get('skip'):
                            # 球在场区外（误检 / 滚出纸面 / 标定漂移）—— 跳过这次预测，
                            # **不碰 PL、不出图**。前 3 次吱一声，之后安静（长跑不刷屏）。
                            V3_STAT['n_out'] += 1
                            if V3_STAT['n_out'] <= 3:
                                print('[V3] ★ 球位在场区外（游戏 %.0f,%.0f）-> 跳过预测'
                                      '（场外的球会让 PL 一路卡死，见 v3_scan.in_field）'
                                      % (_st['bx'], _st['by']), flush=True)
                        else:
                            _v3_note(_st)
                            # ★★ 2026-10-01 夜（用户第③点）：**进球复盘**。
                            #   扇形**正中那一路**（index 8，k=0）= 球员瞄的那个角度
                            #   （= 手机设的入射角）判定 `goal` = **进洞**（v3_fixedpoint
                            #   实锤：goal 就是"从洞口进"）。要的是**上升沿**：一直瞄着
                            #   洞不能一直弹复盘 —— 只在从"没进"变"会进"的那一次触发。
                            #   触发了就把这张轨迹图贴在 `_REV` 上，循环尾的直播推帧
                            #   看见它在期内就**先不盖**（review 秒后自动回直播）。
                            if _review_tick(_st.get('shot'), v3_hdmi_live, v3_review,
                                            time.time(), v3_pump is not None):
                                # ★ 2026-10-01 夜提速：直播档平时是**轻 tick**（不出图）。
                                #   真进球这一刻才带 want_img=True **现算**这张复盘图
                                #   （同一份输入再跑一次，~0.6s 一次性），随后 ~几帧内
                                #   贴屏。换来的是其余每一 tick 省下折线+渲染那 565ms。
                                _stf = _v3_live(v3_world, v3_V, v3_H,
                                                (ball['x'], ball['y']), _vp,
                                                [(p[0], p[1]) for p in trail[-30:]],
                                                v3_mod.BALL_R,
                                                outdir, v3_dump,
                                                ang_in=v3_angle, spd_in=v3_speed,
                                                charge=_rtb.charge_of(ball.get('cls')),
                                                fan_half=v3_fan, steps=_eff_steps(v3_speed),
                                                arcs=v3_arcs,
                                                track_angle=v3_track_angle,
                                                quiet=True, want_img=True)
                                if _stf.get('img') is not None:
                                    _v3_note_full_only(_stf)
                                    _REV['img'] = _stf['img']
                                    V3_STAT['n_review'] += 1
                                    print('[复盘] ★ 这一杆会**进洞**（角 %.1f°、17 路里'
                                          ' %d 路出射）-> 现算轨迹复盘图（折线 %.0fms +'
                                          ' 渲染 %.1fms）贴屏 %.0fs 再回直播'
                                          % (_st.get('ang_ui', _st['ang']), _st['n_goal'], _stf['ms_arcs'],
                                             _stf['ms_render'], v3_review), flush=True)
                            # ★ P2：把这张图推上板载 HDMI（帧泵）。开销记进 V3_STAT；
                            #   连败 3 次就掐掉自己 —— 屏幕不许拖死游戏循环（跟踪是主线）。
                            #   ★ live 档**不走这里**：它的画面是实拍+叠加（下面的
                            #     "直播档上屏"块），这里推的仿真图只在复盘时借道 _REV。
                            if v3_pump is not None and not v3_hdmi_live:
                                try:
                                    _h = v3_pump.show(_st['img'])
                                    V3_STAT['n_show'] += 1
                                    V3_STAT['ms_show'].append(_h['ms_total'])
                                    if len(V3_STAT['ms_show']) > 200:
                                        V3_STAT['ms_show'].pop(0)
                                except Exception as _eh:
                                    V3_STAT['n_show_fail'] += 1
                                    print('[HDMI] ★ 推帧失败（第 %d 次）：%s'
                                          % (V3_STAT['n_show_fail'], str(_eh)[:140]),
                                          flush=True)
                                    if V3_STAT['n_show_fail'] >= 3:
                                        print('[HDMI] 连续失败 3 次 -> **关掉 HDMI 直出**，'
                                              '游戏照跑', flush=True)
                                        try:
                                            v3_pump.close()
                                        except Exception:
                                            pass
                                        v3_pump = None
                            _cost = ('PL %.1fms + 折线 %.0fms + 渲染 %.1fms = **%.0fms**'
                                     % (_st['ms_axi'], _st['ms_arcs'],
                                        _st['ms_render'], _st['ms_total'])
                                     if _st.get('full') else
                                     'PL %.1fms（轻 tick：不折线/不渲染）' % _st['ms_axi'])
                            print('[V3] #%d 球位(游戏 %.0f,%.0f) 角%.1f° 速度%.0f | %s | '
                                  '绿%d 红%d 临界%s'
                                  % (V3_STAT['n'], _st['bx'], _st['by'], _st.get('ang_ui', _st['ang']),
                                     _st['spd'], _cost,
                                     _st['n_goal'], _st['n_bounce'],
                                     'none' if _st['crit'] is None else
                                     '%.1f°(离洞边 %.1fpx)' % (_st['crit'],
                                                               abs(_st['margin'] or 0.0))),
                                  flush=True)
                    except Exception as e:
                        V3_STAT['n_fail'] += 1
                        print('[V3] ★ 预测失败（第 %d 次）：%s'
                              % (V3_STAT['n_fail'], str(e)[:140]), flush=True)
                        if V3_STAT['n_fail'] >= 3:
                            # 连续三次栽了就别再拖着游戏循环：跟踪本身才是主线，
                            # 预测是加分项 —— 关掉它，长跑还能跑完（口径要诚实）。
                            print('[V3] 连续失败 3 次 -> **关掉实时预测**，专心跑跟踪', flush=True)
                            v3_every = 0

            # ---- ★★ 直播档上屏（用户第①点）：屏幕 = 实拍 + 场区/球/轨迹叠加 ----
            #   只在 --v3-hdmi-live 下走（stst 档上屏还留在上面"每次预测"那段，
            #   一个字节没动）。位置放在检测/V3 之后：这帧的新球位、新轨迹都在手上
            #   了才画 —— 屏幕上看到的就是**当前**的跟踪结果。
            #   ★ 复盘（第③点）在期内就先不盖（那张轨迹图是"这一杆进了"的证据）。
            #   ★ 相机没出图**不算帧泵的错**（屏上留上一帧，live 档特性）；只有推帧
            #     失败才记账，连败 8 次关泵（屏幕不许拖死游戏循环，跟踪才是主线）。
            if v3_pump is not None and v3_hdmi_live:
                _now = time.time()
                if _REV['img'] is not None and _now < _REV['until']:
                    # ★★ 2026-10-01 修 bug（现场"一按发射就卡、图看不到"的真凶）：
                    #   live 档以前这里**只有一句 pass** —— 复盘图（发射那杆/进球）
                    #   贴进 _REV 后全代码库没有任何一处把它推上屏：屏幕干冻
                    #   v3_review 秒再回直播，图从头到尾没显示过。现在窗口内推一次
                    #   （"推没推过"的判据抽在 `_rev_needs_push`，台架 review_push
                    #   场景钉死 —— 别再把条件写回内联）。
                    if _rev_needs_push(_REV):
                        try:
                            _h = v3_pump.show(_REV['img'])
                            V3_STAT['n_show'] += 1
                            V3_STAT['ms_show'].append(_h['ms_total'])
                            if len(V3_STAT['ms_show']) > 200:
                                V3_STAT['ms_show'].pop(0)
                        except Exception as _eh:
                            V3_STAT['n_show_fail'] += 1
                            if V3_STAT['n_show_fail'] <= 3:
                                print('[HDMI] ★ 复盘图推帧失败：%s'
                                      % str(_eh)[:140], flush=True)
                else:
                    # 窗口结束：复盘状态整个清干净（img 与 pushed 一起清 —— 不然
                    # pushed 会攥着一个旧对象不放，"推过"不该跨窗口存在）。
                    _REV['img'] = None
                    _REV['pushed'] = None
                    _rawg = None
                    # ★★ 2026-10-13 提速（用户："鼠标响应速度有点迟缓"）：**商店画面
                    #   开着时不抓图、不检测**——商店图是合成画面，相机数据这一帧没有
                    #   消费者（检测的 29ms + 抓图的 11ms 全省给光标）。球位在店里冻结，
                    #   开局时跟踪器自然重锁（本来每局开局也要重扫）。
                    # ★★（原本这里重定义 _shop_on——2026-10-13 实况坑：它漏了"局前"，
                    #   导致局前同时跑合成图+旧直播两套推帧、局部光标把合成碎片贴到
                    #   直播画面上（"划过的区域变成商店图标的一部分"）。统一用外层的
                    #   `_gate`，一个字都不许再分叉。）
                    # ★ 2026-10-01 夜加的两笔账：抓图 / 叠加 —— 上一趟 1.6fps 有 ~350ms
                    #   没记账，就是藏在这两笔（和 _build_live 的 cv2 调用）里。
                    #   别估，量：这两行让下一次长跑的账能对平。
                    _tg0 = time.perf_counter()
                    if (not _gate and not _hole_on and not _edit_on
                            and not _PPLACE['on']):
                        try:
                            # tries=0：**不做** ctrl[4]+重初始化那套救援（要 4~5 秒，
                            # 卡一次就把游戏循环拖死）；这里快失败，屏上留上一帧。
                            # ★ 2026-10-16：HOLE 冻结期也跳过抓图（瞄准图是合成
                            #   画面，相机这一帧没有消费者——省 11ms+ 给光标）。
                            # ★ 2026-10-28：摆放态也不用这里抓（它有专用识别帧）。
                            _rawg, _gi = _grab(cap, tries=0)
                        except Exception:
                            V3_STAT['n_live_noimg'] += 1
                    _LT['grab'].append((time.perf_counter() - _tg0) * 1000.0)
                    if len(_LT['grab']) > 200:
                        _LT['grab'].pop(0)
                    # ★★ 商店 v2：商店画面**不依赖相机**（相机坏了也照样能买）——
                    #   放在 `if _rawg` 之前；命中框在这里写回（点击要用）。
                    if _gate or _PPLACE['on']:
                        try:
                            _pp_live = False
                            _mpos = (0, 0)
                            if v3_hub is not None:
                                _mpos = (v3_hub.mx, v3_hub.my)
                            _ob_balls = ((live_balls + _PEB['balls'])
                                         if _PEB['on'] else live_balls)
                            # ★ 2026-10-25：局前/商店阶段也要更新 /match——
                            #   此前只在检测路径写，局前会停在上一局的 ROUND_END，
                            #   手机网页看到的局数/阶段是滞后的。
                            if time.time() - _BALLS['t'] > 0.5:
                                _tch0 = time.perf_counter()
                                _phone_state_tick(_MF['ob'], _MF['mod'],
                                                  v3_world, _SHOPV['open'],
                                                  live_balls, _tracks,
                                                  v3_mod, v3_H)
                                _ms_add('phone', _tch0)
                            if _PPLACE['on'] or _ADJ['on']:
                                # ★★ 2026-10-27 用户定案 B：布置态优先用**实时
                                #   相机画面**（相机+场区/已生效障碍门+新件黄圈）；
                                #   抓不到帧才退回合成俯视图。识别仍是 PS 0.3s，
                                #   不碰 PL/RTL/bit。
                                # ★★ 2026-10-28：调整界面复用同一套画面（_ADJ）。
                                _pstate = _PPLACE if _PPLACE['on'] else _ADJ
                                if _ADJ['on']:
                                    _unp = []
                                    for _pu in (1, 2):
                                        try:
                                            _au = int(_MF['ob'].items[_pu]['obstacle'])
                                            _bu = int(_MF['ob'].items[_pu]['portal'])
                                        except Exception:
                                            _au = _bu = 0
                                        if _au or _bu:
                                            _unp.append('P%d 障碍%d/门%d'
                                                        % (_pu, _au, _bu))
                                    _ADJ['status'] = (
                                        '当前：障碍 %d 根 / 传送门 %d 对'
                                        '（黄框=可调整；开局后实心黄锁定）'
                                        % (len(getattr(v3_world, 'obstacles', None) or []),
                                           len(getattr(v3_world, 'portals', None) or []) // 2))
                                    if _unp:
                                        _ADJ['status'] += ('　未放置：' + ' '.join(_unp)
                                                           + '——按 V/G 摆放')
                                _pp_live = False
                                _rawp = None
                                _pinterp = ('nearest' if live_fast
                                            else 'linear')
                                # ★ 先用刚识别用过的原图（0.6s 内）——省一次
                                #   抓图+解码（摆放画面本来就慢，够用）
                                _pdet_img = _pstate.get('det_img')
                                _pdet_ok = (_pdet_img is not None
                                            and time.time()
                                            - float(_pstate.get('det_t') or 0.0)
                                            < 0.6)
                                if _pdet_ok:
                                    try:
                                        _limg = _build_pplace_live(
                                            None, 0, v3_H, v3_world,
                                            _MF['ob'], _pstate, _mpos,
                                            fire_margin=fire_margin,
                                            angle=v3_angle, speed=v3_speed,
                                            crop=live_crop,
                                            crop_margin=crop_margin,
                                            interp=_pinterp,
                                            pre_img=_pdet_img)
                                        _h = _live_show(
                                            v3_pump, _limg, _LIVEPUSH,
                                            interp=_pinterp)
                                        V3_STAT['n_live'] += 1
                                        V3_STAT['ms_live'].append(
                                            _h['ms_total'])
                                        if len(V3_STAT['ms_live']) > 200:
                                            V3_STAT['ms_live'].pop(0)
                                        _SHOPV['rects'] = {}
                                        _SHOPV['lk'] = None
                                        _pp_live = True
                                    except Exception:
                                        _pp_live = False
                                if not _pp_live:
                                    try:
                                        _rawp, _ = _grab(cap, tries=0)
                                    except Exception:
                                        _rawp = None
                                if not _pp_live and _rawp is not None:
                                    try:
                                        _rswp = ((swap ^ mswap ^ 1)
                                                 if mswap is not None else swap)
                                        _limg = _build_pplace_live(
                                            _rawp, _rswp, v3_H, v3_world,
                                            _MF['ob'], _pstate, _mpos,
                                            fire_margin=fire_margin,
                                            angle=v3_angle, speed=v3_speed,
                                            crop=live_crop,
                                            crop_margin=crop_margin,
                                            interp=_pinterp)
                                        _h = _live_show(
                                            v3_pump, _limg, _LIVEPUSH,
                                            interp=_pinterp)
                                        V3_STAT['n_live'] += 1
                                        V3_STAT['ms_live'].append(
                                            _h['ms_total'])
                                        if len(V3_STAT['ms_live']) > 200:
                                            V3_STAT['ms_live'].pop(0)
                                        _SHOPV['rects'] = {}
                                        # 下一个合成画面强制整帧（live 期间
                                        # 没走 _key_s 推帧门）
                                        _SHOPV['lk'] = None
                                        _pp_live = True
                                    except Exception as _eph:
                                        V3_STAT['n_live_noimg'] += 1
                                        _pp_live = False
                                _pstate['live_view'] = bool(_pp_live)
                                # ★ 2026-10-29 诊断：布置/调整界面也按 5s 存一张
                                #   上屏图（就是黄框+实拍合成后的画面），用来核对
                                #   "框和实物黑条到底差在哪"。只写 tmpfs，不碰相机。
                                if (_pp_live and live_dump
                                        and time.time() - _LIVE_DUMP['t'] > 5.0):
                                    _LIVE_DUMP['t'] = time.time()
                                    try:
                                        cv2.imwrite(live_dump, _limg)
                                    except Exception:
                                        pass
                                if _pp_live:
                                    # 本帧已由 _live_show 上屏；跳过合成推帧门
                                    # （内容键/光标局部更新不适用于实拍画面）。
                                    continue
                                _limg = _build_pplace_view(
                                    v3_pump.width, v3_pump.height,
                                    _MF['ob'], _MF['mod'], v3_world,
                                    _pstate, _mpos, _ob_balls)
                                _SHOPV['rects'] = {}
                                # 兜底合成画面首帧强制整帧（live 期间没走推帧门）
                                _SHOPV['lk'] = None
                                # ★ 诊断：合成布局画面也按 5s 存一张（否则抓不到
                                #   "黄框 vs 实物"的现场帧）。
                                if (live_dump
                                        and time.time() - _LIVE_DUMP['t'] > 5.0):
                                    _LIVE_DUMP['t'] = time.time()
                                    try:
                                        cv2.imwrite(live_dump, _limg)
                                    except Exception:
                                        pass
                            elif _OBSP['on']:
                                # ★ 2026-10-19 布置 PLACE v1：障碍摆放画面（合成
                                #   俯视图；光标在视图内画，共享下面的推帧门）。
                                _limg = _build_obsp_view(
                                    v3_pump.width, v3_pump.height,
                                    _MF['ob'], _MF['mod'], v3_world,
                                    _OBSP, _mpos, _ob_balls)
                                _SHOPV['rects'] = _OBSP.get('rects') or {}
                            elif _shop_on:
                                _limg = _build_shop(v3_pump.width, v3_pump.height,
                                                    _MF['ob'], _MF['mod'], _mpos)
                                _SHOPV['rects'] = _shop_layout(v3_pump.width,
                                                               v3_pump.height)
                            else:
                                # ★ 局前入口 = 合成画面（不开相机——光标丝滑；
                                #   相机直播在开局瞬间回来）
                                _limg, _ent = _build_prep_gate(
                                    v3_pump.width, v3_pump.height,
                                    _MF['ob'], _MF['mod'], _mpos)
                                _SHOPV['rects'] = {'ent': _ent}
                            # ★★ 2026-10-13 防闪终版（用户："一动鼠标就闪，这绝对有问题"）：
                            #   限频只是掩盖——撕裂的根在**整帧就地写**。改**局部更新**：
                            #   · 内容级变化（选单/积分/就绪/进出店）→ 整帧 show；
                            #   · 只是光标动了 → `show_rect`（老位∪新位的小矩形，微秒级
                            #     写入，撞上引擎扫线概率≈0）→ **高帧率刷也看不见撕裂**；
                            #   · 都不动 → 一个字节不写（屏绝对稳）。40ms 下限对齐读取器
                            #     的写盘节流（≈25fps 光标）。
                            _nowp = time.monotonic()
                            _mob = _MF['ob']
                            _key_s = (_mob.round, _mob.sel_item[1], _mob.sel_item[2],
                                      _mob.points[1], _mob.points[2],
                                      _mob.sub[1], _mob.sub[2], _shop_on,
                                      # ★ 布置 PLACE v1：摆放态内容键（位置/角度/
                                      #   弹窗/合法色变都算内容变化 -> 整帧推）
                                      _OBSP['on'], _OBSP['who'],
                                      round(_OBSP['cx']), round(_OBSP['cy']),
                                      round(_OBSP['ang']), _OBSP['confirm'],
                                      (_obst_valid(v3_world, _OBSP['cx'],
                                                   _OBSP['cy'],
                                                   ang=_OBSP['ang'])
                                       if _OBSP['on'] else None),
                                      # ★ 2026-10-25 实体布置态内容键：候选
                                      #   出现/消失/移动都要整帧推（看到"识别到"）
                                      _PPLACE['on'], _PPLACE['who'],
                                      _PPLACE['kind'], _PPLACE['status'],
                                      # ★★ 2026-10-28 实况："买完询问框看起来卡住/
                                      #   被切掉"——询问框开/关没进内容键，商店只推
                                      #   光标小矩形，提示框只被"切"出一块。进键！
                                      _PBUY['on'], _PBUY['who'], _PBUY['kind'],
                                      tuple((c.get('kind'),
                                             round(float(c.get('gx', 0.0))),
                                             round(float(c.get('gy', 0.0))))
                                            for c in (_PPLACE['cand'] or [])[:3]))
                            _crect = (_mpos[0] - 6, _mpos[1] - 6,
                                      _mpos[0] + 26, _mpos[1] + 28)
                            _lrect = _SHOPV.get('lr')
                            if _key_s != _SHOPV.get('lk'):
                                _SHOPV['lk'] = _key_s
                                _SHOPV['lt'] = _nowp
                                _SHOPV['lr'] = _crect
                                _h = v3_pump.show(_limg)
                                V3_STAT['n_shop'] += 1
                                V3_STAT['ms_shop'].append(_h['ms_total'])
                                if len(V3_STAT['ms_shop']) > 200:
                                    V3_STAT['ms_shop'].pop(0)
                            elif (_lrect is not None and _lrect != _crect
                                  and _nowp - _SHOPV.get('lt', 0.0) > 0.04):
                                _u = (min(_lrect[0], _crect[0]),
                                      min(_lrect[1], _crect[1]),
                                      max(_lrect[2], _crect[2]),
                                      max(_lrect[3], _crect[3]))
                                _SHOPV['lt'] = _nowp
                                _SHOPV['lr'] = _crect
                                _h = v3_pump.show_rect(_limg, _u)
                                V3_STAT['n_shop'] += 1
                                V3_STAT['ms_shop'].append(_h['ms_total'])
                                if len(V3_STAT['ms_shop']) > 200:
                                    V3_STAT['ms_shop'].pop(0)
                        except Exception as _eh:
                            V3_STAT['n_show_fail'] += 1
                            if V3_STAT['n_show_fail'] <= 3:
                                print('[商店] ★ 商店画面推帧失败：%s'
                                      % str(_eh)[:140], flush=True)
                    elif _hole_on:
                        # ★★ 2026-10-16 加洞口 v2：**俯视瞄准图**（用户："鼠标大
                        #   漂移，不好控制"——实拍直播 3fps 整帧拖光标；合成画面
                        #   照抄商店档：内容=key 变才整帧推，光标动只推小矩形，
                        #   20+fps）。命中框由 _build_hole_view 写回 _HOLEV['rects']
                        #   （画布坐标——与 _hole_mouse_click 同源）。
                        try:
                            _hb2 = _hole_view_balls(
                                (live_balls + _PEB['balls']) if _PEB['on']
                                else live_balls, _tracks, v3_mod, v3_H)
                            _mp2 = (0, 0)
                            if v3_hub is not None:
                                _mp2 = (int(v3_hub.mx), int(v3_hub.my))
                            _key_h = (_MF['ob'].hole['player'],
                                      _MF['ob'].hole['edge'],
                                      round(float(_MF['ob'].hole['pos']), 1),
                                      bool(_HOLEV['confirm']),
                                      _HOLEV.get('quota') or '',
                                      bool(_HOLEV.get('mouse')),
                                      int(max(0.0, float(_MF['mod'].PAUSE_MAX)
                                              - float(_MF['ob'].hole.get('t', 0.0)))),
                                      tuple((round(b[0] / 8.0), round(b[1] / 8.0),
                                             b[2]) for b in _hb2),
                                      id(v3_world), len(v3_world.holes))
                            if (_key_h != _HCACHE['key']
                                    or _HCACHE['base'] is None):
                                _HCACHE['key'] = _key_h
                                _HCACHE['base'] = _build_hole_view(
                                    v3_pump.width, v3_pump.height, _MF['ob'],
                                    _MF['mod'], v3_world, _HOLEV, (0, 0), _hb2)
                            _limg = _HCACHE['base'].copy()
                            _draw_cursor(_limg, _mp2[0], _mp2[1])
                            _nowh = time.monotonic()
                            _crect2 = (_mp2[0] - 6, _mp2[1] - 6,
                                       _mp2[0] + 26, _mp2[1] + 28)
                            _lrect2 = _HCACHE['lr']
                            if _key_h != _HCACHE.get('pushed'):
                                _HCACHE['pushed'] = _key_h
                                _HCACHE['lt'] = _nowh
                                _HCACHE['lr'] = _crect2
                                _h = v3_pump.show(_limg)
                                V3_STAT['n_shop'] += 1
                                V3_STAT['ms_shop'].append(_h['ms_total'])
                                if len(V3_STAT['ms_shop']) > 200:
                                    V3_STAT['ms_shop'].pop(0)
                            elif (_lrect2 is not None and _lrect2 != _crect2
                                  and _nowh - _HCACHE.get('lt', 0.0) > 0.04):
                                _u2 = (min(_lrect2[0], _crect2[0]),
                                       min(_lrect2[1], _crect2[1]),
                                       max(_lrect2[2], _crect2[2]),
                                       max(_lrect2[3], _crect2[3]))
                                _HCACHE['lt'] = _nowh
                                _HCACHE['lr'] = _crect2
                                _h = v3_pump.show_rect(_limg, _u2)
                                V3_STAT['n_shop'] += 1
                                V3_STAT['ms_shop'].append(_h['ms_total'])
                                if len(V3_STAT['ms_shop']) > 200:
                                    V3_STAT['ms_shop'].pop(0)
                            if (live_dump
                                    and _now - _LIVE_DUMP['t'] > 5.0):
                                # ★ 2026-10-16（板 r78 现场教训）：冻结期也要按同一
                                #   5s 闸存图 —— 否则 live.bmp 停在"进洞前那帧"，
                                #   想核对瞄准图长什么样就全瞎了（今天真栽在这）。
                                _LIVE_DUMP['t'] = _now
                                _p = (live_dump if live_dump.startswith('/')
                                      else os.path.join(outdir, live_dump))
                                cv2.imwrite(_p, _limg)
                            time.sleep(0.004)      # 冻结期不空转烧 CPU
                        except Exception as _eh3:
                            V3_STAT['n_show_fail'] += 1
                            if V3_STAT['n_show_fail'] <= 3:
                                print('[洞口] ★ 瞄准图推帧失败：%s'
                                      % str(_eh3)[:140], flush=True)
                            if V3_STAT['n_show_fail'] >= 8:
                                print('[洞口] 连续失败 8 次 -> **关掉 HDMI**，游戏照跑',
                                      flush=True)
                                try:
                                    v3_pump.close()
                                except Exception:
                                    pass
                                v3_pump = None
                    elif (_edit_on and _MF['ob'].edit.get('player')
                          is not None):
                        # ★★ 2026-10-29：改磁场界面的**俯视瞄准图**（与加洞口
                        #   同一套布局）：合成画布 + 选中块高亮 + 底部输入框。
                        try:
                            _eb2 = _hole_view_balls(
                                (live_balls + _PEB['balls']) if _PEB['on']
                                else live_balls, _tracks, v3_mod, v3_H)
                            _mp3 = (0, 0)
                            if v3_hub is not None:
                                _mp3 = (int(v3_hub.mx), int(v3_hub.my))
                            _key_e = (_MF['ob'].edit['player'],
                                      _MF['ob'].edit['idx'],
                                      _MF['ob'].edit['stage'],
                                      int(_MF['ob'].edit.get('sign', 1)),
                                      str(_MF['ob'].edit.get('txt') or ''),
                                      int(max(0.0, float(_MF['mod'].PAUSE_MAX)
                                              - float(_MF['ob'].edit.get('t', 0.0)))),
                                      tuple((round(b[0] / 8.0),
                                             round(b[1] / 8.0), b[2])
                                            for b in _eb2),
                                      id(v3_world), len(v3_world.blocks))
                            if (_key_e != _ECACHE['key']
                                    or _ECACHE['base'] is None):
                                _ECACHE['key'] = _key_e
                                _ECACHE['base'] = _build_edit_view(
                                    v3_pump.width, v3_pump.height,
                                    _MF['ob'], _MF['mod'], v3_world, _eb2)
                            _limg = _ECACHE['base'].copy()
                            _draw_cursor(_limg, _mp3[0], _mp3[1])
                            _nowe = time.monotonic()
                            _crect3 = (_mp3[0] - 6, _mp3[1] - 6,
                                       _mp3[0] + 26, _mp3[1] + 28)
                            _lrect3 = _ECACHE.get('lr')
                            if _key_e != _ECACHE.get('pushed'):
                                _ECACHE['pushed'] = _key_e
                                _ECACHE['lt'] = _nowe
                                _ECACHE['lr'] = _crect3
                                _h = v3_pump.show(_limg)
                                V3_STAT['n_shop'] += 1
                                V3_STAT['ms_shop'].append(_h['ms_total'])
                                if len(V3_STAT['ms_shop']) > 200:
                                    V3_STAT['ms_shop'].pop(0)
                            elif (_lrect3 is not None and _lrect3 != _crect3
                                  and _nowe - _ECACHE.get('lt', 0.0) > 0.04):
                                _u3 = (min(_lrect3[0], _crect3[0]),
                                       min(_lrect3[1], _crect3[1]),
                                       max(_lrect3[2], _crect3[2]),
                                       max(_lrect3[3], _crect3[3]))
                                _ECACHE['lt'] = _nowe
                                _ECACHE['lr'] = _crect3
                                _h = v3_pump.show_rect(_limg, _u3)
                                V3_STAT['n_shop'] += 1
                                V3_STAT['ms_shop'].append(_h['ms_total'])
                                if len(V3_STAT['ms_shop']) > 200:
                                    V3_STAT['ms_shop'].pop(0)
                            if (live_dump
                                    and _now - _LIVE_DUMP['t'] > 5.0):
                                _LIVE_DUMP['t'] = _now
                                _p = (live_dump if live_dump.startswith('/')
                                      else os.path.join(outdir, live_dump))
                                cv2.imwrite(_p, _limg)
                            time.sleep(0.004)
                        except Exception as _eh4:
                            V3_STAT['n_show_fail'] += 1
                            if V3_STAT['n_show_fail'] <= 3:
                                print('[道具] ★ 改磁场界面推帧失败：%s'
                                      % str(_eh4)[:140], flush=True)
                            if V3_STAT['n_show_fail'] >= 8:
                                print('[道具] 连续失败 8 次 -> **关掉 HDMI**，'
                                      '游戏照跑', flush=True)
                                try:
                                    v3_pump.close()
                                except Exception:
                                    pass
                                v3_pump = None
                    if _rawg is not None:
                        # ★★ 2026-10-05 双人（用户："预设轨迹需要两条，颜色区分"）：
                        #   每颗球（固定编号 1/2）各算**自己的一条**瞄准预览：
                        #   · 起点/方向/门槛全部走 _fire_gate（与发射同一把尺——贴线
                        #     夹回、方向 = 那颗球的最近边法线 + 该玩家的输入角）；
                        #   · 线的颜色 = **玩家色**（P1 亮绿 / P2 亮紫）——这就是"分谁"
                        #     的视觉语言（与网页两块面板同色）；
                        #   · 快扇 17 路端点：该角度能进 = 玩家色亮点、打回 = 灰；
                        #   · 每颗球独立节流（球位/该玩家输入变了才算 + 0.5s 下限）。
                        #   ★★ 商店 v2：商店画面开着时不重算（看不见，纯省账）
                        if v3_hub is not None and not _SHOPV['open']:
                            for _pk, _pang, _pspd, _pbno in (
                                    (1, v3_angle, v3_speed, v3_ball1),
                                    (2, v3_angle2, v3_speed2, v3_ball2)):
                                _pv = _PREV[_pk]
                                # ★ 后台进程算完：换线 + 锚点对齐到请求时的位姿
                                if (_PVP.get('done') is not None
                                        and _PVP.get('done') == _PVP.get('pending')
                                        and (_PVP.get('req') or {}).get('pk') == _pk):
                                    _rq = _PVP.get('req') or {}
                                    if _PVP.get('pts'):
                                        _pv['pts'] = [tuple(p) for p in _PVP['pts']]
                                        _pv['goal'] = bool(_PVP.get('goal'))
                                    _pv['kx'] = float(_rq.get('sx', _pv['kx']))
                                    _pv['ky'] = float(_rq.get('sy', _pv['ky']))
                                    _pv['kang'] = float(_rq.get('ang', _pv['kang']))
                                    _pv['kspd'] = float(_rq.get('spd', _pv['kspd']))
                                    _pv['okey'] = _rq.get('okey')
                                    _pv['t'] = time.time()
                                    _PVP['done'] = None
                                    _PVP['pending'] = None
                                    _PVP['req'] = None
                                try:
                                    _pt = next((t for t in _tracks
                                                if getattr(t, 'no', None) == _pbno),
                                               None)
                                    if _pt is None:
                                        # ★ 2026-11-01 用户实况："P1 轨迹时不时
                                        #   就消失"——丢轨/球号重排是瞬断，旧线先
                                        #   保持 2 秒；真丢了才清。
                                        if (_pv.get('pts')
                                                and _now - float(_pv.get('t') or 0)
                                                <= 2.0):
                                            continue
                                        _pv['pts'] = None
                                        _pv['fan'] = None
                                        continue
                                    # ★★★ 2026-10-06 二修（实测定案：预览重算吃掉
                                    #   ~450ms/帧——把两位控球号指到不存在的 9 号后
                                    #   帧率从 1.3 飙到 3.5，账立平）：第一修用
                                    #   round(平滑位) 仍被 ±1px 残留抖动反复点名。
                                    #   现在改**锚点阈值**——球位离上次计算点 >6px、
                                    #   或角度 >0.5°、或速度 >5 才算"变了"（静止 =
                                    #   零重算）；再叠 0.3s 限频。
                                    # ★ 2026-10-19：障碍/传送门变了也要重算——
                                    #   实物是开局后才认出来的，不把物件签名放进
                                    #   锚点，预览会一直用"还没有障碍"的旧线
                                    #   （实况"线直接穿过去"的真身）。
                                    # ★★ 2026-10-20 回归修复（用户"发射像 PPT"）：
                                    #   10-19 把障碍签名放进锚点，但实物识别每帧在抖
                                    #   （长度/角度 ±几px/几度）→ 锚点永远"变了"→
                                    #   预览每帧重算 450ms。这里改**滞回**：与上次
                                    #   预览计算时的实物值比，位置差 >40px、厚度
                                    #   >15px、角度 >15° 才重算；小抖不触发。
                                    # ★★ 多件：锚点签名纳全部 3 障碍/4 门
                                    _obs_now = (getattr(v3_world, 'obstacles', None)
                                                or [])[:3]
                                    _p0s = (getattr(v3_world, 'portals', None)
                                            or [])[:4]
                                    _ok_old = _pv.get('okey')
                                    _ok_moved = False
                                    _o_now = tuple((o.cx, o.cy, o.hw, o.hh,
                                                    math.degrees(o.ang))
                                                   for o in _obs_now)
                                    if (_ok_old is None or len(_ok_old) < 2
                                            or len(_ok_old[0]) != len(_o_now)):
                                        _ok_moved = True
                                    else:
                                        for _a, _b in zip(_o_now, _ok_old[0]):
                                            if (abs(_a[0] - _b[0]) > 40.0
                                                    or abs(_a[1] - _b[1]) > 40.0
                                                    or abs(_a[2] - _b[2]) > 40.0
                                                    or abs(_a[3] - _b[3]) > 15.0
                                                    or abs(_a[4] - _b[4]) > 15.0):
                                                _ok_moved = True
                                                break
                                    _p_now = tuple((p.x, p.y) for p in _p0s)
                                    if (_ok_old is None or len(_ok_old) < 2
                                            or len(_ok_old[1]) != len(_p_now)):
                                        _ok_moved = True
                                    else:
                                        for _a, _b in zip(_p_now, _ok_old[1]):
                                            if (abs(_a[0] - _b[0]) > 30.0
                                                    or abs(_a[1] - _b[1]) > 30.0):
                                                _ok_moved = True
                                                break
                                    _okey = (_o_now, _p_now)
                                    _moved = (abs(_pt.sx - _pv['kx']) > 6.0
                                              or abs(_pt.sy - _pv['ky']) > 6.0
                                              or abs(_pang - _pv['kang']) > 0.5
                                              or abs(_pspd - _pv['kspd']) > 5.0
                                              or _ok_moved)
                                    if not _moved:
                                        continue
                                    # ★★ 2026-10-28（用户："预瞄线还是要保留"）：
                                    #   虚线一直在——精确 lane 放后台线程；上一个还在
                                    #   算就先不发起新的（旧线继续显示）。
                                    # ★★ 2026-10-28（用户："预瞄线还是要保留"）：
                                    # ★★ 2026-10-29（用户："预瞄线不准、按键后要
                                    #   愣一秒多"）：每次位姿变化都**发给后台进程**，
                                    #   旧线继续显示，算完（~0.3s）自动换线——不再等
                                    #   停稳、也不冻主循环。
                                    if _now - _pv['t'] <= 0.15:
                                        continue
                                    _pok, _pwhy2, _pfi = _fire_gate(
                                        v3_mod, v3_H,
                                        dict(x=_pt.sx, y=_pt.sy, r=_pt.sr,
                                             cls=_pt.cls), _pang, fire_margin)
                                    if not _pok:
                                        # ★ 2026-11-01：发射门暂时不过（球略离边/
                                        #   跟踪抖）先保持旧线 2 秒，别一闪就没。
                                        if (_pv.get('pts')
                                                and _now - float(_pv.get('t') or 0)
                                                <= 2.0):
                                            continue
                                        _pv['pts'] = None
                                        _pv['fan'] = None
                                        continue
                                    _ar = _pfi['ang']
                                    _pbx, _pby = _pfi['bx'], _pfi['by']
                                    _chg = _rtb.charge_of(_pt.cls)
                                    _fld = [(b.x0, b.y0, b.x1, b.y1, b.b)
                                            for b in v3_world.blocks]
                                    _hol = [dict(h) for h in v3_world.holes]
                                    _oa = [(o.cx, o.cy, o.hw, o.hh,
                                            math.degrees(o.ang))
                                           for o in
                                           (getattr(v3_world, 'obstacles', None)
                                            or [])][:3]
                                    _pa = [(p.x, p.y, p.pair) for p in
                                           (getattr(v3_world, 'portals', None)
                                            or [])][:4]
                                    # 17 路端点（PL，~2.4ms）照旧同步刷新
                                    _pv['fan'] = _pv_fan(
                                        v3_world, v3_V, _fld, _hol,
                                        _pbx, _pby, _ar, _pspd, _chg,
                                        _oa, _pa, v3_fan, _vs.PL_BOUNCE)
                                    _pv['t'] = _now
                                    # 后台进程挂了/结果丢了：超时 2s 释放，重发
                                    if _PVP.get('pending') is not None:
                                        _pp0 = _PVP.get('proc')
                                        if (_pp0 is None or _pp0.poll() is not None
                                                or time.time()
                                                - float(_PVP.get('t_send') or 0.0)
                                                > 2.0):
                                            _PVP['pending'] = None
                                            _PVP['done'] = None
                                    if (_PVP.get('pending') is None
                                            and _pvp_start()):
                                        _PVP['id'] += 1
                                        _PVP['pending'] = _PVP['id']
                                        _PVP['t_send'] = time.time()
                                        _PVP['req'] = dict(
                                            pk=_pk, sx=_pt.sx, sy=_pt.sy,
                                            ang=_pang, spd=_pspd,
                                            okey=_okey)
                                        _rq = dict(
                                            id=_PVP['id'], bx=_pbx, by=_pby,
                                            ar=_ar, sp=_pspd, q=int(_chg),
                                            steps=_eff_steps(_pspd),
                                            nb=int(_vs.PL_BOUNCE),
                                            fld=[list(x) for x in _fld],
                                            hol=[dict(h) for h in _hol],
                                            oa=[list(x) for x in _oa],
                                            pa=[list(x) for x in _pa])
                                        try:
                                            _PVP['proc'].stdin.write(
                                                json.dumps(_rq,
                                                           separators=(',', ':'))
                                                + '\n')
                                            _PVP['proc'].stdin.flush()
                                        except Exception:
                                            _PVP['pending'] = None
                                    continue
                                except Exception as _ep:
                                    _pv['pts'] = None
                                    _pv['fan'] = None
                                    if not _pv['warned']:
                                        _pv['warned'] = True
                                        print('[预览] P%d 瞄准预览异常：%s'
                                              % (_pk, str(_ep)[:120]), flush=True)
                        # ★★ 2026-10-05（规则订正）：**真球推进**——每帧按真实流逝
                        #   时间补物理步（1/240s 一步；卡顿后一次最多补 1200 步≈5s，
                        #   防跳跃堆积）。出射/碰撞在这里出账；物理与预览逐位一致。
                        if v3_hub is not None and live_balls:
                            _now_sb = time.time()
                            # ★★ v5（2026-10-07 用户改规则）：非对战阶段**全场冻结**
                            #   （结算/局前/暂停）——只把补步基准跟上（防解冻跳步），
                            #   位置/速度/轨迹原样保留；下一局从冻结处接着飞
                            #   （LiveBall.who 记着发球人，进球仍算他的分）。
                            if (_MF['ob'] is not None
                                    and _MF['ob'].phase != 'PLAY'):
                                _STEP['t'] = _now_sb
                                _nstep = 0
                            else:
                                if _STEP['t'] is None:
                                    _STEP['t'] = _now_sb
                                _nstep = int((_now_sb - _STEP['t']) * 240.0)
                                _STEP['t'] = _now_sb
                                if _nstep > 1200:
                                    _nstep = 1200
                            if _nstep > 0:
                                _flds = [(bb.x0, bb.y0, bb.x1, bb.y1, bb.b)
                                         for bb in v3_world.blocks]
                                for _ev in _rtb.advance(
                                        live_balls, _flds,
                                        list(v3_world.holes), _nstep, side=0,
                                        obstacles=_obst_tuples(
                                            getattr(v3_world, 'obstacles', None)),
                                        portals=_portal_tuples(
                                            getattr(v3_world, 'portals', None))):
                                    _kind, _b = _ev[0], _ev[1]
                                    # ★★ M2.4：落点账（洞口自适应的"热点"数据源）——
                                    #   真球撞墙（非洞口）的位置按边记账；贴边判定用 8
                                    #   游戏像素容差；c=左右边记 y、上下边记 x（与 engine
                                    #   的 hit_marks 同口径）。每局开始在 round_start 清。
                                    if _kind == 'bounce':
                                        _bx = _b.x / 256.0
                                        _by = _b.y / 256.0
                                        _dl = _bx
                                        _dr = v3_mod.FIELD_W - _bx
                                        _dt = _by
                                        _db = v3_mod.FIELD_H - _by
                                        _dm = min(_dl, _dr, _dt, _db)
                                        if _dm <= 8.0:
                                            _ek = ('left' if _dm == _dl else
                                                   'right' if _dm == _dr else
                                                   'top' if _dm == _dt else 'bottom')
                                            _l2 = _HITS[_ek]
                                            _l2.append(_by if _ek in ('left', 'right')
                                                       else _bx)
                                            if len(_l2) > _HITS_CAP:
                                                del _l2[:len(_l2) - _HITS_CAP]
                                    if _kind == 'goal':
                                        _b.t_exit = time.time()
                                        _LIVE_ST['n_exit'] += 1
                                        # ★★ M2.2 对局记分：谁发的球谁得分（老球
                                        #   who=None 不参与对局）；进球 = 本局立即结束
                                        #   （规则书 §2：+10 分、清场、进下一局局前）。
                                        if (_MF['ob'] is not None
                                                and getattr(_b, 'who', None) in (1, 2)):
                                            _MF['ob'].on_goal(_b.who)
                                            print('[对局] ★ %s'
                                                  % _MF['ob'].msg, flush=True)
                                        _secs = _b.n_steps / 240.0
                                        _em = ('球从%s洞出射！弹了 %d 次 / 飞了 %.1fs'
                                               % (_EDGE_CN.get(_b.exit_edge, '?'),
                                                  _b.bounces, _secs))
                                        print('[进球] ★ %s' % _em, flush=True)
                                        _shot_write(dict(
                                            t=round(time.time(), 3), ok=True,
                                            why='', msg=_em, seq=_b.seq,
                                            kind='goal', bounces=_b.bounces,
                                            secs=round(_secs, 1),
                                            edge=_b.exit_edge))
                                        _EXITS.append((time.time(),
                                                       _b.x / 256.0, _b.y / 256.0))
                                    elif _kind == 'collide':
                                        _LIVE_ST['n_coll'] += 1
                                        _ob = _ev[2] if len(_ev) > 2 else None
                                        _cx = (_b.x + (_ob.x if _ob else _b.x)) / 2 / 256.0
                                        _cy = (_b.y + (_ob.y if _ob else _b.y)) / 2 / 256.0
                                        print('[碰撞] 两球相碰于 (%.0f,%.0f)'
                                              '（等质量弹性反弹）'
                                              % (_cx, _cy), flush=True)
                                        _COLLS.append((time.time(), _cx, _cy))
                                    elif _kind == 'annihilate':
                                        # ★★ 2026-10-06（规则书 §3）：红(正)撞蓝(负)
                                        #   -> 双双湮灭（照抄 engine._ball_ball）。
                                        _LIVE_ST['n_ann'] += 1
                                        _ob = _ev[2] if len(_ev) > 2 else None
                                        _ax = (_b.x + (_ob.x if _ob else _b.x)) / 2 / 256.0
                                        _ay = (_b.y + (_ob.y if _ob else _b.y)) / 2 / 256.0
                                        print('[湮灭] ★ 红蓝相撞湮灭于 (%.0f,%.0f)'
                                              '（正负电子相消，两球同时消失）'
                                              % (_ax, _ay), flush=True)
                                        _ANNIS.append((time.time(), _ax, _ay))
                                live_balls[:] = [
                                    b for b in live_balls if b.alive or
                                    (b.t_exit and time.time() - b.t_exit < 1.5)]
                                # ★ 2026-10-05 用户反馈：出射后的残影原来留 5s（"久久不散"）
                                #   → 砍到 1.5s；出射点还有绿圈留档，不会丢信息。
                        # ---- ★★ 2026-10-16 粒子引擎（--pe）：一帧节拍 ----------------
                        #   与 CPU 真球**同一根补步尺**（同样的 *240 补步、同样的
                        #   非 PLAY 冻结规则——两边粒子视觉同速）；引擎异常自停。
                        if _PEB['on'] and v3_hub is not None:
                            _now_pb = time.time()
                            if (_MF['ob'] is not None
                                    and _MF['ob'].phase != 'PLAY'):
                                _PEB['t'] = _now_pb
                                _pe_n = 0
                            else:
                                if _PEB['t'] is None:
                                    _PEB['t'] = _now_pb
                                _pe_n = int((_now_pb - _PEB['t']) * 240.0)
                                _PEB['t'] = _now_pb
                                if _pe_n > 1200:
                                    _pe_n = 1200
                            _pe_pump(_PEB, v3_world, _pe_n)
                        try:
                            _tb0 = time.perf_counter()
                            # ★ 发射模式**不画"历史轨迹"绿线**（原用途=跟踪模式看
                            #   "球从哪来/往哪走"；发射瞄准不用它——用户 2026-10-04
                            #   反馈它遮挡视野）。
                            _trail_show = [] if v3_hub is not None else trail
                            _prev_list = []
                            for _pk2 in (1, 2):
                                _pv2 = _PREV[_pk2]
                                if _pv2['pts']:
                                    _prev_list.append(dict(
                                        pts=_pv2['pts'], color=_pv2['color'],
                                        fan=_pv2['fan']))
                            if _gate:
                                # ★★ 商店/局前画面**已在上面推过**（统一门 _gate）（不依赖相机
                                #   的那块）——直播叠加这帧不做，省下 _build_live
                                pass
                            else:
                                # ★★ 2026-10-08 实况修（重下 bit 后"原图花屏"实锤）：
                                #   原图解码字序原来用启动瞬间冻结的 swap（那时恒 0，
                                #   标定后从不跟踪）——"重掷骰子"翻字序后原图假彩，
                                #   而地图有动态自检所以自愈。实测关系（早/今两例）：
                                #   **原图字序 = 地图自检字序 ^ 1**（地图与原图字节
                                #   正好相反）→ 原图解码跟着 mswap 实时推导，自愈。
                                _rsw = (swap ^ mswap ^ 1) if mswap is not None else swap
                                _limg = _build_live(_rawg, _rsw, v3_H, v3_world,
                                                    ball, _trail_show, v3_angle,
                                                    v3_speed,
                                                    fire_margin=fire_margin,
                                                    previews=_prev_list,
                                                    angle2=(v3_angle2
                                                            if v3_hub is not None
                                                            else None),
                                                    speed2=(v3_speed2
                                                            if v3_hub is not None
                                                            else None),
                                                    live_balls=((
                                                        live_balls + _PEB['balls'])
                                                        if _PEB['on']
                                                        else live_balls),
                                                    tracks=_tracks,
                                                    bno1=v3_ball1, bno2=v3_ball2,
                                                    match_line=(
                                                        _match_line_cn(_match_snapshot(
                                                            _MF['ob'], 0, _MF['mod']))
                                                        if _MF['ob'] is not None
                                                        else None),
                                                    hud=not no_hud)
                                _LT['build'].append((time.perf_counter() - _tb0)
                                                    * 1000.0)
                                if len(_LT['build']) > 200:
                                    _LT['build'].pop(0)
                                # ★ 2026-10-14：暂停中（改磁场/加洞口）叠加高亮/预览
                                if (_MF['ob'] is not None
                                        and _MF['ob'].phase in ('EDIT', 'HOLE')):
                                    _pause_overlay(_limg, v3_H, v3_world,
                                                   _MF['ob'], v3_mod, _HOLEV)
                                if (_MF['ob'] is not None
                                        and _MF['ob'].phase == 'PREP'):
                                    # ★★ 商店 v2：局前在直播图上叠「商店」入口
                                    #   + 光标（同时缩到画布尺寸——帧泵免缩放）
                                    _mpos2 = (0, 0)
                                    if v3_hub is not None:
                                        _mpos2 = (v3_hub.mx, v3_hub.my)
                                    _limg, _ent = _shop_prep_overlay(
                                        _limg, v3_pump.width, v3_pump.height,
                                        _MF['ob'], _mpos2)
                                    # 整体替换：入口框与商店画面框**不同页**，
                                    # 留着旧的会盖住新按钮的命中（dict 顺序先到先得）
                                    _SHOPV['rects'] = {'ent': _ent}
                                # ★ 2026-10-25：HDMI 去 HUD + 场区裁剪放大。
                                #   叠加已全部画在 1024x720 原图上，裁完再缩放；
                                #   瞬时道具横幅在裁剪后画（不会被裁掉）。
                                # ★★ 2026-10-31：PL 帧泵（PLPUMP=1）——透视矫正+缩放+
                                #   打包+写显存全在 PL；PS 只打包成 32 位写进源缓冲。
                                _plp_ok = False
                                if (_PLP.get('on') and _limg.shape[0] == 720
                                        and _limg.shape[1] == 1024):
                                    if no_hud:
                                        _item_banner(_limg)
                                    try:
                                        _plp_ms = _plp_show(_limg, v3_H)
                                        _plp_ok = True
                                        _h = {'kind': 'full',
                                              'ms_total': _plp_ms,
                                              'ms_resize': 0.0,
                                              'ms_diff': 0.0,
                                              'ms_push': 0.0, 'flow': None}
                                    except Exception as _epp:
                                        _PLP['err'] += 1
                                        if _PLP['err'] <= 3:
                                            print('[PL帧泵] ★ 失败，回退 PS：%s'
                                                  % str(_epp)[:120], flush=True)
                                        _PLP['on'] = False
                                if not _plp_ok:
                                    if (live_crop and _limg.shape[0] == 720
                                            and _limg.shape[1] == 1024):
                                        _limg = _live_crop_image(
                                            _limg, v3_H, crop_margin,
                                            interp=('nearest' if live_fast
                                                    else 'linear'),
                                            holes=getattr(v3_world, 'holes', None))
                                    if no_hud:
                                        _item_banner(_limg)
                                    # ★ 2026-10-23 差距2：变化区局部推帧（整帧
                                    #   变化/别的画面推过时自己退回整帧；见 _live_show）
                                    _h = _live_show(
                                        v3_pump, _limg, _LIVEPUSH,
                                        interp=('nearest' if live_fast else 'linear'))
                                _lk = _h.get('kind')
                                if _lk == 'full':
                                    V3_STAT['n_live_full'] += 1
                                elif _lk == 'rect':
                                    V3_STAT['n_live_rect'] += 1
                                else:
                                    V3_STAT['n_live_skip'] += 1
                                V3_STAT['n_live'] += 1
                                V3_STAT['ms_live'].append(_h['ms_total'])
                                if len(V3_STAT['ms_live']) > 200:
                                    V3_STAT['ms_live'].pop(0)
                                V3_STAT['ms_live_parts'].append(
                                    (_h.get('ms_resize', 0.0),
                                     _h.get('ms_diff', 0.0),
                                     _h.get('ms_push', 0.0)))
                                if len(V3_STAT['ms_live_parts']) > 200:
                                    V3_STAT['ms_live_parts'].pop(0)
                            if live_dump and _now - _LIVE_DUMP['t'] > 5.0:
                                # 验证用：每 5 秒最多存一张（写盘在板上 150~390ms，
                                # 不限频会把直播档拖成幻灯片）
                                _LIVE_DUMP['t'] = _now
                                _p = (live_dump if live_dump.startswith('/')
                                      else os.path.join(outdir, live_dump))
                                cv2.imwrite(_p, _limg)
                                print('[直播] 存图 %s' % _p, flush=True)
                        except Exception as _eh:
                            V3_STAT['n_live_fail'] += 1
                            if V3_STAT['n_live_fail'] <= 3:
                                print('[直播] ★ 推帧失败（第 %d 次）：%s'
                                      % (V3_STAT['n_live_fail'], str(_eh)[:140]), flush=True)
                            if V3_STAT['n_live_fail'] >= 8:
                                print('[直播] 连续失败 8 次 -> **关掉 HDMI**，游戏照跑',
                                      flush=True)
                                try:
                                    v3_pump.close()
                                except Exception:
                                    pass
                                v3_pump = None

            if n_frames % 30 == 0:
                el = time.time() - t_start
                fps = n_frames / el if el > 0 else 0.0
                # ★ 2026-10-28 拆账：`n_iter`=循环真跑了几圈（含被熔断/坏帧
                #   continue 掉的）；与 n_frames 差得多说明"帧均"被空转抬高。
                _iter_txt = '  循环%d/有效%d' % (n_iter, n_frames)
                if len(_FT) >= 11:
                    _inst = (len(_FT) - 1) / max(1e-3, _FT[-1] - _FT[0])
                    _iter_txt += '  即时%.1ffps' % _inst
                dt = np.array(t_det[-60:]) * 1000.0
                fz = ('  熔断%d' % _DET['n_fuse']) if _DET['n_fuse'] else ''
                _lv = ''
                if v3_hdmi_live and (_LT['grab'] or _LT['build']):
                    # ★★ 2026-10-06（用户："点发射后像PPT翻页，必须修"）：**全账拆开印**
                    #   —— 抓图+叠加+**推帧**+其余，四项相加=每帧循环均值。推帧那笔
                    #   一直记着（V3_STAT['ms_live']）只是没印出来；"其余"= 循环均值
                    #   减去已记账三项，专抓没被点名的隐藏耗时。
                    _ms_show = (float(np.mean(V3_STAT['ms_live'][-30:]))
                                if V3_STAT['ms_live'] else 0.0)
                    _ms_grab = (float(np.mean(_LT['grab'][-30:]))
                                if _LT['grab'] else 0.0)
                    _ms_build = (float(np.mean(_LT['build'][-30:]))
                                 if _LT['build'] else 0.0)
                    _ms_det = float(dt.mean()) if dt.size else 0.0
                    # ★ 2026-10-28：**其余**改成按最近 30 帧的**即时周期**算
                    #   （原来的 el/n_frames 是开机累计均值，含启动慢帧，越跑越小，
                    #    读不出当下的卡顿账）。
                    _ms_loop = (el / max(1, n_frames)) * 1000.0
                    if len(_FT) >= 11:
                        _ms_loop = ((_FT[-1] - _FT[0])
                                    / max(1, len(_FT) - 1)) * 1000.0
                    _pr = V3_STAT['ms_live_parts'][-30:]
                    _pm = ([sum(_x[i] for _x in _pr) / len(_pr)
                            for i in range(3)] if _pr else [0.0, 0.0, 0.0])
                    _v3l = (sum(V3_STAT['ms_lite']) / len(V3_STAT['ms_lite'])
                            if V3_STAT.get('ms_lite') else 0.0)
                    _lv = ('  直播 抓图%.0f+叠加%.0f+推帧%.0fms(缩%.0f 差%.0f 写%.0f)'
                           '  检测%.0f 其余%.0f  V3轻%.0f/次'
                           '  [屏%.0f 电%.0f 物%.0f 追%.0f 图%.0f 预%.0f]'
                           '（帧均%.0f  推帧全%d/局部%d/跳%d）'
                           % (_ms_grab, _ms_build, _ms_show,
                              _pm[0], _pm[1], _pm[2], _ms_det,
                              _ms_loop - _ms_det - _ms_grab - _ms_build - _ms_show,
                              _v3l,
                              _ms_mean('screen'), _ms_mean('phone'),
                              _ms_mean('phys'), _ms_mean('trk'),
                              _ms_mean('b_img'), _ms_mean('prev'),
                              _ms_loop, V3_STAT['n_live_full'],
                              V3_STAT['n_live_rect'], V3_STAT['n_live_skip']))
                if ball is not None:
                    v = ''
                    if len(trail) >= 2:
                        dx = trail[-1][0] - trail[-2][0]
                        dy = trail[-1][1] - trail[-2][1]
                        v = '  Δ=(%+.0f,%+.0f)px' % (dx, dy)
                    print('[%5.1fs] %4.1f fps  球=%s (%.0f,%.0f) r=%.0f%s  检测 %.1fms  '
                          '自愈 看门狗%d+主循环%d(升级 %d)%s%s%s'
                          % (el, fps, ball['cls'], ball['x'], ball['y'], ball['r'], v,
                             dt.mean() if dt.size else 0.0,
                             WD_STAT['n_recover'], _RESCUE['n_pulse'],
                             WD_STAT['n_pwr'] + _RESCUE['n_pwr'], fz, _lv,
                             _iter_txt), flush=True)
                else:
                    print('[%5.1fs] %4.1f fps  画面里没球  检测 %.1fms  '
                          '自愈 看门狗%d+主循环%d(升级 %d)%s%s%s'
                          % (el, fps, dt.mean() if dt.size else 0.0,
                             WD_STAT['n_recover'], _RESCUE['n_pulse'],
                             WD_STAT['n_pwr'] + _RESCUE['n_pwr'], fz, _lv,
                             _iter_txt), flush=True)

            if render_every and n_frames - last_render >= render_every:
                last_render = n_frames
                big = cv2.resize(mbgr, (BW * 8, BH * 8), interpolation=cv2.INTER_NEAREST)
                for i in range(1, len(trail)):
                    cv2.line(big, (int(trail[i - 1][0]), int(trail[i - 1][1])),
                             (int(trail[i][0]), int(trail[i][1])), (0, 255, 0), 2)
                if ball is not None:
                    cv2.circle(big, (int(ball['x']), int(ball['y'])),
                               max(4, int(ball['r'])), (0, 255, 255), 2)
                cv2.putText(big, '%.1f fps' % (n_frames / max(1e-3, time.time() - t_start)),
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
                cv2.imwrite(outdir + '/game_d.png', big)

    el = time.time() - t_start
    dt = np.array(t_det) * 1000.0 if t_det else np.array([0.0])
    print('')
    print('==== 长跑汇总（%.0fs）====' % el)
    print('  有效帧 %d（%.1f fps），找到球 %d 帧（%.0f%%）'
          % (n_frames, n_frames / max(1e-3, el), n_hit,
             100.0 * n_hit / max(1, n_frames)))
    print('  检测耗时 均值 %.1fms  P90 %.1fms  最大 %.1fms（读回另计 ~0.9ms）'
          % (dt.mean(), float(np.percentile(dt, 90)), dt.max()))
    if _DET['n_err']:
        print('  ★ 检测异常 %d 次（这些帧被跳过，没让长跑崩掉；上面第一条有 traceback）'
              % _DET['n_err'])
    if _DET['n_fuse']:
        _rcs = np.array(_DET['rc'], np.float64) if _DET['rc'] else np.array([0.0])
        print('  ★ 坏帧熔断 %d 帧（%.0f%%）：候选多 + 相邻行对不上 -> 这些帧的球不可信，'
              '没进轨迹也没喂 V3' % (_DET['n_fuse'],
                                    100.0 * _DET['n_fuse'] / max(1, n_frames)))
        print('     相邻行差均值 中位 %.1f（真场景 5~9 / 撕裂帧 ~18）' % float(np.median(_rcs)))
    # ★ 2026-09-29 修的口径 bug：这一行原来只报**看门狗**的账（WD_STAT），
    #   而主循环自己那套救援阶梯（脉冲 ctrl[3] / 升级传感器断电）另记在 _RESCUE 里
    #   —— 实测 60s 那趟日志里明明有两次脉冲 + 一次升级，汇总行却写"自愈 0 次"。
    #   现在就报**两者之和**，并分开列出来（数对不上比没有数更坏）。
    print('  自愈 %d 次（看门狗 %d + 主循环 %d），其中升级到"传感器断电重上电" %d 次；'
          '累计卡死 %.1fs'
          % (WD_STAT['n_recover'] + _RESCUE['n_pulse'],
             WD_STAT['n_recover'], _RESCUE['n_pulse'],
             WD_STAT['n_pwr'] + _RESCUE['n_pwr'], WD_STAT['stalled_total']))
    de = np.diff([p[3] for p in trail]) if len(trail) > 1 else np.array([0.0])
    if de.size > 1:
        print('  轨迹点 %d 个，相邻点时间间隔 中位 %.0fms（= 游戏循环周期）'
              % (len(trail), float(np.median(de)) * 1000.0))

    # ---- ★ P1：V3 实时预测的记账（PL 的账 / 板载 CPU 的账 分开报）----
    if (V3_STAT['n'] or V3_STAT['n_out'] or V3_STAT['n_fail']
            or v3_hub is not None or V3_STAT['n_show'] or V3_STAT['n_show_fail']
            or V3_STAT['n_live'] or V3_STAT['n_live_fail'] or V3_STAT['n_live_noimg']
            or V3_STAT['n_review'] or V3_STAT['n_trk_rej']):
        def _arr(k):
            return np.array(V3_STAT[k], np.float64) if V3_STAT[k] else np.array([0.0])

        def _p90(k):
            return float(np.percentile(_arr(k), 90))

        every = v3_every_cfg
        print('')
        print('==== ★ V3 实时预测（P1：游戏循环里每次拿跟踪球位喂一簇）====')
        print('  预测 %d 次（轨迹样本不足跳过 %d 次；球在场区外跳过 %d 次；失败 %d 次）%s'
              % (V3_STAT['n'], V3_STAT['n_skip'], V3_STAT['n_out'],
                 V3_STAT['n_fail'],
                 '' if v3_every else '  ← 中途因连续失败被关掉'))
        if v3_hub is not None:
            print('     （发射模式：以上"预测"**只在按下发射时发生** —— 平时一帧不跑）')
        if V3_STAT['n'] == 0:
            # ★ 同族的老毛病：什么都不跑 = 什么都不报，看起来"很快"。必须喊出来。
            print('  ★★ 一次预测都没跑成 —— 下面的耗时全是 0，**不是快，是没有**。')
            print('     球整场都在场区外？先看 game_d.png 里球画在哪、标定对不对')
        print('  PL AXI 往返（写场景->启动->读回）   均值 %.2fms  P90 %.2fms'
              % (_arr('ms_axi').mean(), _p90('ms_axi')))
        if v3_hdmi_live:
            # ★ 2026-10-01 夜提速后的新口径：直播档分**两种 tick**，账分开报 ——
            #   提速前每次预测都现算折线+渲染（565ms/6 帧，摊到每帧 94ms）；
            #   现在平时只有 ~3ms 的轻 tick，那 565ms 只在真进球那一刻花一次。
            #   ★ 混着报均值 = 骗人（轻 tick 会把 457ms 平均成个位数）。
            _lite = _arr('ms_lite')
            _full = _arr('ms_total_full') if V3_STAT['ms_total_full'] else np.array([0.0])
            _n_lite = V3_STAT['n'] - V3_STAT['n_full']
            print('  ★ 直播档提速（轻/重两档）：轻 tick %d 次（只读 PL 判定）均值 %.1fms；'
                  '重 tick %d 次（现算折线+渲染=复盘图）均值 %.0fms'
                  % (_n_lite, _lite.mean(), V3_STAT['n_full'], _full.mean()))
            if V3_STAT['n_full']:
                print('      重 tick 明细：折线 %.0fms / 渲染 %.1fms / 写盘 %.1fms%s'
                      % (_arr('ms_arcs').mean(), _arr('ms_render').mean(),
                         _arr('ms_write').mean(),
                         '' if (v3_dump and v3_dump.lower() not in ('none', '-'))
                         else '（本趟没写盘）'))
            _tick_mean = (_lite.sum() + _full.sum()) / max(1, V3_STAT['n'])
            print('  **一簇的总账**（轻 %d 次 + 重 %d 次）平均 %.1fms/次，'
                  '每 %d 个有效帧一次 -> 摊到每帧 %.1fms'
                  % (_n_lite, V3_STAT['n_full'], _tick_mean, every, _tick_mean / every))
            if v3_hub is not None:
                print('     （发射模式：上面"每 %d 帧摊一次"的分母**不成立** —— '
                      '这一趟的预测是按**杆**触发的）' % every)
        else:
            print('  %s   均值 %.0fms  P90 %.0fms'
                  % ('折线：板载 CPU 跑定点黄金模型画 17 条' if v3_arcs else
                     '折线：只有临界那条（浮点预览）', _arr('ms_arcs').mean(),
                     _p90('ms_arcs')))
            # ★ 2026-09-29 夜修口径 bug：这一行原来写"渲染 + 写盘"却只报了 ms_render
            #   （ms_write 根本没进 V3_STAT）—— 而板上**写盘才是最贵的一笔**（PNG 编码
            #   210~380ms）。现在两笔分开报，谁也不许躲在别人后面。
            print('  渲染（轨迹束+标注+放大）            均值 %.1fms  P90 %.1fms'
                  % (_arr('ms_render').mean(), _p90('ms_render')))
            print('  写盘（PNG 编码 + 落盘）             均值 %.1fms  P90 %.1fms%s'
                  % (_arr('ms_write').mean(), _p90('ms_write'),
                     '' if (v3_dump and v3_dump.lower() not in ('none', '-'))
                     else '  ← 本趟没写盘（--v3-dump none）'))
            print('  **一次预测总计**                   均值 %.0fms  每 %d 个有效帧一次'
                  ' -> 摊到每帧 %.1fms'
                  % (_arr('ms_total').mean(), every, _arr('ms_total').mean() / every))
        print('  判定分布（17 路均值）：绿 %.1f / 红 %.1f；出到临界角 %d 次'
              % (float(np.mean(V3_STAT['goal'])), float(np.mean(V3_STAT['bounce'])),
                 len(V3_STAT['crit'])))
        if v3_hub is not None:
            # ★ 输入层的账也要报出来：改了 N 次 / 读失败 N 次。读失败>0 就是
            #   "文件被删了/写坏了"，此时输入层**保持上一次的值**（口径见 v3_input.py）。
            print('  输入层（%s）：改 %d 次，读失败 %d 次，最后来自 %s'
                  % (v3_hub.path, v3_hub.n_change, v3_hub.n_bad, v3_hub.src))
            print('     当前 %.1f° / %.0f px/s   ← 手机 `v3_input.py serve` /'
                  ' 键盘 `v3_input.py key` 都写这一份' % (v3_angle, v3_speed))
            # ★★ 发射模式的账（用户①②③三条）：请求 -> 发射/拒绝；三个拒绝原因
            #   分开列（口径不许只报好看的）；杆数是要点：没人按 = 0 杆 = 0 预测。
            print('  ★ 发射模式（输入层开着）：请求 %d 次 -> 发射 %d 杆；'
                  '拒绝：没球 %d / 出界 %d；冷却忽略 %d'
                  % (_FIRE['n_req'], _FIRE['n_ok'], _FIRE['n_noball'],
                     _FIRE['n_out'], _FIRE['n_cool']))
            if _MF['ob'] is not None:
                # ★ M2.2 对局账（要平：口径不许只报好看的）
                print('  ★ 对局（--match）终态：%s' % _MF['ob'].status_line())
                print('     对局拒发：暂停 %d / 结算中 %d / 弹药不足 %d'
                      % (_FIRE['n_paused'], _FIRE['n_round_end'],
                         _FIRE['n_no_ammo']))
            if _FIRE['n_ok']:
                print('     发射结果（真球，2026-10-05 订正）：发射 %d 颗 / 已出射 %d 颗'
                      ' / 球球碰撞 %d 次 / 异号湮灭 %d 次；场上还剩 %d 颗；'
                      '最近一杆的结果在 %s'
                      % (_LIVE_ST['n_fired'], _LIVE_ST['n_exit'],
                         _LIVE_ST['n_coll'], _LIVE_ST['n_ann'],
                         sum(1 for _b in live_balls if _b.alive), _SHOT_PATH))
            elif _FIRE['n_req']:
                print('     ★ 一杆都没发出去 —— 门槛是"球身压着红色场区"'
                      '（场区内任意位置可发；球心在场区外/球身不沾场区才会拒）')
        if (V3_STAT['n_show'] or V3_STAT['n_show_fail'] or V3_STAT['n_live']
                or V3_STAT['n_live_fail'] or V3_STAT['n_live_noimg']):
            # ★ P2：HDMI 的账单独报 —— 这笔是**板载 CPU 推帧**（缩放+打包+拷入+切换+
            #   等一帧），与 PL 无关；和"写盘"也不是同一笔（上屏不用编码 PNG）。
            if v3_hdmi_live:
                # ★ 直播档：推的是实拍+叠加（每圈一笔：抓图+解码+缩放+打包）——
                #   这笔账直接顶在游戏循环的周期上，"掉到几 fps"要能一眼算出来。
                print('  ★ HDMI 直播档（实拍+叠加，单缓冲原地写）：推帧 %d 次'
                      '（均值 %.1fms/次），推帧失败 %d 次，相机没出图 %d 次'
                      '（屏上留上一帧，不算失败）%s'
                      % (V3_STAT['n_live'],
                         _arr('ms_live').mean(), V3_STAT['n_live_fail'],
                         V3_STAT['n_live_noimg'],
                         '，已中途关掉' if v3_pump is None else ''))
                print('     判读：整段屏幕**不黑不闪**才是直播档成立；'
                      '一笔推帧=抓图+解码+缩放+打包，直接吃 fps（降它先换 INTER_NEAREST）')
                print('     直播每帧另两笔：抓图 %.1fms + 叠加 %.1fms'
                      '（与上面的"检测 ms"并排看，就是每帧的主账）'
                      % (float(np.mean(_LT['grab'])) if _LT['grab'] else 0.0,
                         float(np.mean(_LT['build'])) if _LT['build'] else 0.0))
            else:
                print('  HDMI 直出（帧泵%s）：推帧 %d 次，失败 %d 次%s'
                      % ('，已中途关掉' if v3_pump is None else '',
                         V3_STAT['n_show'], V3_STAT['n_show_fail'],
                         '' if not V3_STAT['n_show'] else
                         '，推帧均值 %.1fms/次' % _arr('ms_show').mean()))
            if v3_pump is not None and (V3_STAT['n_show'] or V3_STAT['n_live']):
                print('     %s' % v3_pump.summary())
        if ((V3_STAT['n_review'] or V3_STAT['n_shot_goal'] or _review_given)
                and v3_hub is None):
            # ★ 发射模式下这一行不适用（那里每次发射都出图，不是"上升沿复盘"），
            #   杆的账在上面"发射模式"那几行里单列 —— 两套口径不许混。
            # ★ 第③点：复盘触发是**上升沿**（一直瞄着洞不重复弹），所以
            #   n_review ≤ n_shot_goal —— 两个数都要报，好对账（口径不许只报好看的）。
            print('  ★ 进球复盘（正中那一路=球员角度 判 goal=进洞）：会进洞的预测 %d 次'
                  ' -> 屏幕出复盘图 %d 次（每次贴 %.0fs）'
                  % (V3_STAT['n_shot_goal'], V3_STAT['n_review'], v3_review))
        if V3_STAT['n_trk_rej']:
            # ★ 第②点：这些帧"候选全在场区外" -> 当没球（老的代码会拿支架/网线当球）
            print('  ★ 追踪过滤（第②点）：%d 帧"候选全在场区外"没球（场外大块被拒；'
                  '容差 %.0fpx，--track-margin 调）'
                  % (V3_STAT['n_trk_rej'], track_margin))
        if v3_dump and v3_dump.lower() not in ('none', '-'):
            print('  图：%s/%s（每次预测覆盖写；取最后一张看现场）' % (outdir, v3_dump))
        else:
            print('  图：**没写盘**（--v3-dump none）—— 省掉写盘那几十 ms，'
                  '接屏幕/现场演示用这档')
        print('  ★ 口径：**PL 的账只有 %.2fms**（AXI 往返）；折线 %.0fms / 渲染 %.1fms /'
              ' 写盘 %.1fms 全是**板载 CPU 跑 Python+cv2**，不算 PL 的账'
              % (_arr('ms_axi').mean(), _arr('ms_arcs').mean(),
                 _arr('ms_render').mean(), _arr('ms_write').mean()))
        if v3_hdmi_live:
            print('     （直播档提速后：折线+渲染只在"真进球那一刻"花一次，不再计进'
                  '每帧；现在每帧的大头 = 检测 + 帧泵推帧 + 抓图/叠加）')
        else:
            print('     （渲染和写盘这两笔在板上比 PL 贵两个数量级 —— 3fps 的真凶是它们）')
    # ★ 2026-10-25 收工退出：若停在加球选择页，切回状态页再关串口
    if screen_on and _screen_out is not None:
        try:
            _screen_out.set_page('index')
            _screen_out.close()
            time.sleep(0.15)          # 让后台线程把 page index 发出去
        except Exception:
            pass
    return 0


_RESCUE = {'n': 0, 't_win': 0.0, 'in_win': 0, 'n_pulse': 0, 'n_pwr': 0, 't_last': 0.0}
_DET = {'n_err': 0, 'n_fuse': 0, 'rc': []}   # rc = 每帧的相邻行差均值（只留最近 500 个）


# ==================================================================== ★ P1：V3 实时预测
# 为什么在这里做（2026-09-28 深夜定妆照之后）：V3 原来只活在 `v3_scan.py` 的出图脚本里
# —— 拍一张静态图，45 倍那行字是**印在图上的**，观众看不到它在动。游戏循环里
# **一句预测都没有**（原来只追轨迹）。P1 = 把 V3 接进来：每 K 个有效帧，拿**当前球位**
# 喂一次 V3，当场画出这一簇 17 路的判定（绿出射/红打回/橙临界），于是"PL 12ms 算完
# 一簇"变成屏幕上每秒刷新两三次的事实。
#
# 口径（与 v3_scan.py 完全一致，两处共用同一份函数）：
#   · 球位/速度：相机像素 -> 游戏坐标 走 v3_scan.cam_to_game（标定单应，与相机模式同一份）
#   · 入射角/速度：默认走**按键输入值**（规则：S/W 步进 2°、A/D 步进 20px/s，球是"摆"在
#     边界上的，不是推出去的）——`--v3-track-angle` / `--v3-speed 0` 才用跟踪值
#   · ★★ P2（2026-09-30）：上面那个"按键输入值"原来**只有命令行参数**（跑起来就钉死了）。
#     `--v3-input <路径|auto>` 打开**输入层**（v3_input.py）：手机网页和有线键盘两个源
#     写同一份状态文件（默认 /dev/shm/v3_input.json），游戏循环每帧 poll 一次。
#     不带这个参数 = 一个字节都不变。<br> ★ 千万别再退回"两个源各改各的全局变量"——
#     演示时手机和键盘会互相覆盖，而且谁最后生效取决于调度的运气（最难查的那种）。
#   · 弹跳上限恒为 v3_scan.PL_BOUNCE(0)：RTL 的 max_bounce 是死输入，写别的值两边必然对不上
V3_STAT = {'n': 0, 'n_fail': 0, 'n_skip': 0, 'n_out': 0,
           'ms_axi': [], 'ms_arcs': [], 'ms_render': [], 'ms_write': [], 'ms_total': [],
           # ★ 2026-10-01 夜提速：直播档把一次预测拆成两种 tick ——
           #   轻 tick（只读 PL 判定，不折线/不渲染，~3ms）平时每 K 帧一次；
           #   重 tick（现算折线+渲染出复盘图，~0.6s）**只在真进球那一刻**。
           #   ms_arcs/ms_render/ms_write/ms_total_full 只装重 tick（stst 档全是重 tick，
           #   口径与以前一模一样）；ms_lite 装轻 tick 的总耗时。混着报均值 = 骗人。
           'n_full': 0, 'ms_lite': [], 'ms_total_full': [],
           'goal': [], 'bounce': [], 'crit': [],
           # ★ P2：HDMI 直出的账（n_show 这个名字原来就占着，就是留给它的）
           'n_show': 0, 'n_show_fail': 0, 'ms_show': [],
           # ★★ 2026-10-08 商店 v2：商店画面推帧的账（n_shop/ms_shop）
           'n_shop': 0, 'ms_shop': [],
           # ★★ 2026-10-01 夜（用户①②③三点）的账：
           #   n_trk_rej  追踪过滤掉"候选全在场区外"的帧数（②：场外大块被拒）
           #   n_shot_goal 这一杆（正中那一路）判 goal（进洞）的预测数（③的分子）
           #   n_review   复盘图实际触发次数（上升沿，≤ n_shot_goal）；n_live 直播推帧数
           #   n_live_fail 推帧失败；n_live_noimg 相机没出图（屏上留上一帧，不算失败）
           'n_trk_rej': 0, 'n_shot_goal': 0, 'n_review': 0,
           'n_live': 0, 'n_live_fail': 0, 'n_live_noimg': 0, 'ms_live': [],
           # ★ 2026-10-23 差距 2：直播推帧三态（整帧/局部/跳过）——板上验收看这个
           'n_live_full': 0, 'n_live_rect': 0, 'n_live_skip': 0,
           # 推帧三段账：[(resize, diff, push)] —— 板上一眼看出瓶颈在哪
           'ms_live_parts': []}

# ★★ 第③点：进球复盘的状态机。img=要贴的轨迹图（贴完=屏上那份）；until=贴到什么时候；
#   prev=上一次预测里"这一杆会进洞"吗（用来做**上升沿**：一直瞄着洞不重复弹）；
#   n=触发次数（汇总用）。只有 live 档会碰它。
_REV = {'img': None, 'until': 0.0, 'prev': False, 'n': 0}
# ★★ 2026-10-29 改磁场俯视图缓存（同 _HCACHE 的套路：内容变整帧/光标动局部）
_ECACHE = {'key': None, 'base': None, 'pushed': None, 'lt': 0.0, 'lr': None}
# ★★ 2026-10-04：发射模式的**实时瞄准预览**缓存（用户："调滑条时轨迹要跟着变"）。
#   循环里按需重算（输入/球位**变了才算** + 0.5s 节流），_build_live 只负责把
#   preview 的 game 坐标点列画成虚线（绿=会进洞 / 橙=打回）。
_PREV = {1: {'kx': 1e9, 'ky': 1e9, 'kang': 1e9, 'kspd': 1e9, 't': 0.0,
             'pts': None, 'goal': None, 'fan': None,
             'color': (0, 230, 0),          # 玩家1 = 亮绿（与网页 P1 同色）
             'warned': False},
         2: {'kx': 1e9, 'ky': 1e9, 'kang': 1e9, 'kspd': 1e9, 't': 0.0,
             'pts': None, 'goal': None, 'fan': None,
             'color': (255, 60, 255),       # 玩家2 = 亮紫（与网页 P2 同色）
             'warned': False}}
# ★★ 发射模式（2026-10-01 深夜，用户①②③三条）的账：
#   last=已消化的 fire 计数（**只认上升沿**；键位见 v3_input.py 的 InputHub.fire）；
#   n_req 请求数 / n_ok 真发出去的杆数 / 三个拒绝原因**分开数**（不许混成一个数）；
#   n_goal,n_bounce = 发射后正中那一路的判定（进洞/打回）；n_show 上屏次数；
#   n_cool 连点被冷却挡下的次数；t_last 上一杆的时刻（1.5s 冷却用）。
_FIRE = {'last1': 0, 'last2': 0, 'n_req': 0, 'n_ok': 0, 'n_noball': 0,
         'n_out': 0, 'n_inside': 0, 'n_goal': 0, 'n_bounce': 0, 'n_show': 0,
         'n_cool': 0, 't_last1': 0.0, 't_last2': 0.0,
         # ★ M2.2（--match）对局拒发账：全场暂停 / 本局已结束 / 该色弹药用完
         'n_paused': 0, 'n_round_end': 0, 'n_no_ammo': 0}
_LIVE_DUMP = {'t': 0.0}      # --live-dump 的限频闸（每 5s 最多一张）
_LIVE_ST = {'n_fired': 0, 'n_exit': 0, 'n_coll': 0, 'n_ann': 0}   # ★ 真球账（含 10-06 湮灭）
_STEP = {'t': None}                                   # 真球按帧补步的时间基准
_DBG = {'t': 0.0}                                     # 候选清单日志的限频闸（15s）
_BALLS = {'t': 0.0}                                   # ★ 球列表写文件的限频闸（0.5s）
_SEL = {'sup1': 0, 'sdn1': 0, 'sup2': 0, 'sdn2': 0}   # ★ 选球键计数基准（M2.1 键盘 Z/X、,/.）
_EXITS = []                                           # [(t, x, y)] 出射闪光（画 4s）
_COLLS = []                                           # [(t, x, y)] 碰撞闪光（画 2s）
_ANNIS = []                                           # [(t, x, y)] 湮灭闪光（画 2s，白圈）
# ★ 直播档每帧两笔的计时（2026-10-01 夜加，找 1.6fps 那 ~350ms 的没记账部分）：
#   grab = _grab（VDMA 读回一整帧原图）、build = _build_live（raw_to_bgr + 叠加）
_LT = {'grab': [], 'build': []}


def _ms_add(key, t0):
    """★ 2026-10-28 卡顿拆账：把一段耗时记进 _LT[key]（保留最近 200 个）。"""
    try:
        _LT.setdefault(key, []).append(
            (time.perf_counter() - float(t0)) * 1000.0)
        if len(_LT[key]) > 200:
            _LT[key].pop(0)
    except Exception:
        pass


def _ms_mean(key, n=30):
    _v = _LT.get(key) or []
    return (float(np.mean(_v[-n:])) if _v else 0.0)
# ★★ 2026-10-23（差距 2 流畅化）：直播推帧的**变化区局部更新**状态。
#   病根：直播档单缓冲 ~3fps 整帧原地写 -> 每帧一道撕裂带（用户"晃手屏幕闪"）。
#   做法：把 1024x720 的叠加图 resize 到帧泵尺寸（1280x720，与现在的拉伸外观
#   完全一致），与"上一张已上屏的图"做分块差分，只 show_rect 变化块；整帧
#   变化（换场景/曝光跳变/别的画面推过）才走整帧 show。每 `full_every` 秒
#   强制整帧刷新一次，防止小噪声长期累积导致 prev 漂移。
#   `pump.n_show` 快照是"上一笔推帧是不是我们推的"的判据：商店/瞄准图/复盘图
#   推过之后 n_show 会变，下一帧直播自动整帧重推（不会把合成画面残留留在边上）。
_LIVE_DIFF = {
    # 差分在**降采样小图**上做（1280x720 全分辨率 max(axis=2) 在 A9 上要十几 ms）。
    # ★ 2026-10-23 板上实测：320x180 INTER_AREA 要 67ms；160x90 降到 39ms。
    'thresh': int(os.environ.get('LIVE_DIFF_THRESH', '12')),     # 小图差值阈值
    'cell': int(os.environ.get('LIVE_DIFF_CELL', '1')),          # 小图池化格边长
    'target_w': int(os.environ.get('LIVE_DIFF_TARGET_W', '160')),  # 小图目标宽
    # ★ 2026-10-23 板上验收：阈值 0.6 时快晃仍偶尔撕（中大型变化走前台局部写）；
    #   降到 0.10 后快晃不闪 —— 大变化强制走 live2 双缓冲换页。
    'max_frac': float(os.environ.get('LIVE_DIFF_MAXFRAC', '0.10')),
    # ★ 2026-10-23 实测：相机噪声会碎成 ~38 个连通域（总面积仅 1.6%），
    #   上限 8 会让每一帧都退回整帧推 —— 放宽到 48；小块写入仍是微秒级。
    'max_rects': int(os.environ.get('LIVE_DIFF_MAXRECTS', '48')),
    'full_every': float(os.environ.get('LIVE_FULL_EVERY', '5.0')),  # 秒
    'area': (os.environ.get('LIVE_DIFF_INTERP', 'area') != 'nearest'),
}
_LIVEPUSH = {'prev_s': None, 'cur_s': None, 'buf': None, 't_full': 0.0,
             'n_show_after': -1, 'n_full': 0, 'n_rect': 0, 'n_skip': 0,
             'kind': None, 'frac': 0.0}

# ★★ 2026-10-31 球的高频插值叠加（SMOOTH=1 才开，默认关 = 与旧行为逐字节相同）
#   问题：整帧 340ms（2.8fps），球在屏幕上"一跳一跳"。
#   做法：整帧照旧 2.8fps 推；**飞行中的球**改由一个后台线程按 ~30ms 节拍在
#         framebuffer 上局部重画（show_rect 微秒级）—— 球看起来是滑的。
#   范围：**只在有球在飞时启用**；摆球/瞄准/改磁场时线程什么都不做（零风险）。
#   安全：线程只读共享状态、异常自己吞；连续出错自动关掉（退回旧的整帧画法）。
_SM = {'on': False, 'stop': False, 'gen': 0, 'frame': None, 'M': None,
       'pump': None, 'balls': [], 'evt': None, 'n': 0, 'err': 0,
       'full': False}


def _smooth_loop():
    """后台线程：把"飞行中的球"画到新位置（局部刷新），让球看起来是滑的。"""
    import threading
    own = None
    last_gen = -1
    prev_rect = None
    while not _SM.get('stop'):
        try:
            ev = _SM.get('evt')
            if not _SM.get('on'):
                if ev is not None:
                    ev.wait(0.1)
                else:
                    time.sleep(0.05)
                if ev is not None:
                    ev.clear()
                continue
            fr = _SM.get('frame')
            Mm = _SM.get('M')
            pump = _SM.get('pump')
            balls = list(_SM.get('balls') or [])
            if fr is None or Mm is None or pump is None or not balls:
                if ev is not None:
                    ev.wait(0.05)
                    ev.clear()
                else:
                    time.sleep(0.03)
                continue
            g = _SM.get('gen')
            if g != last_gen:
                # 新整帧：整幅重拷一份当画布（1280x720x3 ≈ 2.7MB，约 2ms）
                own = fr.copy()
                last_gen = g
                prev_rect = None
            if own is None:
                time.sleep(0.02)
                continue
            now = time.time()
            pts = []
            for b in balls:
                _sq = b.get('seq') or []
                if len(_sq) >= 2:
                    # ★ 精确航点：按时间插值（引擎步进，反弹/进洞都准）
                    if now <= _sq[0][0]:
                        cx, cy = _sq[0][1], _sq[0][2]
                    elif now >= _sq[-1][0]:
                        cx, cy = _sq[-1][1], _sq[-1][2]
                    else:
                        cx, cy = _sq[-1][1], _sq[-1][2]
                        for _i in range(len(_sq) - 1):
                            if _sq[_i][0] <= now < _sq[_i + 1][0]:
                                _a = ((now - _sq[_i][0])
                                      / max(1e-6, _sq[_i + 1][0] - _sq[_i][0]))
                                cx = _sq[_i][1] + (_sq[_i + 1][1] - _sq[_i][1]) * _a
                                cy = _sq[_i][2] + (_sq[_i + 1][2] - _sq[_i][2]) * _a
                                break
                else:
                    dt = now - float(b.get('t') or now)
                    if dt < 0.0:
                        dt = 0.0
                    if dt > 0.16:        # 没航点时退化为线性外推（封顶 160ms）
                        dt = 0.16
                    cx = b['cx'] + b['vx'] * dt
                    cy = b['cy'] + b['vy'] * dt
                _q = [[[cx, cy]]]
                _te = b.get('te')
                if _te is not None:
                    _q.append([_te[0], _te[1]])
                _pp = cv2.perspectiveTransform(
                    np.array(_q, np.float32), Mm)
                p = _pp[0][0]
                _ts = (int(_pp[1][0][0]), int(_pp[1][0][1])) if len(_q) > 1 else None
                pts.append((int(p[0]), int(p[1]), int(b['r']),
                            b['col'], _ts))
            H, W = own.shape[:2]
            x0 = y0 = 10 ** 9
            x1 = y1 = -1
            for (px, py, r, _c, _ts) in pts:
                x0 = min(x0, px - r - 4); y0 = min(y0, py - r - 4)
                x1 = max(x1, px + r + 6); y1 = max(y1, py + r + 6)
                if _ts is not None:
                    x0 = min(x0, min(px, _ts[0]) - 4)
                    y0 = min(y0, min(py, _ts[1]) - 4)
                    x1 = max(x1, max(px, _ts[0]) + 5)
                    y1 = max(y1, max(py, _ts[1]) + 5)
            if prev_rect is not None:
                x0 = min(x0, prev_rect[0]); y0 = min(y0, prev_rect[1])
                x1 = max(x1, prev_rect[2]); y1 = max(y1, prev_rect[3])
            x0 = max(0, x0); y0 = max(0, y0)
            x1 = min(W, x1); y1 = min(H, y1)
            if x1 > x0 and y1 > y0:
                # 从**最新整帧**复原这块（整帧里没有球头：_build_live 只画拖尾）
                own[y0:y1, x0:x1] = fr[y0:y1, x0:x1]
                for (px, py, r, col, _ts) in pts:
                    if _ts is not None:
                        # ★ 拖尾末端 -> 球头：补上这段，拖尾就不会"追不上球"
                        cv2.line(own, _ts, (px, py), col, 3)
                    cv2.circle(own, (px, py), r, col, -1)
                    cv2.circle(own, (px, py), r, (40, 40, 40), 2)
                pump.show_rect(own, (x0, y0, x1, y1))
                prev_rect = (x0, y0, x1, y1)
                _SM['n'] += 1
                if _SM['n'] % 200 == 1:
                    print('[平滑] 局部刷新 %d 次（球头 %d 个）'
                          % (_SM['n'], len(pts)), flush=True)
            if ev is not None:
                ev.wait(0.03)
                ev.clear()
            else:
                time.sleep(0.03)
        except Exception:
            _SM['err'] += 1
            if _SM['err'] > 20:
                # 连续出错：关掉自己，退回旧画法（主循环下一帧会重新画球头）
                _SM['on'] = False
            time.sleep(0.1)


def _sm_start(pump):
    """按 SMOOTH=1 启动插值线程（默认关）。"""
    import threading
    if _SM.get('th') is not None:
        return True
    _SM['evt'] = threading.Event()
    _SM['pump'] = pump
    _SM['on'] = True
    _SM['stop'] = False
    _SM['th'] = threading.Thread(target=_smooth_loop, daemon=True,
                                 name='smooth_ball')
    _SM['th'].start()
    print('[平滑] 球插值叠加**开**（只在球飞行时局部刷新；SMOOTH=0 可关）',
          flush=True)
    return True


def _v3_live(world, V, Hmm, ball_px, vel_px, trail_px, radius_game, outdir, dump,
             ang_in=0.0, spd_in=400.0, fan_half=12.0, steps=600, win=8,
             arcs=True, track_angle=False, quiet=False, want_img=True, tag='',
             charge=1):
    """★ P1：跑一次 V3 预测（+ 可选渲染出图）。返回计时/结果字典（喂给 V3_STAT 记账）。

    参数都是**相机像素**（球位/速度/轨迹），映射在函数里统一走 cam_to_game ——
    调用方（游戏循环）不需要知道标定这回事，也不可能和相机模式用出两套映射。

    ★ `vel_px`（速度，供拟合/跟踪角用）与 `trail_px`（画尾巴用的历史点）**是两件事**：
      速度只要最近 6 点（噪声小），尾巴要长（几十点才看得见）。第一版把两者混用，
      尾巴缩成几个像素、图上根本看不出来。

    ★★ `want_img=False` = **轻 tick**（2026-10-01 夜提速，直播档平时走这条）：
      只发 PL 事务、读回判定，**不算折线、不渲染、不写盘**（那三笔加起来 ~565ms，
      而直播时屏幕看的是实拍叠加，那张仿真图只有"进球复盘"才用得上）。
      返回的 dict 里 `full=False`、`img=None`、折线/渲染/写盘三个耗时都是 0；
      判定类字段（shot/n_goal/n_bounce/crit/margin）与重 tick 完全一样
      —— 复盘触发就靠它们，触发那一刻调用方再带 `want_img=True` 现算那张图。
    """
    import v3_scan                                   # 局部导入：--v3 不开就不碰它
    bx, by, gvx, gvy, ang_tr = v3_scan.cam_to_game(Hmm, ball_px[0], ball_px[1],
                                                   vel_px[0], vel_px[1])
    ang = (float(ang_tr) if track_angle
           else _ui_ang_to_phys(ang_in))
    # 显示用界面角（0=右/逆时针）：日志/复盘与手机同一口径
    ang_ui = ((float(ang_in) % 360.0) if not track_angle
              else ((-float(ang_tr)) % 360.0))
    spd = float(spd_in) if spd_in > 0 else float((gvx * gvx + gvy * gvy) ** 0.5)
    if not v3_scan.in_field(bx, by):
        # ★★ 场外的球喂给 PL = 某一路 FSM 卡死 + 汇聚 done 永不置位（实测那串
        #    "2002 ms" 就是这么来的，见 v3_scan.in_field 的对照实验）。
        #    相机模式里误检/球滚出纸面很常见，所以就地拒绝：**一次 PL 事务都不发**，
        #    也不出图（宁可不画，也不画一张 16/17 路的假图）。
        return dict(skip='out', bx=bx, by=by, ang=ang, ang_ui=ang_ui,
                    spd=spd, ang_tr=ang_tr, gv=(gvx, gvy), note='')
    # ★★ 2026-10-19 布置 PLACE：PL 场景带 AABB 障碍 + 方形传送门
    _oa = [(o.cx, o.cy, o.hw, o.hh, math.degrees(o.ang))
           for o in (getattr(world, 'obstacles', None) or [])][:3]
    _pa = [(p.x, p.y, p.pair)
           for p in (getattr(world, 'portals', None) or [])][:4]
    sc = dict(fields=[(b.x0, b.y0, b.x1, b.y1, b.b) for b in world.blocks],
              holes=list(world.holes), x0=bx, y0=by, ang0=ang, sp=spd,
              q=charge, steps=steps, bounce=v3_scan.PL_BOUNCE, side=0,
              fan_half=fan_half, obs=(_oa[0] if _oa else None),
              obs_list=_oa, portals=_pa)
    t0 = time.perf_counter()
    # scan_bundle 每次会打 3~4 行（自检/耗时/临界）—— 一次预测打一次还行，
    # 2~3 Hz 刷下去就是刷屏。所以：**第一次**和**自检不过**的时候放出来，
    # 其余时候吞掉，只留下面那行紧凑的 [V3]。
    fan = crit = margin = ms_axi = ms_arcs = note = st = None
    # ★★ 2026-10-04：**scan_bundle 的 arcs 折线一律不算了**（arcs_eff=False）。
    #   原来重 tick 让 scan_bundle 跑 17 路定点黄金模型折线（600 步时 ~300ms，
    #   自适应步数后 3360 步时 ~1700ms！）——而那些折线**已经没有任何画图消费者**
    #   （复盘图改走 render_dashed 自己算的 1+10 路）。判定仍由 PL 给（~3ms）。
    #   发射反馈从 3.5s 压回 <0.4s（用户按发射"没反应"3.5 秒的真凶）。
    #   逐位一致性心跳改为下面 render_dashed 之后的**正中一路**轻量比对。
    arcs_eff = False
    txt = ''
    if quiet:
        import contextlib
        import io as _io
        _buf = _io.StringIO()
        with contextlib.redirect_stdout(_buf):
            fan, crit, margin, ms_axi, ms_arcs, note, st = v3_scan.scan_bundle(
                world, V, v3_scan._MM_PL, sc, win, True, arcs_eff)
        txt = _buf.getvalue()
        if '不一致' in txt:                 # 自检炸了就**必须**让人看见那几行
            sys.stdout.write(txt)
            sys.stdout.flush()
    else:
        fan, crit, margin, ms_axi, ms_arcs, note, st = v3_scan.scan_bundle(
            world, V, v3_scan._MM_PL, sc, win, True, arcs_eff)
    t1 = time.perf_counter()
    kind = [f[2][0] for f in fan]
    # ★★ 2026-10-01 夜（第③点）：`shot` = **正中那一路**（index = len//2，即 k=0）
    #   的判定。v3_pl.write_scene 生成的角度是 ang0 + k*step（k∈[-win,win]），所以
    #   正中那一路就是**球员瞄的角度**（= 输入层设的入射角）。它判 `goal` = 这一杆
    #   从洞口进（v3_fixedpoint 实锤：goal 就是"从洞口进"）—— 这就是"进球"事件，
    #   也是复盘图的触发信号（见游戏循环里的 _REV 状态机）。
    mid = len(kind) // 2 if kind else 0
    _common = dict(bx=bx, by=by, ang=ang, ang_ui=ang_ui, spd=spd,
                   ang_tr=ang_tr, gv=(gvx, gvy),
                   shot=(kind[mid] if kind else None),
                   shot_ang=(fan[mid][0] if kind else ang),
                   ms_axi=ms_axi or 0.0,
                   n_goal=sum(1 for k in kind if k == 'goal'),
                   n_bounce=sum(1 for k in kind if k == 'bounce'),
                   crit=None if crit is None else fan[crit][0],
                   margin=margin, note=note)
    if not want_img:
        # ★★ 轻 tick：到此为止（PL 判定已经在手里）。折线(457ms)+渲染(105ms)+写盘
        #    一概不做 —— 直播档平时就走这条，省下来的全在游戏循环的周期上。
        _common.update(img=None, full=False, out=None,
                       ms_arcs=0.0, ms_render=0.0, ms_write=0.0,
                       ms_total=(t1 - t0) * 1000.0)
        return _common
    extra = 'PL scan: %.2f ms (17 lanes, max_bounce=%d)' % (ms_axi or 0.0,
                                                            v3_scan.PL_BOUNCE)
    if tag:
        # ★ 发射那一杆的图带上"从哪条边、瞄多少度"（像素图上**只用 ASCII** ——
        #   Hershey 字体画不出中文；中文留给控制台和手机网页）
        extra = '%s   |   %s' % (tag, extra)
    if arcs and note:
        extra += '   |   self-check: %s' % note
    trail_game = [v3_scan.cam_to_game(Hmm, p[0], p[1])[:2] for p in trail_px]
    # ★★ 2026-10-04：**新样式复盘图**（用户定案）——单虚线主轨迹 + ±5°/1° 淡扇
    #   + 中文标注。判定/反弹数用 **PL 正中那一路**的（与手机端回话同一口径），
    #   折线由 render_dashed 内部的定点黄金模型画（与 PL 逐位一致，scan_bundle
    #   已当场自检过）。旧 render 留给 CLI（定妆照/--arcs 报告图）。
    _mid = len(fan) // 2 if fan else 0
    _nbr = (fan[_mid][2][1]
            if (fan and isinstance(fan[_mid][2], tuple)) else 0)
    img, _rr, _frs = v3_scan.render_dashed(
        world, sc, shot=_common['shot'], shot_nb=_nbr, tag=tag, extra=extra,
        ball_game=(bx, by), trail_game=trail_game)
    # ★ 2026-10-04：**轻量心跳**——只比正中那一路（PL 回读 vs 黄金模型重算），
    #   零成本地守住"逐位一致"这条契约（17 路版本是 300~1700ms 的账，发射反馈
    #   等不起）。不一致就把那一路的两个值喊出来——静默失败最贵。
    try:
        _pex, _pey = fan[_mid][1][-1]
        if abs(_rr['x'] - _pex) > 0.02 or abs(_rr['y'] - _pey) > 0.02:
            print('[自检] ★ 正中一路 端点不一致：PL(%.2f,%.2f) 金(%.2f,%.2f)'
                  % (_pex, _pey, _rr['x'], _rr['y']), flush=True)
        elif (_rr['res'] == 'goal') != (_common['shot'] == 'goal'):
            print('[自检] ★ 正中一路 判定不一致：PL=%s 金=%s'
                  % (_common['shot'], _rr['res']), flush=True)
    except Exception:
        pass
    t2 = time.perf_counter()
    # ★ 支持**绝对路径**：写 /dev/shm（tmpfs）比写 SD 卡快得多，现场演示用得上
    out = dump if dump.startswith('/') else os.path.join(outdir, dump)
    if dump and dump.lower() not in ('none', '-'):
        # ★ PNG 压缩级别 1（默认是 3）：这张图是**现场看一眼**用的，不是归档，
        #   文件大点无所谓；把压缩从 3 降到 1 能省掉写盘时间的一截。
        cv2.imwrite(out, img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    t3 = time.perf_counter()
    # ★ `img=` 是给 **P2（板上 HDMI 直出）** 留的接口：图已经在手里了，推给显示器
    #   只要拿这一个值，**不用碰游戏循环**。配 --v3-dump none 时更是"零写盘"：
    #   现场演示 = 不落盘 + 直接上屏（写盘在板上要 149~390ms，是全程最贵的一笔）。
    _common.update(img=img, full=True, out=out,
                   ms_arcs=ms_arcs or 0.0, ms_render=(t2 - t1) * 1000.0,
                   ms_write=(t3 - t2) * 1000.0, ms_total=(t3 - t0) * 1000.0)
    return _common


def _v3_note(st):
    """把一次预测的计时记进 V3_STAT（列表封顶 200 个，够算均值/P90 就行）。

    ★ 2026-10-01 夜提速后按**轻/重**分账：轻 tick（只读 PL 判定）的耗时进 ms_lite；
    重 tick（折线+渲染+写盘，=真进球那一刻现算复盘图；stst 档则是每次预测）进
    ms_arcs/ms_render/ms_write/ms_total_full，n_full 记重 tick 的次数。
    混着报均值 = 骗人（轻 tick ~3ms 会把折线那 457ms 平均成个位数）。
    """
    V3_STAT['n'] += 1
    for k in ('ms_axi', 'ms_total'):
        V3_STAT[k].append(st[k])
        if len(V3_STAT[k]) > 200:
            V3_STAT[k].pop(0)
    if st.get('full'):
        _v3_note_full_only(st)
    else:
        V3_STAT['ms_lite'].append(st['ms_total'])
        if len(V3_STAT['ms_lite']) > 200:
            V3_STAT['ms_lite'].pop(0)
    V3_STAT['goal'].append(st['n_goal'])
    V3_STAT['bounce'].append(st['n_bounce'])
    if st['crit'] is not None:
        V3_STAT['crit'].append(st['crit'])
    # ★ 第③点：这一杆（正中那一路）判成 goal 的次数 —— 汇总里要跟"复盘触发次数"
    #   对上账（触发是上升沿，所以 n_review ≤ n_shot_goal）
    if st.get('shot') == 'goal':
        V3_STAT['n_shot_goal'] += 1


def _v3_note_full_only(st):
    """把一次**重 tick** 的三笔大账记进 V3_STAT（不进 n/ms_axi —— 复盘那次现算的
    图不算一次"预测"，它是同一 tick 的第二次调用，别把账记花）。"""
    V3_STAT['n_full'] += 1
    for k in ('ms_arcs', 'ms_render', 'ms_write'):
        V3_STAT[k].append(st[k])
        if len(V3_STAT[k]) > 200:
            V3_STAT[k].pop(0)
    # ms_total_full 装"重 tick 的总耗时" = st['ms_total']（轻/重每个 tick 的 ms_total
    # 都各算各的，这里挑重 tick 的进账）—— 别让列表名再跑到 st 里去找一个不存在的键
    V3_STAT['ms_total_full'].append(st['ms_total'])
    if len(V3_STAT['ms_total_full']) > 200:
        V3_STAT['ms_total_full'].pop(0)


def _in_obstacle_game(gx, gy, obs):
    """球心是否落在障碍物**本体**里（2026-11-01 用户规则：禁识别球区）。"""
    for o in (obs or []):
        try:
            dx = float(gx) - float(o.cx)
            dy = float(gy) - float(o.cy)
            c = math.cos(-float(o.ang)); s = math.sin(-float(o.ang))
            lx = dx * c - dy * s
            ly = dx * s + dy * c
            if abs(lx) <= float(o.hw) and abs(ly) <= float(o.hh):
                return True
        except Exception:
            continue
    return False


def _track_filter(mod, Hm, det, margin, obs=None):
    """★ 第②点：只保留球心落在**场区外扩 margin 游戏像素**内的候选。

    返回 `(保留的候选列表, 被拒的最大者或 None)` —— 第二个值只用于诊断打印。
    球是要摆在**边界上**的，所以判据是"外扩框"而不是"严格在场内"；折算走
    `cam_to_game`（与 V3 判定同一份标定）—— 场外的手机支架/网线从此一票否决。
    ★ 2026-11-01：`obs` = 障碍物列表；球心落在障碍物**本体**内 -> 禁识别区，
       候选直接丢掉（用户："别让障碍物里面再识别出球"）。
    """
    keep = []
    for b in det:
        gx, gy = mod.cam_to_game(Hm, b['x'], b['y'])[:2]
        if not (-margin <= gx <= mod.FIELD_W + margin and
                -margin <= gy <= mod.FIELD_H + margin):
            continue
        if obs and _in_obstacle_game(gx, gy, obs):
            continue
        keep.append(b)
    if keep or not det:
        return keep, None
    b = max(det, key=lambda q: q.get('area', 0.0))
    return keep, (b, mod.cam_to_game(Hm, b['x'], b['y'])[:2])


def _review_tick(shot, live, review, now, pump_on):
    """★ 第③点：进球复盘的**上升沿**触发。返回 True = 这一次要出复盘图。

    上升沿 = 上一次预测"没进洞"、这一次"会进洞"。一直瞄着洞（连续 goal）**不
    重复弹** —— 不然瞄着洞口不动，屏幕就一直卡在复盘图上。
    ★ `prev` **每次**预测都要更新（哪怕这次因为 live/review/pump 没开而不触发），
      否则一开 live 就会把"老早就在 goal"当成新进球弹一张图出来。
    """
    goal = (shot == 'goal')
    fired = False
    if live and review > 0.0 and pump_on and goal and not _REV['prev']:
        _REV['until'] = now + review
        _REV['n'] += 1
        fired = True
    _REV['prev'] = goal
    return fired


def _rev_needs_push(rev):
    """这张复盘图还没推过吗？是 -> 记下"已推"并返回 True（成败都只试一次）。

    ★ 为什么要单独抽出来（2026-10-01 现场，用户："按发射看不到图"）：live 档的
      复盘图（发射那杆 / 进球）以前**全代码库没有任何一处推它上屏** —— 那一支
      只有一句 `pass`，屏幕干冻 `v3_review` 秒再回直播。修好之后"推一次"的
      判据就是这里，台架 `review_push` 场景把它钉死：
        · 同一张图（同一个对象）只推一次 —— 靠 `is` 认对象；
        · 换图 = 换新对象 -> 自然再推（连发两杆不用等窗口结束）；
        · **不许用 `==`**：图是 numpy 数组，`==` 返回的是数组，`if` 真值判断
          会当场抛异常（而且是打在游戏循环里）—— `is` 既是唯一安全也是唯一
          对的写法："新对象"本来就该重推。
    """
    if rev.get('img') is None or rev.get('pushed') is rev['img']:
        return False
    rev['pushed'] = rev['img']
    return True


# ---- ★★ 差距 2：直播局部推帧（2026-10-23）------------------------------------

def _diff_rects(prev, cur, thresh=12, cell=4, max_frac=0.6, max_rects=8,
                min_cells=1, margin=6):
    """cur 相对 prev 的**变化块**（像素矩形列表）+ 变化面积占比。

    返回 (rects, frac)：
      · rects=[]        —— 完全没变（一字节都不用写；屏幕保持上一帧）
      · rects is None   —— 变化太大/太碎（frac>max_frac 或块数>max_rects）→ 整帧推
      · 其它            —— 每个矩形都已加 margin、夹在画面内；相邻/重叠的已合并

    为什么要池化：板上 1280x720x3 的逐像素扫描 + 连通域在 A9 上要十几 ms；先把
    cell×cell 取**最大差**再判阈值，扫描量降 16 倍，单像素噪声也被压下去。
    `min_cells` 忽略极小块（传感器噪点/抗锯齿边）。
    """
    d = cv2.absdiff(cur, prev)
    dm = d.max(axis=2) if d.ndim == 3 else d
    h, w = dm.shape
    cell = max(1, int(cell))
    ch, cw = h // cell, w // cell
    if ch <= 0 or cw <= 0:
        return None, 1.0
    # (ch, cell, cw, cell) 分块取最大 —— 一次 numpy 归约，C 速度
    small = dm[:ch * cell, :cw * cell].reshape(ch, cell, cw, cell).max(axis=(1, 3))
    mask = (small > int(thresh)).astype(np.uint8)
    if not mask.any():
        return [], 0.0
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    rects, area_cells = [], 0
    for i in range(1, n):
        x, y, wc, hc, area = (int(v) for v in stats[i])
        if area < int(min_cells):
            continue
        area_cells += area
        rects.append((max(0, x * cell - margin),
                      max(0, y * cell - margin),
                      min(w, (x + wc) * cell + margin),
                      min(h, (y + hc) * cell + margin)))
    frac = (area_cells * cell * cell) / float(w * h)
    if not rects or len(rects) > int(max_rects) or frac > float(max_frac):
        return None, frac
    # 重叠/相邻矩形合并（最多 max_rects 个，O(k²) 足够）
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                if not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]):
                    rects[i] = (min(a[0], b[0]), min(a[1], b[1]),
                                max(a[2], b[2]), max(a[3], b[3]))
                    del rects[j]
                    merged = True
                    break
            if merged:
                break
    return rects, frac


# ★★ 2026-10-29：精确虚线放**独立进程**算（`pv_worker.py`）——纯 Python 的
#   lane_fixed 在主线程会冻 235ms、在线程会抢 GIL（实测主循环 2.0fps）；
#   独立进程在第二颗 A9 核上跑，主循环只收发 JSON（不冻不抢）。
_PVP = {'proc': None, 'id': 0, 'pending': None, 'done': None,
        'pts': None, 'goal': False, 'req': None, 'th': None, 't_send': 0.0}


def _pvp_start():
    _p = _PVP.get('proc')
    if _p is not None and _p.poll() is None:
        return True
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _PVP['proc'] = subprocess.Popen(
            [sys.executable, os.path.join(_here, 'pv_worker.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=_here)

        def _rd():
            _pp = _PVP.get('proc')
            if _pp is None or _pp.stdout is None:
                return
            for _ln in _pp.stdout:
                try:
                    _r = json.loads(_ln)
                except Exception:
                    continue
                if _r.get('id') == _PVP.get('pending'):
                    _PVP['pts'] = _r.get('pts')
                    _PVP['goal'] = bool(_r.get('goal'))
                    _PVP['done'] = _r.get('id')
        _PVP['th'] = threading.Thread(target=_rd, daemon=True,
                                      name='pv_reader')
        _PVP['th'].start()
        return True
    except Exception as _ep:
        _PVP['proc'] = None
        print('[预览] 后台计算进程启动失败：%s' % str(_ep)[:120], flush=True)
        return False


def _pvp_stop():
    _p = _PVP.get('proc')
    try:
        if _p is not None and _p.poll() is None:
            _p.terminate()
    except Exception:
        pass
    _PVP['proc'] = None


atexit.register(_pvp_stop)


def _pv_fan(world, V, fields, holes, bx, by, ar, sp, q, oa, pa, fan_half,
            bounce):
    """瞄准预览的 17 路端点（PL 快扇，~2-3ms）。���败返回 None。"""
    try:
        import contextlib
        import io as _io3
        import v3_scan as _vs2
        _sc2 = dict(fields=fields, holes=holes, x0=bx, y0=by, ang0=ar,
                    sp=sp, q=q, steps=_eff_steps(sp), bounce=bounce,
                    side=0, fan_half=fan_half,
                    obs=(oa[0] if oa else None), obs_list=oa, portals=pa)
        _b3 = _io3.StringIO()
        with contextlib.redirect_stdout(_b3):
            _f2, _c2, _m2, _a2, _r2, _n2, _s2 = _vs2.scan_bundle(
                world, V, _vs2._MM_PL, _sc2, 8, True, False)
        return [(x[1][-1][0], x[1][-1][1], x[2][0] == 'goal') for x in _f2]
    except Exception:
        return None



# ★★ 2026-10-31 PL 帧泵（PLPUMP=1 才开，默认关 = 与旧行为逐字节相同）
#   开时：PS 只把叠加好的 1024x720 图打包成 32 位写进 DDR，**透视矫正+缩放+打包+写显存
#   全在 PL**（frame_pump_0 @0x43B40000）。PS 不再跑 warpPerspective（~100ms）和
#   _live_show 的 resize/pack（~50ms）。
_PLP = {'on': False, 'fp': None, 'src': None, 'tbl': None, 'dst': None,
        'n': 0, 'ms': [], 'err': 0, 'tbl_key': None}


def _plp_init(pump):
    """初始化 PL 帧泵（第一次直播帧调用；需要 pump 的单缓冲地址）。"""
    try:
        import numpy as np
        from pynq import allocate
        from frame_pump_pl import FramePumpPL, build_row_table  # noqa: F401
        _PLP['fp'] = FramePumpPL(verbose=True)
        _PLP['src'] = allocate(shape=(720, 1024), dtype=np.uint32)
        _PLP['tbl'] = allocate(shape=(720 * 4,), dtype=np.uint32)
        _PLP['dst'] = pump.bufs[0]
        _PLP['n'] = 0
        _PLP['ms'] = []
        _PLP['tbl_key'] = None
        _PLP['on'] = True
        print('[PL帧泵] 已就绪：源图 %dx%d → 显存（单缓冲，%s）'
              % (1024, 720, 'live'), flush=True)
        return True
    except Exception as e:
        print('[PL帧泵] ★ 初始化失败（回退 PS 渲染）：%s' % str(e)[:140],
              flush=True)
        _PLP['on'] = False
        return False


def _plp_show(img, Hm):
    """把一帧交给 PL 帧泵（打包 → 起泵 → 等完成）。返回 (ms_total, kind)。"""
    import time as _t
    from frame_pump_pl import pack_bgr32, build_row_table
    t0 = _t.perf_counter()
    # 行表：标定换了才重建
    key = id(Hm)
    if _PLP.get('tbl_key') != key:
        _PLP['tbl'][:] = build_row_table(Hm, 1280, 720, 0.03, 0.012, 1024, 720)
        try:
            _PLP['tbl'].flush()
        except Exception:
            pass
        _PLP['tbl_key'] = key
    _PLP['src'][:] = pack_bgr32(img)
    try:
        _PLP['src'].flush()
    except Exception:
        pass
    t1 = _t.perf_counter()
    dt = _PLP['fp'].start_wait(timeout_s=0.5)
    self_ms = (_t.perf_counter() - t0) * 1e3
    _PLP['n'] += 1
    _PLP['ms'].append(self_ms)
    if len(_PLP['ms']) > 100:
        _PLP['ms'].pop(0)
    return self_ms

def _live_show(pump, img, st, interp='nearest', now=None, **kw):
    """直播帧上屏：resize 到帧泵尺寸 → 与上一张已上屏图差分 → 只推变化块。

    返回 dict(kind='full'|'rect'|'skip', ms_total, ms_resize, ms_diff, rects, frac)。
    `st` 是模块级 `_LIVEPUSH`（测试可传自己的 dict）。

    差分在**降采样小图**上做（`_LIVE_DIFF.target_w`，720p→320x180）：
    全分辨率 `absdiff + max(axis=2)` 在 A9 上要十几 ms，小图整条链 ~1-3ms。
    矩形再按降采样倍数放大回帧泵坐标。

    安全性：
      · 别的画面（商店/瞄准图/复盘图）推过 → `pump.n_show` 变化 → 本帧整帧重推；
      · 尺寸变了（prev_s 对不上）→ 整帧重推；
      · 每 `full_every` 秒强制整帧刷新一次（防噪声长期累积）；
      · 帧泵不是 live 档时 `show_rect` 自己会退回整帧 show —— 行为仍正确，只是没省。
    """
    import cv2 as _cv2
    import numpy as _np
    t0 = time.perf_counter()
    W, H = int(pump.width), int(pump.height)
    buf = st.get('buf')
    if (img.shape[0] != H or img.shape[1] != W):
        if buf is None or buf.shape != (H, W, 3):
            buf = _np.empty((H, W, 3), _np.uint8)
            st['buf'] = buf
        _itp = (_cv2.INTER_NEAREST if interp == 'nearest' else _cv2.INTER_LINEAR)
        small = _cv2.resize(img, (W, H), dst=buf, interpolation=_itp)
    else:
        small = img
    t1 = time.perf_counter()
    now = time.monotonic() if now is None else float(now)
    opts = dict(_LIVE_DIFF)
    opts.update(kw)
    # ---- 降采样小图（差分只用它；上屏仍是全尺寸 small）----
    _tw = max(32, int(opts.get('target_w', 320)))
    sc = max(1, W // _tw)
    sw, sh = max(1, W // sc), max(1, H // sc)
    prev_s, cur_s = st.get('prev_s'), st.get('cur_s')
    if prev_s is None or prev_s.shape != (sh, sw, 3):
        prev_s = None                       # 尺寸变了 -> 本帧整帧
    if cur_s is None or cur_s.shape != (sh, sw, 3):
        cur_s = _np.empty((sh, sw, 3), _np.uint8)
        st['cur_s'] = cur_s
    if opts.get('area', True):
        _dst_itp = _cv2.INTER_AREA
    else:
        _dst_itp = _cv2.INTER_NEAREST
    _cv2.resize(small, (sw, sh), dst=cur_s, interpolation=_dst_itp)
    # 别的推帧路径动过帧泵 / 尺寸变了 / 到了强制刷新时刻 -> 整帧
    # ★★ 2026-10-31：扣掉**平滑线程自己的局部写**——它每画一次都会 +1 n_show，
    #   不扣的话这里每帧都误判"别的画面推过" -> 强制整帧重推（实测推帧 45->89ms）
    _n_show_now = int(getattr(pump, 'n_show', 0)) - int(_SM.get('n', 0))
    force = (prev_s is None
             or int(st.get('n_show_after', -1)) != _n_show_now
             or now - float(st.get('t_full', 0.0)) >= float(opts['full_every']))
    rects, frac = [], 0.0
    if not force:
        # min_cells=2：小图上孤立的一个 4x4 块（=全图 16x16）多半是噪点/抗锯齿，
        # 不单独推；球/轨迹/HUD 都是连成片的，不会漏。
        _rs, frac = _diff_rects(prev_s, cur_s, thresh=opts['thresh'],
                                cell=opts['cell'], max_frac=opts['max_frac'],
                                max_rects=opts['max_rects'],
                                min_cells=2, margin=2)
        if _rs is None:
            rects = None
        else:
            rects = [(max(0, x0 * sc), max(0, y0 * sc),
                      min(W, x1 * sc), min(H, y1 * sc)) for (x0, y0, x1, y1) in _rs]
    t2 = time.perf_counter()
    if force or rects is None:
        hdl = pump.show(small)
        st['t_full'] = now
        st['n_full'] = int(st.get('n_full', 0)) + 1
        kind = 'full'
    elif not rects:
        hdl = {'ms_total': 0.0, 'flow': None}
        st['n_skip'] = int(st.get('n_skip', 0)) + 1
        kind = 'skip'
    else:
        # ★ 2026-10-23：优先走帧泵的**批量矩形写**（一次取缓冲视图，写多块）。
        #   没有该方法（老帧泵/测试替身）就退回逐块 show_rect —— 行为一致。
        if hasattr(pump, 'show_rects'):
            h2 = pump.show_rects(small, rects)
            _ms = float(h2.get('ms_total', 0.0))
        else:
            _ms = 0.0
            for (x0, y0, x1, y1) in rects:
                h2 = pump.show_rect(small, (x0, y0, x1, y1))
                _ms += float(h2.get('ms_total', 0.0))
        hdl = {'ms_total': _ms, 'flow': None}
        st['n_rect'] = int(st.get('n_rect', 0)) + 1
        kind = 'rect'
    # prev_s 只在**真的推了**时更新（skip 时屏幕没变，prev 必须代表屏幕）
    if kind != 'skip':
        if st.get('prev_s') is None or st['prev_s'].shape != cur_s.shape:
            st['prev_s'] = cur_s.copy()
        else:
            st['prev_s'][:] = cur_s
    # ★★ 2026-10-31（SMOOTH=1）：告诉平滑线程"这是最新整帧/当前代"，
    #   并叫醒它立刻在球的新位置补画（换页后新前台缓冲里没有球头）。
    if _SM.get('on') and kind != 'skip':
        # ★ 发布"推上去的那张"：如果 _live_show 做过 resize，small 是复用缓冲，
        #   必须拷一份（否则线程下次复原时它已被下一帧覆盖 -> 画面出鬼影）
        _SM['frame'] = small if small is img else small.copy()
        _SM['pump'] = pump
        _SM['gen'] = int(_SM.get('gen', 0)) + 1
        _ev = _SM.get('evt')
        if _ev is not None:
            _ev.set()
    # ★ 注意：这里必须取**推完之后**的计数（上面 _n_show_now 是推之前的快照，
    #   直接用它会让下一帧永远误判"别人推过" -> 每帧整帧重推，台架当场抓红）
    st['n_show_after'] = (int(getattr(pump, 'n_show', 0))
                          - int(_SM.get('n', 0)))
    st['kind'] = kind
    st['frac'] = frac
    return dict(kind=kind, rects=rects, frac=frac,
                n_rects=(len(rects) if rects else 0),
                ms_resize=(t1 - t0) * 1e3, ms_diff=(t2 - t1) * 1e3,
                ms_push=(time.perf_counter() - t2) * 1e3,
                ms_total=(time.perf_counter() - t0) * 1e3,
                flow=hdl.get('flow'))


# ---- ★★ 发射模式（用户①②③三条）：门槛 / 方向 / 回话 ---------------------------

# 边 -> 朝场内的法线角（度）。约定 vx = sp*cos(a)、vy = sp*sin(a)（a=0 -> +x；
# 游戏坐标 y 向下）—— 与 v3_scan/v3_pl 同一套；规则表 v4：入射角以边界法线为 0°。
def _ui_ang_to_phys(a):
    """界面角度 -> 物理角度。

    ★★ 2026-10-28 用户定案：界面用**标准坐标系**——0°=右水平、**逆时针为正**
    （90°=上，180°=左，270°=下），360° 一圈。游戏/相机的 y 轴向下，所以物理角度
    = -界面角度（mod 360）。所有喂 `realtime_ball`/`lane_fixed`/PL 的角都必须
    过这一把尺；显示给玩家的仍是界面角度。
    """
    return (-float(a)) % 360.0


def _eff_steps(speed):
    """瞄准预览/发射的**自适应步数**（2026-10-04 用户："速度很小时轨迹也很短"）。

    每步位移 = v/240 像素（DT=1/240）——固定 600 步时低速球只走 2.5 秒的路
    （v=100 → 250px，图上就一小截）。这里按**目标路径 ~3500px** 反推步数，
    钳到 [1200, 4000]（4000 给 PL 的 max_steps 寄存器上限 4095 留余量）。
    预览与发射用**同一个函数**——所见即所得。
    """
    return min(4000, max(1200, int(3500 * 240.0 / max(float(speed), 50.0))))
_EDGE_CN = {'left': '左', 'right': '右', 'top': '上', 'bottom': '下'}


def _fire_gate(mod, Hm, ball, ang_rel, margin):
    """★ 发射门槛（用户①②）：返回 `(ok, why, info)`。

    why ∈ {'noball', 'out', None}；info = dict(bx, by, edge, d, ang,
    snap, cam, bx0, by0)。（'inside' 已于 2026-10-28 取消。）
    球位折算走 `cam_to_game`（与 V3 判定**同一份**标定 —— 不可能出现两套映射）；
    方向 = **界面绝对角**（0°=右水平、逆时针为正；2026-10-28 用户定案），
    不再用边法线；`ang_rel` 是界面角度，返回 `info['ang']` 物理角、`info['ang_ui']`
    界面角（日志/手机显示用界面角）。

    ★★ 2026-10-01 深夜（fire_run2 实跑教训）：**球摆在边线上是默认玩法，必须收**。
      上一趟 53 杆全拒在"球心在场区外 3px"——实测球心压在右边线上 → 游戏坐标
      x=963 > 960。原判据 `in_field`（离边 ≥19px）本意是护 PL（贴墙的起点会让
      某一路 FSM 卡死，实测 2000ms 超时），但它把**球心压线的正常摆法**也拒了。
      现在改成"捡回来"：球心离安全盒外扩 ≤ 一个球半径（= 球身还压在场区上）时，
      把**喂 PL 的起点**沿轴向夹回安全盒内 2px —— PL 收到的永远是安全起点
      （与原来 19px 门完全等价，卡死风险为零），打出去的却是玩家想要的那一杆。
      球心离安全盒比一个球半径还远（球都不沾场区了）才拒 `out`。
      ★★ 2026-10-28 用户定案：**取消"球在区域中间拒绝"与紫色可发射带**——
      球在红色场区内任意位置（只要球身还压在场区上）都可识别/初始/发射；
      方向 = **界面绝对角**（0°=右水平、逆时针为正）。`margin` 参数保留
      只为兼容旧调用，不再参与拒绝。
    """
    if ball is None:
        return False, 'noball', {}
    bx0, by0 = mod.cam_to_game(Hm, ball['x'], ball['y'])[:2]
    # 安全盒 = in_field 的界（离每边 ≥ IN_FIELD_MARGIN + BALL_R）。
    s = float(mod.IN_FIELD_MARGIN) + float(mod.BALL_R)
    x0, x1 = s, mod.FIELD_W - s
    y0, y1 = s, mod.FIELD_H - s
    ovf = max(x0 - bx0, bx0 - x1, y0 - by0, by0 - y1)   # >0 = 球心在安全盒外多少像素
    snap, cam, bx, by = False, None, bx0, by0
    # ★ 2026-10-28 用户定案：出界判据改成**红色场区**（不是安全盒）——
    #   球身压一点场区就算场内；喂 PL 的起点仍在下面夹回安全盒。
    _rc = float(ball.get('r') or 0.0)
    _rg = float(mod.BALL_R)
    if _rc > 0.0:
        _rx, _ry = mod.cam_to_game(Hm, ball['x'] + _rc, ball['y'])[:2]
        _rg = max(1.0, ((_rx - bx0) ** 2 + (_ry - by0) ** 2) ** 0.5)
    if (bx0 + _rg < 0.0 or bx0 - _rg > mod.FIELD_W
            or by0 + _rg < 0.0 or by0 - _rg > mod.FIELD_H):
        d = {'left': bx0, 'right': mod.FIELD_W - bx0,
             'top': by0, 'bottom': mod.FIELD_H - by0}
        edge = min(d, key=d.get)
        return False, 'out', dict(bx=bx0, by=by0, edge=edge, d=d[edge],
                                  ovf=ovf, r=_rg)
    # ★ 触发线是 −2px（不是 0）：球心**刚好压在**安全界上时也夹一下 —— 免得
    #   game->cam->game 往返的浮点毛刺把 in_field 判到界外（差 1e-9 也是差）。
    if ovf > -2.0:
        bx = min(max(bx0, x0 + 2.0), x1 - 2.0)    # 夹回安全盒，再往里 2px
        by = min(max(by0, y0 + 2.0), y1 - 2.0)    # （防 game->cam->game 往返毛刺）
        cam = mod.game_to_cam(Hm, bx, by)
        snap = True
    d = {'left': bx, 'right': mod.FIELD_W - bx,
         'top': by, 'bottom': mod.FIELD_H - by}
    edge = min(d, key=d.get)
    _aui = float(ang_rel) % 360.0
    ang = _ui_ang_to_phys(_aui)
    return True, None, dict(bx=bx, by=by, edge=edge, d=d[edge], ang=ang,
                            ang_ui=_aui, snap=snap, cam=cam,
                            bx0=bx0, by0=by0)


def _fire_msg(why, info, margin):
    """拒绝/发射的原因写成**给选手看的中文**（进 v3_shot.json 的 msg，手机网页
    直接显示）。像素图上的文字仍只用 ASCII —— 这里是控制台/手机，中文没问题。"""
    if why == 'noball':
        # ★ 2026-10-05 夜（"控球3按发射没反应"）：区分两种 noball —— 场上有球、
        #   只是**没有你选的这个编号**时，老文案（"没找到球…把球摆到边界上"）
        #   对着屏幕上的球说，纯误导；这里把真正的原因（号不在/有哪些号）说清。
        if info.get('no_such') is not None:
            _have = '、'.join(str(n) for n in info.get('have', [])) or '无'
            return ('%d 号球当前不在追踪里（可能刚被跟丢过）——'
                    '看屏幕上的圈号重选一颗（现在场上是 %s 号），'
                    '或把球在场内晃一下再按"发射"'
                    % (info['no_such'], _have))
        return '没找到球 —— 把球放在红色场区内再按"发射"'
    if why == 'out':
        # ★ 2026-10-01 深夜（fire_run2 教训）：原来这条一个数字都不带 —— 用户 53 杆
        #   全拒在这句笼统话上、只能猜。现在把"哪条边、差多少"直接说出来
        #   （d<0 = 球心已在线**外**；此时还要报球半径，让人知道挪多远）。
        if 'edge' in info and 'd' in info:
            _e = _EDGE_CN.get(info.get('edge'), '?')
            _d = float(info.get('d', 0.0))
            return ('球身没有压着红色场区（最近%s边 %.0fpx，球半径 %.0fpx）—— '
                    '把球放回场区内再发射'
                    % (_e, abs(_d), float(info.get('r', 0.0))))
        return '球不在红色场区内 —— 把球放回场区再发射'
    if why == 'paused':
        # ★ M2.2 对局：全局暂停（改磁场/加洞口）期间双方都不能发射
        return '全场暂停中（一方正在改磁场/加洞口）—— 等恢复再发射'
    if why == 'round_end':
        return '本局不在对战中（局前筹备或结算中）—— 等本局开始再发射'
    if why == 'no_ammo':
        return ('该色弹药用完（红球剩 %d / 蓝球剩 %d）——本局每人 3 红 2 蓝，'
                '等下一局补满' % (int(info.get('red', 0)),
                                  int(info.get('blue', 0))))
    return str(why)


def _shot_write(obj, player=1):
    """把这一杆的结果**原子写**进 _SHOT_PATH（游戏写、手机读 —— serve 的 /shot）。

    ★★ 2026-10-05 双人：文件形如 {"p1": {...}, "p2": {...}} —— 写谁只动谁那格
      （读旧文件、改一格、原子替换）。serve 的 `/shot?p=N` 取对应格；旧的单块
      格式（顶层直接有 msg）在 p=1 时原样兼容返回。
    ★ 原子 = 临时文件 + os.replace：手机轮询**永远读不到半截 JSON**。
    ★ encoding='utf-8' **显式给**：板子若在 C locale 下，默认编码可能是 ASCII，
      msg 里的中文会当场炸成 UnicodeEncodeError（"写不进去"在现场最难查）。
    ★ 写失败**不抛** —— 手机少一条反馈而已，游戏循环绝不许因为它停。
    """
    # ★ 2026-10-16：道具类结果（kind='item'）顺手弹屏上横幅——见 _ITEM_MSG。
    #   挂在这里是因为**所有**道具路径（键盘/鼠标/手机发起、成功与被拒）都会
    #   经过本函数——一处收口不会漏（"按 C 没反应"的治本）。
    if isinstance(obj, dict) and obj.get('kind') == 'item':
        _item_msg_set(obj.get('msg'))
    try:
        d = os.path.dirname(_SHOT_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        try:
            with open(_SHOT_PATH, 'r', encoding='utf-8') as f:
                old = json.load(f)
        except Exception:
            old = {}
        if not isinstance(old, dict):
            old = {}
        if 'msg' in old and 'p1' not in old and 'p2' not in old:
            old = {'p1': old}                 # 旧单块 -> 归到 p1 格
        old['p%d' % int(player)] = obj
        tmp = _SHOT_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(old, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _SHOT_PATH)
    except Exception as e:
        print('[发射] ★ 结果写不进 %s：%s' % (_SHOT_PATH, str(e)[:140]), flush=True)


def _balls_write(tracks):
    """★ 2026-10-06：把"场上现在有几号球"原子写进 _BALLS_PATH（手机网页 /balls 读）。

    内容 = 已发号的轨迹（t.no 非空）按号排序：[{'no':1,'cls':'blue'}, ...]。
    和 _shot_write 同一套纪律：显式 utf-8、tmp+replace 原子替换、失败不抛
    （网页少一行列表而已，游戏循环绝不许因为它停）。
    """
    try:
        _bs = sorted(({'no': int(t.no), 'cls': str(t.cls)}
                      for t in (tracks or []) if t.no is not None),
                     key=lambda b: b['no'])
        d = os.path.dirname(_BALLS_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = _BALLS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'t': round(time.time(), 3), 'balls': _bs}, f,
                      ensure_ascii=False)
        os.replace(tmp, _BALLS_PATH)
    except Exception:
        pass


# ============================================================================
#  ★★ 2026-10-07 M2.3：对局状态 -> 手机（/match）+ 链路退化检测
# ============================================================================

_LINK_TRK = {'n_last': 0, 'times': []}   # 卡顿脉冲的滚动窗口（见 _link_track）
# ★★ M2.4（洞口自适应）：本局落点账（按边；c=左右边取 y、上下边取 x——engine 同口径）。
#   数据源=真球 'bounce' 事件；每局开始清。"hotspot" 统计与移动计划在 hole_adapt.py。
_HITS = {'left': [], 'right': [], 'top': [], 'bottom': []}
_HITS_CAP = 5000                         # 上限（防长跑局攒爆；只留最近的）
# ★ 2026-10-07（用户："挪一小步我这的视角看不出来"）：洞口移动/每局随机的**醒目
#   动画**数据——(edge, 旧 c, 新 c) 列表 + 时刻；_build_live 在 3 秒内画：旧位
#   灰残影、新位黄亮框脉动、旧->新箭头、底部中文提示。
_HMOVE = {'t': -1e9, 'items': []}
_HMOVE_SECS = 3.0
_HUD_HOLE_FLASH = '洞口移动！'
# ★★ 2026-10-08 道具系统第一段（规则书 §6.2 局前选购）：选购事件的上升沿基准。
#   数据源=v3_input 的 p{N}_mup/mdn/buy 计数器（W/S/↑↓ 同键双语义 + E/*）。
_SHOP = {'mup1': 0, 'mdn1': 0, 'buy1': 0, 'mup2': 0, 'mdn2': 0, 'buy2': 0,
         # ★★ 2026-10-08 商店 v2：鼠标左键 / 手机「进入商店」的上升沿基准
         'mbtn': 0, 'sopen1': 0, 'sopen2': 0,
         # ★ 2026-10-19 布置 PLACE v1：障碍摆放触发键（V/小键盘-）的上升沿基准
         'obs1': 0, 'obs2': 0,
         # ★ 2026-10-25 实体传送门摆放触发键（G/小键盘7）的上升沿基准
         'ppl1': 0, 'ppl2': 0}
# ★★ 2026-10-08 商店 v2（用户设计定案）：局间全屏「商店画面」。
#   open  = 画面开着吗（购买键/点入口/手机"进入商店" 开；双方就绪=开局时自动关）
#   rects = 最近一帧的命中框表（ent=入口按钮 / pN_itI=道具行 / pN_buy / pN_done）
_SHOPV = {'open': False, 'rects': {}}
# ★★ 2026-10-14 局中道具（规则书 §6.4/§7.1）：计数器上升沿基准 + 改磁场快照 + 确认基准
# ★★ 2026-10-29 改磁场新交互的临时状态（角度/速度快照 + 数字键基准）
_EDC = {'ang': None}


def _edit_apply_value(m, world):
    """把 VALUE 阶段输入的数值落到当前块（方向 sign × 大小，夹 0.10..0.80）。

    输入为空 -> 保留该块原来的 |b|（即只改方向）。返回新的 b。"""
    try:
        _idx = int(m.edit['idx'])
        _blk = world.blocks[_idx]
        _txt = str(m.edit.get('txt') or '').strip()
        _v = None
        try:
            if _txt:
                _v = float(_txt)
        except Exception:
            _v = None
        if _v is None:
            _v = abs(float(_blk.b))
        _v = max(B_EDIT_MIN, min(B_EDIT_MAX, _v))
        _blk.b = (1.0 if int(m.edit.get('sign', 1)) >= 0 else -1.0) * _v
        return float(_blk.b)
    except Exception:
        return None


_MAS = {'edit1': 0, 'edit2': 0, 'hole1': 0, 'hole2': 0,
        'mup1': 0, 'mdn1': 0, 'mup2': 0, 'mdn2': 0,
        'aup1': 0, 'adn1': 0, 'aup2': 0, 'adn2': 0, 'esc': 0,
        'cfm1': 0, 'cfm2': 0,
        # ★ 2026-10-15（加洞口 v2·手机俯视图）：p{N}_hopos 点位置计数上升沿
        'hopos1': 0, 'hopos2': 0,
        # ★ 2026-10-16 粒子引擎风暴键（B/N）的上升沿基准
        'pe1': 0, 'pe2': 0, 'snap': None,
        # ★ 2026-10-25 局中加球键（F/小键盘8）的上升沿基准——
        #   板上实锤：漏了这两个键 -> 主循环第一帧 _mas_ev('aball1') KeyError，
        #   游戏当场崩（和 2026-10-14 的 mup/mdn 同款事故）。
        'aball1': 0, 'aball2': 0}
#  ★★ 2026-10-14 实况事故（板上"按E卡死"）：上面这行 mup/mdn 四个键漏了 ->
#    进 FINE 微调第一帧 _mas_ev('mup1') KeyError -> 主循环抛穿、游戏进程整个死。
#    教训并入纪律：**新增计数器消费方时，_MAS 的键表要对着消费代码逐个数**。
#   改磁场强度可调范围/步进（板载口径：世界块 b=±0.40 体感值，PC 原型的 0.5~3.0 是另一套单位）
B_EDIT_MIN, B_EDIT_MAX, B_EDIT_STEP = 0.10, 0.80, 0.05
HOLE_W_GAME = 44.0
# ★★ 2026-10-15 加洞口 v2（对话5 死前定稿的设计，用户拍板）：**鼠标跟随**交互状态。
#   进入 HOLE 后**鼠标一动即接管**：最近边选边、沿边滑动定位置；左键=定住→弹窗
#   （「确定/取消」鼠标可点）；E=确定、ESC=取消（键盘兜底）；弹窗「取消」/ESC=回跟随，
#   跟随中再 ESC=整个取消。没动过鼠标 = 老键盘流程**一字节不变**（C 切边→E 定边→
#   W/S 微调→E 放置）。归属：**谁按发起键（C=P1 / 小键盘+=P2）扣谁的额度**——弹窗写明。
#   who=发起人；m0=进入时的鼠标快照（判断"动没动"）；mouse=鼠标已接管；
#   confirm=弹窗开着（位置已定住）；rects=弹窗按钮命中框（画布 1280x720 坐标）；
#   last=最近鼠标画布位置（画光标）；quota=弹窗第二行扣额文案（开弹窗那刻算好）。
_HOLEV = {'who': None, 'm0': (0, 0), 'mouse': False, 'confirm': False,
          'rects': {}, 'last': (0, 0), 'quota': '', 'pw': 1280, 'ph': 720}
# ★★ 2026-10-19 布置 PLACE v1（三天计划第 1 天）：**障碍摆放**（PS 侧闭环。
#   物理 realtime_ball 已支持 obstacles（0cee762 半成品移植）；这里补交互+渲染+
#   商店接线。PL 束/粒子引擎暂不认障碍——预览/风暴不反射障碍，口径已记 HANDOFF）。
#   交互照抄加洞口 v2：鼠标一动接管（画布直映射）→ 左键定住 → 弹窗确定/取消；
#   W/S（P1）/↑↓（P2）旋转 ±15°；E=确定、ESC=取消；`V`(P1)/小键盘`-`(P2) 进入。
#   一次一人摆放；额度=商店买的 items[p]['obstacle']；障碍**永久**（跨局保留）。
OBS_W_GAME = 120.0        # 默认长度（游戏像素）
OBS_H_GAME = 14.0         # 厚度
OBS_MARGIN = 20.0         # 离场区边的最小间距
OBS_ANG_STEP = 15.0       # 旋转步长（度）
_OBST = []                # engine.Obstacle 列表（跨局保留；每局 build_world 后回注）
_OBSP = {'on': False, 'who': 0, 'cx': 480.0, 'cy': 270.0, 'ang': 0.0,
         'confirm': False, 'rects': {}, 't': 0.0, 'mouse': False,
         'm0': (0, 0)}


def _obsp_clear():
    _OBSP.update({'on': False, 'who': 0, 'confirm': False, 'rects': {}})
# ★★ 2026-10-19 实物识别（用户定案：黑长条=障碍/绿椭圆=传送门/黑球=洞口标记）。
#   每帧在缩略图上识别（phys_objects.detect），**稳定性 >=0.4s 才生效**（防抖/
#   防坏帧）；障碍**物理优先**：识别到长条就盖过手工摆放的 _OBST（没识别到才回退）。
#   黑洞标记会接管该侧洞口（期间洞自适应让位）。
_PHYS = {'bars': [], 'balls': [], 'greens': [], 'marks': [],
         'mark_on': False, 'nerr': 0, 't': 0.0,
         # ★★ 2026-10-25 实体道具「购买→使用」：
         #   base_* = 开局就在桌上的**免费**实体件（只认新摆的）；
         #   own_*  = 已买道具并扣过次数的实体件（永久）；
         #   applied_* = 最近一帧通过稳定性门槛的识别结果（摆放判定用）。
         'base_ready': False, 't0': None,
         'base_bars': [], 'base_greens': [],
         'own_bars': [], 'own_pairs': [],
         'applied_bars': [], 'applied_greens': []}

# 生效门（用户 2026-10-25 定案：**只认新摆的**）——开局免费件继续生效；
# 之后新出现的实体件必须在局前商店买了道具、按 V/G 进入摆放、相机稳定识别后
# 才扣次数并生效。PHYS_ITEM_GATE=0 可退回"实体件全免费"（演示/排查用）。
PHYS_ITEM_GATE = os.environ.get('PHYS_ITEM_GATE', '1') != '0'
PHYS_BAR_MATCH = 60.0       # 免费/已购黑条的位置匹配容差（游戏 px）
PHYS_GREEN_MATCH = 75.0     # 免费/已购绿门的位置匹配容差
PHYS_BASE_WAIT = 1.5        # 开局多久后锁定"免费基线"（等跟踪器稳定 0.4s+）
PHYS_PLACE_DET = 0.30       # 摆放态识别节拍（秒；全分辨率版用户定案 0.30）

# ★ 2026-10-25 实体布置态（障碍=V/小键盘-，传送门=G/小键盘7）：
#   {'on','who','kind','t','t_det','cand':[{'kind','gx','gy','n'}],'status'}
_PPLACE = {'on': False, 'who': 0, 'kind': '', 't': 0.0, 't_det': 0.0,
           'cand': [], 'status': '', 'confirm': False,
           # ★ 2026-10-27 用户定案：自动识别不稳 -> 鼠标点选黑条锁定
           'pick': None, 't_pick': 0.0,
           # 当前布置画面的几何（点选坐标反算用；由 _build_pplace_live 每帧写）
           'live_view': False, 'raw_w': 1024, 'raw_h': 720,
           'crop_on': False,
           # ★★ 2026-10-28：True=对局中摆放（MatchFlow.phase=PLACE，全场暂停）
           'in_match': False}
# 对局中摆放结束要回到 MatchFlow.end_place()；_pplace_clear 是模块级函数拿不到
# main() 里的 _MF，所以在这里存一份引用（进入时写、清空时用）。
_PM = {'m': None}
# ★★ 2026-10-29 用户定案：**确认放置即冻结**——已摆好的件按确认那一刻的位置
#   定死（实心），后面再摆什么都不改它；直到下一局结束后的调整界面才重定。
_FROZEN = {'obs': [], 'ports': []}


def _frozen_hit_obs(o):
    """engine.Obstacle 是否就是某个冻结件（中心/条身 40px 内）。"""
    for (cx, cy, w, h, ang) in (_FROZEN.get('obs') or []):
        if math.hypot(float(o.cx) - cx, float(o.cy) - cy) <= 40.0:
            return True
        _a = math.radians(float(ang))
        _ca, _sa = math.cos(_a), math.sin(_a)
        _dx = float(o.cx) - cx
        _dy = float(o.cy) - cy
        _u = max(-w / 2.0, min(w / 2.0, _dx * _ca + _dy * _sa))
        if math.hypot(float(o.cx) - (cx + _ca * _u),
                      float(o.cy) - (cy + _sa * _u)) <= 40.0:
            return True
    return False


def _frozen_merge(cur):
    """识别出的条 + 冻结件：冻结优先，识别到的同根件剔除（不重复）。"""
    _fz = [(float(t[0]), float(t[1]), float(t[2]), float(t[3]), float(t[4]))
           for t in (_FROZEN.get('obs') or [])][:3]
    _keep = []
    for _t in (cur or ()):
        _dup = False
        for (_cx, _cy, _w, _h, _ang) in _fz:
            if math.hypot(_t[0] - _cx, _t[1] - _cy) <= 40.0:
                _dup = True
                break
            _a = math.radians(_ang)
            _ca, _sa = math.cos(_a), math.sin(_a)
            _dx = _t[0] - _cx
            _dy = _t[1] - _cy
            _u = max(-_w / 2.0, min(_w / 2.0, _dx * _ca + _dy * _sa))
            if math.hypot(_t[0] - (_cx + _ca * _u),
                          _t[1] - (_cy + _sa * _u)) <= 40.0:
                _dup = True
                break
        if not _dup:
            _keep.append(_t)
    return (_fz + _keep)[:3]

# ★★ 2026-10-28 用户定案：双方就绪、本局没买实体件 -> 先给「调整障碍/传送门」
#   界面（实时相机画面，可现场挪实物），按 E/小键盘* = 开始本局，ESC = 回商店。
#   `adjust` 真值由 MatchFlow.adjust 驱动；这里只管画面与按键。
# ★★ 2026-10-28 用户定案（第 3 条）：买完障碍/传送门先**问一句要不要现在用**。
#   E/小键盘* = 立即摆放；ESC = 先留着（对局中按 V/G 再摆，本局临时有效）。
_PBUY = {'on': False, 'who': 0, 'kind': '', 'b': 0, 'esc': 0}

_ADJ = {'is_adjust': True, 'on': False, 'who': 1, 'kind': 'obstacle',
        'cand': [], 'status': '', 'confirm': False, 'pick': None,
        't_det': 0.0, 'live_view': False, 'raw_w': 1024, 'raw_h': 720,
        'crop_on': False}


def _map_to_game(vs, Hm, mx, my):
    """缩略图 map 像素 -> 游戏坐标（块心 -> 相机像素 -> 标定）。"""
    return vs.cam_to_game(Hm, float(mx) * 8.0 + 4.0,
                          float(my) * 8.0 + 4.0)[:2]


def _phys_track(old, news, now, gate=40.0, keep_s=0.8, apply_s=0.4):
    """最近邻稳定性跟踪：命中项刷新（位置 EMA 平滑），失联 >keep_s 丢弃；
    年龄 >=apply_s 的才算"稳定"。返回 (tracks, applied)。"""
    tracks = []
    used = set()
    for tr in old:
        best = None
        bd = gate
        for j, nw in enumerate(news):
            if j in used:
                continue
            d = math.hypot(nw['gx'] - tr['gx'], nw['gy'] - tr['gy'])
            if d <= bd:
                bd, best = d, j
        if best is None:
            if now - tr['last'] <= keep_s:
                tracks.append(tr)
            continue
        nw = news[best]
        used.add(best)
        # ★ 2026-10-19 上板：位置/长度/角度全用 EMA —— 原始值每帧直接覆盖会让
        #   大屏的障碍框"跳动"（用户实况点名："黑条的识别卡在跳动"）。
        tr['gx'] = tr['gx'] * 0.7 + nw['gx'] * 0.3
        tr['gy'] = tr['gy'] * 0.7 + nw['gy'] * 0.3
        for k, v in nw.items():
            if k in ('gx', 'gy'):
                continue
            if (isinstance(v, (int, float))
                    and isinstance(tr.get(k), (int, float))):
                tr[k] = tr[k] * 0.7 + v * 0.3
            else:
                tr[k] = v
        tr['last'] = now
        tracks.append(tr)
    for j, nw in enumerate(news):
        if j not in used:
            tr = dict(nw)
            tr['first'] = now
            tr['last'] = now
            tracks.append(tr)
    applied = [tr for tr in tracks if now - tr.get('first', now) >= apply_s]
    return tracks, applied


def _near_any(gx, gy, pts, tol, glen=0.0, gang=0.0):
    """(gx,gy) 是否落在 pts 里任一点的 tol 内（实体件归属匹配）。

    ★★ 2026-10-29 用户实况（"黑条老是丢"）：黑条的检测中心/长度每帧在漂
    （同一根在 长60/119/206、中心 342/394/448 之间跳），只按**中心点** 60px
    匹配，一漂出去就判"不是那根"。这里加一条：**基线点落在这一帧条身的
    线段上**（点到线段 <= tol）也算同一根——中心怎么漂都不丢。
    """
    _gl = float(glen or 0.0)
    _a = math.radians(float(gang or 0.0))
    _ca, _sa = math.cos(_a), math.sin(_a)
    for _p in (pts or ()):
        _px, _py = float(_p[0]), float(_p[1])
        if math.hypot(float(gx) - _px, float(gy) - _py) <= tol:
            return True
        if _gl > 1.0:
            _dx = _px - float(gx)
            _dy = _py - float(gy)
            _t = max(-_gl / 2.0, min(_gl / 2.0, _dx * _ca + _dy * _sa))
            _qx = float(gx) + _ca * _t
            _qy = float(gy) + _sa * _t
            if math.hypot(_px - _qx, _py - _qy) <= tol:
                return True
    return False


def _phys_active(tracks, base, owned, tol):
    """★ 2026-10-25 生效门：只有**免费基线**和**已购（扣过道具）**的实体件生效。

    用户定案“只认新摆的”：开局就在桌上的黑条/绿门继续免费生效；之后新出现的
    实体件必须在局前买了道具、按 V/G 摆放并被相机稳定识别，才扣次数进 owned。
    其余新件只是摆件（不挡球、不传送）——否则商店买不买都一样。
    """
    if not PHYS_ITEM_GATE:
        return list(tracks or ())
    out = []
    for _t in (tracks or ()):
        _gl = float(_t.get('glen', 0.0) or 0.0)
        _ga = float(_t.get('gang', 0.0) or 0.0)
        if (_near_any(_t.get('gx'), _t.get('gy'), base, tol, _gl, _ga)
                or _near_any(_t.get('gx'), _t.get('gy'), owned, tol, _gl, _ga)):
            out.append(_t)
    return out


def _phys_unclaimed(tracks, base, owned, tol):
    """不属于免费基线、也没被道具认领过的实体件（摆放态候选）。"""
    if not PHYS_ITEM_GATE:
        return []
    out = []
    for _t in (tracks or ()):
        _gl = float(_t.get('glen', 0.0) or 0.0)
        _ga = float(_t.get('gang', 0.0) or 0.0)
        if (_near_any(_t.get('gx'), _t.get('gy'), base, tol, _gl, _ga)
                or _near_any(_t.get('gx'), _t.get('gy'), owned, tol, _gl, _ga)):
            continue
        out.append(_t)
    return out


def _bar_near_pt(bars, x, y, tol=32.0):
    """点 (x,y) 是否压在/贴着某根黑条身（中心或线段 tol 内）。"""
    for _b in (bars or []):
        _gx = float(_b['gx']); _gy = float(_b['gy'])
        if math.hypot(float(x) - _gx, float(y) - _gy) <= tol:
            return True
        _gl = float(_b.get('glen', 0.0) or 0.0)
        if _gl > 1.0:
            _a = math.radians(float(_b.get('gang', 0.0) or 0.0))
            _ca, _sa = math.cos(_a), math.sin(_a)
            _dx = float(x) - _gx
            _dy = float(y) - _gy
            _u = max(-_gl / 2.0, min(_gl / 2.0, _dx * _ca + _dy * _sa))
            if math.hypot(float(x) - (_gx + _ca * _u),
                          float(y) - (_gy + _sa * _u)) <= tol:
                return True
    return False


def _drop_greens_on_bars(greens, bars, tol=32.0):
    """★ 2026-10-29 用户实况（"有障碍的地方识别出了传送门"）：压/贴在黑条身
    上的绿点不当传送门。与购买摆放 `_pplace` 的 32px 规则同口径。"""
    return [t for t in (greens or ())
            if not _bar_near_pt(bars, t['gx'], t['gy'], tol)]


def _drop_ports_on_obs(ports, obstacles, tol=32.0):
    """★ 2026-10-29 用户实况（"调整后确认开局，传送门跳到障碍物上面"）：
    **锁定/应用锁定时再兜一道**——整对门里只要有一扇压/贴黑条，就丢掉整对
    （留单扇没有对端，也没有意义）。obstacles = [(cx,cy,w,h,ang_deg)]。"""
    _bars = [dict(gx=float(cx), gy=float(cy), glen=float(w),
                  gang=float(ang))
             for (cx, cy, w, h, ang) in (obstacles or ())]
    _pairs = {}
    for (x, y, pair) in (ports or ()):
        _pairs.setdefault(int(pair) // 2, []).append(
            (float(x), float(y), int(pair)))
    _out = []
    for _k in sorted(_pairs):
        _grp = _pairs[_k]
        if any(_bar_near_pt(_bars, p[0], p[1], tol) for p in _grp):
            continue
        _out.extend(_grp)
    return _out


def _portal_pair_candidate(greens, bars=None):
    """从绿团里挑一对门：**间距 >=80 的组合里取最近的一对**。

    ★★ 2026-10-29 用户实况（"两对门分不清哪一对"）：原来按面积挑两个最大的，
    桌上有两对门时会把不同对的两扇配在一起。改按**最近**配对——同一对实体
    门通常挨得最近。找不到返回 None。"""
    _c = [t for t in (greens or ()) if t.get('area', 0) >= 12]
    # ★ 2026-10-29 用户实况（"门画到障碍上了"）：压在黑条上的绿点不当门。
    if bars:
        _c = [t for t in _c if not _bar_near_pt(bars, t['gx'], t['gy'])]
    _best = None
    for _i in range(len(_c)):
        for _j in range(_i + 1, len(_c)):
            _a, _b = _c[_i], _c[_j]
            _d = math.hypot(float(_a['gx']) - float(_b['gx']),
                            float(_a['gy']) - float(_b['gy']))
            if _d >= 80.0 and (_best is None or _d < _best[0]):
                _best = (_d, _a, _b)
    return None if _best is None else (_best[1], _best[2])


def _pplace_candidates(kind, bars, greens, base_bars, base_greens,
                       own_bars, own_pairs, bar_match=None):
    """摆放态候选（纯函数，台架可测）。

    kind='obstacle' -> 未认领黑条列表（每项 dict(kind='bar', gx, gy, glen)）；
    kind='portal'   -> 一对未认领绿门 [a,b]（间距>=80px）或 []。
    """
    _own_g = [p for pr in (own_pairs or ()) for p in pr]
    if kind == 'obstacle':
        # ★ 2026-10-29：候选的基线/已购识别半径 60->45（贴得稍近的新条也能亮黄框；
        #   真·基线自身仍会被排除；实际放置的叠障碍检查在 _obst_valid）
        _bm = float(bar_match if bar_match is not None else 45.0)
        _c = _phys_unclaimed(bars, base_bars, own_bars, _bm)
        return [dict(kind='bar', gx=t['gx'], gy=t['gy'],
                     glen=float(t.get('glen', 0.0)),
                     gthick=float(t.get('gthick', 0.0)),
                     gang=float(t.get('gang', 0.0))) for t in _c]
    if kind == 'portal':
        _c = _phys_unclaimed(greens, base_greens, _own_g, PHYS_GREEN_MATCH)
        _pr = _portal_pair_candidate(_c, bars=bars)
        if _pr is None:
            return []
        return [dict(kind='green', gx=_pr[0]['gx'], gy=_pr[0]['gy']),
                dict(kind='green', gx=_pr[1]['gx'], gy=_pr[1]['gy'])]
    return []


def _pplace_clear():
    # ★ 2026-10-28：对局中摆放 -> 先结束 PLACE 暂停阶段（回 PLAY）
    try:
        _m0 = _PM.get('m')
        if (_PPLACE.get('in_match') and _m0 is not None
                and getattr(_m0, 'phase', '') == 'PLACE'):
            _m0.end_place()
    except Exception:
        pass
    _PM['m'] = None
    _PPLACE.update({'on': False, 'who': 0, 'kind': '', 't': 0.0,
                    't_det': 0.0, 'cand': [], 'status': '',
                    'confirm': False, 'pick': None, 't_pick': 0.0,
                    'in_match': False, 'pair': None})


def _adj_sync(m):
    """★ 2026-10-28：把 MatchFlow.adjust 同步成「调整障碍/传送门」界面。

    True（PREP + 双方就绪 + 本局没买实体件）-> 关商店、进调整画面；
    开局/离开局前 -> 自动退出。"""
    _want = bool(m is not None and m.phase == 'PREP'
                 and getattr(m, 'adjust', False))
    if _want and not _ADJ['on']:
        # ★ 调整界面按实时识别显示（玩家要挪件）；冻结表**先不动**——ESC 回商店
        #   时旧布局还在；真正开始本局时再按调整后的世界重定（_adj_start）。
        _PHYS['adj_ports'] = []      # 配对滞回记忆从本次调整重新开始
        _ADJ.update({'on': True, 't_det': 0.0, 'cand': [], 'confirm': False,
                     'pick': None, 'live_view': False})
        _SHOPV['open'] = False
        print('[布置] ★ 双方就绪 -> 调整障碍/传送门界面'
              '（E/小键盘* 开始本局，ESC 回商店）', flush=True)
        _item_msg_set('调整障碍/传送门：按 E/小键盘* 开始本局')
    elif not _want and _ADJ['on']:
        _ADJ['on'] = False


def _adj_start(m, who=0, world=None):
    """调整界面里的「开始本局」（E / 小键盘*）。返回 (ok, msg)。

    ★★ 2026-10-29 用户实况（"调整界面移过的件，进对局就没了"）：开局第一帧
    正常路径会先用"已购/基线 60px"过滤重建世界再锁——挪过的件不在原位置，
    被过滤掉后才锁进对局。修：**开始本局前先用调整界面当前的世界锁定**
    （全量池），PLAY 第一帧直接吃这份锁定。"""
    if m is None or not _ADJ['on']:
        return False, '不在调整界面'
    if world is not None and not _PHYS.get('lock'):
        try:
            _phys_lock(world)
        except Exception:
            pass
        # ★★ 2026-10-29 用户实况（"调整挪过的件，下一局局前全认不到"）：
        #   挪动只进了锁定副本，base/own 里还是旧位置——下一轮 PREP 按旧位置
        #   匹配全部落空。这里把**免费基线重定到当前调整后的实际位置**，
        #   下一轮（含购买后的摆放界面）就能按新位置认出它们。
        try:
            _PHYS['base_bars'] = [(float(o.cx), float(o.cy))
                                  for o in (getattr(world, 'obstacles', None)
                                            or [])]
            _PHYS['base_greens'] = [(float(p.x), float(p.y))
                                    for p in (getattr(world, 'portals', None)
                                              or [])]
        except Exception:
            pass
        # ★ 调整后的布局重定为冻结件（本局内不再动）
        try:
            _FROZEN['obs'] = [(float(o.cx), float(o.cy), float(o.hw) * 2.0,
                               float(o.hh) * 2.0, math.degrees(float(o.ang)))
                              for o in (getattr(world, 'obstacles', None)
                                        or [])][:3]
            _pg = {}
            for _p in (getattr(world, 'portals', None) or []):
                _pg.setdefault(int(_p.pair) // 2, {})[int(_p.pair) % 2] = (
                    float(_p.x), float(_p.y))
            _FROZEN['ports'] = [(_g[1], _g[0]) for _k, _g in sorted(_pg.items())
                                if 0 in _g and 1 in _g][:2]
        except Exception:
            pass
    ok, msg = m.begin_play()
    if ok:
        _ADJ['on'] = False
        _item_msg_set('开始本局！障碍/传送门已锁定')
        print('[对局] ★ P%d 按「开始本局」-> 开局（障碍/传送门锁定）'
              % int(who or 0), flush=True)
        try:
            _shot_write(dict(t=round(time.time(), 3), ok=True, why='ready',
                             kind='ready', seq=0,
                             msg='P%d 开始本局（障碍/传送门锁定）' % int(who or 0)),
                        player=int(who or 0))
        except Exception:
            pass
    else:
        _item_msg_set(msg)
    return ok, msg


def _pplace_key(who):
    """布置确认键的显示名：P1=E，P2=小键盘*（与购买同键）。

    ★ 2026-10-27 用户实况：P2 的提示也写着「E 确定」，但 P2 的 E 不进 P2
      的计数（E 只加 buy1）→ 看了提示按 E 没反应。提示必须按玩家区分。
    """
    return 'E' if int(who) == 1 else '小键盘*'


def _pplace_enter(m, who, kind):
    """进入实体布置态（局前/对局中；额度=商店买的 items[p][kind]）。

    ★ 2026-10-28（第 3 条）：对局中也能临时加障碍/传送门——本局有效
      （PLAY 里确认时写进锁定副本，不跨局）。返回 (ok, msg)。
    ★★ 2026-10-30 用户定案：**加洞口也走同一套 PLACE 流程**——按 C/小键盘+
      进实时相机布置态，黑球贴边框出黄框，E/* 确认；额度=免费→道具→30 分。"""
    if m is None:
        return False, '不在对局'
    if kind == 'hole':
        if m.phase != 'PLAY':
            return False, '加洞口只能在对局中使用'
        if not (int(m.free_hole.get(who, 0)) > 0
                or int(m.items[who]['hole']) > 0
                or int(m.points[who]) >= 30):
            return False, 'P%d 没有加洞口额度（免费/道具/30分）' % int(who)
    else:
        if m.phase not in ('PREP', 'PLAY'):
            return False, '当前阶段不能布置（局前或对局中才可以）'
        if int(m.items[who][kind]) <= 0:
            _n = {'obstacle': '障碍', 'portal': '传送门'}[kind]
            return False, 'P%d 没有%s额度（局前商店先买）' % (who, _n)
    if not _PHYS.get('base_ready'):
        return False, '相机还在稳定中，等一两秒再按'
    if _PPLACE['on']:
        return False, '已经有布置在进行'
    _inm = (m.phase == 'PLAY')
    if _inm:
        # ★★ 2026-10-28 用户定案：对局中临时摆放 = **全场暂停**（PLACE 阶段）
        _okp0, _msgp0 = m.begin_place(who)
        if not _okp0:
            return False, _msgp0
    _PM['m'] = m
    _PPLACE.update({'on': True, 'who': int(who), 'kind': kind,
                    't': time.time(), 't_det': 0.0, 'cand': [],
                    'status': 'waiting', 'confirm': False,
                    'pick': None, 't_pick': 0.0, 'in_match': _inm,
                    'hkey': None, 'hballs': []})
    if kind == 'hole':
        return True, ('P%d 布置加洞口：把黑球贴到场区任意边框，'
                      '黄框出现后按 %s 放置（扣免费/道具/30分；ESC 取消）'
                      % (int(who), _pplace_key(who)))
    _n = {'obstacle': '黑条', 'portal': '一对绿门'}[kind]
    _when = '本局临时' if m.phase == 'PLAY' else ''
    return True, ('P%d 布置%s%s：把实体%s放到场区，识别到黄框后按 %s 放置完成'
                  '（才扣 1 次；ESC 取消）'
                  % (who, _when,
                     '障碍' if kind == 'obstacle' else '传送门', _n,
                     _pplace_key(who)))


def _pplace_check(m, now=None, world=None):
    """摆放态每帧（~3Hz）判定：出现未认领的稳定实体件 -> 扣道具、登记、退出。

    返回 True=这一帧完成了一次布置。任何异常都不许带走主循环。"""
    if not _PPLACE['on']:
        return False
    now = time.time() if now is None else now
    try:
        if m is None or m.phase not in ('PREP', 'PLAY', 'PLACE'):
            _item_msg_set('已离开可布置阶段——布置取消（额度未扣）')
            _pplace_clear()
            return False
        _who, _kind = int(_PPLACE['who']), _PPLACE['kind']
        # ★ 传送门手动点选进行中：以玩家点的为准（不让自动候选覆盖）
        if _PPLACE.get('pair'):
            _PPLACE['cand'] = [dict(p) for p in _PPLACE['pair']]
            _PPLACE['confirm'] = (len(_PPLACE['pair']) >= 2)
            return False
        if _kind == 'hole':
            # ★★ 2026-10-30：加洞口 = 黑球贴任意边框，实时出候选（同障碍流程）
            if not (int(m.free_hole.get(_who, 0)) > 0
                    or int(m.items[_who]['hole']) > 0
                    or int(m.points[_who]) >= 30):
                _item_msg_set('P%d 没有加洞口额度了——布置取消' % _who)
                _pplace_clear()
                return False
            _bc = _ball_border_candidate(_PPLACE.get('hballs') or [])
            if _bc is None:
                _PPLACE['cand'] = []
                _PPLACE['confirm'] = False
                _PPLACE['status'] = 'waiting'
                return False
            _ed, _ps = _bc
            _key = (_ed, round(float(_ps), 1))
            if _PPLACE.get('hkey') != _key:
                _PPLACE['hkey'] = _key
                print('[布置] P%d 黑球贴边 -> 洞口候选 %s %.0f'
                      '（E/* 放置 / ESC 取消）'
                      % (_who, _ed, _ps), flush=True)
                _item_msg_set('黑球贴边候选：%s %.0f——E/* 放置，ESC 取消'
                              % (_ed, _ps))
            _PPLACE['cand'] = [dict(kind='hole', edge=_ed, pos=float(_ps))]
            _PPLACE['confirm'] = True
            _PPLACE['status'] = 'confirm'
            return False
        if int(m.items[_who][_kind]) <= 0:
            _item_msg_set('P%d 没有额度了——布置取消' % _who)
            _pplace_clear()
            return False
        # ★★ 2026-10-27 鼠标点选：锁定的那根优先，**不再走自动候选/基线过滤**。
        #   玩家明确点了"这一根"，哪怕它离基线 <60px 也算数；只有连续 1.5s
        #   看不到它才放回自动识别。
        _pick = _PPLACE.get('pick')
        if _pick:
            _bestp = None
            for _t in (_PHYS.get('applied_bars')
                       or _PHYS.get('bars') or []):
                try:
                    _d = math.hypot(float(_t['gx']) - float(_pick['gx']),
                                    float(_t['gy']) - float(_pick['gy']))
                    if _d <= 45.0:
                        _bestp = _t
                        break
                except Exception:
                    continue
            if _bestp is not None:
                _pick.update({'gx': float(_bestp['gx']),
                              'gy': float(_bestp['gy']),
                              'glen': float(_bestp.get('glen', 0.0)),
                              'gthick': float(_bestp.get('gthick', 0.0)),
                              'gang': float(_bestp.get('gang',
                                                       _pick.get('gang', 0.0)))})
                _PPLACE['cand'] = [dict(_pick)]
                _PPLACE['status'] = 'confirm'
                _PPLACE['confirm'] = True
                return False
            if now - float(_PPLACE.get('t_pick') or 0.0) > 1.5:
                _PPLACE['pick'] = None
                _PPLACE['confirm'] = False
                _PPLACE['cand'] = []
                _PPLACE['status'] = 'waiting'
                _item_msg_set('选中的黑条不见了——重新点或等自动识别')
                print('[布置] 点选的黑条 1.5s 未见——回到自动识别', flush=True)
            return False
        # ★ 2026-10-29 用户实况（"黑条时有时无/放中间更难"）：候选改用**跟踪集合**
        #   （最近 0.8s 见过就算，不再等 0.4s 稳定）；只影响"黄框提示"，
        #   点击/确认/合法性都不变。基线匹配半径放宽到 45px（见下）。
        _cand = _pplace_candidates(
            _kind, _PHYS.get('applied_bars') or _PHYS.get('bars') or [],
            (_PHYS.get('applied_greens') or _PHYS.get('greens') or [])
            if _kind == 'portal' else (_PHYS.get('greens') or []),
            _PHYS.get('base_bars') or [], _PHYS.get('base_greens') or [],
            _PHYS.get('own_bars') or [], _PHYS.get('own_pairs') or [])
        _PPLACE['cand'] = _cand
        if not _cand:
            if _PPLACE.get('confirm'):
                _PPLACE['confirm'] = False
                _PPLACE['status'] = 'waiting'
                _item_msg_set('新件不见了——继续摆放')
            else:
                _PPLACE['status'] = 'waiting'
            return False
        if _PPLACE.get('confirm'):
            return False            # 候选已亮出，等 E 确认 / ESC 取消
        # ★★ 2026-10-27 用户实况：之前识别到就**自动扣**（P1 扣在场区边缘 877,10、
        #   P2 扣在基线附近 311,219 的错件上）——太快且没有确认。现在只亮候选：
        #   黄圈 + 「E/小键盘* 确定 / ESC 取消」，**不扣**。
        _PPLACE['confirm'] = True
        _PPLACE['status'] = 'confirm'
        _msg = ('识别到新件（黄框）——%s 放置完成 / ESC 取消（候选 %d 个）'
                % (_pplace_key(_PPLACE['who']), len(_cand)))
        print('[布置] ' + _msg, flush=True)
        _item_msg_set(_msg)
        return False
    except Exception as _e:
        print('[布置] ★ 判定异常（本次不生效）：%s' % str(_e)[:140], flush=True)
        _pplace_clear()
        return False


def _pplace_commit_now(m, world=None):
    """确认摆放（E/小键盘*）：扣 1 次并登记；**不做自动扣**。返回 (ok,msg)。"""
    if not _PPLACE['on'] or not _PPLACE.get('confirm'):
        return False, '没有待确认的新件'
    _who, _kind = int(_PPLACE['who']), _PPLACE['kind']
    if m is None or m.phase not in ('PREP', 'PLAY', 'PLACE'):
        _pplace_clear()
        return False, '当前阶段不能布置——已取消（未扣）'
    if _kind == 'hole':
        if not (int(m.free_hole.get(_who, 0)) > 0
                or int(m.items[_who]['hole']) > 0
                or int(m.points[_who]) >= 30):
            _pplace_clear()
            return False, 'P%d 没有加洞口额度了' % _who
    elif int(m.items[_who][_kind]) <= 0:
        _pplace_clear()
        return False, 'P%d 没有额度了' % _who
    _cand = _PPLACE.get('cand') or []
    if not _cand:
        return False, '新件不见了——继续摆放'
    if _kind == 'hole':
        _c = _cand[0]
        _ed = str(_c.get('edge') or '')
        _ps = float(_c.get('pos') or 0.0)
        if world is not None:
            try:
                if not _hole_valid(world, _ed, _ps):
                    return False, '这个位置放不下（太靠角或与已有洞口重叠）'
            except Exception:
                pass
        if int(m.free_hole.get(_who, 0)) > 0:
            m.free_hole[_who] -= 1
        elif int(m.items[_who]['hole']) > 0:
            m.items[_who]['hole'] -= 1
        else:
            m.points[_who] -= 30
        if world is not None:
            world.holes.append({'edge': _ed, 'c': _ps, 'w': HOLE_W_GAME,
                                'target': 0})
        _msg = ('P%d 加洞口生效（%s %.0f）' % (_who, _ed, _ps))
    elif _kind == 'obstacle':
        _t = max(_cand, key=lambda c: float(c.get('glen', 0.0)))
        if world is not None:
            try:
                if not _obst_valid(world, float(_t['gx']), float(_t['gy']),
                                   ang=float(_t.get('gang', 0.0))):
                    return False, '这里放不下（贴边/盖洞/叠障碍）'
            except Exception:
                pass
        _inm2 = m.phase in ('PLAY', 'PLACE')
        if (_inm2 and _PHYS.get('lock')
                and len(_PHYS.get('lock_obs') or []) >= 3):
            return False, '本局障碍已满 3 根（最多 3 根）'
        m.items[_who]['obstacle'] -= 1
        if _inm2 and _PHYS.get('lock'):
            # ★ 本局临时件：写进锁定副本（不跨局），立刻重建世界
            _PHYS.setdefault('lock_obs', []).append(
                (float(_t['gx']), float(_t['gy']),
                 float(_t.get('glen', 0.0)), float(_t.get('gthick', 0.0)),
                 float(_t.get('gang', 0.0))))
            if world is not None:
                _phys_apply_lock(world)
        else:
            _PHYS.setdefault('own_bars', []).append((_t['gx'], _t['gy']))
            # ★ 确认即冻结：按这一刻的位置定死（实心、不再随识别漂）
            _FROZEN['obs'] = (_FROZEN['obs'] + [
                (float(_t['gx']), float(_t['gy']),
                 float(_t.get('glen', 0.0)), float(_t.get('gthick', 0.0)),
                 float(_t.get('gang', 0.0)))])[:3]
        _msg = ('P%d 实体障碍生效（%s%.0f,%.0f；剩余 %d 个）'
                % (_who, '本局临时 ' if _inm2 else '',
                   _t['gx'], _t['gy'], int(m.items[_who]['obstacle'])))
    else:
        if len(_cand) < 2:
            return False, '需要一对绿门（两个新件）'
        _a, _b = _cand[0], _cand[1]
        # ★ 2026-10-29 用户实况：门压到黑条上就判"放不下"（不当传送门）
        if world is not None:
            _bars_w = [dict(gx=float(o.cx), gy=float(o.cy),
                            glen=float(o.hw) * 2.0, gang=math.degrees(float(o.ang)))
                       for o in (getattr(world, 'obstacles', None) or [])]
            if (_bar_near_pt(_bars_w, _a['gx'], _a['gy'])
                    or _bar_near_pt(_bars_w, _b['gx'], _b['gy'])):
                return False, '传送门压在障碍上——挪开再放'
        _inm2 = m.phase in ('PLAY', 'PLACE')
        if (_inm2 and _PHYS.get('lock')
                and len(_PHYS.get('lock_ports') or []) >= 4):
            return False, '本局传送门已满 2 对（最多 2 对）'
        m.items[_who]['portal'] -= 1
        if _inm2 and _PHYS.get('lock'):
            _np = len(_PHYS.get('lock_ports') or []) // 2
            _PHYS.setdefault('lock_ports', []).extend(
                [(float(_a['gx']), float(_a['gy']), 2 * _np + 1),
                 (float(_b['gx']), float(_b['gy']), 2 * _np)])
            if world is not None:
                _phys_apply_lock(world)
        else:
            _PHYS.setdefault('own_pairs', []).append(
                ((_a['gx'], _a['gy']), (_b['gx'], _b['gy'])))
            _FROZEN['ports'] = (_FROZEN['ports'] + [
                ((float(_a['gx']), float(_a['gy'])),
                 (float(_b['gx']), float(_b['gy'])))])[:2]
        _msg = ('P%d 实体传送门生效（%s两门 %.0f,%.0f / %.0f,%.0f；剩余 %d 个）'
                % (_who, '本局临时 ' if _inm2 else '',
                   _a['gx'], _a['gy'], _b['gx'], _b['gy'],
                   int(m.items[_who]['portal'])))
    # ★ 本局真正摆过（就绪时决定要不要进调整界面，看这个而不是"买没买"）
    try:
        if m.phase == 'PREP':
            m.mark_placed(_who)
    except Exception:
        pass
    _PPLACE['status'] = 'done'
    print('[布置] ' + _msg, flush=True)
    _item_msg_set(_msg)
    try:
        _shot_write(dict(t=round(time.time(), 3), ok=True, why='item',
                         kind='item', seq=0, msg=_msg), player=_who)
    except Exception:
        pass
    _pplace_clear()
    return True, _msg


def _pplace_pick_bar(gx, gy, bars, max_d=90.0):
    """点选黑条：取离点击点**条身线段**最近的一根（同距取更长）。

    ★ 2026-10-27 用户定案：自动识别会在多根条/碎片之间跳，改成玩家用鼠标
      明确点「这一根」。点到线段距离（不是中心距）——点长条任意位置都能选中。
    """
    _best = None
    for _b in (bars or []):
        try:
            _gl = max(1.0, float(_b.get('glen', 0.0)))
            _ang = math.radians(float(_b.get('gang', 0.0)))
            _ca, _sa = math.cos(_ang), math.sin(_ang)
            _dx = float(gx) - float(_b['gx'])
            _dy = float(gy) - float(_b['gy'])
            _t = max(-1.0, min(1.0,
                               (_dx * _ca + _dy * _sa) / (_gl / 2.0)))
            _px = float(_b['gx']) + _ca * _t * (_gl / 2.0)
            _py = float(_b['gy']) + _sa * _t * (_gl / 2.0)
            _d = math.hypot(float(gx) - _px, float(gy) - _py)
            if _d > max_d:
                continue
            if (_best is None or _d < _best[0] - 1e-6
                    or (abs(_d - _best[0]) <= 1e-6
                        and _gl > float(_best[1].get('glen', 0.0)))):
                _best = (_d, _b)
        except Exception:
            continue
    return _best[1] if _best else None


def _pplace_click_game(mx, my, Hm, pump_w, pump_h, crop_margin=0.06):
    """布置画面点击 -> 游戏坐标（与当前显示同一份裁剪/缩放口径）。

    实时相机视图：1280x720 输出 = 原图 1024x720 裁剪后缩放（CROP=1）；
    合成俯视图：960x540 居中（左右各 (W-960)/2）。
    """
    import v3_scan
    if _PPLACE.get('live_view'):
        # ★ 2026-10-30：直播画面已做**场区透视矫正**（raw->1280x720 单应）；
        #   点击反算走它的逆变换，再 cam_to_game。旧裁剪口径留作兜底。
        _wv = _LIVE_WARP.get('inv')
        if _wv is not None:
            try:
                _dx = float(mx) * 1280.0 / max(1.0, float(pump_w))
                _dy = float(my) * 720.0 / max(1.0, float(pump_h))
                _p = cv2.perspectiveTransform(
                    np.array([[[_dx, _dy]]], np.float64), _wv)[0][0]
                return v3_scan.cam_to_game(Hm, float(_p[0]), float(_p[1]))[:2]
            except Exception:
                pass
        _rw = float(_PPLACE.get('raw_w') or 1024)
        _rh = float(_PPLACE.get('raw_h') or 720)
        if _PPLACE.get('crop_on'):
            _r = _live_crop_rect(int(_rw), int(_rh), Hm, crop_margin)
            if _r is not None:
                _x0, _y0, _x1, _y1 = _r
                _cx = _x0 + (float(mx) / max(1.0, float(pump_w))) * (_x1 - _x0)
                _cy = _y0 + (float(my) / max(1.0, float(pump_h))) * (_y1 - _y0)
                return v3_scan.cam_to_game(Hm, _cx, _cy)[:2]
        return v3_scan.cam_to_game(
            Hm, float(mx) * _rw / max(1.0, float(pump_w)),
            float(my) * _rh / max(1.0, float(pump_h)))[:2]
    _ox = (int(pump_w) - 960) // 2
    _oy = (int(pump_h) - 540) // 2
    return float(mx) - _ox, float(my) - _oy


def _pplace_click_green(mx, my, Hm, pump_w, pump_h, crop_margin=0.06):
    """传送门摆放：点第一扇绿门 -> 再点第二扇（间距>=80）-> 黄框 -> E 确认。"""
    _greens = _PHYS.get('greens') or _PHYS.get('applied_greens') or []
    if not _greens:
        _item_msg_set('还没看到绿门——先把一对他绿门放到场区再点')
        print('[布置] 点选：场上还没有绿门（跟踪集合为空）', flush=True)
        return True
    try:
        _gx, _gy = _pplace_click_game(mx, my, Hm, pump_w, pump_h, crop_margin)
    except Exception as _e:
        print('[布置] 点选坐标换算失败：%s' % str(_e)[:120], flush=True)
        return False
    _best = None
    for _g in _greens:
        _d = math.hypot(float(_g['gx']) - _gx, float(_g['gy']) - _gy)
        if _best is None or _d < _best[0]:
            _best = (_d, _g)
    if _best is None or _best[0] > 90.0:
        _dbg = ' | '.join('(%.0f,%.0f)' % (float(t['gx']), float(t['gy']))
                          for t in _greens[:6])
        print('[布置] 点选绿门落空：游戏(%.0f,%.0f)；跟踪绿门=%s'
              % (_gx, _gy, _dbg), flush=True)
        _item_msg_set('没点到绿门——靠近绿门再点一次')
        return True
    _pick = dict(kind='green', gx=float(_best[1]['gx']),
                 gy=float(_best[1]['gy']))
    _pp = [dict(p) for p in (_PPLACE.get('pair') or [])]
    if _pp and math.hypot(_pp[0]['gx'] - _pick['gx'],
                          _pp[0]['gy'] - _pick['gy']) < 80.0:
        _item_msg_set('两扇门太近了（间距至少 80 像素）——点另一扇')
        return True
    _pp.append(_pick)
    _PPLACE['pair'] = _pp
    _PPLACE['cand'] = [dict(p) for p in _pp]
    _PPLACE['confirm'] = (len(_pp) >= 2)
    _PPLACE['status'] = 'confirm' if len(_pp) >= 2 else 'pick1'
    _key = _pplace_key(_PPLACE['who'])
    if len(_pp) >= 2:
        _msg = ('已点选一对绿门——%s 放置完成 / ESC 取消' % _key)
    else:
        _msg = '已点第一扇绿门——再点第二扇（间距至少 80 像素）'
    _item_msg_set(_msg)
    print('[布置] ' + _msg, flush=True)
    return True


def _pplace_mouse_click(mx, my, Hm, pump_w, pump_h, crop_margin=0.06):
    """布置态左键点选：黑条（一下锁定）/ 绿门（点两扇凑一对）。返回 True=已消费。"""
    if not _PPLACE.get('on'):
        return False
    if _PPLACE.get('kind') == 'hole':
        return True              # 加洞口只认黑球贴边，鼠标点选不参与
    if _PPLACE.get('kind') == 'portal':
        return _pplace_click_green(mx, my, Hm, pump_w, pump_h, crop_margin)
    # ★★ 2026-10-27 实况：用户点的短条原始识别**看得到**（条 2 (345,205 长102)），
    #   但时有时无、没达到"稳定 0.4s"——只查 applied_bars 会一直落空。
    #   点选改查**跟踪集合**（最近 0.8s 见过就算；玩家已经用手指点明了）。
    # ★ 2026-10-29 用户实况（"一根条画出两个黄框"）：优先用**合并后**的稳定
    #   集合（applied_bars）——反光把黑条切成两半时，两个碎片要先并成一根。
    _bars = _PHYS.get('applied_bars') or _PHYS.get('bars') or []
    if not _bars:
        _item_msg_set('还没看到黑条——先把黑条放到场区再点')
        print('[布置] 点选：场上还没有黑条（跟踪集合为空）', flush=True)
        return True
    try:
        _gx, _gy = _pplace_click_game(mx, my, Hm, pump_w, pump_h, crop_margin)
    except Exception as _e:
        print('[布置] 点选坐标换算失败：%s' % str(_e)[:120], flush=True)
        return False
    _b = _pplace_pick_bar(_gx, _gy, _bars)
    if _b is None:
        _item_msg_set('没点到黑条——靠近黑条再点一次')
        _dbg = ' | '.join('(%.0f,%.0f 长%.0f)'
                          % (float(t['gx']), float(t['gy']),
                             float(t.get('glen', 0.0)))
                          for t in _bars[:6])
        print('[布置] 点选落空：点击画布(%d,%d)->游戏(%.0f,%.0f)，'
              '90px 内没有黑条；跟踪条=%s'
              % (mx, my, _gx, _gy, _dbg), flush=True)
        return True
    _pick = dict(kind='bar', gx=float(_b['gx']), gy=float(_b['gy']),
                 glen=float(_b.get('glen', 0.0)),
                 gthick=float(_b.get('gthick', 0.0)),
                 gang=float(_b.get('gang', 0.0)))
    _PPLACE['pick'] = _pick
    _PPLACE['t_pick'] = time.time()
    _PPLACE['cand'] = [_pick]
    _PPLACE['confirm'] = True
    _PPLACE['status'] = 'confirm'
    _key = _pplace_key(_PPLACE['who'])
    _msg = ('已点选黑条（长 %.0f）——%s 放置完成 / ESC 取消'
            % (_pick['glen'], _key))
    _item_msg_set(_msg)
    print('[布置] ' + _msg, flush=True)
    return True


def _portal_tuples(portals):
    """engine.Portal 列表 -> realtime_ball 口径 (x, y, pair)。"""
    return [(p.x, p.y, p.pair) for p in (portals or [])]


def _norm_holes(world):
    """★ 2026-10-29 用户实况（"两个洞只有一个能进球"）：engine.setup_round 给洞
    设了 target（右=0 / 左=1），真球 `advance(side=0)` 只认 `target==side` 的洞
    → 左洞永不判进球。演示口径（规则 A）：**两个洞都算进球**，target 全摊 0。
    预览/PL/黄金模型吃的是同一份 world.holes，所以口径一致。"""
    for _h in (getattr(world, 'holes', None) or []):
        try:
            _h['target'] = 0
        except Exception:
            pass
    return world


def _phys_in_field(gx, gy, m=10.0):
    """实物必须在**场区内**（场区外=线缆/桌面杂物——2026-10-19 上板实锤：
    右侧转接板被当成竖长条、顶边黑物被当成黑洞标记）。

    ★★ 2026-10-29：摆放态把容差放宽到 60px——用户把条/门摆到场区边角上
    （中心略出界）是合理的，旧的 10px 会直接把它们丢掉，表现为"边角怎么都选不中"。"""
    return (-m <= gx <= 960.0 + m) and (-m <= gy <= 540.0 + m)


def _hole_balls_from_pd(pd, vs, Hm, fm=60.0):
    """★ 2026-10-30 加洞口**专用**黑球检测链（与障碍/传送门完全分开）：

    只取 `phys_objects` 的 balls -> 缩略图映射进场区（外扩 fm）；
    不碰 bars/greens、不写 `_PHYS['balls']`、不改世界/锁定副本。"""
    out = []
    for b in (pd.get('balls') or []):
        try:
            _g = _map_to_game(vs, Hm, b['cx'], b['cy'])
        except Exception:
            continue
        if _phys_in_field(_g[0], _g[1], fm):
            out.append(dict(gx=float(_g[0]), gy=float(_g[1]),
                            area=b.get('area', 0)))
    return out


def _merge_bars(bars, gap=40.0, ang_tol=15.0, perp_tol=20.0):
    """把同一根黑条被遮挡/反光切开的碎片合并成一根（2026-10-28 实况：条 4）。

    只在**共线、垂直距离小、沿线方向间隙小**时合并；两根真正分开的条不会并。
    """
    items = [dict(b) for b in (bars or [])]
    if len(items) < 2:
        return items
    changed = True
    while changed:
        changed = False
        out = []
        dead = set()
        for i in range(len(items)):
            if i in dead:
                continue
            a = items[i]
            ax = math.radians(float(a.get('gang', 0.0)))
            ac, as_ = math.cos(ax), math.sin(ax)
            ah = max(1.0, float(a.get('glen', 0.0)) / 2.0)
            for j in range(i + 1, len(items)):
                if j in dead:
                    continue
                b = items[j]
                bd = abs((float(b.get('gang', 0.0))
                          - math.degrees(ax) + 180.0) % 360.0 - 180.0)
                if bd > ang_tol:
                    continue
                dx = float(b['gx']) - float(a['gx'])
                dy = float(b['gy']) - float(a['gy'])
                along = dx * ac + dy * as_
                perp = abs(-dx * as_ + dy * ac)
                bh = max(1.0, float(b.get('glen', 0.0)) / 2.0)
                gapv = max(0.0, abs(along) - (ah + bh))
                if perp > perp_tol or gapv > gap:
                    continue
                t0 = min(-ah, along - bh)
                t1 = max(ah, along + bh)
                # ★ 合起来超过长条上限（~260 游戏 px）就不是"同一根的碎片"
                #   ——两根各 ~200 的长条不能并成一根（用户实况：两根并成一根）
                if (t1 - t0) > 260.0:
                    continue
                a = dict(gx=float(a['gx']) + ac * (t0 + t1) / 2.0,
                         gy=float(a['gy']) + as_ * (t0 + t1) / 2.0,
                         glen=t1 - t0,
                         gthick=max(float(a.get('gthick', 0.0)),
                                    float(b.get('gthick', 0.0))),
                         gang=math.degrees(ax))
                ah = max(1.0, a['glen'] / 2.0)
                dead.add(j)
                changed = True
            out.append(a)
        items = out
    return items


def _ball_near_bars(b, bars, pad=10.0):
    """黑球候选是否贴在黑条上（碎片/反光造成的假黑球，2026-10-28 实况）。"""
    for a in (bars or []):
        ang = math.radians(float(a.get('gang', 0.0)))
        ca, sa = math.cos(ang), math.sin(ang)
        dx = float(b['gx']) - float(a['gx'])
        dy = float(b['gy']) - float(a['gy'])
        along = abs(dx * ca + dy * sa)
        perp = abs(-dx * sa + dy * ca)
        if (along <= float(a.get('glen', 0.0)) / 2.0 + pad
                and perp <= max(8.0,
                                float(a.get('gthick', 0.0)) / 2.0 + pad)):
            return True
    return False


def _coast(ph, key, cur, now, keep=2.0):
    """当前有效件 + 最近 keep 秒内见过的旧件（识别闪断不消失）。

    ★★ 2026-10-29 用户实况（"摆传送门时两根障碍全没了"）：黑条识别本身有
    闪断（日志 `条 0`），一帧没认到就被从世界剔除。这里做 2 秒**保持**：
    旧件只要还在 keep 内、且没有被当前检测覆盖（中心 30px / 条身线段 30px），
    就继续显示；真的拿走/移走超过 2 秒才消失。"""
    _old = ph.get(key) or []
    _res, _seen = [], []
    for _c in (cur or ()):
        _res.append(_c)
        _seen.append((_c, now))
    for _o, _t0 in _old:
        if now - float(_t0) > keep:
            continue
        _dup = False
        for _c in (cur or ()):
            _dx = float(_o[0]) - float(_c[0])
            _dy = float(_o[1]) - float(_c[1])
            if math.hypot(_dx, _dy) <= 30.0:
                _dup = True
                break
            _gl = float(_c[2] if len(_c) > 2 else 0.0)
            _ga = math.radians(float(_c[4] if len(_c) > 4 else 0.0))
            if _gl > 1.0:
                _u = max(-_gl / 2.0,
                         min(_gl / 2.0, _dx * math.cos(_ga) + _dy * math.sin(_ga)))
                if math.hypot(_o[0] - (float(_c[0]) + math.cos(_ga) * _u),
                              _o[1] - (float(_c[1]) + math.sin(_ga) * _u)) <= 30.0:
                    _dup = True
                    break
        if not _dup:
            _res.append(_o)
            _seen.append((_o, _t0))
    ph[key] = _seen
    return _res


def _phys_lock(world):
    """开局瞬间锁定当前障碍/传送门/洞口标记；PLAY 期间不再随识别而变。"""
    if _PHYS.get('lock'):
        return
    _PHYS['lock'] = True
    # 存回 engine.Obstacle 的构造口径：w/h=2*hw/2*hh、ang_deg
    _PHYS['lock_obs'] = [(float(o.cx), float(o.cy),
                          float(o.hw) * 2.0, float(o.hh) * 2.0,
                          math.degrees(float(o.ang)))
                         for o in (getattr(world, 'obstacles', None) or [])]
    _PHYS['lock_ports'] = _drop_ports_on_obs(
        [(float(p.x), float(p.y), int(p.pair))
         for p in (getattr(world, 'portals', None) or [])],
        _PHYS['lock_obs'])
    # ★ 只锁障碍/传送门（用户口径）——洞口与黑洞标记仍按规则动（30s 挪步/
    #   每局随机/标记跟随），锁了会把洞冻结在开局位置。
    _PHYS['lock_holes'] = []
    _PHYS['lock_marks'] = []
    _PHYS['lock_mark_on'] = False
    print('[实物] ★ 对局开始：障碍/传送门已锁定（%d 根 / %d 个门；PLAY 期间不可改）'
          % (len(_PHYS['lock_obs']), len(_PHYS['lock_ports'])), flush=True)


def _phys_apply_lock(world):
    """PLAY 每帧：用锁定副本覆盖世界（不受识别抖动/碎片/假球影响）。"""
    import engine as _eng
    world.obstacles = [_eng.Obstacle(cx, cy, w, h, ang)
                       for (cx, cy, w, h, ang)
                       in (_PHYS.get('lock_obs') or [])]
    # ★ 2026-10-29：锁里万一混进"压条门"（锁定瞬间条还没识别到），应用时
    #   再剔一次——PLAY 里绝不会画出压在障碍上的传送门。
    world.portals = [_eng.Portal(x, y, pair)
                     for (x, y, pair) in _drop_ports_on_obs(
                         _PHYS.get('lock_ports') or [],
                         _PHYS.get('lock_obs') or [])]
    # 洞口/标记不在锁定范围（见 _phys_lock）。


def _phys_update(world, pd, vs, Hm, manual_obs, raw=False, raw_scale=1.0,
                 balls=None):
    """一帧实物识别结果 -> 世界（稳定性过滤后）：障碍/传送门/洞口标记。

    raw=False：pd 坐标是 map 像素（128x90）；raw=True：pd 是原图（1024x720）
    像素——摆放/调整界面用原图识别（小绿门/短条在缩略图上只有几像素，认不稳）。"""
    def _to_game(mx, my):
        if raw:
            _k = float(raw_scale)
            return vs.cam_to_game(Hm, float(mx) * _k, float(my) * _k)[:2]
        return _map_to_game(vs, Hm, mx, my)
    now = time.time()
    _fm = 60.0 if (_PPLACE.get('on') or _ADJ.get('on')) else 10.0
    # ★★ 2026-10-29 用户点单：TEST_PHYS_LOG=1 打**原始**识别（含被场区边界
    #   丢掉的条）——用来定位"上面认得、下面认不到"。正式运行不设（默认关）。
    _diag = bool(os.environ.get('TEST_PHYS_LOG'))
    _dbars = []
    _bars = []
    for b in (pd.get('bars') or []):
        _ca = math.cos(math.radians(float(b['ang'])))
        _sa = math.sin(math.radians(float(b['ang'])))
        _hx, _hy = _ca * b['length'] / 2.0, _sa * b['length'] / 2.0
        _g1 = _to_game(b['cx'] - _hx, b['cy'] - _hy)
        _g2 = _to_game(b['cx'] + _hx, b['cy'] + _hy)
        _glen = math.hypot(_g2[0] - _g1[0], _g2[1] - _g1[1])
        _sc = _glen / max(1e-9, float(b['length']))
        _gcx = (_g1[0] + _g2[0]) / 2.0
        _gcy = (_g1[1] + _g2[1]) / 2.0
        _inf = _phys_in_field(_gcx, _gcy, _fm)
        if _diag:
            _dbars.append((_gcx, _gcy, _glen, float(b['thick']) * _sc, _inf))
        if not _inf:
            continue
        _bars.append(dict(gx=_gcx, gy=_gcy,
                          glen=_glen, gthick=float(b['thick']) * _sc,
                          gang=math.degrees(math.atan2(_g2[1] - _g1[1],
                                                       _g2[0] - _g1[0]))))
    if _diag and now - float(_PHYS.get('t_diag') or 0.0) >= 1.5:
        _PHYS['t_diag'] = now
        print('[实物诊断] 场区余量 %.0fpx；detect 原始条 %d：%s'
              % (_fm, len(_dbars), ' | '.join(
                  '(%.0f,%.0f 长%.0f 厚%.1f%s)'
                  % (t[0], t[1], t[2], t[3], '' if t[4] else ' ◀场外')
                  for t in _dbars[:8]) or '（无）'), flush=True)
    # ★ 2026-10-28：同一根条被切开的碎片先合并（实况 `[实物] 条 4`）
    _bars = _merge_bars(_bars)
    _balls = []
    for b in (pd.get('balls') or []):
        _g = _to_game(b['cx'], b['cy'])
        if _phys_in_field(_g[0], _g[1], _fm):
            _balls.append(dict(gx=_g[0], gy=_g[1], area=b['area']))
    # ★ 2026-10-28：贴在黑条上的黑球候选是碎片/反光——不当洞口标记
    _balls = [b for b in _balls if not _ball_near_bars(b, _bars)]
    # ★★ 2026-10-28 用户实况（"摆放时出现虚假的球"）：障碍/传送门摆放期间
    #   **不认黑洞标记**。加洞口布置走独立检测链（见 `_hole_balls_from_pd`），
    #   根本不进这个函数。
    if _PPLACE.get('on'):
        _balls = []
    _greens = []
    for g in (pd.get('greens') or []):
        _pp = _to_game(g['cx'], g['cy'])
        if _phys_in_field(_pp[0], _pp[1], _fm):
            _greens.append(dict(gx=_pp[0], gy=_pp[1], area=g['area']))
    _PHYS['bars'], _ab = _phys_track(_PHYS['bars'], _bars, now)
    # ★ 2026-10-28 二修（用户："障碍物还是分成两段"）：碎片在两帧里交替出现、
    #   同一帧凑不齐时，上面的原始合并够不着——稳定集合再合并一次（世界/候选
    #   都吃合并后的整条，黄框不再套两段）。
    _ab = _merge_bars(_ab)
    # ★ 标定日志（1.5s 限频）：现场按实物调阈值时一眼看到识别量
    if now - _PHYS.get('t', 0.0) > 1.5:
        _PHYS['t'] = now
        if _bars or _greens or _balls:
            print('[实物] 条 %d%s | 绿 %d%s | 黑球 %d%s' % (
                len(_bars),
                (' (%.0f,%.0f 长%.0f 角%.0f)' % (_bars[0]['gx'],
                                                _bars[0]['gy'],
                                                _bars[0]['glen'],
                                                _bars[0]['gang'])
                 if _bars else ''),
                len(_greens),
                (' (%.0f,%.0f a%.0f)%s' % (
                    _greens[0]['gx'], _greens[0]['gy'],
                    _greens[0].get('area', 0),
                    (' (%.0f,%.0f a%.0f)' % (
                        _greens[1]['gx'], _greens[1]['gy'],
                        _greens[1].get('area', 0)))
                    if len(_greens) > 1 else '')
                 if _greens else ''),
                len(_balls),
                (' (%.0f,%.0f)' % (_balls[0]['gx'], _balls[0]['gy'])
                 if _balls else '')), flush=True)
    _PHYS['balls'], _ak = _phys_track(_PHYS['balls'], _balls, now,
                                      apply_s=1.0)
    _PHYS['greens'], _ag = _phys_track(_PHYS['greens'], _greens, now)
    # ★ 2026-10-25 实体道具门：保存本帧"稳定"集合（摆放判定用），并在开局
    #    PHYS_BASE_WAIT 后锁定**免费基线**——此前已在桌上的实体件算免费地形。
    _PHYS['applied_bars'] = list(_ab)
    _PHYS['applied_greens'] = list(_ag)
    if _PHYS.get('t0') is None:
        _PHYS['t0'] = now
    if not _PHYS.get('base_ready') and \
            now - float(_PHYS['t0']) >= PHYS_BASE_WAIT:
        _PHYS['base_bars'] = [(t['gx'], t['gy']) for t in _ab]
        _PHYS['base_greens'] = [(t['gx'], t['gy']) for t in _ag]
        _PHYS['base_ready'] = True
        print('[实物] 免费基线锁定：黑条 %d / 绿门 %d'
              '（之后新摆的实体件要买道具才生效）'
              % (len(_PHYS['base_bars']), len(_PHYS['base_greens'])),
              flush=True)
    # ★ 只认**最大的一个**黑球做洞口标记（上板实锤：多个暗斑会把左右洞
    #   都拽走——日志里 left/right 每 1.5s 来回跳）。
    _ak = sorted(_ak, key=lambda t: -t.get('area', 0))[:1]
    # ★ 大屏用：稳定后的黑球标记位置（画在球的实际位置，别再只画边上的洞线）
    _PHYS['marks'] = list(_ak)

    # ★★ 多件：最多 3 根（PL 预览 3 套障碍寄存器）。已购优先，其次免费基线；
    #   同组按长度降序。保持确定性顺序 —— PL/黄金模型/真球吃的是同一份列表。
    import engine as _eng
    if _ADJ.get('on'):
        # ★★ 2026-10-29 用户实况（"挪一下位置就再也认不到"）：调整界面改用
        #   **跟踪集合**（最近 0.8s 见过就算）——挪动后新位置立刻算，
        #   识别偶尔漏一帧也不会消失；开局瞬间再锁定这份布局。
        _act = list(_PHYS.get('applied_bars') or _PHYS.get('bars') or _ab)
    else:
        _act = _phys_active(_ab, _PHYS.get('base_bars') or [],
                            _PHYS.get('own_bars') or [], PHYS_BAR_MATCH)
    if _ADJ.get('on'):
        # 调整界面：**当前识别到的全部条**（最多 3 根，长的优先），
        # 不再要求"离原已购/基线位置 60px 内"——挪动后的条会立刻回来。
        _bars3 = sorted(_act, key=lambda t: -float(t.get('glen', 0.0)))[:3]
    else:
        _own_b = _PHYS.get('own_bars') or []
        _own_act = [t for t in _act
                    if _near_any(t['gx'], t['gy'], _own_b, PHYS_BAR_MATCH)]
        _base_act = [t for t in _act
                     if _near_any(t['gx'], t['gy'],
                                  _PHYS.get('base_bars') or [],
                                  PHYS_BAR_MATCH)]
        _own_ids = {id(t) for t in _own_act}
        _pool = (sorted(_own_act, key=lambda t: -float(t.get('glen', 0.0)))
                 + sorted([t for t in _base_act if id(t) not in _own_ids],
                          key=lambda t: -float(t.get('glen', 0.0))))
        _bars3 = _pool[:3]
    _cur_obs = [(float(t['gx']), float(t['gy']), float(t['glen']),
                 float(t['gthick']), float(t['gang'])) for t in _bars3]
    # 识别闪断保持（2s）：已生效的条不会因为一两帧没认到就消失
    _cur_obs = _coast(_PHYS, 'last_obs', _cur_obs, now)
    # ★★ 2026-10-29 用户实况（"发射的棋子被认成障碍"）：跟踪到的实物球附近
    #   （影子/拖影/棋子轮廓）不认成障碍——球是玩家在打的，不是地形。
    if balls:
        _bp = []
        for (_bx0, _by0, _br0) in balls:
            try:
                _g0 = vs.cam_to_game(Hm, float(_bx0), float(_by0))[:2]
            except Exception:
                continue
            _bp.append((float(_g0[0]), float(_g0[1]),
                        max(35.0, float(_br0) * 1.6)))
        if _bp:
            _cur_obs = [t for t in _cur_obs
                        if not any(math.hypot(t[0] - b[0], t[1] - b[1]) < b[2]
                                   for b in _bp)]
    if not _ADJ.get('on'):
        # ★ 确认放置的件按"确认那一刻"定死（冻结优先，识别同根件剔除）
        _cur_obs = _frozen_merge(_cur_obs)
    _obs = [_eng.Obstacle(*t) for t in _cur_obs][:3]
    world.obstacles = _obs if _obs else manual_obs

    # ★★ 多件：最多 2 对门（PL 预览 2 套门寄存器）。已购对优先（最新在前），
    #   不足再从剩余绿门里补一对（面积>=12、间距>=80px）。列表顺序固定为
    #   [对0门A, 对0门B, 对1门A, 对1门B]，pair=列表内另一端下标。
    _own_g = [p for pr in (_PHYS.get('own_pairs') or []) for p in pr]
    if _ADJ.get('on'):
        _ag_act = list(_PHYS.get('greens') or _ag)
    else:
        _ag_act = _phys_active(_ag, _PHYS.get('base_greens') or [], _own_g,
                               PHYS_GREEN_MATCH)
    # ★ 2026-10-29 用户实况（"有障碍的地方识别出了传送门，和障碍重叠"）：
    #   配对前先把压在黑条上的绿点剔掉——此前只有购买摆放那条路有这条规则，
    #   世界生成（调整界面/对局）漏了，绿门落在黑条上仍会被配成传送门。
    #   用 `_cur_obs`（已含冻结合并）而不是 `_bars3`：PLAY 里冻结条某帧没被
    #   识别到时也照样挡住绿点。
    _ag_act = _drop_greens_on_bars(
        _ag_act, [dict(gx=_t[0], gy=_t[1], glen=_t[2], gang=_t[4])
                  for _t in _cur_obs])
    _pairs = []
    _used = set()
    if _ADJ.get('on'):
        # 调整界面：**当前识别到的绿门自由配对**（最多 2 对，面积>=6、间距>=80），
        # 不再按"已购对的原位置"匹配——挪动后立刻按新位置配对。
        _remA = [t for t in _ag_act if t.get('area', 0) >= 6]
        while len(_pairs) < 2:
            _candA = [t for t in _remA if id(t) not in _used]
            _foundA = None
            _bestD = None
            for _i in range(len(_candA)):
                for _j in range(_i + 1, len(_candA)):
                    _dA = math.hypot(_candA[_i]['gx'] - _candA[_j]['gx'],
                                     _candA[_i]['gy'] - _candA[_j]['gy'])
                    if _dA >= 80.0 and (_bestD is None or _dA < _bestD):
                        _bestD = _dA
                        _foundA = (_candA[_i], _candA[_j])
            if _foundA is None:
                break
            _pairs.append(_foundA)
            _used.add(id(_foundA[0])); _used.add(id(_foundA[1]))
    for _pr in ([] if _ADJ.get('on') else reversed(_PHYS.get('own_pairs') or [])):
        if len(_pairs) >= 2:
            break
        _p0 = [t for t in _ag_act
               if _near_any(t['gx'], t['gy'], [_pr[0]], PHYS_GREEN_MATCH)
               and id(t) not in _used]
        _p1 = [t for t in _ag_act
               if _near_any(t['gx'], t['gy'], [_pr[1]], PHYS_GREEN_MATCH)
               and id(t) not in _used]
        if (_p0 and _p1 and _p0[0] is not _p1[0]
                and math.hypot(_p0[0]['gx'] - _p1[0]['gx'],
                               _p0[0]['gy'] - _p1[0]['gy']) >= 80.0):
            _pairs.append((_p0[0], _p1[0]))
            _used.add(id(_p0[0])); _used.add(id(_p1[0]))
    if len(_pairs) < 2 and not _ADJ.get('on'):
        _rem = [t for t in _ag_act
                if t.get('area', 0) >= 12 and id(t) not in _used]
        _rem = sorted(_rem, key=lambda t: -float(t.get('area', 0)))
        _found = None
        for _i in range(len(_rem)):
            for _j in range(_i + 1, len(_rem)):
                if math.hypot(_rem[_i]['gx'] - _rem[_j]['gx'],
                              _rem[_i]['gy'] - _rem[_j]['gy']) >= 80.0:
                    _found = (_rem[_i], _rem[_j])
                    break
            if _found is not None:
                break
        if _found is not None:
            _pairs.append(_found)
    _fz_pairs = [((float(_a[0]), float(_a[1])), (float(_b[0]), float(_b[1])))
                 for (_a, _b) in (_FROZEN.get('ports') or [])][:2] \
        if not _ADJ.get('on') else []
    _fz_pts = [_pt for _pr in _fz_pairs for _pt in _pr]
    _rec_pairs = []
    for (_pa, _pb) in _pairs:
        if (_near_any(_pa['gx'], _pa['gy'], _fz_pts, 30.0)
                or _near_any(_pb['gx'], _pb['gy'], _fz_pts, 30.0)):
            continue
        _rec_pairs.append(((float(_pa['gx']), float(_pa['gy'])),
                           (float(_pb['gx']), float(_pb['gy']))))
    _all_pairs = (_fz_pairs + _rec_pairs)[:2]
    world.portals = []
    for _k, (_pa, _pb) in enumerate(_all_pairs):
        # pair 下标 = 列表内另一端（0↔1、2↔3）；RTL 的 pair0/1 同序
        world.portals.append(_eng.Portal(_pa[0], _pa[1], 2 * _k + 1))
        world.portals.append(_eng.Portal(_pb[0], _pb[1], 2 * _k))

    # ★★ 2026-10-30 用户定案：黑球不再**免费自动**挪洞——它只是
    #   「加洞口」道具的物理标记：使用道具时按黑球贴边交点出候选（黄框），
    #   E/* 确认才落洞、扣额度。这里只保留“场上有黑球标记”这一状态
    #   （30s 洞口自适应让位用），不改 world.holes。
    _PHYS['mark_on'] = bool(_ak)


def _obst_half_extent(w, h, ang_deg):
    """旋转矩形在 x/y 轴上的半投影（AABB 半宽/半高）。纯函数。"""
    _a = math.radians(float(ang_deg) % 180.0)
    _ca, _sa = abs(math.cos(_a)), abs(math.sin(_a))
    return ((w / 2.0) * _ca + (h / 2.0) * _sa,
            (w / 2.0) * _sa + (h / 2.0) * _ca)


def _obst_valid(world, cx, cy, w=OBS_W_GAME, h=OBS_H_GAME, ang=0.0):
    """障碍合法性（纯函数，台架可测）：**只要求中心在场区内**（贴边摆放合理——
    球本来就不会飞出红色场区，盒子伸出去的部分不影响物理）；
    避开已有洞口（近似 40px）与已有障碍（6px）。

    ★★ 2026-10-29 用户实况："边角下很难放置，条很大也放不下"——原来要求
    整根条旋转后的 AABB 完整在场区内还留 20px，贴边/角落必被拒。现改中心判据。
    """
    _ex, _ey = _obst_half_extent(w, h, ang)
    # ★ 2026-10-29 二修（实况：条中心 (992,-53) 被判"贴边"）：允许中心在
    #   场区外 **60px**（与摆放识别容差一致）——贴边/角落摆条时中心常压在线外。
    if not (-60.0 <= float(cx) <= 1020.0):
        return False
    if not (-60.0 <= float(cy) <= 600.0):
        return False
    if world is not None:
        for _h2 in (getattr(world, 'holes', None) or []):
            _e2, _c2 = _h2.get('edge'), float(_h2['c'])
            _hx = 0.0 if _e2 == 'left' else (960.0 if _e2 == 'right' else _c2)
            _hy = 0.0 if _e2 == 'top' else (540.0 if _e2 == 'bottom' else _c2)
            if (abs(float(cx) - _hx) < _ex + 40.0
                    and abs(float(cy) - _hy) < _ey + 40.0):
                return False
        for _o2 in (getattr(world, 'obstacles', None) or []):
            _ox, _oy = _obst_half_extent(_o2.hw * 2.0, _o2.hh * 2.0,
                                         math.degrees(_o2.ang))
            if (abs(float(cx) - _o2.cx) < _ex + _ox + 6.0
                    and abs(float(cy) - _o2.cy) < _ey + _oy + 6.0):
                return False
        # ★ 2026-10-29 用户实况（"障碍物上面有传送门"）：条也不能压在门上。
        for _p2 in (getattr(world, 'portals', None) or []):
            if math.hypot(float(cx) - float(_p2.x),
                          float(cy) - float(_p2.y)) < _ex + 26.0:
                return False
    return True


def _obst_tuples(obstacles):
    """engine.Obstacle 列表 -> realtime_ball 口径 (cx,cy,w,h,ang_deg)。纯函数。"""
    return [(o.cx, o.cy, o.hw * 2.0, o.hh * 2.0, math.degrees(o.ang))
            for o in (obstacles or [])]


def _obsp_commit(m, world, obstacles):
    """把当前预览落实成一个障碍：校验 -> 扣额度 -> 加进列表（并回注世界）。
    返回 (ok, msg)。纯逻辑（engine.Obstacle 的构造除外），台架可测。"""
    if not _OBSP['on']:
        return False, '不在布置态'
    _who = int(_OBSP['who'])
    if m is None or m.items[_who]['obstacle'] <= 0:
        return False, '没有障碍额度'
    if not _obst_valid(world, _OBSP['cx'], _OBSP['cy'], ang=_OBSP['ang']):
        return False, '这里放不下（贴边/盖洞/叠障碍）'
    import engine as _eng
    _o = _eng.Obstacle(float(_OBSP['cx']), float(_OBSP['cy']),
                       OBS_W_GAME, OBS_H_GAME, float(_OBSP['ang']))
    obstacles.append(_o)
    m.items[_who]['obstacle'] -= 1
    if world is not None:
        world.obstacles = obstacles
    return True, 'P%d 布置障碍 (%.0f,%.0f %.0f度)' % (
        _who, _o.cx, _o.cy, math.degrees(_o.ang))


def _obsp_mouse_click(hub, world):
    """布置态的鼠标左键（上升沿处调用）：未定住->定住+弹窗；弹窗中->点确定/取消。
    返回 True=这一笔已被布置流程消费（商店命中框不许再吃）。"""
    if not _OBSP['on'] or _MF['ob'] is None or _MF['ob'].phase != 'PREP':
        return False
    _mx, _my = int(getattr(hub, 'mx', 0) or 0), int(getattr(hub, 'my', 0) or 0)
    if _OBSP['confirm']:
        _hit = _shop_click(_mx, _my, _OBSP.get('rects') or {})
        if _hit == 'ok':
            _ok, _msg = _obsp_commit(_MF['ob'], world, _OBST)
            print('[布置] %s' % _msg, flush=True)
            _item_msg_set(_msg if _ok else ('放不下：' + _msg))
            if _ok:
                _shot_write(dict(t=round(time.time(), 3), ok=True, why='item',
                                 kind='item', seq=0, msg=_msg),
                            player=_OBSP['who'])
                _obsp_clear()
        elif _hit == 'no':
            _OBSP['confirm'] = False
            _item_msg_set('继续移动位置（点「确定」或按 E 放置）')
        return True                     # 弹窗开着时点哪里都算消费（不误触商店）
    _OBSP['confirm'] = True
    _item_msg_set('确认放置这个障碍？（点「确定」或按 E）')
    return True
# ★★ 2026-10-16（用户："有时候按 C 没反应，也不懂是没次数还是卡了"）：道具/暂停类
#   操作的**屏上应答横幅**——被拒原因（没额度/对方占用/不在对战中）、落洞成功、
#   键盘/鼠标控制权切换……全都弹在大屏上方一行中文里（2.6 秒）。此前这些消息
#   只进控制台和手机"最近一杆"，蹲在板子前盯大屏的人无从判断。
# ★★ 2026-10-16 粒子引擎（档3，--pe）：**PL 里的 16 粒并行物理**的风暴模式状态。
#   影子球队列 `_PeBall` 与 LiveBall 的**渲染子集**鸭子兼容（x/y=Q10.8 定点、
#   trail=浮点游戏坐标、charge/alive）——只在**渲染**处与 live_balls 合并；
#   **绝不进 CPU 物理循环**（_rtb.advance 那条线永远只见 CPU 球）。
_PEB = {'on': False, 'mm': None, 'mod': None, 'balls': [], 'next': 0,
        'scene_key': None, 'fails': 0, 'n_goal': 0, 'n_annih': 0,
        'flashes': [], 'ms': [], 't': None}


class _PeBall:
    __slots__ = ('pidx', 'x', 'y', 'charge', 'who', 'alive', 'trail', 'die_t')

    def __init__(self, pidx, x, y, charge, who=0):
        self.pidx = pidx
        self.x = x                     # Q10.8 定点（与 LiveBall 同口径）
        self.y = y
        self.charge = charge
        self.who = who
        self.alive = True
        self.trail = [(x / 256.0, y / 256.0)]
        self.die_t = None


def _pe_scene_key(world):
    # ★ 2026-10-19：实物障碍/传送门进了 PL 场景 —— 它们变了也要重写场景
    _obs = getattr(world, 'obstacles', None) or []
    # ★ 2026-10-20：与预览锚点同一口径——粗量化，防实物抖动引发场景重写
    _osig = tuple((int(round(o.cx / 20.0)) * 20, int(round(o.cy / 20.0)) * 20,
                   int(round(o.hw / 10.0)) * 10, int(round(o.hh / 5.0)) * 5,
                   int(round(math.degrees(o.ang) / 10.0)) * 10)
                  for o in _obs[:3])
    _psig = tuple((int(round(p.x / 20.0)) * 20, int(round(p.y / 20.0)) * 20)
                  for p in (getattr(world, 'portals', None) or [])[:4])
    return (tuple((round(b.x0, 1), round(b.y0, 1), round(b.x1, 1),
                   round(b.y1, 1), round(float(b.b), 3)) for b in world.blocks),
            tuple((h['edge'], round(float(h['c']), 1)) for h in world.holes),
            _osig, _psig)


def _pe_spawn_storm(st, gx, gy, n=4, chg=1):
    """从 (gx,gy) 放一簇粒子：360 度均布、**整簇同号**、速度按序号散布。

    ★ 2026-10-17 板上实况修：原来红蓝交替 + 同点出生 -> 异号粒子瞬间两两湮灭
    （HUD 实测 湮灭42 / 只剩 1 颗，观感=炸了个寂寞）。改成同号爆散：同号只能
    弹性互撞 -> 被撞散成一团可见的粒子云；红蓝湮灭留给"双方风暴相遇"的时刻。"""
    import v3_fixedpoint as _vf
    mm = st['mm']
    for k in range(n):
        idx = st['next']
        # ★ 2026-11-01：% 16（引擎 NPART=16，真正的 16 粒并行物理）
        #   出生会被引擎越界防护拒掉，但游戏侧仍记影子球 => 幽灵粒子。瘦身提交
        #   消息声称"驱动 next%32 同步"，实际没有软件文件入库，这里补上。）
        st['next'] = (idx + 1) % 16
        ang = -180.0 + 360.0 * k / n
        # ★ 2026-10-19 观感：260~470 -> 140~260 px/s，风暴在场上多活 1~2 秒
        spd = 140.0 + 120.0 * ((k * 7) % 5) / 4.0
        try:
            st['mod'].spawn(mm, _vf, idx, gx, gy, ang, spd, chg, 0)
        except Exception:
            return
        sh = _PeBall(idx, int(round(gx * 256)), int(round(gy * 256)), chg)
        for j, ex in enumerate(st['balls']):        # 同号替换
            if ex.pidx == idx:
                st['balls'][j] = sh
                break
        else:
            st['balls'].append(sh)


def _pe_pump(st, world, nstep):
    """粒子引擎一帧节拍：场景变则重写 -> 推 nstep 子步 -> 轮询影子球/事件。

    任何异常 -> **停引擎**（绝不让它带走主循环；st['fails'] 记账）。
    """
    try:
        import time as _t
        import v3_fixedpoint as _vf
        mm = st['mm']
        _k = _pe_scene_key(world)
        if _k != st['scene_key']:
            st['scene_key'] = _k
            st['mod'].set_scene(mm, _vf,
                                [(b.x0, b.y0, b.x1, b.y1, b.b)
                                 for b in world.blocks],
                                list(world.holes),
                                obstacles=getattr(world, 'obstacles', None),
                                portals=getattr(world, 'portals', None))
            # ★★ 2026-10-19 实况修：实物障碍每 1.5s 抖动一次 → 场景 key 变 →
            #   原来无条件清空影子，屏上正在飞的风暴**秒没**（看着像"按 BN
            #   没反应"）。障碍/传送门抖动只重写场景，影子继续映射引擎粒子。
            # ★★ 2026-11-01 用户规则订正：**未出射粒子保留到下一局**。
            #   每局开局会随机换磁场块/洞口 → 场景 key 变；旧代码在这里
            #   `st['balls']=[]` 把上一局还没出射的粒子全吞了（用户实况：
            #   "上局 4 颗未出射，下局直接清空"）。PL 引擎的 set_scene 只重写
            #   场景、不动粒子状态，影子必须保留，让它们在新场里继续飞。
            _kb = _k[:2]
            if _kb != st.get('scene_blk'):
                st['scene_blk'] = _kb
                print('[粒子] 换场：保留 %d 颗（含未出射）' % len(st['balls']),
                      flush=True)
        if nstep > 0 and st['balls']:
            _t0 = _t.perf_counter()
            st['mod'].run(mm, min(int(nstep), 240))
            st['ms'].append((_t.perf_counter() - _t0) * 1000.0)
            if len(st['ms']) > 200:
                st['ms'].pop(0)
            _now = _t.time()
            for sh in st['balls']:
                p = st['mod'].get_particle(mm, sh.pidx)
                _ox, _oy = sh.x / 256.0, sh.y / 256.0
                sh.x, sh.y = p['x'], p['y']
                sh.alive = p['alive']
                if sh.alive:
                    nx, ny = p['x'] / 256.0, p['y'] / 256.0
                    if (nx - _ox) ** 2 + (ny - _oy) ** 2 >= 64.0:
                        sh.trail.append((nx, ny))
                        if len(sh.trail) > 120:
                            del sh.trail[0]
                elif sh.die_t is None:
                    sh.die_t = _now
            # ★★ 2026-10-19 实况（发射后像 PPT）：风暴死后影子一直不剪 →
            #   PE 泵每帧仍 run + 逐粒读回（16~32 次 AXI），主循环白背。
            #   死亡超过 1s 的影子直接移出列表；空列表时下面连 run 都不发。
            st['balls'] = [sh for sh in st['balls']
                           if sh.alive
                           or (_now - (sh.die_t if sh.die_t is not None
                                       else _now)) < 1.0]
            st['balls'] = [sh for sh in st['balls']
                           if sh.alive or (_now - (sh.die_t or _now)) < 1.5]
        _nev = 0
        while _nev < 16:
            ev = st['mod'].pop_event(mm)
            if ev is None:
                break
            _nev += 1
            if ev['kind'] == 1:
                st['n_goal'] += 1
            elif ev['kind'] == 2:
                st['n_annih'] += 1
                for sh in st['balls']:
                    if sh.pidx == ev['pidx'] and not sh.alive:
                        st['flashes'].append((_t.time(),
                                              sh.x / 256.0, sh.y / 256.0))
                        break
        _tn = _t.time()
        st['flashes'][:] = [f for f in st['flashes']
                            if _tn - f[0] < 1.2][-16:]
    except Exception as _e:
        st['fails'] += 1
        if st['fails'] <= 2:
            print('[粒子] ★ 引擎一帧失败（停引擎，游戏照跑）：%s'
                  % str(_e)[:160], flush=True)
        st['on'] = False


_ITEM_MSG = {'txt': '', 't': -1e9}
_ITEM_MSG_SECS = 2.6


def _item_msg_set(txt):
    """设置了横幅文案（空串 = 不动）。任何异常静默——反馈坏了不许带走主循环。"""
    try:
        if txt:
            _ITEM_MSG['txt'] = str(txt)
            _ITEM_MSG['t'] = time.time()
    except Exception:
        pass


def _item_banner(img):
    """屏上应答横幅（三块屏通用：直播/瞄准图/商店·局前）。任何异常静默。"""
    try:
        if (not _ITEM_MSG['txt']
                or time.time() - _ITEM_MSG['t'] > _ITEM_MSG_SECS):
            return
        _w2 = img.shape[1]
        _sp = _zh_sprite(_ITEM_MSG['txt'], 30, (80, 235, 255))
        if _sp is None:
            return
        _sw = _sp.shape[1]
        # ★ 2026-10-30 用户实况（"提示相互重叠"）：横幅原来在 y=34，正好压在
        #   布置/加洞口标题（y=16, 32px）上；下移到 y=60（标题下方）。
        _zh_hud(img, [(_ITEM_MSG['txt'], (max(8, (_w2 - _sw) // 2), 60),
                       30, (80, 235, 255))])
    except Exception:
        return
_LINK_WARN_N = 3                         # 5 分钟窗口内 >= N 次脉冲 -> 警告重下 bit
                                         # （今天板上标定：健康 ~0-1 次/18min；
                                         #   退化态 ~1/15-50s——阈值取 3 很安全）
_LINK_WARN_WIN = 300.0


def _link_track(st, n_now, now, window=_LINK_WARN_WIN):
    """卡顿计数器 -> 滚动窗口内的脉冲次数（就地更新 st，返回窗口内次数）。

    ★ 用**计数器差分**而不是在脉冲点上挂钩子：主循环脉冲（`_RESCUE['n_pulse']`）
      与看门狗线程脉冲（`WD_STAT['n_recover']`）是两个来源，差分法一处覆盖两处，
      也不用动任何救援代码。纯函数（给 st/计数/时间就行）——台架可测。
    """
    if n_now > st['n_last']:
        st['times'].extend([now] * (n_now - st['n_last']))   # 限频下多为 1 拍
    # n_now < n_last（计数器回绕/换进程）：只对基准，不补记
    st['n_last'] = n_now
    while st['times'] and now - st['times'][0] > window:
        st['times'].pop(0)
    return len(st['times'])


def _match_snapshot(m, link_n=0, mf_mod=None, shop_open=False,
                    world=None, hole=None, balls=None, place=None):
    """对局状态 -> 手机端 dict（**纯函数**，台架可测）。m=None（非对局）-> None。

    就绪字段只在局前阶段有意义（其余阶段 None）；link_warn 由滚动窗口脉冲数判。
    ★ `mf_mod` = match_flow 模块（常量来源）——由调用方传入：ball_game 里它只
      在"输入层罩子"内 import（台架 AST 钉死这条），所以这里**不许再 import**。
    ★★ 2026-10-15 加洞口 v2（手机俯视图）：可选带上
      world -> scene（场区/磁场块/洞口 + _hole_valid 的口径参数——前端本地
               即时判色用它，**数值只准来自这里，JS 里不许写死**）；
      hole  -> 加洞口快照（_hole_snap；不在 HOLE 时也是 {'active': False}）；
      balls -> [[x, y, charge], ...]（游戏坐标；红+1/蓝-1/未知0）。
      老调用方（改口 HUD 行/台架）不传这三样 -> dict 与从前**一字不差**。
    """
    if m is None:
        return None
    def _side(p):
        return {'score': m.goals[p] * mf_mod.GOAL_POINTS,
                'red': m.ammo[p][1], 'blue': m.ammo[p][-1],
                'points': m.points[p],               # ★ 选购用：积分余额
                'ready': (m.sub[p] == 'READY') if m.phase == 'PREP' else None,
                'shop': (m.shop_label(p)             # ★ 选购用：当前选中项
                         if (m.phase == 'PREP' and m.sub[p] == 'SHOP')
                         else None)}
    _out = {'on': True, 'round': m.round, 'max': mf_mod.MAX_ROUNDS,
            'phase': m.phase, 'p1': _side(1), 'p2': _side(2),
            'timer': (max(0.0, mf_mod.TIMER_PERIOD - m.timer)
                      if m.phase == 'PLAY' else None),
            'grace_left': (max(0.0, mf_mod.GRACE_SECS - m.grace)
                           if m.grace is not None else None),
            'msg': (m.msg if m.msg_t > 0 else ''),
            # ★★ 2026-10-08 商店 v2：商店画面开着吗（手机页据此把"进入商店"换成"购买"）
            'shop_open': bool(shop_open),
            'link_n': int(link_n), 'link_warn': bool(link_n >= _LINK_WARN_N)}
    if place is not None:
        _out['place'] = place          # ★ 2026-10-31：实时摆放态（手机显示确定/取消条）
    if world is not None:
        _out['scene'] = {
            # 场区尺寸 = 项目统一口径（v3_scan.FIELD_W/H，两处都是 960x540）
            'W': 960.0, 'H': 540.0,
            'blocks': [[float(b.x0), float(b.y0), float(b.x1), float(b.y1),
                        round(float(b.b), 3)] for b in world.blocks],
            'holes': [[str(h['edge']), float(h['c']), float(h['w'])]
                      for h in world.holes],
            'params': {'margin': 50.0, 'mw': HOLE_W_GAME, 'gap': 12.0}}
    if hole is not None:
        _out['hole'] = hole
    if balls is not None:
        _out['balls'] = balls
    return _out


def _match_write(d):
    """原子写对局状态（手机 /match 读）。同 _balls_write 纪律：失败不抛，
    游戏循环绝不许因为它停。"""
    if d is None:
        return
    try:
        d = dict(d)
        d['t'] = round(time.time(), 3)
        _dd = os.path.dirname(_MATCH_PATH)
        if _dd and not os.path.isdir(_dd):
            os.makedirs(_dd, exist_ok=True)
        tmp = _MATCH_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _MATCH_PATH)
    except Exception:
        pass


# ============================================================================
#  ★★ 2026-10-07：图上的**中文 HUD**（贴片层）——用户三点：别压场地 / 中文 / 简短
# ============================================================================

_HUD_FIRE_HINT = '发射带：球心放到紫框外'   # 底部静态提示（原来的英文长句）
# ★ 2026-10-16（用户："加洞口我怎么切换选手一还是选手二"）：常驻一行说明**入口与
#   归属**——C 永远是选手一、小键盘+ 或 / 永远是选手二（各自额度独立，不用"切换"）。
_HUD_HOLE_HINT = '加洞：P1=C ｜ P2=小键盘+ 或 /'

_ZH_SPR = {}          # 贴片缓存：(text, size, color) -> BGR ndarray / False(渲染失败)


def _hud_top_line(angle, speed, bno1, angle2, speed2, bno2):
    """屏顶一行（中文短句；单/双人两种口径）。纯函数——台架可测。"""
    if angle2 is None:
        return '角度 %.0f°  速度 %.0f px/s' % (angle, speed)
    return ('P1 %.0f° %.0f px/s 控球%d ｜ P2 %.0f° %.0f px/s 控球%d'
            % (angle, speed, bno1 or 1, angle2, speed2, bno2 or 2))


def _shop_ask_use(m, p, buy_cnt=0, esc_cnt=0):
    """★ 2026-10-28 用户定案（第 3 条）：买完实体件 -> **问一句要不要现在用**。

    不再自动进摆放：商店画面上弹一行提示，E/小键盘*（再按一次购买键）= 立即
    摆放；ESC = 先留着（对局中按 V/G 再摆，本局临时有效）。返回 kind 或 None。"""
    _kind = getattr(m, 'last_buy', None)
    if _kind not in ('obstacle', 'portal'):
        return None
    m.last_buy = None
    _PBUY.update({'on': True, 'who': int(p), 'kind': _kind,
                  'b': int(buy_cnt), 'esc': int(esc_cnt)})
    _cn = '障碍' if _kind == 'obstacle' else '传送门'
    _key = 'E' if int(p) == 1 else '小键盘*'
    _msg = ('P%d 买了%s——现在使用？按 %s 立即摆放 / ESC 先留着'
            '（对局中按 %s 再摆）'
            % (int(p), _cn, _key, 'V' if _kind == 'obstacle' else 'G'))
    print('[布置] ' + _msg, flush=True)
    _item_msg_set(_msg)
    return _kind


def _pbu_resolve(m, use_now):
    """购买询问的答复：use_now=True -> 进摆放；False -> 保留道具。"""
    if not _PBUY['on']:
        return False, '没有待答复的购买'
    _who, _kind = int(_PBUY['who']), _PBUY['kind']
    if not use_now:
        _PBUY['on'] = False
        _cn = '障碍' if _kind == 'obstacle' else '传送门'
        _msg = ('P%d 的%s先留着——对局中按 %s 摆放（本局临时有效）'
                % (_who, _cn, 'V' if _kind == 'obstacle' else 'G'))
        print('[布置] ' + _msg, flush=True)
        _item_msg_set(_msg)
        return True, _msg
    _ok, _msg = _pplace_enter(m, _who, _kind)
    print('[布置] 购买后立即摆放（%s）：%s' % ('OK' if _ok else '未进入', _msg),
          flush=True)
    if _ok:
        _PBUY['on'] = False
    else:
        # ★ 相机还没稳定/已有布置在进行：**保留询问框**，别让玩家以为"点了没反应"
        _msg += '（再点一次或按 V/G）'
    _item_msg_set(_msg)
    return _ok, _msg


def _shop_apply(m, p, act):
    """★ 2026-10-08 局前选购一笔事件作用到 match_flow（纯函数，台架可测）。
    act ∈ {'mup','mdn','buy'}；不在选购态一律 None（静默忽略——局中按 W 是调角度）。"""
    if m.phase != 'PREP' or m.sub[p] != 'SHOP':
        return None
    if act in ('mup', 'mdn'):
        return '选：%s' % m.shop_move(p, -1 if act == 'mup' else 1)
    if act == 'buy':
        _ok, _msg = m.buy(p)
        return _msg
    return None


# ★★ 2026-10-25 加球选择页（用户定案）：按 F/小键盘8 -> 串口屏跳 `addball`
#   页 -> 触摸「加红球/加蓝球」-> **选完才扣 1 个加球道具**并加弹药；
#   取消/超时/离开对局一律不扣，并切回状态页 `index`。
ABALL_PAGE = 'addball'
ABALL_TIMEOUT = 20.0
_ABALL = {'on': False, 'who': 0, 't': 0.0, 't_ready': 0.0,
          'last_b': -1, 'last_r': -1, 'last_n': -1, 'who_sent': 0}


def _aball_texts(b, r, n):
    """addball 页三个实时数字：t13 当前蓝球 / t14 当前红球 / t12 剩余加球。

    ★ 2026-10-25 用户定案：**剩余 0 时 t12 直接显示「没道具了，点取消」**
      （不再显示「剩余加球 0 个」）；打开页面时 0 个也立即给这个提示。
    返回 (状态键, 控件名, 文本, 数值) —— 数值直接用于"变化才发"的比较。"""
    _b, _r, _n = int(b), int(r), int(n)
    _t12 = ('没道具了，点取消' if _n <= 0
            else '剩余加球 %d 个' % _n)
    return (('last_b', 't13', '当前有蓝球 %d 个' % _b, _b),
            ('last_r', 't14', '当前有红球 %d 个' % _r, _r),
            ('last_n', 't12', _t12, _n))


def _aball_set_count(m, screen, force=False):
    """把 t13/t14/t12 写到 addball 页；默认只发**变化**的（实时刷新）。

    返回真正发出的条数。force=True 时全写（切页前用）。"""
    if m is None or screen is None:
        return 0
    if not _ABALL['on'] and not force:
        return 0
    # 切页后等页面加载完再写（否则会被页面初始值覆盖）
    if not force and time.time() < float(_ABALL.get('t_ready') or 0.0):
        return 0
    _who = int(_ABALL['who'])
    try:
        _b = int(m.ammo[_who][-1])
        _r = int(m.ammo[_who][1])
        _n = int(m.items[_who]['ball'])
    except Exception:
        return 0
    _sent = 0
    for _key, _ctrl, _txt, _val in _aball_texts(_b, _r, _n):
        if not force and _ABALL.get(_key) == _val:
            continue
        if screen.set_text(ABALL_PAGE, _ctrl, _txt, max_bytes=32):
            _ABALL[_key] = _val
            _sent += 1
    return _sent


def _aball_set_who(screen):
    """切页后补写 t8（选手一使用/选手二使用）——只写一次。"""
    if not _ABALL['on'] or screen is None:
        return False
    if time.time() < float(_ABALL.get('t_ready') or 0.0):
        return False
    _who = int(_ABALL['who'])
    if _ABALL.get('who_sent') == _who:
        return False
    if screen.set_text(ABALL_PAGE, 't8',
                       '选手一使用' if _who == 1 else '选手二使用',
                       max_bytes=16):
        _ABALL['who_sent'] = _who
        return True
    return False


def _aball_begin(m, p, screen):
    """按 F/小键盘8：打开串口屏加球选择页。返回 (ok, msg)。"""
    if m is None or m.phase != 'PLAY':
        return False, '加球只能在**对局中**用（局前商店买「加球」）'
    if m.grace is not None:
        return False, '本局弹药已打光（落定中）——加球留到下一局'
    _left = int(m.items[p]['ball'])
    if _ABALL['on']:
        return False, '已有加球选择在进行（先选红/蓝或取消）'
    if screen is None or not screen.available():
        return False, '串口屏未连接——加球要在屏上选颜色（等 1 秒再按）'
    # ★★ 2026-10-25 板上实锤：TJC **切页会重载页面**，切页前写的 t8/t12/t13/t14
    #   会被初始值（N 个）覆盖。所以顺序必须是：先 `page addball`，等页面加载
    #   （t_ready=0.5s）后由主循环把四个动态文本补写上去。
    if not screen.set_page(ABALL_PAGE):
        return False, '串口屏忙——再按一次'
    _now = time.time()
    _ABALL.update({'on': True, 'who': int(p), 't': _now,
                   't_ready': _now + 0.5,
                   'last_b': -1, 'last_r': -1, 'last_n': -1,
                   'who_sent': 0})
    if _left <= 0:
        # 0 个也开页（给玩家一个明确回应）：t12 会显示「没道具了，点取消」
        return True, ('P%d 没有加球道具了（页面已打开，t12 已提示；'
                      '点「取消」返回）' % p)
    return True, ('P%d 加球：请在串口屏选「加红球」或「加蓝球」'
                  '（取消不扣）' % p)


def _aball_finish(m, color, screen):
    """选择页选完红/蓝：扣 1 个加球道具，立即 +1 红/蓝弹药。返回 (ok,msg)。

    ★ 2026-10-25 用户定案：**选完不自动切回**——页面保持，可连续点红/蓝
      （每次扣 1 个，数字实时刷新）；玩家自己点「取消」/ESC 才回 index。
      只有对局结束/落定期才自动关页（不扣）。"""
    if not _ABALL['on']:
        return False, ''
    _who = int(_ABALL['who'])
    if m is None or m.phase != 'PLAY' or m.grace is not None:
        return False, _aball_cancel(
            screen, '对局已结束——加球页关闭（未扣）')
    if int(m.items[_who]['ball']) <= 0:
        # 没道具了也留在页面上；按用户要求**直接在 t12 上提示**
        # （反正剩余就是 0，不再显示「剩余加球 0 个」）
        if screen is not None:
            try:
                screen.set_text(ABALL_PAGE, 't12',
                                '没道具了，点取消', max_bytes=64)
            except Exception:
                pass
        return False, 'P%d 没有加球道具了（点「取消」返回）' % _who
    m.items[_who]['ball'] -= 1
    if color == 'red':
        m.ammo[_who][1] += 1
        _cn = '红球'
    else:
        m.ammo[_who][-1] += 1
        _cn = '蓝球'
    _msg = ('P%d 加球：%s +1（现有 红%d/蓝%d；剩余加球 %d）'
            % (_who, _cn, m.ammo[_who][1], m.ammo[_who][-1],
               int(m.items[_who]['ball'])))
    # ★ 实时：把扣完/加完后的三个数字写到页面上（页面保持不切走）
    try:
        _aball_set_count(m, screen)
    except Exception:
        pass
    _ABALL['t'] = time.time()      # 每用一次重置超时（连续加球不会被踢出）
    return True, _msg


def _aball_cancel(screen, why=''):
    """取消加球选择：不扣道具，切回状态页。返回给玩家看的文案。"""
    _who = int(_ABALL.get('who') or 0)
    _ABALL.update({'on': False, 'who': 0, 't': 0.0, 't_ready': 0.0,
                   'last_b': -1, 'last_r': -1, 'last_n': -1,
                   'who_sent': 0})
    if screen is not None:
        try:
            screen.set_page('index')
        except Exception:
            pass
    return why or ('P%d 加球取消（未扣）' % _who)


def _shop_click(x, y, rects):
    """★ 商店 v2：鼠标点 (x,y) 落在哪个命中框（rects={名:(x0,y0,x1,y1)}）。
    纯函数——台架可测。没中返回 None。"""
    try:
        x, y = int(x), int(y)
    except Exception:
        return None
    for k, (x0, y0, x1, y1) in (rects or {}).items():
        if x0 <= x <= x1 and y0 <= y <= y1:
            return k
    return None


def _shop_layout(w, h):
    """★ 商店 v2：商店画面的命中框表（与 _build_shop 画法同源；纯函数——
    台架断言"框都在画布内、买家与完成不重叠"）。坐标 = 画布像素（1280x720）。"""
    r = {}
    for p, (x0, x1) in ((1, (24, 620)), (2, (660, 1256))):
        for i in range(5):
            y0 = 140 + i * 74
            r['p%d_it%d' % (p, i)] = (x0, y0, x1, y0 + 64)
        yb = 140 + 5 * 74 + 10
        xm = x0 + int((x1 - x0) * 0.52)
        r['p%d_buy' % p] = (x0, yb, xm - 6, yb + 56)
        r['p%d_done' % p] = (xm + 6, yb, x1, yb + 56)
    return r


def _match_line_cn(sn):
    """对局状态 dict（_match_snapshot）-> 屏上**中文短句**。纯函数——台架可测。

    （match_flow.hud_line() 那份 ASCII 现在只给日志/排查用；屏上走这一份。）
    """
    if not sn:
        return None
    rn = ('第 %d/%d 局' % (sn['round'], sn['max'])) if sn['round'] <= sn['max'] \
        else '加赛局'
    ph = sn['phase']
    if ph == 'PLAY':
        if sn.get('grace_left') is not None:
            return '弹药打光——等球落定 %.0f 秒' % sn['grace_left']
        return ('%s　比分 %d : %d　弹药 %d红%d蓝·%d红%d蓝　剩 %.0f 秒'
                % (rn, sn['p1']['score'], sn['p2']['score'],
                   sn['p1']['red'], sn['p1']['blue'],
                   sn['p2']['red'], sn['p2']['blue'], sn.get('timer') or 0.0))
    if ph == 'PREP':
        def _sd_txt(_sd, _tag, _buykey):
            if _sd.get('shop') is not None:
                return '%s[%s]%s买' % (_tag, _sd['shop'], _buykey)
            return '%s %s' % (_tag, '已就绪' if _sd['ready'] else '等待')
        return ('%s　局前选购　%s　%s　积分 %d/%d'
                % (rn, _sd_txt(sn['p1'], 'P1', 'E'),
                   _sd_txt(sn['p2'], 'P2', '*'),
                   int(sn['p1'].get('points', 0)),
                   int(sn['p2'].get('points', 0))))
    if ph == 'ROUND_END':
        return '本局结束（弹药打光）'
    if ph in ('EDIT', 'HOLE', 'PLACE'):
        return '%s　暂停中' % rn
    if ph == 'MATCH_END':
        w = sn['p1']['score'] - sn['p2']['score']
        return ('比赛结束：%s胜'
                % ('P1' if w > 0 else ('P2' if w < 0 else '平局')))
    return rn


def _zh_sprite(s, size=22, col=(0, 255, 255)):
    """把一行文本渲成小 BGR 贴片（深底、按字符串缓存；没 PIL/字体返回 None）。

    渲染走 v3_scan._zh_font（PIL + zh_font_subset.ttf，路径/缓存它都管）；
    失败返回 None —— 调用方退 cv2 ASCII（同 put_texts 的纪律）。
    """
    key = (s, size, col)
    if key in _ZH_SPR:
        sp = _ZH_SPR[key]
        return sp if sp is not False else None
    sp = False
    try:
        from PIL import Image, ImageDraw
        import v3_scan as _vs
        f = _vs._zh_font(size)
        if f is not None:
            _t = Image.new('RGB', (8, 8))
            _d0 = ImageDraw.Draw(_t)
            try:
                bb = _d0.textbbox((0, 0), s, font=f)       # PIL>=8
            except Exception:
                try:
                    _w0, _h0 = _d0.textsize(s, font=f)     # 老 PIL
                    bb = (0, 0, _w0, _h0)
                except Exception:
                    bb = (0, 0, size * max(1, len(s)), size + 6)
            w = max(8, bb[2] - bb[0] + 12)
            h = max(8, bb[3] - bb[1] + 8)
            im = Image.new('RGB', (w, h), (18, 18, 22))
            dd = ImageDraw.Draw(im)
            dd.text((6 - bb[0], 4 - bb[1]), s, font=f,
                    fill=(col[2], col[1], col[0]))
            sp = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    except Exception:
        sp = False
    _ZH_SPR[key] = sp
    return sp if sp is not False else None


def _zh_hud(img, items):
    """中文 HUD 上屏：items = [(str, (x, y), size, (b, g, r)), ...]。

    贴片缓存 + 每帧 numpy 粘贴；没字体退 cv2 ASCII（'?' 占位，只提醒一次）。
    """
    for s, (x, y), size, col in items:
        sp = _zh_sprite(s, size, col)
        if sp is None:
            cv2.putText(img, s.encode('ascii', 'replace').decode(), (x, y + size),
                        cv2.FONT_HERSHEY_SIMPLEX, size / 30.0, col, 1)
            continue
        h, w = sp.shape[:2]
        _H2, _W2 = img.shape[:2]
        # ★ 2026-10-23 实况：只夹右/下不夹左/上 —— 标签 x 为负时切片
        #   `img[..., x:x1]` 会变成 0 宽，源 26x84 → 广播异常 → 连续 8 次
        #   推帧失败 → HDMI 保护性自关。四边全夹，源按偏移裁。
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(_W2, x + w), min(_H2, y + h)
        if x1 <= x0 or y1 <= y0:
            continue
        img[y0:y1, x0:x1] = sp[y0 - y:y1 - y, x0 - x:x1 - x]


# ---- ★★ 2026-10-08 商店 v2：全屏商店画面 + 局前入口浮层 + 光标 -----------------
def _draw_cursor(img, x, y, col=(255, 255, 255)):
    """白色箭头光标（经典的 7 点小箭头；描黑边防浅色背景上看不见）。"""
    try:
        x, y = int(x), int(y)
    except Exception:
        return
    arr = np.array([[x, y], [x, y + 19], [x + 5, y + 14], [x + 9, y + 21],
                    [x + 12, y + 19], [x + 8, y + 12], [x + 15, y + 12]],
                   np.int32)
    cv2.fillPoly(img, [arr], col)
    cv2.polylines(img, [arr], True, (24, 24, 24), 1)


# ★★ 2026-10-13 提速：商店基图缓存——选单/积分/就绪没变就不重画（每帧只
#   拷贝基图 ~2ms + 画光标），光标帧率显著提升。
_SHOPCACHE = {'key': None, 'base': None}


def _purchase_prompt_rects(w, h):
    """购买询问框的命中框（与 _purchase_prompt 的画法成对）。"""
    _bw, _bh = min(w - 40, 1120), 70
    _x0, _y0 = (w - _bw) // 2, h - _bh - 18
    _bw1 = 300
    return {'use': (_x0 + _bw - 2 * _bw1 - 16, _y0 + 10,
                    _x0 + _bw - _bw1 - 8, _y0 + _bh - 10),
            'later': (_x0 + _bw - _bw1 + 2, _y0 + 10,
                      _x0 + _bw - 6, _y0 + _bh - 10),
            'box': (_x0, _y0, _x0 + _bw, _y0 + _bh)}


def _purchase_prompt(img, w, h):
    """★ 2026-10-28（第 3 条）：买完实体件的「现在使用？」横幅（商店共用）。

    两个可点按钮：立即摆放 / 先留着；键盘同义（E/小键盘* = 立即，ESC = 留着）。"""
    if not _PBUY['on']:
        return img
    _who = int(_PBUY['who'])
    _cn = '障碍' if _PBUY['kind'] == 'obstacle' else '传送门'
    _key = 'E' if _who == 1 else '小键盘*'
    _r = _purchase_prompt_rects(w, h)
    _x0, _y0, _x1, _y1 = _r['box']
    cv2.rectangle(img, (_x0, _y0), (_x1, _y1), (18, 18, 18), -1)
    cv2.rectangle(img, (_x0, _y0), (_x1, _y1), (0, 235, 255), 2)
    _zh_hud(img, [('P%d 买了%s——现在使用？' % (_who, _cn),
                   (_x0 + 16, _y0 + 16), 26, (0, 235, 255)),
                  ('（对局中也能按 %s 临时摆放）'
                   % ('V' if _PBUY['kind'] == 'obstacle' else 'G'),
                   (_x0 + 16, _y0 + 46), 18, (176, 176, 176))])
    for _nm, _txt, _col in (('use', '立即摆放 %s' % _key, (60, 210, 90)),
                            ('later', '先留着 ESC', (200, 200, 200))):
        _bx0, _by0, _bx1, _by1 = _r[_nm]
        cv2.rectangle(img, (_bx0, _by0), (_bx1, _by1), _col, 2)
        _zh_hud(img, [(_txt, (_bx0 + 14, _by0 + 8), 24, _col)])
    return img


def _pbuy_mouse_click(mx, my, w, h, m):
    """购买询问框的鼠标答复。返回 True=已消费。"""
    if not _PBUY['on']:
        return False
    _r = _purchase_prompt_rects(w, h)
    for _nm, _use in (('use', True), ('later', False)):
        _x0, _y0, _x1, _y1 = _r[_nm]
        if _x0 <= float(mx) <= _x1 and _y0 <= float(my) <= _y1:
            _pbu_resolve(m, _use)
            return True
    # ★ 询问是**模态**：框开着时，点其它地方一律吞掉（别误触商店选单/购买）
    return True


def _build_shop(w, h, m, mf, mouse=(0, 0)):
    """★ 商店画面（全屏）。返回 BGR(w, h)——**就按帧泵尺寸渲染**（show() 免缩放，
    光标才流畅）。m=MatchFlow；mf=match_flow 模块；mouse=(mx,my)。中文走贴片。"""
    _key = (w, h, m.round, m.sel_item[1], m.sel_item[2], m.points[1],
            m.points[2], m.sub[1], m.sub[2],
            tuple(sorted(m.items[1].items())), tuple(sorted(m.items[2].items())))
    if _SHOPCACHE['key'] == _key and _SHOPCACHE['base'] is not None:
        img = _SHOPCACHE['base'].copy()
        _purchase_prompt(img, w, h)
        _draw_cursor(img, mouse[0], mouse[1])
        return img
    img = np.full((h, w, 3), (30, 24, 20), np.uint8)
    cv2.rectangle(img, (0, 0), (w, 84), (46, 36, 28), -1)
    cv2.line(img, (0, 84), (w, 84), (86, 226, 255), 2)
    _zh_hud(img, [
        ('第 %d/%d 局 · 商店' % (m.round, mf.MAX_ROUNDS), (26, 16), 36,
         (86, 226, 255)),
        ('选道具 → 购买 → 完成（两边都完成 = 开局）', (w - 560, 32), 20,
         (176, 176, 176))])
    r = _shop_layout(w, h)
    for p in (1, 2):
        pcol = (92, 220, 132) if p == 1 else (232, 106, 192)
        ready = (m.sub[p] == 'READY')
        x0, x1 = (24, 620) if p == 1 else (660, 1256)
        cv2.rectangle(img, (x0, 96), (x1, h - 120), pcol, 2)
        _zh_hud(img, [('玩家%d　积分 %d' % (p, m.points[p]), (x0 + 12, 102), 26,
                       pcol)])
        for i, (key, price, _nm) in enumerate(mf.ITEMS):
            bx0, by0, bx1, by1 = r['p%d_it%d' % (p, i)]
            sel = (m.sel_item[p] == i)
            if sel:
                cv2.rectangle(img, (bx0, by0), (bx1, by1), pcol, 3)
            _its = [
                ('%s' % mf.ITEM_SHORT.get(key, key), (bx0 + 20, by0 + 6), 32,
                 (76, 236, 255) if sel else (216, 216, 216)),
                ('%d分' % price, (bx1 - 130, by0 + 4), 25,
                 (120, 224, 120) if m.points[p] >= price else (128, 128, 132))]
            _got = m.items[p].get(key, 0)
            if _got:
                _its.append(('已购%d' % _got, (bx1 - 130, by0 + 36), 20,
                             (150, 220, 150)))
            _zh_hud(img, _its)
        bx0, by0, bx1, by1 = r['p%d_buy' % p]
        _can = (m.points[p] >= mf.ITEMS[m.sel_item[p]][1])
        cv2.rectangle(img, (bx0 + 6, by0), (bx1 - 6, by1),
                      (70, 170, 90) if (_can and not ready) else (96, 96, 104), -1)
        _zh_hud(img, [('购买', ((bx0 + bx1) // 2 - 40, by0 + 10), 28,
                       (245, 245, 245))])
        dx0, dy0, dx1, dy1 = r['p%d_done' % p]
        cv2.rectangle(img, (dx0 + 6, dy0), (dx1 - 6, dy1),
                      (58, 132, 76) if ready else (150, 92, 58), -1)
        _zh_hud(img, [(('已就绪 OK' if ready else '完成·就绪'),
                       ((dx0 + dx1) // 2 - 72, dy0 + 10), 28, (238, 238, 238))])
    _zh_hud(img, [('键盘：P1 W/S+E　P2 ↑/↓+小键盘*　｜　鼠标：点选→购买→完成',
                   (24, h - 96), 20, (170, 170, 170))])
    _SHOPCACHE['key'] = _key
    _SHOPCACHE['base'] = img.copy()      # 缓存**无光标**的基图（光标每帧另画）
    _purchase_prompt(img, w, h)          # ★ 购买询问横幅（即时件，不进缓存）
    _item_banner(img)                    # ★ 道具应答横幅（不进缓存基图——它是瞬时件）
    _draw_cursor(img, mouse[0], mouse[1])
    return img


def _edit_apply(blk, act):
    """改磁场 ADJUST 一笔作用在块上（纯函数，台架可测）。规则照抄 PC 原型：
    W/↑=方向正（|b|）｜S/↓=方向负（-|b|）｜A/←=强度-步进｜D/→=强度+步进（符号保留、
    夹在 [B_EDIT_MIN, B_EDIT_MAX]）。"""
    b = float(blk.b)
    if act == 'dir+':
        b = abs(b) if abs(b) > 1e-9 else B_EDIT_STEP
    elif act == 'dir-':
        b = -(abs(b) if abs(b) > 1e-9 else B_EDIT_STEP)
    elif act == 'mag+':
        b = (1 if b >= 0 else -1) * min(B_EDIT_MAX, abs(b) + B_EDIT_STEP)
    elif act == 'mag-':
        b = (1 if b >= 0 else -1) * max(B_EDIT_MIN, abs(b) - B_EDIT_STEP)
    blk.b = b
    return b


def _hole_valid(world, edge, pos):
    """洞口合法性（照抄 PC 原型 hole_place_valid）：在边内(留 50px 角距)且与
    同边已有洞口不重叠（间距 < (44 + w)/2 + 12 = 拒）。纯函数——台架可测。"""
    _len = 960.0 if edge in ('top', 'bottom') else 540.0
    if not (50.0 <= pos <= _len - 50.0):
        return False
    for h in world.holes:
        if h.get('edge') == edge and abs(pos - float(h['c']))                 < (HOLE_W_GAME + float(h['w'])) / 2.0 + 12.0:
            return False
    return True


def _hole_from_game(gx, gy, W=960.0, H=540.0, margin=50.0):
    """★ 加洞口 v2（纯函数，台架可测）：游戏坐标 -> (边, 沿边位置)。

    取离该点最近的边；位置 = 该轴上的投影，夹进 [margin, 轴长-margin]（与
    _hole_valid 同角距）。平局（正好在对角平分线上）按 上→下→左→右 的固定
    顺序——确定性优先，别依赖 dict 巧合。
    """
    _d = (('top', float(gy)), ('bottom', H - float(gy)),
          ('left', float(gx)), ('right', W - float(gx)))
    _e = min(_d, key=lambda kv: kv[1])[0]
    _v = float(gx) if _e in ('top', 'bottom') else float(gy)
    _lim = (W if _e in ('top', 'bottom') else H) - margin
    return _e, min(_lim, max(margin, _v))


def _ball_border_candidate(marks, tol=45.0):
    """★ 2026-10-30 用户定案（加洞口道具）：黑球贴到**任意**场区边框时，
    交点 = 洞口候选。marks = `_PHYS['marks']`（最多一颗黑球）。没贴边/
    离边超过 tol 游戏像素 -> None（不接管，保留键盘/鼠标流程）。"""
    if not marks:
        return None
    _b = marks[0]
    try:
        _gx = float(_b['gx'])
        _gy = float(_b['gy'])
    except Exception:
        return None
    if not (-tol <= _gx <= 960.0 + tol and -tol <= _gy <= 540.0 + tol):
        return None
    _d = min(_gx, 960.0 - _gx, _gy, 540.0 - _gy)
    if _d > tol:
        return None
    return _hole_from_game(_gx, _gy, margin=50.0)


def _game_from_mouse(mx, my, pump_w, pump_h, img_w, img_h, Hm, vs):
    """画布鼠标 (mx,my) -> 游戏坐标（只做换算链，不取边）。

    换算链（对话5 摸过的"三处代码"之一，尺寸换算）：画布 1280x720 → 直播图
    1024x720。帧泵是**整图拉伸到整屏**（show() 里 cv2.resize，无留边），所以
    就是线性缩放；再用与球位判定**同一份**标定 cam_to_game 折进游戏坐标。
    """
    _lx = float(mx) * float(img_w) / float(pump_w)
    _ly = float(my) * float(img_h) / float(pump_h)
    _gx, _gy = vs.cam_to_game(Hm, _lx, _ly)[:2]
    return _gx, _gy


def _hole_from_mouse(mx, my, pump_w, pump_h, img_w, img_h, Hm, vs):
    """画布鼠标 -> (edge, pos)（= _game_from_mouse + 最近边；台架已钉）。

    ★ 2026-10-16：这是**实拍叠加时代**的链（鼠标指着相机画面）——瞄准图上线后
    交互主链已换成 `_hole_side_from_canvas`（直映射）。本函数保留作回退/参照，
    台架继续钉它的行为，别删。"""
    _gx, _gy = _game_from_mouse(mx, my, pump_w, pump_h, img_w, img_h, Hm, vs)
    return _hole_from_game(_gx, _gy)


def _hole_side_from_canvas(mx, my, pump_w, pump_h):
    """★ 2026-10-16 瞄准图模式：画布鼠标 -> (edge, pos)，**直映射**。

    俯视瞄准图与游戏系 1:1 居中（偏移 ((w-960)/2, (h-540)/2)，与
    _build_hole_view 的画法成对——两个公式必须一致）。用户实况：还走相机标定
    链时，屏幕左侧的点被透视算成"离左边极远" -> 永远吸到上下边（"左边一部分
    吸附不到"的根因）。点在场外也没关系：_hole_from_game 取最近边 + 夹紧。
    """
    _ox, _oy = (int(pump_w) - 960) // 2, (int(pump_h) - 540) // 2
    return _hole_from_game(float(mx) - _ox, float(my) - _oy)


def _hole_apply_pos(m, edge, pos, mfmod, world, src='点位置'):
    """一笔"点位置"落到 HOLE 预览——**鼠标点击**（先经 _hole_from_mouse 算
    edge/pos）与**手机俯视图点按**（_hole_from_game 算 edge/pos）共用核心：

    set_pos → 合法则进/保持弹窗确认态（顺手更新扣额文案）、不合法则回到跟随态
    并提示。返回 True=位置合法。
    （弹窗开着时的再次点击/点按=挪位置——手机"再点别处可挪"就走这条。）
    """
    m.hole_set_pos(edge, pos)
    _ok = _hole_valid(world, edge, pos)
    if _ok:
        if not _HOLEV['confirm']:
            _HOLEV['quota'] = _hole_quota_txt(m, m.hole['player'], mfmod)
            print('[道具] %s：%s %.0f——等「确定」（点别处可挪）'
                  % (src, edge, pos), flush=True)
        else:
            # ★ 2026-10-15 实测补（板 r75：静默挪位让复盘困惑）：弹窗开着时
            #   再点别处 = 挪位置（设计如此）——但**要留痕**，不然日志里
            #   "last 198 / 超时落了 top 420" 对不上，现场会当成 bug 查半天。
            print('[道具] %s：挪到 %s %.0f（弹窗还开着，点「确定」落洞）'
                  % (src, edge, pos), flush=True)
    else:
        print('[道具] %s：这个位置放不下（太靠角或与已有洞口重叠）——挪一下再点'
              % src, flush=True)
        _item_msg_set('这个位置放不下（太靠角或与已有洞口重叠）——挪一下再点')
    _HOLEV['confirm'] = bool(_ok)
    return _ok


def _place_snap():
    """实时摆放态快照（手机用；纯读 _PPLACE，不改状态）。

    ★★ 2026-10-31：加洞口/加障碍/传送门 在 2026-10-30 之后都走 PLACE 暂停流程，
    手机以前**看不到**这个状态（只能摸键盘 E）。现在 /match 带上它，手机就
    能显示「已识别 xxx —— 点确定生效 / 点取消退出」的悬浮条。
    """
    if not _PPLACE.get('on'):
        return {'on': False}
    _c = (_PPLACE.get('cand') or [None])[0]
    return {'on': True,
            'who': int(_PPLACE.get('who') or 1),
            'kind': str(_PPLACE.get('kind') or ''),
            'confirm': bool(_PPLACE.get('confirm')),
            'status': str(_PPLACE.get('status') or ''),
            'edge': (_c.get('edge') if _c else None),
            'pos': (round(float(_c.get('pos')), 1)
                    if (_c and _c.get('pos') is not None) else None)}


def _hole_snap(m, mf_mod, world):
    """对局状态里的**加洞口快照**（手机俯视图用；纯函数，台架可测）。

    不在 HOLE -> {'active': False}（网页据此收起俯视图）。
    stage：confirm=位置已定住等确认；follow=鼠标/点按跟随中；edge=键盘还没定边
    （pos 无意义，valid=None 让前端画中性标记）。left=30s 超时倒计时。
    """
    if m is None or m.phase != 'HOLE' or world is None:
        return {'active': False}
    _ed, _ps = m.hole['edge'], float(m.hole['pos'])
    _st = m.hole.get('stage')
    return {'active': True, 'who': m.hole['player'], 'edge': _ed,
            'pos': round(_ps, 1),
            'valid': (bool(_hole_valid(world, _ed, _ps))
                      if _st == 'FINE' else None),
            'stage': ('confirm' if _HOLEV['confirm']
                      else ('follow' if _st == 'FINE' else 'edge')),
            'left': round(max(0.0, mf_mod.PAUSE_MAX
                              - float(m.hole.get('t', 0.0))), 1)}


def _hole_commit(m, world):
    """把当前 HOLE 预览落实成真洞。三处共用：E 确认 / 30s 超时 / 弹窗「确定」。

    合法性照 _hole_valid；额度在 match_flow.hole_place 里扣——免费额度 > 道具 >
    30 分，全部记在**发起人**（hole['player']）账上。
    返回 True=已放置（phase 回 PLAY）；False=位置不合法（msg 已由 match_flow 写好，
    phase 不动——调用方决定是"再挪"还是"取消"）。
    """
    _ed, _ps = m.hole['edge'], float(m.hole['pos'])
    if not _hole_valid(world, _ed, _ps):
        m.hole_place(False)              # 写 msg；stage 不在 FINE 时是 no-op
        return False
    if not m.hole_place(True):           # 兜底：绝不"洞进了世界但额度没扣"
        return False
    world.holes.append({'edge': _ed, 'c': _ps, 'w': HOLE_W_GAME,
                        'target': 0})
    return True


def _hole_quota_txt(ob, p, mf):
    """弹窗第二行：这一单怎么扣（免费额度 > 道具 > 积分）——纯函数，台架可测。"""
    if int(ob.free_hole.get(p, 0)) > 0:
        return '免费额度剩 %d 次' % int(ob.free_hole[p])
    if int(ob.items[p].get('hole', 0)) > 0:
        return '道具剩 %d 个' % int(ob.items[p]['hole'])
    _price = 30
    try:
        _price = next(int(it[1]) for it in mf.ITEMS if it[0] == 'hole')
    except Exception:
        pass
    return '扣 %d 分·现 %d 分' % (_price, int(ob.points[p]))


def _holev_clear():
    """加洞口 v2 状态归零（每次离开 HOLE 都调；幂等）。"""
    _HOLEV.update({'who': None, 'mouse': False, 'confirm': False,
                   'rects': {}, 'quota': '', 'ball': None})


def _hole_mouse_click(m, hub, pump, Hm, vs, world, mfmod):
    """★ 加洞口 v2：鼠标左键一笔——弹窗开着=点「确定/取消」；跟随中=定住位置→弹窗。

    返回 True = 这一笔已被加洞口消费（调用方别再走商店分支）；None = 不适用。
    位置不合法不开弹窗（预览早就红着，再给一句人话；"确定会被拒"提前到点击时）。
    """
    if (m is None or m.phase != 'HOLE' or pump is None
            or _HOLEV['who'] != m.hole['player']):
        return None
    if _HOLEV['confirm']:
        _hb = _shop_click(hub.mx, hub.my, _HOLEV['rects'])
        if _hb == 'no':
            _HOLEV['confirm'] = False
            print('[道具] 已取消确认——回到跟随（继续挪）', flush=True)
        elif _hb == 'ok':
            _pk4 = m.hole['player']
            _ed7, _ps7 = m.hole['edge'], float(m.hole['pos'])
            if _hole_commit(m, world):
                _holev_clear()
                print('[道具] %s（边=%s 位置=%.0f；鼠标点「确定」）'
                      % (m.msg, _ed7, _ps7), flush=True)
                _shot_write(dict(t=round(time.time(), 3), ok=True, why='item',
                                 kind='item', seq=0,
                                 msg='P%d 加洞口(%s %.0f)'
                                     % (_pk4, _ed7, _ps7)), player=_pk4)
        return True
    # 跟随中：点击 = 定住位置（先把跟随刷到点击处）；合法才开弹窗
    _HOLEV['mouse'] = True
    try:
        # ★ 2026-10-16：瞄准图模式 -> 直映射（旧相机链换掉，见 _hole_side_from_canvas）
        _ed8, _ps8 = _hole_side_from_canvas(hub.mx, hub.my,
                                            pump.width, pump.height)
    except Exception:
        return True
    _hole_apply_pos(m, _ed8, _ps8, mfmod, world, src='鼠标点位置')
    return True


def _pause_overlay(img, Hm, world, m, vs, hv=None):
    """★ 2026-10-14 暂停叠加（改磁场/加洞口）：直播图上画。
    文字只用 ASCII（老规矩；中文走 _zh_hud 贴片）；几何走 game_to_cam（与画场区
    同一份标定）。任何异常静默返回（叠加不值得把主循环带走）。

    ★ 2026-10-15 加洞口 v2：hv=_HOLEV——鼠标跟随模式下多画两样：
      · 白色光标（画布坐标 → 直播图坐标，缩放系数=直播图尺寸/帧泵画布）；
      · confirm 弹窗（「确定/取消」按钮命中框**写回 hv['rects']**（画布坐标），
        点击处理与画法同源、不会错位）。
    """
    try:
        if m.phase == 'EDIT':
            _ix = int(m.edit['idx'])
            if not (0 <= _ix < len(world.blocks)):
                return
            _b = world.blocks[_ix]
            _p0 = vs.game_to_cam(Hm, _b.x0, _b.y0)
            _p1 = vs.game_to_cam(Hm, _b.x1, _b.y1)
            cv2.rectangle(img, (int(_p0[0]), int(_p0[1])),
                          (int(_p1[0]), int(_p1[1])), (0, 230, 255), 3)
            cv2.putText(img, 'P%d EDIT blk %d/9  B=%+.2f  %s'
                        % (m.edit['player'], _ix + 1, float(_b.b),
                           {'SELECT': 'STEP1 block (A/D, E=ok, ESC=cancel)',
                            'DIR': 'STEP2 dir (W/S, E=ok, ESC=back)',
                            'VALUE': 'STEP3 value (digits, E=ok, ESC=back)'
                            }.get(m.edit['stage'], 'STEP?')),
                        (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.66,
                        (0, 230, 255), 2)

        elif m.phase == 'HOLE':
            _ed = m.hole['edge']
            _ps = float(m.hole['pos'])
            if _ed in ('left', 'right'):
                _fx = 0.0 if _ed == 'left' else 960.0
                _a = vs.game_to_cam(Hm, _fx, _ps - 22.0)
                _b2 = vs.game_to_cam(Hm, _fx, _ps + 22.0)
            else:
                _fy = 0.0 if _ed == 'top' else 540.0
                _a = vs.game_to_cam(Hm, _ps - 22.0, _fy)
                _b2 = vs.game_to_cam(Hm, _ps + 22.0, _fy)
            _ok = _hole_valid(world, _ed, _ps)
            _col = (0, 255, 0) if _ok else (0, 0, 255)
            cv2.line(img, (int(_a[0]), int(_a[1])),
                     (int(_b2[0]), int(_b2[1])), _col, 5)
            _mouse_on = bool(hv and hv.get('mouse'))
            _conf = bool(hv and hv.get('confirm'))
            if _conf:
                _hint = 'CONFIRM: click OK/NO  |  E=ok  ESC=back'
            elif _mouse_on:
                _hint = 'MOUSE: move=slide  LMB=fix  E=ok  ESC=cancel'
            elif m.hole['stage'] == 'EDGE':
                _hint = 'EDGE(C=cycle, E=fix edge, ESC=cancel)'
            else:
                _hint = 'FINE(E=place, ESC=cancel)'
            cv2.putText(img, 'P%d HOLE edge=%s pos=%.0f  %s  %s'
                        % (m.hole['player'], _ed, _ps, _hint,
                           'OK' if _ok else 'BLOCKED'),
                        (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.66, _col, 2)
            # 中文操作提示（贴片层）——跟着状态走，屏上够大够醒目
            if not _conf:
                _zh_hud(img, [(
                    '移动鼠标选边，左键定住位置（E 确定 / ESC 取消）'
                    if _mouse_on else
                    '键盘：C 切边 · E 定边/确认 · ESC 取消（也可动鼠标）',
                    (14, 40), 22, _col)])
            if hv:
                _sx = img.shape[1] / float(hv.get('pw') or 1280)
                _sy = img.shape[0] / float(hv.get('ph') or 720)

                def _r2i(_r):
                    return (int(_r[0] * _sx), int(_r[1] * _sy),
                            int(_r[2] * _sx), int(_r[3] * _sy))

                if _conf:
                    # 确认弹窗（画布坐标定几何 → 缩到直播图；命中框写回画布）
                    _pw = float(hv.get('pw') or 1280)
                    _ph = float(hv.get('ph') or 720)
                    _bx0, _by0 = int(_pw / 2 - 330), int(_ph / 2 - 130)
                    _bx1, _by1 = int(_pw / 2 + 330), int(_ph / 2 + 130)
                    _okr = (_bx0 + 48, _by1 - 92, _bx0 + 296, _by1 - 28)
                    _nor = (_bx1 - 296, _by1 - 92, _bx1 - 48, _by1 - 28)
                    hv['rects'] = {'ok': _okr, 'no': _nor}
                    _q0, _q1, _q2, _q3 = _r2i((_bx0, _by0, _bx1, _by1))
                    cv2.rectangle(img, (_q0, _q1), (_q2, _q3), (16, 20, 26), -1)
                    cv2.rectangle(img, (_q0, _q1), (_q2, _q3), (86, 226, 255), 3)
                    _who_cn = '选手一' if m.hole['player'] == 1 else '选手二'
                    _zh_hud(img, [
                        ('%s · 加洞口（%s）' % (_who_cn, hv.get('quota') or ''),
                         (_q0 + 24, _q1 + 14), 30, (86, 226, 255)),
                        ('确认放置这个洞口？', (_q0 + 24, _q1 + 62), 24,
                         (204, 204, 204))])
                    for _r, _lb, _cb in ((_okr, '确定', (70, 170, 90)),
                                         (_nor, '取消', (150, 92, 58))):
                        _rr = _r2i(_r)
                        cv2.rectangle(img, (_rr[0], _rr[1]),
                                      (_rr[2], _rr[3]), _cb, -1)
                        _zh_hud(img, [(_lb, (_rr[0] + 30, _rr[1] + 10), 28,
                                       (238, 238, 238))])
                # 光标画最后（永远在最上层）
                _lx = int(hv.get('last', (0, 0))[0] * _sx)
                _ly = int(hv.get('last', (0, 0))[1] * _sy)
                _draw_cursor(img, _lx, _ly)
    except Exception:
        return


# ★★ 2026-10-15 加洞口 v2·大屏**俯视瞄准图**（用户实况："鼠标大漂移，不好控制"）
#   ——病根：实拍直播档每帧 ~340ms（抓图+检测+叠加+推帧），光标被 3fps 的整帧
#   刷新拖着走。改法照抄商店档的成功经验：HOLE 暂停期**全场冻结**（不抓相机、
#   不检测），大屏换**合成画面**（与手机俯视图同一套语言），内容不变时零写入、
#   光标动只推小矩形 -> 光标 20+fps 丝滑。几何：场区 960x540 **1:1 居中**
#   （1280x720 画布 -> 偏移 (160,90)），换算零缩放零误差。
_HCACHE = {'key': None, 'base': None, 'lr': None, 'lt': 0.0}


def _hole_view_balls(live_balls, tracks, vs, Hm):
    """俯视瞄准图上的球：真球（游戏系） + 跟踪到的实物球（相机系折游戏系）。

    与手机俯视图的 balls 同一口径（_match_snapshot 用的那段同源）。任何异常
    就地吞掉——瞄准图画少一颗球不算事，游戏循环不能被它带走。"""
    out = []
    try:
        for b in (live_balls or []):
            if getattr(b, 'alive', True):
                # ★ 2026-10-16 修：LiveBall 的位置是 Q10.8 定点（/256 才到游戏像素），
                #   这里原来没除 —— 瞄准图会把在飞球画到 256 倍远（屏外）。
                out.append((float(b.x) / 256.0, float(b.y) / 256.0,
                            int(getattr(b, 'charge', 0) or 0)))
    except Exception:
        pass
    try:
        if vs is not None and Hm is not None:
            for t in (tracks or [])[:10]:
                g = vs.cam_to_game(Hm, t.x, t.y)[:2]
                out.append((float(g[0]), float(g[1]),
                            {'red': 1, 'blue': -1}.get(
                                str(getattr(t, 'cls', '')), 0)))
    except Exception:
        pass
    return out


def _paint_top_scene(img, ox, oy, world, balls, obstacles=None):
    """俯视合成画面的**公共场景画法**（瞄准图/布置图共用一份，2026-10-19 抽出）：
    场区面板+边框 / 磁场块 ⊙⊗+B / 洞口(绿) / 球(红+1 蓝-1 灰未知) / 障碍(深灰条)。

    ★ 从 _build_hole_view 原样抽出（参数 obstacles=None 时逐像素一致——台架
      有两像素级断言钉着）。obstacles 缺省读 world.obstacles（布置的障碍
      在瞄准图里同样要画出来）。"""
    def _g2c(x, y):
        return (int(ox + float(x)), int(oy + float(y)))

    # 场区面板 + 边框
    cv2.rectangle(img, (ox, oy), (ox + 960, oy + 540), (40, 35, 30), -1)
    cv2.rectangle(img, (ox, oy), (ox + 960, oy + 540), (0, 165, 255), 2)
    # 磁场块（出屏偏蓝 / 入屏偏红，与直播档同色语言）+ B 值
    if world is not None:
        for b in world.blocks:
            _p0 = _g2c(b.x0, b.y0)
            _p1 = _g2c(b.x1, b.y1)
            _tint = (76, 56, 36) if float(b.b) >= 0 else (36, 44, 84)
            cv2.rectangle(img, _p0, _p1, _tint, -1)
            cv2.rectangle(img, _p0, _p1, (110, 110, 110), 1)
            _cc = ((_p0[0] + _p1[0]) // 2, (_p0[1] + _p1[1]) // 2)
            cv2.putText(img, 'B%+.1f' % float(b.b), (_cc[0] - 30, _cc[1] + 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.62, (210, 160, 100), 2)
        # 已有洞口（绿）
        for hh in world.holes:
            _e2, _c2, _w2 = hh['edge'], float(hh['c']), float(hh['w']) / 2.0
            if _e2 == 'left':
                _a2, _b2 = _g2c(0.0, _c2 - _w2), _g2c(0.0, _c2 + _w2)
            elif _e2 == 'right':
                _a2, _b2 = _g2c(960.0, _c2 - _w2), _g2c(960.0, _c2 + _w2)
            elif _e2 == 'top':
                _a2, _b2 = _g2c(_c2 - _w2, 0.0), _g2c(_c2 + _w2, 0.0)
            else:
                _a2, _b2 = _g2c(_c2 - _w2, 540.0), _g2c(_c2 + _w2, 540.0)
            cv2.line(img, _a2, _b2, (0, 200, 0), 8)
        # 障碍：**实际碰撞盒 AABB**（黄框，PL/预览的碰撞口径）+ 里层灰暗物体
        import v3_fixedpoint as _vfp1
        for _ob in (obstacles if obstacles is not None
                    else (getattr(world, 'obstacles', None) or [])):
            _c4, _s4 = math.cos(_ob.ang), math.sin(_ob.ang)
            _ehw1 = _ob.hw + float(_vfp1.BALL_R)
            _ehh1 = _ob.hh + float(_vfp1.BALL_R)
            _abp = []
            for _lx1, _ly1 in ((-_ehw1, -_ehh1), (_ehw1, -_ehh1),
                               (_ehw1, _ehh1), (-_ehw1, _ehh1)):
                _abp.append(_g2c(_ob.cx + _lx1 * _c4 - _ly1 * _s4,
                                 _ob.cy + _lx1 * _s4 + _ly1 * _c4))
            cv2.polylines(img, np.array([_abp], np.int32), True,
                          (0, 235, 255), 2)
            _pts = []
            for _lx, _ly in ((-_ob.hw, -_ob.hh), (_ob.hw, -_ob.hh),
                             (_ob.hw, _ob.hh), (-_ob.hw, _ob.hh)):
                _pts.append(_g2c(_ob.cx + _lx * _c4 - _ly * _s4,
                                 _ob.cy + _lx * _s4 + _ly * _c4))
            _pa = np.array([_pts], np.int32)
            cv2.fillPoly(img, _pa, (52, 52, 52))
            cv2.polylines(img, _pa, True, (150, 150, 150), 1)
        # 传送门：方形触发区（PL/预览口径，半边长 PORTAL_R）+ 中心芯
        for _pt in (getattr(world, 'portals', None) or []):
            _pr1 = float(_vfp1.PORTAL_R)
            _sq1 = [_g2c(_pt.x - _pr1, _pt.y - _pr1),
                    _g2c(_pt.x + _pr1, _pt.y - _pr1),
                    _g2c(_pt.x + _pr1, _pt.y + _pr1),
                    _g2c(_pt.x - _pr1, _pt.y + _pr1)]
            cv2.polylines(img, np.array([_sq1], np.int32), True,
                          (0, 200, 0), 2)
            _pc4 = _g2c(_pt.x, _pt.y)
            cv2.circle(img, _pc4, 4, (0, 200, 0), -1)
    # 球（红+1 / 蓝-1 / 未知灰；白圈）
    for (_bx, _by, _ch) in (balls or []):
        _c3 = _g2c(_bx, _by)
        _fc = ((60, 60, 230) if _ch == 1
               else ((230, 90, 60) if _ch == -1 else (150, 150, 150)))
        cv2.circle(img, _c3, 9, _fc, -1)
        cv2.circle(img, _c3, 9, (240, 240, 240), 1)


def _build_hole_view(w, h, m, mfmod, world, hv, mouse, balls):
    """加洞口暂停期的俯视瞄准图（合成画布 w x h，**无光标版**——光标由调用方
    每帧画，好走 show_rect 局部更新）。命中框写回 hv['rects']（画布坐标——
    与 _hole_mouse_click / _shop_click 同一坐标系，不错位）。"""
    img = np.full((h, w, 3), (26, 22, 18), np.uint8)
    _ox, _oy = (w - 960) // 2, (h - 540) // 2

    def _g2c(x, y):
        return (int(_ox + float(x)), int(_oy + float(y)))

    _paint_top_scene(img, _ox, _oy, world, balls)
    # 洞口预览（边 + 位置；绿=可放 / 红=放不下）+ 位置中心标记
    _ed = m.hole['edge']
    _ps = float(m.hole['pos'])
    _okp = (_hole_valid(world, _ed, _ps)
            if m.hole.get('stage') == 'FINE' else False)
    _is_ball = bool(hv.get('ball'))
    # ★ 2026-10-30：黑球贴边候选用**黄色**（与障碍候选同一套语言）
    _col = ((0, 235, 255) if _is_ball else (0, 230, 0)) if _okp \
        else (0, 0, 255)
    if _ed == 'left':
        _p1, _p2 = _g2c(0.0, _ps - 22.0), _g2c(0.0, _ps + 22.0)
    elif _ed == 'right':
        _p1, _p2 = _g2c(960.0, _ps - 22.0), _g2c(960.0, _ps + 22.0)
    elif _ed == 'top':
        _p1, _p2 = _g2c(_ps - 22.0, 0.0), _g2c(_ps + 22.0, 0.0)
    else:
        _p1, _p2 = _g2c(_ps - 22.0, 540.0), _g2c(_ps + 22.0, 540.0)
    cv2.line(img, _p1, _p2, _col, 8)
    if _is_ball:
        cv2.rectangle(img, (min(_p1[0], _p2[0]) - 10,
                            min(_p1[1], _p2[1]) - 10),
                      (max(_p1[0], _p2[0]) + 10,
                       max(_p1[1], _p2[1]) + 10), _col, 3)
    _pc = ((_p1[0] + _p2[0]) // 2, (_p1[1] + _p2[1]) // 2)
    cv2.circle(img, _pc, 13, _col, 2)
    # 光标 -> 最近边 的虚线指引（让"点的哪里取的哪条边"一眼可见）
    if mouse and not hv.get('confirm'):
        _mx2, _my2 = int(mouse[0]), int(mouse[1])
        _steps = 12
        for _i2 in range(_steps):
            _t0 = _i2 / float(_steps)
            _t1 = min(1.0, _t0 + 0.55 / _steps)
            _q0 = (int(_mx2 + (_pc[0] - _mx2) * _t0),
                   int(_my2 + (_pc[1] - _my2) * _t0))
            _q1 = (int(_mx2 + (_pc[0] - _mx2) * _t1),
                   int(_my2 + (_pc[1] - _my2) * _t1))
            cv2.line(img, _q0, _q1, (90, 90, 90), 1)
    # 文案（中文贴片；全部是字库门禁里已覆盖的串）
    _who_cn = '选手一' if m.hole['player'] == 1 else '选手二'
    _left = max(0.0, float(mfmod.PAUSE_MAX) - float(m.hole.get('t', 0.0)))
    _zh_hud(img, [('加洞口 · %s · 剩 %.0f 秒' % (_who_cn, _left),
                   (24, 16), 32, (86, 226, 255))])
    if not hv.get('confirm'):
        _hint = ('黑球贴边候选：E/小键盘* 确认 · ESC 取消 · 移动鼠标可改'
                 if hv.get('ball') else
                 ('移动鼠标选边，左键定住位置（E 确定 / ESC 取消）'
                  if hv.get('mouse') else
                  '键盘：C 切边 · E 定边/确认 · ESC 取消（也可动鼠标）'))
        _zh_hud(img, [(_hint, (24, h - 54), 24, (204, 204, 204))])
    else:
        # 确认弹窗（与直播档同一组文案；坐标=画布坐标，直接画）
        _bx0, _by0 = w // 2 - 330, h // 2 - 130
        _bx1, _by1 = w // 2 + 330, h // 2 + 130
        _okr = (_bx0 + 48, _by1 - 92, _bx0 + 296, _by1 - 28)
        _nor = (_bx1 - 296, _by1 - 92, _bx1 - 48, _by1 - 28)
        hv['rects'] = {'ok': _okr, 'no': _nor}
        cv2.rectangle(img, (_bx0, _by0), (_bx1, _by1), (26, 20, 16), -1)
        cv2.rectangle(img, (_bx0, _by0), (_bx1, _by1), (86, 226, 255), 3)
        _zh_hud(img, [
            ('%s · 加洞口（%s）' % (_who_cn, hv.get('quota') or ''),
             (_bx0 + 24, _by0 + 14), 30, (86, 226, 255)),
            ('确认放置这个洞口？', (_bx0 + 24, _by0 + 62), 24, (204, 204, 204))])
        for _r, _lb, _cb in ((_okr, '确定', (70, 170, 90)),
                             (_nor, '取消', (150, 92, 58))):
            cv2.rectangle(img, (_r[0], _r[1]), (_r[2], _r[3]), _cb, -1)
            _zh_hud(img, [(_lb, (_r[0] + 30, _r[1] + 10), 28, (238, 238, 238))])
    _item_banner(img)                       # ★ 道具应答横幅（在光标层之下）
    return img


def _build_obsp_view(w, h, m, mfmod, world, obsp, mouse, balls):
    """布置 PLACE v1：**障碍摆放画面**（合成俯视图；光标由本函数画进来，
    共享调用方的整帧/局部推帧门）。命中框写回 obsp['rects']（画布坐标，
    与 _shop_click / _obsp_mouse_click 同一坐标系）。"""
    img = np.full((h, w, 3), (26, 22, 18), np.uint8)
    _ox, _oy = (w - 960) // 2, (h - 540) // 2
    _paint_top_scene(img, _ox, _oy, world, balls)
    _okp = _obst_valid(world, obsp['cx'], obsp['cy'], ang=obsp['ang'])
    _col = (0, 230, 0) if _okp else (0, 0, 255)
    # 待放障碍预览（与真障碍同款深灰条；绿框=可放 / 红框=放不下）
    _c4 = math.cos(math.radians(float(obsp['ang'])))
    _s4 = math.sin(math.radians(float(obsp['ang'])))
    _pts = []
    for _lx, _ly in ((-OBS_W_GAME / 2.0, -OBS_H_GAME / 2.0),
                     (OBS_W_GAME / 2.0, -OBS_H_GAME / 2.0),
                     (OBS_W_GAME / 2.0, OBS_H_GAME / 2.0),
                     (-OBS_W_GAME / 2.0, OBS_H_GAME / 2.0)):
        _pts.append((int(_ox + obsp['cx'] + _lx * _c4 - _ly * _s4),
                     int(_oy + obsp['cy'] + _lx * _s4 + _ly * _c4)))
    _pa = np.array([_pts], np.int32)
    cv2.fillPoly(img, _pa, (52, 52, 52))
    cv2.polylines(img, _pa, True, _col, 3)
    cv2.drawMarker(img, (int(_ox + obsp['cx']), int(_oy + obsp['cy'])),
                   _col, cv2.MARKER_CROSS, 22, 2)
    # 文案（中文贴片；全部要进字库门禁）
    _who_cn = '选手一' if obsp['who'] == 1 else '选手二'
    _zh_hud(img, [('布置障碍 · %s（剩余 %d 个）'
                   % (_who_cn, int(m.items[obsp['who']]['obstacle'])),
                   (24, 16), 32, (86, 226, 255))])
    if not obsp.get('confirm'):
        _zh_hud(img, [('鼠标点位置，左键定住；W/S 旋转 %.0f°；E 确定 / ESC 取消'
                       % OBS_ANG_STEP, (24, h - 54), 24, (204, 204, 204))])
    else:
        _bx0, _by0 = w // 2 - 330, h // 2 - 130
        _bx1, _by1 = w // 2 + 330, h // 2 + 130
        _okr = (_bx0 + 48, _by1 - 92, _bx0 + 296, _by1 - 28)
        _nor = (_bx1 - 296, _by1 - 92, _bx1 - 48, _by1 - 28)
        obsp['rects'] = {'ok': _okr, 'no': _nor}
        cv2.rectangle(img, (_bx0, _by0), (_bx1, _by1), (26, 20, 16), -1)
        cv2.rectangle(img, (_bx0, _by0), (_bx1, _by1), (86, 226, 255), 3)
        _zh_hud(img, [
            ('%s · 布置障碍（%.0f度）' % (_who_cn, float(obsp['ang'])),
             (_bx0 + 24, _by0 + 14), 30, (86, 226, 255)),
            ('确认放置这个障碍？' if _okp else '这里放不下（贴边/盖洞/叠障碍）',
             (_bx0 + 24, _by0 + 62), 24,
             (204, 204, 204) if _okp else (80, 80, 255))])
        for _r, _lb, _cb in ((_okr, '确定', (70, 170, 90)),
                             (_nor, '取消', (150, 92, 58))):
            cv2.rectangle(img, (_r[0], _r[1]), (_r[2], _r[3]), _cb, -1)
            _zh_hud(img, [(_lb, (_r[0] + 30, _r[1] + 10), 28, (238, 238, 238))])
    _item_banner(img)
    if mouse:
        _draw_cursor(img, int(mouse[0]), int(mouse[1]))
    return img


def _pplace_draw_cand(img, cand, to_px, color=(0, 235, 255)):
    """候选高亮：黑条=**贴着实物的旋转黄框**（长度/角度/厚度），绿门=黄色方框。"""
    for _c in (cand or [])[:6]:
        try:
            if _c.get('kind') == 'hole':
                # ★ 2026-10-30：加洞口候选——黄框贴在边框交点上
                _ed = str(_c.get('edge') or '')
                _ps = float(_c.get('pos') or 0.0)
                if _ed in ('left', 'right'):
                    _gx = 0.0 if _ed == 'left' else 960.0
                    _pa = to_px(_gx, _ps - 22.0)
                    _pb = to_px(_gx, _ps + 22.0)
                else:
                    _gy = 0.0 if _ed == 'top' else 540.0
                    _pa = to_px(_ps - 22.0, _gy)
                    _pb = to_px(_ps + 22.0, _gy)
                cv2.line(img, (int(_pa[0]), int(_pa[1])),
                         (int(_pb[0]), int(_pb[1])), color, 8)
                cv2.rectangle(img,
                              (min(int(_pa[0]), int(_pb[0])) - 10,
                               min(int(_pa[1]), int(_pb[1])) - 10),
                              (max(int(_pa[0]), int(_pb[0])) + 10,
                               max(int(_pa[1]), int(_pb[1])) + 10),
                              color, 3)
                continue
            if _c.get('kind') == 'bar':
                _ca = math.cos(math.radians(float(_c.get('gang', 0.0))))
                _sa = math.sin(math.radians(float(_c.get('gang', 0.0))))
                # ★ 2026-10-29 用户实况（"框盖不住整根条"）：判据仍用二阶矩
                #   量出的黑芯尺寸，**画框**统一外扩 8/5 游戏像素（视觉余量），
                #   不动物理长度；黑芯端部软边不会被漏在框外。
                _hw = max(16.0, float(_c.get('glen', 0.0)) / 2.0 + 8.0)
                _hh = max(5.0, float(_c.get('gthick', 0.0)) / 2.0 + 5.0)
                _pts = []
                for _lx, _ly in ((-_hw, -_hh), (_hw, -_hh),
                                 (_hw, _hh), (-_hw, _hh)):
                    _gx = float(_c['gx']) + _lx * _ca - _ly * _sa
                    _gy = float(_c['gy']) + _lx * _sa + _ly * _ca
                    _p = to_px(_gx, _gy)
                    _pts.append((int(_p[0]), int(_p[1])))
                cv2.polylines(img, [np.array([_pts], np.int32)], True,
                              color, 3)
                _pc = to_px(float(_c['gx']), float(_c['gy']))
                cv2.drawMarker(img, (int(_pc[0]), int(_pc[1])), color,
                               cv2.MARKER_CROSS, 20, 2)
            else:
                _pc = to_px(float(_c['gx']), float(_c['gy']))
                _x, _y = int(_pc[0]), int(_pc[1])
                cv2.rectangle(img, (_x - 24, _y - 24), (_x + 24, _y + 24),
                              color, 3)
                cv2.drawMarker(img, (_x, _y), color,
                               cv2.MARKER_CROSS, 20, 2)
        except Exception:
            continue


def _pplace_text(img, w, h, m, pplace, mouse):
    """布置态的文案层（合成/实时相机共用）：标题/提示/识别状态/横幅/光标。"""
    if pplace.get('is_adjust'):
        # ★★ 2026-10-28 用户定案：双方就绪后先给「调整障碍/传送门」界面；
        #   开局后锁定（实心黄）。这里只显示状态与开始键，不做扣额/摆放。
        _zh_hud(img, [('调整障碍 / 传送门（开局前可挪动实物）', (24, 16), 32,
                       (86, 226, 255))])
        _zh_hud(img, [('把桌上的实体黑条 / 绿门摆到想要的位置，画面 0.3 秒刷新；'
                       '按 E / 小键盘* 开始本局，ESC 回商店',
                       (24, h - 54), 22, (204, 204, 204))])
        _zh_hud(img, [(pplace.get('status') or '', (24, h - 92), 24,
                       (0, 235, 255))])
        _item_banner(img)
        if mouse:
            _draw_cursor(img, int(mouse[0]), int(mouse[1]))
        return img
    _who_cn = '选手一' if int(pplace['who']) == 1 else '选手二'
    _is_obs = (pplace.get('kind') == 'obstacle')
    _is_hole = (pplace.get('kind') == 'hole')
    try:
        _left = int(m.items[pplace['who']][pplace['kind']])
    except Exception:
        _left = 0
    if _is_hole:
        _quota = _hole_quota_txt(m, pplace['who'], None)
        _title = '布置加洞口 · %s（%s）' % (_who_cn, _quota)
    else:
        _title = ('布置实体障碍 · %s（剩余 %d 个）' if _is_obs
                  else '布置实体传送门 · %s（剩余 %d 个）') % (_who_cn, _left)
    _zh_hud(img, [(_title, (24, 16), 32, (86, 226, 255))])
    _key = _pplace_key(pplace['who'])
    if _is_hole:
        _tip = ('把黑球贴到场区任意边框；黄框出现后 %s 放置 / ESC 取消'
                '（扣免费/道具/30分）' % _key)
    elif _is_obs:
        _tip = ('把实体黑条放到场区（离已有件 >60px）；点一下黑条可锁定，'
                '黄框后 %s 放置完成 / ESC 取消' % _key)
    else:
        _tip = ('把一对实体绿门放到场区（间距至少80像素，离已有件 >75px）；'
                '用鼠标点两扇门可锁定；黄框后 %s 放置完成 / ESC 取消' % _key)
    _zh_hud(img, [(_tip, (24, h - 54), 22, (204, 204, 204))])
    _n = len(pplace.get('cand') or [])
    if pplace.get('confirm'):
        if _is_hole:
            _st = '黑球贴边候选（黄框）——%s 放置 / ESC 取消' % _key
        elif pplace.get('pick'):
            _st = '已锁定这根黑条（黄框）——%s 放置完成 / ESC 取消' % _key
        else:
            _st = '识别到新件（黄框）——%s 放置完成 / ESC 取消' % _key
        _sc = (0, 235, 255)
    elif _n:
        _st, _sc = ('识别到 %d 个新件——等待稳定…' % _n, (0, 235, 255))
    elif _is_hole:
        _st, _sc = ('把黑球贴到边框（相机 0.3 秒看一次）', (150, 150, 150))
    else:
        _st, _sc = ('还没识别到新件（相机 0.3 秒看一次；放稳后等 1 秒）',
                    (150, 150, 150))
    _zh_hud(img, [(_st, (24, h - 92), 24, _sc)])
    # ★ 2026-10-30 用户实况（"提示相互重叠"）：布置画面已有标题/提示/状态，
    #   不再叠道具横幅（同一条信息会在顶部再压一层）。
    if mouse:
        _draw_cursor(img, int(mouse[0]), int(mouse[1]))
    return img


def _build_pplace_view(w, h, m, mfmod, world, pplace, mouse, balls):
    """★ 2026-10-25 实体道具布置画面（**合成俯视图**，相机抓不到时兜底）。

    玩家在桌上摆实体黑条 / 一对实体绿门；识别稳定（0.4s）后 `_pplace_check`
    自动扣 1 次并生效。这里只负责把"游戏看到的新件"画出来——看得见才敢摆。
    """
    img = np.full((h, w, 3), (26, 22, 18), np.uint8)
    _ox, _oy = (w - 960) // 2, (h - 540) // 2
    _paint_top_scene(img, _ox, _oy, world, balls)
    _pplace_draw_cand(img, pplace.get('cand'),
                      lambda gx, gy: (_ox + gx, _oy + gy))
    return _pplace_text(img, w, h, m, pplace, mouse)


def _build_pplace_live(raw, swap, Hm, world, m, pplace, mouse,
                       fire_margin=70.0, angle=0.0, speed=400.0,
                       crop=False, crop_margin=0.06, interp='nearest',
                       pre_img=None):
    """★ 2026-10-27 用户定案 B：布置态**实时相机画面**（比合成俯视图直观）。

    相机实拍 + 场区/块/洞/已生效障碍门（`_build_live` 现成叠加）+ 新件黄圈 +
    布置文案。识别仍是 PS `phys_objects`（0.3s 节拍），不碰 PL/RTL/bit。
    """
    img = _build_live(raw, swap, Hm, world, None, [], angle, speed,
                      fire_margin=fire_margin, previews=None,
                      live_balls=[], tracks=[], hud=False, img=pre_img)
    # ★ 2026-10-27 点选坐标反算：记下这一帧原图尺寸与是否裁剪（点击用同一份口径）
    pplace['raw_w'], pplace['raw_h'] = int(img.shape[1]), int(img.shape[0])
    import v3_scan
    _pplace_draw_cand(img, pplace.get('cand'),
                      lambda gx, gy: v3_scan.game_to_cam(Hm, gx, gy))
    _did_crop = False
    if crop and img.shape[0] == 720 and img.shape[1] == 1024:
        img = _live_crop_image(img, Hm, crop_margin, interp=interp,
                               holes=getattr(world, 'holes', None))
        _did_crop = True
    pplace['crop_on'] = _did_crop
    return _pplace_text(img, img.shape[1], img.shape[0], m, pplace, mouse)


def _phone_state_tick(ob, mod, world, shop_open, live_balls, tracks,
                      vs, Hm):
    """手机要的二份 json 的 0.5s 节拍（球列表 / 对局+俯视图数据）。

    正常检测路径与 **HOLE 冻结路径**共用这一份——冻结期检测/跟踪全跳过（全场
    冻结、画面不动），但 **json 必须活着**（手机俯视图和状态栏 0.5s 一刷，
    不刷就"死了"）。写失败不抛（_balls_write/_match_write 自己的纪律）。"""
    _BALLS['t'] = time.time()
    _balls_write(tracks)
    if ob is not None:
        from vdma_capture import WD_STAT       # 与主循环同一份（模块级单例）
        _ln = _link_track(_LINK_TRK,
                          _RESCUE['n_pulse'] + WD_STAT['n_recover'],
                          time.time())
        _bah = []
        try:
            for _bh9 in (live_balls or []):
                if getattr(_bh9, 'alive', True):
                    _bah.append([round(float(_bh9.x), 1),
                                 round(float(_bh9.y), 1),
                                 int(getattr(_bh9, 'charge', 0) or 0)])
        except Exception:
            pass
        try:
            if vs is not None and Hm is not None:
                for _th9 in (tracks or [])[:10]:
                    _gh9 = vs.cam_to_game(Hm, _th9.x, _th9.y)[:2]
                    _bah.append([round(float(_gh9[0]), 1),
                                 round(float(_gh9[1]), 1),
                                 {'red': 1, 'blue': -1}.get(
                                     str(getattr(_th9, 'cls', '')), 0)])
        except Exception:
            pass
        try:
            # ★ 2026-10-16 粒子引擎（--pe）：影子球也上手机俯视图
            for _sh9 in (_PEB['balls'] if _PEB['on'] else []):
                if _sh9.alive:
                    _bah.append([round(_sh9.x / 256.0, 1),
                                 round(_sh9.y / 256.0, 1),
                                 int(_sh9.charge)])
        except Exception:
            pass
        _match_write(_match_snapshot(
            ob, _ln, mod, shop_open=shop_open, world=world,
            hole=_hole_snap(ob, mod, world), balls=_bah,
            place=_place_snap()))


def _build_prep_gate(w, h, m, mf, mouse=(0, 0)):
    """★ 2026-10-13：局前「商店入口」合成画面（不抓相机——进店前光标丝滑；
    开局后直播画面自动回来）。返回 (img, ent_rect)。mf=match_flow 模块。"""
    img = np.full((h, w, 3), (26, 22, 18), np.uint8)
    cv2.rectangle(img, (0, 0), (w, 84), (46, 36, 28), -1)
    cv2.line(img, (0, 84), (w, 84), (86, 226, 255), 2)
    _zh_hud(img, [('第 %d/%d 局 · 局前' % (m.round, mf.MAX_ROUNDS), (26, 16), 36,
                   (86, 226, 255))])
    bw, bh = 660, 156
    x0, y0 = (w - bw) // 2, h // 2 - bh // 2
    cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), (16, 20, 26), -1)
    cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), (86, 226, 255), 3)
    _zh_hud(img, [
        ('商店：点这里进入', (x0 + 30, y0 + 16), 42, (86, 226, 255)),
        ('或 P1 按 E ｜ P2 按 小键盘* ｜ 手机点「进入商店」', (x0 + 30, y0 + 88),
         22, (204, 204, 204))])
    _zh_hud(img, [('进店选购 → 各自「完成」= 就绪 → 双方就绪开局', (24, h - 58), 22,
                   (150, 150, 150))])
    _item_banner(img)                       # ★ 道具应答横幅
    _draw_cursor(img, mouse[0], mouse[1])
    return img, (x0, y0, x0 + bw, y0 + bh)


def _shop_prep_overlay(img, w, h, m, mouse=(0, 0)):
    """局前（商店画面没开）：直播图缩到画布尺寸 + 大「商店」入口按钮 + 光标。
    返回 (img, rect)——rect 由调用方存进 _SHOPV['rects']['ent'] 供点击命中。"""
    if img.shape[1] != w or img.shape[0] != h:
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
    bw, bh = 640, 134
    x0, y0 = (w - bw) // 2, h - bh - 36
    cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), (16, 20, 26), -1)
    cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), (86, 226, 255), 3)
    _zh_hud(img, [
        ('商店（第 %d 局）：点这里进入' % m.round, (x0 + 26, y0 + 14), 36,
         (86, 226, 255)),
        ('或 P1 按 E ｜ P2 按 小键盘*（手机面板也能买）', (x0 + 26, y0 + 76), 21,
         (200, 200, 200))])
    _draw_cursor(img, mouse[0], mouse[1])
    return img, (x0, y0, x0 + bw, y0 + bh)


def _track_num_label(t):
    """轨迹在圈旁画的**编号** —— ★★ 就是 **t.no**（发射/预览点名用的同一把号）。

    ★★ 2026-10-05 夜现场 bug（"控球3按发射没反应"）的**直接**根因就在这：
      这里原来画的是 `enumerate` 的**列表序号**（_ti+1），而发射是按 `t.no`
      点名的 —— 两套号！编号一旦不连续（比如 {1,2,4}），屏幕会把"4号"画成
      "3"，玩家选 3 -> 按 t.no==3 点名找不到 -> 拒绝 noball。修法：画的就是
      t.no；未确认（no=None，灰/青圈已表示"新生"）返回 None = 不画。
    """
    return None if getattr(t, 'no', None) is None else int(t.no)


def _live_tag(img, text, px, py, color, size=18):
    """★ 2026-10-30 用户实况（"文字要在屏幕上看得见"）：直播标签统一黑底+
    贴边夹紧；物体靠上时调用方把 y 放到物体下方（见 _build_live）。"""
    try:
        _sp = _zh_sprite(text, size, color)
        _tw = int(_sp.shape[1]) if _sp is not None else int(len(text) * size)
        _th = int(_sp.shape[0]) if _sp is not None else int(size + 6)
        _x = int(px)
        _y = int(py)
        _x = max(4, min(int(img.shape[1]) - _tw - 8, _x))
        _y = max(4, min(int(img.shape[0]) - _th - 8, _y))
        cv2.rectangle(img, (_x - 3, _y - 3),
                      (_x + _tw + 3, _y + _th + 3), (0, 0, 0), -1)
        _zh_hud(img, [(text, (_x, _y), size, color)])
    except Exception:
        pass


def _build_live(raw, swap, Hm, world, ball, trail, angle, speed,
                fire_margin=70.0, previews=None, angle2=None, speed2=None,
                live_balls=None, tracks=None, bno1=None, bno2=None,
                match_line=None, hud=True, img=None):
    """★ 直播档画面：**摄像头实拍** + 场区/洞口/球/轨迹叠加（返回 BGR，原图尺寸）。

    用户第①点要的"划定区域"就画在这上面：
      · 场区边界（橙，2px）—— 游戏坐标 960x540 四角经 game_to_cam 折回相机像素
        （与球位判定**同一份**标定；两向映射必须成对，见 v3_scan.game_to_cam 注释）
      · 洞口（绿，6px 粗线）—— 与仿真图同一套视觉语言，玩家知道该往哪打
      · 球圈（黄）+ 历史轨迹（绿）—— 直接是相机像素，不用折算
      · 文字**只用 ASCII**（Hershey 字体画不出中文，"离洞口边"那排 `?????` 是
        2026-09-29 踩过的坑）；中文只留给控制台。

    ★ 这里是**每帧**都要跑的（直播档 ~7fps），所以只做最省的几笔 cv2 调用：
      不放大、不加权、不落盘（存图由调用方按 5s 限频另外做）。
    """
    import v3_scan
    from ball_map_detect import raw_to_bgr
    if _SM.get('on'):
        _SM['balls'] = []              # ★ 每帧重建"飞行球"清单（给平滑线程）
    _flds_w = ([(bb.x0, bb.y0, bb.x1, bb.y1, bb.b) for bb in world.blocks]
               if (world is not None and _SM.get('on') and live_balls) else [])
    if img is None:
        _tb_img = time.perf_counter()
        img = raw_to_bgr(raw, swap)
        _ms_add('b_img', _tb_img)      # ★ 拆账：原图解码占多少
    else:
        # ★ 传入的帧是**共用缓存**（摆放识别帧）——必须拷一份再画，否则叠加
        #   会写回缓存、下一帧在旧线上再画一遍（重影）。
        img = img.copy()
    # 场区四角 -> 相机像素（游戏坐标是 y 向下的，和相机像素同向，别反）
    _cs = [v3_scan.game_to_cam(Hm, 0.0, 0.0),
           v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, 0.0),
           v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, v3_scan.FIELD_H),
           v3_scan.game_to_cam(Hm, 0.0, v3_scan.FIELD_H)]
    # ★★ 2026-10-28 用户定案（第 4/5 条）：**回滚**——场区框用原来的颜色/粗细，
    #   也不再单独加"凸显框"（会压到障碍物）。原样：橙色 2px，画在预瞄线之前。
    cv2.polylines(img, [np.array([[_cs[0], _cs[1], _cs[2], _cs[3]]],
                                 np.int32)], True, (0, 165, 255), 2,
                  cv2.LINE_AA)
    # ★★ 2026-10-28 用户定案：**去掉紫色可发射带线**——球在红色场区内任意位置
    #   都可识别/初始/发射（`_fire_gate` 不再按 margin 拒绝）。`fire_margin`
    #   参数保留只为兼容旧调用，不再画线。
    # ★★ 2026-10-06（M1-① "分区磁场并可视化"）：**把磁场分块画到直播画面上**——
    #   细边框 + 块心符号（⊙=出屏 / ⊗=入屏；Hershey 画不了 ⊙⊗，用"圆圈+点/X"
    #   图元拼出来）+ ASCII 标签 B=±x.x。颜色沿袭仿真图语言：出屏偏蓝、入屏偏红
    #   （v3_scan 老 render 同款）。世界是 ±隔块翻转的 3 块时，屏幕上能直接看到
    #   "左右弯交替"的分区——这是规则书"分块磁场"的现场可视化。
    if world is not None:
        for b in world.blocks:
            _bq = np.array([[v3_scan.game_to_cam(Hm, b.x0, b.y0),
                             v3_scan.game_to_cam(Hm, b.x1, b.y0),
                             v3_scan.game_to_cam(Hm, b.x1, b.y1),
                             v3_scan.game_to_cam(Hm, b.x0, b.y1)]], np.int32)
            cv2.polylines(img, _bq, True, (150, 150, 150), 1)
            _ctr = v3_scan.game_to_cam(Hm, (b.x0 + b.x1) / 2.0,
                                       (b.y0 + b.y1) / 2.0)
            _cx, _cy = int(_ctr[0]), int(_ctr[1])
            _bcol = (200, 120, 0) if b.b > 0 else (0, 80, 220)   # 出屏偏蓝 / 入屏偏红
            cv2.circle(img, (_cx, _cy), 11, _bcol, 2)
            if b.b > 0:                                          # ⊙ 出屏：圈 + 点
                cv2.circle(img, (_cx, _cy), 2, _bcol, -1)
            else:                                                # ⊗ 入屏：圈 + X
                cv2.line(img, (_cx - 7, _cy - 7), (_cx + 7, _cy + 7), _bcol, 2)
                cv2.line(img, (_cx - 7, _cy + 7), (_cx + 7, _cy - 7), _bcol, 2)
            cv2.putText(img, 'B%+.1f' % b.b, (_cx - 22, _cy + 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, _bcol, 2)
    # ★ 2026-10-19 布置 PLACE v1：**已布置的障碍**画进直播叠加。
    #   ★ 用户定案：碰撞体 = **旋转矩形**（贴合黑条），碰撞盒按 hw+BALL_R /
    #     hh+BALL_R 扩张；**黄框就是轨迹真正反弹的边界**，里层灰暗是物体本身。
    if world is not None:
        import v3_fixedpoint as _vfp0
        _locked = bool(_PHYS.get('lock'))
        for _ob in (getattr(world, 'obstacles', None) or []):
            _o4 = math.cos(_ob.ang)
            _s4 = math.sin(_ob.ang)
            _ehw = _ob.hw + float(_vfp0.BALL_R)
            _ehh = _ob.hh + float(_vfp0.BALL_R)
            _abp = []
            for _lx, _ly in ((-_ehw, -_ehh), (_ehw, -_ehh),
                             (_ehw, _ehh), (-_ehw, _ehh)):
                _cc = v3_scan.game_to_cam(Hm,
                                          _ob.cx + _lx * _o4 - _ly * _s4,
                                          _ob.cy + _lx * _s4 + _ly * _o4)
                _abp.append([int(_cc[0]), int(_cc[1])])
            cv2.polylines(img, [np.array(_abp, np.int32)], True,
                          (0, 235, 255), 2)
            _obp = []
            for _lx, _ly in ((-_ob.hw, -_ob.hh), (_ob.hw, -_ob.hh),
                             (_ob.hw, _ob.hh), (-_ob.hw, _ob.hh)):
                _wx = _ob.cx + _lx * _o4 - _ly * _s4
                _wy = _ob.cy + _lx * _s4 + _ly * _o4
                _cc4 = v3_scan.game_to_cam(Hm, _wx, _wy)
                _obp.append([int(_cc4[0]), int(_cc4[1])])
            # ★★ 2026-10-29 用户定案：**确认放置即实心**（冻结，不再动）；
            #   未确认/可调整的才是黄色空框；PLAY 全部实心。
            _solid = _locked or _frozen_hit_obs(_ob)
            cv2.fillPoly(img, [np.array(_obp, np.int32)],
                         (0, 235, 255) if _solid else (40, 40, 40))
            cv2.polylines(img, [np.array(_obp, np.int32)], True,
                          (0, 235, 255), 3 if _solid else 2)
            _ocx = int(sum(p[0] for p in _abp) / 4.0)
            _ocy = int(sum(p[1] for p in _abp) / 4.0)
            # ★ 标签按**游戏坐标**放到场内一侧（靠上放下方），再映射到相机像素
            _lgy = (_ob.cy + _ob.hh + 30.0 if _ob.cy < 60.0
                    else _ob.cy - _ob.hh - 32.0)
            _lcc = v3_scan.game_to_cam(Hm, _ob.cx, _lgy)
            _live_tag(img, '障碍', int(_lcc[0]) - 18, int(_lcc[1]),
                      (0, 235, 255))
        # 传送门：**圆形触发区**（用户定案；半径 PORTAL_R，16 边形近似）。
        for _pt in (getattr(world, 'portals', None) or []):
            _pr5 = float(_vfp0.PORTAL_R)
            _cir = []
            for _k5 in range(16):
                _t5 = 2.0 * math.pi * _k5 / 16.0
                _cc6 = v3_scan.game_to_cam(Hm,
                                           _pt.x + _pr5 * math.cos(_t5),
                                           _pt.y + _pr5 * math.sin(_t5))
                _cir.append([int(_cc6[0]), int(_cc6[1])])
            cv2.polylines(img, [np.array(_cir, np.int32)], True, (0, 200, 0), 2)
            _pc5 = v3_scan.game_to_cam(Hm, _pt.x, _pt.y)
            cv2.circle(img, (int(_pc5[0]), int(_pc5[1])), 4, (0, 200, 0), -1)
            _lgy = (_pt.y + 35.0 if _pt.y < 60.0 else _pt.y - 45.0)
            _lcc = v3_scan.game_to_cam(Hm, _pt.x, _lgy)
            _live_tag(img, '传送门', int(_lcc[0]) - 27, int(_lcc[1]),
                      (0, 255, 0))
        # ★ 2026-10-19 上板：黑球标记（实物洞口）画在球的实际位置
        for _mk in (_PHYS.get('marks') or []):
            _mc = v3_scan.game_to_cam(Hm, _mk['gx'], _mk['gy'])
            _mmx, _mmy = int(_mc[0]), int(_mc[1])
            cv2.circle(img, (_mmx, _mmy), 14, (255, 0, 255), 2)
            cv2.drawMarker(img, (_mmx, _mmy), (255, 0, 255),
                           cv2.MARKER_CROSS, 10, 2)
            _lgy = (float(_mk['gy']) + 38.0
                    if float(_mk['gy']) < 70.0
                    else float(_mk['gy']) - 45.0)
            _lcc = v3_scan.game_to_cam(Hm, float(_mk['gx']), _lgy)
            _live_tag(img, '洞口标记', int(_lcc[0]) - 32, int(_lcc[1]),
                      (255, 0, 255))
    # 洞口：游戏坐标里洞是"边上的一段"（edge + 中心 c + 宽 w），折成像素里的一条线
    if world is not None:
        for h in world.holes:
            e, c, w2 = h['edge'], float(h['c']), float(h['w']) / 2.0
            if e == 'left':
                p1, p2 = (0.0, c - w2), (0.0, c + w2)
            elif e == 'right':
                p1, p2 = (v3_scan.FIELD_W, c - w2), (v3_scan.FIELD_W, c + w2)
            elif e == 'top':
                p1, p2 = (c - w2, 0.0), (c + w2, 0.0)
            else:                                   # bottom
                p1, p2 = (c - w2, v3_scan.FIELD_H), (c + w2, v3_scan.FIELD_H)
            _q1 = v3_scan.game_to_cam(Hm, p1[0], p1[1])
            _q2 = v3_scan.game_to_cam(Hm, p2[0], p2[1])
            cv2.line(img, (int(_q1[0]), int(_q1[1])), (int(_q2[0]), int(_q2[1])),
                     (0, 200, 0), 6)
        # ★★ 2026-10-07（用户："挪一小步我这的视角看不出来"）：洞口移动/每局随机的
        #   **醒目动画**（3 秒）：旧位灰色残影 + 新位黄亮框（0.5s 一发脉动）+
        #   旧->新 箭头 + 底部中文提示（见 HUD 段）。
        _hfm = time.time() - _HMOVE['t']
        if 0.0 <= _hfm <= _HMOVE_SECS and _HMOVE['items']:
            _blink = int(_hfm * 6.0) % 2 == 0
            for _e2, _oc, _nc in _HMOVE['items']:
                _w2f = 22.0
                if _e2 == 'left':
                    _g1, _g2 = (0.0, _oc - _w2f), (0.0, _oc + _w2f)
                    _n1, _n2 = (0.0, _nc - _w2f), (0.0, _nc + _w2f)
                elif _e2 == 'right':
                    _g1, _g2 = (v3_scan.FIELD_W, _oc - _w2f), (v3_scan.FIELD_W, _oc + _w2f)
                    _n1, _n2 = (v3_scan.FIELD_W, _nc - _w2f), (v3_scan.FIELD_W, _nc + _w2f)
                elif _e2 == 'top':
                    _g1, _g2 = (_oc - _w2f, 0.0), (_oc + _w2f, 0.0)
                    _n1, _n2 = (_nc - _w2f, 0.0), (_nc + _w2f, 0.0)
                else:
                    _g1, _g2 = (_oc - _w2f, v3_scan.FIELD_H), (_oc + _w2f, v3_scan.FIELD_H)
                    _n1, _n2 = (_nc - _w2f, v3_scan.FIELD_H), (_nc + _w2f, v3_scan.FIELD_H)
                _hg1 = v3_scan.game_to_cam(Hm, *_g1)
                _hg2 = v3_scan.game_to_cam(Hm, *_g2)
                _hn1 = v3_scan.game_to_cam(Hm, *_n1)
                _hn2 = v3_scan.game_to_cam(Hm, *_n2)
                cv2.line(img, (int(_hg1[0]), int(_hg1[1])),
                         (int(_hg2[0]), int(_hg2[1])), (70, 70, 70), 3)
                cv2.line(img, (int(_hn1[0]), int(_hn1[1])),
                         (int(_hn2[0]), int(_hn2[1])), (0, 230, 255),
                         9 if _blink else 5)
                _gm = ((_hg1[0] + _hg2[0]) / 2.0, (_hg1[1] + _hg2[1]) / 2.0)
                _nm = ((_hn1[0] + _hn2[0]) / 2.0, (_hn1[1] + _hn2[1]) / 2.0)
                cv2.arrowedLine(img, (int(_gm[0]), int(_gm[1])),
                                (int(_nm[0]), int(_nm[1])), (0, 230, 255), 3,
                                tipLength=0.22)
    # ★★ 2026-10-05 双人：**两条瞄准预览**（每颗球一条）——
    #   颜色 = 玩家色（P1 亮绿 / P2 亮紫，与网页两块面板同色）；虚线 + 各自快扇
    #   端点（该角度能进 = 玩家色亮点、打回 = 灰）。批量折算 + 一条 polylines 画
    #   全部虚线段（板上逐点/逐段吃不消，见 game_to_cam_batch 注释）。
    for _pv in (previews or []):
        _ppt = _pv.get('pts')
        if _ppt and len(_ppt) >= 2:
            _cs = v3_scan.game_to_cam_batch(Hm, _ppt)
            # ★★ 2026-10-19 实况：传送门瞬移在 pts 里是**大跳**（几 百像素）。
            #   画线必须在跳跃处断开——否则会画出一条"穿门而过"的直线，
            #   看起来像"碰到绿门没传送"（其实金模型已经瞬移）。
            _chs, _cur = [], [_cs[0]]
            for _q in _cs[1:]:
                _dxq = float(_q[0]) - float(_cur[-1][0])
                _dyq = float(_q[1]) - float(_cur[-1][1])
                if _dxq * _dxq + _dyq * _dyq > 3600.0:      # 60px
                    _chs.append(_cur)
                    _cur = [_q]
                else:
                    _cur.append(_q)
            _chs.append(_cur)
            for _ch in _chs:
                _segs = v3_scan._dash_segments(
                    [(float(p[0]), float(p[1])) for p in _ch])
                if _segs:
                    cv2.polylines(img,
                                  np.round(np.asarray(_segs, np.float64)).astype(np.int32),
                                  False, _pv['color'], 3)
        _pfan = _pv.get('fan')
        if _pfan:
            _fcs = v3_scan.game_to_cam_batch(Hm, [(p[0], p[1]) for p in _pfan])
            for _k3, (_fax, _fay, _fok) in enumerate(_pfan):
                _fa = (int(_fcs[_k3][0]), int(_fcs[_k3][1]))
                if _fok:
                    cv2.circle(img, _fa, 6, _pv['color'], -1)     # 亮色：能进洞
                else:
                    cv2.circle(img, _fa, 4, (150, 150, 150), -1)  # 灰：打回/没进
    # ★★ 2026-10-05（规则订正）：**真球**（发射后留场、弹到出射）——拖尾 + 球体 +
    #   闪光圈。坐标同为 game 坐标、同一套折算；位置存 Q10.8（/256 转浮点）。
    _nowr = time.time()
    for _lb in (live_balls or []):
        _tr = _lb.trail
        if len(_tr) >= 2:
            # ★ 批量折算 + 一条折线一次画（逐点/逐段在板上吃不消，见 game_to_cam_batch 注释）
            _tc = np.round(v3_scan.game_to_cam_batch(Hm, _tr)).astype(np.int32)
            _bcol = _charge_color(getattr(_lb, 'charge', 0), _lb.alive)
            cv2.polylines(img, [_tc], False, _bcol, 3)
        if _lb.alive:
            _bp = v3_scan.game_to_cam(Hm, _lb.x / 256.0, _lb.y / 256.0)
            _bp2 = v3_scan.game_to_cam(Hm, _lb.x / 256.0 + 11.0, _lb.y / 256.0)
            _brad = max(6, int(((_bp2[0]-_bp[0]) ** 2
                                + (_bp2[1]-_bp[1]) ** 2) ** 0.5))
            if _SM.get('on') and world is not None:
                # ★ 2026-10-31（SMOOTH=1）：球头交给高频线程画。
                #   ★★ 不能只做线性外推（帧 400ms，外推会过冲/滞后）——**用引擎
                #   把副本步进到未来 400ms**（物理确定性：连反弹/进洞都准），
                #   生成航点给线程按时间插值。
                _bv = v3_scan.game_to_cam(Hm, _lb.x / 256.0 + _lb.vx / 256.0,
                                          _lb.y / 256.0 + _lb.vy / 256.0)
                _seq = []
                try:
                    import copy as _cpy
                    import realtime_ball as _rtb2
                    _c = _cpy.copy(_lb)
                    _tf = _nowr
                    for _k in range(9):
                        if _k:
                            _rtb2.advance(
                                [_c], _flds_w, list(world.holes), 12, side=0,
                                obstacles=_obst_tuples(
                                    getattr(world, 'obstacles', None)),
                                portals=_portal_tuples(
                                    getattr(world, 'portals', None)))
                            if not _c.alive:
                                break
                        _tf += 12 / 240.0
                        _cc = v3_scan.game_to_cam(Hm, _c.x / 256.0,
                                                  _c.y / 256.0)
                        _seq.append((_tf, float(_cc[0]), float(_cc[1])))
                except Exception:
                    _seq = []
                _te = None
                try:
                    if _tr:
                        _tp = v3_scan.game_to_cam(Hm, _tr[-1][0], _tr[-1][1])
                        _te = (float(_tp[0]), float(_tp[1]))
                except Exception:
                    _te = None
                _SM['balls'].append(
                    {'cx': float(_bp[0]), 'cy': float(_bp[1]),
                     'vx': float(_bv[0] - _bp[0]),
                     'vy': float(_bv[1] - _bp[1]),
                     'r': int(_brad),
                     'col': tuple(int(_c2) for _c2 in _bcol),
                     't': _nowr, 'seq': _seq, 'te': _te})
            else:
                cv2.circle(img, (int(_bp[0]), int(_bp[1])), _brad, _bcol, -1)
                cv2.circle(img, (int(_bp[0]), int(_bp[1])), _brad, (40, 40, 40), 2)
    # 出射/碰撞的闪光圈（各留 4s / 2s；顺手清过期的，列表不许长）
    _EXITS[:] = [e for e in _EXITS if _nowr - e[0] < 10.0]
    for (_et, _ex, _ey) in _EXITS:
        if _nowr - _et < 4.0:
            _ep = v3_scan.game_to_cam(Hm, _ex, _ey)
            cv2.circle(img, (int(_ep[0]), int(_ep[1])), 22, (0, 200, 0), 4)
    _COLLS[:] = [c for c in _COLLS if _nowr - c[0] < 10.0]
    for (_ct, _cx, _cy) in _COLLS:
        if _nowr - _ct < 2.0:
            _cp = v3_scan.game_to_cam(Hm, _cx, _cy)
            # ★ 2026-10-05 用户反馈"碰撞框有时像紫色"：那不是变紫——是碰撞**那一瞬间**
            #   两颗品红球还压在圈里（r=26 刚好卡在球边上，看着像"紫球+红边"）。
            #   放大到 r=34、笔画 5px：球一进场就是"红圈兜住紫球"，一眼分明。
            cv2.circle(img, (int(_cp[0]), int(_cp[1])), 34, (0, 0, 255), 5)
    # ★ 2026-10-06：异号湮灭闪光（白圈+紫芯，2s）——正负电子相消的现场证据
    _ANNIS[:] = [x for x in _ANNIS if _nowr - x[0] < 10.0]
    for (_at2, _ax2, _ay2) in _ANNIS:
        if _nowr - _at2 < 2.0:
            _ap2 = v3_scan.game_to_cam(Hm, _ax2, _ay2)
            cv2.circle(img, (int(_ap2[0]), int(_ap2[1])), 30, (255, 255, 255), 4)
            cv2.circle(img, (int(_ap2[0]), int(_ap2[1])), 14, (255, 0, 255), 2)
    # 历史轨迹（相机像素）——老规矩：后端已经限长（trail_len 个点），这里不裁剪
    # ★ 2026-10-23：跳变处断开（同"传送门断线"的做法），避免假目标把轨迹拉满屏
    # ★ 2026-10-23（差距 2）：逐段 cv2.line（最多 59 次调用）改成**按断点分块 +
    #   一次 polylines** —— 画出来的线一样（同色同粗），A9 上少几十次调用。
    if len(trail) >= 2:
        _tpp = [(int(_q[0]), int(_q[1])) for _q in trail]
        _tch, _tcur = [], [_tpp[0]]
        for _q in _tpp[1:]:
            _tdx = _q[0] - _tcur[-1][0]
            _tdy = _q[1] - _tcur[-1][1]
            if _tdx * _tdx + _tdy * _tdy > 150.0 * 150.0:
                _tch.append(_tcur)
                _tcur = [_q]
            else:
                _tcur.append(_q)
        _tch.append(_tcur)
        for _ch in _tch:
            if len(_ch) >= 2:
                cv2.polylines(img, [np.asarray(_ch, np.int32)], False,
                              (0, 255, 0), 2)
    # ★★ 2026-10-05 夜（用户反馈"其他球没有存在感"）：**多目标足迹，要一眼看见**——
    #   亮色大圈（画在球边**外面** 6px，不贴在球身上）+ 每颗一个**编号** + 各自拖尾。
    #   红球→亮红圈、蓝球→亮蓝圈、未知色→亮青圈；编号带深色描边保证任何底色都读得出。
    for _tk in tracks or []:
        # ★ 2026-10-23：只画**已确认发号**的球——手影/新目标确认前不画圈/不画拖尾
        if _track_num_label(_tk) is None:
            continue
        if _tk.cls == 'red':
            _tkcol = (0, 0, 255)
        elif _tk.cls == 'blue':
            _tkcol = (255, 0, 0)
        else:
            _tkcol = (0, 200, 200)
        # ★ 2026-10-23：未确认（手/新目标）不画拖尾；已确认的也在跳变处断开
        if _track_num_label(_tk) is not None and len(_tk.trail) >= 2:
            _tp = [(int(_q[0]), int(_q[1])) for _q in _tk.trail]
            _chunks = []; _cur = [_tp[0]]
            for _q in _tp[1:]:
                _qdx = _q[0] - _cur[-1][0]; _qdy = _q[1] - _cur[-1][1]
                if _qdx * _qdx + _qdy * _qdy > 150.0 * 150.0:
                    _chunks.append(_cur); _cur = [_q]
                else:
                    _cur.append(_q)
            _chunks.append(_cur)
            for _ch in _chunks:
                if len(_ch) >= 2:
                    cv2.polylines(img, [np.asarray(_ch, np.int32)], False,
                                  (int(_tkcol[0]*0.5), int(_tkcol[1]*0.5),
                                   int(_tkcol[2]*0.5)), 2)
        _tkc = (int(_tk.sx), int(_tk.sy))       # ★ 平滑位：圈不再随识别框呼吸
        cv2.circle(img, _tkc, int(_tk.sr) + 6, _tkcol, 3)
        _tkl = (_tkc[0] + int(_tk.sr) + 8, _tkc[1] - int(_tk.sr) - 4)
        # ★★ 2026-10-05 夜修：画的必须是与发射同尺的 t.no（不是列表序号 ——
        #   详见 _track_num_label 的注释）；未确认(None)不画（灰/青圈即"新生"）。
        _tnum = _track_num_label(_tk)
        if _tnum is not None:
            # ★ 2026-10-07（用户："P1 跳出屏幕外"）：标签位置一律**夹进画面**——
            #   球贴边时翻转/贴边，绝不出框（编号标签同理防一手）。
            _nw, _nh = img.shape[1], img.shape[0]
            _nx = _tkl[0]
            if _nx > _nw - 34:
                _nx = _tkc[0] - int(_tk.sr) - 34        # 右侧放不下 -> 翻到左侧
            _nx = max(4, min(_nw - 34, _nx))
            _ny = max(22, min(_nh - 8, _tkl[1]))
            cv2.putText(img, '%d' % _tnum, (_nx, _ny),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 4)
            cv2.putText(img, '%d' % _tnum, (_nx, _ny),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, _tkcol, 2)
        # ★★ 2026-10-07（用户："选中了哪颗球没有明确提示、经常认错"——手机上有
        #   高亮，屏上没有）：把**当前控球**在场面上圈出来——玩家色外环
        #   （P1 亮绿 / P2 亮紫，与各自轨迹/网页同色）+ 圈旁 "P1"/"P2" 小标；
        #   0.5s 脉动加粗（静止时也一眼看得出"它在用哪颗"）。两人选同一颗 -> 双环。
        #   ★ 用户实况："P1 跳出屏幕外"——标签位置**先左后右翻转 + 两轴夹进画面**，
        #     球贴任何一条边，字样都必须可见。
        _sel = []
        if bno1 and _tk.no == bno1:
            _sel.append(((0, 230, 0), 'P1'))
        if bno2 and _tk.no == bno2:
            _sel.append(((255, 60, 255), 'P2'))
        if _sel:
            _pls = int(time.time() * 2.0) % 2 == 0
            _W2, _H2 = img.shape[1], img.shape[0]
            for _si, (_scol, _stag) in enumerate(_sel):
                cv2.circle(img, _tkc, int(_tk.sr) + 14 + _si * 7, _scol,
                           5 if _pls else 3)
                _tlx = _tkc[0] - int(_tk.sr) - 46 - _si * 30
                if _tlx < 4:
                    _tlx = _tkc[0] + int(_tk.sr) + 12 + _si * 30
                _tlx = max(4, min(_W2 - 44, _tlx))
                _tly = max(24, min(_H2 - 8, _tkc[1] + 6))
                cv2.putText(img, _stag, (_tlx, _tly),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (30, 30, 30), 5)
                cv2.putText(img, _stag, (_tlx, _tly),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, _scol, 2)
    if ball is not None and angle2 is None:
        # ★★ 2026-10-07（用户实况："屏幕跳动、连球轨迹都跟着变"实锤）：黄圈 = 单球
        #   时代的"主球"（离边最近的 confirmed 轨迹），多球场景里它会在**两颗同色球
        #   之间来回翻面**（日志指纹 Δ=±233,168，每十来秒一次）——屏上就是个乱跳的
        #   圈，还容易被当成"轨迹坏了"。**fire 模式（输入层开着）一律不画**：
        #   编号圈 + P1/P2 高亮已经把"谁在哪、选哪颗"说全了，这个黄圈只会添乱。
        cv2.circle(img, (int(ball['x']), int(ball['y'])),
                   max(4, int(ball['r'])), (0, 255, 255), 2)
    # ---- HUD（★ 2026-10-07 用户三点：文字别压场地 / 改中文 / 简短）------------
    #   全部走**中文贴片**路线（_zh_hud）：PIL 渲成小图**按字符串缓存**、没变不
    #   重渲；每帧只做 numpy 赋值粘贴（µs 级）。没 PIL/字体自动退 ASCII
    #   （cv2.putText，非 ASCII 变 '?'——与 v3_scan.put_texts 同一条纪律）。
    #   布局（1024x720 实拍图上，场区约 y=78~505）：**顶部一条 + 底部三条**，
    #   全部落在场区之外；文案尽量短（详细状态看手机 /match）。
    _Hpx = img.shape[0]
    _hud = [(_hud_top_line(angle, speed, bno1, angle2, speed2, bno2),
             (8, 8), 22, (0, 255, 255))]
    if live_balls:
        _nalive = sum(1 for _lb in live_balls if _lb.alive)
        _hud.append(('在飞 %d · 已发 %d · 出射 %d'
                     % (_nalive, _LIVE_ST['n_fired'], _LIVE_ST['n_exit']),
                     (8, _Hpx - 128), 18, (255, 80, 220)))
    if _PEB['on']:
        _npe = sum(1 for _sh in _PEB['balls'] if _sh.alive)
        _mspe = (sum(_PEB['ms'][-20:]) / len(_PEB['ms'][-20:])
                 if _PEB['ms'] else 0.0)
        _hud.append(('PL 硬件粒子 %d 在飞 · 物理 %.1fms/帧 · 湮灭%d 出射%d'
                     % (_npe, _mspe, _PEB['n_annih'], _PEB['n_goal']),
                     (8, _Hpx - 106), 18, (120, 220, 120)))
    if time.time() - _HMOVE['t'] <= _HMOVE_SECS and _HMOVE['items']:
        _hud.append((_HUD_HOLE_FLASH, (8, _Hpx - 170), 22, (0, 230, 255)))
    _hud.append((_HUD_FIRE_HINT, (8, _Hpx - 88), 20, (255, 0, 255)))
    _hud.append((_HUD_HOLE_HINT, (8, _Hpx - 66), 20, (190, 190, 190)))
    if match_line:
        _hud.append((match_line, (8, _Hpx - 46), 24, (90, 255, 150)))
    if hud:
        _zh_hud(img, _hud)
        _item_banner(img)                   # ★ 道具应答横幅（2.6s；见 _ITEM_MSG）
    return img


_CROP_WARN = {'w': False}


def _charge_color(chg, alive=True):
    """★ 2026-10-25：粒子轨迹/球体按**电荷**着色，不再统一品红。

    红球 +1 -> 红，蓝球 -1 -> 蓝，未知/0 -> 青；死亡/残影整体压暗。
    （轨迹是玩家看“这是什么粒子”的唯一线索，红蓝必须分开。）
    """
    chg = int(chg or 0)
    if chg > 0:
        c = (0, 0, 255)          # BGR 红
    elif chg < 0:
        c = (255, 0, 0)          # BGR 蓝
    else:
        c = (0, 200, 200)        # 未知：青
    if not alive:
        c = (int(c[0] * 0.6), int(c[1] * 0.6), int(c[2] * 0.6))
    return c


def _live_crop_rect(W, H, Hm, margin=0.06):
    """场区裁剪矩形（与 _live_crop_image 同口径）：(x0i,y0i,x1i,y1i) 或 None。

    ★ 2026-10-27：布置态鼠标点选要把 1280x720 的点击反算回相机像素，
      必须和显示用**同一份**裁剪几何（同一 Hm/margin），否则点哪选哪会偏。
    """
    try:
        import v3_scan
        pts = [v3_scan.game_to_cam(Hm, 0.0, 0.0),
               v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, 0.0),
               v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, v3_scan.FIELD_H),
               v3_scan.game_to_cam(Hm, 0.0, v3_scan.FIELD_H)]
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        w, h = x1 - x0, y1 - y0
        if w <= 8.0 or h <= 8.0:
            return None
        x0 -= w * margin
        x1 += w * margin
        y0 -= h * margin
        y1 += h * margin
        # ★★ 2026-10-30 用户实况（"场区橙色边框不在屏幕正中"）：原来用 bbox
        #   中心再夹回画幅，相机没拍全场区时（四角 -26..1190 / -89..574）
        #   场区被挤到左上。改成**四角平均质心**当中心、**不再夹回画幅**；
        #   超出相机的部分由 _live_crop_image 补黑边，场区永远居中。
        cx, cy = sum(xs) / 4.0, sum(ys) / 4.0
        w, h = x1 - x0, y1 - y0
        target = 16.0 / 9.0
        if w / h < target:
            w = h * target
        else:
            h = w / target
        if w > W:
            w, h = float(W), float(W) / target
        if h > H:
            h, w = float(H), float(H) * target
        # ★★ 2026-10-30 用户实况（"场区放大过头，四周要留空间"）：允许裁剪框
        #   超出相机画幅——超出部分由 `_live_crop_image` 用桌面色延展补齐；
        #   场区四周留出 margin（默认 10%）的空间，不再放大裁切到贴边。
        x0 = cx - w * 0.5
        y0 = cy - h * 0.5
        x0i, y0i = int(round(x0)), int(round(y0))
        x1i = min(W, int(round(x0 + w)))
        y1i = min(H, int(round(y0 + h)))
        if x1i <= x0i or y1i <= y0i:
            return None
        return x0i, y0i, x1i, y1i
    except Exception:
        return None


_LIVE_WARP = {'key': None, 'M': None, 'inv': None}
# ★ 2026-10-30 用户定案：左右留白保持小（约 margin），**上下再多 1.2%**
#   （用户："上下留白再大一点点"）；内框不是严格 16:9，但仍是正长方形，
#   场区画面纵向轻微拉伸 ~2%，肉眼基本无感。
FRAME_VPAD = 0.012


def _live_warp_matrix(W, H, Hm, margin=0.03):
    """★ 2026-10-30 用户定案：把场区**透视四边形矫正成屏幕内 16:9 矩形**。

    输出 1280x720；内框四周留 `margin`（比例）的小留白（约 3% → 左右 38px
    / 上下 22px）。场区内容（球/障碍/洞口/橙色框）一起矫正，框是正长方形。
    返回 raw->display 的单应矩阵；`_LIVE_WARP['inv']` 是 display->raw 的反变换
    （布置态点击反算用）。"""
    try:
        import cv2
        import numpy as np
        import v3_scan
        _mx = float(margin) * 1280.0
        _my = (float(margin) + FRAME_VPAD) * 720.0
        _dst = np.array([[_mx, _my],
                         [1280.0 - _mx, _my],
                         [1280.0 - _mx, 720.0 - _my],
                         [_mx, 720.0 - _my]], np.float32)
        _src = np.array([
            list(v3_scan.game_to_cam(Hm, 0.0, 0.0))[:2],
            list(v3_scan.game_to_cam(Hm, v3_scan.FIELD_W, 0.0))[:2],
            list(v3_scan.game_to_cam(Hm, v3_scan.FIELD_W,
                                     v3_scan.FIELD_H))[:2],
            list(v3_scan.game_to_cam(Hm, 0.0, v3_scan.FIELD_H))[:2]],
            np.float32)
        _M = cv2.getPerspectiveTransform(_src, _dst)
        _LIVE_WARP.update({'key': (int(W), int(H), round(float(margin), 4),
                                   id(Hm)),
                           'M': _M, 'inv': np.linalg.inv(_M)})
        return _M
    except Exception:
        return None


# ★★ 2026-10-31（用户："还是一跳一跳的"）：profile 实锤 cv2.warpPerspective
#   每帧 **101.7ms**，占"叠加 108ms"的九成。改成**预计算映射表 + cv2.remap**
#   （板上实测 54.4ms，1.87×；两种做法只有 21/921600 个像素不同 = 视觉无差）。
#   映射表只在 (尺寸/留白/标定对象) 变化时重建（一次 ~280ms）。
_LIVE_WARP_MAP = {'key': None, 'm1': None, 'm2': None}


def _live_warp_maps(M, key):
    """由单应 M 生成 remap 用的反向映射表（缓存）。"""
    import cv2
    import numpy as np
    c = _LIVE_WARP_MAP
    if c['key'] == key and c['m1'] is not None:
        return c['m1'], c['m2']
    _Minv = np.linalg.inv(M)
    xs, ys = np.meshgrid(np.arange(1280, dtype=np.float32),
                         np.arange(720, dtype=np.float32))
    pts = np.stack([xs, ys], -1).reshape(-1, 1, 2)
    back = cv2.perspectiveTransform(pts, _Minv).reshape(720, 1280, 2)
    c['m1'] = np.ascontiguousarray(back[..., 0])
    c['m2'] = np.ascontiguousarray(back[..., 1])
    c['key'] = key
    return c['m1'], c['m2']


def _live_crop_image(img, Hm, margin=0.03, interp='nearest', holes=None):
    """★ 2026-10-30：场区透视矫正到屏幕内 16:9 内框（小留白），输出 1280x720。

    所有叠加（场区/球/轨迹/障碍/新件候选）已在原图上画好，随透视变换一起走；
    变换后在内框上重画一圈抗锯齿橙色框。Hm=None/失败 -> 原图。
    """
    if img is None or Hm is None:
        return img
    try:
        import cv2
        H, W = img.shape[:2]
        _M = _live_warp_matrix(W, H, Hm, margin)
        if _M is None:
            return img
        if _SM.get('on'):
            _SM['M'] = _M          # ★ 平滑线程要用同一份变换做 相机->屏幕
        itp = cv2.INTER_NEAREST if interp == 'nearest' else cv2.INTER_LINEAR
        # ★ 2026-10-31：warpPerspective -> 预计算 map + remap（板上 101.7->54.4ms）
        #   缓存键**按矩阵数值**算（不用 id(Hm)——对象每帧换掉就会每帧重建表，
        #   实测那样反而变成 100ms = 54 remap + 50 建表，白忙）
        _k = (int(W), int(H), round(float(margin), 4), str(interp),
              hash(np.asarray(_M, np.float64).tobytes()))
        _m1, _m2 = _live_warp_maps(_M, _k)
        _out = cv2.remap(img, _m1, _m2, itp,
                         borderMode=cv2.BORDER_REPLICATE)
        _mx = int(round(float(margin) * 1280.0))
        _my = int(round((float(margin) + FRAME_VPAD) * 720.0))
        cv2.rectangle(_out, (_mx, _my), (1280 - _mx, 720 - _my),
                      (0, 165, 255), 3, cv2.LINE_AA)
        # ★★ 2026-10-30 用户实况（"洞口绿线需要覆盖橙框"）：橙框在变换后最后
        #   才画，会把洞口绿线盖住；这里按同一份变换把绿洞线**重画到最上层**。
        if holes:
            try:
                import v3_scan
                _hw = []
                for _h in holes:
                    _ed = str(_h.get('edge') or '')
                    _c = float(_h.get('c') or 0.0)
                    _rad = max(4.0, float(_h.get('w') or 44.0) / 2.0)
                    if _ed == 'left':
                        _hw = [(0.0, _c - _rad), (0.0, _c + _rad)]
                    elif _ed == 'right':
                        _hw = [(960.0, _c - _rad), (960.0, _c + _rad)]
                    elif _ed == 'top':
                        _hw = [(_c - _rad, 0.0), (_c + _rad, 0.0)]
                    else:
                        _hw = [(_c - _rad, 540.0), (_c + _rad, 540.0)]
                    _ps = []
                    for _gx, _gy in _hw:
                        _cc = v3_scan.game_to_cam(Hm, _gx, _gy)
                        _p = cv2.perspectiveTransform(
                            np.array([[[float(_cc[0]), float(_cc[1])]]],
                                     np.float64), _M)[0][0]
                        _ps.append((int(round(float(_p[0]))),
                                    int(round(float(_p[1])))))
                    cv2.line(_out, _ps[0], _ps[1], (0, 230, 0), 8,
                             cv2.LINE_AA)
            except Exception:
                pass
        return _out
    except Exception as e:
        if not _CROP_WARN['w']:
            _CROP_WARN['w'] = True
            print('[裁剪] ★ 场区透视矫正失败（退回原图）：%s' % str(e)[:120],
                  flush=True)
        return img


def _rescue_due(now, t_fresh, st):
    """主循环救援的**触发判据**：真卡（帧计数 stale ≥2.5s）+ 限频（≥12s）→ True。

    ★ 抽出来就是为了能离线喂时间线（台架 `rescue_ladder` 场景）。两把尺的来历
      （2026-10-01 fire_run5，用户："一卡一卡"）：
      · **2.5s**：旧值 1.2s 会把慢性退化链路上 1~3s 的**自恢复**抽风也当成"真卡"，
        每抽一下就按一次（run5 里 15 次脉冲 + 11 次断电重上电，每按一次屏幕冻
        几秒 —— 救援自己成了卡顿源）；
      · **12s**：旧值 5s 会让救援连环上 —— 上一次的动作（尤其升级重上电，传感器
        要 ~1s 出流）还没落定就被下一次盖掉，救不了还制造新停顿。
    动作与阶梯状态机在 `_rescue_escalate`（由它更新 st['t_last']）。
    """
    return now - t_fresh > 2.5 and now - st['t_last'] > 12.0


def _rescue_escalate(mm3):
    """主循环里的救援阶梯：先脉冲 ctrl[3]，30s 窗口内卡到**第 3 回**才升级到
    传感器断电重上电。

    ★ 2026-10-01（fire_run5）：升级门槛从第 2 回抬到第 3 回、升级后重起窗口 ——
    旧逻辑在慢性退化链路上每 10 秒就来一轮"脉冲→断电"，每次冻几秒，救不了还
    制造新停顿（现场"一卡一卡"的元凶之一）。
    与驱动里那个看门狗线程**互补**：线程可能被卡住的 AXI 读阻塞（实测 29 秒才醒），
    而主循环永远在跑 —— 谁先醒谁救。限频在调用处（>=12s 一次）。
    """
    import time as _t
    now = _t.time()
    _RESCUE['t_last'] = now
    if now - _RESCUE['t_win'] > 30.0:
        _RESCUE['t_win'] = now
        _RESCUE['in_win'] = 0
    _RESCUE['in_win'] += 1
    v = mm3.read(0x00)
    # ★ try/finally（2026-09-27 血泪）：救援序列中途被打断（进程结束/被杀）会把
    #   ctrl 留在 `v|0x03`（= 传感器断电 + 停 xclk）那一步 —— 之后所有"链路死了"
    #   都是自己造成的。finally 保证**无论怎么退出都还原**。
    try:
        if _RESCUE['in_win'] >= 3:
            # 升级：传感器断电重上电（照抄 stall_probe.pulse_power_cycle 的序列）
            mm3.write(0x00, v | 0x03)                # pwdn + 停 xclk
            _t.sleep(0.5)
            mm3.write(0x00, v & ~0x03)
            _t.sleep(0.2)
            mm3.write(0x00, (v & ~0x03) | (1 << 4))  # force_sensor_rst
            _t.sleep(0.3)
            _RESCUE['in_win'] = 0
            _RESCUE['t_win'] = _t.time()             # 升级后重新起算 30s 窗口
            _RESCUE['n_pwr'] += 1
            print('[自愈] 主循环：30s 内又卡 -> 升级【传感器断电重上电】(第 %d 次)'
                  % (_RESCUE['n_pwr'],), flush=True)
        else:
            mm3.write(0x00, v | (1 << 3))
            _t.sleep(0.30)
            _RESCUE['n_pulse'] += 1
            print('[自愈] 主循环：卡了 -> 脉冲 ctrl[3]（第 %d 次；本 30s 窗口内第 %d 回）'
                  % (_RESCUE['n_pulse'], _RESCUE['in_win']), flush=True)
    finally:
        mm3.write(0x00, v & ~0x03)                   # 还原：传感器上电 + xclk 开
        _t.sleep(0.4)


def _grab(cap, tries=1):
    """抓一张原图；失败时按老办法救一次（ctrl[4] + 重跑初始化）。"""
    from pynq import MMIO
    from ball_detect import _init_video
    import numpy as _np
    for k in range(tries + 1):
        try:
            idx, raw = cap.frame(timeout=3.0)
            return _np.ascontiguousarray(_np.asarray(raw, _np.uint8)).copy(), idx
        except Exception:
            if k == tries:
                raise
            mm3 = MMIO(0x43A30000, 0x10000)
            cur = mm3.read(0x00)
            mm3.write(0x00, cur | (1 << 4))
            time.sleep(0.3)
            mm3.write(0x00, cur & ~(1 << 4))
            time.sleep(0.5)
            _init_video(cap)


def _build_edit_view(w, h, m, mfmod, world, balls):
    """改磁场俯视图（合成画布；与加洞口同一套布局）。命中框写回 edit[rects]。"""
    img = np.full((h, w, 3), (26, 22, 18), np.uint8)
    _ox, _oy = (w - 960) // 2, (h - 540) // 2
    _paint_top_scene(img, _ox, _oy, world, balls)
    _ed = m.edit
    _idx = int(_ed.get('idx', 0))
    _blks = list(getattr(world, 'blocks', None) or [])
    _stage = str(_ed.get('stage') or 'SELECT')
    # 选中的块按阶段换色：黄=选块 / 橙=方向 / 绿=数值（当前步骤醒目）
    _sc = {'SELECT': (0, 235, 255), 'DIR': (255, 190, 70),
           'VALUE': (110, 235, 120)}.get(_stage, (0, 235, 255))
    if 0 <= _idx < len(_blks):
        _b = _blks[_idx]
        _r0 = (int(_ox + _b.x0), int(_oy + _b.y0))
        _r1 = (int(_ox + _b.x1), int(_oy + _b.y1))
        _ov = img.copy()
        cv2.rectangle(_ov, _r0, _r1, _sc, -1)
        cv2.addWeighted(_ov, 0.18, img, 0.82, 0, img)
        cv2.rectangle(img, _r0, _r1, _sc, 4)
    # ★★ 2026-10-29 用户实况（"按 W/S 块还是蓝色，没参与感"）：方向/数值
    #   阶段把选中块**按待生效方向上色**——+ 出屏=蓝、- 入屏=红（与场地同色
    #   语言），W/S 一按颜色立刻翻；并盖上大号「+ 出屏 / - 入屏」和预览 B。
    if _stage in ('DIR', 'VALUE') and 0 <= _idx < len(_blks):
        _b = _blks[_idx]
        _sg = 1 if int(_ed.get('sign', 1)) >= 0 else -1
        _r0 = (int(_ox + _b.x0), int(_oy + _b.y0))
        _r1 = (int(_ox + _b.x1), int(_oy + _b.y1))
        _pt = (76, 56, 36) if _sg > 0 else (36, 44, 84)
        _pc = (255, 150, 40) if _sg > 0 else (40, 50, 255)
        cv2.rectangle(img, _r0, _r1, _pt, -1)
        cv2.rectangle(img, _r0, _r1, _pc, 4)
        _pcx, _pcy = (_r0[0] + _r1[0]) // 2, (_r0[1] + _r1[1]) // 2
        _pv = abs(float(_b.b))
        if _stage == 'VALUE':
            try:
                _tx = str(_ed.get('txt') or '')
                if _tx:
                    _pv = max(B_EDIT_MIN, min(B_EDIT_MAX, float(_tx)))
            except Exception:
                pass
        cv2.putText(img, 'B%+.2f' % (_sg * _pv),
                    (_pcx - 40, _pcy - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        _zh_hud(img, [('+ 出屏' if _sg > 0 else '- 入屏',
                       (_pcx - 30, _pcy + 10), 30, _pc)])
    _who = '选手一' if int(_ed.get('player', 1)) == 1 else '选手二'
    _left = max(0.0, float(mfmod.PAUSE_MAX) - float(_ed.get('t', 0.0)))
    _sign = '+' if int(_ed.get('sign', 1)) >= 0 else '-'
    _txt = str(_ed.get('txt') or '')
    _step = {'SELECT': 1, 'DIR': 2, 'VALUE': 3}.get(_stage, 1)
    _ck = 'E' if int(_ed.get('player', 1)) == 1 else '小键盘 *'
    _bw, _bh = min(w - 80, 1000), 156
    _bx, _by = (w - _bw) // 2, h - _bh - 20
    cv2.rectangle(img, (_bx, _by), (_bx + _bw, _by + _bh), (18, 18, 18), -1)
    cv2.rectangle(img, (_bx, _by), (_bx + _bw, _by + _bh), _sc, 2)
    _zh_hud(img, [('改磁场 · %s · 第%d/3步 · 块%d/%d · 剩%.0f秒'
                   % (_who, _step, _idx + 1, len(_blks), _left),
                   (_bx + 14, _by + 8), 24, (86, 226, 255))])
    if _stage == 'SELECT':
        _l1 = '第1步 选块：A/D（P2 ←/→）或鼠标点块'
        _l2 = '按 %s / 点「确认选定」锁定这块（ESC 取消）' % _ck
    elif _stage == 'DIR':
        _l1 = '第2步 方向：W/S（P2 ↑/↓）—— 当前 %s' % _sign
        _l2 = '按 %s / 点「确认方向」锁定（ESC 回选块）' % _ck
    else:
        _l1 = '第3步 数值：%s（0.10~0.80，退格删）' % (_txt or '_')
        _l2 = '按 %s / 点「确认修改」应用（ESC 回方向）' % _ck
    _zh_hud(img, [(_l1, (_bx + 14, _by + 42), 23, (230, 230, 230)),
                  (_l2, (_bx + 14, _by + 84), 20, (150, 150, 150))])
    _okr = (_bx + _bw - 370, _by + 12, _bx + _bw - 160, _by + _bh - 12)
    _nor = (_bx + _bw - 145, _by + 12, _bx + _bw - 12, _by + _bh - 12)
    _bname = {'SELECT': '确认选定', 'DIR': '确认方向',
              'VALUE': '确认修改'}.get(_stage, '确认')
    for _r, _t, _c in ((_okr, _bname, (60, 210, 90)),
                       (_nor, '取消', (200, 200, 200))):
        cv2.rectangle(img, (_r[0], _r[1]), (_r[2], _r[3]), _c, 2)
        _zh_hud(img, [(_t, (_r[0] + 16, _r[1] + 18), 28, _c)])
    _ed['rects'] = {'ok': _okr, 'no': _nor}
    return img


def _edit_mouse_hit(m, hub, w, h, world):
    """改磁场界面鼠标：点块=选块；点确认/取消=记一笔动作（主循环执行）。"""
    if m is None or m.phase != 'EDIT' or hub is None:
        return False
    try:
        mx, my = int(hub.mx), int(hub.my)
    except Exception:
        return False
    _r = (m.edit or {}).get('rects') or {}
    for _k in ('ok', 'no'):
        _b = _r.get(_k)
        if _b and _b[0] <= mx <= _b[2] and _b[1] <= my <= _b[3]:
            _EDC['mouse_act'] = _k
            return True
    # ★ 2026-10-29 三步确认：任何阶段点块 = 回到选块阶段（需重新确认）
    if m.edit.get('stage') not in ('SELECT', 'DIR', 'VALUE'):
        return True
    _ox = (int(w) - 960) // 2
    _oy = (int(h) - 540) // 2
    _gx, _gy = mx - _ox, my - _oy
    for _i, _b in enumerate(getattr(world, 'blocks', None) or []):
        if _b.x0 <= _gx <= _b.x1 and _b.y0 <= _gy <= _b.y1:
            if m.edit.get('stage') != 'SELECT':
                m.edit['stage'] = 'SELECT'
                m.edit['txt'] = ''
                print('[道具] P%d 鼠标点块 -> 回到选块（需重新确认）'
                      % int(m.edit['player']), flush=True)
            m.edit_pick_block(_i, len(world.blocks))
            print('[道具] P%d 鼠标选块 -> %d（B=%+.2f）'
                  % (int(m.edit['player']), _i + 1, float(_b.b)),
                  flush=True)
            break
    return True


if __name__ == '__main__':
    sys.exit(main())


