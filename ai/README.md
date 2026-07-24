# OASIS AI Assistant

Offline local-LLM assistant. It answers questions about OASIS live data by
driving a small local model through the OASIS API, exposed as an MCP server.

## Pieces
- `server/mcp_server.py` — MCP server (read tools; self-loops to OASIS API).
- `orchestrator/` — Flask host: MCP runtime bridge, model client, tool loop, SSE routes.
- `web/` — minimal standalone chat page at `/assistant`.
- `config.json` — model endpoint, OASIS API base, loop limits.
- `models/` — GGUF model files (gitignored; Plan 2 stages these).

## Architecture

The assistant is **three cooperating processes**, not one. Its most surprising
property — and the usual first question — is that the MCP layer contains **no API
implementation of its own.** That is deliberate: OASIS already *has* a REST API
(the Flask blueprints: `/api/aprs/…`, `/api/adsb/…`, `/api/lookup`, `/api/health`,
…). The MCP server is a thin **adapter** that re-exposes those existing endpoints
to the local model. Each tool is ~one line of real work — an HTTP call back into
OASIS's own API on localhost (the "self-loop").

### What FastMCP does — and does not — do

FastMCP is **not** a web framework and defines **no routes**, which is why
`server/mcp_server.py` looks empty of anything API-shaped. Its entire job is:

1. **Protocol.** It speaks MCP over **stdio** (`build_server().run()` → stdio
   transport). There is no port, no HTTP server, no OpenAPI here.
2. **Schema generation.** You hand it a plain Python callable
   (`mcp.add_tool(fn, name=…, description=…)`) and it derives the JSON input
   schema the model sees from the function's **typed signature**. That is the
   trick in `server/tools/read_tools.py` — `_fn.__signature__` is synthesized so
   FastMCP emits honest argument types (`callsign: str`, `minutes: int`).

The *implementation* of each tool lives in the `server/tools/` modules, and it is
just glue: `read_tools.py` → `oasis_get(path)` → `httpx.GET http://127.0.0.1:8083/api/…`.
Adding a new read capability is literally one row in `READ_TOOLS`.

### The three processes

| Process | What it is | Endpoint |
|---------|------------|----------|
| **Flask host** | The OASIS web app *and* the assistant orchestrator (`ai/orchestrator/`). Holds the real REST API. | `:8083` |
| **llama-server** | The local LLM, run as the `oasis-ai` systemd daemon. OpenAI-compatible. | `:8087/v1` |
| **MCP server** | `python -m ai.server.mcp_server`, spawned by the host as a **stdio subprocess** and kept warm. Exposes OASIS's API as MCP tools. | stdio |

### Request lifecycle

```
Browser  (/assistant page, ai/web/)
   │  POST /api/assistant/chat   { message }
   ▼
┌───────────────────────────────────────────────────────────────────────┐
│ Flask host  (gunicorn, :8083)            ai/orchestrator/              │
│                                                                       │
│   routes.py ──▶ loop.py   agentic loop, ≤ max_tool_iterations (5)      │
│                   │                                                    │
│        ┌──────────┴───────────┐                                       │
│        ▼                      ▼                                       │
│   model_client.py        mcp_runtime.py                               │
│   (chat completions)     (persistent stdio ClientSession on a         │
│        │                  background asyncio loop; warm subprocess)   │
│        │                      │   list_tools / call_tool  (stdio)     │
└────────┼──────────────────────┼───────────────────────────────────────┘
         │                      │
         ▼                      ▼
   llama-server           MCP server subprocess
   :8087 /v1              FastMCP("oasis"), stdio transport
   (the local LLM)             │
                               │   each tool fn:
                               ▼
                       oasis_get / oasis_post   (server/tools/http.py)
                               │   HTTP  ── self-loop ──▶
                               ▼
                       OASIS REST API   :8083 /api/…
                       (the SAME Flask app — the real implementation)
```

1. The browser POSTs a message to `/api/assistant/chat` (`orchestrator/routes.py`).
2. `loop.py` runs the agentic loop: it asks the model (`model_client.py` → llama-server)
   for a reply, offering it the MCP tool list obtained from `mcp_runtime.py`.
3. If the model calls a tool, the host forwards it over stdio to the MCP subprocess
   (`call_tool`). The tool runs `oasis_get(...)` → hits OASIS's own Flask API on
   localhost → returns JSON text straight back to the model. Loop repeats up to
   `max_tool_iterations`, then the final answer streams back to the page.

### Read vs. write (the confirm gate)

Read tools (`read_tools.py`, `ref.py`) are unrestricted GETs. **Write** tools
(`actions.py` — `service_control`, `aprs_post_warning`, `satellite_monitor`) are
POSTs and would change station state, so they are intercepted by
`orchestrator/gate.py`: instead of executing, the call is parked in `pending.py`
and surfaced to the operator, who approves it via `POST /api/assistant/confirm`
before `oasis_post` fires. `config.json` controls this — `action_tools` lists the
gated tools, `actions_enabled` turns writes on/off, and `auto_actions` (empty by
default) is the allow-list of writes permitted to run *without* a confirm.

## Dev run (Mac/Linux)
1. `pip install mcp httpx` into the venv (Plan 2 vendors these wheels).
2. Start a model on the endpoint in `config.json` (e.g. a local llama-server
   exposing `http://127.0.0.1:8087/v1`). Without a model, `/assistant` loads and
   `/api/assistant/health` reports `mcp_ready`, but chat needs the model up.
3. `./start.sh`, open `http://localhost:8083/assistant`.

The MCP server subprocess is spawned automatically by the Flask host on the first
`/api/assistant/*` call (including the health check at page load); you do not run
it by hand (though `python -m ai.server.mcp_server` works for an external MCP
client over stdio).

## Self-loop
Flask host → MCP subprocess → OASIS REST API (localhost:8083). Intentional and
safe under multiple gunicorn workers — do not remove the HTTP hop.

## Pi install
Run `python3 ai/runtime/install.py` (or via `setup-oasis.py` → choose "AI assistant
(Pi 5 / 8 GB)"). The installer:
- Fetches the llama.cpp arm64 binary from GitHub releases
- Downloads the Qwen 2.5 3B q4_k_m GGUF model from Hugging Face
- Installs `mcp` + `httpx` wheels into the OASIS `.venv`
- Writes and enables the `oasis-ai` systemd unit (listens on 127.0.0.1:8087)

The installer self-gates to Pi 5 / Linux-arm64 / 8 GB RAM / Python ≥ 3.10; it
skips cleanly on other platforms. The dashboard AI Assistant card can Stop the
service to reclaim ~2 GB RAM; Start brings it back.

## Mac dev
The prebuilt llama.cpp binary is Linux-arm64 and won't run on macOS. For local
development on a Mac, install llama.cpp via Homebrew and run the model server by
hand:
```bash
brew install llama.cpp
llama-server --jinja -m <path/to/qwen-2.5-3b-q4_k_m.gguf> \
  --host 127.0.0.1 --port 8087
```
Then `./start.sh` and open `http://localhost:8083/assistant` to exercise the
Plan-1 MCP loop against `/api/assistant`.
