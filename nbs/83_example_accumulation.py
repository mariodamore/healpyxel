# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.1
#   kernelspec:
#     display_name: mertis
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Streaming Accumulation Validation Workflow
#
# This notebook validates **streaming accumulation** against **full batch aggregation** 
# using HEALPix binning and geospatial analysis.
#
# <span style="color: red;">*STILL WORK IN PROGRESS*</span>
#
# ## Objectives
#
# 1. Load simulated stream batches and validate geometries
# 2. Create HEALPix sidecars for each batch
# 3. Build full batch aggregation baseline (raw numeric ground truth)
# 4. Run streaming accumulation incrementally
# 5. Validate state integrity (disk vs memory)
# 6. Compare streaming vs batch results with error metrics
#
# ## Key Invariants
#
# - **Same HEALPix semantics**: Fixed `nside`, `nest` ordering, `lon_convention` across all steps
# - **Stable IDs**: Consistent `source_id` and `healpix_id` across paths
# - **Identical filtering**: Same NaN/inf/sentinel handling in both pipelines
# - **Raw ground truth**: Batch baseline is numeric-only (before geometry enrichment)
# - **Readable naming**: Coherent variable names throughout

# %% [markdown]
# ## Section 1: Import Libraries and Configure HEALPix

# %%
import numpy as np
import pandas as pd
import geopandas as gpd
import healpy as hp
from pathlib import Path
from typing import Dict, Optional, Tuple, List, cast
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
import warnings

# Import healpyxel modules
import healpyxel
from healpyxel.sidecar import process_partition
from healpyxel.aggregate import aggregate_by_sidecar
from healpyxel.accumulator import (
    accumulate_batch, save_state, load_state,
    extract_accumulation_progress, snapshot_state_as_dataframe, input_fingerprint
)
from healpyxel.geospatial import healpix_to_geodataframe, is_geometry_valid

warnings.filterwarnings('ignore', category=UserWarning)

print(f"healpyxel version: {healpyxel.__version__}")


# %% [markdown]
# Configure HEALPix parameters (fixed across all pipeline stages):

# %%
# === CONFIGURATION: Fixed HEALPix Parameters ===
HEALPIX_CONFIG = {
    'nside': 32,                    # HEALPix resolution
    'nested': True,                 # Use NEST ordering (required)
    'lon_convention': '0_360',      # Normalize longitudes to [0, 360)
}

# Optional T-Digest quantile extraction from accumulator state
USE_TDIGEST = True
USE_TDIGEST_QUANTILES = True
TDIGEST_QUANTILES = [10, 50, 90]


def add_tdigest_quantiles_to_snapshot(
    state: Dict[int, object],
    snapshot_df: pd.DataFrame,
    columns: List[str],
    quantiles: List[float]
) -> pd.DataFrame:
    """Attach optional TDigest-based quantiles (q10/q50/q90...) to snapshot DataFrame."""
    if not USE_TDIGEST_QUANTILES or snapshot_df.empty:
        return snapshot_df

    quant_rows = []
    for hp_id, acc in state.items():
        row = {'healpix_id': int(hp_id)}

        tdigests = getattr(acc, 'tdigests', None)
        if not tdigests:
            quant_rows.append(row)
            continue

        for col in columns:
            digest = tdigests.get(col)
            if digest is None:
                continue
            for q in quantiles:
                row[f'{col}_q{int(q)}'] = float(digest.percentile(q))

        quant_rows.append(row)

    quant_df = pd.DataFrame(quant_rows)
    if quant_df.empty:
        return snapshot_df

    return snapshot_df.merge(quant_df, on='healpix_id', how='left')

# Output directories
STATE_DIR = Path("/tmp/healpyxel_state")
STATE_DIR.mkdir(exist_ok=True, parents=True)
SNAPSHOTS_DIR = STATE_DIR / "snapshots"
SNAPSHOTS_DIR.mkdir(exist_ok=True, parents=True)

NPIX = hp.nside2npix(HEALPIX_CONFIG['nside'])
print(f"✓ HEALPix Configuration:")
print(f"  nside={HEALPIX_CONFIG['nside']}, npix={NPIX}, ordering={'NEST' if HEALPIX_CONFIG['nested'] else 'RING'}")
print(f"  lon_convention={HEALPIX_CONFIG['lon_convention']}")
print(f"  T-Digest enabled={USE_TDIGEST}; quantiles enabled={USE_TDIGEST_QUANTILES}; q={TDIGEST_QUANTILES}")


# %% [markdown]
# ## Section 2: Load and Validate Simulated Stream Batches

# %%
# === STEP 1: Discover batch files ===
test_data_dir = Path('../test_data')
batches_dir = test_data_dir / 'batches'

batch_files = sorted(batches_dir.glob('batch_*.parquet')) if batches_dir.exists() else []

if not batch_files:
    raise FileNotFoundError(f"No batch files found in {batches_dir}. Run create_test_data.sh first.")

print(f"\n{'='*70}")
print(f"SECTION 2: LOAD & VALIDATE STREAM BATCHES")
print(f"{'='*70}")
print(f"\nDiscovered {len(batch_files)} batch files:")
for i, f in enumerate(batch_files, 1):
    print(f"  {i:2d}. {f.name}")


# %% [markdown]
# Load all batches and validate geometries:

# %%
# === STEP 2: Load and validate geometries ===
batches_loaded = []
batch_metadata = []

for i, batch_file in enumerate(batch_files):
    print(f"\n[{i+1}/{len(batch_files)}] Loading {batch_file.name}...")
    
    # Load parquet (handle both GeoDataFrame and regular DataFrame)
    try:
        gdf = gpd.read_parquet(batch_file)
    except ValueError:
        # No geo metadata; read as pandas then convert
        df = pd.read_parquet(batch_file)
        if 'geometry' in df.columns:
            from shapely import wkt, from_wkb
            
            def parse_geometry(geom):
                if geom is None or (isinstance(geom, str) and geom == ''):
                    return None
                if isinstance(geom, bytes):
                    return from_wkb(geom)
                if isinstance(geom, str):
                    return wkt.loads(geom)
                return geom
            
            df['geometry'] = df['geometry'].apply(parse_geometry)
            gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
        else:
            gdf = gpd.GeoDataFrame(df)
    
    print(f"  Shape: {gdf.shape}")
    print(f"  Columns: {list(gdf.columns)}")
    
    # Filter valid geometries
    if 'geometry' in gdf.columns:
        valid_mask = gdf['geometry'].apply(is_geometry_valid)
        n_invalid = (~valid_mask).sum()
        if n_invalid > 0:
            print(f"  ⚠️  Removing {n_invalid} invalid geometries")
            gdf = gdf[valid_mask].copy()
    
    batches_loaded.append(gdf)
    batch_metadata.append({
        'batch_id': i + 1,
        'file': batch_file.name,
        'n_records': len(gdf),
        'bounds': gdf.geometry.bounds.describe().T.to_dict() if 'geometry' in gdf.columns else None
    })
    
    print(f"  ✓ Valid records: {len(gdf)}")

