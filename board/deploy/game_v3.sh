#!/bin/bash
# game_v3.sh —— 板上"游戏循环 + V3 实时预测"（★ P1）一条命令跑。
#
#   第 1 步：pl_probe 体检（下 bit 的铁律：盲目下 bit = PL 半编程 = MMIO 读挂死总线）
#   第 2 步：ball_game.py 跑相机游戏循环，**每 K 个有效帧**拿当前球位喂一次 V3，
#            当场画出那一簇 17 路的判定（绿出射/红打回/橙临界），覆盖写 v3_live.png
#   第 3 步：提示把图 + 长跑汇总 scp 回 PC
#
# ★ 为什么要它（P1，2026-09-28 深夜定妆照之后）：V3 原来只活在 v3_scan.py 的出图脚本里
#   —— 45 倍那行字是**印在静态图上**的，观众看不到它在动。游戏循环里原来一句预测都没有。
#   接进来之后，"PL 12ms 算完一簇"变成屏幕上每秒刷新两三次的事实。
#
# 用法（板上）：./game_v3.sh                       # 默认 60 秒、每 6 帧预测一次
#   SEC=120 ./game_v3.sh                          # 跑 2 分钟
#   V3EVERY=12 STEPS=600 ./game_v3.sh             # 预测稀一点/便宜一点
#   ARCS=no ./game_v3.sh                          # 只给临界那条画弧（每帧省 ~150ms）
#   ANGLE=10 ./game_v3.sh                         # 按键输入角 10°（默认 0°=垂直边界入射）
#   TRACK=1 ./game_v3.sh                          # 改用**跟踪出来的**方向/速度（原型模式）
#   INPUT=1 ./game_v3.sh                          # ★ 开输入层：手机网页/有线键盘说了算
#   HDMI=1 DUMP=none ./game_v3.sh                 # ★ P2：直播档上板载 HDMI —— 屏幕 =
#                                                 #   **摄像头实拍 + 场区/球/轨迹叠加**，
#                                                 #   不闪不黑；进球（正中那路判 goal）
#                                                 #   -> 出轨迹复盘图 REVIEW 秒再回直播
#   LIVE=0 HDMI=1 ./game_v3.sh                    #   老档：每次预测换一张仿真图（stst）
#   FAST=1 HDMI=1 ./game_v3.sh                    #   直播档缩放换 NEAREST（省 ~60ms/帧）
#
# ★ INPUT=1 的演示流程（现场就这么跑）：
#     ① 板上起遥控：  ./v3_remote.sh start        （手机浏览器开它打印的那个网址）
#     ② 有线键盘（可选，另开一个 ssh 窗口）：  ./v3_remote.sh key
#     ③ 跑游戏：      INPUT=1 ./game_v3.sh
#   键位/按钮：**空格 = 发射**、W/S 角度±2°、A/D 速度±20 px/s、R 复位（见 v3_input.py）
#   ★ INPUT=1 时**状态文件说了算**：文件里有值就用文件里的（哪怕它比命令行新），
#     开局那一笔会明确打印"盖掉命令行给的 X" —— 不许悄悄盖。
#
# ★★ INPUT=1 就是**发射模式**（2026-10-01 深夜，用户三条）：
#     球摆在**场区边界带**上（球心离最近那条边 ≤ FIREMARGIN 游戏像素，默认 70
#     ≈ 实体 3.5cm；再近于 19px 会贴线，PL 会卡死 -> 也拒），手机网页按大按钮
#     "发射" / 键盘按**空格** -> 从**那条边**打一杆：方向 = 边法线 + 输入角
#     （规则表 v4：入射角以边界法线为 0°）、发射点 = 球的位置。★★ 2026-10-05
#     规则订正：发射 = **真球入场**——镜面反射、弹到出射为止、多球互撞、实时拖尾。
#     球在区域**中间**按 = **拒绝**（原因回手机网页，/shot 轮询显示）。
#     ★ 平时**一帧都不预测**（这样"球是摆的、不是推的"才成立）；那一杆才花
#       折线+渲染那 ~0.6s（冷却 1.5s，连点不会把循环打穿）。
#
# 环境变量：SEC V3EVERY STEPS FAN SEED B ANGLE SPEED ARCS TRACK DUMP INPUT HDMI PE
#           MATCH（1=局状态机 M2.2：10 局制/弹药/进球+10/结算清场；只在 INPUT=1 下有效）
#           LIVE（默认 1=直播档；0=老 stst）REVIEW（复盘秒数，默认 6）FAST（1=NEAREST）
#           TRKMARGIN（场区外扩容差，默认 20 游戏像素）LIVEDUMP（直播存图路径，默认不存）
#           FIREMARGIN（发射模式的门槛：球心离最近那条边 ≤ 这个值才算"摆在边界上"，
#                       默认 70 游戏像素 ≈ 实体 3.5cm；只用 INPUT=1 时有意义）
#           RENDER（每 N 帧写一张 game_d.png；默认：直播档 0=不写 / 其它 30）
#           TAILN（末尾日志留几行，默认 40；0=**不截断**，排查时用）
#
# ★★ PY 必须是 PYNQ venv 里的那个 python3（2026-09-28 深夜实锤）：
#   /usr/bin/python3 是系统 python，**没有 pynq** —— pl_probe 的子进程会报
#   "ERR No module named 'pynq'" -> rc=9（未知，不下 bit）-> 一条命令白跑。
PY="${PY:-/usr/local/share/pynq-venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"
if ! "$PY" -c 'import pynq' >/dev/null 2>&1; then
  echo "!! $PY 里 import pynq 失败 —— 这个 python3 不是 PYNQ venv 的那个"
  echo "   试：PY=/usr/local/share/pynq-venv/bin/python3 ./game_v3.sh"
  exit 9
