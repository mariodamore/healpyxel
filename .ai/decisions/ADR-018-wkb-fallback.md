# ADR-018: WKB fallback + --correct-geometry for broken spatial partition metadata

- **Status:** Active
- **Date:** 2026-08-13
- **Author:** session 2026-08-13

## Context

Parquet files written by duckdb (or other tools) may include a `geo:` metadata key
but with broken spatial partition metadata.  When `dask_geopandas.read_parquet()`
encounters this, it raises a `ValueError("Spatial partition metadata mismatch")`.

The existing sidecar reader (`_read_input_lazy`) had a 3-tier cascade:
1. `dask_geopandas.read_parquet()` — fails with broken metadata
2. `dask.dataframe.read_parquet()` — succeeds but reads geometry columns as raw WKB bytes
3. Raise IOError

Downstream, `process_partition()` only knows two workflows:
- scalar lon/lat columns (`hasattr(gdf, 'geometry')` is False for plain dask)
- geometry column (`hasattr(gdf, 'geometry')` is True for GeoDataFrames)

When tier 2 falls back, neither workflow is available → the fatal
"Partition has no usable lon/lat columns and no geometry" error.

## Decision

Two complementary fixes:

1. **WKB decode fallback** (in `_read_input_lazy`): after tier 2 reads with plain
   dask, automatically decode WKB bytes to shapely objects via `map_partitions`
   + `shapely.from_wkb`.  If geometry mode was selected but plain dask was read,
   extract scalar lon/lat from decoded geometries and feed them as `_lon`/`_lat`
   columns to the efficient scalar workflow.

2. **`--correct-geometry` CLI flag** (in `healpyxel_inspect`): a one-time rewrite
   command that reads the parquet, decodes WKB, and writes with proper GeoParquet
   metadata via `geopandas.GeoDataFrame.to_parquet(schema_geometry=True)`.  This
   eliminates the per-run overhead entirely.

## Alternatives Considered

- **Silent error with user-instruction hint only**: rejected because the fix is
  cheap (microseconds per geometry) and transparent — users shouldn't need to
  manually fix their input files for the pipeline to work.
- **Always extract lon/lat from geometry**: rejected because it adds overhead for
  files where `dask_geopandas` works natively (e.g. the 150k sample).
- **Drop geometry heuristics entirely**: rejected because geometry workflows are
  central to the fuzzy-mode design.

## Consequences

- Positive: pipeline works transparently on WKB-encoded files; users are informed
  of the permanent fix via `--correct-geometry`.
- Positive: no performance regression for files with valid spatial metadata.
- Negative: the WKB decode adds a small per-run cost (seconds vs minutes of HEALPix
  computation — negligible in practice).
- Negative: `_convert_wkb_columns_to_geometry` assumes the first geometry-like
  column is the primary one.  Multi-geometry-column files are rare in this domain.

## Waiver

None.
