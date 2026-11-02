#!/bin/bash
# verify_onboard.sh — 板子一活过来，一条命令把这一版全部验完
#
# 为什么要有它：板子今晚反复硬挂（推测供电/网口问题），在线窗口可能很短。
# 这个脚本把"部署 + 四项验证"串起来，任一步失败就停并报清楚，避免又要人守着。
#
# 验证顺序（从便宜到贵，先看 4 倍压缩有没有消失）：
#   1. 部署 bit + hwh（**逐处对 md5**，防上次那种 scp 静默失败）
#   2. bram_write_map2.py —— 地址覆盖必须从 1440/5760 变成 **5760/5760 全覆盖**
#   3. wcnt_poll.py       —— 本帧实时写字数必须能爬到 5760（先前只到 1440）
#   4. vision_diag.py     —— A/B/C/D 四项；C(黄金模型) 这次才第一次**有效**
#
# 用法（PC 上，Git Bash）：bash software/verify_onboard.sh [--skip-deploy]

set -u
BOARD="xilinx@192.168.2.99"
PW="xilinx"
DEPLOY="$(cd "$(dirname "$0")/../vivado/ov5640_pynq/deploy" && pwd)"
SKIP_DEPLOY=0
[ "${1:-}" = "--skip-deploy" ] && SKIP_DEPLOY=1

say() { echo ""; echo "================ $* ================"; }
die() { echo "★ 失败：$*"; exit 1; }

# 等板子上线（最多等 6 分钟 —— 反复硬挂的环境里，等待比反复重试划算）
wait_board() {
  for i in $(seq 1 36); do
    if ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=no "$BOARD" "echo up" 2>/dev/null | grep -q up; then
      echo "板子在线（等了 $((i*10))s）"; return 0
    fi
    sleep 10
  done
  return 1
}

ensure_wp() {  # 板子重烧后 /home/xilinx/wp 会消失，而 scp 不会自动建目录
  ssh -o ConnectTimeout=15 "$BOARD" "mkdir -p /home/xilinx/wp" || die "建 /home/xilinx/wp 失败"
}

# ★ 2026-09-26 补：把 wp 下**全部** .py 都刷进 /boot（原来只 cp 正在跑的那一个）。
#   因为 vision_diag / bram_write_map2 / wcnt_poll 都 import fastmem、ball_detect，
#   而 ball_detect 又 import vdma_capture —— 这些模块也必须在 /boot 里才 import 得到
#   （python3 -u /boot/x.py 时 sys.path[0] = /boot）。
# ★ 2026-09-26 再补：zocl 重载。
#   现象：连着跑两三个 PYNQ 进程之后，下一步 `import pynq` **确定性段错误**，
#         rc=139，**一行输出都没有**（跑的是 python3 -u，不是缓冲问题），
#         dmesg 里是反复的 zocl_create_client / zocl_destroy_client + apport 收 core。
#         当时是第 2 步 bram_write_map2 刚退出、第 3/4 步双双全灭。
#   治法：`sudo modprobe -r zocl && sudo modprobe zocl`，**不用重启板子**，秒回。
#   为什么无条件重载、而不是"先探测再救"：探测本身要再起一个 PYNQ 进程，而进程
#   生命周期次数正是触发它的东西 —— 越探越糟。重载幂等、约 1s，PL 里的 bit 不受
#   影响（每个脚本自己新开 MMIO 映射，没有跨步骤的长命进程）。真有进程占着模块时
#   modprobe -r 会失败、zocl 保持原样，也不会把事情弄坏。
zocl_reload() {
  ssh -o ConnectTimeout=15 "$BOARD" \
    "echo $PW | sudo -S -E bash -c 'modprobe -r zocl 2>/dev/null; modprobe zocl 2>/dev/null; true' \
     >/dev/null 2>&1; lsmod | grep -c '^zocl'" 2>/dev/null | tail -1
}

