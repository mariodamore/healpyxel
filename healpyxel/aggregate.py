"""Split-apply-combine aggregation for HEALPix tessellated data.

This module implements the **aggregation** stage of the healpyxel pipeline.
It reads observation data together with a sidecar file (source → HEALPix
cell mapping), groups observations by cell, and computes summary statistics
(mean, median, std, MAD, robust std, min, max) per cell.

**Key capabilities:**

* **Flexible backends** — DuckDB for efficient column-projection loading,
  Dask for parallel aggregation, or plain pandas as fallback.
* **Quality filtering** — pandas query expressions to filter observations
  before aggregation.
* **Min-count thresholding** — cells with fewer than N observations
  output NaN for all statistics, preventing unreliable values from sparse
  cells.
* **Densification** — optionally expand sparse output to the full HEALPix
  grid (all ``12 * nside²`` cells), filling unobserved cells with NaN.
* **Batch processing** — process multiple sidecars in batch with
  per-sidecar error tolerance and overwrite prompts.

The public API is the :func:`aggregate_by_sidecar` function; everything else
supports it or wraps it for CLI/pipe usage.
"""

import argparse
import logging
import sys
import os
import json
from pathlib import Path
from typing import Optional, Sequence, Iterable, Callable, Dict, List
from datetime import datetime, timezone
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa
import numpy as np
import re

try:
    from tqdm.auto import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    # Fallback: no-op progress bar
    class tqdm:
        def __init__(self, iterable, *args, **kwargs):
            self.iterable = iterable
        def __iter__(self):
            return iter(self.iterable)
        def set_postfix(self, *args, **kwargs):
            pass
        def close(self):
            pass

try:
    import dask.dataframe as dd
    DASK_AVAILABLE = True
except ImportError:
    DASK_AVAILABLE = False
    dd = None

try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False
    duckdb = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Aggregation function lookup
def _mad(arr: np.ndarray) -> float:
    """Compute Median Absolute Deviation."""
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.median(np.abs(arr - np.median(arr))))

def _robust_std(arr: np.ndarray) -> float:
    """Compute robust standard deviation (MAD * 1.4826)."""
    m = _mad(arr)
    if np.isnan(m):
        return float("nan")
    return float(m * 1.4826)  # Approximation for normal distribution

AGG_LOOKUP: Dict[str, Callable] = {
    "mean": lambda a: float(np.nanmean(a)) if np.any(np.isfinite(a)) else float("nan"),
    "median": lambda a: float(np.nanmedian(a)) if np.any(np.isfinite(a)) else float("nan"),
    "std": lambda a: float(np.nanstd(a, ddof=0)) if np.any(np.isfinite(a)) else float("nan"),
    "min": lambda a: float(np.nanmin(a)) if np.any(np.isfinite(a)) else float("nan"),
    "max": lambda a: float(np.nanmax(a)) if np.any(np.isfinite(a)) else float("nan"),
    "mad": lambda a: _mad(a),
    "robust_std": lambda a: _robust_std(a),
}

def generate_output_filename(
    input_file: Path,
    sidecar_file: Path,
    output_dir: Optional[Path] = None,
    densified: bool = False
) -> Path:
    """Generate output filename that matches the parseable structure of the sidecar.

    Constructs a descriptive output filename by combining the input file stem
    with the sidecar suffix and an optional densification marker.

    Parameters
    ----------
    input_file : Path
        Original input parquet file.
    sidecar_file : Path
        Sidecar file being used (suffix is extracted from this).
    output_dir : Path or None
        Output directory. Defaults to the same directory as ``sidecar_file``.
    densified : bool
        If True, append ``-densified`` marker to the output filename.

    Returns
    -------
    Path
        Output file path with format::

            <stem>-aggregated[-densified].<sidecar_suffix>.parquet

    Examples
    --------
    For ``input=mascs_data.parquet`` and
    ``sidecar=mascs_data.cell-healpix_assignment-strict_nside-64_order-nested.parquet``:

    * Sparse: ``mascs_data-aggregated.cell-healpix_assignment-strict_nside-64_order-nested.parquet``
    * Densified: ``mascs_data-aggregated-densified.cell-healpix_assignment-strict_nside-64_order-nested.parquet``
    """
    input_stem = input_file.stem
    sidecar_name = sidecar_file.name

    # Extract the suffix after input_stem
    if sidecar_name.startswith(f"{input_stem}."):
        # Get everything after "input_stem."
        sidecar_suffix = sidecar_name[len(input_stem) + 1:]
        # Remove .parquet extension
        if sidecar_suffix.endswith('.parquet'):
            sidecar_suffix = sidecar_suffix[:-len('.parquet')]
    else:
        # Fallback: use whole sidecar name without extension
        logger.warning(f"Sidecar name doesn't start with expected stem '{input_stem}'")
        sidecar_suffix = sidecar_file.stem

    # Build output filename with densification marker
    densify_marker = "-densified" if densified else ""
    output_name = f"{input_stem}-aggregated{densify_marker}.{sidecar_suffix}.parquet"

    # Use output_dir if specified, otherwise use sidecar directory
    if output_dir is None:
        output_dir = sidecar_file.parent

    return output_dir / output_name

def extract_nside_from_filename(filename: str) -> Optional[int]:
    """
    Extract nside value from sidecar filename using regex.

    Conservative: returns None if ambiguous or not found.

    Args:
        filename: Sidecar filename string

    Returns:
        nside value as integer, or None if not found/ambiguous

    Example:
        extract_nside_from_filename("data.nside-128.parquet") -> 128
    """
    patterns = [
        r"(?:^|[._-])nside[=_-](\d+)",
        r"healpix[_-]nside[=_-](\d+)",
    ]

    values: set[int] = set()
    for pat in patterns:
        matches = re.findall(pat, filename, flags=re.IGNORECASE)
        for m in matches:
            try:
                values.add(int(m))
            except (ValueError, TypeError):
                continue

    if len(values) == 1:
        return values.pop()
    return None

