# IDEAS_HEALPYXEL — Ideas from the design chat, verified against `dev`

Source: `.ai/i-wrote-this-package-healpyxel.md` (2026-07-11 → 2026-08-15).
Purpose: de-duplicated checklist of every improvement/feature idea raised for **healpyxel**, with per-item status verified against the actual code.

**Verification baseline:** `dev` HEAD `66929c3` (2026-08-15). Checked module by module.

### Status legend
- `[x]` — implemented on `dev`
- `[~]` — **partial** (implemented, but differently / incompletely vs the idea)
- `[ ]` — not implemented

ADRs referenced: `.ai/decisions/ADR-001` (healpy over cdshealpix), `ADR-013` (body geometry backend), `ADR-015` (multi-resolution sidecar via NEST bit-shift), `ADR-016` (pipeline wrapper), `ADR-018` (WKB fallback).

---

## 1. Sidecar generation — move off GeoPandas joins to native spherical queries

Context: dev branch at chat start used GeoPandas to link every input FOV to all HEALPix cells it touches (or its center). Scale: thousands → ~1M FOVs, nside 8 → 128 (higher later). FOVs are rectangles at nadir but heavily distorted at extreme geometry. Target output: GeoParquet.

- [x] Replace GeoPandas geometric `sjoin` / polygon generation with native spherical HEALPix queries — no `sjoin` anywhere; `sidecar.py` uses `compute_healpix_ids_from_lonlat` (→ `hp.ang2pix`) and `_query_healpix_spherical`. **Note:** fuzzy mode is implemented via great-circle (SLERP) arc sampling + interior centroid + vertices, then `ang2pix` — *not* `hp.query_polygon`/`query_disc`.
- [~] **Vectorized center lookup (O(1))** — `compute_healpix_ids_from_lonlat` is vectorized over arrays, but the scalar lon/lat workflow in `process_partition` loops row-by-row (`for i, (src_id, lon, lat) …`), so it is not a single batch call over all rows.
- [~] **Vectorized touching lookup** — implemented as spherical arc-sampling (SLERP, `n_edge=80`, interior centroid + vertices) feeding `ang2pix`; boundary-touching cells are caught, but not via `hp.query_polygon(..., inclusive=True)`.
- [~] **Decouple GeoPandas entirely from the index-calculation loop** — reads happen lazily via `dask_geopandas.read_parquet` (ADR-018 fallback to plain dask + WKB decode); `antimeridian` removed from the computation path (ADR-013, sphere handles wrapping). But shapely `Polygon`/`MultiPolygon` geometries are still processed in the loop; geopandas is not used for joins, yet geometry objects remain central.
- [x] **Long-format GeoParquet** — sidecar output is `[source_id, healpix_id, (weight)]`, one row per (source, cell) pair.
- [ ] **Parquet partitioning by `healpix_id`** — not done. Outputs are per-nside files (`<stem>.cell-healpix_assignment-<mode>_nside-<n>_order-nested.parquet`), coalesced or as a `.parts/` directory; no partitioning by cell id.
- [x] **Parallelized sidecar pipeline** — dask partitions across `--ncores`.
- [ ] Optional: adopt the **MOC (Multi-Order Coverage)** standard (`mocpy`) — not done; `cdshealpix` is forbidden (ADR-001), `mocpy` not used.

## 2. Instrument / spacecraft PSF — geometry strategy

Instrument aperture is angularly constant, but changing spacecraft elevation and observation angle distort the projected surface FOV. We have surface-projected FOV polygons but not 3D ray vectors.

- [ ] **Backward-lookup / ray-tracing method (most accurate)** — not done; requires spacecraft 3D position + pointing matrix (3D rays not available).
- [~] **Analytical ellipsoid / bivariate-Gaussian Jacobian method** — a PSF is implemented as a callable `data_psf(dx, dy)` evaluated at the **centroid-to-centroid** offset in raw lon/lat degrees (`compute_assignment_weight`). This is the *lat/lon bivariate-Gaussian* form, not the unit-sphere/tangent-plane form later recommended.
- [x] Valid on spherical bodies (Mercury, Moon) — `geometry.Sphere` (default) + `geometry.Ellipsoid` backends (ADR-013); `SpiceDSK` is a documented placeholder (not implemented).
- [ ] **Great-circle / tangent-plane caveat near the limb** — not done; PSF is evaluated in raw lon/lat `dx`,`dy`, with no tangent-plane/gnomonic handling.

