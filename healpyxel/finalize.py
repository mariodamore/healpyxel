"""Finalize HEALPix maps from accumulator state."""

from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import logging
import argparse
import json
from datetime import datetime, timezone
import sys

import numpy as np
import pandas as pd

from .metadata import HEALPyxelxMetadata, FileType

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Import from accumulator
from healpyxel.accumulator import load_state, CellAccumulator, TDIGEST_AVAILABLE

try:
    import healpy as hp
    HEALPY_AVAILABLE = True
except ImportError:
    HEALPY_AVAILABLE = False
    hp = None

try:
    from tqdm.auto import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    tqdm = None

def finalize_statistics(
    state: Dict[int, CellAccumulator],
    percentiles: Optional[List[float]] = None,
    min_count: int = 1,
) -> pd.DataFrame:
    """
    Convert accumulator state to final statistics DataFrame.

    Args:
        state: Accumulator state dictionary {healpix_id: CellAccumulator}
        percentiles: List of percentiles to compute (e.g., [25, 50, 75])
        min_count: Minimum observations required per cell (cells below this are NaN)

    Returns:
        DataFrame indexed by healpix_id with statistics columns:
        - {col}_n: observation count
        - {col}_mean: mean value
        - {col}_std: standard deviation
        - {col}_min: minimum value
        - {col}_max: maximum value
        - {col}_p{N}: percentile (if T-Digest available)
    """
    if percentiles is None:
        percentiles = []

    logger.info(f"Finalizing statistics for {len(state)} cells")
    logger.info(f"Minimum observation count: {min_count}")

    rows = []
    iterator = state.items() if not TQDM_AVAILABLE else tqdm(state.items(), desc="Computing statistics")

    for hp_id, acc in iterator:
        row = {'healpix_id': int(hp_id)}

        # Get all columns processed
        columns = list(acc.stats_by_column.keys())

        for col in columns:
            stats = acc.stats_by_column[col]

            # Always include count
            row[f'{col}_n'] = int(stats.n)

            # Skip cells with insufficient data
            if stats.n < min_count:
                row[f'{col}_mean'] = float('nan')
                row[f'{col}_std'] = float('nan')
                row[f'{col}_min'] = float('nan')
                row[f'{col}_max'] = float('nan')

                for p in percentiles:
                    row[f'{col}_p{int(p)}'] = float('nan')
                continue

            # Basic statistics
            row[f'{col}_mean'] = stats.mean
            row[f'{col}_std'] = stats.std
            row[f'{col}_min'] = stats.min_val if np.isfinite(stats.min_val) else float('nan')
            row[f'{col}_max'] = stats.max_val if np.isfinite(stats.max_val) else float('nan')

            # Percentiles from T-Digest
            if hasattr(acc, 'tdigests') and col in acc.tdigests:
                digest = acc.tdigests[col]
                for p in percentiles:
                    try:
                        value = digest.percentile(p)
                        row[f'{col}_p{int(p)}'] = float(value)
                    except Exception as e:
                        logger.warning(f"Failed to compute p{p} for {col} in cell {hp_id}: {e}")
                        row[f'{col}_p{int(p)}'] = float('nan')
            else:
                # No T-Digest available, set to NaN
                for p in percentiles:
                    row[f'{col}_p{int(p)}'] = float('nan')

        rows.append(row)

    df = pd.DataFrame(rows).set_index('healpix_id').sort_index()

    logger.info(f"✓ Finalized {len(df)} cells")
    logger.info(f"  Columns: {list(df.columns)}")

    # Report coverage statistics
    for col in columns:
        if f'{col}_n' in df.columns:
            valid_cells = (df[f'{col}_n'] >= min_count).sum()
            total_obs = df[f'{col}_n'].sum()
            logger.info(f"  {col}: {valid_cells} valid cells, {int(total_obs):,} total observations")

    return df

