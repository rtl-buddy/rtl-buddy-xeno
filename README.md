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

Eleven operators covering both consumer pools. See umbrella
[xeno#2](https://github.com/rtl-buddy/rtl-buddy-xeno/issues/2)
for the full table including MCY-expressibility.

| Kind                                | Status      | Used by         | Parser layer       | Exercises                       |
| ----------------------------------- | ----------- | --------------- | ------------------ | ------------------------------- |
| `CLOCK_POLARITY_SWAP`               | implemented | cdc#221         | regex (no extras)  | CDC-006                         |
| `ATTRIBUTE_TOGGLE`                  | implemented | cdc#221         | regex (no extras)  | CDC-002 / -003 / -008 / -019 / RDC-001 / -007 depending on attribute |
| `ASSIGN_DROP`                       | implemented | rtl_buddy#206   | Verible + slang    | property survival on the LHS    |
| `SYNC_CHAIN_DEPTH_PERTURB`          | stub (#221) | cdc#221         | Verible + slang    | CDC-002, CDC-018                |
| `BIT_EXTRACT_PERMUTE`               | stub (#221) | cdc#221         | Verible + slang    | CDC-019, CDC-020                |
| `RESET_POLARITY_FLIP`               | stub (#221) | cdc#221         | Verible + slang    | RDC-007                         |
| `ARITH_FLIP` (`+ ↔ -`, `* ↔ /`)     | stub (#206) | rtl_buddy#206   | Verible (+ optional slang) | property survival       |
| `BIT_OP_FLIP` (`& ↔ \|`, `~` ±)     | stub (#206) | rtl_buddy#206   | Verible (+ optional slang) | property survival       |
| `COND_NEGATE`                       | stub (#206) | rtl_buddy#206   | Verible            | property survival               |
| `COND_CONST`                        | stub (#206) | rtl_buddy#206   | Verible            | property survival               |
| `PORT_BINDING_SWAP`                 | stub (#206) | rtl_buddy#206   | Verible            | property survival + maybe CDC   |

Stubbed kinds raise `NotImplementedError` pointing at the design
issue rather than silently skipping. They are wired into the
`MutationKind` enum so consumers compile against the final surface
even while the operator pool grows.

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

```bash
# Bare install — only CLOCK_POLARITY_SWAP and ATTRIBUTE_TOGGLE work.
pip install rtl-buddy-xeno

# With Verible CST support (required by most structural operators).
# Pulls in `rtl-buddy-view>=0.2.0` from GitHub (not yet on PyPI).
pip install "rtl-buddy-xeno[verible]"

# With pyslang elaboration (required by the four semantically-gated
# structural operators).
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

Python 3.13, src-layout, frozen dataclasses, pure functions in the
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
