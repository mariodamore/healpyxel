"""Tests for the pipeline wrapper (healpyxel.workflow) and aggregate --nside selection.

Covers the regression where ``run_pipeline`` aggregated/skipped the wrong HEALPix
resolutions when ``nsides`` were given in a non-ascending order. The fix selects the
sidecar by ``--nside`` (rather than a positional ``--sidecar-index``) and adds pre/post
guards so a wrong resolution fails loudly instead of silently producing the wrong output.
"""

import io
import json
from pathlib import Path

import pytest

from healpyxel.workflow import (
    _build_aggregate_cmd,
    _build_geoparquet_cmd,
    _aggregate_meta_exists,
    save_script,
    run_pipeline,
)
from healpyxel.aggregate import collect_sidecar_outputs, parse_arguments, run as agg_run


# ── unit: command builders / skip logic ──────────────────────────

def test_build_aggregate_cmd_emits_nside(tmp_path):
    """Aggregate commands must select the sidecar by --nside, not a positional index."""
    input_ = tmp_path / "in.parquet"
    out = tmp_path / "out"
    kw = {"aggregate": True, "columns": ("r750",), "aggs": ("median",)}
    cmd = _build_aggregate_cmd(input_, out, 256, kw)
    assert "--nside" in cmd
    assert "256" in cmd
    assert "--sidecar-index" not in cmd
    assert "healpyxel_aggregate" in cmd


def test_save_script_emits_nside(tmp_path):
    """The reproducible bash script must use --nside for each aggregate command."""
    input_ = tmp_path / "in.parquet"
    input_.touch()
    out = tmp_path / "out"
    out.mkdir()
    buf = io.StringIO()
    save_script(
        input_,
        out,
        nsides=(128, 64, 32),
        columns=("r750", "r950"),
        aggs=("median", "std"),
        output=buf,
    )
    script = buf.getvalue()
    assert "--sidecar-index" not in script
    # One aggregate command per nside, each selecting by --nside.
    assert script.count("healpyxel_aggregate") == 3
    assert script.count("--nside") >= 3
    for n in (128, 64, 32):
        assert f"# ── healpyxel aggregate (nside={n}) ──" in script


def test_build_geoparquet_cmd_emits_ncores(tmp_path):
    """GeoParquet commands must forward ncores to the polygon builder."""
    agg = tmp_path / "agg.parquet"
    kw = {"ncores": 4, "output_suffix": ".geo", "chunk_size": 65536}
    cmd = _build_geoparquet_cmd(agg, kw)
    assert "--ncores" in cmd
    assert "4" in cmd


def test_build_geoparquet_cmd_omits_ncores_when_none(tmp_path):
    """When ncores is None (unset), no --ncores flag should be emitted."""
    agg = tmp_path / "agg.parquet"
    cmd = _build_geoparquet_cmd(agg, {"output_suffix": ".geo"})
    assert "--ncores" not in cmd


def test_save_script_emits_geoparquet_ncores(tmp_path):
    """The reproducible bash script must pass --ncores to GeoParquet commands."""
    input_ = tmp_path / "in.parquet"
    input_.touch()
    out = tmp_path / "out"
    out.mkdir()
    buf = io.StringIO()
    save_script(
        input_,
        out,
        nsides=(64, 32),
        ncores=4,
        columns=("r750",),
        aggs=("median",),
        output=buf,
    )
    script = buf.getvalue()
    # Each geoparquet command should carry --ncores 4.
    assert script.count("healpyxel_to_geoparquet") == 2
    assert script.count("--ncores") >= 2
    assert "--ncores" in script


def _write_agg_meta(tmp_path, name, nside, mode, out_file="agg.parquet"):
    meta = {
        "processing": {"stage": "aggregate", "output_file": str(tmp_path / out_file),
                       "sidecar_file": "sidecar.parquet"},
        "sidecar_metadata": {"healpix": {"nside": nside, "mode": mode}},
        "_legacy": {"healpix_nside": str(nside), "healpix_mode": mode},
    }
    meta_path = tmp_path / name
    meta_path.write_text(json.dumps(meta))
    (tmp_path / out_file).write_bytes(b"placeholder")
    return meta_path


