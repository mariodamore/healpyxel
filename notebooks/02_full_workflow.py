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
# # Full Workflow

# %%
#| echo: false
import healpyxel
from healpyxel import core
import pandas as pd
import numpy as np

# %% [markdown]
# ## Test Data
#
# Check available test data in the package

# %%
#| code-fold: true
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
# ## Quick Test with Sample Data
#
# If test data is available, let's try a quick aggregation

# %%
#| echo: false

# Check for sample data
sample_file = test_data_dir / 'samples/sample_50k.parquet'

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

# %%
#| echo: false
import duckdb
from math import isnan
import pathlib
stats = []
# reuse/clear existing stats list
stats.clear()

# print("\nInspecting all parquet files in test data directory and collecting lat/lon stats:")
for parquet_file in test_data_dir.rglob('*.parquet'):
    size_mb = parquet_file.stat().st_size / 1024 / 1024
    rel = parquet_file.relative_to(test_data_dir)
    try:
        q = (
            f"SELECT COUNT(*) AS n_rows, "
            f"MIN(lat_center) AS lat_min, MAX(lat_center) AS lat_max, "
            f"MIN(lon_center) AS lon_min, MAX(lon_center) AS lon_max "
            f"FROM read_parquet('{parquet_file.as_posix()}')"
        )
        n_rows, lat_min, lat_max, lon_min, lon_max = duckdb.query(q).fetchone()
    except Exception:
        # If lat_center/lon_center missing or another error, still try to get row count
        try:
            n_rows = duckdb.query(f"SELECT COUNT(*) FROM read_parquet('{parquet_file.as_posix()}')").fetchone()[0]
        except Exception:
            n_rows = None
        lat_min = lat_max = lon_min = lon_max = None

    stats.append({
        "file": str(rel),
        "size_mb": size_mb,
        "n_rows": n_rows,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lon_min": lon_min,
        "lon_max": lon_max,
    })
    # print(f"  {rel}: {size_mb:.2f} MB, {n_rows} rows")

# Aggregate into a dataframe and display
df_stats = pd.DataFrame(stats)
df_stats['filename'] = df_stats['file'].apply(lambda x: pathlib.Path(x).stem)
display(df_stats)

# %% [markdown]
# Those are the boundaries used to sample the initial data

# %%
#| echo: false

# convert to geopandas creating a polygon box with lat_min	lat_max	lon_min	lon_max per file
import geopandas as gpd
from shapely.geometry import box
df_stats['geometry'] = df_stats.apply(
    lambda row: box(row['lon_min'], row['lat_min'], row['lon_max'], row['lat_max'])
    if not (isnan(row['lat_min']) or isnan(row['lat_max']) or isnan(row['lon_min']) or isnan(row['lon_max']))
    else None,
    axis=1
)
gdf_stats = gpd.GeoDataFrame(df_stats, geometry='geometry', crs='EPSG:4326')
ax = gdf_stats.plot(column='filename', legend=False, figsize=(10, 6))
ax.set_title("Geospatial summary of parquet files")
ax.set_xlabel("Longitude");
ax.set_ylabel("Latitude");

# %%
# #| hide
# # convert to geopandas creating a polygon box with lat_min	lat_max	lon_min	lon_max per file
# ax = gdf_stats[~gdf_stats.filename.str.contains('sample')].plot(column='filename', legend=False, figsize=(20, 6), aspect=0.25)
# ax.set_xlim([150, 200])
# ax.set_xlabel('Longitude')
# ax.set_ylabel('Latitude')
# ax.set_title('Geospatial coverage of parquet files (excluding sample files)')

# %% [markdown]
# ## Create Sidecar for Sample Data
#
# Now let's create a HEALPix sidecar for the sample data. We can do this in memory without writing to a file.

# %%
#| code-fold: true

# Load the 50k sample
import geopandas as gpd
from shapely import wkb

sample_file = test_data_dir / 'samples' / 'sample_50k.parquet'
print(f"Loading: {sample_file}")

# Read as regular pandas DataFrame first (geometry is stored as WKB binary)
df = pd.read_parquet(sample_file)
print(f"Loaded {len(df)} rows")
print(f"Columns: {list(df.columns)}")

