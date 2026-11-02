#!/bin/bash
# watch_board.sh — 守着板子上线（板子一回来立刻报，省得人盯着）
#
# 背景：2026-09-25 板子又一次失联。实测结论（见对话）：
#   - PC 以太网 2 = Up/Connected/100Mbps，静态 192.168.2.1/24
#   - 192.168.2.0/24 全段 ARP 扫，254 个地址**零应答**（邻居表全 Unreachable、MAC 全 0）
#   ⇒ 不是 IP 变了、不是防火墙、不是热点：是板子上的 Linux 没在应答。
#
# 用法：bash software/watch_board.sh [轮数]   （每轮 10s，默认 56 轮 ≈ 9.5 分钟）
set -u
BOARD="xilinx@192.168.2.99"
N="${1:-56}"
for i in $(seq 1 "$N"); do
  if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes "$BOARD" "echo UP" 2>/dev/null | grep -q UP; then
    echo "BOARD_UP  等了 $((i*10)) 秒"
    exit 0
  fi
  sleep 10
done
echo "BOARD_STILL_DOWN  等满 $((N*10)) 秒仍无应答"
exit 1
