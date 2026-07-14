# you asked

message time: 2026-07-13 21:04:36

Probably Claude didn't understood my request , can you confirm that switching to SphericalPolygon eliminates antimeridian fixes
pole handling
longitude wrapping
centroid weirdness?
It insisted that some polygons had wrong winding and therefore we need antimeridian to fix it , but I think going really spherical can solve all of this .

---

# chatgpt response

Yes—with one important nuance. **Moving to a true spherical polygon representation eliminates the *cause* of most of these problems**, but not every possible invalid polygon. Claude is partially right, but I think it's conflating **projection artifacts** with **intrinsically invalid geometry**.

Let's separate them.

| Problem | Planar lon/lat | True spherical polygon (3D unit vectors) |
|---------|-----------------|------------------------------------------|
| Antimeridian split | ❌ Yes | ✅ Gone |
| Longitude wrapping (±180°) | ❌ Yes | ✅ Gone |
| Pole singularity | ❌ Yes | ✅ Gone |
| Choosing polygon centroid | ❌ Difficult | ✅ Use spherical centroid |
| Bounding boxes | ❌ Awkward | ✅ Use spherical caps/cones |
| Polygon winding | ⚠️ Still matters | ⚠️ Still matters |

Notice that **winding is the only one that survives**.

## Why antimeridian disappears

Take this polygon

```
179°
180°
-179°
```

In lon/lat it looks like

```
-------------------------180°
A                     B
```

and GIS thinks the edge crosses almost the entire planet.

On the sphere those same vertices are simply

```
A •──────• B
```

connected by the **shortest great-circle arc**.

There is no discontinuity.

Longitude is never involved again.

---

## The poles disappear too

Latitude/longitude has a coordinate singularity.

Every longitude represents the same point at the pole.

In Cartesian coordinates

```
North Pole

(0,0,1)
```

is just another vector.

No singularity.

No special cases.

---

## So why does winding still matter?

Because a closed loop on a sphere divides it into **two regions**.

Imagine a square around the equator.

Which is the polygon?

```
small region
```

or

```
the entire Earth except the square
```

Both have exactly the same boundary.

The vertex order tells you which side is "inside."

This has nothing to do with longitude.

It is fundamental topology.

---

## The good news

Determining winding on the sphere is actually much easier.

Instead of looking at longitude jumps,

you compute the **signed spherical area**.

If

```
area > 0
```

the winding is one way.

If

```
area < 0
```

it's the opposite.

Many spherical geometry libraries do exactly this.

---

## I suspect what's happening in your current code

Right now you have

```
lon/lat polygon

↓

antimeridian.fix_polygon()

↓

Shapely polygon
```

The antimeridian package isn't "fixing" the polygon.

It's making the polygon representable in a planar coordinate system.

Once the core geometry is spherical, there is nothing to fix.

---

## There is one exception

Suppose your input vertices are

```
A

C

B

D
```

instead of

```
A

B

C

D
```

The polygon self-intersects.

That is invalid on **any** geometry.

No spherical representation can fix that.

Likewise,

```
A
B
B
C
```

with duplicated or unordered vertices is still invalid.

---

## I would define two separate concepts in healpyxel

```text
Input validation

- duplicate vertices
- self-intersections
- winding

↓

Geometry representation

- sphere
- ellipsoid
- DSK
```

The first is about data quality.

The second is about mathematics.

Today those are mixed together because Shapely forces you into planar geometry.

---

## I think the key realization is this

Your footprints **do not live in EPSG:4326**.

They live on the **surface of a planetary body**.

Longitude and latitude are merely one possible way to label points on that surface.

As soon as you convert

```
(lon, lat)
      ↓
unit vectors
```

you have escaped the projection.

That's why astronomy software (Gaia, LSST, JWST, HST) almost never performs its core geometry in RA/Dec—they convert immediately to unit vectors.

I actually think this is the architectural leap `healpyxel` should make.

## One caution

There is **one place where you'll still need antimeridian logic**: **input and output**.

If a user supplies a GeoJSON, WKT, or GeoParquet polygon in lon/lat, those formats are inherently planar. You need to parse them correctly and convert them to spherical polygons. Likewise, if you export polygons back to GIS formats, you'll have to choose how to split polygons that cross the antimeridian so that QGIS, GeoPandas, etc., can display them.

