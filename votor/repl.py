import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich import box

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.shortcuts import CompleteStyle

console = Console()

VOTOR_DIR    = Path(".vectormind")
HISTORY_FILE = VOTOR_DIR / ".repl_history"

PROMPT_STYLE = Style.from_dict({
    # Prompt
    "prompt":                             "#00ffaa bold",
    "arrow":                              "#56b6c2",
    "path":                               "#61afef bold",


# Completion — READLINE_LIKE, flat, no scroll, no background
    "completion-menu":                    "bg:default noinherit",
    "completion-menu.completion":         "fg:#56b6c2 bg:default",
    "completion-menu.completion.current": "fg:#e5c07b bg:default bold",
    "completion-menu.meta":               "fg:#5c6370 bg:default",
    "completion-menu.meta.current":       "fg:#7f848e bg:default",
    "completion-menu.border":             "bg:default noinherit",
    "scrollbar":                          "bg:default noinherit",
    "scrollbar.background":               "bg:default noinherit",
    "scrollbar.button":                   "bg:default noinherit",
})

COMMANDS = [
    ("/init",      "Initialize votor for this project"),
    ("/index",     "Full re-index of entire project"),
    ("/update",    "Re-index changed files only"),
    ("/status",    "Show index health and analytics summary"),
    ("/dashboard", "Launch analytics dashboard in browser"),
    ("/history",   "Show AI change history"),
    ("/undo",      "Revert last AI change"),
    ("/revert",    "Revert to before change #n"),
    ("/diff",      "Show diff of change #n"),
    ("/config",    "Show current configuration"),
    ("/provider",  "Switch AI provider or model"),
    ("/sources",   "Toggle showing retrieved sources"),
    ("/thinking",  "Toggle showing raw model token stream"),
    ("/clear",     "Clear the screen"),
    ("/help",      "Show help"),
    ("/exit",      "Exit votor"),
]


class VotorCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            word = text.split()[0] if text.split() else "/"
            for cmd, desc in COMMANDS:
                if cmd.startswith(word):
                    yield Completion(
                        cmd,
                        start_position=-len(word),
                        display=f"{cmd:<12} {desc}",
                        display_meta=""
                    )


BANNER = r"""
    ___       ___       ___       ___       ___   
   /\__\     /\  \     /\  \     /\  \     /\  \  
  /:/ _/_   /::\  \    \:\  \   /::\  \   /::\  \ 
 |::L/\__\ /:/\:\__\   /::\__\ /:/\:\__\ /::\:\__\
 |::::/  / \:\/:/  /  /:/\/__/ \:\/:/  / \;:::/  /
  L;;/__/   \::/  /   \/__/     \::/  /   |:\/__/ 
             \/__/               \/__/     \|__|  
"""

