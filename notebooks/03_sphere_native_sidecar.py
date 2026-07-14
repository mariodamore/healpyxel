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
# # Sphere-Native Sidecar Generation
#
# How healpyxel assigns observation footprints to HEALPix cells using
# SLERP-great-circle sampling on unit vectors — sidestepping antimeridian
# crossings, pole singularities, and winding-order problems entirely.

# %%
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.collections import PatchCollection

from healpyxel.geometry import Sphere
from healpyxel.sidecar import (
    process_partition,
    _sample_great_circle_arc,
    _query_healpix_single_polygon,
    compute_healpix_ids_from_lonlat,
)
import geopandas as gpd
from shapely.geometry import Polygon

# %% [markdown]
# ## 1. The Problem: Planar lon/lat Geometry
#
# Representing planetary footprints as lon/lat polygons in a flat coordinate system
# creates well-known pathologies:
#
# - **Antimeridian crossing**: A polygon spanning from 179° to -179° is drawn across
#   358° of the map, not the 2° it spans on the sphere.
# - **Pole singularity**: All longitudes converge at the pole; a "small" polygon near
#   the pole may wrap incorrectly in lon/lat.
# - **Winding order**: In planar GIS, CW vs CCW determines which side is "inside."
#   On the sphere, winding still matters topologically — but dense sampling sidesteps it.
#
# healpyxel's observation geometry lives on a **spherical body**, not on a projection.
# The computation engine should reflect that.

# %% [markdown]
# ## 2. The Solution: Unit Vectors + SLERP Great-Circle Arcs
#
# The key insight: convert every polygon vertex to a **unit vector** (x, y, z) on the
# unit sphere. Consecutive vertices are then connected by the **shortest great-circle
# arc** using SLERP (spherical linear interpolation):
#
# $$
# v(t) = \frac{\sin((1-t)\theta)}{\sin\theta} \cdot v_0 + \frac{\sin(t\theta)}{\sin\theta} \cdot v_1
# $$
#
# where $\theta = \arccos(v_0 \cdot v_1)$ is the angular distance between vertices.
#
# Properties:
# - **Antimeridian crossing**: (179°, 5°) to (-179°, 5°) is only 2° on the sphere —
#   SLERP takes the short arc automatically.
# - **Pole safe**: The north pole is just the vector (0, 0, 1).
# - **Convention independent**: (181°, 5°) in [0,360] maps to the same unit vector
#   as (-179°, 5°) in [-180,180].
# - **Winding agnostic**: We sample all edges and add interior points; CW and CCW
#   windings produce identical HEALPix cell sets.
#
# The SLERP formula is implemented in `_sample_great_circle_arc()` in `sidecar.py`.

# %% [markdown]
# ## 3. Walkthrough: SLERP on a Simple Polygon
#
# Let's trace what happens for a small rectangle at high latitude, first showing
# the great-circle arc sampling.

# %%
# Create a simple polygon at high latitude
coords = [(95.0, 70.0), (105.0, 70.0), (105.0, 75.0), (95.0, 75.0), (95.0, 70.0)]
poly = Polygon(coords)

body = Sphere()

# Extract vertices
c = np.array(poly.exterior.coords)
lons_v = c[:, 0].astype(float)
lats_v = c[:, 1].astype(float)
xyz_v = body.lonlat_to_xyz(lons_v, lats_v)  # (3, N)

print("Polygon vertices (lon, lat):")
for lon, lat in zip(lons_v, lats_v):
    print(f"  ({lon:.1f}°, {lat:.1f}°)")

print(f"\nUnit vectors (x, y, z):")
for i in range(xyz_v.shape[1]):
    print(f"  v{i}: [{xyz_v[0, i]:+.4f}, {xyz_v[1, i]:+.4f}, {xyz_v[2, i]:+.4f}]")

# %%
# Sample the first edge (v0 -> v1) via SLERP
v0 = xyz_v[:, 0]
v1 = xyz_v[:, 1]
arc_pts = _sample_great_circle_arc(v0, v1, 20)  # 20 points on the great-circle arc
arc_lon, arc_lat = body.xyz_to_lonlat(arc_pts)