def validate_sidecar_metadata(
    sidecar_path: Path,
    input_file: Path,
    require_metadata: bool = False
) -> Dict:
    """Validate sidecar metadata file and check source file match.

    Reads the ``.meta.json`` companion file for a sidecar parquet and
    verifies that the recorded ``source_file`` matches the expected input
    file. This prevents accidentally using a sidecar derived from a
    different observation file.

    Parameters
    ----------
    sidecar_path : Path
        Path to sidecar parquet file.
    input_file : Path
        Expected source input file path (matched by filename).
    require_metadata : bool
        If True, raise ``FileNotFoundError`` when ``.meta.json`` is missing.
        If False (default), log a warning and return an empty dict.

    Returns
    -------
    dict
        Metadata dictionary from ``.meta.json``. Empty dict if not found
        and ``require_metadata=False``.

    Raises
    ------
    FileNotFoundError
        If ``require_metadata=True`` and ``.meta.json`` not found.
    ValueError
        If ``source_file`` in metadata doesn't match ``input_file`` name.
    """
    metadata_path = sidecar_path.with_suffix('.meta.json')

    if not metadata_path.exists():
        if require_metadata:
            raise FileNotFoundError(f"Required metadata file not found: {metadata_path}")
        else:
            logger.warning(f"Metadata file not found (lenient mode): {metadata_path}")
            return {}

    try:
        with open(metadata_path, 'r') as f:
            metadata = json.load(f)
    except Exception as e:
        if require_metadata:
            raise ValueError(f"Failed to read metadata from {metadata_path}: {e}")
        else:
            logger.warning(f"Could not parse metadata file: {e}")
            return {}

    # Check source_file match
    source_file = metadata.get('processing', {}).get('source_file')
    if source_file:
        # Compare filenames (not full paths, to handle moved files)
        expected_name = input_file.name
        actual_name = Path(source_file).name

        if expected_name != actual_name:
            raise ValueError(
                f"Source file mismatch in sidecar metadata:\n"
                f"  Expected: {expected_name}\n"
                f"  Found in metadata: {actual_name}\n"
                f"  Sidecar: {sidecar_path.name}"
            )

    return metadata

def collect_sidecar_outputs(
    input_parquet: Path,
    output_dir: Path,
    read_stats: bool = False
) -> pd.DataFrame:
    """Scan ``output_dir`` for sidecar parquet files matching the input stem.

    Searches the output directory for sidecar files that belong to the given
    input parquet file. Validates via ``.meta.json`` metadata when available,
    falling back to filename pattern matching.

    Parameters
    ----------
    input_parquet : Path
        Path to the original input parquet file. Used for stem matching
        and source_file validation.
    output_dir : Path
        Directory containing sidecar parquet files.
    read_stats : bool
        If True, read each sidecar to compute row counts and unique HEALPix
        counts. Slower but provides richer metadata.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: ``file``, ``coalesced``, ``mode``, ``nside``,
        ``order``, ``n_rows``, ``n_unique_healpix``, and optionally
        ``derived_from_parent``.
    """
    from healpyxel.metadata import HEALPyxelxMetadata

    stem = input_parquet.stem

    if not output_dir.exists():
        raise FileNotFoundError(f"Output directory does not exist: {output_dir}")

    logger.debug(f"Scanning {output_dir} for sidecar files matching stem: {stem}")

    rows = []
    for p in output_dir.rglob("*.parquet"):
        # Skip partition-directory style outputs
        if p.name.endswith(".parts"):
            continue
        if not p.is_file():
            continue

        # Skip the original input file itself
        try:
            if p.resolve() == input_parquet.resolve():
                logger.debug(f"Skipping original input file: {p.name}")
                continue
        except Exception:
            if p.name == input_parquet.name:
                logger.debug(f"Skipping original input file: {p.name}")
                continue

        # Quick filter: skip files with -aggregated in filename
        if "-aggregated" in p.name:
            logger.debug(f"Skipping aggregate file: {p.name}")
            continue

        meta_json = None
        metadata_path = p.with_suffix('.meta.json')
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r') as f:
                    meta_json = json.load(f)
            except Exception as e:
                logger.warning(f"Could not parse metadata file for {p.name}: {e}")
                meta_json = None

        # Validate stage from metadata - only accept sidecars
        if meta_json:
            stage = meta_json.get('processing', {}).get('stage') or meta_json.get('file_type')
            if stage and stage != 'sidecar':
                logger.debug(f"Skipping non-sidecar file (stage={stage}): {p.name}")
                continue

        # Decide if this sidecar belongs to the input file
        if meta_json:
            source_file = meta_json.get('processing', {}).get('source_file')
            if source_file:
                if Path(source_file).name != input_parquet.name:
                    logger.debug(f"Skipping {p.name}: source_file mismatch")
                    continue
        else:
            # Fallback to filename stem matching
            if not p.name.startswith(f"{stem}."):
                continue

        logger.debug(f"Found sidecar candidate: {p.name}")

        # Initialize row
        out_row = {"file": str(p), "coalesced": True}

        # Prefer metadata-derived HEALPix info
        if meta_json:
            try:
                hp_meta = HEALPyxelxMetadata.from_dict(meta_json)
                out_row["nside"] = hp_meta.nside
                out_row["order"] = hp_meta.order
                out_row["mode"] = hp_meta.mode
            except Exception as e:
                logger.debug(f"Could not parse HEALPix metadata for {p.name}: {e}")

            file_type = meta_json.get('file_type') or meta_json.get('processing', {}).get('stage')
            if file_type:
                out_row["file_type"] = file_type

        # Fallback to filename parsing to fill missing values
        name = p.name
        if name.lower().endswith(".parquet"):
            base = name[:-len(".parquet")]
        else:
            base = name

        # Guard: plain input file (no suffix) is not a sidecar
        if not meta_json and base == stem:
            logger.debug(f"Skipping base input file without sidecar suffix: {p.name}")
            continue

        tail = base[len(stem) + 1:] if len(base) > len(stem) else ""
        if tail:
            meta = {}
            for seg in tail.split("."):
                for group in seg.split("_"):
                    if "-" not in group:
                        continue
                    k, v = group.split("-", 1)
                    if not k:
                        continue
                    meta[k] = v

            # Map 'assignment' -> 'mode' for compatibility
            if "assignment" in meta and "mode" not in meta:
                meta["mode"] = meta.pop("assignment")

            # Fill only missing fields
            for k, v in meta.items():
                if k not in out_row:
                    out_row[k] = v

        # Cast nside to int if present
        if "nside" in out_row:
            try:
                out_row["nside"] = int(out_row["nside"])
            except Exception:
                pass

        # Optional lightweight stats
        if read_stats:
            n_rows = None
            n_unique = None
            try:
                df = pd.read_parquet(p, columns=["source_id", "healpix_id"])
                n_rows = int(len(df))
                if "healpix_id" in df.columns:
                    n_unique = int(df["healpix_id"].nunique())
            except Exception as e:
                logger.warning(f"Could not read stats from {p.name}: {e}")
                n_rows = None
                n_unique = None
            out_row["n_rows"] = n_rows
            out_row["n_unique_healpix"] = n_unique

        # ADR-015 provenance: bit-shift derivation info
        if meta_json:
            pp = meta_json.get("processing_params", {})
            derived = pp.get("derived_from_parent")
            if derived is not None:
                out_row["derived_from_parent"] = int(derived)

        rows.append(out_row)

    if not rows:
        logger.warning(f"No sidecar files found matching stem: {stem}")
        return pd.DataFrame(
            columns=[
                "file",
                "coalesced",
                "mode",
                "nside",
                "order",
                "n_rows",
                "n_unique_healpix",
            ],
        )

    df_out = pd.DataFrame(rows)

    # Ensure nside/int dtypes where possible
    if "nside" in df_out.columns:
        try:
            df_out["nside"] = pd.to_numeric(df_out["nside"], errors="coerce").astype("Int64")
        except Exception:
            pass

    # Sort by nside then mode if available
    sort_keys = [k for k in ("nside", "mode") if k in df_out.columns]
    if sort_keys:
        df_out = df_out.sort_values(sort_keys).reset_index(drop=True)
    else:
        df_out = df_out.reset_index(drop=True)

    logger.info(f"Found {len(df_out)} sidecar file(s)")
    return df_out

