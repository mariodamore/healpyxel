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
# # Gaussian PSF - WIP!
#
# Use of Gaussian spread function both for measurement or healpix cell assignment and weigthing.
#
# <span style="color: red;">*STILL WORK IN PROGRESS*</span>

# %%
# Import the GaussianPSF class from healpyxel.sidecar
from healpyxel.sidecar import GaussianPSF

import numpy as np
import matplotlib.pyplot as plt

# %%
# Create a Gaussian PSF with a chosen sigma
sigma = 1.0
psf = GaussianPSF(sigma=sigma)

# Create a grid of (dx, dy) values
grid_size = 100
extent = 3 * sigma
x = np.linspace(-extent, extent, grid_size)
y = np.linspace(-extent, extent, grid_size)
dx, dy = np.meshgrid(x, y)

# Evaluate the PSF on the grid
z = psf(dx, dy)

# %%
plt.figure(figsize=(6, 5))
plt.imshow(z, extent=[-extent, extent, -extent, extent], origin='lower', cmap='viridis')
plt.colorbar(label='PSF Value')
plt.title(f'2D Gaussian PSF (sigma={sigma})')
plt.xlabel('dx')
plt.ylabel('dy')
plt.show()

# %%
plt.figure()
plt.plot(x, psf(x, 0), label='PSF along dy=0')
plt.title('Gaussian PSF 1D cross-section')
plt.xlabel('dx')
plt.ylabel('PSF Value')
plt.legend()
plt.show()

# %%
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

# %%
from healpyxel.sidecar import process_partition, write_coalesced_output, get_psf
import pandas as pd
from pathlib import Path
import geopandas as gpd
from shapely import wkb

# Load your test data
input_path = test_data_dir / 'batches' / 'batch_001.parquet'
# Read as regular pandas DataFrame first (geometry is stored as WKB binary)
df = pd.read_parquet(input_path)

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
gdf.head(3)

# %%
gdf['lon_center'].describe()

# %%
import numpy as np

def healpix_cell_diameter_deg(nside):
    # Area of a HEALPix cell (steradians)
    area_sr = 4 * np.pi / (12 * nside * nside)
    # Convert to square degrees
    area_deg2 = area_sr * (180/np.pi)**2
    # Equivalent diameter (degrees) for a circle of this area
    diameter_deg = 2 * np.sqrt(area_deg2 / np.pi)
    return diameter_deg
    


# %%
# Set parameters
nside = 32
mode = "fuzzy"
lon_convention = "0_360"
sigma_level = 0.01  # Number of sigma to cover the cell
cell_radius = healpix_cell_diameter_deg(nside) / 2
sigma = cell_radius / sigma_level
cell_psf = get_psf("gaussian", sigma=sigma)  # You can adjust sigma as needed
print(f"{cell_radius=}")
print(f"{sigma_level=}")
print(f"{sigma=:}")

# %%
plt.figure()
x = np.linspace(-3* cell_radius, 3*cell_radius, 200)
plt.figure(figsize=(15,4))
plt.plot(x, cell_psf(x, 0), label='PSF along dy=0')
# put vertical lines at +/- cell_diameter
plt.axvline(cell_radius, color='r', linestyle='--', label='Cell Diameter ')
plt.axvline(-cell_radius, color='r', linestyle='--')
plt.title('Gaussian PSF 1D cross-section')
plt.xlabel('dx')
plt.ylabel('PSF Value')
plt.legend()
plt.show()

# %%

processed_partition = process_partition(
    gdf, 
    nside, 
    mode, 
    base_index=None,
    lon_convention=lon_convention,
    data_psf=None,
    cell_psf=cell_psf
    )

print(processed_partition.describe())

# %%
processed_partition['weight'].plot( cmap='viridis', figsize=(8,6), legend=True)

# %%
processed_partition.groupby('healpix_id').agg(len)[['weight']].sort_values(by='weight', ascending=False).head(10)

# %%
processed_partition.groupby('healpix_id').agg(sum)[['weight']].sort_values(by='weight', ascending=False).head(10)

# %%
healpix_id = 6374
cell = processed_partition.query(f'healpix_id == {healpix_id}')

cell

# %%
import healpy as hp
lon, lat = 175, 43  # Example from your data
theta = np.radians(90 - lat)
phi = np.radians(lon)
hid = hp.ang2pix(nside, theta, phi, nest=True)
print(hid)

from healpyxel.sidecar import get_healpix_cell_geometry

# Return a shapely Polygon for the given HEALPix cell.
# Uses healpy boundaries (in degrees, lon/lat).

print(get_healpix_cell_geometry(hid, nside=nside,nest=False).exterior.xy)

# %%
gdf_cell = gdf.iloc[cell['source_id'].values].copy()

# %%

gdf_cell['weight'] = cell.set_index('source_id')['weight']
fig, ax = plt.subplots(figsize=(20,8))
gdf_cell.plot(aspect=0.2, ax=ax, column='weight', legend=True)

# %%
gdf_cell['weight'].plot( cmap='viridis', figsize=(8,6), legend=True)

# %%
from sqlalchemy import column


fig, axs = plt.subplots(ncols=2, figsize=(10,8))
gdf_cell.plot(ax=axs[0], aspect='equal', column='weight')
poly = get_healpix_cell_geometry(healpix_id, nside=nside)
x_poly, y_poly = poly.exterior.xy

axs[1].plot(x_poly, y_poly, color='red', linewidth=2)
axs[1].fill(x_poly, y_poly, facecolor='red', alpha=0.15)

# %%