HELP_TEXT = """
[bold #00ffaa]Commands[/bold #00ffaa]

  [#00ffaa]/init[/#00ffaa]              [#abb2bf]Initialize votor for this project[/#abb2bf]
  [#00ffaa]/index[/#00ffaa]             [#abb2bf]Full re-index of entire project[/#abb2bf]
  [#00ffaa]/update[/#00ffaa]            [#abb2bf]Re-index changed files only[/#abb2bf]
  [#00ffaa]/status[/#00ffaa]            [#abb2bf]Show index health and analytics summary[/#abb2bf]
  [#00ffaa]/dashboard[/#00ffaa]         [#abb2bf]Launch analytics dashboard in browser[/#abb2bf]
  [#00ffaa]/history[/#00ffaa]           [#abb2bf]Show AI change history (git log)[/#abb2bf]
  [#00ffaa]/undo[/#00ffaa]              [#abb2bf]Revert last AI change[/#abb2bf]
  [#00ffaa]/revert [dim]<n>[/dim][/#00ffaa]       [#abb2bf]Revert to before change #n[/#abb2bf]
  [#00ffaa]/diff [dim]<n>[/dim][/#00ffaa]         [#abb2bf]Show diff of change #n[/#abb2bf]
  [#00ffaa]/config[/#00ffaa]            [#abb2bf]Show current configuration[/#abb2bf]
  [#00ffaa]/provider[/#00ffaa]          [#abb2bf]Switch AI provider or model[/#abb2bf]
  [#00ffaa]/sources[/#00ffaa]           [#abb2bf]Toggle showing retrieved sources[/#abb2bf]
  [#00ffaa]/thinking[/#00ffaa]          [#abb2bf]Toggle showing raw model token stream[/#abb2bf]
  [#00ffaa]/clear[/#00ffaa]             [#abb2bf]Clear the screen[/#abb2bf]
  [#00ffaa]/help[/#00ffaa]              [#abb2bf]Show this help[/#abb2bf]
  [#00ffaa]/exit[/#00ffaa]              [#abb2bf]Exit votor[/#abb2bf]

[#5c6370]Just type your question to query the codebase.[/#5c6370]
[#5c6370]Votor will find relevant context and respond.[/#5c6370]
"""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_banner():
    console.print(f"[bold #00ffaa]{BANNER}[/bold #00ffaa]")
    console.print(
        f"  [#abb2bf]project-aware coding assistant[/#abb2bf]  "
        f"[#5c6370]v0.1.0[/#5c6370]\n"
    )


def print_response(result: dict, show_sources: bool):
    console.print(Panel(
        Markdown(result["answer"]),
        title=f"[bold #00ffaa]votor[/bold #00ffaa] [#5c6370]({result['provider']}/{result['model']})[/#5c6370]",
        border_style="#1e1e2e",
        padding=(1, 2)
    ))

    if show_sources and result.get("sources"):
        source_table = Table(
            show_header=True,
            box=box.SIMPLE,
            header_style="#5c6370",
            show_edge=False,
            padding=(0, 2)
        )
        source_table.add_column("file",      style="#61afef", no_wrap=True)
        source_table.add_column("chunk",     justify="right", style="#5c6370")
        source_table.add_column("relevance", justify="right")

        for s in result["sources"]:
            score       = s["score"]
            score_color = "#00ffaa" if score > 0.85 else "#e5c07b" if score > 0.7 else "#e06c75"
            source_table.add_row(
                s["file"],
                str(s["chunk"]),
                f"[{score_color}]{score:.0%}[/{score_color}]"
            )

        console.print(Panel(
            source_table,
            title="[#5c6370]sources[/#5c6370]",
            border_style="#1e1e2e",
            padding=(0, 2)
        ))

    t_embed    = result.get("t_embed", 0)
    t_retrieve = result.get("t_retrieve", 0)
    t_classify = result.get("t_classify", 0)
    t_sub      = result.get("t_sub_tools", 0)
    t_llm      = result.get("t_llm", 0)
    savings    = result.get("savings_pct", 0)

    cost_group = (
        f"  [#e5c07b]{result['total_tokens']:,}[/#e5c07b] [#5c6370]tokens[/#5c6370]"
        f"  [#e5c07b]${result['cost']:.4f}[/#e5c07b]"
    )

    timing_group = (
        f"  [#5c6370]embed[/#5c6370] [#abb2bf]{t_embed}s[/#abb2bf]"
        f"  [#5c6370]retrieve[/#5c6370] [#abb2bf]{t_retrieve}s[/#abb2bf]"
        f"  [#5c6370]classify[/#5c6370] [#abb2bf]{t_classify}s[/#abb2bf]"
        + (f"  [#5c6370]sub[/#5c6370] [#abb2bf]{t_sub}s[/#abb2bf]" if t_sub > 0 else "")
        + f"  [#5c6370]llm[/#5c6370] [#abb2bf]{t_llm}s[/#abb2bf]"
        f"  [#5c6370]total[/#5c6370] [#abb2bf]{result['response_time']}s[/#abb2bf]"
    )

    model_group = (
        f"  [#c678dd]{result['model']}[/#c678dd]"
        f"  [#5c6370]{result['retrieval_score']:.0%} retrieval[/#5c6370]"
        + (f"  [#00ffaa]~{savings}% saved[/#00ffaa]" if savings else "")
    )

    console.print(cost_group + "   " + timing_group + "   " + model_group + "\n")


