# ADR-011: Exclude WIP notebooks from published docs

- **Status:** Active
- **Date:** 2026-07-13
- **Author:** session 2026-07-13

## Context
Several tutorial notebooks still contain exploratory or incomplete cells that fail when Quarto executes them during documentation builds. This blocks the published site even when the API reference and stable tutorials are otherwise healthy.

## Decision
Only render the stable tutorial notebooks in the published Quarto site. Keep unfinished accumulation notebooks out of the automatic render set and sidebar until they are self-contained and deterministic under Quarto.

## Alternatives Considered
- Render every notebook unconditionally: rejected because a single WIP notebook can break the entire docs build.
- Keep the WIP notebooks in the sidebar but mark them hidden: rejected because that still encourages direct navigation to unstable pages.

## Consequences
- Positive: the docs site builds reliably and the published navigation only exposes stable content.
- Negative: WIP tutorial pages remain available only as source files until they are stabilized.
