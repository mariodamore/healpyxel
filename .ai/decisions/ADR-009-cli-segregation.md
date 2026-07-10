# ADR-009: CLI segregation — pure submodules, single gateway in cli.py

- **Status:** Active
- **Date:** 2026-07-10
- **Author:** session 2026-07-10

## Context

`healpyxel.sidecar`, `healpyxel.aggregate`, `healpyxel.accumulator`, `healpyxel.finalize`, and `healpyxel.geospatial` each contain inline argparse/click CLI logic mixed with domain logic. This causes:

1. **Import-time side effects**: importing `sidecar` pulls in `argparse` and `os` even when only calling `process_partition()` programmatically
2. **Double-parsing bug**: `sidecar_cli` in `cli.py` calls `parse_arguments(argv)` then passes the original `argv` to `sidecar_main`, which parses again
3. **Dead wrappers**: `aggregate_cli`, `accumulator_cli`, `finalize_cli`, `to_geoparquet_cli` are pure passthroughs with misleading docstrings claiming they "handle argparse setup"
4. **Scattered CLI surface**: the `[project.scripts]` entry points point to `cli.py`, but the real CLI logic lives in the submodules

## Decision

Refactor to a clean separation:

```
cli.py                  ← single gateway: argparse + entry-point dispatch
├── sidecar_cli(argv)   → parses argv → calls healpyxel.sidecar.run(...)
├── aggregate_cli(argv) → parses argv → calls healpyxel.aggregate.run(...)
├── accumulator_cli(argv) → parses argv → calls healpyxel.accumulator.run(...)
├── finalize_cli(argv)  → parses argv → calls healpyxel.finalize.run(...)
├── to_geoparquet_cli(argv) → parses argv → calls healpyxel.geospatial.run(...)
└── cache_cli()         → stays (Click-based, already correct)

healpyxel/sidecar.py    ← pure domain API: process_partition(), run(config), …
healpyxel/aggregate.py  ← pure domain API: run_aggregation(), run(config), …
healpyxel/accumulator.py ← pure domain API: accumulate(), run(config), …
healpyxel/finalize.py   ← pure domain API: finalize(), run(config), …
healpyxel/geospatial.py ← pure domain API: convert_to_geoparquet(), run(config), …
```

Each submodule:
- Removes its `parse_arguments()` / inline argparse from `main()`
- Exposes `run(config_dict_or_namespace)` as the new primary entry point
- `main()` is removed (or kept as a thin `run(sys.argv[1:])` shim that can be deleted later)

`cli.py`:
- Owns all argparse definition (one parser per command)
- Calls `submodule.run(config)` with a plain dict or namespace
- Keeps `validate_lon_lat_columns` as public utility
- Keeps `cache_cli` (Click-based, unique)

## Consequences

- Positive: submodules are trivially importable without argparse/click/Click dependencies
- Positive: `sidecar_cli` double-parse bug disappears
- Positive: `[project.scripts]` entry points → `cli.py`, single CLI surface
- Positive: testing submodules in isolation — no argparse mocking needed
- Positive: `__init__.py` can re-export clean: `from .sidecar import process_partition, run`
- Negative: ~5 modules to refactor, risk of breaking `python -m healpyxel.sidecar` workflow
- Negative: argparse definitions move to `cli.py`, which becomes a larger file (~300–400 lines)

## Alternatives Considered

- **Keep current structure**: rejected — buggy (double-parse), misleading (dead wrappers), unclean (CLI logic in domain modules)
- **Remove `cli.py` entirely, point entry points to submodules**: cleaner but then `validate_lon_lat_columns` and `cache_cli` are stranded; also means no single gateway for CLI changes
- **Use Click everywhere**: rejected — Click is good for `healpyxel_to_geoparquet` and `healpyxel_cache`, but the other commands use complex subcommands and filters that work better with argparse; mixed CLI frameworks is fine here

## Waiver

n/a
