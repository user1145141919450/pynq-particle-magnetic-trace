#!/bin/bash
# v3_wifi.sh —— ★ P2：板子用 USB WiFi 网卡上网/组网的一条命令（手机遥控的前置）
#
#   ./v3_wifi.sh scan              扫一遍周围的热点（顺带体检网卡在不在）
#   ./v3_wifi.sh up "SSID" "密码"   连上去 + 拿 IP + 打印手机该开的网址
#   ./v3_wifi.sh status            现在什么情况（网卡/连的哪个/拿到的 IP/服务在不在）
#   ./v3_wifi.sh down              断开（不拔网卡）
#
# ★★ 为什么这个脚本能成立（2026-09-30 上板查实，**只差一块网卡**）：
#   · 内核里 **CFG80211=y / MAC80211=y / WLAN=y**（编进内核，不是模块）
#   · USB 网卡驱动都在盘上：rtl8xxxu.ko / rtl8192cu.ko / rt2800usb.ko / mt7601u.ko
#   · 固件也都在：mt7601u.bin / rt2870.bin / rtlwifi/
#   · 用户态工具齐：wpa_supplicant / wpa_cli / iw / iwconfig / iwlist / dhclient
#   · PYNQ 3.1 还自带 `pynq.lib.wifi.Wifi`（本脚本不用它，但可以交叉验证）
#   ⇒ **买网卡认准 RTL8188EU / RTL8192CU / RT5370 / MT7601U 芯片**（内核自带驱动）；
#     别买 RTL8811CU/RTL8812AU/RTL8821CU 那种 AC 双频的（驱动要自己编译，赛前别碰）。
#
# ★ 板子本身**没有 WiFi 芯片**（/sys/class/net 只有 eth0/dummy0/lo/sit0），
#   所以"真无线"必须有这块网卡。没网卡时的替代：手机 USB 共享网络 / 网线插路由器。

IFACE="${WIFI_IF:-}"          # 不给就自动找 wlan*

die() { echo "!! $*"; exit 9; }

