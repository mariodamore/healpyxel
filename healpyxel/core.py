"""Utility functions for HEALPix indexing and statistical analysis.

Provides helpers shared across the healpyxel pipeline:

* :func:`validate_nside` — ensure nside is a power of two
* :func:`mad` — Median Absolute Deviation estimator
* :func:`robust_std` — MAD-based robust standard deviation
* :func:`setup_logger` — standardised logging configuration
* :func:`healpix_cell_sizes` — tabulate cell angular and linear sizes
"""

import logging
from pathlib import Path
from typing import Optional, Sequence, Union

import healpy as hp
import numpy as np
import pandas as pd

def validate_nside(nside: int) -> int:
    """Validate that nside is a power of 2.

    Args:
        nside: HEALPix resolution parameter

    Returns:
        Validated nside value

    Raises:
        ValueError: If nside is not a power of 2

    Examples:
        >>> validate_nside(64)
        64
        >>> validate_nside(100)
        Traceback (most recent call last):
        ...
        ValueError: nside must be a power of 2, got 100
    """
    if nside <= 0 or (nside & (nside - 1)) != 0:
        raise ValueError(f"nside must be a power of 2, got {nside}")
    return nside

def mad(arr: np.ndarray) -> float:
    """Compute Median Absolute Deviation.

    Args:
        arr: Input array

    Returns:
        MAD value (float)

    Examples:
        >>> arr = np.array([1, 2, 3, 4, 5])
        >>> mad(arr)
        1.0
    """
    arr = np.asarray(arr, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.median(np.abs(arr - np.median(arr))))

def robust_std(arr: np.ndarray) -> float:
    """Compute robust standard deviation using MAD * 1.4826.

    The factor 1.4826 makes MAD consistent with standard deviation
    for normally distributed data.

    Args:
        arr: Input array

    Returns:
        Robust standard deviation (float)

    Examples:
        >>> arr = np.array([1, 2, 3, 4, 5])
        >>> robust_std(arr)
        1.4826
    """
    return mad(arr) * 1.4826

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Create a logger with the standard healpyxel output format.

    Only adds a handler when the logger has no handlers yet, so repeated
    calls with the same name do not produce duplicate output.

    Parameters
    ----------
    name : str
        Logger name (typically the calling module, e.g.
        ``"healpyxel.sidecar"``).
    level : int
        Logging level threshold (default: ``logging.INFO``).

    Returns
    -------
    logging.Logger
        Configured logger with a ``StreamHandler`` using
        ``"%(asctime)s %(levelname)s %(message)s"`` format.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        )
        logger.addHandler(handler)

    return logger


def healpix_cell_sizes(
    radii: Sequence[tuple[str, float]] | None = None,
    nside: Sequence[int] = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192),
) -> pd.DataFrame:
    """Return HEALPix cell sizes for one or more spherical bodies.

    Computes ``cells`` (total count) and ``angular_size_deg`` once — these
    depend only on ``nside`` — then adds one ``cell_size_km`` column per
    body.  This matches the structure of the reference table in the docs.

    Args:
        radii: Sequence of ``(column_name, body_radius_km)`` pairs.
            ``column_name`` is the label for the output column (e.g.
            ``"Mercury Cell Size (km)"``).  Pass ``None`` or an empty
            sequence to get only the radius-independent columns.
        nside: Sequence of NSIDE values to compute.  Defaults to the
            standard set (1 … 8192).

    Returns:
        Flat DataFrame (no MultiIndex) with columns:

        - ``nside``
        - ``Number of Cells``
        - ``Cell Angular Size (deg)``
        - one ``cell_size_km`` column per element in ``radii``

        If ``radii`` is empty/None, only the first three columns are
        present.

    Examples:
        Single body:

        >>> df = healpix_cell_sizes(radii=[("Moon", 1737.4)])
        >>> float(df.loc[df["nside"] == 64, "Moon"].iloc[0])
        27.78

        Multiple bodies:

        >>> df = healpix_cell_sizes(radii=[("Mercury", 2439.7), ("Moon", 1737.4)])
        >>> sorted(df.columns.tolist())
        ['Cell Angular Size (deg)', 'Mercury', 'Moon', 'Number of Cells', 'nside']

        No radii (nside-only quantities):

        >>> df = healpix_cell_sizes()
        >>> sorted(df.columns.tolist())
        ['Cell Angular Size (deg)', 'Number of Cells', 'nside']
        >>> len(df)
        14
    """
    rows = []
    for n in nside:
        pix_area_sr = hp.nside2pixarea(n)
        area_deg2 = pix_area_sr * (180.0 / np.pi) ** 2
        ang_deg = np.sqrt(area_deg2)
        ang_rad = np.radians(ang_deg)
        rows.append({
            "nside": n,
            "Number of Cells": hp.nside2npix(n),
            "Cell Angular Size (deg)": round(ang_deg, 3),
        })

    df = pd.DataFrame(rows)

    if radii:
        for name, r_km in radii:
            values = []
            for n in nside:
                pix_area_sr = hp.nside2pixarea(n)
                area_deg2 = pix_area_sr * (180.0 / np.pi) ** 2
                ang_rad = np.radians(np.sqrt(area_deg2))
                values.append(round(r_km * ang_rad, 3))
            df[name] = values

    return df