def print_status(s: dict):
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(s["last_indexed"])
        last_indexed = dt.strftime("%-d %b %Y, %H:%M")
    except Exception:
        last_indexed = s.get("last_indexed", "never")

    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 3))
    table.add_column(style="#5c6370", min_width=16)
    table.add_column()

    table.add_row("indexed files",  f"[#abb2bf]{s['total_files']}[/#abb2bf]")
    table.add_row("indexed chunks", f"[#abb2bf]{s['total_chunks']}[/#abb2bf]")
    table.add_row("last indexed",   f"[#abb2bf]{last_indexed}[/#abb2bf]")
    table.add_row("", "")
    table.add_row("total queries",  f"[#abb2bf]{s['total_queries']}[/#abb2bf]")
    table.add_row("total tokens",   f"[#e5c07b]{s['total_tokens']:,}[/#e5c07b]")
    table.add_row("total cost",     f"[#e5c07b]${s['total_cost']:.4f}[/#e5c07b]")
    table.add_row("avg response",   f"[#abb2bf]{s['avg_response_time']:.2f}s[/#abb2bf]")

    console.print(Panel(
        table,
        title="[#5c6370]status[/#5c6370]",
        border_style="#1e1e2e",
        padding=(1, 2)
    ))


def _parse_history_msg(msg: str) -> tuple[str, str]:
    """Parse votor commit message into (file, action) for clean display."""
    import re

    msg = msg.strip()

    # Normalize encoding corruption (mojibake from cp1252/utf-8 mismatch)
    msg = msg.replace("\xe2\x80\x94", "\u2014")   # UTF-8 bytes read as latin-1
    msg = msg.replace("â\x80\x94", "\u2014")

    # Strip votor: prefix
    if msg.lower().startswith("votor:"):
        msg = msg[6:].strip()

    # Pattern: "edit session — FILE" (em dash or hyphen)
    m = re.match('edit session\\s*[\u2014\\-]+\\s*(.+)', msg, re.IGNORECASE)
    if m:
        return m.group(1).strip(), "edit session"

    # Pattern: "edited FILE (lines X-Y)"
    m = re.match(r'(edited|created|deleted)\s+(.+?)\s+\((.+?)\)', msg, re.IGNORECASE)
    if m:
        return m.group(2).strip(), m.group(3).strip()

    # Pattern: "edited FILE"
    m = re.match(r'(edited|created|deleted)\s+(.+)', msg, re.IGNORECASE)
    if m:
        return m.group(2).strip(), m.group(1).lower()

    if "revert" in msg.lower():
        return "", "revert"

    return msg, ""


def print_history(history: list):
    if not history:
        console.print("[#5c6370]No AI changes recorded yet.[/#5c6370]\n")
        return

    table = Table(show_header=True, box=box.SIMPLE, show_edge=False, padding=(0, 2))
    table.add_column("#",      style="#5c6370",  width=4)
    table.add_column("hash",   style="#c678dd",  width=9)
    table.add_column("file",   style="#61afef",  min_width=24)
    table.add_column("action", style="#5c6370",  min_width=14)
    table.add_column("when",   style="#5c6370")

    for h in history:
        file_path, action = _parse_history_msg(h["msg"])
        table.add_row(
            str(h["n"]),
            h["hash"][:7],
            file_path or "[#5c6370]\u2014[/#5c6370]",
            action    or "[#5c6370]\u2014[/#5c6370]",
            h["time"]
        )

    console.print(Panel(
        table,
        title="[#5c6370]ai change history[/#5c6370]",
        border_style="#1e1e2e",
        padding=(1, 1)
    ))


