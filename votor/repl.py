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
    "prompt":                             "#00ff9d bold",
    "arrow":                              "#5a5a7a",
    "path":                               "#5a5a7a",
    "completion-menu":                    "bg:default noinherit",
    "completion-menu.completion":         "fg:#3d4559 bg:default",
    "completion-menu.completion.current": "fg:#5a6a8a bg:default bold",
    "completion-menu.meta":               "fg:#2e3a4a bg:default",
    "completion-menu.meta.current":       "fg:#3d4f63 bg:default",
    "completion-menu.border":             "bg:default",
    "scrollbar":                          "bg:default",
    "scrollbar.background":               "bg:default",
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
                        display=cmd,
                        display_meta=f"  {desc}"
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
[bold #00ff9d]Commands[/bold #00ff9d]

  [#00ff9d]/init[/#00ff9d]              Initialize votor for this project
  [#00ff9d]/index[/#00ff9d]             Full re-index of entire project
  [#00ff9d]/update[/#00ff9d]            Re-index changed files only
  [#00ff9d]/status[/#00ff9d]            Show index health and analytics summary
  [#00ff9d]/dashboard[/#00ff9d]         Launch analytics dashboard in browser
  [#00ff9d]/history[/#00ff9d]           Show AI change history (git log)
  [#00ff9d]/undo[/#00ff9d]              Revert last AI change
  [#00ff9d]/revert [dim]<n>[/dim]       Revert to before change #n
  [#00ff9d]/diff [dim]<n>[/dim]         Show diff of change #n
  [#00ff9d]/config[/#00ff9d]            Show current configuration
  [#00ff9d]/provider[/#00ff9d]          Switch AI provider or model
  [#00ff9d]/sources[/#00ff9d]           Toggle showing retrieved sources
  [#00ff9d]/clear[/#00ff9d]             Clear the screen
  [#00ff9d]/help[/#00ff9d]              Show this help
  [#00ff9d]/exit[/#00ff9d]              Exit votor

[dim]Just type your question to query the codebase.[/dim]
[dim]Votor will find relevant context and respond.[/dim]
"""


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_banner():
    console.print(f"[bold #00ff9d]{BANNER}[/bold #00ff9d]")
    console.print(
        f"[dim]  project-aware coding assistant[/dim]  "
        f"[#5a5a7a]v0.1.0[/#5a5a7a]\n"
    )


def print_response(result: dict, show_sources: bool):
    console.print(Panel(
        Markdown(result["answer"]),
        title=f"[bold #00ff9d]votor[/bold #00ff9d] [dim]({result['provider']}/{result['model']})[/dim]",
        border_style="#1e1e2e",
        padding=(1, 2)
    ))

    if show_sources and result.get("sources"):
        source_table = Table(
            show_header=True,
            box=box.SIMPLE,
            header_style="dim",
            show_edge=False,
            padding=(0, 1)
        )
        source_table.add_column("file",      style="#00cfff", no_wrap=True)
        source_table.add_column("chunk",     justify="right", style="dim")
        source_table.add_column("relevance", justify="right")

        for s in result["sources"]:
            score       = s["score"]
            score_color = "#00ff9d" if score > 0.85 else "#f39c12" if score > 0.7 else "#e17055"
            source_table.add_row(
                s["file"],
                str(s["chunk"]),
                f"[{score_color}]{score:.0%}[/{score_color}]"
            )

        console.print(Panel(
            source_table,
            title="[dim]retrieved sources[/dim]",
            border_style="#1e1e2e",
            padding=(0, 1)
        ))

    savings = f"  [dim]saved[/dim] [#00ff9d]~{result.get('savings_pct', 0)}%[/#00ff9d]" \
              if result.get("savings_pct") else ""

    console.print(
        f"  [dim]tokens[/dim] [white]{result['total_tokens']}[/white]"
        f"  [dim]cost[/dim] [white]${result['cost']:.4f}[/white]"
        f"  [dim]time[/dim] [white]{result['response_time']:.2f}s[/white]"
        f"  [dim]retrieval[/dim] [white]{result['retrieval_score']:.0%}[/white]"
        f"  [dim]model[/dim] [#7c6af7]{result['model']}[/#7c6af7]"
        f"{savings}\n"
    )


def print_status(s: dict):
    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="white")
    table.add_row("indexed files",  str(s["total_files"]))
    table.add_row("indexed chunks", str(s["total_chunks"]))
    table.add_row("last indexed",   s["last_indexed"])
    table.add_row("total queries",  str(s["total_queries"]))
    table.add_row("total tokens",   str(s["total_tokens"]))
    table.add_row("total cost",     f"${s['total_cost']:.4f}")
    table.add_row("avg response",   f"{s['avg_response_time']:.2f}s")
    console.print(Panel(table, title="[dim]status[/dim]", border_style="#1e1e2e"))


def print_history(history: list):
    if not history:
        console.print("[dim]No AI changes recorded yet.[/dim]\n")
        return
    table = Table(show_header=True, box=box.SIMPLE, show_edge=False, padding=(0, 1))
    table.add_column("#",       style="dim",     width=4)
    table.add_column("hash",    style="#7c6af7", width=9)
    table.add_column("message", style="white")
    table.add_column("when",    style="dim")
    for h in history:
        table.add_row(str(h["n"]), h["hash"][:7], h["msg"], h["time"])
    console.print(Panel(table, title="[dim]ai change history[/dim]", border_style="#1e1e2e"))


def print_config(cfg: dict):
    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="#00ff9d")
    for k, v in cfg.items():
        if not isinstance(v, list):
            table.add_row(k.replace("_", " "), str(v))
    console.print(Panel(table, title="[dim]configuration[/dim]", border_style="#1e1e2e"))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_init(force: bool = False):
    console.print()
    try:
        from votor.init_flow import run_init
        result = run_init(force=force)

        # run_init returns (config, do_index) or just config if already initialized
        if isinstance(result, tuple):
            config, do_index = result
        else:
            config, do_index = result, False

        console.print(f"\n[#00ff9d]✓[/#00ff9d] Initialized.\n")

        if do_index:
            handle_index(full=True, config=config)

    except Exception as e:
        console.print(f"[#e17055]✗[/#e17055] Init failed: {e}\n")


def handle_index(full: bool = True, config: dict = None):
    label = "full index" if full else "incremental update"
    console.print(f"[dim]Running {label}...[/dim]\n")
    try:
        from votor.indexer import index_project
        stats = index_project(incremental=not full, force=full, config=config)
        console.print(
            f"[#00ff9d]✓[/#00ff9d] Indexed [white]{stats['files']}[/white] files, "
            f"[white]{stats['chunks']}[/white] chunks"
            + (f" [dim]({stats['updated']} updated, {stats['skipped']} skipped)[/dim]"
               if not full else "")
            + "\n"
        )
    except Exception as e:
        console.print(f"[#e17055]✗[/#e17055] Indexing failed: {e}\n")


def handle_status():
    try:
        from votor.db import get_stats
        from votor.analytics import get_summary
        stats   = get_stats()
        summary = get_summary()
        print_status({**stats, **summary})
    except Exception as e:
        console.print(f"[#e17055]Error: {e}[/#e17055]\n")


def handle_history():
    def handle_config():
        try:
            import json
            config_file = Path(".vectormind/config.json")
            if config_file.exists():
                with open(config_file) as f:
                    cfg = json.load(f)
                print_config(cfg)
            else:
                console.print("[dim]No config found. Run /init first.[/dim]\n")
        except Exception as e:
            console.print(f"[#e17055]Error: {e}[/#e17055]\n")
    try:
        from votor.tools import git_log
        history = git_log()
        print_history(history)
    except Exception as e:
        console.print(f"[#e17055]Error: {e}[/#e17055]\n")


def handle_undo():
    try:
        from votor.tools import git_undo
        console.print("[dim]Reverting last AI change...[/dim]")
        result = git_undo()
        if result["success"]:
            console.print(f"[#00ff9d]✓[/#00ff9d] [dim]Reverted: {result['reverted']}[/dim]\n")
        else:
            console.print(f"[#e17055]✗[/#e17055] {result['error']}\n")
    except Exception as e:
        console.print(f"[#e17055]Error: {e}[/#e17055]\n")


def handle_revert(args: list):
    if not args:
        console.print("[#e17055]Usage: /revert <n>[/#e17055]\n")
        return
    try:
        from votor.tools import git_revert_to
        n      = int(args[0])
        result = git_revert_to(n)
        if result["success"]:
            console.print(f"[#00ff9d]✓[/#00ff9d] Reverted {len(result['reverted'])} changes.\n")
        else:
            console.print(f"[#e17055]✗[/#e17055] {result['error']}\n")
    except ValueError:
        console.print("[#e17055]Usage: /revert <n> — n must be a number[/#e17055]\n")
    except Exception as e:
        console.print(f"[#e17055]Error: {e}[/#e17055]\n")


def handle_diff(args: list):
    if not args:
        console.print("[#e17055]Usage: /diff <n>[/#e17055]\n")
        return
    try:
        from votor.tools import git_diff, show_diff
        n      = int(args[0])
        result = git_diff(n)
        if result["success"]:
            show_diff(result["diff"], title=f"diff — change #{n}")
        else:
            console.print(f"[#e17055]✗[/#e17055] {result['error']}\n")
    except ValueError:
        console.print("[#e17055]Usage: /diff <n> — n must be a number[/#e17055]\n")
    except Exception as e:
        console.print(f"[#e17055]Error: {e}[/#e17055]\n")


def handle_config():
    try:
        import json
        config_file = Path(".vectormind/config.json")
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            print_config(cfg)
        else:
            console.print("[dim]No config found. Run /init first.[/dim]\n")
    except Exception as e:
        console.print(f"[#e17055]Error: {e}[/#e17055]\n")


def handle_provider():
    console.print(Panel(
        "[dim]To switch provider or model, run [white]/init[/white] again.\n"
        "Or edit [white].vectormind/config.json[/white] directly.[/dim]",
        title="[dim]provider[/dim]",
        border_style="#1e1e2e"
    ))


def handle_dashboard():
    console.print("[dim]Launching dashboard at [white]http://localhost:8000[/white]...[/dim]")
    import subprocess
    subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "votor.dashboard:app", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    console.print("[#00ff9d]✓[/#00ff9d] Dashboard running at [link=http://localhost:8000]http://localhost:8000[/link]\n")


def handle_query(question: str, show_sources: bool):
    if not VOTOR_DIR.exists():
        console.print("[#e17055]Not initialized. Run /init first.[/#e17055]\n")
        return

    try:
        from votor.query import run_query
        result = run_query(question, show_sources=show_sources)

        if result.get("error") == "no_context":
            console.print(f"[#e17055]{result['answer']}[/#e17055]\n")
            return

        print_response(result, show_sources)

    except Exception as e:
        console.print(f"[#e17055]Query failed: {e}[/#e17055]\n")


# ---------------------------------------------------------------------------
# Main REPL loop
# ---------------------------------------------------------------------------

def main():
    VOTOR_DIR.mkdir(exist_ok=True)

    print_banner()

    project_name = Path.cwd().name

    if not VOTOR_DIR.exists() or not (VOTOR_DIR / "config.json").exists():
        console.print(Panel(
            "[dim]No votor index found in this project.\n"
            "Run [white]/init[/white] to get started.[/dim]",
            border_style="#1e1e2e"
        ))
    else:
        console.print(f"[dim]  Project: [white]{project_name}[/white][/dim]")
        console.print(f"[dim]  Type [white]/help[/white] for commands or just ask a question.\n[/dim]")

    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=VotorCompleter(),
        complete_while_typing=True,
        complete_in_thread=False,
        complete_style=CompleteStyle.READLINE_LIKE,
        style=PROMPT_STYLE,
        mouse_support=False,
    )

    show_sources  = False
    _ctrl_c_count = 0

    while True:
        try:
            raw = session.prompt(HTML(
                f'<path>{project_name}</path> <arrow>›</arrow> <prompt>votor</prompt> '
            ))
        except KeyboardInterrupt:
            if _ctrl_c_count >= 1:
                console.print("\n[dim]bye.[/dim]\n")
                break
            _ctrl_c_count += 1
            console.print("\n[dim]Press Ctrl+C again or type /exit to quit.[/dim]\n")
            continue
        except EOFError:
            break

        _ctrl_c_count = 0
        inp = raw.strip()
        if not inp:
            continue

        if inp.startswith("/"):
            parts = inp.split()
            cmd   = parts[0].lower()
            args  = parts[1:]

            if cmd in ("/exit", "/quit"):
                console.print("\n[dim]bye.[/dim]\n")
                break
            elif cmd == "/help":
                console.print(Panel(HELP_TEXT, title="[dim]votor help[/dim]", border_style="#1e1e2e"))
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
                console.print(f"[dim]Sources: [white]{state}[/white][/dim]\n")
            elif cmd == "/clear":
                console.clear()
                print_banner()
            else:
                console.print(f"[#e17055]Unknown command: {cmd}[/#e17055] — type [white]/help[/white]\n")
        else:
            handle_query(inp, show_sources)


if __name__ == "__main__":
    main()