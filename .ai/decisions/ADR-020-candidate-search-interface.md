# ADR-020: Separate Geometry Representation from Candidate Search

- **Status:** Proposed
- **Date:** 2026-08-16
- **Author:** session 2026-08-16 — promoted from
  `.ai/extra/draft_ADR_Separate_Geometry_Representation_from_Candidate_Search.md`,
  corrected against the current (post-ADR-013) codebase, and extended to cover
  the concrete implementations ADR-019 needs.

## Context

The original draft this ADR promotes diagnosed a real coupling problem: geometry
representation and HEALPix candidate-cell selection were tangled together through
GeoPandas, Shapely, and an `STRtree` built over HEALPix cell polygons. That
diagnosis was accurate *at the time it was written* — under ADR-010, STRtree was
the retained, load-bearing candidate-search mechanism.

It is no longer accurate as a description of the current codebase, and this
promotion corrects it. ADR-013 replaced the STRtree-based pipeline with SLERP
dense point-sampling: the default fuzzy path has **no separate candidate-search
step at all** today. Coverage is emergent from where sampled points happen to
land, not from a "narrow the search space, then test exactly" structure. This is
a different problem from the one the original draft described, though it points
at the same fix: neither STRtree-coupled-to-geometry (pre-ADR-013) nor
no-candidate-search-layer-at-all (post-ADR-013, default path) gives the project a
seam to plug in a new search strategy without touching `process_partition`
directly.

ADR-019 needs exactly that seam for its opt-in exhaustive mode, and needs it now
— it specifies two concrete candidate-search implementations
(`healpy.query_polygon` for convex FOVs, `healpy.query_disc` bounding-cap for
everything else) that don't have a home to live in without this ADR. Rather than
let ADR-019 hard-code its dispatch logic directly into `sidecar.py`, this ADR
defines the interface those implementations — and future ones — should satisfy,
so:

- replacing or adding a candidate-search backend doesn't require touching
  geometry code;
- supporting new geometry backends (SPICE DSK footprints, ellipsoids) doesn't
  require touching candidate-search code;
- introducing a sphere-native exact-intersection primitive (per
  `draft_ADR_SphereNative.md`) doesn't require replacing the whole pipeline.

These concerns should be orthogonal. Today, by omission rather than by design,
they aren't — there's simply nothing there to be orthogonal *to* yet.

## Decision

Geometry representation and candidate-cell search become two independent
subsystems, connected only through the interface below.

The **geometry layer** answers:

- What is the observation footprint?
- Is this point inside the footprint?
- Does this surface region intersect the footprint?

The **candidate-search layer** answers only:

- Which HEALPix cells are worth testing exactly?

Neither subsystem knows how the other is implemented.

## Architecture

```text
                 Observation

                      │

              Geometry Backend

      Sphere
      Ellipsoid
      DSK (future)

                      │

              Geometry Adapter

                      │

          Candidate Search Engine

      query_polygon()      (convex, native)
      query_disc()          (bounding cap, always safe)
      Hierarchical Traversal (future)
      STRtree               (historical — removed by ADR-013)

                      │

         Candidate HEALPix Cell IDs

                      │

      Exact Geometry Intersection Test

                      │

           Final Sidecar Generation
```

## Responsibilities

### Geometry Layer

Responsible for:

- surface representation
- point containment
- polygon intersection
- polygon validity
- spherical or ellipsoidal mathematics

Not responsible for:

- acceleration structures
- indexing
- caching

### Candidate Search Layer

Responsible for:

- reducing the search space
- producing a superset of possible intersecting HEALPix cells
- declaring, if relevant, the class of geometry it's valid for (see
  Applicability, below — this is new relative to the original draft)

Not responsible for:

- exact geometry
- polygon topology
- PSF
- weighting

Returning extra candidates is acceptable. Missing a valid candidate is never
acceptable — this is the invariant the whole ADR exists to protect (see Design
Principle).

## Candidate Search Interface

```python
candidate_cells(
    geometry,
    nside,
) -> np.ndarray
```

The caller should not know whether the implementation uses `query_polygon`,
`query_disc`, hierarchical subdivision, or a future GPU/S2-based search. Only
the returned candidate IDs matter.

**Backend configuration vs. call-site uniformity.** ADR-019's two backends need
different tuning knobs (`query_polygon` needs `fact`; `query_disc` needs an
angular margin). The interface above stays uniform by keeping that configuration
at construction time, not call time — e.g. a backend is a small configured
callable or partial application, not a function whose signature grows with every
new implementation's parameters:

```python
search = QueryPolygonSearch(fact=16)          # or QueryDiscSearch(margin_deg=1.0)
candidates = search(geometry, nside)
```

