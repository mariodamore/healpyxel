"""Pipeline wrapper: Sidecar → Aggregate → GeoParquet.

Exposes :func:`run_pipeline` for programmatic workflow execution and
:func:`save_script` for reproducible bash output generation.

The wrapper orchestrates the three core stages:

1. **Sidecar** — spatial join of input observations onto HEALPix cells
2. **Aggregate** — per-cell statistics on the sidecar outputs
3. **GeoParquet** — join aggregate data with HEALPix geometries

Each stage may be skipped if a valid sidecar / aggregate / GeoParquet output
already exists (detected via ``.meta.json`` sidecar files).

Parameters mirror the three CLI parsers.  Per-stage keyword overrides
(``sidecar_kwargs``, ``aggregate_kwargs``, ``geoparquet_kwargs``) can be
used to fine-tune any stage without affecting defaults.
"""

from __future__ import annotations

import logging
import sys
import time
from io import TextIOBase
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from healpyxel.sidecar import parse_arguments as sidecar_parse, run as sidecar_run
from healpyxel.aggregate import parse_arguments as agg_parse, run as agg_run, AGG_LOOKUP
from healpyxel.geospatial import parse_arguments as geo_parse, run as geo_run

logger = logging.getLogger("healpyxel.workflow")


# ── column discovery ──────────────────────────────────────────────

def _discover_columns(input: Path) -> list[str]:
    """Return numeric column names from a parquet file via PyArrow.

    Inspects the file schema and returns only the integer, floating-point,
    and decimal columns.  Used to auto-populate the aggregate ``--columns``
    argument when the user does not explicitly provide one.

    Parameters
    ----------
    input : Path
        Path to the input parquet file.

    Returns
    -------
    list[str]
        Names of numeric columns detected in the schema.
    """
    pf = pq.ParquetFile(input)
    cols = [
        f.name
        for f in pf.schema_arrow
        if (pa.types.is_integer(f.type)
                or pa.types.is_floating(f.type)
                or pa.types.is_decimal(f.type))
    ]
    return cols

# ── command-string builders ───────────────────────────────────────

def _build_sidecar_cmd(
    input: Path,
    output_dir: Path,
    nsides: tuple[int, ...],
    kw: dict[str, Any],
) -> list[str]:
    """Build the CLI command list for the sidecar stage.

    Constructs a ``healpyxel_sidecar`` command from the merged keyword
    dictionary.  Includes ``-i``, ``--output_dir``, ``--nside``, and
    all sidecar-specific flags present in *kw*.

    Parameters
    ----------
    input : Path
        Input parquet path.
    output_dir : Path
        Working directory for sidecar outputs.
    nsides : tuple[int, ...]
        HEALPix resolutions to process.
    kw : dict[str, Any]
        Merged keyword dict (function defaults overridden by
        ``sidecar_kwargs``).

    Returns
    -------
    list[str]
        Command token list suitable for :func:`healpyxel.sidecar.parse_arguments`.
    """
    cmd = ["healpyxel_sidecar", "-i", str(input)]

    if output_dir:
        cmd += ["--output_dir", str(output_dir)]
    cmd += ["--nside"] + [str(n) for n in nsides]

    # value-or-flag mapping (kw key → CLI flag)
    flag_map = {
        "mode": "--mode",
        "ncores": "--ncores",
        "lon_convention": "--lon-convention",
        "lon_col": "--lon-col",
        "lat_col": "--lat-col",
        "stats_sample_size": "--stats-sample-size",
        "body_model": "--body-model",
        "body_radius": "--body-radius",
        "body_polar_radius": "--body-polar-radius",
        "data_psf": "--data-psf",
        "cell_psf": "--cell-psf",
        "data_psf_sigma_level": "--data-psf-sigma-level",
        "cell_psf_sigma_level": "--cell-psf-sigma-level",
        "psf_combine": "--psf-combine",
        "loglevel": "--loglevel",
        "output_dir": "--output_dir",
    }
    for key, flag in flag_map.items():
        val = kw.get(key)
        if val is None:
            continue
        cmd += [flag, str(val)]

    # boolean flags (only append when True)
    bool_flags = {
        "no_coalesce": "--no-coalesce",
        "stats": "--geo-stats",
        "no_psf_normalize": "--no-psf-normalize",
        "no_multi_res_optimize": "--no-multi-res-optimize",
    }
    for key, flag in bool_flags.items():
        if kw.get(key):
            cmd.append(flag)

    return cmd


