"""Streaming accumulation into HEALPix cells.

This module implements the **accumulator** stage of the healpyxel pipeline.
It provides streaming (incremental) statistics computation for HEALPix cells,
enabling mission-day-by-mission-day processing without loading all data at once.

**Core concepts:**

* **StreamingStats** — Welford's algorithm for running mean, std, min, max.
  No raw data storage required; just count, sum, sum-of-squares.
* **CellAccumulator** — Per-cell container holding ``StreamingStats`` for
  multiple value columns plus optional TDigest for approximate percentile
  computation.
* **TDigest** — Streaming quantile data structure. Provides ~1e-3 accuracy
  vs exact batch computation. Used for streaming median, percentile, and
  interquartile range estimation.
* **State serialization** — Accumulator state is serialized to parquet with
  embedded metadata and companion ``.meta.json``. Supports round-trip
  serialization/deserialization with idempotent input fingerprinting.

**Workflow:**

1. Initialize or load existing state.
2. For each batch: merge with sidecar, group by HEALPix cell, update
   ``CellAccumulator`` instances.
3. Optionally track progress for visualization.
4. Save state to parquet with metadata.

The state file can be passed to finalize for map production, or loaded
for continued accumulation of subsequent batches.
"""

from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import logging
import argparse
import json
from datetime import datetime, timezone
import sys

import numpy as np
import pandas as pd

from .metadata import HEALPyxelxMetadata

try:
    from tdigest import TDigest
    TDIGEST_AVAILABLE = True
except ImportError:
    TDIGEST_AVAILABLE = False
    TDigest = None

try:
    import dask.dataframe as dd
    DASK_AVAILABLE = True
except ImportError:
    DASK_AVAILABLE = False
    dd = None

try:
    from tqdm.auto import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    tqdm = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

class StreamingStats:
    """Container for streaming statistics using Welford's algorithm.

    Maintains running statistics — count, sum, sum of squares, min, max —
    without storing raw data. Supports merge for parallel/reduce workflows
    and serialization to/from dictionaries.

    The mean and standard deviation are computed on-the-fly from the
    accumulated state, giving numerically stable one-pass computation.

    Examples
    --------
    >>> stats = StreamingStats()
    >>> stats.update([1.0, 2.0, 3.0])
    >>> stats.n
    3
    >>> stats.mean
    2.0
    >>> stats.std
    0.816496580927726
    """

    def __init__(self):
        self.n = 0
        self.sum = 0.0
        self.sum_sq = 0.0
        self.min_val = float('inf')
        self.max_val = float('-inf')

    def update(self, values):
        """Add new observations to the running statistics.

        Args:
            values: Scalar (float/int), list, or NumPy array.
                   Automatically converted to 1D float64 array.

        Examples:
            >>> stats = StreamingStats()
            >>> stats.update(1.0)              # Scalar
            >>> stats.update([2.0, 3.0])       # List
            >>> stats.update(np.array([4.0]))  # Array
            >>> stats.n
            4
        """
        # Normalize: scalar → 1D array
        if np.isscalar(values):
            values = np.array([values], dtype=np.float64)
        else:
            values = np.asarray(values, dtype=np.float64)

        # Ensure 1D
        if values.ndim != 1:
            raise ValueError(f"Expected 1D array, got shape {values.shape}")

        # Filter finite values only
        values = values[np.isfinite(values)]
        if len(values) == 0:
            return

        self.n += len(values)
        self.sum += np.sum(values)
        self.sum_sq += np.sum(values ** 2)
        self.min_val = min(self.min_val, float(np.min(values)))
        self.max_val = max(self.max_val, float(np.max(values)))

    def merge(self, other: 'StreamingStats'):
        """Merge with another StreamingStats object (for parallel processing)."""
        if not isinstance(other, StreamingStats):
            raise TypeError("Can only merge with another StreamingStats")

        self.n += other.n
        self.sum += other.sum
        self.sum_sq += other.sum_sq
        self.min_val = min(self.min_val, other.min_val)
        self.max_val = max(self.max_val, other.max_val)

    @property
    def mean(self) -> float:
        """Compute mean from running statistics."""
        return self.sum / self.n if self.n > 0 else float('nan')

    @property
    def std(self) -> float:
        """Compute standard deviation from running statistics."""
        if self.n <= 1:
            return float('nan')
        variance = (self.sum_sq / self.n) - (self.mean ** 2)
        return float(np.sqrt(max(0, variance)))  # Ensure non-negative

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            'n': int(self.n),
            'sum': float(self.sum),
            'sum_sq': float(self.sum_sq),
            'min': float(self.min_val) if np.isfinite(self.min_val) else None,
            'max': float(self.max_val) if np.isfinite(self.max_val) else None,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'StreamingStats':
        """Deserialize from dictionary."""
        stats = cls()
        stats.n = d['n']
        stats.sum = d['sum']
        stats.sum_sq = d['sum_sq']
        stats.min_val = d.get('min', float('inf'))
        if stats.min_val is None:
            stats.min_val = float('inf')
        stats.max_val = d.get('max', float('-inf'))
        if stats.max_val is None:
            stats.max_val = float('-inf')
        return stats

