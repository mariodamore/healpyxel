"""HEALPix sidecar generation: map source geometries to HEALPix cells.

This module implements the **sidecar** stage of the healpyxel pipeline.
A sidecar is a mapping file that assigns each observation (source) to one
or more HEALPix cells, optionally weighted by a Point Spread Function (PSF).

**Workflows supported:**

* **Strict mode** — each source is assigned to a single HEALPix cell
  (centroid-based). Fast and deterministic.
* **Fuzzy mode** — each source is assigned to *all* HEALPix cells its field-of-view
  polygon touches, using spherical polygon sampling. Handles sources that
  cross cell boundaries and those near the antimeridian.

Body geometry backends
-----------------------

Coordinates can be converted through a body model (Sphere, Ellipsoid, or SPICE DSK)
before HEALPix indexing, enabling correct geodetic handling for non-spherical bodies.

Multi-resolution optimization
------------------------------

When multiple nside values are requested, the finest nside is computed once
via the full pipeline. Lower nsides are derived by NEST bit-shift aggregation,
avoiding redundant geometric computation.
"""

import argparse
import logging
import os
import warnings
from pathlib import Path
import sys
import numpy as np

import pandas as pd

try:
    import dask_geopandas as dg
except Exception:
    raise ImportError("This script requires dask_geopandas. Install it with `pip install dask-geopandas`.")

try:
    # cdshealpix is preferred for speed
    import cdshealpix as ch
except Exception:
    ch = None

try:
    import healpy as _healpy
except Exception:
    _healpy = None

from healpyxel.geometry import Sphere, Ellipsoid, SpiceDSK, BodyGeometry

from shapely import get_coordinates, from_wkb, prepare  # shapely>=2.0
from shapely.geometry import Polygon, MultiPolygon
from tqdm.auto import tqdm

try:
    from dask.diagnostics.progress import ProgressBar as DaskProgressBar
    DASK_PROGRESS_AVAILABLE = True
except ImportError:
    try:
        from dask.diagnostics import ProgressBar as DaskProgressBar
        DASK_PROGRESS_AVAILABLE = True
    except ImportError:
        DASK_PROGRESS_AVAILABLE = False
        DaskProgressBar = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("healpix_sidecar")

def compute_healpix_ids_from_lonlat(nside: int, lons: np.ndarray, lats: np.ndarray,
                                     body: BodyGeometry | None = None) -> np.ndarray:
    """Compute HEALPix cell IDs for arrays of lon/lat coordinates in degrees.

    This is the fundamental coordinate-to-cell conversion function used
    throughout the pipeline. It normalizes longitudes to [0, 360) and
    delegates to healpy's ``ang2pix`` (NEST ordering).

    If a body geometry backend is provided, coordinates are first converted
    through the body model (lon/lat → xyz → unit sphere) before HEALPix
    indexing. This enables correct geodetic handling for ellipsoidal bodies
    like Earth or Mars.

    Prefers healpy over cdshealpix for consistency with the rest of the
    codebase. Falls back to cdshealpix if healpy is not available.

    Parameters
    ----------
    nside : int
        HEALPix nside parameter (must be a power of 2).
    lons : np.ndarray
        Longitude values in degrees. Shape (N,).
    lats : np.ndarray
        Latitude values in degrees. Shape (N,).
    body : BodyGeometry or None
        Optional body geometry backend for coordinate conversion.

    Returns
    -------
    np.ndarray
        1D integer array of HEALPix cell IDs, same length as input arrays.
        Empty array if input arrays are empty.
    """
    if lons.size == 0:
        return np.array([], dtype=np.int64)

    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)

    # ADR-013: apply body geometry conversion at the I/O boundary
    if body is not None and not body.is_sphere():
        xyz = body.lonlat_to_xyz(lons, lats)
        norm = np.linalg.norm(xyz, axis=0)
        norm_safe = np.where(norm > 1e-15, norm, 1.0)
        xyz_unit = xyz / norm_safe
        # Convert unit vectors back to healpy convention (theta, phi)
        lons = np.degrees(np.arctan2(xyz_unit[1], xyz_unit[0]))
        lons = np.mod(lons, 360.0)
        lats = 90.0 - np.degrees(np.arccos(np.clip(xyz_unit[2], -1.0, 1.0)))
    else:
        lons = np.mod(lons, 360.0)

    # Prefer healpy (to match notebook usage). Fall back to cdshealpix if healpy not available.
    if _healpy is not None:
        # healpy expects theta (colat) and phi (lon) in radians
        phi = np.radians(lons)
        theta = np.radians(90.0 - lats)
        return _healpy.ang2pix(nside, theta, phi, nest=True)

    if ch is not None:
        try:
            return np.asarray(ch.lonlat_to_healpix(nside, lons, lats, nest=True), dtype=np.int64)
        except Exception:
            try:
                return np.asarray(ch.lonlat_to_healpix(nside, lons, lats), dtype=np.int64)
            except Exception:
                logger.debug("cdshealpix present but call failed")

    raise RuntimeError("No HEALPix implementation available: install healpy or cdshealpix")

