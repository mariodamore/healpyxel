#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="$ROOT_DIR/test_data/samples/sample_50k.parquet"
OUT_DIR="$ROOT_DIR/test_data/derived/cli_quickstart"
NSIDES=(32 64)
MODE=fuzzy
LON_CONVENTION=0_360

mkdir -p "$OUT_DIR"
echo "Input file: $INPUT"
echo "Output directory: $OUT_DIR"
 # 1) Create HEALPix sidecar(s)
healpyxel_sidecar \
  --input "$INPUT" \
  --nside "${NSIDES[@]}" \
  --mode "$MODE" \
  --lon-convention "$LON_CONVENTION" \
  --output_dir "$OUT_DIR"
echo "✓ Sidecar(s) created"
# output:
#sample_50k.cell-healpix_assignment-fuzzy_nside-32_order-nested.parquet
#sample_50k.cell-healpix_assignment-fuzzy_nside-32_order-nested.meta.json
#sample_50k.cell-healpix_assignment-fuzzy_nside-64_order-nested.parquet
#sample_50k.cell-healpix_assignment-fuzzy_nside-64_order-nested.meta.json

# 2) Aggregate sparse regridded map (all sidecars)
healpyxel_aggregate \
  --input "$INPUT" \
  --sidecar-dir "$OUT_DIR" \
  --sidecar-index all \
  --aggregate \
  --columns r1050 \
  --aggs mean median std mad robust_std

echo "✓ Sparse regridded map(s) created"
# output:
# sample_50k-aggregated.cell-healpix_assignment-fuzzy_nside-32_order-nested.parquet
# sample_50k-aggregated.cell-healpix_assignment-fuzzy_nside-32_order-nested.meta.json
# sample_50k-aggregated.cell-healpix_assignment-fuzzy_nside-64_order-nested.parquet
# sample_50k-aggregated.cell-healpix_assignment-fuzzy_nside-64_order-nested.meta.json

# 3) Aggregate (densified) regridded map (all sidecars)
healpyxel_aggregate \
  --input "$INPUT" \
  --sidecar-dir "$OUT_DIR" \
  --sidecar-index all \
  --aggregate \
  --columns r1050 \
  --aggs mean median std mad robust_std \
  --densify
echo "✓ Densified regridded map(s) created"
# 4) Convert aggregated regridded maps to GeoParquet for visualization
for f in "$OUT_DIR"/*-aggregated*parquet; do
  echo "$f"
  healpyxel_to_geoparquet -a "$f" -d "$OUT_DIR" -l -180_180 -f
done
echo "✓ GeoParquet file(s) created"
# output:
# sample_50k-aggregated.cell-healpix_assignment-fuzzy_nside-32_order-nested.geo.parquet
# sample_50k-aggregated.cell-healpix_assignment-fuzzy_nside-64_order-nested.geo.parquet
