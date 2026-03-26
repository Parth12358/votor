import json
import os
import subprocess
from pathlib import Path

from rich.console import Console

from votor.providers import (
    list_providers, list_models, list_embedding_models,
    validate_provider, provider_needs_key, PROVIDERS
)

console = Console()

VOTOR_DIR   = Path(".vectormind")

CONFIG_FILE = VOTOR_DIR / "config.json"
ENV_FILE    = Path(".env")


DEFAULT_CONFIG = {
    "main_provider":    "openai",
    "write_mode":       "edit",
    "verify_changes":   False,
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
        "dist", "build", ".next", "votor.egg-info"
    ]
}


# ---------------------------------------------------------------------------
# Step UI helpers
# ---------------------------------------------------------------------------

def _step_header(title: str, description: str):
    """Print a clean step header with title and description."""
    console.print()
    console.rule(style="#1e1e2e")
    console.print(f"  [#5c6370]{title}[/#5c6370]")
    console.print(f"  [#abb2bf]{description}[/#abb2bf]")
    console.print()


def _key_hints():
    """Print key guide below every interactive prompt."""
    console.print(
        f"\n  [#5c6370]e[/#5c6370] [#abb2bf]edit[/#abb2bf]   "
        f"[#5c6370]s[/#5c6370] [#abb2bf]skip[/#abb2bf]   "
        f"[#5c6370]?[/#5c6370] [#abb2bf]explain[/#abb2bf]   "
        f"[#5c6370]q[/#5c6370] [#abb2bf]quit[/#abb2bf]"
    )


def _step_confirm(label: str, value: str, color: str = "#61afef"):
    """Print confirmation line after a step completes."""
    console.print(f"\n  [#00ffaa]✓[/#00ffaa]  [#5c6370]{label}[/#5c6370] [{color}]{value}[/{color}]")


STEP_EXPLANATIONS = {
    "main_agent": (
        "The main agent answers your coding questions and plans file changes.\n"
        "  Use a strong API model (claude-haiku, gpt-4o-mini) for best results.\n"
        "  Local models (ollama) work but give lower quality answers."
    ),
    "sub_agent": (
        "The sub agent classifies your intent and reads files — it never reasons.\n"
        "  A small fast local model (qwen2.5:7b) is ideal here.\n"
        "  Sub runs free locally — it never touches the paid API."
    ),
    "embeddings": (
        "Embeddings convert your code into vectors for semantic search.\n"
        "  nomic-embed-text (ollama) is free and works well.\n"
        "  Must match the model used at index time — changing this requires /index --force."
    ),
    "git": (
        "Votor commits every AI file change to git with a votor: prefix.\n"
        "  This gives you full history, undo, and revert for all AI changes.\n"
        "  Git is required for edit mode to work."
    ),
    "index": (
        "Chunk size controls how files are split for indexing.\n"
        "  Smaller chunks = more precise retrieval but more vectors.\n"
        "  top_k controls how many chunks are retrieved per query."
    ),
}


def _explain(step_key: str):
    """Print explanation for a step."""
    text = STEP_EXPLANATIONS.get(step_key, "No explanation available for this step.")
    console.print()
    console.print(f"  [#e5c07b]?[/#e5c07b]  [#abb2bf]{text}[/#abb2bf]")
    console.print()


def _check_quit(result):
    """If result is None (user cancelled), ask to confirm quit."""
    if result is None:
        console.print(f"\n  [#e06c75]q[/#e06c75]  [#abb2bf]Quit setup? All progress will be lost.[/#abb2bf]")
        confirm = ask_yes_no("Quit setup?", default=False)
        if confirm:
            console.print(f"\n  [#5c6370]Setup cancelled.[/#5c6370]\n")
            raise SystemExit(0)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ask_provider(prompt_text: str, default: str = "openai") -> str:
    """Show available providers as a hint then accept free-text input."""
    providers = list_providers()
    hint = " / ".join(f"[#c678dd]{p}[/#c678dd]" for p in providers)
    console.print(f"\n  [#5c6370]{prompt_text}[/#5c6370]")
    console.print(f"  [#5c6370]options:[/#5c6370] {hint}")

    while True:
        from prompt_toolkit import prompt as pt_prompt
        raw = pt_prompt(f"  [{default}]: ").strip().lower()
        value = raw if raw else default

        if value in providers:
            return value

        console.print(f"  [#e06c75]✗[/#e06c75]  [#abb2bf]Unknown provider. Choose from: {', '.join(providers)}[/#abb2bf]")


