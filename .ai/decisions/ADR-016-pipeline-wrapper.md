# ADR-016: Pipeline wrapper for healpyxel 3-phase workflow

- **Status:** Active
- **Date:** 2026-08-11
- **Author:** session 2026-08-11

## Context

The 3-phase pipeline (Sidecar → Aggregate → GeoParquet, ADR-005) is currently
orchestrated only via a bash script like
[`create_healpyxel.sh`](../../data/MERTIS/ESA/OGS/flybys_mercury_flyby_5/bcmer_tm_all_20241201T000000_20241202T080000_20241201T200155-AccExecFailureParamEventBootSciHK/cal_geo_cubic/create_healpyxel.sh).
That script:

1. Calls `healpyxel_sidecar`, `healpyxel_aggregate`, `healpyxel_to_geoparquet` as subprocesses
2. Uses `awk` to parse `healpyxel_aggregate --schema` output for column auto-discovery
3. Hardcodes defaults (nsides, mode, aggs, HK columns, etc.)
4. Has per-nside skip-if-exists logic

The Python submodule API (`sidebar.run()`, `aggregate.run()`, `geospatial.run()`)
already supports:
- Direct function calls with dict or Namespace config (ADR-009 pattern)
- Native pyarrow column auto-discovery via `--all-columns` (no awk needed)
- Full parameter surface for all three phases

A user-facing wrapper is missing. Users who want to:
- programmatically run the pipeline from a notebook (`.py:percent`, per CLAUDE.md)
- override specific parameters (different aggs, filter expressions, densify, etc.)
- reproduce a run as a bash script for logging or re-execution

…must either copy-modify the bash script or manually construct CLI commands.

## Decision

Create a single new module `healpyxel/workflow.py` with two public functions:

```python
def run_pipeline(
    input: Path,
    output_dir: Path,
    nsides: tuple[int, ...] = (32, 64, 128),
    # ... common + per-stage params
    sidecar_kwargs: dict | None = None,
    aggregate_kwargs: dict | None = None,
    geoparquet_kwargs: dict | None = None,
    verbose: bool = False,
    log: TextIO | None = None,
) -> list[dict]: ...

def save_script(
    input: Path,
    output_dir: Path,
    nsides: tuple[int, ...] = (32, 64, 128),
    # ... same params as run_pipeline
    output: Path,
) -> None: ...
```

**`run_pipeline`** runs the 3-phase pipeline via direct `run()` calls (no subprocess).
Each phase accepts a flat parameter namespace covering every CLI argument from the
corresponding parser, plus per-stage override dicts (`sidecar_kwargs`, etc.) for
full flexibility.

**`save_script`** writes a self-contained bash script with the equivalent CLI
invocations — copy-paste runnable without the original Python, with `set -euo pipefail`
prepended.

### Parameter design

The signature mirrors the three CLI parsers (`sidecar.parse_arguments`,
`aggregate.parse_arguments`, `geospatial.parse_arguments`):

- Common params: `input`, `output_dir`, `nsides`, `lon_convention`, `yes`, `loglevel`
- Sidecar-specific: `mode`, `ncores`, `lon_col`, `lat_col`, `no_coalesce`,
  `stats_sample_size`, `body_model`, `body_radius`, `body_polar_radius`,
  `data_psf`, `cell_psf`, `data_psf_sigma_level`, `cell_psf_sigma_level`,
  `psf_combine`, `no_psf_normalize`, `no_multi_res_optimize`
- Aggregate-specific: `columns`, `aggregate_columns`, `aggs`, `filter`, `densify`,
  `use_duckdb`, `use_dask`, `dask_npartitions`, `min_count`, `stop_on_error`,
  `verbose`, `quiet`
- GeoParquet-specific: `output_suffix`, `output_dir`, `nside`, `order`,
  `fix_antimeridian`, `chunk_size`, `densify`

Named override dicts (`sidecar_kwargs`, etc.) take highest precedence over
function-argument defaults.

### Column auto-discovery