def print_config(cfg: dict):
    def pm(provider_key: str, model_key: str) -> str:
        p = cfg.get(provider_key, "")
        m = cfg.get(model_key, "")
        return f"[#c678dd]{p}[/#c678dd] [#5c6370]/[/#5c6370] [#61afef]{m}[/#61afef]"

    def val(v, color: str = "#abb2bf") -> str:
        return f"[{color}]{v}[/{color}]"

    def boolean(v) -> str:
        return "[#00ffaa]on[/#00ffaa]" if v else "[#5c6370]off[/#5c6370]"

    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 3))
    table.add_column(style="#5c6370", min_width=18)
    table.add_column()

    # Agents
    table.add_row("[#5c6370]agents[/#5c6370]", "")
    table.add_row("main",       pm("main_provider", "main_model"))
    table.add_row("fallback",   val(cfg.get("fallback_model", ""), "#61afef"))
    table.add_row("sub",        pm("sub_provider", "sub_model"))
    table.add_row("verify",     boolean(cfg.get("verify_changes", False)))
    table.add_row("max rounds", val(cfg.get("write_max_calls", 6), "#e5c07b"))
    table.add_row("", "")

    # Embeddings
    table.add_row("[#5c6370]embeddings[/#5c6370]", "")
    table.add_row("provider",   val(cfg.get("embedding_provider", ""), "#c678dd"))
    table.add_row("model",      val(cfg.get("embedding_model", ""),    "#61afef"))
    table.add_row("", "")

    # Index
    table.add_row("[#5c6370]index[/#5c6370]", "")
    table.add_row("top k",      val(cfg.get("top_k",         5),   "#e5c07b"))
    table.add_row("chunk size", val(cfg.get("chunk_size",  200),   "#e5c07b"))
    table.add_row("overlap",    val(cfg.get("chunk_overlap", 20),  "#e5c07b"))
    table.add_row("", "")

    # Git
    table.add_row("[#5c6370]git[/#5c6370]", "")
    table.add_row("remote",     val(cfg.get("git_remote", "none")))
    git_url = cfg.get("git_remote_url", "")
    if git_url:
        table.add_row("url",    val(git_url, "#61afef"))

    console.print(Panel(
        table,
        title="[#5c6370]configuration[/#5c6370]",
        border_style="#1e1e2e",
        padding=(1, 2)
    ))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_init(force: bool = False):
    console.print()
    try:
        from votor.init_flow import run_init
        from votor.providers import clear_client_cache
        from votor.query import invalidate_prompts_cache
        result = run_init(force=force)
        clear_client_cache()
        invalidate_prompts_cache()
        if isinstance(result, tuple):
            config, do_index = result
        else:
            config, do_index = result, False
        console.print(f"\n[#00ffaa]✓[/#00ffaa] Initialized.\n")
        if do_index:
            handle_index(full=True, config=config)
    except Exception as e:
        console.print(f"[#e06c75]✗[/#e06c75] Init failed: {e}\n")


def handle_index(full: bool = True, config: dict = None):
    label = "full index" if full else "incremental update"
    console.print(f"[#5c6370]Running {label}...[/#5c6370]\n")
    try:
        from votor.indexer import index_project
        from votor.query import invalidate_full_context_cache
        stats = index_project(incremental=not full, force=full, config=config)
        invalidate_full_context_cache()
        console.print(
            f"[#00ffaa]✓[/#00ffaa] Indexed [#abb2bf]{stats['files']}[/#abb2bf] files, "
            f"[#abb2bf]{stats['chunks']}[/#abb2bf] chunks"
            + (f" [#5c6370]({stats['updated']} updated, {stats['skipped']} skipped)[/#5c6370]"
               if not full else "")
            + "\n"
        )
    except Exception as e:
        console.print(f"[#e06c75]✗[/#e06c75] Indexing failed: {e}\n")


def handle_status():
    try:
        from votor.db import get_stats
        from votor.analytics import get_summary
        stats   = get_stats()
        summary = get_summary()
        print_status({**stats, **summary})
    except Exception as e:
        console.print(f"[#e06c75]Error: {e}[/#e06c75]\n")