def _serialize_tdigest_raw(digest) -> dict:
    """Serialize TDigest using the library's native .to_dict() method.

    TDigest.to_dict() returns {'n': int, 'delta': float, 'K': int, 'centroids': list}.
    Centroids are dicts with keys 'm' (mean) and 'c' (count).
    """
    try:
        # Use native to_dict() method—forces materialization internally
        digest_dict = digest.to_dict()

        # Extract centroids in (mean, count) tuple format
        centroids = []
        for centroid in digest_dict.get('centroids', []):
            if isinstance(centroid, dict):
                # IMPORTANT: preserve centroid weight as float (no int truncation)
                centroids.append((float(centroid['m']), float(centroid['c'])))
            else:
                logger.warning(f"Unexpected centroid format: {type(centroid)}")

        return {
            'centroids': centroids,
            'compression': float(digest_dict.get('K', 100)),
            'n': float(digest_dict.get('n', 0))
        }
    except Exception as e:
        logger.warning(f"Failed to serialize TDigest: {e}. Falling back to empty state.")
        return {'centroids': [], 'compression': 100.0, 'n': 0.0}

class CellAccumulator:
    """Accumulator for a single HEALPix cell.

    Maintains streaming statistics (:class:`StreamingStats`) for multiple
    value columns plus optional TDigest for approximate percentile computation.

    One ``CellAccumulator`` instance is created per HEALPix cell that receives
    observations. The accumulate loop creates/updates these in a dict keyed
    by ``healpix_id``.

    Serialization is handled via :meth:`to_dict` (in-memory) and the
    :func:`save_state` / :func:`load_state` pipeline for disk persistence.
    Supports round-trip through TDigest's centroid-based merge.
    """

    def __init__(self, use_tdigest: bool = True):
        self.stats_by_column: Dict[str, StreamingStats] = {}
        # ADR-014: TDigest is the QuantileReducer.
        # Accuracy: ~1e-3 vs exact batch median when using batch_update().
        self.use_tdigest = use_tdigest and TDIGEST_AVAILABLE

        if self.use_tdigest:
            self.tdigests: Dict[str, TDigest] = {}

    def update(self, column: str, values: np.ndarray):
        """Add observations for a specific column.

        Args:
            column: Column name
            values: Array of new observations
        """
        # Update basic streaming stats
        if column not in self.stats_by_column:
            self.stats_by_column[column] = StreamingStats()
        self.stats_by_column[column].update(values)

        # Update T-Digest for approximate percentiles (vectorized batch update)
        if self.use_tdigest:
            if column not in self.tdigests:
                self.tdigests[column] = TDigest()

            # Only process finite values
            values_clean = values[np.isfinite(values)]
            if len(values_clean) > 0:
                # CRITICAL: Use batch update, not per-value (reduces centroid quantization error)
                # TDigest.batch_update() is more accurate than 1-by-1 calls
                if hasattr(self.tdigests[column], 'batch_update'):
                    # If library supports it (check tdigest docs), use vectorized path
                    self.tdigests[column].batch_update(values_clean.tolist())
                else:
                    # Fallback: still 1-by-1, but this is suboptimal
                    for v in values_clean:
                        self.tdigests[column].update(float(v))

    def merge(self, other: 'CellAccumulator'):
        """Merge with another CellAccumulator (for parallel processing)."""
        # Merge streaming stats
        for col, stats in other.stats_by_column.items():
            if col not in self.stats_by_column:
                self.stats_by_column[col] = StreamingStats()
            self.stats_by_column[col].merge(stats)

        # Merge T-Digests
        # ADR-014: TDigest is the QuantileReducer — mergeable via centroid re-insertion
        if self.use_tdigest and hasattr(other, 'tdigests'):
            for col, digest in other.tdigests.items():
                if col not in self.tdigests:
                    self.tdigests[col] = TDigest()
                # Merge by adding all centroids (digest.C is an AccumulationTree, use values())
                for centroid in digest.C.values():
                    self.tdigests[col].update(centroid.mean, centroid.count)

    def to_dict(self) -> dict:
        """Convert accumulator state to dictionary for serialization.

        Uses aggressive (raw) TDigest serialization to ensure centroids are
        materialized even if percentiles were never queried.

        Returns:
            Dict with 'stats' (per-column StreamingStats) and optionally 'tdigests'
        """
        result = {
            'stats': {col: stats.to_dict()
                    for col, stats in self.stats_by_column.items()}
        }

        if self.use_tdigest and hasattr(self, 'tdigests'):
            # Always use aggressive serialization to guarantee centroid materialization
            result['tdigests'] = {
                col: _serialize_tdigest_raw(digest)
                for col, digest in self.tdigests.items()
            }

        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any], use_tdigest: bool = True) -> 'CellAccumulator':
        """Deserialize from dictionary."""
        acc = cls(use_tdigest=use_tdigest)

        # Restore streaming stats
        for col, stats_dict in d['stats'].items():
            acc.stats_by_column[col] = StreamingStats.from_dict(stats_dict)

        # Restore T-Digests
        if use_tdigest and TDIGEST_AVAILABLE and 'tdigests' in d:
            for col, digest_dict in d['tdigests'].items():
                digest = TDigest()
                # Reconstruct from centroids (preserve floating-point weights)
                for mean, count in digest_dict['centroids']:
                    digest.update(float(mean), float(count))
                acc.tdigests[col] = digest

        return acc

