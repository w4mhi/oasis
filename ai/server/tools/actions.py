"""Write-action MCP tools (POST self-loops to the OASIS API).

These EXECUTE when called — the confirm gate that protects them lives in the
orchestrator (ai/orchestrator/gate.py), which intercepts action-tool calls
before they reach here. Their names must match config.action_tools.
"""
from ai.server.tools.http import oasis_post

ACTION_TOOL_NAMES = ("service_control", "aprs_post_warning", "satellite_monitor")

_CFG = None


def _post(path, body, headers=None):
    return oasis_post(path, body, base=_CFG.oasis_api_base,
                      timeout=_CFG.request_timeout_s, headers=headers)


def service_control(unit: str, action: str) -> str:
    """Start, stop, or restart an OASIS service (e.g. graywolf, oasis-ai, kiwix).
    action must be one of: start, stop, restart."""
    return _post("/api/service", {"unit": unit, "action": action},
                 headers={"X-OASIS-Request": "1"})


def aprs_post_warning(lat: float, lon: float, warning_type: str, note: str = "") -> str:
    """Post an APRS hazard/warning object at a location. THIS TRANSMITS on the
    APRS network. lat/lon in decimal degrees; warning_type is a short label."""
    return _post("/api/aprs/warnings",
                 {"lat": lat, "lon": lon, "type": warning_type, "note": note})


def satellite_monitor(norad: int, monitor: bool) -> str:
    """Add or remove a satellite (by NORAD id) from the monitored set."""
    return _post("/api/satellites/select", {"norad": norad, "selected": monitor})


_TOOLS = {
    "service_control": service_control,
    "aprs_post_warning": aprs_post_warning,
    "satellite_monitor": satellite_monitor,
}


def register(mcp, cfg):
    global _CFG
    _CFG = cfg
    for name, fn in _TOOLS.items():
        mcp.add_tool(fn, name=name, description=fn.__doc__)
