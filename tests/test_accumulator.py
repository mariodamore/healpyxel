"""Tests for the streaming accumulator module."""

import pytest
import numpy as np
import pandas as pd
import tempfile
import json
from pathlib import Path

from healpyxel.accumulator import (
    StreamingStats,
    CellAccumulator,
    accumulate_batch,
    save_state,
    load_state,
    state_to_dataframe,
    input_fingerprint,
    ensure_not_processed,
    validate_accumulator_sidecar_compatibility,
    find_sidecar,
    TDIGEST_AVAILABLE,
)
from healpyxel.metadata import HEALPyxelxMetadata, FileType


# ---------------------------------------------------------------------------
# StreamingStats tests
# ---------------------------------------------------------------------------

class TestStreamingStats:
    """Tests for the Welford-based StreamingStats class."""

    def test_update_scalar(self):
        stats = StreamingStats()
        stats.update(1.0)
        assert stats.n == 1
        assert stats.mean == 1.0
        assert np.isnan(stats.std)

    def test_update_list(self):
        stats = StreamingStats()
        stats.update([1.0, 2.0, 3.0])
        assert stats.n == 3
        assert np.isclose(stats.mean, 2.0)
        assert np.isclose(stats.std, np.std([1.0, 2.0, 3.0], ddof=0))

    def test_update_array(self):
        stats = StreamingStats()
        stats.update(np.array([1.0, 2.0, 3.0]))
        assert stats.n == 3
        assert np.isclose(stats.mean, 2.0)
        assert np.isclose(stats.std, np.std([1.0, 2.0, 3.0], ddof=0))

    def test_incremental_mean(self):
        """Welford mean should match batch mean exactly."""
        stats = StreamingStats()
        stats.update(1.0)
        stats.update(2.0)
        stats.update(3.0)
        assert np.isclose(stats.mean, 2.0)

    def test_incremental_std_ddof0(self):
        stats = StreamingStats()
        stats.update([1.0, 2.0, 3.0])
        expected_std = np.std([1.0, 2.0, 3.0], ddof=0)
        assert np.isclose(stats.std, expected_std)

    def test_min_max(self):
        stats = StreamingStats()
        stats.update([5.0, 1.0, 3.0, 9.0])
        assert stats.min_val == 1.0
        assert stats.max_val == 9.0

    def test_merge_two_stats(self):
        a = StreamingStats()
        b = StreamingStats()
        a.update([1.0, 2.0])
        b.update([3.0, 4.0])
        a.merge(b)
        assert a.n == 4
        assert np.isclose(a.mean, 2.5)

    def test_merge_empty(self):
        a = StreamingStats()
        a.update([1.0, 2.0])
        empty = StreamingStats()
        a.merge(empty)
        assert a.n == 2
        assert np.isclose(a.mean, 1.5)

    def test_merge_type_error(self):
        a = StreamingStats()
        with pytest.raises(TypeError):
            a.merge("not a StreamingStats")

    def test_serialize_deserialize(self):
        stats = StreamingStats()
        stats.update([1.0, 2.0, 3.0])
        d = stats.to_dict()
        restored = StreamingStats.from_dict(d)
        assert restored.n == 3
        assert np.isclose(restored.mean, 2.0)
        assert np.isclose(restored.std, np.std([1.0, 2.0, 3.0], ddof=0))

    def test_finite_filter(self):
        stats = StreamingStats()
        stats.update([1.0, np.nan, 3.0, np.inf, 5.0])
        assert stats.n == 3
        assert np.isclose(stats.mean, 3.0)

    def test_empty_input(self):
        stats = StreamingStats()
        stats.update([])
        assert stats.n == 0
        assert np.isnan(stats.mean)
        assert np.isnan(stats.std)


# ---------------------------------------------------------------------------
# CellAccumulator tests
# ---------------------------------------------------------------------------

