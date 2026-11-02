#!/bin/bash
# ============================================================================
# run_v3_sim.sh —— V3 轨迹束的四档 xsim 自检，一条命令跑完并给出**可信**的 PASS/FAIL
#
# ----------------------------------------------------------------------------
# 为什么要有这个脚本，而不是照 TB 头部的注释手敲：
# ----------------------------------------------------------------------------
#   tb_traj_bundle.v 头部写着"用法（在工程根目录跑）：
#       xvlog rtl/v3_bundle/traj_lane.v ..."  —— **这条注释是错的**。
#   能跑通的 cwd 是**仓库根** E:/pynq-magnetic-field-demo，而且路径前缀是
#   vivado/ov5640_pynq/ ：
#       · 向量在 <仓库根>/sim/v3/{fan,goal}/      （工程目录底下**没有** sim/）
#       · RTL  在 <仓库根>/vivado/ov5640_pynq/rtl/v3_bundle/
#                                                 （仓库根底下**没有** rtl/）
#   两个目录都不满足注释里写的相对路径 —— 照注释敲必然"找不到文件"。
#   （本项目"文档说谎"家族：§5c 门禁、max_bounce 死输入、RTL 顶部过期的寄存器图注释。）
#
# ----------------------------------------------------------------------------
# 为什么 PASS 的判据是**日志标记**而不是退出码：
# ----------------------------------------------------------------------------
#   同 Vivado 那条教训 —— 退出码不可信（未捕获错误也返回 0）。
#   ★ 更关键：本脚本会**断言请求的 case 真的被加载了**。
#     这正是 case_name[23:0] 那个 bug 的永久防线：
#     当时 `CASE=goal` **静默地**跑 fan 的向量，两套向量都 PASS，
#     光看 ">>> PASS" 永远发现不了，必须看 "case=xxx" 那行印的是谁。
#     所以：case 名对不上 => 直接判不通过，哪怕结果是 PASS。
#
# 用法：  ./sim/run_v3_sim.sh              （PC 上跑，不碰板子、不碰相机）
#         VIVADO_BIN=/path/to/bin ./sim/run_v3_sim.sh   （Vivado 不在默认位置时）
# 退出码：0 = 四档全绿；1 = 有档不通过；9 = 环境/向量缺失（不是 RTL 的问题）
# ============================================================================
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || { echo "!! 进不了仓库根 $ROOT"; exit 9; }

VDIR="${VIVADO_BIN:-}"
pick_tool() {
    if command -v "$1" >/dev/null 2>&1; then command -v "$1"; return 0; fi
    for ext in .bat "" .exe; do
        if [ -x "$VDIR/$1$ext" ]; then echo "$VDIR/$1$ext"; return 0; fi
    done
    return 1
}
XVLOG="$(pick_tool xvlog)" || { echo "!! 找不到 xvlog（Vivado 装了没？VIVADO_BIN 可以指路）"; exit 9; }
XELAB="$(pick_tool xelab)" || { echo "!! 找不到 xelab"; exit 9; }
XSIM="$(pick_tool xsim)"   || { echo "!! 找不到 xsim";  exit 9; }

RTL="$ROOT/vivado/ov5640_pynq/rtl/v3_bundle"
LOGD="$ROOT/sim/_logs"
mkdir -p "$LOGD"

echo "=============================================================="
echo "  V3 轨迹束 xsim 自检   （cwd = $ROOT）"
echo "=============================================================="
echo "  xvlog = $XVLOG"
echo "  rtl   = $RTL"

# ---- 硬门禁：向量在不在。缺了就直接停，别让 $readmemh 拿 X 值跑出一堆假 FAIL ----
miss=0
for c in fan goal bounce phys; do
    for f in blk_tab hole_tab lane_v exp params; do
        [ -f "sim/v3/$c/$f.mem" ] || { echo "!! 缺向量：sim/v3/$c/$f.mem"; miss=1; }
    done
done
[ -f "sim/v3/phys/phys.mem" ] || { echo "!! 缺向量：sim/v3/phys/phys.mem"; miss=1; }
[ "$miss" = 0 ] || { echo "!! 向量不全 —— 先查 sim/v3/ 是不是被挪走了"; exit 9; }
echo "  向量  = sim/v3/{fan,goal,bounce}/  五个 .mem 齐"
echo

