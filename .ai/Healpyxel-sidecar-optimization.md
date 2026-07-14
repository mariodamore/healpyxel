> From: https://chatgpt.com/c/6a51fc45-1e80-83ed-b8b7-55b64d3b9f65

# you asked

message time: 2026-07-11 10:20:02

Healpyxel project   
i wrote this package https://mariodamore.github.io/healpyxel/ i want to improve the sidecar generation https://github.com/mariodamore/healpyxel/tree/dev currently dev branch uses geopandas to link all input fov with all healpix cells touching or using center.  
  
  
What is the typical number of FOVs and the target HEALPix nside value you are processing?  
  
variable from thousands to a million, nside from 8 to top 128, higher in future .  
I use lat lon coordinates.  
  
Are your FOVs standard geometries (e.g., rectangles, circles) or arbitrary irregular polygons?  
  
in nadir are recaltangles, but extreme geometry can be distorted.  I typically have few vertex points of the polygons, not dense point along the exterior.
  
What file format (e.g., CSV, JSON, FITS) is your desired target for the generated sidecar index?  
  
I'm using geoparquet for efficient analysis.  
  
  
Can you access the links I gave you ?

---

# chatgpt response

Yes. I can access the documentation site, but I couldn't directly browse the GitHub source tree from the URL you provided. I was, however, able to inspect the published documentation and package description, including the workflow around `healpyxel_sidecar`, the fuzzy/strict modes, and the GeoParquet outputs. ([PyPI](https://pypi.org/project/healpyxel/?utm_source=chatgpt.com))

Based on what you've told me, I think your current approach

> FOV polygons → GeoPandas spatial join → HEALPix polygons

is a good reference implementation, but it is probably not the algorithm you want for production datasets with **10⁶ footprints**.

The main bottleneck is that you're asking a generic GIS engine to solve a problem that has much stronger structure:

- all target geometries are known beforehand (HEALPix)
- they form a hierarchical tessellation
- they're on the sphere rather than a projected plane
- FOVs have very few vertices

That means you can do much better than polygon-vs-polygon spatial joins.

I think the long-term architecture should evolve into something like:

| Version | Method | Expected scalability |
|---------|--------|----------------------|
| v0 | GeoPandas sjoin (today) | good for ≤100k |
| v1 | STRtree + Shapely 2 | ~5–20× faster |
| v2 | Hierarchical HEALPix traversal | another order of magnitude |
| v3 | Pure spherical algorithm (no GeoPandas dependency) | HPC scale |

For planetary missions (MASCS, MERTIS, CRISM, etc.), I would actually avoid treating HEALPix as polygons most of the time.

Instead, I'd use a hierarchical search:

1. Compute the spherical bounding cap (or bounding box) of the footprint.
2. Find candidate HEALPix cells intersecting that cap.
3. Refine only those candidates with polygon intersection.
4. Emit `(source_id, healpix_id, weight)`.

For `nside=128`, a footprint typically overlaps only tens of cells, so the complexity becomes almost proportional to the number of assignments rather than the total number of pixels.

---

One thing that also caught my attention is your mention that you only have **a few footprint vertices**.

That's actually an advantage.

For spacecraft observations you generally don't need dense polygons. You can perform spherical clipping directly on great-circle edges, which is both more accurate and much faster than first densifying and then using planar GIS operations.

---

I think healpyxel could eventually become more than an aggregation package. It could become the **standard spherical indexing engine for planetary footprints**, analogous to what H3 is for Earth applications.

The sidecar generation is really the core intellectual contribution of the project.

I'd be happy to help design a next-generation sidecar algorithm specifically for million-footprint datasets. I think we can come up with an approach that is **10–100× faster** than the current GeoPandas implementation while remaining fully compatible with your GeoParquet sidecar format.

---

# you asked

message time: 2026-07-11 10:21:32

I already started a R-tree sidecar indexing in sidecar, give me a prompt I can feed Claude to to start planning those improvements

---

# chatgpt response

Here's a prompt I'd use. It frames Claude as an architect rather than asking it to immediately write code, which usually produces better designs.

---

**Document Title: **

# Role

You are an expert in computational geometry, HEALPix, spatial indexing, planetary remote sensing, and high-performance Python. You are reviewing the architecture of the open-source package **healpyxel**.

Your goal is **not** to rewrite the package, but to help design the next-generation sidecar indexing engine that will scale from thousands to millions of observation footprints while remaining simple to maintain.

## Project

healpyxel creates sidecar indices linking observation footprints (FOVs) to HEALPix cells.

Current development branch:

- already contains an experimental R-tree implementation
- currently relies on GeoPandas/Shapely
- outputs GeoParquet sidecar files
- supports different matching modes (touching, center, etc.)

Repository:

https://github.com/mariodamore/healpyxel

Documentation:

https://mariodamore.github.io/healpyxel/

## Dataset characteristics

Typical number of footprints:

- 10³ to 10⁶

Current HEALPix NSIDE:

- 8–128
- should scale to higher NSIDE in the future

Coordinate system:

- latitude/longitude on the sphere

Footprints:

- mostly spacecraft field-of-view polygons
- near nadir they are rectangles
- off-nadir they become distorted quadrilaterals
- polygons usually contain only 4–10 vertices
- polygon edges are not densely sampled

Output:

- GeoParquet sidecar table

Each sidecar record links

(observation_id, healpix_cell)

and may later contain additional metadata such as overlap fraction or coverage weight.

## Current implementation

The current implementation builds HEALPix polygons and performs polygon-based spatial joins using GeoPandas.

This is correct but becomes expensive for large datasets.

An experimental R-tree implementation has already been started.

## What I want from you

Please perform an architectural review.

Do NOT immediately propose code.

Instead discuss:

