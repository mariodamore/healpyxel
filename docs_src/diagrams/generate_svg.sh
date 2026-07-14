#!/usr/bin/env bash
# Generate SVG diagrams from D2 source files.
# Run from repo root (or adjust DIAGRAM_DIR below).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/svg"
D2="${D2:-d2}"

if ! command -v "$D2" &>/dev/null; then
    echo "ERROR: d2 CLI not found. Install from https://d2lang.com/ or set D2 env var." >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

for src in "$SCRIPT_DIR"/*.d2; do
    name="$(basename "$src" .d2)"
    dest="$OUT_DIR/$name.svg"
    echo "  $src -> $dest"
    "$D2" "$src" "$dest"
done

echo "Done. Generated $(ls "$OUT_DIR"/*.svg 2>/dev/null | wc -l) SVG(s) in $OUT_DIR"
