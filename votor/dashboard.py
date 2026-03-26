import asyncio
import json
import re
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

_event_loop = None  # set once at server startup via lifespan


@asynccontextmanager
async def _lifespan(app):
    global _event_loop
    _event_loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="votor dashboard", docs_url=None, redoc_url=None, lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR   = Path(__file__).parent / "static"
CONFIG_FILE  = Path(".vectormind/config.json")

# Connected dashboard WebSocket clients
_ws_clients: list[WebSocket] = []
_ws_lock     = threading.Lock()
_query_lock  = threading.Lock()  # prevents concurrent terminal + dashboard queries


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket):
    await ws.accept()
    with _ws_lock:
        _ws_clients.append(ws)
    try:
        cfg = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
        await ws.send_json({
            "type":          "init",
            "project":       Path(".").resolve().name,
            "main_provider": cfg.get("main_provider", ""),
            "main_model":    cfg.get("main_model", ""),
            "config":        cfg,
        })

        while True:
            data = await ws.receive_json()
            await _handle_ws_message(ws, data)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        with _ws_lock:
            if ws in _ws_clients:
                _ws_clients.remove(ws)


async def _handle_ws_message(ws: WebSocket, data: dict):
    """Handle incoming messages from dashboard clients."""
    msg_type = data.get("type")

    if msg_type == "get_init":
        cfg = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
        await ws.send_json({
            "type":          "init",
            "project":       Path(".").resolve().name,
            "main_provider": cfg.get("main_provider", ""),
            "main_model":    cfg.get("main_model", ""),
            "config":        cfg,
        })

    elif msg_type == "query":
        t = threading.Thread(target=_run_query_from_dashboard, args=(data,), daemon=True)
        t.start()

    elif msg_type == "index_force":
        broadcast_sync({"type": "busy", "busy": True})
        t = threading.Thread(target=_run_index, args=(True,), daemon=True)
        t.start()

    elif msg_type == "index_update":
        broadcast_sync({"type": "busy", "busy": True})
        t = threading.Thread(target=_run_index, args=(False,), daemon=True)
        t.start()


_MARKUP_RE = re.compile(r'\[/?[^\]]*\]')


class _BroadcastConsole:
    """
    Wraps qmod.console so that every console.print() call:
      1. Passes through to the real terminal console (unchanged).
      2. Strips Rich markup and broadcasts a `log` event to the browser.
    Non-string objects (Panel, Markdown, Progress) are passed through only —
    they are never broadcast.
    """
    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def print(self, *args, **kwargs):
        self._real.print(*args, **kwargs)
        text_parts = [a for a in args if isinstance(a, str)]
        if text_parts:
            plain = _MARKUP_RE.sub('', ' '.join(text_parts)).strip()
            if plain:
                broadcast_sync({"type": "log", "html": plain})

    def status(self, *args, **kwargs):
        return self._real.status(*args, **kwargs)


