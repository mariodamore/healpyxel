#!/bin/bash
#
# create_test_data.sh
#
# Generate test datasets from MASCS observations to simulate streaming mission data.
# Uses DuckDB for efficient slicing of large parquet files.
#
# Usage:
#   ./create_test_data.sh [--clean]
#
# Output:
#   test_data/
#   ├── batches/                    # Sequential "daily" batches
#   │   ├── batch_001.parquet       # Lon 175-176° (~4k obs, ~1.6MB)
#   │   ├── batch_002.parquet       # Lon 176-177°
#   │   └── ...
#   ├── samples/                    # Size-based samples
#   │   ├── sample_5k.parquet       # Small (5k obs)
#   │   ├── sample_50k.parquet      # Medium (50k obs)
#   │   └── sample_150k.parquet     # Large (150k obs)
#   └── regions/                    # Geographic regions
#       ├── north_pole.parquet
#       ├── south_pole.parquet
#       └── equator.parquet

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
# SOURCE_FILE in not included in the repo due to size constraints, 15GB
SOURCE_FILE="../mascs_data_MeSS.parquet"
TEST_DATA_DIR="test_data"
DUCKDB_CMD="duckdb"

# Column selection (exclude large/unnecessary columns)
EXCLUDE_COLS="waves, photom_iof, geometry_bbox"

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

print_info() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_step() {
    echo -e "${BLUE}▶${NC} $1"
}

check_requirements() {
    print_step "Checking requirements..."
    
    # Check if source file exists
    if [ ! -f "$SOURCE_FILE" ]; then
        print_error "Source file not found: $SOURCE_FILE"
        echo "  Please ensure mascs_data_MeSS.parquet is in the current directory"
        exit 1
    fi
    print_info "Source file found: $SOURCE_FILE ($(du -h "$SOURCE_FILE" | cut -f1))"
    
    # Check if DuckDB is available
    if ! command -v duckdb &> /dev/null; then
        print_error "DuckDB not found"
        echo ""
        echo "  Install DuckDB:"
        echo "    • Ubuntu/Debian: wget https://github.com/duckdb/duckdb/releases/latest/download/duckdb_cli-linux-amd64.zip"
        echo "    • Or via pip: pip install duckdb"
        echo "    • Or download from: https://duckdb.org/docs/installation/"
        exit 1
    fi
    print_info "DuckDB found: $(duckdb --version)"
}

create_directory_structure() {
    print_step "Creating directory structure..."
    
    mkdir -p "$TEST_DATA_DIR"/{batches,samples,regions,validation}
    
    print_info "Created test_data/ directory structure"
}

execute_duckdb_query() {
    local query="$1"
    local output_file="$2"
    local description="$3"
    
    echo -n "  Creating $(basename "$output_file")... "
    
    # Execute query with spatial extension loaded inline
    # DuckDB will execute statements separated by semicolons in order
    local full_query="INSTALL spatial; LOAD spatial; $query"
    
    echo "$full_query" | $DUCKDB_CMD 2>&1 | grep -v "Warning" || true
    
    # Check if file was created successfully
    if [ -f "$output_file" ]; then
        local size=$(du -h "$output_file" | cut -f1)
        local count=$(echo "SELECT COUNT(*) FROM '$output_file'" | $DUCKDB_CMD 2>/dev/null | tail -1 || echo "?")
        echo -e "${GREEN}✓${NC} ${size} (${count} obs)"
    else
        echo -e "${RED}✗ Failed${NC}"
        return 1
    fi
}

# ============================================================================
# Data Generation Functions
# ============================================================================

create_sequential_batches() {
    print_header "1. Creating Sequential Batches (Simulating Daily Observations)"
    
    echo "  Slicing by longitude to simulate temporal progression..."
    echo ""
    
    # Create batches around longitude 180° (interesting region with north/south data)
    # Each batch: 1° longitude slice ≈ 4k observations ≈ 1.6 MB
    
    local start_lon=175
    local end_lon=185
    local batch_num=1
    
    for lon in $(seq $start_lon $((end_lon-1))); do
        local next_lon=$((lon + 1))
        local output_file="$TEST_DATA_DIR/batches/batch_$(printf "%03d" $batch_num).parquet"
        
        local query="COPY (
            SELECT * EXCLUDE($EXCLUDE_COLS)
            FROM '$SOURCE_FILE'
            WHERE lon_center BETWEEN $lon AND $next_lon
        ) TO '$output_file' (FORMAT parquet, COMPRESSION snappy);"
        
        execute_duckdb_query "$query" "$output_file" "Lon ${lon}-${next_lon}°"
        
        batch_num=$((batch_num + 1))
    done
    
    echo ""
    print_info "Created $((batch_num - 1)) sequential batches"
    echo "  Use these to simulate streaming data ingestion"
}

