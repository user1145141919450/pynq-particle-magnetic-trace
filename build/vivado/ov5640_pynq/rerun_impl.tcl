# ============================================================================
# rerun_impl.tcl —— **只重跑实现**，复用已有的 synth_1
#
# 为什么要单独一个脚本，而不是再跑一遍 build_project.tcl：
#   build_project.tcl 第 24 行是 `create_project ... -force` —— 它会把整个工程
#   推倒重建，连带重新生成 BD、重跑 ~37 个 OOC 综合。改一行 impl 指令要付这个
#   代价不划算，而且重建本身是个新变量（万一 BD 重生成出岔子，就分不清是谁的锅）。
#   综合结果（synth_1）没有任何变化时，**只重跑 impl_1 就够了**。
#
# ----------------------------------------------------------------------------
# 这一轮为什么要重跑（构建 #5 的证据）：
# ----------------------------------------------------------------------------
#   构建 #5 综合+实现后 WNS = **-0.033ns**、WHS = +0.024ns，只剩 **4 条**失败端点
#   （构建 #4 是 -0.202 / 27 条）。RTL 那一刀（洞表提到 traj_bundle 只算一遍，见
#   known_issues.md §1.1）已经干掉了 84% 的违例，**没干完**。
#
#   剩下这 4 条的画像：
#     · 全在 clk_fpga_0（axi_dynclk_PXL_CLK_O / ov5640_pclk 都是干净的）
#     · 最差一条 g_lane[9]/x_reg[3]/C → … → mb[21]_i_1__8_psdsp/D
#       数据路径 9.763ns = 逻辑 2.398ns(24.6%) + **走线 7.365ns(75.4%)**
#       8 级逻辑里走线占 7.365 ⇒ 平均 ~0.92ns/级，正常应该是 0.2~0.4ns/级
#       ⇒ 单元被摆得**很远**，不是逻辑太深
#     · 17 路 lane 是**完全相同**的电路，只有第 7/9/14/15 路的这 4 个端点失败
#       ⇒ 是**布局运气**问题，不是结构问题
#     · 同时 runme.log 里有 `[Route 35-447] Congestion is preventing the router
#       from routing all nets`，East 8x8 区 Max Cong **91.5%**，有效拥塞等级 4
#
#   ★ 关键前提：**Vivado 的 place/route 对同样的输入是确定性的**。
#     所以「什么都不改、再跑一遍」= 复现出同一个 -0.033，白等一小时。
#     要重跑就**必须换指令**，否则没意义。
#
#   ★ 为什么不去动 RTL：那 4 条路径都汇到乘法器的操作数寄存器 mb 上，要打断它
#     得加流水级 ⇒ 改 FSM 时序 ⇒ 本项目"17/17 逐位一致"那套向量要重验，
#     为 33ps 冒这个险不值。先把便宜的先试（纯物理优化，RTL 一个字节不动）。
#
# ============================================================================
# ★ 实验记录（一次只动一个变量）
# ============================================================================
# 基线 = 构建 #5： place=ExtraTimingOpt / route=NoTimingRelaxation / physopt=Explore
#                  ⇒ WNS **-0.033**，4 条失败端点  ← **目前最好的**
#
# 【实验 1】只动布线：route->AggressiveExplore, physopt->AggressiveExplore
#   结果：**更差**。routed -0.094(3条) → physopt -0.078(**4条**，physopt 反而把
#   条数搞多了)。日志中间值全程比基线差（TNS -45 vs -11 之类）。
#   ⇒ 结论：这个设计对"更激进的布线"反应是**负**的。和之前 AltSpreadLogic
#     那次"越走越差"是同一个方向 —— 别再加码了。
#
#   ★★ 实验 1 顺带炸出一条**确定性的硬事实**（比结论本身更值钱）：
#     两个日志的**前四条时序行一模一样**，逐字节相同：
#       Physopt Est. Summary | WNS=-0.672 | TNS=-44.068
#       Physopt Est. Summary | WNS=-0.836 | TNS=-22.649
#       Route Intermediate   | WNS=0.003  | WHS=-0.355 | THS=-347.911
#       Route Intermediate   | WNS=0.003
#     ⇒ `place_design -directive ExtraTimingOpt` 对这个网表**每次都产出
#       **完全相同的布局**（Vivado 的布局是确定性的）。
#     ⇒ **只换布线指令，等于在同一个（拥塞 91.5% 的）布局上反复重试** ——
#       布局这一头不动，"走线占 75%"的病根就永远不动。
#     ⇒ 要换布局，**必须换 place 指令**（或换网表）。
#
# 【实验 2】只动布局（本次）：place->Explore，route/physopt 退回基线
#     place   ExtraTimingOpt -> **Explore**
#     route   AggressiveExplore -> **NoTimingRelaxation**（退回基线）
#     physopt AggressiveExplore -> **Explore**（退回基线）
#   理由：17 路 lane 电路**完全相同**，只有第 7/9/14/15 路失败 ⇒ 这是**布局/分配**
#   问题不是结构问题（13 路同样结构是能收敛的，证明结构本身够快）。
#   而 ExtraTimingOpt 给的布局是**唯一且固定**的 ⇒ 必须换 place 才有新布局。
#   Explore 的官方描述："Increased placer effort in **detail placement and
#   post-placement optimization**" —— 正是我们需要的那个环节。
#
#   ★ 为什么不用 AggressiveExplore 做 place：**它不存在**。探针实测
#     `place_design -directive AggressiveExplore` ⇒ [Common 17-110] Invalid
#     property value。place 的合法取值（抓 `-help` 权威清单）只有：
#       Explore / EarlyBlockPlacement / WLDrivenBlockPlacement /
#       ExtraNetDelay_high|low / AltSpreadLogic_high|medium|low /
#       ExtraPostPlacementOpt / ExtraTimingOpt / SSI_* / RuntimeOptimized /
#       Quick / RQS / Auto_1|2|3
#     —— 去掉已知会变差的 AltSpreadLogic_*，剩下够格的就是 Explore。
#     （教训：名字别靠记。没先探针的话，会在开完工程之后才 ABORT。）
#
# 下一轮若还不过，候选：place=ExtraPostPlacementOpt、place=ExtraNetDelay_high。
# 再不行就该动 RTL 了（那 4 条都汇到乘法器操作数 mb，要加流水级 ⇒ 改 FSM 时序
# ⇒ "17/17 逐位一致"那套向量要全部重验 —— 贵，但那是唯一能真正缩短逻辑的办法）。
#
#   ★ 为什么不重蹈"越走越差"那次的覆辙：build_project.tcl 第 334~340 行记着，
#     上一版用 Congestion_SpreadLogic_high（= place AltSpreadLogic_high +
#     route AlternateCLBRouting）跑了 4h44m 还越走越差 —— 那个策略是**故意把
#     逻辑摊开**，代价就是每根线都变长，方向本身就错了。
#     AggressiveExplore 是**时序驱动**的"多试几轮挑最好的"，方向相反。
#
# 判据（**看日志标记，不看退出码** —— Vivado 未捕获 Tcl 错误也返回 0）：
#   RERUN: STAGE=directives-ok  指令真的生效了（回读对账）
#   RERUN: STAGE=gated          门禁跑过了（接着看 WNS/WHS 和 时序门禁通过/未收敛）
#   RERUN: DONE                 全流程走完
#   RERUN: ABORT=...            中途停，等号后面是原因
#
# 用法（cmd，别在 Git-Bash 里传带 = 的参数）：
#   cd E:\pynq-magnetic-field-demo\vivado\ov5640_pynq
#   F:\Xilinx2022\Vivado\2023.2\bin\vivado.bat -mode batch -source rerun_impl.tcl \
#       -log rerun_impl1.log -journal rerun_impl1.jou
# ============================================================================