def ask_choice(prompt_text: str, choices: list[str], default: str = None) -> str:
    """Plain keyboard input with options shown as hint."""
    from prompt_toolkit import prompt as pt_prompt
    default = default or choices[0]
    hint = " / ".join(choices)
    console.print(f"\n  [#5c6370]{prompt_text}[/#5c6370]")
    console.print(f"  [#5c6370]options:[/#5c6370] [#abb2bf]{hint}[/#abb2bf]")

    while True:
        raw = pt_prompt(f"  [{default}]: ").strip()
        value = raw if raw else default
        if value in choices:
            return value
        console.print(f"  [#e06c75]✗[/#e06c75]  [#abb2bf]Invalid choice. Options: {', '.join(choices)}[/#abb2bf]")


def ask_text(prompt_text: str, default: str = "") -> str:
    """Simple text input using prompt_toolkit."""
    from prompt_toolkit import prompt as pt_prompt
    console.print(f"\n[dim]{prompt_text}[/dim]")
    raw = pt_prompt(f"  [{default}]: ").strip()
    return raw if raw else default


def ask_yes_no(prompt_text: str, default: bool = True) -> bool:
    """Plain y/n keyboard input."""
    from prompt_toolkit import prompt as pt_prompt
    default_str = "Y/n" if default else "y/N"
    console.print(f"\n  [#5c6370]{prompt_text}[/#5c6370]")
    raw = pt_prompt(f"  [{default_str}]: ").strip().lower()
    if not raw:
        return default
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

def setup_main_agent(config: dict, redo: bool = False) -> dict:
    """Configure main LLM provider and model."""
    _step_header(
        "main agent",
        "Answers your questions and plans file changes. Use a strong model here."
    )
    _key_hints()

    provider = ask_provider(
        "Provider for main agent:",
        default=config.get("main_provider", "openai")
    )

    # Check API key
    if provider_needs_key(provider):
        env_key = PROVIDERS[provider]["env_key"]
        existing = os.getenv(env_key, "")
        if not existing:
            console.print(f"\n  [#e5c07b]![/#e5c07b]  [#abb2bf]No {env_key} found in .env[/#abb2bf]")
            key = ask_text(f"Enter your {provider} API key:", default="")
            if key:
                write_env(env_key, key)
                os.environ[env_key] = key
                console.print(f"  [#00ffaa]✓[/#00ffaa]  [#5c6370]Saved to .env[/#5c6370]")
        else:
            console.print(f"  [#00ffaa]✓[/#00ffaa]  [#5c6370]{env_key} found[/#5c6370]")

    if provider == "ollama":
        model = ask_text(
            "Ollama model name for main agent (e.g. qwen2.5:14b, llama3.1:8b):",
            default="qwen2.5:14b"
        )
    else:
        models = list_models(provider)
        model = ask_choice(
            f"Model for main agent ({provider}):",
            models,
            default=models[0] if models else ""
        )
        _check_quit(model)

    # Fallback
    fallback = model
    if provider == "ollama":
        want_fallback = ask_yes_no("Set a fallback model?", default=True)
        if want_fallback:
            fallback = ask_text(
                "Fallback model name (e.g. qwen2.5:14b):",
                default="qwen2.5:14b"
            )
    elif len(list_models(provider)) > 1:
        want_fallback = ask_yes_no("Set a fallback model for complex queries?", default=True)
        if want_fallback:
            remaining = [m for m in list_models(provider) if m != model]
            fallback = ask_choice("Fallback model:", remaining, default=remaining[0])
            _check_quit(fallback)

    config["main_provider"]  = provider
    config["main_model"]     = model
    config["fallback_model"] = fallback

    _step_confirm("main agent", f"{provider} / {model}", color="#c678dd")
    if fallback != model:
        _step_confirm("fallback", fallback, color="#5c6370")

    return config


