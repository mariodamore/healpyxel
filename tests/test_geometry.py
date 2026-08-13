import pytest
import numpy as np

from healpyxel.geometry import (
    Sphere, Ellipsoid, SpiceDSK, BodyGeometry
)


class TestSphere:
    """Test Sphere geometry backend."""

    def test_default_radius(self):
        s = Sphere()
        assert s.radius == 1.0

    def test_custom_radius(self):
        s = Sphere(radius=1737.4)
        assert s.radius == 1737.4

    def test_name(self):
        s = Sphere()
        assert "Sphere" in s.name()

    def test_is_sphere_true(self):
        s = Sphere()
        assert s.is_sphere() is True

    def test_lonlat_to_xyz_north_pole(self):
        s = Sphere()
        xyz = s.lonlat_to_xyz(np.array([0.0]), np.array([90.0]))
        assert np.allclose(xyz.ravel(), [0, 0, 1], atol=1e-10)

    def test_lonlat_to_xyz_south_pole(self):
        s = Sphere()
        xyz = s.lonlat_to_xyz(np.array([0.0]), np.array([-90.0]))
        assert np.allclose(xyz.ravel(), [0, 0, -1], atol=1e-10)

    def test_lonlat_to_xyz_equator(self):
        s = Sphere()
        xyz = s.lonlat_to_xyz(np.array([0.0, 90.0, 180.0, 270.0]),
                               np.array([0.0, 0.0, 0.0, 0.0]))
        assert np.allclose(xyz[2], 0)
        assert np.allclose(xyz[0] ** 2 + xyz[1] ** 2 + xyz[2] ** 2, 1)

    def test_xyz_to_lonlat_north_pole(self):
        s = Sphere()
        lon, lat = s.xyz_to_lonlat(np.array([[0], [0], [1]]))
        assert abs(lat[0] - 90.0) < 1e-10

    def test_xyz_to_lonlat_south_pole(self):
        s = Sphere()
        lon, lat = s.xyz_to_lonlat(np.array([[0], [0], [-1]]))
        assert abs(lat[0] - (-90.0)) < 1e-10

    def test_roundtrip_single_point(self):
        s = Sphere()
        lon = np.array([42.0, -120.0, 180.0])
        lat = np.array([30.0, -45.0, 60.0])
        xyz = s.lonlat_to_xyz(lon, lat)
        lon_rt, lat_rt = s.xyz_to_lonlat(xyz)
        assert np.allclose(lon % 360, lon_rt % 360, atol=1e-10)
        assert np.allclose(lat, lat_rt, atol=1e-10)

    def test_roundtrip_vectorized(self):
        s = Sphere()
        pts = np.random.RandomState(42).uniform(-180, 180, 50)
        lats = np.random.RandomState(99).uniform(-90, 90, 50)
        xyz = s.lonlat_to_xyz(pts, lats)
        lon_rt, lat_rt = s.xyz_to_lonlat(xyz)
        assert np.allclose(pts % 360, lon_rt % 360, atol=1e-9)
        assert np.allclose(lats, lat_rt, atol=1e-9)


class TestEllipsoid:
    """Test Ellipsoid geometry backend."""

    def test_default_params__sphere(self):
        e = Ellipsoid()
        assert e._a == 6371e3
        assert e._b == 6371e3
        assert e.is_sphere() is True

    def test_custom_radii(self):
        e = Ellipsoid(radius=100.0, polar_radius=99.0)
        assert e._a == 100.0
        assert e._b == 99.0

    def test_polar_radius_none__creates_sphere(self):
        e = Ellipsoid(radius=500.0, polar_radius=None)
        assert e.is_sphere() is True
        assert e._b == 500.0

    def test_is_sphere_false_with_flattening(self):
        e = Ellipsoid(radius=6371e3, polar_radius=6356e3)
        assert e.is_sphere() is False

    def test_name_sphere(self):
        e = Ellipsoid()
        assert "sphere" in e.name()

    def test_name_ellipsoid(self):
        e = Ellipsoid(radius=6371e3, polar_radius=6356e3)
        assert "Ellipsoid" in e.name()

    def test_lonlat_to_xyz_shape(self):
        e = Ellipsoid()
        lon = np.array([0.0, 90.0])
        lat = np.array([0.0, 0.0])
        xyz = e.lonlat_to_xyz(lon, lat)
        assert xyz.shape == (3, 2)

    def test_xyz_to_lonlat_shape(self):
        e = Ellipsoid()
        xyz = np.zeros((3, 5))
        lon, lat = e.xyz_to_lonlat(xyz)
        assert lon.shape == (5,)
        assert lat.shape == (5,)

    def test_roundtrip_single_point(self):
        e = Ellipsoid()
        lon = np.array([42.0])
        lat = np.array([30.0])
        xyz = e.lonlat_to_xyz(lon, lat)
        lon_rt, lat_rt = e.xyz_to_lonlat(xyz)
        assert np.allclose(lon % 360, lon_rt % 360, atol=1e-10)
        assert np.allclose(lat, lat_rt, atol=1e-10)

    def test_roundtrip_vectorized(self):
        rng = np.random.RandomState(42)
        lon = rng.uniform(-180, 180, 50)
        lat = rng.uniform(-90, 90, 50)
        e = Ellipsoid(radius=6371e3, polar_radius=6356e3)
        xyz = e.lonlat_to_xyz(lon, lat)
        lon_rt, lat_rt = e.xyz_to_lonlat(xyz)
        assert np.allclose(lon % 360, lon_rt % 360, atol=1e-9)
        assert np.allclose(lat, lat_rt, atol=1e-9)

    def test_roundtrip_with_flattening(self):
        rng = np.random.RandomState(0)
        lon = rng.uniform(-180, 180, 100)
        lat = rng.uniform(-90, 90, 100)
        e = Ellipsoid(radius=100.0, polar_radius=98.0)
        xyz = e.lonlat_to_xyz(lon, lat)
        lon_rt, lat_rt = e.xyz_to_lonlat(xyz)
        assert np.allclose(lon % 360, lon_rt % 360, atol=1e-9)
        assert np.allclose(lat, lat_rt, atol=1e-9)


class TestSpiceDSK:
    """Test SpiceDSK placeholder raises NotImplementedError."""

    def test_lonlat_to_xyz_raises(self):
        d = SpiceDSK()
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            d.lonlat_to_xyz(np.array([0.0]), np.array([0.0]))

    def test_xyz_to_lonlat_raises(self):
        d = SpiceDSK()
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            d.xyz_to_lonlat(np.zeros((3, 1)))

    def test_name_placeholder(self):
        assert "not implemented" in SpiceDSK().name()

    def test_is_sphere_false(self):
        assert SpiceDSK().is_sphere() is False


class TestBodyGeometryProtocol:
    """Test that all backends satisfy the BodyGeometry protocol."""

    def test_sphere_satisfies_protocol(self):
        assert isinstance(Sphere(), BodyGeometry)

    def test_ellipsoid_satisfies_protocol(self):
        assert isinstance(Ellipsoid(), BodyGeometry)

    def test_spicedsk_satisfies_protocol(self):
        assert isinstance(SpiceDSK(), BodyGeometry)
