"""Shared Maidenhead locator helpers for the OASIS suite."""

import re


def latlon_to_grid(lat, lon, precision=6):
    """Convert latitude/longitude to a Maidenhead locator string."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None

    if precision not in (4, 6, 8):
        precision = 6

    lon += 180.0
    lat += 90.0

    locator = []
    locator.append(chr(int(lon / 20) + ord("A")))
    locator.append(chr(int(lat / 10) + ord("A")))
    lon = lon % 20
    lat = lat % 10

    locator.append(str(int(lon / 2)))
    locator.append(str(int(lat / 1)))
    lon = lon % 2
    lat = lat % 1

    if precision >= 6:
        locator.append(chr(int(lon / (2 / 24.0)) + ord("a")))
        locator.append(chr(int(lat / (1 / 24.0)) + ord("a")))
        lon = lon % (2 / 24.0)
        lat = lat % (1 / 24.0)

    if precision >= 8:
        locator.append(str(int(lon / (2 / 240.0))))
        locator.append(str(int(lat / (1 / 240.0))))

    return "".join(locator[:precision])


def grid_to_latlon(grid):
    """Maidenhead locator → (lat, lon) at the square/subsquare centre."""
    g = (grid or "").strip().upper()
    if not re.match(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$", g):
        return None
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3]) * 1
    if len(g) >= 6:
        lon += (ord(g[4]) - 65) * (2 / 24) + (1 / 24)
        lat += (ord(g[5]) - 65) * (1 / 24) + (0.5 / 24)
    else:
        lon += 1
        lat += 0.5
    return (round(lat, 4), round(lon, 4))