fi
echo "[env] python3 = $PY （已确认 import pynq 可用）"
cd /home/xilinx/wp || exit 9

SEC="${SEC:-60}"; V3EVERY="${V3EVERY:-6}"; STEPS="${STEPS:-600}"; FAN="${FAN:-12}"
SEED="${SEED:-7}"; B="${B:-0.40}"; ANGLE="${ANGLE:-0}"; SPEED="${SPEED:-400}"
DUMP="${DUMP:-none}"
# ★ 2026-10-01 夜（用户①②③三点）：直播档默认**开**（它是"接屏幕"的正解：
#   老 stst 每次预测停机换图 -> 屏幕一闪一闪，而且看不到实拍）。
LIVE="${LIVE:-1}"; REVIEW="${REVIEW:-6}"; # ★ 2026-10-19 用户实况"发射后像 PPT"：直播档默认 NEAREST（省 ~60ms/帧）；
#   要画质可 FAST=0 显式关。
FAST="${FAST:-1}"
# ★ 2026-10-24：SCREEN=1 -> 串口屏状态输出（TJC 115200，页面 index）。
#   只读游戏状态；串口没插/掉线不影响游戏。
SCREEN="${SCREEN:-0}"
# ★ 2026-10-25：HUD=0 -> HDMI 去掉常驻文字；CROP=1 -> 场区裁剪放大。
#   有串口屏（SCREEN=1）时默认 HUD=0/CROP=1；没屏时默认保留 HUD、不裁。
if [ "$SCREEN" = "1" ]; then
  HUD="${HUD:-0}"; CROP="${CROP:-1}"
else
  HUD="${HUD:-1}"; CROP="${CROP:-0}"
fi
CROPMARGIN="${CROPMARGIN:-0.03}"
# ★ 2026-10-01 夜提速（第二趟）：直播档默认**不写盘** —— game_d.png 就是一张带 fps
#   的缩略图，PNG 编码+落盘在板上 149~390ms，直播时没人看它（要留证据：RENDER=30 显式给）。
if [ "$HDMI" = "1" ] && [ "$LIVE" = "1" ] && [ -z "${RENDER:-}" ]; then
  RENDER=0
else
  RENDER="${RENDER:-30}"