print(f"Edge v0->v1: ({lons_v[0]}°, {lats_v[0]}°) → ({lons_v[1]}°, {lats_v[1]}°)")
print(f"  Angular distance: {np.degrees(np.arccos(np.clip(np.dot(v0, v1), -1, 1))):.2f}°")
print(f"  SLERP samples (first 5):")
for i in range(5):
    print(f"    t={i/19:.2f}: ({arc_lon[i]:.3f}°, {arc_lat[i]:.3f}°)")

# %% [markdown]
# The SLERP samples follow the **shortest great-circle arc** on the sphere.
# For a 10° edge at 70°N latitude, the arc curves slightly toward the pole —
# this is physically correct for a sensor footprint on a spherical body.

# %% [markdown]
# ## 4. Antimeridian Crossing: No Fix Needed
#
# A polygon crossing the antimeridian at low latitude:
# vertices from 179° → -179° span only 2° on the sphere.

# %%
def plot_polygon_and_slrp(ax, coords_list, body, color, label, n_edge=40, ls="-"):
    """Plot polygon vertices and SLERP edge samples on a lon/lat map."""
    xyz_all = []
    for i in range(len(coords_list) - 1):
        lon0, lat0 = coords_list[i]
        lon1, lat1 = coords_list[i + 1]
        v0 = body.lonlat_to_xyz(np.array([lon0]), np.array([lat0])).flatten()
        v1 = body.lonlat_to_xyz(np.array([lon1]), np.array([lat1])).flatten()
        arc = _sample_great_circle_arc(v0, v1, n_edge)
        a_lon, a_lat = body.xyz_to_lonlat(arc)
        ax.plot(a_lon, a_lat, color=color, lw=1.5, ls=ls, alpha=0.7)

    # Vertices
    lons = [c[0] for c in coords_list]
    lats = [c[1] for c in coords_list]
    ax.scatter(lons, lats, color=color, zorder=5, s=40, label=label)
    return lons, lats


fig, axes = plt.subplots(2, 1, figsize=(10, 8))

# Panel A: Antimeridian crossing ([-180, 180] convention)
ax = axes[0]
am_coords = [(175, 5), (179, 5), (-179, 5), (-175, 5), (-175, -5), (-179, -6), (179, -6), (175, -5), (175, 5)]
plot_polygon_and_slrp(ax, am_coords, body, "tab:blue", "antimeridian FOV", n_edge=60)
ax.axvline(180, color="k", lw=0.8, ls="--", alpha=0.4)
ax.axvline(-180, color="k", lw=0.8, ls="--", alpha=0.4)
ax.axvline(0, color="gray", lw=0.5, ls=":", alpha=0.3)
ax.set_xlim(-200, 200)
ax.set_ylim(-15, 15)
ax.set_xlabel("Longitude (°)")
ax.set_ylabel("Latitude (°)")
ax.set_title("Antimeridian Crossing ([-180, 180])")
ax.legend(loc="upper left")
ax.grid(True, alpha=0.3)

# Panel B: Same polygon in [0, 360] convention
ax = axes[1]
am_coords_360 = [(175, 5), (179, 5), (181, 5), (185, 5), (185, -5), (181, -6), (179, -6), (175, -5), (175, 5)]
plot_polygon_and_slrp(ax, am_coords_360, body, "tab:orange", "antimeridian FOV [0,360]", n_edge=60)
ax.axvline(180, color="k", lw=0.8, ls="--", alpha=0.4)
ax.axvline(360, color="k", lw=0.8, ls="--", alpha=0.4)
ax.set_xlim(150, 390)
ax.set_ylim(-15, 15)
ax.set_xlabel("Longitude (°)")
ax.set_title("Same FOV in [0, 360] Convention")
ax.legend(loc="upper left")
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.show()

# %% [markdown]
# **Key observation**: In both panels, the edges follow the **short great-circle arc**.
# The edge from (179°, 5°) to (-179°, 5°) — which would be drawn across 358° in a
# planar lon/lat plot — is correctly represented as the 2° arc on the sphere.
#
# Both conventions ([-180,180] and [0,360]) produce **identical unit vectors**,
# hence identical SLERP sampling and identical HEALPix cell assignments.

# %% [markdown]
# ## 5. Winding Order: Sidestepped by Dense Sampling
#
# Our SLERP-based approach does not depend on winding order. We sample **every edge**
# (CW or CCW) and add interior points. The result is the same set of HEALPix cells
# regardless of vertex ordering.

