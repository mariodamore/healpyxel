"""Body geometry backends for healpyxel.

Provides a pluggable interface for describing planetary body shapes:
  - Sphere    : perfect sphere (Moon, Mercury, most asteroids)
  - Ellipsoid : oblate spheroid (Earth, Mars, Venus)
  - SpiceDSK  : SPICE Digital Shape Kernel (future, not yet implemented)

All backends share the same interface. The computation engine uses them
to normalize input coordinates to unit vectors for HEALPix indexing.

antimeridian is NOT needed in the computation path — it is only used
in the geospatial output layer (healpix_to_geoparquet) for visualization.
"""

import numpy as np
from typing import Protocol, runtime_checkable


@runtime_checkable
class BodyGeometry(Protocol):
    """Protocol (interface) for body geometry backends.

    All backends must implement these methods. The body model is used
    at the I/O boundary: input lon/lat is converted to 3D vectors,
    and output is converted back to lon/lat.
    """

    def lonlat_to_xyz(self, lon_deg: np.ndarray, lat_deg: np.ndarray) -> np.ndarray:
        """Convert lon/lat degrees to 3D Cartesian vectors.

        Returns array of shape (3, N) with x, y, z components.
        """
        ...

    def xyz_to_lonlat(self, xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Convert 3D Cartesian vectors back to lon/lat degrees.

        Args:
            xyz: array of shape (3, N)

        Returns:
            (lon_deg, lat_deg) each of shape (N,)
        """
        ...

    def name(self) -> str:
        """Human-readable body model name for metadata/logging."""
        ...

    def is_sphere(self) -> bool:
        """True if this backend is a perfect sphere (optimization hint)."""
        ...


class Sphere:
    """Perfect sphere geometry.

    The default backend — works for Moon, Mercury, most asteroids.
    Internal representation is unit vectors (radius=1.0) by default.

    Parameters
    ----------
    radius : float
        Sphere radius in arbitrary units. Default 1.0 (unit sphere).
        The radius is scale-invariant for HEALPix indexing; only the
        direction matters, not the magnitude.
    """

    def __init__(self, radius: float = 1.0):
        self.radius = float(radius)

    def lonlat_to_xyz(self, lon_deg, lat_deg):
        lon = np.asarray(lon_deg, dtype=np.float64)
        lat = np.asarray(lat_deg, dtype=np.float64)
        phi = np.radians(np.mod(lon, 360.0))
        theta = np.radians(90.0 - lat)
        r = self.radius
        return np.stack([
            r * np.cos(phi) * np.sin(theta),
            r * np.sin(phi) * np.sin(theta),
            r * np.cos(theta),
        ], axis=0)

    def xyz_to_lonlat(self, xyz):
        x, y, z = xyz[0], xyz[1], xyz[2]
        r2 = x * x + y * y + z * z
        r = np.sqrt(np.maximum(r2, 1e-30))
        lon = np.degrees(np.arctan2(y, x))
        lat = 90.0 - np.degrees(np.arccos(np.clip(z / r, -1.0, 1.0)))
        return lon, lat

    def name(self):
        return f"Sphere(radius={self.radius})"

    def is_sphere(self):
        return True


class Ellipsoid:
    """Oblate spheroid geometry.

    For bodies with measurable flattening: Earth (f=1/298), Mars, Venus.
    Uses parametric (not geodetic) equations — sufficient for sidecar
    indexing where sub-pixel accuracy is not the limiting factor.

    Parameters
    ----------
    radius : float
        Equatorial radius in arbitrary units.
    polar_radius : float or None
        Polar radius. If None, creates a sphere (same as Sphere(radius)).
    """

    def __init__(self, radius: float = 6371e3, polar_radius: float | None = None):
        self._a = float(radius)
        if polar_radius is not None:
            self._b = float(polar_radius)
            self._is_sphere = False
        else:
            self._b = float(radius)
            self._is_sphere = True

    def lonlat_to_xyz(self, lon_deg, lat_deg):
        """Convert lon/lat to ellipsoid parametric xyz.

        Uses equal-spacing in lat (not area-preserving). The resulting
        vectors are normalized to unit length before passing to healpy,
        so the ellipsoid shape is effectively used for the coordinate
        conversion only — HEALPix indexing is always on the unit sphere.
        """
        lon = np.asarray(lon_deg, dtype=np.float64)
        lat = np.asarray(lat_deg, dtype=np.float64)
        phi = np.radians(np.mod(lon, 360.0))
        theta = np.radians(90.0 - lat)  # colatitude
        a = self._a
        b = self._b
        return np.stack([
            np.cos(phi) * np.sin(theta),
            np.sin(phi) * np.sin(theta),
            (a / b) * np.cos(theta),
        ], axis=0)

    def xyz_to_lonlat(self, xyz):
        """Convert ellipsoid xyz back to lon/lat (inverse parametric)."""
        x, y, z = xyz[0], xyz[1], xyz[2]
        a = self._a
        b = self._b
        lon = np.degrees(np.arctan2(y, x))
        # Inverse of z = (a/b) * cos(theta) => theta = arccos(z * b / a)
        cos_theta = np.clip(z * b / a, -1.0, 1.0)
        lat = 90.0 - np.degrees(np.arccos(cos_theta))
        return lon, lat

    def name(self):
        if self._is_sphere:
            return f"Ellipsoid(sphere, radius={self._a:.0f})"
        f = 1.0 - self._b / self._a
        return f"Ellipsoid(a={self._a:.0f}, b={self._b:.0f}, f={f:.4f})"

    def is_sphere(self):
        return self._is_sphere


class SpiceDSK:
    """SPICE DSK shape model backend.

    NOT YET IMPLEMENTED. This is a placeholder that documents the
    intended interface and raises NotImplementedError if called.

    When implemented, it will delegate to SPICE for ray interception
    and polygon generation on arbitrary body shapes. The sidecar
    pipeline will not need to change — only this backend.

    See ADR-013 for the implementation plan.

    Example usage after implementation::

        dsk = SpiceDSK(meta_kernel='path/to/mercury.mk', dsk_id='MERCURY')
        xyz = dsk.lonlat_to_xyz(lons, lats)
    """

    def lonlat_to_xyz(self, lon_deg, lat_deg):
        raise NotImplementedError(
            "SpiceDSK is not yet implemented. "
            "See ADR-013 for the implementation plan. "
            "Use Sphere() or Ellipsoid() for now."
        )

    def xyz_to_lonlat(self, xyz):
        raise NotImplementedError(
            "SpiceDSK is not yet implemented. "
            "See ADR-013 for the implementation plan. "
            "Use Sphere() or Ellipsoid() for now."
        )

    def name(self):
        return "SpiceDSK(not implemented)"

    def is_sphere(self):
        return False
