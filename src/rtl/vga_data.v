module  vga_data(
        input                   active_video                    ,
        input           [15:0]  rgb_data_i                      ,
        output  wire    [15:0]  rgb_data_o                      
);

assign  rgb_data_o      =       (active_video == 1'b1) ? {rgb_data_i[7:0], rgb_data_i[15:8]} : 16'h0;

endmodule