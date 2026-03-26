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
| Git author | votor commits show `votor <votor@local>` as author |
| init_flow | Ollama free-text model input, `write_prompts()` generates prompts.json on init |
| PyPI / GitHub | Workflow for automated releases documented |
| .gitignore | `.vectormind/` fully ignored |
| README | Updated with hybrid config, ollama models, accurate install instructions |
| Edit mode progress bar | Phase 3 execution loop replaced with Rich `Progress` bar — updates description per step, diffs print inline below bar, errors print inline, delete confirmation pauses bar (`query.py`) |
| 1a egg-info exclude | `votor.egg-info` added to `exclude_dirs` in `DEFAULT_CONFIG` (`init_flow.py`) and default config in `indexer.py` |
| 1c Qdrant concurrent access | `close_client()` added to `db.py` with module-level `_client` cache; called before `index_project()` in auto-update block so Qdrant lock is released before re-indexing (`query.py`, `db.py`) |
| Dashboard rebuild | Full rewrite of `dashboard.py` — FastAPI + WebSocket (`/ws/dashboard`), REST endpoints for analytics/status/history/diff/undo/revert/config/side-chat, `start_dashboard()` daemon thread launcher, `broadcast_sync()` using captured `_event_loop`, plain `threading.Thread` for query/index handlers, `access_log=False`, uvicorn on `0.0.0.0` |
| Dashboard launch on startup | `repl.py` starts dashboard server at launch, prints clickable `http://127.0.0.1:8000` link; `handle_dashboard()` reuses running server instead of spawning subprocess |
| Dashboard WS reconnect | `connectWS` in `index.html` tries `localhost:8000` then `127.0.0.1:8000`, logs connect/disconnect/error to browser console |
| Dashboard terminal parity | Terminal shows `dashboard ❯ query`, then sub/main status lines, then the same panel and metrics footer as REPL queries. Panel title shows `(dashboard / model)`. Prompt reprinted after response. Null console swap removed — `qmod.console` stays as real terminal so all `console.print` calls in `run_query()` flow through. `_set_headless(True)` still silences streamed tokens in `_stream_to_console`. |
| 1d Debug print cleanup | All `[votor dashboard]`, `[votor run_query]`, `[votor headless]`, and `sub raw:` debug prints removed from `dashboard.py` and `query.py`. |

---

## Remaining — In Priority Order

---

---

### 0. UI/UX Redesign — TOP PRIORITY

The current UI is functional but clunky. Every surface needs a pass — init wizard, REPL prompt, response panels, history, status, and config views.

**Areas to redesign:**

**`/init` wizard**
- Current: raw prompt_toolkit radio lists with minimal styling
- Issues: feels disconnected, no progress indication, no way to go back, ollama model selection is a plain text input with no guidance
- Goal: polished step-by-step wizard with clear section headers, progress indicator (step 1/5), inline validation, and clean summary before confirming

**REPL prompt**
- Current: `project ❯ votor` — redundant, `votor` appears twice since the binary is called `votor`
- Goal: cleaner prompt, maybe just `project ❯ ` with a subtle model indicator

**Response panel**
- Current: large bordered panel with markdown — takes up a lot of vertical space
- Issues: footer metrics line is dense and hard to scan, sources panel is a separate box
- Goal: tighter layout, metrics on one clean line, sources inline or collapsible

**`/status`**
- Current: plain key-value table in a panel
- Goal: visual layout with index health indicator, cost trend, most accessed files highlighted

**`/history`**
- Current: plain table of git commits
- Goal: richer view showing file changed, lines affected, time ago — with inline `/diff` preview on keypress

**`/config`**
- Current: flat key-value dump
- Goal: grouped sections (main agent, sub agent, embeddings, index settings) with current values highlighted

**Edit mode progress** ✓ DONE
- Rich `Progress` bar replaces inline prints — description updates per step, diffs and errors print inline below bar

**General**
- Consistent color language across all views — success, warning, error, dim
- Better use of whitespace — current panels are cramped
- Spinner consistency — some steps have spinners, others don't

