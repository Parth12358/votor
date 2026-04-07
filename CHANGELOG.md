# Changelog

## [Unreleased]

### Added
- Dashboard rebuild: Full rewrite of `dashboard.py` — FastAPI + WebSocket (`/ws/dashboard`), REST endpoints for analytics/status/history/diff/undo/revert/config/side-chat, `start_dashboard()` daemon thread launcher, `broadcast_sync()` using captured `_event_loop`, thread-safe client list, uvicorn on `0.0.0.0`
- Dashboard launch on startup: `repl.py` starts dashboard server at launch, prints clickable link; `handle_dashboard()` reuses running server
- Dashboard WS reconnect: `connectWS` tries `localhost:8000` then `127.0.0.1:8000`
- Dashboard terminal parity: Terminal shows `dashboard ❯ query`, sub/main status lines, same panel/footer as REPL queries, prompt reprinted after response. `_BroadcastConsole` wraps `qmod.console` — terminal output unchanged, browser gets `log` events for every `console.print` line
- Dashboard browser log events: `_BroadcastConsole` strips Rich markup and broadcasts `{"type": "log", "html": plain}` — browser chat now shows `sub → classify`, `main → call 1/2` etc. in real time
- Dashboard busy lock: `_query_lock` prevents terminal and dashboard from running concurrent queries — second request gets an error event immediately
- Dashboard index progress: `index_project()` accepts `on_progress` callback; `_run_index` passes a lambda that emits `index_progress` events — browser progress bar now advances during indexing
- Dashboard analytics fix: Frontend read `q.created_at` but DB column is `timestamp` — fixed to `q.timestamp`, Plotly charts now receive real data
- Dashboard markdown rendering: `marked.js` added to browser UI; streaming tokens accumulated in `S.curRaw`, rendered via `marked.parse()` on `query_complete`; streaming shows single replacing `.stream-raw` span, finalize swaps it for rendered HTML
- Dashboard streaming tokens: `votor/events.py` shim added; `broadcast_sync` registered at dashboard startup; `_stream_to_console` headless branch broadcasts `{"type":"token","token":chunk}` per chunk
- Dashboard edit mode progress: `run_edit_mode` broadcasts `step_progress` before each step and `diff` after each successful edit/create; commit line already forwarded via `_BroadcastConsole`
- Multi-file edit support: `MAX_FILE_REQUEST_ROUNDS` raised 3→5; `write_plan_prompt` updated: batch `need_files` requests, explicit dependency ordering rule, example shows multiple paths

### Changed
- Updated README with hybrid config, ollama models, accurate install instructions
- Updated `votor.egg-info` added to `exclude_dirs` in `DEFAULT_CONFIG` (`init_flow.py`) and default config in `indexer.py`
- Updated pyproject keywords from `chromadb` to `qdrant`, `ollama` to reflect actual stack
- Added `close_client()` to `db.py`; called before `index_project()` in auto-update block so Qdrant lock is released before re-indexing
- Cleaned up debug prints from `[votor dashboard]`, `[votor run_query]`, `[votor headless]`, and `sub raw:` in `dashboard.py` and `query.py`.
