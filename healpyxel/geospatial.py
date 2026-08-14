from typing import Iterable, Optional, Tuple, List, Union
from pathlib import Path
import logging
import math
import numpy as np
import pandas as pd
import healpy as hp
from shapely.geometry import Polygon, MultiPolygon, mapping
from shapely import wkb
import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import json
import antimeridian
import warnings
from tqdm.auto import tqdm

import os
import configparser

from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

def is_geometry_valid(geom: BaseGeometry) -> bool:
    """
    Check if a geometry is valid for use in a spherical projection.

    This function ensures that the geometry is either associated with a CRS in degrees
    or has latitude/longitude coordinates within valid bounds. If no CRS is provided,
    it checks that the latitude is within [-90, 90] and longitude is within [-180, 360].

    Args:
        geom (BaseGeometry): A Shapely geometry object to validate.

    Returns:
        bool: True if the geometry is valid for spherical projection, False otherwise.

    Example:
        >>> from shapely.geometry import Polygon
        >>> geom = Polygon([(-180, -90), (-180, 90), (180, 90), (180, -90), (-180, -90)])
        >>> is_geometry_valid(geom)  # Valid lat/lon bounds
        True
        >>> geom = Polygon([(1e32, 1e32), (180, 90), (180, -90), (1e32, 1e32)])
        >>> is_geometry_valid(geom)  # Invalid due to extreme coordinates
        False
    """

    # Check bounds of the geometry
    if geom.is_empty:
        return False
    bounds = geom.bounds  # (minx, miny, maxx, maxy)
    lon_min, lat_min, lon_max, lat_max = bounds
    return (
        -180 <= lon_min <= 360 and
        -180 <= lon_max <= 360 and
        -90 <= lat_min <= 90 and
        -90 <= lat_max <= 90
    )

def _resolve_xdg_dir(xdg_env: str, xdg_default: str, fallback_subdir: str) -> Path:
    """Resolve XDG Base Directory with fallback to home.

    Implements XDG Base Directory Specification (https://standards.freedesktop.org/basedir-spec/basedir-spec-latest.html).

    Args:
        xdg_env: Name of XDG environment variable (e.g., 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME')
        xdg_default: Default path if env var not set (e.g., '~/.cache', '~/.config')
        fallback_subdir: Subdirectory within XDG dir (e.g., 'healpyxel/healpix_grids')

    Returns:
        Resolved Path with environment variable expanded, fallback applied, directory created.

    Example:
        >>> cache_dir = _resolve_xdg_dir('XDG_CACHE_HOME', '~/.cache', 'healpyxel/healpix_grids')
        PosixPath('/home/user/.cache/healpyxel/healpix_grids')

        >>> # With env var set
        >>> os.environ['XDG_CACHE_HOME'] = '/mnt/fast'
        >>> cache_dir = _resolve_xdg_dir('XDG_CACHE_HOME', '~/.cache', 'healpyxel/healpix_grids')
        PosixPath('/mnt/fast/healpyxel/healpix_grids')
    """
    if xdg_env in os.environ:
        xdg_base = Path(os.environ[xdg_env]).expanduser()
    else:
        xdg_base = Path(xdg_default).expanduser()

    resolved = xdg_base / fallback_subdir
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved

#| export
def _get_cache_dir(cli_arg: Optional[Union[str, Path]] = None,
                   env_override: Optional[str] = None) -> Path:
    """Resolve HEALPix cache directory with full precedence.

    Precedence (highest to lowest):
    1. cli_arg         — CLI --cache-dir argument (explicit user override)
    2. env_override    — HEALPYXEL_CACHE env var (session preference)
    3. XDG_CACHE_HOME  — $XDG_CACHE_HOME/healpyxel/healpix_grids (or ~/.cache/... if not set)
    4. XDG fallback    — $HOME/.cache/healpyxel/healpix_grids

    Args:
        cli_arg: Value from --cache-dir CLI argument (if provided)
        env_override: Value from HEALPYXEL_CACHE env var (overrides os.environ lookup)

    Returns:
        Resolved cache Path, directory guaranteed to exist.

    Example:
        >>> # Case 1: CLI override (highest)
        >>> _get_cache_dir(cli_arg='/tmp/cache')
        PosixPath('/tmp/cache')

        >>> # Case 2: Env var (no CLI arg)
        >>> os.environ['HEALPYXEL_CACHE'] = '/mnt/ssd/healpyxel'
        >>> _get_cache_dir()
        PosixPath('/mnt/ssd/healpyxel')

        >>> # Case 3: XDG spec (no CLI, no env var)
        >>> os.environ.pop('HEALPYXEL_CACHE', None)
        >>> os.environ['XDG_CACHE_HOME'] = '/custom/cache'
        >>> _get_cache_dir()
        PosixPath('/custom/cache/healpyxel/healpix_grids')

        >>> # Case 4: XDG fallback (nothing set)
        >>> os.environ.pop('XDG_CACHE_HOME', None)
        >>> _get_cache_dir()
        PosixPath('/home/user/.cache/healpyxel/healpix_grids')
    """
    # Precedence 1: CLI argument (explicit override)
    if cli_arg is not None:
        cache_dir = Path(cli_arg).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    # Precedence 2: HEALPYXEL_CACHE env var
    if env_override is None:
        env_override = os.environ.get('HEALPYXEL_CACHE')
    if env_override is not None:
        cache_dir = Path(env_override).expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    # Precedence 3 & 4: XDG spec with fallback to ~/.cache
    return _resolve_xdg_dir('XDG_CACHE_HOME', '~/.cache', 'healpyxel/healpix_grids')