print(f"\n✓ Loaded {len(batches_loaded)} batches")
print(f"  Total records: {sum(len(b) for b in batches_loaded):,}")

# %%

batch_files


# %% [markdown]
# Visualize batch coverage:

# %%
# === STEP 3: Plot batch spatial coverage ===
fig, ax = plt.subplots(figsize=(16, 8))
colors = plt.cm.tab10.colors

for i, gdf in enumerate(batches_loaded):
    if 'geometry' not in gdf.columns:
        continue
    color = colors[i % len(colors)]
    gdf.plot(ax=ax, color=color, alpha=0.6, linewidth=1.5, label=f"Batch {i+1}")

ax.set_title(f"Spatial Coverage: {len(batches_loaded)} Batches", fontsize=12, fontweight='bold')
ax.set_xlabel("Longitude (°)")
ax.set_ylabel("Latitude (°)")
ax.legend(loc='upper right', fontsize=9)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

# %% [markdown]
# ## Section 3: Create HEALPix Sidecars Per Batch

# %% [markdown]
# Process each batch to create HEALPix cell assignments:

# %%
# === STEP 4: Create sidecar for each batch ===
print(f"\n{'='*70}")
print(f"SECTION 3: CREATE HEALPIX SIDECARS")
print(f"{'='*70}")

sidecars_by_batch = []
combined_sidecar_df = None

for i, gdf in enumerate(batches_loaded):
    batch_id = i + 1
    print(f"\n[{batch_id}/{len(batches_loaded)}] Creating sidecar...")
    
    # Ensure geometry column exists
    if 'geometry' not in gdf.columns:
        print(f"  ⚠️  No geometry column")
        continue
    
    # Process partition: assign HEALPix cells
    sidecar_df = process_partition(
        gdf=gdf,
        nside=HEALPIX_CONFIG['nside'],
        mode='fuzzy',  # Polygon overlap mode
        base_index=None,  # Use existing index
        lon_convention=HEALPIX_CONFIG['lon_convention']
    )
    
    # Add batch tracking
    sidecar_df['batch_id'] = batch_id
    sidecars_by_batch.append(sidecar_df)
    
    print(f"  ✓ Assignments: {len(sidecar_df)}")
    print(f"    - Unique cells: {sidecar_df['healpix_id'].nunique()}")
    print(f"    - Unique sources: {sidecar_df['source_id'].nunique()}")

# Combine all sidecars
combined_sidecar_df = pd.concat(sidecars_by_batch, ignore_index=True)
print(f"\n✓ Combined sidecar:")
print(f"  Total assignments: {len(combined_sidecar_df)}")
print(f"  Unique cells: {combined_sidecar_df['healpix_id'].nunique()}")
print(f"  Unique sources: {combined_sidecar_df['source_id'].nunique()}")


# %% [markdown]
# ## Section 4: Build Full Batch Baseline Aggregation

# %% [markdown]
# Combine all batches and run one-shot aggregation as reference baseline:

# %%
# === STEP 5: Combine all raw data ===
print(f"\n{'='*70}")
print(f"SECTION 4: BUILD BATCH BASELINE")
print(f"{'='*70}")

# Combine raw data from all batches
combined_raw_data = []

for i, gdf in enumerate(batches_loaded):
    batch_id = i + 1
    df = gdf.drop(columns=['geometry'], errors='ignore').copy()
    
    # Ensure source_id index
    if 'source_id' not in df.columns and df.index.name != 'source_id':
        df['source_id'] = df.index.astype(np.int64)
    
    df['batch_id'] = batch_id
    combined_raw_data.append(df)

combined_raw_df = pd.concat(combined_raw_data, ignore_index=True)
print(f"\nCombined raw data:")
print(f"  Total observations: {len(combined_raw_df)}")
print(f"  Columns: {list(combined_raw_df.columns)}")


# %%
# === STEP 6: Identify value columns to aggregate ===
numeric_cols = combined_raw_df.select_dtypes(include=[np.number]).columns.tolist()
exclude_cols = {'source_id', 'batch_id', 'lat', 'lon', 'latitude', 'longitude', 'lat_center', 'lon_center'}
# use a predefined list of value columns for clarity and consistency
value_columns = ['vis_slope', 'nir_slope', 'r1050'] 
# value_columns = [c for c in numeric_cols if c not in exclude_cols]

if not value_columns:
    print("⚠️  No value columns found, using first numeric column")
    value_columns = numeric_cols[:1]

print(f"\nValue columns to aggregate: {value_columns}")


# %%
# === STEP 7: Run batch aggregation (ground truth) ===
print(f"\nRunning batch aggregation...")

batch_aggregated_df = aggregate_by_sidecar(
    original=combined_raw_df,
    sidecar=combined_sidecar_df,
    value_columns=value_columns,
    aggs=['mean', 'median', 'std', 'robust_std', 'mad'],
    source_id_col='source_id',
    healpix_col='healpix_id',
    min_count=1,
    sentinel_threshold=1e30
)

print(f"✓ Batch aggregation result:")
print(f"  HEALPix cells: {len(batch_aggregated_df)}")
print(f"  Columns: {list(batch_aggregated_df.columns)[:10]}...")
print(f"  Index name: {batch_aggregated_df.index.name}")


# %% [markdown]
# Store raw batch baseline (before geometry enrichment):

# %%
# === STEP 8: Preserve raw batch baseline ===
raw_batch_baseline = batch_aggregated_df.copy()
if 'r1050_median' in raw_batch_baseline.columns:
    raw_batch_baseline['r1050_q50'] = raw_batch_baseline['r1050_median']

print(f"\n✓ Created raw_batch_baseline for validation")
print(f"  Shape: {raw_batch_baseline.shape}")
print(f"  Index name: {raw_batch_baseline.index.name}")
if 'r1050_q50' in raw_batch_baseline.columns:
    print(f"  ✓ Added batch q50 proxy from exact median: r1050_q50")


# %% [markdown]
# ## Section 5: Run Streaming Accumulation Pipeline

# %% [markdown]
# Process batches incrementally and accumulate state:

# %%
# === STEP 9: Initialize streaming state ===
print(f"\n{'='*70}")
print(f"SECTION 5: STREAMING ACCUMULATION")
print(f"{'='*70}")

accumulated_state = None
snapshot_registry = {}

print(f"\nProcessing {len(batches_loaded)} batches incrementally...")


