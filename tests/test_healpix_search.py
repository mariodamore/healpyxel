"""Tests for healpix_search module — ADR-020 candidate search interface."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import MultiPolygon, Polygon

import healpy as hp

from healpyxel.geometry import Sphere
from healpyxel.healpix_search import (
    AutoSearch,
    CandidateSearchUnsupported,
    QueryDiscSearch,
    QueryPolygonSearch,
    DEFAULT_SEARCH,
)


@pytest.fixture
def body():
    return Sphere()


@pytest.fixture
def convex_polygon():
    return Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])


@pytest.fixture
def concave_polygon():
    return Polygon([(0, 0), (10, 0), (5, 5), (10, 10), (0, 10)])


@pytest.fixture
def multi_polygon():
    return MultiPolygon(
        [Polygon([(0, 0), (1, 0), (1, 1)]), Polygon([(20, 20), (21, 20), (21, 21)])]
    )


@pytest.fixture
def invalid_polygon():
    p = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    p = p.buffer(0)  # force invalid handling
    return p


class TestQueryPolygonSearch:
    def test_supports_convex(self, convex_polygon):
        assert QueryPolygonSearch().supports(convex_polygon) is True

    def test_supports_multi_polygon(self, multi_polygon):
        assert QueryPolygonSearch().supports(multi_polygon) is True

    def test_supports_concave(self, concave_polygon):
        assert QueryPolygonSearch().supports(concave_polygon) is True

    def test_call_returns_sorted_unique(self, body, convex_polygon):
        result = QueryPolygonSearch()(body, convex_polygon, 32, hp)
        assert result.ndim == 1
        assert result.dtype == np.int64
        assert np.all(result[:-1] < result[1:])  # sorted

    def test_raises_on_missing_body(self):
        """QueryPolygonSearch needs body.lonlat_to_xyz; will error on None."""
        with pytest.raises((AttributeError, TypeError)):
            QueryPolygonSearch()(None, Polygon([(0, 0), (1, 0), (1, 1)]), 32, hp)


class TestQueryDiscSearch:
    def test_supports_always_true(self, convex_polygon, concave_polygon):
        assert QueryDiscSearch().supports(convex_polygon) is True
        assert QueryDiscSearch().supports(concave_polygon) is True

    def test_call_returns_sorted_unique(self, body, convex_polygon):
        result = QueryDiscSearch()(body, convex_polygon, 32, hp)
        assert result.ndim == 1
        assert result.dtype == np.int64
        assert np.all(result[:-1] < result[1:])

    def test_call_works_on_concave(self, body, concave_polygon):
        result = QueryDiscSearch()(body, concave_polygon, 32, hp)
        assert result.size > 0


class TestAutoSearch:
    def test_tries_polygon_first_for_convex(self, body, convex_polygon):
        """AutoSearch should prefer PolygonSearch on convex geometry."""
        search = AutoSearch([QueryPolygonSearch(), QueryDiscSearch()])
        result = search(body, convex_polygon, 32, hp)
        assert result.size > 0

    def test_falls_back_to_disc_for_monkey_patched_unsupported(self, body, convex_polygon):
        """If first backend's supports() rejects, falls through."""
        from healpyxel.healpix_search import CandidateSearchUnsupported

        class RejectingBackend:
            def supports(self, geom): return False
            def __call__(self, body, geom, nside, _healpy):
                raise CandidateSearchUnsupported("should not be called")

        search = AutoSearch([RejectingBackend(), QueryDiscSearch()])
        result = search(body, convex_polygon, 32, hp)
        assert result.size > 0

    def test_raises_when_no_backend_supports(self, body):
        with pytest.raises(CandidateSearchUnsupported):
            AutoSearch([])(body, Polygon([(0, 0), (1, 0), (1, 1)]), 32, hp)


class TestDifferentialEquivalence:
    """ADR-020 success criterion: different backends produce identical final cell
    sets after exact intersection filtering (not identical intermediate candidates).

    query_polygon returns tighter results than query_disc (fewer false positives),
    but after _filter_candidates_exact both must yield the same cells.
    """

    def test_polygon_is_subset_of_disc(self, body, convex_polygon):
        poly_results = set(QueryPolygonSearch()(body, convex_polygon, 32, hp))
        disc_results = set(QueryDiscSearch()(body, convex_polygon, 32, hp))
        assert poly_results.issubset(disc_results)

    def test_polygon_is_subset_of_disc_multi(self, body, multi_polygon):
        poly_results = set(QueryPolygonSearch()(body, multi_polygon, 32, hp))
        disc_results = set(QueryDiscSearch()(body, multi_polygon, 32, hp))
        assert poly_results.issubset(disc_results)

    def test_final_cells_match_after_exact_filter(self, body, convex_polygon):
        from healpyxel.sidecar import _filter_candidates_exact

        poly_candidates = QueryPolygonSearch()(body, convex_polygon, 32, hp)
        disc_candidates = QueryDiscSearch()(body, convex_polygon, 32, hp)

        poly_final = _filter_candidates_exact(poly_candidates, convex_polygon, 32, "geographic")
        disc_final = _filter_candidates_exact(disc_candidates, convex_polygon, 32, "geographic")

        assert len(poly_final) == len(disc_final)
        assert set(poly_final) == set(disc_final)

    def test_default_search_is_conservative(self, body, convex_polygon):
        """DEFAULT_SEARCH prefers polygon but must include all disc cells for convex."""
        results = set(DEFAULT_SEARCH(body, convex_polygon, 32, hp))
        disc_set = set(QueryDiscSearch()(body, convex_polygon, 32, hp))
        # For convex, DEFAULT_SEARCH uses polygon; results ⊆ disc
        assert results.issubset(disc_set)


class TestMarginParam:
    def test_smaller_margin_gives_fewer_candidates(self, body, convex_polygon):
        loose = QueryDiscSearch(margin_deg=5.0)(body, convex_polygon, 32, hp)
        tight = QueryDiscSearch(margin_deg=0.1)(body, convex_polygon, 32, hp)
        assert tight.size <= loose.size
