// Exercises RESET_FANIN_MERGE: three same-polarity reset-named ports,
// two async-reset flops resetting off *different* resets, and one plain
// single-clock flop with no reset edge at all.
module reset_fanin (
    input  logic clk,
    input  logic global_rst_n,
    input  logic local_rst_n,
    input  logic arst_n,
    input  logic d,
    output logic q_a,
    output logic q_b,
    output logic q_free
);
    always_ff @(posedge clk or negedge global_rst_n) begin
        if (!global_rst_n) q_a <= 1'b0;
        else               q_a <= d;
    end

    always_ff @(posedge clk or negedge local_rst_n) begin
        if (!local_rst_n) q_b <= 1'b0;
        else              q_b <= q_a;
    end

    always_ff @(posedge clk) q_free <= q_b;
endmodule