fi
TRKMARGIN="${TRKMARGIN:-20}"; LIVEDUMP="${LIVEDUMP:-}"
FIREMARGIN="${FIREMARGIN:-70}"
# ★ 2026-09-29 夜：默认**不写盘**（DUMP=none）。板上逐笔拆账量的（见
#   E:\tmp_pkgs\render_breakdown.py）：一次 V3 里 PL 只要 2.7ms、渲染 59ms，
#   而 **PNG 编码+落盘要 149~390ms** —— 全流程最贵的一笔，写它不值。
#   要看图：DUMP=/dev/shm/v3_live.bmp（31ms，写 tmpfs）或 DUMP=v3_live.png（149ms）。

echo "== 1) PL 体检（下 bit 前必须先跑 pl_probe）=="
# ★ 2026-10-01 夜实锤（又一个"环境问题被当成硬件结论"）：
#   日志**不能用固定名 /tmp/_pp.log** —— 本板 fs.protected_regular=2，sticky 的 /tmp 里
#   **连 root 也不许** `>` 覆盖别人名下的文件（EACCES）⇒ `$?` 拿到的是**重定向失败的 1**，
#   会被下面当成"bit 不在 -> 下 bit"，再让 ball_game 去撞坏掉的 /boot（当场 OSError）。
#   改：mktemp 现开新文件（必属当前用户）+ **空日志一律按未知处理**（绝不猜）。
PLOG="$(mktemp /tmp/_pp.XXXXXX.log 2>/dev/null)" || PLOG=/home/xilinx/wp/.pl_probe.log
$PY -u pl_probe.py > "$PLOG" 2>&1; RC=$?
if [ ! -s "$PLOG" ]; then
  echo "   !! 体检日志没写成/是空的（$PLOG）-> 探针根本没跑起来，**按未知处理（绝不下 bit）**"
  exit 9
fi
tail -3 "$PLOG"
case "$RC" in
  0) NOBIT=--nobit; echo "   rc=0 我们的流水线在跑 -> --nobit（绝不重下 bit）";;
  2) NOBIT=--nobit; echo "   rc=2 我们的 bit 在、流水线闲着 -> --nobit（等下 ball_game 自己初始化）";;
  1)
     # ★ rc=1 = 我们的 bit 不在。下 bit **只有 load_bit.py 一个正确实现**（内部自带
     #   授权阶梯 / md5 对账 / 下完立刻复查）。**绝不能让 ball_game 自己去下** ——
     #   它写死 /boot/ov5640_m1a_wrapper.bit，而 /boot（FAT）早已损坏，实测当场
     #   "OSError: Bitstream file ... does not exist"（2026-10-01 夜）。
     echo "   rc=1 我们的 bit 不在 -> 走 load_bit.py（下 bit 的唯一实现）"
     [ -f load_bit.py ] || { echo "   !! 板上没有 load_bit.py -> 不冒险，退出"; exit 9; }
     $PY -u load_bit.py > "$PLOG" 2>&1; LRC=$?
     [ -s "$PLOG" ] || { echo "   !! load_bit 日志是空的 -> 按未知处理，退出"; exit 9; }
     tail -8 "$PLOG"
     [ "$LRC" = "0" ] || { echo "   !! load_bit.py rc=$LRC -> 没下成，退出（绝不硬跑）"; exit 9; }
     $PY -u pl_probe.py > "$PLOG" 2>&1; RC=$?
     [ -s "$PLOG" ] || { echo "   !! 复查日志是空的 -> 按未知处理，退出"; exit 9; }
     tail -3 "$PLOG"
     case "$RC" in
       0|2) NOBIT=--nobit; echo "   下完复查 rc=$RC -> 继续（--nobit）";;
       *) echo "   !! 下完复查 rc=$RC -> 不能继续，退出；重跑一次脚本再试"; exit 9;;
     esac;;
  *) echo "   rc=$RC 探针没得出结论 -> **不下 bit**，退出；重跑一次脚本再试"; exit 9;;
esac

ARGS="--sec $SEC $NOBIT --max-r ${MAXR:-120} --render $RENDER --v3 $V3EVERY
      --v3-seed $SEED --v3-b $B --v3-fan $FAN --v3-steps $STEPS
      --v3-angle $ANGLE --v3-speed $SPEED --v3-dump $DUMP
      --track-margin $TRKMARGIN --fire-margin $FIREMARGIN"