# ★ 2026-09-26 三补：pl_probe —— 下 bit 之前先问一句"是不是已经在下边跑着了"。
#   本板实测：**往一块已经在跑的 PL 里下 bit，会让 fpga_manager 超时，把 PL 留在
#   半编程状态，随后任何 AXI 事务都可能锁死总线、整机拖垮**（ping 掉 80%、SSH 无响应、
#   只能断电）。最阴的是：上一步脚本退出后 VDMA 仍在 PL 里继续搬数据 ——
#   于是"跑完一个脚本再下 bit"恰好是最危险的动作。见 software/board/pl_probe.py 头注释。
#   退出码（2026-09-26 v2 改口径，把"bit 在不在"和"流水线跑没跑"分开）：
#     0 = 我们的 bit 在 **且帧计数在涨** -> 别再下 bit
#     1 = **我们的 bit 不在 PL 里**（被 SIGBUS 实锤）-> 必须下，且这是唯一会 SIGBUS 的状态
#     2 = 我们的 bit 在，但帧计数不动（传感器没出流）-> 下 bit 安全（还能重掷传感器骰子），
#         而且**这种状态读寄存器不会 SIGBUS**，A/B 项照测
#     9 = **探针没能得出结论**（PYNQ 自己在 import 时段错误了）-> **绝不下 bit**。
#         注意 SIGSEGV(-11) 和 SIGBUS(-7) 是两件事：前者是 PYNQ 崩了，
#         跟"我们的 bit 在不在"毫无关系（探针第一版就是在这里判反的）。
#         下错一次要断电重启整机；不下最多重跑一遍脚本 —— 所以未知一律不下。
pl_probe() {
  ssh -o ConnectTimeout=15 "$BOARD" \
    "echo $PW | sudo -S -E XILINX_XRT=/usr PYTHONIOENCODING=utf-8 bash -lc 'python3 -u /home/xilinx/wp/pl_probe.py'" \
    >/tmp/pl_probe.out 2>&1
  local rc=$?
  sed 's/^/  /' /tmp/pl_probe.out
  # ★ 传输失败不能伪装成结论：ssh 自己出错（网断了/SSH 死了）rc 是 255，sudo 失败是 1，
  #   这两个数在退出码表里**都**有别的含义（1 = "我们的 bit 不在"！），照抄进来会
  #   让关卡把"探针根本没跑起来"误读成"我们的 bit 不在"、或者反过来放行。
  #   所以只认真正跑完的探针：输出里必须出现 '[PL]' 这个标记，否则一律 rc=9（未知）。
  if ! grep -q '\[PL\]' /tmp/pl_probe.out; then
    echo "  ★ 探针没跑起来（ssh rc=$rc，输出里没有 [PL] 标记）"
    return 9
  fi
  return $rc
}

onboard() {  # onboard <脚本名>
  printf '  zocl 已重载（防 import pynq 段错误），模块计数 %s\n' "$(zocl_reload)"
  # ★ `-X faulthandler`：段错误时让 Python 自己把**当前栈**打到 stderr。
  #   本板那个间歇性 SIGSEGV 至今只有"rc=139 + 一行输出都没有"这一个症状，
  #   什么都定位不了；faulthandler 能在崩的那一刻把"死在 import pynq 的哪一行"
  #   钉死（纯 C 崩溃也能打出 Python 层栈）。它只在致命信号时才有输出，平时零影响。
  ssh -o ConnectTimeout=10 "$BOARD" \
    "echo $PW | sudo -S -E XILINX_XRT=/usr PYTHONIOENCODING=utf-8 bash -lc 'cp /home/xilinx/wp/*.py /boot/ && python3 -X faulthandler -u /boot/$1 ${2:-}'"
}

# ---------------------------------------------------------------- 1. 部署
if [ $SKIP_DEPLOY -eq 0 ]; then
  say "1/4 部署 bit + hwh"
  wait_board || die "板子 6 分钟没上线 —— 需要物理复位/查供电"
  ensure_wp
  L=$(md5sum "$DEPLOY/ov5640_m1a_wrapper.bit" | cut -d' ' -f1)
  echo "本地 bit md5 = $L"
  for i in 1 2 3 4 5; do
    scp -o StrictHostKeyChecking=no -o ConnectTimeout=20 \
        "$DEPLOY/ov5640_m1a_wrapper.bit" "$DEPLOY/ov5640_m1a_wrapper.hwh" "$BOARD:/home/xilinx/wp/" >/dev/null 2>&1 && break
    echo "  scp 重试 $i"; sleep 6
  done
  R=$(ssh -o ConnectTimeout=15 "$BOARD" "md5sum /home/xilinx/wp/ov5640_m1a_wrapper.bit | cut -d' ' -f1") || die "读不回远程 md5"
  [ "$L" = "$R" ] || die "★ scp 后 md5 不一致（$L vs $R）—— 绝不继续，否则又在旧 bit 上验收"
  echo "scp 后 md5 一致 ✓"
  ssh -o ConnectTimeout=15 "$BOARD" \
      "echo $PW | sudo -S cp -f /home/xilinx/wp/ov5640_m1a_wrapper.bit /home/xilinx/wp/ov5640_m1a_wrapper.hwh /boot/ && md5sum /boot/ov5640_m1a_wrapper.bit | cut -d' ' -f1" \
      | tail -1 | grep -q "$L" || die "/boot 里的 bit md5 不对"
  echo "/boot 里 md5 一致 ✓"
else
  say "1/4 跳过部署（--skip-deploy）"