set proj_dir [file dirname [file normalize [info script]]]
set proj_name ov5640_m1a
set xpr       $proj_dir/proj/$proj_name.xpr

if {![file exists $xpr]} {
    puts "RERUN: ABORT=no-project :: $xpr 不存在"
    exit 1
}
puts "RERUN: 用已有工程（不重建）-> $xpr"

open_project $xpr
set impl_run [get_runs impl_1]
puts "RERUN: 重跑前 strategy=[get_property -quiet strategy $impl_run]"
puts "RERUN: 重跑前 place=[get_property -quiet STEPS.PLACE_DESIGN.ARGS.DIRECTIVE $impl_run] route=[get_property -quiet STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE $impl_run]"

# ---- 顺序要点：reset 先、strategy 后、directive 最后 --------------------
#   设 strategy 有可能把各 step 的参数重置回该策略的默认值，
#   所以**覆盖必须放在策略之后**，否则会被悄悄盖回去。
reset_run impl_1
puts "RERUN: 已 reset_run impl_1（synth_1 不动，复用）"

catch { set_property strategy Performance_ExtraTimingOpt $impl_run }

# ★ 这里的 catch 是**用来报告**的，不是用来吞的 ——
#   build_project.tcl 第 345 行那条教训：参数名写错会被 catch{} 静默吞掉，
#   日志里毫无痕迹。所以把 catch 到的原文打出来，并且直接 ABORT。
proc _setchk {what prop val run} {
    if {[catch {set_property $prop $val $run} _e]} {
        puts "RERUN: ABORT=${what}-rejected :: $prop <- $val : $_e"
        exit 1
    }
}

