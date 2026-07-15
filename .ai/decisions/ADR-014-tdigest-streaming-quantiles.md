# ADR-014: TDigest for Streaming Quantile Computation

- **Status:** Active
- **Date:** 2026-07-15
- **Author:** Session 2026-07-15 (code review + test implementation)

## Context

The accumulation pipeline needs to provide percentile/quantile statistics (median, p25,
p75, etc.) in a streaming context where raw data cannot be stored per cell.

Welford's algorithm provides exact online computation of mean, variance, min, and max —
all mergeable across batches. However, Welford has no streaming quantile/percentile
answer. We need a data structure that:

1. Supports incremental updates (one value at a time or in batches)
2. Supports merge (combining two partial states into one)
3. Supports serialization (save/load to disk between mission days)
4. Has bounded memory (does not store all raw observations)
5. Is a Python-native dependency (consistent with project constraints)

## Decision

Use the `tdigest` pip package as the `QuantileReducer` implementation.

TDigest is used inside `CellAccumulator` as a per-cell, per-column `TDigest` object.
It is updated via `batch_update()` (preferred) or individual `update()` calls when
`batch_update` is not available. Serialization uses `_serialize_tdigest_raw()` which
forces centroid materialization via `digest.to_dict()` to avoid the lazy-serialization
edge case fixed in v0.2.2.

## Implementation Notes

- **Merge path:** `CellAccumulator.merge()` iterates `digest.C.values()` to retrieve
  `Centroid` objects and calls `update(centroid.mean, centroid.count)`.
- **Serialization:** TDigest centroids are stored as `(mean, count)` tuples in
  `tdigests_json` column of the state Parquet. Floating-point weights are preserved
  (no int truncation).
- **Deserialization:** `from_dict()` reconstructs TDigest by re-inserting centroids
  with `digest.update(float(mean), float(count))`.
- **Graceful degradation:** If `tdigest` is not installed, `TDIGEST_AVAILABLE` is
  False and percentile tracking is silently disabled. The `--no-tdigest` CLI flag
  allows explicit opt-out.

## Alternatives Considered

- **DDSketch:** Provides relative-error quantiles with better merge semantics than
  TDigest. Rejected because no mature Python-native implementation existed at project
  inception, and the relative-error guarantee is less intuitive for absolute-value
  planetary reflectance data.
- **GK algorithm:** Guaranteed error bounds but significantly more complex to implement
  correctly in Python. Rejected in favor of the simpler TDigest for the initial
  implementation.
- **Exact quantile storage:** Store sorted values up to a fixed cap per cell, fall back
  to TDigest when cap exceeded. Rejected because it adds memory complexity and breaks
  simple merge semantics for large cells.
- **No streaming quantiles:** Provide only mean/std/min/max via Welfest; percentiles
  only via batch `aggregate.py`. Rejected because real-time mission monitoring
  requires streaming percentile estimates for mosaic quality control.

## Consequences

- **Positive:** Streaming percentiles are available; mergeable across batches;
  serializable to disk; bounded memory; single pip dependency.
- **Negative:** Percentiles are approximate (~1e-3 error vs exact batch computation
  when using `batch_update`). Users comparing streaming output to `aggregate.py` output
  will see minor discrepancies in median/quantile columns. This error bound should be
  documented for downstream users.
- **Trade-off accepted:** The approximation error is acceptable for the mission-monitoring
  use case where streaming capability is required. Exact percentiles would require
  storing all raw observations, which violates the bounded-memory streaming constraint.

## Waiver

None. This decision is fully consistent with `00_CONSTRAINTS.md`.
