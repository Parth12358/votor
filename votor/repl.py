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
        border_style="#3e4451",
        padding=(1, 2)
    ))

    if show_sources and result.get("sources"):
        source_table = Table(
            show_header=True,
            box=box.SIMPLE,
            header_style="#5c6370",
            show_edge=False,
            padding=(0, 1)
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
            title="[#5c6370]retrieved sources[/#5c6370]",
            border_style="#3e4451",
            padding=(0, 1)
        ))

    savings = (
        f"  [#5c6370]saved[/#5c6370] [#00ffaa]~{result.get('savings_pct', 0)}%[/#00ffaa]"
        if result.get("savings_pct") else ""
    )

    t_embed      = result.get("t_embed", 0)
    t_retrieve   = result.get("t_retrieve", 0)
    t_classify   = result.get("t_classify", 0)
    t_sub_tools  = result.get("t_sub_tools", 0)
    t_llm        = result.get("t_llm", 0)

    console.print(
        f"  [#5c6370]tokens[/#5c6370] [#abb2bf]{result['total_tokens']}[/#abb2bf]"
        f"  [#5c6370]cost[/#5c6370] [#e5c07b]${result['cost']:.4f}[/#e5c07b]"
        f"  [#5c6370]embed[/#5c6370] [#abb2bf]{t_embed}s[/#abb2bf]"
        f"  [#5c6370]retrieve[/#5c6370] [#abb2bf]{t_retrieve}s[/#abb2bf]"
        f"  [#5c6370]classify[/#5c6370] [#abb2bf]{t_classify}s[/#abb2bf]"
        f"  [#5c6370]sub[/#5c6370] [#abb2bf]{t_sub_tools}s[/#abb2bf]"
        f"  [#5c6370]llm[/#5c6370] [#abb2bf]{t_llm}s[/#abb2bf]"
        f"  [#5c6370]total[/#5c6370] [#abb2bf]{result['response_time']}s[/#abb2bf]"
        f"  [#5c6370]retrieval[/#5c6370] [#abb2bf]{result['retrieval_score']:.0%}[/#abb2bf]"
        f"  [#5c6370]model[/#5c6370] [#c678dd]{result['model']}[/#c678dd]"
        f"{savings}\n"
    )


def print_status(s: dict):
    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 2))
    table.add_column(style="#5c6370")
    table.add_column(style="#abb2bf")
    table.add_row("indexed files",  str(s["total_files"]))
    table.add_row("indexed chunks", str(s["total_chunks"]))
    table.add_row("last indexed",   s["last_indexed"])
    table.add_row("total queries",  str(s["total_queries"]))
    table.add_row("total tokens",   str(s["total_tokens"]))
    table.add_row("total cost",     f"[#e5c07b]${s['total_cost']:.4f}[/#e5c07b]")
    table.add_row("avg response",   f"{s['avg_response_time']:.2f}s")
    console.print(Panel(table, title="[#5c6370]status[/#5c6370]", border_style="#3e4451"))


def print_history(history: list):
    if not history:
        console.print("[#5c6370]No AI changes recorded yet.[/#5c6370]\n")
        return
    table = Table(show_header=True, box=box.SIMPLE, show_edge=False, padding=(0, 1))
    table.add_column("#",       style="#5c6370", width=4)
    table.add_column("hash",    style="#c678dd", width=9)
    table.add_column("message", style="#abb2bf")
    table.add_column("when",    style="#5c6370")
    for h in history:
        table.add_row(str(h["n"]), h["hash"][:7], h["msg"], h["time"])
    console.print(Panel(table, title="[#5c6370]ai change history[/#5c6370]", border_style="#3e4451"))


def print_config(cfg: dict):
    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 2))
    table.add_column(style="#5c6370")
    table.add_column(style="#00ffaa")
    for k, v in cfg.items():
        if not isinstance(v, list):
            table.add_row(k.replace("_", " "), str(v))
    console.print(Panel(table, title="[#5c6370]configuration[/#5c6370]", border_style="#3e4451"))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_init(force: bool = False):
    console.print()
    try:
        from votor.init_flow import run_init
        result = run_init(force=force)
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
        stats = index_project(incremental=not full, force=full, config=config)
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
    console.print("[#5c6370]Launching dashboard at [#61afef]http://localhost:8000[/#61afef]...[/#5c6370]")
    import subprocess
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "votor.dashboard:app", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    console.print("[#00ffaa]✓[/#00ffaa] Dashboard at [link=http://localhost:8000]http://localhost:8000[/link]\n")


def handle_query(question: str, show_sources: bool):
    if not VOTOR_DIR.exists():
        console.print("[#e06c75]Not initialized. Run /init first.[/#e06c75]\n")
        return
    try:
        from votor.query import run_query
        result = run_query(question, show_sources=show_sources)
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
    print_banner()

    project_name = Path.cwd().name

    if not (VOTOR_DIR / "config.json").exists():
        console.print(Panel(
            "[#5c6370]No votor index found in this project.\n"
            "Run [#00ffaa]/init[/#00ffaa] to get started.[/#5c6370]",
            border_style="#3e4451"
        ))
    else:
        console.print(f"  [#5c6370]project[/#5c6370] [#61afef]{project_name}[/#61afef]")
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
            elif cmd == "/clear":
                console.clear()
                print_banner()
            else:
                console.print(f"  [#e06c75]unknown: {cmd}[/#e06c75] [#5c6370]— type [#00ffaa]/help[/#00ffaa][/#5c6370]\n")
        else:
            handle_query(inp, show_sources)


if __name__ == "__main__":
    main()