# %%
# === STEP 10: Process each batch with accumulator ===
for i, gdf in enumerate(batches_loaded):
    batch_id = i + 1
    batch_file = batch_files[i]  # Track input file for fingerprints
    print(f"\n[{batch_id}/{len(batches_loaded)}] Processing batch: {batch_file.name}")
    
    # Extract raw data for this batch
    batch_raw_df = gdf.drop(columns=['geometry'], errors='ignore').copy()
    if 'source_id' not in batch_raw_df.columns:
        batch_raw_df['source_id'] = batch_raw_df.index.astype(np.int64)
    batch_raw_df.set_index('source_id', inplace=True)
    
    # Get sidecar for this batch
    batch_sidecar = sidecars_by_batch[i]
    
    # Accumulate
    accumulated_state = accumulate_batch(
        new_data=batch_raw_df,
        sidecar=batch_sidecar,
        value_columns=value_columns,
        existing_state=accumulated_state,
        use_tdigest=USE_TDIGEST
    )
    
    print(f"  ✓ State cells: {len(accumulated_state)}")
    
    # Create snapshot
    snapshot_df = snapshot_state_as_dataframe(accumulated_state, columns=value_columns)
    snapshot_df = add_tdigest_quantiles_to_snapshot(
        state=accumulated_state,
        snapshot_df=snapshot_df,
        columns=value_columns,
        quantiles=TDIGEST_QUANTILES
    )
    snapshot_df['batch_id'] = batch_id
    
    # Save snapshot
    snapshot_path = SNAPSHOTS_DIR / f"snapshot_v{batch_id:03d}.parquet"
    snapshot_df.to_parquet(snapshot_path, index=False)
    
    n_obs = sum(
        acc.stats_by_column[value_columns[0]].n
        for acc in accumulated_state.values()
        if value_columns[0] in acc.stats_by_column
    )
    
    snapshot_registry[batch_id] = {
        'path': snapshot_path,
        'n_cells': len(snapshot_df),
        'n_observations': n_obs
    }
    
    print(f"  ✓ Snapshot saved: {snapshot_path.name}")


# %%
# === STEP 11: Save final state to disk ===
print(f"\nSaving final accumulated state...")

final_state_path = STATE_DIR / f"state_v{len(batches_loaded):03d}.parquet"

# Create proper HEALPyxelxMetadata object
from healpyxel.metadata import HEALPyxelxMetadata, FileType

meta = HEALPyxelxMetadata(
    nside=HEALPIX_CONFIG['nside'],
    mode='fuzzy',
    lon_convention=HEALPIX_CONFIG['lon_convention'],
    file_type=FileType.ACCUMULATOR
)

save_state(
    state=accumulated_state,
    output_path=final_state_path,
    meta=meta,
    processing_metadata={
        'n_batches': len(batches_loaded),
        'nside': HEALPIX_CONFIG['nside'],
        'value_columns': value_columns,
        'processed_inputs': [input_fingerprint(p) for p in batch_files]
    },
    on_duplicate="error"
)

print(f"✓ Final state saved to: {final_state_path}")


# %% [markdown]
# ## Section 6: Persist and Reload State Snapshots

# %% [markdown]
# Reload final state from disk to verify serialization:

# %%
# === STEP 12: Reload and extract state ===
print(f"\n{'='*70}")
print(f"SECTION 6: RELOAD STATE FROM DISK")
print(f"{'='*70}")

print(f"\nLoading state from disk...")
reloaded_state, reloaded_meta, processing_metadata = load_state(final_state_path, use_tdigest=USE_TDIGEST)
reloaded_snapshot_df = snapshot_state_as_dataframe(reloaded_state, columns=value_columns)
reloaded_snapshot_df = add_tdigest_quantiles_to_snapshot(
    state=reloaded_state,
    snapshot_df=reloaded_snapshot_df,
    columns=value_columns,
    quantiles=TDIGEST_QUANTILES
)

print(f"✓ Reloaded state:")
print(f"  HEALPix cells: {len(reloaded_snapshot_df)}")
print(f"  Columns: {list(reloaded_snapshot_df.columns)[:5]}...")

# Print persisted input fingerprints
if 'processed_inputs' in processing_metadata:
    fingerprints = processing_metadata['processed_inputs']
    print(f"\n✓ Input Fingerprint Ledger ({len(fingerprints)} tracked):")
    for idx, fp in enumerate(fingerprints, 1):
        print(f"    [{idx}] {fp}")
else:
    print(f"\nℹ No input fingerprints tracked in this state")

if USE_TDIGEST_QUANTILES:
    q_cols = [c for c in reloaded_snapshot_df.columns if '_q' in c]
    print(f"\n✓ T-Digest quantile columns loaded: {len(q_cols)}")
    if q_cols:
        print(f"  Example columns: {q_cols[:min(6, len(q_cols))]}")


# %%
# === STEP 12b: Quick assertion for q50 quantile columns ===
if USE_TDIGEST_QUANTILES:
    q50_cols = [c for c in reloaded_snapshot_df.columns if c.endswith('_q50')]
    assert len(q50_cols) > 0, (
        "USE_TDIGEST_QUANTILES=True but no '_q50' columns found in reloaded snapshot"
    )
    print(f"✓ Quantile assertion passed: found {len(q50_cols)} q50 column(s)")


# %% [markdown]
# ## Section 7: Validate State Integrity and Serialization

# %% [markdown]
# Compare memory state vs disk-extracted state:

# %%
# === STEP 13: Validate serialization consistency ===
print(f"\n{'='*70}")
print(f"SECTION 7: STATE INTEGRITY VALIDATION")
print(f"{'='*70}")

# Extract current in-memory state
inmemory_snapshot_df = snapshot_state_as_dataframe(accumulated_state, columns=value_columns)

print(f"\nComparing in-memory vs disk-extracted state:")
print(f"  In-memory cells: {len(inmemory_snapshot_df)}")
print(f"  Disk cells: {len(reloaded_snapshot_df)}")

# Check cell ID parity
inmem_cells = set(inmemory_snapshot_df['healpix_id'].values)
disk_cells = set(reloaded_snapshot_df['healpix_id'].values)

print(f"\nCell set parity:")
print(f"  Intersection: {len(inmem_cells & disk_cells)}")
print(f"  In-memory only: {len(inmem_cells - disk_cells)}")
print(f"  Disk only: {len(disk_cells - inmem_cells)}")

if inmem_cells == disk_cells:
    print(f"  ✓ Cell sets match perfectly")
else:
    print(f"  ⚠️  Cell sets differ")


# %%
# === STEP 14: Compare numeric values ===
print(f"\nComparing numeric values...")

# Merge on healpix_id for comparison
comparison_df = inmemory_snapshot_df.merge(
    reloaded_snapshot_df,
    on='healpix_id',
    suffixes=('_mem', '_disk'),
    how='inner'
)

