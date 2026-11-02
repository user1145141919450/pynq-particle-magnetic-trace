// ============================================================================
// tb_frame_pump.v —— frame_pump 的逐像素台架（2026-10-31）
//
// 用一个内存 + 简单 AXI 从模型跑两档：
//   ① 恒等映射（sx=x, sy=y）：输出必须**逐像素等于**源图
//   ② 2 倍水平抽稀（sx=2x）：输出[x] 必须等于 源[2x]
// 任何不一致直接打印并 $finish。
// ============================================================================
`timescale 1ns / 1ps

module tb_frame_pump;

    localparam integer DW = 32;      // 输出宽（16 的倍数）
    localparam integer DH = 4;       // 输出高
    localparam integer SW = 64;      // 源宽
    localparam integer SH = 8;
    localparam integer MEMW = 1 << 18;

    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    // ---------------- AXI-Lite 主 ----------------
    reg  [5:0]  s_awaddr = 0; reg s_awvalid = 0; wire s_awready;
    reg  [31:0] s_wdata = 0;  reg s_wvalid = 0;  wire s_wready;
    wire [1:0]  s_bresp; wire s_bvalid; reg s_bready = 0;
    reg  [5:0]  s_araddr = 0; reg s_arvalid = 0; wire s_arready;
    wire [31:0] s_rdata; wire [1:0] s_rresp; wire s_rvalid; reg s_rready = 0;
    reg  [2:0]  s_awprot = 0, s_arprot = 0;

    // ---------------- AXI 读 ----------------
    wire [5:0]  rd_arid;
    wire [31:0] rd_araddr; wire [7:0] rd_arlen; wire [2:0] rd_arsize;
    wire [1:0]  rd_arburst; wire rd_arlock; wire [3:0] rd_arcache;
    wire [2:0]  rd_arprot;
    wire rd_arvalid; reg rd_arready = 1;
    wire rd_rready;
    reg  [31:0] rd_rdata = 0; reg [1:0] rd_rresp = 0, rd_rlast = 0, rd_rvalid = 0;
    wire [5:0]  rd_rid = 6'd0;

    // ---------------- AXI 写 ----------------
    wire [5:0]  wr_awid;
    wire [31:0] wr_awaddr; wire [7:0] wr_awlen; wire [2:0] wr_awsize;
    wire [1:0]  wr_awburst; wire wr_awlock; wire [3:0] wr_awcache;
    wire [2:0]  wr_awprot;
    wire wr_awvalid; reg wr_awready = 1;
    wire [31:0] wr_wdata; wire [3:0] wr_wstrb; wire wr_wlast; wire wr_wvalid;
    reg wr_wready = 1;
    wire [5:0]  wr_bid = 6'd0;
    reg  [1:0]  wr_bresp = 0; reg wr_bvalid = 0; wire wr_bready;

    frame_pump #(.IDW(6), .SRC_ROW_SHIFT(8)) dut (
        .clk(clk), .rstn(rstn),
        .s_axi_awaddr(s_awaddr), .s_axi_awprot(s_awprot),
        .s_axi_awvalid(s_awvalid), .s_axi_awready(s_awready),
        .s_axi_wdata(s_wdata), .s_axi_wstrb(4'hF),
        .s_axi_wvalid(s_wvalid), .s_axi_wready(s_wready),
        .s_axi_bresp(s_bresp), .s_axi_bvalid(s_bvalid), .s_axi_bready(s_bready),
        .s_axi_araddr(s_araddr), .s_axi_arprot(s_arprot),
        .s_axi_arvalid(s_arvalid), .s_axi_arready(s_arready),
        .s_axi_rdata(s_rdata), .s_axi_rresp(s_rresp),
        .s_axi_rvalid(s_rvalid), .s_axi_rready(s_rready),
        .m_rd_arid(rd_arid), .m_rd_araddr(rd_araddr), .m_rd_arlen(rd_arlen),
        .m_rd_arsize(rd_arsize), .m_rd_arburst(rd_arburst),
        .m_rd_arlock(rd_arlock), .m_rd_arcache(rd_arcache),
        .m_rd_arprot(rd_arprot),
        .m_rd_arvalid(rd_arvalid), .m_rd_arready(rd_arready),
        .m_rd_rid(rd_rid), .m_rd_rdata(rd_rdata), .m_rd_rresp(rd_rresp),
        .m_rd_rlast(rd_rlast), .m_rd_rvalid(rd_rvalid), .m_rd_rready(rd_rready),
        .m_wr_awid(wr_awid), .m_wr_awaddr(wr_awaddr), .m_wr_awlen(wr_awlen),
        .m_wr_awsize(wr_awsize), .m_wr_awburst(wr_awburst),
        .m_wr_awlock(wr_awlock), .m_wr_awcache(wr_awcache),
        .m_wr_awprot(wr_awprot),
        .m_wr_awvalid(wr_awvalid), .m_wr_awready(wr_awready),
        .m_wr_wdata(wr_wdata), .m_wr_wstrb(wr_wstrb),
        .m_wr_wlast(wr_wlast), .m_wr_wvalid(wr_wvalid), .m_wr_wready(wr_wready),
        .m_wr_bid(wr_bid), .m_wr_bresp(wr_bresp),
        .m_wr_bvalid(wr_bvalid), .m_wr_bready(wr_bready)
    );

    // ---------------- 内存 + AXI 从模型 ----------------
    reg [31:0] mem [0:MEMW-1];

    // 读：AR -> (2 拍延迟) -> R 连续 r_len+1 拍
    reg        r_pend = 0;
    reg [31:0] r_addr = 0;
    reg [7:0]  r_len = 0;
    reg [7:0]  r_cnt = 0;
    reg [1:0]  r_dly = 0;

    always @(posedge clk) begin
        if (!rstn) begin
            rd_rvalid <= 0; rd_rlast <= 0; rd_rdata <= 0;
            r_pend <= 0; r_cnt <= 0;
        end else begin
            // ★ 模型只支持一笔在途读：忙时把 arready 拉低（协议正确）
            rd_arready <= !r_pend;
            if (rd_arvalid && rd_arready && !r_pend) begin
                $display("[%0t] AR latch addr=%08x len=%0d pend=%b", $time, rd_araddr, rd_arlen, r_pend);
                r_addr <= rd_araddr; r_len <= rd_arlen;
                r_pend <= 1; r_dly <= 2'd2; r_cnt <= 8'd0;
            end
            if (r_pend && !rd_rvalid) begin
                if (r_dly != 0) r_dly <= r_dly - 2'd1;
                else begin
                    rd_rdata  <= mem[(r_addr >> 2) + r_cnt];
                    rd_rlast  <= (r_cnt == r_len);
                    $display("[%0t] R beat cnt=%0d addr=%08x", $time, r_cnt, (r_addr>>2)+r_cnt);
                    rd_rvalid <= 1'b1;
                    if (r_cnt == r_len) begin r_pend <= 0; r_cnt <= 0; end
                    else r_cnt <= r_cnt + 8'd1;
                end
            end else if (rd_rvalid && rd_rready) begin
                rd_rvalid <= 0; rd_rlast <= 0;
            end
        end
    end

    // 写：AW 锁地址 -> W 逐拍写 -> LAST 后 2 拍 B
    reg        w_pend = 0;
    reg [31:0] w_addr = 0;
    reg [7:0]  w_len = 0, w_cnt = 0;
    reg [1:0]  b_dly = 0;
    reg        b_pend = 0;

    always @(posedge clk) begin
        if (!rstn) begin
            wr_bvalid <= 0; w_pend <= 0; w_cnt <= 0; b_pend <= 0;
        end else begin
            if (wr_awvalid && wr_awready && !w_pend) begin
                $display("[%0t] AW latch addr=%08x len=%0d", $time, wr_awaddr, wr_awlen);
                w_addr <= wr_awaddr; w_len <= wr_awlen;
                w_pend <= 1; w_cnt <= 0;
            end
            if (w_pend && wr_wvalid && wr_wready) begin
                if (w_cnt < 3) $display("  [W] cnt=%0d addr=%08x data=%08x", w_cnt, (w_addr>>2)+w_cnt, wr_wdata);
                mem[(w_addr >> 2) + w_cnt] <= wr_wdata;
                if (w_cnt == w_len) begin
                    w_pend <= 0; b_pend <= 1; b_dly <= 2'd2;
                end else w_cnt <= w_cnt + 8'd1;
            end
            if (b_pend) begin
                if (b_dly != 0) b_dly <= b_dly - 2'd1;
                else begin wr_bvalid <= 1; b_pend <= 0; end
            end
            if (wr_bvalid && wr_bready) wr_bvalid <= 0;
        end
    end

    // ---------------- AXI-Lite 任务 ----------------
    task axil_write(input [5:0] a, input [31:0] d);
        begin
            @(posedge clk);
            s_awaddr <= a; s_awvalid <= 1; s_wdata <= d; s_wvalid <= 1; s_bready <= 1;
            @(posedge clk);
            s_awvalid <= 0; s_wvalid <= 0;
            while (!s_bvalid) @(posedge clk);
            @(posedge clk);
            s_bready <= 0;
        end
    endtask

    task axil_read(input [5:0] a, output [31:0] d);
        begin
            @(posedge clk);
            s_araddr <= a; s_arvalid <= 1; s_rready <= 1;
            @(posedge clk);
            s_arvalid <= 0;
            while (!s_rvalid) @(posedge clk);
            d = s_rdata;
            @(posedge clk);
            s_rready <= 0;
        end
    endtask

    // ---------------- 状态监视（调试用）----------------
    reg [3:0] st_prev = 15;
    always @(posedge clk) begin
        if (rstn && dut.st !== st_prev) begin
            st_prev <= dut.st;
            $display("[%0t] st=%0d oy=%0d ox=%0d wb_n=%0d sxq=%0d syq=%0d px=%08x rq=%b rv=%b",
                     $time, dut.st, dut.oy, dut.ox, dut.fifo_cnt,
                     dut.sxq, dut.syq, dut.px_addr, rd_arvalid, rd_rvalid);
            $display("    tbl_w0=%08x w1=%08x w2=%08x beat=%0d", dut.tbl_w0, dut.tbl_w1, dut.tbl_w2, dut.tbl_beat);
        end
    end

    // WDDBG
    integer wd_n = 0;
    always @(posedge clk) begin
        if (rstn && dut.st == 6 && wd_n < 6) begin
            wd_n <= wd_n + 1;
            $display("  [WD] wb_i=%0d wvalid=%b wready=%b wlast=%b", dut.wb_i,
                     wr_wvalid, wr_wready, wr_wlast);
        end
    end

    // ---------------- 测试 ----------------
    localparam [31:0] SRC = 32'h0000_1000;
    localparam [31:0] DST = 32'h0001_0000;
    localparam [31:0] TBL = 32'h0002_0000;

    integer i, x, y, bad, nbad;
    reg [31:0] v, st;

    task run_frame(input integer scale);   // scale=1 恒等 / 2 抽稀
        begin
            // 行表
            for (y = 0; y < DH; y = y + 1) begin
                mem[(TBL >> 2) + y * 4 + 0] = 32'sd0;              // sx0
                mem[(TBL >> 2) + y * 4 + 1] = (y << 8);            // sy0
                mem[(TBL >> 2) + y * 4 + 2] = (scale << 8);        // dsx
                mem[(TBL >> 2) + y * 4 + 3] = 32'sd0;              // dsy
            end
            axil_write(6'h00, SRC);
            axil_write(6'h04, DST);
            axil_write(6'h08, SW);
            axil_write(6'h0C, SH);
            axil_write(6'h10, DW);
            axil_write(6'h14, DH);
            axil_write(6'h18, TBL);
            $display("cfg src=%08x dst=%08x sw=%0d dw=%0d tbl=%08x",
                     dut.r_src, dut.r_dst, dut.r_sw, dut.r_dw, dut.r_tbl);
            axil_write(6'h1C, 32'd1);
            // 等 DONE
            st = 0;
            for (i = 0; i < 200000 && !st[1]; i = i + 1)
                axil_read(6'h20, st);
            if (!st[1]) begin
                $display("ERR: 等 DONE 超时（scale=%0d）", scale);
                $finish;
            end
        end
    endtask

    initial begin
        rstn = 0;
        for (i = 0; i < MEMW; i = i + 1) mem[i] = 32'hDEAD_0000 + i;
        // 源图：一块可辨认的图案
        for (y = 0; y < SH; y = y + 1)
            for (x = 0; x < SW; x = x + 1)
                mem[(SRC >> 2) + y * SW + x] = (y << 24) | (x << 8) | 8'h5A;
        repeat (10) @(posedge clk);
        rstn = 1;
        repeat (5) @(posedge clk);

        // ---- 档 ①：恒等 ----
        for (i = 0; i < 4096; i = i + 1) mem[(DST >> 2) + i] = 32'h0;
        run_frame(1);
        nbad = 0;
        for (y = 0; y < DH; y = y + 1)
            for (x = 0; x < DW; x = x + 1) begin
                v = mem[(DST >> 2) + y * DW + x];
                if (v !== mem[(SRC >> 2) + y * SW + x]) begin
                    if (nbad < 5)
                        $display("  ① 不符 (%0d,%0d) got=%08x exp=%08x",
                                 x, y, v, mem[(SRC >> 2) + y * SW + x]);
                    nbad = nbad + 1;
                end
            end
        $display("档① 恒等映射：不符 %0d / %0d 像素  os_err=%b", nbad, DW * DH, dut.os_err);

        // ---- 档 ②：2 倍抽稀（sx = 2x）----
        for (i = 0; i < 4096; i = i + 1) mem[(DST >> 2) + i] = 32'h0;
        run_frame(2);
        bad = 0;
        for (y = 0; y < DH; y = y + 1)
            for (x = 0; x < DW; x = x + 1) begin
                v = mem[(DST >> 2) + y * DW + x];
                if (v !== mem[(SRC >> 2) + y * SW + 2 * x]) begin
                    if (bad < 5)
                        $display("  ② 不符 (%0d,%0d) got=%08x exp=%08x",
                                 x, y, v, mem[(SRC >> 2) + y * SW + 2 * x]);
                    bad = bad + 1;
                end
            end
        $display("档② 2x 抽稀：不符 %0d / %0d 像素  os_err=%b", bad, DW * DH, dut.os_err);

        if (nbad == 0 && bad == 0) $display("SIM DONE —— 全部一致");
        else $display("SIM DONE —— 有错");
        $finish;
    end

    initial begin
        #20_000_000;
        $display("ERR: 全局超时");
        $finish;
    end

endmodule
