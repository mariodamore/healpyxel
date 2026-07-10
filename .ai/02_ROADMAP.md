# Roadmap

## Current Phase: Core Pipeline Implementation

**Goal**: Complete the 4-phase HEALPix spatial aggregation pipeline with streaming support

### In scope

- **Sidecar** (01_sidecar.ipynb): ✅ Map geometries to HEALPix cells with fuzzy/multi-NSIDE support
- **Aggregate** (02_aggregate.ipynb): ✅ Classical batch processing with robust statistics
- **Accumulator** (03_accumulator.ipynb): ✅ Streaming variant with Welford + TDigest
- **Finalize** (04_finalize.ipynb): ✅ Upsample & export with densification
- **CLI** (05_cli.ipynb): ✅ Command-line interface for all phases
- **Visualization** (06_visualization.ipynb): ✅ Map rendering and post-processing
- **Geospatial** (07_geospatial.ipynb): ✅ Geometry utilities and antimeridian handling

### Out of scope

- **cdshealpix** — healpy is the approved library; no alternatives
- **scipy.stats** — custom robust stats maintain consistency
- **Python loops over datasets** — vectorization is required
- **Speculative optimizations** — only profile-driven improvements

## Upcoming Phases

- **Phase: Scale Testing** — Validate performance at 50M observation scale
- **Phase: FITS Export** — Enhanced FITS format support for planetary science archives
- **Phase: Real-time Streaming** — Production-grade streaming pipeline integration

## Completed Phases

- **Phase: Foundation** — Core utilities, HEALPix helpers, robust statistics (mad, robust_std)
- **Phase: Pipeline Core** — All 4 phases implemented with nbdev
- **Phase: CLI** — Command-line interface with rich output
- **Phase: Visualization** — Map rendering with multiple projections

## Documentation Structure

This project uses the `.ai/` folder as single source of truth:

| File | Purpose |
|------|---------|
| `00_CONSTRAINTS.md` | Hard rules — forbidden patterns, required tech |
| `00_PHILOSOPHY.md` | Design principles, architectural style, rationale |
| `02_ROADMAP.md` | Current phase, scope boundaries |
| `03_CURRENT_STATUS.md` | Active state only: NOW / NEXT / KNOWN_ISSUES |
| `decisions/index.md` | One-liner per ADR + current status |
| `IMPLEMENTATION_PLAN.md` | Tactical status — concrete implementation progress |
| `PROJECT_PLAN.md` | Legacy — content merged into this file and others |

_Last updated: 2026-05-22_