def densify_healpix_map(
    sparse_df: pd.DataFrame,
    nside: int,
    fill_value: float = np.nan
) -> pd.DataFrame:
    """
    Create a complete HEALPix grid by filling empty cells with fill_value.

    Args:
        sparse_df: DataFrame with healpix_id index (sparse)
        nside: HEALPix nside parameter
        fill_value: Value for empty cells (default: NaN)

    Returns:
        Dense DataFrame with all 12*nside**2 cells
    """
    if not HEALPY_AVAILABLE:
        logger.error("healpy required for densification (pip install healpy)")
        raise ImportError("healpy not available")

    n_pixels = hp.nside2npix(nside)
    logger.info(f"Densifying to full grid (nside={nside}, {n_pixels} cells)")

    # Create full index
    full_index = pd.Index(range(n_pixels), name='healpix_id')

    # Reindex with fill_value
    dense_df = sparse_df.reindex(full_index, fill_value=fill_value)

    logger.info(f"✓ Densified: {len(sparse_df)} → {len(dense_df)} cells")

    return dense_df

def export_to_geotiff(
    df: pd.DataFrame,
    column: str,
    output_path: Path,
    nside: int,
    crs: str = 'IAU:19900',  # Mercury IAU CRS
):
    """
    Export a column to GeoTIFF format (requires rasterio and healpy).

    Note: This creates an equirectangular projection from HEALPix data.
    """
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError:
        logger.error("rasterio required for GeoTIFF export (pip install rasterio)")
        raise

    if not HEALPY_AVAILABLE:
        logger.error("healpy required for GeoTIFF export (pip install healpy)")
        raise ImportError("healpy not available")

    logger.info(f"Exporting {column} to GeoTIFF: {output_path}")

    # Convert to healpy array
    n_pixels = hp.nside2npix(nside)
    healpix_map = np.full(n_pixels, np.nan, dtype=np.float32)

    for hp_id, value in df[column].items():
        if 0 <= hp_id < n_pixels:
            healpix_map[hp_id] = value

    # Convert HEALPix to equirectangular grid
    width, height = 1440, 720  # 0.25 deg resolution
    lon = np.linspace(-180, 180, width)
    lat = np.linspace(90, -90, height)
    lon_grid, lat_grid = np.meshgrid(lon, lat)

    # HEALPix uses colatitude (0 at north pole)
    theta = np.radians(90 - lat_grid)
    phi = np.radians(lon_grid)

    # Query HEALPix values
    pixels = hp.ang2pix(nside, theta, phi, nest=True)
    grid = healpix_map[pixels]

    # Write GeoTIFF
    transform = from_bounds(-180, -90, 180, 90, width, height)

    with rasterio.open(
        output_path,
        'w',
        driver='GTiff',
        height=height,
        width=width,
        count=1,
        dtype=grid.dtype,
        crs=crs,
        transform=transform,
        compress='deflate',
        nodata=np.nan,
    ) as dst:
        dst.write(grid, 1)

    logger.info(f"✓ Exported GeoTIFF ({width}x{height})")

def _normalize_load_state_result(result) -> Tuple[Dict[int, CellAccumulator], Optional[HEALPyxelxMetadata]]:
    """Normalize load_state outputs across versions."""
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return result, None

def _in_ipython_kernel() -> bool:
    """Return True when running inside an IPython kernel (notebook)."""
    try:
        from IPython import get_ipython
        ip = get_ipython()
        return ip is not None and 'IPKernelApp' in ip.config
    except Exception:
        return False

