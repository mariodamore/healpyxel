# ADR-010: Abandon hierarchical HEALPix traversal, retain STRtree + shapely.prepare()

- **Status:** Superseded by ADR-013
- **Date:** 2026-07-12
- **Author:** session 2026-07-12

## Context

Phase 1 (`shapely.prepare()`) of the spatial-index optimization roadmap was successfully completed: source geometries and grid geometries are now prepared before `intersects()` checks, giving a consistent ~5-10% speedup on fuzzy-mode fuzzy assignments.

Phase 2 proposed replacing the STRtree-based polygon-to-cell intersection with a custom hierarchical HEALPix traversal that walks the HEALPix tree, pruning branches that don't intersect the source polygon. The goal was to eliminate polygon-to-polygon intersection costs entirely.

An implementation was built (`_builtin_hierarchical_healpix`) and tested on `test_data/samples/sample_50k.parquet`. It returned zero cells for all geometries.

## Decision

Reject the hierarchical traversal approach for the current codebase. Retain:
- STRtree spatial index for fuzzy mode (fast O(log M) candidate retrieval)
- `shapely.prepare()` on both grid and source geometries (faster per-intersection check)
- Dense sampling fallback (`compute_healpix_ids_from_polygon`) when STRtree is unavailable

Remove the broken `_builtin_hierarchical_healpix()` function and its call site.

## Root Cause of Failure

The hierarchical traversal failed due to a fundamental property of HEALPix: **polar cells wrap around the south/north poles in ways that cannot be represented as simple lon/lat polygons**.

Specifically:
1. `get_healpix_cell_geometry()` was misinterpreting `healpy.boundaries()` return format: it treated the 3D Cartesian `(x, y, z)` array as `(theta, phi)`, producing completely wrong cell geometries.
2. Even after fixing the coordinate conversion, polar NSIDE=1 cells (e.g., cell 11) span from 315° through 0° to 270°, wrapping through the south pole. `shapely.Polygon` cannot represent this topology — it collapses to a degenerate shape covering 0° to 270° latitude.
3. The intersects() check against a source polygon at lon/lat (324°, -41°) returned `False` because the cell geometry was placed at the wrong longitude (0°/270°) and didn't cover the source location in planar coordinates.

Any polygon-based cell geometry approach inherits this problem at the poles. The STRtree path works because `healpix_to_geodataframe()` (via `_make_polygon_from_corners`) handles polar cells via `antimeridian.fix_polygon` and multi-part representations.

## Consequences

- **Positive:** Simpler codebase — removed ~160 lines of broken hierarchical traversal code
- **Positive:** STRtree + prepare() is reliable and well-tested (61 sidecar tests pass)
- **Positive:** Future optimization work can focus on STRtree improvements (prepared geometries, vectorized dense sampling) rather than a custom traversal
- **Negative:** The Python-level loop over geometries in fuzzy mode remains the bottleneck. Per-geometry STRtree query + per-cell intersects() is O(N·k) where k is candidate count.
- **Negative:** No path to HEALPix-native traversal without addressing spherical geometry (planar shapely intersects() is fundamentally wrong for polar cells and antimeridian-crossing cells)

## Alternatives Considered

- **Fix hierarchical traversal with spherical geometry**: Replace `shapely.intersects()` with a spherical-aware test. Would require either `spherical_geometry` (STScI) or a custom 3D dot-product approach. Rejected for now — adds a dependency and complexity; STRtree + prepare() is sufficient for NSIDE ≤ 128.
- **Hierarchical traversal using pre-built grid only**: Use the cached STRtree but walk it hierarchically (query coarse, only descend into matching children). This is essentially what STRtree already does internally — no benefit over direct query.
- **Vectorized dense sampling**: Replace per-polygon `interpolate()` loop with batched `healpy.ang2pix()` on pre-sampled boundary points. This is already partially implemented in `compute_healpix_ids_from_polygon` and is the existing fallback when STRtree is unavailable.
- **No optimization (keep pre-Phase-1 code)**: Rejected — `shapely.prepare()` gave measurable improvement with essentially zero risk.

## What Was Implemented (Phase 1 Only)

1. **`shapely.prepare()` on grid geometries** in `_get_healpix_grid()`: grid cell geometries are prepared once at cache-build time.
2. **`shapely.prepare()` on source geometries** in `process_partition()` fuzzy mode: each source polygon is prepared before the intersects() loop.
3. **Bug fix in `get_healpix_cell_geometry()`**: correctly converts `healpy.boundaries()` 3D Cartesian output `(x, y, z)` to lon/lat via `theta = arccos(z)` and `phi = arctan2(y, x)` (previously misinterpreted as `(theta, phi)` directly). This fix benefits all cell-geometry construction in the codebase.
4. **Bug fix: removed undefined `_normalize_lon_convention()` calls** — three call sites that would raise NameError; replaced with direct `lon_convention` pass-through.

## Performance

- Fuzzy mode on 50k sample: ~1.15s with STRtree + prepare()
- STRtree is the dominant path; dense sampling fallback only activates when shapely.STRtree is unavailable
- All 196 existing tests pass

## When to Revisit

Revisit hierarchical or spherical-geometry approaches when:
- NSIDE > 128 is required (grid memory becomes a concern)
- Source polygons are very large (many cells per polygon) and STRtree query returns thousands of candidates
- A spherical-geometry library (e.g., `spherical_geometry` from STScI) is added as a dependency

**Updated 2026-08-16:** The trigger condition "source polygons very large... STRtree query returns thousands of candidates" has been revisited by ADR-019, which resolves the large-FOV correctness issue using `healpy.query_disc` + exact intersection instead of reviving STRtree or hierarchical traversal.
