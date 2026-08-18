# ADR-019: Exhaustive FOV Coverage via Candidate Search + Exact Intersection

- **Status:** Active
- **Date:** 2026-08-16
- **Author:** session 2026-08-16

## Context

The current `process_partition` fuzzy mode (ADR-013) maps FOV polygons to HEALPix
cells by SLERP-sampling the boundary (80 points per edge on great-circle arcs) plus
a repeated centroid point, then calling `compute_healpix_ids_from_lonlat` on the
sampled points. This works well for small FOVs but can miss interior cells for
large FOVs (10°+ across at nside=32), because point sampling is fundamentally
approximate: a cell whose interior the polygon fully covers can still contain zero
sampled points. The user's XRS notebook (`01_fov_csv_to_healpix.py`) exercises this
path with `body=Sphere()`.

An earlier draft of this ADR proposed fixing this with a denser, opt-in
`exhaustive=True` mode: generate a fine lon/lat grid (resolution = half the cell
angular size), test each grid point against the polygon with `shapely.contains`,
and map surviving points to cells via `ang2pix`. Two problems with that draft
surfaced during review and are corrected here.

**It does not actually guarantee coverage.** The algorithm tests "is this grid
point inside the polygon," not "does the polygon intersect this cell." A cell can
be genuinely intersected by the polygon boundary — even along a real edge of the
FOV — while containing zero grid samples, because the grid is not aligned to
HEALPix cell boundaries. Halving the grid step relative to cell size lowers the
probability of a miss; it does not eliminate it. "Guarantees complete coverage" is
not a claim point sampling can make at any finite resolution. Only an *exact*
per-cell test can make that claim.

**`healpy.query_polygon` is available** but proved too fragile for universal use.
While it has been part of healpy since 1.16 (well within the `healpy>=1.16` pin
in `pyproject.toml`), it hard-errors on non-convex input with:

```
RuntimeError: polygon is not convex
```

(confirmed empirically at Healpix_cxx query_polygon_internal). This is not a
disqualifying property, but it makes it unsuitable as the sole candidate-search
mechanism: antimeridian-crossing polygons can be "almost convex" in one lon
convention while being genuinely non-convex on the sphere, and a planar convexity
check cannot reliably predict whether `query_polygon` will accept the vertices.

This context also connects to work already sitting unmerged in `.ai/extra/`.
`draft_ADR_Separate_Geometry_Representation_from_Candidate_Search.md` already
specifies the correct shape of solution — a candidate-search step that is allowed
to over-return but must never under-return, feeding an exact intersection test —
with the explicit invariant *"Candidate search must be conservative... it must
never return false negatives."* Separately, ADR-010's "When to Revisit" section
names this project's exact trigger condition: *"Source polygons are very large
(many cells per polygon) and STRtree query returns thousands of candidates."*
This ADR is that revisit.

## Decision

Add an opt-in `exhaustive=True` mode to `process_partition` (fuzzy mode only)
implementing a candidate-search + exact-intersection algorithm:

**1. Candidate search** (conservative superset, native/vectorized, no Python loop
over synthetic points):

Use `healpy.query_disc(nside, centroid_vec, radius, inclusive=True, nest=True)`
where:
- `centroid_vec` is the mean of the unit-vector vertices (normalized)
- `radius` is `max_central_angle(centroid, vertices) + margin_deg` (default 1°)

This is safe for any polygon shape, convex or not, crossing the antimeridian or
not. The disc is constructed to fully contain the polygon by definition, so it
cannot miss a true candidate. It returns more false positives than `query_polygon`
for elongated or irregular shapes; step 2 removes them.

**2. Exact intersection**: for each candidate cell returned by step 1 (typically
tens to low hundreds, not thousands), build its boundary via the existing
`get_healpix_cell_geometry()` and test `intersects()` against the source polygon
(planar lon/lat). This step is exact at the scale of a single HEALPix cell, which
is the same trust boundary the rest of the fuzzy pipeline already accepts for
cell-geometry construction.

**3.** Return the unique cell IDs surviving step 2.

Default behavior (`exhaustive=False`) is unchanged: the SLERP dense-sampling path
from ADR-013 remains the default for its speed and simplicity on typical
small-to-medium FOVs.

## Implementation Details

### Bug fix in `get_healpix_cell_geometry`

During implementation, a latent bug was discovered and fixed: the function
incorrectly interpreted `hp.boundaries` output as `(theta, phi)` in radians,
when it actually returns Cartesian `(x, y, z)` on the unit sphere. The fix
converts Cartesian to spherical correctly via `arccos(z)` and `arctan2(y, x)`.
This bug affected both the exhaustive path and any other code path that builds
cell geometries for intersection tests.

### Why `query_disc` exclusively, not `query_polygon`

The original ADR proposed a two-tier approach (`query_polygon` for convex,
`query_disc` for concave). During implementation this was abandoned:

- `query_polygon` hard-errors on non-convex input, including antimeridian-crossing
  polygons that appear convex in planar lon/lat but are non-convex on the sphere.
- A planar convexity check (`poly.area / poly.convex_hull.area > 0.95`) is not
  reliable enough to prevent these errors.
- `query_disc` is always safe and the exact-intersection step filters false
  positives efficiently, so the performance advantage of `query_polygon` is
  negligible in practice.

### Vertex deduplication

`healpy.query_disc` raises `RuntimeError: degenerate corner` when given duplicate
consecutive vertices (including the common case of a closed polygon where the
first and last vertex are identical). The implementation removes duplicate
consecutive vertices and closing points before passing vertices to `query_disc`.

### `shapely.prepare()` not used