def get_numeric_columns(file_path: Path) -> List[str]:
    """Return all float/double column names from a parquet file.

    Used by --all-columns to auto-discover aggregation targets.
    Excludes int, string, bool, and other non-float types.
    """
    try:
        pf = pq.ParquetFile(file_path)
        schema = pf.schema_arrow
        numeric = []
        for field in schema:
            if pa.types.is_floating(field.type):
                numeric.append(field.name)
        return numeric
    except Exception as e:
        logger.error(f"Failed to read schema from {file_path}: {e}")
        return []


def print_dry_run_summary(
    input_file: Path,
    sidecar_path: Path,
    output_path: Path,
    columns: List[str],
    aggs: List[str],
    filter_expr: Optional[str],
    min_count: int,
    densify: bool,
    use_duckdb: bool,
    use_dask: bool,
    dask_npartitions: Optional[int]
) -> None:
    """Print a summary of what would be executed (dry-run mode)."""
    print("\n" + "=" * 80)
    print("DRY RUN - No files will be modified")
    print("=" * 80)
    print(f"\nInput File:        {input_file}")
    print(f"Sidecar File:      {sidecar_path.name}")
    print(f"Output File:       {output_path}")
    print(f"\nValue Columns:     {', '.join(columns)}")
    print(f"Aggregations:      {', '.join(aggs)}")
    print(f"Filter Expression: {filter_expr or '(none)'}")
    print(f"Min Count:         {min_count}")
    print(f"Densify:           {densify}")
    print(f"\nBackend:")
    print(f"  DuckDB:          {use_duckdb}")
    print(f"  Dask:            {use_dask}")
    if use_dask and dask_npartitions:
        print(f"  Dask Partitions: {dask_npartitions}")
    print("\n" + "=" * 80)

def densify_healpix_aggregates(
    agg_sparse_df: pd.DataFrame,
    nside: int,
    healpix_col: str = "healpix_id"
) -> pd.DataFrame:
    """Densify aggregated DataFrame to include all HEALPix cells.

    Expands a sparse aggregation result (only observed cells) to the full
    HEALPix grid (all ``12 * nside²`` cells). Missing cells are filled with
    NaN for numeric columns and None for others.

    Parameters
    ----------
    agg_sparse_df : pd.DataFrame
        Aggregated DataFrame with ``healpix_id`` as index (sparse —
        only observed cells).
    nside : int
        HEALPix nside parameter.
    healpix_col : str
        Name of the HEALPix ID column. Default: ``'healpix_id'``.

    Returns
    -------
    pd.DataFrame
        Densified DataFrame with RangeIndex ``[0, 12*nside²)`` as index,
        containing all HEALPix cells.
    """
    import healpy as hp

    n_pixels = hp.nside2npix(nside)
    full_index = pd.RangeIndex(start=0, stop=n_pixels, name=healpix_col)

    # Reindex to full grid, filling missing with NaN
    densified = agg_sparse_df.reindex(full_index)

    logger.info(f"Densified from {len(agg_sparse_df)} to {len(densified)} cells (nside={nside})")
    return densified

