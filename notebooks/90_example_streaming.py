# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
#   kernelspec:
#     display_name: python3
#     language: python
#     name: python3
# ---

# %% [markdown]
#
# # Streaming WIP!
#
# <span style="color: red;">*STILL WORK IN PROGRESS*</span>
#

# %%
from pathlib import Path
import pandas as pd
import numpy as np

# Get test data directory (notebooks sit one level below project root)
test_data_dir = Path('../test_data')
batches_dir = test_data_dir / 'batches'
validation_dir = test_data_dir / 'validation'

# %% [markdown]
# ## Step 1: Load First Batch
#
# Let's inspect the first batch to understand the data structure:

# %%
batch_001 = pd.read_parquet(batches_dir / 'batch_001.parquet')

print(f"Batch 001 Info:")
print(f"  Observations: {len(batch_001):,}")
print(f"  Columns: {len(batch_001.columns)}")
print(f"  Memory: {batch_001.memory_usage(deep=True).sum() / 1024 / 1024:.1f} MB")
print(f"\n  Lon range: {batch_001['lon_center'].min():.2f}° to {batch_001['lon_center'].max():.2f}°")
print(f"  Lat range: {batch_001['lat_center'].min():.2f}° to {batch_001['lat_center'].max():.2f}°")

# Show spectral columns
spectral_cols = [col for col in batch_001.columns if col.startswith('r') and col[1:4].isdigit()]
print(f"\n  Spectral columns: {spectral_cols}")

batch_001.head(3)

# %% [markdown]
# ## Step 2: Generate HEALPix Sidecar
#
# For this example, we'll use the existing `healpix_sidecar.py` script (will be converted to module later):

# %%
# Once healpix_sidecar is converted to a module, this will be:
# from healpyxel import sidecar
# sidecar_df = sidecar.generate(batch_001, nside=64, mode='fuzzy')

# For now, run the CLI:
print("Generate sidecar using CLI:")
print(f"  python healpix_sidecar.py --input {batches_dir / 'batch_001.parquet'} --nside 64 --mode fuzzy")
print("\nThis will create: batch_001.cell-healpix_assignment-fuzzy_nside-64_order-nested.parquet")

# %% [markdown]
# ## Step 3: Streaming Accumulation Workflow
#
# Process multiple batches incrementally:

# %% [markdown]
# ### Conceptual Workflow
#
# ```python
# # Day 1: Initialize
# from healpyxel import accumulator
#
# state = accumulator.accumulate_batch(
#     new_data=batch_001,
#     sidecar=sidecar_001,
#     value_columns=['r750', 'r950'],
#     existing_state=None,  # First batch
#     use_tdigest=True
# )
#
# # Day 2: Incremental update
# state = accumulator.accumulate_batch(
#     new_data=batch_002,
#     sidecar=sidecar_002,
#     value_columns=['r750', 'r950'],
#     existing_state=state,  # Reuse previous state
#     use_tdigest=True
# )
#
# # Finalize: Convert to statistics
# from healpyxel import finalize
#
# results = finalize.finalize_statistics(
#     state=state,
#     percentiles=[25, 50, 75],
#     min_count=2
# )
# ```

# %% [markdown]
# ## Step 4: Validation
#
# Compare streaming results with batch processing:

# %%
# Load combined validation file
combined_file = validation_dir / 'combined_batch_001_003.parquet'

if combined_file.exists():
    combined = pd.read_parquet(combined_file)
    
    print("Validation Dataset:")
    print(f"  Combined (batches 1-3): {len(combined):,} obs")
    print(f"  Lon range: {combined['lon_center'].min():.2f}° to {combined['lon_center'].max():.2f}°")
    
    # Load individual batches
    batch_001_len = len(pd.read_parquet(batches_dir / 'batch_001.parquet'))
    batch_002_len = len(pd.read_parquet(batches_dir / 'batch_002.parquet'))
    batch_003_len = len(pd.read_parquet(batches_dir / 'batch_003.parquet'))
    
    total_individual = batch_001_len + batch_002_len + batch_003_len
    
    print(f"\n  Individual batches sum: {total_individual:,} obs")
    print(f"  Difference: {abs(len(combined) - total_individual)} obs")
    
    if len(combined) == total_individual:
        print("  ✓ Counts match perfectly!")
    else:
        print("  ⚠️  Count mismatch (may be due to filtering)")
else:
    print(f"⚠️  Validation file not found: {combined_file}")

# %% [markdown]
# ## Step 5: Performance Metrics
#
# Compare memory usage between approaches:

# %%
import sys

# Memory for batch processing (loading all data at once)
if combined_file.exists():
    combined = pd.read_parquet(combined_file)
    batch_memory = combined.memory_usage(deep=True).sum() / 1024 / 1024
    
    print("Memory Comparison:")
    print(f"  Batch processing: {batch_memory:.1f} MB (load all data)")
    print(f"  Streaming: ~5-10 MB per batch + state file (~10-20 MB)")
    print(f"\n  Memory savings: ~{batch_memory - 20:.1f} MB")
    print(f"  Efficiency: {20 / batch_memory * 100:.1f}% of batch memory")

# %% [markdown]
# ## Summary
#
# This notebook demonstrates:
#
# 1. ✅ Loading test batch data
# 2. ✅ Understanding data structure
# 3. ⏳ Sidecar generation (to be implemented)
# 4. ⏳ Streaming accumulation (to be implemented)
# 5. ⏳ Finalization to statistics (to be implemented)
# 6. ✅ Validation approach
# 7. ✅ Performance benefits
#
# **Next steps:**
# - Convert `healpix_*.py` scripts to nbdev modules
# - Implement Python API for all functions
# - Add comprehensive tests
# - Create more example notebooks

# %% [markdown]
#
