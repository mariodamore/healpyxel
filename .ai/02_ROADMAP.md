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

- **Phase: Exhaustive FOV Coverage (ADR-019)** — Opt-in `exhaustive=True` mode for `process_partition` using `healpy.query_disc` + exact `shapely.intersects()` filtering. Guarantees complete coverage for large FOVs. Fixed latent bug in `get_healpix_cell_geometry()`. All 456 tests pass.
- **Phase: Scale Testing** — Validate accumulator performance at 50M observation scale
- **Phase: PSF Redesign (pluggable/unit-sphere)** — Replace the legacy centroid/lat-lon PSF with a pluggable model: sub-pixel integration via `nside_high`, unit-sphere angular-separation `PSF(θ)`, boresight source, sparse stencil, two accuracy modes, tangent/spherical backends. See `IDEAS_HEALPYXEL.md` §4/§5/§6/§9.
- **Phase: Downstream PSF weight application** — Wire the sidecar `weight` column into aggregation (currently ignored).
- **Phase: Cloud-native exports (COG / GeoZarr)** — Regular GeoTIFF exists; add COG and native Zarr/GeoZarr (incl. multi-level Zarr) exporters. See `IDEAS_HEALPYXEL.md` §11/§12.
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
- **Phase: Multi-resolution sidecar (ADR-015)** — NEST bit-shift aggregation; compute `nside_max` once, derive lower nsides (verified 2026-08-15)

## Documentation Structure

This project uses the `.ai/` folder as single source of truth:

| File | Purpose |
|------|---------|
| `00_CONSTRAINTS.md` | Hard rules — forbidden patterns, required tech |
| `00_PHILOSOPHY.md` | Design principles, architectural style, rationale |
| `02_ROADMAP.md` | Current phase, scope boundaries |
| `03_CURRENT_STATUS.md` | Active state only: NOW / NEXT / KNOWN_ISSUES |
| `decisions/index.md` | One-liner per ADR + current status |

_Last updated: 2026-08-16_
