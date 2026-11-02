# ooc_line_engine.tcl —— line_engine（画线核 + AXI 写主）的 OOC 综合
set script_dir [file dirname [file normalize [info script]]]
cd $script_dir
set_part xc7z020clg400-1
read_verilog rtl/v3_bundle/line_draw.v
read_verilog rtl/v3_bundle/axi_wr_master.v
read_verilog rtl/v3_bundle/line_engine.v
synth_design -top line_engine -part xc7z020clg400-1 -mode out_of_context
create_clock -period 10.000 -name clk [get_ports clk]
report_utilization -file line_engine_util.rpt
report_timing_summary -file line_engine_timing.rpt
puts "=== LINE_ENGINE OOC DONE ==="
exit
