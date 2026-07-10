import numpy as np
import pandas as pd
from typing import Optional, Union, Tuple

# Import with guards for optional dependencies
try:
    from matplotlib import cm, colors
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    from skimage import exposure
    SKIMAGE_AVAILABLE = True
except ImportError:
    SKIMAGE_AVAILABLE = False

try:
    import healpy
    HEALPY_AVAILABLE = True
except ImportError:
    HEALPY_AVAILABLE = False

try:
    import skyproj
    SKYPROJ_AVAILABLE = True
except ImportError:
    SKYPROJ_AVAILABLE = False

def _check_dependencies():
    """Check if required visualization dependencies are available."""
    if not MATPLOTLIB_AVAILABLE:
        raise ImportError(
            "matplotlib is required for visualization. "
            "Install with: pip install matplotlib"
        )
    if not SKIMAGE_AVAILABLE:
        raise ImportError(
            "scikit-image is required for visualization. "
            "Install with: pip install scikit-image"
        )

def prepare_healpix_map(
    aggregated_dense: pd.DataFrame,  # Dense HEALPix aggregated DataFrame (from densify_healpix_aggregates)
    output_column: str = 'r1050_median',  # Column name to visualize
    equalize: bool = True,  # Apply histogram equalization for contrast enhancement
    percentile_cutoff: Optional[Union[float, Tuple[float, float]]] = None,  # Percentile clipping: single value (symmetric) or (min, max) tuple
    cmap: str = 'Spectral_r'  # Matplotlib colormap name
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, 'cm.ScalarMappable']:  # Returns: (healpix_map, valid_pixels, invalid_pixels, mappable)
    """
    Prepare a HEALPix map array and ScalarMappable for plotting.

    This function processes aggregated HEALPix data for visualization by:
    - Extracting the specified column as a numpy array
    - Optionally applying histogram equalization (enhances contrast)
    - Optionally clipping extreme values using percentiles
    - Creating a ScalarMappable with original data range for colorbars

    Parameters
    ----------
    aggregated_dense : pd.DataFrame
        Dense HEALPix DataFrame with all npix cells (from densify_healpix_aggregates)
    output_column : str, default='r1050_median'
        Name of the column to visualize
    equalize : bool, default=True
        Apply histogram equalization to enhance contrast
    percentile_cutoff : float or tuple of floats, optional
        Clip extreme values:
        - Single number (e.g., 5): clip at [5%, 95%]
        - Tuple (e.g., (2, 98)): clip at [2%, 98%]
        - None or False: no clipping
    cmap : str, default='Spectral_r'
        Matplotlib colormap name

    Returns
    -------
    healpix_map : np.ndarray
        Processed HEALPix map array (float64, length=npix)
    valid_pixels : np.ndarray
        Boolean mask of valid (non-NaN) pixels
    invalid_pixels : np.ndarray
        Boolean mask of invalid (NaN) pixels
    mappable : matplotlib.cm.ScalarMappable
        ScalarMappable configured with original data range for colorbars

    Examples
    --------
    ```python
    # Basic usage
    healpix_map, valid, invalid, mappable = prepare_healpix_map(
        aggregated_dense,
        output_column='r1050_median'
    )

    # With percentile clipping
    healpix_map, valid, invalid, mappable = prepare_healpix_map(
        aggregated_dense,
        output_column='r1050_median',
        percentile_cutoff=5  # Clip at 5% and 95%
    )

    # Without equalization
    healpix_map, valid, invalid, mappable = prepare_healpix_map(
        aggregated_dense,
        output_column='r1050_median',
        equalize=False
    )
    ```
    """
    _check_dependencies()

    # Determine whether to perform percentile clipping
    percentile_clip = not (percentile_cutoff is None or percentile_cutoff is False)

    # Extract base arrays
    healpix_map = aggregated_dense[output_column].astype(float).copy().values
    valid_pixels = ~np.isnan(healpix_map)
    invalid_pixels = ~valid_pixels

    # Optional histogram equalization (enhances contrast)
    if equalize and np.any(valid_pixels):
        healpix_map[valid_pixels] = exposure.equalize_hist(healpix_map[valid_pixels])

    # Optional percentile clipping
    if percentile_clip and np.any(valid_pixels):
        # Support single-value or two-element sequence for percentile_cutoff
        if hasattr(percentile_cutoff, '__iter__') and not isinstance(percentile_cutoff, (str, bytes)):
            try:
                lower_p, upper_p = map(float, percentile_cutoff)
            except Exception:
                raise ValueError("percentile_cutoff iterable must contain two numeric elements")
        else:
            lower_p = float(percentile_cutoff)
            upper_p = 100.0 - float(percentile_cutoff)

        # Validate percentiles
        if not (0.0 <= lower_p <= 100.0 and 0.0 <= upper_p <= 100.0):
            raise ValueError("percentile_cutoff values must be in [0, 100]")
        if lower_p >= upper_p:
            raise ValueError("percentile_cutoff lower percentile must be < upper percentile")

        vmin = float(np.nanpercentile(healpix_map[valid_pixels], lower_p))
        vmax = float(np.nanpercentile(healpix_map[valid_pixels], upper_p))
        healpix_map[valid_pixels] = np.clip(healpix_map[valid_pixels], vmin, vmax)

    # Create ScalarMappable using original (pre-equalized) values for colorbar
    orig_map = aggregated_dense[output_column].astype(float).values
    if np.any(valid_pixels):
        vmin = float(np.nanmin(orig_map[valid_pixels]))
        vmax = float(np.nanmax(orig_map[valid_pixels]))
        norm = colors.Normalize(vmin=vmin, vmax=vmax)
    else:
        norm = colors.Normalize(vmin=0.0, vmax=1.0)

    mappable = cm.ScalarMappable(norm=norm, cmap=cmap)
    # Use masked array so colorbar ignores invalid pixels
    mappable.set_array(np.ma.masked_where(invalid_pixels, orig_map))

    return healpix_map, valid_pixels, invalid_pixels, mappable