def setup_sub_agent(config: dict) -> dict:
    """Configure sub-agent provider and model."""
    _step_header(
        "sub agent",
        "Classifies intent and reads files. Local small model recommended — runs free."
    )
    _key_hints()

    same = ask_yes_no(
        f"Use same provider as main ({config['main_provider']})?",
        default=config["main_provider"] == "ollama"
    )

    if same:
        provider = config["main_provider"]
    else:
        provider = ask_provider(
            "Provider for sub agent:",
            default=config.get("sub_provider", "ollama")
        )
        if provider_needs_key(provider) and provider != config["main_provider"]:
            env_key = PROVIDERS[provider]["env_key"]
            existing = os.getenv(env_key, "")
            if not existing:
                key = ask_text(f"Enter your {provider} API key:", default="")
                if key:
                    write_env(env_key, key)
                    os.environ[env_key] = key

    if provider == "ollama":
        model = ask_text(
            "Ollama model name for sub agent (e.g. qwen2.5:7b, qwen2.5:1.5b):",
            default="qwen2.5:7b"
        )
    else:
        models = list_models(provider)
        model = ask_choice(
            f"Model for sub agent ({provider}):",
            models,
            default=models[0] if models else ""
        )
        _check_quit(model)

    config["sub_provider"] = provider
    config["sub_model"]    = model

    _step_confirm("sub agent", f"{provider} / {model}", color="#c678dd")
    return config


def setup_embeddings(config: dict) -> dict:
    """Configure embedding model."""
    _step_header(
        "embeddings",
        "Converts code into vectors for search. Must stay consistent — changing requires re-index."
    )
    _key_hints()

    provider = ask_provider(
        "Embedding provider:",
        default=config.get("embedding_provider", "ollama")
    )

    if provider == "ollama":
        model = ask_text(
            "Ollama embedding model (e.g. nomic-embed-text, mxbai-embed-large):",
            default="nomic-embed-text"
        )
    else:
        models = list_embedding_models(provider)
        model = ask_choice(
            f"Embedding model ({provider}):",
            models,
            default=models[0] if models else ""
        )
        _check_quit(model)

    config["embedding_provider"] = provider
    config["embedding_model"]    = model

    _step_confirm("embeddings", f"{provider} / {model}", color="#c678dd")
    return config


def setup_git(config: dict) -> dict:
    """Configure git and optional remote."""
    _step_header(
        "git",
        "Every AI change is committed with a votor: prefix. Required for edit mode."
    )
    _key_hints()

    if not check_git():
        console.print(f"  [#e5c07b]![/#e5c07b]  [#abb2bf]No git repository found in this directory.[/#abb2bf]")
        init = ask_yes_no("Initialize a git repository?", default=True)
        if init:
            init_git()
        else:
            console.print(f"  [#5c6370]Skipping git. AI changes won't be tracked.[/#5c6370]")
            config["git_remote"] = "none"
            return config

    remote = ask_choice(
        "Set up a git remote?",
        ["none", "github", "gitlab"],
        default="none"
    )
    _check_quit(remote)
    config["git_remote"] = remote

    if remote in ("github", "gitlab"):
        url = ask_text(f"{remote} repository URL:")
        if url:
            config["git_remote_url"] = url
            set_git_remote(url)
            want_token = ask_yes_no("Store a personal access token for pushing?", default=False)
            if want_token:
                token = ask_text("Personal access token (stored in .env):")
                if token:
                    write_env("VOTOR_GIT_TOKEN", token)

    _step_confirm("git remote", remote)
    return config


