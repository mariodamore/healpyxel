import pytest
from pathlib import Path
import tempfile
import pandas as pd
import numpy as np
import json

from healpyxel.aggregate import (
    collect_sidecar_outputs,
    _is_interactive_session,
    generate_output_filename,
    extract_nside_from_filename,
    validate_sidecar_metadata,
    AGG_LOOKUP,
    aggregate_by_sidecar,
    densify_healpix_aggregates,
)

class TestAggLookup:
    """Test aggregation functions in AGG_LOOKUP."""

    def test_agg_lookup_mean(self):
        """Test mean aggregation function."""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = AGG_LOOKUP["mean"](arr)
        assert result == 3.0

    def test_agg_lookup_mean_with_nan(self):
        """Test mean with NaN values."""
        arr = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
        result = AGG_LOOKUP["mean"](arr)
        assert result == 3.0  # nanmean ignores NaNs

    def test_agg_lookup_mean_all_nan(self):
        """Test mean when all values are NaN."""
        arr = np.array([np.nan, np.nan, np.nan])
        result = AGG_LOOKUP["mean"](arr)
        assert np.isnan(result)

    def test_agg_lookup_median(self):
        """Test median aggregation function."""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = AGG_LOOKUP["median"](arr)
        assert result == 3.0

    def test_agg_lookup_std(self):
        """Test standard deviation aggregation function."""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = AGG_LOOKUP["std"](arr)
        expected = np.std(arr, ddof=0)
        assert np.isclose(result, expected)

    def test_agg_lookup_min(self):
        """Test minimum aggregation function."""
        arr = np.array([3.0, 1.0, 4.0, 2.0, 5.0])
        result = AGG_LOOKUP["min"](arr)
        assert result == 1.0

    def test_agg_lookup_max(self):
        """Test maximum aggregation function."""
        arr = np.array([3.0, 1.0, 4.0, 2.0, 5.0])
        result = AGG_LOOKUP["max"](arr)
        assert result == 5.0

    def test_agg_lookup_mad(self):
        """Test median absolute deviation."""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = AGG_LOOKUP["mad"](arr)
        # MAD of [1,2,3,4,5] is median(|1-3|, |2-3|, |3-3|, |4-3|, |5-3|) = median([2,1,0,1,2]) = 1.0
        assert result == 1.0

    def test_agg_lookup_robust_std(self):
        """Test robust standard deviation."""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = AGG_LOOKUP["robust_std"](arr)
        # robust_std = MAD * 1.4826 = 1.0 * 1.4826
        assert np.isclose(result, 1.4826)

