#!/bin/bash
# v3_shoot.sh —— 板上"一条命令出定妆照"。两种口径：
#
#   ./v3_shoot.sh          ★ 出图模式（--no-cam）：**完全不碰相机与视频链**，秒级完成。
#                            场景 = seed 决定的场区 + 固定 B + 合成球位，谁跑都一样。
#   ./v3_shoot.sh --cam      相机模式：先自愈链路、再跟踪真球出图（答辩现场演示用）。
#
# ★ 为什么默认这条路（2026-09-28 夜，定妆照复盘）：
#   上次那张"定妆照"其实是个**退化场景** —— 球被映射到 (916,136)（离右边界 45px、
#   正好落在洞口里），17 路全部瞬间出射；而且 PL 模式原来**根本不画弧线**（每条路
#   只有一个 5px 终点小点），于是图上一片空、看不出任何故事。
#   场景参数本来就可以离线复现，所以参数是用 tools/v3_shot_search.py 搜好再上板的：
#     seed=7 → 3 块磁场、右洞 c=127(target 0)、左洞 c=134(target 1)
#     球位(300,230) ±12° 17 路 → 绿 3 条出射 / 红 14 条打回 / 橙色临界擦洞口边 0.7px
#     （口径 max_bounce=0：RTL 的 max_bounce 是死输入，PL 是"撞墙即停"，详见 v3_scan.py 注释）
#   ★ 本脚本一条命令的板上实测（2026-09-28 深夜，产物 logs/v3_demo_pl.png）：
#     [自检] 黄金模型 vs PL 回读：端点 逐位一致 17/17、判定 一致 17/17
#     PL 一簇 17 路 AXI 往返 11.99 ms（模块自报周期低 8 位 196）
#     同口径 PS 串行（板载 CPU 跑同一份定点模型、同一簇角度）534 ms -> 加速 45 倍
#     （另一轮量到 12.43 ms / 545 ms = 44 倍，量级稳定）
#
# ★ 下 bit 的铁律：**先跑 pl_probe**。盲目下 bit = PL 半编程 = MMIO 读挂死总线 =
#   只能断电重启（2026-09-26 踩过）。
# ★★ PY 必须是 **PYNQ venv 里的那个 python3**（2026-09-28 深夜实锤）：
#   本板有两个 python3：登录 shell 的 PATH 里是 /usr/local/share/pynq-venv/bin/python3
#   （有 pynq），而 **/usr/bin/python3 是系统 python，里面没有 pynq**。
#   脚本原来写死 /usr/bin/python3 -> pl_probe 的隔离子进程报
#   "ERR No module named 'pynq'" -> 按新规矩判成 rc=9（未知，不下 bit）-> 一条命令出不了图。
#   注意这跟"非登录 shell"是**两件事**：cwd 对了、也不是没登录，只是解释器选错了。
#   本板 python 解析顺序：/usr/local/share/pynq-venv/bin/python3 -> /usr/bin/python3。
PY="${PY:-/usr/local/share/pynq-venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"
if ! "$PY" -c 'import pynq' >/dev/null 2>&1; then
  echo "!! $PY 里 import pynq 失败 —— 这个 python3 不是 PYNQ venv 的那个"
  echo "   试：PY=/usr/local/share/pynq-venv/bin/python3 ./v3_shoot.sh"
  exit 9
fi
echo "[env] python3 = $PY （已确认 import pynq 可用）"
cd /home/xilinx/wp || exit 9

POS="${POS:-300,230}"; BB="${B:-0.40}"; FAN="${FAN:-12}"
ANG="${ANG:-0}"; SPD="${SPD:-400}"; SEED="${SEED:-7}"; OUT="${OUT:-v3_demo.png}"

if [ "$1" = "--cam" ]; then
  echo "== 1) 电源位体检 =="
  $PY -u link_doctor.py --sensor-on | grep -E "电源|传感器" | head -3

  echo "== 2) 速率体检 =="
  $PY -u link_doctor.py | grep "速率" | head -1

  echo "== 3) 视核活没活（不活就重配 PL）=="
  # ★ 固定名 /tmp 文件是坑（fs.protected_regular=2：sticky 目录里连 root 也不许覆盖
  #   别人名下的文件）-> 一律 mktemp 现开（同 game_v3.sh 2026-10-01 夜的实锤）
  _DEAD="$(mktemp /tmp/_dead.XXXXXX 2>/dev/null)" || _DEAD=/home/xilinx/wp/.v3_dead
  $PY -u link_doctor.py --watch 2 | grep -c "^   t=.*0.0 fps" > "$_DEAD"
  if [ "$(cat "$_DEAD")" -ge 2 ]; then
    echo "   视核 0 帧 -> 重配 PL（清视核 resync）"
    $PY -u ball_game.py --bit-only 2>&1 | tail -1
  else
    echo "   视核在跑，不重配"
  fi

  echo "== 4) 出图（相机模式：跟踪真球 + --arcs 画弧线）=="
  $PY -u v3_scan.py --n "${N:-12}" --nobit --b "$BB" --fan "$FAN" --steps 2000 --arcs \
       --dump "$OUT" 2>&1 | grep -E "自检|PL\]|扫完|临界|输出|有效帧|汇总|一簇" | tail -10
  echo "== 5) 取图（在 PC 上执行）==  scp xilinx@<板IP>:/home/xilinx/wp/$OUT ./logs/"
  exit $?
fi

echo "== 1) PL 体检（下 bit 前必须先跑 pl_probe）=="
# ★ 2026-10-01 夜：日志改 mktemp + 空日志按未知处理 + rc=1 走 load_bit.py。
#   理由同 game_v3.sh 里那段长注释（protected_regular=2 / ball_game 写死坏 /boot）。
PLOG="$(mktemp /tmp/_pp.XXXXXX.log 2>/dev/null)" || PLOG=/home/xilinx/wp/.pl_probe.log
$PY -u pl_probe.py > "$PLOG" 2>&1; RC=$?
if [ ! -s "$PLOG" ]; then
  echo "   !! 体检日志没写成/是空的（$PLOG）-> 探针根本没跑起来，**按未知处理（绝不下 bit）**"
  exit 9
fi
tail -3 "$PLOG"
case "$RC" in
  0) NOBIT=--nobit; echo "   rc=0 我们的流水线在跑 -> --nobit（绝不重下 bit）";;
  2) NOBIT=--nobit; echo "   rc=2 我们的 bit 在、流水线闲着 -> --nobit（V3 不需要视频流）";;
  1)
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

echo "== 2) 出图（PL 给判定 + 定点黄金模型画弧线，全程不碰相机）=="
$PY -u v3_scan.py --no-cam $NOBIT --pos "$POS" --b "$BB" --fan "$FAN" --angle "$ANG" \
     --speed "$SPD" --seed "$SEED" --steps 2000 --arcs --dump "$OUT" 2>&1 \
   | grep -E "自检|PL\]|扫完|临界|输出|汇总|一簇|参照|加速|注：" | tail -12

echo "== 3) 取图（在 PC 上执行）==  scp xilinx@<板IP>:/home/xilinx/wp/$OUT ./logs/"
