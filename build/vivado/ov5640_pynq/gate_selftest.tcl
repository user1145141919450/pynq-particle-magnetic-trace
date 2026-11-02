# ============================================================================
# gate_selftest.tcl —— 真的**执行** build_project.tcl 里那段时序门禁（§5c）
#
# 为什么不用"语法检查"：
#   我第一版检查器是 `catch {proc __chk {} $src}`，拿一个**故意漏花括号**的文件
#   做反向对照 —— 它照样打 "OK"。**一个测不出错的测试不是测试**（同一天已经栽过一次：
#   tb 里 `case_name[23:0] == "goal"` 恒假，goal 向量从来没被加载过）。
#   所以改成：把真实文件里那一段**抠出来、灌桩子、真跑**，并且**正反两档都要有**。
#
# 用法：tclsh gate_selftest.tcl <build_project.tcl 路径> <临时沙箱目录>
# 退出码：0 = 正反两档行为都对；1 = 有错。
# ============================================================================

set tclfile [lindex $argv 0]
set sandbox [lindex $argv 1]

# ---- 1. 从**真实文件**里抠出 §5c 那一段（不抄一份，抄的会漂）----
set fp [open $tclfile r]
set src [read $fp]
close $fp

set start [string first "open_run impl_1\n# ★ 两个 -quiet" $src]
if {$start < 0} {
    # 退而求其次：从 open_run impl_1 到"门禁通过"那句
    set start [string first "open_run impl_1\n" $src]
}
set end [string first "puts \"INFO: 时序门禁通过\"" $src]
if {$start < 0 || $end < 0} {
    puts "FAIL: 在 $tclfile 里找不到 §5c 那一段（start=$start end=$end）"
    exit 1
}
set block [string range $src $start [expr {$end + 40}]]

# ★★ 2026-10-01：抠不出东西必须**当场判死**，不能接着往下跑。
#   起因：门禁挪进 timing_gate.tcl 时，我在**文件头注释里**原样抄了一遍结束标记，
#   于是 `string first` 先命中注释里那份 ⇒ end < start ⇒ string range 返回 **0 字节**
#   ⇒ 下面全部"通过"的假象。当时只因为桩子行为不同才碰巧报了 FAIL —— 靠运气。
#   **空代码块 = 在验空气**，这与本工程"测不出错的测试"家族前 3 例是同一个病。
if {$end < $start} {
    puts "FAIL: 结束标记出现在起始标记**之前**（start=$start end=$end）"
    puts "      —— 多半是有人在注释里把标记原样抄了一份"
    exit 1
}
if {[string length $block] < 200} {
    puts "FAIL: 抠出来只有 [string length $block] 字节，这不是门禁代码（阈值 200）"
    exit 1
}
puts "== 抠出门禁代码 [string length $block] 字节，头尾各一行："
foreach l [lrange [split $block "\n"] 0 1] { puts "   | $l" }
puts "   ..."
puts "== 开始灌桩子执行 =="

# ---- 2. 桩子 ----
set proj_dir $sandbox
set proj_name ov5640_m1a
set ::exited 0
# ★ 2026-10-01：先把**真的** exit 存起来。下面这个桩子把 exit 覆盖掉之后，
#   脚本末尾那句 `exit 0` 会去调桩子 ⇒ 抛未捕获异常 ⇒ tclsh 实际退出码是 **1**。
#   也就是说本文件开头写的"退出码 0 = 两档都对"**从来就不可能成立**，
#   任何按退出码判它的调用方都会被反向误导 —— 又一个"检查器在说谎"。
rename exit _real_exit
proc exit {code} { set ::exited $code; return -code error -errorcode {EXIT} "exit $code" }
proc open_run {args} { return }
proc get_timing_paths {args} { return [list fake_path_obj] }
proc get_property {args} { return $::FAKE_SLACK }
proc llength  {args} { return 1 }

proc run_gate {wns whs} {
    set ::FAKE_SLACK $wns
    set ::exited 0
    set rc [catch {uplevel #0 $::block} err]
    # $wns 是脚本里的局部变量，直接用全局的两个假值即可
    return [list $rc $err $::exited]
}

# 脚本里 $wns/$whs 由 get_property 返回 —— 桩子按调用顺序给 max/min
proc get_property_ordered {} { }

# ---- 3. 沙箱：造一个"本次构建的 bit" ----
file delete -force $sandbox
file mkdir $sandbox/proj/$proj_name.runs/impl_1
set bitf $sandbox/proj/$proj_name.runs/impl_1/ov5640_m1a_wrapper.bit
set fh [open $bitf w]; puts $fh "FAKE BITSTREAM"; close $fh
puts "== 沙箱 bit: $bitf"

# ---- 4. 负档：时序违例 ⇒ 必须改名 + exit 1 ----
set ::calln 0
proc get_property {args} {
    incr ::calln
    # 第一次问 = max(WNS)，第二次问 = min(WHS)
    if {$::calln % 2 == 1} { return $::FAKE_WNS } else { return $::FAKE_WHS }
}
set ::FAKE_WNS -0.202
set ::FAKE_WHS  0.026
set ::exited 0
set rc [catch {uplevel #0 $block} err]
puts ""
puts "== 负档（WNS=-0.202 WHS=+0.026）"
puts "   catch rc=$rc  exit 桩被调用->code=$::exited"
if {[file exists $bitf]} {
    puts "   FAIL: bit **还在原地** —— 门禁又在说谎（这正是要修的那个病）"
    set nbad 1
} elseif {[file exists ${bitf}.TIMING_FAILED]} {
    puts "   OK: bit 已改名为 [file tail ${bitf}].TIMING_FAILED"
    set nbad 0
} else {
    puts "   FAIL: bit 不见了，但也没有 .TIMING_FAILED —— rename 目标名写错了？"
    set nbad 1
}
if {$::exited != 1} { puts "   FAIL: 没走到 exit 1（code=$::exited）"; set nbad 1 } else {
    puts "   OK: 走到了 exit 1"
}

# ---- 5. 正档：时序通过 ⇒ **不许**改名，不许 exit ----
file delete -force $sandbox
file mkdir $sandbox/proj/$proj_name.runs/impl_1
set fh [open $bitf w]; puts $fh "GOOD BITSTREAM"; close $fh
set ::calln 0
set ::FAKE_WNS 0.123
set ::FAKE_WHS 0.045
set ::exited 0
set rc [catch {uplevel #0 $block} err]
puts ""
puts "== 正档（WNS=+0.123 WHS=+0.045）"
if {![file exists $bitf]} {
    puts "   FAIL: 时序是通过的，bit 却被改名/弄丢了"
    set nbad 1
} else {
    puts "   OK: bit 原封不动（[string trim [exec cat $bitf]]）"
}
if {$::exited != 0} { puts "   FAIL: 时序通过了还 exit（code=$::exited）"; set nbad 1 } else {
    puts "   OK: 没有 exit"
}

puts ""
if {$nbad} { puts "结论：**不通过**"; _real_exit 1 }
puts "结论：通过 —— 违例时真的让 bit 不可用，通过时真的不碰它。"
_real_exit 0