**Applicability.** The original draft assumed every candidate-search
implementation is universally valid for any geometry; ADR-019 breaks that
assumption — `query_polygon` hard-errors (`RuntimeError: polygon is not
convex`) on non-convex input rather than silently returning a wrong (too-small)
result. That's a precondition, not a performance trade-off, and the interface
needs a documented, uniform way to express it so a dispatcher can fall back
cleanly instead of every call site re-implementing its own convexity check.
Concretely: a backend either declares `supports(geometry) -> bool`, or raises a
specific `CandidateSearchUnsupported` exception the dispatcher catches — either
is fine, but pick one convention project-wide rather than letting ADR-019's
manual convex/concave branch be the only precedent. A dispatcher backend
(`AutoSearch`, trying backends in declared preference order) is the natural
composition once this exists.

## Candidate Search Implementations

### Historical

- **STRtree over HEALPix polygons** — the ADR-010/pre-013 approach. Planar,
  memory-intensive, depends on caching Shapely polygons for every HEALPix cell
  in range. Removed from the default path by ADR-013. Not currently used
  anywhere in the codebase; listed here for continuity with ADR-010's rationale,
  not as a live option.

### Current (introduced by ADR-019)

- **`healpy.query_polygon(nside, xyz_vertices, inclusive=True, fact=...,
  nest=True)`** — native HEALPix C++ implementation, no Python loop, no cached
  index. Applicability: convex spherical polygons only (hard error otherwise —
  verified empirically). Benchmarked ~40x faster than a planar point-grid
  equivalent on a 10°×10° FOV at nside=32.
- **`healpy.query_disc(nside, centroid_vec, radius, inclusive=True, nest=True)`**
  bounding-cap search — universally applicable regardless of polygon shape or
  convexity, at the cost of looser (more false-positive) candidates for
  elongated or irregular footprints. This is the fallback when `query_polygon`'s
  precondition isn't met.

### Future

- **Hierarchical Traversal** — recursive HEALPix-tree subdivision, descending
  only into cells intersecting the footprint. Scales to very high nside;
  naturally adaptive. ADR-010 abandoned an earlier attempt at this over a
  coordinate-conversion bug at the poles, not over the approach itself — worth
  revisiting under this ADR's interface once there's a candidate-search-layer
  seam to put it behind, so a fixed implementation doesn't require touching
  geometry code again.
- **S2-based search, GPU implementations, distributed search, cached spatial
  indices** — all should satisfy the same `candidate_cells()` interface.

## Caching

Implementations may internally cache acceleration structures (e.g., a
hierarchical-traversal index, or HEALPix lookup tables). Caching is an
implementation detail; the public interface must remain unchanged regardless of
whether a given backend caches anything.

## Alternatives Considered

- **Leave candidate-search dispatch inline in `process_partition` /
  `sidecar.py`** (i.e., let ADR-019's convex/concave branch be the only
  instance, with no shared interface): rejected — this is precisely the
  coupling the original draft warned against ("introducing spherical polygons
  currently implies replacing the entire pipeline"). Every future
  candidate-search idea (hierarchical traversal done correctly, S2, GPU) would
  mean re-touching `process_partition` and re-deriving the "never return false
  negatives" invariant from scratch at each call site.
- **Fold this into `draft_ADR_SphereNative.md`'s broader `SurfaceModel`
  abstraction** rather than landing it separately: considered, but rejected as
  a sequencing choice, not a rejection of the idea — `SurfaceModel` needs
  sphere-native `contains()`/`intersects()` primitives that don't exist yet, and
  ADR-019 shouldn't wait on that larger effort. The original draft already
  anticipated this: "Either ADR should be implementable without requiring the
  other" (there, referring to what is now ADR-013; the same reasoning applies to
  `SphereNative`). `SurfaceModel`, when it lands, can become a candidate-search
  *consumer* — a geometry backend the search layer queries — without changing
  this interface.
- **Revive STRtree as the (only) implementation behind the new interface**:
  rejected on the same grounds ADR-019 rejected it — `query_polygon`/`query_disc`
  give the same conservative-superset guarantee via native HEALPix calls, no
  cached Shapely index required for the search step itself.
- **Enforce the "never false-negative" invariant only informally (code review /
  docstrings)** rather than structurally: rejected as insufficient given how
  easy it is to violate silently (a grid-sampling approach can violate it
  without ever raising an error, as ADR-019's review of the original grid
  proposal showed). See Success Criteria for the structural check this ADR
  recommends instead.

## Consequences

### Positive