1. Strengths and weaknesses of the current GeoPandas approach.

2. Whether the R-tree strategy is the correct long-term direction.

3. Whether HEALPix's hierarchical nature should replace generic GIS algorithms.

4. Algorithms used by astronomy libraries or sky-survey software for footprint-to-HEALPix indexing.

5. Whether spherical geometry should replace planar geometry.

6. Candidate algorithms including:
   - STRtree
   - R-tree
   - bounding-cap filtering
   - hierarchical HEALPix traversal
   - recursive subdivision
   - S2 geometry
   - H3 comparison
   - vectorized point-in-polygon
   - prepared geometries
   - spherical polygon clipping

7. Complexity analysis of each approach.

8. Memory requirements.

9. Parallelization opportunities.

10. GPU feasibility.

11. Whether the sidecar format itself could be improved.

12. How to future-proof the architecture for NSIDE > 128 and tens of millions of observations.

## Deliverables

Please produce:

1. A review of the existing architecture.

2. A comparison table of candidate algorithms.

3. A proposed roadmap with stages such as:
   - Version 1
   - Version 2
   - Version 3

4. A recommended long-term architecture.

5. Identify any existing open-source libraries that solve parts of this problem and could be reused instead of reinvented.

Assume maintainability is as important as raw performance.

Do not write implementation code unless explicitly requested in a later prompt.

This prompt should steer the discussion toward architecture, algorithms, scalability, and existing ecosystem solutions before any coding begins. Once you have Claude's design review, it's much easier to evaluate trade-offs and decide which optimizations are worth implementing.

---

# you asked

message time: 2026-07-11 12:44:06

I also started the idea of implementing PSF both for instrument and for target cell healpix.for healpix or relatively straightforward, due to the constant angular size , I am doubting how to implement this for spacecraft FOV .angular aperture is constant, but changing spacecraft elevation and observation angle change surface fov. give me some ideas for this .
 I only have instrument for coordinates projected onto the target, not the 3D arrays for the rays .I m using mostly spherical bodies (mercury, moon ) for now.

---

# chatgpt response

This is actually a very interesting direction, and I think it's where **healpyxel** could distinguish itself from existing HEALPix tools. Most packages treat an observation as a binary polygon ("inside" or "outside"), whereas instruments really measure a **weighted integral** of the surface radiance.

The key point is that the PSF fundamentally lives in **angular space**, not on the planetary surface.

As you noted:

- the instrument PSF is fixed in instrument coordinates (e.g., Gaussian, Airy disk, measured PSF),
- the spacecraft altitude and emission angle determine how that angular PSF projects onto the surface,
- the projected footprint changes shape and area, but the instrument itself hasn't changed.

Since you don't have the full ray bundle, I'd consider several levels of approximation.

### Level 1: Uniform footprint (current)

Every location inside the FOV receives equal weight.

This is essentially

$$
w(x)=1
$$

inside the polygon.

This is simple but ignores optical blur.

---

### Level 2: Angular Gaussian projected onto the surface (recommended)

For every observation:

1. Compute the footprint centroid.
2. Estimate local East-North tangent vectors.
3. Estimate the surface scale (meters per microradian) from spacecraft altitude.
4. Project the instrument PSF as an ellipse on the tangent plane.

Near nadir this becomes a circular Gaussian.

Toward the limb it stretches into an ellipse because the projection is oblique.

This requires only

- footprint polygon
- spacecraft altitude
- incidence/emission angle (or at least viewing angle)

which you often already have.

---

### Level 3: Affine transform from instrument frame

Since you know the projected corner coordinates:

- instrument detector is originally a rectangle
- surface polygon is a quadrilateral

Compute the homography (or affine approximation)

```
Instrument pixel plane
        ↓
Surface polygon
```

Then define the PSF in detector coordinates and map it through the transform.

Advantages:

- no ray tracing
- naturally handles skewed footprints
- reproduces off-nadir distortion

This is probably the sweet spot.

---

### Level 4: Barycentric interpolation

Instead of treating the footprint as a single polygon:

- divide quadrilateral into two triangles
- interpolate detector coordinates using barycentric coordinates
- evaluate the PSF in detector space

This is surprisingly accurate for spacecraft footprints.

---

### Level 5: Future ray-tracing mode

Eventually, if SPICE kernels or instrument models provide the actual viewing rays,

```
pixel
   ↓
ray bundle
   ↓
surface intercepts
   ↓
PSF integration
```

healpyxel could support this without changing the API.

---

## For HEALPix weighting

Once you have a continuous PSF, the sidecar changes from

```
obs → cell
```

to

```
obs → cell → weight
```

where

```
Σ weights = 1
```

This is much more useful scientifically.

---

## I would avoid rasterizing

A tempting solution is

```
polygon
↓
high-resolution raster
↓
sample PSF
↓
aggregate
```

I think that's the wrong direction.

Instead evaluate the PSF directly at candidate HEALPix cell centers (or integrate over cells if needed).

For NSIDE ≤128 the number of intersecting cells is usually only a few dozen, making this approach much cheaper.

---

## One idea I particularly like

Instead of defining the PSF on the planetary surface, define it in **normalized detector coordinates**.

Imagine every footprint starts as a unit square:

```
(-1,-1)        (1,-1)

(-1, 1)        (1, 1)
```

Your PSF is always defined there:

- Gaussian
- measured optics
- asymmetric
- wavelength dependent

Then every observation computes a mapping

```
unit square
        ↓
observed quadrilateral
```

using the four projected corner coordinates.

This means:

- the PSF is **independent of altitude**,
- **independent of the target body**,
- **independent of the projection**,

and only the mapping changes from observation to observation. The same detector PSF can therefore be applied consistently to Mercury, the Moon, Mars, or an asteroid.

