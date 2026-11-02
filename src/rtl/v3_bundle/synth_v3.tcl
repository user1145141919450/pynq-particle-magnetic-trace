# synth_v3.tcl —— V3「并行轨迹束」的资源/时序评估（OOC 综合，不建工程、不动现有 BD）
#
# 目的：上板前先回答"17 路装不装得进 Zynq-7020"。OOC（out-of-context）综合只把
#   traj_bundle 单独综合一遍，几分钟出资源数，不影响 ov5640_pynq 那个工程。
#
# 用法（工程根目录）：
#   vivado -mode batch -source vivado/ov5640_pynq/rtl/v3_bundle/synth_v3.tcl

set outdir "vivado/ov5640_pynq/rtl/v3_bundle"
read_verilog [list \
    "$outdir/traj_lane.v" \
    "$outdir/traj_bundle.v" \
]
synth_design -top traj_bundle -part xc7z020clg400-1 -mode out_of_context

# 给个 100MHz 时钟，量一下关键路径（OOC 综合没有 BD 的时钟，要自己建）
# 注：batch 模式下 set_input_delay 里的 remove_from_collection 会报 invalid command，
# 这里只建时钟、不做 IO 延时约束 —— 看内部逻辑的关键路径够用了。
create_clock -period 10.000 -name clk [get_ports clk]

report_utilization    -file "$outdir/v3_util.rpt"
report_timing_summary -file "$outdir/v3_timing.rpt"
puts "================================================"
puts "V3 综合完成："
puts "  资源 -> $outdir/v3_util.rpt"
puts "  时序 -> $outdir/v3_timing.rpt"
puts "================================================"
