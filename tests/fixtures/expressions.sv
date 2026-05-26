// Exercise the rb-mut expression-level operators: ARITH_FLIP,
// BIT_OP_FLIP, COND_NEGATE, COND_CONST. Each operator gets ≥3
// candidate sites in this fixture.
module expressions (
    input  logic        clk,
    input  logic [7:0]  a,
    input  logic [7:0]  b,
    input  logic        c,
    input  logic        d,
    input  logic        e,
    output logic [7:0]  sum,
    output logic [7:0]  diff,
    output logic [7:0]  prod,
    output logic [7:0]  band_,
    output logic [7:0]  bor_,
    output logic [7:0]  bnot_,
    output logic [7:0]  sel
);
    // ARITH_FLIP candidates: +, -, * (3 sites)
    assign sum  = a + b;
    assign diff = a - b;
    assign prod = a * b;

    // BIT_OP_FLIP candidates: &, |, ~ (3 sites)
    assign band_ = a & b;
    assign bor_  = a | b;
    assign bnot_ = ~a;

    // COND_NEGATE / COND_CONST candidates: ternary + 2 if-statements (3 sites)
    assign sel = c ? a : b;

    always_ff @(posedge clk) begin
        if (c & d) begin
            sum <= a;
        end else if (e) begin
            sum <= b;
        end
    end
endmodule
