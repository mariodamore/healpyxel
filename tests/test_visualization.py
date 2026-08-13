import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

from healpyxel.visualization import prepare_healpix_map, _check_dependencies


class TestPrepareHealpixMap:
    """Test prepare_healpix_map with mocked heavy dependencies."""

    def _make_df(self, values, nan_indices=None):
        """Create a dense HEALPix DataFrame with optional NaN injection."""
        arr = np.array(values, dtype=float, copy=True)
        if nan_indices:
            for i in nan_indices:
                arr[i] = np.nan
        return pd.DataFrame({'data_col': arr})

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_basic_output_shape(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0, np.nan, 5.0])
        healpix_map, valid, invalid, mappable = prepare_healpix_map(
            df, output_column='data_col', equalize=False, percentile_cutoff=None
        )
        assert len(healpix_map) == 5
        assert valid.sum() == 4
        assert invalid.sum() == 1

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_all_nan_column_returns_default_mappable(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = pd.DataFrame({'data_col': np.array([np.nan, np.nan, np.nan], dtype=float)})
        healpix_map, valid, invalid, mappable = prepare_healpix_map(
            df, output_column='data_col', equalize=False, percentile_cutoff=None
        )
        assert valid.sum() == 0
        assert invalid.sum() == 3
        assert np.all(np.isnan(healpix_map))

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_outlier_clipping_reduces_max(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        m = pd.DataFrame({'data_col': np.array([1.0, 2.0, 3.0, 100.0, 5.0], dtype=float)})
        healpix_map, valid, _, _ = prepare_healpix_map(
            m, output_column='data_col', equalize=False, percentile_cutoff=5
        )
        assert np.all(np.isfinite(healpix_map[valid]))
        assert healpix_map[3] < 100.0  # outlier clipped from 100 down

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_percentile_cutoff_tuple(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        m = pd.DataFrame({'data_col': np.array(list(range(1, 11)), dtype=float)})
        prepare_healpix_map(
            m, output_column='data_col', equalize=False,
            percentile_cutoff=(10, 90)
        )

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_percentile_cutoff_none(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0, 4.0, 5.0])
        prepare_healpix_map(
            df, output_column='data_col', equalize=False, percentile_cutoff=None
        )

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_percentile_cutoff_false(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0, 4.0, 5.0])
        prepare_healpix_map(
            df, output_column='data_col', equalize=False, percentile_cutoff=False
        )

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_percentile_cutoff_invalid_lower(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(ValueError, match="must be in"):
            prepare_healpix_map(
                df, output_column='data_col', equalize=False,
                percentile_cutoff=-5
            )

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_percentile_cutoff_inverted(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(ValueError, match="lower percentile must be < upper"):
            prepare_healpix_map(
                df, output_column='data_col', equalize=False,
                percentile_cutoff=(80, 20)
            )

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_missing_column_raises(self, mock_sm, mock_norm, mock_eq):
        df = pd.DataFrame({'other': [1.0, 2.0]})
        with pytest.raises(KeyError, match="nonexistent"):
            prepare_healpix_map(df, output_column='nonexistent', equalize=False)

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_masked_array_for_invalid(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, np.nan, 4.0])
        _, valid, invalid, mappable = prepare_healpix_map(
            df, output_column='data_col', equalize=False
        )
        assert valid[2] is np._NoValue or not valid[2]
        assert invalid[2]

    @patch('healpyxel.visualization.exposure.equalize_hist')
    @patch('healpyxel.visualization.colors.Normalize')
    @patch('healpyxel.visualization.cm.ScalarMappable')
    def test_custom_cmap(self, mock_sm, mock_norm, mock_eq):
        mock_eq.side_effect = lambda x: x
        df = self._make_df([1.0, 2.0, 3.0])
        _, _, _, mappable = prepare_healpix_map(
            df, output_column='data_col', equalize=False, cmap='viridis'
        )


class TestCheckDependencies:
    """Test _check_dependencies raises for missing heavy deps."""

    def test_raise_when_matplotlib_missing(self):
        with patch.dict('healpyxel.visualization.__dict__',
                        {'MATPLOTLIB_AVAILABLE': False, 'SKIMAGE_AVAILABLE': True},
                        clear=False):
            with pytest.raises(ImportError, match="matplotlib"):
                _check_dependencies()

    def test_raise_when_skimage_missing(self):
        with patch.dict('healpyxel.visualization.__dict__',
                        {'MATPLOTLIB_AVAILABLE': True, 'SKIMAGE_AVAILABLE': False},
                        clear=False):
            with pytest.raises(ImportError, match="scikit-image"):
                _check_dependencies()

    def test_pass_when_both_available(self):
        with patch.dict('healpyxel.visualization.__dict__',
                        {'MATPLOTLIB_AVAILABLE': True, 'SKIMAGE_AVAILABLE': True},
                        clear=False):
            _check_dependencies()
