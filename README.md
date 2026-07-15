# healpyxel

HEALPix-based spatial aggregation for planetary science data — streaming accumulation, sidecar generation, and batch aggregation in one package.

## What is HEALPix?

**HEALPix** (Hierarchical Equal Area isoLatitude Pixelization) partitions a sphere into pixels of equal surface area. Unlike rectangular projections, every pixel is the same size, resolution is hierarchical ($NSIDE$), and computation is efficient — making it ideal for statistical analysis across the entire globe.

## The Problem: Data Distortion & Scale

In planetary science, data arrives as scattered points, tracks, or footprints from spectrometers and altimeters. Traditional approaches face two hurdles:

1. **Projection Bias:** Standard grids distort the poles, making global surface calculations mathematically biased.
2. **The Memory Wall:** Modern missions generate billions of points. Loading a full high-resolution map into RAM to update it is often impossible.

`healpyxel` solves this by treating the sphere as a modern data engineering target rather than just a geometric grid.

## Who is this for?

- **Remote Sensing & Planetary Science:** Instruments like 1-point spectrometers (MESSENGER/MASCS), laser altimeters, push-broom spectrometers.
- **The Sidecar Workflow:** Index your data without modifying source files. `healpyxel` creates lightweight sidecar files mapping GeoParquet rows to HEALPix cells.
- **Large-Scale Data Engineering:** Process TB-scale datasets using Split-Apply-Combine on GeoParquet.
- **Streaming & Incremental Ingestion:** Update global maps as new data arrives without reprocessing the entire archive.

## Who is this NOT for?

- **High-Resolution 2D Imagery:** For dense image-to-HEALPix reprojection (CCD frames), use [reproject](https://reproject.readthedocs.io/) or [astropy-healpix](https://astropy-healpix.readthedocs.io/).
- **Standard Xarray/Dask Unstructured Grids:** For deep integration with general unstructured meshes, use [UXarray](https://uxarray.readthedocs.io/).
- **MOC & LIGO workflows:** For gravitational wave IO formats, check out [mhealpy](https://mhealpy.readthedocs.io/).

## How it Works: The Sidecar Strategy

`healpyxel` implements a **Split-Apply-Combine** pattern tailored for spherical geometry:

1. **Split** — Instead of rewriting your heavy raw data, `healpyxel` generates a small Parquet file containing only the index of the original data and its corresponding `healpix_id`.
2. **Apply** — Join this sidecar with any column in your original dataset to calculate statistics (Mean, Std Dev, Count) per cell.
3. **Combine** — Results are combined into a final HEALPix map or a streaming accumulator.

**Pro-Tip:** For multi-pixel sensors (push-broom spectrometer), flatten your 2D acquisitions into a 1D tabular format (one row per spatial pixel) before saving to GeoParquet. `healpyxel` is optimized to ingest these "shredded" lines at high speed.

## Install

```bash
pip install healpyxel
```

Optional extras: `geospatial`, `streaming`, `viz`, `dev`.

## Quick Start

```bash
healpyxel_sidecar  --input data.parquet --nside 64 --mode fuzzy
healpyxel_aggregate --input data.parquet --sidecar-dir . --columns val --aggs median
healpyxel_to_geoparquet -a output.parquet -d . -l -180_180
```

For streaming/incremental workflows:

```bash
healpyxel_accumulate --input day001.parquet --columns col --state-output state.parquet
healpyxel_finalize   --state state.parquet --output mosaic.parquet --densify
```

## Documentation

Full tutorials, API reference, and examples: https://mariodamore.github.io/healpyxel/

## License

Apache-2.0
