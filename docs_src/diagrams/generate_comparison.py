#!/usr/bin/env python3
# Generate the HEALPix grid comparison image (Orthographic projection).
# Run from repo root: python docs_src/diagrams/generate_comparison.py

import matplotlib
matplotlib.use("Agg")  # no display needed

import matplotlib.pyplot as plt
import cartopy.crs as ccrs

from healpyxel.geospatial import healpix_to_geodataframe

NSIDES = [8, 16, 32]
OUT_README = "healpix_grids_comparison.png"        # for README.md
OUT_DOCS   = "docs/index_files/healpix_grids_comparison.png"

fig, axes = plt.subplots(
    2, 2, figsize=(12, 12), subplot_kw={"projection": ccrs.Orthographic(0, 0)}
)
axes = axes.flatten()

for ax, nside in zip(axes, NSIDES):
    gdf = healpix_to_geodataframe(
        nside=nside,
        pixels="all",
        order="nested",
        lon_convention="0_360",
        fix_antimeridian=True,
        cache_mode="use",
    )
    gdf.plot(
        column=gdf.index,
        cmap="tab20",
        legend=False,
        edgecolor="black",
        linewidth=0.5,
        ax=ax,
        transform=ccrs.PlateCarree(),
    )
    ax.set_aspect("equal")
    ax.set_title(f"nside = {nside}\n{gdf.shape[0]:,} cells", fontsize=13)

fig.suptitle("HEALPix Grid comparison (Orthographic)", fontsize=14, y=1.02)
fig.tight_layout()
fig.savefig(OUT_README, dpi=150, bbox_inches="tight")
print(f"Saved {OUT_README}")
