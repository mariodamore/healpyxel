# Design Philosophy

Guidance, not rules. These evolve as the project matures.

## Core Principles

- **Science-first vectorization** — No Python loops over datasets. Performance targets (<100ms on 1M rows) are not goals; they are minimum viability thresholds.
- **Robustness over convenience** — We implement `mad()` and `robust_std()` ourselves. Consistency across 10+ notebooks matters more than using "standard" libraries. One bug, one fix.
- **HEALPix as the single coordinate system** — `healpy` (NEST ordering), `antimeridian` for edge cases. Context collapse is the enemy; too many tools create too many failure modes.
- **Predictability over speed** — We don't optimize for speed first. We optimize for *predictability* first — speed follows.

## Architectural Style

- **Notebooks as source of truth** — nbdev is not "literate programming" as a buzzword, but as a *debugging discipline*. Every notebook cell is a testable hypothesis. Every `#| export` is a promise: *this will run in production*.
- **Single responsibility per phase** — The 4-phase pipeline isn't arbitrary. Each phase has a *single* job — and if it tries to do more, we refactor.
- **Trust internal guarantees** — Don't add error handling, fallbacks, or validation for scenarios that can't happen. Validate only at system boundaries (user input, external APIs).

## What We Optimise For

- **Scientific integrity** — When remote data arrives at 100x scale, our code won't break — it will *fail gracefully*.
- **Precision consistency** — A `float64`-only policy won't drift in precision across batches.
- **Disciplined documentation** — Every architectural choice is recorded. Every dead end is logged. Session files are never deleted.

## What We Accept as Trade-offs

- **Duplication over wrong abstractions** — Some duplication is acceptable to avoid the wrong abstraction.
- **Custom implementations** — We accept the cost of implementing `mad()` ourselves to maintain consistency with `scipy.stats`.
- **No speculative optimization** — We won't suggest "maybe use Numba" unless profiling shows a hard bottleneck.

## The 4-Phase Pipeline

1. **Sidecar** → *Map geometry to space* (fuzzy, multi-NSIDE)
2. **Aggregate** → *Batch statistics* (median, MAD, robust_std)
3. **Accumulator** → *Streaming state* (Welford + TDigest)
4. **Finalize** → *Upsample & export* (densification, FITS/PNG)

Each phase has a *single* job — and if it tries to do more, we refactor.

## For AI Agents

- **Before writing code**: Check `.ai/IMPLEMENTATION_PLAN.md` — don't re-implement ✅ items.
- **Before refactoring**: Check `.ai/PROJECT_PLAN.md` — is this in Phase 1 or Phase 3?
- **When in doubt**: Assume the constraint exists for a reason. Ask *why*, don't ask *can I*.

> This isn't a "guide for AI agents." It's a *handshake* — between past decisions and future code.

_Last updated: 2026-05-22_
