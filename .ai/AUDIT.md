# AUDIT: OOM and Memory Patterns

Date: 2026-08-14
Status: Findings documented; fixes applied to sidecar.py only.

## 1. Confirmed OOM Bug (Fixed)

**File:** `healpyxel/sidecar.py`
**Function:** `compute_geo_statistics()`
**Pattern:** Whole-file parquet reads for sampling/column detection.

The original implementation used `gpd.read_parquet(input_path, max_rows=100)` and `gpd.read_parquet(input_path, max_rows=sample_size)` to detect columns and compute statistics. Despite `max_rows`, `geopandas.read_parquet` still materializes the full row-group metadata and can load large swaths of wide files into memory, causing OOM on files with many spectrum/reflectance columns.

**Fix applied:**
- Replaced whole-file sampling with schema-footer-only detection via `_parquet_column_names()` (reads only the parquet footer metadata).
- Added `columns` parameter to `_read_input_lazy()` so the sidecar pipeline only loads the exact columns needed (`geometry` or `[lon_col, lat_col]`).
- This eliminates the OOM risk for wide input files during sidecar generation.

## 2. Other Potential OOM Patterns Found

### 2.1 `inspect.py` — Verbose full-file read (Medium Risk)

**Lines:** 155, 208
**Code:**
```python
df_sample = pd.read_parquet(file_path, engine="pyarrow").head(1)
# ...
df_full = pd.read_parquet(file_path, engine="pyarrow")
```

**Context:** In verbose mode, `inspect.py` reads the entire parquet file to count nulls in geometry columns. This is user-initiated (`--verbose`), but on wide files it can OOM.

**Recommendation:** Use DuckDB or column selection for the null-count pass. Only read geometry-like columns.

### 2.2 `accumulator.py` — Unfiltered input data reads (Medium Risk)

**Lines:** 900, 1123
**Code:**
```python
df = pd.read_parquet(input_path, engine='pyarrow')  # line 900
new_data = pd.read_parquet(input_path)               # line 1123
```

**Context:** The accumulator loads the full input data file. If the input is a wide parquet (e.g., MASCS spectra with hundreds of wavelength columns), this can consume significant memory.

**Recommendation:** Pass `columns` from config to restrict to the value columns actually needed for accumulation.

### 2.3 `metadata.py` — Full-file validation read (Low-Medium Risk)

**Line:** 539
**Code:**
```python
df = pd.read_parquet(path)
```

**Context:** `load_with_validation()` reads the entire file to validate structure. For large aggregate outputs this is bounded, but for sidecar outputs it could be wide if the original input was wide and columns were not pruned.

**Recommendation:** If validation only needs `healpix_id` and `source_id`, read with `columns=['healpix_id', 'source_id']`.

### 2.4 `geospatial.py` — Cache reads (Low Risk)

**Lines:** 232, 605
**Code:**
```python
pd.read_parquet(cache_file).set_index('healpix_id')
```

**Context:** These read cached HEALPix boundary files. The cache size is bounded by `12 * nside^2` rows (fixed geometry table), so memory usage is predictable and small.

**Status:** Acceptable as-is.

### 2.5 `aggregate.py` — Sidecar read in CLI (Low Risk)

**Line:** 1275
**Code:**
```python
sidecar = pd.read_parquet(sidecar_path)
```

**Context:** The sidecar output is already narrow (`source_id`, `healpix_id`, optional `weight`). No OOM risk.

**Status:** Acceptable as-is.

## 3. Summary

| File | Line(s) | Pattern | Risk | Status |
|------|---------|---------|------|--------|
| `sidecar.py` | `compute_geo_statistics` | Whole-file read for sampling | **High** | **Fixed** |
| `inspect.py` | 155, 208 | Full-file read in verbose mode | Medium | Documented |
| `accumulator.py` | 900, 1123 | Unfiltered input data read | Medium | Documented |
| `metadata.py` | 539 | Full-file validation read | Low-Medium | Documented |
| `geospatial.py` | 232, 605 | Bounded cache reads | Low | Acceptable |
| `aggregate.py` | 1275 | Narrow sidecar read | Low | Acceptable |

## 4. Recommended Follow-ups

1. **`inspect.py`**: Add `columns` parameter to the verbose null-count read, or use DuckDB for the count.
2. **`accumulator.py`**: Thread `columns` config through to `pd.read_parquet` calls so only needed value columns are loaded.
3. **`metadata.py`**: Restrict `load_with_validation()` to `['healpix_id', 'source_id']` when possible.
