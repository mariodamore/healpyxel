# ADR-017: Separate inspection CLI (`healpyxel_inspect`) from aggregation

- **Status:** Active
- **Date:** 2026-08-12
- **Author:** session 2026-08-12

## Context

`healpyxel_aggregate` carries four inspection-only flags: `--schema`, `--list-sidecars`,
`--sidecar-schema`, and `--stats`. These flags perform no computation — they display
parquet schemas, list available sidecars with metadata, and show row counts. They mix
inspection concern with the aggregation CLI's core responsibility.

The other CLI commands (`healpyxel_sidecar`, `healpyxel_accumulator`, `healpyxel_finalize`,
`healpyxel_to_geoparquet`) have no such inspection flags. Only aggregate does.

## Decision

Extract inspection functions and CLI into a dedicated `healpyxel_inspect` entry point.

**New module: `healpyxel/inspect.py`**
- `print_parquet_schema(file_path, show_metadata)` — display Arrow schema + metadata
- `print_sidecar_summary(sidecars_df, input_file)` — tabular summary of sidecar files
- `parse_arguments(argv=None)` — argparse for `--schema`, `--list-sidecars`,
  `--sidecar-schema INDEX`, `--stats`
- `run(config)` — dispatches inspection commands, early-exit (no aggregation)

**Modified: `healpyxel/aggregate.py`**
- Remove `print_parquet_schema()` and `print_sidecar_summary()` (moved to inspect.py)
- Remove 4 argparse entries (`--schema`, `--list-sidecars`, `--sidecar-schema`, `--stats`)
- Remove inspection early-exit branches in `run()`
- Keep `collect_sidecar_outputs()` — still used internally by aggregation logic

**Modified: `healpyxel/cli.py`**
- Add `inspect_cli()` entry point delegating to `healpyxel.inspect`

**Modified: `pyproject.toml`**
- Add `healpyxel_inspect = "healpyxel.cli:inspect_cli"`

## Alternatives Considered

- **Keep inspection flags in aggregate:** Rejected. Violates single-responsibility;
  forces every aggregate invocation to carry unused argparse complexity; inconsistent
  with the rest of the CLI surface.
- **Add inspection flags to all CLI commands:** Rejected. Even more surface area;
  inspection is a cross-cutting concern better served by a dedicated tool.

## Consequences

- **Positive:** `healpyxel_aggregate` is now purely computational — argparse is leaner,
  help output is clearer.
- **Positive:** `healpyxel_inspect` provides a discoverable, focused entry point for
  parquet and sidecar investigation.
- **Positive:** Mirrors the existing CLI pattern (one tool per responsibility).
- **Negative:** Users running `healpyxel_aggregate --schema` must switch to
  `healpyxel_inspect --schema`. A clear error message in aggregate.py will direct them.

## Waiver

N/A. This decision does not override any constraint in `00_CONSTRAINTS.md`.
