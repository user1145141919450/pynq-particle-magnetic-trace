// ============================================================================
// tb_spawn_gl.v —— 门级（综合网表）复现"幽灵写"：spawn idx=1 后读回 idx0/idx1
// 用 AXI 读回判据（与板上 raw dump 同路径）。配合 _tmp_pe_synth_netlist.v 使用。
// ============================================================================
`timescale 1ns / 1ps

module tb_spawn_gl;
    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    reg  [11:0] awaddr = 0; reg [2:0] awprot = 0; reg awvalid = 0; wire awready;
    reg  [31:0] wdata = 0;  reg [3:0] wstrb = 4'hF; reg wvalid = 0; wire wready;
    wire [1:0]  bresp;      wire bvalid;          reg bready = 0;
    reg  [11:0] araddr = 0; reg [2:0] arprot = 0; reg arvalid = 0; wire arready;
    wire [31:0] rdata;      wire [1:0] rresp;     wire rvalid; reg rready = 0;

    ov5640_m1a_pe_axi_0_0 dut (
        .s_axi_aclk(clk), .s_axi_aresetn(rstn),
        .s_axi_awaddr(awaddr), .s_axi_awprot(awprot), .s_axi_awvalid(awvalid),
        .s_axi_awready(awready), .s_axi_wdata(wdata), .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid), .s_axi_wready(wready), .s_axi_bresp(bresp),
        .s_axi_bvalid(bvalid), .s_axi_bready(bready), .s_axi_araddr(araddr),
        .s_axi_arprot(arprot), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid),
        .s_axi_rready(rready));

    task axi_wr(input [11:0] a, input [31:0] d);
        begin
            @(posedge clk); awaddr <= a; awvalid <= 1'b1; wdata <= d;
            wvalid <= 1'b1; bready <= 1'b1;
            wait (awready || wready);
            @(posedge clk); awvalid <= 1'b0; wvalid <= 1'b0;
            wait (bvalid);
            @(posedge clk); bready <= 1'b0;
        end
    endtask

    task axi_rd(input [11:0] a, output [31:0] d);
        begin
            @(posedge clk); araddr <= a; arvalid <= 1'b1; rready <= 1'b1;
            wait (arready);
            @(posedge clk); arvalid <= 1'b0;
            wait (rvalid);
            d = rdata;
            @(posedge clk); rready <= 1'b0;
        end
    endtask

    task spawn(input [5:0] idx, input [31:0] x, input [31:0] y,
               input [31:0] vx, input [31:0] vy, input [1:0] chg,
               input [1:0] who);
        reg [31:0] meta;
        begin
            axi_wr(12'h028, x);
            axi_wr(12'h02C, y);
            axi_wr(12'h030, vx);
            axi_wr(12'h034, vy);
            meta = {22'd0, who, chg, idx};
            axi_wr(12'h024, meta);
            @(posedge clk); @(posedge clk);
        end
    endtask

    reg [31:0] w0, w1, w2, w3;
    task dump(input [8*8-1:0] tag, input [11:0] base);
        begin
            axi_rd(base + 0,  w0);
            axi_rd(base + 4,  w1);
            axi_rd(base + 8,  w2);
            axi_rd(base + 12, w3);
            $display("%0s: w=%08x %08x %08x %08x", tag, w0, w1, w2, w3);
        end
    endtask

    initial begin
        rstn = 0;
        repeat (5) @(posedge clk);
        rstn = 1;
        repeat (3) @(posedge clk);
        axi_wr(12'h038, 32'd2816);
        axi_wr(12'h03C, 32'd245760);
        axi_wr(12'h040, 32'd138240);
        axi_wr(12'h044, 32'd0);

        $display("== GL: spawn idx=1 alone (x=101120, vx=-102400, chg=01, who=10) ==");
        spawn(6'd1, 32'd101120, 32'd69120, 32'hFFFE7000, 32'd0, 2'b01, 2'b10);
        dump("GL idx0", 12'h400);
        dump("GL idx1", 12'h410);
        $display("GL-DONE");
        $finish;
    end
endmodule
