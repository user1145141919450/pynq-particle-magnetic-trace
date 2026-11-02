// *********************************************************************************
// Project Name : OSXXXX
// Author       : dengkanwen
// Email        : dengkanwen@163.com
// Website      : http://www.opensoc.cn/
// Create Time  : 2017/5/21 17:52:33
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
// 2017/5/21    Kevin           1.0                     Original
//  
// *********************************************************************************
`timescale      1ns/1ns

module  ov5640_data(
        // system signals
        input                   s_rst_n                 ,       
        // OV5640
        input                   ov5640_pclk             ,       
        input                   ov5640_href             ,       
        input                   ov5640_vsync            ,       
        input           [ 7:0]  ov5640_data             ,       
        // User Interfaces
        output  reg     [15:0]  m_data                  ,
        output  reg             m_wr_en                   
);

//========================================================================\
// =========== Define Parameter and Internal signals =========== 
//========================================================================/
wire                            ov5640_vsync_pos                ;
reg                             ov5640_vsync_r1                 ;

reg                             byte_flag                       ;       // 0: first byte, 1: second byte
reg     [ 3:0]                  frame_cnt                       ;
wire                            frame_vaild                     ;
//=============================================================================
//**************    Main Code   **************
//=============================================================================
always  @(posedge ov5640_pclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                m_data  <=      'h0;
        else if(byte_flag == 1'b1)
                m_data  <=      {m_data[15:8], ov5640_data};
        else
                m_data  <=      {ov5640_data, m_data[7:0]};
end

always  @(posedge ov5640_pclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                byte_flag       <=      1'b0;
        else if(ov5640_href == 1'b1)
                byte_flag       <=      ~byte_flag;
        else
                byte_flag       <=      1'b0;
end

always  @(posedge ov5640_pclk) begin
        ov5640_vsync_r1 <=      ov5640_vsync;
end

always  @(posedge ov5640_pclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                frame_cnt       <=      'd0;
        else if(frame_vaild == 1'b0 && ov5640_vsync_pos == 1'b1)
                frame_cnt       <=      frame_cnt + 1'b1;
end

always  @(posedge ov5640_pclk or negedge s_rst_n) begin
        if(s_rst_n == 1'b0)
                m_wr_en <=      1'b0;
        else if(frame_vaild == 1'b1 && byte_flag == 1'b1)
                m_wr_en <=      1'b1;
        else
                m_wr_en <=      1'b0;
end

assign  frame_vaild             =       (frame_cnt >= 'd10) ? 1'b1 : 1'b0;
assign  ov5640_vsync_pos        =       ov5640_vsync & ~ov5640_vsync_r1;

endmodule
