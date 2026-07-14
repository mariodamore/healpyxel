# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.19.4
#   kernelspec:
#     display_name: mertis
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Streaming Accumulation - WIP!
#
# Accumulate streaming data in states stored on disk, no need to reprocess all the data.
#
# <span style="color: red;">*STILL WORK IN PROGRESS*</span>
#
#
# This notebook validates the **streaming accumulation pipeline** against a **full batch aggregation baseline** using mission batches as a simulated ingestion stream.
#
# ## Objectives
#
# 1. **Load simulated stream batches**
#    - Discover batch files in order.
#    - Load and validate geometries.
#    - Plot batch GeoDataFrames for spatial sanity checks.
#
# 2. **Create sidecars per batch**
#    - Assign observations/geometries to HEALPix cells.
#    - Keep HEALPix configuration fixed (`nside`, `nest`, longitude convention).
#
# 3. **Create full batch baseline (end-of-mission reference)**
#    - Combine all batches.
#    - Run one-shot aggregation over the full dataset.
#    - Plot aggregated geospatial result for inspection.
#    - Preserve this as **raw numeric ground truth** (before optional geometry enrichment).
#
# 4. **Run streaming accumulation**
#    - Process batches incrementally.
#    - Update accumulator state in memory.
#    - Persist versioned state snapshots to disk.
#
# 5. **Validate state integrity**
#    - Reload final state from disk.
#    - Compare disk-extracted statistics vs in-memory state (serialization/regression check).
#
# 6. **Validate streaming accuracy vs batch baseline**
#    - Compare final streaming statistics to full batch aggregate.
#    - Report count parity, per-cell/value differences, and summary error metrics.
#
# ## Required parity/invariants (critical)
#
# - Same key semantics across paths (use stable IDs; avoid cross-batch ID collisions).
# - Same value filtering rules in both paths (NaN/inf/sentinel handling).
# - Same HEALPix semantics in all steps (`nside`, ordering, lon convention).
# - Compare numeric outputs against **raw aggregate baseline**, not geometry-modified views.
# - Focus on variables naming readability and coherent naming among the steps.
#
# ## Success criteria
#
# - Cell set parity between streaming and batch.
# - Observation-count parity (or explicitly explained deltas).
# - Mean differences near zero within tolerance; any residuals are justified (not pipeline mismatch).

# %% [markdown]
#
# t