# Convert WKB geometry column to shapely geometries
if 'geometry' in df.columns:
    print("\nConverting WKB geometry to GeoDataFrame...")
    df['geometry'] = df['geometry'].apply(lambda x: wkb.loads(bytes(x)) if x is not None else None)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs='EPSG:4326')
    print(f"CRS: {gdf.crs}")
else:
    print("\nNo geometry column found!")
    gdf = df

# Show first few rows
gdf.head(3).iloc[:,-10:]

# %% [markdown]
# Create sidecar in memory using the process_partition function

# %%
#| code-fold: true

# Create sidecar in memory using the process_partition function
from healpyxel.sidecar import process_partition

# Parameters
nside = 32  # HEALPix resolution
mode = 'fuzzy'  # 'fuzzy' allows multiple cells per geometry, 'strict' only single-cell geometries

# Process the GeoDataFrame
sidecar_df = process_partition(
    gdf=gdf,
    nside=nside,
    mode=mode,
    base_index=0,  # Start source_id from 0
    lon_convention='0_360',  # Use '0_360' or '-180_180' (underscores, not hyphens!)
)

print(f"Created sidecar with {len(sidecar_df)} assignments")
print(f"Unique geometries: {sidecar_df['source_id'].nunique()}")
print(f"Unique HEALPix cells: {sidecar_df['healpix_id'].nunique()}")
print(f"\nSidecar columns: {list(sidecar_df.columns)}")
print(f"Sidecar dtypes:\n{sidecar_df.dtypes}")

# Show first few assignments
sidecar_df.head(10)

# %% [markdown]
# Check how many cells each geometry touches (for fuzzy mode)

# %%
#| echo: false

assignments_per_geom = sidecar_df.groupby('source_id').size()

print(f"Assignment statistics:")
print(f"  Min cells per geometry: {assignments_per_geom.min()}")
print(f"  Max cells per geometry: {assignments_per_geom.max()}")
print(f"  Mean cells per geometry: {assignments_per_geom.mean():.2f}")
print(f"  Median cells per geometry: {assignments_per_geom.median():.0f}")

# Show distribution
print(f"\nDistribution of assignments per geometry:")
print(assignments_per_geom.value_counts().sort_index().head(10))

# %% [markdown]
# Optional: Save sidecar to file for later use

# %%
#| code-fold: true
sidecar_output = pathlib.Path(f'/tmp/sample_50k_sidecar_nside{nside}_{mode}.parquet')
sidecar_df.to_parquet(sidecar_output, index=False)
print(f"Saved sidecar to: {sidecar_output}")
print(f"File size: {sidecar_output.stat().st_size / 1024:.2f} KB")

# %% [markdown]
# ## Aggregate Data by HEALPix Cells
#
# Now let's use the sidecar to aggregate the `r1050` column from the original data by HEALPix cells.

# %%
#| echo: false
# Import the aggregate module
from healpyxel.aggregate import aggregate_by_sidecar

# Check if r1050 column exists
if 'r1050' in gdf.columns:
    print("✓ Column 'r1050' found in the data")
    print(f"  Range: [{gdf['r1050'].min():.3f}, {gdf['r1050'].max():.3f}]")
    print(f"  Missing values: {gdf['r1050'].isna().sum()} / {len(gdf)}")
else:
    print("❌ Column 'r1050' not found!")
    print(f"Available columns: {list(gdf.columns)}")

# %% [markdown]
# Aggregate r1050 by HEALPix cells with explicit aggregation functions.
#
# Convert GeoDataFrame to regular DataFrame for aggregation (geometry not needed)/
#

# %%
#| echo: false
df_for_agg = pd.DataFrame(gdf.drop(columns='geometry'))

# Perform aggregation with all available functions explicitly
aggregated = aggregate_by_sidecar(
    original=df_for_agg,
    sidecar=sidecar_df,
    value_columns=['r1050'],
    aggs=['mean', 'median', 'std', 'mad', 'robust_std'],  # Explicit list of aggregations
    source_id_col='source_id',
    healpix_col='healpix_id',
    min_count=1,  # Minimum number of sources per cell
   sentinel_threshold=1e30  # Mask extreme values
)