def extract_accumulation_progress(
    accumulated_state: Dict[int, CellAccumulator],
    value_columns: List[str],
    batch_id: int,
    state_path: Path
) -> pd.DataFrame:
    """
    Extract per-cell diagnostics from accumulated state for visualization & debugging.

    Creates a rich DataFrame with healpix_id + per-column statistics (n, mean, std, min, max).
    Safe for Parquet round-trip: healpix_id is an explicit COLUMN.

    Args:
        accumulated_state: Dict[int, CellAccumulator] from accumulate_batch()
        value_columns: List of column names being accumulated
        batch_id: Batch identifier (for metadata)
        state_path: Path to saved state file (for reference)

    Returns:
        DataFrame with one row per cell, columns: healpix_id, batch_id,
        and per-column stats (n, mean, std, min, max)

    Example:
        >>> progress_df = extract_accumulation_progress(
        ...     accumulated_state, ['r1050'], batch_id=1,
        ...     state_path=Path("/tmp/state_v001.parquet")
        ... )
        >>> progress_df['healpix_id'].nunique()
        256
        >>> progress_df[['r1050_n', 'r1050_mean']].describe()
    """
    rows = []

    for healpix_id, accumulator in accumulated_state.items():
        row = {
            'healpix_id': int(healpix_id),
            'batch_id': int(batch_id),
            'state_file': str(state_path.name),
        }

        # Extract stats for each value column
        for col in value_columns:
            if col in accumulator.stats_by_column:
                stats = accumulator.stats_by_column[col]
                row[f'{col}_n'] = int(stats.n)
                row[f'{col}_mean'] = float(stats.mean) if np.isfinite(stats.mean) else np.nan
                row[f'{col}_std'] = float(stats.std) if np.isfinite(stats.std) else np.nan
                row[f'{col}_min'] = float(stats.min_val) if np.isfinite(stats.min_val) else np.nan
                row[f'{col}_max'] = float(stats.max_val) if np.isfinite(stats.max_val) else np.nan
            else:
                # Null placeholders if column not found
                row.update({
                    f'{col}_n': 0,
                    f'{col}_mean': np.nan,
                    f'{col}_std': np.nan,
                    f'{col}_min': np.nan,
                    f'{col}_max': np.nan,
                })

        rows.append(row)

    progress_df = pd.DataFrame(rows)

    # Validation: healpix_id must be column
    assert 'healpix_id' in progress_df.columns, "healpix_id must be explicit column"
    assert progress_df['healpix_id'].nunique() == len(accumulated_state), \
        "healpix_id uniqueness violated"

    return progress_df

def persist_accumulation_progress(
    progress_df: pd.DataFrame,
    output_dir: Path,
    batch_id: int
) -> Path:
    """
    Save accumulation progress DataFrame for visualization & debugging.

    Creates a TSV sidecar alongside state parquet for human inspection.
    Also maintains a cumulative progress log.

    Args:
        progress_df: Output from extract_accumulation_progress()
        output_dir: Directory to save files
        batch_id: Batch identifier

    Returns:
        Path to saved progress TSV file
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Individual batch progress
    progress_tsv = output_dir / f"progress_v{batch_id:03d}.tsv"
    progress_df.to_csv(progress_tsv, sep='\t', index=False, float_format='%.6f')
    logger.info(f"✓ Saved batch progress: {progress_tsv}")

    # Cumulative progress log (append mode)
    progress_log = output_dir / "accumulation_progress.log"
    with open(progress_log, 'a') as f:
        f.write(f"\n{'='*70}\n")
        f.write(f"Batch {batch_id:03d} | {pd.Timestamp.now().isoformat()}\n")
        f.write(f"{'='*70}\n")
        f.write(progress_df.to_string(index=False))
        f.write(f"\nCells: {len(progress_df)} | Total observations: {progress_df[[c for c in progress_df.columns if '_n' in c]].sum().sum():.0f}\n")

    return progress_tsv

def snapshot_state_as_dataframe(
    state: Dict[int, CellAccumulator],
    columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Extract current statistics from in-memory state as a DataFrame.

    Useful for plotting intermediate results during streaming without
    full serialization overhead.

    Args:
        state: Dict[int, CellAccumulator] from accumulate_batch()
        columns: Specific columns to extract (None = all)

    Returns:
        DataFrame with healpix_id and per-column statistics (n, mean, std, min, max)

    Example:
        >>> for batch in batches:
        ...     state = accumulate_batch(batch, sidecar, ...)
        ...     snap = snapshot_state_as_dataframe(state, columns=['r750'])
        ...     plot_intermediate(snap)  # Plot live
    """
    rows = []
    for hp_id, acc in state.items():
        row = {'healpix_id': int(hp_id)}

        cols_to_process = columns or list(acc.stats_by_column.keys())

        for col in cols_to_process:
            if col not in acc.stats_by_column:
                continue

            stats = acc.stats_by_column[col]
            row[f'{col}_n'] = int(stats.n)
            row[f'{col}_mean'] = float(stats.mean) if stats.n > 0 else np.nan
            row[f'{col}_std'] = float(stats.std) if stats.n > 1 else np.nan
            row[f'{col}_min'] = float(stats.min_val)
            row[f'{col}_max'] = float(stats.max_val)

        rows.append(row)

    return pd.DataFrame(rows) if rows else pd.DataFrame()

