# votor — Remaining Work Report

---

## Completed

| Area | What was done |
|---|---|
| Read path hardening | Path traversal, size limits, encoding warning, dedup cache, truncation |
| Structural chunking | pygments-based boundary chunking replacing word-count |
| Sub agent (read) | Intent classification, tool execution, prompts externalized |
| providers.py fixes | Ollama model validation, embedding provider config field |
| Performance | Client caching, embedding batching, context token cache |
| Streaming | Main model streams tokens in real time, `/thinking` toggle |
| Edit mode | Line-range edits, verification phase, batch commit, VS Code M indicator |
| Edit mode bugfixes | tool_call tag stripping, create→edit fallback, path normalization, delete guard |
| Edit mode progress bar | Rich `Progress` bar replaces inline prints — description updates per step, diffs and errors print inline below bar, delete confirmation pauses bar |
| Git author | votor commits show `votor <votor@local>` as author |
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

---

## Remaining — In Priority Order

---

### 0. UI/UX Redesign

The core is functional. Every display surface still needs a polish pass.

**`/init` wizard**
- Current: raw prompt_toolkit radio lists with minimal styling
- Goal: polished step-by-step wizard with section headers, progress indicator (step 1/5), inline validation, summary before confirming

**REPL prompt**
- Current: `project ❯ votor` — `votor` appears twice
- Goal: cleaner prompt, maybe just `project ❯ ` with a subtle model indicator

**Response panel**
- Current: large bordered panel, dense footer metrics line
- Goal: tighter layout, metrics easier to scan, sources inline or collapsible

**`/status`, `/history`, `/config`**
- Current: plain tables and key-value dumps
- Goal: grouped sections, index health indicator, inline diff preview on history

**General**
- Consistent color language — success, warning, error, dim
- Spinner consistency — some steps have spinners, others don't

**Scope:** Primarily `repl.py` and `init_flow.py`. No logic changes — purely display layer.

---

### 0b. Dashboard — Remaining Gaps

Backend fully rewritten and working. Terminal parity complete. Browser receives query progress via `log` events, answer via `query_complete`, index progress via `index_progress`, analytics charts populated.

**Still open:**

**Streaming tokens to browser**
Queries complete and the answer appears at once via `query_complete.answer`. Token-by-token streaming requires `_stream_to_console` to emit `{"type": "token", "content": chunk}` broadcasts when `_is_headless()`. Small change to `query.py` — import and call `broadcast_sync` from within the headless stream-consume loop.
> Markdown rendering of the final answer is now working — `marked.js` renders `S.curRaw` on `query_complete`.

**Edit mode progress in browser**
Edit mode runs entirely in the terminal — `step_progress`, `diff`, and commit events are never broadcast. Requires emitting these from `run_edit_mode()` in `query.py`. Medium scope.

**Terminal mirror panel**
The browser has a terminal mirror strip that expects `terminal_output` events. Nothing emits them. Deferred to event bus refactor.

---

### 0b Implementation Plan

#### Step 1 — Streaming tokens (`query.py`, small)

`_stream_to_console()` currently consumes the stream silently when `_is_headless()`. Change the headless branch to broadcast each chunk as a `token` event:

```python
# query.py — _stream_to_console(), headless branch
from votor.dashboard import broadcast_sync   # add at top of file

if _is_headless():
    for chunk in stream_gen:
        if isinstance(chunk, str):
            full_content += chunk
            broadcast_sync({"type": "token", "token": chunk})   # ← add
        else:
            result = chunk
    ...
```

Frontend already handles `token` events via `appendTok`. No frontend changes needed.

> **Circular import risk:** `query.py` is imported by `dashboard.py`. Fix: move `broadcast_sync` import inside the function body, or expose it via a thin `events.py` shim (no-op when dashboard is not running).

---

#### Step 2 — Edit mode progress (`query.py`, medium)

`run_edit_mode()` drives a Rich progress bar and calls `console.print` for diffs and commits. `_BroadcastConsole` already forwards `console.print` as `log` events, so text lines arrive in the browser. What's missing: structured `step_progress` and `diff` events for the browser's progress bar and diff viewer.

**2a — step_progress events**

In the step-execution loop (around line 755), after updating the progress bar description, add:

```python
broadcast_sync({
    "type": "step_progress",
    "current": step_idx + 1,
    "total": len(steps),
    "action": action,
    "file": file_path,
})
```

Frontend's `appendStep()` already renders these as a progress bar. No frontend changes needed.

**2b — diff events**

After `show_diff()` calls (lines 780-783, 803-806), add:

```python
broadcast_sync({
    "type": "diff",
    "title": f"{action}: {file_path}",
    "diff": diff_text,
})
```

Frontend's `appendDiff()` already renders these. No frontend changes needed.

**2c — commit event**

After the commit line (line 865), add:

```python
broadcast_sync({
    "type": "log",
    "html": f'<span class="g">✓ committed {n} file(s)</span>',
})
```