def compute_healpix_ids_from_polygon(nside: int, geom, n_samples: int = 200) -> np.ndarray:
    """Find all HEALPix cells that a polygon touches using dense boundary sampling.

    Samples the polygon boundary densely (not just vertices) and collects all
    unique HEALPix cell IDs. Also samples the polygon interior and bounding box
    corners to ensure complete coverage, preventing missed cells at polygon
    corners or near cell boundaries.

    Parameters
    ----------
    nside : int
        HEALPix nside parameter.
    geom : shapely Polygon or MultiPolygon
        Source geometry in lon/lat degrees.
    n_samples : int
        Number of samples along polygon boundary per polygon part.
        Higher values increase accuracy at the cost of computation time.

    Returns
    -------
    np.ndarray
        Sorted array of unique HEALPix cell IDs that the polygon touches.
    """
    if geom is None or (hasattr(geom, 'is_empty') and geom.is_empty):
        return np.array([], dtype=np.int64)

    sampled_lons = []
    sampled_lats = []

    polys = list(getattr(geom, 'geoms', [geom]))
    for poly in polys:
        if not hasattr(poly, 'exterior'):
            continue

        n_edge = max(n_samples // len(polys), 20)
        distances = np.linspace(0, poly.exterior.length, n_edge)
        for d in distances:
            pt = poly.exterior.interpolate(d)
            sampled_lons.append(pt.x)
            sampled_lats.append(pt.y)

        minx, miny, maxx, maxy = poly.bounds
        for cx, cy in [(minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)]:
            sampled_lons.append(cx)
            sampled_lats.append(cy)

        # Sample interior points to catch cells whose only intersection is interior
        centroid = poly.centroid
        sampled_lons.append(centroid.x)
        sampled_lats.append(centroid.y)

    if not sampled_lons:
        return np.array([], dtype=np.int64)

    lons_samp = np.array(sampled_lons, dtype=float)
    lats_samp = np.array(sampled_lats, dtype=float)
    lons_norm = np.mod(lons_samp, 360.0)
    lats_f = lats_samp

    mask = np.isfinite(lons_norm) & np.isfinite(lats_f)
    if not np.any(mask):
        return np.array([], dtype=np.int64)

    hids = compute_healpix_ids_from_lonlat(nside, lons_norm[mask], lats_f[mask])
    return np.unique(hids) if hids.size > 0 else np.array([], dtype=np.int64)

def _sample_great_circle_arc(v0, v1, n_samples):
    """Sample n_samples points along the great-circle arc from v0 to v1.

    Uses SLERP on the unit sphere — always takes the short arc.
    This is the sphere-native replacement for shapely's planar interpolate().
    """
    v0 = v0 / max(np.linalg.norm(v0), 1e-15)
    v1 = v1 / max(np.linalg.norm(v1), 1e-15)
    dot = float(np.dot(v0, v1))
    if dot > 0.999999:
        pts = np.tile(v0, (n_samples, 1))
        return pts.T
    theta = np.arccos(np.clip(dot, -1.0, 1.0))
    t = np.linspace(0.0, 1.0, n_samples)
    sin_t = np.sin(theta)
    if sin_t < 1e-15:
        pts = np.tile(v0, (n_samples, 1))
        return pts.T
    result = (np.outer(np.sin((1.0 - t) * theta), v0) + np.outer(np.sin(t * theta), v1)) / sin_t
    norms = np.linalg.norm(result, axis=1, keepdims=True)
    result = result / np.maximum(norms, 1e-15)
    return result.T  # (3, n_samples)

def _query_healpix_single_polygon(body, geom, nside, _healpy):
    """Find HEALPix cells for a single polygon using sphere-native sampling.

    Converts polygon vertices to unit vectors, samples great-circle arcs
    between consecutive vertices via SLERP, then converts back to lon/lat
    for HEALPix indexing. No antimeridian, no shapely interpolate.
    """
    coords = get_coordinates(geom)
    lons = coords[:, 0].astype(np.float64)
    lats = coords[:, 1].astype(np.float64)

    if lons.size < 3:
        return np.array([], dtype=np.int64)

    xyz = body.lonlat_to_xyz(lons, lats)  # (3, N)
    n_vertices = xyz.shape[1]
    n_edge = 80

    all_lons = []
    all_lats = []

    # Sample each edge via great-circle arc (including closing edge: last->first)
    for i in range(n_vertices):
        v0 = xyz[:, i]
        v1 = xyz[:, (i + 1) % n_vertices]
        arc = _sample_great_circle_arc(v0, v1, n_edge)  # (3, n_edge)
        arc_lon, arc_lat = body.xyz_to_lonlat(arc)
        all_lons.extend(arc_lon.tolist())
        all_lats.extend(arc_lat.tolist())

    # Interior point: centroid from unit vectors
    centroid_xyz = xyz.mean(axis=1)
    centroid_norm = np.linalg.norm(centroid_xyz)
    if centroid_norm > 1e-15:
        centroid_xyz = centroid_xyz / centroid_norm
        centroid_lon, centroid_lat = body.xyz_to_lonlat(centroid_xyz.reshape(3, 1))
        all_lons.extend([centroid_lon[0]] * 10)
        all_lats.extend([centroid_lat[0]] * 10)
    else:
        # Full-sphere: use first vertex
        lone, late = body.xyz_to_lonlat(xyz[:, 0].reshape(3, 1))
        all_lons.extend([lone[0]] * 10)
        all_lats.extend([late[0]] * 10)

    # Also add the original vertex coordinates (exact polygon corners)
    all_lons.extend(lons.tolist())
    all_lats.extend(lats.tolist())

    lons_samp = np.asarray(all_lons, dtype=np.float64)
    lats_samp = np.asarray(all_lats, dtype=np.float64)

    return compute_healpix_ids_from_lonlat(nside, lons_samp, lats_samp, body=body)

def _query_healpix_spherical(body, geom, nside, _healpy):
    """Find HEALPix cells intersecting a polygon via spherical query_disc.

    Handles MultiPolygon by processing each part separately.
    """
    geoms = list(getattr(geom, 'geoms', [geom]))
    all_hids = []
    for part in geoms:
        hids = _query_healpix_single_polygon(body, part, nside, _healpy)
        all_hids.extend(hids)
    return np.unique(np.asarray(all_hids, dtype=np.int64)) if all_hids else np.array([], dtype=np.int64)

# ADR-019: exhaustive FOV coverage via candidate search + exact intersection
def candidate_cells(body, geom, nside, _healpy, fact=16, margin_deg=1.0):
    """Return a conservative superset of HEALPix cells intersecting a polygon.

    Uses healpy.query_polygon for convex polygons (fast, native) and
    healpy.query_disc as a bounding-cap fallback for concave polygons.
    The candidate set is guaranteed to contain all touched cells; exact
    intersection filtering is required to remove false positives.

    Parameters
    ----------
    body : BodyGeometry
        Body geometry backend for lon/lat -> xyz conversion.
    geom : shapely Polygon or MultiPolygon
        Source geometry in lon/lat degrees.
    nside : int
        HEALPix nside parameter.
    _healpy : module
        healpy module (passed to avoid circular imports).
    fact : int
        Oversampling factor for healpy.query_polygon (default 16).
    margin_deg : float
        Angular margin in degrees added to the bounding cap radius for
        the query_disc fallback (default 1.0).

    Returns
    -------
    np.ndarray
        Sorted array of unique candidate HEALPix cell IDs.
    """
    geoms = list(getattr(geom, 'geoms', [geom]))
    all_hids = set()
    for part in geoms:
        coords = get_coordinates(part)
        lons = coords[:, 0].astype(np.float64)
        lats = coords[:, 1].astype(np.float64)

        # Remove duplicate consecutive vertices and closing point (degenerate corners break query_polygon)
        if lons.size >= 1 and lons[0] == lons[-1] and lats[0] == lats[-1]:
            lons = lons[:-1]
            lats = lats[:-1]
        unique_mask = np.concatenate([[True], (lons[1:] != lons[:-1]) | (lats[1:] != lats[:-1])])
        lons = lons[unique_mask]
        lats = lats[unique_mask]

        if lons.size < 3:
            continue

        xyz = body.lonlat_to_xyz(lons, lats)  # (3, N)

        # Always use query_disc for robustness. While query_polygon is faster for
        # convex polygons, it hard-errors on non-convex input and is unreliable
        # for antimeridian-crossing polygons. query_disc is always safe and the
        # exact-intersection step filters false positives efficiently.
        centroid_xyz = xyz.mean(axis=1)
        centroid_norm = np.linalg.norm(centroid_xyz)
        if centroid_norm > 1e-15:
            centroid_xyz = centroid_xyz / centroid_norm
        else:
            centroid_xyz = xyz[:, 0]

        # Max central angle from centroid to any vertex
        dots = np.clip(np.dot(centroid_xyz, xyz), -1.0, 1.0)
        max_angle = np.arccos(dots.min())
        radius = max_angle + np.radians(margin_deg)

        hids = _healpy.query_disc(
            nside, centroid_xyz, radius, inclusive=True, nest=True
        )

        all_hids.update(hids)

    return np.array(sorted(all_hids), dtype=np.int64) if all_hids else np.array([], dtype=np.int64)


def _filter_candidates_exact(candidate_hids, geom, nside, lon_convention):
    """Filter candidate cells to those actually intersecting the polygon.

    Uses shapely.intersects against cell geometries built via
    get_healpix_cell_geometry.

    Parameters
    ----------
    candidate_hids : np.ndarray
        Candidate HEALPix cell IDs from candidate_cells().
    geom : shapely Polygon or MultiPolygon
        Source geometry in lon/lat degrees.
    nside : int
        HEALPix nside parameter.
    lon_convention : str
        Longitude convention for cell geometry construction.

    Returns
    -------
    np.ndarray
        Filtered array of HEALPix cell IDs that truly intersect the polygon.
    """
    result = []
    for hid in candidate_hids:
        cell_geom = get_healpix_cell_geometry(hid, nside, lon_convention=lon_convention)
        if geom.intersects(cell_geom):
            result.append(hid)
    return np.array(result, dtype=np.int64)


def detect_lonlat_columns(gdf_sample) -> tuple[str | None, str | None]:
    """Auto-detect longitude and latitude columns from a DataFrame sample.

    Scans column names (case-insensitive) against common patterns for
    longitude and latitude fields. Returns the first match for each.

    Parameters
    ----------
    gdf_sample : pd.DataFrame or gpd.GeoDataFrame
        Sample DataFrame to inspect for column name patterns.

    Returns
    -------
    tuple[str | None, str | None]
        ``(lon_column, lat_column)`` or ``(None, None)`` if not found.
        Column names are returned with their original casing.
"""
    # Common column name patterns (case-insensitive)
    lon_patterns = ['lon', 'longitude', 'long', 'x', 'easting']
    lat_patterns = ['lat', 'latitude', 'y', 'northing']

    cols_lower = {col.lower(): col for col in gdf_sample.columns if col != 'geometry'}

    lon_col = None
    lat_col = None

    # Try to find longitude column
    for pattern in lon_patterns:
        for col_lower, col_orig in cols_lower.items():
            if pattern in col_lower:
                lon_col = col_orig
                break
        if lon_col:
            break

    # Try to find latitude column
    for pattern in lat_patterns:
        for col_lower, col_orig in cols_lower.items():
            if pattern in col_lower:
                lat_col = col_orig
                break
        if lat_col:
            break

    return lon_col, lat_col

def _parquet_column_names(input_path) -> list:
    """Read only the parquet schema footer to get column names (no data read).

    The footer is a tiny metadata block at the end of the file, so this is cheap
    regardless of file size and never materializes any rows in memory.
    """
    import pyarrow.parquet as pq
    return list(pq.ParquetFile(str(input_path)).schema_arrow.names)


def _find_geometry_column(colnames) -> str | None:
    """Return the first column whose name looks like a geometry column."""
    for col in colnames:
        cl = col.lower()
        if any(kw in cl for kw in ('polygon', 'geometry', 'wkt', 'wkb', 'geom')):
            return col
    return None

def compute_geo_statistics(input_path: Path, lon_col: str | None = None,
                          lat_col: str | None = None,
                          sample_size: int = 10000,
                          lon_convention: str | None = None) -> dict:
    """Compute geographical statistics for a GeoParquet file using DuckDB.

    Analyzes the coordinate distribution of a parquet file, either from
    explicit lon/lat columns or from geometry column centroids. Optionally
    filters data by longitude convention to assess coverage.

    Parameters
    ----------
    input_path : Path
        Path to input GeoParquet or parquet file.
    lon_col : str or None
        Name of longitude column. If None, will auto-detect or extract
        from geometry column.
    lat_col : str or None
        Name of latitude column. If None, will auto-detect or extract
        from geometry column.
    sample_size : int
        Number of rows to sample for geometry-based coordinate extraction.
    lon_convention : str or None
        Optional longitude convention for filtering:
        ``'0_360'`` for [0,360) × [-90,90],
        ``'minus_plus180'`` for [-180,180) × [-90,90],
        ``None`` (default) for no filtering (raw data).

    Returns
    -------
    dict
        Statistics dictionary with keys:
        ``lon``, ``lat`` (each with min, max, mean, std, count),
        ``source`` (``'columns'`` or ``'geometry'``),
        ``lon_col``, ``lat_col``, ``filtered``,
        ``total_count``, ``filtered_count``.
    """
    import duckdb
    import geopandas as gpd

    logger.info(f"Computing geo-statistics for {input_path}")

    # Read a small sample to detect columns
    # Try GeoParquet first, fallback to regular parquet
    try:
        sample_gdf = gpd.read_parquet(input_path, max_rows=100)
    except Exception as e:
        logger.debug(f"Could not read as GeoParquet: {e}")
        try:
            # Fallback to pandas for regular parquet using pyarrow
            import pyarrow.parquet as pq
            table = pq.read_table(input_path, columns=None)
            sample_df = table.slice(0, 100).to_pandas()
            # Create a mock GeoDataFrame structure for column detection
            sample_gdf = sample_df
        except Exception as e2:
            logger.warning(f"Could not read parquet file: {e2}")
            return {}

    # Auto-detect columns if not provided
    if lon_col is None or lat_col is None:
        detected_lon, detected_lat = detect_lonlat_columns(sample_gdf)
        lon_col = lon_col or detected_lon
        lat_col = lat_col or detected_lat

    # Check if columns exist in the data
    has_lon_col = lon_col is not None and lon_col in sample_gdf.columns
    has_lat_col = lat_col is not None and lat_col in sample_gdf.columns

    result = {
        'lon': {},
        'lat': {},
        'source': None,
        'lon_col': lon_col,
        'lat_col': lat_col,
        'filtered': lon_convention is not None,
        'lon_convention': lon_convention
    }

    # Determine filtering bounds based on convention
    where_clause = ""
    if lon_convention == '0_360':
        where_clause = f'WHERE "{lon_col}" >= 0 AND "{lon_col}" < 360 AND "{lat_col}" >= -90 AND "{lat_col}" <= 90'
        logger.info(f"Applying filtering: lon=[0,360), lat=[-90,90]")
    elif lon_convention == 'minus_plus180':
        where_clause = f'WHERE "{lon_col}" >= -180 AND "{lon_col}" < 180 AND "{lat_col}" >= -90 AND "{lat_col}" <= 90'
        logger.info(f"Applying filtering: lon=[-180,180), lat=[-90,90]")
    elif lon_convention is not None:
        logger.warning(f"Unknown lon_convention '{lon_convention}', computing raw statistics")
        result['filtered'] = False

    try:
        # Strategy 1: Use explicit lon/lat columns if available
        if has_lon_col and has_lat_col:
            logger.info(f"Using explicit columns: lon='{lon_col}', lat='{lat_col}'")
            result['source'] = 'columns'

            # Use DuckDB for efficient statistics computation
            con = duckdb.connect()

            # Get total count before filtering (if filtering is applied)
            if where_clause:
                total_query = f"SELECT COUNT(*) FROM read_parquet('{str(input_path)}')"
                total_count = con.execute(total_query).fetchone()[0]
                result['total_count'] = int(total_count)

            # Build query with optional WHERE clause for filtering
            query = f"""
            SELECT
                COUNT(*) as count,
                MIN("{lon_col}") as lon_min,
                MAX("{lon_col}") as lon_max,
                AVG("{lon_col}") as lon_mean,
                STDDEV("{lon_col}") as lon_std,
                MIN("{lat_col}") as lat_min,
                MAX("{lat_col}") as lat_max,
                AVG("{lat_col}") as lat_mean,
                STDDEV("{lat_col}") as lat_std
            FROM read_parquet('{str(input_path)}')
            {where_clause}
            """

            stats = con.execute(query).fetchone()
            con.close()

            filtered_count = int(stats[0])

            result['lon'] = {
                'min': float(stats[1]) if stats[1] is not None else None,
                'max': float(stats[2]) if stats[2] is not None else None,
                'mean': float(stats[3]) if stats[3] is not None else None,
                'std': float(stats[4]) if stats[4] is not None else None,
                'count': filtered_count
            }
            result['lat'] = {
                'min': float(stats[5]) if stats[5] is not None else None,
                'max': float(stats[6]) if stats[6] is not None else None,
                'mean': float(stats[7]) if stats[7] is not None else None,
                'std': float(stats[8]) if stats[8] is not None else None,
                'count': filtered_count
            }

            if where_clause:
                result['filtered_count'] = filtered_count

        # Strategy 2: Extract from geometry column (sample-based for efficiency)
        else:
            logger.info("Extracting coordinates from geometry column (sampling)")
            result['source'] = 'geometry'

            # Read a larger sample for better statistics
            try:
                gdf = gpd.read_parquet(input_path, max_rows=sample_size)
            except Exception:
                gdf = sample_gdf

            # Extract coordinates from geometries
            coords_list = []
            for geom in gdf.geometry:
                if geom is None or geom.is_empty:
                    continue

                # Handle different geometry types
                if geom.geom_type == 'Point':
                    coords_list.append([geom.x, geom.y])
                elif geom.geom_type in ['Polygon', 'MultiPolygon']:
                    # Use centroid for polygons
                    centroid = geom.centroid
                    coords_list.append([centroid.x, centroid.y])
                else:
                    # Try to get any coordinates
                    try:
                        c = get_coordinates(geom)
                        if len(c) > 0:
                            coords_list.append([c[0, 0], c[0, 1]])
                    except Exception:
                        continue

            if coords_list:
                coords = np.array(coords_list)
                lons = coords[:, 0]
                lats = coords[:, 1]

                # Filter out invalid values
                valid_mask = np.isfinite(lons) & np.isfinite(lats)
                lons = lons[valid_mask]
                lats = lats[valid_mask]

                if len(lons) > 0:
                    result['lon'] = {
                        'min': float(np.min(lons)),
                        'max': float(np.max(lons)),
                        'mean': float(np.mean(lons)),
                        'std': float(np.std(lons)),
                        'count': len(lons)
                    }
                    result['lat'] = {
                        'min': float(np.min(lats)),
                        'max': float(np.max(lats)),
                        'mean': float(np.mean(lats)),
                        'std': float(np.std(lats)),
                        'count': len(lats)
                    }

                    logger.info(f"Computed statistics from {len(lons)} sampled geometries")

    except Exception as e:
        logger.warning(f"Failed to compute geo-statistics: {e}")
        return {}

    return result

def format_geo_statistics(stats: dict) -> str:
    """Format geo-statistics for display using rich tables.

    Creates a human-readable table with geographical statistics including
    longitude/latitude min/max/mean/std, data source, column names used,
    and validation warnings. Falls back to plain text if rich is unavailable.

    Parameters
    ----------
    stats : dict
        Statistics dictionary from :func:`compute_geo_statistics`.

    Returns
    -------
    str
        Formatted string representation suitable for terminal display.
    """
    if not stats or not stats.get('lon') or not stats.get('lat'):
        return "No geo-statistics available"

    try:
        from rich.console import Console
        from rich.table import Table
        from io import StringIO

        console = Console(file=StringIO(), width=100)

        # Create main statistics table
        table = Table(title="Geographical Statistics", show_header=True, header_style="bold magenta")
        table.add_column("Statistic", style="cyan", width=12)
        table.add_column("Longitude", justify="right", style="green")
        table.add_column("Latitude", justify="right", style="green")

        lon = stats['lon']
        lat = stats['lat']

        # Add rows
        table.add_row("Count", f"{lon.get('count', 'N/A'):,}", f"{lat.get('count', 'N/A'):,}")
        table.add_row("Min", f"{lon.get('min', float('nan')):.6f}", f"{lat.get('min', float('nan')):.6f}")
        table.add_row("Max", f"{lon.get('max', float('nan')):.6f}", f"{lat.get('max', float('nan')):.6f}")
        table.add_row("Mean", f"{lon.get('mean', float('nan')):.6f}", f"{lat.get('mean', float('nan')):.6f}")
        table.add_row("Std Dev", f"{lon.get('std', float('nan')):.6f}", f"{lat.get('std', float('nan')):.6f}")

        console.print(table)

        # Add metadata
        console.print(f"\n[bold]Data Source:[/bold] {stats.get('source', 'unknown')}")
        if stats.get('lon_col'):
            console.print(f"[bold]Longitude Column:[/bold] {stats['lon_col']}")
        if stats.get('lat_col'):
            console.print(f"[bold]Latitude Column:[/bold] {stats['lat_col']}")

        # Show filtering information
        if stats.get('filtered'):
            total = stats.get('total_count', 0)
            filtered = stats.get('filtered_count', 0)
            if total > 0:
                pct = 100.0 * filtered / total
                dropped = total - filtered
                console.print(f"\n[bold yellow]Filtering Applied:[/bold yellow] --lon-convention {stats.get('lon_convention')}")
                console.print(f"  Total records: {total:,}")
                console.print(f"  After filtering: {filtered:,} ({pct:.1f}%)")
                if dropped > 0:
                    console.print(f"  [red]Dropped: {dropped:,} ({100.0 - pct:.1f}%)[/red]")
        else:
            console.print(f"\n[bold]Filtering:[/bold] None (raw data)")

        # Validation warnings
        lon_min, lon_max = lon.get('min'), lon.get('max')
        lat_min, lat_max = lat.get('min'), lat.get('max')

        warnings = []

        if lat_min is not None and lat_max is not None:
            if lat_min < -90 or lat_max > 90:
                warnings.append(f"⚠️  Latitude out of valid range [-90, 90]: [{lat_min:.2f}, {lat_max:.2f}]")

        # Suggest appropriate longitude convention based on data
        if lon_min is not None and lon_max is not None:
            if lon_min >= 0 and lon_max <= 360:
                console.print(f"\n[bold cyan]Suggested convention:[/bold cyan] --lon-convention 0_360")
            elif lon_min >= -180 and lon_max <= 180:
                console.print(f"\n[bold cyan]Suggested convention:[/bold cyan] --lon-convention minus_plus180")
            else:
                warnings.append(f"⚠️  Longitude range [{lon_min:.2f}, {lon_max:.2f}] doesn't fit standard conventions")

        if warnings:
            console.print("\n[bold red]Validation Warnings:[/bold red]")
            for warning in warnings:
                console.print(f"  {warning}")

        # Get the string output
        output = console.file.getvalue()
        return output

    except ImportError:
        # Fallback to simple text formatting if rich is not available
        lines = ["=" * 60]
        lines.append("GEOGRAPHICAL STATISTICS")
        lines.append("=" * 60)
        lines.append(f"{'Statistic':<15} {'Longitude':>20} {'Latitude':>20}")
        lines.append("-" * 60)

        lon = stats['lon']
        lat = stats['lat']

        lines.append(f"{'Count':<15} {lon.get('count', 'N/A'):>20,} {lat.get('count', 'N/A'):>20,}")
        lines.append(f"{'Min':<15} {lon.get('min', float('nan')):>20.6f} {lat.get('min', float('nan')):>20.6f}")
        lines.append(f"{'Max':<15} {lon.get('max', float('nan')):>20.6f} {lat.get('max', float('nan')):>20.6f}")
        lines.append(f"{'Mean':<15} {lon.get('mean', float('nan')):>20.6f} {lat.get('mean', float('nan')):>20.6f}")
        lines.append(f"{'Std Dev':<15} {lon.get('std', float('nan')):>20.6f} {lat.get('std', float('nan')):>20.6f}")
        lines.append("=" * 60)
        lines.append(f"Data Source: {stats.get('source', 'unknown')}")
        if stats.get('lon_col'):
            lines.append(f"Longitude Column: {stats['lon_col']}")
        if stats.get('lat_col'):
            lines.append(f"Latitude Column: {stats['lat_col']}")

        # Show filtering information
        if stats.get('filtered'):
            total = stats.get('total_count', 0)
            filtered = stats.get('filtered_count', 0)
            if total > 0:
                pct = 100.0 * filtered / total
                dropped = total - filtered
                lines.append(f"\nFiltering Applied: --lon-convention {stats.get('lon_convention')}")
                lines.append(f"  Total records: {total:,}")
                lines.append(f"  After filtering: {filtered:,} ({pct:.1f}%)")
                if dropped > 0:
                    lines.append(f"  Dropped: {dropped:,} ({100.0 - pct:.1f}%)")
        else:
            lines.append("\nFiltering: None (raw data)")

        return "\n".join(lines)

def process_partition(gdf, nside: int, mode: str, base_index: int | None = None,
                     lon_convention: str = 'minus_plus180',
                     lon_col: str | None = None,
                     lat_col: str | None = None,
                     data_psf=None, cell_psf=None, combine_method='multiply',
                     body: BodyGeometry | None = None,
                     exhaustive: bool = False
                        ) -> pd.DataFrame:
    """Process a single dask partition and return DataFrame of assignments.

    This is the core assignment function that maps each source in a partition to
    HEALPix cells. It supports two assignment workflows:

    1. **Scalar lon/lat columns** (efficient for strict mode): pass ``lon_col``
       and ``lat_col`` for direct ``healpy.ang2pix`` indexing.
    2. **Geometry-based** (for fuzzy mode): pass geometries via ``gdf.geometry``
       for polygon-to-cell intersection.

    In fuzzy mode, FOV polygons are assigned to all HEALPix cells they touch
    using spherical polygon sampling. This correctly handles polygons that cross
    cell boundaries, unlike vertex-only sampling.

    When ``exhaustive=True``, fuzzy mode uses a two-tier candidate search
    (``healpy.query_polygon`` / ``healpy.query_disc``) followed by exact
    ``shapely.intersects`` filtering to guarantee complete coverage for large
    FOVs. This is slower but exact; the default sampling path is retained for
    performance.

    Supports multi-resolution optimization where the finest nside is computed
    independently and lower nsides are derived by bit-shift aggregation.

    Parameters
    ----------
    gdf : GeoDataFrame or DataFrame
        A dask partition containing source observations.
    nside : int
        HEALPix nside parameter (power of 2).
    mode : str
        Assignment mode: ``'strict'`` (single cell per source) or
        ``'fuzzy'`` (all touched cells per source).
    base_index : int or None
        Base index for global source_id generation across partitions.
    lon_convention : str
        Longitude convention: ``'minus_plus180'`` or ``'0_360'``.
    lon_col : str or None
        Longitude column name. If None, use geometry column.
    lat_col : str or None
        Latitude column name. If None, use geometry column.
    data_psf : callable or None
        Optional data Point Spread Function. Called as ``data_psf(dx, dy)``.
    cell_psf : callable or None
        Optional cell Point Spread Function. Called as ``cell_psf(dx, dy)``.
    combine_method : str
        How to combine PSF weights: ``'multiply'``, ``'sum'``, ``'min'``,
        or ``'max'``.
    body : BodyGeometry or None
        Optional body geometry backend for coordinate conversion.
    exhaustive : bool
        If True, use candidate-search + exact-intersection for fuzzy mode
        (requires ``body`` to be set). Default False.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns ``['source_id', 'healpix_id']`` and optional
        ``'weight'`` column when PSF is active.
    """
    import pandas as _pd

    # Set lon/lat bounds based on convention
    if lon_convention == '0_360':
        lon_min, lon_max = 0.0, 360.0
        lat_min, lat_max = -90.0, 90.0
    elif lon_convention == 'minus_plus180':
        lon_min, lon_max = -180.0, 180.0
        lat_min, lat_max = -90.0, 90.0
    else:
        raise ValueError(f"Invalid lon_convention: {lon_convention}")

    out_rows = []
    dropped_count = 0
    total_count = 0
    dropped_prefilter = 0

    if gdf is None or len(gdf) == 0:
        return _pd.DataFrame(columns=["source_id", "healpix_id"])

    # Determine source IDs
    if base_index is not None:
        src_ids = base_index + np.arange(len(gdf), dtype=np.int64)
    else:
        src_ids = gdf["source_id"].to_numpy() if "source_id" in gdf.columns else gdf.index.to_numpy()

    # --- WORKFLOW 1: Scalar lon/lat columns (efficient) ---
    if lon_col is not None and lat_col is not None and lon_col in gdf.columns and lat_col in gdf.columns:
        logger.debug(f"Using scalar lon/lat workflow: {lon_col}, {lat_col}")

        lons = gdf[lon_col].to_numpy(dtype=np.float64)
        lats = gdf[lat_col].to_numpy(dtype=np.float64)

        for i, (src_id, lon, lat) in enumerate(zip(src_ids, lons, lats)):
            try:
                total_count += 1

                # Validate coordinate bounds
                if not (np.isfinite(lon) and np.isfinite(lat)):
                    dropped_count += 1
                    dropped_prefilter += 1
                    continue

                if lat < lat_min or lat > lat_max or lon < lon_min or lon > lon_max:
                    dropped_count += 1
                    dropped_prefilter += 1
                    logger.debug(f"Dropped {src_id}: out of bounds ({lon}, {lat})")
                    continue

                # Compute HEALPix cell for this point
                hid = compute_healpix_ids_from_lonlat(
                    nside, np.array([lon]), np.array([lat]), body=body
                )[0]
                weight = 1.0

                # Apply PSF if present (point mode)
                if data_psf or cell_psf:
                    w_data = data_psf(0.0, 0.0) if data_psf else 1.0  # Point is at (0,0) relative to itself
                    w_cell = cell_psf(0.0, 0.0) if cell_psf else 1.0
                    if combine_method == 'multiply':
                        weight = w_data * w_cell
                    elif combine_method == 'sum':
                        weight = w_data + w_cell
                    elif combine_method == 'min':
                        weight = min(w_data, w_cell)
                    elif combine_method == 'max':
                        weight = max(w_data, w_cell)

                out_rows.append({"source_id": int(src_id), "healpix_id": int(hid), "weight": weight})

            except Exception as e:
                logger.debug(f"Skipping source {src_id}: {e}")
                dropped_count += 1
                total_count += 1
                continue

    # --- WORKFLOW 2: Geometry-based (fuzzy mode or explicit geometry column) ---
    elif hasattr(gdf, 'geometry') and gdf.geometry is not None:
        logger.debug("Using geometry-based workflow")

        # ADR-019: validate exhaustive mode prerequisites before processing
        if exhaustive and body is None:
            raise NotImplementedError(
                "exhaustive=True requires body geometry (use body=Sphere()). "
                "Planar exhaustive mode not yet implemented."
            )

        for src_id, geom in zip(src_ids, gdf.geometry.to_numpy()):
            try:
                if geom is None or (hasattr(geom, 'is_empty') and geom.is_empty):
                    continue

                def _is_valid_latitude(geometry):
                    """Only reject NaN coords or lat outside [-90, 90].
                    ADR-013: lon wrapping is handled by body.lonlat_to_xyz (np.mod)."""
                    if geometry is None or (hasattr(geometry, 'is_empty') and geometry.is_empty):
                        return False
                    geoms = [geometry] if getattr(geometry, "geom_type", "") == "Polygon" else list(getattr(geometry, "geoms", [geometry]))
                    for g in geoms:
                        if getattr(g, "exterior", None) is not None:
                            for coord in g.exterior.coords:
                                lon, lat = coord[0], coord[1]
                                if not (np.isfinite(lon) and np.isfinite(lat)):
                                    return False
                                if lat < -90.0 or lat > 90.0:
                                    return False
                        for interior in getattr(g, "interiors", []):
                            for coord in interior.coords:
                                lon, lat = coord[0], coord[1]
                                if not (np.isfinite(lon) and np.isfinite(lat)):
                                    return False
                                if lat < -90.0 or lat > 90.0:
                                    return False
                    return True

                total_count += 1
                geom2 = geom  # ADR-013: no antimeridian.fix_polygon; sphere handles wrapping

                if not _is_valid_latitude(geom2):
                    dropped_count += 1
                    dropped_prefilter += 1
                    logger.debug(f"Dropped geometry {src_id}: invalid coordinates")
                    continue

                try:
                    coords = get_coordinates(geom2)
                except Exception:
                    coords_list = []
                    if isinstance(geom2, Polygon):
                        coords_list.extend(np.asarray(geom2.exterior.coords, dtype=float))
                        for r in geom2.interiors:
                            coords_list.extend(np.asarray(r.coords, dtype=float))
                    elif isinstance(geom2, MultiPolygon):
                        for part in geom2.geoms:
                            coords_list.extend(np.asarray(part.exterior.coords, dtype=float))
                            for r in part.interiors:
                                coords_list.extend(np.asarray(r.coords, dtype=float))
                    else:
                        try:
                            coords_list = np.asarray(list(geom2.coords), dtype=float)
                        except Exception:
                            coords_list = []

                    if len(coords_list) == 0:
                        continue
                    coords = np.asarray(coords_list, dtype=float)

                if coords.size == 0:
                    continue

                lons = coords[:, 0].astype(float)
                lats = coords[:, 1].astype(float)

                mask = np.isfinite(lons) & np.isfinite(lats)
                if not np.any(mask):
                    dropped_count += 1
                    logger.debug(f"Dropped geometry {src_id} - all coordinates non-finite")
                    continue

                lons = lons[mask]
                lats = lats[mask]

                if mode == 'fuzzy' and geom2.geom_type in ('Polygon', 'MultiPolygon'):
                    if exhaustive:
                        # ADR-019: exhaustive candidate search + exact intersection
                        if body is None:
                            raise NotImplementedError(
                                "exhaustive=True requires body geometry (use body=Sphere()). "
                                "Planar exhaustive mode not yet implemented."
                            )
                        candidates = candidate_cells(body, geom2, nside, _healpy)
                        hids = _filter_candidates_exact(candidates, geom2, nside, lon_convention)
                    elif body is None:
                        # ADR-013: planar path (no body)
                        hids = compute_healpix_ids_from_polygon(nside, geom2)
                    else:
                        # ADR-013: spherical query_disc replaces STRtree + dense sampling.
                        # No antimeridian, no shapely cell polygons, no dense boundary fallback.
                        hids = _query_healpix_spherical(body, geom2, nside, _healpy)
                else:
                    hids = compute_healpix_ids_from_lonlat(nside, lons, lats, body=body)
                if hids.size == 0:
                    continue

                unique = np.unique(hids)
                if mode == "strict":
                    if unique.size == 1:
                        weight = 1.0
                        if data_psf or cell_psf:
                            src_centroid = geom.centroid
                            cell_geom = get_healpix_cell_geometry(unique[0], nside, lon_convention=lon_convention)
                            dx = src_centroid.x - cell_geom.centroid.x
                            dy = src_centroid.y - cell_geom.centroid.y
                            w_data = data_psf(dx, dy) if data_psf else 1.0
                            w_cell = cell_psf(dx, dy) if cell_psf else 1.0
                            if combine_method == 'multiply':
                                weight = w_data * w_cell
                            elif combine_method == 'sum':
                                weight = w_data + w_cell
                            elif combine_method == 'min':
                                weight = min(w_data, w_cell)
                            elif combine_method == 'max':
                                weight = max(w_data, w_cell)
                        out_rows.append({"source_id": int(src_id), "healpix_id": int(unique[0]), "weight": weight})
                    else:
                        out_rows.append({"source_id": int(src_id), "healpix_id": int(unique[0]), "weight": 1.0})
                else:
                    for hid in unique:
                        weight = 1.0
                        if data_psf or cell_psf:
                            src_centroid = geom.centroid
                            cell_geom = get_healpix_cell_geometry(hid, nside, lon_convention=lon_convention)
                            dx = src_centroid.x - cell_geom.centroid.x
                            dy = src_centroid.y - cell_geom.centroid.y
                            w_data = data_psf(dx, dy) if data_psf else 1.0
                            w_cell = cell_psf(dx, dy) if cell_psf else 1.0
                            if combine_method == 'multiply':
                                weight = w_data * w_cell
                            elif combine_method == 'sum':
                                weight = w_data + w_cell
                            elif combine_method == 'min':
                                weight = min(w_data, w_cell)
                            elif combine_method == 'max':
                                weight = max(w_data, w_cell)
                        out_rows.append({"source_id": int(src_id), "healpix_id": int(hid), "weight": weight})

            except Exception as e:
                logger.debug(f"Skipping source {src_id} due to error: {e}")
                total_count += 1
                dropped_count += 1
                continue

    # --- NO WORKFLOW AVAILABLE ---
    else:
        logger.error(f"Partition has no usable lon/lat columns ({lon_col}, {lat_col}) and no geometry column. Cannot process.")
        raise ValueError(f"Cannot process partition: no lon/lat columns and no geometry")

    # Log statistics
    if total_count > 0:
        drop_pct = 100.0 * dropped_count / total_count if total_count > 0 else 0
        if dropped_count > 0:
            logger.info(f"Partition (lon_convention={lon_convention}): processed {total_count} geometries, "
                       f"dropped {dropped_count} ({drop_pct:.1f}%)")

    if len(out_rows) == 0:
        return _pd.DataFrame(columns=["source_id", "healpix_id", "weight"])

    df_out = _pd.DataFrame(out_rows)
    df_out["source_id"] = df_out["source_id"].astype(np.int64)
    df_out["healpix_id"] = df_out["healpix_id"].astype("UInt64")
    if 'weight' in df_out.columns:
        df_out["weight"] = df_out["weight"].astype(float)
    return df_out

def add_psf_weights_to_sidecar(
    sidecar_df,
    src_geoms,
    cell_geoms,
    data_psf=None,
    cell_psf=None,
    combine_method='multiply',
    normalize=True
):
    """Add a ``weight`` column to the sidecar DataFrame using PSF functions.

    For each source-to-cell assignment, computes the weight by evaluating the
    data PSF and cell PSF at the centroid-to-centroid offset, then combines
    them using the specified method. Optionally normalizes weights per cell
    so they sum to 1.0.

    Parameters
    ----------
    sidecar_df : pd.DataFrame
        DataFrame with at least ``source_id`` and ``healpix_id`` columns.
    src_geoms : sequence
        Source geometries indexed by ``source_id``.
    cell_geoms : dict or sequence
        Mapping from ``healpix_id`` to cell geometry.
    data_psf : callable or None
        Data Point Spread Function. Called as ``data_psf(dx, dy)``.
    cell_psf : callable or None
        Cell Point Spread Function. Called as ``cell_psf(dx, dy)``.
    combine_method : str
        How to combine PSF weights: ``'multiply'``, ``'sum'``, ``'min'``,
        or ``'max'``.
    normalize : bool
        If True, normalize weights per cell so they sum to 1.0.

    Returns
    -------
    pd.DataFrame
        Copy of ``sidecar_df`` with an added ``weight`` column.
    """
    weights = []
    for row in sidecar_df.itertuples(index=False):
        src_id = row.source_id
        cell_id = row.healpix_id
        src_geom = src_geoms[src_id]
        cell_geom = cell_geoms[cell_id]
        w = compute_assignment_weight(src_geom, cell_geom, data_psf, cell_psf, combine_method)
        weights.append(w)
    sidecar_df = sidecar_df.copy()
    sidecar_df['weight'] = weights
    if normalize:
        sidecar_df = normalize_weights_per_cell(sidecar_df, cell_col='healpix_id', weight_col='weight')
    return sidecar_df

def normalize_weights_per_cell(df, cell_col='healpix_id', weight_col='weight'):
    """Normalize weights so that sum of weights per cell is 1.0."""
    sums = df.groupby(cell_col)[weight_col].transform('sum')
    df[weight_col] = df[weight_col] / sums
    return df

def _aggregate_healpix(df, nside_max, nside_target,
                       no_psf_normalize=False):
    """Bit-shift cell IDs from nside_max to nside_target and sum weights.

    ADR-015: parent-child relationship in NEST ordering.
    For each cell at nside_max, its parent at nside_target is
    healpix_id >> (2 * log2(nside_max / nside_target)).

    Args:
        df: DataFrame with columns [source_id, healpix_id] and optional 'weight'.
        nside_max: highest (finest) nside in the current run.
        nside_target: the lower nside to derive.
        no_psf_normalize: if True, skip per-cell weight normalization.

    Returns:
        DataFrame with columns [source_id, healpix_id, weight] where healpix_id
        is now in nside_target resolution and weights are aggregated.
    """
    import numpy as _np

    if nside_target == nside_max:
        return df.copy()

    shift = 2 * int(_np.log2(nside_max // nside_target))
    agg = df.copy()
    hids_int = _np.array(agg['healpix_id'].values, dtype=_np.int64)
    agg['healpix_parent'] = hids_int >> shift
    grouped = agg.groupby(['source_id', 'healpix_parent'], sort=False).agg({'weight': 'sum'}).reset_index()
    grouped.rename(columns={'healpix_parent': 'healpix_id'}, inplace=True)
    grouped['healpix_id'] = grouped['healpix_id'].astype('UInt64')
    if not no_psf_normalize and 'weight' in grouped.columns:
        sums = grouped.groupby('healpix_id')['weight'].transform('sum')
        grouped['weight'] = grouped['weight'] / sums
    return grouped[['source_id', 'healpix_id', 'weight']]

def _write_single_parquet(df, out_file, nside, mode, has_weight=True):
    """Write a DataFrame as a single parquet file with sidecar metadata."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([
        ("source_id", pa.int64()),
        ("healpix_id", pa.uint64()),
    ] + ([("weight", pa.float64())] if has_weight else []))
    pq_meta = {"nside": str(nside), "mode": mode, "order": "nested"}
    schema = schema.with_metadata({k: v.encode() for k, v in pq_meta.items()})
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, str(out_file))

def compute_assignment_weight(
    src_geom,
    cell_geom,
    data_psf=None,
    cell_psf=None,
    combine_method='multiply',
    data_psf_sigma=None,
    cell_psf_sigma=None
):
    """
    Compute the assignment weight for a (source geometry, cell geometry) pair.
    - data_psf: callable or None
    - cell_psf: callable or None
    - combine_method: 'multiply', 'sum', 'min', 'max'
    """
    # For now, use centroid-to-centroid distance for polygons
    # (future: integrate over geometry or use rasterized PSF)
    src_centroid = src_geom.centroid
    cell_centroid = cell_geom.centroid
    dx = src_centroid.x - cell_centroid.x
    dy = src_centroid.y - cell_centroid.y
    w_data = data_psf(dx, dy) if data_psf else 1.0
    w_cell = cell_psf(dx, dy) if cell_psf else 1.0
    if combine_method == 'multiply':
        return w_data * w_cell
    elif combine_method == 'sum':
        return w_data + w_cell
    elif combine_method == 'min':
        return min(w_data, w_cell)
    elif combine_method == 'max':
        return max(w_data, w_cell)
    else:
        raise ValueError(f"Unknown combine_method: {combine_method}")

def build_output_path(input_path: Path, mode: str, nside: int) -> Path:
    """Build output path for sidecar file based on input and parameters.

    Constructs a descriptive filename that encodes the processing parameters
    for easy identification and downstream parsing.

    Parameters
    ----------
    input_path : Path
        Source input file path.
    mode : str
        Assignment mode: ``'strict'`` or ``'fuzzy'``.
    nside : int
        HEALPix nside parameter.

    Returns
    -------
    Path
        Output path in the same directory as input, with format::

            <stem>.cell-healpix_assignment-<mode>_nside-<nside>_order-nested.parquet
    """
    return input_path.parent / f"{input_path.stem}.cell-healpix_assignment-{mode}_nside-{nside}_order-nested.parquet"

def write_sidecar_metadata(output_path: Path, input_path: Path, nside: int, mode: str,
                           lon_convention: str, ncores: int, args,
                           derived_from_parent: int | None = None) -> Path:
    """Write sidecar processing metadata to a JSON companion file.

    Creates a ``.meta.json`` file alongside the sidecar parquet file with
    complete processing parameters, HEALPix configuration, and provenance
    information. When the sidecar is derived from a higher-nside computation
    via bit-shift aggregation, the parent nside is recorded.

    Parameters
    ----------
    output_path : Path
        Path to the sidecar parquet file.
    input_path : Path
        Path to the source input file.
    nside : int
        HEALPix nside parameter.
    mode : str
        Assignment mode: ``'strict'`` or ``'fuzzy'``.
    lon_convention : str
        Longitude convention used: ``'0_360'`` or ``'minus_plus180'``.
    ncores : int
        Number of Dask worker cores used.
    args : argparse.Namespace
        Parsed command-line arguments (for recording PSF settings, etc.).
    derived_from_parent : int or None
        If set, this sidecar was derived from a higher-nside sidecar via
        NEST bit-shift aggregation. The value is the parent nside.

    Returns
    -------
    Path
        Path to the written ``.meta.json`` metadata file.
    """
    from datetime import datetime, timezone
    from healpyxel.metadata import HEALPyxelxMetadata

    metadata = {
        'processing': {
            'stage': 'sidecar',
            'timestamp': datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            'source_file': str(input_path.absolute()),
            'output_file': str(output_path.absolute())
        },
        'healpix': {
            'nside': nside,
            'mode': mode,
            'order': 'nested',
            'npix': 12 * nside ** 2
        },
        'coordinates': {
            'lon_convention': lon_convention,
            'lon_range': [0, 360] if lon_convention == '0_360' else [-180, 180],
            'lat_range': [-90, 90]
        },
        'processing_params': {
            'ncores': ncores,
            'coalesced': not args.no_coalesce,
            'derived_from_parent': derived_from_parent,
            'data_psf': getattr(args, 'data_psf', None),
            'data_psf_sigma_level': getattr(args, 'data_psf_sigma_level', None),
            'cell_psf': getattr(args, 'cell_psf', None),
            'cell_psf_sigma_level': getattr(args, 'cell_psf_sigma_level', None),
            'psf_combine': getattr(args, 'psf_combine', None),
            'psf_normalize': not getattr(args, 'no_psf_normalize', False)
        }
    }

    return HEALPyxelxMetadata.write_json(metadata, output_path, validate=True)

def validate_nside(nside: int) -> bool:
    """Validate that nside is a positive power of two."""
    return nside > 0 and (nside & (nside - 1)) == 0

_BODY_REGISTRY = {
    'sphere': Sphere,
    'ellipsoid': Ellipsoid,
    'dsk': SpiceDSK,
}

def get_body(body_model: str, **kwargs) -> BodyGeometry:
    """Create a BodyGeometry instance from a model name.

    Factory function that instantiates the appropriate body geometry backend
    based on the model name. Supports sphere, ellipsoid, and SPICE DSK models.

    Parameters
    ----------
    body_model : str
        One of ``'sphere'``, ``'ellipsoid'``, or ``'dsk'``.
    **kwargs
        Passed to the backend constructor. For ``'sphere'`` and
        ``'ellipsoid'``: ``radius`` (float, default 1.0) and
        ``polar_radius`` (float or None). For ``'dsk'``: no arguments needed.

    Returns
    -------
    BodyGeometry
        Instance of the selected geometry backend.

    Raises
    ------
    ValueError
        If ``body_model`` is not a recognized model name.
    """
    cls = _BODY_REGISTRY.get(body_model.lower())
    if cls is None:
        raise ValueError(
            f"Unknown body model '{body_model}'. "
            f"Choose from: {list(_BODY_REGISTRY.keys())}"
        )
    return cls(**kwargs)

def _get_body(config) -> BodyGeometry:
    """Extract body geometry from config (dict or argparse Namespace).

    Defaults to Sphere(radius=1.0) if not specified.
    """
    model_name = _get_config(config, 'body_model', 'sphere')
    radius = _get_config(config, 'body_radius', 1.0)
    polar_radius = _get_config(config, 'body_polar_radius', None)
    if model_name == 'ellipsoid':
        return Ellipsoid(radius=radius, polar_radius=polar_radius)
    elif model_name == 'dsk':
        return SpiceDSK()
    else:
        return Sphere(radius=radius)

def parse_arguments(argv=None):
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Create HEALPix sidecar mapping source geometries to cells.")
    parser.add_argument("--input", "-i", required=True, help="Path to input GeoParquet file")
    parser.add_argument("--nside", "-n", type=int, nargs='+', default=None,
                        help="One or more HEALPix nside values (powers of 2). Required unless --geo-stats is used. Example: -n 64 128")
    parser.add_argument("--mode", "-m", choices=["strict", "fuzzy"], default="fuzzy",
                        help="Assignment mode: strict (single-cell centroid) or fuzzy (all cells whose geometry intersects the FOV polygon, using R-tree spatial index)")
    parser.add_argument("--ncores", type=int, default=max(1, (os.cpu_count() or 2) - 1),
                        help="Number of cores to use for Dask workers (defaults to cpu_count-1)")
    parser.add_argument("--output_dir", "-o", default=None,
                        help="Directory to write the output file (defaults to same folder as input)")
    parser.add_argument("--no-coalesce", dest="no_coalesce", action="store_true",
                        help="Do not coalesce partitions into a single file; write partitioned parquet (default: coalesce to single file)")
    parser.add_argument("--lon-convention", type=str, default=None, choices=['0_360', 'minus_plus180'],
                        help="Longitude convention: '0_360' for [0,360) or 'minus_plus180' for [-180,180). Required unless --geo-stats is used. Can be used with --geo-stats to apply filtering.")
    parser.add_argument("--geo-stats", action="store_true",
                        help="Compute and display geographical statistics (lon/lat ranges, mean, std) before processing")
    parser.add_argument("--lon-col", type=str, default=None,
                        help="Longitude column name (if not specified, will auto-detect or extract from geometry)")
    parser.add_argument("--lat-col", type=str, default=None,
                        help="Latitude column name (if not specified, will auto-detect or extract from geometry)")
    parser.add_argument("--geometry", action="store_true",
                        help="Force geometry-based assignment using the geometry column (works in both strict and fuzzy "
                             "modes, for Point and Polygon geometries). When set, --lon-col/--lat-col are ignored.")
    parser.add_argument("--stats-sample-size", type=int, default=10000,
                        help="Number of rows to sample when extracting coordinates from geometry (default: 10000)")
    parser.add_argument("--loglevel", "-l", choices=["debug", "info", "warning", "error"], default="info",
                        help="Set logging level (default: info)")

    # Add PSF-related CLI arguments to an argparse parser
    parser.add_argument('--data-psf', type=str, default='none', choices=['none', 'gaussian'],
                        help='Data point spread function type (default: none)')
    parser.add_argument('--data-psf-sigma-level', type=float, default=2.0,
                        help='Sigma level for data PSF (default: 2.0)')
    parser.add_argument('--cell-psf', type=str, default='none', choices=['none', 'gaussian'],
                        help='Cell spread function type (default: none)')
    parser.add_argument('--cell-psf-sigma-level', type=float, default=2.0,
                        help='Sigma level for cell PSF (default: 2.0)')
    parser.add_argument('--psf-combine', type=str, default='multiply', choices=['multiply', 'sum', 'min', 'max'],
                        help='How to combine data and cell PSF weights (default: multiply)')
    parser.add_argument('--no-psf-normalize', action='store_true',
                        help='Disable normalization of weights per cell (default: normalize)')
    parser.add_argument('--no-multi-res-optimize', action='store_true',
                        help='Disable multi-resolution optimization: recompute each nside independently '
                             '(default: optimize by computing highest nside once and bit-shift for lower nsides)')

    parser.add_argument('--body-model', type=str, default='sphere',
                        choices=['sphere', 'ellipsoid', 'dsk'],
                        help='Body geometry model for coordinate conversion (default: sphere). '
                             'Use ellipsoid for Earth/Mars applications. dsk is not yet implemented.')
    parser.add_argument('--body-radius', type=float, default=1.0,
                        help='Body equatorial radius in arbitrary units (default: 1.0). '
                             'Only used for sphere/ellipsoid models.')
    parser.add_argument('--body-polar-radius', type=float, default=None,
                        help='Body polar radius (for ellipsoid flattening). If None, '
                             'body is spherical (default: None).')

    return parser.parse_args(argv)

class PSF:
    """Base class for Point Spread Functions (PSF)."""
    def __init__(self):
        pass
    def __call__(self, dx, dy):
        raise NotImplementedError

class GaussianPSF(PSF):
    """2D Gaussian PSF centered at (0,0)."""
    def __init__(self, sigma=None):
        super().__init__()
        self.sigma = sigma  # If None, must be set by user or context
    def __call__(self, dx, dy):
        if self.sigma is None:
            raise ValueError("Sigma must be set for GaussianPSF.")
        r2 = dx**2 + dy**2
        return np.exp(-0.5 * r2 / (self.sigma**2))

PSF_REGISTRY = {
    'gaussian': GaussianPSF,
    'none': lambda *a, **k: 1.0,
}

def get_psf(psf_type, sigma=None):
    if psf_type == 'none':
        return lambda dx, dy: 1.0
    cls = PSF_REGISTRY.get(psf_type, None)
    if cls is None:
        raise ValueError(f"Unknown PSF type: {psf_type}")
    return cls(sigma=sigma)

def write_partitioned_output(tasks, out_file: Path, nparts: int) -> int:
    """Write output as partitioned parquet files (one per partition).

    Returns:
        Total number of rows written
    """
    import dask

    out_part_dir = out_file.with_suffix('.parts')
    out_part_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing partitioned parquet to {out_part_dir} (no coalesce requested, {nparts} partitions)")
    parts = dask.compute(*tasks)
    total_rows = 0
    files_written = 0
    for idx, df_part in enumerate(parts):
        if df_part is None or len(df_part) == 0:
            continue
        df_part = df_part.astype({"source_id": "int64", "healpix_id": "UInt64"})
        part_path = out_part_dir / f"part-{idx:06d}.parquet"
        try:
            df_part.to_parquet(str(part_path), engine="pyarrow", index=False)
            total_rows += len(df_part)
            files_written += 1
        except Exception:
            # fallback: write via pyarrow Table to ensure metadata compatibility
            try:
                import pyarrow as pa
                table = pa.Table.from_pandas(df_part, preserve_index=False)
                import pyarrow.parquet as pq
                pq.write_table(table, str(part_path))
                total_rows += len(df_part)
                files_written += 1
            except Exception:
                logger.exception("Failed to write partitioned parquet for %s", part_path)
    logger.info(f"Wrote partitioned parquet to {out_part_dir}: {files_written} files, {total_rows} total rows")
    return total_rows

def write_coalesced_output(tasks, out_file: Path, nside: int, mode: str, ncores: int, nparts: int) -> int:
    """Write output as a single coalesced parquet file with incremental batching.

    Returns:
        Total number of rows written
    """
    import dask
    import pyarrow as pa
    import pyarrow.parquet as pq

    logger.info(f"Computing {nparts} partitions and writing single parquet file to {out_file}")

    if nparts == 0:
        # nothing to write; write empty file with explicit schema and metadata
        schema = pa.schema([
            ("source_id", pa.int64()),
            ("healpix_id", pa.uint64()),
        ])
        pq_meta = {"nside": str(nside), "mode": mode, "order": "nested"}
        schema = schema.with_metadata({k: v.encode() for k, v in pq_meta.items()})
        empty_table = pa.Table.from_pandas(
            pd.DataFrame(columns=["source_id", "healpix_id"]).astype({"source_id": "int64", "healpix_id": "UInt64"}),
            schema=schema, preserve_index=False
        )
        pq.write_table(empty_table, str(out_file))
        logger.info(f"Wrote empty output {out_file}")
        return 0

    # --- Patch: Dynamically detect if 'weight' column is present in the first non-empty batch ---
    batch_size = max(1, ncores)
    schema = None
    pq_meta = {"nside": str(nside), "mode": mode, "order": "nested"}
    first_batch_has_weight = False

    # Find the first non-empty batch to determine schema
    for i in range(0, nparts, batch_size):
        batch = tasks[i : i + batch_size]
        import dask
        res = dask.compute(*batch)
        non_empty = [r for r in res if (r is not None and len(r) > 0)]
        if not non_empty:
            continue
        df_batch = pd.concat(non_empty, ignore_index=True)
        # Check for weight column
        if "weight" in df_batch.columns:
            first_batch_has_weight = True
        break  # Only need to check the first non-empty batch

    # Build schema accordingly
    if first_batch_has_weight:
        schema = pa.schema([
            ("source_id", pa.int64()),
            ("healpix_id", pa.uint64()),
            ("weight", pa.float64()),
        ])
    else:
        schema = pa.schema([
            ("source_id", pa.int64()),
            ("healpix_id", pa.uint64()),
        ])
    schema = schema.with_metadata({k: v.encode() for k, v in pq_meta.items()})

    writer = None
    total_rows_written = 0
    total_batches_processed = 0

    # Per-nside progress bar that advances as Dask computes partition batches.
    pbar = tqdm(total=nparts, desc=f"nside={nside}", unit="part", position=0, leave=True)
    try:
        for i in range(0, nparts, batch_size):
            batch = tasks[i : i + batch_size]
            batch_start = i + 1
            batch_end = min(i + batch_size, nparts)
            pbar.set_description(f"nside={nside} [{batch_start}-{batch_end}/{nparts}]")

            # compute this batch with progress tracking if available
            if DASK_PROGRESS_AVAILABLE and logger.level <= logging.INFO:
                with DaskProgressBar():
                    res = dask.compute(*batch)
            else:
                res = dask.compute(*batch)
            # res is a tuple of pandas DataFrames
            non_empty = [r for r in res if (r is not None and len(r) > 0)]
            if not non_empty:
                pbar.update(len(batch))
                continue
            df_batch = pd.concat(non_empty, ignore_index=True)
            # ensure dtypes
            df_batch = df_batch.astype({"source_id": "int64", "healpix_id": "UInt64"})
            # If schema has weight but batch does not, add column
            if first_batch_has_weight and "weight" not in df_batch.columns:
                df_batch["weight"] = np.nan
            # create arrow table with enforced schema/metadata
            table = pa.Table.from_pandas(df_batch, schema=schema, preserve_index=False)
            batch_rows = len(df_batch)
            total_rows_written += batch_rows
            total_batches_processed += 1

            if writer is None:
                # overwrite existing single file if present
                if out_file.exists():
                    try:
                        out_file.unlink()
                    except Exception:
                        logger.warning(f"Could not remove existing output file {out_file}")
                writer = pq.ParquetWriter(str(out_file), schema)
            writer.write_table(table)

            # update progress with statistics
            pbar.set_postfix({
                'rows': total_rows_written,
                'batch_rows': batch_rows
            })
            pbar.update(len(batch))
    finally:
        pbar.close()

    # close the writer if we created one
    if writer is not None:
        writer.close()
        logger.info(f"Wrote single parquet file: {out_file} ({total_rows_written} rows, {total_batches_processed} batches)")
    else:
        # if nothing was written, write empty file with schema
        logger.info(f"Wrote empty output {out_file}")
        empty_table = pa.Table.from_pandas(
            pd.DataFrame(columns=["source_id", "healpix_id"]).astype({"source_id": "int64", "healpix_id": "UInt64"}),
            schema=schema, preserve_index=False
        )
        pq.write_table(empty_table, str(out_file))

    return total_rows_written

def _convert_wkb_columns_to_geometry(ddf):
    """Convert WKB binary geometry columns in a plain dask DataFrame to shapely objects.

    ADR-018: when dask_geopandas.read_parquet fails (e.g. broken spatial partition
    metadata from duckdb), plain dask reads geometry columns as raw WKB bytes.
    This function detects such columns by dtype and name heuristic, then uses
    map_partitions + shapely.from_wkb to convert them so downstream geometry-based
    workflows still work.
    """
    binary_cols = [col for col in ddf.columns
                   if ddf[col].dtype == 'object'
                   and any(kw in col.lower() for kw in ('polygon', 'geometry', 'wkt', 'wkb', 'geom'))]
    if not binary_cols:
        return ddf
    geom_col = binary_cols[0]

    def _decode(part_df):
        geom_series = part_df[geom_col]
        if geom_series.empty or not isinstance(geom_series.iloc[0], bytes):
            return part_df
        part_df = part_df.copy()
        part_df[geom_col] = geom_series.apply(lambda b: from_wkb(b) if isinstance(b, bytes) else b)
        return part_df

    try:
        ddf = ddf.map_partitions(_decode, meta=ddf._meta)
    except Exception:
        pass
    return ddf

def _read_input_lazy(input_path: Path, ncores: int,
                     columns: list | None = None) -> "dask.dataframe.DataFrame":
    """Three-tier lazy parquet reader with graceful degradation.

    Tier 1: dask_geopandas.read_parquet — preserves spatial metadata if valid.
    Tier 2: plain dask.dataframe.read_parquet — ignores broken spatial metadata.
            WKB geometry columns are decoded to shapely objects (ADR-018).
    Tier 3: raise with clear diagnostic.

    Parameters
    ----------
    columns : list or None
        Subset of columns to read. The sidecar only needs the geometry column
        (geometry mode) or the lon/lat columns (scalar mode); reading every
        column can balloon memory on wide input files (e.g. spectrum/reflectance
        columns). Pass ``None`` to read everything.

    Performance note
    ----------------
    Sidecar only uses scalar lon/lat columns fed to ``healpy.ang2pix``.
    Spatial partitions provide **zero** benefit for full-table HEALPix cell
    assignment, so Tier 2 is functionally equivalent to Tier 1.  The WKB
    decode adds negligible overhead (microseconds per geometry) compared to
    the HEALPix assignment work.
    """
    import dask.dataframe as dd

    # Tier 1 — try dask-geopandas
    try:
        import dask_geopandas as dg

        ddf = dg.read_parquet(str(input_path), columns=columns)
        logger.info(
            "Read with dask_geopandas (%d partitions, spatial partitions OK)",
            ddf.npartitions,
        )
        return ddf
    except ValueError as exc:
        if "spatial partitions" in str(exc).lower():
            path_name = input_path.name if input_path is not None else "(unknown)"
            logger.warning(
                "Spatial partition metadata mismatch in '%s' — "
                "falling back to plain dask.dataframe (geometry col will be decoded from WKB).  "
                "To fix permanently: healpyxel_inspect -i %s --correct-geometry output.parquet",
                path_name, path_name,
            )
        else:
            logger.warning(
                "dask_geopandas.read_parquet failed (%s) — falling back to plain dask",
                exc,
            )
    except ImportError:
        logger.info("dask_geopandas not installed, using plain dask.dataframe")
    except Exception as exc:
        logger.warning(
            "dask_geopandas.read_parquet failed unexpectedly (%s) — "
            "falling back to plain dask",
            exc,
        )

    # Tier 2 — plain dask (geometry col becomes WKB bytes; decode for workflow compat)
    try:
        ddf = dd.read_parquet(str(input_path), columns=columns)
        logger.info(
            "Read with plain dask.dataframe (%d partitions, %d columns)",
            ddf.npartitions,
            len(ddf.columns),
        )
        # ADR-014: When dask_geopandas falls back, WKB bytes need decoding
        # so geometry-based workflows (fuzzy mode) still work.
        ddf = _convert_wkb_columns_to_geometry(ddf)
        return ddf
    except Exception as exc:
        # Tier 3 — nothing works
        raise IOError(
            f"Cannot read '{input_path}' with either dask_geopandas or "
            f"dask.dataframe. Last error: {exc}"
        ) from exc

def _get_config(config, key, default=None):
    """Access config value from dict or argparse Namespace."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)

def run(config):
    """Run sidecar pipeline from a config dict or argparse Namespace.

    Replaces argparse-based main() so sidecar can be used as a pure library.

    When running on parquet files written with broken spatial partition metadata
    (e.g. by duckdb), the reader falls back to plain dask and automatically
    decodes WKB geometry columns (ADR-018).  If geometry mode was requested
    but no scalar lon/lat columns are available, coordinates are extracted
    from decoded geometries and passed to the scalar workflow.
    """
    input_val = _get_config(config, 'input')
    if input_val is None:
        raise RuntimeError(
            f"'input' argument is missing from sidecar config. "
            f"Config type: {type(config).__name__}. "
            f"Config keys/attrs: {list(vars(config).keys()) if hasattr(config, '__dict__') else list(config.keys()) if isinstance(config, dict) else 'unknown'}. "
            "Ensure '-i' / '--input' is passed to sidecar.parse_arguments()."
        )
    input_path = Path(input_val)
    if not input_path.exists():
        raise RuntimeError(f"Input path does not exist: {input_path}")

    if not _get_config(config, 'geo_stats'):
        if _get_config(config, 'nside') is None:
            raise RuntimeError("--nside is required when not using --geo-stats")
        if _get_config(config, 'lon_convention') is None:
            raise RuntimeError("--lon-convention is required when not using --geo-stats")

    nsides = _get_config(config, 'nside')
    if nsides is not None:
        for n in nsides:
            if not validate_nside(n):
                raise RuntimeError(f"nside must be a positive power of two: invalid value {n}")

    level_map = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
    }
    root_level = level_map.get(_get_config(config, 'loglevel', 'info'), logging.INFO)
    logging.getLogger().setLevel(root_level)
    logger.setLevel(root_level)

    logger.info(f"Input file: {input_path.name}")
    logger.info(f"Mode: {_get_config(config, 'mode')}, lon-convention: {_get_config(config, 'lon_convention')}, nside: {nsides}")

    # Resolve the assignment workflow and the exact subset of columns needed.
    # This reads only the parquet schema footer (no rows), so it never
    # materializes the full file in memory — the previous implementation read
    # the whole file to sample, which OOM-killed the process on wide files.
    schema_names = _parquet_column_names(input_path)
    geometry_col = _find_geometry_column(schema_names)
    has_geometry = geometry_col is not None

    lon_col = _get_config(config, 'lon_col')
    lat_col = _get_config(config, 'lat_col')
    use_geometry = _get_config(config, 'geometry', False)
    mode_val = _get_config(config, 'mode', 'fuzzy')

    if use_geometry:
        if geometry_col is None:
            raise RuntimeError(
                "--geometry requested but no geometry column found in parquet schema. "
                f"Available columns: {schema_names}"
            )
        lon_col = None
        lat_col = None
        logger.info(
            f"Geometry mode forced via --geometry: using column '{geometry_col}' "
            f"(mode={mode_val})"
        )
    elif lon_col is None and lat_col is None and has_geometry:
        # Existing default: when no lon/lat are given and a geometry column exists,
        # use geometry-based assignment (works for strict and fuzzy, Point/Polygon).
        lon_col = None
        lat_col = None
        logger.info(
            f"Geometry column '{geometry_col}' detected — using geometry-based mode "
            f"(mode={mode_val}, ignoring scalar lon/lat columns)"
        )
    elif lon_col is None or lat_col is None:
        # Auto-detect lon/lat from schema column names (no data read).
        logger.info(
            f"Auto-detecting lon/lat columns (user provided: "
            f"lon_col={_get_config(config, 'lon_col')}, lat_col={_get_config(config, 'lat_col')})"
        )
        detected_lon, detected_lat = detect_lonlat_columns(pd.DataFrame(columns=schema_names))
        lon_col = lon_col or detected_lon
        lat_col = lat_col or detected_lat
        if isinstance(config, dict):
            config['lon_col'] = lon_col
            config['lat_col'] = lat_col
        else:
            config.lon_col = lon_col
            config.lat_col = lat_col
        if detected_lon or detected_lat:
            logger.info(f"✓ Auto-detected: lon_col='{lon_col}', lat_col='{lat_col}'")
        elif has_geometry:
            # No scalar lon/lat found but a geometry column exists — fall back to it.
            lon_col = None
            lat_col = None
            logger.warning(
                f"Could not auto-detect lon/lat columns from schema. "
                f"Falling back to geometry column '{geometry_col}'."
            )
        else:
            logger.warning(
                f"Could not auto-detect lon/lat columns. Checked: {schema_names}"
            )
    else:
        logger.info(f"✓ Using user-provided lon/lat: lon_col='{_get_config(config, 'lon_col')}', lat_col='{_get_config(config, 'lat_col')}'")

    # Only materialize the columns the chosen workflow actually needs. The sidecar
    # never uses spectrum/reflectance/string columns, so loading them is pure waste.
    if lon_col is None and lat_col is None:
        if geometry_col is None:
            raise RuntimeError(
                "No lon/lat columns and no geometry column available to assign cells."
            )
        read_columns = [geometry_col]
    else:
        read_columns = [c for c in (lon_col, lat_col) if c is not None]
    logger.info(f"Reading only sidecar-relevant columns: {read_columns}")

    # Keep config in sync with the resolved columns for downstream ADR-018 logic.
    if isinstance(config, dict):
        config['lon_col'] = lon_col
        config['lat_col'] = lat_col
    else:
        config.lon_col = lon_col
        config.lat_col = lat_col

    if _get_config(config, 'geo_stats'):
        lon_conv = _get_config(config, 'lon_convention')
        if lon_conv:
            logger.info(f"Computing geographical statistics with filtering (--lon-convention {lon_conv})...")
        else:
            logger.info("Computing geographical statistics (raw data, no filtering)...")
        try:
            stats = compute_geo_statistics(
                input_path,
                lon_col=_get_config(config, 'lon_col'),
                lat_col=_get_config(config, 'lat_col'),
                sample_size=_get_config(config, 'stats_sample_size', 10000),
                lon_convention=lon_conv
            )
            if stats:
                formatted_stats = format_geo_statistics(stats)
                print("\n" + formatted_stats + "\n")
                stats_output = input_path.with_suffix('.geo_stats.json')
                try:
                    import json
                    with open(stats_output, 'w') as f:
                        json.dump(stats, f, indent=2)
                    logger.info(f"Saved geo-statistics to {stats_output}")
                except Exception as e:
                    logger.debug(f"Could not save statistics to JSON: {e}")
                logger.info("Geo-statistics complete. Exiting (use without --geo-stats to process data).")
                return 0
            else:
                logger.error("Could not compute geo-statistics")
                return 1
        except Exception as e:
            logger.error(f"Geo-statistics computation failed: {e}")
            if logger.level <= logging.DEBUG:
                import traceback
                traceback.print_exc()
            return 1

    logger.info(f"Reading input lazily from {input_path}; ncores={_get_config(config, 'ncores')}; mode={_get_config(config, 'mode')}; nsides={nsides}")
    logger.info(f"Longitude convention: {_get_config(config, 'lon_convention')}")

    body = _get_body(config)
    logger.info(f"Body geometry model: {body.name()}")

    ddf = _read_input_lazy(input_path, _get_config(config, 'ncores'), columns=read_columns)

    # ADR-018: If geometry mode was selected but dask_geopandas fell back,
    # extract scalar lon/lat from decoded geometry column so the efficient
    # scalar workflow is used instead of the unavailable .geometry attribute.
    lon_col = _get_config(config, 'lon_col')
    lat_col = _get_config(config, 'lat_col')
    if lon_col is None and lat_col is None and not hasattr(ddf, 'geometry'):
        for col in ddf.columns:
            if col.lower().endswith(('geom', 'geometry')) or 'geom' in col.lower():
                try:
                    from shapely import get_coordinates

                    def _add_lonlat(part_df):
                        part_df = part_df.copy()
                        geom_series = part_df[col]
                        if geom_series.empty or not hasattr(geom_series.iloc[0], 'geom_type'):
                            return part_df
                        coords = get_coordinates(geom_series)
                        part_df['_lon'] = coords[:, 0]
                        part_df['_lat'] = coords[:, 1]
                        return part_df

                    ddf = ddf.map_partitions(
                        _add_lonlat,
                        meta=ddf._meta.assign(**{'_lon': 'float64', '_lat': 'float64'})
                    )
                    if isinstance(config, dict):
                        config['lon_col'] = '_lon'
                        config['lat_col'] = '_lat'
                    else:
                        config.lon_col = '_lon'
                        config.lat_col = '_lat'
                    logger.info(f"Extracted scalar lon/lat from geometry column '{col}' "
                                f"(lon_col='_lon', lat_col='_lat')")
                    break
                except Exception:
                    continue

    meta = pd.DataFrame({"source_id": pd.Series(dtype="int64"), "healpix_id": pd.Series(dtype="UInt64")})
    logger.info("Starting partitioned HEALPix assignment (this may take time)")

    no_multi_res_optimize = _get_config(config, 'no_multi_res_optimize', False)
    mode_val = _get_config(config, 'mode', 'fuzzy')
    lon_conv = _get_config(config, 'lon_convention')
    ncores = _get_config(config, 'ncores')

    if no_multi_res_optimize or len(nsides) <= 1:
        for nside in tqdm(nsides, desc="nsides", unit="nside"):
            logger.info("Processing nside=%s", nside)
            import dask
            delayed_partitions = ddf.to_delayed()
            try:
                part_lengths = ddf.map_partitions(lambda df: len(df)).compute().tolist()
            except Exception:
                part_lengths = [int(dask.compute(dask.delayed(lambda df: len(df))(p))[0]) for p in delayed_partitions]

            if len(part_lengths) != len(delayed_partitions):
                logger.warning("Partition count mismatch; falling back to equal offsets")
                part_lengths = [len(delayed_partitions)] * len(delayed_partitions)

            offsets = np.concatenate(([0], np.cumsum(part_lengths)[:-1]))

            data_psf = None
            cell_psf = None
            if _get_config(config, 'cell_psf', 'none') != 'none':
                logger.info(f"Using cell PSF: {_get_config(config, 'cell_psf')} (sigma_level={_get_config(config, 'cell_psf_sigma_level')})")
                cell_psf = get_psf(_get_config(config, 'cell_psf'), sigma=_get_config(config, 'cell_psf_sigma_level'))
            if _get_config(config, 'data_psf', 'none') != 'none':
                logger.info(f"Using data PSF: {_get_config(config, 'data_psf')} (sigma_level={_get_config(config, 'data_psf_sigma_level')})")
                data_psf = get_psf(_get_config(config, 'data_psf'), sigma=_get_config(config, 'data_psf_sigma_level'))

            lon_col = _get_config(config, 'lon_col')
            lat_col = _get_config(config, 'lat_col')
            tasks = [dask.delayed(process_partition)(
                        part, nside, mode_val,
                        int(offsets[i]),
                        lon_conv,
                        lon_col=lon_col,
                        lat_col=lat_col,
                        data_psf=data_psf,
                        cell_psf=cell_psf,
                        combine_method=_get_config(config, 'psf_combine', 'multiply'),
                        body=body,
                        )
                        for i, part in enumerate(delayed_partitions)
                    ]
            nparts = len(tasks)

            if _get_config(config, 'output_dir'):
                out_dir = Path(_get_config(config, 'output_dir'))
                out_dir.mkdir(parents=True, exist_ok=True)
            else:
                out_dir = input_path.parent

            out_file = out_dir / build_output_path(input_path, mode_val, nside).name

            if _get_config(config, 'no_coalesce'):
                total_rows = write_partitioned_output(tasks, out_file, nparts)
                metadata_path = write_sidecar_metadata(
                    out_file, input_path, nside, mode_val,
                    lon_conv, ncores, config
                )
                logger.info(f"Wrote metadata to {metadata_path}")
                continue

            try:
                import dask
                import pyarrow as pa
                import pyarrow.parquet as pq
            except Exception as e:
                logger.error("pyarrow and dask are required for coalescing partitions to a single file: %s", e)
                logger.info("Falling back to writing partitioned parquet folder instead")
                out_part_dir = str(out_file.with_suffix('.parts'))
                continue

            total_rows = write_coalesced_output(tasks, out_file, nside, mode_val, ncores, nparts)
            metadata_path = write_sidecar_metadata(
                out_file, input_path, nside, mode_val,
                lon_conv, ncores, config
            )
            logger.info(f"Wrote metadata to {metadata_path}")

    else:
        nsides_sorted = sorted(nsides, reverse=True)
        nside_max = nsides_sorted[0]
        nsides_lower = nsides_sorted[1:]
        logger.info(f"Multi-resolution optimization active: computing nside={nside_max}, "
                     f"aggregating {len(nsides_lower)} lower nsides via bit-shift")

        import dask
        delayed_partitions = ddf.to_delayed()
        try:
            part_lengths = ddf.map_partitions(lambda df: len(df)).compute().tolist()
        except Exception:
            part_lengths = [int(dask.compute(dask.delayed(lambda df: len(df))(p))[0]) for p in delayed_partitions]

        if len(part_lengths) != len(delayed_partitions):
            logger.warning("Partition count mismatch; falling back to equal offsets")
            part_lengths = [len(delayed_partitions)] * len(delayed_partitions)

        offsets = np.concatenate(([0], np.cumsum(part_lengths)[:-1]))

        data_psf = None
        cell_psf = None
        if _get_config(config, 'cell_psf', 'none') != 'none':
            logger.info(f"Using cell PSF: {_get_config(config, 'cell_psf')} (sigma_level={_get_config(config, 'cell_psf_sigma_level')})")
            cell_psf = get_psf(_get_config(config, 'cell_psf'), sigma=_get_config(config, 'cell_psf_sigma_level'))
        if _get_config(config, 'data_psf', 'none') != 'none':
            logger.info(f"Using data PSF: {_get_config(config, 'data_psf')} (sigma_level={_get_config(config, 'data_psf_sigma_level')})")
            data_psf = get_psf(_get_config(config, 'data_psf'), sigma=_get_config(config, 'data_psf_sigma_level'))

        lon_col = _get_config(config, 'lon_col')
        lat_col = _get_config(config, 'lat_col')
        tasks_max = [dask.delayed(process_partition)(
                        part, nside_max, mode_val,
                        int(offsets[i]),
                        lon_conv,
                        lon_col=lon_col,
                        lat_col=lat_col,
                        data_psf=data_psf,
                        cell_psf=cell_psf,
                        combine_method=_get_config(config, 'psf_combine', 'multiply'),
                        body=body,
                        )
                        for i, part in enumerate(delayed_partitions)
                    ]

        if _get_config(config, 'output_dir'):
            out_dir = Path(_get_config(config, 'output_dir'))
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = input_path.parent

        out_file_max = out_dir / build_output_path(input_path, mode_val, nside_max).name

        try:
            import dask
            import pyarrow as pa
            import pyarrow.parquet as pq
        except Exception as e:
            logger.error("pyarrow and dask are required for coalescing partitions to a single file: %s", e)
            logger.info("Falling back to writing partitioned parquet folder instead")
            out_part_dir = str(out_file_max.with_suffix('.parts'))
            write_partitioned_output(tasks_max, out_file_max, len(tasks_max))
            metadata_path = write_sidecar_metadata(
                out_file_max, input_path, nside_max, mode_val,
                lon_conv, ncores, config
            )
            logger.info(f"Wrote metadata to {metadata_path}")
            # Still aggregate lower nsides if part_dir provides usable data
            # Skip for now; partitioned output format cannot be easily aggregated here
            return 0

        total_rows_max = write_coalesced_output(tasks_max, out_file_max, nside_max, mode_val, ncores, len(tasks_max))
        metadata_path = write_sidecar_metadata(
            out_file_max, input_path, nside_max, mode_val,
            lon_conv, ncores, config
        )
        logger.info(f"Wrote metadata for nside={nside_max} to {metadata_path}")

        no_psf_normalize = _get_config(config, 'no_psf_normalize', False)
        df_max = pd.read_parquet(out_file_max)
        has_weight = 'weight' in df_max.columns

        for nside_i in nsides_lower:
            logger.info(f"Aggregating nside={nside_max} -> nside={nside_i} "
                        f"(shift={2 * int(np.log2(nside_max // nside_i))})")
            df_agg = _aggregate_healpix(df_max, nside_max, nside_i,
                                        no_psf_normalize=no_psf_normalize)
            out_file_i = out_dir / build_output_path(input_path, mode_val, nside_i).name
            _write_single_parquet(df_agg, out_file_i, nside_i, mode_val, has_weight=has_weight)
            metadata_path_i = write_sidecar_metadata(
                out_file_i, input_path, nside_i, mode_val,
                lon_conv, ncores, config,
                derived_from_parent=nside_max
            )
            logger.info(f"Wrote aggregated nside={nside_i} ({len(df_agg)} rows) to {metadata_path_i}")

    return 0

def main(argv=None):
    """CLI entry point for healpyxel_sidecar."""
    args = parse_arguments(argv)
    return run(args)

# CLI entry point (use via command line or import main() function)

def get_healpix_cell_geometry(healpix_id, nside, nest=True, lon_convention='0_360'):
    """
    Return a shapely Polygon for the given HEALPix cell.
    Uses healpy boundaries (Cartesian x,y,z on unit sphere) and converts to lon/lat.
    """
    import healpy as hp
    from shapely.geometry import Polygon

    # Get the boundary vertices of the cell (returns Cartesian x,y,z, shape (3, N))
    vertices = hp.boundaries(nside, healpix_id, step=1, nest=nest)
    x = vertices[0]
    y = vertices[1]
    z = vertices[2]

    # Convert Cartesian to spherical (theta, phi) then to lon/lat
    theta = np.arccos(np.clip(z, -1.0, 1.0))  # colatitude in radians
    phi = np.arctan2(y, x)                      # longitude in radians
    lats = 90.0 - np.degrees(theta)
    lons = np.degrees(phi)

    # Adjust lon convention
    if lon_convention in ('-180_180', 'minus_plus180'):
        lons = np.where(lons > 180, lons - 360, lons)
    elif lon_convention == '0_360':
        lons = np.mod(lons, 360.0)

    # Build polygon (healpy returns vertices in order)
    coords = list(zip(lons, lats))
    return Polygon(coords)