# Example: Plotting with geospatial overlay at each iteration
# In example notebook (82_example_visualization_workflow.ipynb or new 84_streaming_viz.ipynb):
#
# from healpyxel.geospatial import healpix_to_geodataframe
#
# for i, batch in enumerate(batch_files):
#     state = accumulate_batch(batch, sidecar, value_columns=['r750', 'r950'], existing_state=state)
#
#     # Get live snapshot
#     snap_df = snapshot_state_as_dataframe(state, columns=['r750'])
#
#     # Attach geometry
#     cells_gdf = healpix_to_geodataframe(
#         nside=512, order='nested', pixels=snap_df['healpix_id'].values
#     )
#     snap_gdf = cells_gdf.merge(snap_df, on='healpix_id', how='left')
#
#     # Plot with matplotlib
#     fig, ax = plt.subplots(figsize=(12, 10))
#     snap_gdf.plot(column='r750_mean', ax=ax, legend=True, cmap='viridis')
#     ax.set_title(f"Batch {i+1}: {len(state)} cells, {snap_df['r750_n'].sum():.0f} obs")
#     plt.show()

def accumulate_batch(
    new_data: pd.DataFrame,
    sidecar: pd.DataFrame,
    value_columns: List[str],
    existing_state: Optional[Dict[int, CellAccumulator]] = None,
    use_tdigest: bool = True,
    filter_expr: Optional[str] = None,
) -> Dict[int, CellAccumulator]:
    """Process one batch of data and update the accumulator state.

    Merges observation data with the sidecar mapping, groups by HEALPix cell,
    and updates each cell's ``CellAccumulator`` with the new values. Handles
    both strict mode (one cell per source) and fuzzy mode (multiple cells
    per source via many-to-one relationship).

    Parameters
    ----------
    new_data : pd.DataFrame
        DataFrame with observations. Index may be implicit or explicit
        ``source_id`` column.
    sidecar : pd.DataFrame
        HEALPix mapping with columns ``['source_id', 'healpix_id']``.
        In fuzzy mode, ``source_id`` may appear multiple times (one row
        per touched HEALPix cell).
    value_columns : List[str]
        Column names to accumulate statistics for.
    existing_state : dict or None
        Previous accumulator state dict ``{healpix_id: CellAccumulator}``.
        Pass ``None`` for the first batch.
    use_tdigest : bool
        Enable TDigest for approximate percentile tracking.
    filter_expr : str or None
        Optional pandas query expression to filter ``new_data`` before
        accumulation (e.g., ``"quality_flag > 0.8"``).

    Returns
    -------
    dict[int, CellAccumulator]
        Updated state dictionary mapping ``healpix_id`` to its accumulator.
    """
    if existing_state is None:
        state = {}
    else:
        state = existing_state.copy()

    logger.info(f"Processing batch with {len(new_data)} observations")
    logger.info(f"Columns to accumulate: {value_columns}")

    # Apply filter if provided
    if filter_expr:
        logger.info(f"Applying filter: {filter_expr}")
        new_data = new_data.query(filter_expr)
        logger.info(f"After filtering: {len(new_data)} observations")

    if len(new_data) == 0:
        logger.warning("No observations after filtering!")
        return state

    # Reset index to convert index → source_id column (ensures uniqueness)
    new_data_reset = new_data.reset_index(drop=False)

    # Handle index name: could be 'index', 'source_id', or something else
    index_col = new_data_reset.columns[0]  # First column after reset is the old index
    if index_col != 'source_id':
        if 'source_id' in new_data_reset.columns:
            # Both exist—drop the reset index column, use existing source_id
            new_data_reset = new_data_reset.drop(columns=[index_col])
        else:
            # Rename the reset index to source_id
            new_data_reset = new_data_reset.rename(columns={index_col: 'source_id'})

    # Sanity check: source_id must be unique
    if 'source_id' in new_data_reset.columns:
        n_unique = new_data_reset['source_id'].nunique()
        if n_unique != len(new_data_reset):
            raise ValueError(
                f"source_id column is not unique: {n_unique} unique values, "
                f"but {len(new_data_reset)} rows. A pre-existing source_id column "
                f"must uniquely identify each observation."
            )

    # Merge sidecar (may have duplicates per source_id in fuzzy mode) with data
    merged = sidecar.merge(
        new_data_reset[['source_id'] + value_columns],
        on='source_id',
        how='inner'
    )

    logger.info(f"Matched {len(merged)} source-cell pairs from sidecar")

    if len(merged) == 0:
        logger.warning("No matches found between sidecar and data!")
        return state

    # Group by healpix_id and accumulate
    cells_updated = 0
    cells_created = 0

    grouped = merged.groupby('healpix_id')
    total_groups = len(grouped)

    iterator = grouped if not TQDM_AVAILABLE else tqdm(grouped, desc="Accumulating cells", total=total_groups)

    for hp_id, grp in iterator:
        # Get or create accumulator for this cell
        if hp_id not in state:
            state[hp_id] = CellAccumulator(use_tdigest=use_tdigest)
            cells_created += 1

        cells_updated += 1

        # Update each column
        for col in value_columns:
            if col not in grp.columns:
                logger.warning(f"Column {col} not found in data, skipping")
                continue

            values = grp[col].dropna().to_numpy()
            if len(values) > 0:
                state[hp_id].update(col, values)

    logger.info(f"Updated {cells_updated} cells (created: {cells_created}, total: {len(state)})")
    return state

