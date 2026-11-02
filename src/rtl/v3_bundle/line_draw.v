// ============================================================================
// line_draw.v —— PL 端整数 DDA 画线核（2026-10-30 v4）
//
// 目的：把「轨迹线」从 PS 的 cv2 画线（~100ms/帧）搬到 PL。
//   输入：一条线的两个端点（18 位）+ 颜色
//   输出：framebuffer 像素流（valid/ready 握手），上层打包成 AXI 写
//
// 算法：整数 DDA（每拍最多走一步）：
//     ex += |dx|; if (ex >= steps) { x += sx; ex -= steps; }
//     ey += |dy|; if (ey >= steps) { y += sy; ey -= steps; }
//   共 max(|dx|,|dy|)+1 个点；1280px 最长线 = 1281 点。
//   ★ 主轴（steps 那个轴）每步必进一格 ⇒ 不会出现重复点。
//
// 版本教训（都是上板前/门禁前实测，别退回去）：
//  ★ v1：`fb_we` 是寄存器电平，而写主一笔要 5~10 拍 ⇒ 反压未生效那一拍会再发
//    一个像素（被写主丢掉）、反压期间重复消费。→ v2 改 valid/ready 握手。
//  ★ v2：DDA 误差加法 → 坐标 → y*1280+x 全挤一拍，实测 WNS **-0.901ns**
//    （全设计 10 条违例全在这一条路上）。→ v3 把"出示像素"拆成独立级。
//  ★ v3：剩下的关键路径还是 ex→比较→x+1 一串（OOC 6.7ns）。→ v4 把 DDA 推进
//    再拆两级：**阈值比较**（ex >= steps-ax，常数阈值）一级、**坐标/误差更新**
//    （mux + 加常数）一级。每级都是"寄存器→一两级逻辑→寄存器"。
//
// 代价：每个像素 3 拍（判定位 / 更新 / 出示）+ 等写主接收；写主一笔本来就要
//   5~10 拍，吞吐不受影响。
//
// 设计约定：
//   · 端点 18 位无符号（屏幕坐标 0..1279 / 0..719；越界点直接丢弃不写）
//   · 颜色 24 位 = {B[23:16], G[15:8], R[7:0]}（与 cv2 BGR 一致）
//   · FB_W 必须是 1280（地址用移位加法：y*1280 = (y<<10)+(y<<8)）
// ============================================================================
`timescale 1ns / 1ps

module line_draw #(
    parameter integer FB_W = 1280,
    parameter integer FB_H = 720
)(
    input  wire        clk,
    input  wire        rstn,

    // ---- 启动 ----
    input  wire        start,          // 单拍脉冲（本核非 busy 时才生效）
    input  wire [17:0] x0,
    input  wire [17:0] y0,
    input  wire [17:0] x1,
    input  wire [17:0] y1,
    input  wire [23:0] color,          // {B,G,R}

    // ---- framebuffer 像素流（valid/ready）----
    output reg         fb_valid,       // 当前像素有效，保持到被接收
    output reg  [20:0] fb_addr,        // 线性像素号：y*FB_W + x
    output reg  [23:0] fb_data,
    input  wire        fb_ready,       // 接收方空闲=1（本拍接收；valid&&ready 才推进）

    output reg         busy,
    output reg         done            // 单拍：最后一个像素已被接收
);

    localparam S_IDLE = 1'b0, S_RUN = 1'b1;

    reg        st;
    reg        pend;                   // 有 1 个点等着出示
    reg        adv;                    // 判定位已算好，等更新
    reg        ex_w, ey_w;             // 本步该不该在 x/y 上走一格（寄存的判定位）
    reg [19:0] thr_x, thr_y;           // steps - ax / steps - ay（常数阈值）
    reg [19:0] dax, day;               // ax - steps / ay - steps（补码常数）
    reg [17:0] ax, ay;
    reg [18:0] steps;
    reg [18:0] i;
    reg signed [19:0] x, y, sx, sy;
    reg [19:0] ex, ey;
    reg [23:0] col;

    // ---- 端点差（组合，只在 start 那一拍用）----
    wire [17:0] ax_w = (x1 >= x0) ? (x1 - x0) : (x0 - x1);
    wire [17:0] ay_w = (y1 >= y0) ? (y1 - y0) : (y0 - y1);
    wire [18:0] steps_w = (ax_w >= ay_w) ? {1'b0, ax_w} : {1'b0, ay_w};

    // ---- 出示级：从**寄存器** x/y 算（地址 = (y<<10)+(y<<8)+x，无乘法）----
    wire inb_c  = (!x[19]) && (x[19:0] < FB_W) && (!y[19]) && (y[19:0] < FB_H);
    wire [20:0] addr_c = ({1'b0, y[19:0]} << 10) + ({1'b0, y[19:0]} << 8)
                         + {1'b0, x[19:0]};

    always @(posedge clk) begin
        if (!rstn) begin
            st <= S_IDLE;
            busy <= 1'b0; done <= 1'b0; pend <= 1'b0; adv <= 1'b0;
            ex_w <= 1'b0; ey_w <= 1'b0;
            thr_x <= 20'd0; thr_y <= 20'd0; dax <= 20'd0; day <= 20'd0;
            fb_valid <= 1'b0; fb_addr <= 21'd0; fb_data <= 24'd0;
            x <= 20'sd0; y <= 20'sd0; sx <= 20'sd0; sy <= 20'sd0;
            ex <= 20'd0; ey <= 20'd0; i <= 19'd0; steps <= 19'd0;
            ax <= 18'd0; ay <= 18'd0; col <= 24'd0;
        end else begin
            done <= 1'b0;
            if (st == S_IDLE) begin
                fb_valid <= 1'b0;
                pend <= 1'b0; adv <= 1'b0;
                if (start) begin
                    ax <= ax_w;
                    ay <= ay_w;
                    steps <= steps_w;
                    // 阈值/常数：ex >= steps-ax 等价于 ex+ax >= steps（比较器只吃常数）
                    thr_x <= (ax_w >= ay_w) ? 20'd0 : ({1'b0, steps_w} - {2'b0, ax_w});
                    thr_y <= (ay_w >= ax_w) ? 20'd0 : ({1'b0, steps_w} - {2'b0, ay_w});
                    dax   <= {2'b0, ax_w} - {1'b0, steps_w};   // = ax - steps（补码）
                    day   <= {2'b0, ay_w} - {1'b0, steps_w};   // = ay - steps（补码）
                    sx <= (x1 > x0) ? 20'sd1 : ((x1 < x0) ? -20'sd1 : 20'sd0);
                    sy <= (y1 > y0) ? 20'sd1 : ((y1 < y0) ? -20'sd1 : 20'sd0);
                    x  <= $signed({2'b00, x0});
                    y  <= $signed({2'b00, y0});
                    ex <= 20'd0; ey <= 20'd0;
                    i  <= 19'd0;
                    col <= color;
                    busy <= 1'b1;
                    pend <= 1'b1;          // 第 0 个点直接进出示级
                    st <= S_RUN;
                end
            end else begin
                if (pend) begin
                    // ---- 出示级 ----
                    fb_valid <= inb_c;
                    fb_data  <= col;
                    if (inb_c)
                        fb_addr <= addr_c;
                    pend <= 1'b0;
                end else if (!adv) begin
                    // ---- 判定位级：常数阈值比较（寄存器→比较器→寄存器）----
                    if (fb_valid && fb_ready) begin
                        fb_valid <= 1'b0;
                        if (i >= steps) begin
                            busy <= 1'b0; done <= 1'b1; st <= S_IDLE;
                        end else begin
                            ex_w <= (ex >= thr_x);
                            ey_w <= (ey >= thr_y);
                            adv  <= 1'b1;
                        end
                    end else if (!fb_valid) begin
                        if (i >= steps) begin
                            busy <= 1'b0; done <= 1'b1; st <= S_IDLE;
                        end else begin
                            ex_w <= (ex >= thr_x);
                            ey_w <= (ey >= thr_y);
                            adv  <= 1'b1;
                        end
                    end
                end else begin
                    // ---- 更新级：mux + 加常数（寄存器→mux→加法器→寄存器）----
                    ex <= ex + (ex_w ? dax : {2'b0, ax});
                    ey <= ey + (ey_w ? day : {2'b0, ay});
                    x  <= x + (ex_w ? sx : 20'sd0);
                    y  <= y + (ey_w ? sy : 20'sd0);
                    i  <= i + 19'd1;
                    adv <= 1'b0;
                    pend <= 1'b1;
                end
            end
        end
    end

endmodule
