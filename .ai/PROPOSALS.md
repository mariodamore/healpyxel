# Proposals from ChatGPT Optimization Review

Generated from conversation: https://chatgpt.com/c/6a51fc45-1e80-83ed-b8b7-55b64d3b9f65
Date: 2026-07-11 to 2026-07-12
Reviewed against: sidecar.py, geospatial.py, aggregate.py, accumulator.py, finalize.py
Reviewed against: ADR-013, ADR-010, ADR-008, 00_CONSTRAINTS.md, MERTIS DSK plans

---

## Classification Key

| Tag | Meaning |
|------|---------|
| ✅ Valid | Proposal aligns with existing code and constraints; implementable now |
| ⚠️ Conditional | Valid only after specific prerequisites are met |
| ❌ Rejected | Contradicts existing ADR, constraint, or is architecturally wrong |
| 🔮 Long-term | Architecturally sound but requires structural changes first |

---

## 1. PSF Integration Architecture

### 1.1 Two-Pass Workflow (Decoupled PSF)

**ChatGPT proposal:** Separate sidecar generation (geometry) from PSF weighting. Generate unweighted sidecar first, then apply PSF as a second pass. Weighted sidecar stored as [fov_id, healpix_id, psf_weight].

**Status:** ✅ Valid
**Source:** PSF already partially implemented in [sidecar.py](healpyxel/sidecar.py) (GaussianPSF, PSF_REGISTRY, get_psf). The centroid-to-centroid weighting in `process_partition()` is the seed.

**Required changes:**
- Add `psf_integration.py` module with vectorized 2-pass engine
- Pass 1: existing sidecar generation (already produces [source_id, healpix_id])
- Pass 2: read unweighted sidecar, evaluate PSF at sub-pixel positions, aggregate weights to NSIDE cell, normalize per observation
- Update downstream aggregation to check for `psf_weight` column and apply during binning; fallback to binary gate if absent

### 1.2 Configurable Subgrid NSIDE

**ChatGPT proposal:** Replace hardcoded NSIDE=512 with `nside_sub` configurable per instrument (based on IFOV and target NSIDE).

**Status:** ✅ Valid
**Source:** Constraints require general NSIDE handling; current code uses configurable NSIDE everywhere except PSF pathway.

**Required changes:**
- Add `subgrid_nside` parameter to PSF integration (default: auto-computed from `nside_analysis` if not specified)
- Remove any fixed 512 assumptions in PSF code

### 1.3 Generalized Parent-Child Mapping

**ChatGPT proposal:** Replace `child >> 12` (which only works for 512→8) with `parent = child >> (2 * factor)` where `factor = log2(nside_sub // nside_analysis)`.

**Status:** ✅ Valid — preferred over hardcoded bit shift

### 1.4 Angular PSF Evaluation (Spherical)

**ChatGPT proposal:** Evaluate PSF using angular separation on the unit sphere: `theta = arccos(u · b)`, `w = exp(-theta² / 2*sigma²)`. Never use (dlat, dlon) as proxy for (dx, dy).

**Status:** ✅ Valid — replaces current centroid-to-centroid lat/lon approach in `process_partition()`
**Why:** Lat/lon Gaussian is wrong near poles and for large FOVs. Unit vector approach is exact everywhere.

### 1.5 Precomputed PSF Stencil / Lookup Table

**ChatGPT proposal:** Precompute weight vs angular distance once. At integration time, only lookup — no exponentials in the main loop.

**Status:** ✅ Valid
**Source:** A PSF with σ=0.3° only has ~300 cells within 3σ at NSIDE=512. Sparse stencil evaluation is much faster than evaluating millions of exponentials.

**Required changes:**
- Cache PSF lookup table keyed by (sigma, nside_sub)
- Vectorized lookup using healpy ang2pix for candidate cells

### 1.6 Sparse Response Matrix as Sidecar Output

**ChatGPT proposal:** Sidecar should store [obs_id, healpix, weight] with weights summing to 1. This is the "observation operator" enabling forward modeling, mosaicking, deconvolution.

**Status:** ✅ Valid — the sidecar format already supports this column. Current code adds `psf_weight` in `process_partition()`.

### 1.7 Fast vs Accurate PSF Evaluation Modes

