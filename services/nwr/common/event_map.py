"""SAME event code -> OASIS warning-catalog type.

SAME carries roughly eighty event codes; maps/traffic/warnings.json has sixteen
marker types. The collapse is therefore LOSSY BY DESIGN, and it is written out
here explicitly rather than inferred, so that what a tornado warning looks like
on the map is a decision someone made rather than an accident of string
matching.

Two rules that are easy to get backwards:

  * INFORMATIONAL codes map to None — they are logged and never plotted. Tests,
    demos, statements and administrative messages are real traffic, but a pin
    for the Required Weekly Test every week is noise that would train the
    operator to ignore pins.

  * An UNKNOWN code falls back to "weather" rather than to None. SAME grows, and
    a warning we do not recognise is far more likely to be a hazard than an
    announcement. Over-plotting is recoverable; a missed tornado warning is not.
"""

FALLBACK_TYPE = "weather"

EVENT_TYPE = {
    # Tornado
    "TOR": "tornado",                                   # Tornado Warning
    "TOA": "tornado",                                   # Tornado Watch
    # Severe thunderstorm / tropical / marine
    "SVR": "storm",   # Severe Thunderstorm Warning
    "SVA": "storm",   # Severe Thunderstorm Watch
    "SQW": "storm",   # Snow Squall Warning
    "SMW": "storm",   # Special Marine Warning
    "TRW": "storm",   # Tropical Storm Warning
    "TRA": "storm",   # Tropical Storm Watch
    "HUW": "storm",   # Hurricane Warning
    "HUA": "storm",   # Hurricane Watch
    # Water: flooding, surge, tsunami
    "FFW": "flood",   # Flash Flood Warning
    "FFA": "flood",   # Flash Flood Watch
    "FFS": "flood",   # Flash Flood Statement
    "FLW": "flood",   # Flood Warning
    "FLA": "flood",   # Flood Watch
    "FLS": "flood",   # Flood Statement
    "CFW": "flood",   # Coastal Flood Warning
    "CFA": "flood",   # Coastal Flood Watch
    "SSW": "flood",   # Storm Surge Warning
    "SSA": "flood",   # Storm Surge Watch
    "DBW": "flood",   # Debris Flow Warning
    "HSW": "flood",   # High Surf Warning  (surf, NOT winter)
    "TSW": "flood",   # Tsunami Warning
    "TSA": "flood",   # Tsunami Watch
    # Winter
    "WSW": "ice",     # Winter Storm Warning
    "WSA": "ice",     # Winter Storm Watch
    "BZW": "ice",     # Blizzard Warning
    "ISW": "ice",     # Ice Storm Warning
    "LES": "ice",     # Lake Effect Snow Warning  (LES, not LEW)
    "FZW": "ice",     # Freeze Warning
    "FSW": "ice",     # Flash Freeze Warning  (freeze, NOT fire)
    # Wind and dust
    "HWW": "wind",    # High Wind Warning
    "HWA": "wind",    # High Wind Watch
    "EWW": "wind",    # Extreme Wind Warning
    "DSW": "wind",    # Dust Storm Warning
    "HFW": "wind",    # Hurricane Force Wind Warning
    "GLW": "wind",    # Gale Warning
    # Fire
    "FRW": "fire",    # Fire Warning
    # Hazardous materials, nuclear, radiological
    "HMW": "hazmat",  # Hazardous Materials Warning
    "NUW": "hazmat",  # Nuclear Power Plant Warning
    "RHW": "hazmat",  # Radiological Hazard Warning
    # Civil, law enforcement, evacuation, emergency management
    "CEM": "eoc",     # Civil Emergency Message
    "CDW": "eoc",     # Civil Danger Warning
    "EVI": "eoc",     # Evacuation Immediate
    "LEW": "eoc",     # Law Enforcement Warning  (LEW is NOT lake-effect snow)
    "LAE": "eoc",     # Local Area Emergency
    "CAE": "eoc",     # Child Abduction Emergency
    "SPW": "eoc",     # Shelter In Place Warning
    "TOE": "eoc",     # 911 Telephone Outage Emergency
    "EAN": "eoc",     # Emergency Action Notification
    # Geological / terrain — no honest home among the specific types
    "EQW": FALLBACK_TYPE,   # Earthquake Warning
    "AVW": FALLBACK_TYPE,   # Avalanche Warning
    "AVA": FALLBACK_TYPE,   # Avalanche Watch
    "VOW": FALLBACK_TYPE,   # Volcano Warning
    "LSW": FALLBACK_TYPE,   # Landslide Warning
}

# Logged, never plotted, never announced as a hazard.
INFORMATIONAL = frozenset({
    "RWT",   # Required Weekly Test
    "RMT",   # Required Monthly Test
    "NPT",   # National Periodic Test
    "NST",   # National Silent Test
    "DMO",   # Practice / Demo
    "ADR",   # Administrative Message
    "SPS",   # Special Weather Statement
    "SVS",   # Severe Weather Statement
    "MWS",   # Marine Weather Statement
    "NMN",   # Network Message Notification
    "NIC",   # National Information Center
    "EAT",   # Emergency Action Termination
})


def warning_type(eee):
    """Catalog type id for a SAME event code, or None when informational.

    None means "log it, do not plot it, do not speak it as a hazard".
    """
    code = (eee or "").strip().upper()
    if code in INFORMATIONAL:
        return None
    return EVENT_TYPE.get(code, FALLBACK_TYPE)