# %%
# Create the same polygon with CCW and CW winding
coords_ccw = [(95.0, 70.0), (105.0, 70.0), (105.0, 75.0), (95.0, 75.0), (95.0, 70.0)]
coords_cw = list(reversed(coords_ccw))

poly_ccw = Polygon(coords_ccw)
poly_cw = Polygon(coords_cw)

gdf_ccw = gpd.GeoDataFrame({"geometry": [poly_ccw]})
gdf_cw = gpd.GeoDataFrame({"geometry": [poly_cw]})

result_ccw = process_partition(gdf_ccw, nside=32, mode="fuzzy", lon_convention="minus_plus180", body=Sphere())
result_cw = process_partition(gdf_cw, nside=32, mode="fuzzy", lon_convention="minus_plus180", body=Sphere())

ccw_cells = set(result_ccw["healpix_id"])
cw_cells = set(result_cw["healpix_id"])

print(f"CCW winding → {len(ccw_cells)} HEALPix cells")
print(f"CW winding  → {len(cw_cells)} HEALPix cells")
print(f"Sets are identical: {ccw_cells == cw_cells}")

# %% [markdown]
# Both windings produce the **exact same cell set**. This is because:
#
# 1. `_sample_great_circle_arc` samples edges regardless of direction
# 2. The centroid (mean of unit vectors) is added as interior coverage
# 3. Original vertices are added for exact corner coverage
# 4. `compute_healpix_ids_from_lonlat` collects all points → HEALPix IDs
#
# Signed spherical area determines *which* side of the loop is "inside" — but we
# never need to know that, because our dense sampling covers both the edges and
# the interior.

# %% [markdown]
# ## 6. Convention Independence: [-180,180] vs [0,360]
#
# The same physical polygon expressed in different longitude conventions must
# produce identical sidecars. Unit vectors make this trivially true.

# %%
am_coords = [(175, 5), (179, 5), (-179, 5), (-175, 5),
             (-175, -5), (-179, -6), (179, -6), (175, -5), (175, 5)]

poly_m180 = Polygon(am_coords)
poly_360 = Polygon([(lon if lon >= 0 else lon + 360, lat) for lon, lat in am_coords])

gdf_m180 = gpd.GeoDataFrame({"geometry": [poly_m180]})
gdf_360 = gpd.GeoDataFrame({"geometry": [poly_360]})

r_m180 = process_partition(gdf_m180, nside=32, mode="fuzzy", lon_convention="minus_plus180", body=Sphere())
r_360 = process_partition(gdf_360, nside=32, mode="fuzzy", lon_convention="0_360", body=Sphere())

cells_m180 = set(r_m180["healpix_id"])
cells_360 = set(r_360["healpix_id"])

print(f"[-180,180] convention → {len(cells_m180)} cells")
print(f"[0,360]   convention → {len(cells_360)} cells")
print(f"Identical sets: {cells_m180 == cells_360}")

# %% [markdown]
# ## 7. Polar Polygon: No Singularity
#
# A triangle wrapping around the north pole works correctly because unit vectors
# have no coordinate singularity.

# %%
# Triangle covering area near the north pole
polar_coords = [(0.0, 85.0), (120.0, 85.0), (240.0, 85.0), (0.0, 85.0)]
poly_polar = Polygon(polar_coords)
gdf_polar = gpd.GeoDataFrame({"geometry": [poly_polar]})

r_polar = process_partition(gdf_polar, nside=32, mode="fuzzy", lon_convention="0_360", body=Sphere())
polar_cells = set(r_polar["healpix_id"])

print(f"Polar polygon → {len(polar_cells)} HEALPix cells")

# Verify cells are near the pole (high healpy indices in nested ordering)
import healpy as hp
for hid in sorted(polar_cells)[:5]:
    theta, phi = hp.pix2ang(32, int(hid), nest=True)
    lat_d = 90.0 - np.degrees(theta)
    lon_d = np.degrees(phi)
    print(f"  cell {hid}: (lon={lon_d:.1f}°, lat={lat_d:.1f}°)")

# %% [markdown]
# ## 8. Full Pipeline: From Footprint to Sidecar
#
# The complete fuzzy-mode pipeline for a single polygon:

# %%
fig, ax = plt.subplots(figsize=(10, 6))