- Future candidate-search backends (hierarchical traversal, S2, GPU) are
  addable without touching geometry code, and future geometry backends (DSK,
  ellipsoid refinements) are addable without touching search code — the
  original draft's central goal.
- ADR-019's two implementations have a documented home and a documented
  precondition-handling convention, instead of a one-off inline branch.
- The "conservative, never under-returns" invariant becomes a structural
  property of the interface (enforceable via the differential test in Success
  Criteria) rather than an unwritten expectation each new backend has to
  rediscover.
- Unblocks hierarchical traversal being retried later without reopening
  ADR-010's postmortem from scratch — it plugs into the same seam.

### Negative

- Adds an abstraction layer (interface + dispatcher + per-backend config
  objects) where today there is either a single inline function
  (`_query_healpix_single_polygon`) or nothing (default path). For a project
  whose `00_CONSTRAINTS.md` explicitly favors avoiding premature abstraction,
  this needs to earn its keep — justified here because ADR-019 already needs
  two backends with a fallback relationship, which is the minimum case where an
  interface pays for itself over inlining.
- The `supports()` / exception-based applicability convention is new; nothing
  in the codebase uses it yet, so ADR-019 will be the first (and, initially,
  only) real test of whether the convention is ergonomic.
- Backend-specific configuration (`fact`, `margin_deg`) living outside the
  uniform `candidate_cells()` signature means construction-site wiring needs its
  own small amount of plumbing in `process_partition`.

## Success Criteria

The following candidate-search implementations, given the same input geometry
and nside, must produce **identical final sidecars** after the exact-intersection
step:

- `query_polygon` (where applicable)
- `query_disc` bounding cap
- Hierarchical Traversal (once implemented)
- STRtree (historical, for regression comparison only)

Differences in execution time or in the number of intermediate candidates are
acceptable. Differences in the final intersecting HEALPix cell set are not.
Recommend implementing this as a differential test fixture — same geometry, all
registered backends, assert equal final cell sets — so a future backend that
silently violates the no-false-negatives invariant fails a test instead of
shipping (this directly addresses the Negative Consequence above: the
convention is new and untested).

## Waiver

Not applicable. No constraint in `00_CONSTRAINTS.md` is overridden. ADR-001
(`healpy` over `cdshealpix`) is preserved throughout — every candidate-search
implementation discussed here uses `healpy`-native calls or already-approved
Shapely operations; `cdshealpix` is never used.

## Relation to Other Decisions

- **ADR-013** (not "ADR-0005," corrected from the original draft, which appears
  to have used a stale/placeholder number): ADR-013 changed the internal
  geometry representation (SLERP dense sampling on `Sphere`/`Ellipsoid`
  backends). This ADR changes how candidate HEALPix cells are discovered.
  Consistent with the original draft's framing, either is implementable without
  requiring the other — this ADR does not touch `body.lonlat_to_xyz()` or any
  other ADR-013 geometry code, only what feeds candidate cells into the
  exact-intersection step.
- **ADR-010**: candidate search returns to the codebase under this ADR, but not
  as a revival of ADR-010's STRtree — as a from-scratch interface with a
  documented invariant and a differential-test success criterion ADR-010's
  approach never had.
- **ADR-019** is this interface's first concrete consumer, providing the
  `query_polygon`/`query_disc` backends and the convex/concave dispatcher. This
  ADR should land alongside or immediately before ADR-019's implementation, not
  after — ADR-019's Implementation Notes already assume `candidate_cells()`
  exists.
- **`draft_ADR_SphereNative.md`**: a future, broader `SurfaceModel` abstraction
  can become a geometry-layer input to this ADR's candidate-search layer without
  either side needing to change, per the Alternatives Considered discussion
  above.

## Long-Term Vision

```text
Geometry Backend
        │
        ▼
Candidate Search
        │
        ▼
Exact Intersection
        │
        ▼
Optional PSF Weighting
        │
        ▼
GeoParquet Sidecar
```

Each layer independently replaceable, benchmarkable, and testable.

## Design Principle

Candidate search is an optimization. Geometry is a mathematical model. The
correctness of healpyxel must depend only on the geometry layer (and, at the
final step, the exact-intersection test) — never on which candidate-search
backend happened to be selected.

Candidate search must be conservative: it may return false positives (extra
candidates later rejected by exact intersection) but must never return false
negatives (a cell that truly intersects the footprint, missed). This is the
invariant ADR-019's review exists to protect — the original point-grid proposal
would have violated it silently; `query_polygon`/`query_disc` don't, by
construction. This invariant is what lets future work swap in increasingly
aggressive acceleration (hierarchical traversal, S2, GPU) without ever
compromising the scientific correctness of generated sidecars.
