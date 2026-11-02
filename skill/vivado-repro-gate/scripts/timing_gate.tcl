# ============================================================================
# timing_gate.tcl —— §5c 时序门禁，**单独一个文件**
#
# 为什么要从 build_project.tcl 里挪出来：
#   现在有两个入口会跑实现 —— 全量构建 `build_project.tcl`，和只重跑实现的
#   `rerun_impl.tcl`（复用 synth_1，省掉 create_project/reset BD 那一段）。
#   门禁要是各抄一份，就会**漂**——本工程已经栽过好几次"抄一份、然后两边不等"。
#   所以：**只有这一份**，两边都 source 它。
#
# 约定（改动本文件时别破坏，gate_selftest.tcl 靠这两个标记抠代码去真跑）：
#   · 起始标记：`open_run impl_1` 后面**紧跟**一行 `# ★ 两个 -quiet`
#   · 结束标记：**最后那行**"时序门禁通过"的 puts
#     ★★ 别在本注释里把那行 puts 原样抄出来！第一版抄了，结果
#        gate_selftest 的 `string first` 先命中**注释里**那一份 ⇒ end < start
#        ⇒ `string range` 返回 0 字节 ⇒ 自检**在验一段空代码**。
#        （这就是本工程"测不出错的测试"家族第 4 例：检查器报成功，守的是空气。）
#
# 调用前提：调用方必须有 `proj_dir` / `proj_name` 两个变量。
# 退出：违例 ⇒ exit 1（调用方不要再往下写 bit）。
#
# ★★ 正确用法是**先 route、再过门禁、最后才 write_bitstream**：
#      launch_runs impl_1 -to_step route_design
#      source timing_gate.tcl
#      launch_runs impl_1 -to_step write_bitstream     ;# 只有过了才走到这
#   本门禁的"改名"分支是**兼容旧顺序的兜底**（旧顺序先跑 write_bitstream，
#   于是违例的 bit 已经躺在盘上）。顺序对了以后那个分支根本不会命中，
#   但留着 —— 万一哪天有人又把 write_bitstream 放前面，它还能兜住。
# ============================================================================

if {![info exists proj_dir] || ![info exists proj_name]} {
    puts "ERROR: 时序门禁缺少 proj_dir / proj_name —— 判定不了，停（不许当成通过）"
    exit 1
}

open_run impl_1
# ★ 两个 -quiet：拿不到路径时**别让 get_property 抛异常**。空列表会让
#   `get_property SLACK {}` 抛 "expected integer but got """ -> 未捕获 Tcl 错误，
#   而本工程的 exit code **不可信**（未捕获错误也返回 0）=> "门禁在真正该报错的
#   时候自己先崩了"，看起来像正常结束。所以下面必须先挡一道 llength。
set _tp_max [get_timing_paths -delay_type max -quiet]
set _tp_min [get_timing_paths -delay_type min -quiet]
if {[llength $_tp_max] == 0 || [llength $_tp_min] == 0} {
    puts "ERROR: 时序门禁自己拿不到路径（max=[llength $_tp_max] min=[llength $_tp_min]）—— 判定不了，停"
    exit 1
}
set wns [get_property SLACK $_tp_max]
set whs [get_property SLACK $_tp_min]
puts "INFO: ★时序门禁★ WNS=${wns}ns  WHS=${whs}ns"
if {$wns < 0 || $whs < 0} {
    puts "ERROR: 时序未收敛（WNS=${wns} WHS=${whs}）"
    foreach _b [glob -nocomplain $proj_dir/proj/$proj_name.runs/impl_1/*.bit] {
        file rename -force $_b ${_b}.TIMING_FAILED
        puts "ERROR:   已把 [file tail $_b] 改名为 [file tail $_b].TIMING_FAILED —— 别下这个板"
    }
    puts "ERROR:   （deploy/ 里可能还留着上一次的**旧** bit，同样不能当本次产物用；"
    puts "ERROR:     用 software/pc/deploy_check.py 查『部署里的 bit 是不是本次的』）"
    exit 1
}
puts "INFO: 时序门禁通过"
