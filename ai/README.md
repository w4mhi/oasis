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
- Installs `mcp` + `httpx` wheels into the system interpreter
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
