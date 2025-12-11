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
