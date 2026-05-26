// Exercises PORT_BINDING_SWAP (two instances with ≥3 named ports each)
// and RESET_POLARITY_FLIP (two reset-bearing always_ff blocks with
// active-low async resets and one extra reset edge for variety).
module child (
    input  logic clk,
    input  logic rst_n,
    input  logic d,
    input  logic en,
    output logic q
);
endmodule

module instances_and_resets (
    input  logic clk,
    input  logic rst_n,
    input  logic d_a,
    input  logic en_a,
    input  logic d_b,
    input  logic en_b,
    output logic q_a,
    output logic q_b
);
    child u_a (
        .clk(clk),
        .rst_n(rst_n),
        .d(d_a),
        .en(en_a),
        .q(q_a)
    );

    child u_b (
        .clk(clk),
        .rst_n(rst_n),
        .d(d_b),
        .en(en_b),
        .q(q_b)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q_a <= 1'b0;
        else if (en_a) q_a <= d_a;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q_b <= 1'b0;
        else if (en_b) q_b <= d_b;
    end
endmodule
