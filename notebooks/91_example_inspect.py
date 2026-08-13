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
#     language: python3
#     name: python3
# ---

# %% [markdown]
# # Inspect Pipeline: Schema Display and Sidecar Discovery
#
# The `healpyxel_inspect` command provides read-only investigation of parquet files
# and HEALPix sidecars.  It is extracted from aggregate.py per ADR-017.
#
# Features:
# - Display parquet schemas (column names, types, metadata)
# - List available sidecars with row counts and file sizes
# - Inspect individual sidecar schemas by index
# - Discover geometry column info (ADR-018)
# - One-time geometry correction for broken spatial metadata

# %%
import sys
from pathlib import Path

# Paths relative to notebooks/ execution context (cwd = notebooks/ when .py notebook runs)
if str(Path('.').resolve()) not in sys.path:
    sys.path.insert(0, str(Path('.').resolve()))

from healpyxel.inspect import (
    print_parquet_schema,
    print_sidecar_summary,
)
from healpyxel.aggregate import collect_sidecar_outputs

# %% [markdown]
# ## Setup: Locate Test Data
#
# Use the sample input file and sidecar outputs from the test data directory.

# %%
#| echo: false
# Locate test data (paths relative to notebooks/)
test_data_dir = Path('../test_data')
input_file = test_data_dir / 'samples' / 'sample_50k.parquet'
sidecar_dir = test_data_dir / 'derived' / 'cli_quickstart'

print(f"Input file: {input_file.name}")
print(f"Sidecar dir: {sidecar_dir}")
print(f"Input exists: {input_file.exists()}")

# %% [markdown]
# ## 1. Display Input File Schema
#
# Show the schema of the input parquet file: column names, types, row count, and file size.

# %%
print_parquet_schema(input_file, show_metadata=True, verbose=False)

# %% [markdown]
# ## 2. Verbose Schema Inspection
#
# With `--verbose`, the inspect command also shows per-column statistics:
# non-null counts, min/max for numeric columns, and unique values for strings.

# %%
print_parquet_schema(input_file, show_metadata=True, verbose=True)

# %% [markdown]
# ## 3. List Available Sidecars
#
# Scan the sidecar directory and display all available sidecar files with metadata.
#
# Shows: mode (nside), order, row counts, file sizes, and derived-from-parent flags.

# %%
sidecars_df = collect_sidecar_outputs(input_file, sidecar_dir, read_stats=True)
print_sidecar_summary(sidecars_df, input_file)

# %% [markdown]
# ## 4. Inspect a Specific Sidecar
#
# Display the schema of a specific sidecar by index.
# First use `--list-sidecars` to see available indices.

# %%
if len(sidecars_df) > 0:
    idx = 0
    sidecar_path = Path(sidecars_df.iloc[idx]["file"])
    print(f"\nSidecar index {idx}: {sidecar_path.name}")
    print("=" * 80)
    print_parquet_schema(sidecar_path, show_metadata=True, verbose=False)
else:
    print("No sidecars found in sidecar_dir.")

# %% [markdown]
# ## 5. Using the CLI Directly
#
# The `healpyxel_inspect` command can also be invoked from the shell:
#
# ```bash
# # Show schema
# healpyxel_inspect -i test_data/samples/sample_50k.parquet --schema
#
# # List sidecars with stats
# healpyxel_inspect -i test_data/samples/sample_50k.parquet --list-sidecars --stats --sidecar-dir test_data/derived/cli_quickstart
#
# # Inspect sidecar index 0
# healpyxel_inspect -i test_data/samples/sample_50k.parquet --sidecar-schema 0 --sidecar-dir test_data/derived/cli_quickstart
#
# # Verbose output
# healpyxel_inspect -i test_data/samples/sample_50k.parquet --schema -v
#
# # Geometry discovery
# healpyxel_inspect -i test_data/samples/sample_50k.parquet --geometry
#
# # One-time geometry correction
# healpyxel_inspect -i test_data/samples/sample_50k.parquet --correct-geometry fixed.parquet --force
# ```

# %%
#| echo: false
# Demonstrate CLI invocation via Python API
from healpyxel.inspect import parse_arguments, run

# Show schema
args = parse_arguments(["-i", str(input_file), "--schema"])
run(args)

# List sidecars
args = parse_arguments(["-i", str(input_file), "--list-sidecars", "--stats", "--sidecar-dir", str(sidecar_dir)])
run(args)

# --geometry flag
args = parse_arguments(["-i", str(input_file), "--geometry"])
run(args)

# --correct-geometry flag (one-time fix)
import tempfile
corrected_path = Path(tempfile.gettempdir()) / 'sample_50k_corrected.parquet'
args = parse_arguments(["-i", str(input_file), "--correct-geometry", str(corrected_path), "--force"])
run(args)
print(f"\nCorrected file exists: {corrected_path.exists()}")
if corrected_path.exists():
    import geopandas as gpd
    gdf_check = gpd.read_file(corrected_path)
    print(f"CRS: {gdf_check.crs}")
    print(f"Geometry column: {gdf_check.geometry.name}")
    corrected_path.unlink(missing_ok=True)