def handle_history():
    try:
        from votor.tools import git_log
        history = git_log()
        print_history(history)
    except Exception as e:
        console.print(f"[#e06c75]Error: {e}[/#e06c75]\n")


def handle_undo():
    try:
        from votor.tools import git_undo
        console.print("[#5c6370]Reverting last AI change...[/#5c6370]")
        result = git_undo()
        if result["success"]:
            console.print(f"[#00ffaa]✓[/#00ffaa] [#5c6370]Reverted: {result['reverted']}[/#5c6370]\n")
        else:
            console.print(f"[#e06c75]✗[/#e06c75] {result['error']}\n")
    except Exception as e:
        console.print(f"[#e06c75]Error: {e}[/#e06c75]\n")


def handle_revert(args: list):
    if not args:
        console.print("[#e06c75]Usage: /revert <n>[/#e06c75]\n")
        return
    try:
        from votor.tools import git_revert_to
        n      = int(args[0])
        result = git_revert_to(n)
        if result["success"]:
            console.print(f"[#00ffaa]✓[/#00ffaa] Reverted {len(result['reverted'])} changes.\n")
        else:
            console.print(f"[#e06c75]✗[/#e06c75] {result['error']}\n")
    except ValueError:
        console.print("[#e06c75]Usage: /revert <n> — n must be a number[/#e06c75]\n")
    except Exception as e:
        console.print(f"[#e06c75]Error: {e}[/#e06c75]\n")


def handle_diff(args: list):
    if not args:
        console.print("[#e06c75]Usage: /diff <n>[/#e06c75]\n")
        return
    try:
        from votor.tools import git_diff, show_diff
        n      = int(args[0])
        result = git_diff(n)
        if result["success"]:
            show_diff(result["diff"], title=f"diff — change #{n}")
        else:
            console.print(f"[#e06c75]✗[/#e06c75] {result['error']}\n")
    except ValueError:
        console.print("[#e06c75]Usage: /diff <n> — n must be a number[/#e06c75]\n")
    except Exception as e:
        console.print(f"[#e06c75]Error: {e}[/#e06c75]\n")


def handle_config():
    try:
        import json
        config_file = Path(".vectormind/config.json")
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            print_config(cfg)
        else:
            console.print("[#5c6370]No config found. Run /init first.[/#5c6370]\n")
    except Exception as e:
        console.print(f"[#e06c75]Error: {e}[/#e06c75]\n")


def handle_provider():
    console.print(Panel(
        "[#5c6370]To switch provider or model, run [#00ffaa]/init --force[/#00ffaa].\n"
        "Or edit [#61afef].vectormind/config.json[/#61afef] directly.[/#5c6370]",
        title="[#5c6370]provider[/#5c6370]",
        border_style="#3e4451"
    ))


def handle_dashboard():
    try:
        from votor.dashboard import start_dashboard
        url = start_dashboard(port=8000, open_browser=True)
        console.print(f"  [#00ffaa]✓[/#00ffaa] [#61afef][link={url}]{url}[/link][/#61afef]\n")
    except Exception as e:
        console.print(f"  [#e06c75]✗ dashboard failed: {e}[/#e06c75]\n")


def handle_query(question: str, show_sources: bool, show_thinking: bool = False):
    if not VOTOR_DIR.exists():
        console.print("[#e06c75]Not initialized. Run /init first.[/#e06c75]\n")
        return
    try:
        from votor.query import run_query
        result = run_query(question, show_sources=show_sources, show_thinking=show_thinking)
        if result.get("error") == "no_context":
            console.print(f"[#e06c75]{result['answer']}[/#e06c75]\n")
            return
        print_response(result, show_sources)
    except KeyboardInterrupt:
        console.print("\n[#5c6370]cancelled.[/#5c6370]\n")
    except Exception as e:
        console.print(f"[#e06c75]Query failed: {e}[/#e06c75]\n")


# ---------------------------------------------------------------------------
# Main REPL loop
# ---------------------------------------------------------------------------

