// ============================================================================
// tb_map_detect_axi.v —— map_detect_axi 的**总线级**自检（上板前最后一道仿真）
//
// 走完整条 AXI4-Lite 路径：写 CTRL 使能 -> 用 tap 注入真实快照（5760 字）
// -> 轮询 STATUS.done -> 读 SEQ/MEDVHI/三色计数 -> 对照 PS 参考。
// 另验：done 粘滞位可由 CTRL[2] 清掉。
//
// 用法（仓库根目录）：
//   xvlog vivado/ov5640_pynq/rtl/map_detect/map_detect_core.v \
//         vivado/ov5640_pynq/rtl/map_detect/map_detect_axi.v \
//         vivado/ov5640_pynq/rtl/map_detect/tb_map_detect_axi.v
//   xelab tb_map_detect_axi -s tb_map_detect_axi
//   xsim tb_map_detect_axi -R
// ============================================================================
`timescale 1ns / 1ps

module tb_map_detect_axi;

    localparam integer NPIX = 11520;
    localparam integer NW   = 5760;
    localparam integer EXP_MED   = 132;
    localparam integer EXP_VHI   = 72;
    localparam integer EXP_RED   = 21;
    localparam integer EXP_BLUE  = 115;
    localparam integer EXP_BLACK = 716;

    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    // ---- AXI 信号 ----
    reg  [11:0] awaddr; reg [2:0] awprot; reg awvalid; wire awready;
    reg  [31:0] wdata;  reg [3:0] wstrb;  reg wvalid;  wire wready;
    wire [1:0]  bresp;  wire bvalid;      reg bready;
    reg  [11:0] araddr; reg [2:0] arprot; reg arvalid; wire arready;
    wire [31:0] rdata;  wire [1:0] rresp; wire rvalid; reg rready;

    // ---- tap ----
    reg        tap_wr_en;
    reg [14:0] tap_wr_addr;
    reg [31:0] tap_wr_data;

    reg [15:0] pix_mem [0:NPIX-1];

    map_detect_axi dut (
        .s_axi_aclk(clk), .s_axi_aresetn(rstn),
        .s_axi_awaddr(awaddr), .s_axi_awprot(awprot), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arprot(arprot), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .tap_wr_en(tap_wr_en), .tap_wr_addr(tap_wr_addr), .tap_wr_data(tap_wr_data),
        .dbg_frame_seq()
    );

    task axi_wr(input [11:0] a, input [31:0] d);
        begin
            @(posedge clk); awaddr <= a; awvalid <= 1'b1; wdata <= d; wvalid <= 1'b1; bready <= 1'b1;
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

    integer i, to, nbad, j, fd;
    reg [31:0] rd, st;
    reg [31:0] seq1, medvhi, cr, cb, ck, ver, ncand, w0, w1;
    reg [56:0] craw;
    reg [1:0]  ccls; reg [13:0] carea; reg [20:0] csx; reg [19:0] csy;

    initial begin
        nbad = 0;
        $readmemh("sim/map_detect/map_snap_gap1.hex", pix_mem);

        awvalid = 0; wvalid = 0; bready = 0; arvalid = 0; rready = 0;
        tap_wr_en = 0; tap_wr_addr = 0; tap_wr_data = 0;
        rstn = 0; repeat (5) @(posedge clk); rstn = 1;
        repeat (3) @(posedge clk);

        // ---- 1) 使能 ----
        axi_wr(12'h000, 32'h0000_0001);
        repeat (3) @(posedge clk);
        axi_rd(12'h000, st);
        $display("  STATUS after en = 0x%08x", st);
        if (st[3] !== 1'b1) begin nbad = nbad + 1; $display("!! en 位没置上"); end

        // ---- 2) tap 注入一帧 ----
        for (i = 0; i < NW; i = i + 1) begin
            @(negedge clk);
            tap_wr_en   = 1'b1;
            tap_wr_addr = i * 4;
            tap_wr_data = {pix_mem[2*i+1], pix_mem[2*i]};
        end
        @(negedge clk);
        tap_wr_en = 1'b0;

        // ---- 3) 轮询 done（v1 多了形态学+CCL，超时放宽到 2M 拍）----
        to = 0;
        rd = 0;
        while (!rd[1] && to < 2000000) begin
            axi_rd(12'h000, rd);
            to = to + 1;
        end
        $display("  done 轮询用了 %0d 次", to);
        if (!rd[1]) begin nbad = nbad + 1; $display("!! done 一直没来"); end
        if (rd[2])  begin nbad = nbad + 1; $display("!! ovf 置位"); end

        // ---- 4) 读结果 ----
        axi_rd(12'h004, seq1);
        axi_rd(12'h008, medvhi);
        axi_rd(12'h00C, cr);
        axi_rd(12'h010, cb);
        axi_rd(12'h014, ck);
        axi_rd(12'h018, ncand);
        axi_rd(12'h01C, ver);
        $display("  SEQ=%0d MEDVHI={vhi=%0d,med=%0d} R=%0d B=%0d K=%0d N_CAND=%0d VER=0x%08x",
                 seq1, medvhi[15:8], medvhi[7:0], cr, cb, ck, ncand[5:0], ver);

        if (seq1 !== 32'd1)          begin nbad = nbad + 1; $display("!! SEQ 应为 1"); end
        if (medvhi[7:0] !== EXP_MED) begin nbad = nbad + 1; $display("!! med 不符"); end
        if (medvhi[15:8] !== EXP_VHI)begin nbad = nbad + 1; $display("!! vhi 不符"); end
        if (cr !== EXP_RED)          begin nbad = nbad + 1; $display("!! red 不符"); end
        if (cb !== EXP_BLUE)         begin nbad = nbad + 1; $display("!! blue 不符"); end
        if (ck !== EXP_BLACK)        begin nbad = nbad + 1; $display("!! black 不符"); end
        if (ver !== 32'h0002_0000)   begin nbad = nbad + 1; $display("!! VERSION 不符（应 v1）"); end
        if (ncand[5:0] !== 6'd9)     begin nbad = nbad + 1; $display("!! N_CAND 应为 9"); end
        if (ncand[6] || ncand[7])    begin nbad = nbad + 1; $display("!! cand/label overflow"); end

        // ---- 4b) 逐个读候选（AXI 全路径），落盘给 check_map_cc.py ----
        fd = $fopen("sim/_logs/mapdet_axi_cand.txt", "w");
        for (j = 0; j < ncand[5:0]; j = j + 1) begin
            axi_wr(12'h020, j[31:0]);
            axi_rd(12'h024, w0);
            axi_rd(12'h028, w1);
            craw = {w1[24:0], w0};
            ccls = craw[56:55]; carea = craw[54:41];
            csx = craw[40:20]; csy = craw[19:0];
            $fwrite(fd, "%0d %0d %0d %0d\n", ccls, carea, csx, csy);
            $display("  cand[%0d] cls=%0d area=%0d sx=%0d sy=%0d",
                     j, ccls, carea, csx, csy);
        end
        $fclose(fd);

        // ---- 5) W1C：写 CTRL[2] 清 done（同时保持 en=1）----
        axi_wr(12'h000, 32'h0000_0005);
        repeat (4) @(posedge clk);
        axi_rd(12'h000, st);
        $display("  STATUS after W1C = 0x%08x", st);
        if (st[1] !== 1'b0) begin nbad = nbad + 1; $display("!! done 没清掉"); end
        if (st[0] !== 1'b0) begin nbad = nbad + 1; $display("!! 清 done 后 busy 应为 0"); end

        // ---- 结论 ----
        $display("------------------------------------------------------------");
        if (nbad == 0)
            $display(">>> PASS map_detect_axi（AXI 全路径 + 真实快照）");
        else
            $display(">>> FAIL 有 %0d 项不符", nbad);
        $display("------------------------------------------------------------");
        $finish;
    end

endmodule