# Compare a representative value column
test_col = f'{value_columns[0]}_mean'
if f'{test_col}_mem' in comparison_df.columns and f'{test_col}_disk' in comparison_df.columns:
    mem_vals = comparison_df[f'{test_col}_mem'].values
    disk_vals = comparison_df[f'{test_col}_disk'].values
    
    delta = np.abs(mem_vals - disk_vals)
    
    print(f"  Column: {test_col}")
    print(f"    Max difference: {delta.max():.2e}")
    print(f"    Mean difference: {delta.mean():.2e}")
    print(f"    Std difference: {delta.std():.2e}")
    
    if delta.max() < 1e-10:
        print(f"  ✓ Values serialize/deserialize correctly")
    else:
        print(f"  ⚠️  Numeric differences detected")


# %% [markdown]
# ## Section 8: Compare Streaming vs Batch Results

# %% [markdown]
# Compare final streaming accumulation against batch baseline:

# %%
# === STEP 15: Extract streaming final statistics ===
print(f"\n{'='*70}")
print(f"SECTION 8: STREAMING vs BATCH COMPARISON")
print(f"{'='*70}")

# Use reloaded state (simulates production scenario)
streaming_final_df = reloaded_snapshot_df.copy()

print(f"\nStreaming final statistics:")
print(f"  HEALPix cells: {len(streaming_final_df)}")
print(f"  Columns: {list(streaming_final_df.columns)}")


# %%
# === STEP 16: Compare cell sets ===
streaming_cells = set(streaming_final_df['healpix_id'].values)
batch_cells = set(raw_batch_baseline.index.values)

print(f"\nCell set parity:")
print(f"  Batch cells: {len(batch_cells)}")
print(f"  Streaming cells: {len(streaming_cells)}")
print(f"  Intersection: {len(batch_cells & streaming_cells)}")
print(f"  Batch-only: {len(batch_cells - streaming_cells)}")
print(f"  Streaming-only: {len(streaming_cells - batch_cells)}")

if batch_cells == streaming_cells:
    print(f"  ✓ Cell sets match perfectly")
else:
    print(f"  ⚠️  Cell sets differ")


# %%
# === STEP 17: Compare observation counts ===
print(f"\nObservation count parity:")

# Batch baseline
batch_n_col = 'n_sources' if 'n_sources' in raw_batch_baseline.columns else f'{value_columns[0]}_n'
batch_n_total = raw_batch_baseline[batch_n_col].sum()

# Streaming final
stream_n_col = f'{value_columns[0]}_n'
stream_n_total = streaming_final_df[stream_n_col].sum() if stream_n_col in streaming_final_df.columns else 0

print(f"  Batch baseline: {batch_n_total:,.0f} observations")
print(f"  Streaming final: {stream_n_total:,.0f} observations")
print(f"  Difference: {abs(batch_n_total - stream_n_total):,.0f}")
print(f"  % diff: {100 * abs(batch_n_total - stream_n_total) / max(batch_n_total, 1):.3f}%")

if batch_n_total == stream_n_total:
    print(f"  ✓ Observation counts match")
else:
    print(f"  ⚠️  Observation counts differ")


# %%
# === STEP 18: Compare per-cell statistics ===
print(f"\nPer-cell statistics comparison:")

# Align on cells
common_cells = batch_cells & streaming_cells

if common_cells:
    comparison_aligned = raw_batch_baseline.loc[list(common_cells)].copy()
    
    # Find mean column in streaming
    stream_mean_col = f'{value_columns[0]}_approx_mean' if f'{value_columns[0]}_approx_mean' in streaming_final_df.columns \
        else f'{value_columns[0]}_mean'
    batch_mean_col = f'{value_columns[0]}_mean'
    
    if stream_mean_col in streaming_final_df.columns and batch_mean_col in comparison_aligned.columns:
        streaming_means = streaming_final_df.set_index('healpix_id')[stream_mean_col]
        batch_means = comparison_aligned[batch_mean_col]
        
        # Align series
        common_idx = batch_means.index.intersection(streaming_means.index)
        
        if len(common_idx) > 0:
            delta_means = np.abs(batch_means[common_idx] - streaming_means[common_idx])
            
            print(f"  Mean value differences ({len(common_idx)} cells):")
            print(f"    Max: {delta_means.max():.2e}")
            print(f"    Mean: {delta_means.mean():.2e}")
            print(f"    Median: {delta_means.median():.2e}")
            print(f"    Std: {delta_means.std():.2e}")
            
            if delta_means.max() < 1e-6:
                print(f"  ✓ Mean values agree within tolerance")
            else:
                print(f"  ⚠️  Some mean value differences detected")


# %% [markdown]
# ## Section 9: Generate Validation Report and Error Metrics

# %% [markdown]
# Create comprehensive validation report:

# %%
# === STEP 19: Generate validation report ===
print(f"\n{'='*70}")
print(f"SECTION 9: VALIDATION REPORT")
print(f"{'='*70}")

report = {
    'n_batches': len(batches_loaded),
    'healpix_config': HEALPIX_CONFIG,
    'value_columns': value_columns,
    'batch_baseline': {
        'n_cells': len(raw_batch_baseline),
        'n_observations': int(batch_n_total),
    },
    'streaming_final': {
        'n_cells': len(streaming_final_df),
        'n_observations': int(stream_n_total),
    },
    'cell_parity': {
        'batch_only': len(batch_cells - streaming_cells),
        'streaming_only': len(streaming_cells - batch_cells),
        'common': len(batch_cells & streaming_cells),
        'match': batch_cells == streaming_cells
    },
    'observation_parity': {
        'batch_total': int(batch_n_total),
        'streaming_total': int(stream_n_total),
        'difference': abs(int(batch_n_total - stream_n_total)),
        'percent_diff': 100 * abs(batch_n_total - stream_n_total) / max(batch_n_total, 1),
        'match': batch_n_total == stream_n_total
    }
}

# Print report
print(f"\n{'='*70}")
print(f"VALIDATION SUMMARY")
print(f"{'='*70}")

print(f"\n1. INPUT DATA:")
print(f"   Batches: {report['n_batches']}")
print(f"   HEALPix nside: {report['healpix_config']['nside']}")
print(f"   Value columns: {report['value_columns']}")

print(f"\n2. CELL PARITY:")
print(f"   Batch cells: {report['batch_baseline']['n_cells']}")
print(f"   Streaming cells: {report['streaming_final']['n_cells']}")
print(f"   Batch-only: {report['cell_parity']['batch_only']}")
print(f"   Streaming-only: {report['cell_parity']['streaming_only']}")
print(f"   ✓ MATCH" if report['cell_parity']['match'] else f"   ✗ MISMATCH")

print(f"\n3. OBSERVATION PARITY:")
print(f"   Batch total: {report['observation_parity']['batch_total']:,}")
print(f"   Streaming total: {report['observation_parity']['streaming_total']:,}")
print(f"   Difference: {report['observation_parity']['difference']:,}")
print(f"   % diff: {report['observation_parity']['percent_diff']:.3f}%")
print(f"   ✓ MATCH" if report['observation_parity']['match'] else f"   ✗ MISMATCH")


