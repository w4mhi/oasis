"""Bridge the async MCP client into sync Flask.

A dedicated background thread runs one asyncio loop that owns a persistent
stdio ClientSession to the MCP server subprocess. The session is opened AND
closed inside a single owning coroutine (_main) so anyio's task-affine cancel
scope (inside stdio_client) is entered and exited on the SAME task. Flask
request threads submit method-call coroutines (list_tools/call_tool) to the
loop via run_coroutine_threadsafe — those only USE the already-open session's
streams, which is safe across tasks. Keeps the subprocess warm across requests
(no per-request spawn) with no event loop in the request thread.
"""
import asyncio
import json
import os
import sys
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ai import config

_CALL_TIMEOUT = 30
_START_TIMEOUT = 30

# Suite root (…/ai/orchestrator/mcp_runtime.py → up two). The MCP server is
# spawned as `python -m ai.server.mcp_server`, and the `ai` package is NOT
# pip-installed, so it only imports when the suite root is the process CWD
# (`python -m` prepends CWD to sys.path). gunicorn runs its worker with CWD set
# to server/, where `import ai` fails and the subprocess dies — which surfaces
# as an anyio ExceptionGroup out of stdio_client and "MCP not ready". Pinning
# the child's cwd here makes the spawn independent of the parent's cwd.
_SUITE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class AssistantRuntime:
    def __init__(self, server_cmd, server_args, call_timeout=_CALL_TIMEOUT, cwd=None):
        self._params = StdioServerParameters(
            command=server_cmd, args=list(server_args), cwd=cwd or _SUITE_ROOT)
        self._call_timeout = call_timeout
        self._loop = asyncio.new_event_loop()
        self._session = None
        self._stop = None            # asyncio.Event, created on the loop thread
        self._error = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=_START_TIMEOUT):
            raise RuntimeError("MCP runtime did not start within %ss" % _START_TIMEOUT)
        if self._error is not None:
            raise self._error

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self):
        # Open and close the session in THIS one task so anyio's cancel scope
        # (inside stdio_client) is entered and exited on the same task.
        self._stop = asyncio.Event()
        try:
            async with stdio_client(self._params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    await self._stop.wait()
        except Exception as exc:  # noqa: BLE001 - surfaced to __init__
            self._error = exc
            self._ready.set()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=self._call_timeout)

    def list_tools(self):
        resp = self._submit(self._session.list_tools())
        out = []
        for t in resp.tools:
            out.append({"type": "function", "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            }})
        return out

    def call_tool(self, name, arguments):
        resp = self._submit(self._session.call_tool(name, arguments or {}))
        parts = [c.text for c in resp.content if getattr(c, "type", None) == "text"]
        return "\n".join(parts) if parts else json.dumps({"ok": True})

    def list_prompts(self):
        resp = self._submit(self._session.list_prompts())
        return [{"name": p.name, "title": p.description or p.name} for p in resp.prompts]

    def get_prompt(self, name):
        resp = self._submit(self._session.get_prompt(name, {}))
        parts = [m.content.text for m in resp.messages
                 if getattr(m.content, "type", None) == "text"]
        return "\n".join(parts)

    def close(self):
        # Signal the owning task to exit its async-with blocks (same task that
        # entered them), then wait for it to unwind and close the loop. Closing
        # the loop also makes any post-close call_tool/list_tools fail fast.
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=self._call_timeout)
        if not self._loop.is_closed():
            self._loop.close()


_runtime = None
_runtime_lock = threading.Lock()


def default_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            cfg = config.load()
            _runtime = AssistantRuntime(sys.executable, ["-m", "ai.server.mcp_server"],
                                        call_timeout=cfg.request_timeout_s + 10)
        return _runtime