def state_to_dataframe(
    state: Dict[int, CellAccumulator],
    use_tdigest: bool = True
) -> pd.DataFrame:
    """Convert accumulated state dict to a DataFrame with explicit healpix_id column.

    Pure function (no I/O). Serializes each ``CellAccumulator`` to JSON strings
    and constructs a DataFrame where ``healpix_id`` is an explicit COLUMN,
    never the index. This ensures safe round-trip serialization and
    deserialization without losing the cell identifiers.

    Parameters
    ----------
    state : dict[int, CellAccumulator]
        Accumulator state from :func:`accumulate_batch`.
    use_tdigest : bool
        Include serialized TDigest data in the output.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns:
        * ``healpix_id`` (int) — HEALPix cell ID as explicit column
        * ``stats_json`` (str) — JSON-serialized StreamingStats per column
        * ``tdigests_json`` (str, optional) — JSON-serialized TDigest data

    Examples
    --------
    >>> state = {32: CellAccumulator(), 64: CellAccumulator()}
    >>> df = state_to_dataframe(state)
    >>> 'healpix_id' in df.columns
    True
    >>> df.index.name is None
    True
    """
    rows = []
    for healpix_id, accumulator in state.items():
        row = {'healpix_id': int(healpix_id)}  # ← EXPLICIT COLUMN

        # Serialize streaming stats for all columns
        row['stats_json'] = json.dumps(
            {col: stats.to_dict()
             for col, stats in accumulator.stats_by_column.items()}
        )

        # Serialize T-Digests (if present)
        if use_tdigest and accumulator.use_tdigest and hasattr(accumulator, 'tdigests'):
            row['tdigests_json'] = json.dumps(
                {col: _serialize_tdigest_raw(digest)
                 for col, digest in accumulator.tdigests.items()}
            )
        else:
            row['tdigests_json'] = None

        rows.append(row)

    df = pd.DataFrame(rows)

    # Explicit validation: healpix_id must be column, not index
    assert 'healpix_id' in df.columns, \
        "CRITICAL: healpix_id lost during DataFrame construction!"
    assert df.index.name is None, \
        "CRITICAL: healpix_id wrongly set as index!"
    assert df['healpix_id'].nunique() == len(state), \
        "CRITICAL: Duplicate healpix_ids detected!"

    return df

def test_state_to_dataframe_preserves_healpix_id():
    """Test that state_to_dataframe() never loses healpix_id."""
    # Create minimal state
    state = {}
    for hp_id in [32, 64, 128]:
        acc = CellAccumulator(use_tdigest=False)
        acc.stats_by_column['test_col'] = StreamingStats()
        acc.stats_by_column['test_col'].update(np.array([1.0, 2.0, 3.0]))
        state[hp_id] = acc

    # Convert to DataFrame
    df = state_to_dataframe(state, use_tdigest=False)

    # Validation suite
    assert isinstance(df, pd.DataFrame), "Must return DataFrame"
    assert 'healpix_id' in df.columns, "healpix_id must be COLUMN"
    assert df.index.name is None, "Index must NOT be named"
    assert list(sorted(df['healpix_id'])) == [32, 64, 128], "HEALPix IDs corrupted"
    assert len(df) == 3, "Row count mismatch"
    assert df['healpix_id'].notna().all(), "No null healpix_ids allowed"

    print("✓ state_to_dataframe() preserves healpix_id correctly")

from pathlib import Path
import hashlib
from typing import Dict, Any

def input_fingerprint(path: str | Path) -> str:
    "Stable fingerprint for duplicate-ingest detection."
    p = Path(path).resolve()
    st = p.stat()
    payload = f"{p.as_posix()}|{st.st_size}|{int(st.st_mtime_ns)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def ensure_not_processed(meta: Dict[str, Any], input_path: str | Path, *, on_duplicate: str = "error") -> Dict[str, Any]:
    """Check/add input fingerprint in state metadata for idempotency.

    Computes a SHA-256 fingerprint from the input file's path, size, and
    mtime. If the fingerprint is already in the ``processed_inputs`` list,
    either raises an error or records the skip, depending on ``on_duplicate``.

    Parameters
    ----------
    meta : dict
        State metadata dict (modified in-place).
    input_path : str or Path
        Path to input data file.
    on_duplicate : str
        ``'error'`` — raise ``ValueError`` if already processed.
        ``'skip'`` — record in ``skipped_duplicate`` and continue.

    Returns
    -------
    dict
        Updated metadata dict with new fingerprint added to
        ``processed_inputs``.
    """
    fp = input_fingerprint(input_path)
    processed = set(meta.get("processed_inputs", []))

    if fp in processed:
        if on_duplicate == "skip":
            meta["skipped_duplicate"] = str(Path(input_path))
            return meta
        raise ValueError(f"Duplicate accumulate detected for input: {input_path}")

    processed.add(fp)
    meta["processed_inputs"] = sorted(processed)
    return meta