#| export
def _get_config_dir(cli_arg: Optional[Union[str, Path]] = None,
                    env_override: Optional[str] = None) -> Path:
    """Resolve HEALPix config directory with full precedence.

    Follows same precedence pattern as _get_cache_dir but for config files.

    Precedence (highest to lowest):
    1. cli_arg         — CLI --config-dir argument
    2. env_override    — HEALPYXEL_CONFIG env var
    3. XDG_CONFIG_HOME — $XDG_CONFIG_HOME/healpyxel (or ~/.config/healpyxel if not set)
    4. XDG fallback    — $HOME/.config/healpyxel

    Args:
        cli_arg: Value from --config-dir CLI argument (if provided)
        env_override: Value from HEALPYXEL_CONFIG env var (overrides os.environ lookup)

    Returns:
        Resolved config Path, directory guaranteed to exist.
    """
    if cli_arg is not None:
        config_dir = Path(cli_arg).expanduser()
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    if env_override is None:
        env_override = os.environ.get('HEALPYXEL_CONFIG')
    if env_override is not None:
        config_dir = Path(env_override).expanduser()
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    return _resolve_xdg_dir('XDG_CONFIG_HOME', '~/.config', 'healpyxel')

def _cache_key(nside: int, order: str = 'nested') -> str:
    """Generate cache parquet filename for HEALPix grid.

    Args:
        nside: HEALPix nside
        order: 'nested' (NEST) or 'ring' (RING)

    Returns:
        Filename string, e.g., 'nside_256_nest_spherical.parquet'

    Example:
        >>> _cache_key(256, 'nested')
        'nside_256_nest_spherical.parquet'

        >>> _cache_key(512, 'ring')
        'nside_512_ring_spherical.parquet'
    """
    order_str = 'nest' if order == 'nested' else 'ring'
    return f'nside_{nside:03d}_{order_str}_spherical.parquet'

#| export
def _load_cached_boundaries(nside: int, order: str = 'nested',
                            cache_dir: Optional[Path] = None) -> Optional[pd.DataFrame]:
    """Load HEALPix boundaries from cache if available.

    Returns DataFrame with spherical coordinates:
      - Index: healpix_id (int64)
      - Columns: theta_0, theta_1, theta_2, theta_3 (polar angles, radians)
      -         phi_0, phi_1, phi_2, phi_3 (azimuth angles, radians)

    Args:
        nside: HEALPix nside
        order: 'nested' or 'ring'
        cache_dir: optional override; defaults to _get_cache_dir()

    Returns:
        DataFrame if cache hit, None if cache miss or read error (warning logged).

    Example:
        >>> df = _load_cached_boundaries(256)
        >>> df.shape
        (49152, 8)
    """
    cache_dir = _get_cache_dir() if cache_dir is None else cache_dir
    cache_file = cache_dir / _cache_key(nside, order)

    if not cache_file.exists():
        return None

    try:
        return pd.read_parquet(cache_file).set_index('healpix_id')
    except Exception as e:
        warnings.warn(f'Failed to load cache {cache_file}: {e}')
        return None

#| export
def _save_cached_boundaries(df: pd.DataFrame, nside: int, order: str = 'nested',
                            cache_dir: Optional[Path] = None) -> Path:
    """Save HEALPix boundaries to cache.

    Args:
        df: DataFrame with index 'healpix_id' and columns theta_0..3, phi_0..3
        nside: HEALPix nside
        order: 'nested' or 'ring'
        cache_dir: optional override; defaults to _get_cache_dir()

    Returns:
        Path to written cache file.

    Raises:
        ValueError if df doesn't have expected columns
    """
    cache_dir = _get_cache_dir() if cache_dir is None else cache_dir
    cache_file = cache_dir / _cache_key(nside, order)

    # Validate columns
    expected_cols = [f'theta_{i}' for i in range(4)] + [f'phi_{i}' for i in range(4)]
    if not all(col in df.columns or col in df.index.names for col in expected_cols):
        raise ValueError(f'DataFrame must have columns: {expected_cols}')

    # Reset index to make healpix_id a column for parquet
    df_copy = df.reset_index()
    df_copy.to_parquet(cache_file, compression='snappy', index=False)
    return cache_file

def _spherical_to_lonlat(theta_arr: np.ndarray, phi_arr: np.ndarray,
                         lon_convention: str = '0_360') -> Tuple[np.ndarray, np.ndarray]:
    """Convert spherical angles to geographic lon/lat.

    Converts from ICRS spherical coordinates (healpy native) to geographic lon/lat.
    Handles longitude convention conversion without polygon validity issues.

    Args:
        theta_arr: polar angles in radians, shape (...,)
        phi_arr: azimuth angles in radians, shape (...,) matching theta_arr
        lon_convention: '0_360' (default) or '-180_180'. Value 'minus_plus180' is also accepted and treated as '-180_180'.

    Returns:
        (lons, lats) both in degrees, shape matches input
        - lons: normalized to convention
        - lats: in [-90, 90] range (same for all conventions)

    Example:
        >>> theta = np.array([np.pi/4, 0])  # 45°, 0° from north pole
        >>> phi = np.array([0, np.pi])  # 0°, 180° azimuth
        >>> lons, lats = _spherical_to_lonlat(theta, phi)
        >>> lats  # Should be [45, 90]
    """
    lats = 90.0 - np.degrees(theta_arr)
    lons = np.degrees(phi_arr)

    if lon_convention == '-180_180':
        lons = ((lons + 180.0) % 360.0) - 180.0
    else:  # '0_360'
        lons = np.mod(lons, 360.0)

    return lons, lats

#| export
def _lonlat_to_polygons(lons: np.ndarray, lats: np.ndarray,
                        lon_convention: str = '0_360',
                        fix_antimeridian: bool = True) -> List[Union[Polygon, MultiPolygon]]:
    """Convert lon/lat corner arrays to Shapely Polygons.

    Args:
        lons: shape (n, 4) or (4,), longitudes in degrees
        lats: shape (n, 4) or (4,), latitudes in degrees
        lon_convention: '0_360' or '-180_180' (for reference; already applied to inputs)
        fix_antimeridian: whether to call antimeridian.fix_polygon on each polygon

    Returns:
        List of n Polygon objects (or single polygon if input shape is (4,))

    Example:
        >>> lons = np.array([[0, 10, 10, 0], [350, 360, 360, 350]])
        >>> lats = np.array([[0, 0, 10, 10], [-5, -5, 5, 5]])
        >>> polys = _lonlat_to_polygons(lons, lats, fix_antimeridian=True)
        >>> len(polys)
        2
    """
    lons = np.atleast_2d(lons)
    lats = np.atleast_2d(lats)

    return _polygons_from_lonlat(lons, lats, lon_convention, fix_antimeridian)