def aggregate_by_sidecar(
    original: pd.DataFrame,
    sidecar: pd.DataFrame,
    value_columns: Sequence[str],
    aggs: Optional[Sequence[str]] = None,
    min_count: int = 1,
    source_id_col: str = "source_id",
    healpix_col: str = "healpix_id",
    sentinel_threshold: float = 1e30,
) -> pd.DataFrame:
    """Aggregate observation data by HEALPix cells using a sidecar mapping.

    This is the core aggregation function. It merges the original observation
    DataFrame with the sidecar mapping, groups by ``healpix_id``, and computes
    the requested summary statistics for each value column.

    Features:
    * Automatically masks extreme sentinel values (configurable threshold).
    * Warns when ``min_count=0`` is used (rarely correct).
    * Reports source_id overlap between sidecar and original data.
    * Uses robust statistics (MAD, robust_std) alongside classical ones.

    Parameters
    ----------
    original : pd.DataFrame
        DataFrame with observation data. Must contain ``source_id`` column
        (implicit index or explicit column) and the ``value_columns``.
    sidecar : pd.DataFrame
        DataFrame with ``source_id`` → ``healpix_id`` mapping. In fuzzy mode,
        one source may map to multiple cells (multiple rows per source_id).
    value_columns : Sequence[str]
        Column names in ``original`` to aggregate.
    aggs : Sequence[str] or None
        Aggregation function names. Default: ``['mean', 'median', 'std',
        'robust_std']``. Valid options: ``mean``, ``median``, ``std``,
        ``min``, ``max``, ``mad``, ``robust_std``.
    min_count : int
        Minimum number of observations per HEALPix cell for valid
        aggregation. Cells with fewer observations output NaN for all
        statistics. Default is 1.
    source_id_col : str
        Name of the source ID column. Default: ``'source_id'``.
    healpix_col : str
        Name of the HEALPix ID column. Default: ``'healpix_id'``.
    sentinel_threshold : float
        Absolute value threshold for masking sentinel/extreme values.
        Values with ``|value| >= threshold`` are replaced with NaN.
        Default: ``1e30``.

    Returns
    -------
    pd.DataFrame
        Aggregated DataFrame with ``healpix_id`` as index and columns
        named ``{value_column}_{aggregation}`` (e.g., ``r1050_mean``).
    """

    if min_count == 0:
        logger.warning(
            "min_count=0 detected: cells with zero sources will produce NaN statistics. "
            "This is rarely correct. Consider --min-count 2 or higher for robust aggregation."
        )

    if aggs is None:
        aggs = ['mean', 'median', 'std', 'robust_std']

    # Validate aggregation functions
    invalid_aggs = [a for a in aggs if a not in AGG_LOOKUP]
    if invalid_aggs:
        raise ValueError(f"Invalid aggregation functions: {invalid_aggs}. "
                        f"Valid options: {list(AGG_LOOKUP.keys())}")

    # Validate required columns
    if source_id_col not in sidecar.columns or healpix_col not in sidecar.columns:
        raise KeyError(f"Sidecar must contain '{source_id_col}' and '{healpix_col}' columns")

    # Ensure sidecar source_id dtype is int64
    logger.debug("Preparing sidecar source_id column")
    sidecar = sidecar.copy()
    try:
        sidecar[source_id_col] = sidecar[source_id_col].astype("int64")
    except Exception:
        sidecar[source_id_col] = pd.to_numeric(sidecar[source_id_col], errors="coerce").astype("Int64").astype("int64")

    # Prepare original DataFrame
    logger.debug("Preparing original DataFrame")
    orig = original.copy()

    # Create source_id if not present (use index)
    if source_id_col not in orig.columns:
        logger.info(f"Creating {source_id_col} column from DataFrame index")
        orig = orig.reset_index().rename(columns={"index": source_id_col})

    # Coerce original source_id dtype to int64
    try:
        orig[source_id_col] = orig[source_id_col].astype("int64")
    except Exception:
        orig[source_id_col] = pd.to_numeric(orig[source_id_col], errors="coerce").astype("Int64").astype("int64")

    # Check for duplicates
    if orig[source_id_col].duplicated().any():
        dup_count = int(orig[source_id_col].duplicated().sum())
        logger.warning(f"Found {dup_count} duplicate source_id values - keeping first occurrence")
        orig = orig.drop_duplicates(subset=source_id_col, keep="first")

    # Coerce value columns to numeric and mask sentinel values
    logger.debug("Processing value columns")
    for col in value_columns:
        orig[col] = pd.to_numeric(orig[col], errors="coerce")

        # Mask extreme sentinel values
        try:
            mask_big = orig[col].abs() >= float(sentinel_threshold)
        except Exception:
            mask_big = pd.Series(False, index=orig.index)

        if mask_big.any():
            n_masked = int(mask_big.sum())
            logger.info(f"Masking {n_masked} sentinel values in column '{col}' (>= {sentinel_threshold})")
            orig.loc[mask_big, col] = np.nan

    # Keep only necessary columns
    cols_to_keep = [source_id_col] + list(value_columns)
    orig = orig[cols_to_keep]

    # Diagnostic: check overlap between sidecar and original
    side_ids = pd.Index(sidecar[source_id_col].unique())
    orig_ids = pd.Index(orig[source_id_col].unique())
    inter = side_ids.intersection(orig_ids)

    if len(side_ids) == 0:
        logger.error("Sidecar contains 0 source_ids")
        raise ValueError("Sidecar is empty")

    pct = 100.0 * len(inter) / len(side_ids)
    logger.info(f"Sidecar source_id overlap: {len(inter)}/{len(side_ids)} ({pct:.1f}%)")

    if len(inter) < len(side_ids):
        n_missing = len(side_ids) - len(inter)
        logger.warning(f"{n_missing} source_ids in sidecar not found in original data")
        if logger.level <= logging.DEBUG:
            missing_sample = list(side_ids.difference(orig_ids)[:10])
            logger.debug(f"Sample missing source_ids: {missing_sample}")

    # Merge sidecar with original data
    logger.info("Merging sidecar with original data")
    merged = sidecar[[source_id_col, healpix_col]].merge(
        orig, on=source_id_col, how="left"
    )
    logger.debug(f"Merged dataframe shape: {merged.shape}")

    # Group by healpix_id and aggregate
    logger.info(f"Grouping by {healpix_col} and computing aggregations")
    rows: list[dict] = []
    grouped = merged.groupby(healpix_col, sort=True)
    n_groups = len(grouped)
    logger.info(f"Processing {n_groups} HEALPix cells")

    # Create progress bar
    pbar = tqdm(
        grouped,
        total=n_groups,
        desc="Aggregating HEALPix cells",
        unit="cell",
        disable=(logger.level > logging.INFO)  # Disable if quiet mode
    )

    for hp_value, grp in pbar:
        row: dict = {healpix_col: hp_value}
        n_sources = int(len(grp))

        # Apply min_count threshold
        if n_sources < int(min_count):
            logger.debug(f"Cell {hp_value}: {n_sources} sources < min_count={min_count}, setting to NaN")
            # Set all aggregations to NaN for this cell
            for col in value_columns:
                for agg in aggs:
                    row[f"{col}_{agg}"] = float("nan")
            row["n_sources"] = n_sources
        else:
            # Compute aggregations for each column
            for col in value_columns:
                arr = grp[col].to_numpy()
                for agg in aggs:
                    func = AGG_LOOKUP[agg]
                    row[f"{col}_{agg}"] = func(arr)
            row["n_sources"] = n_sources

        rows.append(row)

    pbar.close()

    # Build result DataFrame
    result = pd.DataFrame(rows)
    result = result.set_index(healpix_col)

    logger.info(f"Aggregation complete: {len(result)} cells with data")
    return result

class CustomFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog):
        # Set the width here (120) while keeping RawDescription features
        super().__init__(prog, width=120)

def _get_config(config, key, default=None):
    """Access config value from dict or argparse Namespace."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)

def parse_arguments(argv=None) -> argparse.Namespace:
    """Parse command-line arguments with educational help text."""
    parser = argparse.ArgumentParser(
        description="Aggregate spatial data by HEALPix cells using sidecar files (split-apply-combine workflow)",
        formatter_class=CustomFormatter,
        epilog="""