[ "$ARCS" = "no" ] && ARGS="$ARGS --v3-no-arcs"
[ "$TRACK" = "1" ] && ARGS="$ARGS --v3-track-angle"
# ★ INPUT=1：开输入层（手机网页 / 有线键盘）。这个 python3 里 import v3_input 能不能成
#   是**硬门槛** —— 免得"按钮按半天没人理"到现场才发现（静默失败最贵）。
if [ "$INPUT" = "1" ]; then
  if "$PY" -c 'import v3_input' 2>/dev/null; then
    ARGS="$ARGS --v3-input auto"
    if [ ! -f /dev/shm/v3_input.json ]; then
      echo "   !! INPUT=1 但 /dev/shm/v3_input.json 还不存在 —— 没人设过值，先用命令行"
      echo "      给的 $ANGLE° / $SPEED；要遥控就先起：./v3_remote.sh start"
    fi
    # ★ MATCH=1（2026-10-06，M2.2）：开**局状态机**（10 局制/弹药/进球+10/结算清场）。
    #   import match_flow 同款**硬门槛**（静默失败最贵）；只在 INPUT=1 时有意义
    #   （对局靠输入层读"发射=就绪"）。
    if [ "$MATCH" = "1" ]; then
      if "$PY" -c 'import match_flow' 2>/dev/null; then
        ARGS="$ARGS --match"
        echo "   MATCH=1：局状态机开（10 局制；局前双方按 空格/回车 就绪）"
      else
        echo "!! MATCH=1 但 $PY 里 import 不到 match_flow（文件没部署？在 wp 目录里跑？）"
        exit 9
      fi
    fi
  else
    echo "!! INPUT=1 但 $PY 里 import 不到 v3_input（文件没部署？在 wp 目录里跑？）"
    exit 9
  fi
fi
# ★ HDMI=1：把 V3 每次预测画的那张图**直接推到板载 HDMI**（接显示器用）。默认关。
#   import hdmi_out 是**硬门槛**（同 INPUT）：没部署/跑错目录时当场喊停，别到现场黑屏。
#   上屏不写盘 —— 现场档建议配 DUMP=none（写盘在板上要 149~390ms，是全程最贵的一笔）。
if [ "$HDMI" = "1" ]; then
  if "$PY" -c 'import hdmi_out' 2>/dev/null; then
    ARGS="$ARGS --v3-hdmi"
    if [ "$LIVE" = "1" ]; then
      # ★ 直播档：屏幕 = 摄像头实拍 + 场区/球/轨迹叠加（单缓冲原地写，不闪不黑）；
      #   进球（正中那一路判 goal）-> 复盘图 REVIEW 秒。**上屏的图不是 V3 那张仿真图**，
      #   所以它跟 DUMP 不冲突（DUMP 管的是"要不要把仿真图写盘"）。
      ARGS="$ARGS --v3-hdmi-live --v3-review $REVIEW"
      [ "$FAST" = "1" ] && ARGS="$ARGS --live-fast"
      [ -n "$LIVEDUMP" ] && ARGS="$ARGS --live-dump $LIVEDUMP"
      echo "   HDMI=1：**直播档**（实拍+叠加常显，不黑不闪；进球出复盘图 ${REVIEW}s；"\
"缩放=$([ "$FAST" = "1" ] && echo NEAREST || echo LINEAR)）"
      echo "       提速：平时预测只读 PL 判定（~3ms）；折线+渲染只在进球那一刻现算；"\
"game_d.png 写盘=$([ "$RENDER" = "0" ] && echo 关 || echo "每 $RENDER 帧")"
    else
      echo "   HDMI=1：V3 预测图直推板载 HDMI（720p60，帧泵 stst 档 —— LIVE=0）"
      case "$DUMP" in
        none|-|"") : ;;
        *) echo "   !! 但 DUMP=$DUMP 还在写盘 —— 上屏不需要它，现场档建议 DUMP=none" ;;
      esac
    fi
  else
    echo "!! HDMI=1 但 $PY 里 import 不到 hdmi_out（文件没部署？在 wp 目录里跑？）"
    exit 9
  fi