**ChatGPT proposal:** Two modes:
- Fast: evaluate PSF at cell centers (Monte Carlo approximation)
- Accurate: integrate PSF over cell area

**Status:** ✅ Valid
**Note:** Fast mode is the default; accurate mode explicitly requested.

### 1.8 Adaptive Subpixel Refinement

**ChatGPT proposal:** Instead of uniform high-NSIDE decomposition, recursively subdivide only cells that contribute significant PSF weight or intersect steep gradient regions. Exploits HEALPix hierarchy, often reduces evaluations by 10×.

**Status:** ⚠️ Conditional — valid only after ADR-010 revisit condition (spherical geometry support)

---

## 2. Body Geometry Backend (Three-Tier Architecture)

**ChatGPT proposal:** A layered geometry backend covering the full range of planetary bodies — from perfect spheres to shape models — without forcing unnecessary complexity into the core algorithm.

> Given your work on MERTIS, PDS4, and planetary data processing, healpyxel's real contribution is a **hierarchical planetary observation operator**. HEALPix is just the discretization; the geometry backend can evolve from a simple sphere to a full DSK without changing the downstream data model.

### 2.1 Sphere — Core Engine

- `Sphere(radius)` — for Moon, Mercury, most asteroids
- Unit vectors internally, no lon/lat in the engine
- Fast, robust, analytically simple
- This is the **default** for the codebase today

### 2.2 Ellipsoid — Experimental Backend

- `Ellipsoid(a, b, c)` — for Earth, Mars, Venus
- Same internal representation (Cartesian vectors)
- Different coordinate conversions and ray intersections
- Adds minimal overhead; user explicitly opts in

### 2.3 DSK Shape Model — Future Extension

- `SpiceDSK()` — for shape models (Bennu, Phobos, future MERTIS DSK)
- HEALPix is still just the discretization; geometry engine handles arbitrary body shape
- Planned for MERTIS: switch from ellipsoid to SPICE DSK for high-resolution topography

### Why This Matters for healpyxel

This is not just a "nice to have." MERTIS planning explicitly includes transitioning to SPICE DSK. This proposal gives that future transition a clean interface: the sidecar generation and aggregation pipeline remain unchanged — only the geometry backend swaps out.

### Prerequisites

- Define a `BodyGeometry` interface (or protocol) with methods: `lonlat_to_xyz`, `xyz_to_lonlat`, `intersect_ray`, `surface_normal`, `contains`
- No new constraints; does not conflict with existing ADR-008/010
- Phase 1 (Sphere) can be implemented without touching the current antimeridian/STRtree logic
- Phase 2 (Ellipsoid) requires only updating the coordinate conversion layer
- Phase 3 (DSK) requires SPICE integration

### antimeridian: I/O Only, Not a Geometry Concern

With the unit-vector Sphere backend, `antimeridian` becomes an **output/export** concern only.

**Current two uses of `antimeridian` in the codebase:**

| Location | Purpose | Fate with Sphere backend |
|----------|---------|--------------------------|
| `sidecar.py` `process_partition()` (line 689) | Fix every input polygon before STRtree indexing — workaround for shapely's planar lon/lat | **Removed.** Unit vectors have no antimeridian. The lon/lat→xyz conversion only needs `np.mod(lon, 360)` to wrap longitude. |
| `geospatial.py` `healpix_to_geodataframe()` and `healpyxel_to_geoparquet()` | Generate valid lon/lat polygons for GeoParquet / map visualization | **Retained.** Map tools (QGIS, Kepler, etc.) expect polygons that don't cross ±180°. `antimeridian.fix_polygon` stays here as the I/O boundary.

**Architecture split:**

```
INPUT (lon/lat from user data)
    │
    ▼  I/O layer: np.mod(lon, 360) → xyz unit vectors
UNIT VECTORS  ← computation engine, never sees lon/lat, never needs antimeridian
    │
    ▼
SIDECAR OUTPUT (parquet: source_id, healpix_id, weight) — no geometry
    │
    ▼
GEOSPATIAL OUTPUT (lon/lat for visualization)
    │
    ▼  I/O layer: antimeridian.fix_polygon → valid MultiPolygon for GeoParquet
```