def _polygons_from_lonlat(lons: np.ndarray, lats: np.ndarray,
                          lon_convention: str = '0_360',
                          fix_antimeridian: bool = True) -> List[Union[Polygon, MultiPolygon]]:
    """Build one shapely Polygon (optionally antimeridian-fixed) per cell.

    This is the module-level worker used both sequentially and inside the
    thread pool. *lons* / *lats* must be 2-D arrays already normalized to the
    requested longitude convention (shape ``(n, 4)``).
    """
    polys = []
    for i in range(len(lons)):
        coords = list(zip(lons[i].tolist(), lats[i].tolist()))
        poly = Polygon(coords)

        if fix_antimeridian:
            try:
                poly = antimeridian.fix_polygon(poly)
                if poly.geom_type == 'MultiPolygon':
                    logger.debug(f"Antimeridian split cached cell into {len(poly.geoms)} parts "
                                 f"(bounds: {poly.bounds})")
            except Exception:
                pass  # Silently fall back to raw polygon

        polys.append(poly)

    return polys


def _lonlat_to_polygons_parallel(lons: np.ndarray, lats: np.ndarray,
                                 lon_convention: str = '0_360',
                                 fix_antimeridian: bool = True,
                                 chunk_size: int = 65536,
                                 ncores: int = 1) -> List[Union[Polygon, MultiPolygon]]:
    """Build polygons for a 2-D lon/lat array, parallelized across row blocks.

    Splits the rows into ``chunk_size`` blocks and maps each block through
    :func:`_polygons_from_lonlat` on a bounded thread pool. GEOS (via shapely)
    releases the GIL during geometry construction, so threads give real
    parallelism. Results are collected in original row order.

    Parameters
    ----------
    lons, lats : np.ndarray
        2-D arrays of shape ``(n, 4)``, already normalized to *lon_convention*.
    lon_convention : str
        Longitude convention (informational; inputs are expected normalized).
    fix_antimeridian : bool
        Whether to call ``antimeridian.fix_polygon`` per cell.
    chunk_size : int
        Number of cells per worker block.
    ncores : int
        Number of worker threads. ``1`` runs sequentially.

    Returns
    -------
    list[Polygon | MultiPolygon]
        One geometry per input row, in row order.
    """
    lons = np.atleast_2d(lons)
    lats = np.atleast_2d(lats)

    n = len(lons)
    starts = list(range(0, n, chunk_size))
    if len(starts) <= 1 or ncores <= 1:
        return _polygons_from_lonlat(lons, lats, lon_convention, fix_antimeridian)

    from concurrent.futures import ThreadPoolExecutor

    def _block(args):
        s = args
        e = min(s + chunk_size, n)
        return _polygons_from_lonlat(lons[s:e], lats[s:e], lon_convention, fix_antimeridian)

    with ThreadPoolExecutor(max_workers=ncores) as ex:
        blocks = list(ex.map(_block, starts))

    return [poly for block in blocks for poly in block]

def _load_user_settings(config_dir: Optional[Path] = None) -> dict:
    """Load user runtime settings from XDG config file.

    Location: $XDG_CONFIG_HOME/healpyxel/settings.ini (or ~/.config/healpyxel/settings.ini if not set)

    Returns dict with keys:
        cache_dir: Path or None (None means use _get_cache_dir() resolution)
        precomputed_nsides: list of int
        fix_antimeridian: bool
        antimeridian_tolerance: float (degrees, for near-meridian detection)

    Non-existent config file returns all defaults (silent failure).
    Parse errors log warnings but return parsed + defaults for unparsed keys.

    Example:
        >>> settings = _load_user_settings()
        >>> settings['precomputed_nsides']
        [32, 64, 128, 256]
    """
    if config_dir is None:
        config_dir = _get_config_dir()

    config_file = config_dir / 'settings.ini'

    # Default values
    defaults = {
        'cache_dir': None,  # None = use _get_cache_dir() resolution
        'precomputed_nsides': [32, 64, 128, 256],
        'fix_antimeridian': True,
        'antimeridian_tolerance': 1.0
    }

    if not config_file.exists():
        return defaults

    try:
        config = configparser.ConfigParser()
        config.read(config_file)

        # Parse [cache] section
        if config.has_section('cache'):
            if config.has_option('cache', 'cache_dir'):
                cache_path = config.get('cache', 'cache_dir').strip()
                if cache_path.lower() != 'auto':  # 'auto' means use default resolution
                    defaults['cache_dir'] = Path(cache_path).expanduser()

            if config.has_option('cache', 'precomputed_nsides'):
                nsides_str = config.get('cache', 'precomputed_nsides')
                try:
                    defaults['precomputed_nsides'] = [
                        int(x.strip()) for x in nsides_str.split(',') if x.strip()
                    ]
                except ValueError as e:
                    warnings.warn(f'Invalid precomputed_nsides in {config_file}: {e}')

        # Parse [general] section
        if config.has_section('general'):
            if config.has_option('general', 'fix_antimeridian'):
                defaults['fix_antimeridian'] = config.getboolean('general', 'fix_antimeridian')

            if config.has_option('general', 'antimeridian_tolerance'):
                try:
                    defaults['antimeridian_tolerance'] = config.getfloat('general', 'antimeridian_tolerance')
                except ValueError as e:
                    warnings.warn(f'Invalid antimeridian_tolerance in {config_file}: {e}')

    except Exception as e:
        warnings.warn(f'Failed to parse {config_file}: {e}; using defaults')

    return defaults

