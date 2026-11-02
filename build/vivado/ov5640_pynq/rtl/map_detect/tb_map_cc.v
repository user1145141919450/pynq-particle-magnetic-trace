// ============================================================================
// tb_map_cc.v —— map_cc（形态学 OPEN + 8 连通 CCL）自检
//
// 向量：sim/map_detect/mask_gap1.hex（11520 个 3 位掩码，raster 序）
// 参考：sim/check_map_cc.py 用同一份掩码 + cv2.morphologyEx/connectedComponents
//       重算连通域，与本 TB 导出的 cand 列表比对（面积/Σx/Σy 逐值）。
//
// 用法（仓库根目录）：sim/run_map_detect_sim.sh 会跑；也可手敲：
//   xvlog vivado/ov5640_pynq/rtl/map_detect/map_cc.v \
//         vivado/ov5640_pynq/rtl/map_detect/tb_map_cc.v
//   xelab tb_map_cc -s tb_map_cc
//   xsim tb_map_cc -R
// ============================================================================
`timescale 1ns / 1ps

module tb_map_cc;

    localparam integer NPIX = 11520;
    localparam integer TIMEOUT = 2000000;   // cycles

    reg clk = 0, rstn = 0;
    always #5 clk = ~clk;

    reg        start = 0;
    reg        in_valid = 0;
    reg [2:0]  in_mask = 0;
    wire       busy, done, cand_ovf, label_ovf;
    wire [5:0] n_cand;
    reg  [5:0] cand_addr = 0;
    wire [56:0] cand_data;

    reg [15:0] mask_mem [0:NPIX-1];

    map_cc dut (
        .clk(clk), .rstn(rstn),
        .start(start), .in_valid(in_valid), .in_mask(in_mask),
        .busy(busy), .done(done), .n_cand(n_cand),
        .cand_ovf(cand_ovf), .label_ovf(label_ovf),
        .cand_addr(cand_addr), .cand_data(cand_data)
    );

    integer i, to, j, fd, fd2;
    reg done_seen = 0;
    reg [2:0] ov;
    reg [1:0]  cls;
    reg [13:0] area;
    reg [20:0] sx;
    reg [19:0] sy;

    always @(posedge clk) if (done) done_seen <= 1'b1;

    initial begin
        $readmemh("sim/map_detect/mask_gap1.hex", mask_mem);
        fd = $fopen("sim/_logs/map_cc_cand.txt", "w");
        if (fd == 0) begin
            $display("!! 打不开 sim/_logs/map_cc_cand.txt");
            $finish;
        end

        repeat (5) @(posedge clk);
        rstn <= 1'b1;
        @(negedge clk);
        start = 1'b1;
        @(negedge clk);
        start = 1'b0;

        for (i = 0; i < NPIX; i = i + 1) begin
            @(negedge clk);
            in_valid = 1'b1;
            in_mask  = mask_mem[i][2:0];
        end
        @(negedge clk);
        in_valid = 1'b0;

        to = 0;
        while (!done_seen && to < TIMEOUT) begin
            @(negedge clk);
            to = to + 1;
        end

        $display("------------------------------------------------------------");
        if (!done_seen) begin
            $display(">>> FAIL timeout（%0d cycles，busy=%b n_cand=%0d）", to, busy, n_cand);
        end else begin
            $display("  busy=%b cand_ovf=%b label_ovf=%b n_cand=%0d",
                     busy, cand_ovf, label_ovf, n_cand);
            for (j = 0; j < n_cand; j = j + 1) begin
                cand_addr = j[5:0];
                #1;
                cls  = cand_data[56:55];
                area = cand_data[54:41];
                sx   = cand_data[40:20];
                sy   = cand_data[19:0];
                $fwrite(fd, "%0d %0d %0d %0d\n", cls, area, sx, sy);
                $display("  cand[%0d] cls=%0d area=%0d sx=%0d sy=%0d",
                         j, cls, area, sx, sy);
            end
            $fclose(fd);
            // 把形态学 OPEN 后的掩码也导出来（定位形态学 vs CCL 用）
            fd2 = $fopen("sim/_logs/map_cc_open.hex", "w");
            if (fd2 != 0) begin
                for (i = 0; i < NPIX; i = i + 1) begin
                    ov = dut.open_buf[i];
                    $fwrite(fd2, "%x\n", ov);
                end
                $fclose(fd2);
            end
            if (n_cand !== 6'd9 || cand_ovf || label_ovf)
                $display(">>> FAIL n_cand 应为 9 / 不应有 overflow");
            else
                $display(">>> PASS map_cc（候选数 9；逐值对拍见 check_map_cc.py）");
        end
        $display("------------------------------------------------------------");
        $finish;
    end

endmodule
