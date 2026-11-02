// *********************************************************************************
// Project Name : OSXXXX
// Author       : dengkanwen
// Email        : dengkanwen@163.com
// Website      : http://www.opensoc.cn/
// Create Time  : 2017/5/9 19:10:40
// File Name    : .v
// Module Name  : 
// Called By    :
// Abstract     :
//
// CopyRight(c) 2016, OpenSoc Studio.. 
// All Rights Reserved
//
// *********************************************************************************
// Modification History:
// Date         By              Version                 Change Description
// -----------------------------------------------------------------------
// 2017/5/9    Kevin           1.0                     Original
//  
// *********************************************************************************
`timescale      1ns/1ns

module  ov5640_top(
        // system signals
        input                   s_rst_n                 ,       
        input                   clk_sys50m              ,       
        input                   clk_sys24m              ,       
        `ifdef  SIM
        input                   div_clk                 ,       
        `endif
        // COMS Interfaces
        output  wire            ov5640_pwdn             ,       
        output  wire            ov5640_rst_n            ,       
        output  wire            ov5640_xclk             ,       
        output  wire            ov5640_iic_scl          ,       
        inout                   ov5640_iic_sda          ,       
        input                   ov5640_pclk             ,       
        input                   ov5640_href             ,       
        input                   ov5640_vsync            ,       
        input           [ 7:0]  ov5640_data             ,    
        // User Interfaces
        output  wire    [15:0]  m_data                  ,
        output  wire            m_wr_en                 ,
        // Debug Interfaces
        input                   estart                  ,
        input           [31:0]  ewdata                  ,
        output  wire    [ 7:0]  riic_data               
);

//========================================================================\
// =========== Define Parameter and Internal signals =========== 
//========================================================================/
wire                            power_done                      ;       
reg     [ 8:0]                  div_cnt                         ;       
`ifndef SIM
wire                            div_clk                         ;      
`endif
reg     [ 2:0]                  estart_arr                      ;       
wire                            start                           ;       
//=============================================================================
//**************    Main Code   **************
//=============================================================================
`ifndef SIM
always  @(posedge clk_sys50m or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                div_cnt <=      'd0;
        else
                div_cnt <=      div_cnt + 1'b1;
end

always  @(posedge div_clk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                estart_arr      <=      'd0;
        else
                estart_arr      <=      {estart_arr[1:0], estart};
end


assign  div_clk         =       div_cnt[8];
`endif
assign  start           =       estart_arr[1] & ~estart_arr[2];
assign  ov5640_xclk     =       clk_sys24m;

power_ctrl      power_ctrl_inst(
        // system signals
        .sclk                   (clk_sys50m             ),      // 50MHz
        .s_rst_n                (s_rst_n                ),
        // ov5640 power interfaces
        .ov5640_pwdn            (ov5640_pwdn            ),
        .ov5640_rst_n           (ov5640_rst_n           ),
        // others
        .power_done             (power_done             )
);
 
ov5640_data     ov5640_data_inst(
        // system signals
        .s_rst_n                (s_rst_n                ),
        // OV5640
        .ov5640_pclk            (ov5640_pclk            ),
        .ov5640_href            (ov5640_href            ),
        .ov5640_vsync           (ov5640_vsync           ),
        .ov5640_data            (ov5640_data            ),
        // User Interfaces
        .m_data                 (m_data                 ),
        .m_wr_en                (m_wr_en                ) 
       
);

ov5640_cfg      ov5640_cfg_inst(
        // system signals
        .sclk                   (div_clk                ),
        .s_rst_n                (s_rst_n & power_done   ),
        // IIC Interfaces
        .iic_scl                (ov5640_iic_scl         ),
        .iic_sda                (ov5640_iic_sda         ),
        // Debug Interfaces
        .estart                 (start                  ),
        .ewdata                 (ewdata                 ),
        .riic_data              (riic_data              )
);


endmodule
