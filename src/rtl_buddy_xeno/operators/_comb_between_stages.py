"""``COMB_BETWEEN_STAGES`` — inject an inverter between adjacent sync stages.

The third chain-shaped operator, after ``SYNC_CHAIN_DEPTH_PERTURB``
(stage deletion) and ``CHAIN_STAGE_INSERT`` (:mod:`._chain_stage_insert`,
stage synthesis). This one leaves the chain's *depth* alone and breaks
its *purity* instead: the downstream reader stops consuming the stage's
Q directly and goes through a combinational cell, which is exactly the
structural precondition CDC-014 (combinational logic between sync-chain
stages) looks for.

.. code-block:: systemverilog

    // parent
    always_ff @(posedge dst_clk) sync_meta <= src_q;
    always_ff @(posedge dst_clk) sync_q    <= sync_meta;

    // mutant (site = stage `sync_meta`)
    always_ff @(posedge dst_clk) sync_meta <= src_q;
    wire [7:0] sync_meta_xeno_comb_1 = ~sync_meta;
    always_ff @(posedge dst_clk) sync_q    <= sync_meta_xeno_comb_1;

**Async-reset stages too** (xeno#34). ``_chain_helpers`` recognises a
second stage shape — ``always_ff @(posedge clk or negedge rst_n) if
(!rst_n) q <= 1'b0; else q <= d;``, ``begin``/``end``-wrapped or bare,
active-high or active-low. Nothing about the emission changes: the
interposed net is the same ``wire ... = ~<lhs>;`` line after the block,
and the reader's rewritten span is its ``else``-branch right-hand side
(the reset branch assigns a constant, so it never matches the stage's
Q). The reset-bearing shape is what rtl-buddy-cdc's fuzz corpus writes
for most of its parents, so recognising it is what gives CDC-014 sites
outside the handful of reset-free templates.

**Parser layer: Verible CST only** (see the no-straddle rule in
:mod:`rtl_buddy_xeno.operators`). Stage discovery and the forward walk
to the reader come from :mod:`._chain_helpers`; emission is a pair of
byte-splices on the original source string. No pyslang.

**Sites.** Every recognised sync stage with a copyable declared type
and a downstream reader — there has to be a *direct* flop-to-flop wire
before there is anything to interpose on. "Reader" is
:func:`._chain_helpers.find_downstream_reader`'s call: an ``always_ff``
in the stage's own module, clocked on the same edge, whose non-blocking
right-hand side is the stage's bare Q. That last requirement is what
keeps the operator off edges that are *already* comb: ``q2 <= q1 & en``
has the cell CDC-014 looks for in the path, so interposing another one
proves nothing, and a repeated reference (``q2 <= q1 & q1``) has no
single span to rewrite. A different-clock consumer is a crossing, not
the next link of this chain.

The site set is therefore exactly ``CHAIN_STAGE_INSERT``'s: the sibling
also filters on ``sensitivity_text``, but a stage with no recoverable
clock text can have no reader either (the reader's clock is matched
against it), so that filter is implied here rather than absent.

**Only the inverter variant is emitted.** xeno#14 also floats ``& 1'b1``
and ``| 1'b0``. They are deliberately not enumerated: CDC-014 fires on
*any* comb cell in the path, so the extra variants would multiply
near-identical mutants across every site without touching a new rule
outcome. The inverter is the canonical form and the most legible in a
diff; a variant axis can be added later if a downstream coverage report
ever shows the gate identity mattering.

**Type.** The net is built from the stage LHS's own declared data type
(:func:`._chain_helpers.declared_type`) with the storage keyword
swapped for ``wire``: ``logic [7:0]`` gives ``wire [7:0] <new> =
~<lhs>;``, ``logic signed [3:0]`` gives ``wire signed [3:0] ...``, and
a scalar ``logic`` gives a plain ``wire <new> = ~<lhs>;``. The original
emission spelled the width as ``wire [$bits(<lhs>)-1:0]``, which
preserves the *width* while erasing the *type* — an enum- or
struct-typed stage would be re-read through a plain packed vector and
the rewired reader could then fail elaboration. Since
``declared_type`` deliberately only vouches for packed
``logic``/``reg``/``bit``/``wire`` forms, a stage it declines
(typedef names, enums, structs, unpacked arrays, ``int``) is **not a
site** — which keeps this operator inside the Verible layer without
ever emitting a net it cannot vouch for.

**Naming.** ``fresh_identifier(sv, lhs, "xeno_comb")`` — ``<lhs>_xeno_comb_<n>``
for the lowest free ``n``, with any existing ``_xeno_comb_<digits>``
suffix stripped off the base first, so repeated application on one
chain produces distinct, collision-free identifiers.

Issue: xeno#14. Discovery: rtl-buddy-cdc#230's coverage gap on CDC-014.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site
from rtl_buddy_xeno.operators import _chain_helpers as _chain

_INFIX = "xeno_comb"

# The storage keyword a copyable declared type opens with (see
# `_chain_helpers.declared_type`), swapped for `wire` so the interposed
# object is a continuously-assigned net rather than a variable.
_NET_KEYWORD_RE = re.compile(r"^(logic|reg|bit|wire)")


def _find_sites(sv: str) -> list[tuple[_chain.SyncStage, _chain.ReaderRef]]:
    """Every sync stage that can carry an interposed comb net.

    Source order (stage position). Two requirements on top of "is a
    recognised stage": the LHS's declared type is copyable, because the
    net is built from it (see the module docstring's "Type"), and there
    is a same-clock, same-module, direct non-blocking reader whose
    right-hand side the net can be spliced onto. Unlike
    ``CHAIN_STAGE_INSERT`` there is no explicit ``sensitivity_text``
    filter — nothing clocked gets emitted here — but the reader search
    matches the reader's edge against the stage's, so a stage with no
    recoverable clock text has no reader anyway.
    """
    stages, _cst_root = _chain.find_sync_stages(sv)
    sites: list[tuple[_chain.SyncStage, _chain.ReaderRef]] = []
    for stage in stages:
        if stage.lhs_type is None:
            continue
        reader = _chain.find_downstream_reader(sv, stage)
        if reader is None:
            continue
        sites.append((stage, reader))
    return sites


def _apply(
    sv: str,
    stage: _chain.SyncStage,
    reader: _chain.ReaderRef,
    new_name: str,
) -> str:
    """Emit the mutant: declare the comb net, rewire the reader onto it.

    The net keeps the stage's declared shape with ``wire`` in place of
    its storage keyword — ``logic signed [3:0]`` becomes ``wire signed
    [3:0]``, a bare ``logic`` becomes a bare ``wire``.
    ``stage.lhs_type`` is never ``None`` here (:func:`_find_sites`
    filters those stages out) and always starts with one of the four
    keywords :data:`_NET_KEYWORD_RE` matches, because that is exactly
    what ``declared_type`` vouches for.

    Two byte-splices on the parent source, applied from the highest
    offset downwards so the lower one's offsets stay valid — and which
    of the two is higher depends on whether the reader sits after the
    stage (the usual chain order) or before it (legal, and exercised by
    the tests), so both orders are handled explicitly.
    """
    net_type = _NET_KEYWORD_RE.sub("wire", stage.lhs_type or "wire", count=1)
    lines = [f"{net_type} {new_name} = ~{stage.lhs_name};"]
    if reader.start >= stage.block_end:
        mutated = _chain.replace_span(sv, reader.start, reader.end, new_name)
        return _chain.insert_after_block(
            mutated, stage.block_start, stage.block_end, lines
        )
    mutated = _chain.insert_after_block(sv, stage.block_start, stage.block_end, lines)
    return _chain.replace_span(mutated, reader.start, reader.end, new_name)


def _predict(lhs_name: str, new_name: str, line: int) -> Prediction:
    """Conservative prediction — no positive CDC-rule claim.

    Interposing a comb cell on a flop-to-flop wire is precisely the
    shape CDC-014 (combinational logic between sync-chain stages) is
    written for, but the Yosys-side rule pack also requires the two
    stages to register the *same data path* — i.e. that the pair really
    is a synchroniser chain rather than two unrelated flops that happen
    to be wired together. The Verible CST recogniser sees one
    ``always_ff`` and its first non-blocking reader; it never traces the
    data path back to a crossing, so the operator cannot verify that
    precondition and makes no positive claim: ``cdc_rules_added`` stays
    empty and the candidate rule is recorded in the rationale instead.
    The downstream coverage report observes what actually fires.

    ``perturbs_signals`` carries both names (xeno#14): the stage's Q now
    reaches its reader inverted, and the new net is a fresh observable
    in its own right.
    """
    return Prediction(
        rationale=(
            f"inserted an inverter `{new_name}` between the sync-chain stage "
            f"driving `{lhs_name}` at line {line} and its downstream reader; "
            "the reader now goes through a comb cell rather than a direct "
            "flop-to-flop wire. CDC-014 (combinational logic between sync "
            "chain stages) fires when this lands on a structurally "
            "well-formed sync chain — the Yosys-side rule also requires the "
            "two stages to register the same data path, which the Verible "
            "CST view cannot verify locally, so cdc_rules_added stays empty "
            "and the downstream coverage report observes the actual rule fire"
        ),
        perturbs_signals=frozenset({lhs_name, new_name}),
        perturbs_liveness=False,
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_sites(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        stage, reader = sites[idx]
        new_name = _chain.fresh_identifier(sv, stage.lhs_name, _INFIX)
        line, _ = _chain.byte_to_line_col(sv, stage.block_start)
        yield Mutant(
            sv=_apply(sv, stage, reader, new_name),
            diff_summary=(
                f"line {line}: insert inverter `{new_name}` between "
                f"`{stage.lhs_name}` and its reader"
            ),
            seed=stage.block_start,
            prediction=_predict(stage.lhs_name, new_name, line),
            kind=MutationKind.COMB_BETWEEN_STAGES,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for stage, _reader in _find_sites(sv):
        line, column = _chain.byte_to_line_col(sv, stage.block_start)
        new_name = _chain.fresh_identifier(sv, stage.lhs_name, _INFIX)
        yield Site(
            kind=MutationKind.COMB_BETWEEN_STAGES,
            line=line,
            column=column,
            snippet=f"sync stage `{stage.lhs_name}` -> comb net `{new_name}`",
            prediction=_predict(stage.lhs_name, new_name, line),
        )


operator = _mutants
candidates = _candidates
