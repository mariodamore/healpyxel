# Constraints

Hard rules. These are non-negotiable unless an ADR with a Waiver field explicitly overrides one.

## Required Technologies

- **healpy** (NEST ordering) — Reason: HEALPix as single coordinate system; consistency across notebooks
- **pathlib** — Reason: Modern Python path handling; consistent with project style
- **antimeridian** — Reason: Required for geometries crossing longitude 180/-180
- **dask-geopandas** — Reason: Lazy loading for scale; context collapse is the enemy
- **nbdev** — Reason: Literate programming as debugging discipline; notebooks are source of truth

## Forbidden Technologies

- **cdshealpix** — Reason: healpy is the approved HEALPix library; avoid fragmentation
- **scipy.stats** — Reason: Custom robust stats (mad(), robust_std()) ensure consistency across 10+ notebooks
- **Python loops over large datasets** — Reason: Vectorization required; if it doesn't run in <100ms on 1M rows, it's technical debt

## Forbidden Patterns

- **Editing healpyxel/*.py directly** — Reason: Notebooks in nbs/ are the source of truth; Python files are auto-generated
- **Speculative optimization** — Reason: Don't suggest "maybe use Numba" without profiling; optimize for predictability first
- **Error handling for impossible cases** — Reason: Trust internal code and framework guarantees; validate only at system boundaries

## nbdev Directive Requirements

All notebook cells must use appropriate directives:

- `#| export` — Marks code for inclusion in Python module (required for all library code)
- `#| hide` — Hide from docs (for imports like `from nbdev.showdoc import *`)
- `#| eval: false` — Don't execute during docs builds (for CLI scripts)
- `#| output: false` — Suppress debug output

## Data Type Requirements

- **float64** — Reason: Precision consistency across batches; use only when performance constraints are specified
- **HEALPix NEST ordering** — Reason: Consistency with healpy; normalize longitudes to [0, 360)

## Performance Requirements

- **Vectorization demand** — Reject any Python loops over large datasets
- **100ms target** — If it doesn't run in <100ms on 1M rows, it's technical debt

## Documentation Requirements

- **Source citations** — Name every `.ai/` file consulted in responses
- **ADR comments in code** — Add `# ADR-NNN` comments when code implements architectural decisions
- **Session logging** — Dead ends are as important as progress; record both

_Last updated: 2026-05-22_