When `aggregate_columns=None` (default):
- Auto-discover all float64/double columns via pyarrow schema (replaces awk parsing)
- Append any user-provided `aggregate_columns` (HK defaults like in the bash script)

When `columns` is explicitly set: use exactly those columns, no auto-discovery.

### Verbose and log output

`verbose=True`: prints the equivalent CLI command before each stage using `$ <cmd>`
format, prints status after each stage, prints a summary at the end.

`log=<file handle>`: writes a self-contained bash script to the handle — same
commands that were executed, with timestamped section headers. Independent of `verbose`.

### Skip-if-exists via meta.json

Skip checks read the `.meta.json` files written by the sidecar and aggregate stages
instead of guessing output filenames:

- **Sidecar**: scan for `*.meta.json` with `processing.stage == "sidecar"`,
  `healpix.nside == nside`, `healpix.mode == mode`, and a verified output file on disk.
- **Aggregate**: scan for `*.meta.json` with `processing.stage == "aggregate"`,
  matching `sidecar_file` reference (or `sidecar_metadata.healpix.nside`), and a
  verified output file on disk.
- **GeoParquet**: uses glob-based `*geo.parquet` check (no dedicated meta.json).

This is robust against future filename convention changes and uses the single source
of truth written by each stage.

### Skip reporting

Skipped stages are explicitly labeled in both console and log:

- **Console**: `⏭ healpyxel sidecar (all nsides=32,64,128) — sidecars for nsides 32,64,128 already exist (verified via .meta.json)`
- **Log**: `# skipped — sidecars for nsides 32,64,128 already exist (verified via .meta.json)`

The `⏭` icon separates skips from `✓` (ok) and `❌` (error) at a glance.

### CLI entry point

`healpyxel_pipeline` is registered in `pyproject.toml` under `[project.scripts]`
via `healpyxel.workflow:pipeline_cli`. It covers the most common pipeline parameters
via argparse; advanced use cases are served by the Python API.

## Alternatives Considered

- **Pure CLI wrapper (shell function / env var):** Rejected. Would not integrate with
  `.py:percent` notebooks (required format per CLAUDE.md), no programmatic access to
  results, no log-to-bash-script capability.
- **Single function with a config object:** Rejected. The three `run()` functions
  already accept dict/Namespace config. A flat parameter list on `run_pipeline` is
  more discoverable and IDE-friendly; override dicts handle advanced use cases.
- **Subprocess delegation instead of direct `run()` calls:** Rejected. Adds ~100ms
  Python startup per call with no functional benefit — the subprocesses converge on
  the same code path. Direct calls are marginally faster and allow native error
  propagation.
- **Always compute at max-nside, never skip sidecar:** Rejected. Would change the
  contract established by the bash script (skip-if-exists) and break workflows where
  sidecars are managed separately.
- **Filename-based skip checks (glob):** Implemented for geoparquet only. For sidecar
  and aggregate, meta.json-based scanning was chosen because:
  - The meta.json is the authoritative artifact written by each stage
  - It encodes mode, nside, order, and output path in a structured way
  - It survives filename convention changes without code changes

## Consequences

- **Positive:** Users can run the full pipeline from notebooks, scripts, or REPL
  with a single `run_pipeline()` call.
- **Positive:** Full parameter override via `*_kwargs` dicts — no CLI surface is
  hidden from advanced users.
- **Positive:** `save_script()` produces an executable bash record of any run,
  enabling reproducibility and audit trails.
- **Positive:** Eliminates the `awk` schema-parsing workaround from the bash script;
  column auto-discovery uses native pyarrow.
- **Negative:** Adds a new module (`workflow.py`) and enlarges the `healpyxel`
  package surface. Requires maintenance when CLI parameters for the three subcommands
  change.
- **Negative:** Two ways to run the pipeline (Python wrapper, bash script) can
  drift if defaults are updated in one but not the other. Tests must guard against
  this (see Verification plan).
- **Negative:** `aggregate_kwargs` merging with `columns` + `aggregate_columns`
  semantics is a footgun if misunderstood. The docstring must be explicit.

## Waiver

N/A. This decision does not override any constraint in `00_CONSTRAINTS.md`.
