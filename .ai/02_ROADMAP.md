# Roadmap

## Current Phase: Production-Grade Streaming & Accumulation

**Goal**: Stabilize and production-proof the streaming accumulation pipeline (Welford + TDigest) for mission-day incremental processing.

### In scope

- **Accumulator bugfixes (ADR-014)** — Fixed 4 production bugs:
  - `tdigest`/`tdigests` typo in `finalize.py` (percentiles always NaN)
  - TDigest merge iterator (`digest.C` is AccumulationTree, not list)
  - `_normalize_load_state_result` 3-tuple handling
  - Empty state KeyError in `finalize_statistics()`
- **Accumulator test coverage** — 58 new tests (test_accumulator.py, test_finalize.py) covering Welford, CellAccumulator, TDigest merge, save/load round-trip, pipeline integration
- **ADR-014** — TDigest as streaming QuantileReducer: design rationale, alternatives considered, ~1e-3 accuracy bound
- **Full test suite: 261 passed, 0 failures**
- **Streaming percentile accuracy** — TDigest `batch_update()` path active; documented ~1e-3 vs exact batch

### Out of scope

- **cdshealpix** — healpy is the approved library; no alternatives
- **scipy.stats** — custom robust stats maintain consistency
- **Python loops over datasets** — vectorization is required
- **Speculative optimizations** — only profile-driven improvements

## Upcoming Phases

- **Phase: Scale Testing** — Validate accumulator performance at 50M observation scale
- **Phase: PSF Implementation** — Angular PSF evaluation, configurable subgrid NSIDE, tangent-plane + spherical backends
- **Phase: FITS Export** — Enhanced FITS format support for planetary science archives

## Completed Phases

- **Phase: Foundation** — Core utilities, HEALPix helpers, robust statistics (mad, robust_std)
- **Phase: Pipeline Core** — All 4 pipeline phases (Sidecar, Aggregate, Accumulator, Finalize)
- **Phase: CLI** — Command-line interface with all entry points
- **Phase: Visualization** — Map rendering with multiple projections
- **Phase: Geospatial** — Geometry utilities and antimeridian handling (output layer)
- **Phase: Cache Management** — XDG-compliant HEALPix grid caching
- **Phase: nbdev→Quarto migration** — Full migration to pure Python + Quarto (ADR-007)
- **Phase: Geometry Backend** — Pluggable body geometry (Sphere/Ellipsoid/DSK) via ADR-013
- **Phase: Accumulator Stabilization** — Bug fixes, test suite, ADR-014, 261 tests green

## Documentation Structure

This project uses the `.ai/` folder as single source of truth:

| File | Purpose |
|------|---------|
| `00_CONSTRAINTS.md` | Hard rules — forbidden patterns, required tech |
| `00_PHILOSOPHY.md` | Design principles, architectural style, rationale |
| `02_ROADMAP.md` | Current phase, scope boundaries |
| `03_CURRENT_STATUS.md` | Active state only: NOW / NEXT / KNOWN_ISSUES |
| `decisions/index.md` | One-liner per ADR + current status |

_Last updated: 2026-07-15_
