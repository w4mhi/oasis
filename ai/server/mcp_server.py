"""OASIS MCP server — exposes station data as tools over MCP (stdio by default).

Standalone and reusable: an external MCP client (e.g. Claude Desktop, when the
box is online) can attach to the same server. Run: python -m ai.server.mcp_server
"""
import os
import sys

# Make the suite root importable when spawned as a subprocess (mirrors app.py).
_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SUITE_ROOT not in sys.path:
    sys.path.insert(0, _SUITE_ROOT)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from ai import config  # noqa: E402
from ai.server.tools import read_tools  # noqa: E402


def build_server(cfg=None):
    cfg = cfg or config.load()
    mcp = FastMCP("oasis")
    read_tools.register(mcp, cfg)
    return mcp


def main():
    build_server().run()  # stdio transport


if __name__ == "__main__":
    main()
