"""
maidenhead.py
-------------
Convert latitude/longitude to a Maidenhead grid locator.

The Maidenhead Locator System divides the globe into a hierarchy of
rectangles. A 6-character locator (e.g. "EM12kx") is precise enough for
amateur-radio use and is what this project returns.

No external libraries required -- this runs fine on a Pi Zero 2 W.
"""


def latlon_to_grid(lat, lon, precision=6):
    """
    Convert a latitude/longitude pair into a Maidenhead grid square.

    Args:
        lat: latitude in decimal degrees  (-90 .. 90)
        lon: longitude in decimal degrees (-180 .. 180)
        precision: number of characters to return (must be even; 4 or 6)

    Returns:
        Maidenhead locator string, or None if inputs are invalid.
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    if precision not in (4, 6, 8):
        precision = 6

    # Shift so the origin is the South Pole / antimeridian corner.
    lon += 180.0
    lat += 90.0

    locator = []

    # Field: 20 deg lon, 10 deg lat  -> letters A-R
    locator.append(chr(int(lon / 20) + ord("A")))
    locator.append(chr(int(lat / 10) + ord("A")))
    lon = lon % 20
    lat = lat % 10

    # Square: 2 deg lon, 1 deg lat  -> digits 0-9
    locator.append(str(int(lon / 2)))
    locator.append(str(int(lat / 1)))
    lon = lon % 2
    lat = lat % 1

    if precision >= 6:
        # Subsquare: 5 min lon, 2.5 min lat -> letters a-x
        locator.append(chr(int(lon / (2 / 24.0)) + ord("a")))
        locator.append(chr(int(lat / (1 / 24.0)) + ord("a")))
        lon = lon % (2 / 24.0)
        lat = lat % (1 / 24.0)

    if precision >= 8:
        # Extended square -> digits 0-9
        locator.append(str(int(lon / (2 / 240.0))))
        locator.append(str(int(lat / (1 / 240.0))))

    return "".join(locator[:precision])


if __name__ == "__main__":
    # Quick self-test. Wichita, KS area should be ~EM17.
    tests = [
        (37.69, -97.34, "EM17"),   # Wichita, KS
        (29.76, -95.37, "EL29"),   # Houston, TX
        (40.71, -74.01, "FN20"),   # New York, NY
    ]
    for lat, lon, expected_field in tests:
        grid = latlon_to_grid(lat, lon)
        print(f"{lat:>8}, {lon:>9} -> {grid}  (expected ~{expected_field})")