def setup_index_options(config: dict) -> dict:
    """Configure chunk size and top_k."""
    _step_header(
        "index settings",
        "Controls how files are chunked and how many chunks are retrieved per query."
    )
    _key_hints()

    chunk = ask_choice(
        "Chunk size (words per chunk):",
        ["100", "200", "300", "500"],
        default="200"
    )
    _check_quit(chunk)
    config["chunk_size"] = int(chunk)

    top_k = ask_choice(
        "Chunks retrieved per query (top_k):",
        ["3", "5", "8", "10"],
        default="5"
    )
    _check_quit(top_k)
    config["top_k"] = int(top_k)

    _step_confirm("chunk size", chunk)
    _step_confirm("top k", top_k)
    return config


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(config: dict):
    """Print final config summary before writing."""
    console.print()
    console.rule(style="#1e1e2e")
    console.print(f"  [#5c6370]summary[/#5c6370]\n")
    console.print(f"  [#5c6370]main[/#5c6370]       [#c678dd]{config['main_provider']}[/#c678dd] [#abb2bf]/[/#abb2bf] [#61afef]{config['main_model']}[/#61afef]")
    console.print(f"  [#5c6370]fallback[/#5c6370]   [#61afef]{config['fallback_model']}[/#61afef]")
    console.print(f"  [#5c6370]sub[/#5c6370]        [#c678dd]{config['sub_provider']}[/#c678dd] [#abb2bf]/[/#abb2bf] [#61afef]{config['sub_model']}[/#61afef]")
    console.print(f"  [#5c6370]embed[/#5c6370]      [#c678dd]{config['embedding_provider']}[/#c678dd] [#abb2bf]/[/#abb2bf] [#61afef]{config['embedding_model']}[/#61afef]")
    console.print(f"  [#5c6370]top k[/#5c6370]      [#abb2bf]{config['top_k']}[/#abb2bf]")
    console.print(f"  [#5c6370]chunk[/#5c6370]      [#abb2bf]{config['chunk_size']}[/#abb2bf]")
    console.print(f"  [#5c6370]git[/#5c6370]        [#abb2bf]{config['git_remote']}[/#abb2bf]")
    console.print()


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

        "write_plan_prompt": "You are votor, a project-aware coding assistant in EDIT MODE.\nYou have been given the full contents of relevant files as tool results.\nYour job is to output an exact write plan as JSON.\n\nCRITICAL RULES:\n- Output ONLY a JSON object with a write_plan array — no explanation, no markdown, no preamble\n- For edit steps: use start_line and end_line (1-indexed) to specify which lines to replace\n- For insert steps: set end_line lower than start_line to insert before start_line without replacing anything\n- For create steps: content must be the complete file content\n- For delete steps: always set confirm to true\n- NEVER include both an edit/create AND a delete step for the same file\n- If a file appears in the tool results (sub already read it), that file EXISTS — always use edit action, NEVER create for it\n- Steps must be in dependency order — if file A imports or depends on file B, plan file B's changes before file A's\n- When changes span multiple files, request ALL needed files in a single need_files call rather than one at a time\n- File paths must be RELATIVE paths exactly as shown in the tool results\n- If you need additional files, output ONLY: {\"need_files\": [\"relative/path/a.py\", \"relative/path/b.py\"]}\n- Maximum 5 file request rounds\n\nWrite plan schema:\n{\n  \"write_plan\": [\n    {\"action\": \"edit\",   \"file\": \"relative/path\", \"start_line\": 1, \"end_line\": 3, \"new_content\": \"replacement text\"},\n    {\"action\": \"edit\",   \"file\": \"relative/path\", \"start_line\": 5, \"end_line\": 4, \"new_content\": \"inserted before line 5\"},\n    {\"action\": \"create\", \"file\": \"relative/path\", \"content\": \"full file content\"},\n    {\"action\": \"delete\", \"file\": \"relative/path\", \"confirm\": true}\n  ]\n}",

        "write_summary_prompt": "You are votor, a project-aware coding assistant.\nYou have just executed a series of file changes on behalf of the user.\nSummarize what was done clearly and concisely.\n\nRules:\n- List each file that was successfully changed and what changed\n- List any steps that failed and why\n- Mention the git commits that were made\n- Be concise — no need to repeat the full diffs\n- Never suggest git commands directly — only reference votor commands like /undo, /diff, /history for follow-up actions\n- If everything succeeded, end with a positive confirmation\n- If some steps failed, suggest what the user might do to fix them",

        "verify_changes_prompt": "You are votor, a project-aware coding assistant.\nYou have just made file changes on behalf of the user.\nReview the diffs and the full file contents after editing.\n\nYour job:\n- Check if the changes correctly implement what the user asked for\n- Identify any issues: wrong lines changed, missing changes, syntax errors, logic problems\n- Be concise — note what is correct and what is wrong\n- Do NOT suggest a new plan — just report what you see\n\nEnd your response with either:\n  VERIFIED — changes look correct\n  ISSUES FOUND — [brief description of problems]"
    }

    with open(prompts_file, "w") as f:
        json.dump(prompts, f, indent=2)

    # (confirmation printed by run_init after write_prompts() returns)


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
    if VOTOR_DIR.exists() and CONFIG_FILE.exists() and not force:
        with open(CONFIG_FILE) as f:
            existing = json.load(f)

        console.print()
        console.print(f"  [#00ffaa]votor[/#00ffaa] [#5c6370]already configured in this project[/#5c6370]")
        console.print(f"  [#5c6370]run [#00ffaa]/init --force[/#00ffaa] to reconfigure[/#5c6370]")
        console.print()
        print_summary(existing)
        return existing

    console.print()
    console.print(f"  [bold #00ffaa]votor[/bold #00ffaa] [#5c6370]setup wizard[/#5c6370]")
    console.print(f"  [#5c6370]Configure your AI providers, models, and index settings.[/#5c6370]")
    console.print(f"  [#5c6370]At each step:[/#5c6370] "
                  f"[#5c6370]e[/#5c6370] [#abb2bf]edit[/#abb2bf]  "
                  f"[#5c6370]s[/#5c6370] [#abb2bf]skip[/#abb2bf]  "
                  f"[#5c6370]?[/#5c6370] [#abb2bf]explain[/#abb2bf]  "
                  f"[#5c6370]q[/#5c6370] [#abb2bf]quit[/#abb2bf]")
    console.print()

    # Create directories
    VOTOR_DIR.mkdir(exist_ok=True)
    (VOTOR_DIR / "qdrant").mkdir(exist_ok=True)

    # Start from defaults
    config = DEFAULT_CONFIG.copy()

    # Ensure .env exists
    if not ENV_FILE.exists():
        ENV_FILE.touch()
        console.print(f"  [#00ffaa]✓[/#00ffaa]  [#5c6370]created[/#5c6370]  [#61afef].env[/#61afef]")

    # Ensure .gitignore
    ensure_gitignore()
    console.print(f"  [#00ffaa]✓[/#00ffaa]  [#5c6370]updated[/#5c6370]  [#61afef].gitignore[/#61afef]")

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
        console.print(f"\n  [#5c6370]Setup cancelled.[/#5c6370]")
        return config

    # Write config
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    console.print(f"\n  [#00ffaa]✓[/#00ffaa]  [#5c6370]config saved[/#5c6370]  [#61afef].vectormind/config.json[/#61afef]")

    write_prompts()
    console.print(f"  [#00ffaa]✓[/#00ffaa]  [#5c6370]prompts saved[/#5c6370]  [#61afef].vectormind/prompts.json[/#61afef]")
    console.print()
    console.print(f"  [#5c6370]next →[/#5c6370] [#abb2bf]index your project so votor can answer questions about it[/#abb2bf]")
    console.print()

    # Ask about full index
    do_index = ask_yes_no("Run full index now?", default=True)

    return config, do_index