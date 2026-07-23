# OASIS AI Assistant

Offline local-LLM assistant. It answers questions about OASIS live data by
driving a small local model through the OASIS API, exposed as an MCP server.

## Pieces
- `server/mcp_server.py` — MCP server (read tools; self-loops to OASIS API).
- `orchestrator/` — Flask host: MCP runtime bridge, model client, tool loop, SSE routes.
- `web/` — minimal standalone chat page at `/assistant`.
- `config.json` — model endpoint, OASIS API base, loop limits.
- `models/` — GGUF model files (gitignored; Plan 2 stages these).

## Dev run (Mac/Linux)
1. `pip install mcp httpx` into the venv (Plan 2 vendors these wheels).
2. Start a model on the endpoint in `config.json` (e.g. a local llama-server
   exposing `http://127.0.0.1:8087/v1`). Without a model, `/assistant` loads and
   `/api/assistant/health` reports `mcp_ready`, but chat needs the model up.
3. `./start.sh`, open `http://localhost:8083/assistant`.

The MCP server subprocess is spawned automatically by the Flask host on first
chat request; you do not run it by hand (though `python -m ai.server.mcp_server`
works for an external MCP client over stdio).

## Self-loop
Flask host → MCP subprocess → OASIS REST API (localhost:8083). Intentional and
safe under multiple gunicorn workers — do not remove the HTTP hop.