# ---- 找网卡 ----------------------------------------------------------------
find_iface() {
  if [ -n "$IFACE" ]; then echo "$IFACE"; return; fi
  for d in /sys/class/net/*; do
    n=$(basename "$d")
    case "$n" in wlan*|wlx*) echo "$n"; return;; esac
  done
  echo ""
}

need_iface() {
  IFACE=$(find_iface)
  if [ -z "$IFACE" ]; then
    echo "!! 找不到无线网卡（没有 wlan* 接口）"
    echo "   1) 网卡插上了吗？看 USB 认没认出来："
    lsusb 2>/dev/null | grep -viE "root hub|linux foundation" | sed 's/^/      /'
    echo "   2) 认出来了但没 wlan* -> 驱动没绑上，看内核日志："
    echo "      dmesg | tail -20"
    echo "   3) 什么都没认出来 -> 换个 USB 口试 / 换网卡（认准 RTL8188EU/8192CU/RT5370/MT7601U）"
    exit 9
  fi
}

case "$1" in
  scan)
    need_iface
    echo "[wifi] 网卡：$IFACE"
    ip link set "$IFACE" up 2>/dev/null
    echo "[wifi] 扫热点中（约 5 秒）……"
    # iw 优先；老内核/老网卡退回 iwlist
    if command -v iw >/dev/null 2>&1; then
      iw dev "$IFACE" scan 2>/dev/null | awk '
        /^BSS/            {sig=""; ssid=""}
        /signal:/         {sig=$2}
        /SSID:/           {sub(/^[ \t]*SSID: /,""); ssid=$0; if(ssid!="") printf "   %-32s %s dBm\n", ssid, sig}'
    else
      iwlist "$IFACE" scan 2>/dev/null | grep -E "ESSID|Signal level" | sed 's/^ */   /'
    fi
    ;;
  up)
    SSID="$2"; PSK="$3"
    [ -n "$SSID" ] || die "用法：./v3_wifi.sh up \"热点名\" \"密码\""
    [ -n "$PSK" ]  || die "还要给密码（WPA/WPA2）：./v3_wifi.sh up \"热点名\" \"密码\""
    need_iface
    echo "[wifi] 网卡：$IFACE"
    ip link set "$IFACE" up
    # ★ wpa_passphrase 生成配置（密码落在 /dev/shm，tmpfs，不写 SD 卡）
    # ★★ 2026-10-07 修（"连上了却误报没连上"实锤，现场咬了第二次）：本镜像
    #   **/run/wpa_supplicant 目录不存在** ⇒ wpa_supplicant 建不了控制套接字 ⇒
    #   wpa_cli 永远查不到 COMPLETED ⇒ 脚本误报失败（实际 ~2 秒就关联上了，
    #   dmesg/iw 都能看到）。三处修：
    #     ① mkdir 控制目录；② 配置显式 ctrl_interface（不赌编译默认）；
    #     ③ 等待环补一条"驱动说连上了就算数"的兜底判据（iw 直读，不依赖套接字）。
    CONF=/dev/shm/wpa_$IFACE.conf
    # ★★ 2026-10-15 坑（与 game_v3.sh 里 /tmp/_pp.log 同族）：本板
    #   fs.protected_regular=2 —— sticky 的 /dev/shm 里，**连 root 也不许**
    #   O_CREAT 覆盖**别人名下**的旧文件（EACCES，rm 却可以）。上一份 conf 若是
    #   xilinx 身份跑脚本时留下的（今天实测：第一次忘加 sudo 就会），root 重跑
    #   必挂“wpa_passphrase 失败”。⇒ 先删旧再说（新文件 O_CREAT 谁都行）。
    rm -f "$CONF"
    mkdir -p /run/wpa_supplicant
    { echo "ctrl_interface=/run/wpa_supplicant"; echo "update_config=0"
      wpa_passphrase "$SSID" "$PSK"; } > "$CONF" || die "wpa_passphrase 失败"
    pkill -f "wpa_supplicant.*$IFACE" 2>/dev/null
    sleep 0.5
    wpa_supplicant -B -i "$IFACE" -c "$CONF" >/dev/null 2>&1 || die "wpa_supplicant 起不来"
    echo "[wifi] 关联中……"
    ok=""
    for i in $(seq 1 30); do
      st=$(wpa_cli -i "$IFACE" status 2>/dev/null | awk -F= '/^wpa_state/{print $2}')
      [ "$st" = "COMPLETED" ] && ok=1 && break
      # 兜底：套接字不可用时直接问驱动"关联上了没"（3 秒后才开始判，给握手留时间）
      if [ "$i" -ge 3 ] && iw dev "$IFACE" link 2>/dev/null | grep -q "^Connected to"; then
        ok=1
        echo "[wifi] （wpa_cli 查不到状态，但驱动显示已关联 —— 按已连上继续）"
        break
      fi
      sleep 1
    done
    if [ -z "$ok" ]; then
      echo "!! 没连上（30 秒内没关联上）。当前状态："
      wpa_cli -i "$IFACE" status 2>/dev/null | sed 's/^/      /'
      iw dev "$IFACE" link 2>/dev/null | sed 's/^/      /'
      echo "   热点名/密码对不对？2.4G 的 SSID 更稳（有些老网卡只认 2.4G）"
      exit 9
    fi
    echo "[wifi] 已关联 ✓ 拿 IP 中……"
    dhclient -1 -r "$IFACE" >/dev/null 2>&1
    dhclient -1 "$IFACE" >/dev/null 2>&1 || echo "   (dhclient 没成功，可能热点不开 DHCP —— 手动 ip addr add)"
    IP=$(ip -4 -o addr show "$IFACE" | awk '{print $4}' | cut -d/ -f1 | head -1)
    if [ -z "$IP" ]; then
      echo "!! 关联上了但没拿到 IP。手动试试：ip addr add 192.168.1.50/24 dev $IFACE"
      exit 9
    fi
    echo "[wifi] ✓ $IFACE = $IP"
    # ★ 2026-10-30：再加一个**固定别名** .50（手机浏览器书签用；DHCP 变了也不怕）。
    #   网段取 DHCP 拿到的前三段 —— 手机热点网段通常固定，别名就固定。
    _PFX=$(echo "$IP" | cut -d. -f1-3)
    _STIP="$_PFX.50"
    ip addr add "$_STIP/24" dev "$IFACE" 2>/dev/null && echo "[wifi] ✓ 固定别名 $_STIP（手机开 http://$_STIP:8080/）"
    "$0" status
    ;;
  down)
    need_iface
    dhclient -r "$IFACE" >/dev/null 2>&1
    wpa_cli -i "$IFACE" terminate >/dev/null 2>&1
    pkill -f "wpa_supplicant.*$IFACE" 2>/dev/null
    echo "[wifi] 已断开（网卡还在）"
    ;;
  status)
    IFACE=$(find_iface)
    echo "== 网卡 =="
    if [ -z "$IFACE" ]; then
      echo "   没有 wlan* —— 网卡没插/没认出来（跑 ./v3_wifi.sh scan 看提示）"
    else
      echo "   $IFACE"
      ip -4 -o addr show "$IFACE" 2>/dev/null | sed 's/^/   /' || echo "   （没 IP）"
      wpa_cli -i "$IFACE" status 2>/dev/null | grep -E "^(wpa_state|ssid|ip_address|bssid)=" | sed 's/^/   /'
      # ★ 2026-10-07：控制套接字不可用时 wpa_cli 是空的 —— 用驱动直读兜底展示
      wpa_cli -i "$IFACE" status 2>/dev/null | grep -q "^wpa_state=" \
        || iw dev "$IFACE" link 2>/dev/null | grep -E "Connected to|SSID:|signal:" | sed 's/^/   /'
    fi
    echo "== 全部网口（手机要连哪个网段就看这里）=="
    ip -4 -o addr show | grep -v " lo " | sed 's/^/   /'
    echo "== 遥控服务 =="
    ss -ltn 2>/dev/null | grep -q ":8080 " && echo "   8080 在听 ✓（手机浏览器开 http://<上面那个IP>:8080/）" \
      || echo "   8080 没人听 -> 先跑 ./v3_remote.sh start"
    ;;
  *)
    sed -n '2,20p' "$0"
    exit 2
    ;;
esac
