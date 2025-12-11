__version__ = "0.1.0"

# Import main modules for convenient access
from . import core
from . import sidecar
from . import aggregate
from . import accumulator
from . import finalize
from . import cli

__all__ = ['core', 'sidecar', 'aggregate', 'accumulator', 'finalize', 'cli', '__version__']
