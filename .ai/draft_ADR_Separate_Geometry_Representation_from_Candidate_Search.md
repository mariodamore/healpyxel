
# draft ADR Separate Geometry Representation from Candidate Search

## Status

Proposed

## Context

The current sidecar generation pipeline combines two independent concerns:

1. Representation of observation geometry.
2. Selection of candidate HEALPix cells.

Today these are tightly coupled through GeoPandas and Shapely.

```text
Observation Polygon
        │
        ▼
Shapely Polygon
        │
        ▼
STRtree
        │
        ▼
Candidate HEALPix Polygons
        │
        ▼
Polygon Intersection
```

This coupling makes it difficult to evolve either component independently.

For example:

* replacing STRtree with a HEALPix-native search requires changing geometry code;
* supporting SPICE DSK footprints requires modifying candidate search logic;
* introducing spherical polygons currently implies replacing the entire pipeline.

These concerns should instead be orthogonal.

## Decision

The geometry representation and the candidate-search algorithm shall become completely independent subsystems.

The geometry layer shall answer questions such as:

* What is the observation footprint?
* Is this point inside the footprint?
* Does this surface region intersect the footprint?

The candidate-search layer shall answer only:

* Which HEALPix cells are worth testing?

Neither subsystem should know how the other is implemented.

## Architecture

```text
                 Observation

                      │

              Geometry Backend

      Sphere
      Ellipsoid
      DSK

                      │

              Geometry Adapter

                      │

          Candidate Search Engine

      STRtree
      query_disc()
      Bounding Cap
      Hierarchical Traversal
      Future algorithms

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

* surface representation
* point containment
* polygon intersection
* polygon validity
* spherical or ellipsoidal mathematics

Not responsible for:

* acceleration structures
* indexing
* caching

### Candidate Search Layer

Responsible for:

* reducing the search space
* producing a superset of possible intersecting HEALPix cells

Not responsible for:

* exact geometry
* polygon topology
* PSF
* weighting

Returning extra candidates is acceptable.

Missing valid candidates is never acceptable.

## Candidate Search Interface

Candidate search should expose a minimal interface.

```python
candidate_cells(
    geometry,
    nside
) -> np.ndarray
```

The caller should not know whether the implementation uses:

* STRtree
* healpy.query_disc()
* spherical bounding caps
* hierarchical subdivision
* GPU acceleration

Only the returned candidate IDs matter.

## Candidate Search Implementations

The following implementations are expected.

### Current

* STRtree over HEALPix polygons

Advantages:

* already implemented
* well tested

Limitations:

* planar
* memory intensive
* depends on Shapely polygons

### Future

#### Bounding Cap Search

Compute a spherical bounding cap around the footprint.

Return HEALPix cells intersecting the cap.

Advantages:

* sphere-native
* projection independent

#### healpy.query_disc()

Use HEALPix's native search routines.

Advantages:

* no spatial index construction
* very fast
* naturally hierarchical

#### Hierarchical Traversal

Traverse the HEALPix tree recursively.

Subdivide only cells intersecting the footprint.

Advantages:

* scales to very high NSIDE
* naturally adaptive

#### Other Implementations

Future implementations may include:

* S2-based search
* GPU implementations
* distributed search
* cached spatial indices

These should all satisfy the same interface.

## Caching

Candidate search implementations may internally cache acceleration structures.

Examples include:

* STRtree
* HEALPix lookup tables
* spherical bounding-cap indices

Caching is an implementation detail.

The public API must remain unchanged.

## Relation to ADR-0005

ADR-0005 changes the internal geometry representation.

This ADR changes how candidate HEALPix cells are discovered.

Either ADR should be implementable without requiring the other.

## Success Criteria

The following candidate-search implementations should produce identical final sidecars:

* STRtree
* Bounding Cap Search
* healpy.query_disc()
* Hierarchical Traversal

Differences in execution time or number of intermediate candidates are acceptable.

Differences in the final intersecting HEALPix cells are not.

## Long-Term Vision

The sidecar generation pipeline should become a composition of interchangeable components.

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

Each layer should be independently replaceable, benchmarkable and testable.

## Design Principle

Candidate search is an optimization.

Geometry is a mathematical model.

The correctness of healpyxel must depend only on the geometry layer.

Candidate search should only improve performance and must never alter scientific results.

"Candidate search must be conservative." That is, it is allowed to return false positives (extra candidate cells that will later be rejected by exact intersection), but it must never return false negatives (miss a cell that truly intersects the footprint). This invariant lets you experiment with increasingly aggressive acceleration strategies—R-trees, healpy.query_disc(), bounding caps, hierarchical traversal, GPUs—without ever compromising the scientific correctness of the generated sidecars. That's a very powerful architectural guarantee to build the rest of the library around.
