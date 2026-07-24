"""Declarative read-tool registry for the OASIS MCP server.

Each ReadTool is a thin GET to the OASIS REST API. No-arg tools take nothing;
parameterised tools (fcc_lookup) declare JSON-schema properties that map 1:1
onto the query string. Adding a read endpoint = one row here.
"""
import inspect
from collections import namedtuple

from ai.server.tools.http import oasis_get

ReadTool = namedtuple("ReadTool", "name path description params")

READ_TOOLS = [
    ReadTool("aprs_stations", "/api/aprs/stations",
             "List APRS stations heard recently (call sign, last-heard, position, speed, comment).",
             {}),
    ReadTool("aprs_track", "/api/aprs/track",
             "Position history for one APRS station. Args: callsign, minutes.",
             {"callsign": {"type": "string", "description": "Station call sign, e.g. W4MHI-9"},
              "minutes": {"type": "integer", "description": "Look-back window in minutes"}}),
    ReadTool("adsb_aircraft", "/api/adsb/aircraft",
             "Aircraft currently decoded on 1090 MHz (flight, altitude, position, squawk).",
             {}),
    ReadTool("adsb_alerts", "/api/adsb/alerts",
             "Active ADS-B alerts: emergency squawks (7500/7600/7700) and station-proximity hits.",
             {}),
    ReadTool("satellite_passes", "/api/satellites/passes",
             "Upcoming satellite passes for the station: sat name, AOS/LOS times, peak elevation.",
             {}),
    ReadTool("fcc_lookup", "/api/lookup",
             "Look up a US amateur-radio licensee by exact call sign.",
             {"callsign": {"type": "string", "description": "Full call sign, e.g. W4MHI"}}),
    ReadTool("system_health", "/api/health",
             "Station system health: CPU, RAM, disk, temperature, uptime, services.",
             {}),
]


_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _tool_description(spec):
    """Tool description plus a Parameters block so the model sees each arg's meaning."""
    if not spec.params:
        return spec.description
    lines = [spec.description, "Parameters:"]
    for name, schema in spec.params.items():
        desc = schema.get("description", "")
        lines.append(f"- {name}: {desc}".rstrip())
    return "\n".join(lines)


def _make_tool_fn(spec, cfg):
    param_names = list(spec.params.keys())

    def _fn(**kwargs):
        params = {k: kwargs[k] for k in param_names if kwargs.get(k) not in (None, "")}
        return oasis_get(spec.path, params, base=cfg.oasis_api_base,
                         timeout=cfg.request_timeout_s)

    _fn.__name__ = spec.name
    _fn.__doc__ = spec.description
    # Give FastMCP a typed signature (honest types) so it can build the input schema.
    _fn.__signature__ = inspect.Signature([
        inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=None,
                          annotation=_TYPE_MAP.get(spec.params[name].get("type"), str))
        for name in param_names
    ])
    return _fn


def register(mcp, cfg):
    for spec in READ_TOOLS:
        mcp.add_tool(_make_tool_fn(spec, cfg), name=spec.name,
                     description=_tool_description(spec))
