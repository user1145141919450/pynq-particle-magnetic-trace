#!/bin/bash
# push_hdmi.sh —— 把 HDMI 首亮要用的那几个文件推上板（**逐条对 md5**）
#
# 为什么要有它：
#   2026-10-01 凌晨修了两处**会直接决定 HDMI 首亮成败**的东西 ——
#     · `hdmi_out.py`   ：v_tc 那一项改成隔离子进程探测 + 配钟后复查
#     · `hdmi_shot.sh`  ：门禁改成 `selftest --with-clk`（不带它等于没验 v_tc）
#   而板子**现在是关着的**，冷启动。上板时要保证推上去的确实是这两个新版本，
#   否则又会踩"下板了但驱动是旧的、而检查全正常"那个老坑。
#
# ★ 本工程的老规矩（都是踩出来的，别省）：
#   · scp **会静默失败** ⇒ 每次推完都要逐条比 md5，不一致就 die，绝不继续。
#   · 脚本一律从 `/home/xilinx/wp`（ext4）跑，**不要**依赖 `/boot`（FAT，曾断电丢写）。
#   · 下 bit **只有** `load_bit.py` 一个正确实现（它有"先 pl_probe 授权 → md5 对账 →
#     下载 → 立刻复查"的阶梯）。本脚本只负责把 bit **文件**送上去，不负责下载。
#
# 用法（PC 上，Git Bash）：
#   bash software/pc/push_hdmi.sh --check    # 只做 PC 侧检查，不碰板子
#   bash software/pc/push_hdmi.sh            # 检查 + 推上板 + 逐条 md5 校验

set -u
BOARD="xilinx@192.168.2.99"
WP="/home/xilinx/wp"
# ★ 本脚本在 software/pc/ 下，比 software/verify_onboard.sh **深一级**
#   ⇒ 要 `../../`，不是 `../`（第一版就写错了，`--check` 当场就报出来）
DEPLOY="$(cd "$(dirname "$0")/../../vivado/ov5640_pynq/deploy" && pwd)"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# 要推的文件。前两个是 bit/hwh，后面是驱动 —— 顺序无所谓，但**都要**逐条校验。
FILES=(
  ov5640_m1a_wrapper.bit
  ov5640_m1a_wrapper.bit.md5
  ov5640_m1a_wrapper.hwh
  hdmi_out.py
  dynclk_tables.py
  hdmi_shot.sh
  vtc_after_clk_test.py
)

say() { echo ""; echo "================ $* ================"; }
die() { echo "★ 失败：$*"; exit 1; }

md5of() { md5sum "$1" | cut -d' ' -f1; }

# ---------------------------------------------------------------------------
say "第 0 步：PC 侧清点（deploy 包必须与软件源逐字节一致）"
[ -d "$DEPLOY" ] || die "deploy 目录不存在：$DEPLOY"
for f in "${FILES[@]}"; do
  [ -f "$DEPLOY/$f" ] || die "deploy 包里缺 $f"
done
echo "  deploy 包里的 ${#FILES[@]} 个文件都在"

# ★ 门禁**不靠** deploy_check.py 的文字输出：那个脚本打印的是 **GBK**
#   （Windows 上 python 的默认 stdout 编码），拿 UTF-8 的中文去 grep 永远匹配不上 ——
#   第一版就是这么写的，结果明明包是好的却报"不一致"。
#   （这是本工程记过好几回的同一个坑：Vivado 日志也是 GBK。）
#   改成**自己直接比 md5**，与子进程的编码无关。
BOARD_DIR="$(cd "$(dirname "$0")/../board" && pwd)"
BAD=""
for f in hdmi_out.py dynclk_tables.py hdmi_shot.sh vtc_after_clk_test.py; do
  [ -f "$BOARD_DIR/$f" ] || die "软件源缺 $f（$BOARD_DIR）"
  L="$(md5of "$BOARD_DIR/$f")"
  R="$(md5of "$DEPLOY/$f")"
  if [ "$L" != "$R" ]; then
    echo "  ! $f   software/board 与 deploy **不一致**（deploy 里是旧的）"
    BAD="$BAD $f"
  fi
done
if [ -n "$BAD" ]; then
  echo ""
  echo "  过期的：$BAD"
  die "deploy 包过期 —— 先跑：python software/pc/deploy_check.py --fix"
