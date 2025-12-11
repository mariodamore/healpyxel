"""Pytest configuration and fixtures for healpyxel tests."""

import pytest
from pathlib import Path
import pandas as pd


@pytest.fixture(scope="session")
def test_data_dir():
    """Return the test_data directory path."""
    pkg_root = Path(__file__).parent.parent
    return pkg_root / "test_data"


@pytest.fixture(scope="session")
def batches_dir(test_data_dir):
    """Return the batches directory path."""
    return test_data_dir / "batches"


@pytest.fixture(scope="session")
def samples_dir(test_data_dir):
    """Return the samples directory path."""
    return test_data_dir / "samples"


@pytest.fixture(scope="session")
def validation_dir(test_data_dir):
    """Return the validation directory path."""
    return test_data_dir / "validation"


@pytest.fixture
def sample_5k(samples_dir):
    """Load the 5k sample parquet file."""
    return pd.read_parquet(samples_dir / "sample_5k.parquet")


@pytest.fixture
def batch_001(batches_dir):
    """Load batch 001 parquet file."""
    return pd.read_parquet(batches_dir / "batch_001.parquet")


@pytest.fixture
def combined_batches(validation_dir):
    """Load combined validation file (batches 1-3)."""
    return pd.read_parquet(validation_dir / "combined_batch_001_003.parquet")


@pytest.fixture
def spectral_columns():
    """Return list of spectral column names (r310-r1400)."""
    return [f"r{w}" for w in range(310, 1401, 5)]


@pytest.fixture
def quality_columns():
    """Return list of quality flag column names."""
    return list("abcdefghijklmnop") + ["q1", "q2", "q3", "q4"]


@pytest.fixture
def nside_values():
    """Return common HEALPix nside values for testing."""
    return [64, 128, 256, 512]
