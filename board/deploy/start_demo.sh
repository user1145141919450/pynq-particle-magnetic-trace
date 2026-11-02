#!/bin/bash
# ============================================================================
# start_demo.sh —— 板上电**自动起演示**（不需要电脑）
#
# 由 /etc/systemd/system/magnetic-demo.service 以 root 调用；也可手动 sudo 跑。
# 顺序：
#   1) 等过开机 150s（load_bit.py 在窗口内拒绝下 bit：PYNQ base.bin ~120s 会写 PL）
#   2) 已在跑就不重复起
#   3) load_bit.py 下**当前** bit（rc=1/2 都会下；rc=0/9 保护性拒绝，不硬来）
#   4) 起键盘读取器（root）+ 手机 serve（有网就用）
#   5) 前台跑 game_v3.sh（systemd Restart=on-failure 负责重起）
#
# 关键前提：/home/xilinx/wp 是 **ext4**，bit + 边车 md5 都在这里；不碰 /boot。
# ============================================================================
set -u
cd /home/xilinx/wp || { echo "[demo] 进不了 /home/xilinx/wp"; exit 9; }
# ★★ systemd 是**干净环境**（不走 /etc/profile）：不 source 这两个，
#    pynq 会报 `No devices found, is the XRT environment sourced?` ->
#    pl_probe rc=9 -> load_bit 拒绝 -> 服务每 15s 重试、永远起不来（2026-10-28 实锤）。
[ -f /etc/profile.d/xrt_setup.sh ] && . /etc/profile.d/xrt_setup.sh
[ -f /etc/profile.d/pynq_venv.sh ] && . /etc/profile.d/pynq_venv.sh
PY=/usr/local/share/pynq-venv/bin/python3
[ -x "$PY" ] || { echo "[demo] 找不到 pynq venv python"; exit 9; }
mkdir -p logs

# 1) 开机 150s 写 PL 窗口（load_bit.py 自己也会拒；这里提前等，免得刷失败日志）
while [ "$(cut -d. -f1 /proc/uptime)" -lt 160 ]; do sleep 5; done

# 2) 已有游戏在跑（手动起的）—— 让位，不抢
if pgrep -f "[b]all_game.py" >/dev/null; then
    echo "[demo] 已有 ball_game 在跑，服务让位"
    exit 0
fi

# 3) 下当前 bit（唯一合法实现；rc=1/2 下，rc=0/9 拒绝）
echo "[demo] load_bit.py ..."
$PY -u load_bit.py >> logs/demo_boot.log 2>&1
_rc=$?
echo "[demo] load_bit rc=$_rc"
# rc=2 = 下完了（bit 在、没出流）；rc=0 = 已确认是我们的 bit；rc=1/9 = 失败
# 失败也继续试 game_v3.sh（它会再判一次；绝不让一个判断失误把整场卡死）

# 4) 输入层：键盘读取器（root）+ 手机 serve；已在跑就跳过
if ! pgrep -f "[v]3_input.py key" >/dev/null; then
    setsid nohup "$PY" -u v3_input.py key \
        > /dev/shm/kbd.log 2>&1 < /dev/null &
fi
if ! pgrep -f "[v]3_input.py serve" >/dev/null; then
    setsid nohup ./v3_remote.sh start \
        > /dev/shm/remote.log 2>&1 < /dev/null &
fi

# 5) 游戏（前台；日志落 ext4，开机前一次转存）
[ -f logs/demo_boot.log ] && mv -f logs/demo_boot.log logs/demo_boot.prev.log
exec env SCREEN=1 MATCH=1 INPUT=1 HDMI=1 FAST=1 SEC=86400 TAILN=0 \
    ./game_v3.sh >> logs/demo_boot.log 2>&1
