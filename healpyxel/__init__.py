"""HEALPix-based spatial aggregation for planetary science data.

Provides a pipeline for mapping spacecraft observations onto HEALPix cells
on planetary surfaces, then aggregating values using robust statistics.
Supports streaming accumulation with TDigest percentiles and GeoParquet
export for visualization.

Quick start::

    from healpyxel.workflow import run_pipeline
    results = run_pipeline(
        input="observations.parquet",
        output_dir="./output",
        nsides=(32, 64, 128),
        columns=("reflectance", "radiance"),
    )

Or use the CLI::

    healpyxel_sidecar -i data.parquet --nside 64 --mode fuzzy
    healpyxel_aggregate -i data.parquet --sidecar-index 0 --aggregate --columns reflectance
"""

__version__ = "0.3.0"

# Import main modules for convenient access
from . import core
from . import sidecar
from . import aggregate
from . import accumulator
from . import finalize
from . import cli
from . import geometry

# Pure-API entry points (ADR-009: submodules have no argparse/click)
from .sidecar import run as sidecar_run, parse_arguments as sidecar_parse_arguments
from .aggregate import run as aggregate_run, parse_arguments as aggregate_parse_arguments
from .accumulator import run as accumulator_run, parse_arguments as accumulator_parse_arguments
from .finalize import run as finalize_run, parse_arguments as finalize_parse_arguments
from .geospatial import run as geospatial_run, parse_arguments as geospatial_parse_arguments
from .geometry import Sphere, Ellipsoid, SpiceDSK, BodyGeometry
from .workflow import run_pipeline, save_script

__all__ = ['core', 'sidecar', 'aggregate', 'accumulator', 'finalize', 'cli', '__version__']
