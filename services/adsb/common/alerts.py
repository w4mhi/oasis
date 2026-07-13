"""Pure ADS-B alert evaluation — no I/O, unit-testable."""
import math

EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}
_SQUAWK_MEANING = {"7500": "unlawful interference", "7600": "radio failure",
                   "7700": "general emergency"}

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def evaluate(ac, station, radius_km):
    out = []
    sq = ac.get("squawk")
    if sq in EMERGENCY_SQUAWKS:
        out.append({"kind": "squawk",
                    "detail": f"{sq} ({_SQUAWK_MEANING[sq]})"})
    if station and ac.get("lat") is not None and ac.get("lon") is not None \
            and station.get("lat") is not None and station.get("lon") is not None:
        d = haversine_km(ac["lat"], ac["lon"], station["lat"], station["lon"])
        if d <= radius_km:
            # distance_km is canonical; the frontend formats it for display via the
            # existing imperial/metric toggle (km↔mi). detail is the log fallback.
            out.append({"kind": "proximity", "distance_km": round(d, 1),
                        "detail": f"{d:.1f} km from station"})
    return out
