# Healpyxel: Technical Overview

> **Purpose:** This document provides a comprehensive technical overview of the healpyxel repository for use with large LLMs in web chat contexts. It describes the current architecture, development workflow, and the proposed transition from nbdev to pure Python + Quarto.

---

## Project Summary

**healpyxel** is a HEALPix-based spatial aggregation tool for planetary science data (specifically MESSENGER/MASCS spectrometer data). It implements split-apply-combine workflows for both batch and streaming processing of geospatial observations.

**Key characteristics:**
- Python 3.10+
- HEALPix spherical pixelization (via `healpy`)
- GeoParquet I/O with lazy loading (`dask-geopandas`)
- Streaming statistics with Welford's algorithm + TDigest
- CLI interface with Rich formatting

---

## Current Architecture (nbdev-based)

### Directory Structure

```
healpyxel/
├── nbs/                          # Source notebooks (nbdev)
│   ├── 00_core.ipynb             # Core utilities (mad, robust_std, validate_nside)
│   ├── 00_setup.ipynb            # Environment setup
│   ├── 00b_metadata.ipynb        # Metadata schema
│   ├── 01_sidecar.ipynb          # HEALPix cell assignment
│   ├── 02_aggregate.ipynb        # Batch aggregation
│   ├── 03_accumulator.ipynb      # Streaming state (Welford + TDigest)
│   ├── 04_finalize.ipynb         # Finalize streaming state
│   ├── 05_cli.ipynb              # CLI entry points
│   ├── 06_visualization.ipynb    # Map rendering
│   ├── 07_geospatial.ipynb       # Geometry utilities
│   ├── 80-89_*.ipynb             # Tutorial examples
│   ├── 90_*.ipynb                # Advanced examples
│   ├── index.ipynb               # Documentation homepage
│   ├── _quarto.yml               # Quarto config (currently unused)
│   ├── nbdev.yml                 # nbdev format config
│   └── sidebar.yml               # Sidebar navigation
│
├── healpyxel/                    # Auto-generated Python modules
│   ├── __init__.py               # Package init (__version__ = "0.1.0")
│   ├── _modidx.py                # nbdev module index (auto-generated)
│   ├── core.py                   # Exported from 00_core.ipynb
│   ├── sidecar.py                # Exported from 01_sidecar.ipynb
│   ├── aggregate.py              # Exported from 02_aggregate.ipynb
│   ├── accumulator.py            # Exported from 03_accumulator.ipynb
│   ├── finalize.py               # Exported from 04_finalize.ipynb
│   ├── cli.py                    # Exported from 05_cli.ipynb
│   ├── visualization.py          # Exported from 06_visualization.ipynb
│   └── geospatial.py             # Exported from 07_geospatial.ipynb
│
├── tests/                        # Handwritten pytest tests
│   ├── conftest.py               # Pytest fixtures
│   └── test_*.py                 # Test modules
│
├── healpyxel/tests/              # Auto-generated test modules from nbdev
│
├── test_data/                    # Test datasets
│   ├── batches/                  # batch_001.parquet, etc.
│   ├── samples/                  # sample_5k.parquet, etc.
│   └── validation/               # Ground truth files
│
├── .ai/                          # Project documentation (single source of truth)
│   ├── 00_CONSTRAINTS.md         # Hard rules (required/forbidden tech)
│   ├── 00_PHILOSOPHY.md          # Design principles
│   ├── 02_ROADMAP.md             # Current phase and scope
│   ├── 03_CURRENT_STATUS.md      # Active state
│   ├── decisions/index.md        # ADR index
│   └── README.md                 # Entry point
│
├── docs/                         # Generated documentation (Quarto output)
├── .github/                      # GitHub configs
├── settings.ini                  # nbdev config
├── pyproject.toml                # Modern Python packaging
└── Makefile                      # Development workflow commands
```

### nbdev Workflow

**Current development cycle:**
1. Code is written in Jupyter notebooks (`nbs/*.ipynb`)
2. Cells marked with `#| export` are exported to Python modules
3. `nbdev_export` converts notebooks → Python files
4. `nbdev_test` runs tests embedded in notebooks
5. `nbdev_prepare` runs export + test + cleanup

**nbdev cell directives:**
```python
#| default_exp core       # Declares output module (first cell)
#| export                 # Marks cell for inclusion in Python module
#| hide                   # Hide from docs
#| eval: false            # Don't execute during docs builds
#| output: false          # Suppress output
```

**Example notebook cell:**
```python
#| export
def mad(arr: np.ndarray) -> float:
    """Compute Median Absolute Deviation."""
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.median(np.abs(arr - np.median(arr))))
```

This becomes `healpyxel/core.py` after `nbdev_export`.

### Build Configuration

**settings.ini** (nbdev):
```ini
[DEFAULT]
lib_name = healpyxel
nbs_path = nbs
lib_path = healpyxel
doc_path = docs
requirements = pandas>=2.0 numpy>=1.24 pyarrow>=12.0 shapely>=2.0 healpy>=1.16 dask-geopandas>=0.3 antimeridian tqdm duckdb>=0.9.0 rich>=13.0
dev_requirements = nbdev>=2.3.12 jupyter
```