For **MERTIS**, **MASCS**, **VIRTIS**, **OMEGA**, and many other pushbroom or framing instruments, this detector-space formulation aligns naturally with how the instruments are calibrated. It also gives you a clean abstraction in `healpyxel`: instruments provide a detector-space PSF, while `healpyxel` handles the geometric transformation to the surface and computes the resulting HEALPix weights. I think this separation of responsibilities would make the package both scientifically robust and extensible to many planetary missions.

---

# you asked

message time: 2026-07-11 12:46:28

Would be then better to convolve instrument psf and healpix cell pfs , where the latter depends only on cell size or nsize, or would be better to treat the healpix cells as binary inside / outside?

---

# chatgpt response

I actually think this gets to the heart of what the **sidecar** should represent.

There are really **two independent kernels** in your observation model:

1. **Instrument Response Function (IRF / PSF)** — how the instrument weights incoming radiance as a function of angle in the detector.
2. **HEALPix Basis Function** — how the continuous planetary surface is represented by discrete cells.

These are conceptually different.

## Option 1: Binary HEALPix cells (my recommendation)

Treat each HEALPix cell as a constant basis function:

$$
B_i(\mathbf{x}) =
\begin{cases}
1 & \mathbf{x}\in \text{cell }i\\
0 & \text{otherwise}
\end{cases}
$$

Then compute

$$
w_i=\int_{\text{cell }i} PSF(\mathbf{x})\,dA.
$$

This means the weight is simply the fraction of the PSF falling inside each cell.

Advantages:

- mathematically clean
- independent of NSIDE
- conservation of energy
- weights always sum to one
- changing NSIDE simply changes the integration grid

This is essentially how image resampling and finite-element methods are formulated.

---

## Option 2: Give every HEALPix cell its own PSF

Suppose every cell is represented by a Gaussian whose σ depends on the cell size.

Then the effective kernel becomes

$$
PSF_{eff}=PSF_{instrument}*PSF_{healpix}
$$

This is attractive because convolution is elegant.

However...

### The problem

A HEALPix pixel is **not** a measurement device.

It has no intrinsic blur.

It is simply a basis function used to discretize the surface.

Adding a PSF means you are intentionally smoothing your data before analysis.

That can be desirable, but it is an analysis choice, not part of the observation geometry.

---

## Analogy

Imagine a CCD camera.

The CCD pixels are square.

When projecting an image onto the CCD, you don't convolve the optics with a "CCD PSF."

Instead:

- optics provide the PSF
- pixels integrate that PSF over their finite area

HEALPix cells are analogous to CCD pixels.

---

## A better interpretation

Instead of calling it a HEALPix PSF, think of it as the **cell response function**

$$
C_i(\mathbf{x})=
\begin{cases}
1 & \text{inside cell}\\
0 & \text{outside}
\end{cases}
$$

Then

$$
w_i=\int PSF(\mathbf{x})\,C_i(\mathbf{x})\,dA.
$$

This is exactly what you want scientifically.

---

## An elegant API for healpyxel

I would actually separate these concepts in the library.

```python
InstrumentKernel
    GaussianPSF
    AiryPSF
    MeasuredPSF
    Uniform

CellKernel
    BinaryCell          # default
    GaussianCell        # optional
    LanczosCell         # experimental
```

Then the user chooses

```python
kernel = InstrumentPSF(...) + BinaryCell()
```

or

```python
kernel = InstrumentPSF(...) + GaussianCell()
```

The default would always be **BinaryCell**, while Gaussian or other cell kernels would be explicitly labeled as **analysis/resampling kernels** rather than observation kernels.

---

## One more idea I think is worth considering

Since healpyxel aims to become a generic planetary package, I would **not store only the `(obs_id, cell_id)` pairs** in the sidecar. Instead, I would evolve it into a sparse response matrix:

| obs_id | healpix | weight |
|--------|----------|--------|
| 17 | 10583 | 0.23 |
| 17 | 10584 | 0.41 |
| 17 | 10585 | 0.28 |
| 17 | 10586 | 0.08 |

where the weights satisfy

$$
\sum_i w_i = 1.
$$

This is far more powerful than a binary index. It represents the **observation operator** mapping the continuous surface to discrete measurements. The same sparse matrix can then be reused for forward modeling, mosaicking, deconvolution, uncertainty propagation, super-resolution, and inversion. For missions like MERTIS, MASCS, Diviner, or future lunar and asteroid instruments, this would make `healpyxel` more than an indexing library—it would become a reusable geometric engine for planetary remote sensing. I think that would be a distinctive and scientifically valuable direction for the project.

---

# you asked

message time: 2026-07-11 19:06:21

What about this proposal   ? 
My extra commmennts 
- N=8 and 512 are only examples, the user would choose which higher level use to decompose FOV based on instrument angular size and current healpix grid size, this proposal use them as fixed.
- can I calculate the gov gaussian in a clever efficient way in the higher n healpix cells space directly?
  
    
You are an expert Python tool developer specializing in planetary science spatial indices and optimized NumPy/HEALPix vector operations.     
    