fi
echo "  ✓ deploy/ 里这 4 个 .py/.sh 与 software/board/ **逐字节一致**"

# 顺带让 deploy_check.py 把**地址不变量**也验一遍（它拿 hdmi_out.py 的四个常量
# 去比 .hwh 里 BD 实际分配的地址）。它退出码非 0 是**正常**的：重建还在跑时
# impl_1 里没有 bit，它有一项"无法判定"。所以这里只打给人看，不当门禁。
if command -v python >/dev/null 2>&1; then
  PY=python
elif command -v python3 >/dev/null 2>&1; then
  PY=python3
else
  PY=""
fi
if [ -n "$PY" ]; then
  echo ""
  echo "  ---- deploy_check.py 的地址不变量（下面若有 '无法判定 bit' 是正常的：重建还在跑）----"
  "$PY" "$(dirname "$0")/deploy_check.py" --quiet 2>&1 | tail -14
fi

echo ""
echo "  本地 md5："
for f in "${FILES[@]}"; do
  printf "    %-30s %s\n" "$f" "$(md5of "$DEPLOY/$f")"
done

# ★ 2026-10-01：重建跑完后 `deploy/` 里的 bit 会被构建的部署步骤覆盖。
#   现在这个 = **第十一例（aux_reset_in）修好后重综合的产物**，已在板上实测：
#   已下板并跑通访问（v_tc VER=0x0602000C / CTL 0→0x7 写回成功 / dynclk LOCK=1）。
#   时序 WNS=+0.018 / WHS=+0.022（与旧 bit 逐位相同的时序数字）。
#   ★ 旧 bit（5828cda4…）的 aux_reset_in 是坏的 —— **不要再回到它**。
KNOWN_BIT="25dae086102cafffd7bfced265b60916"
B="$(md5of "$DEPLOY/ov5640_m1a_wrapper.bit")"
echo ""
if [ "$B" = "$KNOWN_BIT" ]; then
  echo "  ✓ bit 就是那个**板上实测过**的（$KNOWN_BIT，第十一例修复版）"
else
  echo "  !!! 注意：bit **换过了**"
  echo "      现在这个      ：$B"
  echo "      板上实测过的  ：$KNOWN_BIT"
  echo ""
  echo "      换掉它的那个产物**没在板上跑过**。继续推也可以 —— 只是下 bit 之前"
  echo "      你要知道自己在下哪个。（★ 别回退到 5828cda4…：那个的 aux_reset_in"
  echo "      接错了，v_tc 必死、HDMI 必黑。）"
  echo ""
  echo "      ★ 黑屏真根因与修复（2026-10-01，第十一例）："
  echo "        rst_pixel/aux_reset_in 原来接在**恒定 0** 上，而这根脚是**低有效**"
  echo "        （C_AUX_RESET_HIGH 只读=0）⇒ 辅助复位恒按着 ⇒ peripheral_aresetn"
  echo "        永远是 0 ⇒ 它**唯一**的消费者 vtc_out/resetn 被钉死 ⇒ v_tc 不产"
  echo "        720p 时序 ⇒ VDMA 0 帧、黑屏（同时解释每次访问 v_tc 都 SIGBUS）。"
  echo "        证据：①同工程**正常**的 rst_ps7_0_100M 网表里 aux_reset_in 接 1'b1，"
  echo "              我们的接常量 0（同 IP 同参数，唯一差别就是这根脚）；"
  echo "        ②proc_sys_reset RTL 1031/929/897 三行（低有效 → asr 锁存 →"
  echo "          lpf_int 恒 1 → 复位序列不前进 → peripheral_aresetn 恒 0）。"
  echo "        修法 = 新增 xl_hdmi_one（恒定 1）专喂这根脚 + BD 回读门禁。"
  echo "        ★ 旧说法「M12 不需要重建 / 该 v_tc 没有可访问的 AXI 控制寄存器"
  echo "          （C_CONTROL=0）⇒ 每访必 SLVERR」**已作废**：.hwh 里根本没有"
  echo "          C_CONTROL 这个参数，该实例有完整的 s_axi 脚（BD 门禁在查它们）。"
  echo "          旧 vtc_after_clk_test rc=1 也不能再读成'复位假说被否' —— 那个"
  echo "          实验只动了钟，**从没碰过 aux 复位这条路**，假说当时根本没被检验。"
  echo "        ★ 新 bit 上预期：v_tc 活着（VER≠0），驱动自动开 CTL.SW|RU|GE"
  echo "          （CTL 复位值=0，发生器需要软件开，旧的'自由跑'说法一并作废）。"
  echo "      ★ 时序：旧 bit 余量只有 WNS=+0.018ns，很薄；新产物**必须过"
  echo "        timing_gate.tcl**（WNS/WHS≥0）才会进 deploy/，没过会被改名"
  echo "        *.bit.TIMING_FAILED 并 exit 1（deploy/ 不动）。"