**pyproject.toml** (packaging):
```toml
[project]
name = "healpyxel"
version = "0.1.0"
dependencies = ['pandas>=2.0', 'numpy>=1.24', 'pyarrow>=12.0', ...]

[project.entry-points.nbdev]
healpyxel = "healpyxel._modidx:d"
```

---

## 4-Phase Pipeline Architecture

### 1. Sidecar (`01_sidecar.ipynb` → `healpyxel/sidecar.py`)
Maps geometries to HEALPix cells.
- **Modes:** `fuzzy` (polygon overlap with weights), `strict` (full containment)
- **Output:** Parquet with `source_id`, `healpix_id`, `weight`
- **Key function:** `sidecar.generate(gdf, nside, mode, order, lon_convention)`

### 2. Aggregate (`02_aggregate.ipynb` → `healpyxel/aggregate.py`)
Classical batch processing—groups by HEALPix cell, computes statistics.
- **Aggregations:** mean, median, std, min, max, mad, robust_std
- **Output:** Parquet with `healpix_id`, `column_agg` (e.g., `r750_median`)
- **Key function:** `aggregate.by_sidecar(original, sidecar, value_columns, aggs, min_count)`

### 3. Accumulator (`03_accumulator.ipynb` → `healpyxel/accumulator.py`)
Streaming variant—maintains incremental state.
- **State:** `count`, `mean`, `m2` (for variance), `tdigest_serialized`
- **Algorithm:** Welford's online mean/std + TDigest for percentiles
- **Key function:** `accumulator.update_state(batch, sidecar, value_columns, state)`

### 4. Finalize (`04_finalize.ipynb` → `healpyxel/finalize.py`)
Converts accumulator state to final statistics.
- **Features:** Densification (upsampling to higher NSIDE)
- **Key function:** `finalize.from_state(state, aggs, densify, nside)`

---

## Dependencies

**Core:**
- `pandas>=2.0` — Data frames
- `numpy>=1.24` — Numerical operations
- `pyarrow>=12.0` — Parquet I/O
- `shapely>=2.0` — Geometry operations
- `healpy>=1.16` — HEALPix (NEST ordering)
- `dask-geopandas>=0.3` — Lazy geospatial I/O
- `antimeridian` — Antimeridian crossing fixes
- `tqdm` — Progress bars
- `duckdb>=0.9.0` — Efficient SQL queries
- `rich>=13.0` — CLI formatting

**Optional:**
- `tdigest` — Percentile tracking (accumulator)
- `matplotlib`, `scikit-image`, `skyproj` — Visualization
- `nbdev>=2.3.12`, `jupyter` — Development

---

## Proposed Transition: Pure Python + Quarto

### Motivation

**Why move away from nbdev?**
1. **Separation of concerns:** Code and documentation should be separate
2. **IDE support:** Better autocomplete, refactoring, and debugging in pure Python
3. **Testing clarity:** Tests in `tests/` vs. tests in notebooks creates confusion
4. **Quarto flexibility:** Quarto supports multiple languages, better publishing options
5. **Team scaling:** New contributors may not know nbdev workflow

### Target Architecture

```
healpyxel/
├── src/healpyxel/              # Python source (NOT auto-generated)
│   ├── __init__.py
│   ├── core.py
│   ├── sidecar.py
│   ├── aggregate.py
│   ├── accumulator.py
│   ├── finalize.py
│   ├── cli.py
│   ├── visualization.py
│   └── geospatial.py
│
├── tests/                       # All tests (pytest)
│   ├── conftest.py
│   ├── test_core.py
│   ├── test_sidecar.py
│   ├── test_aggregate.py
│   ├── test_accumulator.py
│   ├── test_finalize.py
│   ├── test_cli.py
│   ├── test_visualization.py
│   └── test_geospatial.py
│
├── docs/                        # Quarto documentation
│   ├── index.qmd               # Homepage
│   ├── _quarto.yml             # Quarto config
│   └── _extensions/            # Quarto extensions
│
├── notebooks/                   # Example notebooks (not source of truth)
│   ├── 01_core.ipynb
│   ├── 02_sidecar.ipynb
│   ├── 03_aggregate.ipynb
│   ├── 04_accumulator.ipynb
│   ├── 05_finalize.ipynb
│   ├── 06_visualization.ipynb
│   ├── 07_geospatial.ipynb
│   └── examples/
│
├── test_data/                   # Unchanged
├── .ai/                         # Unchanged
├── pyproject.toml               # Updated (remove nbdev)
└── Makefile                     # Updated (remove nbdev commands)
```

### Key Changes

| Current (nbdev) | Target (Python + Quarto) |
|-----------------|-------------------------|
| `nbs/*.ipynb` is source of truth | `src/healpyxel/*.py` is source of truth |
| `#| export` directives in cells | Standard Python modules |
| Tests in notebooks (`#| export` test functions) | `tests/test_*.py` with pytest |
| `nbdev_export` → Python files | Direct Python development |
| `nbdev_test` → notebook tests | `pytest tests/` |
| `nbdev_docs` → Quarto via nbdev hooks | `quarto render docs/` |
| `settings.ini` for config | `pyproject.toml` only |
| `healpyxel/_modidx.py` (auto-generated) | Standard `__init__.py` exports |

