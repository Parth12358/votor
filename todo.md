# votor — Remaining Work Report

---

## Completed
1. /forget command — wipe all conversation chunks without touching code index
2. Cross-session memory vs session-only memory
3. Whether conversation chunks should be excluded from edit mode context
| Read path hardening | Path traversal, size limits, encoding warning, dedup cache, truncation |
| Structural chunking | pygments-based boundary chunking replacing word-count |
| Sub agent (read) | Intent classification, tool execution, prompts externalized |
| providers.py fixes | Ollama model validation, embedding provider config field |
| Performance | Client caching, embedding batching, context token cache |
| Streaming | Main model streams tokens in real time, `/thinking` toggle |
| Edit mode | Line-range edits, verification phase, batch commit, VS Code M indicator |
| Edit mode bugfixes | tool_call tag stripping, create→edit fallback, path normalization, delete guard |
| 2 Chunk rewrite (Option B) | Large | Low | Shelved |
| 5 Step mode | Medium | High | Open |
| 5 Step mode | Medium | High | Open |
| 2 Chunk rewrite (Option B) | Large | Low | Shelved |
| init_flow | Ollama free-text model input, `write_prompts()` generates prompts.json on init |
| PyPI / GitHub | Workflow for automated releases documented |
| .gitignore | `.vectormind/` fully ignored |
| README | Updated with hybrid config, ollama models, accurate install instructions |
| 1a egg-info exclude | `votor.egg-info` added to `exclude_dirs` in `DEFAULT_CONFIG` (`init_flow.py`) and default config in `indexer.py` |
| 1b pyproject keywords | Updated from `chromadb` to `qdrant`, `ollama` to reflect actual stack |
| 1c Qdrant concurrent access | `close_client()` added to `db.py`; called before `index_project()` in auto-update block so Qdrant lock is released before re-indexing |
| 1d Debug print cleanup | All `[votor dashboard]`, `[votor run_query]`, `[votor headless]`, and `sub raw:` debug prints removed from `dashboard.py` and `query.py` |
| Dashboard rebuild | Full rewrite of `dashboard.py` — FastAPI + WebSocket (`/ws/dashboard`), REST endpoints for analytics/status/history/diff/undo/revert/config/side-chat, `start_dashboard()` daemon thread launcher, `broadcast_sync()` using captured `_event_loop`, thread-safe client list, uvicorn on `0.0.0.0` |
| Dashboard launch on startup | `repl.py` starts dashboard server at launch, prints clickable link; `handle_dashboard()` reuses running server |
| Dashboard WS reconnect | `connectWS` tries `localhost:8000` then `127.0.0.1:8000` |
| Dashboard terminal parity | Terminal shows `dashboard ❯ query`, sub/main status lines, same panel/footer as REPL queries, prompt reprinted after response. `_BroadcastConsole` wraps `qmod.console` — terminal output unchanged, browser gets `log` events for every `console.print` line |
| Dashboard browser log events | `_BroadcastConsole` strips Rich markup and broadcasts `{"type": "log", "html": plain}` — browser chat now shows `sub → classify`, `main → call 1/2` etc. in real time |
| Dashboard busy lock | `_query_lock` prevents terminal and dashboard from running concurrent queries — second request gets an error event immediately |
| Dashboard index progress | `index_project()` accepts `on_progress` callback; `_run_index` passes a lambda that emits `index_progress` events — browser progress bar now advances during indexing |
| Dashboard analytics fix | Frontend read `q.created_at` but DB column is `timestamp` — fixed to `q.timestamp`, Plotly charts now receive real data |
| Dashboard markdown rendering | `marked.js` added to browser UI; streaming tokens accumulated in `S.curRaw`, rendered via `marked.parse()` on `query_complete`; streaming shows single replacing `.stream-raw` span, finalize swaps it for rendered HTML |
| Dashboard streaming tokens | `votor/events.py` shim added; `broadcast_sync` registered at dashboard startup; `_stream_to_console` headless branch broadcasts `{"type":"token","token":chunk}` per chunk |
| Dashboard edit mode progress | `run_edit_mode` broadcasts `step_progress` before each step and `diff` after each successful edit/create; commit line already forwarded via `_BroadcastConsole` |
| Multi-file edit support | `MAX_FILE_REQUEST_ROUNDS` raised 3→5; `write_plan_prompt` updated: batch `need_files` requests, explicit dependency ordering rule, example shows multiple paths |

---

## Remaining — In Priority Order

---

### ~~0. UI/UX Redesign~~ ✓ Done

`/init` wizard, REPL prompt, response panel, `/status`/`/history`/`/config` views, color language, and spinner consistency all polished. Scope was `repl.py` and `init_flow.py`.

---

### ~~0b. Dashboard~~ ✓ Done

- ✓ Markdown rendering — `marked.js`, `S.curRaw` accumulator, rendered on `query_complete`
- ✓ Streaming tokens — `events.py` shim, `_stream_to_console` headless branch broadcasts `token` events per chunk
- ✓ Edit mode progress — `step_progress` + `diff` events broadcast from `run_edit_mode()`
- ✓ Terminal mirror — removed from `index.html`; `_BroadcastConsole` terminal_output broadcasts also removed as no longer needed