This means:
- `antimeridian` remains a required dependency (for the `healpyxel_to_geoparquet` CLI entrypoint and visualization output)
- ADR-008 ("antimeridian.fix_polygon must run before bounds pre-filter") which targets `process_partition()` **can be waived** for the computation path
- The `00_CONSTRAINTS.md` antimeridian requirement stays, but its scope narrows to the export layer

---

## 3. Unit Vector Internal Representation

**ChatGPT proposal:** Convert all geometry to unit vectors (x, y, z) at ingestion. Longitude/latitude is only import/export format. Core engine never operates on lon/lat.

**Status:** ✅ Implemented (2026-07-14) — ADR-013
**Implementation:** `_query_healpix_single_polygon()` converts polygon vertices to unit vectors via `body.lonlat_to_xyz()`, then uses SLERP great-circle sampling on unit vectors. Lon/lat is only used at the I/O boundary.
**Note:** No spherical polygon–cell intersection library needed — SLERP dense sampling replaces the need for exact intersection tests.

### 3.1 Great-Circle Edges via SLERP

**ChatGPT proposal:** Edges should be great-circle arcs, not straight lines in lon/lat projection. This is how the physical footprint boundary is formed.

**Status:** ✅ Implemented (2026-07-14) — ADR-013
**Implementation:** `_sample_great_circle_arc()` in `sidecar.py` uses SLERP on unit vectors to produce correct short great-circle arcs between consecutive polygon vertices. Automatically handles:
- Antimeridian-crossing edges (e.g., 179° → -179° is only 2° on the sphere)
- Pole-proximal edges (no coordinate singularity)
- All longitude conventions (same unit vectors regardless of convention)
**Why:** Directly eliminates limb, pole, and antimeridian pathologies without any external geometry library.

---

## 4. Candidate Improvements to Current Fuzzy-Mode Pipeline

### 4.1 SLERP Great-Circle Dense Sampling (Replaces query_disc plan)

**ChatGPT proposal:** Use SLERP on unit vectors for dense boundary sampling: `v(t) = sin((1-t)θ)/sin(θ)·v0 + sin(t·θ)/sin(θ)·v1`.

**Status:** ✅ Implemented (2026-07-14) — ADR-013
**Rationale:** Originally planned `query_disc` + spherical edge-sign test was found unreliable for certain polygon shapes (near-pole filtering failures). SLERP dense sampling is:
- Deterministic: same polygon + nside → same cells always
- Convention-independent: [-180,180] and [0,360] produce identical results
- Winding-agnostic: works regardless of vertex ordering
- Pole-safe: unit vectors at the pole are valid; no singularity
- No external geometry library needed for the hot path
- Automatically handles antimeridian-crossing edges (short great-circle arc)

**Implementation:** `_sample_great_circle_arc(v0, v1, n_samples)` + `_query_healpix_single_polygon()` in `sidecar.py`. Each edge sampled with 80 SLERP points + centroid (10x) + original vertices. No STRtree, no antimeridian, no shapely interpolate.

### 4.2 Fix Polar Cell Geometry

**ChatGPT proposal:** Fix polar cell geometry construction to correctly handle cells that wrap through poles.

**Status:** ✅ Resolved (2026-07-14) — ADR-013
**Resolution:** The STRtree + shapely cell-polygon path was eliminated from the fuzzy hot path. Polar wrap is no longer a concern because `_query_healpix_single_polygon` operates on unit vectors, not shapely cell polygons.

### 4.3 Dense Sampling as body=None Fallback

**ChatGPT proposal:** Retain dense boundary sampling for cases where no body geometry backend is configured.

**Status:** ✅ Implemented (2026-07-14)
**Implementation:** `compute_healpix_ids_from_polygon()` in `sidecar.py` is retained as the fallback when `body=None`. This uses the old STRtree + shapely-based dense sampling path and is the only remaining use of STRtree in sidecar generation.

---

## 5. PSF Evaluation Geometry Backends

### 5.1 Tangent-Plane Gaussian (Fast Path)

**ChatGPT proposal:** For small FOVs (common orbital mapping), build local ENU basis and project PSF as ellipse on tangent plane. Fast and accurate for footprints spanning < 1–2°.