## 3. HEALPix cell PSF (nside-dependent) — later deemed physically wrong, keep as background

- [ ] **Option 1 — Top-hat / uniform area weights** — no explicit top-hat width (`θpix ≈ 58.63/nside`) implementation; default `--cell-psf none` effectively gives binary weight 1.0.
- [~] **Option 2 — nside-scaled Gaussian smoothing** — `--cell-psf gaussian` exists (`GaussianPSF`), but `sigma` is user-supplied (`--cell-psf-sigma-level`, default 2.0), **not** derived from nside (29.315/nside).
- [ ] **Option 3 — `hp.pixwin`** — not used.

**Resolution:** A HEALPix cell is *not* a sensor — it has no optics/detector/cross-talk. Treating it as a PSF source is physically a misnomer. **HEALPix cells are rigid binary bins; PSF is an instrument property only.** → [x] Default is binary (cell PSF `none`); the non-physical `--cell-psf gaussian` option still exists and defaults off.

## 4. Correct physical model — instrument PSF integrated over binary HEALPix bins

- [ ] Weight = spatial integral of the instrument PSF over the cell area — not done; `compute_assignment_weight` uses centroid-to-centroid only (source comment: *"future: integrate over geometry or use rasterized PSF"*).
- [~] **High-resolution point-sampling path** — partial: PSF is sampled at the cell **centroid** (not a per-cell-center subgrid across many candidate cells).
- [ ] **Low-resolution sub-pixel path** (subdivide FOV into a fine grid, `ang2pix` each sub-point, sum per cell) — not done.

## 5. Hierarchical sub-pixel integration (use HEALPix itself to sample)

- [ ] Query the FOV at a high resolution `nside_high` via `hp.query_polygon` and evaluate PSF at those child-cell centers — not done; PSF is evaluated on the target cell centroids directly, no `nside_high` subgrid.
- [~] **Bit-shift aggregate children→parent** — the NEST bit-shift aggregation exists (`_aggregate_healpix`, ADR-015) but aggregates *assignment cells* from `nside_max` to lower nsides; it does **not** aggregate PSF subgrid weights from a separate `nside_high`.
- [ ] Expose `nside_high` as a tunable setting — no such setting.

## 6. Choosing `nside_high` (oversampling rule)

All three items not implemented (no `nside_high`/oversampling concept exists):
- [ ] Rule of thumb: sub-pixel resolution ≥ 3–5× smaller than narrowest FOV dim / PSF FWHM (`k=4`).
- [ ] Dynamic formula `nside_high = 2^ceil(log2(58.63·k/θmin))`, capped.
- [ ] Per-instrument `θmin` estimate (incl. MERTIS → `nside_sub` 1024).

## 7. Edge cases — highly distorted FOV spanning a massive area

- [ ] **Adaptive `nside_high` degradation / memory guardrail** — not present.
- [~] **Use absolute 3D unit vectors** — the spatial query path converts to unit vectors via `body.lonlat_to_xyz` (Sphere/Ellipsoid) for arc sampling, so wrapping is handled for cell assignment; but the **PSF** still uses raw lon/lat `dx`,`dy`.
- [ ] **Split processing into Nadir (fast) vs Distorted/Limb (safe) paths** — not present.
- [ ] **Horizon / limb clipping** (`dot(target_vec, sc_vec) <= 0`) — not present.

## 8. Multi-resolution reuse & the unweighted master index

