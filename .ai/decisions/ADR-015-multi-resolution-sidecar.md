# ADR-015: Multi-resolution sidecar via NEST bit-shift aggregation

- **Status:** Active
- **Date:** 2026-08-11
- **Author:** session 2026-08-11

## Context

Users may request sidecar computation for multiple HEALPix resolutions (nsides) in a
single invocation, e.g. `--nside 4 8 16 32`. The current `run()` loop in
[sidecar.py:1494-1568](healpyxel/sidecar.py#L1494-L1568) recomputes the full geometric
pipeline independently for each nside. This means the expensive fuzzy-mode polygon-to-cell
mapping (SLERP-based, ADR-013) runs N times for N nsides, discarding all intermediate work.

For cases like `-n 4 8`, the nside=8 result encodes all the information needed:
every nside=8 cell maps to exactly one nside=4 parent. In NEST ordering
(ADR-001, constraint `00_CONSTRAINTS.md`), the relationship is a pure bit-shift:

```
parent = child_id >> (2 * log2(N_high / N_low))
```

For 8 -> 4: `child >> 2`. For 512 -> 8: `child >> 6`.

## Decision

When multiple nsides are requested, compute the sidecar once at the highest nside only.
Derive all lower-nside assignments by aggregating (bit-shifting + grouping) the
high-resolution results. The lowest-cost nside is recomputed independently.

**Algorithm:**
1. Compute sidecar at `nside_max` (the finest resolution requested).
2. For each lower nside `nside_i`:
   - Compute `parent_hid = (source_healpix_at_nmax >> (2 * log2(nside_max / nside_i)))`
   - Group by `(source_id, parent_hid)` and sum weights (for PSF aggregation)
   - Deduplicate so each (source_id, healkpix_id) pair appears once
3. Write output files as before — same path format, same schema.

**PSF behavior:**
- Strict mode (point sources): PSF weight is already 1.0 at all nsides. Aggregation is a no-op.
- Fuzzy mode (polygons): weights from multiple child cells contributing to one parent are
  summed. This preserves the total weight contribution of each source, which is the
  meaningful quantity for downstream accumulation.

The current `--psf-combine` and `--no-psf-normalize` flags retain their meaning. If
`psf_normalize` is active (default), normalization is reapplied after aggregation, so the
output per-cell weights sum to 1.0 — matching the current behavior.

## Alternatives Considered

- **Cache the highest-nside result to disk for reuse:** Rejected. Adds I/O complexity and
  a cache invalidation problem. The bit-shift aggregation is done in-memory within a single
  `run()` call; no cache needed.
- **Recalculate per nside (current behavior):** Rejected. Full geometric recompute per
  resolution is redundant for the common multi-resolution workflow. The SLERP path is the
  slowest part of the pipeline.
- **Always compute at max nside, never independently:** Rejected. Would change output
  for single-nside invocations (no lower resolution to anchor against). Only activates when
  N > 1 nsides are requested.

## Consequences

- **Positive:** Eliminates redundant geometric computation when N > 1 nsides are requested.
  The bit-shift operation is O(N_rows) vs O(N_rows × N_polygons × N_slope_samples) for a
  full recompute. Expected speedup is proportional to (N_nsides - 1) / N_nsides for the
  expensive fuzzy path.
- **Positive:** Guarantees exact consistency across resolutions — the nside=4 sidecar is
  guaranteed to be the parent of the nside=8 sidecar, no floating-point drift.
- **Negative:** PSF weights at lower nsides are now the sum of high-res child weights rather
  than independently computed values. For Gaussian PSF on point sources this is identical;
  for fuzzy polygons it differs slightly because the centroid-to-cell-centroid distance at
  the lower resolution is not simply the sum of upper distances. In practice, the downstream
  accumulator normalizes per cell anyway, and the total weight contribution per source is
  preserved, so the numerical difference is bounded by the metric resolution.
- **Negative:** Adds complexity to `run()` — the loop now has two code paths (smart vs. naive)
  instead of one. The bit-shift aggregation is straightforward but must be tested against
  the current recompute to confirm identical output for PSF-free cases.

## Waiver

N/A. This decision does not override any constraint in `00_CONSTRAINTS.md`.