for f in traj_lane.v traj_bundle.v v3_bundle_axi.v tb_traj_bundle.v tb_v3_bundle_axi.v; do
    [ -f "$RTL/$f" ] || { echo "!! 缺 RTL：$RTL/$f"; exit 9; }
done

# ---- 编译（两个 TB 一起，模块名不冲突）----
echo "--- 编译 ---"
if ! "$XVLOG" "$RTL/traj_lane.v" "$RTL/traj_bundle.v" "$RTL/v3_bundle_axi.v" \
              "$RTL/tb_traj_bundle.v" "$RTL/tb_v3_bundle_axi.v" > "$LOGD/xvlog.out" 2>&1; then
    echo "!! 编译失败 —— 看 $LOGD/xvlog.out"; tail -20 "$LOGD/xvlog.out"; exit 1
fi
echo "  编译通过"
echo

# ---- 跑一档 ----
# $1 = 顶层 TB 名   $2 = case 名（fan / goal）
NFAIL=0
run_one() {
    tb="$1"; cs="$2"; snap="${tb}_${cs}"
    echo "--- $tb  case=$cs ---"
    if ! "$XELAB" -debug typical "$tb" -s "$snap" > "$LOGD/$snap.elab" 2>&1; then
        echo "   !! 精化(xelab)失败 —— 看 $LOGD/$snap.elab"; tail -15 "$LOGD/$snap.elab"
        NFAIL=$((NFAIL+1)); return
    fi
    # ★ plusarg 走**响应文件**：cmd.exe/Git-Bash 会把 `-testplusarg CASE=goal` 里的 `=`
    #   搞坏（报 "Expected a switch but found g"）。一行一个 token 就没这个问题。
    printf -- '-runall\n-testplusarg\nCASE=%s\n' "$cs" > "$LOGD/$snap.opts"
    "$XSIM" "$snap" -f "$LOGD/$snap.opts" > "$LOGD/$snap.out" 2>&1

    # ---- 判据 ----
    # ① 印出来的 case 名必须是我们要的那一档（防"静默跑错向量"）
    if grep -q "case=$cs" "$LOGD/$snap.out" || grep -q "v3/$cs/" "$LOGD/$snap.out"; then
        echo "   加载的 case 对：$(grep -m1 -o "case[= ] *[^ ]*" "$LOGD/$snap.out" | head -1)"
    else
        echo "   ★★ case 名对不上！请求 $cs，日志里找不到它的指纹 —— 判不通过"
        echo "      （这一档历史上就是靠这个才揪出 case_name 位宽 bug 的）"
        NFAIL=$((NFAIL+1)); return
    fi
    # ② 结果标记
    if grep -q '>>> FAIL' "$LOGD/$snap.out"; then
        echo "   ★ FAIL："; grep '>>> FAIL' "$LOGD/$snap.out"
        grep -E '^  \* ' "$LOGD/$snap.out" | head -10
        NFAIL=$((NFAIL+1)); return
    fi
    if grep -q '>>> PASS' "$LOGD/$snap.out"; then
        echo "   PASS  $(grep -m1 '>>> PASS' "$LOGD/$snap.out" | sed 's/^>>> //')"
    else
        echo "   ★★ 既没有 PASS 也没有 FAIL —— 跑完了吗？（看 $LOGD/$snap.out）"
        NFAIL=$((NFAIL+1)); return
    fi
}

run_one tb_traj_bundle   fan
run_one tb_traj_bundle   goal
run_one tb_traj_bundle   bounce
run_one tb_traj_bundle   phys
run_one tb_v3_bundle_axi fan
run_one tb_v3_bundle_axi goal
run_one tb_v3_bundle_axi bounce

echo
echo "=============================================================="
if [ "$NFAIL" = 0 ]; then
    echo "  六档全绿：17 路结果与定点黄金模型**逐位一致**（含 goal 撞墙/进洞、bounce 弹性回弹档）"
    echo "  日志：$LOGD/"
    exit 0
fi
echo "  ★ $NFAIL 档不通过 —— 看 $LOGD/ 下对应的 .out"
exit 1
