#!/bin/bash
# hdmi_shot.sh —— 板上"一条命令验 HDMI 输出"。P2 屏幕部分的现场脚本。
#
#   ./hdmi_shot.sh              ★ 默认：自检 → 配钟 → （跳过 vtc）→ 彩条 → 保持 30 秒数帧
#   ./hdmi_shot.sh --selftest     自检：配一次 dynclk 然后验四个 IP（vtc 按正常件判）
#   ./hdmi_shot.sh --hold         出图后一直保持（Ctrl-C 退出，退出时自动停干净）
#   ./hdmi_shot.sh --syncen       给 VDMA 加 genlock 同步使能（**默认不加**，见下）
#   ./hdmi_shot.sh --stop         收尾：停 DMA + 关 vtc + 关钟
#
# ============================================================================
# 上板前必须**人肉**保证的三件事（脚本查不了，只能靠眼睛）
# ============================================================================
#   1. 显示器接在 PYNQ-Z2 的 **HDMI OUT** 口（板子**另一个** HDMI 口是 IN，接错了
#      永远没画面，而且软件侧看不出任何区别）。
#   2. 显示器输入源切到那个口（HDMI1/HDMI2 别选错）。
#   3. 板子已上电、**且已经下载了本次新综合的 bit**（HDMI IP 是这次才加进去的，
#      老 bit 里根本没有 vtc/dynclk/rgb2dvi → 跑这个脚本只会读回一堆 0）。
#
# ★ 关于下 bit：本脚本**不负责**下 bit。本工程下 bit 只有**一个**正确实现
#   = `load_bit.py`（它带"先 pl_probe 授权 → md5 对账 → 下载 → 立刻复查"的阶梯，
#   以及"开机 150 秒窗口内拒绝"的保护）。盲目下 bit 踩过的坑：
#   PL 半编程 → MMIO 读挂死总线 → 只能断电。
#   所以：先 `python3 load_bit.py <新 bit>`，再回来跑本脚本。
#
# ============================================================================
# ★★ python3 必须用 PYNQ venv 里那个（本板有两个 python3，这是第三个同类坑了）
# ============================================================================
#   登录 shell 的 PATH 里是 /usr/local/share/pynq-venv/bin/python3（**有 pynq**），
#   而 /usr/bin/python3 是系统 python（**没有 pynq**）。
#   选错的症状是 import pynq 失败，会被误判成"硬件/环境坏了"。
# ★ 另外两条本板铁律：
#   · 脚本一律从 /home/xilinx/wp（ext4）跑，**不要**依赖 /boot（FAT，曾断电丢写、
#     而且内核把它挂成了只读）。
#   · 要 root 权限时不能只 sudo：xilinx 不在 render/video 组，
#     得 `echo xilinx|sudo -S -E bash -lc "..."`（两个都要）。
PY="${PY:-/usr/local/share/pynq-venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"
if ! "$PY" -c 'import pynq' >/dev/null 2>&1; then
  echo "!! $PY 里 import pynq 失败 —— 这个 python3 不是 PYNQ venv 的那个"
  echo "   本板有两个 python3，正确的是 /usr/local/share/pynq-venv/bin/python3"
  echo "   试：PY=/usr/local/share/pynq-venv/bin/python3 ./hdmi_shot.sh"
  exit 9
fi
echo "[env] python3 = $PY"
echo "[env] cwd     = $(pwd)"

# 本脚本和驱动必须在同一个目录（hdmi_out.py 要 import 同目录的 dynclk_tables）
cd "$(dirname "$0")" || exit 9

MODE="run"
case "${1:-}" in
  --selftest) MODE="selftest" ;;
  --hold)     MODE="run"; HOLD="--hold" ;;
  --syncen)   MODE="run"; SYNCEN="--syncen" ;;
  --stop)     MODE="stop" ;;
  "")         MODE="run" ;;
  *) echo "未知参数：$1（看脚本头部的用法）"; exit 2 ;;
esac

echo "=============================================================="
echo "  HDMI 输出现场验证  (模式=$MODE)"
echo "=============================================================="

