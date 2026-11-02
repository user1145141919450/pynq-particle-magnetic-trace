#!/bin/bash
# board_line_verify.sh —— PL 画线引擎上板验收（2026-10-31）
#
# 前提：板子已上电（ping 192.168.2.99 通），PC 侧 deploy 包已是最新
#       （python tools/finish_build.py 跑过，显示"可以上板"）
#
# 做的事（**全部按项目铁律**）：
#   1) 推 5 个文件（bit / hwh / bit.md5 / line_pl.py / line_bench.py），逐条 md5 对账
#   2) 板上：wp_health.py → pl_probe.py（看 PL 现况）→ load_bit.py（唯一允许入口）
#   3) 板上：line_bench.py（不碰 VDMA：自己 allocate 1280x720，PL 画完 PS 读回逐点核对）
#   4) 把板上输出存到 logs/board_line_$(date).log
#
# 不碰相机/不摆球/不动 HDMI —— 纯软件+PL，没有物理风险。
set -u
BOARD="xilinx@192.168.2.99"
WP="/home/xilinx/wp"
PY="/usr/local/share/pynq-venv/bin/python3"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOG="$ROOT/logs/board_line_$(date +%Y%m%d_%H%M%S).log"

say() { echo ""; echo "======== $* ========"; }
die() { echo "★ 失败：$*"; exit 1; }

mkdir -p "$ROOT/logs"

say "0. 板子在线？"
ping -n 1 -w 1500 192.168.2.99 >/dev/null 2>&1 || die "192.168.2.99 ping 不通（板子没上电？）"
echo "板子在线"

say "1. 推文件（逐条 md5）"
bash "$HERE/push_files.sh" ov5640_m1a_wrapper.bit ov5640_m1a_wrapper.hwh \
     ov5640_m1a_wrapper.bit.md5 line_pl.py line_bench.py || die "推文件失败（md5 对不上？）"

say "2. 板上：健康检查 + PL 现况 + 下 bit"
{
  echo "==== 上板验收 $(date) ===="
  echo "---- wp_health ----"
  ssh "$BOARD" "cd $WP && $PY wp_health.py" 2>&1 | tail -20
  echo "---- pl_probe ----"
  echo xilinx | ssh "$BOARD" "sudo -S -E bash -lc 'cd $WP && $PY pl_probe.py'" 2>&1 | tail -10
  echo "---- load_bit ----"
  echo xilinx | ssh "$BOARD" "sudo -S -E bash -lc 'cd $WP && $PY load_bit.py'" 2>&1 | tail -20
} | tee "$LOG"

say "3. line_bench（PL 画线逐点核对 + 计速）"
echo xilinx | ssh "$BOARD" \
  "sudo -S -E bash -lc 'cd $WP && $PY line_bench.py'" 2>&1 | tee -a "$LOG"

say "4. 判据"
_sum=$(grep -E "^通过 [0-9]+ / 失败 [0-9]+" "$LOG" | tail -1)
if [ -n "$_sum" ] && echo "$_sum" | grep -q "失败 0$"; then
  echo "★ $_sum —— PL 画线通路的像素级验收通过"
else
  echo "★ 没拿到『通过 N / 失败 0』（实际：${_sum:-无}），逐条看日志：$LOG"
fi
echo "日志：$LOG"