Already handled by `log` events — no new event type needed.

---

#### Step 3 — Circular import fix (`events.py`, small)

Create `votor/events.py` as a thin shim so `query.py` never imports from `dashboard.py` directly:

```python
# votor/events.py
_broadcast_fn = None

def register(fn):
    global _broadcast_fn
    _broadcast_fn = fn

def broadcast(event: dict):
    if _broadcast_fn:
        _broadcast_fn(event)
```

In `dashboard.py` startup:

```python
import votor.events as _events
_events.register(broadcast_sync)
```

In `query.py`:

```python
from votor.events import broadcast
# replace broadcast_sync(...) calls with broadcast(...)
```

When running from the REPL (no dashboard), `_broadcast_fn` is `None` and all calls are no-ops.

---

#### Step 4 — Terminal mirror panel (deferred)

The browser has a terminal mirror strip expecting `terminal_output` events. Requires the full event bus (item 1) to capture REPL-initiated terminal output. Defer until Step 3's `events.py` shim is in place and proven.

---

#### Effort summary

| Sub-task | File(s) | Effort |
|---|---|---|
| Streaming tokens | `query.py` | 1–2 hours |
| Circular import shim | `events.py`, `dashboard.py`, `query.py` | 1 hour |
| step_progress events | `query.py` | 1 hour |
| diff events | `query.py` | 1 hour |
| Terminal mirror | deferred | — |

---

### 1. Event Bus (full terminal↔dashboard mirroring)

For true real-time mirroring of terminal output in the browser, every `console.print` needs to emit an event that both frontends receive. The `_BroadcastConsole` approach covers dashboard-initiated queries. Terminal-initiated queries (from the REPL) still don't appear in the browser at all.

**Files needed:**
- `votor/events.py` — event bus, subscriber registry
- Refactor `query.py` — replace `console.print` with `emit_event()`
- Refactor `repl.py` — subscribe to events, print to terminal
- Update `dashboard.py` — subscribe to events, broadcast to WebSocket

Large architectural change. Defer until streaming tokens and edit mode progress are working first.

---

### 2. Reason Mode

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

### 3. Option B — Chunk Rewrite at Index Time

Shelved — needs more planning.

**The idea:** Sub rewrites raw code chunks into natural language descriptions before embedding. Improves retrieval for plain-English queries against code.

**Status:** Stub exists in `chunker.py` as commented `summarize_chunks()`. Leave until retrieval quality is measured.

---

### 4. Conversation Memory

**The idea:** Embed each exchange into Qdrant with `type: conversation` metadata. Retrieve relevant past exchanges alongside code chunks on future queries.

**Config:**
```json
"conversation_memory": false,
"top_k_conversations": 3
```

**Scope:** `query.py`, `db.py`, new `/forget` command in `repl.py`.

---

### 5. Multi-File Edit Support

Raise `max_file_request_rounds` cap and strengthen `write_plan_prompt` guidance for cross-file dependency ordering.

**Config:** `"max_file_request_rounds": 5`

---

### 6. Step Mode — Interactive Todo List

Main generates a full plan once, presents it as a persistent checklist. User drives execution step by step.

**Commands:** `run N`, `skip N`, `edit N`, `done`, `list`

**Config:** `write_mode: "step"`

**Scope:** New `run_step_mode()` in `query.py`, new `step ❯` sub-loop in `repl.py`.

---

### 7. Watch Mode

Auto `/update` on file save using `watchdog` (already in dependencies). 2s debounce, prints `↺ updated: filename.py`.

**Config:** `"watch_mode": false` — `/watch` to toggle

---

### 8. Parallel Client Support

Two votor terminals in the same project crash on Qdrant (`Storage folder already accessed by another instance`).

**Options:** request queue, Qdrant server mode, or file locking.

**Config:** `"qdrant_mode": "embedded"` or `"server"`

---

### 9. File Tree — Index, Context, and Dashboard

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
| 0 UI/UX redesign | Large | High — affects every interaction | Partial — edit mode progress bar done |
| 0b Dashboard | Large | High — browser gaps remain | Partial — markdown ✓, streaming ✓, edit mode progress ✓, terminal mirror open |
| 1a egg-info exclude | Trivial | — | ✓ Done |
| 1b pyproject keywords | Trivial | — | ✓ Done |
| 1c Qdrant concurrent access | Small | — | ✓ Done |
| 1d debug print cleanup | Trivial | — | ✓ Done |
| 1 Event bus (full mirroring) | Large | Medium — needed for true parity | Open |
| 2 Reason mode | Medium | Medium | Open |
| 3 Chunk rewrite (Option B) | Large | Low | Shelved |
| 4 Conversation memory | Medium | Medium | Open |
| 5 Multi-file edit support | Medium | High | Open |
| 6 Step mode | Medium | High | Open |
| 7 Watch mode | Small | Low | Open |
| 8 Parallel client support | Medium | Low | Open |
| 9 File tree | Medium | Low | Open |
