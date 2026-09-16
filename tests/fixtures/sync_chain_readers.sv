// Chain-shaped fixture for CHAIN_STAGE_INSERT and COMB_BETWEEN_STAGES:
// sync stages *with* and *without* a downstream non-blocking
// reader, on both multi-bit and 1-bit signals.
//
// Recognised stages, shape A (single-clock, single non-blocking assignment):
//   sync_meta, sync_q, sync_out, flag_meta, lone_q, sg_meta, sg_q  -> 7
// Recognised stages, shape B (two-edge sensitivity, if/else with a
// constant reset branch — xeno#34):
//   flag_out, rst_meta, rst_q                                      -> 3
// Of those 10, the ones with a same-clock, same-module, direct
// non-blocking reader:
//   sync_meta (read by sync_q), sync_q (read by sync_out),
//   flag_meta (read by the reset-bearing flag_out block),
//   sg_meta (read by sg_q), rst_meta (read by rst_q)               -> 5
// sync_out, sg_q and rst_q are read only by a continuous `assign`,
// lone_q by nothing at all, and flag_out drives an output nothing
// re-reads, so none of the five is a site for either operator.
// SYNC_CHAIN_DEPTH_PERTURB does not use this fixture, and would see
// only the 7 shape-A stages if it did (see find_stage_spans).
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
    output logic signed [3:0] sg_out,
    output logic              rst_out
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

    // Chain D — the rtl-buddy-cdc corpus shape (xeno#34): every stage
    // carries an async active-low reset, `begin`/`end`-wrapped. Both
    // stages are recognised; only `rst_meta` has a non-blocking reader
    // (`rst_q`), whose tail feeds the output through a continuous
    // assign — so chain D contributes exactly one site per operator.
    logic rst_meta, rst_q;
    always_ff @(posedge dst_clk or negedge rst_n) begin
        if (!rst_n) rst_meta <= 1'b0;
        else        rst_meta <= flag_src;
    end
    always_ff @(posedge dst_clk or negedge rst_n) begin
        if (!rst_n) rst_q <= 1'b0;
        else        rst_q <= rst_meta;
    end
    assign rst_out = rst_q;

    // A lone flop nothing downstream consumes — never a site.
    always_ff @(posedge dst_clk) lone_q <= lone_d;
endmodule
