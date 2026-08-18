# IDEAS_PLANETL — Ideas for the planETL companion package

Source: `.ai/i-wrote-this-package-healpyxel.md` (2026-07-24 → 2026-08-14).
Purpose: a de-duplicated design brief for a new package **planETL** (Planetary Extract, Transform, Load), conceived as the data-ingestion companion to **healpyxel**.
Each item is a checkbox. Most of this is greenfield — nothing is built yet.

**Positioning:** `planETL` = the *data porter*; `healpyxel` = the *grid engine*.
```
planETL (stream/scan raw archives → clean local GeoParquet)
   → healpyxel Pass 1 (unweighted sidecars)
   → healpyxel Pass 2 (PSF weights / bit-shift to analysis grid)
```

---

## 1. Concept & scope

- [ ] **Cloud-native, resumable planetary data normalisation.** Automated streaming ingestion layer for raw planetary track/footprint data (laser altimeters MOLA/MLA/LALT; spectrometers MASCS/VIR/Diviner/Kaguya-SP).
- [ ] Solves the classic "PDS data-wrangling bottleneck": convert chaotic multi-format remote/local archives into structured, optimized **GeoParquet** datasets ready for parallelized spatial analysis.
- [ ] **Decoupled scope**: `planETL` owns I/O and file parsing only; `healpyxel` owns math/grid/PSF. Keeps both lightweight and focused (good for PlanetaryPy affiliation review).
- [ ] **Advantage over conventional pipelines**: can start, stop, and recover a long ingestion run without storing the entire raw dataset locally; you only need the GeoParquet for analysis anyway; a file's presence means it was processed.

## 2. Core architecture — fsspec + geopandas + dask + geoparquet

- [ ] **`fsspec` as a virtual filesystem** to treat remote web servers (PDS/JAXA/ESA) like a local folder; stream bytes into RAM buffers instead of downloading 500 MB files; decompress mid-air (saves local disk).
- [ ] **`geopandas` as spatial standardizer**: assign the correct target planetary CRS (spherical datum, e.g. Moon `+a=1737400`, Mars `+a=3396190`, Ceres `+a=473000`) so downstream math respects the true body sphere.
- [ ] **Store to GeoParquet as the permanent caching layer**: compressed, spatial metadata in the file header, reloadable instantly without re-parsing text.
- [ ] **`dask` as the conductor**: split the file list into small batches, distribute across CPU cores (each streams via fsspec, parses via geopandas, writes small geoparquet); keeps RAM flat and avoids OOM on 1M footprints.
- [ ] **Out-of-core scaling via Arrow/Dask + DuckDB** pushdown on the resulting partitioned GeoParquet.
- [ ] **Prefer multiple partitioned GeoParquet files over one monolithic file** (partition by orbit / mission phase / date / coarse HEALPix cell) to keep files small and enable chunked parallel processing.

## 3. Unified configuration dictionary template

- [ ] A single schema describing each instrument: name, target, `protocol`, `base_url`, `url_pattern` (with `{year}/{month}/{day}/{orbit}` placeholders), `file_format` (ascii_fixed / ascii_csv / pds_binary / pds3_cube), `parsing_kwargs`, `spatial_mapping` (point lon/lat cols or polygon vertices), `physical_crs`, `primary_metric`.
- [ ] Example keys: `lro_diviner`, `kaguya_sp`, `mgs_mola`, `dawn_vir`, `kaguya_lalt` (see HEALPYXEL doc §14 for the dataset matrix).
- [ ] A `PlanetaryDataStreamer` class that formats the dynamic URL from `url_pattern` + params, opens it via `fsspec.filesystem(protocol)`, parses by `file_format`, standardizes geometry, and returns a uniform target-aware GeoDataFrame.
- [ ] Binary formats (MOLA `.B`, Dawn `.QUB`) route to external PDS parsers (see §6 `pdr`).

## 4. Dual-source strategy — remote stream AND local scan (no manifest required)

- [ ] **`.stream()`**: fsspec-based remote ingestion (cloud).
- [ ] **`.scan()`**: native support for locally stored archives WITHOUT a manifest — required so users who already have partial datasets (or are building a new archive) aren't forced to re-download or invent an index.
- [ ] Local scanning uses `pathlib` recursive **glob** (e.g. `**/*.LBL`) instead of fixed URLs; match labels to data arrays; process identically to remote files.
- [ ] This makes `planETL` a universal data-normalization engine, not just a download tool.

## 5. Idempotency, incremental processing & checkpoints

- [ ] **Pull-based checkpointing / "state-of-file" recovery**: use the presence of a local `.parquet` file as an atomic completion marker; on restart, skip completed files in milliseconds, resuming exactly where it left off (crash/battery/network safe).
- [ ] Pseudo-logic: for each remote URL → compute local path from naming convention → `if local_path.exists(): continue` → else stream, parse, `to_parquet` (atomic write).
- [ ] **Local manifest caching for fast startup**: read the root `manifest.tab` / `cumindex.tab` / `collection.xml` once into a DataFrame (via fsspec) instead of recursively crawling HTML; **differential sync** against the local GeoParquet dir gives an instant progress bar / missing-file list.
- [ ] **Cleanup**: delete raw PDS `.LBL`/`.TAB`/binary files immediately after generating clean GeoParquet to keep the local drive small.

## 6. PDS parsing — pdr (Planetary Data Reader)