def save_state(
    state: Dict[int, CellAccumulator],
    output_path: Path,
    meta: HEALPyxelxMetadata,
    processing_metadata: Optional[Dict[str, Any]] = None,
    input_path: Optional[Path] = None,
    on_duplicate: str = "error",
) -> None:
    """Serialize accumulated state to Parquet with metadata.

    Converts the state dict to a DataFrame via :func:`state_to_dataframe`,
    then writes it to Parquet with an Arrow schema that embeds HEALPix
    metadata. Also writes a companion ``.meta.json`` sidecar with full
    processing history.

    Includes idempotency checks via input fingerprinting (SHA-256 of path
    + size + mtime) to prevent accidental double-accumulation of the
    same batch.

    Parameters
    ----------
    state : dict[int, CellAccumulator]
        Accumulator state from :func:`accumulate_batch`.
    output_path : Path
        Path to write the parquet state file.
    meta : HEALPyxelxMetadata
        HEALPix metadata instance (nside, mode, order, etc.).
    processing_metadata : dict or None
        Optional batch info dict with keys like ``batch_id``, ``columns``,
        ``n_cells``, ``total_observations``, etc.
    input_path : Path or None
        Path to input data file. Used for fingerprint tracking to detect
        duplicate accumulation.
    on_duplicate : str
        Action if input was already processed: ``'error'`` (raise) or
        ``'skip'`` (silently record).

    Raises
    ------
    ValueError
        If ``input_path`` already in ``processed_inputs`` and
        ``on_duplicate='error'``.
    """
    import pyarrow as pa

    # Construct DataFrame (pure, testable operation)
    df = state_to_dataframe(state, use_tdigest=True)

    # Verify healpix_id survived DataFrame construction
    assert 'healpix_id' in df.columns, \
        "CRITICAL BUG: healpix_id lost in state_to_dataframe()!"

    # Build metadata dict
    meta_dict = meta.to_dict()
    proc_meta = dict(processing_metadata or {})

    # Register input fingerprint for duplicate detection
    if input_path is not None:
        proc_meta = ensure_not_processed(proc_meta, input_path, on_duplicate=on_duplicate)

    meta_dict['processing_metadata'] = proc_meta
    meta_dict['creation_timestamp'] = pd.Timestamp.now().isoformat()

    # Construct Arrow schema with metadata
    schema = pa.Schema.from_pandas(df)
    schema = schema.with_metadata({
        b'accumulator_metadata': json.dumps(meta_dict).encode(),
        **{k.encode() if isinstance(k, str) else k:
           (v.encode() if isinstance(v, str) else str(v).encode())
           for k, v in (schema.metadata.items() if schema.metadata else [])}
    })

    # Write to Parquet
    df.to_parquet(
        output_path,
        index=False,  # ← CRITICAL: Don't save index (healpix_id is column)
        engine='pyarrow',
        compression='snappy',
        schema=schema
    )

    # Save companion JSON metadata sidecar
    meta_sidecar = output_path.with_suffix('.meta.json')
    with open(meta_sidecar, 'w') as f:
        json.dump(meta_dict, f, indent=2, default=str)

    logger.info(f"Saved accumulated state: {len(df)} cells → {output_path}")
    logger.info(f"Metadata sidecar: {meta_sidecar}")
    if input_path and 'processed_inputs' in proc_meta:
        logger.info(f"Tracked inputs: {len(proc_meta['processed_inputs'])} unique files processed")

def load_state(
    input_path: Path,
    use_tdigest: bool = True
) -> Tuple[Dict[int, CellAccumulator], Optional[HEALPyxelxMetadata], Dict[str, Any]]:
    """Load accumulator state, HEALPix metadata, and processing history from parquet.

    Reads both embedded Arrow metadata and companion ``.meta.json`` sidecar
    to reconstruct the full state including processed input fingerprints for
    idempotency checks.

    Parameters
    ----------
    input_path : Path
        Path to state parquet file.
    use_tdigest : bool
        Whether to restore TDigest data for percentile tracking.

    Returns
    -------
    tuple[dict[int, CellAccumulator], HEALPyxelxMetadata or None, dict]
        ``(state, metadata, processing_metadata)`` where:
        * ``state`` — deserialized accumulator dict
        * ``metadata`` — HEALPyxelxMetadata or None if not found
        * ``processing_metadata`` — dict with ``processed_inputs``,
          ``last_updated``, etc. (empty dict if sidecar not found)

    Raises
    ------
    FileNotFoundError
        If state file does not exist.
    """
    logger.info(f"Loading state from {input_path}")

    if not input_path.exists():
        raise FileNotFoundError(f"State file not found: {input_path}")

    # Try to load HEALPix metadata from embedded Arrow schema
    meta = None
    try:
        meta = HEALPyxelxMetadata.from_parquet(input_path)
        logger.info(f"Loaded HEALPix metadata: nside={meta.nside}, mode={meta.mode}")
    except ValueError as e:
        logger.warning(f"No HEALPix metadata found: {e}. Loading state without validation.")

    # Load state DataFrame
    df = pd.read_parquet(input_path, engine='pyarrow')

    state = {}
    iterator = df.iterrows()
    if TQDM_AVAILABLE:
        iterator = tqdm(df.iterrows(), total=len(df), desc="Loading state")

    for _, row in iterator:
        hp_id = int(row['healpix_id'])

        # Reconstruct accumulator from JSON-serialized data
        acc_dict = {
            'stats': json.loads(row['stats_json'])
        }

        if 'tdigests_json' in row and pd.notna(row['tdigests_json']):
            acc_dict['tdigests'] = json.loads(row['tdigests_json'])

        state[hp_id] = CellAccumulator.from_dict(acc_dict, use_tdigest=use_tdigest)

    logger.info(f"✓ Loaded {len(state)} cells")

    # Load processing history from companion .meta.json sidecar
    processing_metadata = {}
    meta_sidecar = input_path.with_suffix('.meta.json')
    if meta_sidecar.exists():
        try:
            with open(meta_sidecar, 'r') as f:
                full_meta = json.load(f)
            processing_metadata = full_meta.get('processing_metadata', {})
            logger.info(f"✓ Loaded processing metadata: {len(processing_metadata.get('processed_inputs', []))} prior inputs tracked")
        except Exception as e:
            logger.warning(f"Could not read processing metadata from {meta_sidecar}: {e}")
    else:
        logger.debug(f"No metadata sidecar found at {meta_sidecar}")

    return state, meta, processing_metadata

