import pytest
import numpy as np
import pandas as pd
import geopandas as gpd
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import healpy as hp
from shapely.geometry import Polygon

from healpyxel.geospatial import (
    is_geometry_valid,
    _spherical_to_lonlat,
    _cache_key,
    _get_cache_dir,
    _get_config_dir,
    _load_cached_boundaries,
    _save_cached_boundaries,
    _load_user_settings,
    init_user_config,
    manage_healpix_cache,
    healpix_to_geodataframe,
    _extract_healpix_params_from_metadata,
    _validate_aggregate_file,
    save_healpix_to_geoparquet,
    export_healpix_to_geotiff,
    _get_config,
    run,
    parse_arguments,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def _clean_env():
    """Back up and restore env vars that affect resolution."""
    backup = {}
    keys = ['HEALPYXEL_CACHE', 'XDG_CACHE_HOME', 'HEALPYXEL_CONFIG', 'XDG_CONFIG_HOME']
    for k in keys:
        backup[k] = os.environ.get(k)
        os.environ.pop(k, None)
    yield
    for k, v in backup.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


@pytest.fixture
def _mock_tqdm():
    with patch('healpyxel.geospatial.tqdm'):
        yield


@pytest.fixture
def _no_antimeridian():
    with patch('healpyxel.geospatial.antimeridian.fix_polygon', side_effect=lambda p: p):
        yield


@pytest.fixture
def _mock_boundaries():
    """Return a deterministic xyz array for healpy.boundaries."""
    npix = 16
    corners = 4
    xyz = np.zeros((npix, 3, corners))
    # Face 1 N poles
    xyz[:, 0, :] = 1.0
    xyz[:, 1, :] = 0.0
    xyz[:, 2, :] = 0.0
    with patch('healpyxel.geospatial.hp.boundaries', return_value=xyz):
        yield


# ---------------------------------------------------------------------------
# is_geometry_valid
# ---------------------------------------------------------------------------

class TestIsGeometryValid:
    def test_valid_bounds(self):
        g = Polygon([(-180, -90), (180, -90), (180, 90), (-180, 90)])
        assert is_geometry_valid(g) is True

    def test_invalid_lon_above_360(self):
        g = Polygon([(400, 0), (410, 0), (410, 10), (400, 10)])
        assert is_geometry_valid(g) is False

    def test_invalid_lon_below_minus180(self):
        g = Polygon([(-200, 0), (-190, 0), (-190, 10), (-200, 10)])
        assert is_geometry_valid(g) is False

    def test_invalid_lat_above_90(self):
        g = Polygon([(0, 100), (10, 100), (10, 110), (0, 110)])
        assert is_geometry_valid(g) is False

    def test_invalid_lat_below_minus90(self):
        g = Polygon([(0, -100), (10, -100), (10, -110), (0, -110)])
        assert is_geometry_valid(g) is False

    def test_empty_geometry(self):
        g = Polygon()  # empty polygon
        assert is_geometry_valid(g) is False

    def test_valid_zero_lon(self):
        g = Polygon([(0, 0), (180, 0), (180, 90), (0, 90)])
        assert is_geometry_valid(g) is True


# ---------------------------------------------------------------------------
# _spherical_to_lonlat
# ---------------------------------------------------------------------------

class TestSphericalToLonlat:
    def test_north_pole(self):
        lons, lats = _spherical_to_lonlat(np.array([0.0]), np.array([0.0]), '0_360')
        assert abs(lats[0] - 90.0) < 1e-6

    def test_south_pole(self):
        lons, lats = _spherical_to_lonlat(np.array([np.pi]), np.array([0.0]), '0_360')
        assert abs(lats[0] - (-90.0)) < 1e-6

    def test_0_360_convention(self):
        lons, _ = _spherical_to_lonlat(np.array([np.pi/2]), np.array([np.pi]), '0_360')
        assert abs(lons[0] - 180.0) < 1e-6

    def test_minus_180_180_convention(self):
        lons, _ = _spherical_to_lonlat(np.array([np.pi/2]), np.array([np.pi]), '-180_180')
        assert abs(abs(lons[0]) - 180.0) < 1e-6

    def test_vectorized(self):
        theta = np.array([0.0, np.pi, np.pi/2])
        phi = np.array([0.0, 0.0, np.pi])
        lons, lats = _spherical_to_lonlat(theta, phi, '0_360')
        assert len(lons) == 3
        assert len(lats) == 3

    def test_lat_in_range(self):
        lons, lats = _spherical_to_lonlat(np.array([0.0, np.pi/2, np.pi]),
                                           np.array([0.0, 0.0, 0.0]), '0_360')
        assert np.all(lats >= -90.0)
        assert np.all(lats <= 90.0)


# ---------------------------------------------------------------------------
# _cache_key
# ---------------------------------------------------------------------------

class TestCacheKey:
    def test_nested_format(self):
        assert _cache_key(256, 'nested') == 'nside_256_nest_spherical.parquet'

    def test_ring_format(self):
        assert _cache_key(256, 'ring') == 'nside_256_ring_spherical.parquet'

    def test_padding(self):
        assert _cache_key(32, 'nested') == 'nside_032_nest_spherical.parquet'

    def test_high_nside(self):
        assert _cache_key(512, 'nested') == 'nside_512_nest_spherical.parquet'


# ---------------------------------------------------------------------------
# _get_cache_dir / _get_config_dir (env, cli, xdg)
# ---------------------------------------------------------------------------

class TestGetCacheDir:
    @patch.dict(os.environ, {}, clear=True)
    def test_default_fallback(self):
        os.environ.pop('XDG_CACHE_HOME', None)
        result = _get_cache_dir()
        assert '.cache' in str(result)
        assert 'healpyxel' in str(result)

    @patch.dict(os.environ, {'HEALPYXEL_CACHE': '/tmp/heal_cache_xyz'})
    def test_env_wins_over_xdg(self):
        os.environ['XDG_CACHE_HOME'] = '/tmp/other'
        result = _get_cache_dir()
        assert '/tmp/heal_cache_xyz' in str(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_cli_wins_over_env(self):
        with tempfile.TemporaryDirectory() as cli_tmp:
            os.environ['HEALPYXEL_CACHE'] = '/tmp/other'
            result = _get_cache_dir(cli_arg=cli_tmp)
            assert str(result) == cli_tmp

    @patch.dict(os.environ, {'XDG_CACHE_HOME': '/tmp/xdgcache'})
    def test_xdg_respected(self):
        os.environ.pop('HEALPYXEL_CACHE', None)
        result = _get_cache_dir()
        assert '/tmp/xdgcache/healpyxel/healpix_grids' == str(result)

    def test_path_expansion(self):
        result = _get_cache_dir(cli_arg='~/my_cache')
        assert result == Path.home() / 'my_cache'

    def test_directory_created(self, _clean_env):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'new' / 'nested' / 'dir'
            _get_cache_dir(cli_arg=target)
            assert target.exists()


class TestGetConfigDir:
    @patch.dict(os.environ, {}, clear=True)
    def test_cli_wins(self):
        with tempfile.TemporaryDirectory() as cli_tmp:
            result = _get_config_dir(cli_arg=cli_tmp)
            assert str(result) == cli_tmp

    @patch.dict(os.environ, {}, clear=True)
    def test_xdg_config_home(self):
        os.environ['XDG_CONFIG_HOME'] = '/tmp/xdgcfg'
        result = _get_config_dir()
        assert '/tmp/xdgcfg/healpyxel' == str(result)

    def test_directory_created(self, _clean_env):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'config' / 'healpyxel'
            _get_config_dir(cli_arg=target)
            assert target.exists()

    def test_healpyxel_config_env_wins_over_xdg(self):
        with tempfile.TemporaryDirectory() as env_tmp:
            os.environ['HEALPYXEL_CONFIG'] = env_tmp
            try:
                result = _get_config_dir()
                assert str(result) == env_tmp
            finally:
                os.environ.pop('HEALPYXEL_CONFIG', None)


# ---------------------------------------------------------------------------
# _load_cached_boundaries / _save_cached_boundaries
# ---------------------------------------------------------------------------

class TestLoadCachedBoundaries:
    def test_cache_hit_returns_dataframe(self):
        cache_dir = Path(tempfile.mkdtemp())
        nside = 4
        key = _cache_key(nside)
        df = pd.DataFrame({
            'healpix_id': range(hp.nside2npix(nside)),
            'theta_0': 0.1, 'theta_1': 0.2, 'theta_2': 0.3, 'theta_3': 0.4,
            'phi_0': 0.1, 'phi_1': 0.2, 'phi_2': 0.3, 'phi_3': 0.4,
        })
        df.to_parquet(cache_dir / key)
        result = _load_cached_boundaries(nside, cache_dir=cache_dir)
        assert result is not None
        assert 'healpix_id' not in result.columns or result.index.name == 'healpix_id'

    def test_cache_miss_returns_none(self):
        empty_dir = Path(tempfile.mkdtemp())
        result = _load_cached_boundaries(64, cache_dir=empty_dir)
        assert result is None

    def test_explicit_cache_dir(self):
        cache_dir = Path(tempfile.mkdtemp())
        key = _cache_key(8)
        npix = hp.nside2npix(8)
        df = pd.DataFrame({
            'healpix_id': range(npix),
            'theta_0': 0.1, 'theta_1': 0.2, 'theta_2': 0.3, 'theta_3': 0.4,
            'phi_0': 0.1, 'phi_1': 0.2, 'phi_2': 0.3, 'phi_3': 0.4,
        })
        df.to_parquet(cache_dir / key)
        result = _load_cached_boundaries(8, cache_dir=cache_dir)
        assert result is not None
        assert len(result) == npix

    def test_warning_on_corrupt(self):
        cache_dir = Path(tempfile.mkdtemp())
        key = _cache_key(8)
        bad_file = cache_dir / key
        bad_file.write_bytes(b'not a parquet')
        with pytest.warns():
            result = _load_cached_boundaries(8, cache_dir=cache_dir)
        assert result is None


class TestSaveCachedBoundaries:
    def test_save_creates_file(self):
        cache_dir = Path(tempfile.mkdtemp())
        npix = 16
        df = pd.DataFrame({
            'healpix_id': range(npix),
            'theta_0': 0.1, 'theta_1': 0.2, 'theta_2': 0.3, 'theta_3': 0.4,
            'phi_0': 0.1, 'phi_1': 0.2, 'phi_2': 0.3, 'phi_3': 0.4,
        })
        path = _save_cached_boundaries(df, 4, cache_dir=cache_dir)
        assert path.exists()
        assert path.name == 'nside_004_nest_spherical.parquet'

    def test_save_validates_missing_columns(self):
        df = pd.DataFrame({'a': [1], 'b': [2]})
        with pytest.raises(ValueError, match="columns"):
            _save_cached_boundaries(df, 4, cache_dir=Path(tempfile.mkdtemp()))

    def test_save_roundtrip(self):
        cache_dir = Path(tempfile.mkdtemp())
        npix = 16
        df = pd.DataFrame({
            'healpix_id': range(npix),
            'theta_0': 0.1, 'theta_1': 0.2, 'theta_2': 0.3, 'theta_3': 0.4,
            'phi_0': 0.1, 'phi_1': 0.2, 'phi_2': 0.3, 'phi_3': 0.4,
        })
        path = _save_cached_boundaries(df, 4, cache_dir=cache_dir)
        loaded = pd.read_parquet(path)
        assert len(loaded) == npix


# ---------------------------------------------------------------------------
# _load_user_settings / init_user_config
# ---------------------------------------------------------------------------

class TestLoadUserSettings:
    def test_defaults_when_no_config(self):
        config_dir = Path(tempfile.mkdtemp())
        settings = _load_user_settings(config_dir=config_dir)
        assert settings['precomputed_nsides'] == [32, 64, 128, 256]
        assert settings['fix_antimeridian'] is True
        assert settings['antimeridian_tolerance'] == 1.0

    def test_defaults_when_no_config_dir_arg(self):
        """No config_dir arg triggers _get_config_dir() call."""
        orig_config_dir = os.environ.pop('XDG_CONFIG_HOME', None)
        orig_home = os.environ.pop('HOME', None)
        try:
            os.environ['HOME'] = tempfile.mkdtemp()
            settings = _load_user_settings()
            assert settings['precomputed_nsides'] == [32, 64, 128, 256]
        finally:
            if orig_config_dir is not None:
                os.environ['XDG_CONFIG_HOME'] = orig_config_dir
            if orig_home is not None:
                os.environ['HOME'] = orig_home

    def test_reads_existing_config(self):
        config_dir = Path(tempfile.mkdtemp())
        (config_dir / 'settings.ini').write_text(
            "[cache]\nprecomputed_nsides = 32, 64\n"
            "[general]\nfix_antimeridian = false\nantimeridian_tolerance = 2.5\n"
        )
        settings = _load_user_settings(config_dir=config_dir)
        assert settings['precomputed_nsides'] == [32, 64]
        assert settings['fix_antimeridian'] is False
        assert settings['antimeridian_tolerance'] == 2.5

    def test_cache_dir_auto_defaults_none(self):
        config_dir = Path(tempfile.mkdtemp())
        (config_dir / 'settings.ini').write_text("[cache]\ncache_dir = auto\n")
        settings = _load_user_settings(config_dir=config_dir)
        assert settings['cache_dir'] is None

    def test_malformed_ini_returns_defaults(self):
        config_dir = Path(tempfile.mkdtemp())
        (config_dir / 'settings.ini').write_text("not valid ini content at all [[[\n")
        settings = _load_user_settings(config_dir=config_dir)
        assert settings['precomputed_nsides'] == [32, 64, 128, 256]


class TestInitUserConfig:
    def test_creates_file_when_missing(self):
        config_dir = Path(tempfile.mkdtemp())
        config_file = init_user_config(config_dir=config_dir)
        assert config_file.exists()
        assert config_file.name == 'settings.ini'

    def test_returns_existing_file(self):
        config_dir = Path(tempfile.mkdtemp())
        existing = config_dir / 'settings.ini'
        existing.write_text("[cache]\n")
        result = init_user_config(config_dir=config_dir)
        assert result == existing

    def test_contains_expected_sections(self):
        config_dir = Path(tempfile.mkdtemp())
        content = (init_user_config(config_dir=config_dir)).read_text()
        assert '[cache]' in content
        assert '[general]' in content
        assert 'precomputed_nsides' in content


# ---------------------------------------------------------------------------
# manage_healpix_cache
# ---------------------------------------------------------------------------

class TestManageHealpixCache:
    def test_list_empty_cache(self, _clean_env, _mock_tqdm):
        cache_dir = Path(tempfile.mkdtemp())
        result = manage_healpix_cache('list', cache_dir=cache_dir)
        assert result['count'] == 0
        assert result['status'] == 'ok'

    def test_generate_and_verify(self, _clean_env, _mock_tqdm, _no_antimeridian):
        cache_dir = Path(tempfile.mkdtemp())
        gen = manage_healpix_cache('generate', nsides=[8], cache_dir=cache_dir, force=True)
        assert gen['status'] == 'ok'
        assert gen['generated'][0]['status'] == 'ok'
        ver = manage_healpix_cache('verify', nsides=[8], cache_dir=cache_dir)
        assert ver['status'] == 'ok'
        assert ver['verified'][0]['status'] == 'ok'

    def test_generate_skips_existing(self, _clean_env, _mock_tqdm, _no_antimeridian):
        cache_dir = Path(tempfile.mkdtemp())
        manage_healpix_cache('generate', nsides=[8], cache_dir=cache_dir, force=True)
        result = manage_healpix_cache('generate', nsides=[8], cache_dir=cache_dir)
        assert result['generated'][0]['status'] == 'skipped'

    def test_verify_missing(self, _clean_env):
        cache_dir = Path(tempfile.mkdtemp())
        result = manage_healpix_cache('verify', nsides=[32], cache_dir=cache_dir)
        assert result['status'] == 'error'
        assert result['verified'][0]['status'] == 'missing'

    def test_clean_removes_files(self, _clean_env, _mock_tqdm, _no_antimeridian):
        cache_dir = Path(tempfile.mkdtemp())
        manage_healpix_cache('generate', nsides=[8], cache_dir=cache_dir, force=True)
        result = manage_healpix_cache('clean', cache_dir=cache_dir)
        assert result['deleted'] == 1
        assert result['status'] == 'ok'

    def test_info_empty_cache(self, _clean_env):
        cache_dir = Path(tempfile.mkdtemp())
        result = manage_healpix_cache('info', cache_dir=cache_dir)
        assert result['total_files'] == 0

    def test_invalid_action_raises(self, _clean_env):
        with pytest.raises(ValueError, match="Unknown action"):
            manage_healpix_cache('bogus')

    def test_verify_requires_nsides(self, _clean_env):
        with pytest.raises(ValueError, match="nsides required"):
            manage_healpix_cache('verify', cache_dir=Path(tempfile.mkdtemp()))

    def test_generate_requires_nsides(self, _clean_env):
        with pytest.raises(ValueError, match="nsides required"):
            manage_healpix_cache('generate', cache_dir=Path(tempfile.mkdtemp()))

    def test_config_action(self, _clean_env):
        result = manage_healpix_cache('config',
                                      cache_dir=Path(tempfile.mkdtemp()),
                                      config_dir=Path(tempfile.mkdtemp()))
        assert 'config_file' in result
        assert 'settings' in result
        assert 'precedence' in result


# ---------------------------------------------------------------------------
# healpix_to_geodataframe
# ---------------------------------------------------------------------------

class TestHealpixToGeodataframe:
    def test_invalid_cache_mode(self):
        with pytest.raises(ValueError, match="cache_mode must be one of"):
            healpix_to_geodataframe(8, cache_mode='invalid')

    def test_cache_mode_off(self):
        import geopandas as gpd
        gdf = healpix_to_geodataframe(8, cache_mode='off', lon_convention='0_360',
                                       fix_antimeridian=False)
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert gdf.crs is not None

    def test_cache_mode_require_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / 'empty'
            cache_dir.mkdir()
            with pytest.raises(ValueError, match="Cache missing"):
                healpix_to_geodataframe(8, cache_mode='require', cache_dir=cache_dir)

    def test_cache_mode_use_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            gdf = healpix_to_geodataframe(8, cache_mode='use',
                                           cache_dir=Path(tmp) / 'empty',
                                           fix_antimeridian=False)
            assert isinstance(gdf, gpd.GeoDataFrame)

    def test_geodataframe_index(self):
        gdf = healpix_to_geodataframe(4, cache_mode='off', fix_antimeridian=False)
        assert gdf.index.name == 'healpix_id'

    def test_geodataframe_crs(self):
        gdf = healpix_to_geodataframe(4, cache_mode='off', fix_antimeridian=False)
        assert gdf.crs.to_string() == 'EPSG:4326'

    def test_subset_pixels(self):
        pixels = [0, 1, 2, 3]
        gdf = healpix_to_geodataframe(4, pixels=pixels, cache_mode='off',
                                       fix_antimeridian=False)
        assert len(gdf) == len(pixels)

    def test_parallel_matches_sequential(self):
        """ncores>1 must produce identical index and geometries to ncores=1."""
        for fix in (False, True):
            seq = healpix_to_geodataframe(8, cache_mode='off',
                                          fix_antimeridian=fix, ncores=1)
            par = healpix_to_geodataframe(8, cache_mode='off',
                                          fix_antimeridian=fix, ncores=3)
            assert seq.index.tolist() == par.index.tolist()
            assert len(seq) == len(par) == hp.nside2npix(8)
            assert all(seq.geometry.iloc[i].equals(par.geometry.iloc[i])
                       for i in range(len(seq)))

    def test_parallel_subset_pixels(self):
        pixels = [0, 1, 2, 3, 4, 5, 6, 7]
        gdf = healpix_to_geodataframe(4, pixels=pixels, cache_mode='off',
                                       fix_antimeridian=False, ncores=2)
        assert len(gdf) == len(pixels)
        assert sorted(gdf.index.tolist()) == pixels

    def test_parallel_cached_path(self):
        """ncores>1 matches ncores=1 when building polygons from cache."""
        nside = 4
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            npix = hp.nside2npix(nside)
            pixels = np.arange(npix, dtype=int)
            xyz = hp.boundaries(nside, pixels, step=1, nest=True)
            theta = np.arccos(np.clip(xyz[:, 2, :], -1, 1))
            phi = np.arctan2(xyz[:, 1, :], xyz[:, 0, :])
            df = pd.DataFrame({
                'healpix_id': pixels,
                'theta_0': theta[:, 0], 'theta_1': theta[:, 1],
                'theta_2': theta[:, 2], 'theta_3': theta[:, 3],
                'phi_0': phi[:, 0], 'phi_1': phi[:, 1],
                'phi_2': phi[:, 2], 'phi_3': phi[:, 3],
            })
            _save_cached_boundaries(df, nside, 'nested', cache_dir)

            seq = healpix_to_geodataframe(nside, cache_mode='use', cache_dir=cache_dir,
                                          fix_antimeridian=False, ncores=1)
            par = healpix_to_geodataframe(nside, cache_mode='use', cache_dir=cache_dir,
                                          fix_antimeridian=False, ncores=3)
            assert seq.index.tolist() == par.index.tolist()
            assert all(seq.geometry.iloc[i].equals(par.geometry.iloc[i])
                       for i in range(len(seq)))


# ---------------------------------------------------------------------------
# _extract_healpix_params_from_metadata
# ---------------------------------------------------------------------------

class TestExtractHealpixParams:
    def test_none_when_empty(self):
        assert _extract_healpix_params_from_metadata({}) == {'nside': None, 'order': None, 'lon_convention': None}

    def test_extracts_explicit(self):
        meta = {
            'sidecar_metadata': {
                'healpix': {'nside': 64, 'order': 'nested'},
                'coordinates': {'lon_convention': '0_360'},
            }
        }
        r = _extract_healpix_params_from_metadata(meta)
        assert r['nside'] == 64
        assert r['order'] == 'nested'
        assert r['lon_convention'] == '0_360'

    def test_minus_plus180_normalized(self):
        meta = {
            'sidecar_metadata': {
                'coordinates': {'lon_convention': 'minus_plus180'},
            }
        }
        r = _extract_healpix_params_from_metadata(meta)
        assert r['lon_convention'] == '-180_180'

    def test_no_sidecar_key(self):
        r = _extract_healpix_params_from_metadata({'processing': {'stage': 'aggregate'}})
        assert r == {'nside': None, 'order': None, 'lon_convention': None}


# ---------------------------------------------------------------------------
# _validate_aggregate_file
# ---------------------------------------------------------------------------

class TestValidateAggregateFile:
    def test_sidecar_raises(self):
        import click
        metadata = {'processing': {'stage': 'sidecar'}}
        path = Path('/tmp/test.parquet')
        with pytest.raises(click.ClickException, match="SIDECAR"):
            _validate_aggregate_file(metadata, path)

    def test_aggregate_passes(self):
        import click
        metadata = {'processing': {'stage': 'aggregate'}}
        _validate_aggregate_file(metadata, Path('/tmp/test.parquet'))

    def test_no_metadata_passes(self):
        _validate_aggregate_file(None, Path('/tmp/test.parquet'))

    def test_unknown_stage_raises(self):
        import click
        metadata = {'processing': {'stage': 'bogus'}}
        with pytest.raises(click.ClickException, match="Unexpected processing stage"):
            _validate_aggregate_file(metadata, Path('/tmp/test.parquet'))


# ---------------------------------------------------------------------------
# save_healpix_to_geoparquet
# ---------------------------------------------------------------------------

class TestSaveHealpixToGeoparquet:
    def test_raises_when_file_exists_no_overwrite(self):
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as f:
            fname = f.name
        try:
            with pytest.raises(FileExistsError):
                save_healpix_to_geoparquet(
                    4, fname, overwrite=False, interactive=False)
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_overwrite_true(self):
        with tempfile.NamedTemporaryFile(suffix='.parquet', delete=False) as f:
            fname = f.name
        try:
            result = save_healpix_to_geoparquet(
                4, fname, overwrite=True, interactive=False)
            assert Path(result).exists()
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_returns_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out.parquet'
            result = save_healpix_to_geoparquet(4, out, overwrite=True, interactive=False)
            assert Path(result) == out


# ---------------------------------------------------------------------------
# _get_config helper
# ---------------------------------------------------------------------------

class TestGetConfig:
    def test_dict_access(self):
        assert _get_config({'key': 'val'}, 'key') == 'val'

    def test_dict_default(self):
        assert _get_config({'a': 1}, 'b', 99) == 99

    def test_namespace_access(self):
        ns = type('NS', (), {'key': 'val'})()
        assert _get_config(ns, 'key') == 'val'


# ---------------------------------------------------------------------------
# parse_arguments
# ---------------------------------------------------------------------------

class TestParseArguments:
    def test_defaults(self):
        args = parse_arguments(['-a', '/tmp/test.parquet'])
        assert args.nside is None
        assert args.order == 'nested'
        assert args.lon_convention == 'auto'

    def test_custom_values(self):
        args = parse_arguments([
            '-a', '/tmp/test.parquet', '-n', '64',
            '-O', 'ring', '--lon-convention=-180_180'
        ])
        assert args.nside == 64
        assert args.order == 'ring'
        assert args.lon_convention == '-180_180'

    def test_densify_flag(self):
        args = parse_arguments(['-a', '/tmp/test.parquet', '--densify'])
        assert args.densify is True

    def test_ncores_default(self):
        args = parse_arguments(['-a', '/tmp/test.parquet'])
        assert args.ncores == 1

    def test_ncores_custom(self):
        args = parse_arguments(['-a', '/tmp/test.parquet', '--ncores', '4'])
        assert args.ncores == 4


# ---------------------------------------------------------------------------
# Inline tests extracted from geospatial.py (re-implemented as class methods)
# ---------------------------------------------------------------------------

class TestXDGPrecedence:
    """XDG directory precedence — adapted from inline tests."""

    def test_cache_dir_precedence(self):
        orig_cache = os.environ.pop('HEALPYXEL_CACHE', None)
        orig_xdg_cache = os.environ.pop('XDG_CACHE_HOME', None)
        try:
            # CLI arg wins
            with tempfile.TemporaryDirectory() as tmp:
                result = _get_cache_dir(cli_arg=tmp)
                assert result == Path(tmp)
            # ENV wins over XDG
            with tempfile.TemporaryDirectory() as tmp:
                os.environ['HEALPYXEL_CACHE'] = tmp
                result = _get_cache_dir()
                assert result == Path(tmp)
            # XDG is respected
            with tempfile.TemporaryDirectory() as xdg_tmp:
                os.environ.pop('HEALPYXEL_CACHE', None)
                os.environ['XDG_CACHE_HOME'] = xdg_tmp
                result = _get_cache_dir()
                assert result == Path(xdg_tmp) / 'healpyxel' / 'healpix_grids'
            # CLI wins over env
            with tempfile.TemporaryDirectory() as cli_tmp, tempfile.TemporaryDirectory() as env_tmp:
                os.environ['HEALPYXEL_CACHE'] = env_tmp
                os.environ['XDG_CACHE_HOME'] = env_tmp
                result = _get_cache_dir(cli_arg=cli_tmp)
                assert result == Path(cli_tmp)
        finally:
            if orig_cache is not None:
                os.environ['HEALPYXEL_CACHE'] = orig_cache
            else:
                os.environ.pop('HEALPYXEL_CACHE', None)
            if orig_xdg_cache is not None:
                os.environ['XDG_CACHE_HOME'] = orig_xdg_cache
            else:
                os.environ.pop('XDG_CACHE_HOME', None)

    def test_config_dir_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _get_config_dir(cli_arg=tmp)
            assert result == Path(tmp)