**Status:** ✅ Valid
**Note:** Faster than spherical arccos for many observations. Should be the default with spherical as fallback for large FOVs.

### 5.2 Spherical Gaussian (Robust Path)

**ChatGPT proposal:** For large FOVs, distant flybys, and limb observations, evaluate PSF using unit vectors and arccos. Always correct.

**Status:** ✅ Valid — the required fallback for edge cases

### 5.3 Auto-Select Geometry Backend

**ChatGPT proposal:** `GaussianPSF(geometry="auto")` selects tangent for small FOVs, spherical for large ones based on footprint angular extent.

**Status:** ✅ Valid
**Per ChatGPT:** "auto selects method based on the footprint's angular size (switching to spherical once the footprint spans more than about 1–2°)."

---

## 6. Rejected Proposals

### 6.1 Convolve Instrument PSF with HEALPix Cell PSF

**ChatGPT analysis:** HEALPix cells are NOT measurement devices. They have no intrinsic blur. Convolving adds smoothing that is an analysis choice, not observation geometry. CCD analogy: you don't convolve optics with a "CCD PSF."

**Status:** ❌ Rejected — conceptually wrong
**Alternative:** Treat cells as binary basis functions. Weight = integral of PSF over cell.

### 6.2 Rasterize PSF onto High-Res Grid, Then Aggregate

**ChatGPT analysis:** "This is the wrong direction." Evaluate PSF directly at candidate cell centers instead.

**Status:** ❌ Rejected — inefficient and unnecessary

### 6.3 Evaluate Gaussian in Lat/Lon Deltas

**ChatGPT analysis:** Computing `(dlat, dlon)` and plugging into `exp(-(dx²+dy²)/2σ²)` is wrong near poles and for large FOVs.

**Status:** ❌ Rejected — scientifically incorrect
**Replace with:** Unit vector angular separation (§1.4)

### 6.4 Hardcoded bit-shift for Parent Lookup

**ChatGPT analysis:** `child >> 12` only works because 512 = 8 × 2⁶. Breaks for any other NSIDE pair.

**Status:** ❌ Rejected — use generalized formula (§1.3)

### 6.5 Fixed NSIDE=512 for Subgrid

**ChatGPT analysis:** "I would never bake 512 into the implementation."

**Status:** ❌ Rejected as default — use configurable/subgrid auto-selection (§1.2)

### 6.6 Replace STRtree with Hierarchical Traversal (Immediate)

**ChatGPT proposal:** Walk HEALPix tree, prune non-intersecting branches.

**Status:** ❌ Rejected
**ADR-010:** Already implemented and tested — returned zero cells for all geometries due to polar cell issues. Retain STRtree.
**Revisit condition:** When spherical-geometry library (e.g., `spherical_geometry` from STScI) is added as a dependency AND NSIDE > 128 is required.

### 6.7 Unit Vector Internal Geometry (Now Implemented via SLERP)

**Original ChatGPT proposal (as immediate action):** Convert all geometry to unit vectors in the current codebase now.

**Original status:** ❌ Rejected as immediate action — conflicted with ADR-008
**Current status (2026-07-14):** ✅ Implemented through a different route — SLERP dense sampling on unit vectors (§3, §3.1, §4.1). Rather than implementing a full spherical-geometry intersection library (which required ADR-008 waiver), unit vectors are now used via great-circle arc SLERP in `_query_healpix_single_polygon`. The fuzzy-mode hot path is fully sphere-native.

---

## Summary Table