#| export
def init_user_config(config_dir: Optional[Path] = None) -> Path:
    """Create default ~/.config/healpyxel/settings.ini if it doesn't exist.

    Args:
        config_dir: optional override for config directory

    Returns:
        Path to config file (whether newly created or already existed)

    Example:
        >>> config_file = init_user_config()
        >>> config_file.exists()
        True
    """
    if config_dir is None:
        config_dir = _get_config_dir()

    config_file = config_dir / 'settings.ini'

    if config_file.exists():
        return config_file

    default_config = """# ~/.config/healpyxel/settings.ini
# HEALPix grid caching and geospatial configuration
# XDG Base Directory compliant: https://standards.freedesktop.org/basedir-spec/

[cache]
# Cache directory for HEALPix grids (parquet files with spherical coordinates)
# Special value 'auto' means use XDG resolution:
#   $XDG_CACHE_HOME/healpyxel/healpix_grids (or ~/.cache/healpyxel/healpix_grids if not set)
# Uncomment to override:
# cache_dir = ~/.cache/healpyxel/healpix_grids

# Precomputed nsides (comma-separated)
# These nsides will be auto-generated/cached on first use if missing
# Default: [32, 64, 128, 256]
precomputed_nsides = 32,64,128,256

[general]
# Whether to fix antimeridian-crossing polygons during HEALPix boundary computation
fix_antimeridian = true

# Tolerance in degrees for antimeridian detection (advanced parameter)
antimeridian_tolerance = 1.0
"""

    config_file.write_text(default_config)
    return config_file

def manage_healpix_cache(action: str = 'list',
                         nsides: Optional[List[int]] = None,
                         cache_dir: Optional[Path] = None,
                         config_dir: Optional[Path] = None,
                         force: bool = False) -> dict:
    """Core cache management logic with precedence awareness.

    No Click dependencies; called by CLI wrapper in 05_cli.ipynb.
    Uses _get_cache_dir() and _get_config_dir() for proper precedence.

    Args:
        action: 'list', 'generate', 'verify', 'clean', 'info', or 'config'
        nsides: list of nside values for 'generate' or 'verify' actions
        cache_dir: explicit CLI override (highest precedence)
        config_dir: explicit CLI override (highest precedence)
        force: whether to overwrite existing cache files during 'generate'

    Returns:
        dict with keys:
            'action': str, action performed
            'cache_dir': str, resolved cache directory
            'config_dir': str, resolved config directory
            'status': 'ok' or 'error'
            'count'/'files'/'deleted'/'generated'/etc: action-specific data

    Raises:
        ValueError for invalid action or missing required args
    """
    import os

    # Resolve directories with full precedence
    cache_dir = _get_cache_dir(cli_arg=cache_dir, env_override=os.environ.get('HEALPYXEL_CACHE'))
    config_dir = _get_config_dir(cli_arg=config_dir, env_override=os.environ.get('HEALPYXEL_CONFIG'))

    result = {
        'action': action,
        'cache_dir': str(cache_dir),
        'config_dir': str(config_dir),
        'files': [],
        'status': 'ok'
    }

    if action == 'list':
        """List all cached HEALPix grids."""
        cache_files = sorted(cache_dir.glob('nside_*.parquet'))
        for f in cache_files:
            try:
                df = pd.read_parquet(f, columns=['healpix_id'])
                result['files'].append({
                    'filename': f.name,
                    'path': str(f),
                    'cells': len(df),
                    'size_mb': f.stat().st_size / 1e6
                })
            except Exception as e:
                warnings.warn(f'Failed to inspect {f}: {e}')
        result['count'] = len(result['files'])

    elif action == 'verify':
        """Verify cache completeness and integrity for specified nsides."""
        if not nsides:
            raise ValueError('nsides required for verify action')
        result['verified'] = []
        for nside in nsides:
            cache_key = _cache_key(nside, 'nested')
            cache_file = cache_dir / cache_key

            if not cache_file.exists():
                result['verified'].append({
                    'nside': nside,
                    'status': 'missing',
                    'error': f'Cache file not found: {cache_file}'
                })
                result['status'] = 'error'
                continue

            try:
                # Load and validate
                df = pd.read_parquet(cache_file)
                expected_npix = hp.nside2npix(nside)

                # Check 1: All pixels present
                if len(df) != expected_npix:
                    result['verified'].append({
                        'nside': nside,
                        'status': 'incomplete',
                        'error': f'Expected {expected_npix} pixels, found {len(df)}',
                        'missing_count': expected_npix - len(df)
                    })
                    result['status'] = 'error'
                    continue

                # Check 2: Correct columns
                required_cols = ['healpix_id', 'theta_0', 'theta_1', 'theta_2', 'theta_3',
                                 'phi_0', 'phi_1', 'phi_2', 'phi_3']
                missing_cols = set(required_cols) - set(df.columns)
                if missing_cols:
                    result['verified'].append({
                        'nside': nside,
                        'status': 'corrupt',
                        'error': f'Missing columns: {missing_cols}'
                    })
                    result['status'] = 'error'
                    continue

                # Check 3: No NaN values in coordinate columns
                coord_cols = [c for c in df.columns if c.startswith('theta_') or c.startswith('phi_')]
                nan_counts = df[coord_cols].isna().sum()
                total_nans = nan_counts.sum()
                if total_nans > 0:
                    result['verified'].append({
                        'nside': nside,
                        'status': 'corrupt',
                        'error': f'Found {total_nans} NaN values in coordinate columns',
                        'nan_columns': nan_counts[nan_counts > 0].to_dict()
                    })
                    result['status'] = 'error'
                    continue

                # Check 4: healpix_id values in valid range
                if df['healpix_id'].min() < 0 or df['healpix_id'].max() >= expected_npix:
                    result['verified'].append({
                        'nside': nside,
                        'status': 'corrupt',
                        'error': f'healpix_id out of range [0, {expected_npix})',
                        'min_id': int(df['healpix_id'].min()),
                        'max_id': int(df['healpix_id'].max())
                    })
                    result['status'] = 'error'
                    continue

                # All checks passed
                result['verified'].append({
                    'nside': nside,
                    'status': 'ok',
                    'path': str(cache_file),
                    'cells': len(df),
                    'size_mb': cache_file.stat().st_size / 1e6
                })

            except Exception as e:
                result['verified'].append({
                    'nside': nside,
                    'status': 'error',
                    'error': f'Verification failed: {str(e)}'
                })
                result['status'] = 'error'

    elif action == 'config':
        """Show current configuration and how precedence resolves."""
        settings = _load_user_settings(config_dir)
        config_file = config_dir / 'settings.ini'
        result['config_file'] = str(config_file)
        result['config_exists'] = config_file.exists()
        result['settings'] = {
            'cache_dir': str(settings['cache_dir']) if settings['cache_dir'] else 'auto (XDG)',
            'precomputed_nsides': settings['precomputed_nsides'],
            'fix_antimeridian': settings['fix_antimeridian'],
            'antimeridian_tolerance': settings['antimeridian_tolerance']
        }
        result['precedence'] = {
            'cache_dir_resolved': str(cache_dir),
            'env_var': f"HEALPYXEL_CACHE={os.environ.get('HEALPYXEL_CACHE', '(not set)')}",
            'xdg_cache_home': f"XDG_CACHE_HOME={os.environ.get('XDG_CACHE_HOME', '(not set, using ~/.cache)')}",
            'xdg_config_home': f"XDG_CONFIG_HOME={os.environ.get('XDG_CONFIG_HOME', '(not set, using ~/.config)')}"
        }

    elif action == 'generate':
        """Generate cache files for specified nsides."""
        if not nsides:
            raise ValueError('nsides required for generate action')
        result['generated'] = []
        for nside in nsides:
            cache_key = _cache_key(nside, 'nested')
            cache_file = cache_dir / cache_key

            if cache_file.exists() and not force:
                result['generated'].append({
                    'nside': nside,
                    'status': 'skipped',
                    'reason': 'already exists (use force=True to overwrite)'
                })
                continue

            try:
                # Generate full grid
                gdf = healpix_to_geodataframe(nside, order='nested', cache_mode='off', lon_convention='0_360')

                # Extract spherical coordinates from boundaries
                npix = hp.nside2npix(nside)
                pixels = np.arange(npix, dtype=int)
                xyz = hp.boundaries(nside, pixels, step=1, nest=True)
                x, y, z = xyz[:, 0, :], xyz[:, 1, :], xyz[:, 2, :]
                theta = np.arccos(np.clip(z, -1, 1))
                phi = np.arctan2(y, x)

                # Build spherical coordinate dataframe
                theta_dict = {f'theta_{i}': theta[:, i] for i in range(4)}
                phi_dict = {f'phi_{i}': phi[:, i] for i in range(4)}
                spherical_df = pd.DataFrame({**theta_dict, **phi_dict})
                spherical_df['healpix_id'] = pixels

                # Save to cache
                _save_cached_boundaries(spherical_df, nside, 'nested', cache_dir)

                result['generated'].append({
                    'nside': nside,
                    'status': 'ok',
                    'path': str(cache_file),
                    'cells': npix
                })
            except Exception as e:
                result['generated'].append({
                    'nside': nside,
                    'status': 'error',
                    'error': str(e)
                })

    elif action == 'clean':
        """Remove all cached HEALPix grid files."""
        try:
            n_deleted = 0
            for f in cache_dir.glob('nside_*.parquet'):
                f.unlink()
                n_deleted += 1
            result['deleted'] = n_deleted
        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)

    elif action == 'info':
        """Show cache directory statistics."""
        cache_files = sorted(cache_dir.glob('nside_*.parquet'))
        total_size = sum(f.stat().st_size for f in cache_files)
        result['total_files'] = len(cache_files)
        result['total_size_mb'] = total_size / 1e6
        result['cache_dir_exists'] = cache_dir.exists()
        result['config_dir_exists'] = config_dir.exists()

    else:
        raise ValueError(f'Unknown action: {action}. Must be one of: list, generate, verify, clean, info, config')

    return result

