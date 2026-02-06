# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Setup GitHub Actions CI/CD.
- Expand visualization capabilities.
- Add more planetary coordinate systems.
- keep the original CRS, make cler we currently accept only coordinates in. Add CRS conversion capability?
- Performance optimization for large datasets.
- Finalize the Grid Cell PFS to extend the strict/fuzzy intersection logic. 

## [0.2.0] - 2026-02-06

### Added

#### Accumulator Module
- `validate_accumulator_sidecar_compatibility()` function to verify state/sidecar metadata consistency
- `_in_ipython_kernel()` helper to detect notebook execution context
- Comprehensive metadata validation when loading state files
- State files now embed full HEALPyxel metadata in parquet schema
- Enhanced `load_state()` to return tuple `(state, metadata)` instead of state only
- Automatic compatibility checks between accumulator state and sidecar files (nside, mode, order validation)

#### Aggregate Module
- `print_parquet_schema()` utility for schema inspection
- `print_sidecar_summary()` for formatted sidecar file listings
- `CustomFormatter` class for improved CLI help text formatting (120-char width)
- `_is_interactive_session()` helper to detect IPython/Jupyter contexts
- Extensive CLI help documentation with usage examples
- Densification marker in output filenames (`-densified` suffix)
- Improved sidecar collection logic with guards against false positives
- Overwrite prompt with `--yes` flag for batch mode operation
- Expanded CLI documentation with domain guidance for all parameters

#### Finalize Module
- `_normalize_load_state_result()` helper for backward compatibility
- `_in_ipython_kernel()` helper for notebook detection
- Enhanced metadata handling with embedded HEALPyxelxMetadata in output files
- Auto-detection of nside from state file metadata

#### CLI Module
- **NEW:** `cache_cli()` entry point for `healpyxel_cache` command with full cache management:
  - List cached grids
  - Generate new caches
  - Verify cache integrity
  - Clean/remove caches
  - Display cache info
  - Initialize user configuration
- XDG Base Directory Specification compliance
- Cache precedence: CLI args → `HEALPYXEL_CACHE` env var → `XDG_CACHE_HOME` → `~/.cache` fallback
- Verification reports cache integrity (completeness, NaN checks, valid healpix_id ranges)
- Non-zero exit codes on verification failures (CI/CD friendly)

#### Geospatial Module
- **NEW:** Comprehensive caching infrastructure with XDG compliance:
  - `_get_cache_dir()` / `_get_config_dir()` with full precedence resolution
  - `_cache_key()` for consistent cache filename generation
  - `_load_cached_boundaries()` / `_save_cached_boundaries()` for boundary cache persistence
  - `manage_healpix_cache()` domain logic for cache operations
- **NEW:** `init_user_config()` creates default `~/.config/healpyxel/settings.ini`
- **NEW:** `_load_user_settings()` reads user runtime preferences
- **NEW:** `export_healpix_to_geotiff()` for GeoTIFF export with rasterio
- Enhanced `healpix_to_geodataframe()` with cache support:
  - `cache_mode` parameter: 'use', 'require', 'off'
  - Cache-aware pixel computation (avoids recomputing cached grids)
  - Handles partial cache hits (compute missing pixels only)
  - Large grid warnings (nside ≥ 1024) with memory estimates
- Spherical coordinate conversion utilities:
  - `_spherical_to_lonlat()` for ICRS → geographic conversion
  - `_lonlat_to_polygons()` for polygon generation from corners
- Comprehensive test suite:
  - `test_xdg_precedence()` - 6 precedence resolution scenarios
  - `test_cache_key_generation()` - filename consistency
  - `test_spherical_conversion()` - coordinate transform validation
  - `test_cache_mode_require_missing_cache()` - cache requirement enforcement
  - `test_cache_verification_complete/missing/incomplete/corrupt_nans()` - integrity checks

#### Module Index
- Added 40+ new function entries for cache management and geospatial utilities
- Added test module entries: `conftest`, `test_aggregate`, `test_core`

### Changed