class TestCellAccumulator:
    """Tests for the CellAccumulator class."""

    def test_single_column_update(self):
        acc = CellAccumulator(use_tdigest=False)
        acc.update("r750", np.array([1.0, 2.0, 3.0]))
        assert acc.stats_by_column["r750"].n == 3
        assert np.isclose(acc.stats_by_column["r750"].mean, 2.0)

    def test_multi_column_update(self):
        acc = CellAccumulator(use_tdigest=False)
        acc.update("r750", np.array([1.0, 2.0]))
        acc.update("r950", np.array([10.0, 20.0]))
        assert acc.stats_by_column["r750"].mean == 1.5
        assert acc.stats_by_column["r950"].mean == 15.0

    def test_tdigest_enabled(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        acc = CellAccumulator(use_tdigest=True)
        acc.update("r750", np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
        assert "r750" in acc.tdigests
        median = acc.tdigests["r750"].percentile(50)
        assert median == 3.0

    def test_tdigest_disabled(self):
        acc = CellAccumulator(use_tdigest=False)
        acc.update("r750", np.array([1.0, 2.0, 3.0]))
        assert not hasattr(acc, 'tdigests') or len(getattr(acc, 'tdigests', {})) == 0

    def test_merge_accumulators(self):
        a = CellAccumulator(use_tdigest=False)
        b = CellAccumulator(use_tdigest=False)
        a.update("r750", np.array([1.0, 2.0]))
        b.update("r750", np.array([3.0, 4.0]))
        a.merge(b)
        assert a.stats_by_column["r750"].n == 4
        assert np.isclose(a.stats_by_column["r750"].mean, 2.5)

    def test_merge_with_tdigest(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        a = CellAccumulator(use_tdigest=True)
        b = CellAccumulator(use_tdigest=True)
        a.update("r750", np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
        b.update("r750", np.array([6.0, 7.0, 8.0, 9.0, 10.0]))
        a.merge(b)
        assert a.stats_by_column["r750"].n == 10
        median = a.tdigests["r750"].percentile(50)
        assert np.isclose(median, 5.5, atol=0.1)

    def test_to_dict_roundtrip(self):
        acc = CellAccumulator(use_tdigest=False)
        acc.update("r750", np.array([1.0, 2.0, 3.0]))
        acc.update("r950", np.array([10.0, 20.0]))
        d = acc.to_dict()
        restored = CellAccumulator.from_dict(d, use_tdigest=False)
        assert restored.stats_by_column["r750"].n == 3
        assert restored.stats_by_column["r950"].mean == 15.0

    def test_to_dict_preserves_tdigest(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        acc = CellAccumulator(use_tdigest=True)
        acc.update("r750", np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
        d = acc.to_dict()
        assert "tdigests" in d
        restored = CellAccumulator.from_dict(d, use_tdigest=True)
        assert "r750" in restored.tdigests


# ---------------------------------------------------------------------------
# accumulate_batch tests
# ---------------------------------------------------------------------------

class TestAccumulateBatch:
    """Tests for the accumulate_batch pipeline function."""

    def _make_data(self, n_rows=10, cols=None, seed=None):
        cols = cols or ["r750"]
        if seed is not None:
            rng = np.random.RandomState(seed)
            values = rng.randn(n_rows, len(cols))
        else:
            values = np.random.randn(n_rows, len(cols))
        data = pd.DataFrame(values, columns=cols)
        data.index.name = "source_id"
        data.index = pd.RangeIndex(100, 100 + n_rows, name="source_id")
        return data

    def _make_sidecar(self, data, mode="fuzzy"):
        source_ids = data.index.tolist()
        if mode == "fuzzy":
            rows = []
            for sid in source_ids:
                cell_a = sid % 8
                cell_b = (sid + 1) % 8
                rows.append({"source_id": sid, "healpix_id": cell_a})
                if sid % 3 == 0:
                    rows.append({"source_id": sid, "healpix_id": cell_b})
            return pd.DataFrame(rows)
        else:
            return pd.DataFrame({
                "source_id": source_ids,
                "healpix_id": [sid % 8 for sid in source_ids],
            })

    def test_basic_accumulation(self):
        data = self._make_data(n_rows=10)
        sidecar = self._make_sidecar(data)
        state = accumulate_batch(data, sidecar, ["r750"], use_tdigest=False)
        assert len(state) > 0
        for hp_id, acc in state.items():
            assert acc.stats_by_column["r750"].n > 0

    def test_incremental_accumulation(self):
        """accumulate_batch with existing_state should merge correctly."""
        data1 = self._make_data(n_rows=10, seed=1)
        sidecar1 = self._make_sidecar(data1)
        state1 = accumulate_batch(data1, sidecar1, ["r750"], use_tdigest=False)

        data2 = self._make_data(n_rows=10, seed=2)
        sidecar2 = self._make_sidecar(data2)
        state2 = accumulate_batch(data2, sidecar2, ["r750"],
                                   existing_state=state1, use_tdigest=False)

        total_n = sum(acc.stats_by_column["r750"].n for acc in state2.values())
        assert total_n >= 20  # fuzzy mode may create additional cell mappings
        assert len(state2) >= len(state1)  # state grows or stays same

    def test_tdigest_accumulation(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        data = self._make_data(n_rows=20, cols=["r750"], seed=42)
        sidecar = self._make_sidecar(data)
        state = accumulate_batch(data, sidecar, ["r750"], use_tdigest=True)
        assert any(
            hasattr(acc, 'tdigests') and len(acc.tdigests) > 0
            for acc in state.values()
        )

    def test_empty_data_warning(self):
        data = pd.DataFrame({"r750": []})
        data.index.name = "source_id"
        sidecar = pd.DataFrame({"source_id": [], "healpix_id": []})
        state = accumulate_batch(data, sidecar, ["r750"], use_tdigest=False)
        assert state == {}

    def test_filter_expr(self):
        data = self._make_data(n_rows=20, cols=["r750"], seed=99)
        data["r750"] = data["r750"] * 100
        sidecar = self._make_sidecar(data)
        state_all = accumulate_batch(data, sidecar, ["r750"], use_tdigest=False)
        state_filtered = accumulate_batch(data, sidecar, ["r750"],
                                           filter_expr="r750 > 0", use_tdigest=False)
        total_all = sum(acc.stats_by_column["r750"].n for acc in state_all.values())
        total_filtered = sum(acc.stats_by_column["r750"].n for acc in state_filtered.values())
        assert total_filtered <= total_all


# ---------------------------------------------------------------------------
# state_to_dataframe tests
# ---------------------------------------------------------------------------

class TestStateToDataframe:
    """Tests for state serialization to DataFrame."""

    def test_healpix_id_is_column(self):
        stats = StreamingStats()
        stats.update([1.0, 2.0, 3.0])
        acc = CellAccumulator(use_tdigest=False)
        acc.stats_by_column["r750"] = stats
        state = {5: acc, 10: acc}

        df = state_to_dataframe(state, use_tdigest=False)
        assert "healpix_id" in df.columns
        assert df.index.name is None
        assert set(df["healpix_id"]) == {5, 10}

    def test_stats_json_present(self):
        stats = StreamingStats()
        stats.update([1.0, 2.0, 3.0])
        acc = CellAccumulator(use_tdigest=False)
        acc.stats_by_column["r750"] = stats
        state = {5: acc}

        df = state_to_dataframe(state, use_tdigest=False)
        assert "stats_json" in df.columns
        row = df[df["healpix_id"] == 5].iloc[0]
        parsed = json.loads(row["stats_json"])
        assert "r750" in parsed
        assert parsed["r750"]["n"] == 3


# ---------------------------------------------------------------------------
# save_state / load_state round-trip tests
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """Tests for save_state / load_state round-trip."""

    def test_save_load_roundtrip(self):
        stats = StreamingStats()
        stats.update([1.0, 2.0, 3.0, 4.0, 5.0])
        acc = CellAccumulator(use_tdigest=False)
        acc.stats_by_column["r750"] = stats
        state = {10: acc, 20: acc}

        meta = HEALPyxelxMetadata(
            nside=64, order="nested", npix=None,
            mode="fuzzy", lon_convention="0_360",
            file_type=FileType.ACCUMULATOR,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test_state.parquet"
            input_file = Path(tmpdir) / "test_input.parquet"
            input_file.write_text("dummy")
            save_state(state, path, meta=meta, input_path=input_file)

            loaded_state, loaded_meta, proc_meta = load_state(path, use_tdigest=False)

            assert len(loaded_state) == 2
            assert loaded_state[10].stats_by_column["r750"].n == 5
            assert np.isclose(loaded_state[10].stats_by_column["r750"].mean, 3.0)
            assert loaded_meta.nside == 64
            assert loaded_meta.mode == "fuzzy"
            assert "processed_inputs" in proc_meta

    def test_save_load_with_tdigest(self):
        if not TDIGEST_AVAILABLE:
            pytest.skip("tdigest package not installed")
        acc = CellAccumulator(use_tdigest=True)
        acc.update("r750", np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]))
        state = {42: acc}

        meta = HEALPyxelxMetadata(
            nside=256, order="nested", npix=None,
            mode="strict", lon_convention="0_360",
            file_type=FileType.ACCUMULATOR,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tdigest_state.parquet"
            input_file = Path(tmpdir) / "test_input.parquet"
            input_file.write_text("dummy")
            save_state(state, path, meta=meta, input_path=input_file)

            loaded_state, _, _ = load_state(path, use_tdigest=True)
            loaded_acc = loaded_state[42]
            assert "r750" in loaded_acc.tdigests
            median = loaded_acc.tdigests["r750"].percentile(50)
            assert np.isclose(median, 5.5, atol=0.5)


# ---------------------------------------------------------------------------
# Fingerprint / idempotency tests
# ---------------------------------------------------------------------------

class TestFingerprint:
    """Tests for input fingerprinting and duplicate detection."""

    def test_fingerprint_deterministic(self):
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"test data")
            fpath = f.name
        try:
            fp1 = input_fingerprint(fpath)
            fp2 = input_fingerprint(fpath)
            assert fp1 == fp2
            assert len(fp1) == 64
        finally:
            Path(fpath).unlink()

    def test_ensure_not_processed_new(self):
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"dummy")
            fpath = f.name
        try:
            meta = {}
            result = ensure_not_processed(meta, fpath)
            assert "processed_inputs" in result
            assert len(result["processed_inputs"]) == 1
        finally:
            Path(fpath).unlink()

    def test_ensure_not_processed_duplicate_error(self):
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"dummy")
            fpath = f.name
        try:
            meta = {}
            result = ensure_not_processed(meta, fpath)
            fp = result["processed_inputs"][-1]
            meta2 = {"processed_inputs": [fp]}
            with pytest.raises(ValueError, match="Duplicate"):
                ensure_not_processed(meta2, fpath)
        finally:
            Path(fpath).unlink()

    def test_ensure_not_processed_duplicate_skip(self):
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(b"dummy")
            fpath = f.name
        try:
            meta = {}
            result = ensure_not_processed(meta, fpath)
            fp = result["processed_inputs"][-1]
            meta3 = {"processed_inputs": [fp]}
            result2 = ensure_not_processed(meta3, fpath, on_duplicate="skip")
            assert "skipped_duplicate" in result2
        finally:
            Path(fpath).unlink()


# ---------------------------------------------------------------------------
# validate_accumulator_sidecar_compatibility tests
# ---------------------------------------------------------------------------

class TestValidateCompatibility:
    """Tests for state/sidecar compatibility validation."""

    def _make_meta(self, nside, mode, order, lon):
        return HEALPyxelxMetadata(
            nside=nside, order=order, npix=None,
            mode=mode, lon_convention=lon,
            file_type=FileType.ACCUMULATOR,
        )

    def test_compatible(self):
        a = self._make_meta(128, "fuzzy", "nested", "0_360")
        b = self._make_meta(128, "fuzzy", "nested", "0_360")
        result = validate_accumulator_sidecar_compatibility(a, b)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_nside_mismatch_raises(self):
        a = self._make_meta(64, "fuzzy", "nested", "0_360")
        b = self._make_meta(128, "fuzzy", "nested", "0_360")
        with pytest.raises(AssertionError, match="nside"):
            validate_accumulator_sidecar_compatibility(a, b)

    def test_mode_mismatch_raises(self):
        a = self._make_meta(128, "fuzzy", "nested", "0_360")
        b = self._make_meta(128, "strict", "nested", "0_360")
        with pytest.raises(AssertionError, match="mode"):
            validate_accumulator_sidecar_compatibility(a, b)

    def test_order_mismatch_raises(self):
        a = self._make_meta(128, "fuzzy", "nested", "0_360")
        b = self._make_meta(128, "fuzzy", "ring", "0_360")
        with pytest.raises(AssertionError, match="order"):
            validate_accumulator_sidecar_compatibility(a, b)

    def test_lon_mismatch_is_warning_not_error(self):
        """Lon convention mismatch should be a warning, not raise."""
        a = self._make_meta(128, "fuzzy", "nested", "0_360")
        b = self._make_meta(128, "fuzzy", "nested", "180_0")
        result = validate_accumulator_sidecar_compatibility(a, b)
        assert result["valid"] is True  # lon mismatch doesn't invalidate
        assert any("lon_convention" in w for w in result["warnings"])
