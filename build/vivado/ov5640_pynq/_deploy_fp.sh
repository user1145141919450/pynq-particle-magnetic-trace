#!/bin/bash
# _deploy_fp.sh —— 帧泵 v2.1 过门禁后的一键上板（2026-10-31）
#
# 做四件事，任一步失败就停：
#   1) tools/finish_build.py：查门禁产物 + hwh 对 IP 基线 + 拷进 deploy + deploy_check --fix
#   2) 生成边车 *.bit.md5（load_bit.py 靠它认"板上的 bit 是不是这一版"）
#   3) push_files.sh 推 bit / md5 / hwh（**逐文件 md5 对账**）
#   4) 板端复核三个文件的 md5 与本地一致
#
# 用法（PC 上，Git Bash，仓库根目录）：
#   bash vivado/ov5640_pynq/_deploy_fp.sh
# 注意：本脚本**不下载 bit、不碰相机**；下 bit 只有 load_bit.py 一条合法路径。
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PJ="$ROOT/vivado/ov5640_pynq"
IMPL="$PJ/proj/ov5640_m1a.runs/impl_1"
DEP="$PJ/deploy"
BIT="$IMPL/ov5640_m1a_wrapper.bit"
BOARD="xilinx@192.168.2.99"

die() { echo ""; echo "★ 失败：$*"; exit 1; }

echo "================ 1) finish_build.py ================"
cd "$ROOT" || die "进不了仓库根"
python tools/finish_build.py || die "finish_build 不过（门禁/IP 基线/deploy 自洽）"

echo "================ 2) 边车 md5 ================"
[ -f "$BIT" ] || die "没有 bit：$BIT"
( cd "$DEP" && md5sum ov5640_m1a_wrapper.bit > ov5640_m1a_wrapper.bit.md5 ) \
    || die "生成边车 md5 失败"
echo "边车：$(cat "$DEP/ov5640_m1a_wrapper.bit.md5")"

echo "================ 3) 推上板 ================"
bash "$ROOT/software/pc/push_files.sh" \
    ov5640_m1a_wrapper.bit ov5640_m1a_wrapper.bit.md5 ov5640_m1a_wrapper.hwh \
    || die "push_files 失败"

echo "================ 4) 板端复核 ================"
for f in ov5640_m1a_wrapper.bit ov5640_m1a_wrapper.bit.md5 ov5640_m1a_wrapper.hwh; do
    loc=$( cd "$DEP" && md5sum "$f" | cut -d' ' -f1 )
    rem=$( ssh -o ConnectTimeout=8 "$BOARD" "cd /home/xilinx/wp && md5sum '$f' | cut -d' ' -f1" )
    if [ "$loc" = "$rem" ]; then
        echo "  [OK]   $f  $loc"
    else
        die "$f md5 不一致：本地=$loc 板上=$rem"
    fi
done
echo ""
echo "★ 已上板。下一步（板上）："
echo "    cd /home/xilinx/wp && /usr/local/share/pynq-venv/bin/python3 -u load_bit.py"
echo "  再跑台架： frame_pump_bench.py；再起游戏： PLPUMP=1 INPUT=1 HDMI=1 ./game_v3.sh"