create_size_samples() {
    print_header "2. Creating Size-Based Samples"
    
    echo "  Random samples for performance testing..."
    echo ""
    
    # Small sample (5k obs) - for quick testing
    execute_duckdb_query \
        "COPY (
            SELECT * EXCLUDE($EXCLUDE_COLS)
            FROM '$SOURCE_FILE'
            USING SAMPLE 5000 ROWS
        ) TO '$TEST_DATA_DIR/samples/sample_5k.parquet' (FORMAT parquet, COMPRESSION snappy);" \
        "$TEST_DATA_DIR/samples/sample_5k.parquet" \
        "Small sample"
    
    # Medium sample (50k obs) - for intermediate testing
    execute_duckdb_query \
        "COPY (
            SELECT * EXCLUDE($EXCLUDE_COLS)
            FROM '$SOURCE_FILE'
            USING SAMPLE 50000 ROWS
        ) TO '$TEST_DATA_DIR/samples/sample_50k.parquet' (FORMAT parquet, COMPRESSION snappy);" \
        "$TEST_DATA_DIR/samples/sample_50k.parquet" \
        "Medium sample"
    
    # Large sample (25k obs) - for stress testing
    execute_duckdb_query \
        "COPY (
            SELECT * EXCLUDE($EXCLUDE_COLS)
            FROM '$SOURCE_FILE'
            USING SAMPLE 25000 ROWS
        ) TO '$TEST_DATA_DIR/samples/sample_25k.parquet' (FORMAT parquet, COMPRESSION snappy);" \
        "$TEST_DATA_DIR/samples/sample_25k.parquet" \
        "Large sample"
    
    echo ""
    print_info "Created 3 size-based samples"
}

# Removed: create_geographic_regions()
# Geographic regions not useful for MASCS data (limited spatial coverage)

create_validation_datasets() {
    print_header "3. Creating Validation Datasets"
    
    echo "  Overlapping slices for testing accumulator vs batch consistency..."
    echo ""
    
    # Combined first 3 batches (for validation)
    execute_duckdb_query \
        "COPY (
            SELECT * EXCLUDE($EXCLUDE_COLS)
            FROM '$SOURCE_FILE'
            WHERE lon_center BETWEEN 175 AND 178
        ) TO '$TEST_DATA_DIR/validation/combined_batch_001_003.parquet' (FORMAT parquet, COMPRESSION snappy);" \
        "$TEST_DATA_DIR/validation/combined_batch_001_003.parquet" \
        "Combined batches 1-3"
    
    # High-quality subset (for testing filters)
    # Filter by quality flags: b != '3' and c != '3', and geometric constraints
    execute_duckdb_query \
        "COPY (
            SELECT * EXCLUDE($EXCLUDE_COLS)
            FROM '$SOURCE_FILE'
            WHERE lon_center BETWEEN 175 AND 185
              AND b != '3'
              AND c != '3'
              AND ang_incidence < 80
              AND ang_emission < 60
        ) TO '$TEST_DATA_DIR/validation/high_quality_subset.parquet' (FORMAT parquet, COMPRESSION snappy);" \
        "$TEST_DATA_DIR/validation/high_quality_subset.parquet" \
        "High-quality observations"
    
    echo ""
    print_info "Created validation datasets"
}

