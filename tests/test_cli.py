import pytest
import pandas as pd
import numpy as np

from healpyxel.cli import (
    validate_lon_lat_columns, sidecar_cli, aggregate_cli, accumulator_cli,
    finalize_cli, to_geoparquet_cli,
)


class TestValidateLonLatColumns:
    """Test validate_lon_lat_columns in healpyxel.cli."""

    def _make_df(self, cols):
        return pd.DataFrame({c: np.zeros(5) for c in cols})

    def test_both_columns_provided_valid(self):
        df = self._make_df(['lon', 'lat', 'other'])
        result = validate_lon_lat_columns(df, 'lon', 'lat', 'sidecar', 'test.parquet')
        assert result == ('lon', 'lat')

    def test_both_provided_missing_lon(self):
        df = self._make_df(['lat', 'other'])
        with pytest.raises(ValueError, match="Specified columns not found"):
            validate_lon_lat_columns(df, 'lon', 'lat', 'sidecar', 'test.parquet')

    def test_both_provided_missing_lat(self):
        df = self._make_df(['lon', 'other'])
        with pytest.raises(ValueError, match="Specified columns not found"):
            validate_lon_lat_columns(df, 'lon', 'lat', 'sidecar', 'test.parquet')

    def test_auto_detect_both_found(self):
        df = self._make_df(['longitude', 'latitude', 'data'])
        result = validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')
        assert result == ('longitude', 'latitude')

    def test_auto_detect_lon_only(self):
        df = self._make_df(['longitude', 'data'])
        with pytest.raises(ValueError, match="Could not auto-detect"):
            validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')

    def test_auto_detect_lat_only(self):
        df = self._make_df(['latitude', 'data'])
        with pytest.raises(ValueError, match="Could not auto-detect"):
            validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')

    def test_auto_detect_none_found(self):
        df = self._make_df(['a', 'b', 'c'])
        with pytest.raises(ValueError, match="Could not auto-detect"):
            validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')

    def test_auto_detect_spot_names(self):
        df = self._make_df(['spot_lon', 'spot_lat', 'data'])
        result = validate_lon_lat_columns(df, None, None, 'accumulator', 'in.parquet')
        assert result == ('spot_lon', 'spot_lat')

    def test_error_includes_mode(self):
        df = self._make_df(['x'])
        with pytest.raises(ValueError, match="SIDECAR MODE"):
            validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')

    def test_error_includes_mode_lowercase(self):
        df = self._make_df(['a'])
        with pytest.raises(ValueError, match="ACCUMULATOR MODE"):
            validate_lon_lat_columns(df, None, None, 'accumulator', 'in.parquet')

    def test_error_includes_available_columns(self):
        df = self._make_df(['q', 'r'])
        with pytest.raises(ValueError, match=r"Available columns:.*q.*r"):
            validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')

    def test_auto_detect_single_named_column_only(self):
        """With only 'x', auto-detect finds lon but no lat -> raises."""
        df = pd.DataFrame({'x': np.zeros(5)})
        with pytest.raises(ValueError, match="Could not auto-detect"):
            validate_lon_lat_columns(df, None, None, 'sidecar', 'test.parquet')


class TestCliEntryPoints:
    """Test CLI entry point wrappers import their submodules correctly.

    Each entry point calls parse_arguments(argv) and run(args) from a
    submodule. We test that the import and dispatch works by invoking
    with no args (which triggers argparse help/sys.exit).
    """

    def test_sidecar_cli_body_runs(self):
        """sidecar_cli executes without import errors."""
        import sys
        original_exit = sys.exit
        exit_results = []
        sys.exit = lambda code=0: exit_results.append(code) or original_exit(code)
        try:
            # Running with no args causes argparse error (exit 2)
            from healpyxel.cli import sidecar_cli
            sidecar_cli()
        except SystemExit as e:
            pass  # Expected for argparse
        finally:
            sys.exit = original_exit
        # argparse returns exit code 2 for no-args
        assert exit_results == [2]

    def test_aggregate_cli_body_runs(self):
        """aggregate_cli executes without import errors."""
        import sys
        original_exit = sys.exit
        exit_results = []
        sys.exit = lambda code=0: exit_results.append(code) or original_exit(code)
        try:
            from healpyxel.cli import aggregate_cli
            aggregate_cli()
        except SystemExit as e:
            pass
        finally:
            sys.exit = original_exit
        assert exit_results == [2]

    def test_accumulator_cli_body_runs(self):
        """accumulator_cli executes without import errors."""
        import sys
        original_exit = sys.exit
        exit_results = []
        sys.exit = lambda code=0: exit_results.append(code) or original_exit(code)
        try:
            from healpyxel.cli import accumulator_cli
            accumulator_cli()
        except SystemExit as e:
            pass
        finally:
            sys.exit = original_exit
        assert exit_results == [2]

    def test_finalize_cli_body_runs(self):
        """finalize_cli executes without import errors."""
        import sys
        original_exit = sys.exit
        exit_results = []
        sys.exit = lambda code=0: exit_results.append(code) or original_exit(code)
        try:
            from healpyxel.cli import finalize_cli
            finalize_cli()
        except SystemExit as e:
            pass
        finally:
            sys.exit = original_exit
        assert exit_results == [2]

    def test_to_geoparquet_cli_body_runs(self):
        """to_geoparquet_cli executes without import errors."""
        import sys
        original_exit = sys.exit
        exit_results = []
        sys.exit = lambda code=0: exit_results.append(code) or original_exit(code)
        try:
            from healpyxel.cli import to_geoparquet_cli
            to_geoparquet_cli()
        except SystemExit as e:
            pass
        finally:
            sys.exit = original_exit
        assert exit_results == [2]
