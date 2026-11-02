// *********************************************************************************
// Project Name : OSXXXX
// Author       : dengkanwen
// Email        : dengkanwen@163.com
// Website      : http://www.opensoc.cn/
// Create Time  : 2021-05-09 09:54:24
// File Name    : .v
// Module Name  : 
// Called By    :
// Abstract     :
//
// CopyRight(c) 2018, OpenSoc Studio.. 
// All Rights Reserved
//
// *********************************************************************************
// Modification History:
// Date         By              Version                 Change Description
// -----------------------------------------------------------------------
// 2021-05-09    Kevin           1.0                     Original
//  
// *********************************************************************************
`timescale      1ns/1ns

module  cmos_data(
        // IIC 
        input                   scl                     ,       
        input                   sda                     ,       
        // Camera
        output  wire            camera_exp              ,
        output  wire            camera_stby             ,       
        input                   camera_led              ,       
        input           [ 7:0]  camera_data             ,
        input                   camera_vs               ,       
        input                   camera_hs               ,       
        // Video in Core
        output  wire            vid_active_video        ,       
        output  wire            vid_hs                  ,       
        output  wire            vid_vs                  ,       
        output  wire    [ 7:0]  vid_data                       
);

assign  camera_exp      =       1'b0;
assign  camera_stby     =       1'b0;
assign  vid_active_video=       camera_hs;
assign  vid_hs          =       ~camera_hs;
assign  vid_vs          =       ~camera_vs;
assign  vid_data        =       camera_data;


endmodule