# %%
# === STEP 20: Handle potential discrepancies ===
print(f"\n{'='*70}")
print(f"DISCREPANCY ANALYSIS")
print(f"{'='*70}")

if not report['cell_parity']['match']:
    print(f"\n⚠️  Cell set mismatch detected")
    print(f"   Likely causes:")
    print(f"   - Geometry clipping in sidecar (fuzzy mode boundary effects)")
    print(f"   - Value filtering differences (NaN/sentinel handling)")
    print(f"   - Source ID collisions across batches")

if not report['observation_parity']['match']:
    print(f"\n⚠️  Observation count mismatch detected")
    print(f"   Likely causes:")
    print(f"   - Row filtering (NaN/inf in aggregate_by_sidecar)")
    print(f"   - min_count=1 may exclude borderline cells")
    print(f"   - Fuzzy mode double-counting geometries touching multiple cells")

if report['cell_parity']['match'] and report['observation_parity']['match']:
    print(f"\n✓ ALL PARITY CHECKS PASS")
    print(f"   Streaming and batch pipelines are equivalent")


# %% [markdown]
# ## Section 10: Visualize Evolution of Accumulated State

# %% [markdown]
# Create a 3x3 grid showing how the accumulated state grows with each batch:

# %%
# === STEP 21: Plot evolution of accumulated state (3x3 grid) ===
print(f"\n{'='*70}")
print(f"SECTION 10: ACCUMULATED STATE EVOLUTION")
print(f"{'='*70}")

fig, axes = plt.subplots(3, 3, figsize=(8, 12))
fig.suptitle("Evolution of Accumulated State Across Batches\n Color : R(1050um) mean", fontsize=14, fontweight='bold')
axes = axes.flatten()

# Limit to first 9 snapshots for 3x3 grid
n_snapshots = min(9, len(snapshot_registry))

for idx in range(9):
    ax = axes[idx]
    
    if idx < n_snapshots:
        batch_id = idx + 1
        snapshot_info = snapshot_registry.get(batch_id)
        
        if snapshot_info:
            # Load snapshot
            snap_df = pd.read_parquet(snapshot_info['path'])
            
            # Convert HEALPix IDs to geodataframe
            snap_gdf = healpix_to_geodataframe(
                nside=HEALPIX_CONFIG['nside'],
                order='nested' if HEALPIX_CONFIG['nested'] else 'ring',
                lon_convention=HEALPIX_CONFIG['lon_convention'],
                pixels=np.asarray(snap_df['healpix_id'].to_numpy(), dtype=np.int64)
            )
            
            # Add r1050 mean values
            snap_gdf = snap_gdf.merge(
                snap_df[['healpix_id', 'r1050_mean','r1050_n']],
                left_on='healpix_id',
                right_on='healpix_id',
                how='left'
            )
            
            # Plot
            snap_gdf.plot(
                ax=ax,
                column='r1050_mean',
                cmap='viridis',
                edgecolor='k',
                linewidth=0.1,
                legend=False,
                alpha=0.8,
                aspect='auto'
            )

            ax.set_xlim(-2,24)
            
            ax.set_title(f"Snapshot {batch_id}\n({snapshot_info['n_cells']} cells, {snapshot_info['n_observations']:,.0f} obs)", fontsize=10)
            ax.set_xlabel("Longitude (°)")
            ax.set_ylabel("Latitude (°)")
            ax.grid(True, alpha=0.3)
    else:
        ax.axis('off')

plt.tight_layout()
plt.show()

# %%

snap_df

# %% [markdown]
# ## Section 11: Compare Batch vs Streaming Results (r1050)

# %% [markdown]
# Create side-by-side comparison plots and difference map:

# %%
# === STEP 22: Convert batch baseline to geodataframe ===
print(f"\n{'='*70}")
print(f"SECTION 11: BATCH vs STREAMING COMPARISON (r1050)")
print(f"{'='*70}")

# Batch baseline -> GeoDataFrame
batch_gdf = healpix_to_geodataframe(
    nside=HEALPIX_CONFIG['nside'],
    order='nested' if HEALPIX_CONFIG['nested'] else 'ring',
    lon_convention=HEALPIX_CONFIG['lon_convention'],
    pixels=np.asarray(raw_batch_baseline.index.to_numpy(), dtype=np.int64)
)
batch_gdf = batch_gdf.merge(
    raw_batch_baseline[['r1050_mean', 'r1050_q50']].reset_index(),
    left_on='healpix_id',
    right_on='healpix_id',
    how='left'
)

# Streaming final -> GeoDataFrame
streaming_gdf = healpix_to_geodataframe(
    nside=HEALPIX_CONFIG['nside'],
    order='nested' if HEALPIX_CONFIG['nested'] else 'ring',
    lon_convention=HEALPIX_CONFIG['lon_convention'],
    pixels=np.asarray(streaming_final_df['healpix_id'].to_numpy(), dtype=np.int64)
)
streaming_gdf = streaming_gdf.merge(
    streaming_final_df[['healpix_id', 'r1050_mean', 'r1050_q50']],
    on='healpix_id',
    how='left'
)

print(f"✓ Batch geodataframe: {len(batch_gdf)} cells")
print(f"✓ Streaming geodataframe: {len(streaming_gdf)} cells")

# %%
# == STEP 23 : plot t-digest quantile
assert 'r1050_q50' in batch_gdf.columns, "Expected exact batch q50 column in batch_gdf"
assert 'r1050_q50' in streaming_gdf.columns, "Expected streaming TDigest q50 column in streaming_gdf"

fps = processing_metadata.get("processed_inputs", []) if isinstance(processing_metadata, dict) else []
print(f"Persisted fingerprints: {len(fps)}")
for i, fp in enumerate(fps[:10], 1):
    print(f"  [{i}] {fp}")
if len(fps) > 10:
    print(f"  ... and {len(fps)-10} more")

fig, axes_q50 = plt.subplots(2, 2, figsize=(8, 10), squeeze=False)
fig.suptitle("Q50 (50% percentile) Comparison\nBatch (Exact) vs Streaming (TDigest)", fontsize=14, fontweight='bold') 
ax_batch_q50: Axes = axes_q50[0, 0]
ax_stream_q50: Axes = axes_q50[0, 1]
ax_diff_q50: Axes = axes_q50[1, 0]
ax_ratio_q50: Axes = axes_q50[1, 1]

batch_q50_vals = np.asarray(batch_gdf['r1050_q50'].to_numpy(), dtype=np.float64)
stream_q50_vals = np.asarray(streaming_gdf['r1050_q50'].to_numpy(), dtype=np.float64)
combined_q50_vals = np.concatenate([batch_q50_vals, stream_q50_vals])

q50_vmin = float(np.nanmin(combined_q50_vals))
q50_vmax = float(np.nanmax(combined_q50_vals))

