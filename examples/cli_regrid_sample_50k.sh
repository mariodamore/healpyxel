#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="$ROOT_DIR/test_data/samples/sample_50k.parquet"
OUT_DIR="$ROOT_DIR/test_data/derived/cli_quickstart"
NSIDES=(32 64)
MODE=fuzzy
LON_CONVENTION=0_360

mkdir -p "$OUT_DIR"

 # 1) Create HEALPix sidecar(s)
healpix_sidecar \
  --input "$INPUT" \
  --nside "${NSIDES[@]}" \
  --mode "$MODE" \
  --lon-convention "$LON_CONVENTION" \
  --output_dir "$OUT_DIR"

# 2) Aggregate sparse regridded map (all sidecars)
healpix_aggregate \
  --input "$INPUT" \
  --sidecar-dir "$OUT_DIR" \
  --sidecar-index all \
  --aggregate \
  --columns r1050 \
  --aggs mean median std mad robust_std \
  --min-count 1
# 3) Aggregate (densified) regridded map (all sidecars)
healpix_aggregate \
  --input "$INPUT" \
  --sidecar-dir "$OUT_DIR" \
  --sidecar-index all \
  --aggregate \
  --columns r1050 \
  --aggs mean median std mad robust_std \
  --min-count 1 \
  --densify