fi

if [ $CHECK_ONLY -eq 1 ]; then
  say "--check 结束（没碰板子）"
  echo "  去掉 --check 就会推上板并逐条校验 md5。"
  exit 0
fi

# ---------------------------------------------------------------------------
say "第 1 步：等板子上线（最多 6 分钟 —— 冷启动要等它起完）"
UP=0
for i in $(seq 1 36); do
  if ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=no "$BOARD" "echo up" 2>/dev/null | grep -q up; then
    echo "  板子在线（等了 $((i*10))s）"
    UP=1
    break
  fi
  sleep 10
done
[ $UP -eq 1 ] || die "等了 6 分钟板子还没上线。查：上电了吗、网线插了吗、显示器的电源线不是网口"

ssh -o ConnectTimeout=15 "$BOARD" "mkdir -p $WP" || die "建 $WP 失败"

# ---------------------------------------------------------------------------
say "第 2 步：推文件 + 逐条 md5 校验"
for f in "${FILES[@]}"; do
  L="$(md5of "$DEPLOY/$f")"
  ok=0
  for i in 1 2 3; do
    if scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 \
           "$DEPLOY/$f" "$BOARD:$WP/" >/dev/null 2>&1; then
      R="$(ssh -o ConnectTimeout=15 "$BOARD" "md5sum $WP/$f 2>/dev/null | cut -d' ' -f1")"
      if [ "$L" = "$R" ]; then ok=1; break; fi
      echo "    $f 第 $i 次 md5 不一致（$L vs ${R:-空}），重推"
    else
      echo "    $f 第 $i 次 scp 失败，重试"
    fi
    sleep 5
  done
  [ $ok -eq 1 ] || die "$f 推上去之后 md5 对不上 —— **绝不继续**（这就是当年那个静默失败）"
  printf "    ✓ %-30s %s\n" "$f" "$L"
done

# ---------------------------------------------------------------------------
say "第 3 步：补可执行位（scp 不带权限位）"
ssh -o ConnectTimeout=15 "$BOARD" "chmod +x $WP/*.sh 2>/dev/null; ls -l $WP/hdmi_shot.sh $WP/vtc_after_clk_test.py"

# ---------------------------------------------------------------------------
say "推完了。下一步（在板上 /home/xilinx/wp 里跑）"
cat <<'EOF'
  1) 确认 PL 现在的状态（这一步只读，不下 bit）：
       python3 pl_probe.py ; echo "rc=$?"
     rc=1 = 位流文件不在/没下  -> 可以下
     rc=2 = 位流在但视频流没跑 -> 可以重下
     rc=0 = 视频流在跑          -> **不要下**
     rc=9 = 说不清             -> **不要下**，先问

  2) 下 bit（唯一正确实现，别绕过）：
       echo xilinx | sudo -S -E bash -lc \
         "cd /home/xilinx/wp && /usr/local/share/pynq-venv/bin/python3 -u load_bit.py ov5640_m1a_wrapper.bit"

  3) 判决实验（v_tc 到底活着没有）：
       echo xilinx | sudo -S -E bash -lc \
         "cd /home/xilinx/wp && /usr/local/share/pynq-venv/bin/python3 -u vtc_after_clk_test.py"
     rc=0 根因坐实（配钟前 SIGBUS、配钟后读到） / rc=1 配钟后仍 SIGBUS
     rc=2 本来就没病（配钟前就读到） / rc=9 前提不足
     ★ aux 修复后的新 bit 上**预期 rc=2**（v_tc 从开机就活着）。若还是 rc=1，
       说明还有第二层问题，把输出留档别再猜。

  4) 出图（★ 先人肉确认显示器接在 HDMI **OUT** 口、输入源选对）：
       ./hdmi_shot.sh
EOF