# ---- ★ 本轮实验的唯一改动点（只改这几行就能换一个变量）----
#   要"退回基线"就填：place=ExtraTimingOpt / route=NoTimingRelaxation / physopt=Explore
set EXP_TAG    "exp4-place-extranetdelay"
set EXP_PLACE  "ExtraNetDelay_high"
set EXP_ROUTE  "NoTimingRelaxation"
set EXP_PPO    "Explore"
puts "RERUN: ===== 实验 $EXP_TAG ====="
puts "RERUN: 目标 place=$EXP_PLACE route=$EXP_ROUTE postroute_physopt=$EXP_PPO"

_setchk place   STEPS.PLACE_DESIGN.ARGS.DIRECTIVE               $EXP_PLACE $impl_run
_setchk route   STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE               $EXP_ROUTE $impl_run
_setchk ppo-en  STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED     true       $impl_run
_setchk ppo-dir STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE $EXP_PPO   $impl_run

# ---- 回读对账：写进去了不算数，**读出来**才算 ----
set _p  [get_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE $impl_run]
set _r  [get_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE $impl_run]
set _po [get_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.ARGS.DIRECTIVE $impl_run]
set _pe [get_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED $impl_run]
puts "RERUN: 回读 strategy=[get_property -quiet strategy $impl_run] place=$_p route=$_r postroute_physopt=$_pe/$_po"
# ★ 三个都要对账，包括 place：即使它有时来自策略而不是我设的，也必须断言 ——
#   万一策略没生效，place 会**悄悄退回**别的取值而不是报错，那种"静默降级"正是
#   本工程反复吃亏的地方。
if {$_p ne $EXP_PLACE || $_r ne $EXP_ROUTE || $_po ne $EXP_PPO || $_pe != 1} {
    puts "RERUN: ABORT=directive-readback-mismatch :: 要的是 place=$EXP_PLACE route=$EXP_ROUTE physopt=1/$EXP_PPO 读回来是 place=$_p route=$_r physopt=$_pe/$_po"
    exit 1
}
puts "RERUN: STAGE=directives-ok tag=$EXP_TAG place=$_p route=$_r postroute_physopt=$_pe/$_po"

# ---- 跑实现（到 write_bitstream），然后过门禁 ----
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
set _prog [get_property PROGRESS [get_runs impl_1]]
if {$_prog ne "100%"} {
    puts "RERUN: ABORT=impl-failed progress=$_prog status=[get_property -quiet STATUS [get_runs impl_1]]"
    exit 1
}
puts "RERUN: 实现跑完 progress=$_prog"

# ★★★ 门禁**只有一份**（timing_gate.tcl），不再抄一遍。
#   它内部会 open_run impl_1 并读 WNS/WHS；违例就改名 + exit 1。
#   前提：本文件的 $proj_dir / $proj_name 正好是它要的那两个变量名。
source [file join $proj_dir timing_gate.tcl]
puts "RERUN: STAGE=gated"

set bit [glob $proj_dir/proj/$proj_name.runs/impl_1/*.bit]
puts "RERUN: DONE bit=$bit"

# ---- 部署：bit + hwh 拷进 deploy/ ----
# ★ 2026-10-01 补：第一版**漏了这一步**，结果 deploy/ 里留的还是 09-28 的旧 bit，
#   而 `deploy_check.py --fix` **修不了它** —— --fix 只同步 software/board 里的
#   **驱动文件**，bit 不在它的同步范围内（它把 deploy 里的 .bit 当"额外的文件"）。
#   靠人工 cp 兜底就是本工程最爱的那种"不报错、等上板才发现"。
#   症状原话（deploy_check 自己写的）：『下板了但屏幕全黑，而所有软件侧检查都正常』。
file mkdir $proj_dir/deploy
file copy -force $bit $proj_dir/deploy/
set hwh_src [glob $proj_dir/proj/$proj_name.gen/sources_1/bd/ov5640_m1a/hw_handoff/*.hwh]
file copy -force $hwh_src $proj_dir/deploy/ov5640_m1a_wrapper.hwh
puts "RERUN: STAGE=deployed bit+hwh -> $proj_dir/deploy/"
puts "RERUN: 下一步 = software/pc/deploy_check.py 验『部署里的 bit 是不是本次的』（要 '包可上板'）"