- [ ] Wrap **`pdr.read()`** to automatically decode PDS3/PDS4 binary and ASCII tables into pandas DataFrames / NumPy arrays — eliminates custom parsers for MOLA `.B`, Dawn `.QUB`, etc.
- [ ] `pdr` expects a companion label (`.LBL` / `.xml`); `planETL` caches remote label+data locally first, then `pdr` reads, geopandas converts, raw files deleted.
- [ ] `planETL` becomes the standard streaming/caching frontend for `pdr`.

## 7. PDS discovery — Peppi (PDS4 Registry)

- [ ] **`pds.peppi` (NASA PDS Engineering Node) as the discovery frontend.** `pdr` parses a file you point at; `peppi` *finds* the files by querying the central PDS4 Registry API.
- [ ] Replace hardcoded URL patterns with a pythonic query client: `pep.Products(client).has_target("Mars").has_instrument_host("MGS").observationals()` → `to_dataframe()` → extract `label_url` / `data_url` lists → feed into the streamer/parser.
- [ ] Advantages: no manual web scraping of nested directory indexes; dynamic time-slice searches; PDS4-compliant by default (future-proof as missions migrate to PDS4).

## 8. Package / repository structure

- [ ] Full-blown Python package (modern `pyproject.toml`, setuptools/hatchling). Proposed layout:
  ```
  planetl/
  ├── .github/workflows/        # CI/CD (tests + PyPI)
  ├── docs/                     # Sphinx/MkDocs for readthedocs
  ├── examples/                 # notebooks: remote vs local pipelines, DuckDB demo
  ├── planetl/
  │   ├── __init__.py           # top-level API (petl.stream / petl.scan)
  │   ├── config/profiles.py    # unified dataset config dictionary
  │   ├── core/crawler.py       # remote HTML/manifest scanning + local dir listing
  │   ├── core/pipeline.py      # orchestrator (Dask-parallel incremental loop)
  │   ├── io/remote_stream.py   # fsspec connectors
  │   ├── io/local_storage.py   # pathlib, checkpoint checks, caching
  │   ├── parsers/pdr_interface.py  # pdr.read() bridge
  │   ├── parsers/geo_converter.py  # dataframes → GeoDataFrames with target CRS
  │   └── utils/checkpoint.py   # filesystem-state resume helper
  ├── tests/
  ├── LICENSE                   # BSD-3/MIT for PlanetaryPy compatibility
  ├── README.md
  └── pyproject.toml
  ```
- [ ] Conceptual API:
  ```python
  import planetl as petl
  remote = petl.pipeline.DataEngine(instrument="lro_diviner")
  parquet_dir = remote.stream(year=2026, month=7)
  local  = petl.pipeline.DataEngine(instrument="mgs_mola")
  parquet_dir = local.scan(source_dir="/Volumes/ExtDrive/MOLA_RAW/")
  # hand parquet_dir straight to healpyxel
  ```

## 9. Ecosystem / PlanetaryPy integration

- [ ] **PlanetaryPy affiliation** — `planETL` + `healpyxel` fill a gap: no community-standard for high-performance cloud-native spatial indexing / multi-instrument aggregation. They slot in after `pdr` (ingestion) and before scientific modeling.
- [ ] **Direct dependencies on existing PlanetaryPy packages**: `pdr` (parsing), `spiceypy` (SPICE kernels for exact 3D boresight/spacecraft geometry → feeds healpyxel unit-sphere PSF), `pvl` (crawl/parse/cache PDS label params for lightweight manifest caches).
- [ ] **Ideas exchange** with the PlanetaryPy Technical Committee (same organization): standardize GeoParquet/GeoZarr metadata conventions; propose `planETL` as the standard streaming/caching frontend for `pdr`.
- [ ] Keep `planETL` focused on I/O and `healpyxel` on the grid — a prerequisite for clean affiliation review.

## 10. Storage & query showcase

- [ ] **GeoParquet as the base file type** (chosen after seeing DuckDB operate on them at scale).
- [ ] **DuckDB showcase** in the examples dir: `INSTALL spatial; LOAD spatial;` then SQL over the partitioned GeoParquet with filter/projection pushdown and WKB geometry parsing (e.g. group-by-orbit elevation stats bounded by lon/lat box) — proves performance at scale.

## 11. Export formats — explicitly OUT of core scope

- [ ] **GeoZarr / COG export is a nice-to-have, NOT the core of planETL scope.** Keep `planETL` on vector-track → GeoParquet. Final dense science-cube exports (GeoZarr / COG) belong to healpyxel's downstream/output layer. (See HEALPYXEL doc §11–12.)
- [ ] GeoZarr = N-dimensional chunked array (cloud-native raster, xarray/Dask); COG = tiled 2D GeoTIFF with overviews (GDAL/GIS). Different paradigms; neither is a core planETL deliverable.

---

## Open questions to resolve before building

- PDS label edge cases: how cleanly does `pdr` handle fragmented legacy PDS3 multi-table labels streamed via fsspec buffers vs local file descriptors?
- Metadata propagation: embed instrument metadata (aperture, FWHM, bands) in the GeoParquet schema, or keep a separate global JSON sidecar profile?
- Partitioning keys: is partitioning purely by `orbit` robust enough for cloud queries, or should `planETL` compute a coarse (e.g. nside=4) HEALPix cell string as an explicit directory partition key?
- Boresight metadata: how is boresight / 3D geometry stored or accessed in the pipeline (relevant to feed healpyxel's unit-sphere PSF)?
- Naming convention for sidecar files on disk (e.g. `sidecar_N8.parquet`) that both `planETL` and `healpyxel` agree on.
