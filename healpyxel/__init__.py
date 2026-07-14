__version__ = "0.2.0"

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

__all__ = ['core', 'sidecar', 'aggregate', 'accumulator', 'finalize', 'cli', '__version__']
