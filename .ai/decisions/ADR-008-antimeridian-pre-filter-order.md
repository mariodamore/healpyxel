# ADR-008: antimeridian.fix_polygon must run before bounds pre-filter

- **Status:** Active
- **Date:** 2026-07-10
- **Author:** session 2026-07-10

## Context

`healpyxel_sidecar` maps source geometries to HEALPix cells. In geometry-based (fuzzy) mode, `process_partition()` first validates all coordinates against a longitude convention bound (`[-180, 180]` or `[0, 360]`), then — only after passing — calls `antimeridian.fix_polygon()` to repair antimeridian-crossing geometries.

The user reported that at higher nside resolutions (e.g., nside=32 vs nside=4), the number of empty HEALPix cells grows substantially. In fuzzy mode, a geometry that occupied a coarse cell should populate all finer sub-cells. This degradation signals that valid observations are being silently lost before reaching any cell assignment.

## Decision

Move `antimeridian.fix_polygon()` to run **before** the bounds pre-filter. Validate the fixed geometry; if the fixed geometry is still invalid, fall back to checking the original. Only drop the observation if both versions are invalid.

This ensures geometries whose coordinates fall slightly outside the boundary (e.g., a vertex at lon=181°) get repaired first and can still be assigned to HEALPix cells.

## Alternatives Considered

- **Relax the bounds check** (e.g., expand by a tolerance): rejected because it's a band-aid — any tolerance larger than zero admits garbage data, and the real fix is to normalize coordinates properly.
- **Post-hoc recovery** (try to assign then fill empty cells from neighbours): rejected because it cannot recover the original observation's data values; the data is already gone, only the cell label could be inferred.
- **Drop the pre-filter entirely**: rejected because we still need to reject coordinates that are trivially out of range or non-finite; `compute_healpix_ids_from_lonlat` will wrap lons via `np.mod`, which could silently place data in the wrong hemisphere.

## Consequences

- Positive: valid observations near the antimeridian are no longer silently dropped; HEALPix cell coverage at high nside is complete for data that should be there.
- Positive: MultiPolygon geometries with one bad component can still contribute from their valid components after antimeridian repair.
- Negative: one more shapely operation per geometry (`antimeridian.fix_polygon`), but it was already being called in the original code; the change just reorders it.
- Negative: `fix_polygon` can occasionally produce empty geometries from degenerate input; the fallback to the original + validity check handles this case.

## Waiver

n/a