def test_aggregate_meta_exists_matches_mode(tmp_path):
    """Skip detection must also match the processing mode, not just nside."""
    # Same nside, different mode -> only the matching-mode aggregate should be found.
    _write_agg_meta(tmp_path, "fuzzy.meta.json", nside=32, mode="fuzzy", out_file="fuzzy.parquet")
    _write_agg_meta(tmp_path, "strict.meta.json", nside=32, mode="strict", out_file="strict.parquet")

    assert _aggregate_meta_exists(tmp_path / "in.parquet", tmp_path, 32,
                                  "sidecar.parquet", mode="fuzzy") is True
    assert _aggregate_meta_exists(tmp_path / "in.parquet", tmp_path, 32,
                                  "sidecar.parquet", mode="strict") is True
    # No aggregate recorded with mode='ring' for nside 32 -> should be False.
    assert _aggregate_meta_exists(tmp_path / "in.parquet", tmp_path, 32,
                                  "sidecar.parquet", mode="ring") is False


# ── aggregate --nside selection (uses pre-built derived sidecars) ─

def test_collect_sidecar_outputs_sorted_by_nside(derived_dir, sample_50k_path):
    """Sidecars are discovered sorted by nside ascending; index map is by nside."""
    df = collect_sidecar_outputs(sample_50k_path, derived_dir, read_stats=False)
    nsides = [int(n) for n in df["nside"].dropna().tolist()]
    assert nsides == sorted(nsides), f"sidecars not sorted ascending: {nsides}"
    index_map = {int(row.nside): i for i, row in df.iterrows() if row.nside is not None}
    assert index_map == {32: 0, 64: 1}


def test_aggregate_nside_resolves_correct_sidecar(derived_dir, sample_50k_path, tmp_path):
    """Aggregating with --nside 64 must produce an nside-64 aggregate."""
    args = parse_arguments([
        "-i", str(sample_50k_path),
        "--sidecar-dir", str(derived_dir),
        "--output", str(tmp_path),
        "--nside", "64",
        "--aggregate",
        "--columns", "r750", "r950",
        "--aggs", "median", "std",
        "-y",
    ])
    agg_run(args)
    written = [p.name for p in tmp_path.glob("*.parquet")]
    assert any("nside-64" in n for n in written), written


def test_aggregate_nside_missing_raises(derived_dir, sample_50k_path, tmp_path):
    """Requesting a resolution with no sidecar must fail loudly."""
    args = parse_arguments([
        "-i", str(sample_50k_path),
        "--sidecar-dir", str(derived_dir),
        "--output", str(tmp_path),
        "--nside", "999",
        "--aggregate",
        "--columns", "r750",
        "-y",
    ])
    with pytest.raises(RuntimeError, match="nside=999 not found"):
        agg_run(args)


# ── end-to-end pipeline (regression for the original bug) ────────

@pytest.fixture(scope="module")
def pipeline_kwargs():
    return dict(
        mode="fuzzy",
        body_model="sphere",
        ncores=4,
        columns=("r750", "r950"),
        aggs=("median", "std"),
        use_duckdb=True,
    )


def test_run_pipeline_descending_nsides_all_outputs(samples_dir, tmp_path, pipeline_kwargs):
    """Descending nsides must still produce aggregates + GeoParquet for every nside."""
    input_path = samples_dir / "sample_5k.parquet"
    results = run_pipeline(
        input=input_path,
        output_dir=tmp_path,
        nsides=(128, 64, 32),  # intentionally non-ascending
        **pipeline_kwargs,
    )
    assert not [r for r in results if r["status"] == "error"], results
    for n in (128, 64, 32):
        assert any(r["stage"] == "aggregate" and r["nside"] == n and r["status"] == "ok"
                   for r in results), f"aggregate nside={n} not ok: {results}"
        assert any(r["stage"] == "geoparquet" and r["nside"] == n and r["status"] == "ok"
                   for r in results), f"geoparquet nside={n} not ok: {results}"
        assert list(tmp_path.glob(f"*-aggregated*_nside-{n}_order-*.parquet")), f"nside={n} agg file"
        assert list(tmp_path.glob(f"*-aggregated*_nside-{n}_order-*.geo.parquet")), f"nside={n} geo file"


def test_run_pipeline_second_run_skips_not_errors(samples_dir, tmp_path, pipeline_kwargs):
    """Re-running on an existing output dir skips completed stages without errors."""
    input_path = samples_dir / "sample_5k.parquet"
    run_pipeline(input=input_path, output_dir=tmp_path, nsides=(64, 32), **pipeline_kwargs)
    results = run_pipeline(input=input_path, output_dir=tmp_path, nsides=(32, 64),
                           **pipeline_kwargs)  # reversed order
    assert not [r for r in results if r["status"] == "error"], results
    for r in results:
        if r["stage"] in ("aggregate", "geoparquet"):
            assert r["status"] == "skip", r