def _build_aggregate_cmd(
    input: Path,
    output_dir: Path,
    nside: int,
    sidecar_index: int,
    kw: dict[str, Any],
) -> list[str]:
    """Build the CLI command list for the aggregate stage.

    Constructs a ``healpyxel_aggregate`` command from the merged keyword
    dictionary.  Tallies ``--sidecar-dir``, ``--output``, ``--sidecar-index``,
    column/aggregation/backend flags, and boolean options present in *kw*.

    Parameters
    ----------
    input : Path
        Original input parquet path (used for auto-column discovery metadata).
    output_dir : Path
        Working directory for sidecar outputs.
    nside : int
        HEALPix resolution for this aggregate run.
    sidecar_index : int
        Index into the ``nsides`` tuple, matching the sidecar output.
    kw : dict[str, Any]
        Merged keyword dict (function defaults overridden by
        ``aggregate_kwargs``).

    Returns
    -------
    list[str]
        Command token list suitable for :func:`healpyxel.aggregate.parse_arguments`.
    """
    cmd = ["healpyxel_aggregate", "-i", str(input)]

    cmd += ["--sidecar-dir", str(output_dir), "--output", str(output_dir)]
    cmd += ["--sidecar-index", str(sidecar_index)]

    # aggregate flag
    if kw.get("aggregate", True):
        cmd.append("--aggregate")

    # columns (may include --all-columns)
    columns = kw.get("columns")
    if columns:
        cmd += ["--columns"] + list(columns)
    elif kw.get("all_columns", False):
        cmd.append("--all-columns")

    # aggs
    aggs = kw.get("aggs")
    if aggs:
        cmd += ["--aggs"] + list(aggs)

    # value-or-flag mapping
    flag_map = {
        "filter": "--filter",
        "dask_npartitions": "--dask-npartitions",
        "min_count": "--min-count",
    }
    for key, flag in flag_map.items():
        val = kw.get(key)
        if val is None:
            continue
        cmd += [flag, str(val)]

    # boolean flags (inverse for use_duckdb: False → --no-duckdb)
    for key, flag in {
        "yes": "--yes",
        "use_dask": "--use-dask",
        "stop_on_error": "--stop-on-error",
        "dry_run": "--dry-run",
        "verbose": "--verbose",
        "quiet": "--quiet",
        "densify": "--densify",
    }.items():
        if kw.get(key):
            cmd.append(flag)
    if kw.get("use_duckdb") is False:
        cmd.append("--no-duckdb")
    elif kw.get("use_duckdb"):
        cmd.append("--use-duckdb")

    return cmd


def _build_geoparquet_cmd(
    agg_path: Path,
    kw: dict[str, Any],
) -> list[str]:
    """Build the CLI command list for the geoparquet stage.

    Constructs a ``healpyxel_to_geoparquet`` command from the merged keyword
    dictionary.  Includes the aggregate input path and all geoparquet-specific
    flags present in *kw* (output directories, nside, order, lon convention,
    antimeridian, chunk size, etc.).

    Parameters
    ----------
    agg_path : Path
        Path to the aggregate parquet file.
    kw : dict[str, Any]
        Merged keyword dict (function defaults overridden by
        ``geoparquet_kwargs``).

    Returns
    -------
    list[str]
        Command token list suitable for :func:`healpyxel.geospatial.parse_arguments`.
    """
    cmd = ["healpyxel_to_geoparquet", "--aggregate-path", str(agg_path)]

    flag_map = {
        "output_suffix": "--output-suffix",
        "output_dir": "--output-dir",
        "nside": "--nside",
        "order": "--order",
        "lon_convention": "--lon-convention",
        "chunk_size": "--chunk-size",
    }
    for key, flag in flag_map.items():
        val = kw.get(key)
        if val is None:
            continue
        cmd += [flag, str(val)]

    bool_map = {
        "fix_antimeridian": "--fix-antimeridian",
        "densify": "--densify",
        "yes": "--yes",
    }
    for key, flag in bool_map.items():
        if kw.get(key):
            cmd.append(flag)

    return cmd


# ── skip-if-exists checks ─────────────────────────────────────────

import json as _json_mod

def _load_meta(meta_path: Path) -> dict | None:
    """Load a ``.meta.json`` sidecar file.

    Parameters
    ----------
    meta_path : Path
        Path to the sidecar metadata JSON file.

    Returns
    -------
    dict or None
        Parsed metadata dictionary, or ``None`` if the file does not exist
        or cannot be parsed.
    """
    try:
        with open(meta_path) as f:
            return _json_mod.load(f)
    except (OSError, _json_mod.JSONDecodeError):
        return None


