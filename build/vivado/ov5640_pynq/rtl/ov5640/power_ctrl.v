// *********************************************************************************
// Project Name : OSXXXX
// Author       : dengkanwen
// Email        : dengkanwen@163.com
// Website      : http://www.opensoc.cn/
// Create Time  : 2017/4/30 17:41:32
// File Name    : .v
// Module Name  : 
// Called By    :
// Abstract     :
//
// CopyRight(c) 2017, OpenSoc Studio.. 
// All Rights Reserved
//
// *********************************************************************************
// Modification History:
// Date         By              Version                 Change Description
// -----------------------------------------------------------------------
// 2017/4/30    Kevin           1.0                     Original
//  
// *********************************************************************************
`timescale      1ns/1ns

module  power_ctrl(
        // system signals
        input                   sclk                    ,       // 50MHz
        input                   s_rst_n                 ,       
        // ov5640 power interfaces
        output  wire            ov5640_pwdn             ,       
        output  wire            ov5640_rst_n            ,       
        // others
        output  wire            power_done                     
);

//========================================================================\
// =========== Define Parameter and Internal signals =========== 
//========================================================================/
localparam      DELAY_6MS       =       30_0000                 ;
localparam      DELAY_2MS       =       10_0000                 ;
localparam      DELAY_21MS      =       105_0000                ;

reg     [18:0]                  cnt_6ms                         ;       
reg     [16:0]                  cnt_2ms                         ;       
reg     [20:0]                  cnt_21ms                        ;       
//=============================================================================
//**************    Main Code   **************
//=============================================================================
always  @(posedge sclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                cnt_6ms <=      'd0;
        else if(ov5640_pwdn == 1'b1)
                cnt_6ms <=      cnt_6ms + 1'b1;
end

always  @(posedge sclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                cnt_2ms <=      'd0;
        else if(ov5640_rst_n == 1'b0 && ov5640_pwdn == 1'b0)
                cnt_2ms <=      cnt_2ms + 1'b1;
end

always  @(posedge sclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                cnt_21ms        <=      'd0;
        else if(ov5640_rst_n == 1'b1 && power_done == 1'b0)
                cnt_21ms        <=      cnt_21ms + 1'b1;
end

assign  power_done      =       (cnt_21ms >= DELAY_21MS) ? 1'b1 : 1'b0;
assign  ov5640_pwdn     =       (cnt_6ms >= DELAY_6MS) ? 1'b0 : 1'b1;
assign  ov5640_rst_n    =       (cnt_2ms >= DELAY_2MS) ? 1'b1 : 1'b0;

endmodule