print(f"Aggregated data shape: {aggregated.shape}")
print(f"Number of HEALPix cells with data: {len(aggregated)}")
print(f"\nAggregated columns: {list(aggregated.columns)}")

# Show first few rows
aggregated.head(10)

# %% [markdown]
# ### Interpret the Results
#
# Each row represents one HEALPix cell with:
# - `healpix_id`: The HEALPix cell identifier
# - `r1050_mean`: Mean of r1050 values in this cell
# - `r1050_median`: Median value (less affected by outliers)
# - `r1050_std`: Standard deviation (spread of values)
# - `r1050_mad`: Median Absolute Deviation (robust measure of spread)
# - `r1050_robust_std`: MAD * 1.4826 (approximates standard deviation for normal distributions)
# - `n_sources`: Number of source measurements in this cell, for all columns
#
# Let's examine the statistics of the aggregated data.
#
#
# first, Display summary statistics of the aggregated results on HEALPix cells:

# %%
#| echo: false
aggregated.describe().T

# %% [markdown]
# Check the distribution of source counts per HEALPix cell:
#

# %%
#| echo: false

print(f"\nCells with only 1 source: {(aggregated['n_sources'] == 1).sum()}")
print(f"Cells with 2-5 sources: {((aggregated['n_sources'] >= 2) & (aggregated['n_sources'] <= 5)).sum()}")
print(f"Cells with 5+ sources: {(aggregated['n_sources'] > 5).sum()}")

display(aggregated[['n_sources']].describe())

# %% [markdown]
# ### HEALPix Metadata
#
# The aggregation results don't automatically include HEALPix metadata. You need to track this separately or read it from a saved sidecar file. For in-memory workflows, store metadata explicitly:

# %%
#| echo: false
# Store HEALPix metadata with aggregated results
metadata = {
    'healpix_nside': nside,
    'healpix_order': 'nested',  # HEALPix default in healpyxel
    'healpix_mode': mode,
}

# Access metadata like in your older scripts
nside_value = int(metadata['healpix_nside'])
order = metadata['healpix_order']
nest_flag = (order == 'nested')

print(f"HEALPix Configuration:")
print(f"  nside: {nside_value}")
print(f"  order: {order}")
print(f"  nested: {nest_flag}")
print(f"  mode: {metadata['healpix_mode']}")

# %% [markdown]
# **Reading metadata from saved sidecar files:**
#
# If you save the sidecar to a parquet file (like we did earlier), the metadata is embedded in the parquet schema and can be read back.
#
#
# Read metadata from saved sidecar file:

# %%
#| echo: false
import pyarrow.parquet as pq

sidecar_file = pathlib.Path(f'/tmp/sample_50k_sidecar_nside{nside}_{mode}.parquet')
if sidecar_file.exists():
    # Read parquet metadata
    parquet_file = pq.ParquetFile(sidecar_file)
    schema_metadata = parquet_file.schema_arrow.metadata
    
    # Decode metadata (stored as bytes)
    file_metadata = {k.decode(): v.decode() for k, v in schema_metadata.items()} if schema_metadata else {}
    
    print("Metadata from saved sidecar file:")
    print(f"  nside: {file_metadata.get('nside', 'N/A')}")
    print(f"  mode: {file_metadata.get('mode', 'N/A')}")
    print(f"  order: {file_metadata.get('order', 'N/A')}")
    
    # Use it like your older scripts
    if 'nside' in file_metadata:
        nside_from_file = int(file_metadata['nside'])
        order_from_file = file_metadata['order']
        nest_flag_from_file = (order_from_file == 'nested')
        print(f"\n  → nest_flag: {nest_flag_from_file}")
else:
    print(f"Sidecar file not found: {sidecar_file}")

# %% [markdown]
# ## Visualize HEALPix Map
#
# Before visualizing, we need to densify the sparse aggregated data to include all HEALPix cells (including empty ones).
#
# We'll use the visualization utilities from `healpyxel.visualization` module.
#
# The aggregated DataFrame only contains cells with data (sparse).
#
#
# Densify to create a full HEALPix grid with all npix = 12 * nside^2 cells
#

