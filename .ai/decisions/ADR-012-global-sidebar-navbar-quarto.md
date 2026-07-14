# ADR-012: Global docked sidebar plus navbar for Quarto docs

- **Status:** Active
- **Date:** 2026-07-13
- **Author:** session 2026-07-13

## Context
The Quarto docs need both a persistent left navigation rail and a top header with theme controls, search, and site-wide entry points. A hybrid multi-sidebar setup did not render the left navigation as expected on the landing page.

## Decision
Use one global docked sidebar with grouped sections for Home, Tutorials, and API Reference, and pair it with a top navbar for site title, search, and primary landing links. Enable light/dark themes so Quarto exposes the theme toggle automatically.

## Alternatives Considered
- Keep the hybrid sidebar list: rejected because it did not produce the desired persistent left navigation on the rendered site.
- Use only top navigation: rejected because the docs need dense hierarchical navigation for the tutorial and API pages.

## Consequences
- Positive: the docs site now matches the expected Quarto website layout with left navigation, a header, and theme switching.
- Negative: the sidebar configuration is longer, so future section changes must keep the sidebar and navbar in sync.
