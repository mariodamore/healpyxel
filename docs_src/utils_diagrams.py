"""Generate all static images used by docs_src/index.qmd.

Produces:
- ``healpix_grids_comparison.png``  (matplotlib + cartopy)
- D2-based SVGs in ``diagrams/svg/`` (requires d2 CLI)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
from cartopy.crs import Orthographic

from healpyxel.geospatial import healpix_to_geodataframe

# ── output dirs ─────────────────────────────────────────────────────────────
DOCS_SRC = Path(__file__).parent
DIAGRAM_DIR = DOCS_SRC / "diagrams" / "svg"
DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)


# ── D2 helpers ──────────────────────────────────────────────────────────────

def _d2_available() -> bool:
    return shutil.which("d2") is not None


def render_d2(
    diagram_str: str,
    output_path: Path,
    *,
    theme: int = 0,
    sketch: bool = True,
    pad: int = 10,
    scale: float = 1.5,
    layout: str = "dagre",
    keep_source: bool = True,
) -> bool:
    """Render a D2 diagram to SVG via the d2 CLI."""
    d2_src = output_path.with_suffix(".d2")
    d2_src.write_text(diagram_str)

    cmd = [
        "d2",
        "--theme", str(theme),
        "--pad", str(pad),
        "--scale", str(scale),
        "--layout", layout,
    ]
    if sketch:
        cmd.append("--sketch")
    cmd.extend([str(d2_src), str(output_path)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ✗ d2 failed for {output_path.name}: {result.stderr.strip()}")
        if not keep_source:
            d2_src.unlink(missing_ok=True)
        return False

    print(f"  ✓ {output_path.relative_to(DOCS_SRC.parent)}")
    return True


# ── D2 diagram sources ───────────────────────────────────────────────────────

PIPELINE_D2 = """
direction: down

# PHASE 1: SPLIT
Observations_Raw: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "source_id , column"
}

healpyxel_sidecar: {
  shape: rectangle
  style: {fill: "#fff4e6"; stroke: "#ff6b6b"; stroke-width: 2}
}

Sidecar: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "healpix_id : source_id"
}

# PHASE 2: APPLY
healpyxel_aggregate: {
  shape: rectangle
  style: {fill: "#fff4e6"; stroke: "#ff6b6b"; stroke-width: 2}
}

# PHASE 3: COMBINE
Aggregated: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "healpix_id : mean(column)"
}

Observations_Raw -> healpyxel_sidecar: "split\\ngeometries"
healpyxel_sidecar -> Sidecar: ".parquet\\n.meta.json"
Observations_Raw -> healpyxel_aggregate: "join via\\nsource_id"
Sidecar -> healpyxel_aggregate: "group by\\nhealpix_id"
healpyxel_aggregate -> Aggregated: ".parquet"
"""

PIPELINE_GEO_D2 = """
direction: down

# PHASE 1: SPLIT
Observations_Raw: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "source_id , column"
}

healpyxel_sidecar: {
  shape: rectangle
  style: {fill: "#fff4e6"; stroke: "#ff6b6b"; stroke-width: 2}
}

Sidecar: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "healpix_id : source_id"
}

# PHASE 2: APPLY
healpyxel_aggregate: {
  shape: rectangle
  style: {fill: "#fff4e6"; stroke: "#ff6b6b"; stroke-width: 2}
}

# PHASE 3a: COMBINE
Aggregated: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "healpix_id : mean(column)"
}

# PHASE 3b: GEOPARQUET
healpyxel_to_geoparquet: {
  shape: rectangle
  style: {fill: "#fff4e6"; stroke: "#ff6b6b"; stroke-width: 2}
}

Geoparquet: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  "healpix_id : mean(column) : geometry"
}

Observations_Raw -> healpyxel_sidecar: "split\\ngeometries"
healpyxel_sidecar -> Sidecar: ".parquet\\n.meta.json"
Observations_Raw -> healpyxel_aggregate: "join via\\nsource_id"
Sidecar -> healpyxel_aggregate: "group by\\nhealpix_id"
healpyxel_aggregate -> Aggregated: ".parquet"
Aggregated -> healpyxel_to_geoparquet: "attach\\ngeometry"
healpyxel_to_geoparquet -> Geoparquet: ".geo.parquet"
"""

SIDECAR_D2 = """
direction: right

Observations: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  source_id: "value | geometry"
  "src_1   ": "10000 | POLY(..)"
  "src_2   ": "20000 | POLY(..)"
  "src_3   ": "30000 | POLY(..)"
}

