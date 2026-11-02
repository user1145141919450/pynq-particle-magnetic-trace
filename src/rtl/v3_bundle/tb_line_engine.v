// ============================================================================
// tb_line_engine.v —— line_engine_top 的逐像素仿真台架（2026-10-30 v2）
//
// 干什么：
//   · 用 AXI4-Lite 主模型下发 8 条线（含超长/水平/垂直/反向/单点/越界）
//   · 用 AXI4 写从模型**故意加入 0~4 拍随机 B 响应延迟**（制造反压）
//   · 把每一笔实际写出的 (地址, 数据) 按顺序写进 line_writes.txt
//   · 轮到下一条前轮询 STATUS（BUSY 掉、DONE 起）——顺带验证读通道与粘滞 done
//
// 判定在 Python 侧做：tools/ 里 gen_line_expect.py 用同一套整数 DDA 算期望像素，
// 与 line_writes.txt 逐笔比对（顺序、地址、数据全等）。
// ============================================================================
`timescale 1ns / 1ps

module tb_line_engine;

    reg clk = 0;
    reg rstn = 0;
    always #5 clk = ~clk;      // 100 MHz

    // ---------------- DUT ----------------
    reg  [5:0]  s_awaddr = 0;
    reg  [2:0]  s_awprot = 0;
    reg         s_awvalid = 0;
    wire        s_awready;
    reg  [31:0] s_wdata = 0;
    reg  [3:0]  s_wstrb = 4'hF;
    reg         s_wvalid = 0;
    wire        s_wready;
    wire [1:0]  s_bresp;
    wire        s_bvalid;
    reg         s_bready = 0;
    reg  [5:0]  s_araddr = 0;
    reg  [2:0]  s_arprot = 0;
    reg         s_arvalid = 0;
    wire        s_arready;
    wire [31:0] s_rdata;
    wire [1:0]  s_rresp;
    wire        s_rvalid;
    reg         s_rready = 0;

    wire [5:0]  m_awid;
    wire [31:0] m_awaddr;
    wire [7:0]  m_awlen;
    wire [2:0]  m_awsize;
    wire [1:0]  m_awburst;
    wire        m_awlock;
    wire [3:0]  m_awcache;
    wire [2:0]  m_awprot;
    wire        m_awvalid;
    reg         m_awready = 0;
    wire [31:0] m_wdata;
    wire [3:0]  m_wstrb;
    wire        m_wlast;
    wire        m_wvalid;
    reg         m_wready = 0;
    reg  [5:0]  m_bid = 0;
    reg  [1:0]  m_bresp = 0;
    reg         m_bvalid = 0;
    wire        m_bready;

    line_engine_top dut (
        .s_axi_aclk(clk), .s_axi_aresetn(rstn),
        .s_axi_awaddr(s_awaddr), .s_axi_awprot(s_awprot),
        .s_axi_awvalid(s_awvalid), .s_axi_awready(s_awready),
        .s_axi_wdata(s_wdata), .s_axi_wstrb(s_wstrb),
        .s_axi_wvalid(s_wvalid), .s_axi_wready(s_wready),
        .s_axi_bresp(s_bresp), .s_axi_bvalid(s_bvalid), .s_axi_bready(s_bready),
        .s_axi_araddr(s_araddr), .s_axi_arprot(s_arprot),
        .s_axi_arvalid(s_arvalid), .s_axi_arready(s_arready),
        .s_axi_rdata(s_rdata), .s_axi_rresp(s_rresp),
        .s_axi_rvalid(s_rvalid), .s_axi_rready(s_rready),
        .m_axi_awid(m_awid), .m_axi_awaddr(m_awaddr), .m_axi_awlen(m_awlen),
        .m_axi_awsize(m_awsize), .m_axi_awburst(m_awburst),
        .m_axi_awlock(m_awlock), .m_axi_awcache(m_awcache),
        .m_axi_awprot(m_awprot),
        .m_axi_awvalid(m_awvalid), .m_axi_awready(m_awready),
        .m_axi_wdata(m_wdata), .m_axi_wstrb(m_wstrb),
        .m_axi_wlast(m_wlast), .m_axi_wvalid(m_wvalid), .m_axi_wready(m_wready),
        .m_axi_bid(m_bid), .m_axi_bresp(m_bresp),
        .m_axi_bvalid(m_bvalid), .m_axi_bready(m_bready)
    );

    // ---------------- AXI4 写从模型（带反压）----------------
    integer fd;
    integer n_wr = 0;
    integer aw_have = 0, w_have = 0;
    reg [31:0] aw_addr_r, w_data_r;
    integer dly, dly_set;
    integer cyc = 0;

    initial begin
        fd = $fopen("line_writes.txt", "w");
        if (fd == 0) begin $display("FATAL: 打不开 line_writes.txt"); $finish; end
        dly_set = 0;
    end

    always @(posedge clk) cyc <= cyc + 1;

    // ready 生成：每 3 拍里有 1 拍拉低（确定性拖拍，制造反压）
    always @(posedge clk) begin
        if (!rstn) begin
            m_awready <= 0; m_wready <= 0;
        end else begin
            m_awready <= ((cyc % 3) != 0);
            m_wready  <= ((cyc % 2) != 0);
        end
    end

    // AW / W 各自捕获
    always @(posedge clk) begin
        if (rstn) begin
            if (m_awvalid && m_awready) begin
                aw_have <= 1; aw_addr_r <= m_awaddr;
                if (m_awlen !== 8'd0) begin
                    $display("ERR: awlen=%0d（应恒 0）", m_awlen); $finish;
                end
                if (m_awsize !== 3'd2) begin
                    $display("ERR: awsize=%0d（应=2，4字节）", m_awsize); $finish;
                end
            end
            if (m_wvalid && m_wready) begin
                w_have <= 1; w_data_r <= m_wdata;
            end
        end
    end

    // B 响应：等 AW+W 齐了再延迟 0~4 拍发 bvalid，发完记录这一笔
    always @(posedge clk) begin
        if (!rstn) begin
            m_bvalid <= 0;
            dly <= 0;
        end else begin
            if (aw_have && w_have && !m_bvalid) begin
                if (dly == 0) begin
                    m_bvalid <= 1;
                    m_bresp  <= 2'b00;
                    m_bid    <= 6'd0;
                    $fwrite(fd, "%08x %08x\n", aw_addr_r, w_data_r);
                    n_wr = n_wr + 1;
                    dly <= ((cyc / 7) % 5);      // 0..4 拍，随周期缓慢变化
                end else begin
                    dly <= dly - 1;
                end
            end
            if (m_bvalid && m_bready) begin
                m_bvalid <= 0;
                aw_have <= 0;
                w_have  <= 0;
            end
        end
    end

    // ---------------- AXI4-Lite 主模型 ----------------
    task axil_write(input [5:0] a, input [31:0] d);
        begin
            @(posedge clk);
            s_awaddr <= a; s_awvalid <= 1; s_wdata <= d; s_wvalid <= 1; s_bready <= 1;
            // 等两者都握手
            fork
                begin : aw_wait
                    while (!(s_awvalid && s_awready)) @(posedge clk);
                end
                begin : w_wait
                    while (!(s_wvalid && s_wready)) @(posedge clk);
                end
            join
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
            while (!(s_arvalid && s_arready)) @(posedge clk);
            @(posedge clk);
            s_arvalid <= 0;
            while (!s_rvalid) @(posedge clk);
            d = s_rdata;
            @(posedge clk);
            s_rready <= 0;
        end
    endtask

    task wait_done(input integer max_cyc);
        integer k; reg [31:0] st;
        begin
            k = 0;
            st = 0;
            while (k < max_cyc) begin
                axil_read(6'h1C, st);
                if (st[1]) begin  // DONE
                    if (st[0]) begin $display("ERR: DONE 与 BUSY 同时为 1  st=%08x | ld_busy=%b buf_valid=%b issued=%b drawn=%b done_r=%b ldv=%b ldr=%b i=%0d steps=%0d", st, dut.u_eng.ld_busy, dut.u_eng.buf_valid, dut.u_eng.issued, dut.u_eng.drawn, dut.u_eng.done_r, dut.u_eng.ld_valid, dut.u_eng.ld_ready, dut.u_eng.u_draw.i, dut.u_eng.u_draw.steps); $finish; end
                    k = max_cyc;
                end else k = k + 1;
            end
            if (!st[1]) begin $display("ERR: 等 DONE 超时"); $finish; end
        end
    endtask

    task one_line(input [17:0] x0, y0, x1, y1, input [23:0] col);
        begin
            axil_write(6'h00, {14'd0, x0});
            axil_write(6'h04, {14'd0, y0});
            axil_write(6'h08, {14'd0, x1});
            axil_write(6'h0C, {14'd0, y1});
            axil_write(6'h10, {8'd0, col});
            axil_write(6'h18, 32'd1);
            wait_done(200000);
        end
    endtask

    // ---------------- 主流程 ----------------
    integer li;
    reg [31:0] dummy;
    initial begin
        rstn = 0;
        repeat (20) @(posedge clk);
        rstn = 1;
        repeat (5) @(posedge clk);

        // FB 基地址 + 逐条线
        axil_write(6'h14, 32'h1000_0000);

        $fwrite(fd, "LINE 0\n");
        one_line(18'd100, 18'd100, 18'd200, 18'd150, 24'h00_00FF);
        $fwrite(fd, "LINE 1\n");
        one_line(18'd0, 18'd0, 18'd1279, 18'd719, 24'h00_FF00);
        $fwrite(fd, "LINE 2\n");
        one_line(18'd640, 18'd0, 18'd640, 18'd719, 24'hFF_0000);
        $fwrite(fd, "LINE 3\n");
        one_line(18'd0, 18'd360, 18'd1279, 18'd360, 24'h12_3456);
        $fwrite(fd, "LINE 4\n");
        one_line(18'd1000, 18'd700, 18'd900, 18'd650, 24'hAB_CDEF);
        $fwrite(fd, "LINE 5\n");   // 全越界 → 0 笔
        one_line(18'd1300, 18'd100, 18'd1400, 18'd200, 24'h00_00FF);
        $fwrite(fd, "LINE 6\n");   // 部分越界
        one_line(18'd1200, 18'd700, 18'd1400, 18'd800, 24'hFF_FFFF);
        $fwrite(fd, "LINE 7\n");   // 单点
        one_line(18'd500, 18'd500, 18'd500, 18'd500, 24'h00_FF00);

        $display("INFO: 共 %0d 笔写", n_wr);
        $fclose(fd);
        $display("SIM DONE");
        $finish;
    end

    // 兜底超时
    initial begin
        #200_000_000;
        $display("ERR: 全局超时");
        $finish;
    end

endmodule
