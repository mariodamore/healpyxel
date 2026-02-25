# # Here’s an example workflow using the healpyxel.accumulate_batch function in a Python script.
# This script demonstrates how to process a batch of data, update the accumulator state, and save the results.
#
# Workflow Explanation
# Load Input Data:

# The script reads the input data from a Parquet file (observations_day001.parquet).
# Load Sidecar Data:

# The sidecar file (sidecars/day001_nside-512.parquet) maps observations to HEALPix cells.
# Initialize Metadata:

# Metadata for the HEALPix grid is defined (e.g., nside, mode, order).
# Process the Batch:

# The accumulate_batch function processes the input data, maps it to HEALPix cells using the sidecar, and updates the accumulator state.
# Save the State:

# The save_state function saves the accumulator state to a Parquet file (state/state_v001.parquet) with metadata.


from pathlib import Path
import pandas as pd
from healpyxel.accumulator import accumulate_batch, save_state, load_state
from healpyxel.metadata import HEALPyxelxMetadata

# Define file paths
input_data_path = Path("observations_day001.parquet")
sidecar_path = Path("sidecars/day001_nside-512.parquet")
state_output_path = Path("state/state_v001.parquet")

# Define columns to accumulate
value_columns = ["r750", "r950", "vis_slope"]

# Load input data
print("Loading input data...")
new_data = pd.read_parquet(input_data_path)

# Load sidecar data
print("Loading sidecar data...")
sidecar = pd.read_parquet(sidecar_path)

# Initialize metadata (example metadata, replace with actual metadata)
meta = HEALPyxelxMetadata(
    nside=512,
    mode="nested",
    order="nested",
    npix=12 * 512**2,
    lon_convention="0_360"
)

# Process the batch
print("Processing batch...")
state = accumulate_batch(
    new_data=new_data,
    sidecar=sidecar,
    value_columns=value_columns,
    existing_state=None,  # None for the first batch
    use_tdigest=True,     # Enable T-Digest for percentiles
    filter_expr=None      # No filtering in this example
)

# Save the state
print("Saving state...")
save_state(
    state=state,
    output_path=state_output_path,
    meta=meta,
    processing_metadata={
        "batch_id": "day001",
        "input_file": str(input_data_path),
        "sidecar_file": str(sidecar_path),
        "columns": value_columns
    }
)

print(f"State saved to {state_output_path}")

# Extending the Workflow
# For subsequent batches, you can load the existing state and update it:

# Define paths for the next batch
input_data_path = Path("observations_day002.parquet")
state_input_path = Path("state/state_v001.parquet")
state_output_path = Path("state/state_v002.parquet")

# Load existing state
print("Loading existing state...")
existing_state, existing_meta = load_state(state_input_path)

# Process the next batch
print("Processing next batch...")
state = accumulate_batch(
    new_data=new_data,
    sidecar=sidecar,
    value_columns=value_columns,
    existing_state=existing_state,  # Load previous state
    use_tdigest=True,
    filter_expr="quality > 0.5 and solar_zenith < 80"  # Example filter
)

# Save the updated state
print("Saving updated state...")
save_state(
    state=state,
    output_path=state_output_path,
    meta=meta,
    processing_metadata={
        "batch_id": "day002",
        "input_file": str(input_data_path),
        "sidecar_file": str(sidecar_path),
        "columns": value_columns
    }
)

print(f"Updated state saved to {state_output_path}")

# This script demonstrates how to use the healpyxel.accumulator API for both initializing and updating the accumulator state.


#### CLI EXAMPLE

# The accumulator.py module is designed to handle streaming planetary science data, accumulating statistics for HEALPix cells incrementally. Here's a breakdown of its functions and how they work in a simple workflow:

# Key Classes and Functions
# 1. StreamingStats
# Purpose: Maintains running statistics (mean, standard deviation, min, max) without storing raw data.
# How It Works:
# Uses Welford's algorithm for efficient computation.
# Updates statistics incrementally as new data arrives.
# Can merge with another StreamingStats object for parallel processing.
# 2. CellAccumulator
# Purpose: Manages statistics for a single HEALPix cell.
# How It Works:
# Tracks statistics for multiple columns using StreamingStats.
# Optionally uses T-Digest for approximate percentile calculations.
# Can merge with another CellAccumulator for distributed processing.
# 3. accumulate_batch
# Purpose: Processes a batch of data and updates the accumulator state.
# How It Works:
# Merges input data with a sidecar file to map observations to HEALPix cells.
# Updates or creates CellAccumulator objects for each HEALPix cell.
# Supports filtering data using a pandas query expression.
# 4. save_state
# Purpose: Saves the accumulator state to a Parquet file with metadata.
# How It Works:
# Serializes CellAccumulator objects into a DataFrame.
# Embeds HEALPix metadata and processing details into the Parquet file.
# 5. load_state
# Purpose: Loads accumulator state and metadata from a Parquet file.
# How It Works:
# Reconstructs CellAccumulator objects from serialized data.
# Validates metadata consistency.
# 6. validate_accumulator_sidecar_compatibility
# Purpose: Ensures compatibility between the accumulator state and sidecar metadata.
# How It Works:
# Checks critical parameters like nside, mode, and order.
# 7. find_sidecar
# Purpose: Locates the appropriate sidecar file for input data.
# How It Works:
# Searches in the same directory or a sidecars/ subdirectory for a matching file.
# 8. main
# Purpose: Command-line interface for the module.
# How It Works:
# Parses arguments for input/output files, columns, and options.
# Loads data, processes it using accumulate_batch, and saves the updated state.

# Simple CLI Workflow Example
# Initialize State from First Batch:

# Input: observations_day001.parquet (data), sidecar_day001.parquet (HEALPix mapping).
# Command

# python accumulator.py --input observations_day001.parquet --sidecar sidecar_day001.parquet \
#   --columns r750 r950 vis_slope --state-output state_v001.parquet

# Steps:
# Load input data and sidecar.
# Map observations to HEALPix cells.
# Create CellAccumulator objects for each cell.
# Save the state to state_v001.parquet.
# Incremental Update with Subsequent Batch:

# Input: observations_day002.parquet, state_v001.parquet.
# Command:

# python accumulator.py --input observations_day002.parquet --sidecar sidecar_day002.parquet \
#   --columns r750 r950 vis_slope --state-input state_v001.parquet --state-output state_v002.parquet

# Steps:
# Load existing state from state_v001.parquet.
# Process new data and update accumulators.
# Save the updated state to state_v002.parquet.
# With Data Filtering:

# Input: observations_day003.parquet, state_v002.parquet.
# Command:

# python accumulator.py --input observations_day003.parquet --sidecar sidecar_day003.parquet \
#   --columns r750 r950 vis_slope --state-input state_v002.parquet --state-output state_v003.parquet \
#   --filter "quality > 0.5 and solar_zenith < 80"

# Steps:
# Apply the filter to the input data.
# Update the state with filtered observations.
# Save the new state to state_v003.parquet.
# Summary
# This module is a robust tool for managing streaming data in HEALPix cells. It supports incremental updates, efficient statistics computation, and metadata validation. The workflow involves loading data, mapping it to HEALPix cells, updating accumulators, and saving the state.

