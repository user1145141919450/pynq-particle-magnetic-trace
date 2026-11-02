#!/bin/bash
# game_loop.sh — 演示用守护：游戏进程崩了/退化就重启；重启也不行就重配 PL。
#
# 为什么要守护（2026-09-27 实测）：链路的失效是**分级**的，而软件只能救前两级：
#   ① 卡死（frame_cnt 不动）        -> 进程内的自愈阶梯（脉冲 ctrl[3] / 传感器断电重上电）
#   ② 视核卡在 resync（plck 侧全活、frame_cnt 恒 0）
#        -> 进程内救不回来，**重启进程**（新的 _init_video + soft_rst）有时能行
#   ③ ①②都无效（例如救援序列被半途打断留下的坏状态）
#        -> 只有**重配 PL**（下 bit）或整机断电才清得掉
# 这个脚本把 ②③ 也自动化了：连续 3 次"刚起就退"就下 bit 重来。
# 用法（板上）：sudo bash /boot/game_loop.sh [每次跑多少秒，默认 600]
SEC="${1:-600}"
NFAIL=0
while true; do
  T0=$(date +%s)
  python3 -u /boot/ball_game.py --sec "$SEC" --nobit --max-r 320 --render 60
  DT=$(( $(date +%s) - T0 ))
  if [ "$DT" -lt 30 ]; then NFAIL=$((NFAIL+1)); else NFAIL=0; fi
  echo "[守护] 本轮跑了 ${DT}s（刚起就退计数 $NFAIL）"
  if [ "$NFAIL" -ge 3 ]; then
    echo "[守护] 连续 $NFAIL 次刚起就退 -> 重配 PL（下 bit）"
    python3 -u /boot/ball_game.py --bit-only
    NFAIL=0
    sleep 3
  fi
  sleep 2
done
