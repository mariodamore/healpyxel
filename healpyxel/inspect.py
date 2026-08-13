"""Inspection CLI: schema display and sidecar discovery.

Provides read-only investigation commands that display parquet schemas,
list available sidecars, and show row counts — without performing any
aggregation computation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa
from shapely import from_wkb

from healpyxel.aggregate import collect_sidecar_outputs

logger = logging.getLogger("healpyxel.inspect")


def _format_bytes(n: int) -> str:
    """Human-readable byte count."""
    for unit, factor in [("MB", 1024**2), ("KB", 1024), ("B", 1)]:
        if n >= factor:
            return f"{n/factor:.1f} {unit}"
    return f"{n} B"


def print_parquet_schema(
    file_path: Path,
    show_metadata: bool = True,
    verbose: bool = False,
) -> None:
    """Print the schema of a parquet file with optional column statistics.

    Displays column names, types, row count, file size, and embedded
    metadata.  When ``verbose=True``, also shows per-column non-null
    counts, min/max for numeric columns, and unique values for strings
    (reads a sample for performance).

    Parameters
    ----------
    file_path : Path
        Path to the parquet file.
    show_metadata : bool
        Whether to display file metadata (default: ``True``).
    verbose : bool
        If ``True``, show per-column statistics (default: ``False``).
    """
    try:
        pf = pq.ParquetFile(file_path)
        schema = pf.schema_arrow
        meta = pf.metadata
        num_rows = meta.num_rows
        file_size = file_path.stat().st_size

        # Column type summary
        n_int = sum(1 for f in schema if pa.types.is_integer(f.type))
        n_float = sum(1 for f in schema if pa.types.is_floating(f.type) or pa.types.is_decimal(f.type))
        n_str = sum(1 for f in schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type))
        n_bin = sum(1 for f in schema if pa.types.is_binary(f.type) or pa.types.is_fixed_size_binary(f.type))

        print(f"\nSchema for: {file_path.name}")
        print("-" * 80)
        print(f"  File size : {_format_bytes(file_size)}")
        print(f"  Row count : {num_rows:,}")
        print(f"  Columns   : {len(schema)}  ({n_int} int, {n_float} float, {n_str} string, {n_bin} binary)")
        print()
        print(schema)

        if show_metadata:
            file_meta = pf.schema_arrow.metadata
            if file_meta:
                print("\nFile Metadata:")
                print("-" * 80)
                for k, v in file_meta.items():
                    key_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                    val_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                    print(f"  {key_str}: {val_str}")

        if verbose:
            _print_column_stats(file_path, schema, num_rows)

    except Exception as e:
        logger.error(f"Failed to read schema from {file_path}: {e}")


def print_geometry_info(file_path: Path, num_rows: int, verbose: bool = False) -> None:
    """Print geometry column discovery info from parquet GEO metadata.

    ADR-018: displays the ``geo:`` parquet metadata key (if present): primary
    geometry column, encoding, geometry types, and bounding box.  Also reports
    whether sidecar will auto-detect the column, and whether dask_geopandas
    can read it natively or if WKB decode will be needed.

    When ``verbose`` is ``True``, also checks the pandas dtype of each
    geometry-like column to reveal whether it reads as shapely objects or
    raw WKB bytes — the key difference that determines sidecar compatibility.
    """
    import json
    geo_key = b'geo'
    geo_metadata = None

    try:
        pf = pq.ParquetFile(file_path)
        schema_meta = pf.schema_arrow.metadata
        if schema_meta and geo_key in schema_meta:
            raw = schema_meta[geo_key]
            geo_metadata = json.loads(raw)
    except Exception:
        pass

    print(f"\nGeometry Discovery for: {file_path.name}")
    print("=" * 80)

    if geo_metadata is None:
        print("  No geo: parquet metadata found — geometry detection relies on "
              "column name heuristics only.")
        print("  Sidecar will scan columns for names containing: "
              "'polygon', 'geometry', 'wkt', 'wkb', 'geom'")
        if not verbose:
            return
        print()
        print("  (Use --verbose to inspect column dtypes and detect "
              "shapely vs WKB encoding)")
        return

    cols_info = geo_metadata.get("columns", {})
    primary = geo_metadata.get("primary_column", None)
    version = geo_metadata.get("version", "?.?.?")

    print(f"  GeoParquet metadata version : {version}")
    if primary:
        print(f"  Primary geometry column     : {primary}")

    for col_name, col_data in cols_info.items():
        enc = col_data.get("encoding", "unknown")
        types = col_data.get("geometry_types", ["unknown"])
        bbox = col_data.get("bbox", None)
        print(f"\n  Column: {col_name}")
        print(f"    Encoding  : {enc}")
        print(f"    Types     : {', '.join(types)}")
        if bbox:
            minx, miny, maxx, maxy = bbox
            print(f"    BBox      : lon=[{minx:.4f}, {maxx:.4f}], "
                  f"lat=[{miny:.4f}, {maxy:.4f}]")

    # Sidecar compatibility check
    print()
    try:
        df_sample = pd.read_parquet(file_path, engine="pyarrow").head(1)
    except Exception:
        df_sample = None

    detected = []
    for col in df_sample.columns if df_sample is not None else []:
        dtype_str = str(df_sample[col].dtype)
        if dtype_str == 'object' and any(
            kw in col.lower() for kw in ('polygon', 'geometry', 'wkt', 'wkb', 'geom')
        ):
            detected.append((col, dtype_str))

    sidecar_will_detect = len(detected) > 0
    print(f"  Sidecar auto-detection      : {'YES' if sidecar_will_detect else 'NO'}")
    if sidecar_will_detect:
        for col, dtype_str in detected:
            has_geom_attr = (
                hasattr(df_sample[col], "geometry")
                if col in df_sample.columns else False
            )
            first_val = df_sample[col].iloc[0]
            is_wkb_bytes = isinstance(first_val, (bytes, bytearray))
            read_mode = "WKB bytes" if is_wkb_bytes else "shapely objects"
            compat = "compatible (dask_geopandas needed)" if is_wkb_bytes else "compatible (plain dask OK)"
            print(f"    Column '{col}': dtype={dtype_str}, read as {read_mode}")
            print(f"      -> {compat}")
        if any(isinstance(
            df_sample[col].iloc[0], (bytes, bytearray)
        ) for col, _ in detected if df_sample[col].iloc[0] is not None):
            print()
            print("  WARNING: Dask_geopandas fallback will read this as raw WKB bytes.")
            print("           Sidecar currently cannot decode WKB — install dask_geopandas")
            print("           or provide --lon-col / --lat-col.")
    else:
        print("  No geometry-like columns found by sidecar heuristics.")

    if verbose:
        print()
        print("  Per-column geometry dtype breakdown:")
        for col in (df_sample.columns if df_sample is not None else []):
            dtype_str = str(df_sample[col].dtype)
            is_geo_heur = any(kw in col.lower()
                              for kw in ('polygon', 'geometry', 'wkt', 'wkb', 'geom'))
            if is_geo_heur:
                first_val = df_sample[col].iloc[0]
                is_bytes = isinstance(first_val, (bytes, bytearray))
                is_shapely = hasattr(first_val, 'geom_type')
                print(f"    {col}: dtype={dtype_str}",
                      f"| bytes={is_bytes}",
                      f"| shapely={is_shapely}",
                      f"| null={df_sample[col].isna().all()}",
                      sep="")
        try:
            df_full = pd.read_parquet(file_path, engine="pyarrow")
            for col, _ in detected:
                nulls = df_full[col].isna().sum()
                print(f"    {col} null count: {nulls}/{num_rows}")
        except Exception:
            pass

    print()


def correct_geometry_metadata(input_path: Path, output_path: Path) -> Path:
    """Rewrite a parquet file with corrected GeoParquet geometry metadata.

    ADR-018: reads the input parquet, detects WKB-encoded geometry columns,
    decodes them via shapely.from_wkb, promotes to a GeoDataFrame with proper
    CRS (EPSG:4326), and writes with ``schema_geometry=True`` so
    dask_geopandas can read spatial partitions natively.

    This is useful when the original file was written with broken spatial
    partition metadata (e.g. by duckdb), causing
    ``dask_geopandas.read_parquet()`` to fail.  Use the ``--correct-geometry``
    CLI flag to invoke this one-time fix and eliminate per-run WKB decode
    overhead.

    Parameters
    ----------
    input_path : Path
        Source parquet file with WKB geometry columns.
    output_path : Path
        Destination parquet file (overwritten if exists).

    Returns
    -------
    Path
        Path to the written output file.

    Raises
    ------
    ImportError
        If geopandas is not installed.
    ValueError
        If no geometry columns are detected.
    """
    try:
        import geopandas as gpd
    except ImportError as e:
        raise ImportError("geopandas required for --correct-geometry. "
                          "Install with: pip install geopandas") from e

    logger.info(f"Reading {input_path.name} for geometry correction...")
    df = pd.read_parquet(input_path)

    geo_cols = [col for col in df.columns
                if df[col].dtype == 'object'
                and any(kw in col.lower()
                        for kw in ('polygon', 'geometry', 'wkt', 'wkb', 'geom'))]

    if not geo_cols:
        raise ValueError(
            f"No geometry columns detected in {input_path.name}. "
            "Columns checked: " + ", ".join(df.columns.tolist())
        )

    gdf = df.copy()
    corrected_cols = []
    for col in geo_cols:
        first_val = gdf[col].iloc[0]
        if isinstance(first_val, bytes):
            logger.info(f"Decoding WKB in column '{col}' ({len(gdf):,} rows)...")
            gdf[col] = gdf[col].apply(
                lambda b: from_wkb(b) if isinstance(b, (bytes, bytearray)) else b
            )
            corrected_cols.append(col)

    # Promote to GeoDataFrame
    primary_col = geo_cols[0]
    gdf = gpd.GeoDataFrame(gdf, geometry=primary_col, crs='EPSG:4326')

    logger.info(f"Writing corrected geometry to {output_path}...")
    try:
        gdf.to_parquet(output_path, index=False, schema_geometry=True)
    except TypeError:
        # schema_geometry requires geopandas >= 0.14
        gdf.to_parquet(output_path, index=False)
        logger.warning("geopandas too old for schema_geometry=True — "
                       "WKB decoded but spatial partition metadata may still be missing. "
                       "Upgrade geopandas for full GeoParquet compliance.")
    logger.info(f"Done. Corrected columns: {corrected_cols if corrected_cols else 'already shapely'}")
    return output_path


def _print_column_stats(file_path: Path, schema, num_rows: int, sample_rows: int = 100_000) -> None:
    """Print per-column statistics (non-null counts, min/max for numeric).

    Reads up to `sample_rows` for performance on large files.
    """
    print(f"\nColumn Statistics  (sample ≤ {sample_rows:,} rows from {num_rows:,} total):")
    print("-" * 80)
    try:
        df = pd.read_parquet(file_path)
        if len(df) > sample_rows:
            df = df.sample(n=sample_rows, random_state=42)

        for col in df.columns:
            non_null = int(df[col].notna().sum())
            dtype = df[col].dtype
            print(f"  {col}:")
            print(f"    type       : {dtype}")
            print(f"    non-null   : {non_null:,} / {len(df):,}")
            if pd.api.types.is_numeric_dtype(dtype):
                try:
                    print(f"    min        : {df[col].min()}")
                    print(f"    max        : {df[col].max()}")
                except Exception:
                    pass
            elif pd.api.types.is_string_dtype(dtype) or pd.api.types.is_object_dtype(dtype):
                try:
                    unique_vals = df[col].dropna().unique()
                    if len(unique_vals) <= 10:
                        print(f"    unique     : {', '.join(str(v) for v in unique_vals)}")
                    else:
                        print(f"    unique     : {len(unique_vals)}  (showing first 10: {', '.join(str(v) for v in unique_vals[:10])})")
                except Exception:
                    pass
            print()

    except Exception as e:
        print(f"  (could not read column stats: {e})")


def print_sidecar_summary(sidecars_df: pd.DataFrame, input_file: Path) -> None:
    """Print a formatted summary table of available sidecar files.

    Displays each sidecar's filename, size, mode, nside, order, row count,
    unique HEALPix cell count, and derived-from-parent flag (for
    bit-shift aggregation).

    Parameters
    ----------
    sidecars_df : pd.DataFrame
        DataFrame from :func:`healpyxel.aggregate.collect_sidecar_outputs`.
    input_file : Path
        Original input parquet file path (displayed as context).
    """
    print(f"\nAvailable Sidecar Files for: {input_file.name}")
    print("=" * 100)

    if len(sidecars_df) == 0:
        print("  (none found)")
        return

    display_cols = ["mode", "nside", "order"]
    show_n_rows = "n_rows" in sidecars_df.columns
    show_n_unique = "n_unique_healpix" in sidecars_df.columns
    show_derived = "derived_from_parent" in sidecars_df.columns
    if show_n_rows:
        display_cols.append("n_rows")
    if show_n_unique:
        display_cols.append("n_unique_healpix")

    for idx, row in sidecars_df.iterrows():
        filename = Path(row["file"]).name
        file_size = Path(row["file"]).stat().st_size if Path(row["file"]).exists() else None

        print(f"\n[{idx}] {filename}  ({_format_bytes(file_size) if file_size else '?'})")
        for col in display_cols:
            if col in row:
                val = row[col]
                if pd.notna(val):
                    if col in ("n_rows", "n_unique_healpix"):
                        print(f"    {col:20s}: {int(val):,}")
                    else:
                        print(f"    {col:20s}: {val}")

        if show_derived and pd.notna(row.get("derived_from_parent")):
            print(f"    {'derived_from_parent':20s}: {int(row['derived_from_parent'])}  (bit-shift aggregation)")

    print("\n" + "=" * 100)
    print(f"Use --sidecar-index <INDEX> to select a specific sidecar (0-{len(sidecars_df)-1})")
    print("Or use --sidecar-index all to process all sidecars in batch mode")
    print()


# =============================================================================
# CLI
# =============================================================================

class CustomFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    pass

def parse_arguments(argv=None) -> argparse.Namespace:
    """Parse command-line arguments for healpyxel_inspect."""
    parser = argparse.ArgumentParser(
        prog="healpyxel_inspect",
        description="Inspect parquet files and discover available HEALPix sidecars.",
        epilog="""
