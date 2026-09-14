"""``CHAIN_STAGE_INSERT`` — deepen a sync chain by one same-clock flop.

The dual of ``SYNC_CHAIN_DEPTH_PERTURB`` (:mod:`._sync_chain_depth_perturb`),
and the operator that closes that module's old "insertion mode is
deferred" note. Where the perturb operator *removes* a chain stage,
this one *synthesises* one: a fresh register, a same-clock ``always_ff``
driving it from the site stage's Q, and a rewrite of the downstream
reader so it consumes the new register instead.

.. code-block:: systemverilog

    // parent
    always_ff @(posedge dst_clk) sync_meta <= src_q;
    always_ff @(posedge dst_clk) sync_q    <= sync_meta;

    // mutant (site = stage `sync_meta`)
    always_ff @(posedge dst_clk) sync_meta <= src_q;
    logic [7:0] sync_meta_xeno_stage_1;
    always_ff @(posedge dst_clk) sync_meta_xeno_stage_1 <= sync_meta;
    always_ff @(posedge dst_clk) sync_q    <= sync_meta_xeno_stage_1;

**Parser layer: Verible CST only** (see the no-straddle rule in
:mod:`rtl_buddy_xeno.operators`). Stage discovery and the forward walk
to the reader come from :mod:`._chain_helpers`; emission is a pair of
byte-splices on the original source string. No pyslang.

**Why the reader must exist.** A stage with no downstream non-blocking
reader — a chain tail feeding a continuous ``assign``, an output port,
or a lone flop nothing consumes — yields no site. Inserting a flop
there would leave the new register dangling: nothing would read it, the
chain would not actually get deeper, and the mutant would differ from
its parent only by dead logic. So the site set is exactly "stages that
have somewhere to insert *into*".

**What counts as a reader** is :func:`._chain_helpers.find_downstream_reader`'s
call: an ``always_ff`` *in the stage's own module*, clocked on the same
edge, whose non-blocking right-hand side is the stage's bare Q. A
different-clock consumer is a crossing rather than the next link of
this chain, and a reader that already computes on the Q
(``q2 <= q1 & en``) was never a clean synchroniser chain — neither is a
site.

**Naming.** The new register is ``fresh_identifier(sv, lhs,
"xeno_stage")`` — ``<lhs>_xeno_stage_<n>`` for the lowest free ``n``,
with any existing ``_xeno_stage_<digits>`` suffix stripped off the base
first. Applying the operator to its own output therefore walks
``q_xeno_stage_1`` → ``q_xeno_stage_2`` rather than compounding the
suffix, which is what makes repeated application on one chain (the way
to reach CDC-018's ≥4-stage cascade from a 2-deep parent) produce
distinct, collision-free identifiers.

**Type.** The declaration copies the stage LHS's own declared data
type verbatim — ``logic [7:0] sync_meta_xeno_stage_1;`` for a
``logic [7:0]`` stage, ``logic signed [3:0] ...`` for a signed one,
bare ``logic ...`` for a scalar. The width-only spelling
``logic [$bits(<lhs>)-1:0] <new>;`` was the original emission and is
wrong: ``$bits`` preserves the *width* while erasing the *type*, so an
enum- or struct-typed stage would be re-declared as a plain packed
vector and the rewired reader could then fail elaboration. The lookup
is :func:`._chain_helpers.declared_type`, which is deliberately narrow
— packed ``logic``/``reg``/``bit``/``wire`` vectors and scalars, with
an optional signedness keyword, and nothing else. A stage whose type it
declines (typedef names, enums, structs, unpacked arrays, ``int``) is
**not a site**, which keeps the operator inside the Verible layer
without ever emitting a declaration it cannot vouch for.

Issue: xeno#13. Discovery: rtl-buddy-cdc#230's coverage gap on CDC-018.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site
from rtl_buddy_xeno.operators import _chain_helpers as _chain

_INFIX = "xeno_stage"


def _find_sites(sv: str) -> list[tuple[_chain.SyncStage, _chain.ReaderRef]]:
    """Every sync stage that can carry a synthesised sibling flop.

    Source order (stage position). Three requirements on top of "is a
    recognised stage":

    - the clock sensitivity text was recovered verbatim — this operator
      has to *emit* that clock into a synthesised block (the
      deletion-side operator has no such need, which is why the shared
      recogniser keeps the stage);
    - the LHS's declared type is copyable, because the new register is
      declared with it (see the module docstring's "Type");
    - there is a same-clock, same-module, direct non-blocking reader to
      insert *into*.
    """
    stages, _cst_root = _chain.find_sync_stages(sv)
    sites: list[tuple[_chain.SyncStage, _chain.ReaderRef]] = []
    for stage in stages:
        if not stage.clock_edge_text or stage.lhs_type is None:
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
    """Emit the mutant: declare + drive the new flop, rewire the reader.

    The declaration copies ``stage.lhs_type`` (never ``None`` here —
    :func:`_find_sites` filters those stages out). Two byte-splices on
    the parent source. They are applied from the
    highest offset downwards so the lower one's offsets stay valid —
    and which of the two is higher depends on whether the reader sits
    after the stage (the usual chain order) or before it (legal, and
    exercised by the tests), so both orders are handled explicitly.
    """
    declaration = f"{stage.lhs_type} {new_name};"
    flop = f"always_ff @({stage.clock_edge_text}) {new_name} <= {stage.lhs_name};"
    lines = [declaration, flop]
    if reader.start >= stage.block_end:
        mutated = _chain.replace_span(sv, reader.start, reader.end, new_name)
        return _chain.insert_after_block(
            mutated, stage.block_start, stage.block_end, lines
        )
    mutated = _chain.insert_after_block(sv, stage.block_start, stage.block_end, lines)
    return _chain.replace_span(mutated, reader.start, reader.end, new_name)


def _predict(lhs_name: str, new_name: str, line: int) -> Prediction:
    """Conservative prediction — no positive CDC-rule claim.

    Deepening a chain by one is the mutation CDC-018 (cascaded
    synchroniser, ≥4-stage chain) is meant to catch, but only when the
    chain was *already* at depth ≥3 before the insertion. The Verible
    CST recogniser sees one ``always_ff`` and its immediate reader — it
    never counts the upstream stages — so the operator cannot verify
    that precondition and makes no positive claim:
    ``cdc_rules_added`` stays empty and the candidate rule is recorded
    in the rationale instead. The downstream coverage report observes
    what actually fires. (A future revision that adds a CST chain-walker
    counting upstream stages could claim CDC-018 when count + 1 ≥ 4;
    that is an optimisation on top, not a prerequisite — see xeno#13.)

    ``perturbs_signals`` stays a positive prediction: the stage's Q now
    reaches its reader one clock later, and that much *is* verifiable
    from the CST alone.
    """
    return Prediction(
        rationale=(
            f"inserted a same-clock flop `{new_name}` between the sync-chain "
            f"stage driving `{lhs_name}` at line {line} and its downstream "
            "reader, deepening the chain by one. CDC-018 (cascaded "
            "synchroniser, chain of ≥4 stages) fires when this lands on a "
            "chain already at depth ≥3 — the Verible CST recogniser sees "
            "one stage plus its reader and cannot count the upstream "
            "stages, so the operator emits without verifying chain depth "
            "and cdc_rules_added stays empty; the downstream coverage "
            "report observes the actual rule fires"
        ),
        perturbs_signals=frozenset({lhs_name}),
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
                f"line {line}: insert sync stage `{new_name}` after `{stage.lhs_name}`"
            ),
            seed=stage.block_start,
            prediction=_predict(stage.lhs_name, new_name, line),
            kind=MutationKind.CHAIN_STAGE_INSERT,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for stage, _reader in _find_sites(sv):
        line, column = _chain.byte_to_line_col(sv, stage.block_start)
        new_name = _chain.fresh_identifier(sv, stage.lhs_name, _INFIX)
        yield Site(
            kind=MutationKind.CHAIN_STAGE_INSERT,
            line=line,
            column=column,
            snippet=f"sync stage `{stage.lhs_name}` -> new stage `{new_name}`",
            prediction=_predict(stage.lhs_name, new_name, line),
        )


operator = _mutants
candidates = _candidates
