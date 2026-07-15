
# Design Review: Refactor the Streaming Accumulation Framework

## Context

The current accumulation pipeline in `healpyxel` already implements a streaming architecture based on mergeable accumulators.

The current implementation includes:

* Welford streaming statistics
* TDigest support for approximate quantiles/median
* Persistent accumulators
* Incremental processing
* Merge operations for distributed execution

Before implementing additional features, I want to review the architecture against the long-term vision below.

The goal is **not** to rewrite working code, but to identify which parts already satisfy this design and which parts should be refactored.

---

# Long-Term Design Goal

The accumulation framework should become a collection of **independent streaming reducers**.

Each reducer is responsible for exactly one statistical quantity.

Examples include:

* CountReducer
* MeanReducer (Welford)
* VarianceReducer (Welford)
* MinReducer
* MaxReducer
* QuantileReducer (TDigest)
* CoverageReducer

Future reducers might include:

* HistogramReducer
* RobustStatisticsReducer
* PCAReducer
* User-defined reducers

The accumulation engine should not know how each statistic is implemented.

Instead it simply forwards observations to each reducer.

---

# Reducer Interface

Every reducer should expose the same interface.

```python
update(value)

update(value, weight)

merge(other)

serialize()

deserialize()

finalize()
```

The weighted update does not need to be implemented immediately, but the interface should already anticipate it.

---

# Persistent Mission Accumulators

The accumulator should be considered a persistent mission product.

Typical workflow:

Day 1

↓

process observations

↓

save accumulator

Day 2

↓

load accumulator

↓

stream only today's observations

↓

save accumulator

This avoids replaying the entire mission history every day.

The accumulator therefore becomes conceptually similar to the sidecar:

* sidecar = persistent geometry cache
* accumulator = persistent statistical state

---

# TDigest Philosophy

TDigest should not be considered "the median implementation."

Instead it is the implementation of a generic QuantileReducer.

The reducer should expose:

* median
* arbitrary quantiles
* percentile ranges

without exposing TDigest-specific details to downstream code.

This allows future implementations to replace TDigest without changing the public API.

---

# Weighted Observations

Future versions of healpyxel will support PSF-weighted sidecars.

A future observation may therefore become:

(value, weight)

rather than simply

(value)

The accumulation framework should therefore be designed today so that weighted reducers can be added without redesigning the API.

Examples:

* WeightedMeanReducer
* WeightedVarianceReducer
* WeightedQuantileReducer (if supported)

Weighted implementations are **not required now**, but the architecture should make them straightforward.

---

# Streaming Philosophy

Streaming reducers should satisfy the following properties:

* incremental
* mergeable
* serializable
* bounded memory
* independent of observation order

The accumulation engine should simply coordinate reducers.

Reducers themselves contain all statistical logic.

---

# Review Requested

Please review the existing implementation and classify each component into one of the following categories:

* Already implemented correctly.
* Minor refactoring recommended.
* Missing component.
* Architectural improvement recommended.

Do **not** rewrite working code.

Instead identify the minimal set of changes required to reach the target architecture.

---

# Success Criteria

The final accumulation framework should satisfy:

* streaming operation over arbitrarily long missions
* persistent accumulators that can be updated daily
* mergeable accumulators for distributed processing
* clean separation between streaming engine and statistical reducers
* readiness for future weighted observations (PSF)
* extensibility for new statistical reducers without modifying the accumulation engine

# extra notes

One additional suggestion that I think is worth incorporating into the design discussion is to distinguish core reducers from optional reducers. Core reducers—such as count, mean, variance, min, max, and quantiles—are fundamental to most planetary mosaicking workflows and should be maintained as part of the core library. More specialized functionality, such as PCA, histograms, or mission-specific statistics, should be implemented as optional reducer plugins. This keeps the accumulation engine lightweight while allowing healpyxel to grow into a flexible framework where new scientific products can be added by implementing the reducer interface rather than modifying the engine itself.
