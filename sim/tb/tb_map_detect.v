// ============================================================================
// tb_map_detect.v —— map_detect_core 第一版自检：注入真实缩略图快照，
// 对拍 PS 参考值（map_snap_gap1.bin，frame_cnt=15648）：
//   med=132 vhi=72 red=21 blue=115 black=716
//
// 向量：sim/map_detect/map_snap_gap1.hex（11520 个 16 位 RGB565，raster 序）
// 跑法：仓库根目录执行 sim/run_map_detect_sim.sh
// ============================================================================
`timescale 1ns / 1ps

module tb_map_detect;

    localparam integer NPIX = 11520;
    localparam integer NW   = 5760;
    localparam integer EXP_MED   = 132;
    localparam integer EXP_VHI   = 72;
    localparam integer EXP_RED   = 21;
    localparam integer EXP_BLUE  = 115;
    localparam integer EXP_BLACK = 716;
    localparam integer TIMEOUT   = 500000;   // cycles（v1 含 CCL，约 150k~190k）

    reg clk = 1'b0;
    reg rstn = 1'b0;
    reg        wr_en = 1'b0;
    reg [14:0] wr_addr = 15'd0;
    reg [31:0] wr_data = 32'd0;

    wire [7:0]  med, vhi;
    wire [15:0] cnt_red, cnt_blue, cnt_black;
    wire [31:0] frame_seq;
    wire        busy, done, ovf;
    wire        dbg_we;
    wire [13:0] dbg_addr;
    wire [23:0] dbg_data;

    reg [15:0] pix_mem [0:NPIX-1];

    integer i, to, fd;
    reg done_seen = 1'b0;
    reg [7:0] dbg_h, dbg_s, dbg_v;

    map_detect_core #(
        .NW(NW),
        .DEBUG(1)
    ) dut (
        .clk(clk),
        .rstn(rstn),
        .wr_en(wr_en),
        .wr_addr(wr_addr),
        .wr_data(wr_data),
        .med(med),
        .vhi(vhi),
        .cnt_red(cnt_red),
        .cnt_blue(cnt_blue),
        .cnt_black(cnt_black),
        .frame_seq(frame_seq),
        .busy(busy),
        .done(done),
        .ovf(ovf),
        .dbg_we(dbg_we),
        .dbg_addr(dbg_addr),
        .dbg_data(dbg_data)
    );

    always #5 clk = ~clk;   // 100 MHz

    always @(posedge clk)
        if (done) done_seen <= 1'b1;

    // 逐像素调试落盘：addr H S V
    always @(posedge clk) begin
        if (dbg_we) begin
            dbg_h = dbg_data[23:16];
            dbg_s = dbg_data[15:8];
            dbg_v = dbg_data[7:0];
            $fwrite(fd, "%04x %02x %02x %02x\n", dbg_addr, dbg_h, dbg_s, dbg_v);
        end
    end

    initial begin
        $readmemh("sim/map_detect/map_snap_gap1.hex", pix_mem);
        fd = $fopen("sim/_logs/map_detect_dbg.hex", "w");
        if (fd == 0) begin
            $display("!! 打不开 sim/_logs/map_detect_dbg.hex");
            $finish;
        end

        repeat (5) @(posedge clk);
        rstn <= 1'b1;
        @(posedge clk);

        // ---- 逐字注入：低 16 = 偶列，高 16 = 奇列；wr_addr 是字节地址 ----
        for (i = 0; i < NW; i = i + 1) begin
            @(negedge clk);
            wr_en   = 1'b1;
            wr_addr = i * 4;
            wr_data = {pix_mem[2*i+1], pix_mem[2*i]};
        end
        @(negedge clk);
        wr_en = 1'b0;

        // ---- 等 done ----
        to = 0;
        while (!done_seen && to < TIMEOUT) begin
            @(negedge clk);
            to = to + 1;
        end
        $fclose(fd);

        $display("------------------------------------------------------------");
        if (!done_seen) begin
            $display(">>> FAIL timeout（%0d cycles，busy=%b st内部未收尾）", to, busy);
        end else begin
            $display("  med=%0d (exp %0d)", med, EXP_MED);
            $display("  vhi=%0d (exp %0d)", vhi, EXP_VHI);
            $display("  red=%0d (exp %0d)", cnt_red, EXP_RED);
            $display("  blue=%0d (exp %0d)", cnt_blue, EXP_BLUE);
            $display("  black=%0d (exp %0d)", cnt_black, EXP_BLACK);
            $display("  frame_seq=%0d ovf=%b busy=%b", frame_seq, ovf, busy);
            if (med === EXP_MED && vhi === EXP_VHI &&
                cnt_red === EXP_RED && cnt_blue === EXP_BLUE &&
                cnt_black === EXP_BLACK && frame_seq === 32'd1 && !ovf)
                $display(">>> PASS map_detect_core v1（真实快照：计数对拍；候选见 tb_map_cc/tb_map_detect_axi）");
            else
                $display(">>> FAIL 结果与 PS 参考不一致");
        end
        $display("------------------------------------------------------------");
        $finish;
    end

endmodule
