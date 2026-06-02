# rtl-buddy-xeno

SystemVerilog AST-mutation corpus generator. Consumes a hand-authored
template, applies CDC-aware structural mutations, and emits mutants
with a `Prediction` annotation declaring how downstream oracles
should react.

Two consumers:

- **rtl-buddy-cdc** fuzzer — oracle is cross-frontend agreement on the
  mutant's CDC-rule finding set ([cdc#221](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/221), Stage 3 Layer B).
- **`rb mut`** mutation-testing harness in rtl_buddy — oracle is FPV /
  regression property survival ([rtl_buddy#206](https://github.com/rtl-buddy/rtl_buddy/issues/206)).

The name is an *Alien* xenomorph reference crossed with the CDC pun —
"xeno" = crossing into a foreign clock domain.

## Quickstart

```python
from rtl_buddy_xeno import Mutator, MutationKind

mutator = Mutator.from_sv("path/to/template.sv")
for mutant in mutator.generate(
    kinds=[MutationKind.CLOCK_POLARITY_SWAP],
    count=10,
    seed=0,
):
    print(mutant.diff_summary)
    print(sorted(mutant.prediction.cdc_rules_added))
    # mutant.sv is the mutated source; feed it to the analyzer.
```

Each `Mutant` carries:

- `sv` — the mutated SystemVerilog source.
- `diff_summary` — short human-readable description.
- `seed` — provenance token (operator-internal; reproduces this
  specific mutant given the same parent + operator).
- `prediction` — a `Prediction` dataclass with both CDC-oracle fields
  (`cdc_rules_added`, `cdc_rules_removed`) and FPV-oracle fields
  (`perturbs_signals`, `perturbs_liveness`); empty defaults mean "no
  prediction in this dimension," distinct from a negative prediction.
- `kind` — which `MutationKind` produced the mutant.

A mutant whose observed delta disagrees with `prediction` is
actionable: either a real gap in the rule pack (file an issue against
rtl-buddy-cdc) or a buggy mutation operator (fix here). That
diagnostic loop is the point.

Use `mutator.candidates(kinds=...)` to enumerate `Site` objects
without actually producing mutants — the budget-estimation primitive
consumed by `rb mut list` (rtl_buddy#206). Use the `schedule=` kwarg
on `generate` to choose between `Schedule.SEQUENTIAL` (default) and
`Schedule.ROUND_ROBIN`; round-robin is the rb-mut idiom for budgeted
runs across multiple kinds.

## Mutation kinds

Eleven operators covering both consumer pools, all implemented. See
umbrella [xeno#2](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/2)
for the full table including MCY-expressibility.

| Kind                            | Status      | Used by       | Parser layer               | Exercises                                                  |
| ------------------------------- | ----------- | ------------- | -------------------------- | ---------------------------------------------------------- |
| `CLOCK_POLARITY_SWAP`           | implemented | cdc#221       | regex (no extras)          | CDC-016 (chain-stage gated; standalone flops predict nothing) |
| `ATTRIBUTE_TOGGLE`              | implemented | cdc#221       | regex (no extras)          | CDC-002 / -003 / -010 / -019, RDC-001 / -007 (per attribute) |
| `ASSIGN_DROP`                   | implemented | rtl_buddy#206 | Verible + slang            | property survival on the LHS                               |
| `SYNC_CHAIN_DEPTH_PERTURB`      | implemented | cdc#221       | Verible                    | CDC-002 / -018 (rationale only — no positive claim)        |
| `BIT_EXTRACT_PERMUTE`           | implemented | cdc#221       | Verible                    | CDC-019 / -020 (rationale only — no positive claim)        |
| `RESET_POLARITY_FLIP`           | implemented | cdc#221       | Verible (+ optional slang) | RDC-007 (rationale only — no positive claim)               |
| `ARITH_FLIP` (`+ ↔ -`, `* ↔ /`) | implemented | rtl_buddy#206 | Verible (+ optional slang) | property survival                                          |
| `BIT_OP_FLIP` (`& ↔ \|`, `~` ±) | implemented | rtl_buddy#206 | Verible (+ optional slang) | property survival                                          |
| `COND_NEGATE`                   | implemented | rtl_buddy#206 | Verible                    | property survival                                          |
| `COND_CONST`                    | implemented | rtl_buddy#206 | Verible                    | property survival                                          |
| `PORT_BINDING_SWAP`             | implemented | rtl_buddy#206 | Verible                    | property survival + maybe CDC                              |

All eleven kinds are implemented — `IMPLEMENTED_KINDS == frozenset(MutationKind)`,
no operator raises `NotImplementedError`. The CDC rule-id predictions
are deliberately conservative: an operator only populates
`cdc_rules_added` when it can structurally justify the claim from its
parser view (`CLOCK_POLARITY_SWAP` requires a ≥2-stage chain heuristic;
`ATTRIBUTE_TOGGLE` keys off a known attribute name). The context-blind
operators (`SYNC_CHAIN_DEPTH_PERTURB`, `BIT_EXTRACT_PERMUTE`,
`RESET_POLARITY_FLIP`) leave `cdc_rules_added` empty and record the
candidate rule in the `rationale` instead, so the downstream coverage
report measures the actual fire rather than trusting an over-confident
guess. The rule-ids above mirror `rtl-buddy-cdc`'s rule pack by
convention — they are string literals, with no code dependency on
rtl-buddy-cdc.

## Parser layering

Decision ratified in [xeno#4](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/4)
(supersedes the v0.0.1 text/slang two-layer sketch). Three orthogonal
jobs, each operator picks which two or three it needs:

1. **Find candidate sites** — locate operator tokens in legal SV
   positions (not inside strings, comments, `` `define `` bodies).
   Source-faithful CST. **Verible**, via `rtl-buddy-view`'s public
   helpers (`rtl_buddy_view.cst_cache` etc., promoted in
   [view#109](https://github.com/rtl-buddy/rtl-buddy-view/issues/109)).
2. **Semantic gating** — "is this `+` inside a `parameter` expression,
   skip"; "is this `always_ff` a synchroniser chain head."
   Elaboration. **pyslang** under the `[slang]` extra.
3. **Emit the mutated source** — byte-splice on the original source
   string. Pure string operation; no parser. Same pattern
   `CLOCK_POLARITY_SWAP` already uses.

Byte offsets are the lingua franca: both Verible CST nodes and
pyslang AST nodes carry source ranges referencing the same bytes, so
an operator that uses pyslang for semantic identification still emits
the splice as a byte-range on the original source string. No
round-trip through either AST for emission.

**No-straddle rule**: a single operator never mixes layers within
itself. It's exactly one of `regex-only`, `Verible-only`, or
`Verible+slang`. No half-and-half within one operator. The rule is
enforceable by reading the top-level structure of
`src/rtl_buddy_xeno/operators/` — each module declares its parser
choice via the imports it carries.

## Install

> **Note**: `rtl-buddy-xeno` is preparing for its first PyPI release.
> Until that lands (see [`RELEASING.md`](RELEASING.md)), install from
> source: `pip install git+https://github.com/rtl-buddy/rtl-buddy-xeno`.

```bash
# Bare install — only the regex-based operators (CLOCK_POLARITY_SWAP
# and ATTRIBUTE_TOGGLE) run without extras.
pip install rtl-buddy-xeno

# With Verible CST support (required by every structural operator
# except CLOCK_POLARITY_SWAP and ATTRIBUTE_TOGGLE). Pulls in
# `rtl-buddy-view>=0.2.0`. NB: until rtl-buddy-view publishes to PyPI
# this extra is only installable via git source — for local
# development use `uv sync --extra verible` against this repo.
pip install "rtl-buddy-xeno[verible]"

# With pyslang elaboration (used by the four semantically-gated
# operators: ASSIGN_DROP, RESET_POLARITY_FLIP, plus the optional
# semantic-gating path on ARITH_FLIP / BIT_OP_FLIP).
pip install "rtl-buddy-xeno[slang]"

# Most operators need both.
pip install "rtl-buddy-xeno[verible,slang]"
```

The `[verible]` and `[slang]` extras are independent; each operator's
module imports lazily so the no-extras install path keeps working
for `CLOCK_POLARITY_SWAP` and `ATTRIBUTE_TOGGLE`. Operators that
require an absent extra raise `ImportError` with a clear pointer at
which extra to install.

## Library boundary

xeno never reads `root_config.yaml` itself — that's the orchestrator's
job (rtl_buddy). Callers inject the Verible CST cache directory as a
function argument; xeno's `cst.py` facade accepts a `cache_dir`
parameter and passes it through to view's `cst_cache.get_or_compute`.
This keeps xeno usable as a standalone library (including in unit
tests) without any project-config dependency.

## Development

```bash
uv sync                       # bare install
uv sync --extra verible       # + Verible CST
uv sync --extra slang         # + pyslang
uv sync --all-extras          # full

uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
```

Python ≥3.11, src-layout, frozen dataclasses, pure functions in the
operator path. Conventions mirror `rtl-buddy-cdc`.

## Design references

- [xeno#2](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/2) — umbrella tracking source-level SV mutation across consumers; includes the operator pool / MCY-expressibility table and the prior-art positioning.
- [xeno#3](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/3) — API design (Prediction shape, full MutationKind enum, `candidates` method, `Schedule` enum).
- [xeno#4](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/4) — parser layering (Verible CST + pyslang + byte-splice; the three-job decision).
- [view#109](https://github.com/rtl-buddy/rtl-buddy-view/issues/109) — upstream view-side helper promotion that unblocks xeno's `[verible]` extra.
- [cdc#221](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/221) — Stage 3 Layer B; the originating consumer.
- [cdc#222](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/222) — Stage 4; future downstream consumer (grammar-generated topologies flow through the same `Mutator.from_sv` surface).
- [rtl_buddy#206](https://github.com/rtl-buddy/rtl_buddy/issues/206) — `rb mut` harness; second consumer.

## License

BSD 3-Clause. See `LICENSE`.
