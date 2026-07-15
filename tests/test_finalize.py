"""Tests for the finalize module."""

import pytest
import numpy as np
import pandas as pd
import tempfile
from pathlib import Path

from healpyxel.finalize import (
    finalize_statistics,
    densify_healpix_map,
    _normalize_load_state_result,
)
from healpyxel.accumulator import (
    StreamingStats,
    CellAccumulator,
    accumulate_batch,
    save_state,
    load_state,
    TDIGEST_AVAILABLE,
)
from healpyxel.metadata import HEALPyxelxMetadata, FileType


# ---------------------------------------------------------------------------
# finalize_statistics tests
# ---------------------------------------------------------------------------

class TestFinalizeStatistics:
    """Tests for finalize_statistics()."""

    def _make_state(self, data_dict):
        """Build a state dict from {hp_id: {col: [values]}}."""
        state = {}
        for hp_id, col_vals in data_dict.items():
            acc = CellAccumulator(use_tdigest=False)
            for col, vals in col_vals.items():
                acc.update(col, np.array(vals))
            state[hp_id] = acc
        return state

    def test_basic_welford_stats(self):
        state = self._make_state({
            0: {"r750": [1.0, 2.0, 3.0]},
            1: {"r750": [10.0, 20.0]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=1)

        assert len(df) == 2
        assert df.loc[0, "r750_n"] == 3
        assert np.isclose(df.loc[0, "r750_mean"], 2.0)
        assert np.isclose(df.loc[0, "r750_std"], np.std([1.0, 2.0, 3.0], ddof=0))
        assert np.isclose(df.loc[0, "r750_min"], 1.0)
        assert np.isclose(df.loc[0, "r750_max"], 3.0)
        assert df.loc[1, "r750_mean"] == 15.0

    def test_min_count_filters(self):
        state = self._make_state({
            0: {"r750": [1.0, 2.0, 3.0]},
            1: {"r750": [10.0]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=2)

        assert not np.isnan(df.loc[0, "r750_mean"])
        assert np.isnan(df.loc[1, "r750_mean"])
        assert np.isnan(df.loc[1, "r750_std"])

    def test_single_cell_single_obs(self):
        state = self._make_state({
            5: {"r750": [42.0]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=1)
        assert df.loc[5, "r750_mean"] == 42.0
        assert np.isnan(df.loc[5, "r750_std"])

    def test_no_percentiles_no_tdigest(self):
        state = self._make_state({
            0: {"r750": [1.0, 2.0, 3.0]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=1)
        assert "r750_p50" not in df.columns

    def test_percentiles_with_tdigest(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        data = pd.DataFrame({"r750": np.array([1.0, 2.0, 3.0, 4.0, 5.0])})
        data.index = pd.RangeIndex(100, 105, name="source_id")
        sidecar = pd.DataFrame({
            "source_id": data.index.tolist(),
            "healpix_id": [0] * 5,
        })
        state = accumulate_batch(data, sidecar, ["r750"], use_tdigest=True)

        df = finalize_statistics(state, percentiles=[25, 50, 75], min_count=1)

        assert "r750_p25" in df.columns
        assert "r750_p50" in df.columns
        assert "r750_p75" in df.columns
        assert np.isclose(df.loc[0, "r750_p50"], 3.0, atol=0.1)

    def test_has_tdigest_detection(self):
        """The has_tdigest check in finalize.py should correctly find tdigest data."""
        data = pd.DataFrame({"r750": np.array([1.0, 2.0, 3.0, 4.0, 5.0])})
        data.index = pd.RangeIndex(100, 105, name="source_id")
        sidecar = pd.DataFrame({
            "source_id": data.index.tolist(),
            "healpix_id": [0] * 5,
        })

        state_with = accumulate_batch(data, sidecar, ["r750"], use_tdigest=True)
        has_t = any(
            hasattr(acc, 'tdigests') and len(acc.tdigests) > 0
            for acc in state_with.values()
        )
        assert has_t is True

        state_without = accumulate_batch(data, sidecar, ["r750"], use_tdigest=False)
        has_f = any(
            hasattr(acc, 'tdigests') and len(acc.tdigests) > 0
            for acc in state_without.values()
        )
        assert has_f is False

    def test_indexed_by_healpix_id(self):
        state = self._make_state({
            0: {"r750": [1.0]},
            2: {"r750": [2.0]},
            1: {"r750": [3.0]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=1)
        assert df.index.name == "healpix_id"
        assert list(df.index) == [0, 1, 2]

    def test_empty_state(self):
        df = finalize_statistics({}, percentiles=[], min_count=1)
        assert len(df) == 0
        assert df.index.name == "healpix_id"

    def test_percentiles_nan_when_no_tdigest(self):
        """When tdigest is not available, percentile columns should be NaN."""
        state = self._make_state({
            0: {"r750": [1.0, 2.0, 3.0, 4.0, 5.0]},
        })
        df = finalize_statistics(state, percentiles=[25, 50, 75], min_count=1)
        assert "r750_p25" in df.columns
        assert np.isnan(df.loc[0, "r750_p25"])
        assert np.isnan(df.loc[0, "r750_p50"])
        assert np.isnan(df.loc[0, "r750_p75"])

    def test_nan_values_filtered(self):
        """All-NaN values are filtered by StreamingStats.update(), so n=0."""
        state = self._make_state({
            0: {"r750": [np.nan, np.nan, np.nan]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=1)
        # NaN values are filtered during update(), so n=0
        assert df.loc[0, "r750_n"] == 0
        assert np.isnan(df.loc[0, "r750_mean"])

    def test_multi_column(self):
        state = self._make_state({
            0: {"r750": [1.0, 2.0], "r950": [10.0, 20.0, 30.0]},
        })
        df = finalize_statistics(state, percentiles=[], min_count=1)
        assert df.loc[0, "r750_n"] == 2
        assert df.loc[0, "r950_n"] == 3
        assert np.isclose(df.loc[0, "r750_mean"], 1.5)
        assert np.isclose(df.loc[0, "r950_mean"], 20.0)


# ---------------------------------------------------------------------------
# densify_healpix_map tests
# ---------------------------------------------------------------------------

class TestDensifyHealpixMap:
    """Tests for densify_healpix_map()."""

    def test_densify_preserves_existing_values(self):
        """Existing cell values should be preserved after densification."""
        df = pd.DataFrame({
            "r750_mean": [1.0, np.nan, 3.0],
            "r750_n": [10, 5, 20],
        }, index=pd.Index([0, 5, 10], name="healpix_id"))

        dense = densify_healpix_map(df, nside=8)  # 768 cells

        assert len(dense) == 768
        assert dense.index.name == "healpix_id"
        assert np.isclose(dense.loc[0, "r750_mean"], 1.0)  # preserved
        assert np.isclose(dense.loc[10, "r750_mean"], 3.0)  # preserved
        assert np.isnan(dense.loc[5, "r750_mean"])  # was NaN in input
        assert np.isnan(dense.loc[1, "r750_mean"])  # new cell (was missing)
        assert np.isnan(dense.loc[767, "r750_mean"])  # new cell (was missing)

    def test_densify_full_grid_size(self):
        df = pd.DataFrame({
            "val": [1.0],
        }, index=pd.Index([0], name="healpix_id"))

        dense = densify_healpix_map(df, nside=4)  # 192 cells
        assert len(dense) == 192

    def test_densify_nside8(self):
        df = pd.DataFrame({
            "val": [1.0, 2.0],
        }, index=pd.Index([0, 767], name="healpix_id"))

        dense = densify_healpix_map(df, nside=8)
        assert len(dense) == 768
        assert dense.loc[0, "val"] == 1.0
        assert dense.loc[767, "val"] == 2.0
        assert np.isnan(dense.loc[384, "val"])

    def test_densify_preserves_index_name(self):
        df = pd.DataFrame({"x": [1.0]}, index=pd.Index([7], name="healpix_id"))
        dense = densify_healpix_map(df, nside=4)
        assert dense.index.name == "healpix_id"


# ---------------------------------------------------------------------------
# _normalize_load_state_result tests
# ---------------------------------------------------------------------------

class TestNormalizeLoadStateResult:
    """Tests for _normalize_load_state_result backward-compat helper."""

    def test_2_tuple(self):
        state = {1: "dummy"}
        result = _normalize_load_state_result((state, "meta"))
        assert result == (state, "meta")

    def test_3_tuple(self):
        """load_state returns (state, meta, processing_metadata) — must extract state+meta."""
        state = {1: "dummy"}
        meta = "meta"
        proc = {"processed_inputs": []}
        result = _normalize_load_state_result((state, meta, proc))
        assert result == (state, meta)

    def test_bare_value(self):
        state = {1: "dummy"}
        result = _normalize_load_state_result(state)
        assert result == (state, None)


# ---------------------------------------------------------------------------
# Integration: save -> load -> finalize pipeline
# ---------------------------------------------------------------------------

class TestSaveLoadFinalizePipeline:
    """End-to-end test: accumulate -> save -> load -> finalize."""

    def test_full_pipeline_welford_only(self):
        np.random.seed(42)
        data = pd.DataFrame({"r750": np.random.randn(50)})
        data.index = pd.RangeIndex(0, 50, name="source_id")

        sidecar = pd.DataFrame({
            "source_id": data.index.tolist(),
            "healpix_id": (data.index % 8).tolist(),
        })

        state = accumulate_batch(data, sidecar, ["r750"], use_tdigest=False)

        meta = HEALPyxelxMetadata(
            nside=64, order="nested", npix=None,
            mode="strict", lon_convention="0_360",
            file_type=FileType.ACCUMULATOR,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pipeline_state.parquet"
            input_file = Path(tmpdir) / "input.parquet"
            input_file.write_text("dummy")
            save_state(state, path, meta=meta, input_path=input_file)

            loaded_state, loaded_meta, _ = load_state(path, use_tdigest=False)
            df = finalize_statistics(loaded_state, percentiles=[], min_count=1)

            assert len(df) == 8
            total_obs = df["r750_n"].sum()
            assert total_obs == 50

    def test_full_pipeline_with_tdigest(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        np.random.seed(42)
        data = pd.DataFrame({"r750": np.random.randn(100)})
        data.index = pd.RangeIndex(0, 100, name="source_id")

        sidecar = pd.DataFrame({
            "source_id": data.index.tolist(),
            "healpix_id": (data.index % 16).tolist(),
        })

        state = accumulate_batch(data, sidecar, ["r750"], use_tdigest=True)

        meta = HEALPyxelxMetadata(
            nside=128, order="nested", npix=None,
            mode="strict", lon_convention="0_360",
            file_type=FileType.ACCUMULATOR,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tdigest_pipeline.parquet"
            input_file = Path(tmpdir) / "input.parquet"
            input_file.write_text("dummy")
            save_state(state, path, meta=meta, input_path=input_file)

            loaded_state, _, _ = load_state(path, use_tdigest=True)
            df = finalize_statistics(loaded_state, percentiles=[25, 50, 75], min_count=1)

            assert "r750_p25" in df.columns
            assert "r750_p50" in df.columns
            assert "r750_p75" in df.columns
            valid_p50 = df["r750_p50"].dropna()
            if len(valid_p50) > 0:
                assert np.abs(valid_p50.mean()) < 0.5