def _healpy_boundaries_lonlat(nside: int, pixels: np.ndarray, nest: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """Return corner longitudes and latitudes for given pixels.
    Uses vectorized `healpy.boundaries`.
    Returns shapes: (npix, ncorner) for lons and lats in degrees.
    """
    # healpy.boundaries with array input returns shape (npix, 3, ncorners) for Cartesian (x,y,z)
    xyz = hp.boundaries(nside, pixels, step=1, nest=nest)  # shape (npix, 3, 4)

    # Extract x, y, z components: shape (npix, 4) each
    x = xyz[:, 0, :]  # shape (npix, 4)
    y = xyz[:, 1, :]  # shape (npix, 4)
    z = xyz[:, 2, :]  # shape (npix, 4)

    # Convert Cartesian to spherical (theta, phi)
    theta = np.arccos(z)  # polar angle in radians, shape (npix, 4)
    phi = np.arctan2(y, x)  # azimuthal angle in radians, shape (npix, 4)

    # Convert to degrees and to lon/lat
    lats = 90.0 - np.degrees(theta)  # shape (npix, 4)
    lons = np.degrees(phi)  # shape (npix, 4), in [-180, 180]
    lons = np.mod(lons, 360.0)  # normalize to [0, 360)

    # Already in correct shape: (npix, 4)
    return lons, lats

def healpix_to_geodataframe(nside: int, order: str = 'nested', lon_convention: str = '0_360',
                              pixels: Optional[Iterable[int]] = None, fix_antimeridian: bool = True,
                              chunk_size: int = 65536, cache_mode: str = 'use',
                              cache_dir: Optional[Path] = None,
                              ncores: int = 1) -> gpd.GeoDataFrame:
    """Create a GeoDataFrame of HEALPix cell polygons.

    Args:
        nside: HEALPix nside
        order: 'nested' or 'ring'
        lon_convention: '0_360' or '-180_180' (affects polygon coordinates). 'minus_plus180' is also accepted.
        pixels: optional iterable of pixel indices; default = all pixels
        fix_antimeridian: whether to call `antimeridian.fix_polygon` on polygons crossing the meridian
        chunk_size: number of pixels to process per chunk for memory control
        cache_mode: one of {'use','require','off'}
            - 'use': load cache if available, otherwise compute requested pixels only
            - 'require': require cache; if missing, raise error (no computation)
            - 'off': ignore cache entirely
        cache_dir: optional cache directory override
        ncores: number of worker threads for polygon construction. Default 1
            (sequential). >1 parallelizes across chunk blocks.

    Returns:
        GeoDataFrame with columns: 'healpix_id' and 'geometry' (EPSG:4326)
    """
    if cache_mode not in ('use', 'require', 'off'):
        raise ValueError("cache_mode must be one of: 'use', 'require', 'off'")

    nest = True if order == 'nested' else False
    npix = hp.nside2npix(nside)
    if pixels is None:
        pixels = np.arange(npix, dtype=int)
    else:
        pixels = np.asarray(list(pixels), dtype=int)

    # Cache applies only to full-grid cache files; never forces full-grid computation.
    is_full_grid = len(pixels) == npix

    def _compute_polygons_for_pixels(pix_array: np.ndarray) -> gpd.GeoDataFrame:
        """Compute polygons for the given pixel array only."""
        total_local = len(pix_array)
        chunks = [pix_array[start:start + chunk_size]
                  for start in range(0, total_local, chunk_size)]

        def _process_chunk(pix_chunk: np.ndarray) -> list[dict]:
            xyz = hp.boundaries(nside, pix_chunk, step=1, nest=nest)  # (npix, 3, 4)
            x, y, z = xyz[:, 0, :], xyz[:, 1, :], xyz[:, 2, :]
            theta = np.arccos(np.clip(z, -1, 1))
            phi = np.arctan2(y, x)
            lons_arr, lats_arr = _spherical_to_lonlat(theta, phi, lon_convention)
            polys = _polygons_from_lonlat(lons_arr, lats_arr, lon_convention, fix_antimeridian)
            return [{'healpix_id': int(pix), 'geometry': poly}
                    for pix, poly in zip(pix_chunk, polys)]

        records_list: list[list[dict]] = []
        with tqdm(total=total_local, desc=f"Building HEALPix geometries (nside={nside})", unit="cell") as pbar:
            if ncores > 1 and len(chunks) > 1:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=ncores) as ex:
                    for records in ex.map(_process_chunk, chunks):
                        records_list.append(records)
                        pbar.update(len(records))
            else:
                for chunk in chunks:
                    records_list.append(_process_chunk(chunk))
                    pbar.update(len(chunk))

        records_local = [r for recs in records_list for r in recs]
        gdf_local = gpd.GeoDataFrame(records_local, geometry='geometry', crs='EPSG:4326')
        gdf_local = gdf_local.set_index('healpix_id')
        return gdf_local

    if cache_mode != 'off':
        cached_df = _load_cached_boundaries(nside, order, cache_dir)
        if cached_df is None:
            if cache_mode == 'require':
                raise ValueError(
                    f"Cache missing for nside={nside}, order={order}. "
                    f"Run healpyxel_cache --generate {nside} to create it."
                )
        else:
            # Subset cache to requested pixels if not full grid
            if not is_full_grid:
                cached_df = cached_df.loc[cached_df.index.intersection(pixels)]

            theta_cols = [f'theta_{i}' for i in range(4)]
            phi_cols = [f'phi_{i}' for i in range(4)]
            theta_arr = cached_df[theta_cols].values
            phi_arr = cached_df[phi_cols].values
            lons_arr, lats_arr = _spherical_to_lonlat(theta_arr, phi_arr, lon_convention)
            if ncores > 1:
                polys = _lonlat_to_polygons_parallel(
                    lons_arr, lats_arr, lon_convention=lon_convention,
                    fix_antimeridian=fix_antimeridian,
                    chunk_size=chunk_size, ncores=ncores)
            else:
                polys = _lonlat_to_polygons(lons_arr, lats_arr, lon_convention=lon_convention,
                                            fix_antimeridian=fix_antimeridian)
            gdf_cached = gpd.GeoDataFrame({'geometry': polys}, index=cached_df.index, crs='EPSG:4326')
            gdf_cached.index.name = 'healpix_id'

            if cache_mode == 'require':
                if len(gdf_cached) != len(pixels):
                    missing = set(pixels) - set(gdf_cached.index.values)
                    raise ValueError(
                        f"Cache is incomplete for nside={nside}. Missing {len(missing)} pixels. "
                        f"Regenerate with healpyxel_cache --generate {nside}"
                    )
                return gdf_cached

            # cache_mode == 'use'
            if len(gdf_cached) == len(pixels):
                return gdf_cached

            # Compute missing pixels and merge
            missing_pixels = np.array(sorted(set(pixels) - set(gdf_cached.index.values)), dtype=int)
            if len(missing_pixels) > 0:
                gdf_missing = _compute_polygons_for_pixels(missing_pixels)
                gdf = pd.concat([gdf_cached, gdf_missing]).sort_index()
                return gdf
            return gdf_cached

    # cache_mode == 'off' or cache miss in 'use' mode
    return _compute_polygons_for_pixels(pixels)

