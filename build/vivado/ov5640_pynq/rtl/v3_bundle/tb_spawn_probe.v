// ============================================================================
// tb_spawn_probe.v —— 复刻板上"spawn idx=1 把 idx=0 半写一遍"的幽灵现象（2026-10-17）
// 序列与板上驱动完全一致：X/Y/VX/VY 各写一笔，再写 META（写即出生）。
// 判据：idx0 只该有 spawn0 的数据；idx1 只该有 spawn1 的数据。
// ============================================================================
`timescale 1ns / 1ps

module tb_spawn_probe;
    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    reg  [11:0] awaddr = 0; reg [2:0] awprot = 0; reg awvalid = 0; wire awready;
    reg  [31:0] wdata = 0;  reg [3:0] wstrb = 4'hF; reg wvalid = 0; wire wready;
    wire [1:0]  bresp;      wire bvalid;          reg bready = 0;
    reg  [11:0] araddr = 0; reg [2:0] arprot = 0; reg arvalid = 0; wire arready;
    wire [31:0] rdata;      wire [1:0] rresp;     wire rvalid; reg rready = 0;

    pe_axi #(.NPART(32), .NBLK(9)) dut (
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

    task dump(input [8*8-1:0] tag);
        begin
            $display("%0s: idx0 x=%0d y=%0d vx=%0d vy=%0d who=%0d chg=%0d ok=%0d",
                     tag, dut.u_pe.p_x[0], dut.u_pe.p_y[0],
                     $signed(dut.u_pe.p_vx[0]), $signed(dut.u_pe.p_vy[0]),
                     dut.u_pe.p_who[0], dut.u_pe.p_chg[0], dut.u_pe.p_ok[0]);
            $display("%0s: idx1 x=%0d y=%0d vx=%0d vy=%0d who=%0d chg=%0d ok=%0d",
                     tag, dut.u_pe.p_x[1], dut.u_pe.p_y[1],
                     $signed(dut.u_pe.p_vx[1]), $signed(dut.u_pe.p_vy[1]),
                     dut.u_pe.p_who[1], dut.u_pe.p_chg[1], dut.u_pe.p_ok[1]);
        end
    endtask

    initial begin
        rstn = 0;
        repeat (5) @(posedge clk);
        rstn = 1;
        repeat (3) @(posedge clk);

        // 场景基础参数（同板上）
        axi_wr(12'h038, 32'd2816);   // BALL_R
        axi_wr(12'h03C, 32'd245760); // FIELD_W
        axi_wr(12'h040, 32'd138240); // FIELD_H
        axi_wr(12'h044, 32'd0);      // N_HOLES

        $display("== S1: spawn idx0 (x=76800, vx=+102400, chg=01, who=01) ==");
        spawn(6'd0, 32'd76800, 32'd69120, 32'd102400, 32'd0, 2'b01, 2'b01);
        dump("after spawn0");
        $display("");
        $display("== S2: spawn idx1 (x=101120, vx=-102400=0xFFFE7000, chg=01, who=10) ==");
        spawn(6'd1, 32'd101120, 32'd69120, 32'hFFFE7000, 32'd0, 2'b01, 2'b10);
        dump("after spawn1");
        $display("");
        $display("== S3: clear -> 只 spawn idx1 ==");
        axi_wr(12'h000, 32'd4);      // clear
        @(posedge clk); @(posedge clk);
        dump("after clear");
        spawn(6'd1, 32'd101120, 32'd69120, 32'hFFFE7000, 32'd0, 2'b01, 2'b10);
        dump("after spawn1-only");
        $display("TB-SPAWN-DONE");
        $finish;
    end
endmodule
