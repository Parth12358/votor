import json
import os
import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from prompt_toolkit import prompt
from prompt_toolkit.shortcuts import radiolist_dialog, checkboxlist_dialog
from prompt_toolkit.styles import Style

from votor.providers import (
    list_providers, list_models, list_embedding_models,
    validate_provider, provider_needs_key, PROVIDERS
)

console = Console()

VOTOR_DIR   = Path(".vectormind")
CONFIG_FILE = VOTOR_DIR / "config.json"
ENV_FILE    = Path(".env")

DIALOG_STYLE = Style.from_dict({
    "dialog":             "bg:#0a0a0f",
    "dialog.body":        "bg:#0a0a0f fg:#e2e2f0",
    "dialog.border":      "fg:#1e1e2e",
    "button":             "bg:#1e1e2e fg:#e2e2f0",
    "button.focused":     "bg:#00ff9d fg:#0a0a0f bold",
    "radio-list":         "bg:#0a0a0f fg:#e2e2f0",
    "radio-list focused": "fg:#00ff9d bold",
})

DEFAULT_CONFIG = {
    "main_provider":    "openai",
    "write_mode":       "edit",
    "main_model":       "gpt-4o-mini",
    "fallback_model":   "gpt-4o",
    "sub_provider":     "openai",
    "sub_model":        "gpt-4o-mini",
    "embedding_provider": "openai",
    "embedding_model":  "text-embedding-3-small",
    "top_k":            5,
    "chunk_size":       200,
    "chunk_overlap":    20,
    "git_remote":       "none",
    "git_remote_url":   "",
    "extensions": [
        ".py", ".js", ".ts", ".jsx", ".tsx",
        ".java", ".cpp", ".c", ".h", ".cs",
        ".go", ".rs", ".rb", ".php",
        ".md", ".txt", ".json", ".yaml", ".yml",
        ".toml", ".env.example"
    ],
    "exclude_dirs": [
        ".vectormind", ".git", "node_modules",
        "__pycache__", ".venv", "venv",
        "dist", "build", ".next"
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ask_choice(prompt_text: str, choices: list[str], default: str = None) -> str:
    """Arrow-key navigable radio list using prompt_toolkit."""
    default = default or choices[0]
    values  = [(c, c) for c in choices]

    result = radiolist_dialog(
        title=prompt_text,
        text="Use arrow keys to select, Enter to confirm:",
        values=values,
        default=default,
        style=DIALOG_STYLE,
    ).run()

    return result if result is not None else default


def ask_text(prompt_text: str, default: str = "") -> str:
    """Simple text input using prompt_toolkit."""
    from prompt_toolkit import prompt as pt_prompt
    console.print(f"\n[dim]{prompt_text}[/dim]")
    raw = pt_prompt(f"  [{default}]: ").strip()
    return raw if raw else default


def ask_yes_no(prompt_text: str, default: bool = True) -> bool:
    """Yes/no using arrow-key radio list."""
    choices = [("yes", "Yes"), ("no", "No")]
    default_val = "yes" if default else "no"

    result = radiolist_dialog(
        title=prompt_text,
        text="Use arrow keys to select, Enter to confirm:",
        values=choices,
        default=default_val,
        style=DIALOG_STYLE,
    ).run()

    if result is None:
        return default
    return result == "yes"
    return raw in ("y", "yes")


def write_env(key: str, value: str):
    """Write or update a key in .env file."""
    lines = []
    found = False

    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"{key}={value}\n")

    with open(ENV_FILE, "w") as f:
        f.writelines(new_lines)


def ensure_gitignore():
    """Make sure .env is in .gitignore."""
    gitignore = Path(".gitignore")
    entries = [".env\n", ".vectormind/qdrant/\n"]

    existing = ""
    if gitignore.exists():
        existing = gitignore.read_text()

    with open(gitignore, "a") as f:
        for entry in entries:
            if entry.strip() not in existing:
                f.write(entry)


def check_git() -> bool:
    """Check if project is a git repo."""
    return Path(".git").exists()


def init_git():
    """Run git init."""
    try:
        subprocess.run(["git", "init"], check=True, capture_output=True)
        console.print("[#00ff9d]✓[/#00ff9d] Git repository initialized.")
        return True
    except Exception:
        console.print("[#e17055]✗[/#e17055] Could not initialize git. Install git and try again.")
        return False


def set_git_remote(url: str):
    """Set git remote origin."""
    try:
        subprocess.run(["git", "remote", "add", "origin", url],
                       check=True, capture_output=True)
        console.print(f"[#00ff9d]✓[/#00ff9d] Remote set to {url}")
    except Exception:
        # Remote might already exist
        subprocess.run(["git", "remote", "set-url", "origin", url],
                       capture_output=True)
        console.print(f"[#00ff9d]✓[/#00ff9d] Remote updated to {url}")


# ---------------------------------------------------------------------------
# Setup sections
# ---------------------------------------------------------------------------

def setup_main_agent(config: dict) -> dict:
    """Configure main LLM provider and model."""
    console.print(Panel(
        "[dim]The main agent answers your coding questions.[/dim]",
        title="[#00ff9d]Main Agent[/#00ff9d]",
        border_style="#1e1e2e"
    ))

    provider = ask_choice(
        "Select provider for main agent:",
        list_providers(),
        default=config.get("main_provider", "openai")
    )

    # Check API key
    if provider_needs_key(provider):
        env_key = PROVIDERS[provider]["env_key"]
        existing = os.getenv(env_key, "")
        if not existing:
            console.print(f"\n[dim]No {env_key} found.[/dim]")
            key = ask_text(f"Enter your {provider} API key:", default="")
            if key:
                write_env(env_key, key)
                os.environ[env_key] = key
                console.print(f"[#00ff9d]✓[/#00ff9d] Saved to .env")
        else:
            console.print(f"[#00ff9d]✓[/#00ff9d] {env_key} found.")

    models = list_models(provider)
    model = ask_choice(
        f"Select model for main agent ({provider}):",
        models,
        default=models[0] if models else ""
    )

    # Fallback model (only for same provider)
    fallback = model
    if len(models) > 1:
        want_fallback = ask_yes_no("Set a fallback model for complex queries?", default=True)
        if want_fallback:
            remaining = [m for m in models if m != model]
            fallback = ask_choice("Select fallback model:", remaining, default=remaining[0])

    config["main_provider"] = provider
    config["main_model"]    = model
    config["fallback_model"] = fallback

    console.print(f"\n[#00ff9d]✓[/#00ff9d] Main agent: [white]{provider}[/white] / [white]{model}[/white]")
    return config


def setup_sub_agent(config: dict) -> dict:
    """Configure sub-agent provider and model."""
    console.print(Panel(
        "[dim]The sub-agent handles indexing, verification, and lightweight tasks.\n"
        "A smaller, faster model is recommended.[/dim]",
        title="[#00ff9d]Sub Agent[/#00ff9d]",
        border_style="#1e1e2e"
    ))

    same = ask_yes_no(
        f"Use same provider as main agent ({config['main_provider']})?",
        default=True
    )

    if same:
        provider = config["main_provider"]
    else:
        provider = ask_choice(
            "Select provider for sub agent:",
            list_providers(),
            default=config.get("sub_provider", "openai")
        )
        if provider_needs_key(provider) and provider != config["main_provider"]:
            env_key = PROVIDERS[provider]["env_key"]
            existing = os.getenv(env_key, "")
            if not existing:
                key = ask_text(f"Enter your {provider} API key:", default="")
                if key:
                    write_env(env_key, key)
                    os.environ[env_key] = key

    models = list_models(provider)
    model = ask_choice(
        f"Select model for sub agent ({provider}):",
        models,
        default=models[0] if models else ""
    )

    config["sub_provider"] = provider
    config["sub_model"]    = model

    console.print(f"\n[#00ff9d]✓[/#00ff9d] Sub agent: [white]{provider}[/white] / [white]{model}[/white]")
    return config


def setup_embeddings(config: dict) -> dict:
    """Configure embedding model."""
    console.print(Panel(
        "[dim]Embeddings convert your code into vectors for search.\n"
        "Anthropic and Groq use OpenAI embeddings by default.[/dim]",
        title="[#00ff9d]Embeddings[/#00ff9d]",
        border_style="#1e1e2e"
    ))

    # Determine available embedding providers
    embed_providers = [p for p in list_providers()
                       if list_embedding_models(p)]

    provider = ask_choice(
        "Select embedding provider:",
        embed_providers,
        default="openai"
    )

    models = list_embedding_models(provider)
    model = ask_choice(
        f"Select embedding model ({provider}):",
        models,
        default=models[0] if models else ""
    )

    config["embedding_provider"] = provider
    config["embedding_model"]    = model

    console.print(f"\n[#00ff9d]✓[/#00ff9d] Embeddings: [white]{provider}[/white] / [white]{model}[/white]")
    return config


def setup_git(config: dict) -> dict:
    """Configure git and optional remote."""
    console.print(Panel(
        "[dim]Votor commits every AI file change to git with a 'votor:' prefix.\n"
        "This gives you full undo/revert history.[/dim]",
        title="[#00ff9d]Git Setup[/#00ff9d]",
        border_style="#1e1e2e"
    ))

    if not check_git():
        init = ask_yes_no("No git repo found. Initialize one?", default=True)
        if init:
            init_git()
        else:
            console.print("[dim]Skipping git setup. AI changes won't be tracked.[/dim]")
            config["git_remote"] = "none"
            return config

    remote = ask_choice(
        "Set up remote sync for AI changes?",
        ["none", "github", "gitlab"],
        default="none"
    )

    config["git_remote"] = remote

    if remote in ("github", "gitlab"):
        url = ask_text(f"Enter your {remote} repository URL:")
        if url:
            config["git_remote_url"] = url
            set_git_remote(url)

            # Optional token for HTTPS push
            want_token = ask_yes_no("Store a personal access token for pushing?", default=False)
            if want_token:
                token = ask_text("Enter token (stored in .env):")
                if token:
                    write_env("VOTOR_GIT_TOKEN", token)

    console.print(f"\n[#00ff9d]✓[/#00ff9d] Git remote: [white]{remote}[/white]")
    return config


def setup_index_options(config: dict) -> dict:
    """Configure chunk size and top_k."""
    console.print(Panel(
        "[dim]These settings control how your project is indexed and retrieved.[/dim]",
        title="[#00ff9d]Index Settings[/#00ff9d]",
        border_style="#1e1e2e"
    ))

    chunk = ask_choice(
        "Chunk size (lines per chunk):",
        ["100", "200", "300", "500"],
        default="200"
    )
    config["chunk_size"] = int(chunk)

    top_k = ask_choice(
        "How many chunks to retrieve per query (top_k):",
        ["3", "5", "8", "10"],
        default="5"
    )
    config["top_k"] = int(top_k)

    console.print(f"\n[#00ff9d]✓[/#00ff9d] Chunk size: [white]{chunk}[/white] | Top K: [white]{top_k}[/white]")
    return config


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(config: dict):
    """Print final config summary before writing."""
    table = Table(show_header=False, box=box.SIMPLE, show_edge=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="#00ff9d")

    table.add_row("main agent",    f"{config['main_provider']} / {config['main_model']}")
    table.add_row("fallback",      config["fallback_model"])
    table.add_row("sub agent",     f"{config['sub_provider']} / {config['sub_model']}")
    table.add_row("embeddings",    f"{config['embedding_provider']} / {config['embedding_model']}")
    table.add_row("top k",         str(config["top_k"]))
    table.add_row("chunk size",    str(config["chunk_size"]))
    table.add_row("git remote",    config["git_remote"])

    console.print(Panel(table, title="[dim]configuration summary[/dim]", border_style="#1e1e2e"))


# ---------------------------------------------------------------------------
# Prompts writer
# ---------------------------------------------------------------------------

def write_prompts():
    """Write default prompts to .vectormind/prompts.json."""
    prompts_file = VOTOR_DIR / "prompts.json"

    prompts = {
        "system_prompt": "You are votor, a project-aware coding assistant.\nYou have been given relevant code chunks retrieved from the project's vector database.\nUse these chunks as your primary context to answer the user's question accurately.\n\nRules:\n- Answer questions using the retrieved context first\n- If you need to see a specific file that was not provided and is essential to answer,\n  output ONLY this JSON on its own line: {\"need_file\": \"path/to/file.py\"}\n  Do not guess. Only request a file if you are certain it exists and is essential.\n- You will receive at most one additional file — make your request count\n- Only use create_file or edit_file if the user explicitly asks you to create or modify something\n- Never delete files\n- Be concise but complete\n- Do not hallucinate code that isn't in the context\n- If a file retrieval result contains an error field, describe the error naturally — never echo raw JSON to the user",

        "classification_prompt": "You are a query classifier. Output JSON only. No text before or after.\n\nClassify the user query into one of three intents:\n\n intent = \"none\"  — general question, no files needed\n intent = \"read\"  — user wants to read/show/check a specific named file\n intent = \"write\" — user wants to create, edit, modify, refactor, fix, delete, or implement something\n\nRules:\n- intent = \"write\" when user uses: create, edit, modify, update, fix, refactor, rename, implement, build, add, remove, delete, change, rewrite\n- intent = \"read\" ONLY when user explicitly names a file AND uses: read, show, open, check, display\n- intent = \"none\" for all general questions about how code works\n- files = list of file paths explicitly mentioned, empty list if none\n\nExamples:\n  \"how does auth work?\" -> {\"intent\": \"none\", \"files\": [], \"reason\": \"general question\"}\n  \"read README.md\" -> {\"intent\": \"read\", \"files\": [\"README.md\"], \"reason\": \"explicit read request\"}\n  \"add rate limiting to login\" -> {\"intent\": \"write\", \"files\": [\"votor/auth.py\"], \"reason\": \"explicit edit request\"}\n  \"create a new utils file\" -> {\"intent\": \"write\", \"files\": [], \"reason\": \"create request\"}\n  \"fix the bug in providers.py\" -> {\"intent\": \"write\", \"files\": [\"votor/providers.py\"], \"reason\": \"fix request\"}\n  \"refactor the indexer\" -> {\"intent\": \"write\", \"files\": [\"votor/indexer.py\"], \"reason\": \"refactor request\"}\n\nOutput this exact JSON and nothing else:\n{\"intent\": \"none|read|write\", \"files\": [], \"reason\": \"...\"}",

        "sub_system_prompt": "You are a file retrieval agent. You ONLY call read_file.\nYou do NOT answer questions. You do NOT explain anything.\nRead each file in the provided list EXACTLY ONCE then stop immediately.\nDo NOT call read_file on the same file twice under any circumstances.\nIf the files list is empty, do absolutely nothing.\nNEVER invent or guess file paths. ONLY use exact paths from the files list.",

        "write_plan_prompt": "You are votor, a project-aware coding assistant in EDIT MODE.\nYou have been given the full contents of relevant files as tool results in this conversation.\nYour job is to output an exact write plan as JSON.\n\nCRITICAL RULES:\n- Output ONLY a JSON object with a write_plan array — no explanation, no markdown, no preamble\n- For edit steps: old_str must be copied VERBATIM from the file content shown in the tool results — character for character including whitespace and newlines\n- For create steps: content must be the complete file content\n- For delete steps: always set confirm to true\n- NEVER include both an edit/create AND a delete step for the same file in one plan\n- NEVER delete a file that you are also editing or creating\n- Steps must be in dependency order\n- File paths must be RELATIVE paths exactly as shown in the tool results — never use absolute paths\n- If you need to see additional files, output ONLY: {\"need_files\": [\"relative/path\"]}\n- Maximum 3 file request rounds — on the 3rd round output the plan with what you have\n\nWrite plan schema:\n{\n  \"write_plan\": [\n    {\"action\": \"edit\",   \"file\": \"relative/path\", \"old_str\": \"verbatim string from file\", \"new_str\": \"replacement\"},\n    {\"action\": \"create\", \"file\": \"relative/path\", \"content\": \"full file content\"},\n    {\"action\": \"delete\", \"file\": \"relative/path\", \"confirm\": true}\n  ]\n}",

        "write_summary_prompt": "You are votor, a project-aware coding assistant.\nYou have just executed a series of file changes on behalf of the user.\nSummarize what was done clearly and concisely.\n\nRules:\n- List each file that was successfully changed and what changed\n- List any steps that failed and why\n- Mention the git commits that were made\n- Be concise — no need to repeat the full diffs\n- If everything succeeded, end with a positive confirmation\n- If some steps failed, suggest what the user might do to fix them"
    }

    with open(prompts_file, "w") as f:
        json.dump(prompts, f, indent=2)

    console.print(f"[#00ff9d]✓[/#00ff9d] Prompts saved to [white].vectormind/prompts.json[/white]")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_init(force: bool = False) -> dict:
    """
    Run the interactive votor setup wizard.
    Creates .vectormind/, config.json, and .env.
    Returns final config dict.
    """
    # Check if already initialized
    if VOTOR_DIR.exists() and not force:
        console.print(Panel(
            "[dim]Votor is already initialized in this project.\n"
            "Run with force=True or use [white]/init[/white] again to reconfigure.[/dim]",
            border_style="#1e1e2e"
        ))
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                return json.load(f)
        return DEFAULT_CONFIG

    console.print(Panel(
        "[bold #00ff9d]Welcome to votor setup[/bold #00ff9d]\n\n"
        "[dim]This wizard will configure your AI provider, models, embeddings,\n"
        "and git settings for this project.[/dim]",
        border_style="#00ff9d",
        padding=(1, 2)
    ))

    # Create directories
    VOTOR_DIR.mkdir(exist_ok=True)
    (VOTOR_DIR / "qdrant").mkdir(exist_ok=True)

    # Start from defaults
    config = DEFAULT_CONFIG.copy()

    # Ensure .env exists
    if not ENV_FILE.exists():
        ENV_FILE.touch()
        console.print("[#00ff9d]✓[/#00ff9d] Created .env")

    # Ensure .gitignore
    ensure_gitignore()
    console.print("[#00ff9d]✓[/#00ff9d] Updated .gitignore\n")

    # Run setup sections
    config = setup_main_agent(config)
    config = setup_sub_agent(config)
    config = setup_embeddings(config)
    config = setup_git(config)
    config = setup_index_options(config)

    # Print summary
    console.print()
    print_summary(config)

    # Confirm
    confirmed = ask_yes_no("Save this configuration?", default=True)
    if not confirmed:
        console.print("[dim]Setup cancelled.[/dim]")
        return config

    # Write config
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    console.print(f"\n[#00ff9d]✓[/#00ff9d] Config saved to [white].vectormind/config.json[/white]")

    write_prompts()

    # Ask about full index
    do_index = ask_yes_no("\nRun full project index now?", default=True)

    return config, do_index