def _sidecar_meta_exists(
    input: Path,
    output_dir: Path,
    nside: int,
    mode: str,
) -> bool:
    """Check whether a valid sidecar output already exists.

    Scans ``output_dir`` for ``.meta.json`` files with ``stage ==
    "sidecar"``, matching ``nside`` and ``mode``.  Also verifies that the
    referenced output parquet still exists on disk.

    Parameters
    ----------
    input : Path
        Original input parquet path (used for correlation context only).
    output_dir : Path
        Directory to scan for sidecar metadata.
    nside : int
        Expected HEALPix resolution.
    mode : str
        Expected processing mode (``"strict"`` or ``"fuzzy"``).

    Returns
    -------
    bool
        ``True`` if a matching, valid sidecar exists.
    """
    target_out = None
    for meta_path in output_dir.glob("*.meta.json"):
        meta = _load_meta(meta_path)
        if not meta:
            continue
        proc = meta.get("processing", {})
        if proc.get("stage") != "sidecar":
            continue
        hp = meta.get("healpix", {})
        if hp.get("nside") == nside and hp.get("mode") == mode:
            out_file = proc.get("output_file", "")
            if out_file and Path(out_file).exists():
                return True
    return False


def _aggregate_meta_exists(
    input: Path,
    output_dir: Path,
    nside: int,
    sidecar_output_file: str,
) -> bool:
    """Check whether a valid aggregate output already exists.

    Scans ``output_dir`` for ``.meta.json`` files with ``stage ==
    "aggregate"``, matching either the given *sidecar_output_file* reference
    or the expected ``nside``.  Also verifies the output parquet is on disk.

    Parameters
    ----------
    input : Path
        Original input parquet path (correlation context).
    output_dir : Path
        Directory to scan for aggregate metadata.
    nside : int
        Expected HEALPix resolution.
    sidecar_output_file : str
        Expected sidecar output path (for exact-match check).

    Returns
    -------
    bool
        ``True`` if a matching, valid aggregate exists.
    """
    for meta_path in output_dir.glob("*.meta.json"):
        meta = _load_meta(meta_path)
        if not meta:
            continue
        proc = meta.get("processing", {})
        if proc.get("stage") != "aggregate":
            continue
        sc_file = proc.get("sidecar_file", "")
        # match by sidecar reference OR by legacy nside tag
        hp_nside = None
        sm = meta.get("sidecar_metadata", {}).get("healpix", {})
        if sm:
            hp_nside = sm.get("nside")
        legacy_nside = meta.get("_legacy", {}).get("healpix_nside")
        if hp_nside is None and legacy_nside is not None:
            try:
                hp_nside = int(legacy_nside)
            except (ValueError, TypeError):
                pass
        if (sc_file == sidecar_output_file
                or hp_nside == nside):
            out_file = proc.get("output_file", "")
            if out_file and Path(out_file).exists():
                return True
    return False


def _geoparquet_exists(
    input: Path,
    output_dir: Path,
    nside: int,
) -> bool:
    """Check whether a GeoParquet output for this run already exists.

    Looks for files matching ``{stem}-aggregated.cell-healpix_assignment-*_nside-{nside}_order-*.geo.parquet``
    in *output_dir*.

    Parameters
    ----------
    input : Path
        Original input parquet path (used for stem matching).
    output_dir : Path
        Directory to scan for GeoParquet outputs.
    nside : int
        Expected HEALPix resolution.

    Returns
    -------
    bool
        ``True`` if at least one matching GeoParquet file exists.
    """
    stem = input.stem
    pattern = f"{stem}-aggregated.cell-healpix_assignment-*_nside-{nside}_order-*.geo.parquet"
    matches = list(output_dir.glob(pattern))
    return len(matches) > 0


# ── log/verbose helpers ───────────────────────────────────────────

def _write_log_header(log: TextIOBase, label: str) -> None:
    """Write a labelled header block to a log stream.

    Parameters
    ----------
    log : TextIO
        File-like text stream to write to.
    label : str
        Section label to display.
    """
    log.write(f"\n# ── {label} ─{'─' * max(0, 60 - len(label))}\n")
    log.write(f"# run: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")


def _print_cmd(label: str, cmd: list[str]) -> None:
    """Print a labelled shell command to stdout.

    Parameters
    ----------
    label : str
        Stage label for context.
    cmd : list[str]
        Command tokens to format and display.
    """
    print(f"\n$ {' '.join(cmd)}")


def _print_status(label: str, status: str, detail: str = "") -> None:
    """Print a stage result line with icon.

    Parameters
    ----------
    label : str
        Stage label.
    status : str
        One of ``"ok"``, ``"skip"``, ``"error"``.
    detail : str
        Optional additional context string.
    """
    icon = {"ok": "✓", "skip": "⏭", "error": "❌"}.get(status, "?")
    msg = f"{icon} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)