# %%
#| echo: false
from healpyxel.aggregate import densify_healpix_aggregates

aggregated_dense = densify_healpix_aggregates(
    agg_sparse_df=aggregated,
    nside=nside,
    healpix_col='healpix_id'
)

print(f"Sparse aggregated cells: {len(aggregated)}")
print(f"Dense HEALPix grid cells: {len(aggregated_dense)} (expected: {12 * nside**2})")
print(f"\nEmpty cells (no data): {aggregated_dense['r1050_median'].isna().sum()}")
print(f"Cells with data: {aggregated_dense['r1050_median'].notna().sum()}")

# Show first few rows including empty cells
aggregated_dense.head(10)

# %% [markdown]
# Import visualization utilities from healpyxel and prepare the HEALPix map for visualization

# %%
#| code-fold: true
# Import visualization utilities from healpyxel
from healpyxel.visualization import prepare_healpix_map
import numpy as np

# Prepare the HEALPix map for visualization
output_column = 'r1050_median'

healpix_map, valid_pixels, invalid_pixels, mappable = prepare_healpix_map(
    aggregated_dense,
    output_column=output_column,
    equalize=True,  # Apply histogram equalization for better contrast
    percentile_cutoff=None,  # Optional: clip outliers, e.g., 5 for [5%, 95%]
    cmap='Spectral_r'
)

print(f"HEALPix map prepared:")
print(f"  Total pixels: {len(healpix_map)}")
print(f"  Valid pixels: {valid_pixels.sum()}")
print(f"  Invalid pixels: {invalid_pixels.sum()}")

# %%
#| eval: false
import healpy

ax = healpy.orthview(healpix_map, nest=nest_flag,
                         title=f'HEALPix Map of {output_column} (nside={nside}, order={order})',
                         cmap='Spectral_r',flip='geo', norm='None', xsize=2500)
healpy.graticule()

# %%
#| echo: false
import skyproj
import healpy

from matplotlib import pyplot as plt
healpix_map[invalid_pixels] = healpy.UNSEEN
# healpix_map_masked = np.ma.masked_where(invalid_pixels, healpix_map)

fig = plt.figure(figsize=(14, 12))
gs = fig.add_gridspec(2, 1, height_ratios=[1, 1])

# Top row: full-width Mollweide projection
ax_top = fig.add_subplot(gs[0, :])
sp_moll = skyproj.MollweideSkyproj(ax=ax_top, lon_0=-180, longitude_ticks='symmetric')
_ = sp_moll.draw_hpxmap(
    healpix_map,
    nest=nest_flag,
    cmap='Spectral_r',
    zoom=True,
)
fig.colorbar(mappable, ax=sp_moll.ax, orientation='vertical', label=output_column)
sp_moll.ax.set_title(f'Mollweide — global view (nside={nside})')

# # Bottom-left: LAEA centered on South pole
# ax_bl = fig.add_subplot(gs[1, 0])
# sp_south = skyproj.LaeaSkyproj(ax=ax_bl, lat_0=-90.0)
# _ = sp_south.draw_hpxmap(
#     healpix_map,
#     nest=nest_flag,
#     cmap='Spectral_r',
#     zoom=False,
# )
# fig.colorbar(mappable, ax=sp_south.ax, orientation='vertical', label=output_column)
# sp_south.ax.set_title('LAEA — South pole (lat_0=-90)')

# # Bottom-right: LAEA centered on North pole
# ax_br = fig.add_subplot(gs[1, 1])
# sp_north = skyproj.LaeaSkyproj(ax=ax_br, lat_0=90.0)
# _ = sp_north.draw_hpxmap(
#     healpix_map,
#     nest=nest_flag,
#     cmap='Spectral_r',
#     zoom=False,
# )
# fig.colorbar(mappable, ax=sp_north.ax, orientation='vertical', label=output_column)
# sp_north.ax.set_title('LAEA — North pole (lat_0=+90)')

plt.suptitle(f'HEALPix Map: {output_column} (nside={nside})');
# plt.tight_layout()