def _load_metadata_for_aggregate(agg_path: Path) -> Optional[dict]:
    """Load metadata JSON sidecar for an aggregate parquet file.

    Looks for {agg_stem}.meta.json in the same directory.
    Returns dict if found, None otherwise (no error).
    """
    meta_path = agg_path.parent / f'{agg_path.stem}.meta.json'
    if meta_path.exists():
        try:
            with open(meta_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            warnings.warn(f'Failed to load metadata {meta_path}: {e}')
            return None
    return None

#| export
def _extract_healpix_params_from_metadata(metadata: dict) -> dict:
    """Extract nside, order, lon_convention from metadata.

    Returns dict with keys (may be empty if not found):
        - 'nside': int or None
        - 'order': 'nested'/'ring' or None
        - 'lon_convention': '0_360'/'-180_180'/None (accepts 'minus_plus180' and normalizes to '-180_180')
    """
    result = {'nside': None, 'order': None, 'lon_convention': None}
    try:
        # Look in sidecar_metadata.healpix
        healpix_meta = metadata.get('sidecar_metadata', {}).get('healpix', {})
        if healpix_meta:
            result['nside'] = healpix_meta.get('nside')
            order_str = healpix_meta.get('order', '').lower()
            if order_str in ('nested', 'ring'):
                result['order'] = order_str

        # Look in sidecar_metadata.coordinates
        coords_meta = metadata.get('sidecar_metadata', {}).get('coordinates', {})
        if coords_meta:
            lon_conv = coords_meta.get('lon_convention')
            if lon_conv in ('0_360', '-180_180', 'minus_plus180'):
                # ADR-009: normalize 'minus_plus180' to canonical form
                result['lon_convention'] = '-180_180' if lon_conv == 'minus_plus180' else lon_conv
    except Exception as e:
        warnings.warn(f'Error extracting HEALPix params from metadata: {e}')

    return result

#| export
def _validate_aggregate_file(metadata: dict, input_path: Path) -> None:
    """Validate that the input file is an aggregate, not a sidecar.

    Raises ClickException if file is wrong type.
    """
    import click
    if metadata:
        stage = metadata.get('processing', {}).get('stage')
        if stage == 'sidecar':
            raise click.ClickException(
                f'\n❌ ERROR: Input file is a SIDECAR, not an AGGREGATE!\n\n'
                f'   File: {input_path.name}\n'
                f'   Stage: {stage}\n\n'
                f'You must pass the aggregate output from healpyxel_aggregate, not the sidecar.\n'
                f'Look for a file named: *aggregate*.parquet\n'
            )
        elif stage not in ('aggregate', None):
            raise click.ClickException(
                f'\n❌ ERROR: Unexpected processing stage: {stage}\n\n'
                f'   File: {input_path.name}\n\n'
                f'Expected: aggregate (output from healpyxel_aggregate)\n'
                f'For more info, check the .meta.json sidecar: {input_path.stem}.meta.json\n'
            )

def save_healpix_to_geoparquet(nside: int, output_path: Union[str, Path], order: str = 'nested',
                               lon_convention: str = '0_360', fix_antimeridian: bool = True,
                               chunk_size: int = 65536, parquet_kwargs: Optional[dict] = None,
                               overwrite: bool = False, interactive: bool = True,
                               ncores: int = 1) -> Path:
    """Build HEALPix vector layer and save as GeoParquet.

    Args:
        nside: HEALPix nside
        output_path: Path to output GeoParquet file
        order: 'nested' or 'ring'
        lon_convention: '0_360' or '-180_180'
        fix_antimeridian: Whether to fix antimeridian-wrapping
        chunk_size: Pixels per chunk when building geometries
        parquet_kwargs: Forwarded to `GeoDataFrame.to_parquet`
        overwrite: Whether to overwrite the file if it exists (default: False)
        interactive: If True, prompt the user for confirmation before overwriting
        ncores: Number of worker threads for polygon construction (default 1).

    Returns:
        Path to the written file

    Raises:
        FileExistsError: If the file exists and overwrite is False
    """
    output_path = Path(output_path)
    parquet_kwargs = parquet_kwargs or {}

    # Check if the file exists
    if output_path.exists():
        if not overwrite:
            if interactive:
                response = input(f"⚠ File {output_path} already exists. Overwrite? [y/N]: ").strip().lower()
                if response != 'y':
                    raise FileExistsError(f"File {output_path} already exists. Use `overwrite=True` or confirm interactively.")
            else:
                raise FileExistsError(f"File {output_path} already exists. Use `overwrite=True` to overwrite.")
        else:
            print(f"⚠ Overwriting existing file: {output_path}")

    # Generate the GeoDataFrame
    gdf = healpix_to_geodataframe(
        nside=nside,
        order=order,
        lon_convention=lon_convention,
        fix_antimeridian=fix_antimeridian,
        chunk_size=chunk_size,
        ncores=ncores
    )

    # Save with geopandas (which will write GeoParquet using pyarrow backend)
    gdf.to_parquet(output_path, **parquet_kwargs)
    print(f"✓ Wrote GeoParquet: {output_path}")
    return output_path

def export_healpix_to_geotiff(
    df: pd.DataFrame,
    column: str,
    output_path: Union[str, Path],
    nside: int,
    order: str = 'nested',
    crs: str = 'IAU:19900',  # Mercury IAU CRS
    width: int = 1440,
    height: int = 720
) -> Path:
    """Export a HEALPix column to GeoTIFF (requires rasterio + healpy).

    Args:
        df: DataFrame with healpix_id index or healpix_id column
        column: data column to export
        output_path: GeoTIFF output path
        nside: HEALPix nside
        order: 'nested' or 'ring'
        crs: CRS string for GeoTIFF
        width: output raster width (pixels)
        height: output raster height (pixels)

    Returns:
        Path to written GeoTIFF
    """
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError as e:
        raise ImportError("rasterio required for GeoTIFF export (pip install rasterio)") from e

    output_path = Path(output_path)

    if column not in df.columns:
        raise KeyError(f"Column not found: {column}")

    # Resolve healpix_id array
    if df.index.name == 'healpix_id':
        hp_ids = df.index.to_numpy(dtype=int)
    elif 'healpix_id' in df.columns:
        hp_ids = df['healpix_id'].to_numpy(dtype=int)
    else:
        raise KeyError("healpix_id not found in index or columns")

    values = df[column].to_numpy(dtype=np.float64)

    n_pixels = hp.nside2npix(nside)
    healpix_map = np.full(n_pixels, np.nan, dtype=np.float64)

    # Guard against out-of-range ids
    mask = (hp_ids >= 0) & (hp_ids < n_pixels)
    healpix_map[hp_ids[mask]] = values[mask]

    # Build equirectangular grid
    lon = np.linspace(-180, 180, width)
    lat = np.linspace(90, -90, height)
    lon_grid, lat_grid = np.meshgrid(lon, lat)

    theta = np.radians(90.0 - lat_grid)
    phi = np.radians((lon_grid + 360.0) % 360.0)

    nest = True if order == 'nested' else False
    pixels = hp.ang2pix(nside, theta, phi, nest=nest)
    grid = healpix_map[pixels]

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

    return output_path

def _get_config(config, key, default=None):
    """Access config value from dict or argparse Namespace."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)

def run(config):
    """Run geoparquet conversion from a config dict or argparse Namespace.

    Reads the aggregate parquet and its .meta.json sidecar to determine the
    lon_convention used during sidecar processing (normalizing 'minus_plus180'
    to '-180_180'), then generates GeoParquet with matching coordinates.

    Note: For QGIS visualization, consider using 'healpyxel_to_geoparquet' with
    the --export_tiff flag or rasterizing externally, as QGIS may cache parquet
    spatial metadata aggressively.
    """
    aggregate_path = _get_config(config, 'aggregate_path')
    output_suffix = _get_config(config, 'output_suffix', '.geo')
    output_dir = _get_config(config, 'output_dir')
    nside = _get_config(config, 'nside')
    order = _get_config(config, 'order', 'nested')
    lon_convention = _get_config(config, 'lon_convention', 'auto')
    fix_antimeridian = _get_config(config, 'fix_antimeridian', True)
    chunk_size = _get_config(config, 'chunk_size', 65536)
    ncores = _get_config(config, 'ncores', 1)
    dense = _get_config(config, 'densify', False)
    yes = _get_config(config, 'yes', False)

    if aggregate_path is None:
        raise RuntimeError("--aggregate-path is required")

    aggregate_path = Path(aggregate_path)
    if not aggregate_path.exists():
        raise FileNotFoundError(f"Aggregate file not found: {aggregate_path}")

    # Load aggregate data
    agg_df = pd.read_parquet(aggregate_path)

    # Load metadata to extract HEALPix params
    meta_path = aggregate_path.parent / f'{aggregate_path.stem}.meta.json'
    metadata = {}
    if meta_path.exists():
        import json
        with open(meta_path) as f:
            metadata = json.load(f)

    meta_params = _extract_healpix_params_from_metadata(metadata)
    nside = nside or meta_params.get('nside')
    order = order or meta_params.get('order') or 'nested'
    if lon_convention == 'auto':
        lon_convention = meta_params.get('lon_convention') or '0_360'

    if nside is None:
        raise RuntimeError("Cannot determine nside from metadata or --nside argument")

    # Determine lon_convention for geometry
    import re
    lon_param = '-180_180' if lon_convention in ('-180_180', 'auto') else '0_360'

    # Build HEALPix geometry grid
    healpix_gdf = healpix_to_geodataframe(
        nside=nside,
        order=order,
        lon_convention=lon_param,
        fix_antimeridian=fix_antimeridian,
        chunk_size=chunk_size,
        ncores=ncores
    )

    if healpix_gdf.index.name != 'healpix_id':
        healpix_gdf = healpix_gdf.set_index('healpix_id') if 'healpix_id' in healpix_gdf.columns else healpix_gdf

    # Filter geometry to only populated cells (no densify by default)
    if 'healpix_id' not in agg_df.index.names:
        agg_df = agg_df.reset_index().set_index('healpix_id')
    populated_ids = pd.Index(agg_df.index, name='healpix_id')
    healpix_pop = healpix_gdf.loc[healpix_gdf.index.isin(populated_ids)]

    merged = healpix_pop.join(agg_df, how='inner')

    # Densify only if explicitly requested
    if dense:
        import healpy as hp
        full_index = pd.RangeIndex(0, hp.nside2npix(nside), name='healpix_id')
        # Build full grid and join with aggregate data (empty cells get NaN)
        merged = healpix_gdf.join(agg_df, how='left')

    # Determine output path
    if output_dir:
        out_path = Path(output_dir) / f'{aggregate_path.stem}{output_suffix}.parquet'
    else:
        out_path = aggregate_path.parent / f'{aggregate_path.stem}{output_suffix}.parquet'

    if out_path.exists() and not yes:
        response = input(f"⚠️  {out_path.name} already exists. Overwrite? [y/N] ").strip().lower()
        if response != 'y':
            print("Skipped.")
            return 0

    merged.to_parquet(out_path, index=True)
    print(f"✓ Wrote GeoParquet: {out_path}")
    return 0

def parse_arguments(argv=None):
    """Parse command-line arguments for to_geoparquet."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Convert aggregate parquet to GeoParquet with HEALPix geometry"
    )
    parser.add_argument('-a', '--aggregate-path', type=Path, required=True)
    parser.add_argument('-y', '--yes', action='store_true')
    parser.add_argument('-s', '--output-suffix', type=str, default='.geo')
    parser.add_argument('-d', '--output-dir', type=Path, default=None)
    parser.add_argument('-n', '--nside', type=int, default=None)
    parser.add_argument('-O', '--order', type=str, default='nested')
    parser.add_argument('-l', '--lon-convention', type=str, default='auto')
    parser.add_argument('-f', '--fix-antimeridian/--no-fix-antimeridian', action='store_true', default=True)
    parser.add_argument('-c', '--chunk-size', type=int, default=65536)
    parser.add_argument('--ncores', type=int, default=1,
                        help='Number of worker threads for polygon construction (default: 1).')
    parser.add_argument('--densify', action='store_true')
    return parser.parse_args(argv)

def main(argv=None):
    """CLI entry point for healpyxel_to_geoparquet."""
    args = parse_arguments(argv)
    return run(args)


