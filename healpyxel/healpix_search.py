"""Candidate HEALPix cell search backends — ADR-020 interface."""

from __future__ import annotations

import numpy as np
from shapely import get_coordinates  # shapely>=2.0


class CandidateSearchUnsupported(Exception):
    """Raised when a backend cannot handle the given geometry."""


class QueryPolygonSearch:
    """Convex polygon search via healpy.query_polygon.

    Applicability: valid Polygon / MultiPolygon only.
    """

    def __init__(self, fact: int = 16):
        self.fact = fact

    def supports(self, geom) -> bool:
        return geom.geom_type in ("Polygon", "MultiPolygon") and geom.is_valid

    def __call__(self, body, geom, nside, _healpy):
        if not self.supports(geom):
            raise CandidateSearchUnsupported(
                "QueryPolygonSearch requires valid Polygon/MultiPolygon"
            )
        geoms = list(getattr(geom, "geoms", [geom]))
        all_hids = set()
        for part in geoms:
            coords = get_coordinates(part)
            lons = coords[:, 0].astype(np.float64)
            lats = coords[:, 1].astype(np.float64)

            if lons.size >= 1 and lons[0] == lons[-1] and lats[0] == lats[-1]:
                lons = lons[:-1]
                lats = lats[:-1]
            unique_mask = np.concatenate(
                [[True], (lons[1:] != lons[:-1]) | (lats[1:] != lats[:-1])]
            )
            lons = lons[unique_mask]
            lats = lats[unique_mask]

            if lons.size < 3:
                continue

            xyz = body.lonlat_to_xyz(lons, lats).T  # (N, 3)
            try:
                hids = _healpy.query_polygon(
                    nside, xyz, inclusive=True, nest=True, fact=self.fact
                )
            except RuntimeError as exc:
                raise CandidateSearchUnsupported(
                    f"query_polygon failed (likely non-convex / unprojectable): {exc}"
                )
            if isinstance(hids, tuple):
                hids = hids[0]
            all_hids.update(hids)

        return (
            np.array(sorted(all_hids), dtype=np.int64)
            if all_hids
            else np.array([], dtype=np.int64)
        )


class QueryDiscSearch:
    """Bounding-cap search via healpy.query_disc.

    Universally applicable regardless of polygon shape or convexity.
    """

    def __init__(self, margin_deg: float = 1.0):
        self.margin_deg = margin_deg

    def supports(self, geom) -> bool:
        return True

    def __call__(self, body, geom, nside, _healpy):
        geoms = list(getattr(geom, "geoms", [geom]))
        all_hids = set()
        for part in geoms:
            coords = get_coordinates(part)
            lons = coords[:, 0].astype(np.float64)
            lats = coords[:, 1].astype(np.float64)

            if lons.size >= 1 and lons[0] == lons[-1] and lats[0] == lats[-1]:
                lons = lons[:-1]
                lats = lats[:-1]
            unique_mask = np.concatenate(
                [[True], (lons[1:] != lons[:-1]) | (lats[1:] != lats[:-1])]
            )
            lons = lons[unique_mask]
            lats = lats[unique_mask]

            if lons.size < 3:
                continue

            xyz = body.lonlat_to_xyz(lons, lats)

            centroid_xyz = xyz.mean(axis=1)
            centroid_norm = np.linalg.norm(centroid_xyz)
            if centroid_norm > 1e-15:
                centroid_xyz = centroid_xyz / centroid_norm
            else:
                centroid_xyz = xyz[:, 0]

            dots = np.clip(np.dot(centroid_xyz, xyz), -1.0, 1.0)
            max_angle = np.arccos(dots.min())
            radius = max_angle + np.radians(self.margin_deg)

            hids = _healpy.query_disc(
                nside, centroid_xyz, radius, inclusive=True, nest=True
            )
            all_hids.update(hids)

        return (
            np.array(sorted(all_hids), dtype=np.int64)
            if all_hids
            else np.array([], dtype=np.int64)
        )


class AutoSearch:
    """Try backends in declared order, fall back on CandidateSearchUnsupported.

    First backend that reports supports(geom) is used. If __call__ raises
    CandidateSearchUnsupported at runtime (e.g., healpy.query_polygon on
    a non-convex polygon), fall through to the next backend.
    """

    def __init__(self, backends):
        self.backends = list(backends)

    def supports(self, geom) -> bool:
        return any(b.supports(geom) for b in self.backends)

    def __call__(self, body, geom, nside, _healpy):
        for backend in self.backends:
            try:
                return backend(body, geom, nside, _healpy)
            except CandidateSearchUnsupported:
                continue
        raise CandidateSearchUnsupported("No backend supports this geometry")


DEFAULT_SEARCH = AutoSearch(
    [
        QueryPolygonSearch(fact=16),
        QueryDiscSearch(margin_deg=1.0),
    ]
)