fi
# ★ PE=1（2026-10-17 档3 粒子引擎风暴模式）：--pe 开 PL 里的 pe_axi（0x40001000）。
#   import particle_pl 同款**硬门槛**（同 MATCH/HDMI）；要 INPUT=1（B=P1 / N=P2 键
#   在输入层）。bit 若还是旧版（无 pe_axi）ball_game 会打印"打开失败"并照常跑 ——
#   与硬门槛不冲突（门槛只保"脚本齐"，不保"bit 新"）。
if [ "$PE" = "1" ]; then
  if "$PY" -c 'import particle_pl' 2>/dev/null; then
    ARGS="$ARGS --pe"
    echo "   PE=1：粒子引擎风暴模式开（B=P1 / N=P2 各放 16 颗；需 INPUT=1 才有点键）"
  else
    echo "!! PE=1 但 $PY 里 import 不到 particle_pl（文件没部署？在 wp 目录里跑？）"
    exit 9
  fi
fi

# ★ SCREEN=1：串口屏状态输出（TJC 115200，页面 index）——只读游戏状态，不影响主循环。
if [ "$SCREEN" = "1" ]; then
  ARGS="$ARGS --screen"
  echo "   SCREEN=1：串口屏状态输出开（TJC 115200，页面 index）"
fi
# ★ HUD=0：HDMI 去常驻文字（状态已上串口屏）；CROP=1：按标定场区裁剪放大。
[ "$HUD" = "0" ] && ARGS="$ARGS --no-hud"
if [ "$CROP" = "1" ]; then
  ARGS="$ARGS --live-crop"
  export LIVE_CROP_MARGIN="$CROPMARGIN"
  echo "   CROP=1：场区裁剪放大（margin=$CROPMARGIN，输出 1280x720）"
fi

echo "== 2) 游戏循环 + V3 实时预测（每 $V3EVERY 个有效帧一次，$SEC 秒）=="
# ★ 2026-10-01 深夜：这里的尾截断原来是写死的 `tail -40` —— 它把**早期输出全切掉**了
#   （run4 的日志只剩 54 行，pl_probe/配置那些行全没了；外面再 tee 也救不回，因为
#   切在管道里）。改成 TAILN 变量：默认 40 不变，排查时 TAILN=0 拿完整日志。
#   pipefail：让 rc 说真话（管道结尾的 rc 是假的老坑）。
if [ "${TAILN:-40}" = "0" ]; then
  # shellcheck disable=SC2086
  $PY -u ball_game.py $ARGS 2>&1
else
  set -o pipefail
  # shellcheck disable=SC2086
  $PY -u ball_game.py $ARGS 2>&1 | tail -n "${TAILN:-40}"
fi

echo "== 3) 取图（在 PC 上执行）=="
case "$DUMP" in
  none|-|"") echo "   （DUMP=$DUMP：本趟没写图。要图：DUMP=/dev/shm/v3_live.bmp ./game_v3.sh）";;
  /*)        echo "   scp xilinx@<板IP>:$DUMP  ./logs/";;
  *)         echo "   scp xilinx@<板IP>:/home/xilinx/wp/$DUMP  ./logs/";;
esac
echo "   scp xilinx@<板IP>:/home/xilinx/wp/game_d.png ./logs/"
if [ "$HDMI" = "1" ] && [ "$LIVE" = "1" ]; then
  echo "   （HDMI=1 直播档：屏幕上是**实拍+叠加**，不是仿真图；仿真图只有进球复盘那几秒）"
  [ -n "$LIVEDUMP" ] && echo "   直播叠加图（每 5s 一张）：scp xilinx@<板IP>:$LIVEDUMP ./logs/"
elif [ "$HDMI" = "1" ]; then
  echo "   （HDMI=1 stst 档：本趟画面直接上屏了 —— 看汇总里的『HDMI 直出』账）"
fi