# ── public API ────────────────────────────────────────────────────

def run_pipeline(
    input: Path,
    output_dir: Path,
    nsides: tuple[int, ...] = (32, 64, 128),
    *,
    # common
    lon_convention: str = "minus_plus180",
    yes: bool = False,
    loglevel: str = "info",
    # sidecar
    mode: str = "fuzzy",
    ncores: int | None = None,
    lon_col: str | None = None,
    lat_col: str | None = None,
    no_coalesce: bool = False,
    stats_sample_size: int = 10000,
    body_model: str = "sphere",
    body_radius: float = 1.0,
    body_polar_radius: float | None = None,
    data_psf: str = "none",
    cell_psf: str = "none",
    data_psf_sigma_level: float = 2.0,
    cell_psf_sigma_level: float = 2.0,
    psf_combine: str = "multiply",
    no_psf_normalize: bool = False,
    no_multi_res_optimize: bool = False,
    # aggregate
    columns: tuple[str, ...] | None = None,
    aggregate_columns: tuple[str, ...] | None = None,
    aggs: tuple[str, ...] = ("mean", "median", "std", "robust_std"),
    filter: str | None = None,
    densify: bool = False,
    use_duckdb: bool = True,
    use_dask: bool = False,
    dask_npartitions: int | None = None,
    min_count: int = 1,
    stop_on_error: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    # geoparquet
    output_suffix: str = ".geo",
    gpq_output_dir: Path | None = None,
    gpq_nside: int | None = None,
    order: str = "nested",
    gpq_lon_convention: str = "auto",
    fix_antimeridian: bool = True,
    chunk_size: int = 65536,
    # pipeline-level overrides
    sidecar_kwargs: dict[str, Any] | None = None,
    aggregate_kwargs: dict[str, Any] | None = None,
    geoparquet_kwargs: dict[str, Any] | None = None,
    log: TextIOBase | None = None,
) -> list[dict]:
    """Run the 3-phase pipeline (sidecar → aggregate → geoparquet).

    Parameters
    ----------
    input : Path
        Input parquet file.
    output_dir : Path
        Working directory for sidecars and aggregates.
    nsides : tuple[int]
        HEALPix resolutions.
    * — common / per-stage params (see function signature)
    sidecar_kwargs / aggregate_kwargs / geoparquet_kwargs : dict | None
        Per-stage overrides. Keys match the CLI argument names (underscores
        instead of hyphens). Take highest precedence over function-argument
        defaults.
    verbose : bool
        Print equivalent CLI command before each stage.
    log : TextIO | None
        If set, write a reproducible bash script to this stream.

    Returns
    -------
    list[dict]
        One entry per executed stage: ``{"stage": str, "nside": int|None,
        "status": "ok"|"skip"|"error", "detail": str}``.
    """
    input = Path(input)
    output_dir = Path(output_dir)
    input = input.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    if not input.exists():
        raise FileNotFoundError(f"Input file not found: {input}")
    if not output_dir.is_dir():
        raise NotADirectoryError(f"Output directory not found: {output_dir}")

    # ── build merged kw dicts (function defaults → *kwargs override)
    sc_kw: dict[str, Any] = {
        "mode": mode,
        "ncores": ncores,
        "lon_convention": lon_convention,
        "lon_col": lon_col,
        "lat_col": lat_col,
        "no_coalesce": no_coalesce,
        "stats_sample_size": stats_sample_size,
        "body_model": body_model,
        "body_radius": body_radius,
        "body_polar_radius": body_polar_radius,
        "data_psf": data_psf,
        "cell_psf": cell_psf,
        "data_psf_sigma_level": data_psf_sigma_level,
        "cell_psf_sigma_level": cell_psf_sigma_level,
        "psf_combine": psf_combine,
        "no_psf_normalize": no_psf_normalize,
        "no_multi_res_optimize": no_multi_res_optimize,
        "output_dir": str(output_dir),
        "loglevel": loglevel,
    }
    if sidecar_kwargs:
        sc_kw.update(sidecar_kwargs)

    # Default ncores if still None
    if sc_kw.get("ncores") is None:
        sc_kw["ncores"] = max(1, (__import__("os").cpu_count() or 2) - 1)

    ag_kw: dict[str, Any] = {
        "aggregate": True,
        "aggs": list(aggs),
        "filter": filter,
        "densify": densify,
        "use_duckdb": use_duckdb,
        "no_duckdb": not use_duckdb,
        "use_dask": use_dask,
        "dask_npartitions": dask_npartitions,
        "min_count": min_count,
        "stop_on_error": stop_on_error,
        "verbose": verbose,
        "quiet": quiet,
        "yes": yes,
        "loglevel": loglevel,
    }
    if aggregate_kwargs:
        ag_kw.update(aggregate_kwargs)

    # If aggregate_columns is specified (but no columns override), build merged list
    auto_cols: list[str] = []
    if columns is None and "columns" not in ag_kw:
        auto_cols = _discover_columns(input)
        if aggregate_columns:
            # Deduplicate while preserving order: auto first, then forced
            seen = set(auto_cols)
            for c in aggregate_columns:
                if c not in seen:
                    auto_cols.append(c)
                    seen.add(c)
        ag_kw["columns"] = tuple(auto_cols)
        ag_kw["all_columns"] = False
    elif columns is not None:
        ag_kw["columns"] = columns
        ag_kw["all_columns"] = False
    else:
        ag_kw["all_columns"] = True
        ag_kw.pop("columns", None)

    geo_kw: dict[str, Any] = {
        "output_suffix": output_suffix,
        "output_dir": str(gpq_output_dir) if gpq_output_dir else None,
        "nside": gpq_nside,
        "order": order,
        "lon_convention": gpq_lon_convention,
        "fix_antimeridian": fix_antimeridian,
        "chunk_size": chunk_size,
        "densify": densify,
        "yes": yes,
    }
    if geoparquet_kwargs:
        geo_kw.update(geoparquet_kwargs)

    # ── Phase 1: Sidecar ──────────────────────────────────────────
    sc_cmd = _build_sidecar_cmd(input, output_dir, nsides, sc_kw)
    sc_label = f"healpyxel sidecar (all nsides={','.join(str(n) for n in nsides)})"

    if log:
        _write_log_header(log, sc_label)
        _write_log_cmd(log, sc_cmd)
    if verbose:
        _print_cmd(sc_label, sc_cmd)

    # skip-if-exists check (per-nside via meta.json)
    sidecar_modes_done: dict[int, str] = {}
    missing_nsides = []
    for n in nsides:
        if _sidecar_meta_exists(input, output_dir, n, sc_kw.get("mode", "fuzzy")):
            sidecar_modes_done[n] = sc_kw.get("mode", "fuzzy")
        else:
            missing_nsides.append(n)

    if not missing_nsides:
        _print_status(sc_label, "skip",
                      f"sidecars for nsides {','.join(str(n) for n in nsides)} already exist (checked via .meta.json)")
        results.append({"stage": "sidecar", "nside": None, "status": "skip",
                        "detail": f"all exist (meta.json confirmed for nside {','.join(str(n) for n in nsides)})"})
        if log:
            log.write(f"# skipped — sidecars for nsides {','.join(str(n) for n in nsides)} already exist "
                      f"(verified via .meta.json)\n")
    else:
        try:
            args = sidecar_parse(sc_cmd[1:])
            sidecar_run(args)
            _print_status(sc_label, "ok", f"output: {output_dir}")
            results.append({"stage": "sidecar", "nside": None, "status": "ok", "detail": ""})
            if log:
                log.write(f"# status: ok\n")
        except Exception as e:
            _print_status(sc_label, "error", str(e))
            results.append({"stage": "sidecar", "nside": None, "status": "error", "detail": str(e)})
            if log:
                log.write(f"# status: error — {e}\n")

    # ── Phase 2: Aggregate (per nside) ────────────────────────────
    for i, nside in enumerate(nsides):
        agg_cmd = _build_aggregate_cmd(input, output_dir, nside, i, ag_kw)
        agg_label = f"healpyxel aggregate (nside={nside})"

        # Find the sidecar output to link via meta.json
        sidecar_out = None
        for meta_path in output_dir.glob("*.meta.json"):
            meta = _load_meta(meta_path)
            if not meta:
                continue
            proc = meta.get("processing", {})
            if proc.get("stage") != "sidecar":
                continue
            hp = meta.get("healpix", {})
            if hp.get("nside") == nside and hp.get("mode") == sc_kw.get("mode", "fuzzy"):
                sidecar_out = proc.get("output_file", "")
                break

        if sidecar_out and _aggregate_meta_exists(input, output_dir, nside, sidecar_out):
            _print_status(agg_label, "skip",
                          f"aggregate for nside={nside} already exists (verify via .meta.json)")
            results.append({"stage": "aggregate", "nside": nside, "status": "skip",
                            "detail": "exists (meta.json confirmed)"})
            if log:
                _write_log_header(log, agg_label)
                _write_log_cmd(log, agg_cmd)
                log.write(f"# skipped — aggregate for nside={nside} already exists "
                          f"(verified via .meta.json)\n")
            continue

        if log:
            _write_log_header(log, agg_label)
            _write_log_cmd(log, agg_cmd)
        if verbose:
            _print_cmd(agg_label, agg_cmd)

        try:
            args = agg_parse(agg_cmd[1:])
            agg_run(args)
            _print_status(agg_label, "ok", f"output: {output_dir}")
            results.append({"stage": "aggregate", "nside": nside, "status": "ok", "detail": ""})
            if log:
                log.write(f"# status: ok\n")
        except Exception as e:
            _print_status(agg_label, "error", str(e))
            results.append({"stage": "aggregate", "nside": nside, "status": "error", "detail": str(e)})
            if log:
                log.write(f"# status: error — {e}\n")

    # ── Phase 3: GeoParquet (per nside) ───────────────────────────
    for nside in nsides:
        agg_matches = list(
            output_dir.glob(
                f"{input.stem}-aggregated.cell-healpix_assignment-*_nside-{nside}_order-*.parquet"
            )
        )
        if not agg_matches:
            _print_status(f"healpyxel geoparquet (nside={nside})", "error", "no aggregate file")
            results.append({"stage": "geoparquet", "nside": nside, "status": "error", "detail": "no aggregate"})
            continue
        geo_path = agg_matches[0]

        if _geoparquet_exists(input, output_dir, nside):
            _print_status(f"healpyxel geoparquet (nside={nside})", "skip",
                          f"geoparquet for nside={nside} already exists")
            results.append({"stage": "geoparquet", "nside": nside, "status": "skip",
                            "detail": "exists (.geo.parquet found)"})
            if log:
                gcmd = _build_geoparquet_cmd(geo_path, geo_kw)
                _write_log_header(log, f"healpyxel geoparquet (nside={nside})")
                _write_log_cmd(log, gcmd)
                log.write(f"# skipped — geoparquet for nside={nside} already exists\n")
            continue

        gpq_cmd = _build_geoparquet_cmd(geo_path, geo_kw)
        gpq_label = f"healpyxel geoparquet (nside={nside})"

        if log:
            _write_log_header(log, gpq_label)
            _write_log_cmd(log, gpq_cmd)
        if verbose:
            _print_cmd(gpq_label, gpq_cmd)

        try:
            args = geo_parse(gpq_cmd[1:])
            geo_run(args)
            _print_status(gpq_label, "ok", f"output: {output_dir}")
            results.append({"stage": "geoparquet", "nside": nside, "status": "ok", "detail": ""})
            if log:
                log.write(f"# status: ok\n")
        except Exception as e:
            _print_status(gpq_label, "error", str(e))
            results.append({"stage": "geoparquet", "nside": nside, "status": "error", "detail": str(e)})
            if log:
                log.write(f"# status: error — {e}\n")

    # ── summary ───────────────────────────────────────────────────
    if verbose or log:
        ok_count = sum(1 for r in results if r["status"] == "ok")
        skip_count = sum(1 for r in results if r["status"] == "skip")
        err_count = sum(1 for r in results if r["status"] == "error")
        summary = f"\n# ── pipeline summary ──\n# ok={ok_count} skip={skip_count} error={err_count}\n"
        if verbose:
            print(f"\n{'─'*40}\nPipeline complete: ✓{ok_count} ⏭{skip_count} ❌{err_count}")
        if log:
            log.write(summary)

    return results


