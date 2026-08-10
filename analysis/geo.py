import math

EARTH_RADIUS_NM = 3440.065  # mean Earth radius, nautical miles


def _angular_distance_rad(lat1, lon1, lat2, lon2):
    """Great-circle angular distance (radians), via the spherical law of cosines."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    return math.acos(min(1, max(-1,
        math.sin(lat1)*math.sin(lat2) + math.cos(lat1)*math.cos(lat2)*math.cos(lon2-lon1)
    )))


def great_circle_distance_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles between two (lat, lon) points."""
    return _angular_distance_rad(lat1, lon1, lat2, lon2) * EARTH_RADIUS_NM


def great_circle_interpolate(lat1, lon1, lat2, lon2, frac):
    """Spherical slerp: (lat, lon) at fraction `frac` (0=start, 1=end)."""
    d = _angular_distance_rad(lat1, lon1, lat2, lon2)
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    if d == 0:
        return math.degrees(lat1), math.degrees(lon1)
    a = math.sin((1-frac)*d) / math.sin(d)
    b = math.sin(frac*d) / math.sin(d)
    x = a*math.cos(lat1)*math.cos(lon1) + b*math.cos(lat2)*math.cos(lon2)
    y = a*math.cos(lat1)*math.sin(lon1) + b*math.cos(lat2)*math.sin(lon2)
    z = a*math.sin(lat1) + b*math.sin(lat2)
    lat = math.atan2(z, math.sqrt(x**2+y**2))
    lon = math.atan2(y, x)
    return math.degrees(lat), math.degrees(lon)


def great_circle_path(lat1, lon1, lat2, lon2, num_points=30):
    """[lat, lon] points sampled along the great-circle path."""
    return [
        list(great_circle_interpolate(lat1, lon1, lat2, lon2, i / (num_points - 1)))
        for i in range(num_points)
    ]


if __name__ == "__main__":
    atl = (33.6367, -84.4281)
    sea = (47.4502, -122.3088)

    lat, lon = great_circle_interpolate(*atl, *sea, 0)
    assert math.isclose(lat, atl[0], abs_tol=1e-9) and math.isclose(lon, atl[1], abs_tol=1e-9), \
        f"frac=0 should return the start point exactly, got ({lat}, {lon})"

    lat, lon = great_circle_interpolate(*atl, *sea, 1)
    assert math.isclose(lat, sea[0], abs_tol=1e-9) and math.isclose(lon, sea[1], abs_tol=1e-9), \
        f"frac=1 should return the end point exactly, got ({lat}, {lon})"

    # JFK-LAX midpoint should bulge north of a naive lat/lon lerp.
    jfk = (40.6413, -73.7781)
    lax = (33.9416, -118.4085)
    gc_lat, gc_lon = great_circle_interpolate(*jfk, *lax, 0.5)
    naive_lat = (jfk[0] + lax[0]) / 2
    assert gc_lat > naive_lat, (
        f"expected great-circle midpoint lat ({gc_lat}) to bulge north of "
        f"naive lerp lat ({naive_lat})"
    )
    print(f"JFK-LAX midpoint: great-circle lat={gc_lat:.2f} vs naive lerp lat={naive_lat:.2f} "
          f"(diff={gc_lat - naive_lat:.2f} deg)")

    path = great_circle_path(*atl, *sea, num_points=5)
    assert len(path) == 5
    assert math.isclose(path[0][0], atl[0], abs_tol=1e-9) and math.isclose(path[0][1], atl[1], abs_tol=1e-9)
    assert math.isclose(path[-1][0], sea[0], abs_tol=1e-9) and math.isclose(path[-1][1], sea[1], abs_tol=1e-9)

    # --- great_circle_distance_nm ---
    same_point = great_circle_distance_nm(*atl, *atl)
    assert math.isclose(same_point, 0.0, abs_tol=1e-3), f"distance to self should be ~0, got {same_point}"

    atl_sea_nm = great_circle_distance_nm(*atl, *sea)
    sea_atl_nm = great_circle_distance_nm(*sea, *atl)
    assert math.isclose(atl_sea_nm, sea_atl_nm, rel_tol=1e-9), "distance should be symmetric"
    # Real ATL-SEA nonstop is ~1,895 nm; check the computed value is plausible.
    assert 1700 < atl_sea_nm < 2100, f"ATL-SEA great-circle distance out of plausible range: {atl_sea_nm} nm"
    print(f"ATL-SEA great-circle distance: {atl_sea_nm:.1f} nm (symmetric: {sea_atl_nm:.1f} nm)")

    print("All geo.py sanity checks passed.")