| # | Proposal | Status | Prerequisites |
|---|---------|--------|--------------|
| 1.1 | Two-pass PSF workflow | ✅ Valid | — |
| 1.2 | Configurable subgrid NSIDE | ✅ Valid | — |
| 1.3 | Generalized parent-child mapping | ✅ Valid | — |
| 1.4 | Angular (unit vector) PSF evaluation | ✅ Valid | — |
| 1.5 | Precomputed PSF stencil/lookup | ✅ Valid | — |
| 1.6 | Sparse response matrix sidecar | ✅ Valid | Already partially done |
| 1.7 | Fast vs accurate PSF modes | ✅ Valid | — |
| 1.8 | Adaptive subpixel refinement | ⚠️ Conditional | Spherical geometry support |
| 2.1 | Sphere core engine tier | ✅ Implemented (2026-07-13) | `healpyxel/geometry.py`, `sidecar.py` wired |
| 2.2 | Ellipsoid experimental tier | ✅ Implemented (2026-07-13) | `Ellipsoid(radius, polar_radius)` in `geometry.py` |
| 2.3 | DSK shape model tier | 🔮 Long-term (dummy stub) | `SpiceDSK` raises NotImplementedError; ADR-013 tracks |
| 3   | Unit vector internal geometry | ✅ Implemented (2026-07-14) | SLERP via ADR-013, no ADR-008 waiver needed |
| 3.1 | Great-circle edges | 🔮 Long-term | Follows unit vector geometry |
| 4.1 | SLERP great-circle dense sampling | ✅ Implemented (2026-07-14) | ADR-013: replaced query_disc plan; more robust for edge cases |
| 4.2 | Fix polar cell geometry | ✅ Resolved (2026-07-14) | ADR-013: SLERP path eliminates shapely cell polygons entirely |
| 4.3 | Dense sampling as body=None fallback | ✅ Implemented (2026-07-14) | `compute_healpix_ids_from_polygon` retained when no body geometry configured |
| 5.1 | Tangent-plane PSF fast path | ✅ Valid | — |
| 5.2 | Spherical PSF robust path | ✅ Valid | — |
| 5.3 | Auto-select PSF backend | ✅ Valid | Combines 5.1 + 5.2 |
| 6.1 | Cell PSF convolution | ❌ Rejected | Conceptually wrong |
| 6.2 | Rasterize PSF | ❌ Rejected | Inefficient |
| 6.3 | Lat/lon Gaussian deltas | ❌ Rejected | Incorrect math |
| 6.4 | Hardcoded bit-shift | ❌ Rejected | Use formula |
| 6.5 | Fixed NSIDE=512 | ❌ Rejected | Use configurable |
| 6.6 | Hierarchical traversal (immediate) | ❌ Rejected | ADR-010 |
| 6.7 | Unit vectors now (immediate) | ❌ Rejected | Conflicts with ADR-008 |

---

## Recommended Implementation Order

1. **Phase A (near-term, no ADR changes):**
   - 1.1 Two-pass PSF workflow + `psf_integration.py`
   - 1.2 Configurable subgrid NSIDE
   - 1.3 Generalized parent-child mapping
   - 1.4 Angular PSF evaluation (unit vectors)
   - 5.1 Tangent-plane PSF fast path
   - 5.2 Spherical PSF robust path
   - 5.3 Auto-select backend
   - 1.5 Precomputed stencil
   - 1.7 Fast/accurate modes

2. **Phase B (after Phase A ships):**
   - 1.6 Sparse response matrix as default sidecar output
   - 4.2 Fix polar cell geometry (shapely limitation — may need spherical test)
   - 4.3 Complete vectorized dense sampling
   - Evaluate ADR-010 revisit conditions (NSIDE > 128 need, spherical-geometry dep)

3. **Phase C (long-term, requires ADR changes):**
   - 2.3 DSK shape model (replace dummy with SPICE implementation — ADR-013)
   - 3 Unit vector internal geometry (waive ADR-008)
   - 3.1 Great-circle edges
   - 1.8 Adaptive subpixel refinement

---

## PSF Module Architecture (Target Design)

```
healpyxel/
├── psf/
│   ├── gaussian.py       # GaussianPSF with geometry="auto" (tangent/spherical)
│   ├── airy.py           # AiryPSF (future)
│   ├── measured.py       # MeasuredPSF for instrument calibration data
│   └── integration.py    # Two-pass weighting engine
│                          # - vectorized, no Python loops
│                          # - uses unit vectors for spherical evaluation
│                          # - lookup table caching
│                          # - adaptive refinement
├── geometry/              # (Phase C)
│   ├── sphere.py         # Sphere(radius) surface model
│   ├── ellipsoid.py      # Ellipsoid(a,b,c) surface model
│   └── dsk.py            # SpiceDSK surface model (future MERTIS)
├── sidecar.py            # Existing, PSF-aware
├── aggregate.py          # Updated to apply psf_weight column
└── ...
```

---

_Last updated: 2026-07-13_