fi

# ---------------------------------------------------------------- 2. 地址覆盖
say "2/4 地址覆盖（判据：从 1440/5760 变成 5760/5760 全覆盖）"
ensure_wp
# ★ 公共依赖：三个脚本都会 import，必须一起上去（少一个，第一句 import 就炸）
DEPS="fastmem.py ball_detect.py vdma_capture.py vdma_state.py"
for f in $DEPS; do
  scp -o ConnectTimeout=20 "$(dirname "$0")/board/$f" "$BOARD:/home/xilinx/wp/" >/dev/null 2>&1 \
    || die "传公共依赖 $f 失败"
done
echo "公共依赖已就位: $DEPS"
scp -o ConnectTimeout=20 "$(dirname "$0")/board/bram_write_map2.py" "$(dirname "$0")/board/pl_probe.py" "$BOARD:/home/xilinx/wp/" >/dev/null 2>&1
ssh -o ConnectTimeout=15 "$BOARD" "echo $PW | sudo -S cp -f /home/xilinx/wp/vdma_state.py /boot/ >/dev/null 2>&1; true" 2>/dev/null

# ★★ 先确认"没有野 DMA"，再谈下不下 bit（2026-09-26 第二十版）
#   上一轮脚本**崩着退出**（段错误 / SIGKILL）时不会执行 atexit，而 VDMA 是 PL 硬件、
#   不随进程退出而停 —— 它会带着**已经释放的**缓冲地址继续跑。实测（第 5 轮验收之后）：
#   DMACR.RS=1、halted=0、错误位 0x19（dma_interr + fsize_err + lsize_err）。
#   那种状态下两件事都会出事：①下 bit 有可能撞上活着的 DMA 流量
#   （fpga_manager 超时 -> PL 半编程 -> 整机挂死）；②新 allocate 到的 CMA 页
#   立刻被它覆盖（第 5 轮的 3fps / 撕裂帧 / errs=0x08 就是这么来的）。
#   这里先停掉它：只清 RS，不写 RESET，对后面的采集没有任何副作用。
halt_vdma() {
  ssh -o ConnectTimeout=15 "$BOARD" \
    "echo $PW | sudo -S -E XILINX_XRT=/usr PYTHONIOENCODING=utf-8 bash -lc 'python3 -u /home/xilinx/wp/vdma_state.py --halt'" \
    2>&1 | sed -e 's/^\[sudo\] password for [^:]*: //' -e 's/^/  /'
}
echo "---- 关卡：先看有没有野 DMA（上一轮崩着退出时留下的）----"
halt_vdma

# ★★ 下 bit 之前先问一句 —— 全程只有这一处可能下 bit
pl_probe; PL_STATE=$?
case $PL_STATE in
  0) echo "  => **我们的流水线正在跑**：本次全程不下 bit（--nobit 接力）"
     BIT_ARG="--nobit" ;;
  2) echo "  => 我们的 bit 在，但帧计数不动（多半是传感器掷到坏骰子）。"
     echo "     此时下 bit 是安全的（没有活着的 DMA 在跟 fpga_manager 抢 PL），"
     echo "     而且重下能顺手**重掷一次传感器状态的骰子** —— 下，值得。"
     echo "     注：这种状态读寄存器不会 SIGBUS，所以就算重下后传感器还是死的，"
     echo "     A/B 项照测得出结果，只是会报视频流不健康。"
     BIT_ARG="" ;;
  1) echo "  => **我们的 bit 不在 PL 里**（SIGBUS 实锤）：必须下 bit"
     echo "     （**本脚本全程只下这一次**；这一步同时写下 PYNQ 的全局状态文件 ——"
     echo "       没有它，后面 --nobit 的进程连 allocate() 都过不了，会报"
     echo "       Overlay is not downloaded）"
     BIT_ARG="" ;;
  9) echo "  ★ 探针**没能得出结论**（rc=9：多半是 import pynq 段错误，跟 bit 在不在无关）"
     echo "    => **不下 bit**。下错一次要断电重启整机；不下最多重跑一遍脚本。"
     echo "       用 --nobit 接着跑：这一轮的 PL 到底能不能用，第 2 步自己会报出来。"
     BIT_ARG="--nobit" ;;
  *) echo "  ★ 探针给了个没见过的码（rc=$PL_STATE，脚本 bug 或 SSH 传输出错）"
     echo "    => 按未知处理，**不下 bit**，用 --nobit 接着跑。"
     echo "       注：开机时 BOOT.BIN 配进来的默认 bit 会让 fpga_manager state 一直"
     echo "       显示 operating，**别拿 state 当依据**（探针 v1 就是在这判反的）。"
     BIT_ARG="--nobit" ;;
