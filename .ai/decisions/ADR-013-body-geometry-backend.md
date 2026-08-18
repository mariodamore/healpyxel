# ADR-013: Pluggable Body Geometry Backend (Sphere / Ellipsoid / DSK)

- **Status:** Active
- **Date:** 2026-07-13
- **Author:** session 2026-07-13

## Context

The current codebase operates implicitly on a unit sphere: `healpy.ang2pix`, `healpy.boundaries`, and all coordinate handling assume a spherical body. This is correct for Mercury and the Moon (flattening ~10⁻⁴) but becomes inadequate for Earth (f=1/298) and Mars.

MERTIS processing plans a future transition from ellipsoid to SPICE DSK (shape model) for high-resolution topography. The current monolithic lon/lat codebase has no clean seam for that transition — it would require invasive changes across `sidecar.py`, `geospatial.py`, and `aggregate.py`.

Separately, ADR-010 identified that unit-vector geometry is the path to eliminating antimeridian and polar pathologies from the computation engine. The sphere-native approach turns out to be the same architectural step.

## Decision

Introduce a `BodyGeometry` interface with three backends:

```
BodyGeometry (interface)
    ├── Sphere(radius=1.0)     — implemented now
    ├── Ellipsoid(a, b, c)     — implemented now
    └── SpiceDSK()             — dummy stub for now
```

The interface provides:

```python
class BodyGeometry:
    def lonlat_to_xyz(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Convert lon/lat degrees to unit (or scaled) vectors."""

    def xyz_to_lonlat(self, xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Convert vectors back to lon/lat degrees."""

    def name(self) -> str:
        """Human-readable body model name for metadata."""

    def is_sphere(self) -> bool:
        """True if this backend is a perfect sphere (optimization hint)."""
```

`Ellipsoid` normalizes lon/lat to an oblate spheroid using the parametric equations:

```
x = (R / r_equatorial) * cos(lat) * cos(lon)
y = (R / r_equatorial) * cos(lat) * sin(lon)
z = (R / r_polar)    * sin(lat)
```

where `R` is the primary body radius and `r_equatorial`, `r_polar` set the flattening.

`Sphere` uses `R` for all axes (or `R=1.0` for unit vectors).

`SpiceDSK` raises `NotImplementedError` with a clear message. It exists so that:
- I/O code can reference it without conditional imports
- DSK can be implemented later as a drop-in replacement
- Metadata and configuration can reference body models uniformly

## Decision: SLERP-based Great-Circle Dense Sampling (Updated 2026-07-14)

The `BodyGeometry` interface enables replacing the entire fuzzy-mode pipeline with a sphere-native algorithm.

### New fuzzy-mode algorithm

```
FOV polygon
    ↓
    extract (lon, lat) vertices from shapely geometry
    ↓
    convert to unit vectors via body.lonlat_to_xyz()
    ↓
    for each edge (including closing edge: last→first):
        SLERP great-circle arc sampling between consecutive unit vector vertices
    ↓
    convert sampled arc points back to (lon, lat) via body.xyz_to_lonlat()
    ↓
    add centroid (repeated for interior coverage) + original vertex coordinates
    ↓
    compute_healpix_ids_from_lonlat(nside, all_lons, all_lats)  →  HEALPix ids
```

The SLERP formula encodes the shortest great-circle arc between two unit vectors:

```
v(t) = sin((1-t)θ)/sin(θ) · v0 + sin(t·θ)/sin(θ) · v1
θ = arccos(clip(v0·v1, -1, 1))
```

This guarantees the correct short arc on the unit sphere — no antimeridian split, no longitude wrapping, no pole singularity.

**Eliminated:**
- `antimeridian.fix_polygon` in the hot path
- `shapely.STRtree` construction and caching (`_HEALPIX_GRID_CACHE`, `_get_healpix_grid`)
- `shapely.Polygon.interpolate()` for dense boundary sampling
- `healpyxel_to_geoparquet` cell-polygon construction for sidecar generation

**Preserved:**
- `geospatial.py` `healpy_to_geodataframe()` still uses `antimeridian.fix_polygon` for visualization output
- `antimeridian` remains a required dependency
- `compute_healpix_ids_from_polygon` retained as fallback when `body=None`

### Why SLERP instead of query_disc + spherical test