- [x] **Compute geometry once, reuse for many resolutions** — ADR-015: compute at `nside_max` once, derive lower nsides by NEST bit-shift (`_aggregate_healpix`). (In-memory within a single `run()`, not a disk master index.)
- [~] **One sidecar file per resolution + two-pass workflow** — one file per nside is done (`build_output_path`). But the chat's *"second pass reads the n512 master from disk, applies PSF, writes weighted n8"* is **not** done: PSF is applied inline during assignment; lower nsides are derived in-memory within a single `run()`.
- [ ] **Downstream convention** (`psf_weight` column present → apply; absent → binary) — **not** wired. Sidecar may carry a `weight` column, but `aggregate.py` reads sidecar with `columns=["source_id","healpix_id"]` (`aggregate_by_sidecar`) and ignores `weight`. Weights are computed/written but **not consumed downstream**.
- [x] **Sidecar generation and scientific aggregation strictly separated; sidecars persist on disk** — separate modules (`sidecar.py` vs `aggregate.py`/`accumulator.py`/`finalize.py`); sidecars written to disk and reused.
- [ ] Alternative: true variable multi-resolution (MOC) sidecars via `mocpy` — not used.
- [x] **ADR-015 disk-master vs in-memory** — ADR-015 explicitly chose **in-memory within a single `run()`** over the chat's disk-master-index variant; the code matches ADR-015. The disk-cache reuse idea from the chat is intentionally not implemented.

## 9. Pluggable PSF architecture (expert review refinements → final design)

- [~] **Do not hardcode NSIDE pairs** — multi-res is generalized via `nside_max`/`nside_target` bit-shift (ADR-015), but there is **no** `nside_sub`/`nside_analysis` PSF-subgrid concept.
- [x] **Generalize bit-shift** — `shift = 2 * int(log2(nside_max // nside_target))`; general (not hardcoded to 512→8).
- [ ] **Do not evaluate the Gaussian in raw lat/lon** — PSF still evaluated in raw lon/lat `dx`,`dy`; no tangent-plane (ENU) and no unit-sphere angular separation.
- [ ] **PSF on the unit sphere** (`θ = arccos(clip(u·b,-1,1))`) — not implemented.
- [ ] **Boresight source** (known intercept or spherical-centroid fallback) — no boresight concept; PSF uses geometry centroid for `dx`,`dy`.
- [ ] **Visibility separation** (`Detector PSF × Visibility × cell`) — not separated; PSF evaluated directly on assignment cells.
- [ ] **Sparse stencil (~3σ)** — not implemented.
- [ ] **Precompute by angular offset (lookup table)** — not implemented.
- [ ] **Two accuracy modes** (Fast cell-center vs Accurate cell-integrated) — only centroid evaluation exists; no accurate/integrated mode.
- [ ] **Adaptive subpixel refinement (quadtree)** — not implemented.
- [ ] **Two geometry backends + `auto`** (`tangent`/`spherical`, switch ~1–2°) — not implemented.
- [~] **Pluggable PSF model** — a `PSF` base class, `GaussianPSF`, and a `PSF_REGISTRY` exist in `sidecar.py`; no Airy, no measured PSF, no wavelength/anisotropic support.
- [ ] **Module layout** (`healpyxel/geometry/sidecar.py`, `healpyxel/psf/`, `healpyxel/integration/`) — PSF lives inline in `sidecar.py`; no `psf/` or `integration/` packages.
- [ ] **Target API shape** (`generate_sidecar` / `weight_sidecar(...)` / `aggregate(...)`) — not present; PSF is passed as inline CLI args / function kwargs to sidecar.
- [~] **Base abstraction** — there is a `PSF.__call__(dx, dy)` base + `GaussianPSF` + registry, but the signature is `(dx, dy)` in lon/lat degrees, **not** `evaluate(theta_rad)` on angular separation.

## 10. Multi-resolution sidecar → single file (see also §11)

- [x] **Downscale via bit-shift** (aggregate `n_high` cells to `n_low` parents without spatial query) — ADR-015, `_aggregate_healpix`.
- [x] **Scaling up is not possible** — documented: only downscaling works.
- [~] **PSF caveat / anchor to highest res** — ADR-015 sums child weights when aggregating (preserves total weight per source), which addresses the caveat for the current centroid-weight model; it is not the unit-sphere subgrid weighting from the chat.

## 11. Output & export formats