def save_script(
    input: Path,
    output_dir: Path,
    nsides: tuple[int, ...] = (32, 64, 128),
    *,
    lon_convention: str = "minus_plus180",
    yes: bool = False,
    loglevel: str = "info",
    mode: str = "fuzzy",
    ncores: int | None = None,
    lon_col: str | None = None,
    lat_col: str | None = None,
    no_coalesce: bool = False,
    stats_sample_size: int = 10000,
    body_model: str = "sphere",
    body_radius: float = 1.0,
    body_polar_radius: float | None = None,
    data_psf: str = "none",
    cell_psf: str = "none",
    data_psf_sigma_level: float = 2.0,
    cell_psf_sigma_level: float = 2.0,
    psf_combine: str = "multiply",
    no_psf_normalize: bool = False,
    no_multi_res_optimize: bool = False,
    columns: tuple[str, ...] | None = None,
    aggregate_columns: tuple[str, ...] | None = None,
    aggs: tuple[str, ...] = ("mean", "median", "std", "robust_std"),
    filter: str | None = None,
    densify: bool = False,
    use_duckdb: bool = True,
    use_dask: bool = False,
    dask_npartitions: int | None = None,
    min_count: int = 1,
    stop_on_error: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    output_suffix: str = ".geo",
    gpq_output_dir: Path | None = None,
    gpq_nside: int | None = None,
    order: str = "nested",
    gpq_lon_convention: str = "auto",
    fix_antimeridian: bool = True,
    chunk_size: int = 65536,
    sidecar_kwargs: dict[str, Any] | None = None,
    aggregate_kwargs: dict[str, Any] | None = None,
    geoparquet_kwargs: dict[str, Any] | None = None,
    output: Path | TextIOBase | None = None,
) -> None:
    """Write a self-contained bash script reproducing this pipeline run.

    Parameters mirror ``run_pipeline``. If *output* is a path, write there.
    If *output* is a file handle, write directly. If *None*, write to stdout.
    """
    if isinstance(output, Path):
        fh: TextIOBase = output.open("w")  # type: ignore[assignment]
        close_fh = True
    elif output is None:
        fh = sys.stdout  # type: ignore[assignment]
        close_fh = False
    else:
        fh = output
        close_fh = False

    try:
        fh.write("#!/usr/bin/env bash\nset -euo pipefail\n\n")
        fh.write(f"# Reproducible healpyxel pipeline script — generated {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n")

        # Reuse the same logic as run_pipeline but only writing commands
        # (no execution). We call run_pipeline with log=fh and verbose=False
        # for the sidecar (it would also try to execute) — instead we manually
        # write the equivalent commands via the same cmd builders.

        # Build the same kw dicts as run_pipeline would
        sc_kw: dict[str, Any] = {
            "mode": mode,
            "ncores": ncores,
            "lon_convention": lon_convention,
            "lon_col": lon_col,
            "lat_col": lat_col,
            "no_coalesce": no_coalesce,
            "stats_sample_size": stats_sample_size,
            "body_model": body_model,
            "body_radius": body_radius,
            "body_polar_radius": body_polar_radius,
            "data_psf": data_psf,
            "cell_psf": cell_psf,
            "data_psf_sigma_level": data_psf_sigma_level,
            "cell_psf_sigma_level": cell_psf_sigma_level,
            "psf_combine": psf_combine,
            "no_psf_normalize": no_psf_normalize,
            "no_multi_res_optimize": no_multi_res_optimize,
            "output_dir": str(output_dir),
            "loglevel": loglevel,
        }
        if sidecar_kwargs:
            sc_kw.update(sidecar_kwargs)

        if sc_kw.get("ncores") is None:
            sc_kw["ncores"] = max(1, (__import__("os").cpu_count() or 2) - 1)

        ag_kw: dict[str, Any] = {
            "aggregate": True,
            "aggs": list(aggs),
            "filter": filter,
            "densify": densify,
            "use_duckdb": use_duckdb,
            "no_duckdb": not use_duckdb,
            "use_dask": use_dask,
            "dask_npartitions": dask_npartitions,
            "min_count": min_count,
            "stop_on_error": stop_on_error,
            "verbose": verbose,
            "quiet": quiet,
            "yes": yes,
            "loglevel": loglevel,
        }
        if aggregate_kwargs:
            ag_kw.update(aggregate_kwargs)

        auto_cols: list[str] = []
        if columns is None and "columns" not in ag_kw:
            auto_cols = _discover_columns(input)
            if aggregate_columns:
                seen = set(auto_cols)
                for c in aggregate_columns:
                    if c not in seen:
                        auto_cols.append(c)
                        seen.add(c)
            ag_kw["columns"] = tuple(auto_cols)
            ag_kw["all_columns"] = False
        elif columns is not None:
            ag_kw["columns"] = columns
            ag_kw["all_columns"] = False
        else:
            ag_kw["all_columns"] = True
            ag_kw.pop("columns", None)

        geo_kw: dict[str, Any] = {
            "output_suffix": output_suffix,
            "output_dir": str(gpq_output_dir) if gpq_output_dir else None,
            "nside": gpq_nside,
            "order": order,
            "lon_convention": gpq_lon_convention,
            "fix_antimeridian": fix_antimeridian,
            "chunk_size": chunk_size,
            "densify": densify,
            "yes": yes,
        }
        if geoparquet_kwargs:
            geo_kw.update(geoparquet_kwargs)

        # Write sidecar command
        sc_cmd = _build_sidecar_cmd(input, output_dir, nsides, sc_kw)
        fh.write("\n# ── healpyxel sidecar ──\n")
        _write_log_cmd(fh, sc_cmd)
        fh.write("\n")

        # Write aggregate commands (per nside)
        for i, nside in enumerate(nsides):
            agg_cmd = _build_aggregate_cmd(input, output_dir, nside, i, ag_kw)
            fh.write(f"\n# ── healpyxel aggregate (nside={nside}) ──\n")
            _write_log_cmd(fh, agg_cmd)
            fh.write("\n")

        # Write geoparquet commands (per nside)
        for nside in nsides:
            geo_path = output_dir / (
                f"{input.stem}-aggregated.cell-healpix_assignment-fuzzy_nside-{nside}_order-nested.parquet"
            )
            gpq_cmd = _build_geoparquet_cmd(geo_path, geo_kw)
            fh.write(f"\n# ── healpyxel geoparquet (nside={nside}) ──\n")
            _write_log_cmd(fh, gpq_cmd)
            fh.write("\n")

    finally:
        if close_fh:
            fh.close()


