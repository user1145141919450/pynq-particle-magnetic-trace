#!/bin/bash
# ============================================================================
# run_map_detect_sim.sh —— 差距 1（PL 检测）第一版的一键自检
#
# 做三件事：
#   1) xvlog/xelab/xsim 跑 tb_map_detect（注入真实缩略图快照）；
#   2) TB 自己断言 med/vhi/三色计数 == PS 参考；
#   3) 可选：用 checker 把 TB 的逐像素 H/S/V 与 cv2 再对一遍（有 opencv 才跑）。
#
# 用法（仓库根目录）：  ./sim/run_map_detect_sim.sh
# 退出码：0=全绿；1=仿真/断言失败；9=环境或向量缺失
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
XVLOG="$(pick_tool xvlog)" || { echo "!! 找不到 xvlog"; exit 9; }
XELAB="$(pick_tool xelab)" || { echo "!! 找不到 xelab"; exit 9; }
XSIM="$(pick_tool xsim)"   || { echo "!! 找不到 xsim";  exit 9; }

RTL="$ROOT/vivado/ov5640_pynq/rtl/map_detect"
LOGD="$ROOT/sim/_logs"
mkdir -p "$LOGD"

echo "=============================================================="
echo "  PL 检测第一版 xsim 自检（cwd = $ROOT）"
echo "=============================================================="
echo "  xvlog = $XVLOG"

[ -f "sim/map_detect/map_snap_gap1.hex" ] || { echo "!! 缺向量 sim/map_detect/map_snap_gap1.hex"; exit 9; }
for f in map_detect_core.v map_detect_axi.v map_cc.v tb_map_detect.v tb_map_detect_axi.v tb_map_cc.v; do
    [ -f "$RTL/$f" ] || { echo "!! 缺 RTL：$RTL/$f"; exit 9; }
done

echo "--- 编译 ---"
if ! "$XVLOG" "$RTL/map_detect_core.v" "$RTL/map_cc.v" "$RTL/tb_map_detect.v" \
        > "$LOGD/mapdet_xvlog.out" 2>&1; then
    echo "!! 编译失败 —— 看 $LOGD/mapdet_xvlog.out"; tail -20 "$LOGD/mapdet_xvlog.out"; exit 1
fi
grep -q "ERROR" "$LOGD/mapdet_xvlog.out" && { echo "!! 编译报错"; grep "ERROR" "$LOGD/mapdet_xvlog.out" | head; exit 1; }
echo "  编译通过"

echo "--- 精化 ---"
if ! "$XELAB" tb_map_detect -s tb_map_detect > "$LOGD/mapdet_xelab.out" 2>&1; then
    echo "!! xelab 失败 —— 看 $LOGD/mapdet_xelab.out"; tail -15 "$LOGD/mapdet_xelab.out"; exit 1
fi
echo "  精化通过"

echo "--- 仿真（核心）---"
"$XSIM" tb_map_detect -R > "$LOGD/mapdet_xsim.out" 2>&1
tail -14 "$LOGD/mapdet_xsim.out"

if ! grep -q ">>> PASS" "$LOGD/mapdet_xsim.out"; then
    echo "!! TB 没 PASS"; exit 1
fi

echo "--- 编译/仿真（AXI 全路径）---"
if ! "$XVLOG" "$RTL/map_detect_core.v" "$RTL/map_detect_axi.v" "$RTL/map_cc.v" \
        "$RTL/tb_map_detect_axi.v" > "$LOGD/mapdet_axi_xvlog.out" 2>&1; then
    echo "!! AXI 编译失败"; tail -20 "$LOGD/mapdet_axi_xvlog.out"; exit 1
fi
grep -q "ERROR" "$LOGD/mapdet_axi_xvlog.out" && { echo "!! AXI 编译报错"; exit 1; }
"$XELAB" tb_map_detect_axi -s tb_map_detect_axi > "$LOGD/mapdet_axi_xelab.out" 2>&1 || {
    echo "!! AXI 精化失败"; tail -15 "$LOGD/mapdet_axi_xelab.out"; exit 1; }
"$XSIM" tb_map_detect_axi -R > "$LOGD/mapdet_axi_xsim.out" 2>&1
tail -12 "$LOGD/mapdet_axi_xsim.out"
if ! grep -q ">>> PASS" "$LOGD/mapdet_axi_xsim.out"; then
    echo "!! AXI TB 没 PASS"; exit 1
fi
if python -c "import cv2, numpy" >/dev/null 2>&1; then
    PYTHONIOENCODING=utf-8 python sim/check_map_cc.py \
        sim/map_detect/mask_gap1.hex sim/_logs/mapdet_axi_cand.txt || exit 1
fi

echo "--- 编译/仿真（形态学 + 8 连通 CCL）---"
if ! "$XVLOG" "$RTL/map_cc.v" "$RTL/tb_map_cc.v" > "$LOGD/mapcc_xvlog.out" 2>&1; then
    echo "!! map_cc 编译失败"; tail -20 "$LOGD/mapcc_xvlog.out"; exit 1
fi
grep -q "ERROR" "$LOGD/mapcc_xvlog.out" && { echo "!! map_cc 编译报错"; exit 1; }
"$XELAB" tb_map_cc -s tb_map_cc > "$LOGD/mapcc_xelab.out" 2>&1 || {
    echo "!! map_cc 精化失败"; tail -15 "$LOGD/mapcc_xelab.out"; exit 1; }
"$XSIM" tb_map_cc -R > "$LOGD/mapcc_xsim.out" 2>&1
tail -14 "$LOGD/mapcc_xsim.out"
if ! grep -q ">>> PASS" "$LOGD/mapcc_xsim.out"; then
    echo "!! map_cc TB 没 PASS"; exit 1
fi
if python -c "import cv2, numpy" >/dev/null 2>&1; then
    PYTHONIOENCODING=utf-8 python sim/check_map_cc.py \
        sim/map_detect/mask_gap1.hex sim/_logs/map_cc_cand.txt || exit 1
fi

# ---- 逐像素复对（可选；没有 cv2 就跳过）----
if python -c "import cv2, numpy" >/dev/null 2>&1; then
    echo "--- 逐像素 H/S/V 对拍（cv2）---"
    PYTHONIOENCODING=utf-8 python sim/check_map_detect_dbg.py sim/map_detect/map_snap_gap1.hex sim/_logs/map_detect_dbg.hex || exit 1
else
    echo "  （本机没有 cv2，跳过逐像素复对）"
fi

echo "=============================================================="
echo "  结论：PASS"
echo "=============================================================="
exit 0