esac

onboard bram_write_map2.py "$BIT_ARG" 2>&1 | tee /tmp/step2.out | tail -22

# ★ 关卡：第 2 步必须把我们的 bit 真的配进去，否则第 3/4 步 --nobit 一读寄存器就 SIGBUS
#   （读一个不存在的外设 = 总线错误；SIGBUS 不是 Python 异常，try/except 拦不住，
#    脚本会一行结论都出不来 —— 2026-09-26 就是这么全灭的）。
#
#   ★★ 这里**绝不能再起一个新 PYNQ 进程去"复查"** —— 第一版就是那么写的，自己翻车：
#      紧跟在 bram_write_map2 退出之后起的新进程，`import pynq` 会**确定性段错误**
#      （本板那个老毛病：rc=139 / SIGSEGV、一行输出都没有，跟"我们的 bit 在不在"
#      **毫无关系**）。实测：第 2 步刚刚证明流水线在跑（5760 字全被覆盖、
#      frame_cnt 283->313），紧接着的探针却死了两次（-7 一次、-11 一次）——
#      全是假警报，差点把一轮已经成功的验收判成失败。
#      （为什么 onboard() 里没事：它每次都先 zocl_reload。探针那条路没有。）
#
#   ★ 正确判据就在**第 2 步自己的输出里**：`[加载] bit 已下载` = 我们的 bit 配进去了，
#     而且那个进程刚刚成功读写过 MMIO（它自己动过 ctrl 和 frame_cnt）。不用再问第二遍。
echo ""
echo "  ---- 关卡：我们的 bit 到底配上去了没有 ----"
# ★ 判据用 ASCII 锚点 `ctrl=0x`（`[配置] ctrl=0x00` 那一行）：它是第 2 步**第一次真正
#   访问 MMIO**的产物。它打出来了 = 我们的寄存器读得通 = 第 3/4 步 --nobit 不会 SIGBUS。
#   这一条比"有没有下 bit"更本质：无论走 --nobit 还是下载，只要 MMIO 通就行。
if grep -q 'ctrl=0x' /tmp/step2.out; then
  echo "  => 第 2 步成功读写过 MMIO（ctrl / frame_cnt 都读到了）✓"
  echo "     我们的 bit 确实在 PL 里 —— 第 3/4 步 --nobit 接力是安全的"
  grep 'frame_cnt' /tmp/step2.out | sed 's/^/     /'
  echo "     （上面帧计数在涨 = 视频流健康；不涨 = 传感器没出流，这时第 3/4 步"
  echo "       照样跑得完、A/B 项照测，但 C/D 会报不合格 —— 重跑本脚本重掷骰子）"
else
  die "第 2 步**没能成功访问 MMIO**（输出里没有 'ctrl=0x' 那一行）——
       说明我们的 bit 不在 PL 里，或者脚本半路就崩了。
       第 3/4 步 --nobit 一读寄存器就 SIGBUS，就此打住。看上面第 2 步的输出：
         · 有 fpga_manager / TimeoutError -> 下载本身失败（多半被上一条采集链抢了 PL）
         · 有 SIGSEGV / 段错误 / 一行输出都没有 -> 本板那个 import pynq 老毛病，
           跟 bit 没关系，重跑本脚本
         · 什么都没有、ssh 就断了 -> 板子可能又硬挂了，先 ping 一下
         · 探针这轮报的是 rc=$PL_STATE
       重跑本脚本 = 重掷一次骰子。"
fi

# ---------------------------------------------------------------- 3. 实时写字数
say "3/4 本帧实时写字数（判据：能爬到 5760）"
# ★ 第 3/4 步一律 --nobit 接力：此时 PL 里一定有一条活着的采集链（第 2 步刚点起来，
#   而且 VDMA 是 PL 硬件、不随进程退出而停），**在这里下 bit 就是把整机搞挂的动作**。
#   这也正是项目自己的老规矩（stream_probe/run_selfcheck 的 noprog 接力链）。
scp -o ConnectTimeout=20 "$(dirname "$0")/board/wcnt_poll.py" "$BOARD:/home/xilinx/wp/" >/dev/null 2>&1
onboard wcnt_poll.py "--n 120 --nobit" 2>&1 | tail -8

# ---------------------------------------------------------------- 4. 完整验收
say "4/4 vision_diag 完整验收（A 结构 / B 错误位 / C 黄金模型 / D 帧率）"
scp -o ConnectTimeout=20 "$(dirname "$0")/board/vision_diag.py" "$(dirname "$0")/pc/vision_golden.py" "$BOARD:/home/xilinx/wp/" >/dev/null 2>&1
onboard vision_diag.py "--nobit" 2>&1 | tail -30

say "完成"
