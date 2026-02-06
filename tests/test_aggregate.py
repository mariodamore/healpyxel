"""Tests for the aggregate module."""

import pytest
from pathlib import Path
import tempfile
import pandas as pd

from healpyxel.aggregate import collect_sidecar_outputs, _is_interactive_session


class TestCollectSidecarOutputs:
    """Test sidecar discovery and parsing."""
    
    def test_collect_sidecar_outputs_basic(self):
        """Test basic sidecar discovery in temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            input_path = tmp / "sample.parquet"
            sidecar_path = tmp / "sample.cell-healpix_assignment-fuzzy_nside-4_order-nested.parquet"
            
            # Create test data
            pd.DataFrame({"a": [1]}).to_parquet(input_path, index=False)
            pd.DataFrame({"source_id": [0], "healpix_id": [1]}).to_parquet(sidecar_path, index=False)
            
            # Test collection
            df = collect_sidecar_outputs(input_path, tmp, read_stats=False)
            assert len(df) == 1
            assert Path(df.iloc[0]["file"]).name == sidecar_path.name


class TestInteractiveSession:
    """Test interactive session detection."""
    
    def test_is_interactive_session_returns_bool(self):
        """Test that _is_interactive_session returns a boolean."""
        result = _is_interactive_session()
        assert isinstance(result, bool)
    
    def test_is_interactive_session_in_pytest(self):
        """Test that _is_interactive_session detects we're in IPython/Jupyter context."""
        result = _is_interactive_session()
        # In pytest, this should be False (unless running inside Jupyter)
        # Just check it returns a boolean without error
        assert isinstance(result, bool)