Sidecar: {
  shape: sql_table
  style: {fill: "#dae8fc"}
  index: "source id | healpix id"
  "1": "src_1 | h_pix_A"
  "3": "src_2 | h_pix_A"
  "4": "src_3 | h_pix_B"
}

Healpix_Target_Grid: {
  shape: sql_table
  style: {fill: "#d5e8d4"}
  index: "healpix id"
  "1": "h_pix_A"
  "2": "h_pix_B"
}

Observations <-> Sidecar: "healpyxel_sidecar\\n(Assign: strict/fuzzy)" {style: {stroke-width: 2}}
Sidecar <-> Healpix_Target_Grid: nside=32
"""

AGGREGATE_D2 = """
direction: right

Observations: {
  shape: sql_table
  style: {fill: "#f8cecc"}
  source_id: "value | geometry"
  "src_1   ": "10000 | POLY(..)"
  "src_2   ": "20000 | POLY(..)"
  "src_3   ": "30000 | POLY(..)"
}

Sidecar: {
  shape: sql_table
  style: {fill: "#dae8fc"}
  source_id: "healpix_id"
  "src_1": "pix_A"
  "src_2": "pix_A"
  "src_3": "pix_B"
}

healpyxel_aggregate: {
  style: {fill: "#fff4e6"}
  "Median(value)"
}

Aggregated: {
  shape: sql_table
  style: {fill: "#d5e8d4"}
  healpix_id: "median | n_sources"
  "pix_A": "015000 | 0000002"
  "pix_B": "030000 | 0000001"
}

Observations -> healpyxel_aggregate #: "group by\\nhealpix_id"
Sidecar -> healpyxel_aggregate #: "via source_id"
healpyxel_aggregate -> Aggregated
"""

COMBINE_D2 = """
direction: right

Aggregated: {
  shape: sql_table
  style: {fill: "#d5e8d4"}
  healpix_id: "median | n_sources"
  "pix_A": "015000 | 00000002"
  "pix_B": "030000 | 00000001"
}

healpyxel_to_geoparquet: {
  style: {fill: "#fff4e6"}
  "HEALPix Cell Geometry\\nvia healpy"
}

Aggregated_Geo: {
  shape: sql_table
  style: {fill: "#e1d5e7"}
  healpix_id: "median | n_sources | geometry"
  "pix_A":    "015000 | 00000002 | POLY(..)"
  "pix_B":    "030000 | 00000001 | POLY(..)"
}

Aggregated -> healpyxel_to_geoparquet
healpyxel_to_geoparquet -> Aggregated_Geo
"""

D2_DIAGRAMS = [
    ("Pipeline_End-to-End.svg", PIPELINE_D2),
    ("Pipeline_End-to-End_Geo.svg", PIPELINE_GEO_D2),
    ("Sidecar.svg", SIDECAR_D2),
    ("Aggregate.svg", AGGREGATE_D2),
    ("Combine.svg", COMBINE_D2),
]


# ── PNG: grid comparison ─────────────────────────────────────────────────────

def generate_grid_comparison() -> None:
    """Generate the HEALPix grid comparison figure."""
    projection = Orthographic(central_longitude=0, central_latitude=0)

    datasets = []
    for nside in (8, 16, 32):
        gdf = healpix_to_geodataframe(
            nside=nside,
            order="nested",
            lon_convention="0_360",
            fix_antimeridian=True,
            cache_mode="use",
        )
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "invalid value encountered")
            gdf_prj = gdf.to_crs(projection.proj4_init)
        datasets.append((nside, gdf_prj))

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    for ax, (nside, gdf_prj) in zip(axes, datasets):
        gdf_prj.plot(
            column=gdf_prj.index,
            ax=ax,
            cmap="Spectral_r",
            legend=False,
            edgecolor="black",
            linewidth=0.25,
        )
        ax.set_title(f"HEALPix Grid (nside={nside})")
        ax.set_aspect("equal")
        ax.axis("off")

    plt.tight_layout()
    out = DOCS_SRC / "healpix_grids_comparison.png"
    plt.savefig(out, dpi=100, bbox_inches="tight")
    print(f"  ✓ {out.relative_to(DOCS_SRC.parent)}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Generating PNG images...")
    generate_grid_comparison()

    if not _d2_available():
        sys.exit(
            "ERROR: d2 CLI not found. Install it to generate SVG diagrams:\n"
            "  https://d2lang.com/tour/install\n"
            "Then re-run: make docs-images"
        )

    print("Generating D2 SVG diagrams...")
    for filename, diagram_src in D2_DIAGRAMS:
        out = DIAGRAM_DIR / filename
        render_d2(diagram_src, out)


if __name__ == "__main__":
    main()
