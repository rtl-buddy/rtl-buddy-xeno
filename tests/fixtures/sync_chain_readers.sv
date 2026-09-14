// Chain-shaped fixture for CHAIN_STAGE_INSERT (and, later, the
// COMB_BETWEEN_STAGES operator): sync stages *with* and *without* a
// downstream non-blocking reader, on both multi-bit and 1-bit signals.
//
// Recognised sync stages (single-clock, single non-blocking assignment):
//   sync_meta, sync_q, sync_out, flag_meta, lone_q, sg_meta, sg_q  -> 7
// Of those, the ones with a same-clock, same-module, direct
// non-blocking reader:
//   sync_meta (read by sync_q), sync_q (read by sync_out),
//   flag_meta (read by the reset-bearing flag_out block),
//   sg_meta (read by sg_q)                                         -> 4
// sync_out and sg_q are read only by a continuous `assign`, and lone_q
// by nothing at all, so none of the three is a CHAIN_STAGE_INSERT site.
module sync_chain_readers (
    input  logic              dst_clk,
    input  logic              rst_n,
    input  logic [7:0]        src_q,
    input  logic              flag_src,
    input  logic              lone_d,
    input  logic signed [3:0] sg_src,
    output logic [7:0]        data_out,
    output logic              flag_out,
    output logic              lone_q,
    output logic signed [3:0] sg_out
);
    // Chain A — a clean 3-stage multi-bit chain whose tail feeds a
    // continuous assign, so the tail stage has no non-blocking reader.
    logic [7:0] sync_meta, sync_q, sync_out;
    always_ff @(posedge dst_clk) sync_meta <= src_q;
    always_ff @(posedge dst_clk) sync_q    <= sync_meta;
    always_ff @(posedge dst_clk) sync_out  <= sync_q;
    assign data_out = sync_out;

    // Chain B — a 1-bit stage whose reader is a multi-edge, reset-bearing
    // always_ff. The reader may have any shape as long as it is clocked
    // on the stage's own edge and reads the stage's Q directly; only the
    // *stage* has to be a single-statement single-clock flop.
    logic flag_meta;
    always_ff @(posedge dst_clk) flag_meta <= flag_src;
    always_ff @(posedge dst_clk or negedge rst_n) begin
        if (!rst_n) flag_out <= 1'b0;
        else        flag_out <= flag_meta;
    end

    // Chain C — a signed segment. The inserted register (and the comb
    // net COMB_BETWEEN_STAGES injects) must copy `logic signed [3:0]`
    // verbatim; a `[$bits(...)-1:0]` spelling would silently drop the
    // signedness.
    logic signed [3:0] sg_meta, sg_q;
    always_ff @(posedge dst_clk) sg_meta <= sg_src;
    always_ff @(posedge dst_clk) sg_q    <= sg_meta;
    assign sg_out = sg_q;

    // A lone flop nothing downstream consumes — never a site.
    always_ff @(posedge dst_clk) lone_q <= lone_d;
endmodule
