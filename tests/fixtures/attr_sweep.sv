// Five SV attribute blocks across the CDC-relevant prediction set.
// Exercises ATTRIBUTE_TOGGLE — each attribute strips to a valid SV
// declaration since attributes are always optional in the grammar.
module attr_sweep (
    input  logic clk,
    input  logic rst_n,
    input  logic [3:0] gray_in,
    output logic       sel
);
    (* cdc_sync *)             logic sync_q;
    (* cdc_gray *)             logic [3:0] gray_q;
    (* glitchless_clock_mux *) logic muxed_clk;
    (* reset_sync *)           logic rst_sync_q;
    (* reset_polarity = "low" *) logic rst_polar_q;

    always_ff @(posedge clk) sync_q     <= sel;
    always_ff @(posedge clk) gray_q     <= gray_in;
    assign muxed_clk = clk;
    always_ff @(posedge clk or negedge rst_n) rst_sync_q   <= sel;
    always_ff @(posedge clk or negedge rst_n) rst_polar_q  <= sel;
endmodule
