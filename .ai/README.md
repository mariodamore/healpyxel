# .ai/ — Project Memory

This folder is the single source of truth for project context.
It is read by AI coding agents (Claude Code, GitHub Copilot) and by human contributors.

## Files at a glance

| File | Read when |
|------|-----------|
| `00_CONSTRAINTS.md` | Every coding session — hard rules |
| `00_PHILOSOPHY.md` | Architecture work, onboarding, new patterns |
| `02_ROADMAP.md` | Planning, scope questions |
| `03_CURRENT_STATUS.md` | Start of every session — current state |
| `decisions/index.md` | Conflict checks, architecture decisions |
| `decisions/ADR-NNN-*.md` | When the index flags a relevant decision |
| `sessions/YYYY-MM-DD.md` | Debugging, "why did we do this" questions |
| `IMPLEMENTATION_PLAN.md` | Tactical status — concrete implementation progress |
| `PROJECT_PLAN.md` | Legacy — content merged into new structure |
| `archive/` | Historical status — rarely needed |

## New contributor?

Start here: `00_PHILOSOPHY.md` → `02_ROADMAP.md` → `03_CURRENT_STATUS.md`

## New agent session?

Start here: `03_CURRENT_STATUS.md` → `00_CONSTRAINTS.md` → `02_ROADMAP.md` → `decisions/index.md`

## Project Overview

**healpyxel** is a HEALPix-based spatial aggregation tool for planetary science data (specifically MESSENGER/MASCS). It implements split-apply-combine workflows for both batch and streaming processing of geospatial observations.

The project uses **nbdev** (literate programming) where Jupyter notebooks in `nbs/` are the source of truth, and Python modules in `healpyxel/` are auto-generated. Never edit files in `healpyxel/` directly—always modify notebooks.

## The 4-Phase Pipeline

1. **Sidecar** → *Map geometry to space* (fuzzy, multi-NSIDE)
2. **Aggregate** → *Batch statistics* (median, MAD, robust_std)
3. **Accumulator** → *Streaming state* (Welford + TDigest)
4. **Finalize** → *Upsample & export* (densification, FITS/PNG)

## Critical Directives

- **Science-first vectorization**: No Python loops over datasets. If it doesn't run in <100ms on 1M rows, it's technical debt.
- **Robustness over convenience**: We implement `mad()` and `robust_std()` ourselves — no `scipy.stats`. Why? Consistency across 10+ notebooks. One bug, one fix.
- **HEALPix as the single coordinate system**: `healpy` (NEST ordering), `antimeridian` for edge cases, and *only* `dask-geopandas` for lazy loading. No alternatives — context collapse is the enemy.

## For AI Agents

- **Before writing code**: Check `.ai/IMPLEMENTATION_PLAN.md` — don't re-implement ✅ items.
- **Before refactoring**: Check `.ai/PROJECT_PLAN.md` — is this in Phase 1 or Phase 3?
- **When in doubt**: Assume the constraint exists for a reason. Ask *why*, don't ask *can I*.

> This isn't a "guide for AI agents." It's a *handshake* — between past decisions and future code.