class TestAggregateBySidecar:
    """Test the main aggregation function."""

    def test_aggregate_by_sidecar_basic(self):
        """Test basic aggregation by sidecar."""
        original = pd.DataFrame({
            "source_id": [0, 1, 2, 3],
            "value": [10.0, 20.0, 30.0, 40.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2, 3],
            "healpix_id": [1, 1, 2, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"]
        )

        assert len(result) == 2
        assert result.loc[1, "value_mean"] == 15.0
        assert result.loc[2, "value_mean"] == 35.0

    def test_aggregate_by_sidecar_multiple_aggs(self):
        """Test aggregation with multiple functions."""
        original = pd.DataFrame({
            "source_id": [0, 1, 2, 3],
            "value": [10.0, 20.0, 30.0, 40.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2, 3],
            "healpix_id": [1, 1, 2, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean", "median", "min", "max"]
        )

        assert result.loc[1, "value_mean"] == 15.0
        assert result.loc[1, "value_median"] == 15.0
        assert result.loc[1, "value_min"] == 10.0
        assert result.loc[1, "value_max"] == 20.0

    def test_aggregate_by_sidecar_multiple_value_columns(self):
        """Test aggregation with multiple value columns."""
        original = pd.DataFrame({
            "source_id": [0, 1, 2],
            "temp": [10.0, 20.0, 30.0],
            "pressure": [1000.0, 1010.0, 1020.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2],
            "healpix_id": [1, 1, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["temp", "pressure"],
            aggs=["mean"]
        )

        assert "temp_mean" in result.columns
        assert "pressure_mean" in result.columns
        assert result.loc[1, "temp_mean"] == 15.0
        assert result.loc[1, "pressure_mean"] == 1005.0

    def test_aggregate_by_sidecar_min_count(self):
        """Test min_count parameter filtering."""
        original = pd.DataFrame({
            "source_id": [0, 1, 2],
            "value": [10.0, 20.0, 30.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2],
            "healpix_id": [1, 1, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"],
            min_count=2
        )

        assert not np.isnan(result.loc[1, "value_mean"])
        assert np.isnan(result.loc[2, "value_mean"])

    def test_aggregate_by_sidecar_with_nan_values(self):
        """Test aggregation with NaN values in data."""
        original = pd.DataFrame({
            "source_id": [0, 1, 2, 3],
            "value": [10.0, np.nan, 30.0, np.nan]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2, 3],
            "healpix_id": [1, 1, 1, 1]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"]
        )

        assert result.loc[1, "value_mean"] == 20.0

    def test_aggregate_by_sidecar_invalid_agg_function(self):
        """Test error handling for invalid aggregation function."""
        original = pd.DataFrame({
            "source_id": [0, 1],
            "value": [10.0, 20.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1],
            "healpix_id": [1, 1]
        })

        with pytest.raises(ValueError, match="Invalid aggregation functions"):
            aggregate_by_sidecar(
                original, sidecar,
                value_columns=["value"],
                aggs=["invalid_agg"]
            )

    def test_aggregate_by_sidecar_missing_columns(self):
        """Test error handling when required columns are missing."""
        original = pd.DataFrame({
            "source_id": [0, 1],
            "value": [10.0, 20.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1],
        })

        with pytest.raises(KeyError):
            aggregate_by_sidecar(
                original, sidecar,
                value_columns=["value"],
                aggs=["mean"]
            )

    def test_aggregate_by_sidecar_with_duplicates_in_source_id(self):
        """Test handling of duplicate source_ids."""
        original = pd.DataFrame({
            "source_id": [0, 0, 1, 2],
            "value": [10.0, 15.0, 20.0, 30.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2],
            "healpix_id": [1, 1, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"]
        )

        assert result.loc[1, "value_mean"] == 15.0

    def test_aggregate_by_sidecar_empty_sidecar(self):
        """Test error handling when sidecar is empty."""
        original = pd.DataFrame({
            "source_id": [0, 1],
            "value": [10.0, 20.0]
        })
        sidecar = pd.DataFrame({
            "source_id": pd.Series([], dtype='int64'),
            "healpix_id": pd.Series([], dtype='int64')
        })

        with pytest.raises(ValueError, match="Sidecar is empty"):
            aggregate_by_sidecar(
                original, sidecar,
                value_columns=["value"],
                aggs=["mean"]
            )

    def test_aggregate_by_sidecar_no_overlap(self):
        """Test behavior when sidecar and original have no overlapping source_ids."""
        original = pd.DataFrame({
            "source_id": [10, 11, 12],
            "value": [10.0, 20.0, 30.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2],
            "healpix_id": [1, 1, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"]
        )

        # Should have cells from sidecar, and aggregated values should be NaN
        # (since no source_ids matched)
        assert len(result) > 0
        assert all(np.isnan(result["value_mean"]))

    def test_aggregate_by_sidecar_with_index_source_id(self):
        """Test when original DataFrame uses index as source_id."""
        original = pd.DataFrame({
            "value": [10.0, 20.0, 30.0]
        }, index=pd.Index([0, 1, 2], name="source_id"))

        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2],
            "healpix_id": [1, 1, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"]
        )

        assert len(result) == 2
        assert result.loc[1, "value_mean"] == 15.0

    def test_aggregate_by_sidecar_n_sources_column(self):
        """Test that n_sources column is correctly computed."""
        original = pd.DataFrame({
            "source_id": [0, 1, 2, 3, 4],
            "value": [10.0, 20.0, 30.0, 40.0, 50.0]
        })
        sidecar = pd.DataFrame({
            "source_id": [0, 1, 2, 3, 4],
            "healpix_id": [1, 1, 1, 2, 2]
        })

        result = aggregate_by_sidecar(
            original, sidecar,
            value_columns=["value"],
            aggs=["mean"]
        )

        assert result.loc[1, "n_sources"] == 3
        assert result.loc[2, "n_sources"] == 2

class TestDensifyHealpixAggregates:
    """Test HEALPix densification function."""

    def test_densify_basic(self):
        """Test basic densification with nside=4."""
        sparse_df = pd.DataFrame({
            "value_mean": [1.0, 2.0, 3.0]
        }, index=pd.Index([0, 5, 10], name="healpix_id"))

        result = densify_healpix_aggregates(sparse_df, nside=4)

        # nside=4 has 12*4^2 = 192 pixels
        assert len(result) == 192
        # Check that original values are preserved
        assert result.loc[0, "value_mean"] == 1.0
        assert result.loc[5, "value_mean"] == 2.0
        assert result.loc[10, "value_mean"] == 3.0
        # Check that missing values are NaN
        assert np.isnan(result.loc[1, "value_mean"])
        assert np.isnan(result.loc[100, "value_mean"])

    def test_densify_nside_8(self):
        """Test densification with nside=8."""
        sparse_df = pd.DataFrame({
            "value": [100.0, 200.0]
        }, index=pd.Index([0, 767], name="healpix_id"))

        result = densify_healpix_aggregates(sparse_df, nside=8)

        # nside=8 has 12*8^2 = 768 pixels (0 to 767)
        assert len(result) == 768
        assert result.loc[0, "value"] == 100.0
        assert result.loc[767, "value"] == 200.0

    def test_densify_multiple_columns(self):
        """Test densification with multiple value columns."""
        sparse_df = pd.DataFrame({
            "temp_mean": [20.0, 25.0],
            "pressure_mean": [1000.0, 1010.0],
            "n_sources": [5, 3]
        }, index=pd.Index([0, 10], name="healpix_id"))

        result = densify_healpix_aggregates(sparse_df, nside=4)

        assert len(result) == 192
        # Check all original values preserved
        assert result.loc[0, "temp_mean"] == 20.0
        assert result.loc[0, "pressure_mean"] == 1000.0
        assert result.loc[0, "n_sources"] == 5
        # Check NaN for missing
        assert np.isnan(result.loc[1, "temp_mean"])
        assert np.isnan(result.loc[1, "pressure_mean"])
        assert np.isnan(result.loc[1, "n_sources"])

    def test_densify_custom_healpix_col(self):
        """Test densification with custom healpix_id column name."""
        sparse_df = pd.DataFrame({
            "value": [10.0, 20.0]
        }, index=pd.Index([0, 5], name="cell_id"))

        result = densify_healpix_aggregates(
            sparse_df, nside=4, healpix_col="cell_id"
        )

        assert len(result) == 192
        assert result.index.name == "cell_id"

    def test_densify_preserves_dtypes(self):
        """Test that densification preserves data types where possible."""
        sparse_df = pd.DataFrame({
            "value_int": [1, 2],
            "value_float": [1.5, 2.5]
        }, index=pd.Index([0, 10], name="healpix_id"))

        result = densify_healpix_aggregates(sparse_df, nside=4)

        # Check that original values have correct types
        assert result.loc[0, "value_int"] == 1
        assert isinstance(result.loc[0, "value_float"], float)

class TestGenerateOutputFilename:
    """Test output filename generation."""

    def test_generate_output_filename_basic(self):
        """Test basic filename generation."""
        input_file = Path("data/sample.parquet")
        sidecar_file = Path("data/sample.cell-healpix_assignment-strict_nside-64_order-nested.parquet")

        result = generate_output_filename(input_file, sidecar_file)
        assert result.name == "sample-aggregated.cell-healpix_assignment-strict_nside-64_order-nested.parquet"

    def test_generate_output_filename_with_densified(self):
        """Test filename generation with densification marker."""
        input_file = Path("sample.parquet")
        sidecar_file = Path("sample.cell-healpix_assignment-fuzzy_nside-128_order-nested.parquet")

        result = generate_output_filename(input_file, sidecar_file, densified=True)
        assert "densified" in result.name
        assert result.name == "sample-aggregated-densified.cell-healpix_assignment-fuzzy_nside-128_order-nested.parquet"

    def test_generate_output_filename_custom_output_dir(self):
        """Test filename generation with custom output directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            output_dir = tmp / "output"
            output_dir.mkdir()

            input_file = Path("sample.parquet")
            sidecar_file = Path("sample.cell-healpix_assignment-strict_nside-32_order-nested.parquet")

            result = generate_output_filename(input_file, sidecar_file, output_dir=output_dir)
            assert result.parent == output_dir
            assert result.name == "sample-aggregated.cell-healpix_assignment-strict_nside-32_order-nested.parquet"

class TestExtractNsideFromFilename:
    """Test nside extraction from filenames."""

    def test_extract_nside_from_filename_basic(self):
        """Test basic nside extraction."""
        filename = "data.cell-healpix_assignment-strict_nside-64_order-nested.parquet"
        result = extract_nside_from_filename(filename)
        assert result == 64

    def test_extract_nside_from_filename_fuzzy(self):
        """Test extraction with fuzzy mode."""
        filename = "sample.cell-healpix_assignment-fuzzy_nside-128_order-nested.parquet"
        result = extract_nside_from_filename(filename)
        assert result == 128

    def test_extract_nside_from_filename_missing(self):
        """Test extraction when nside not found."""
        filename = "sample-no-nside-info.parquet"
        result = extract_nside_from_filename(filename)
        assert result is None

    def test_extract_nside_from_filename_ambiguous(self):
        """Test extraction with ambiguous nside (multiple values)."""
        filename = "data_nside-64_nside-128.parquet"
        result = extract_nside_from_filename(filename)
        assert result is None

class TestValidateSidecarMetadata:
    """Test sidecar metadata validation."""

    def test_validate_sidecar_metadata_not_found_lenient(self):
        """Test lenient mode when metadata file missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sidecar_path = tmp / "sample.sidecar.parquet"
            input_file = tmp / "sample.parquet"

            sidecar_path.touch()
            input_file.touch()

            result = validate_sidecar_metadata(sidecar_path, input_file, require_metadata=False)
            assert result == {}

    def test_validate_sidecar_metadata_not_found_strict(self):
        """Test strict mode when metadata file missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sidecar_path = tmp / "sample.sidecar.parquet"
            input_file = tmp / "sample.parquet"

            sidecar_path.touch()
            input_file.touch()

            with pytest.raises(FileNotFoundError):
                validate_sidecar_metadata(sidecar_path, input_file, require_metadata=True)

    def test_validate_sidecar_metadata_valid(self):
        """Test validation with valid metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sidecar_path = tmp / "sample.sidecar.parquet"
            input_file = tmp / "sample.parquet"
            metadata_path = tmp / "sample.sidecar.meta.json"

            sidecar_path.touch()
            input_file.touch()

            metadata = {
                "processing": {
                    "source_file": "sample.parquet",
                    "nside": 64
                }
            }
            metadata_path.write_text(json.dumps(metadata))

            result = validate_sidecar_metadata(sidecar_path, input_file, require_metadata=True)
            assert result["processing"]["nside"] == 64

    def test_validate_sidecar_metadata_source_mismatch(self):
        """Test validation when source file names don't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sidecar_path = tmp / "sample.sidecar.parquet"
            input_file = tmp / "sample.parquet"
            metadata_path = tmp / "sample.sidecar.meta.json"

            sidecar_path.touch()
            input_file.touch()

            metadata = {
                "processing": {
                    "source_file": "wrong_sample.parquet",
                    "nside": 64
                }
            }
            metadata_path.write_text(json.dumps(metadata))

            with pytest.raises(ValueError, match="Source file mismatch"):
                validate_sidecar_metadata(sidecar_path, input_file, require_metadata=True)

class TestCollectSidecarOutputs:
    """Test sidecar discovery and parsing."""

    def test_collect_sidecar_outputs_basic(self):
        """Test basic sidecar discovery in temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_path = tmp / "sample.parquet"
            sidecar_path = tmp / "sample.cell-healpix_assignment-fuzzy_nside-4_order-nested.parquet"

            pd.DataFrame({"a": [1]}).to_parquet(input_path, index=False)
            pd.DataFrame({"source_id": [0], "healpix_id": [1]}).to_parquet(sidecar_path, index=False)

            df = collect_sidecar_outputs(input_path, tmp, read_stats=False)
            assert len(df) == 1
            assert Path(df.iloc[0]["file"]).name == sidecar_path.name

    def test_collect_sidecar_outputs_multiple_sidecars(self):
        """Test collection with multiple sidecar files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_path = tmp / "sample.parquet"
            sidecar1 = tmp / "sample.cell-healpix_assignment-strict_nside-64_order-nested.parquet"
            sidecar2 = tmp / "sample.cell-healpix_assignment-fuzzy_nside-128_order-nested.parquet"

            pd.DataFrame({"a": [1]}).to_parquet(input_path, index=False)
            pd.DataFrame({"source_id": [0], "healpix_id": [1]}).to_parquet(sidecar1, index=False)
            pd.DataFrame({"source_id": [0], "healpix_id": [2]}).to_parquet(sidecar2, index=False)

            df = collect_sidecar_outputs(input_path, tmp, read_stats=False)
            assert len(df) == 2
            files = {Path(row["file"]).name for idx, row in df.iterrows()}
            assert sidecar1.name in files
            assert sidecar2.name in files

    def test_collect_sidecar_outputs_with_stats(self):
        """Test sidecar collection with stats data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_path = tmp / "sample.parquet"
            sidecar_path = tmp / "sample.cell-healpix_assignment-strict_nside-32_order-nested.parquet"

            pd.DataFrame({"a": [1, 2, 3]}).to_parquet(input_path, index=False)
            pd.DataFrame({
                "source_id": [0, 1, 2],
                "healpix_id": [1, 1, 2],
                "weight": [1.0, 0.5, 1.0]
            }).to_parquet(sidecar_path, index=False)

            df = collect_sidecar_outputs(input_path, tmp, read_stats=True)
            assert len(df) == 1
            assert "nside" in df.columns
            assert df.iloc[0]["nside"] == 32

    def test_collect_sidecar_outputs_no_matches(self):
        """Test behavior when no sidecar files match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_path = tmp / "sample.parquet"

            pd.DataFrame({"a": [1]}).to_parquet(input_path, index=False)

            df = collect_sidecar_outputs(input_path, tmp, read_stats=False)
            assert len(df) == 0

    def test_collect_sidecar_outputs_filters_by_input_name(self):
        """Test that collection only matches sidecars for the given input file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input1 = tmp / "sample1.parquet"
            input2 = tmp / "sample2.parquet"
            sidecar1 = tmp / "sample1.cell-healpix_assignment-strict_nside-64_order-nested.parquet"
            sidecar2 = tmp / "sample2.cell-healpix_assignment-strict_nside-64_order-nested.parquet"

            for f in [input1, input2, sidecar1, sidecar2]:
                if f.name.startswith("sample"):
                    pd.DataFrame({"a": [1]}).to_parquet(f, index=False)

            df1 = collect_sidecar_outputs(input1, tmp, read_stats=False)
            assert len(df1) == 1
            assert sidecar1.name in df1.iloc[0]["file"]

            df2 = collect_sidecar_outputs(input2, tmp, read_stats=False)
            assert len(df2) == 1
            assert sidecar2.name in df2.iloc[0]["file"]

class TestInteractiveSession:
    """Test interactive session detection."""

    def test_is_interactive_session_returns_bool(self):
        """Test that _is_interactive_session returns a boolean."""
        result = _is_interactive_session()
        assert isinstance(result, bool)

    def test_is_interactive_session_in_pytest(self):
        """Test that _is_interactive_session detects we're in IPython/Jupyter context."""
        result = _is_interactive_session()
        assert isinstance(result, bool)

    def test_is_interactive_session_consistency(self):
        """Test that repeated calls return consistent results."""
        result1 = _is_interactive_session()
        result2 = _is_interactive_session()
        assert result1 == result2
