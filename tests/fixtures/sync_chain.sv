// Three-stage sync chain. Exercises CLOCK_POLARITY_SWAP (3 posedge
// tokens) and ASSIGN_DROP (3 non-blocking assignments).
module sync_chain (
    input  logic clk_dst,
    input  logic d_src,
    output logic q_dst
);
    logic q0, q1;
    always_ff @(posedge clk_dst) q0    <= d_src;
    always_ff @(posedge clk_dst) q1    <= q0;
    always_ff @(posedge clk_dst) q_dst <= q1;
endmodule