I need you to plana an implemention of a dual-pass, disk-cached Point Spread Function (PSF) integration feature for my package `healpyxel` (https://github.io).     
    
### Pipeline Architecture & Constraints    
1. **Decoupled Architecture:** Sidecar generation and scientific aggregation are strictly separated.     
2. **Disk Persistence:** Auxiliary sidecar files are generated once and stored permanently on disk as GeoParquet/Parquet. I hate repeating expensive geospatial calculations.    
3. **Physical Correctness:** HEALPix cells are treated as rigid binary spatial bins. The PSF is purely an instrument property evaluated by integrating its continuous 2D Gaussian function over the cells using hierarchical sub-pixel sampling.    
    
### The Two-Pass Workflow to Implement    
A complete run must proceed in these two distinct steps:    
    
- **Pass 1 (Unweighted Base Generation):** Run your existing geometric pipeline to generate two raw, unweighted sidecar index files on disk: `sidecar_n512.parquet` (the high-resolution master grid used for sub-pixel splitting) and `sidecar_n8.parquet` (the target low-resolution analysis grid). These contain only `[fov_id, healpix_id]`.    
- **Pass 2 (PSF Integration Engine):** Run a secondary weighting pipeline. It must read the `n512` and `n8` sidecars from disk, evaluate a 2D Gaussian PSF on the centers of the `n512` sub-pixels, use fast bit-shifting (`child_id >> 12`) to map and aggregate those weights into the `n8` parent cells, normalize the weights to sum to 1.0 per `fov_id`, and save a new weighted `sidecar_n8_weighted.parquet` file containing `[fov_id, healpix_id, psf_weight]`.    
    
### Your Task    
Modify/add modules in the codebase to support this workflow. Please deliver:    
    
1. **`psf_integration.py` Module:**    
   - A highly optimized, fully vectorized function that processes millions of rows instantly without any internal Python loops over pixels.    
   - Use `hp.pix2ang(nside=512, ..., nest=True)` to instantly resolve sub-pixel coordinates without geometry queries.    
   - Implement a safe coordinate delta check for the 2D Gaussian calculation (ensure you handle coordinate wrapping if an FOV crosses the boundaries or poles).    
   - Use pandas or NumPy grouping to aggregate the sub-pixel weights into their `nside=8` parent blocks.    
   - Normalize the weights so they sum to 1.0 per `fov_id`. If a cell intersection exists but receives no weight, fill it with `0.0`.    
    
2. **Downstream Execution Pattern:**    
   - Ensure the downstream aggregation engine is updated to check for the presence of the `psf_weight` column. If present, it must apply the weights during data binning; if absent, it must fallback to treating the sidecar as a standard unweighted binary gate.    
    
Keep the code modular, strictly type-hinted, and performant enough to handle up to 1,000,000 FOVs.

---

# chatgpt response

I think the overall idea is **excellent**, but there are a couple of assumptions that I'd challenge because they may become limiting as `healpyxel` evolves. Since you want this to be a core part of the library, I'd try to make it independent of any particular pair of NSIDEs.

## What I like

The decoupling is exactly right.

```
Geometry
   ↓
sidecar
   ↓
PSF weighting
   ↓
weighted sidecar
   ↓
aggregation
```

This has several advantages:

- expensive geometry computed only once
- different PSFs can be applied to the same observations
- users can experiment with different instrument models
- future support for wavelength-dependent PSFs
- future support for anisotropic PSFs

I think this architecture is one of the strongest aspects of your proposal.

---

## I would avoid hardcoding NSIDE=512

This is the part I'd redesign.

Your proposal says

```
n512  -> master grid
n8    -> analysis grid
```

Instead I'd make it

```
nside_sub
nside_analysis
```

chosen automatically or by the user.

For example

```
MERTIS
instrument IFOV ≈ 500 m

↓

minimum sampling:
~5–10 samples across FWHM

↓

required subgrid:
NSIDE 1024
```

whereas another instrument may only need NSIDE 128.

I would never bake 512 into the implementation.

---

## I wouldn't use bit shifting

The expression

```
child >> 12
```

only works because

```
512 = 8 × 2⁶
```

and because NEST ordering has a particular hierarchy.

Instead use the general relationship

```python
factor = int(np.log2(nside_sub // nside_parent))
parent = child >> (2 * factor)
```

That works for

- 1024→128
- 512→32
- 2048→16

without changing the code.

---

## The Gaussian shouldn't necessarily be evaluated in latitude/longitude

This is actually my biggest concern.

Suppose you compute

```
Δlat
Δlon
```

and plug those into

```
exp(-(dx²+dy²)/(2σ²))
```

Near the poles this becomes distorted.

Instead I'd work entirely in the local tangent plane.

For each FOV

```
center

↓

build local ENU basis

↓

project child cell centers

↓

evaluate Gaussian
```

Then the Gaussian is always isotropic in angular space.

---

## Can you compute the Gaussian directly on the higher-NSIDE cells?

Yes—and I think that's exactly what you should do.

In fact, I'd go one step further.

Don't think of it as "evaluating the Gaussian."

Think of it as a **precomputed stencil**.

Suppose the instrument PSF has

```
σ = 0.3°
```

At NSIDE 512 only maybe

```
300
```

cells lie within

```
3σ
```

You don't need to evaluate millions of exponentials.

Instead

```
center

↓

query nearby HEALPix cells

↓

compute offsets

↓

evaluate Gaussian once
```

This becomes a sparse stencil around each observation.

---

## Even better: precompute by angular offset

Here's an idea I haven't seen implemented in planetary software.

Your PSF depends only on

```
angular distance
```

from the boresight.

Not on

- Mercury
- Moon
- altitude
- latitude

only on angular separation.

So you could precompute

```
distance
↓

weight
```

for every possible offset on a sufficiently fine angular grid.

Then for each FOV

```
child cells

↓

angular separation

↓

lookup table

↓

weight
```

No exponentials inside the main loop.

---

## One concern about the current proposal

The proposal says

> use the centers of the n512 subpixels

That is a Monte Carlo approximation.

Scientifically it's probably fine.

But if the Gaussian becomes narrower than one subpixel, accuracy degrades.

I'd therefore expose two methods:

**Fast**

```
cell center
```

**Accurate**

```
Gaussian integrated over the cell
```

Most users would choose Fast.

---

## A future-proof architecture

Personally I'd structure the modules like this:

```
healpyxel/

geometry/
    sidecar.py

psf/
    gaussian.py
    airy.py
    measured.py

integration/
    weighting.py
    aggregation.py

cache/
    sidecar.parquet
    weighted.parquet
```

Then users could write

```python
sidecar = generate_sidecar(...)

weighted = weight_sidecar(
    sidecar,
    psf=GaussianPSF(fwhm=0.4*u.deg),
    subgrid="auto",
)

cube = aggregate(weighted)
```

The key abstraction is that the PSF is a pluggable model, not something tied to a specific NSIDE or implementation.

## Overall assessment

I'd say your proposal is about **85–90% of the way there**. The main improvements I'd suggest are:

- Make the subpixel NSIDE adaptive rather than fixed (512 is just one choice).
- Generalize the parent-child mapping instead of hardcoding bit shifts.
- Evaluate the PSF in a local tangent plane or via angular separation, not directly in latitude/longitude.
- Treat the PSF evaluation as a reusable sparse stencil or lookup table to minimize repeated Gaussian calculations.

One additional idea that could make `healpyxel` unique in the planetary community is to support **adaptive subpixel refinement**. Rather than decomposing every FOV to a uniformly high NSIDE, recursively subdivide only the HEALPix cells that contribute significant PSF weight or intersect the steepest parts of the Gaussian. Cells far from the PSF center can remain coarse, while those near the peak are refined until a target accuracy is reached. This quadtree-like strategy exploits HEALPix's hierarchy, often reducing the number of evaluations by an order of magnitude while preserving accuracy. For narrow instrument PSFs or future high-NSIDE applications, this could become one of the package's most distinctive performance features.

---

# you asked

message time: 2026-07-11 19:11:12

Tangent-plane projection instead of lat/lon deltas : this is right for most of the data , but I have some flybys taken very far away , so fov are extremely big on the moon. Tangent approximation is not ok there , right ?
And what about tangential observation?

---

# chatgpt response

You're thinking about exactly the right edge cases. In fact, I think this is where you should separate **the PSF model** from **the geometric model**.

The tangent-plane approximation is excellent **only when the angular extent of the footprint is small**. For MERTIS in nominal mapping orbit, that's almost always true. But for distant flybys or limb observations, it can break down.

### Case 1: Nadir, small FOV (95% of mapping data)

```
      spacecraft
          |
          |
         \|/
      _________
     /         \
    |  Mercury  |
```

The footprint subtends a very small angle on the sphere, so locally the surface is almost flat.

A tangent-plane Gaussian is essentially exact.

---

### Case 2: High altitude flyby

Suppose you're 20,000 km from the Moon.

The IFOV is still the same angular size, but the projected footprint may be hundreds of kilometers across.

Now the footprint covers several degrees on the sphere.

A Euclidean Gaussian in (x,y) is no longer the same as an angular Gaussian.

---

### Case 3: Tangential (limb) observations

These are even more problematic.

```
          spacecraft
             *
            /
           /
----------/---------
        Moon
```

The footprint becomes

- highly elongated
- strongly skewed
- possibly even non-convex after projection (depending on the target model)

Here there isn't even a single "best" tangent plane.

---

## I think the PSF should live on the unit sphere

Instead of defining

$$
PSF(x,y)
$$

define

$$
PSF(\theta)
$$

where θ is the angular distance from the boresight.

Then for every candidate HEALPix cell center:

1. Convert the cell center to a unit vector **u**.
2. Convert the boresight to a unit vector **b**.
3. Compute

$$
\theta=\arccos(\mathbf u\cdot\mathbf b)
$$

4. Evaluate

$$
w=e^{-\theta^2/2\sigma^2}
$$

This is valid

- at the poles,
- across longitude wrap-around,
- for distant flybys,
- for any spherical body,
- independent of map projection.

No tangent plane required.

---

## But what about the projected footprint?

This is the subtle point.

The PSF tells you how much the detector responds to rays at different angles.

The projected footprint tells you **which rays actually hit the planet**.

These are different.

I'd think of the weighting as

```
Detector PSF
        ×
Visibility on target
        ×
HEALPix cell
```

where the visibility is simply your footprint polygon.

So the algorithm becomes

```
Candidate cells

↓

inside footprint?

↓

YES

↓

angular distance from boresight

↓

evaluate PSF

↓

normalize
```

Notice that nowhere do we approximate the surface as flat.

---

## Tangential observations

For limb observations I'd still use exactly the same PSF.

The only difference is the footprint geometry.

The PSF remains circular in angular space because that's how the optics work.

The projection onto the surface becomes elongated automatically because the intersection of the viewing cone with the sphere is elongated.

So you don't need an "elliptical PSF." You need a correct geometric intersection and then evaluate the same angular PSF.

---

## One caveat: do you know the boresight?

From what you've said previously, you have the projected footprint vertices but **not** the full ray bundle.

The question is: **do you have the boresight intercept on the target?**

If yes (e.g., the center latitude/longitude of the FOV), then the spherical approach above works well.

If you only have the footprint polygon and not the boresight direction, you can approximate the boresight by the spherical centroid of the footprint. This is very accurate for small footprints and usually still good for moderate ones, though it becomes less reliable for extremely large or highly asymmetric footprints.

## My recommendation for `healpyxel`

I wouldn't choose between tangent-plane and spherical methods. Instead, I'd make them two interchangeable geometry backends behind the same API:

```python
GaussianPSF(
    sigma=...,
    geometry="auto"   # "tangent", "spherical", or "auto"
)
```

where:

- **`tangent`** is the fast path for small footprints (the common case for orbital mapping),
- **`spherical`** computes angular separations using unit vectors and is valid for flybys, polar regions, and limb observations,
- **`auto`** selects the method based on the footprint's angular size (for example, switching to spherical once the footprint spans more than about 1–2°).

This gives you the speed of the tangent-plane method for millions of typical observations, while retaining a robust, physically meaningful solution for the unusual but scientifically important cases like distant flybys and tangential viewing geometries.

---

# you asked

message time: 2026-07-12 18:09:57

Back to the sidecar generation:
I work with polygon defined in (lon , lat) on planetary bodies , mostly big and spherical.

The big problem is polygon degeneracy near limb (due to observation geometry) , near Poles and antimeridian (due to projection).

I filter input geometry polygon with https://www.gadom.ski/antimeridian/latest/ , works well mostly, but thee are always edge case with global datasets. Healpy solves the problem very well, that's why I choose it.

Is there a better way to work with the input polygon? Maybe another coordinate system (3D ? Something on the sphere ?) that make those problems aways and makes easy to work with healpix too?

---

# chatgpt response

I think you've identified the fundamental issue: **the problem isn't HEALPix, it's representing spherical polygons in a planar GIS framework.**

The pathologies you're seeing all have the same root cause:

- **Antimeridian:** longitude has a discontinuity at ±180°.
- **Poles:** longitude becomes singular.
- **Limb observations:** the projection from the sphere to (lon, lat) becomes highly distorted.
- **Large polygons:** they are not planar polygons at all—they are **spherical polygons**.

HEALPix avoids these issues because it never works in longitude/latitude internally. It works on the **unit sphere**.

## I would seriously consider moving the core of healpyxel to 3D unit vectors

Represent every vertex as

$$
\mathbf{v}=(x,y,z)
$$

with

$$
x=\cos\phi\cos\lambda
$$
$$
y=\cos\phi\sin\lambda
$$
$$
z=\sin\phi
$$

where φ is latitude and λ is longitude.

Now:

- no antimeridian
- no poles
- no longitude wrap
- no singularities

The sphere is continuous.

---

## Even better: define edges as great circles

Your current polygons are probably interpreted as straight segments in lon/lat.

Physically, the boundary between two projected corner rays is a **great-circle arc** (or, more precisely, the intersection of a viewing plane with the sphere).

So instead of

```text
vertex ---- vertex
```

you have

```text
great-circle arc
```

This is exactly how astronomy libraries treat sky footprints.

---

## HEALPix is already in this world

Every HEALPix pixel center can be obtained as

```python
vec = hp.pix2vec(...)
```

instead of

```python
lon, lat = hp.pix2ang(...)
```

Now testing becomes

```text
point inside spherical polygon?
```

rather than

```text
point inside planar polygon?
```

---

## Candidate libraries

There are a few ecosystems that already work this way.

### S2 Geometry (Google)

Probably the gold standard.

Everything is

- unit vectors
- spherical edges
- spherical polygons
- hierarchical cells

Very robust.

The downside is that it uses the S2 tessellation instead of HEALPix, although the geometric algorithms are reusable.

---

### Astropy spherical geometry

There is (or has been) a `spherical_geometry` package developed for HST and JWST footprint calculations.

It works entirely on the sphere using unit vectors.

Its algorithms are very relevant to what you're doing.

---

### SPICE

NASA SPICE already computes intercept polygons on planetary bodies in Cartesian coordinates.

If you later integrate more deeply with SPICE, staying in 3D will feel much more natural.

---

## My dream architecture for healpyxel

I wouldn't have the geometry engine know anything about longitude/latitude.

Instead:

```text
Input
    lon,lat
        ↓

convert once

        ↓

unit vectors

        ↓

all geometry

        ↓

candidate HEALPix cells

        ↓

output IDs
```

Longitude/latitude becomes only an import/export format.

---

## Even the R-tree could change

Right now you probably index

```text
bounding boxes
```

in lon/lat.

Those inherit all the projection problems.

Instead you could index

```text
bounding cones
```

or

```text
3D bounding volumes
```

around the polygon.

This is much closer to how astronomical catalogues perform spatial searches.

---

## One thing I'd investigate

I would investigate whether the sidecar generation really needs polygon–polygon intersection at all.

Given that your polygons have only 4–10 vertices, another approach is:

1. Convert vertices to unit vectors.
2. Compute a spherical bounding cap (center vector + angular radius).
3. Use `healpy.query_disc()` (or equivalent) to get candidate HEALPix cells.
4. Perform a **spherical point-in-polygon** or **spherical polygon–cell** test only on those candidates.

This leverages HEALPix's native spherical indexing instead of building millions of planar polygons.

## I think the long-term direction is clear

If I were redesigning `healpyxel` today, I'd make it **sphere-native** rather than **GIS-native**:

- **Geometry representation:** unit vectors (ECEF-like coordinates on the unit sphere).
- **Edges:** great-circle arcs rather than straight lines in longitude/latitude.
- **Candidate search:** HEALPix spherical queries (`query_disc`, `query_polygon`) instead of planar R-trees wherever possible.
- **Intersection tests:** spherical polygon algorithms for the small set of candidate cells.
- **I/O:** accept and return lon/lat for user convenience, but keep those coordinates out of the core computational engine.

That architecture eliminates antimeridian and polar singularities by construction, aligns naturally with HEALPix, and positions `healpyxel` to handle not just Mercury and the Moon but any nearly spherical body with the same robust geometric framework. I think it would also be a genuinely novel contribution, since most planetary software still relies heavily on planar GIS operations once footprints have been projected.

---

# you asked

message time: 2026-07-12 18:13:04

This is nice , but this would work best or only on spherical bodies , right ?
AMS also on Mercury, with low spherical eccentricity, it is an approximation , earth too , or am I wrong ?

---

# chatgpt response

You're exactly right. This is probably the most important design decision if you want `healpyxel` to become a general planetary library.

The answer is: **yes, it is an approximation—but for Mercury and the Moon it's an extremely good one.**

### The hierarchy I'd use

Think of three geometric models:

| Model | Body | Geometry | Complexity |
|-------|------|----------|------------|
| Sphere | Moon, Mercury, most asteroids | Unit sphere | ★ |
| Oblate spheroid | Earth, Mars, Venus | Ellipsoid | ★★ |
| Shape model (DEM/SPC/DSK) | Bennu, Eros, Phobos, comet nuclei | Triangular mesh | ★★★★★ |

Your current science is almost entirely in the first row.

---

### Mercury

Mercury is very close to spherical.

Its mean radius is about **2440 km**, while the equatorial-polar radius difference is only on the order of **1 km**. That's a flattening of roughly

$$
f \approx \frac{1}{2440} \approx 4\times10^{-4}.
$$

That is tiny.

For almost every remote-sensing application, the spherical approximation introduces errors far smaller than:

- pointing uncertainty,
- PSF width,
- spacecraft navigation uncertainty,
- surface topography.

---

### The Moon

Even better.

The Moon's flattening is even smaller than Earth's and is negligible for footprint indexing.

I'd happily use a unit sphere.

---

### Earth

Earth is different.

Flattening is

$$
f\approx\frac{1}{298}.
$$

Now the difference between geodetic latitude and geocentric latitude becomes important.

That's why GIS software uses WGS84 instead of a sphere.

---

### Asteroids

Here the spherical model completely breaks down.

Take Bennu.

It is basically a spinning top.

The surface normal differs significantly from the radial direction.

A polygon defined by lat/lon is no longer the natural representation.

---

## Here's the key insight

I wouldn't make `healpyxel` "sphere-only."

I'd make it **geometry-backend agnostic**.

For example:

```python
BodyGeometry

Sphere(radius)

Ellipsoid(a, b, c)

ShapeModel(dsk)
```

Then all of your algorithms work with an interface like

```python
body.lonlat_to_xyz(...)

body.xyz_to_lonlat(...)

body.intersect_ray(...)

body.surface_normal(...)
```

For a sphere those are simple, closed-form equations.

For an ellipsoid they're slightly more involved.

For a DSK they rely on ray-triangle intersections.

The rest of `healpyxel` doesn't need to know which backend is being used.

---

## Why I still like 3D vectors

Even on an ellipsoid, I would **still store points internally as Cartesian vectors**.

The only difference is how you convert between `(lon, lat)` and `(x, y, z)`.

Once you're in Cartesian space:

- no antimeridian,
- no poles,
- robust dot and cross products,
- great numerical stability.

That remains true regardless of whether the underlying surface is spherical or ellipsoidal.

---

## My recommendation for `healpyxel`

Given your background with MERTIS, MASCS, and other planetary instruments, I'd optimize for the common case:

1. **Core engine:** sphere-native, using unit vectors and HEALPix. This will cover Mercury, the Moon, and many other nearly spherical bodies with excellent accuracy and maximal robustness.
2. **Experimental backend:** ellipsoid support, mainly for Earth and Mars applications. The internal representation can still be Cartesian vectors, but conversions and ray intersections use an ellipsoid model.
3. **Future extension:** a shape-model backend for SPICE DSK or similar triangular meshes. At that point, HEALPix becomes just the indexing scheme, while the geometry engine handles arbitrary body shapes.

This layered architecture gives you the performance and simplicity that most planetary missions need today, while leaving a clear path toward more complex bodies without forcing that complexity into the core algorithms from the start. I think that's the balance most likely to make `healpyxel` broadly useful while keeping the implementation maintainable.

---

# you asked

message time: 2026-07-12 18:15:29

I like this approach.
We plan to switch spice calculation for MERTIS from ellipsoid to SPICE DSK at some point , that will make a difference for high resolution due to topography.

---

# chatgpt response

I think that's actually a very compelling long-term vision for `healpyxel`, and it aligns well with the direction of modern planetary missions.

The transition

> **Ellipsoid → DSK**

is not just an implementation detail—it changes what your "footprint" means geometrically.

## Level 1: Sphere

```text
Instrument rays
        ↓
Unit sphere
        ↓
Polygon
        ↓
HEALPix
```

Fast, robust, analytically simple.

This is ideal for:

- Mercury (MERTIS nominal)
- Moon
- global products
- low/medium resolution

---

## Level 2: Ellipsoid

The intercept algorithm changes, but once you have the polygon, `healpyxel` can operate almost identically.

The differences are small and mostly affect geolocation accuracy.

---

## Level 3: DSK (Shape model)

This is where things become much more interesting.

Now the footprint boundary is no longer determined by a smooth body.

Instead, rays intersect a triangulated surface.

That introduces effects such as:

- terrain occlusion,
- self-shadowing,
- multiple disconnected footprint regions,
- non-convex footprints,
- holes in footprints.

A single detector pixel may even produce several disconnected surface patches in extreme terrain.

---

## This suggests a clean separation

I'd almost define two packages inside `healpyxel`:

```text
Geometry Engine
----------------
Sphere
Ellipsoid
DSK
```

↓

```text
Index Engine
----------------
HEALPix
HTM (future?)
S2 (future?)
```

↓

```text
Science Layer
----------------
Sidecars
PSF
Aggregation
Statistics
```

Notice that the indexing layer doesn't care where the polygon came from.

---

## I would avoid making healpyxel SPICE-dependent

Instead I'd define a geometry interface.

Something like

```python
class SurfaceModel:

    def footprint(...):
        ...

    def ray_intersections(...):
        ...
```

Then

```python
SphereSurface
EllipsoidSurface
SpiceDSKSurface
```

all implement the same interface.

For MERTIS, the DSK implementation would call SPICE.

For a lunar dataset, another project could provide a different implementation.

---

## The sidecar becomes surprisingly powerful

Imagine a MERTIS observation.

Today

```text
TM

↓

CAL

↓

Footprint

↓

Sidecar
```

Tomorrow

```text
TM

↓

CAL

↓

SPICE DSK

↓

Accurate footprint

↓

Weighted sidecar
```

Nothing downstream changes.

That's excellent software architecture.

---

## One thing I think would make `healpyxel` unique

Most planetary software starts with **rays**.

I think `healpyxel` should start with **surface models**.

For example:

```python
body = Sphere()

body = Ellipsoid()

body = SpiceDSK()
```

Then all of your algorithms ask the body

```python
body.contains(point)

body.intersects(ray)

body.normal(point)

body.area(polygon)
```

instead of asking

```python
lon

lat
```

This is much more object-oriented and extensible.

## I can also see a publishable software paper emerging

Given your experience with MERTIS, PDS4, and planetary data processing, I don't think `healpyxel` should be presented simply as "HEALPix utilities."

I think its real contribution is a **hierarchical planetary observation operator**.

Conceptually, it would look like this:

```text
               Instrument Geometry
                       │
        ┌──────────────┼──────────────┐
        │              │              │
     Sphere       Ellipsoid        DSK
        │              │              │
        └──────────────┼──────────────┘
                       │
             Surface Footprint
                       │
                HEALPix Indexing
                       │
          Sparse Observation Matrix
         (obs_id, cell_id, weight)
                       │
      Aggregation • Mapping • Inversion
```

The key insight is that **HEALPix is just the discretization**, while the geometry backend can evolve from a simple sphere to a full DSK without changing the downstream data model. That abstraction is scientifically elegant and, as far as I'm aware, not something that existing planetary libraries expose as a reusable, mission-independent framework. I think that could make `healpyxel` valuable not only for MERTIS, but also for instruments on EnVision, VERITAS, JUICE, Hera, and future lunar missions.

---

# you asked

message time: 2026-07-12 18:35:52

There the current dev branch sidecar generation python

---

# chatgpt response

I had a look through the current `sidecar.py`, and I actually think you're at an interesting transition point.

The R-tree cache you've introduced (`_HEALPIX_GRID_CACHE` + `shapely.STRtree`) is a good improvement over the previous brute-force approach. 

However, I think you've started mixing **three different responsibilities** inside one module:

1. **Geometry engine**
   - antimeridian fixing
   - polygon intersection
   - STRtree
   - HEALPix polygon generation

2. **Observation model**
   - PSF
   - weighting
   - normalization

3. **I/O**
   - parquet
   - metadata
   - Dask
   - CLI

For example, I noticed the PSF code has already begun to appear directly inside `process_partition()`, where weights are computed from centroid-to-centroid distances. 

Personally, I would stop extending that function now.

---

## I think there is a much bigger redesign available

Everything we've discussed over the last few hours points toward something like

```text
                 Geometry backend
                 ----------------

           Sphere
           Ellipsoid
           DSK

                    │

                    ▼

          Geometry Adapter

    lon/lat polygons
            │
            ▼
      unit vectors (xyz)

                    │

                    ▼

          Candidate Search

    query_disc
    R-tree
    STRtree
    S2
    ...

                    │

                    ▼

      Sidecar Generator

     source_id
     healpix_id

                    │

                    ▼

        Weighting Engine

      Gaussian
      Airy
      Measured PSF

                    │

                    ▼

       Weighted Sidecar
```

Notice that **geometry** and **PSF** never talk to each other.

---

## I actually think your R-tree should eventually disappear

This may sound surprising.

I like the R-tree **today**, but I don't think it's your end game.

Right now you're doing roughly

```text
Polygon

↓

Bounding box

↓

R-tree

↓

Candidate cells

↓

Polygon intersection
```

But HEALPix already has an excellent spatial hierarchy.

I think the future algorithm is closer to

```text
Polygon

↓

Bounding cap

↓

healpy.query_disc()

↓

50 candidate cells

↓

Exact spherical test
```

No polygons for every HEALPix cell.

No STRtree.

No huge GeoDataFrame.

---

## The most important redesign

I'd replace

```python
Polygon
```

everywhere inside the computational engine with

```python
SphericalPolygon
```

where internally

```python
vertices_xyz
```

are stored.

Longitude/latitude becomes an import/export format only.

That one decision would eliminate:

- antimeridian fixes
- pole handling
- longitude wrapping
- centroid weirdness

---

## What I would ask Claude next

I actually wouldn't ask it to optimize the current implementation.

I'd ask it something much more ambitious:

> Design a new geometry abstraction layer for healpyxel where the computational engine never operates directly on longitude/latitude or Shapely polygons. Geometry should instead be represented internally as spherical polygons defined by unit vectors, while preserving compatibility with the existing GeoParquet interface. Compare this architecture against the current STRtree implementation and discuss how it would enable future support for ellipsoids, SPICE DSK shape models, PSF integration, and hierarchical HEALPix traversal.

I think that is where the project is heading.

---

One final observation. From reading your current code, I think `healpyxel` is no longer just "a library that maps polygons to HEALPix cells." It is evolving into a **geometry engine for planetary remote sensing**.

If you embrace that direction, the central abstraction of the library is no longer a `GeoDataFrame`; it's an **Observation**:

```python
Observation
    geometry
    body
    instrument
    psf
    metadata
```

The sidecar is then just one product that the observation can generate.

Given your work on MERTIS, Hayabusa2 NIRS3, EnVision, and future DSK-based processing, I actually think this is a stronger long-term vision than continuing to evolve a GIS-centric implementation. It would make `healpyxel` one of the few libraries designed around **planetary observation geometry** rather than around GIS primitives.