# Simple rectangle
simple_coords = [(100.0, 70.0), (105.0, 70.0), (105.0, 75.0), (100.0, 75.0), (100.0, 70.0)]
simple_poly = Polygon(simple_coords)
gdf_simple = gpd.GeoDataFrame({"geometry": [simple_poly]})
r_simple = process_partition(gdf_simple, nside=32, mode="fuzzy", lon_convention="minus_plus180", body=Sphere())
simple_cells = set(r_simple["healpix_id"])

# Plot polygon and SLERP edges
plot_polygon_and_slrp(ax, simple_coords, body, "tab:blue", "FOV polygon", n_edge=80)

# Plot HEALPix cell boundaries for the assigned cells
import healpy as hp

for hid in simple_cells:
    cell_boundary = hp.boundaries(32, int(hid), step=1, nest=True)  # (3, 4) corners
    cell_lon = np.degrees(hp.vec2ang(cell_boundary)[1])
    cell_lat = 90.0 - np.degrees(hp.vec2ang(cell_boundary)[0])
    # Close the ring
    cell_lon = np.append(cell_lon, cell_lon[0])
    cell_lat = np.append(cell_lat, cell_lat[0])
    ax.plot(cell_lon, cell_lat, color="tab:red", lw=0.8, alpha=0.5)

# Vertex cells (subset check)
import healpy as hp
vertex_hids = compute_healpix_ids_from_lonlat(32,
    np.array([c[0] for c in simple_coords]),
    np.array([c[1] for c in simple_coords]),
    body=body)

print(f"FOV polygon: {len(simple_coords)-1} edges at (70-75°N, 100-105°E)")
print(f"Fuzzy mode assigned {len(simple_cells)} HEALPix cells at nside=32")
print(f"Vertex cells ({len(vertex_hids)}): {sorted(vertex_hids)}")
print(f"All HEALPix cells ({len(simple_cells)}): {sorted(simple_cells)}")
print(f"Vertices ⊆ Fuzzy: {set(vertex_hids).issubset(simple_cells)}")

# Draw vertex cells more prominently
for hid in vertex_hids:
    cell_boundary = hp.boundaries(32, int(hid), step=1, nest=True)
    cell_lon = np.degrees(hp.vec2ang(cell_boundary)[1])
    cell_lat = 90.0 - np.degrees(hp.vec2ang(cell_boundary)[0])
    cell_lon = np.append(cell_lon, cell_lon[0])
    cell_lat = np.append(cell_lat, cell_lat[0])
    ax.plot(cell_lon, cell_lat, color="tab:green", lw=2)

ax.set_xlim(95, 110)
ax.set_ylim(68, 77)
ax.set_xlabel("Longitude (°)")
ax.set_ylabel("Latitude (°)")
ax.set_title("Sphere-Native Fuzzy Assignment: Polygon → HEALPix Cells")
ax.legend(handles=[
    mpatches.Patch(color="tab:blue", label="Polygon edges (SLERP)"),
    mpatches.Patch(color="tab:red", label="Fuzzy-mode cells"),
    mpatches.Patch(color="tab:green", label="Vertex cells (subset)"),
], loc="upper left")
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.show()

# %% [markdown]
# **Green** = cells touched by polygon vertices (strict mode).
# **Red** = all cells assigned by fuzzy-mode SLERP.
# The vertex cells are always a **subset** of the fuzzy cells.

# %% [markdown]
# ## 9. Summary
#
# | Problem | Planar lon/lat | Sphere-native (SLERP) |
# |---------|----------------|------------------------|
# | Antimeridian split | Needs `antimeridian.fix_polygon` | **No fix needed** |
# | Longitude wrapping (±180°) | Coordinate discontinuity | **Never appears** |
# | Pole singularity | All longitudes = same point | **No singularity** |
# | Winding order | Matters (CW ≠ CCW) | **Sidestepped by dense sampling** |
# | Bounding boxes | Planar distortion | **Not needed** |
# | `shapely.STRtree` | Required for fuzzy mode | **Eliminated** |
# | `shapely.interpolate()` | Planar, walks long way around | **Replaced by SLERP** |
#
# The fuzzy-mode hot path is now fully sphere-native. The only remaining use of
# `antimeridian` is in `healpyxel_to_geoparquet` (Phase 3 of the pipeline) for
# generating valid GeoParquet polygons for GIS visualization — that is an
# **I/O concern**, not a geometric one.