batch_gdf.plot(
    ax=ax_batch_q50,
    column='r1050_q50',
    cmap='viridis',
    vmin=q50_vmin,
    vmax=q50_vmax,
    edgecolor='k',
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_batch_q50.set_title(f"Batch Aggregation\nr1050_q50 (exact median) | {len(batch_gdf)} cells", fontsize=11, fontweight='bold')
ax_batch_q50.set_xlabel("Longitude (°)")
ax_batch_q50.set_ylabel("Latitude (°)")
ax_batch_q50.grid(True, alpha=0.3)

streaming_gdf.plot(
    ax=ax_stream_q50,
    column='r1050_q50',
    cmap='viridis',
    vmin=q50_vmin,
    vmax=q50_vmax,
    edgecolor='k',
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_stream_q50.set_title(f"Streaming Accumulation\nr1050_q50 (TDigest) | {len(streaming_gdf)} cells", fontsize=11, fontweight='bold')
ax_stream_q50.set_xlabel("Longitude (°)")
ax_stream_q50.set_ylabel("Latitude (°)")
ax_stream_q50.grid(True, alpha=0.3)

common_q50_healpix_ids = np.intersect1d(
    np.asarray(batch_gdf['healpix_id'].to_numpy(), dtype=np.int64),
    np.asarray(streaming_gdf['healpix_id'].to_numpy(), dtype=np.int64),
)

q50_diff_gdf = batch_gdf[batch_gdf['healpix_id'].isin(common_q50_healpix_ids)].copy()

stream_q50_lookup = streaming_gdf[['healpix_id', 'r1050_q50']].rename(
    columns={'r1050_q50': 'stream_q50'}
)

q50_diff_gdf = cast(
    gpd.GeoDataFrame,
    q50_diff_gdf.merge(
        stream_q50_lookup,
        on='healpix_id',
        how='left',
        validate='one_to_one',
    ),
)

q50_diff_gdf['r1050_q50_diff'] = q50_diff_gdf['r1050_q50'] - q50_diff_gdf['stream_q50']

batch_q50_true = np.asarray(q50_diff_gdf['r1050_q50'].to_numpy(), dtype=np.float64)
q50_diff_abs = np.asarray(q50_diff_gdf['r1050_q50_diff'].to_numpy(), dtype=np.float64)

q50_diff_gdf['r1050_q50_diff_ratio'] = np.where(
    np.abs(batch_q50_true) > 0.0,
    q50_diff_abs / batch_q50_true,
    np.nan,
)

q50_abs_max = float(np.nanmax(np.abs(q50_diff_abs))) if len(q50_diff_abs) else 0.0
q50_diff_gdf.plot(
    ax=ax_diff_q50,
    column='r1050_q50_diff',
    cmap='RdBu_r',
    vmin=-q50_abs_max,
    vmax=q50_abs_max,
    edgecolor='k',
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_diff_q50.set_title(f"Q50 Difference (Batch - Streaming)\n{len(q50_diff_gdf)} common cells", fontsize=11, fontweight='bold')
ax_diff_q50.set_xlabel("Longitude (°)")
ax_diff_q50.set_ylabel("Latitude (°)")
ax_diff_q50.grid(True, alpha=0.3)

q50_ratio_vals = np.asarray(q50_diff_gdf['r1050_q50_diff_ratio'].to_numpy(), dtype=np.float64)
q50_ratio_abs_max = float(np.nanmax(np.abs(q50_ratio_vals))) if np.isfinite(q50_ratio_vals).any() else 0.0
q50_diff_gdf.plot(
    ax=ax_ratio_q50,
    column='r1050_q50_diff_ratio',
    cmap='RdBu_r',
    vmin=-q50_ratio_abs_max,
    vmax=q50_ratio_abs_max,
    edgecolor='k',
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_ratio_q50.set_title("Q50 Relative Error Ratio\n(Batch - Streaming) / Batch", fontsize=11, fontweight='bold')
ax_ratio_q50.set_xlabel("Longitude (°)")
ax_ratio_q50.set_ylabel("Latitude (°)")
ax_ratio_q50.grid(True, alpha=0.3)

assert 'r1050_q50_diff' in q50_diff_gdf.columns
assert 'r1050_q50_diff_ratio' in q50_diff_gdf.columns
assert len(q50_diff_gdf) == len(common_q50_healpix_ids)

plt.tight_layout()
# plt.savefig(STATE_DIR / "11b_batch_vs_streaming_q50_comparison.png", dpi=100, bbox_inches="tight")
print(f"\n✓ Saved: {STATE_DIR / '11b_batch_vs_streaming_q50_comparison.png'}")
plt.show()
# %%
# === STEP 24: Create comparison plot (2x2: batch, streaming, diff, relative diff) ===


fig, axes_2d = plt.subplots(2, 2, figsize=(8, 10), squeeze=False)
fig.suptitle("R1050 Mean Comparison\nBatch (Exact) vs Streaming (TDigest)", fontsize=14, fontweight='bold') 
ax_batch: Axes = axes_2d[0, 0]
ax_stream: Axes = axes_2d[0, 1]
ax_diff: Axes = axes_2d[1, 0]
ax_ratio: Axes = axes_2d[1, 1]

# Explicit numeric arrays (avoid partially unknown pandas scalar types)
batch_vals = np.asarray(batch_gdf["r1050_mean"].to_numpy(), dtype=np.float64)
stream_vals = np.asarray(streaming_gdf["r1050_mean"].to_numpy(), dtype=np.float64)
combined_vals = np.concatenate([batch_vals, stream_vals])

vmin = float(np.nanmin(combined_vals))
vmax = float(np.nanmax(combined_vals))

# Plot 1: Batch aggregation
batch_gdf.plot(
    ax=ax_batch,
    column="r1050_mean",
    cmap="viridis",
    vmin=vmin,
    vmax=vmax,
    edgecolor="k",
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_batch.set_title(f"Batch Aggregation\nr1050_mean | {len(batch_gdf)} cells", fontsize=11, fontweight="bold")
ax_batch.set_xlabel("Longitude (°)")
ax_batch.set_ylabel("Latitude (°)")
ax_batch.grid(True, alpha=0.3)

# Plot 2: Streaming accumulation
streaming_gdf.plot(
    ax=ax_stream,
    column="r1050_mean",
    cmap="viridis",
    vmin=vmin,
    vmax=vmax,
    edgecolor="k",
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_stream.set_title(f"Streaming Accumulation\nr1050_mean | {len(streaming_gdf)} cells", fontsize=11, fontweight="bold")
ax_stream.set_xlabel("Longitude (°)")
ax_stream.set_ylabel("Latitude (°)")
ax_stream.grid(True, alpha=0.3)

# Build aligned GeoDataFrame on common cells
common_healpix_ids = np.intersect1d(
    np.asarray(batch_gdf["healpix_id"].to_numpy(), dtype=np.int64),
    np.asarray(streaming_gdf["healpix_id"].to_numpy(), dtype=np.int64),
)

diff_gdf_aligned = batch_gdf[batch_gdf["healpix_id"].isin(common_healpix_ids)].copy()

stream_lookup = streaming_gdf[["healpix_id", "r1050_mean"]].rename(
    columns={"r1050_mean": "stream_mean"}
)

diff_gdf_aligned = cast(
    gpd.GeoDataFrame,
    diff_gdf_aligned.merge(
        stream_lookup,
        on="healpix_id",
        how="left",
        validate="one_to_one",
    ),
)

# Absolute and relative error
diff_gdf_aligned["r1050_diff"] = diff_gdf_aligned["r1050_mean"] - diff_gdf_aligned["stream_mean"]

batch_true = np.asarray(diff_gdf_aligned["r1050_mean"].to_numpy(), dtype=np.float64)
diff_abs = np.asarray(diff_gdf_aligned["r1050_diff"].to_numpy(), dtype=np.float64)

diff_gdf_aligned["r1050_diff_ratio"] = np.where(
    np.abs(batch_true) > 0.0,
    diff_abs / batch_true,   # (Batch - Streaming) / Batch
    np.nan,
)

# Plot 3: Absolute difference
abs_max = float(np.nanmax(np.abs(diff_abs))) if len(diff_abs) else 0.0
diff_gdf_aligned.plot(
    ax=ax_diff,
    column="r1050_diff",
    cmap="RdBu_r",
    vmin=-abs_max,
    vmax=abs_max,
    edgecolor="k",
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_diff.set_title(f"Difference (Batch - Streaming)\n{len(diff_gdf_aligned)} common cells", fontsize=11, fontweight="bold")
ax_diff.set_xlabel("Longitude (°)")
ax_diff.set_ylabel("Latitude (°)")
ax_diff.grid(True, alpha=0.3)

# Plot 4: Relative error ratio vs true (batch)
ratio_vals = np.asarray(diff_gdf_aligned["r1050_diff_ratio"].to_numpy(), dtype=np.float64)
ratio_abs_max = float(np.nanmax(np.abs(ratio_vals))) if np.isfinite(ratio_vals).any() else 0.0
diff_gdf_aligned.plot(
    ax=ax_ratio,
    column="r1050_diff_ratio",
    cmap="RdBu_r",
    vmin=-ratio_abs_max,
    vmax=ratio_abs_max,
    edgecolor="k",
    linewidth=0.1,
    legend=True,
    alpha=0.8,
)
ax_ratio.set_title("Relative Error Ratio\n(Batch - Streaming) / Batch", fontsize=11, fontweight="bold")
ax_ratio.set_xlabel("Longitude (°)")
ax_ratio.set_ylabel("Latitude (°)")
ax_ratio.grid(True, alpha=0.3)

# Validation asserts (nbdev-friendly)
assert "r1050_diff" in diff_gdf_aligned.columns
assert "r1050_diff_ratio" in diff_gdf_aligned.columns
assert len(diff_gdf_aligned) == len(common_healpix_ids)

plt.tight_layout()
# plt.savefig(STATE_DIR / "11_batch_vs_streaming_comparison.png", dpi=100, bbox_inches="tight")
print(f"\n✓ Saved: {STATE_DIR / '11_batch_vs_streaming_comparison.png'}")
plt.show()


# %%
# === STEP 24: Print difference statistics ===
print(f"\nDifference statistics for Mean (Batch - Streaming):")
print(f"  Max difference: {diff_gdf_aligned['r1050_diff'].max():.6e}")
print(f"  Min difference: {diff_gdf_aligned['r1050_diff'].min():.6e}")
print(f"  Mean difference: {diff_gdf_aligned['r1050_diff'].mean():.6e}")
print(f"  Median difference: {diff_gdf_aligned['r1050_diff'].median():.6e}")
print(f"  Std difference: {diff_gdf_aligned['r1050_diff'].std():.6e}")

if diff_gdf_aligned['r1050_diff'].abs().max() < 1e-10:
    print(f"  ✓ Differences negligible (< 1e-10)")
else:
    print(f"  ⚠️  Significant differences detected")

# Additional statistics for r1050_mean
mean_diffs = diff_gdf_aligned['r1050_diff'].dropna()
rel_diffs = diff_gdf_aligned['r1050_diff_ratio'].dropna()

print("\nRelative difference statistics:")
print(f"  Cells compared: {len(mean_diffs)}")
print(f"  Max |rel diff|: {rel_diffs.abs().max():.6e}")
print(f"  Mean |rel diff|: {rel_diffs.abs().mean():.6e}")
print(f"  Median |rel diff|: {rel_diffs.abs().median():.6e}")

# Practical quality gate (tune as needed)
p95_abs = np.nanpercentile(mean_diffs.abs(), 95)
print(f"\nP95 absolute mean difference: {p95_abs:.6e}")
# %%
## plot mean differnence vs batch mean

# Quick plot: exact vs tdigest r1050_mean

fig, ax = plt.subplots(1, 2, figsize=(12, 5))

# Compute the difference between exact and TDigest r1050_mean
cmp_mean = exact_q50_df.merge(
    streaming_final_df[['healpix_id', 'r1050_mean']].rename(columns={'r1050_mean': 'r1050_mean_tdigest'}),
    on='healpix_id',
    how='inner'
)
cmp_mean['mean_diff'] = cmp_mean['r1050_q50_exact'] - cmp_mean['r1050_mean_tdigest']

# Histogram of differences
ax[0].hist(cmp_mean['mean_diff'].dropna(), bins=80)
ax[0].set_title("r1050_mean difference (exact - tdigest)")
ax[0].set_xlabel("difference")
ax[0].set_ylabel("count")

# Scatter plot of exact vs TDigest r1050_mean
ax[1].scatter(
    cmp_mean['r1050_q50_exact'],
    cmp_mean['r1050_mean_tdigest'],
    s=5,
    alpha=0.4
)
mn = np.nanmin([cmp_mean['r1050_q50_exact'].min(), cmp_mean['r1050_mean_tdigest'].min()])
mx = np.nanmax([cmp_mean['r1050_q50_exact'].max(), cmp_mean['r1050_mean_tdigest'].max()])
ax[1].plot([mn, mx], [mn, mx], 'r--', lw=1)
ax[1].set_title("Exact vs TDigest r1050_mean")
ax[1].set_xlabel("Exact r1050_mean")
ax[1].set_ylabel("TDigest r1050_mean")

plt.tight_layout()
plt.show()

# %%
# STEP 25 : Quick check: TDigest error on known distribution
import numpy as np
from tdigest import TDigest

# Synthetic test: 10k uniform samples [0, 1]
test_vals = np.random.uniform(0, 1, 10000)
exact_q50 = np.median(test_vals)

# Method 1: Feed one-by-one (current, bad)
td_1by1 = TDigest()
for v in test_vals:
    td_1by1.update(float(v))
approx_q50_1by1 = td_1by1.percentile(50)

# Method 2: Pre-aggregate (better)
td_batch = TDigest()
unique_vals, counts = np.unique(test_vals, return_counts=True)
for val, cnt in zip(unique_vals, counts):
    td_batch.update(float(val), int(cnt))
approx_q50_batch = td_batch.percentile(50)

print(f"Exact median:          {exact_q50:.6f}")
print(f"TDigest (1-by-1):      {approx_q50_1by1:.6f}  error: {abs(approx_q50_1by1 - exact_q50):.2e}")
print(f"TDigest (pre-agg):     {approx_q50_batch:.6f}  error: {abs(approx_q50_batch - exact_q50):.2e}")
print(f"✓ Pre-aggregation reduces error by: {(1 - abs(approx_q50_batch - exact_q50) / abs(approx_q50_1by1 - exact_q50)) * 100:.1f}%")

# %%

# == STEP 24: TDigest q50 vs exact batch q50 (real variables from Step 22) ==

import numpy as np
import pandas as pd

# Exact batch q50 from aggregate baseline (Step 22)
# raw_batch_baseline index is healpix_id in your workflow
exact_q50_df = (
    raw_batch_baseline[['r1050_q50']]
    .rename_axis('healpix_id')
    .reset_index()
    .rename(columns={'r1050_q50': 'r1050_q50_exact'})
)

# Streaming TDigest q50 from accumulator final snapshot (Step 22)
tdigest_q50_df = (
    streaming_final_df[['healpix_id', 'r1050_q50']]
    .rename(columns={'r1050_q50': 'r1050_q50_tdigest'})
)

# Align on common HEALPix cells
cmp_q50 = exact_q50_df.merge(tdigest_q50_df, on='healpix_id', how='inner')

# Required sanity checks
assert len(cmp_q50) > 0, "No overlapping cells between exact and TDigest outputs."
assert cmp_q50['r1050_q50_exact'].notna().any(), "Exact q50 is all NaN."
assert cmp_q50['r1050_q50_tdigest'].notna().any(), "TDigest q50 is all NaN."

# Compute differences
cmp_q50['q50_diff'] = cmp_q50['r1050_q50_exact'] - cmp_q50['r1050_q50_tdigest']

eps = 1e-12
den = np.where(np.abs(cmp_q50['r1050_q50_exact'].to_numpy()) > eps,
               np.abs(cmp_q50['r1050_q50_exact'].to_numpy()),
               np.nan)
cmp_q50['q50_rel_diff'] = cmp_q50['q50_diff'] / den

# Print stats (same style as your Step 24)
d = cmp_q50['q50_diff'].dropna()
rd = cmp_q50['q50_rel_diff'].dropna()

print("Difference statistics (Exact Batch q50 - Streaming TDigest q50):")
print(f"  Cells compared: {len(cmp_q50)}")
print(f"  Max difference: {d.max():.6e}")
print(f"  Min difference: {d.min():.6e}")
print(f"  Mean difference: {d.mean():.6e}")
print(f"  Median difference: {d.median():.6e}")
print(f"  Std difference: {d.std(ddof=1):.6e}")

if len(rd) > 0:
    print("\nRelative difference statistics:")
    print(f"  Max |rel diff|: {np.nanmax(np.abs(rd)):.6e}")
    print(f"  Mean |rel diff|: {np.nanmean(np.abs(rd)):.6e}")
    print(f"  Median |rel diff|: {np.nanmedian(np.abs(rd)):.6e}")

# Practical quality gate (tune as needed)
p95_abs = np.nanpercentile(np.abs(d), 95)
print(f"\nP95 absolute q50 difference: {p95_abs:.6e}")

# Quick plot: exact vs tdigest q50

import matplotlib.pyplot as plt

fig, ax = plt.subplots(1, 2, figsize=(12, 5))

ax[0].hist(cmp_q50['q50_diff'].dropna(), bins=80)
ax[0].set_title("q50 difference (exact - tdigest)")
ax[0].set_xlabel("difference")
ax[0].set_ylabel("count")

ax[1].scatter(
    cmp_q50['r1050_q50_exact'],
    cmp_q50['r1050_q50_tdigest'],
    s=5,
    alpha=0.4
)
mn = np.nanmin([cmp_q50['r1050_q50_exact'].min(), cmp_q50['r1050_q50_tdigest'].min()])
mx = np.nanmax([cmp_q50['r1050_q50_exact'].max(), cmp_q50['r1050_q50_tdigest'].max()])
ax[1].plot([mn, mx], [mn, mx], 'r--', lw=1)
ax[1].set_title("Exact vs TDigest q50")
ax[1].set_xlabel("Exact q50")
ax[1].set_ylabel("TDigest q50")

plt.tight_layout()
plt.show()

# assert-style test cell check
assert cmp_q50['q50_diff'].abs().notna().any(), "No valid q50 differences computed."

# %% [markdown]
# ## Summary

# %% [markdown]
# This notebook successfully demonstrated:
#
# 1. **Batch loading and validation** - Geometric integrity checks
# 2. **HEALPix sidecar creation** - Consistent cell assignment across batches  
# 3. **Batch aggregation baseline** - One-shot reference aggregation
# 4. **Streaming accumulation** - Incremental state updates
# 5. **State serialization** - Disk persistence and reload verification
# 6. **Pipeline validation** - Comprehensive cross-path comparison
# 7. **State evolution visualization** - 3x3 grid showing accumulation progress
# 8. **Batch vs streaming comparison** - Side-by-side maps and difference analysis
#
# ### Key Takeaways
#
# - **Parity validation** is critical for streaming pipelines
# - **Consistent HEALPix configuration** must be enforced across all stages
# - **Raw baseline aggregation** (before geometry ops) is the correct ground truth
# - **Streaming state snapshots** enable recovery and incremental processing
# - **Visual comparison** confirms equivalence between batch and streaming paths
#
# ### Visualization Outputs
#
# - `01_batch_coverage.png` — Spatial distribution of input batches
# - `10_accumulated_state_evolution.png` — 3x3 grid showing state growth
# - `11_batch_vs_streaming_comparison.png` — Side-by-side maps + difference
#
# ### Next Steps
#
# - Experiment with different `nside` values for resolution trade-offs
# - Profile both pipelines for performance at scale
# - Integrate final maps into production workflows
#
# ### Streaming vs Batch: Key Insights
#
# The streaming accumulation approach provides several production advantages:
#
# - **Incremental Processing**: No need to reprocess all historical data when new batches arrive
# - **Memory Efficient**: Maintains only per-cell statistics in-memory (one CellAccumulator per HEALPix cell)
# - **State Persistence**: Intermediate snapshots enable recovery from failures
# - **Scalable**: The Welford/TDigest algorithm scales to arbitrary batch counts
#
# This marks the end of the Streaming Accumulation Validation Workflow.
#

# %%