def main():
    VOTOR_DIR.mkdir(exist_ok=True)

    # Start dashboard server
    try:
        from votor.dashboard import start_dashboard
        import time
        dashboard_url = start_dashboard(port=8000)
        time.sleep(0.3)  # let server start before printing anything
    except Exception:
        dashboard_url = None

    print_banner()

    project_name = Path.cwd().name

    if not (VOTOR_DIR / "config.json").exists():
        console.print(Panel(
            "[#5c6370]No votor index found in this project.\n"
            "Run [#00ffaa]/init[/#00ffaa] to get started.[/#5c6370]",
            border_style="#3e4451"
        ))
    else:
        console.print(f"  [#5c6370]project[/#5c6370]    [#61afef]{project_name}[/#61afef]")
        if dashboard_url:
            console.print(
                f"  [#5c6370]dashboard[/#5c6370]   "
                f"[#61afef][link={dashboard_url}]{dashboard_url}[/link][/#61afef]"
                f"  [#5c6370]← click to open[/#5c6370]"
            )
        console.print(f"  [#5c6370]type [#00ffaa]/help[/#00ffaa] for commands or just ask a question[/#5c6370]\n")

    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=VotorCompleter(),
        complete_while_typing=True,
        complete_in_thread=False,
        style=PROMPT_STYLE,
        mouse_support=False,
    )

    show_sources  = False
    show_thinking = False
    _ctrl_c_count = 0

    while True:
        try:
            raw = session.prompt(HTML(
                f'<path>{project_name}</path><arrow> ❯ </arrow><prompt>votor</prompt> '
            ))
        except KeyboardInterrupt:
            if _ctrl_c_count >= 1:
                console.print("\n[#5c6370]bye.[/#5c6370]\n")
                break
            _ctrl_c_count += 1
            console.print("\n[#5c6370]Press Ctrl+C again or type /exit to quit.[/#5c6370]\n")
            continue
        except EOFError:
            break

        _ctrl_c_count = 0
        inp = raw.strip()
        if not inp:
            sys.stdout.write("\033[1A\033[2K")  # move up one line, clear it
            sys.stdout.flush()
            continue

        if inp.startswith("/"):
            parts = inp.split()
            cmd   = parts[0].lower()
            args  = parts[1:]

            if cmd in ("/exit", "/quit"):
                console.print("\n[#5c6370]bye.[/#5c6370]\n")
                break
            elif cmd == "/help":
                console.print(Panel(HELP_TEXT, title="[#5c6370]votor help[/#5c6370]", border_style="#3e4451"))
            elif cmd == "/init":
                handle_init(force="--force" in args)
            elif cmd == "/index":
                handle_index(full=True)
            elif cmd == "/update":
                handle_index(full=False)
            elif cmd == "/status":
                handle_status()
            elif cmd == "/history":
                handle_history()
            elif cmd == "/undo":
                handle_undo()
            elif cmd == "/revert":
                handle_revert(args)
            elif cmd == "/diff":
                handle_diff(args)
            elif cmd == "/config":
                handle_config()
            elif cmd == "/provider":
                handle_provider()
            elif cmd == "/dashboard":
                handle_dashboard()
            elif cmd == "/sources":
                show_sources = not show_sources
                state = "on" if show_sources else "off"
                console.print(f"  [#5c6370]sources[/#5c6370] [#abb2bf]{state}[/#abb2bf]\n")
            elif cmd == "/thinking":
                show_thinking = not show_thinking
                state = "on" if show_thinking else "off"
                console.print(f"  [#5c6370]thinking[/#5c6370] [#abb2bf]{state}[/#abb2bf]\n")
            elif cmd == "/clear":
                console.clear()
                print_banner()
            else:
                console.print(f"  [#e06c75]unknown: {cmd}[/#e06c75] [#5c6370]— type [#00ffaa]/help[/#00ffaa][/#5c6370]\n")
        else:
            handle_query(inp, show_sources, show_thinking)


if __name__ == "__main__":
    main()