The original plan called for `healpy.query_disc()` plus a spherical point-in-polygon edge-sign test. That approach was found unreliable for certain polygon shapes (especially near the poles) due to inconsistent edge signs from numerical precision issues. SLERP dense sampling is:
- **Deterministic**: Given the same polygon and nside, always returns the same cells
- **Winding-agnostic**: Works regardless of vertex ordering (CW/CCW)
- **Convention-independent**: Same cells regardless of lon convention [-180,180] or [0,360]
- **Pole-safe**: Unit vectors at the pole are valid; no singularity
- **Simple**: No external geometry library needed for the hot path

### Winding order and invalid polygons

SLERP-based sampling does not depend on winding order — it samples edges and adds interior points regardless of polygon orientation. However, this does NOT mean the sidecar engine accepts invalid input. **Invalid polygons** (self-intersections, duplicated vertices) are still rejected upstream by Shapely. The SLERP approach only eliminates the need for:
- Antimeridian splitting
- Longitude wrapping fixes
- Planar centroid calculations

Input data quality remains the responsibility of upstream software.

### antimeridian scope

With this interface in place, `antimeridian.fix_polygon` is eliminated from the computation engine (`process_partition`). `antimeridian` remains required but its scope narrows to the `geospatial.py` export layer (§2 in PROPOSALS.md).

## Consequences

### Positive

- **Clean seam for MERTIS DSK transition:** when SPICE backing is added, only `SpiceDSK` changes — no changes to `sidecar.py`, `aggregate.py`, or the pipeline
- **Eliminates antimeridian from the hot path:** `process_partition()` calls `body.lonlat_to_xyz()` once at ingestion; no `antimeridian.fix_polygon` inside the per-geometry loop
- **Correct geometry for Earth/Mars:** Ellipsoid backend gives geodetically correct results when explicitly requested
- **Testable interface:** each backend has a small, pure surface
- **Backward compatible:** default `radius=1.0` reproduces current unit-sphere behavior exactly
- **Simpler than query_disc:** no spherical filter debugging, no edge-sign instability
- **Convention-independent:** [-180,180] and [0,360] lon conventions produce identical cell assignments

### Negative

- **Dense sampling overhead:** ~80 points per edge + centroid, slightly more CPU than `query_disc` for large polygons
- **New module** adds one import to the sidecar hot path
- **Two coordinate transformations** per ingestion (lon/lat → xyz at input, xyz → lon/lat at output/visualization)
- **Ellipsoid doesn't help current data** (Mercury/Moon are spherical) — it's for Earth/Mars applications only
- **ADR-008 partially waived:** antimeridian.fix_polygon no longer required before bounds pre-filter in the computation path (but still needed for geospatial export)
- **ADR-019 selectively reintroduces candidate search** for the opt-in exhaustive path only. The "Eliminated: shapely.STRtree construction and caching" line above is not contradicted for the default path — ADR-019's exhaustive mode is explicitly opt-in and uses `healpy.query_disc`, not STRtree.

## Alternatives Considered

- **Keep current implicit sphere:** rejected — blocks MERTIS DSK transition and leaves antimeridian as a permanent core dependency
- **query_disc + spherical edge-sign test:** tried and found unreliable for certain polygon shapes (near-pole filtering failures)
- **Full spherical geometry library (e.g., `spherical_geometry` from STScI):** rejected for now — adds a heavy dependency; the SLERP approach avoids it entirely
- **Only add `DummyDSK` without Sphere/Ellipsoid:** rejected — the real value is making the computation engine body-agnostic now so Sphere/Ellipsoid differences are caught early

## Implementation Notes

1. New file: `healpyxel/geometry.py` (Sphere, Ellipsoid, SpiceDSK, BodyGeometry)
2. `sidecar.py`: removed `import antimeridian` from hot path
3. `sidecar.py`: added `_sample_great_circle_arc(v0, v1, n_samples)` using SLERP
4. `sidecar.py`: rewrote `_query_healpix_single_polygon()` to use SLERP on unit vectors
5. `geospatial.py` `healpy_to_geodataframe()` continues to produce lon/lat GeoParquet for visualization — antimeridian stays there
6. Default body in `sidecar.run()` is `Sphere(radius=1.0)`, configurable via CLI `--body-model` or config dict
7. `SpiceDSK` stub docstring points to ADR-013 as the implementation target