def _run_query_from_dashboard(data: dict):
    """Run a votor query triggered from the dashboard."""
    from rich.console import Console as RichConsole
    from rich.panel import Panel
    from rich.markdown import Markdown
    import votor.query as qmod
    from votor.query import run_query, _set_headless

    question      = data.get("text", "")
    show_sources  = data.get("show_sources", False)
    show_thinking = data.get("show_thinking", False)

    if not _query_lock.acquire(blocking=False):
        broadcast_sync({"type": "error", "message": "A query is already running — please wait."})
        return

    broadcast_sync({"type": "input_received", "source": "dashboard", "text": question})
    _term = RichConsole()
    _term.print(f"\n  [#5c6370]dashboard[/#5c6370] [#61afef]❯[/#61afef] [#abb2bf]{question}[/#abb2bf]")
    broadcast_sync({"type": "busy", "busy": True})

    original_console = qmod.console
    try:
        qmod.console = _BroadcastConsole(original_console)
        _set_headless(True)  # silence streamed answer text in _stream_to_console
        result = run_query(
            question,
            show_sources=show_sources,
            show_thinking=show_thinking,
        )
        _term.print(Panel(
            Markdown(result.get("answer", "")),
            title=f"[bold #00ffaa]votor[/bold #00ffaa] [#5c6370](dashboard / {result.get('model','')})[/#5c6370]",
            border_style="#1e1e2e",
            padding=(1, 2)
        ))
        t_embed    = result.get("t_embed", 0)
        t_retrieve = result.get("t_retrieve", 0)
        t_classify = result.get("t_classify", 0)
        t_sub      = result.get("t_sub_tools", 0)
        t_llm      = result.get("t_llm", 0)
        savings    = result.get("savings_pct", 0)
        _term.print(
            f"  [#e5c07b]{result.get('total_tokens',0):,}[/#e5c07b] [#5c6370]tokens[/#5c6370]"
            f"  [#e5c07b]${result.get('cost',0):.4f}[/#e5c07b]"
            f"  [#5c6370]embed[/#5c6370] [#abb2bf]{t_embed}s[/#abb2bf]"
            f"  [#5c6370]retrieve[/#5c6370] [#abb2bf]{t_retrieve}s[/#abb2bf]"
            f"  [#5c6370]classify[/#5c6370] [#abb2bf]{t_classify}s[/#abb2bf]"
            + (f"  [#5c6370]sub[/#5c6370] [#abb2bf]{t_sub}s[/#abb2bf]" if t_sub > 0 else "")
            + f"  [#5c6370]llm[/#5c6370] [#abb2bf]{t_llm}s[/#abb2bf]"
            f"  [#5c6370]total[/#5c6370] [#abb2bf]{result.get('response_time',0)}s[/#abb2bf]"
            f"  [#c678dd]{result.get('model','')}[/#c678dd]"
            + (f"  [#00ffaa]~{savings}% saved[/#00ffaa]" if savings else "")
            + "\n"
        )
        _term.print(
            f"[bold #61afef]{Path('.').resolve().name}[/bold #61afef]"
            f"[#56b6c2] ❯ [/#56b6c2]"
            f"[bold #00ffaa]votor[/bold #00ffaa] ",
            end="",
        )
        broadcast_sync({
            "type":            "query_complete",
            "tokens":          result.get("total_tokens", 0),
            "cost":            result.get("cost", 0),
            "response_time":   result.get("response_time", 0),
            "model":           result.get("model", ""),
            "retrieval_score": result.get("retrieval_score", 0),
            "answer":          result.get("answer", ""),
            "sources":         result.get("sources", []) if show_sources else [],
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        _term.print(f"  [#e06c75]✗ dashboard query failed: {e}[/#e06c75]\n")
        broadcast_sync({"type": "error", "message": str(e)})
    finally:
        qmod.console = original_console
        _set_headless(False)
        _query_lock.release()
        try:
            broadcast_sync({"type": "busy", "busy": False})
        except Exception:
            pass


def _run_index(force: bool):
    """Run index triggered from dashboard."""
    from votor.indexer import index_project

    def _on_progress(current, total, file):
        broadcast_sync({"type": "index_progress", "current": current, "total": total, "file": file})

    try:
        stats = index_project(incremental=not force, force=force, on_progress=_on_progress)
        broadcast_sync({
            "type":   "index_complete",
            "files":  stats.get("files", 0),
            "chunks": stats.get("chunks", 0),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        broadcast_sync({"type": "error", "message": str(e)})
    finally:
        try:
            broadcast_sync({"type": "busy", "busy": False})
        except Exception:
            pass


def broadcast_sync(event: dict):
    """Broadcast an event to all connected dashboard WebSocket clients (thread-safe)."""
    if _event_loop is None:
        return
    with _ws_lock:
        clients = list(_ws_clients)
    if not clients:
        return
    dead = []
    for ws in clients:
        try:
            fut = asyncio.run_coroutine_threadsafe(ws.send_json(event), _event_loop)
            fut.add_done_callback(lambda f: None)
        except Exception:
            dead.append(ws)
    if dead:
        with _ws_lock:
            for ws in dead:
                if ws in _ws_clients:
                    _ws_clients.remove(ws)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/api/analytics")
def api_analytics():
    try:
        from votor.analytics import get_summary, get_recent_queries
        summary = get_summary()
        queries = get_recent_queries(limit=200)
        return {**summary, "queries": queries}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/status")
def api_status():
    try:
        from votor.db import get_stats
        from votor.analytics import get_summary
        stats   = get_stats()
        summary = get_summary()
        # Build files list with chunk counts
        files = [{"path": f, "chunks": 0, "indexed_at": ""} for f in stats.get("files", [])]
        return {**stats, **summary, "files": files}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/history")
def api_history():
    try:
        from votor.tools import git_log
        return git_log()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/diff/{n}")
def api_diff(n: int):
    try:
        from votor.tools import git_diff
        return git_diff(n)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/undo")
def api_undo():
    try:
        from votor.tools import git_undo
        return git_undo()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/revert/{n}")
def api_revert(n: int):
    try:
        from votor.tools import git_revert_to
        return git_revert_to(n)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/config")
def api_get_config():
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                return json.load(f)
        return {}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/config")
async def api_save_config(request):
    try:
        from fastapi import Request
        body = await request.json()
        with open(CONFIG_FILE, "w") as f:
            json.dump(body, f, indent=2)
        broadcast_sync({"type": "config_saved", "config": body})
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/side-chat")
async def api_side_chat(request):
    try:
        body      = await request.json()
        text      = body.get("text", "")
        provider  = body.get("provider", "openai")
        model     = body.get("model", "gpt-4o-mini")
        api_key   = body.get("api_key", "")

        from votor.providers import call_llm
        import os
        if api_key:
            # Temporarily set key for this call
            env_key_map = {
                "openai":    "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "groq":      "GROQ_API_KEY",
            }
            env_key = env_key_map.get(provider)
            if env_key:
                os.environ[env_key] = api_key

        result = call_llm(provider, model, [{"role": "user", "content": text}])
        return {"content": result["content"]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Static files + root
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index), headers={"Cache-Control": "no-store"})
    return JSONResponse({"error": "Dashboard frontend not found. Add votor/static/index.html."})


# ---------------------------------------------------------------------------
# Server launcher
# ---------------------------------------------------------------------------

_server_thread: threading.Thread | None = None
_server_url: str | None = None
_server_lock = threading.Lock()


def start_dashboard(port: int = 8000, open_browser: bool = False) -> str:
    """
    Start the dashboard server in a background daemon thread.
    Returns the URL. Safe to call multiple times — only starts once.
    """
    global _server_thread, _server_url

    with _server_lock:
        if _server_thread is not None and _server_thread.is_alive():
            return _server_url

        url = f"http://127.0.0.1:{port}"
        _server_url = url

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="error",
            access_log=False,
            loop="asyncio",
        )
        server = uvicorn.Server(config)

        _server_thread = threading.Thread(target=server.run, daemon=True)
        _server_thread.start()

        import votor.events as _events
        _events.register(broadcast_sync)

    if open_browser:
        import time
        time.sleep(1.5)  # give server a moment to start
        webbrowser.open(url)

    return url