EXAMPLES:

  # Display schema of input file
  healpyxel_inspect -i data.parquet --schema

  # List available sidecars with row counts and sizes
  healpyxel_inspect -i data.parquet --list-sidecars --stats

  # Verbose: show per-column non-null counts and min/max
  healpyxel_inspect -i data.parquet --schema -v

  # Display schema of a specific sidecar
  healpyxel_inspect -i data.parquet --sidecar-schema 0
""",
        formatter_class=CustomFormatter,
    )

    parser.add_argument(
        "-i", "--input", required=True, type=Path,
        help="Path to input parquet file",
    )

    parser.add_argument(
        "-d", "--sidecar-dir", type=Path, default=None,
        help="Directory containing sidecar files (default: same dir as input)",
    )

    parser.add_argument(
        "--schema",
        action="store_true",
        help="Display schema of input parquet file (column names, types, metadata) and exit. "
             "Shows row count, file size, and column type summary.",
    )

    parser.add_argument(
        "--list-sidecars",
        action="store_true",
        help="Scan sidecar directory and display available sidecars with metadata. "
             "Shows: mode (nside), order, row counts, file sizes. "
             "Optionally compute row counts and unique HEALPix cell counts with --stats.",
    )

    parser.add_argument(
        "--sidecar-schema",
        type=int,
        metavar="INDEX",
        help="Display schema of specific sidecar file by index (0-based). "
             "First run --list-sidecars to see available indices and metadata.",
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help="When used with --list-sidecars, compute and display row counts and "
             "unique HEALPix cell counts for each sidecar (slower, requires reading files).",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show additional details: per-column non-null counts, "
             "min/max for numeric columns, unique values for strings.",
    )

    parser.add_argument(
        "--geometry",
        action="store_true",
        help="Show geometry column discovery info: GEO metadata, column names, "
             "encoding, geometry types, bbox, and sidecar compatibility.",
    )

    parser.add_argument(
        "--correct-geometry", type=Path, default=None, metavar="OUTPUT.parquet",
        help="Rewrite the input parquet with corrected GeoParquet metadata. "
             "Decodes WKB geometry columns so dask_geopandas.read_parquet() "
             "works natively. Requires geopandas.",
    )

    parser.add_argument(
        "--loglevel",
        choices=["debug", "info", "warning", "error"],
        default="info",
        help="Logging verbosity",
    )

    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite output file without prompting (used with --correct-geometry).",
    )

    return parser.parse_args(argv)


def run(config) -> int:
    """Run the inspect CLI.

    Handles all ``healpyxel_inspect`` commands: schema display, sidecar
    discovery, geometry column inspection (``--geometry`` / ``--verbose``),
    and one-time geometry correction (``--correct-geometry``).

    Args:
        config: argparse Namespace from parse_arguments()

    Returns:
        Exit code (0 = success)
    """
    import logging as _logging
    _logging.basicConfig(
        level=getattr(_logging, config.loglevel.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    input_file = config.input
    sidecar_dir = config.sidecar_dir or input_file.parent

    if not input_file.exists():
        raise RuntimeError(f"Input file not found: {input_file}")

    if not input_file.is_file():
        raise RuntimeError(f"Not a file: {input_file}")

    try:
        _num_rows = pq.ParquetFile(input_file).metadata.num_rows
    except Exception:
        _num_rows = 0

    # Rewrite with corrected geometry metadata if requested
    if config.correct_geometry:
        out_path = config.correct_geometry
        if out_path.exists() and not config.force:
            response = input(f"⚠️  {out_path} already exists. Overwrite? [y/N] ").strip().lower()
            if response != 'y':
                print("Cancelled.")
                return 0
        try:
            correct_geometry_metadata(input_file, out_path)
        except Exception as e:
            logger.error(f"Geometry correction failed: {e}")
            return 1
        return 0

    # Show geometry info first when explicitly requested
    if config.geometry:
        print_geometry_info(input_file, _num_rows, verbose=config.verbose)

    # Show schema if requested
    if config.schema:
        print_parquet_schema(input_file, verbose=config.verbose)

    # List sidecars / show sidecar schema if requested
    if config.list_sidecars or config.sidecar_schema is not None:
        if not sidecar_dir.exists():
            raise RuntimeError(f"Sidecar directory not found: {sidecar_dir}")

        sidecars_df = collect_sidecar_outputs(
            input_file,
            sidecar_dir,
            read_stats=config.stats,
        )

        if config.list_sidecars:
            print_sidecar_summary(sidecars_df, input_file)

        if config.sidecar_schema is not None:
            if config.sidecar_schema < 0 or config.sidecar_schema >= len(sidecars_df):
                raise RuntimeError(
                    f"Invalid sidecar index: {config.sidecar_schema}. "
                    f"Valid range: 0-{len(sidecars_df)-1}"
                )
            sidecar_path = Path(sidecars_df.iloc[config.sidecar_schema]["file"])
            print_parquet_schema(sidecar_path, verbose=config.verbose)

    # Embed geometry info in verbose schema output
    if config.verbose and not config.geometry:
        print_geometry_info(input_file, _num_rows, verbose=True)

    # If no action specified, show schema anyway
    no_action = not (config.schema or config.list_sidecars
                     or config.sidecar_schema is not None
                     or config.geometry)
    if no_action:
        print_parquet_schema(input_file, verbose=config.verbose)
        logger.info("No action specified. Showing schema. Use --schema, --list-sidecars, --sidecar-schema, or --geometry for specific output.")

    logger.info("Done!")
    return 0
