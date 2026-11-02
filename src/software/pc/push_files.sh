#!/bin/bash
# push_files.sh —— 把 **指定的几个文件** 推上板（**逐条对 md5**）。通用版 push_hdmi.sh。
#
# 为什么要有它：push_hdmi.sh 的 FILES 是写死的（HDMI 首亮那几个）。之后每次改代码
# （比如 2026-10-01 夜的直播档 / 追踪过滤 / 进球复盘），要么改那个写死的表，要么手打
# scp —— 手打 scp 就是当年那个**静默失败**的坑：scp 推失败/推了个旧版本不报错，
# 上板跑出来的是老代码，现场查半天。所以这里把"推 + 逐条 md5 校验"抽成通用工具。
#
# ★ 本工程的老规矩（都是踩出来的，别省）：
#   · scp **会静默失败** ⇒ 每次推完都要逐条比 md5，不一致就 die，绝不继续。
#   · 板上**只从 ext4 的 /home/xilinx/wp** 跑（/boot 是 FAT，曾断电丢写+假通过）。
#   · 下 bit **只有** load_bit.py 一个正确实现；本脚本**只送文件**，不下载、不碰相机。
#
# 用法（PC 上，Git Bash）：
#   bash software/pc/push_files.sh --check ball_game.py game_v3.sh   # 只看 PC 侧一致性
#   bash software/pc/push_files.sh ball_game.py game_v3.sh hdmi_out.py v3_scan.py
#
# 先决条件：跑过 `python software/pc/deploy_check.py --fix`（deploy/ 与 software/board/ 一致）

set -u
BOARD="xilinx@192.168.2.99"
WP="/home/xilinx/wp"
DEPLOY="$(cd "$(dirname "$0")/../../vivado/ov5640_pynq/deploy" && pwd)"
BOARD_DIR="$(cd "$(dirname "$0")/../board" && pwd)"
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then CHECK_ONLY=1; shift; fi
FILES=("$@")

say() { echo ""; echo "================ $* ================"; }
die() { echo "★ 失败：$*"; exit 1; }
md5of() { md5sum "$1" | cut -d' ' -f1; }

[ ${#FILES[@]} -gt 0 ] || die "没给文件名。用法：bash push_files.sh 文件1 文件2 ..."

# ---------------------------------------------------------------------------
say "第 0 步：PC 侧清点（deploy 包必须与软件源逐字节一致）"
[ -d "$DEPLOY" ] || die "deploy 目录不存在：$DEPLOY"
for f in "${FILES[@]}"; do
  [ -f "$DEPLOY/$f" ] || die "deploy 包里缺 $f（先跑 deploy_check.py --fix）"
  case "$f" in
    *.py|*.sh)
      [ -f "$BOARD_DIR/$f" ] || die "软件源缺 $f（$BOARD_DIR）"
      L="$(md5of "$BOARD_DIR/$f")"; R="$(md5of "$DEPLOY/$f")"
      if [ "$L" != "$R" ]; then
        echo "  ! $f   software/board 与 deploy **不一致**（deploy 里是旧的）"
        die "deploy 包过期 —— 先跑：python software/pc/deploy_check.py --fix"
      fi
      printf "    ✓ %-24s %s（源=deploy）\n" "$f" "$L"
      ;;
    *)  printf "    · %-24s %s（非源码，只查 deploy 存在）\n" "$f" "$(md5of "$DEPLOY/$f")" ;;
  esac
done

if [ $CHECK_ONLY -eq 1 ]; then
  say "--check 结束（没碰板子）"
  exit 0
fi

# ---------------------------------------------------------------------------
say "第 1 步：等板子上线（最多 3 分钟）"
UP=0
for i in $(seq 1 18); do
  if ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=no "$BOARD" "echo up" 2>/dev/null | grep -q up; then
    echo "  板子在线（等了 $((i*10))s）"; UP=1; break
  fi
  sleep 10
done
[ $UP -eq 1 ] || die "等了 3 分钟板子还没上线（上电了吗？网线？）"

# ---------------------------------------------------------------------------
say "第 2 步：推文件 + 逐条 md5 校验"
for f in "${FILES[@]}"; do
  L="$(md5of "$DEPLOY/$f")"
  ok=0
  for i in 1 2 3; do
    if scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 "$DEPLOY/$f" "$BOARD:$WP/" >/dev/null 2>&1; then
      R="$(ssh -o ConnectTimeout=15 "$BOARD" "md5sum $WP/$f 2>/dev/null | cut -d' ' -f1")"
      if [ "$L" = "$R" ]; then ok=1; break; fi
      echo "    $f 第 $i 次 md5 不一致（$L vs ${R:-空}），重推"
    else
      echo "    $f 第 $i 次 scp 失败，重试"
    fi
    sleep 4
  done
  [ $ok -eq 1 ] || die "$f 推上去之后 md5 对不上 —— **绝不继续**"
  printf "    ✓ %-24s %s\n" "$f" "$L"
done

# ---------------------------------------------------------------------------
say "第 3 步：补可执行位"
ssh -o ConnectTimeout=15 "$BOARD" "chmod +x $WP/*.sh 2>/dev/null; echo chmod ok"

say "推完了（没下 bit、没碰相机）。板上对账："
ssh -o ConnectTimeout=15 "$BOARD" "cd $WP && md5sum ${FILES[*]}"
