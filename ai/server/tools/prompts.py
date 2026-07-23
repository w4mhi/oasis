"""Load canned quick-action prompts from ai/server/prompts/*.md and register
them as MCP prompts on the FastMCP server (reusable by any MCP client).

Each file: a `# Title` first line + a body. The prompt's get_prompt returns a
single user message carrying the body. Adding a prompt = drop a new .md here.
Server-side only (imported by build_server, which runs in the MCP subprocess).
"""
import glob
import os

from mcp.server.fastmcp.prompts import Prompt

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROMPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "prompts"))


def load_prompts(directory):
    out = []
    for path in sorted(glob.glob(os.path.join(directory, "*.md"))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        name = os.path.splitext(os.path.basename(path))[0]
        lines = text.splitlines()
        title = name
        body_lines = lines
        if lines and lines[0].lstrip().startswith("#"):
            title = lines[0].lstrip("# ").strip() or name
            body_lines = lines[1:]
        body = "\n".join(body_lines).strip()
        out.append((name, title, body))
    return out


def _make_fn(body):
    def _fn() -> str:
        return body
    return _fn


def register(mcp, cfg=None):
    for name, title, body in load_prompts(_PROMPTS_DIR):
        fn = _make_fn(body)
        fn.__name__ = name.replace("-", "_")
        mcp.add_prompt(Prompt.from_function(fn, name=name, description=title))
