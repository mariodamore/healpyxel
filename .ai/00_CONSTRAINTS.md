# Constraints

Hard rules. These are non-negotiable unless an ADR with a Waiver field explicitly overrides one.

## Required Technologies

- **healpy** (NEST ordering) — Reason: HEALPix as single coordinate system; consistency across notebooks
- **pathlib** — Reason: Modern Python path handling; consistent with project style
- **antimeridian** — Reason: Required for geometries crossing longitude 180/-180
- **Percent format (.py:percent)** — Reason: Bridge format for Quarto docs and Jupyter notebooks
- **Quarto** — Reason: Documentation rendering for .py:percent examples (replaces nbdev)
- **quartodoc** — Reason: API reference generation within Quarto (replaces nbdev showdoc)
- **jupytext** — Reason: .ipynb ↔ .py:percent conversion for tutorial notebooks
- **dask-geopandas** — Reason: Lazy loading for scale; context collapse is the enemy

## Forbidden Technologies

- **cdshealpix** — Reason: healpy is the approved HEALPix library; avoid fragmentation
- **scipy.stats** — Reason: Custom robust stats (mad(), robust_std()) ensure consistency across 10+ notebooks
- **Python loops over large datasets** — Reason: Vectorization required; if it doesn't run in <100ms on 1M rows, it's technical debt

## Forbidden Patterns

- **Speculative optimization** — Reason: Don't suggest "maybe use Numba" without profiling; optimize for predictability first
- **Error handling for impossible cases** — Reason: Trust internal code and framework guarantees; validate only at system boundaries

## Percent Format Requirements

All tutorial notebooks must use the `.py:percent` format (ADR-007):

- `# %%` cell markers for code cells
- `# %% [markdown]` for markdown cells
- Works with Jupyter, VS Code, and Quarto `jupyter` engine
- Source files in `notebooks/*.py` (not `nbs/*.ipynb`)
- No notebook-specific tooling required

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

_Last updated: 2026-07-14_