def validate_accumulator_sidecar_compatibility(
    state_meta: HEALPyxelxMetadata,
    sidecar_meta: HEALPyxelxMetadata
) -> dict:
    """Validate that accumulator state is compatible with sidecar file.

    Checks that nside, mode, and order match between the accumulated state
    and the sidecar file. Prevents silent corruption from mixing
    incompatible files (e.g., different HEALPix resolutions).

    Parameters
    ----------
    state_meta : HEALPyxelxMetadata
        Metadata from the loaded accumulator state file.
    sidecar_meta : HEALPyxelxMetadata
        Metadata from the sidecar file.

    Returns
    -------
    dict
        Validation results with keys: ``valid``, ``errors``, ``warnings``.

    Raises
    ------
    AssertionError
        If critical parameters (nside, mode, order) mismatch.
    """
    results = {
        'valid': True,
        'errors': [],
        'warnings': []
    }

    # Check nside match (critical)
    if state_meta.nside != sidecar_meta.nside:
        msg = f"nside mismatch: state={state_meta.nside}, sidecar={sidecar_meta.nside}"
        results['errors'].append(msg)
        results['valid'] = False

    # Check mode match (critical)
    if state_meta.mode != sidecar_meta.mode:
        msg = f"mode mismatch: state={state_meta.mode}, sidecar={sidecar_meta.mode}"
        results['errors'].append(msg)
        results['valid'] = False

    # Check order match (critical)
    if state_meta.order != sidecar_meta.order:
        msg = f"order mismatch: state={state_meta.order}, sidecar={sidecar_meta.order}"
        results['errors'].append(msg)
        results['valid'] = False

    # Check lon_convention (warning if different)
    if state_meta.lon_convention != sidecar_meta.lon_convention:
        results['warnings'].append(
            f"lon_convention differs: state={state_meta.lon_convention}, "
            f"sidecar={sidecar_meta.lon_convention}"
        )

    if not results['valid']:
        raise AssertionError(f"State/sidecar compatibility check failed: {results['errors']}")

    return results

def find_sidecar(input_path: Path, nside: Optional[int] = None, mode: str = 'fuzzy') -> Optional[Path]:
    """Attempt to find matching sidecar file for input data.

    Args:
        input_path: Path to input parquet file
        nside: Desired nside (if None, finds any matching sidecar)
        mode: Assignment mode ('fuzzy' or 'strict')

    Returns:
        Path to matching sidecar file, or None if not found
    """
    # Look in same directory and 'sidecars/' subdirectory
    search_dirs = [input_path.parent, input_path.parent / 'sidecars']

    base_name = input_path.stem
    pattern = f"{base_name}.cell-healpix_assignment-{mode}"
    if nside:
        pattern += f"_nside-{nside}"
    pattern += "_order-nested.parquet"

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        # Exact match
        candidate = search_dir / pattern
        if candidate.exists():
            return candidate

        # Pattern match (if nside not specified)
        if not nside:
            for candidate in search_dir.glob(f"{base_name}.cell-healpix_assignment-{mode}_nside-*_order-nested.parquet"):
                return candidate

    return None

def parse_arguments(argv=None):
    """Parse command-line arguments for accumulator."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Accumulate streaming data into HEALPix cells with incremental statistics",
    )
    parser.add_argument('-i', '--input', required=True, type=Path)
    parser.add_argument('-s', '--sidecar', type=Path)
    parser.add_argument('--nside', type=int)
    parser.add_argument('--mode', choices=['fuzzy', 'strict'], default='fuzzy')
    parser.add_argument('-c', '--columns', nargs='+', required=True)
    parser.add_argument('--state-input', type=Path)
    parser.add_argument('-o', '--state-output', required=True, type=Path)
    parser.add_argument('-f', '--filter', dest='filter_expr')
    parser.add_argument('--no-tdigest', action='store_true')
    parser.add_argument('--batch-id')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-q', '--quiet', action='store_true')
    return parser.parse_args(argv)

def _get_config(config, key, default=None):
    """Access config value from dict or argparse Namespace."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)