- [x] **GeoParquet core; aggregated output as pure tables indexed by `healpix_id`** — yes (aggregate/accumulator outputs).
- [x] `healpyxel.geospatial` adds a geometry column on demand — `healpix_to_geodataframe` / `save_healpix_to_geoparquet` / boundary caching exist.
- [~] **DuckDB showcase** — DuckDB is used **internally** (aggregate data loading `--use-duckdb`, sidecar geo-statistics), but there is no dedicated showcase/example file demonstrating SQL over the sidecars.
- [~] **COG export — leave to GDAL** — `export_healpix_to_geotiff` writes a **regular GeoTIFF** (GTiff, deflate, rasterio) via nearest-neighbor equirectangular resample; it is **not** a Cloud-Optimized GeoTIFF, and there is no `gdal_rasterize`/COG tutorial.
- [ ] **Native `to_zarr()` / GeoZarr exporter** — not implemented.
- [~] **Two export paths** — *visual/2D* GeoTIFF path exists; *analysis/GeoZarr* path does not.
- [ ] **Dense GeoZarr does not need resampling** — n/a (no Zarr support).
- [ ] **GeoZarr vs COG documentation** — not documented.

## 12. Multiple HEALPix levels in a single GeoZarr file

All items not implemented — there is no Zarr / GeoZarr support in the package:
- [ ] Zarr Groups / `multiscales` per-resolution arrays.
- [ ] `xarray` DataTree + `xdggs` integration.
- [ ] Downstream coarse-level / fine-level byte-range reads.
- [ ] QGIS caveat handling / regular 2D GeoZarr visual path.

## 13. Interoperability — UXarray / xdggs / mocpy / healpy

- [ ] **UXarray** UGRID mesh conversion — not used.
- [ ] **xdggs / mocpy** for hierarchical multi-resolution — not used.
- [ ] **Contribute UXarray Parquet read** (`ux.read_healpyxel()`) — not done.
- [ ] **healpy vs healpyxel relationship documented** — `healpy` is used as the engine (README/philosophy/`00_CONSTRAINTS`), but there is no explicit comparison/interop section in the docs.

## 14. Gallery & showcase datasets

Goal: a documentation gallery with mosaicked data showing the package off; already have MESSENGER MASCS + MLA (Mercury). Prefer point / single-polygon data from spherical bodies with physically meaningful units, single-measurement granules to convey computation time on modest machines.

- [ ] **LRO Diviner (Moon)** — brightness/bolometric temperature (K), radiance — not added.
- [ ] **Kaguya/SELENE Spectral Profiler SP (Moon)** — reflectance I/F — not added.
- [ ] **Kaguya/SELENE Laser Altimeter LALT (Moon)** — topography — not added.
- [ ] **LRO LOLA (Moon)** — laser altimeter topography — not added.
- [ ] **MGS MOLA (Mars)** — topographic elevation — not added.
- [ ] **Dawn VIR (Ceres & Vesta)** — reflectance/emissivity — not added.
- [ ] **Recommended showcase strategy** — the package is documented as *"Developed for MESSENGER/MASCS"*; the Moon-multi-instrument overlay showcase is **not** built (only an `examples/cli_regrid_sample_50k.sh` sample exists).
- [ ] **Per-dataset documentation** (name, target, curator, URL, partitioning, levels, units, granule size) — not done.
- [ ] **Showcase the decoupled two-pass advantage** on a modest machine — not done.

---

## Summary

**Mostly done (sidecar core):** spherical queries replacing `sjoin` (§1), long-format GeoParquet + dask parallelism (§1), body geometry backend (ADR-013, §2), multi-resolution NEST bit-shift (ADR-015, §8/§10), separation of sidecar vs aggregation (§8), pure-table outputs + `.geospatial` geometry layer (§11).

**Partially done:** a PSF exists but in the *old* centroid/lat-lon `(dx,dy)` form (§2/§3/§9), DuckDB used internally (§11), regular GeoTIFF export (§11).

**Not done (open ideas):** the entire pluggable/unit-sphere PSF redesign (§4/§5/§6/§9 — sub-pixel integration, boresight, sparse stencil, lookup table, adaptive quadtree, geometry backends), edge-case handling (§7), downstream `weight` application (§8), COG/GeoZarr/multi-level-Zarr exports (§11/§12), UXarray/xdggs/mocpy interop (§13), and the gallery datasets (§14).

_Last verified: 2026-08-15 on `dev` HEAD `66929c3`._