# ── internal log writer ───────────────────────────────────────────

def _write_log_cmd(log: TextIOBase, cmd: list[str]) -> None:
    """Write a single command in bash-friendly multi-line format."""
    binary = cmd[0]
    args = cmd[1:]
    log.write(f"{binary} \\\n")
    for i, arg in enumerate(args):
        if i < len(args) - 1:
            log.write(f"  {arg} \\\n")
        else:
            log.write(f"  {arg}\n")


def pipeline_cli(argv=None):
    """CLI entry point for the ``healpyxel_pipeline`` command.

    Defines the argument parser and invokes :func:`run_pipeline` with the
    parsed namespace.  Supports the full pipeline (sidecar, aggregate,
    geoparquet phases) plus optional per-stage overrides through
    keyword arguments.

    Parameters
    ----------
    argv : list[str] or None
        Argument list (defaults to ``sys.argv[1:]`` when ``None``).
    """
    import textwrap

    parser = __import__("argparse").ArgumentParser(
        description="Run healpyxel pipeline: sidecar → aggregate → geoparquet",
        formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            For full control, pass parameters via the Python API:
                from healpyxel.workflow import run_pipeline
                run_pipeline(input=..., columns=..., aggregate_kwargs={...})
            """),
    )
    # common
    parser.add_argument('-i', '--input', type=Path, required=True,
                        help='Input parquet file (required).')
    parser.add_argument('-o', '--output-dir', type=Path, required=True,
                        help='Working directory for sidecars/aggregates.')
    parser.add_argument('-n', '--nsides', type=int, nargs='+', default=[32, 64, 128],
                        help='HEALPix nside values (default: 32 64 128).')
    parser.add_argument('--lon-convention', default='minus_plus180',
                        choices=['0_360', 'minus_plus180'])
    parser.add_argument('-y', '--yes', action='store_true',
                        help='Suppress overwrite prompts.')
    parser.add_argument('--loglevel', default='info', choices=['debug', 'info', 'warning', 'error'])

    # sidecar
    parser.add_argument('--mode', default='fuzzy', choices=['strict', 'fuzzy'])
    parser.add_argument('--ncores', type=int, default=None)
    parser.add_argument('--body-model', default='sphere',
                        choices=['sphere', 'ellipsoid', 'dsk'])

    # aggregate
    parser.add_argument('--columns', nargs='+', default=None,
                        help='Value columns to aggregate (overrides auto-discovery).')
    parser.add_argument('--aggregate-columns', nargs='+', dest='aggregate_columns', default=None,
                        help='Additional columns always included alongside auto-discovered ones.')
    parser.add_argument('--aggs', nargs='+', default=['mean', 'median', 'std', 'robust_std'],
                        choices=list(AGG_LOOKUP.keys()),
                        help='Aggregation functions (default: mean median std robust_std).')
    parser.add_argument('--filter', default=None,
                        help='Pandas query expression to filter before aggregation.')
    parser.add_argument('--densify', action='store_true')
    parser.add_argument('--no-duckdb', action='store_true')
    parser.add_argument('--use-dask', action='store_true')
    parser.add_argument('--min-count', type=int, default=1)
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print equivalent CLI commands before each stage.')

    # pipeline
    parser.add_argument('--log', type=Path, default=None,
                        help='Write a reproducible bash script to this file.')

    args = parser.parse_args(argv)

    return run_pipeline(
        input=args.input,
        output_dir=args.output_dir,
        nsides=tuple(args.nsides),
        lon_convention=args.lon_convention,
        yes=args.yes,
        loglevel=args.loglevel,
        mode=args.mode,
        ncores=args.ncores,
        body_model=args.body_model,
        columns=tuple(args.columns) if args.columns else None,
        aggregate_columns=tuple(args.aggregate_columns) if args.aggregate_columns else None,
        aggs=tuple(args.aggs),
        filter=args.filter,
        densify=args.densify,
        use_duckdb=not args.no_duckdb,
        use_dask=args.use_dask,
        min_count=args.min_count,
        verbose=args.verbose,
        log=open(args.log, 'w') if args.log else None,
    )
