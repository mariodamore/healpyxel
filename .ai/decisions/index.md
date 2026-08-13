# Decision Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| ADR-001 | healpy over cdshealpix for HEALPix operations | Active | 2026-05-22 |
| ADR-002 | Custom robust stats (mad, robust_std) over scipy.stats | Active | 2026-05-22 |
| ADR-003 | nbdev literate programming as development discipline | Superseded by ADR-007 | 2026-05-22 |
| ADR-004 | float64-only policy for precision consistency | Active | 2026-05-22 |
| ADR-005 | 4-phase pipeline architecture (Sidecar, Aggregate, Accumulator, Finalize) | Active | 2026-05-22 |
| ADR-006 | .ai/ folder as single source of truth for project context | Active | 2026-05-22 |
| ADR-007 | Migrate from nbdev to pure Python + Quarto | Active | 2026-07-10 |
| ADR-008 | antimeridian.fix_polygon must run before bounds pre-filter | Active | 2026-07-10 |
| ADR-009 | CLI segregation — pure submodules, single gateway in cli.py | Active | 2026-07-10 |
| ADR-010 | Abandon hierarchical HEALPix traversal, retain STRtree + shapely.prepare() | Superseded by ADR-013 | 2026-07-12 |
| ADR-011 | Exclude WIP notebooks from published docs | Active | 2026-07-13 |
| ADR-012 | Global sidebar/navbar for Quarto docs | Active | 2026-07-13 |
| ADR-013 | Pluggable body geometry backend (Sphere/Ellipsoid/DSK) | Active | 2026-07-13 |
| ADR-014 | TDigest for streaming quantile computation | Active | 2026-07-15 |
| ADR-015 | Multi-resolution sidecar via NEST bit-shift aggregation | Active | 2026-08-11 |
| ADR-016 | Pipeline wrapper for healpyxel 3-phase workflow | Active | 2026-08-11 |
| ADR-017 | Separate inspection CLI (`healpyxel_inspect`) from aggregation | Active | 2026-08-12 |
| ADR-018 | WKB fallback + --correct-geometry for broken spatial partition metadata | Active | 2026-08-13 |

## Notes

- This index starts at ADR-001 as part of the documentation structure migration (2026-05-22)
- Existing architectural decisions from `PROJECT_PLAN.md` and `IMPLEMENTATION_PLAN.md` are recorded here for continuity
- New decisions must be documented with individual ADR files in this directory