def run(config):
    """Run accumulator pipeline from a config dict or argparse Namespace."""
    args = config
    if isinstance(config, dict):
        import argparse
        args = argparse.Namespace(**{k: v for k, v in config.items() if v is not None})

    if _get_config(config, 'verbose'):
        logging.getLogger().setLevel(logging.DEBUG)
    elif _get_config(config, 'quiet'):
        logging.getLogger().setLevel(logging.WARNING)

    use_tdigest = not _get_config(config, 'no_tdigest', False)
    if use_tdigest and not TDIGEST_AVAILABLE:
        logger.warning("T-Digest not available, disabling percentile tracking")
        use_tdigest = False

    input_path = _get_config(config, 'input')
    if not input_path.exists():
        raise RuntimeError(f"Input file not found: {input_path}")

    sidecar_path = _get_config(config, 'sidecar')
    if sidecar_path is None:
        logger.info("No sidecar specified, attempting auto-detection...")
        sidecar_path = find_sidecar(input_path, nside=_get_config(config, 'nside'),
                                     mode=_get_config(config, 'mode', 'fuzzy'))
        if sidecar_path is None:
            raise RuntimeError("Could not find sidecar file; specify --sidecar explicitly")
        logger.info(f"Found sidecar: {sidecar_path}")

    if not sidecar_path.exists():
        raise RuntimeError(f"Sidecar file not found: {sidecar_path}")

    logger.info(f"Loading sidecar metadata from {sidecar_path}")
    try:
        sidecar_meta = HEALPyxelxMetadata.from_parquet(sidecar_path)
    except ValueError as e:
        raise RuntimeError(f"Failed to load sidecar metadata: {e}")

    existing_state = None
    prior_processed_inputs = []
    state_input = _get_config(config, 'state_input')
    if state_input:
        if not state_input.exists():
            raise RuntimeError(f"State input file not found: {state_input}")
        try:
            existing_state, existing_meta, prior_proc_meta = load_state(state_input, use_tdigest=use_tdigest)
            prior_processed_inputs = prior_proc_meta.get('processed_inputs', [])
            if existing_meta:
                try:
                    validate_accumulator_sidecar_compatibility(existing_meta, sidecar_meta)
                except AssertionError as e:
                    raise RuntimeError(f"State/sidecar compatibility check failed: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load existing state: {e}")
    else:
        logger.info("No existing state provided, initializing new accumulator")

    logger.info(f"Loading input data from {input_path}")
    try:
        new_data = pd.read_parquet(input_path)
    except Exception as e:
        raise RuntimeError(f"Failed to load input data: {e}")

    columns = _get_config(config, 'columns')
    missing_cols = [col for col in columns if col not in new_data.columns]
    if missing_cols:
        raise RuntimeError(f"Columns not found: {missing_cols}")

    logger.info(f"Loading sidecar from {sidecar_path}")
    try:
        sidecar = pd.read_parquet(sidecar_path)
    except Exception as e:
        raise RuntimeError(f"Failed to load sidecar: {e}")

    start_time = datetime.now()
    state = accumulate_batch(
        new_data=new_data,
        sidecar=sidecar,
        value_columns=columns,
        existing_state=existing_state,
        use_tdigest=use_tdigest,
        filter_expr=_get_config(config, 'filter_expr'),
    )

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Accumulation completed in {elapsed:.1f}s")

    total_obs = sum(
        acc.stats_by_column[columns[0]].n
        for acc in state.values()
        if columns[0] in acc.stats_by_column
    )

    processing_metadata = {
        'last_updated': datetime.now().isoformat(),
        'batch_id': _get_config(config, 'batch_id') or input_path.name,
        'input_file': str(input_path),
        'sidecar_file': str(sidecar_path),
        'columns': columns,
        'filter': _get_config(config, 'filter_expr'),
        'n_cells': len(state),
        'total_observations': int(total_obs),
        'use_tdigest': use_tdigest,
        'processing_time_sec': round(elapsed, 2),
        'processed_inputs': prior_processed_inputs,
    }

    state_output = _get_config(config, 'state_output')
    state_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        save_state(state, state_output, meta=sidecar_meta,
                   processing_metadata=processing_metadata)
    except Exception as e:
        raise RuntimeError(f"Failed to save state: {e}")

    logger.info(f"Accumulation complete!  Cells: {len(state)}  Observations: {total_obs:,}")
    return 0

def main(argv=None):
    """CLI entry point for healpyxel_accumulator."""
    args = parse_arguments(argv)
    return run(args)

def _in_ipython_kernel() -> bool:
    """Return True when running inside an IPython kernel (notebook)."""
    try:
        from IPython import get_ipython
        ip = get_ipython()
        return ip is not None and 'IPKernelApp' in ip.config
    except Exception:
        return False

if __name__ == '__main__':
    if not _in_ipython_kernel():
        sys.exit(main())
    else:
        logger.info("Notebook context detected; skipping CLI entrypoint.")