#### Accumulator Module
- **BREAKING:** `load_state()` now returns `(state, metadata)` tuple instead of dict only
- **BREAKING:** `save_state()` signature changed: requires `HEALPyxelxMetadata` instead of optional dict
- State files now include comprehensive processing metadata in parquet schema
- Enhanced logging with compatibility validation messages
- IPython kernel detection prevents CLI execution in notebooks

#### Aggregate Module
- **BREAKING:** Default `min_count` changed from 0 to 1
- `generate_output_filename()` now accepts `densified` parameter for filename markers
- Enhanced dry-run summary with better formatting
- Improved error handling in batch mode (collect errors, continue processing)
- CLI help text expanded with domain-specific guidance (min-count, filters, backends)
- Sidecar collection now skips original input file (prevents false positive matches)

#### Finalize Module
- Main entry point now requires state files with embedded HEALPyxelxMetadata
- Auto-detects nside from metadata (no longer requires `--nside` flag for densify/export)
- Output files embed validated metadata in parquet schema
- Backward compatibility maintained via `_normalize_load_state_result()`

#### Geospatial Module
- `healpix_to_geodataframe()` signature extended with `cache_mode` and `cache_dir` parameters
- Main CLI now accepts `--cache-mode` flag
- Sparse mode now computes only requested pixels (not full grid + filter)
- Dense mode includes safety checks for large nsides (interactive confirmation)

### Fixed

#### Aggregate Module
- Sidecar collection no longer includes original input file as false positive
- Sidecar collection correctly handles files with no suffix (base input files)
- Overwrite prompt prevents silent data loss (requires `--yes` in batch mode)

#### Accumulator Module
- State/sidecar compatibility validation prevents silent corruption from mixing incompatible files
- Proper error handling when state file lacks metadata (clear error message)

#### Geospatial Module
- Spherical coordinate conversion now handles antimeridian correctly
- Cache verification detects incomplete/corrupt caches (NaN values, missing pixels, invalid IDs)

### Removed

#### Finalize Module
- **BREAKING:** `extract_nside_from_metadata()` removed (replaced by embedded metadata in parquet schema)

### Infrastructure
- All CLI entry points now detect IPython/Jupyter context (prevent argparse conflicts in notebooks)
- Consistent use of `HEALPyxelxMetadata` across pipeline stages
- XDG Base Directory Specification compliance for cache/config files
- Converted Python scripts to nbdev notebooks
- Python API implemented across all modules
- Comprehensive tests added

### Migration Guide

**For users calling `load_state()` directly:**
```python
# Old (v0.1.x)
state = load_state(path)

# New (v0.2.0)
state, meta = load_state(path)
```

**For aggregation workflows with `min_count=0`:**
- Workflows using `min_count=0` will now log warnings
- Consider changing to `min_count=1` or higher for production use

**For cache directory configuration:**
- Cache directory precedence changed to: `--cache-dir` > `HEALPYXEL_CACHE` > `XDG_CACHE_HOME` > `~/.cache/healpyxel`
- Set `HEALPYXEL_CACHE` environment variable or use `healpyxel_cache config init` to configure

## [0.1.0] - 2025-12-11

### Added
- Initial package structure with nbdev
- Test data suite (59MB, 15 files)
- Example notebooks:
  - `index.ipynb` - Package homepage
  - `00_setup.ipynb` - Setup guide and test data exploration
  - `90_example_streaming.ipynb` - Streaming workflow example
- Original scripts ready for migration:
  - `healpix_sidecar.py` - HEALPix cell assignment
  - `healpix_aggregate.py` - Batch aggregation
  - `healpix_accumulator.py` - Streaming accumulation
  - `healpix_finalize.py` - Statistics finalization
- Development tools:
  - Makefile with common tasks
  - Documentation generation with nbdev
  - Git repository initialized
- Documentation:
  - README.md (from index.ipynb)
  - QUICKSTART.md
  - PACKAGE_AUDIT.md

### Infrastructure
- Python package structure with setuptools
- Modern pyproject.toml configuration
- nbdev settings.ini configuration
- Apache 2.0 license

[Unreleased]: https://github.com/mariodamore/healpyxel/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/mariodamore/healpyxel/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mariodamore/healpyxel/releases/tag/v0.1.0
