// ============================================================================
// tb_pe_axi.v —— 粒子引擎 AXI4-Lite 封装的**总线级**自检（2026-10-16）
// 判据（自检式，直接打 PASS/FAIL）：
//   B1 场景+出生走总线 -> 粒子读回逐位；B2 run 2000 子步 -> 终点 216171/2930
//   B3 进洞 -> ST_GOAL=1 + 弹事件 = 0x0155（edge_of 修后）；B4 湮灭 -> 两条事件
// ============================================================================
`timescale 1ns / 1ps

module tb_pe_axi;
    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    reg  [11:0] awaddr = 0; reg [2:0] awprot = 0; reg awvalid = 0; wire awready;
    reg  [31:0] wdata = 0;  reg [3:0] wstrb = 4'hF; reg wvalid = 0; wire wready;
    wire [1:0]  bresp;      wire bvalid;          reg bready = 0;
    reg  [11:0] araddr = 0; reg [2:0] arprot = 0; reg arvalid = 0; wire arready;
    wire [31:0] rdata;      wire [1:0] rresp;     wire rvalid; reg rready = 0;

    // ★ 2026-10-17：参数改为实建配置（与 pe_axi 默认/BD 一致）
    pe_axi #(.NPART(16), .NBLK(9)) dut (
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

    task ck(input [8*40-1:0] tag, input [63:0] got, input [63:0] exp);
        begin
            if (got === exp)
                $display("PASS %0s got=%0d", tag, got);
            else
                $display("FAIL %0s got=%0d exp=%0d", tag, got, exp);
        end
    endtask

    reg [31:0] d;
    integer i;
    initial begin
        rstn = 0; repeat (5) @(posedge clk); rstn = 1;
        repeat (3) @(posedge clk);

        // ---- 场景：一块 B=+0.4 + 无洞 ----
        axi_wr(12'h038, 32'd2816);          // BALL_R
        axi_wr(12'h03C, 32'd245760);        // FIELD_W
        axi_wr(12'h040, 32'd138240);        // FIELD_H
        axi_wr(12'h044, 32'd0);             // N_HOLES
        axi_wr(12'h100, 32'h7FF92C);        // 块0 ang
        axi_wr(12'h104, 32'd0);             // x0
        axi_wr(12'h108, 32'd0);             // y0
        axi_wr(12'h10C, 32'd245760);        // x1
        axi_wr(12'h110, 32'd138240);        // y1

        // ---- B1 出生 + 读回 ----
        axi_wr(12'h028, 32'd25600);         // SPAWN_X
        axi_wr(12'h02C, 32'd69120);         // SPAWN_Y
        axi_wr(12'h030, 32'd102400);        // SPAWN_VX
        axi_wr(12'h034, 32'd0);             // SPAWN_VY
        // meta = {..., who[9:8], chg[7:6], idx[5:0]} -> who=1,chg=1,idx=0 => 0x140
        axi_wr(12'h024, 32'h00000140);
        axi_rd(12'h400, d); ck("B1.x", d, 64'd25600);
        axi_rd(12'h404, d); ck("B1.y", d, 64'd69120);
        axi_rd(12'h408, d); ck("B1.vx", d, 64'd102400);
        axi_rd(12'h40C, d); ck("B1.w3", d, 64'h01500000);
        // w3 = {7'd0, alive=1, who=1, chg=1, vy=0} -> alive<<24 | who<<22 | chg<<20

        // ---- B2 run 2000 子步 -> 终点逐位 ----
        axi_wr(12'h008, 32'd2000);
        axi_wr(12'h000, 32'h1);
        wait (dut.busy);
        wait (!dut.busy);
        repeat (2) @(posedge clk);
        axi_rd(12'h400, d); ck("B2.x", d, 64'd216171);
        axi_rd(12'h404, d); ck("B2.y", d, 64'd2930);

        // ---- B3 进洞：clear + 开洞 + 重出生（1600px/s）----
        axi_wr(12'h000, 32'h4);             // clear
        axi_wr(12'h208, 32'h0000000B);  // en=1,edge=01(右),tgt=1      // 洞0 meta: en=1,edge=1,tgt=1
        axi_wr(12'h200, 32'd45568);         // 洞0 c=178*256
        axi_wr(12'h204, 32'd11264);         // 洞0 w=44*256
        axi_wr(12'h044, 32'd1);             // N_HOLES=1
        axi_wr(12'h028, 32'd25600);
        axi_wr(12'h02C, 32'd69120);
        axi_wr(12'h030, 32'd409600);
        axi_wr(12'h034, 32'd0);
        axi_wr(12'h024, 32'h00000140);      // spawn idx0
        axi_wr(12'h008, 32'd400);
        axi_wr(12'h000, 32'h1);
        wait (dut.busy); wait (!dut.busy);
        repeat (2) @(posedge clk);
        axi_rd(12'h010, d); ck("B3.goal", d, 64'd1);
        axi_rd(12'hE00, d); ck("B3.evn", d, 64'd1);
        axi_wr(12'hE00, 32'd1);             // 弹一条
        repeat (3) @(posedge clk);
        axi_rd(12'hE04, d); ck("B3.ev", d, 64'h0055);  // idx=0 的本例（2026-10-17 edge_of 修后：edge=right）

        // ---- B4 湮灭 ----
        axi_wr(12'h000, 32'h4);
        axi_wr(12'h044, 32'd0);
        axi_wr(12'h028, 32'd76800);  axi_wr(12'h02C, 32'd69120);
        axi_wr(12'h030, 32'd102400); axi_wr(12'h034, 32'd0);
        axi_wr(12'h024, 32'h00000140);
        axi_wr(12'h028, 32'd78080);  axi_wr(12'h02C, 32'd69120);
        axi_wr(12'h030, 32'hFFFE7000); // -102400 补码（32 位）
        axi_wr(12'h034, 32'd0);
        axi_wr(12'h024, 32'h000002C1); // idx=1, chg=11(-1), who=10(2)
        axi_wr(12'h008, 32'd2);
        axi_wr(12'h000, 32'h1);
        wait (dut.busy); wait (!dut.busy);
        repeat (2) @(posedge clk);
        axi_rd(12'h014, d); ck("B4.annih", d, 64'd1);
        $display("DBG ev_count=%0d wp=%0d rp=%0d", dut.u_pe.ev_count,
                 dut.u_pe.ev_wp, dut.u_pe.ev_rp);
        axi_rd(12'hE00, d); ck("B4.evn", d, 64'd2);
        axi_wr(12'hE00, 32'd1); repeat (3) @(posedge clk);
        axi_rd(12'hE04, d); ck("B4.ev1", d, 64'h0016);
        axi_wr(12'hE00, 32'd1); repeat (3) @(posedge clk);
        axi_rd(12'hE04, d); ck("B4.ev2", d, 64'h012E);

        $display("TB-AXI-DONE");
        $finish;
    end
endmodule
