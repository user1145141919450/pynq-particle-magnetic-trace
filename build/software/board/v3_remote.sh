#!/bin/bash
# v3_remote.sh —— ★ P2：手机网页遥控 / 有线键盘 的启停（就管 v3_input.py 这一个文件）
#
#   ./v3_remote.sh start        后台起手机遥控网页（默认 8080），并打印该开的网址
#   ./v3_remote.sh stop         停掉它（连状态文件一起清）
#   ./v3_remote.sh status       看它在不在 + 当前状态 + 板子有哪些网口
#   ./v3_remote.sh key          前台跑**有线键盘**（要 root；Ctrl+C 退）
#   ./v3_remote.sh key --grab   同上，但独占键盘（敲键不会漏到 shell）
#
# ★★ 为什么要有这个脚本（2026-09-30 夜）：手机那条路我原来是用一条 ssh 挂着 nohup 起的
#    —— 那条 ssh 通道一直占着不关（本机看着像"卡住 120 秒"），进程也成了孤儿。
#    起服务必须**脱离终端**（setsid + </dev/null），还得留 pid 能停得掉。
#
# ★ 板子**没有 WiFi 网卡**（/sys/class/net 只有 eth0）——手机要连得上它，只有三条路：
#     ① 板子的网线插在**手机也连着的那台路由器**上 -> 手机浏览器开 http://<板IP>:8080/
#     ② **手机开"USB 共享网络"、用线插板的 USB HOST 口** <- 推荐，驱动都在
#        （盘上有 rndis_host / cdc_ncm / ipheth，安卓和 iPhone 都覆盖；不依赖路由器）
#     ③ 板子网线直插电脑、电脑开网络共享（要配，最麻烦）
#   ②的具体手续：插线 -> 手机弹"是否信任/允许共享网络"选允许 -> 板上 ip -4 addr 多出
#   一个网口（安卓 rndis0 / iPhone eth1，一般是 172.20.10.x 网段）-> 手机浏览器开
#   http://172.20.10.2:8080/（板子那边拿到的 IP，用 status 看）

PY="${PY:-/usr/local/share/pynq-venv/bin/python3}"
PORT="${PORT:-8080}"
DIR=/home/xilinx/wp
PIDF=/tmp/v3in_serve.pid
LOG=/tmp/v3in_serve.log
cd "$DIR" || exit 9

case "$1" in
  start)
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      echo "[遥控] 已经在跑了（pid $(cat "$PIDF")）—— 要重起先 ./v3_remote.sh stop"
    else
      # ★ 固定名 /tmp 文件的坑（fs.protected_regular=2：sticky 目录里连 root 也不许
      #   覆盖别人名下的文件）—— 换用户跑时 `>"$LOG"` 会 EACCES，而这里是 setsid 的
      #   重定向，失败是**无声**的（服务起不来还以为起了）。先删掉，让下面全新创建。
      rm -f "$PIDF" "$LOG"
      # ★ setsid + </dev/null + 全重定向：进程**脱离这个终端**，ssh 一断开也不受影响
      setsid "$PY" -u v3_input.py serve --port "$PORT" >"$LOG" 2>&1 </dev/null &
      echo $! > "$PIDF"
      sleep 1.5
      if kill -0 "$(cat "$PIDF")" 2>/dev/null; then
        echo "[遥控] 已起（pid $(cat "$PIDF")，端口 $PORT）"
      else
        echo "[遥控] ★ 没起来 —— $LOG 最后几行："
        tail -5 "$LOG"
        exit 9
      fi
    fi
    "$0" status
    ;;
  stop)
    if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
      kill "$(cat "$PIDF")" && echo "[遥控] 已停（pid $(cat "$PIDF")）"
      sleep 0.3
    else
      echo "[遥控] 本来就没在跑"
    fi
    rm -f "$PIDF"
    ;;
  status)
    echo "== 谁在听 =="
    ss -ltn 2>/dev/null | grep -q ":$PORT " && echo "   端口 $PORT：在听 ✓" \
      || echo "   端口 $PORT：没人听（没起 / 起失败，看 $LOG）"
    [ -f "$PIDF" ] && echo "   pid 文件：$(cat "$PIDF")"
    echo "== 当前状态（游戏循环读的就是这一份）=="
    "$PY" -u v3_input.py status || true
    echo "== 板子的网口（手机要走哪个）=="
    ip -4 -o addr show | grep -v " lo " | sed 's/^/   /'
    echo "   ★ 板子**没有 WiFi**，只有上面这些口；手机要用 **同一个网段**的地址访问"
    ;;
  key)
    shift
    # ★ 键盘要 root（/dev/input 是 root:input）。这里保留登录 shell：venv 的 python3
    #   在非交互 shell 里也能拿到（路径写死了）
    echo "[键盘] 需要 root 读 /dev/input —— 输 xilinx 的口令"
    exec sudo -S -E "$PY" -u v3_input.py key "$@"
    ;;
  *)
    sed -n '2,30p' "$0"
    exit 2
    ;;
esac