**Scope:** Primarily `repl.py` and `init_flow.py`. No logic changes — purely display layer.

---

---

### 0b. Dashboard Redesign — PRIORITY 2 ✓ BACKEND DONE / FRONTEND PARTIAL

Backend fully rewritten — WebSocket, REST endpoints, daemon thread launcher, event loop capture, thread-safe broadcast. Dashboard starts on REPL launch with clickable link. WS reconnect logic tries localhost then 127.0.0.1.

**Remaining frontend gaps:**
- Analytics charts still rendering empty (API response format may need verification)
- Streaming chat responses not yet wired (query runs but doesn't stream tokens)
- Edit mode step progress not shown in dashboard chat view

**Full terminal parity — everything the REPL can do:**

**Chat panel (primary — replaces terminal input)**
- Full chat interface connected to the same `run_query()` pipeline
- Streaming responses in real time via WebSocket
- Supports read mode and edit mode queries
- Shows token counts, cost, timing in answer — same as terminal footer ✓ Done via `query_complete` event
- Sub/main call indicators (`sub → classify`, `main → call 1/2`) still need to be broadcast as `log` events so the browser chat shows them in real time — terminal already shows them now
- Edit mode shows step execution progress, diffs inline, batch commit confirmation
- Message history for the session
- `/sources` toggle, `/thinking` toggle available as buttons

**Index panel**
- Current index status (files, chunks, last indexed)
- Trigger `/index --force` and `/update` from the browser with live progress
- Show indexed files list with chunk counts and last indexed time
- `/status` equivalent — full analytics summary

**History panel**
- All votor git commits with file, lines changed, time ago
- `/diff N` inline — click a commit to see the diff
- `/undo` and `/revert N` buttons with confirmation dialog

**Config panel**
- View and edit `.vectormind/config.json` through a form UI
- Provider/model inputs (same logic as `/init` wizard)
- Save button triggers config reload without restart
- Prompts editor — view and edit all prompts from `prompts.json`
- Write mode selector (edit / step / reason when implemented)

**Analytics panel (fix existing)**
- Queries over time
- Token usage and cost per query and cumulative
- Retrieval score trends
- Response time breakdown (embed / classify / sub / llm)
- Most accessed files
- Model usage breakdown
- Token savings vs full context

**Side chat panel (optional, user-configurable)**
- A separate chat window the user can configure to connect to any LLM of their preference
- Not connected to votor's pipeline — purely a companion chat
- User sets provider/model/API key independently from votor's main config
- Use case: user wants GPT-4o as a thinking partner while votor handles the codebase
- Collapsible — hidden by default, shown when user enables it in settings

**Tech stack:**
- Keep FastAPI backend
- Replace Chart.js with Plotly or Recharts
- WebSocket for streaming chat and index progress
- Frontend: single-page app — React or a well-structured single HTML file
- Dark theme matching terminal color palette (`#0a0a0f`, `#00ffaa`, `#1e1e2e`)

**Scope:** `votor/dashboard.py` full rewrite. New `votor/static/` directory for frontend assets. New WebSocket endpoints for streaming. All existing REPL commands exposed as REST/WebSocket endpoints.

---

### 1. Loose Ends (quick fixes, do second)

**1a — `votor.egg-info/SOURCES.txt` shows in retrieval sources** ✓ DONE
Added `votor.egg-info` to `exclude_dirs` in `DEFAULT_CONFIG` (`init_flow.py`) and default config (`indexer.py`).

**1b — `pyproject.toml` keywords still say `chromadb`**
Update keywords to reflect actual stack:
```toml
keywords = ["ai", "vector", "coding", "assistant", "qdrant", "ollama", "openai", "anthropic", "rag"]
```

**1c — `/update` after edit mode uses Qdrant concurrent access** ✓ DONE
`close_client()` added to `db.py`; called before `index_project()` in auto-update block. Lock is released cleanly before re-indexing. Removed the "already accessed" special-case fallback message.

**1d — Debug print still in `classify_intent()`**
The `sub raw:` debug print added during diagnosis may still be in `query.py`. Verify and remove if present.

---

### 2. Reason Mode (write mode variant)

Plan discussed, no report written yet.

**Flow:**
```
sub reads files
    ↓
main reasons → decides first action
    ↓
sub executes one step
    ↓
main reviews result → decides next action
    ↓
repeat up to write_max_calls - 1 times
    ↓
main final summary
```

**Config:**
```json
"write_mode":       "reason",
"write_max_calls":  4
```

**Key difference from edit mode:** Main can course-correct between steps. Better for complex multi-file refactors where each step depends on the result of the previous one. More expensive — N main calls instead of 2.

**Scope:** New function `run_reason_mode()` in `query.py`. Routed from `run_query()` when `write_mode == "reason"`.

---

### 3. Option B — Chunk Rewrite at Index Time

Shelved — needs more planning.

**The idea:** Sub rewrites raw code chunks into natural language descriptions before embedding. Improves retrieval for plain-English queries against code.

**Open questions:**
- What model quality is needed for useful rewrites? (3b models produce generic output)
- How to store both raw chunk (for main context) and rewritten description (for embedding vector) in Qdrant
- Cost/time impact on `/update` for large projects
- Whether `nomic-embed-text` already handles code well enough that rewrites add marginal value

**Status:** Stub exists in `chunker.py` as commented `summarize_chunks()`. Leave until retrieval quality is measured and compared against a rewrite approach.

---

### 4. Conversation Memory

Not discussed in detail yet.

**The idea:** Embed each exchange (question + answer) into Qdrant with `type: conversation` metadata. On future queries, retrieve relevant past exchanges alongside code chunks.

**Design questions to answer before implementing:**
- Separate Qdrant collection for conversations or same collection with metadata filter?
- How many past exchanges to retrieve per query (top_k_conversations)?
- `/forget` command — wipe all conversation chunks without touching code index
- Cross-session memory vs session-only memory
- Whether conversation chunks should be excluded from edit mode context

**Config fields needed:**
```json
"conversation_memory": false,
"top_k_conversations": 3
```

---

### 5. Multi-File Edit Support

Currently main can only plan changes to files that sub explicitly read upfront. For queries involving changes across many files, main needs to be able to request additional files mid-plan and produce a coordinated multi-file write plan.

**The idea:** Main receives context from sub's initial file reads, generates a plan that spans multiple files, and can request additional files via `need_files` before finalizing. Sub reads them and feeds them back. Main outputs a single `write_plan` with steps across all affected files in dependency order.

**What's needed:**
- The `need_files` loop already exists (max 3 rounds) — may need the cap raised for large refactors
- Main needs stronger guidance in `write_plan_prompt` on how to order cross-file steps correctly
- Sub needs to handle reading many files efficiently without looping

**Config:**
```json
"max_file_request_rounds": 5
```

---

### 6. Step Mode — Interactive Todo List Execution

A mode where main generates a full step plan once, then presents it as a persistent interactive checklist. The user drives execution at their own pace and in their own order — nothing happens automatically. This is designed for live, controlled changes where the user wants to stay in the loop for every action.

**Example — migrate from Postgres to MongoDB:**
```
main generates plan:
  [ ] 1. remove postgres import from db.py
  [ ] 2. add mongodb import to db.py
  [ ] 3. update connection fields for mongodb
  [ ] 4. verify templates and queries

user sees the checklist and picks which step to run:
  > run 1
  sub executes step 1 → diff shown → marked [✓]

  [ ] 2. add mongodb import to db.py   ← user picks next
  > run 2
  ...

  > run 4   ← user can run out of order
  > skip 3  ← or skip a step
  > edit 2  ← or correct a step before running
```

**Flow:**
```
main generates write_plan as a numbered checklist
    ↓
votor displays full checklist with [ ] status indicators
    ↓
REPL waits for user commands:
  "run N"  → sub executes step N → mark [✓]
  "skip N" → mark [~] skipped
  "edit N" → user types natural language correction → main revises step → confirm before run
  "done"   → exit step mode, main summarizes what was done vs skipped
  "list"   → reprint current checklist state
    ↓
plan persists in memory for the session — user can come back to remaining steps
    ↓
on "done" → main summarizes executed steps, skipped steps, git commits made
```

**Key design decisions:**
- User controls order — steps can be run out of sequence
- Plan is generated once — main is only called again if user edits a step
- Each step shows a diff before marking complete
- Checklist state persists in session memory — user can run other queries and come back
- `edit N` sends the step back to main with the user's correction for revision — one extra main call per edited step

**Config:** `write_mode: "step"`

**REPL:** Enters a sub-loop with its own prompt `step ❯` when in step mode. Regular votor queries still work — typing a question exits step mode temporarily, `list` brings the checklist back.

**Scope:** New `run_step_mode()` in `query.py`. New sub-loop in `repl.py` for the `step ❯` prompt. Checklist state stored as a simple list in session memory.

---

### 7. Watch Mode

Auto `/update` on file save using `watchdog` (already in `pyproject.toml` dependencies). Keeps the index fresh automatically without manual `/update` calls.

**Config:**
```json
"watch_mode": false
```

**REPL command:** `/watch` to toggle on/off like `/sources`

**Scope:** Background thread monitoring project directory. Debounce 2s to avoid re-indexing on every keystroke. Prints `↺ updated: filename.py` in REPL when a file is re-indexed.

---

### 8. Parallel Client Support

Allow multiple votor instances to run in the same project simultaneously without clashing.

**Current problem:**
- Two votor terminals in the same project crash immediately on Qdrant — `Storage folder already accessed by another instance`
- SQLite analytics writes would also conflict under concurrent load
- `file_hashes.json` read/write race conditions during `/update`

**Two scenarios:**
- Same project, two terminals — currently broken
- Different projects, two terminals — already works fine, no conflict

**Primary use case:** Running a read query in one terminal while an edit is executing in another. Or running two read queries in parallel for speed.

**Options to fix:**
- **Request queue** — single Qdrant client shared via a lightweight local server process, requests queued and served one at a time. Adds complexity but keeps embedded mode.
- **Qdrant server mode** — optional config to point at a running Qdrant server instead of embedded. Multiple clients connect to the same server. Adds Docker/server dependency but is the cleanest solution for teams.
- **File locking** — lock `.vectormind/` during writes, queue reads. Simple but adds latency.

**Config (for server mode option):**
```json
"qdrant_mode": "embedded",   // or "server"
"qdrant_url":  "http://localhost:6333"
```

**Scope:** `votor/db.py` — connection management. Affects `indexer.py` and `query.py` wherever Qdrant client is opened.

---

## Summary Table

| Item | Effort | Priority | Status |
|---|---|---|---|
| 0 UI/UX redesign | Large | Top — affects every interaction | Partial — edit mode progress bar done |
| 0b Dashboard rebuild | Large | Priority 2 — full GUI + chat + config | Partial — terminal parity done, browser gaps remain |
| 1a egg-info exclude | Trivial | High — affects retrieval quality now | ✓ Done |
| 1b pyproject keywords | Trivial | Medium — cosmetic | Open |
| 1c Qdrant concurrent access on auto-update | Small | High — affects UX after every edit | ✓ Done |
| 1d debug print cleanup | Trivial | High — shouldn't be in production | ✓ Done |
| 2 Reason mode | Medium | Medium — useful but edit mode covers most cases | Open |
| 3 Chunk rewrite (Option B) | Large | Low — needs more planning | Shelved |
| 4 Conversation memory | Medium | Medium — significant UX improvement | Open |
| 5 Multi-file edit support | Medium | High — needed for real refactor tasks | Open |
| 6 Step mode (interactive todo list) | Medium | High — safety net for sensitive changes | Open |
| 7 Watch mode | Small | Low — quality of life | Open |
| 8 Parallel client support | Medium | Low — nice to have for power users | Open |