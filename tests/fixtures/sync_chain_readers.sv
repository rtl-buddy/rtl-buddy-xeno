// Chain-shaped fixture for CHAIN_STAGE_INSERT (and, later, the
// COMB_BETWEEN_STAGES operator): sync stages *with* and *without* a
// downstream non-blocking reader, on both multi-bit and 1-bit signals.
//
// Recognised sync stages (single-clock, single non-blocking assignment):
//   sync_meta, sync_q, sync_out, flag_meta, lone_q     -> 5
// Of those, the ones with a downstream always_ff reader:
//   sync_meta (read by sync_q), sync_q (read by sync_out),
//   flag_meta (read by the reset-bearing flag_out block) -> 3
// sync_out is read only by a continuous `assign`, and lone_q by nothing
// at all, so neither is a CHAIN_STAGE_INSERT site.
module sync_chain_readers (
    input  logic        dst_clk,
    input  logic        rst_n,
    input  logic [7:0]  src_q,
    input  logic        flag_src,
    input  logic        lone_d,
    output logic [7:0]  data_out,
    output logic        flag_out,
    output logic        lone_q
);
    // Chain A — a clean 3-stage multi-bit chain whose tail feeds a
    // continuous assign, so the tail stage has no non-blocking reader.
    logic [7:0] sync_meta, sync_q, sync_out;
    always_ff @(posedge dst_clk) sync_meta <= src_q;
    always_ff @(posedge dst_clk) sync_q    <= sync_meta;
    always_ff @(posedge dst_clk) sync_out  <= sync_q;
    assign data_out = sync_out;

    // Chain B — a 1-bit stage whose reader is a multi-edge, reset-bearing
    // always_ff. The reader may have any shape; only the *stage* has to
    // be a single-statement single-clock flop.
    logic flag_meta;
    always_ff @(posedge dst_clk) flag_meta <= flag_src;
    always_ff @(posedge dst_clk or negedge rst_n) begin
        if (!rst_n) flag_out <= 1'b0;
        else        flag_out <= flag_meta;
    end

    // A lone flop nothing downstream consumes — never a site.
    always_ff @(posedge dst_clk) lone_q <= lone_d;
endmodule
