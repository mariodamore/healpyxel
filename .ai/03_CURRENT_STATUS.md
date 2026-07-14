# Current Status

## NOW
- [x] **Fuzzy mode coordinate bounds bug** — `_is_valid_latitude()` was using `lon_convention`-specific bounds to validate input geometry coords, rejecting data in different conventions. Fixed to use ±360 for lon regardless of convention.
- [x] **Replace fuzzy mode with spherical query_disc** — ADR-013: replaced STRtree + dense sampling with `healpy.query_disc` + spherical point-in-polygon test. No antimeridian, no shapely cell polygons in hot path. ADR-010 superseded by ADR-013.

## NEXT
- [ ] **PSF Phase A implementation** — Two-pass workflow, Angular PSF evaluation (unit vectors), Configurable subgrid NSIDE, Generalized parent-child mapping, Tangent-plane + spherical backends
- [ ] **Scale testing** — Validate performance at 50M observation scale

## KNOWN_ISSUES

- **Legacy files** — `PROJECT_PLAN.md` should be archived after migration complete
- **WIP notebooks** — 83_example_accumulation*.py excluded from published docs until self-contained

### Bugs

- None currently documented

### Technical Debt

- **Legacy docs** — `PROJECT_PLAN.md` and `IMPLEMENTATION_PLAN.md` should be archived
- **Float-to-int conversions** — Several locations in geospatial.py use `float(x)` where `x` is already float; minor style cleanup needed

_Last updated: 2026-07-13_