create_metadata() {
    print_header "4. Creating Metadata File"
    
    local metadata_file="$TEST_DATA_DIR/README.md"
    
    cat > "$metadata_file" << 'EOF'
# MASCS Test Data

Test datasets derived from `mascs_data_MeSS.parquet` for healpyxel package development and testing.

## Dataset Structure

```
test_data/
├── batches/          Sequential slices simulating streaming data
├── samples/          Random samples for performance testing
└── validation/       Datasets for testing correctness
```

## Datasets

### Sequential Batches (`batches/`)

Simulate daily mission data acquisition. Each batch represents observations from a 1° longitude slice around 180° meridian.

- **batch_001.parquet**: Lon 175-176° (~4k obs, ~1.6 MB)
- **batch_002.parquet**: Lon 176-177° (~4k obs, ~1.6 MB)
- **batch_003.parquet**: Lon 177-178° (~4k obs, ~1.6 MB)
- ... (up to batch_010)

**Use case**: Test streaming accumulator with incremental updates
```bash
healpyxel-accumulate --input batches/batch_001.parquet --state-output state_v001.parquet
healpyxel-accumulate --input batches/batch_002.parquet --state-input state_v001.parquet --state-output state_v002.parquet
```

### Size Samples (`samples/`)

Random samples for quick testing and benchmarking.

- **sample_5k.parquet**: 5,000 observations (~2 MB) - Quick tests
- **sample_50k.parquet**: 50,000 observations (~20 MB) - Medium-scale tests
- **sample_25k.parquet**: 25,000 observations (~10 MB) - Large-scale tests

**Use case**: Performance benchmarking and unit tests

### Validation Datasets (`validation/`)

Datasets for verifying correctness of streaming vs batch processing.

- **combined_batch_001_003.parquet**: Union of first 3 batches (lon 175-178°)
- **high_quality_subset.parquet**: Filtered observations (b != '3', c != '3', ang_incidence < 80°, ang_emission < 60°)

**Use case**: 
- Validate that streaming accumulator produces identical results to batch aggregation
- Test data filtering with `--filter` argument

## Example Workflows

### Test Streaming Accumulation

```bash
# Process batches sequentially
for batch in batches/batch_*.parquet; do
    batch_num=$(basename $batch .parquet | sed 's/batch_//')
    prev_num=$(printf "%03d" $((10#$batch_num - 1)))
    
    if [ "$batch_num" = "001" ]; then
        # Initialize
        healpyxel-accumulate --input $batch --columns r750 r950 --state-output state_v${batch_num}.parquet
    else
        # Incremental update
        healpyxel-accumulate --input $batch --columns r750 r950 \
            --state-input state_v${prev_num}.parquet --state-output state_v${batch_num}.parquet
    fi
done

# Finalize
healpyxel-finalize --state state_v010.parquet --output final_mosaic.parquet --percentiles 25 50 75
```

### Validate Consistency

```bash
# Method 1: Batch processing
healpyxel-sidecar --input validation/combined_batch_001_003.parquet --nside 64 --mode fuzzy
healpyxel-aggregate validation/combined_batch_001_003.parquet --columns r750 r950 --aggs mean std --output batch_result.parquet

# Method 2: Streaming accumulation
healpyxel-accumulate --input batches/batch_001.parquet --columns r750 r950 --state-output state_v001.parquet
healpyxel-accumulate --input batches/batch_002.parquet --columns r750 r950 --state-input state_v001.parquet --state-output state_v002.parquet
healpyxel-accumulate --input batches/batch_003.parquet --columns r750 r950 --state-input state_v002.parquet --state-output state_v003.parquet
healpyxel-finalize --state state_v003.parquet --output streaming_result.parquet

# Compare
python -c "
import pandas as pd
batch = pd.read_parquet('batch_result.parquet')
stream = pd.read_parquet('streaming_result.parquet')
print('Max difference:', (batch['r750_mean'] - stream['r750_mean']).abs().max())
"
```

## Data Characteristics

**Source**: MESSENGER/MASCS observations of Mercury
**Excluded columns**: `waves`, `photom_iof`, `geometry_bbox` (large arrays)
**Compression**: Snappy (optimized for read speed)
**Typical observation**: ~400 bytes per row

## Regeneration

To recreate these test datasets:

```bash
./create_test_data.sh
```

To clean and regenerate:

```bash
./create_test_data.sh --clean
```
EOF

    print_info "Created $metadata_file"
}

generate_summary() {
    print_header "Summary"
    
    echo ""
    echo "Test data directory: $TEST_DATA_DIR/"
    echo ""
    
    # Count files and total size
    local total_files=$(find "$TEST_DATA_DIR" -name "*.parquet" | wc -l)
    local total_size=$(du -sh "$TEST_DATA_DIR" | cut -f1)
    
    echo "Statistics:"
    echo "  • Total files: $total_files"
    echo "  • Total size: $total_size"
    echo ""
    
    # List by category
    for category in batches samples validation; do
        local count=$(find "$TEST_DATA_DIR/$category" -name "*.parquet" 2>/dev/null | wc -l)
        if [ $count -gt 0 ]; then
            local size=$(du -sh "$TEST_DATA_DIR/$category" 2>/dev/null | cut -f1)
            echo "  • $category/: $count files ($size)"
        fi
    done
    
    echo ""
    print_info "Test data generation complete!"
    echo ""
    echo "Next steps:"
    echo "  1. Review test_data/README.md for usage examples"
    echo "  2. Test healpyxel package with small samples"
    echo "  3. Validate streaming vs batch consistency"
}

# ============================================================================
# Main Execution
# ============================================================================

main() {
    # Parse arguments
    if [ "$1" = "--clean" ]; then
        print_warning "Cleaning existing test_data directory..."
        rm -rf "$TEST_DATA_DIR"
        echo ""
    fi
    
    # Print header
    clear
    print_header "MASCS Test Data Generator"
    echo ""
    echo "  Generating test datasets for healpyxel package"
    echo "  Source: $SOURCE_FILE"
    echo "  Output: $TEST_DATA_DIR/"
    echo ""
    
    # Execute pipeline
    check_requirements
    echo ""
    
    create_directory_structure
    echo ""
    
    create_sequential_batches
    echo ""
    
    create_size_samples
    echo ""
    
    create_validation_datasets
    echo ""
    
    create_metadata
    echo ""
    
    generate_summary
}

# Run main function
main "$@"
