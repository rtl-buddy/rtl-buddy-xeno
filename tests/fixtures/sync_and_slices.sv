// Exercises SYNC_CHAIN_DEPTH_PERTURB (three independent sync-chain
// stages — each is a single-clock single-non-blocking always_ff) and
// BIT_EXTRACT_PERMUTE (multiple bit-select ranges with integer-literal
// bounds).
module sync_and_slices (
    input  logic        clk,
    input  logic [15:0] wide_in,
    input  logic [7:0]  d_src,
    output logic [3:0]  nibble_out,
    output logic [3:0]  high_nibble_out,
    output logic [7:0]  q_dst
);
    logic [7:0] q0, q1;
    logic [3:0] nib_q0;

    // Three sync stages — each is a single-clock single-statement
    // always_ff (the SYNC_CHAIN_DEPTH_PERTURB shape).
    always_ff @(posedge clk) q0    <= d_src;
    always_ff @(posedge clk) q1    <= q0;
    always_ff @(posedge clk) q_dst <= q1;

    // Two bit-select ranges with integer-literal bounds for
    // BIT_EXTRACT_PERMUTE.
    assign nibble_out      = wide_in[3:0];
    assign high_nibble_out = wide_in[15:12];

    // Another sync chain on a narrower bus, for additional coverage.
    always_ff @(posedge clk) nib_q0 <= wide_in[3:0];
endmodule
