# rtl-buddy-xeno

SystemVerilog AST-mutation corpus generator. Consumes a hand-authored
template, applies CDC-aware structural mutations, and emits mutants with
an `expected_change` annotation declaring the analyzer finding-set delta
each mutant should produce.

Two consumers in mind:

- **rtl-buddy-cdc** fuzzer — oracle is cross-frontend agreement on the
  mutant's finding set (rtl-buddy-cdc#221, Stage 3 Layer B).
- **rtl-buddy-fpv** mutation-testing flow (future) — oracle is property
  survival.

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
    print(mutant.expected_change.rules_added)
    # mutant.sv is the mutated source; feed it to the analyzer.
```

Each `Mutant` carries:

- `sv` — the mutated SystemVerilog source.
- `diff_summary` — short human-readable description of the mutation.
- `seed` — provenance token (operator-internal; reproduces this specific
  mutant given the same parent + operator).
- `expected_change` — predicted finding-set delta vs. the parent
  template (`rules_added`, `rules_removed`, `rationale`).
- `kind` — which `MutationKind` produced the mutant.

A mutant whose observed analyzer delta disagrees with `expected_change`
is actionable: either a real gap in the rule pack (file an issue against
rtl-buddy-cdc) or a buggy mutation operator (fix here). That diagnostic
loop is the point.

## Mutation kinds

| Kind                          | Status        | Exercises    |
| ----------------------------- | ------------- | ------------ |
| `CLOCK_POLARITY_SWAP`         | implemented   | CDC-006      |
| `SYNC_CHAIN_DEPTH_PERTURB`    | stub (#221)   | CDC-002, CDC-018 |
| `BIT_EXTRACT_PERMUTE`         | stub (#221)   | CDC-019, CDC-020 |
| `ATTRIBUTE_TOGGLE`            | stub (#221)   | user-attribute paths |
| `RESET_POLARITY_FLIP`         | stub (#221)   | RDC-007      |

Stubbed kinds raise `NotImplementedError` with a pointer back to the
design issue rather than silently skipping. They are wired into the
`MutationKind` enum so consumers compile against the final surface even
while the operator pool grows.

## Parser choice

AST manipulation needs an SV parser. Three options were on the table:

| Option            | Pros                                  | Cons                                                  |
| ----------------- | ------------------------------------- | ----------------------------------------------------- |
| **pyslang**       | full SystemVerilog; semantic accuracy | heavy native dep; AST→source needs source-location bookkeeping |
| **text / regex**  | zero runtime deps; trivial to bootstrap | brittle on comments / macros / multi-line `always` blocks |
| **Lark grammar**  | declarative; pure-Python              | partial SV grammar in practice; reinvents pyslang's job |

**Chosen: text-based for v0, pyslang as the declared graduation target.**

Rationale:

- `clock_polarity_swap` is a token rewrite (`posedge` ↔ `negedge`). It
  doesn't need an AST. Forcing pyslang on it would be over-engineering
  for the simplest operator we want to ship.
- The structural operators (`SYNC_CHAIN_DEPTH_PERTURB`,
  `BIT_EXTRACT_PERMUTE`) *do* need an AST — there is no way to identify
  a sync chain or permute bit slices without semantic structure. Those
  graduate to a pyslang-backed rewrite under the `[slang]` extra when
  they land.
- Pyslang is already in `rtl-buddy-cdc`'s dependency closure (`[slang]`
  extra at `pyslang>=10,<11`); the runtime cost is amortised across the
  two consumers.
- The `expected_change` annotation is the consumer-facing contract.
  Parser choice is implementation detail and may evolve per operator
  without disturbing the API.

When a mutation operator requires structural reasoning, it should be
added to the planned `rtl_buddy_xeno.slang` submodule (gated behind a
lazy `import pyslang`), not retrofitted onto the regex path. Mixing the
two layers behind a single operator name is the failure mode to avoid.

## Development

```bash
uv sync --group dev
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest -q
```

Python 3.13, src-layout, frozen dataclasses, pure functions in the
operator path. Conventions mirror `rtl-buddy-cdc`.

## Design references

- [rtl-buddy-cdc#221](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/221)
  — Stage 3. Layer B defines the mutator surface this package implements.
- [rtl-buddy-cdc#222](https://github.com/rtl-buddy/rtl-buddy-cdc/issues/222)
  — Stage 4. Grammar-based novel-topology generation; a future consumer
  that flows generated `.sv` through the same `Mutator.from_sv(...)`
  pipeline.

## License

BSD 3-Clause. See `LICENSE`.
