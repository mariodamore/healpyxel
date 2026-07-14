
# draft ADR: Adopt a Sphere-Native Geometry Core

## Status

Proposed

## Context

`healpyxel` currently performs sidecar generation using GeoPandas/Shapely polygons represented in longitude/latitude coordinates.

This has worked well but repeatedly exposes projection-related edge cases:

* polygons crossing the antimeridian
* polygons near the poles
* longitude wrapping at ±180°
* distorted centroids
* complex polygon repair logic
* dependency on the `antimeridian` package
* planar assumptions inside Shapely

These problems are not caused by the observation geometry itself. They arise because spherical geometry is projected onto a planar coordinate system.

Internally, HEALPix already represents locations on the unit sphere.

Future versions of healpyxel should also support:

* spherical bodies (Moon, Mercury)
* ellipsoids (Earth, Mars)
* SPICE DSK shape models

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

* antimeridian splitting
* longitude wrapping
* pole singularities
* planar centroid errors
* planar buffering
* planar bounding boxes

Input/output compatibility with GeoParquet and GeoJSON should remain unchanged.

## Important Non-Goals

This ADR does **not** solve invalid polygons.

The following are still invalid regardless of geometry representation:

* duplicated vertices
* self-intersections
* incorrect vertex ordering from upstream software

Input validation remains a separate concern.

## Candidate Search

The current STRtree implementation is considered an implementation detail.

Future implementations may use:

* STRtree
* HEALPix hierarchical traversal
* healpy.query_disc()
* spherical bounding caps
* other sphere-native acceleration structures

The public API should not depend on the chosen algorithm.

## Migration Strategy

Phase 1

* Introduce GeometryAdapter.
* Keep existing Shapely implementation.
* Ensure existing tests continue to pass.

Phase 2

* Introduce SphereSurface.
* Convert input lon/lat polygons to unit vectors.
* Replace internal geometry operations with spherical equivalents.

Phase 3

* Add EllipsoidSurface.

Phase 4

* Add SpiceDSKSurface.

No downstream APIs should change during these phases.

## Success Criteria

The following datasets should produce identical HEALPix sidecars before and after the migration:

* simple nadir observations
* footprints crossing the antimeridian
* footprints containing a pole
* global footprints
* limb observations

The geometry engine should no longer require explicit antimeridian repair internally.

Any remaining antimeridian handling should exist only in import/export adapters for GIS formats.

## Design Principle

healpyxel is not a GIS library.

It is a planetary observation geometry engine.

The geometry core should therefore model planetary surfaces directly rather than treating longitude/latitude as computational coordinates.


I would also add one more ADR immediately after this, because it naturally follows from the first:

draft_ADR_Separate_Geometry_Representation_from_Candidate_Search.md

Right now, Claude tends to think "switching to spherical polygons" also means "replace the R-tree." Those are orthogonal decisions. The first ADR changes what your geometry is (unit vectors, great-circle edges, abstract surface models). The second decides how you accelerate spatial queries (STRtree today, healpy.query_disc() tomorrow, bounding caps later).

Keeping those decisions separate will make the implementation much cleaner and allow you to benchmark different search strategies without changing the geometry model. I also think it will fit very well with your spec-driven development approach, because each ADR captures a single architectural decision with a clear rationale, migration path, and measurable success criteria.