EXAMPLES:

  # Inspect input file schema
  %(prog)s -i data.parquet --schema

  # List available sidecar files with statistics
  %(prog)s -i data.parquet --list-sidecars --stats

  # Single sidecar aggregation with robust statistics
  %(prog)s -i data.parquet --sidecar-index 0 --aggregate \\
    --columns reflectance radiance --aggs mean median robust_std \\
    --min-count 2

  # Batch process all sidecars and densify output
  %(prog)s -i data.parquet --sidecar-index all --aggregate \\
    --columns value --densify -y

  # Filter data before aggregation (only observations with QA > 0.8)
  %(prog)s -i data.parquet --sidecar-index 0 --aggregate \\
    --columns value --filter 'qa_flag > 0.8'

  # Use DuckDB for efficient loading of large files (default behavior)
  %(prog)s -i data.parquet --sidecar-index 0 --aggregate \\
    --columns reflectance --use-duckdb

  # Process with Dask for parallel aggregation (8 partitions)
  %(prog)s -i data.parquet --sidecar-index all --aggregate \\
    --columns value --use-dask --dask-npartitions 8

  # Dry-run: preview what would be done without writing
  %(prog)s -i data.parquet --sidecar-index 0 --aggregate \\
    --columns value --dry-run

  # Verbose logging for debugging failed aggregations
  %(prog)s -i data.parquet --sidecar-index 0 --aggregate \\
    --columns value --verbose

  # Batch process with per-sidecar error tolerance (skip failures)
  %(prog)s -i data.parquet --sidecar-index all --aggregate \\
    --columns value
  # vs. stop on first error:
  %(prog)s -i data.parquet --sidecar-index all --aggregate \\
    --columns value --stop-on-error
        """
    )

    # =========================================================================
    # INPUT/OUTPUT
    # =========================================================================
    parser.add_argument(
        '-i', '--input',
        type=Path,
        required=True,
        metavar='FILE',
        help='Input parquet file with observational data (required). '
             'Must contain a "source_id" column and value columns specified by --columns.'
    )

    parser.add_argument(
        '-d', '--sidecar-dir',
        type=Path,
        default=None,
        metavar='DIR',
        help='Directory containing sidecar files (default: same directory as input file). '
             'Sidecars are HEALPix cell assignments generated by `healpix_sidecar`. '
             'Script auto-detects files matching input stem and .meta.json metadata.'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        nargs='?',
        const=None,
        default=None,
        metavar='FILE',
        help='Output parquet file or directory (default: auto-generated from sidecar metadata). '
             'If directory, filename is auto-generated as: '
             '<input_stem>-aggregated[-densified].<sidecar_suffix>.parquet. '
             'Auto-naming ensures consistent filenames across batch runs.'
    )

    # =========================================================================
    # AGGREGATION CONTROL
    # =========================================================================
    sel_group = parser.add_mutually_exclusive_group()
    sel_group.add_argument(
        '--sidecar-index',
        type=str,
        nargs='+',
        metavar='INDEX',
        help='Select which sidecar(s) to process by position (sorted by nside ascending). Three modes:\n'
             '  --sidecar-index all           Process all sidecars in batch mode\n'
             '  --sidecar-index 0             Process single sidecar (index 0)\n'
             '  --sidecar-index 0 2 4         Process specific sidecars (batch mode)\n'
             'Mutually exclusive with --nside. Use --list-sidecars to see available indices.\n'
             'Batch mode processes all specified sidecars sequentially, aggregating '
             'by different HEALPix resolutions in one command.'
    )
    sel_group.add_argument(
        '--nside',
        type=int,
        nargs='+',
        metavar='NSIDE',
        help='Select sidecar(s) by HEALPix resolution (e.g. --nside 256). '
             'Mutually exclusive with --sidecar-index. Preferred: unambiguous and '
             'independent of the sidecar discovery ordering.'
    )

    parser.add_argument(
        '--aggregate',
        action='store_true',
        help='Perform aggregation (required flag to enable processing). '
             'Must be paired with --sidecar-index or --nside, and either --columns or '
             '--all-columns. Without this flag, only schema inspection operations run.'
    )

    col_group = parser.add_mutually_exclusive_group()
    col_group.add_argument(
        '--columns',
        nargs='+',
        metavar='COL',
        help='Value columns to aggregate (space-separated; required for --aggregate). '
             'Example: --columns reflectance radiance emission. '
             'Each column will have aggregation functions applied (mean, median, std, etc.). '
             'Columns must exist in input parquet file (verify with --schema first). '
             'Mutually exclusive with --all-columns.'
    )
    col_group.add_argument(
        '--all-columns',
        action='store_true',
        help='Aggregate all float/double columns in the input parquet file. '
             'Automatically discovers numeric columns from the schema. '
             'Useful when the column list is long or changes frequently. '
             'Mutually exclusive with --columns.'
    )

    # =========================================================================
    # AGGREGATION STATISTICS
    # =========================================================================
    parser.add_argument(
        '--aggs',
        nargs='+',
        metavar='AGG',
        choices=list(AGG_LOOKUP.keys()),
        help=f"Aggregation functions to compute (space-separated; default: mean median std robust_std). "
             f"Choices: {', '.join(AGG_LOOKUP.keys())}. "
             f"Robust options (mad, robust_std) recommended for outlier-prone data. "
             f"Each function produces one output column per value column: "
             f"<column>_<agg> (e.g., reflectance_mad, reflectance_robust_std)."
    )

    parser.add_argument(
        '--min-count',
        type=int,
        default=1,
        metavar='N',
        help='Minimum observations per HEALPix cell for valid aggregation (default: 1). '
             'Cells with fewer than N observations output NaN for all statistics. '
             'Domain guidance:\n'
             '  min-count 1   — Include any cell touched by data (permissive)\n'
             '  min-count 2-3 — Recommended for robust_std/MAD (handle outliers)\n'
             '  min-count 5+  — Strict validation (reject sparse cells)\n'
             '  min-count 0   — RARE: explicitly allow NaN-only cells (use only if you '
             'understand the consequence). Default changed from 0→1 to prevent silent '
             'propagation of empty cells.'
    )

    parser.add_argument(
        '--filter',
        type=str,
        metavar='EXPR',
        help='Pandas query expression to filter observations before aggregation '
             '(e.g., --filter "(qa_flag > 0.8) and (wavelength < 1000)"). '
             'Applied during data load, reducing I/O and memory footprint. '
             'Boolean operators: and, or, not. Parentheses required. '
             'Column names must match input parquet exactly (case-sensitive). '
             'Useful for quality filtering, spectral selection, or temporal subsetting. '
             'Filtering happens *before* aggregation, so min-count applies to filtered data.'
    )

    parser.add_argument(
        '--densify',
        action='store_true',
        help='Densify output to include all HEALPix cells (0 to 12*nside²-1), '
             'filling unobserved cells with NaN. Output shape: n_pixels × n_aggregations. '
             'Enables uniform grids for visualization and downstream analysis (e.g., map-making). '
             'Trade-off: sparse output (only observed cells) is smaller; '
             'dense output is larger but suitable for rasterization and numpy operations. '
             'Requires nside from sidecar metadata (auto-detected).'
    )

    # =========================================================================
    # DATA LOADING BACKENDS
    # =========================================================================
    parser.add_argument(
        '--use-duckdb',
        action='store_true',
        default=True,
        help='Use DuckDB for efficient data loading (default: enabled). '
             'DuckDB performs column projection and WHERE filtering at the parquet '
             'layer, reducing memory and I/O. Recommended for large files (>1GB). '
             'If DuckDB unavailable, falls back to pandas/dask automatically. '
             'See --no-duckdb to disable.'
    )

    parser.add_argument(
        '--no-duckdb',
        action='store_true',
        help='Disable DuckDB and use pandas/dask for data loading instead. '
             'Use if DuckDB is unavailable or causes issues. '
             'Pandas will load full columns into memory; less efficient for wide tables '
             'or large files. Dask (if enabled) provides parallel reads as fallback.'
    )

    parser.add_argument(
        '--use-dask',
        action='store_true',
        help='Use Dask for parallel aggregation of large datasets (works with/without DuckDB). '
             'Dask partitions HEALPix groupby operations across CPU cores, '
             'speeding up aggregation of cells with many observations per partition. '
             'Trade-off: overhead for small datasets (<100k rows); beneficial for >1M rows. '
             'Combine with --dask-npartitions to control parallelism. '
             'Requires: pip install dask[dataframe].'
    )

    parser.add_argument(
        '--dask-npartitions',
        type=int,
        metavar='N',
        help='Number of Dask partitions for parallel processing (default: CPU count - 1). '
             'Each partition processes a subset of rows independently, then results merge. '
             'Tuning guidance:\n'
             '  N = CPU cores     — Start here (good I/O balance)\n'
             '  N = 2 × CPU cores — For I/O-bound workloads (network storage)\n'
             '  N = 1             — Debug mode (no parallelism, easier tracing)\n'
             'Requires --use-dask. Ignored if Dask unavailable.'
    )

    # =========================================================================
    # BATCH PROCESSING & ERROR HANDLING
    # =========================================================================
    parser.add_argument(
        '--stop-on-error',
        action='store_true',
        help='Stop batch processing on first error (default: skip failed sidecar, continue). '
             'Default behavior: collect errors and report summary. '
             'Use --stop-on-error for strict validation (e.g., CI/CD pipelines). '
             'Batch results logged at end with success/error counts and paths.'
    )

    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Skip overwrite prompts and force output file write (non-interactive mode). '
             'Use in batch scripts/cron jobs to avoid hanging on existing output files. '
             'Without -y, user is prompted before overwriting.'
    )

    parser.add_argument(
        '-n', '--dry-run',
        action='store_true',
        help='Show what would be done without modifying files (preview mode). '
             'Prints: input/output paths, columns, aggregations, filters, backend config. '
             'Useful for validating command-line arguments before committing to long runs. '
             'No parquet files are written in dry-run mode.'
    )

    # =========================================================================
    # LOGGING & VERBOSITY
    # =========================================================================
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable debug-level logging (default: INFO). '
             'Prints detailed progress: cell-by-cell statistics, column type coercions, '
             'source_id overlap analysis, metadata parsing. '
             'Use when debugging data quality issues or tracing slow aggregations.'
    )

    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress all output except errors (default: INFO level). '
             'Useful for production runs where you only want to see failures. '
             'Combines well with --yes for silent batch execution.'
    )

    # =========================================================================
    # PARSE & POST-PROCESSING
    # =========================================================================
    args = parser.parse_args(argv)

    # Set default sidecar_dir if not provided
    if args.sidecar_dir is None:
        args.sidecar_dir = args.input.parent

    # Validate --sidecar-index format: "all" string or list of integers
    if args.sidecar_index is not None:
        if len(args.sidecar_index) == 1 and args.sidecar_index[0].lower() == 'all':
            args.sidecar_index = ['all']
        else:
            try:
                args.sidecar_index = [int(idx) for idx in args.sidecar_index]
            except ValueError:
                parser.error("--sidecar-index must be 'all' or space-separated integers (e.g., 0 2 4)")

    return args

def process_single_sidecar(
    input_file: Path,
    sidecar_path: Path,
    sidecar_metadata_json: Dict,
    sidecars_df: pd.DataFrame,
    sidecar_index: int,
    args: argparse.Namespace,
    use_duckdb: bool
) -> Dict:
    """Process a single sidecar file through the full aggregation workflow.

    This function orchestrates one sidecar through the complete aggregation
    pipeline: load data, merge with sidecar, compute aggregations,
    optionally densify, and write output with metadata.

    Parameters
    ----------
    input_file : Path
        Original input parquet file.
    sidecar_path : Path
        Path to sidecar parquet file.
    sidecar_metadata_json : dict
        Loaded metadata from ``.meta.json`` companion file.
    sidecars_df : pd.DataFrame
        DataFrame collected by :func:`collect_sidecar_outputs` with all
        available sidecar metadata.
    sidecar_index : int
        Index of this sidecar in ``sidecars_df``.
    args : argparse.Namespace
        Parsed command-line arguments controlling aggregation behavior.
    use_duckdb : bool
        Whether DuckDB is available and enabled for data loading.

    Returns
    -------
    dict
        Processing summary with keys: ``status`` (``'success'``,
        ``'skip'``, ``'error'``), ``output_path``, ``metadata_path``,
        ``n_cells``, ``error`` (if any).
    """
    result = {
        'sidecar_index': sidecar_index,
        'sidecar_path': str(sidecar_path),
        'status': 'success',
        'error': None,
        'output_path': None
    }

    try:
        logger.info(f"Processing sidecar [{sidecar_index}]: {sidecar_path.name}")

        # Determine output path with densification marker
        if args.output is None:
            output_dir = args.sidecar_dir if args.sidecar_dir.exists() else Path.cwd()
            output_path = generate_output_filename(input_file, sidecar_path, output_dir, densified=args.densify)
            logger.info(f"Generated output filename: {output_path}")
        elif args.output.is_dir():
            output_path = generate_output_filename(input_file, sidecar_path, args.output, densified=args.densify)
            logger.info(f"Output is directory, using: {output_path}")
        elif args.output.suffix == '':
            args.output.mkdir(parents=True, exist_ok=True)
            output_path = generate_output_filename(input_file, sidecar_path, args.output, densified=args.densify)
            logger.info(f"Output has no extension (directory), using: {output_path}")
        else:
            output_path = args.output

        result['output_path'] = str(output_path)

        # Check for existing file and prompt for overwrite
        if output_path.exists() and not args.yes:
            logger.warning(f"Output file already exists: {output_path.name}")
            response = input("Overwrite? [y/N] ").strip().lower()
            if response != 'y':
                result['status'] = 'skipped'
                result['error'] = "User declined overwrite"
                logger.info("Skipping this sidecar")
                return result

        # Dry run: show what would be done
        if args.dry_run:
            aggs_to_use = args.aggs or ['mean', 'median', 'std', 'robust_std']
            use_duckdb_check = args.use_duckdb and not args.no_duckdb and DUCKDB_AVAILABLE
            use_dask_check = args.use_dask and DASK_AVAILABLE

            print_dry_run_summary(
                input_file=input_file,
                sidecar_path=sidecar_path,
                output_path=output_path,
                columns=args.columns,
                aggs=aggs_to_use,
                filter_expr=args.filter,
                min_count=args.min_count,
                densify=args.densify,
                use_duckdb=use_duckdb_check,
                use_dask=use_dask_check,
                dask_npartitions=args.dask_npartitions
            )
            result['status'] = 'dry_run'
            return result

        # Determine columns to load
        cols_needed = list(args.columns)
        source_id_col = 'source_id'

        try:
            pf = pq.ParquetFile(input_file)
            available_cols = pf.schema_arrow.names

            if source_id_col in available_cols:
                if source_id_col not in cols_needed:
                    cols_needed = [source_id_col] + cols_needed
            else:
                logger.info(f"Column '{source_id_col}' not found - will use DataFrame index")
        except Exception as e:
            logger.warning(f"Could not read parquet schema: {e}")

        # Load data with appropriate backend
        if use_duckdb:
            logger.info(f"Loading data with DuckDB (efficient column selection)")
            logger.info(f"Columns to load: {cols_needed}")

            try:
                cols_str = ', '.join(f'"{col}"' for col in cols_needed)

                if args.filter:
                    query = f"SELECT {cols_str} FROM read_parquet('{input_file}') WHERE {args.filter}"
                    logger.info(f"Applying filter during scan: {args.filter}")
                else:
                    query = f"SELECT {cols_str} FROM read_parquet('{input_file}')"

                logger.debug(f"DuckDB query: {query}")
                df = duckdb.query(query).to_df()
                logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")

            except Exception as e:
                logger.error(f"DuckDB query failed: {e}")
                logger.error("Falling back to pandas")
                use_duckdb = False

        if not use_duckdb:
            logger.info(f"Loading data with {'Dask' if args.use_dask else 'pandas'}")
            logger.info(f"Columns to load: {cols_needed}")

            if args.use_dask:
                npartitions = args.dask_npartitions or max(1, (os.cpu_count() or 2) - 1)
                logger.info(f"Using {npartitions} Dask partitions")

                try:
                    ddf = dd.read_parquet(input_file, engine='pyarrow', columns=cols_needed)
                    if ddf.npartitions != npartitions:
                        logger.debug(f"Repartitioning from {ddf.npartitions} to {npartitions} partitions")
                        ddf = ddf.repartition(npartitions=npartitions)
                except Exception as e:
                    logger.error(f"Failed to load with Dask: {e}")
                    raise

                if args.filter:
                    logger.info(f"Applying filter: {args.filter}")
                    try:
                        ddf = ddf.query(args.filter)
                    except Exception as e:
                        logger.error(f"Filter failed: {e}")
                        raise

                logger.info("Computing to pandas DataFrame...")
                df = ddf.compute()
                logger.info(f"Loaded {len(df)} rows")
            else:
                try:
                    df = pd.read_parquet(input_file, columns=cols_needed)
                    logger.info(f"Loaded {len(df)} rows")
                except Exception as e:
                    logger.error(f"Failed to load with pandas: {e}")
                    raise

                if args.filter:
                    logger.info(f"Applying filter: {args.filter}")
                    try:
                        df = df.query(args.filter)
                        logger.info(f"After filtering: {len(df)} rows")
                    except Exception as e:
                        logger.error(f"Filter failed: {e}")
                        raise

        # Load sidecar
        logger.info(f"Loading sidecar from {sidecar_path}")
        sidecar = pd.read_parquet(sidecar_path)
        logger.info(f"Sidecar contains {len(sidecar)} mappings")

        # Perform aggregation
        logger.info("Starting aggregation")
        agg_result = aggregate_by_sidecar(
            original=df,
            sidecar=sidecar,
            value_columns=args.columns,
            aggs=args.aggs,
            min_count=args.min_count,
        )

        logger.info(f"Aggregation complete: {len(agg_result)} HEALPix cells")

        # Apply densification if requested
        if args.densify:
            logger.info("Densifying output to full HEALPix grid")
            nside_value = sidecars_df.iloc[sidecar_index].get('nside')
            if nside_value is None or pd.isna(nside_value):
                logger.error("Cannot densify: nside not found in sidecar metadata")
                raise ValueError("nside required for densification")
            agg_result = densify_healpix_aggregates(agg_result, int(nside_value))

        # Save output
        logger.info(f"Writing output to {output_path}")

        # Build aggregation metadata
        sidecar_metadata = sidecars_df.iloc[sidecar_index].to_dict()

        output_metadata = {
            'processing': {
                'stage': 'aggregate',
                'timestamp': datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                'source_file': str(input_file),
                'sidecar_file': str(sidecar_path),
                'output_file': str(output_path.absolute())
            },
            'aggregation': {
                'value_columns': args.columns,
                'aggregations': args.aggs or ['mean', 'median', 'std', 'robust_std'],
                'filter_query': args.filter or None,
                'min_count': args.min_count,
                'densified': args.densify,
                'n_cells_with_data': len(agg_result),
                'columns_loaded': cols_needed,
                'output_shape': {'rows': agg_result.shape[0], 'cols': agg_result.shape[1]}
            },
            'backend': {
                'used_duckdb': use_duckdb,
                'used_dask': args.use_dask,
                'dask_npartitions': args.dask_npartitions if args.use_dask else None
            },
            'sidecar_metadata': sidecar_metadata_json
        }

        # Legacy flat metadata for backwards compatibility
        output_metadata['_legacy'] = {
            'healpix_mode': sidecar_metadata.get('mode', 'unknown'),
            'healpix_nside': str(sidecar_metadata.get('nside', '')),
            'healpix_order': sidecar_metadata.get('order', 'unknown'),
        }

        agg_result.to_parquet(output_path, index=True)

        # Write metadata as separate JSON sidecar
        from healpyxel.metadata import HEALPyxelxMetadata
        metadata_path = HEALPyxelxMetadata.write_json(
            output_metadata,
            output_path,
            validate=False
        )

        logger.info(f"Wrote metadata to {metadata_path}")
        logger.info(f"Successfully processed sidecar [{sidecar_index}]")

        result['metadata_path'] = str(metadata_path)
        result['n_cells'] = len(agg_result)

        return result

    except Exception as e:
        logger.error(f"Failed to process sidecar [{sidecar_index}]: {e}")
        result['status'] = 'error'
        result['error'] = str(e)

        if args.stop_on_error:
            raise

def _is_interactive_session() -> bool:
    """Return True when running inside IPython/Jupyter."""
    try:
        get_ipython  # type: ignore[name-defined]
        return True
    except Exception:
        return False

def run(config):
    """Run aggregation pipeline from a config dict or argparse Namespace."""
    args = config
    if isinstance(config, dict):
        import argparse
        # Build a namespace from dict for backward compatibility with process_single_sidecar
        args = argparse.Namespace(**{k: v for k, v in config.items() if v is not None})
        # Apply parse_arguments post-processing (sidecar_dir default, sidecar_index normalization)
        if not hasattr(args, 'sidecar_dir') or args.sidecar_dir is None:
            args.sidecar_dir = args.input.parent if hasattr(args.input, 'parent') else Path('.')
        if hasattr(args, 'sidecar_index') and args.sidecar_index is not None:
            if len(args.sidecar_index) == 1 and args.sidecar_index[0].lower() == 'all':
                args.sidecar_index = ['all']
            else:
                args.sidecar_index = [int(idx) for idx in args.sidecar_index]

    # Resolve --all-columns into args.columns so downstream code sees a single list
    if getattr(args, 'all_columns', False):
        if not hasattr(args, 'columns') or args.columns is None:
            args.columns = get_numeric_columns(args.input)
            if not args.columns:
                raise RuntimeError(f"No float/double columns found in {args.input}. Cannot use --all-columns.")
            logger.info(f"--all-columns: discovered {len(args.columns)} numeric columns")
        args.all_columns = False  # resolve to avoid confusion downstream

    if _is_interactive_session():
        logger.info("Interactive session detected; skipping CLI argument parsing.")
        return 0

    # Configure logging level
    if _get_config(config, 'quiet'):
        logger.setLevel(logging.ERROR)
    elif _get_config(config, 'verbose'):
        logger.setLevel(logging.DEBUG)

    input_file = _get_config(config, 'input')
    if not input_file.exists():
        raise RuntimeError(f"Input file not found: {input_file}")

    if not input_file.is_file():
        raise RuntimeError(f"Not a file: {input_file}")

    # Aggregation (single or batch mode)
    sidecar_index = _get_config(config, 'sidecar_index')
    columns = _get_config(config, 'columns')
    all_columns = _get_config(config, 'all_columns', False)
    if sidecar_index or _get_config(config, 'nside') or _get_config(config, 'aggregate'):
        if sidecar_index is None and _get_config(config, 'nside') is None:
            raise RuntimeError("--sidecar-index or --nside is required when using --aggregate")
        if all_columns:
            columns = get_numeric_columns(input_file)
            if not columns:
                raise RuntimeError(f"No float/double columns found in {input_file}. Cannot use --all-columns.")
            logger.info(f"--all-columns: discovered {len(columns)} numeric columns: {columns}")
        elif not columns:
            raise RuntimeError("--columns is required when using --aggregate")

        sidecar_dir = args.sidecar_dir
        if not sidecar_dir.exists():
            raise RuntimeError(f"Sidecar directory not found: {sidecar_dir}")

        sidecars_df = collect_sidecar_outputs(input_file, sidecar_dir, read_stats=False)

        if len(sidecars_df) == 0:
            raise RuntimeError("No sidecar files found")

        nside_sel = _get_config(config, 'nside')
        if nside_sel is not None:
            # Resolve requested nside(s) to positional indices in the sorted sidecar table
            indices_to_process = []
            for n in nside_sel:
                matches = sidecars_df.index[sidecars_df['nside'] == n].tolist()
                if not matches:
                    raise RuntimeError(f"nside={n} not found among sidecars. "
                                       f"Available: {sorted(sidecars_df['nside'].dropna().astype(int).unique().tolist())}")
                indices_to_process.append(int(matches[0]))
            logger.info(f"Selected sidecar(s) by nside: {list(zip(nside_sel, indices_to_process))}")
        elif sidecar_index == ['all']:
            indices_to_process = list(range(len(sidecars_df)))
            logger.info(f"Batch mode: processing all {len(indices_to_process)} sidecar(s)")
        else:
            indices_to_process = sidecar_index
            invalid_indices = [idx for idx in indices_to_process if idx < 0 or idx >= len(sidecars_df)]
            if invalid_indices:
                raise RuntimeError(f"Invalid sidecar indices: {invalid_indices}. Valid range: 0-{len(sidecars_df)-1}")
            logger.info(f"Batch mode: processing {len(indices_to_process)} sidecar(s): {indices_to_process}")

        use_duckdb = _get_config(config, 'use_duckdb', True) and not _get_config(config, 'no_duckdb', False)
        if use_duckdb and not DUCKDB_AVAILABLE:
            logger.warning("DuckDB not available. Install with: pip install duckdb")
            logger.warning("Falling back to pandas/dask")
            use_duckdb = False

        use_dask = _get_config(config, 'use_dask', False)
        if use_dask and not DASK_AVAILABLE:
            logger.warning("Dask not available. Install with: pip install dask[dataframe]")
            logger.warning("Disabling Dask")
            use_dask = False

        batch_results = []

        for idx in indices_to_process:
            sidecar_path = Path(sidecars_df.iloc[idx]['file'])

            try:
                sidecar_metadata_json = validate_sidecar_metadata(
                    sidecar_path, input_file, require_metadata=False
                )
            except ValueError as e:
                logger.error(f"Metadata validation failed for sidecar [{idx}]: {e}")
                if _get_config(config, 'stop_on_error'):
                    raise RuntimeError(f"Metadata validation failed: {e}")
                batch_results.append({
                    'sidecar_index': idx, 'sidecar_path': str(sidecar_path),
                    'status': 'error', 'error': f"Metadata validation failed: {e}"
                })
                continue

            try:
                result = process_single_sidecar(
                    input_file=input_file,
                    sidecar_path=sidecar_path,
                    sidecar_metadata_json=sidecar_metadata_json,
                    sidecars_df=sidecars_df,
                    sidecar_index=idx,
                    args=args,
                    use_duckdb=use_duckdb
                )
                batch_results.append(result)
            except Exception as e:
                logger.error(f"Fatal error processing sidecar [{idx}]: {e}")
                if _get_config(config, 'stop_on_error'):
                    raise RuntimeError(f"Stopping batch processing due to --stop-on-error") from e
                batch_results.append({
                    'sidecar_index': idx, 'sidecar_path': str(sidecar_path),
                    'status': 'error', 'error': str(e)
                })

        if len(indices_to_process) > 1:
            success_count = sum(1 for r in batch_results if r['status'] == 'success')
            error_count = sum(1 for r in batch_results if r['status'] == 'error')
            logger.info("\n" + "="*80)
            logger.info("BATCH PROCESSING SUMMARY")
            logger.info(f"Total: {len(batch_results)}  Success: {success_count}  Errors: {error_count}")
            if error_count > 0:
                logger.info("Failed sidecars:")
                for r in batch_results:
                    if r['status'] == 'error':
                        logger.error(f"  [{r['sidecar_index']}] {Path(r['sidecar_path']).name}: {r['error']}")
            if success_count > 0:
                logger.info("Successfully processed:")
                for r in batch_results:
                    if r['status'] == 'success':
                        logger.info(f"  [{r['sidecar_index']}] {Path(r['output_path']).name} ({r.get('n_cells', '?')} cells)")
            logger.info("="*80)

        logger.info("Aggregation complete!")
        return 0

    logger.info("Done!")
    return 0

def main():
    """CLI entry point for healpyxel_aggregate."""
    args = parse_arguments()
    return run(args)

if __name__ == '__main__':
    main()