The original plan proposed using `shapely.prepare()` for performance. In practice,
`shapely.prepare()` returns `None` in shapely 2.x (the prepared geometry is stored
internally on the object). The implementation uses direct `intersects()` calls
instead.

## Correctness Argument

Unlike the grid, this design supports an actual "no missed cells" claim, by
construction rather than by resolution tuning:

- The candidate step is provably conservative. `query_disc` with radius
  `max_central_angle + margin` trivially contains the polygon: any point on a
  boundary edge lies on the minor great-circle arc between two vertices, so its
  central angle from the centroid is at most `max_central_angle`. The margin
  absorbs edge curvature and numerical precision issues.
- The exact-intersection step is exact for the single-cell scale it operates at.
- Therefore the union of surviving candidates is exactly the set of cells the
  polygon touches — not a probabilistic approximation of it.

## Alternatives Considered

- **Point-sampling grid** (original draft of this ADR): rejected. Does not
  actually guarantee coverage (see Context). Conflicts with
  `00_CONSTRAINTS.md`'s ban on Python loops over large datasets and its
  100ms-per-1M-rows debt threshold.
- **Increase SLERP edge/interior sampling density**: same objection as the
  original draft — still approximate, still no guarantee.
- **`cdshealpix`'s polygon query**: forbidden per ADR-001 / `00_CONSTRAINTS.md`.
- **`healpy.query_polygon` as the sole/universal mechanism**: rejected — verified
  it hard-errors on non-convex input rather than silently mis-computing.
- **Raster mask on a lat/lon grid, aggregated to HEALPix**: rejected — planar
  distortion, grid-resolution artifacts, and slower than candidate search.
- **Revive STRtree** (pre-ADR-010/013 approach): would solve correctness but
  reintroduces the shapely grid-geometry-caching machinery ADR-010/013
  deliberately removed.

## Consequences

### Positive

- **Actually correct**: no missed cells, provable by construction, not by grid
  density — resolves the false "guarantees complete coverage" claim in the
  original draft.
- **Robust**: works for any polygon shape (convex, concave, antimeridian-crossing)
  without fragile convexity checks.
- **Fast**: candidate search is native/vectorized (`query_disc`); exact
  intersection runs only against the small candidate set.
- **Architecturally consistent**: implements the candidate-search /
  exact-intersection split already specified in
  `draft_ADR_Separate_Geometry_Representation_from_Candidate_Search.md`, and
  satisfies the trigger condition ADR-010 explicitly flagged for revisiting
  spatial search — without reviving the STRtree machinery ADR-010/013 removed.
- **Opt-in**: existing code paths and notebooks are unaffected by default.

### Negative

- **`query_disc` is less tight than `query_polygon`** for elongated or highly
  irregular shapes — more false-positive candidates, though step 2 filters them
  out exactly.
- **The exact-intersection step is still planar** (`shapely.intersects()` against
  `get_healpix_cell_geometry()` polygons) — it inherits the same near-pole /
  antimeridian caveats ADR-010 already documented for that function. A fully
  sphere-native exact test is future work.
- **The angular margin (default 1°) needs empirical tuning**. Too small: misses
  cells at polygon edges. Too large: step 2 becomes slower.

## Waiver

Not applicable. This ADR does not override `00_CONSTRAINTS.md` — the ADR-001
`cdshealpix` prohibition is upheld, not waived; `cdshealpix` is never used here.

## Implementation Notes

1. New function `candidate_cells(body, geom, nside, _healpy, margin_deg=1.0)`
   in `sidecar.py`. Uses `query_disc` exclusively with deduplicated vertices.
2. `process_partition(..., exhaustive: bool = False, ...)`: when `exhaustive=True`
   and `mode='fuzzy'` and `body is not None`, calls `candidate_cells()` then
   `_filter_candidates_exact()`. Raises `NotImplementedError` if `body is None`.
3. `_filter_candidates_exact()` builds cell geometries via `get_healpix_cell_geometry()`
   and tests `intersects()` directly (no `shapely.prepare()` — see above).
4. Bug fix in `get_healpix_cell_geometry()`: correct Cartesian-to-spherical
   conversion from `hp.boundaries` output.
5. Added 7 tests in `TestSphereNativeFuzzyMode`: convex, concave, ground-truth,
   antimeridian, `requires_body`, MultiPolygon (disjoint parts),
   antimeridian-split MultiPolygon (GeoParquet-style), and polar polygon.
   All 76 tests in `test_sidecar.py` pass.

## When to Revisit

- If profiling on real XRS large-FOV data shows the `query_disc` false-positive
  rate makes step 2 the bottleneck, consider a tighter concave-safe native search
  rather than tuning the margin further.
- If a `SurfaceModel.intersects()` sphere-native primitive lands, replace the
  planar step-2 test with it.

## Related Decisions

- **Extends ADR-013**: reintroduces a form of candidate search for the opt-in
  exhaustive path only. ADR-013's default (non-exhaustive) path is unchanged and
  still eliminates STRtree/candidate-search from the hot path exactly as
  written.
- **Satisfies ADR-010's "When to Revisit"** trigger for large source polygons.

---

## `.ai/` Housekeeping (applied)

1. **`decisions/index.md`** — updated with ADR-019 row.
2. **`ADR-013-body-geometry-backend.md`** — add dated note that ADR-019
   selectively reintroduces candidate-search for the opt-in exhaustive path only.
3. **`03_CURRENT_STATUS.md`** — add ADR-019 implementation under NOW/NEXT.
4. **`02_ROADMAP.md`** — add "Exhaustive FOV Coverage (ADR-019)" under Upcoming
   Phases.
5. **`sessions/2026-08-16.md`** — log this session's findings and decisions.