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
    """Setup a logger with standard formatting.

    Args:
        name: Logger name
        level: Logging level (default: INFO)

    Returns:
        Configured logger
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


# ADR-size-recovery: HEALPix cell size utility
def healpix_cell_sizes(
    body_radius_km: float,
    nside: Sequence[int] = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192),
) -> pd.DataFrame:
    """Return cell angular and linear sizes for a spherical body.

    Computes the cell angular size (deg) and arc-length (km) for each
    HEALPix ``nside`` resolution, assuming a spherical body of given
    radius.  Useful for building reference tables like those in the
    docs.

    Args:
        body_radius_km: Radius of the spherical body in km.
        nside: Sequence of NSIDE values to compute.  Defaults to the
            standard set (1 .. 8192).

    Returns:
        DataFrame with columns ``nside``, ``cells`` (total count),
        ``angular_size_deg``, ``cell_size_km``.

    Examples:
        >>> df = healpix_cell_sizes(body_radius_km=1737.4)
        >>> df.loc[df["nside"] == 64, "cell_size_km"].round(3).iloc[0]
        27.78
    """
    results = []
    for n in nside:
        pix_area_sr = hp.nside2pixarea(n)
        area_deg2 = pix_area_sr * (180.0 / np.pi) ** 2
        ang_deg = np.sqrt(area_deg2)
        ang_rad = np.radians(ang_deg)
        cell_km = body_radius_km * ang_rad
        results.append({
            "nside": n,
            "cells": hp.nside2npix(n),
            "angular_size_deg": round(ang_deg, 3),
            "cell_size_km": round(cell_km, 3),
        })
    return pd.DataFrame(results).set_index("nside")
