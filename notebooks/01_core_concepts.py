# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
#   kernelspec:
#     display_name: python3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Core Concepts

# %%
#| echo: false
import healpyxel
from healpyxel import core
import pandas as pd
import numpy as np

print(f"healpyxel version: {healpyxel.__version__}")

# %% [markdown]
# ## Core Utilities
#
# Test the core utility functions:

# %%
#| echo: false
# Validate nside parameter
try:
    core.validate_nside(64)  # Valid power of 2
    print("✓ nside=64 is valid")
except ValueError as e:
    print(f"✗ {e}")

try:
    core.validate_nside(100)  # Invalid
except ValueError as e:
    print(f"✓ Caught invalid nside: {e}")

# %%
#| echo: false
# Test MAD (Median Absolute Deviation)
data = np.array([1, 2, 3, 4, 5, 100])  # Outlier at 100
mad_value = core.mad(data)
print(f"MAD of {data}: {mad_value:.2f}")

# %%
#| echo: false
# Test robust standard deviation
robust_std = core.robust_std(data)
normal_std = np.std(data)
print(f"Robust std: {robust_std:.2f}")
print(f"Normal std: {normal_std:.2f}")
print(f"Robust std is less affected by the outlier (100)")

# %% [markdown]
# ## Test Data
#
# Check available test data in the package:

# %%
#| echo: false
from pathlib import Path

# Look for test data
test_data_dir = Path('../test_data')
if test_data_dir.exists():
    print("Test data directory found!")
    print(f"\nContents:")
    for item in sorted(test_data_dir.iterdir()):
        if item.is_file():
            size_mb = item.stat().st_size / 1024 / 1024
            print(f"  {item.name}: {size_mb:.2f} MB")
        elif item.is_dir():
            n_files = len(list(item.glob('*')))
            print(f"  {item.name}/: {n_files} files")
else:
    print("Test data directory not found. Run create_test_data.sh to generate test data.")

# %% [markdown]
# ## CLI Commands
#
# The package provides four CLI commands:
#
# - `healpix_sidecar` - Create sidecar files with HEALPix assignments
# - `healpix_aggregate` - Aggregate data into HEALPix cells
# - `healpix_accumulator` - Stream large datasets with online statistics
# - `healpix_finalize` - Finalize accumulated state to statistical maps
#
# Check if commands are available:

# %%
#| echo: false
import subprocess

commands = ['healpix_sidecar', 'healpix_aggregate', 'healpix_accumulator', 'healpix_finalize']

for cmd in commands:
    result = subprocess.run(['which', cmd], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"✓ {cmd}: {result.stdout.strip()}")
    else:
        print(f"✗ {cmd}: not found")

# %% [markdown]
# ## Quick Test with Sample Data
#
# If test data is available, let's try a quick aggregation:

# %%
#| echo: false
# Check for sample data
sample_file = test_data_dir / 'sample_001.parquet'

if sample_file.exists():
    print(f"Loading sample: {sample_file.name}")
    df = pd.read_parquet(sample_file)
    
    print(f"\nShape: {df.shape}")
    print(f"\nColumns: {list(df.columns)}")
    print(f"\nFirst few rows:")
    display(df.head())
    
    # Check for lat/lon columns
    if 'latitude' in df.columns and 'longitude' in df.columns:
        print(f"\n✓ Found latitude/longitude columns for HEALPix conversion")
        print(f"  Lat range: [{df['latitude'].min():.2f}, {df['latitude'].max():.2f}]")
        print(f"  Lon range: [{df['longitude'].min():.2f}, {df['longitude'].max():.2f}]")
else:
    print("Sample data not found. Generate test data first:")
    print("  cd .. && bash create_test_data.sh")

# %% [markdown]
# ## Create Sidecar for Sample Data (CLI)
#
# Below is a complete CLI regridding example that mirrors the notebook workflow using `sample_50k.parquet`, with `nside=32`. It writes outputs into a dedicated folder to keep the sidecar index stable.
#
# Save the script as `examples/cli_regrid_sample_50k.sh` and run it from the repo root:
#
# ```bash
# !/usr/bin/env bash
# set -euo pipefail
#
# ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# INPUT="$ROOT_DIR/test_data/samples/sample_50k.parquet"
# OUT_DIR="$ROOT_DIR/test_data/derived/cli_quickstart"
# NSIDE=32
# MODE=fuzzy
# LON_CONVENTION=0_360
#
# mkdir -p "$OUT_DIR"
#
# # 1) Create HEALPix sidecar
# healpix_sidecar \
#   --input "$INPUT" \
#   --nside "$NSIDE" \
#   --mode "$MODE" \
#   --lon-convention "$LON_CONVENTION" \
#   --output_dir "$OUT_DIR"
#
# # 2) Aggregate (densified) regridded map
# healpix_aggregate \
#   --input "$INPUT" \
#   --sidecar-dir "$OUT_DIR" \
#   --sidecar-index 0 \
#   --aggregate \
#   --columns r1050 \
#   --aggs mean median std mad robust_std \
#   --min-count 1 \
#   --densify \
#   --output "$OUT_DIR/sample_50k_nside${NSIDE}_r1050_aggregate.parquet"
# ```
#
# > If you want to aggregate a different column, replace `r1050` in the script.
#
# Or use `--all-columns` to automatically aggregate every float/double column
# in the input (no need to list them manually):
# ```
# healpix_aggregate \
#   --input "$INPUT" \
#   --sidecar-dir "$OUT_DIR" \
#   --sidecar-index 0 \
#   --aggregate \
#   --all-columns \
#   --aggs mean median std \
#   --output "$OUT_DIR/sample_50k_nside${NSIDE}_all_cols_aggregate.parquet"
# ```

# %%
#| echo: false
from pathlib import Path
from healpyxel.sidecar import build_output_path

assert build_output_path(
    Path("sample_50k.parquet"),
    mode="fuzzy",
    nside=32,
).name == "sample_50k.cell-healpix_assignment-fuzzy_nside-32_order-nested.parquet"

# %% [markdown]
# ## Next Steps
#
# For more detailed examples, see:
#
# - **Sidecar workflow**: `01_sidecar.ipynb` - HEALPix assignment
# - **Aggregation**: `02_aggregate.ipynb` - Statistical aggregation
# - **Streaming**: `03_accumulator.ipynb` - Large dataset processing
# - **Finalization**: `04_finalize.ipynb` - Final map products
#
# Or run the CLI commands directly:
#
# ```bash
# # Create sidecar with HEALPix IDs
# healpix_sidecar --help
#
# # Aggregate into HEALPix cells
# healpix_aggregate --help
#
# # Stream large datasets
# healpix_accumulator --help
#
# # Finalize statistical maps
# healpix_finalize --help
# ```

# %% [markdown]
# ## Package Information

# %%
#| echo: false
# Show available modules
print("Available modules:")
for attr in dir(healpyxel):
    if not attr.startswith('_'):
        obj = getattr(healpyxel, attr)
        if hasattr(obj, '__file__'):
            print(f"  - {attr}")