# ---- 第 1 步：自检。**不过就不往下走** ----
# 为什么把它当硬门禁：地址错/bit 没下 的时候，"继续跑"只会产生一堆
# 看起来像硬件故障的读数，把真正的原因（bit 不对）埋掉。
echo
echo "--- [1/2] 自检（配钟 + 验地址与 IP 是否活着）---"
# ★ 为什么带 --with-clk：**像素钟（dynclk）本身是 HDMI 通路的必需件**，门禁顺带把它
#   起起来验一遍；不带它的话，dynclk 只被读到"REF 值对不对"，没验"起没起来"。
# ★ v_tc（0x43C20000）现在是**正常件**（2026-10-01 深夜·第十一例改口径）：
#   黑屏真根因 = `rst_pixel/aux_reset_in` 原接恒定 0（低有效）⇒ vtc_out/resetn
#   被钉死 ⇒ 访问全 SIGBUS。修好并重下 bit 后 VER 读得到、CTL 写得进 ⇒
#   自检这一项**按正常判**（VER≠0 即 OK）。"没有 AXI 控制寄存器/自由跑/SIGBUS
#   是预期" 三条**全部作废**；发生器必须软件开（驱动 vtc_enable() 已做）。
if ! "$PY" hdmi_out.py selftest --with-clk; then
  echo
  echo "!! 自检没过 —— 不继续（继续只会浪费上板窗口）"
  echo "   最常见的原因，按可能性排序："
  echo "     a) 板子上的 bit 还是**旧**的（HDMI 的 IP 是这次才加的）"
  echo "        → 先 python3 load_bit.py <新 bit> 再回来跑"
  echo "     b) 地址不是 0x43C00000/0x43C10000/0x43C20000/0x43C30000"
  echo "        → 打开 deploy/ov5640_m1a_wrapper.hwh 搜 axi_dynclk/vtc_out 的 baseaddr"
  echo "     c) bit 下载过程本身出问题了 → load_bit.py 会自己报，看它的输出"
  echo "     d) 上面出现 'Bus error' / rc=135 / SIGBUS → 那个地址**没有任何从机应答**。"
  echo "        ★ 只有 vtc 那一行 SIGBUS 不会走到这里（它已不是门禁）；"
  echo "          真被拦住了，看的是 dynclk 那行的 STATUS.bit0 是不是 1。"
  exit 9
fi

if [ "$MODE" = "selftest" ]; then
  echo
  echo "自检完成。（★ 本模式配过一次 dynclk —— 那是 HDMI 通路的必需件；v_tc 已在"
  echo "   第十一例修好后按正常件判。时钟现在还在跑，要关用 --stop）"
  echo "下一步：确认显示器接在 HDMI **OUT** 口、输入源选对，然后跑 ./hdmi_shot.sh"
  exit 0
fi

# ---- 第 2 步：出图 ----
echo
echo "--- [2/2] 出图 ---"
if [ "$MODE" = "stop" ]; then
  "$PY" hdmi_out.py stop
  exit $?
fi

echo "★ 现在应该能在显示器上看到：上半部 3/4 = 8 条竖彩条"
echo "  （白/黄/青/绿/品红/红/蓝/黑），下半部 1/4 = 左黑右白的水平渐变。"
echo
# shellcheck disable=SC2086
"$PY" hdmi_out.py run --seconds 30 $HOLD $SYNCEN
RC=$?
echo
echo "=============================================================="
if [ $RC -ne 0 ]; then
  echo "驱动返回 $RC —— 看上面的输出"
else
  echo "驱动正常退出。**对照显示器上的实际画面**，按下表判读："
fi
cat <<'EOF'

  画面现象                      → 指向                     → 下一步
  ---------------------------------------------------------------------------
  完全黑（连背光提示都没有）     → 显示器认为"无信号"        → TMDS 引脚约束/差分对
  黑屏但显示器有背光/菜单        → TMDS 有信号但时序空/无数据 → 先看驱动打印：v_tc 的
                                                              CTL.GE 置没置、VDMA 帧率。
                                                              帧率 0 ⇒ 先 --dmacr 0x5001
                                                              对照（第十二例），再查
                                                              gen_clken 布线（BD 里钉在
                                                              常量 1，与官方参考不同）
  彩条颜色正常 ★               → 通路的每一段都对          → 收工
  彩条在、但**成对互换**        → rgb2dvi 的 G/B 字节序没补上 → 改 hdmi_out.py 的
  （黄↔品红、绿↔蓝；白/黑/                         fill_bars() 的打包（只改这一处；
    红/青不动 —— 2026-10-01                        BD 若加 color_swap 则反向改）
  花屏/斜纹/滚动/闪             → 像素钟频率不对或时序不符  → 看驱动打印的"像素钟 = ..."
                                                              （应 ≈74.29MHz）
  只有画面一部分/边缘撕裂       → 分辨率或 HSIZE/STRIDE 不符 → 确认 1280x720 与
                                                              vtc 建 BD 时的 C_GEN_* 一致
                                                              （软件改不了它，只能重综合）
  显示器亮但全灰/全白           → 数据线全 0/全 1            → TMDS data 的引脚约束
EOF
echo
echo "收尾：脚本退出时驱动会自动停 DMA/vtc/时钟（atexit）。"
echo "      要手动停：./hdmi_shot.sh --stop"
echo "★ 复跑前先查野 DMA：hdmi_out.py selftest 会打印'无野 DMA'那一行。"
exit $RC