### Migration Steps

1. **Create new directory structure:**
   ```bash
   mkdir -p src/healpyxel docs notebooks
   ```

2. **Copy Python modules from `healpyxel/` to `src/healpyxel/`:**
   - Remove `healpyxel/_modidx.py` (nbdev-specific)
   - Update `healpyxel/__init__.py` (keep, but no nbdev entry point)

3. **Convert notebooks to source code:**
   - Extract `#| export` cells into Python files
   - Remove `#| default_exp` directives
   - Keep markdown cells as docstrings

4. **Migrate tests:**
   - Export test functions from notebooks to `tests/test_*.py`
   - Ensure all tests use pytest fixtures from `conftest.py`

5. **Update `pyproject.toml`:**
   ```toml
   [build-system]
   requires = ["setuptools>=64", "wheel"]
   build-backend = "setuptools.build_meta"

   [project]
   name = "healpyxel"
   # Remove: [project.entry-points.nbdev]

   [tool.setuptools.packages.find]
   where = ["src"]
   ```

6. **Update `Makefile`:**
   - Remove: `export`, `test`, `docs`, `preview`, `prepare`, `readme`, `clean-nbs`
   - Add: `test` (pytest), `docs` (quarto render), `lint` (ruff/mypy)

7. **Configure Quarto:**
   - Migrate `nbs/_quarto.yml` to `docs/_quarto.yml`
   - Update sidebar navigation to reference Python API docs
   - Use `jupyter` engine for notebooks in `notebooks/`

8. **Update CI/CD:**
   - Replace `nbdev_test` with `pytest`
   - Replace `nbdev_export` with `pip install -e .`
   - Replace `nbdev_docs` with `quarto render`

### Benefits

| Aspect | Before (nbdev) | After (Python + Quarto) |
|--------|----------------|-------------------------|
| Development | Edit notebooks, export to Python | Edit Python directly |
| Testing | Tests in notebooks | Standard pytest |
| IDE support | Limited (notebook cells) | Full (autocomplete, refactoring) |
| Documentation | nbdev → Quarto | Direct Quarto |
| Code review | Notebook diffs | Python diffs |
| Onboarding | Learn nbdev workflow | Standard Python |

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Loss of nbdev test automation | Migrate all tests to pytest; add coverage reporting |
| Docs drift from code | Use Sphinx or pdoc for API docs; Quarto for tutorials |
| Notebook examples not updated | Add pre-commit hook to validate notebooks |
| Team unfamiliar with new workflow | Document migration; run training session |

---

## Technical Constraints (from `.ai/00_CONSTRAINTS.md`)

**Required:**
- `healpy` (NEST ordering) — No `cdshealpix`
- `pathlib` for paths
- `antimeridian` for crossing geometries
- `float64` for precision consistency

**Forbidden:**
- `scipy.stats` — Use custom `mad()`, `robust_std()`
- Python loops over datasets — Vectorization required
- Editing Python files directly (current nbdev workflow) — Will change in migration

**Performance:**
- <100ms on 1M rows for core operations
- Vectorized NumPy/Pandas operations only

---

## Documentation Files (`.ai/` folder)

- **`00_CONSTRAINTS.md`** — Hard rules (required/forbidden tech, patterns)
- **`00_PHILOSOPHY.md`** — Design principles (science-first, robustness)
- **`02_ROADMAP.md`** — Current phase, scope
- **`03_CURRENT_STATUS.md`** — NOW/NEXT/KNOWN_ISSUES
- **`decisions/index.md`** — ADR index (6 decisions recorded)
- **`README.md`** — Entry point for humans and agents

---

## Quick Reference: Key Functions

### `healpyxel/core.py`
```python
validate_nside(nside: int) -> int  # Validate power of 2
mad(arr: np.ndarray) -> float       # Median Absolute Deviation
robust_std(arr: np.ndarray) -> float  # MAD * 1.4826
setup_logger(name: str) -> Logger   # Standard logger setup
```

### `healpyxel/sidecar.py`
```python
generate(gdf, nside, mode, order, lon_convention) -> DataFrame
```

### `healpyxel/aggregate.py`
```python
by_sidecar(original, sidecar, value_columns, aggs, min_count) -> DataFrame
```

### `healpyxel/accumulator.py`
```python
update_state(batch, sidecar, value_columns, state) -> DataFrame
```

### `healpyxel/finalize.py`
```python
from_state(state, aggs, densify, nside) -> DataFrame
```

---

## Contact

- **Author:** Mario D'Amore
- **Email:** mario.damore@dlr.de
- **Repository:** https://github.com/mariodamore/healpyxel
- **Documentation:** https://mariodamore.github.io/healpyxel/

---

*Last updated: 2026-05-22*
