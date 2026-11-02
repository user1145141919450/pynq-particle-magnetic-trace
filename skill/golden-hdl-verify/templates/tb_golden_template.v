// ============================================================================
// tb_golden_template.v — golden-vector testbench skeleton
//
// Usage:
//   1) Replace the DUT instantiation and the input/output mapping below.
//   2) Export expected values from your Python fixed-point model, one value
//      per line in a hex file (see the readmemh below).
//   3) Run with your simulator; the TB prints PASS/FAIL and the first
//      mismatch (index / expected / actual), then calls $finish.
//
// Design rules that make this useful:
//   - The Python model is the only source of expected values.
//   - Compare every element, not just a checksum: a checksum hides which
//     element is wrong.
//   - Always print the first mismatch with its index.
//   - Keep the vector deterministic: fixed seed, fixed initial state.
// ============================================================================
`timescale 1ns / 1ps

module tb_golden_template;

    // ---------------- clock / reset ----------------
    reg clk = 0;
    always #5 clk = ~clk;          // 100 MHz
    reg rstn = 0;

    // ---------------- test vector storage ----------------
    // One 32-bit word per line, e.g. "0001A2B3".
    localparam integer NVEC = 1024;
    reg [31:0] exp_vec [0:NVEC-1];

    initial begin
        $readmemh("expected.hex", exp_vec);
    end

    // ---------------- DUT signals (replace) ----------------
    reg         start = 0;
    reg  [31:0] din   = 0;
    wire        done;
    wire [31:0] dout;

    // Replace with your DUT.
    // my_core dut (
    //     .clk(clk), .rstn(rstn),
    //     .start(start), .din(din),
    //     .done(done), .dout(dout)
    // );

    // ---------------- compare ----------------
    integer i;
    integer errors = 0;

    task check_one(input integer idx, input [31:0] got);
        begin
            if (got !== exp_vec[idx]) begin
                if (errors < 10) begin
                    $display("MISMATCH idx=%0d expected=0x%08x actual=0x%08x",
                             idx, exp_vec[idx], got);
                end
                errors = errors + 1;
            end
        end
    endtask

    // ---------------- stimulus ----------------
    initial begin
        rstn = 0;
        repeat (10) @(posedge clk);
        rstn = 1;
        repeat (5) @(posedge clk);

        for (i = 0; i < NVEC; i = i + 1) begin
            din   = exp_vec[i];       // replace: drive the real input
            start = 1'b1;
            @(posedge clk);
            start = 1'b0;
            // Wait for done; add a timeout so a hung DUT fails instead of
            // hanging the simulation.
            fork : wait_done
                begin
                    wait (done === 1'b1);
                    disable wait_done;
                end
                begin
                    repeat (1000) @(posedge clk);
                    $display("TIMEOUT idx=%0d", i);
                    errors = errors + 1;
                    disable wait_done;
                end
            join
            @(posedge clk);
            check_one(i, dout);
        end

        if (errors == 0) $display("PASS: %0d vectors", NVEC);
        else             $display("FAIL: %0d error(s) in %0d vectors", errors, NVEC);
        $finish;
    end

endmodule