---

### 1. Reason Mode

New write mode variant where main can course-correct between steps.

**Flow:**
```
sub reads files → main decides first action → sub executes one step
→ main reviews result → decides next action → repeat up to write_max_calls
→ main final summary
```

**Config:** `write_mode: "reason"`, `write_max_calls: 4`

**Scope:** New `run_reason_mode()` in `query.py`. Routed from `run_query()` when `write_mode == "reason"`.

---

### 2. Option B — Chunk Rewrite at Index Time

Shelved — needs more planning.

**The idea:** Sub rewrites raw code chunks into natural language descriptions before embedding. Improves retrieval for plain-English queries against code.

**Status:** Stub exists in `chunker.py` as commented `summarize_chunks()`. Leave until retrieval quality is measured.

---

### 3. Conversation Memory

**The idea:** Embed each exchange into Qdrant with `type: conversation` metadata. Retrieve relevant past exchanges alongside code chunks on future queries.

**Config:**
```json
"conversation_memory": false,
"top_k_conversations": 3
```

**Scope:** `query.py`, `db.py`, new `/forget` command in `repl.py`.

---

### ~~4. Multi-File Edit Support~~ ✓ Done

`MAX_FILE_REQUEST_ROUNDS` raised 3→5 in `query.py`. `write_plan_prompt` updated in `init_flow.py` and `.vectormind/prompts.json`: explicit dependency ordering rule, batch `need_files` requests, example shows multiple paths.

---

### 5. Step Mode — Interactive Todo List

Main generates a full plan once, presents it as a persistent checklist. User drives execution step by step.

**Commands:** `run N`, `skip N`, `edit N`, `done`, `list`

**Config:** `write_mode: "step"`

**Scope:** New `run_step_mode()` in `query.py`, new `step ❯` sub-loop in `repl.py`.

---

### 6. Watch Mode

Auto `/update` on file save using `watchdog` (already in dependencies). 2s debounce, prints `↺ updated: filename.py`.

**Config:** `"watch_mode": false` — `/watch` to toggle

---

### 7. Parallel Client Support

Two votor terminals in the same project crash on Qdrant (`Storage folder already accessed by another instance`).

**Options:** request queue, Qdrant server mode, or file locking.

**Config:** `"qdrant_mode": "embedded"` or `"server"`

---

### 8. File Tree — Index, Context, and Dashboard

Build and persist a project file tree on every index. Inject it into LLM context so main knows exact file paths without guessing. Show it in the dashboard sidebar.

**Files to change:**

| File | What changes |
|---|---|
| `indexer.py` | `build_file_tree()`, `save_file_tree()`, `load_file_tree()`, `tree_to_string()` — called at end of `index_project()` |
| `tools.py` | `_update_tree_for_file()` — called after every `create_file`, `edit_file_lines`, `delete_file` so the tree stays in sync without a full rebuild |
| `query.py` | Tree injected as `## Project File Tree` block into main LLM messages in `run_query()` and `run_edit_mode()` |
| `dashboard.py` | `GET /api/tree` endpoint — returns `load_file_tree()` |
| `static/index.html` | Tree sidebar panel below nav, `loadTree()` + `handleTreeFileClick()`, called on `init` and `index_complete` events |

**`build_file_tree()`** — walks project from `.`, respects `exclude_dirs` and `extensions` from config, produces nested dict where leaves are `None` and dirs are dicts. Hidden files skipped except `.env.example`. Empty dirs pruned.

**`_update_tree_for_file(path, deleted)`** — incremental tree patch after a single file change. Loads `file_tree.json`, walks to the leaf, adds or removes it, saves back. Wrapped in `try/except` — never blocks a file operation.

**`tree_to_string()`** — converts nested dict to compact indented string for LLM context. Dirs first, then files, both sorted alphabetically.

**LLM context injection** — `tree_block` prepended to the user message in both `run_query()` and `run_edit_mode()`:
```
## Project File Tree

```
votor/
  dashboard.py
  query.py
  ...
```

## Context
...
```

**Dashboard sidebar** — collapsible file tree below nav. Dirs show a `▸` toggle, files are clickable and pre-fill the chat input with `read <filename>` and switch to the chat panel. File count shown in header. Refreshes on `init` and `index_complete` events.

---

## Summary Table

| Item | Effort | Priority | Status |
|---|---|---|---|
| 0 UI/UX redesign | Large | High — affects every interaction | ✓ Done |
| 0b Dashboard | Large | High — browser gaps remain | ✓ Done |
| 1a egg-info exclude | Trivial | — | ✓ Done |
| 1b pyproject keywords | Trivial | — | ✓ Done |
| 1c Qdrant concurrent access | Small | — | ✓ Done |
| 1d debug print cleanup | Trivial | — | ✓ Done |
| 1 Reason mode | Medium | Medium | Open |
| 2 Chunk rewrite (Option B) | Large | Low | Shelved |
| 3 Conversation memory | Medium | Medium | Open |
| 4 Multi-file edit support | Medium | High | ✓ Done |
| 5 Step mode | Medium | High | Open |
| 6 Watch mode | Small | Low | Open |
| 7 Parallel client support | Medium | Low | Open |
| 8 File tree | Medium | Low | Open |
