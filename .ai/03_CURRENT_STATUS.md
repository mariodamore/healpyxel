# Current Status

## NOW
- [x] **ADR-015 multi-resolution sidecar** — DONE (verified 2026-08-15 on HEAD `66929c3`): `sidecar.run()` computes `nside_max` once, derives lower nsides via `_aggregate_healpix` NEST bit-shift; `--no-multi-res-optimize` disables. See ADR-015.
- [x] **ADR-019 exhaustive FOV coverage** — DONE (2026-08-16): Added `exhaustive=True` mode to `process_partition` using `healpy.query_disc` + exact `shapely.intersects()` filtering. Fixed latent bug in `get_healpix_cell_geometry()` (Cartesian vs theta/phi). 5 new tests, all 456 tests pass.
- [ ] **83_example_accumulation.py** — Make the accumulation validation notebook self-contained so it can leave ADR-011 exclusion from published docs
- [ ] **TDigest accuracy documentation** — Add ~1e-3 accuracy bound note to README/notebook for downstream users comparing streaming vs batch percentile output

## NEXT
- [ ] **ADR-015 equivalence tests** — Add tests comparing `_aggregate_healpix` (bit-shift) output to a full recompute for identical output (multi-nside, no-PSF case). Implementation is in place; equivalence coverage is not yet explicit.
- [ ] **PSF Phase A — pluggable/unit-sphere redesign** — The current PSF is the legacy centroid/lat-lon `(dx,dy)` form (in `sidecar.py`). Open items (see `IDEAS_HEALPYXEL.md` §4/§5/§6/§9): sub-pixel integration via `nside_high`, unit-sphere angular-separation `PSF(θ)`, boresight source, sparse stencil, two accuracy modes, tangent/spherical backends.
- [ ] **Downstream `weight` consumption** — Sidecar `weight` (PSF) column is written but `aggregate.py` reads only `[source_id, healpix_id]`; weights are not applied in aggregation (see `IDEAS_HEALPYXEL.md` §8).
- [ ] **COG / GeoZarr / multi-level-Zarr exports** — Only a regular (non-COG) GeoTIFF exists (`export_healpix_to_geotiff`); no Zarr/GeoZarr exporter (`IDEAS_HEALPYXEL.md` §11/§12).
- [ ] **Scale testing** — Validate accumulator performance at 50M observation scale
- [ ] **FITS Export** — Enhanced FITS format support for planetary science archives

## KNOWN_ISSUES

### Bugs
- None currently documented

### Technical Debt
- **PSF in raw lat/lon** — PSF evaluated via centroid-to-centroid `(dx,dy)` degrees; no unit-sphere/tangent handling, no cell-area integration (see `IDEAS_HEALPYXEL.md` §2/§4)
- **Legacy docs** — `PROJECT_PLAN.md` and `IMPLEMENTATION_PLAN.md` should be archived
- **Float-to-int conversions** — Several locations in geospatial.py use `float(x)` where `x` is already float
- **Exhaustive mode margin tuning** — The 1° angular margin in `query_disc` is a reasonable default but may need empirical calibration for specific instruments/FOV sizes.

_Last updated: 2026-08-16_