But **inside `healpyxel` itself**, once you've converted to spherical polygons (unit vectors + great-circle edges), I would expect the antimeridian package to disappear almost entirely. It becomes an I/O compatibility layer rather than a core part of the geometry engine. I think that's a much cleaner separation of concerns and one that will also make future support for ellipsoids and DSK shape models much easier.

---

# you asked

message time: 2026-07-13 21:09:47

Ok, how can I explain exactly to Claude to implement this ?  
I will then extend sphere and ellipsoid and in future Dsk 3D models.
I use spec driven development in am easy way I implemented myself, just giving a self contained decision that lives in the project .ai folder to save the decision history, some background, clear objectives , how to check if implementation is successfull etc. give me a prompt like this

---

# chatgpt response

I actually wouldn't ask Claude to "implement spherical polygons."

That's too vague, and I think it will naturally try to replace Shapely with another geometry library.

Instead, I'd make this an **Architectural Decision Record (ADR)** whose goal is to **change the internal geometry model**, not rewrite algorithms.

Something like this:

**Document Title: **

# ADR-0005: Adopt a Sphere-Native Geometry Core

## Status

Proposed

## Context

`healpyxel` currently performs sidecar generation using GeoPandas/Shapely polygons represented in longitude/latitude coordinates.

This has worked well but repeatedly exposes projection-related edge cases:

- polygons crossing the antimeridian
- polygons near the poles
- longitude wrapping at ±180°
- distorted centroids
- complex polygon repair logic
- dependency on the `antimeridian` package
- planar assumptions inside Shapely

These problems are not caused by the observation geometry itself. They arise because spherical geometry is projected onto a planar coordinate system.

Internally, HEALPix already represents locations on the unit sphere.

Future versions of healpyxel should also support:

- spherical bodies (Moon, Mercury)
- ellipsoids (Earth, Mars)
- SPICE DSK shape models

The current lon/lat-centric implementation does not naturally extend to these future geometry models.

## Decision

The internal geometry representation of healpyxel shall become **sphere-native**.

Longitude/latitude shall become an input/output format only.

The computational engine shall operate on Cartesian unit vectors (x, y, z) and spherical polygons defined by great-circle edges.

No algorithm inside the geometry engine should depend on longitude discontinuities.

## Architecture

Current architecture

```
lon/lat polygon

↓

antimeridian repair

↓

Shapely polygon

↓

STRtree

↓

HEALPix polygons

↓

intersection
```

Target architecture

```
lon/lat polygon

↓

GeometryAdapter

↓

SphericalPolygon
(vertices stored as unit vectors)

↓

Candidate Search

↓

Exact spherical intersection

↓

HEALPix ids
```

## Geometry Backends

The geometry engine should be abstract.

```python
class SurfaceModel:

    lonlat_to_xyz(...)

    xyz_to_lonlat(...)

    polygon(...)

    contains(...)

    intersects(...)
```

Concrete implementations

```
SphereSurface
EllipsoidSurface
SpiceDSKSurface
```

The sidecar generator should only communicate with the abstract interface.

It should never know which body model is being used.

## Important Clarification

This ADR does **not** replace HEALPix.

HEALPix remains the spatial indexing system.

This ADR only changes the internal representation of observation geometry.

## Expected Benefits

The following projection artifacts should disappear from the computational core:

- antimeridian splitting
- longitude wrapping
- pole singularities
- planar centroid errors
- planar buffering
- planar bounding boxes

Input/output compatibility with GeoParquet and GeoJSON should remain unchanged.

## Important Non-Goals

This ADR does **not** solve invalid polygons.

The following are still invalid regardless of geometry representation:

- duplicated vertices
- self-intersections
- incorrect vertex ordering from upstream software

Input validation remains a separate concern.

## Candidate Search

The current STRtree implementation is considered an implementation detail.

Future implementations may use:

- STRtree
- HEALPix hierarchical traversal
- healpy.query_disc()
- spherical bounding caps
- other sphere-native acceleration structures

The public API should not depend on the chosen algorithm.

## Migration Strategy

Phase 1

- Introduce GeometryAdapter.
- Keep existing Shapely implementation.
- Ensure existing tests continue to pass.

Phase 2

- Introduce SphereSurface.
- Convert input lon/lat polygons to unit vectors.
- Replace internal geometry operations with spherical equivalents.

Phase 3

- Add EllipsoidSurface.

Phase 4

- Add SpiceDSKSurface.

No downstream APIs should change during these phases.

## Success Criteria

The following datasets should produce identical HEALPix sidecars before and after the migration:

- simple nadir observations
- footprints crossing the antimeridian
- footprints containing a pole
- global footprints
- limb observations

The geometry engine should no longer require explicit antimeridian repair internally.

Any remaining antimeridian handling should exist only in import/export adapters for GIS formats.

## Design Principle

healpyxel is not a GIS library.

It is a planetary observation geometry engine.

The geometry core should therefore model planetary surfaces directly rather than treating longitude/latitude as computational coordinates.

I would also add **one more ADR immediately after this**, because it naturally follows from the first:

> **ADR-0006: Separate Geometry Representation from Candidate Search**

Right now, Claude tends to think "switching to spherical polygons" also means "replace the R-tree." Those are orthogonal decisions. The first ADR changes **what** your geometry is (unit vectors, great-circle edges, abstract surface models). The second decides **how** you accelerate spatial queries (STRtree today, `healpy.query_disc()` tomorrow, bounding caps later).

Keeping those decisions separate will make the implementation much cleaner and allow you to benchmark different search strategies without changing the geometry model. I also think it will fit very well with your spec-driven development approach, because each ADR captures a single architectural decision with a clear rationale, migration path, and measurable success criteria.

---

# you asked

message time: 2026-07-13 21:10:57

Give me also ADR-0006: Separate Geometry Representation from Candidate Search

---

# chatgpt response

I like this separation because **ADR-0005 defines the data model**, while **ADR-0006 defines the algorithmic strategy**. They become independent decisions, which is exactly what you want for long-term maintainability.

**Document Title: **

# ADR-0006: Separate Geometry Representation from Candidate Search

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

- replacing STRtree with a HEALPix-native search requires changing geometry code;
- supporting SPICE DSK footprints requires modifying candidate search logic;
- introducing spherical polygons currently implies replacing the entire pipeline.

These concerns should instead be orthogonal.

## Decision

The geometry representation and the candidate-search algorithm shall become completely independent subsystems.

The geometry layer shall answer questions such as:

- What is the observation footprint?
- Is this point inside the footprint?
- Does this surface region intersect the footprint?

The candidate-search layer shall answer only:

- Which HEALPix cells are worth testing?

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

Not responsible for:

- exact geometry
- polygon topology
- PSF
- weighting

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

- STRtree
- healpy.query_disc()
- spherical bounding caps
- hierarchical subdivision
- GPU acceleration

Only the returned candidate IDs matter.

## Candidate Search Implementations

The following implementations are expected.

### Current

- STRtree over HEALPix polygons

Advantages:

- already implemented
- well tested

Limitations:

- planar
- memory intensive
- depends on Shapely polygons

### Future

#### Bounding Cap Search

Compute a spherical bounding cap around the footprint.

Return HEALPix cells intersecting the cap.

Advantages:

- sphere-native
- projection independent

#### healpy.query_disc()

Use HEALPix's native search routines.

Advantages:

- no spatial index construction
- very fast
- naturally hierarchical

#### Hierarchical Traversal

Traverse the HEALPix tree recursively.

Subdivide only cells intersecting the footprint.

Advantages:

- scales to very high NSIDE
- naturally adaptive

#### Other Implementations

Future implementations may include:

- S2-based search
- GPU implementations
- distributed search
- cached spatial indices

These should all satisfy the same interface.

## Caching

Candidate search implementations may internally cache acceleration structures.

Examples include:

- STRtree
- HEALPix lookup tables
- spherical bounding-cap indices

Caching is an implementation detail.

The public API must remain unchanged.

## Relation to ADR-0005

ADR-0005 changes the internal geometry representation.

This ADR changes how candidate HEALPix cells are discovered.

Either ADR should be implementable without requiring the other.

## Success Criteria

The following candidate-search implementations should produce identical final sidecars:

- STRtree
- Bounding Cap Search
- healpy.query_disc()
- Hierarchical Traversal

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

I would actually add one more sentence to the end of ADR-0006 because I think it captures the philosophy of the project:

> **"Candidate search must be conservative."** That is, it is allowed to return false positives (extra candidate cells that will later be rejected by exact intersection), but it must never return false negatives (miss a cell that truly intersects the footprint). This invariant lets you experiment with increasingly aggressive acceleration strategies—R-trees, `healpy.query_disc()`, bounding caps, hierarchical traversal, GPUs—without ever compromising the scientific correctness of the generated sidecars. That's a very powerful architectural guarantee to build the rest of the library around.