def parse_arguments(argv=None):
    """Parse command-line arguments for finalize."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Finalize accumulator state to statistical HEALPix maps",
    )
    parser.add_argument('-s', '--state', required=True, type=Path)
    parser.add_argument('-o', '--output', required=True, type=Path)
    parser.add_argument('-p', '--percentiles', type=float, nargs='+', default=[25, 50, 75])
    parser.add_argument('--min-count', type=int, default=1)
    parser.add_argument('--densify', action='store_true')
    parser.add_argument('--nside', type=int)
    parser.add_argument('--export-tiff', nargs=2, metavar=('COLUMN', 'OUTPUT'))
    parser.add_argument('--crs', default='IAU:19900')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-q', '--quiet', action='store_true')
    return parser.parse_args(argv)

def _get_config(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)

def run(config):
    """Run finalize pipeline from a config dict or argparse Namespace."""
    args = config
    if isinstance(config, dict):
        import argparse
        args = argparse.Namespace(**{k: v for k, v in config.items() if v is not None})

    if _get_config(config, 'verbose'):
        logging.getLogger().setLevel(logging.DEBUG)
    elif _get_config(config, 'quiet'):
        logging.getLogger().setLevel(logging.WARNING)

    state_path = _get_config(config, 'state')
    if not state_path.exists():
        raise RuntimeError(f"State file not found: {state_path}")

    try:
        meta_in = HEALPyxelxMetadata.from_parquet(state_path)
    except ValueError as e:
        raise RuntimeError(f"State file missing HEALPix metadata: {e}")

    meta_out = HEALPyxelxMetadata(
        nside=meta_in.nside, order=meta_in.order, npix=meta_in.npix,
        mode=meta_in.mode, lon_convention=meta_in.lon_convention,
        file_type=FileType.FINALIZE,
    )

    densify = _get_config(config, 'densify')
    export_tiff = _get_config(config, 'export_tiff')
    if densify or export_tiff:
        nside = _get_config(config, 'nside')
        if not nside:
            nside = meta_in.nside
            logger.info(f"Auto-detected nside={nside} from metadata")

    if load_state is None:
        raise RuntimeError("Could not import load_state from accumulator module")

    logger.info(f"Loading state from {state_path}")
    try:
        state_result = load_state(state_path, use_tdigest=True)
        state, _ = _normalize_load_state_result(state_result)
    except Exception as e:
        raise RuntimeError(f"Failed to load state: {e}")

    has_tdigest = any(
        hasattr(acc, 'tdigests') and len(acc.tdigest) > 0
        for acc in state.values()
    )

    percentiles = _get_config(config, 'percentiles', [25, 50, 75])
    if percentiles and not has_tdigest:
        logger.warning("T-Digest data not found; percentiles will be NaN")

    logger.info("Computing statistics...")
    df = finalize_statistics(
        state=state,
        percentiles=percentiles if has_tdigest else [],
        min_count=_get_config(config, 'min_count', 1),
    )

    if densify:
        if not HEALPY_AVAILABLE:
            raise RuntimeError("healpy required for densification")
        df = densify_healpix_map(df, nside=nside)

    output = _get_config(config, 'output')
    logger.info(f"Saving to {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, engine='pyarrow', compression='snappy',
                  schema_metadata=meta_out.to_parquet_metadata())

    full_metadata = {
        'processing': {
            'stage': 'finalize',
            'timestamp': datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            'source_state': str(state_path),
            'output_file': str(output),
            'n_cells': len(df),
            'percentiles': percentiles if has_tdigest else [],
            'min_count': _get_config(config, 'min_count', 1),
            'densified': densify,
            'export_tiff': export_tiff,
        },
        'healpix': {
            'nside': meta_out.nside, 'mode': meta_out.mode,
            'order': meta_out.order, 'npix': meta_out.npix,
        },
        'coordinates': {
            'lon_convention': meta_out.lon_convention,
            'lon_range': [0, 360] if meta_out.lon_convention == '0_360' else [-180, 180],
            'lat_range': [-90, 90],
        },
    }
    HEALPyxelxMetadata.write_json(full_metadata, output, validate=True)
    logger.info(f"Saved metadata to {output.with_suffix('.meta.json')}")

    if export_tiff:
        column, tiff_path = export_tiff
        if column not in df.columns:
            raise RuntimeError(f"Column {column} not found")
        try:
            export_to_geotiff(df=df, column=column, output_path=Path(tiff_path),
                              nside=nside, crs=_get_config(config, 'crs', 'IAU:19900'))
        except Exception as e:
            raise RuntimeError(f"GeoTIFF export failed: {e}")

    logger.info(f"Finalization complete!  Output: {output}  Cells: {len(df)}")
    return 0

def main(argv=None):
    """CLI entry point for healpyxel_finalize."""
    args = parse_arguments(argv)
    return run(args)

if __name__ == '__main__':
    sys.exit(main())
    if _in_ipython_kernel():
        logger.info("Notebook context detected; skipping CLI entrypoint.")
    else:
        sys.exit(main())
