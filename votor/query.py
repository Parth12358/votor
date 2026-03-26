import time
import json
import tiktoken
from pathlib import Path

from rich.console import Console

from votor.providers import call_llm, embed_query, calculate_cost
from votor.db import get_collection, query_chunks
from votor.analytics import log_query
from votor.tools import dispatch_tool, TOOL_DEFINITIONS, show_diff

console = Console()

CONFIG_FILE  = Path(".vectormind/config.json")
PROMPTS_FILE = Path(".vectormind/prompts.json")

# Module-level caches
_prompts_cache: dict | None = None
_full_context_tokens_cache: int  = 0
_full_context_tokens_dirty: bool = True

# Prompts are loaded from .vectormind/prompts.json at query time
# to prevent prompt strings from being indexed and poisoning retrieval

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "main_provider":   "openai",
        "main_model":      "gpt-4o-mini",
        "fallback_model":  "gpt-4o",
        "embedding_model": "text-embedding-3-small",
        "top_k":           5,
    }


def load_prompts() -> dict:
    """
    Load prompt strings from .vectormind/prompts.json. Cached for session lifetime.
    Falls back to empty strings — surfaces as LLM errors, not silent failures.
    """
    global _prompts_cache
    if _prompts_cache is not None:
        return _prompts_cache
    if PROMPTS_FILE.exists():
        with open(PROMPTS_FILE) as f:
            _prompts_cache = json.load(f)
    else:
        _prompts_cache = {
            "system_prompt":         "",
            "classification_prompt": "",
            "sub_system_prompt":     "",
        }
    return _prompts_cache


def invalidate_prompts_cache():
    """Call after prompts file changes (e.g. /init --force)."""
    global _prompts_cache
    _prompts_cache = None


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(text: str, model: str = "gpt-4o") -> int:
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.encoding_for_model("gpt-4o")
    return len(enc.encode(text))


def estimate_full_context_tokens() -> int:
    try:
        client, _ = get_collection()
        from votor.db import list_indexed_files
        files = list_indexed_files(client)
        total = 0
        enc = tiktoken.encoding_for_model("gpt-4o")
        for file_path in files:
            p = Path(file_path)
            if p.exists():
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    total += len(enc.encode(text))
                except Exception:
                    pass
        return total
    except Exception:
        return 0


def get_full_context_tokens() -> int:
    """
    Return cached full context token count.
    Recomputes only when cache is marked dirty (after index/update).
    Avoids reading and tokenizing all project files on every query.
    """
    global _full_context_tokens_cache, _full_context_tokens_dirty
    if _full_context_tokens_dirty:
        _full_context_tokens_cache = estimate_full_context_tokens()
        _full_context_tokens_dirty = False
    return _full_context_tokens_cache


def invalidate_full_context_cache():
    """Mark cache as dirty. Call after /index or /update."""
    global _full_context_tokens_dirty
    _full_context_tokens_dirty = True


# Per-thread headless flag — prevents concurrent REPL and dashboard queries from sharing state
import threading as _threading
_tls = _threading.local()

def _is_headless() -> bool:
    return getattr(_tls, "headless_mode", False)

def _set_headless(value: bool):
    _tls.headless_mode = value


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

def assemble_context(documents: list, metadatas: list, scores: list) -> str:
    file_chunks: dict = {}
    for doc, meta, score in zip(documents, metadatas, scores):
        file = meta.get("file", "unknown")
        if file not in file_chunks:
            file_chunks[file] = []
        file_chunks[file].append({
            "content":     doc,
            "chunk_index": meta.get("chunk_index", 0),
            "score":       score
        })

    parts = []
    for file, chunks in file_chunks.items():
        chunks.sort(key=lambda x: x["chunk_index"])
        part = f"### {file}\n"
        for chunk in chunks:
            part += f"// relevance: {chunk['score']}\n"
            part += chunk["content"] + "\n"
        parts.append(part)

    return "\n---\n".join(parts)


# ---------------------------------------------------------------------------
# Tool definitions per provider
# ---------------------------------------------------------------------------

def get_tools_for_provider(provider: str) -> list:
    if provider in ("openai", "groq", "ollama"):
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            k: {"type": "string", "description": v}
                            for k, v in t["parameters"].items()
                        },
                        "required": list(t["parameters"].keys())
                    }
                }
            }
            for t in TOOL_DEFINITIONS
        ]
    elif provider == "anthropic":
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        k: {"type": "string", "description": v}
                        for k, v in t["parameters"].items()
                    },
                    "required": list(t["parameters"].keys())
                }
            }
            for t in TOOL_DEFINITIONS
        ]
    return []


# ---------------------------------------------------------------------------
# Tool call loop
# ---------------------------------------------------------------------------

def run_tool_loop(
    provider: str,
    model: str,
    messages: list,
    allow_tools: bool = True,
    max_iterations: int = 5
) -> dict:
    total_input  = 0
    total_output = 0
    iterations   = 0
    _read_cache: dict[str, dict] = {}  # dedup within this turn only

    while iterations < max_iterations:
        iterations += 1

        if not allow_tools:
            # Plain call, no tools
            result = call_llm(provider, model, messages)
            return {
                "content":       result["content"],
                "input_tokens":  result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "model":         model,
                "provider":      provider,
            }

        if provider in ("openai", "groq", "ollama"):
            result = _call_openai_with_tools(provider, model, messages)
        elif provider == "anthropic":
            result = _call_anthropic_with_tools(provider, model, messages)
        else:
            result = call_llm(provider, model, messages)
            return {
                "content":       result["content"],
                "input_tokens":  result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "model":         model,
                "provider":      provider,
            }

        total_input  += result.get("input_tokens", 0)
        total_output += result.get("output_tokens", 0)

        # No tool calls — done
        if not result.get("tool_calls"):
            return {
                "content":       result["content"],
                "input_tokens":  total_input,
                "output_tokens": total_output,
                "model":         model,
                "provider":      provider,
            }

        # Append assistant message with tool calls
        messages.append({
            "role":    "assistant",
            "content": result.get("content") or None,
            "tool_calls": [
                {
                    "id":       tc["id"],
                    "type":     "function",
                    "function": {
                        "name":      tc["name"],
                        "arguments": json.dumps(tc["params"])
                    }
                }
                for tc in result["tool_calls"]
            ]
        })

        for tc in result["tool_calls"]:
            tool_name   = tc["name"]
            tool_params = tc["params"]

            console.print(f"  [#5c6370]→ {tool_name}([#61afef]{tool_params.get('path', '')}[/#61afef])[/#5c6370]")

            if tool_name == "read_file":
                path_key = tool_params.get("path", "")
                if path_key in _read_cache:
                    tool_result = _read_cache[path_key]
                else:
                    tool_result = dispatch_tool(tool_name, tool_params)
                    _read_cache[path_key] = tool_result
            else:
                tool_result = dispatch_tool(tool_name, tool_params)

            if tool_name == "edit_file" and tool_result.get("success") and tool_result.get("diff_preview"):
                show_diff(tool_result["diff_preview"], title=f"edit — {tool_params.get('path', '')}")

            # truncate large read_file results before they enter context
            CONTENT_CHAR_LIMIT = 8_000
            if tool_name == "read_file" and tool_result.get("content"):
                c = tool_result["content"]
                if len(c) > CONTENT_CHAR_LIMIT:
                    tool_result = dict(tool_result)  # don't mutate cache entry
                    tool_result["content"] = c[:CONTENT_CHAR_LIMIT]
                    tool_result["truncated"] = True
                    tool_result["truncated_note"] = (
                        f"Content truncated to {CONTENT_CHAR_LIMIT} chars. "
                        "Use a more specific query or request a section."
                    )

            if provider in ("openai", "groq", "ollama"):
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      json.dumps(tool_result)
                })
            elif provider == "anthropic":
                messages.append({
                    "role": "user",
                    "content": [{
                        "type":        "tool_result",
                        "tool_use_id": tc["id"],
                        "content":     json.dumps(tool_result)
                    }]
                })

    return {
        "content":       "Reached maximum tool iterations.",
        "input_tokens":  total_input,
        "output_tokens": total_output,
        "model":         model,
        "provider":      provider,
    }


def _call_openai_with_tools(provider: str, model: str, messages: list) -> dict:
    from votor.providers import get_llm_client
    client = get_llm_client(provider)
    tools  = get_tools_for_provider(provider)

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
        temperature=0.2,
        max_tokens=2048,
    )

    choice  = response.choices[0]
    message = choice.message

    tool_calls = []
    if message.tool_calls:
        for tc in message.tool_calls:
            try:
                params = json.loads(tc.function.arguments)
            except Exception:
                params = {}
            tool_calls.append({
                "id":     tc.id,
                "name":   tc.function.name,
                "params": params,
            })

    return {
        "content":       message.content or "",
        "tool_calls":    tool_calls,
        "input_tokens":  response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
    }


def _call_anthropic_with_tools(provider: str, model: str, messages: list) -> dict:
    from votor.providers import get_llm_client
    client = get_llm_client("anthropic")
    tools  = get_tools_for_provider("anthropic")

    system_msg    = ""
    user_messages = []
    for m in messages:
        if m["role"] == "system":
            system_msg = m["content"]
        else:
            user_messages.append(m)

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_msg,
        messages=user_messages,
        tools=tools,
        temperature=0.2,
    )

    content    = ""
    tool_calls = []

    for block in response.content:
        if block.type == "text":
            content = block.text
        elif block.type == "tool_use":
            tool_calls.append({
                "id":     block.id,
                "name":   block.name,
                "params": block.input,
            })

    return {
        "content":       content,
        "tool_calls":    tool_calls,
        "input_tokens":  response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


# ---------------------------------------------------------------------------
# Sub agent — intent classification
# ---------------------------------------------------------------------------

def classify_intent(question: str, config: dict) -> dict:
    """
    Use sub model to classify whether a query requires tool use.
    Returns:
        {
            "needs_tools":  bool,
            "likely_files": list[str],
            "reason":       str,
            "input_tokens":  int,
            "output_tokens": int,
        }
    """
    sub_provider = config.get("sub_provider", config.get("main_provider", "openai"))
    sub_model    = config.get("sub_model",    config.get("main_model",    "gpt-4o-mini"))

    classification_prompt = load_prompts()["classification_prompt"]

    messages = [
        {"role": "system", "content": classification_prompt},
        {"role": "user",   "content": question}
    ]

    try:
        result = call_llm(sub_provider, sub_model, messages, max_tokens=150)
        content = result["content"].strip()

        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        parsed = json.loads(content)
        return {
            "intent":        parsed.get("intent", "none"),  # "none" | "read" | "write"
            "files":         parsed.get("files", []),
            "reason":        parsed.get("reason", ""),
            "input_tokens":  result["input_tokens"],
            "output_tokens": result["output_tokens"],
        }
    except Exception:
        return {"intent": "none", "files": [], "reason": "classification_failed",
                "input_tokens": 0, "output_tokens": 0}


# ---------------------------------------------------------------------------
# Sub agent — tool execution loop
# ---------------------------------------------------------------------------

def run_sub_tool_loop(
    config: dict,
    base_context: str,
    question: str,
    likely_files: list,
    max_iterations: int = 8
) -> dict:
    """
    Sub model tool execution loop. Reads files and assembles enriched context for main.
    Returns:
        {
            "enriched_context": str,
            "files_read":       list,
            "tool_calls":       int,
            "input_tokens":     int,
            "output_tokens":    int,
        }
    """
    sub_provider = config.get("sub_provider", config.get("main_provider", "openai"))
    sub_model    = config.get("sub_model",    config.get("main_model",    "gpt-4o-mini"))

    sub_system = load_prompts()["sub_system_prompt"]

    messages = [
        {"role": "system", "content": sub_system},
        {"role": "user",   "content": (
            f"The user asked: {question}\n\n"
            f"Files likely needed: {likely_files}\n\n"
            f"Read the necessary files using read_file."
        )}
    ]

    result = run_tool_loop(
        provider=sub_provider,
        model=sub_model,
        messages=messages,
        allow_tools=True,
        max_iterations=max_iterations
    )

    files_read = []
    tool_content_blocks = []

    for msg in messages:
        if msg.get("role") == "tool":
            try:
                tool_result = json.loads(msg["content"])
                if tool_result.get("exists") and tool_result.get("content"):
                    path = tool_result.get("path", "unknown")
                    content = tool_result["content"]
                    files_read.append(path)
                    tool_content_blocks.append(f"### {path} (full file)\n{content}")
            except Exception:
                pass

    enriched_context = base_context
    if tool_content_blocks:
        enriched_context += "\n\n---\n\n## Files Retrieved by Sub Agent\n\n"
        enriched_context += "\n\n---\n\n".join(tool_content_blocks)

    return {
        "enriched_context": enriched_context,
        "files_read":       files_read,
        "tool_calls":       len(files_read),
        "input_tokens":     result.get("input_tokens", 0),
        "output_tokens":    result.get("output_tokens", 0),
    }


# ---------------------------------------------------------------------------
# Edit mode orchestration
# ---------------------------------------------------------------------------

def run_edit_mode(
    question: str,
    config: dict,
    base_context: str,
    classification: dict,
) -> dict:
    """
    Edit mode orchestration.
    Sub reads files → main plans exact diffs → sub executes → main summarizes.
    Always exactly 2 main calls.
    """
    provider     = config.get("main_provider", "openai")
    model        = config.get("main_model",    "gpt-4o-mini")
    sub_provider = config.get("sub_provider",  provider)
    sub_model    = config.get("sub_model",     model)
    prompts      = load_prompts()

    total_input  = 0
    total_output = 0

    # -------------------------------------------------------------------------
    # Phase 1: Sub reads identified files into messages thread
    # -------------------------------------------------------------------------
    console.print(f"  [#5c6370]sub[/#5c6370] [#c678dd]{sub_provider}/{sub_model}[/#c678dd] [#5c6370]→ reading files for edit context[/#5c6370]")

    sub_messages = [
        {"role": "system", "content": prompts["sub_system_prompt"]},
        {"role": "user",   "content": (
            f"The user wants to make changes to the project.\n\n"
            f"Files to read: {classification['files']}\n\n"
            f"Read all listed files using read_file."
        )}
    ]

    # Cap iterations to number of files — one read per file maximum
    max_iter = max(len(classification["files"]), 1)
    run_tool_loop(
        provider=sub_provider,
        model=sub_model,
        messages=sub_messages,
        allow_tools=True,
        max_iterations=max_iter
    )

    # Normalize absolute paths in tool results to relative paths
    project_root = str(Path(".").resolve())
    for msg in sub_messages:
        if msg.get("role") == "tool":
            try:
                tool_result = json.loads(msg["content"])
                if tool_result.get("path"):
                    abs_path = tool_result["path"]
                    if abs_path.startswith(project_root):
                        rel = abs_path[len(project_root):].lstrip("\\/")
                        tool_result["path"] = rel
                        msg["content"] = json.dumps(tool_result)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Phase 2: Main generates write plan (with file request loop, cap 3 rounds)
    # -------------------------------------------------------------------------
    plan_messages = [
        {"role": "system", "content": prompts["write_plan_prompt"]},
        {"role": "user",   "content": (
            f"## Retrieved Context\n\n{base_context}\n\n"
            f"## User Request\n\n{question}"
        )}
    ]

    # Append sub's tool results (file contents) directly to plan messages
    for msg in sub_messages:
        if msg["role"] in ("assistant", "tool") or (
            msg["role"] == "user" and msg not in (sub_messages[0], sub_messages[1])
        ):
            plan_messages.append(msg)

    file_request_rounds = 0
    MAX_FILE_REQUEST_ROUNDS = 3
    write_plan = None

    while file_request_rounds <= MAX_FILE_REQUEST_ROUNDS:
        console.print(f"  [#5c6370]main[/#5c6370] [#c678dd]{provider}/{model}[/#c678dd] [#5c6370]→ generating edit plan (round {file_request_rounds + 1})[/#5c6370]")

        from votor.providers import stream_llm
        plan_result = _stream_to_console(
            stream_llm(provider, model, plan_messages, max_tokens=4096),
            show_thinking=False
        )
        total_input  += plan_result.get("input_tokens", 0)
        total_output += plan_result.get("output_tokens", 0)

        content = plan_result["content"].strip()

        # Strip markdown fences
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        # Strip <tool_call> tags (some local models wrap output this way)
        if "<tool_call>" in content:
            content = content.replace("<tool_call>", "").replace("</tool_call>", "").strip()

        try:
            parsed = json.loads(content)
        except Exception:
            console.print(f"  [#e06c75]✗ main returned invalid plan JSON — aborting[/#e06c75]")
            console.print(f"  [#5c6370]debug raw plan: {repr(content[:500])}[/#5c6370]")
            return {
                "answer":         "Edit mode failed — main did not return a valid plan.",
                "steps_executed": [],
                "input_tokens":   total_input,
                "output_tokens":  total_output,
                "model":          model,
                "provider":       provider,
            }

        if "need_files" in parsed and file_request_rounds < MAX_FILE_REQUEST_ROUNDS:
            needed = parsed["need_files"]
            console.print(f"  [#5c6370]main requested files:[/#5c6370] [#61afef]{needed}[/#61afef]")

            extra_messages = [
                {"role": "system", "content": prompts["sub_system_prompt"]},
                {"role": "user",   "content": f"Read these files: {needed}"}
            ]
            run_tool_loop(
                provider=sub_provider,
                model=sub_model,
                messages=extra_messages,
                allow_tools=True,
                max_iterations=len(needed) + 2
            )
            for msg in extra_messages:
                if msg["role"] in ("assistant", "tool"):
                    plan_messages.append(msg)

            plan_messages.append({
                "role": "user",
                "content": "Here are the additional files you requested. Now output the write plan."
            })
            file_request_rounds += 1
            continue

        if "write_plan" in parsed:
            write_plan = parsed["write_plan"]
            break

        console.print(f"  [#e06c75]✗ main returned unexpected response — aborting[/#e06c75]")
        return {
            "answer":         "Edit mode failed — unexpected response from main.",
            "steps_executed": [],
            "input_tokens":   total_input,
            "output_tokens":  total_output,
            "model":          model,
            "provider":       provider,
        }

    if not write_plan:
        return {
            "answer":         "Edit mode failed — could not generate plan after maximum file request rounds.",
            "steps_executed": [],
            "input_tokens":   total_input,
            "output_tokens":  total_output,
            "model":          model,
            "provider":       provider,
        }

    # -------------------------------------------------------------------------
    # Phase 3: Sub executes plan steps mechanically — with progress bar
    # -------------------------------------------------------------------------
    from rich.progress import Progress, BarColumn, TextColumn, MofNCompleteColumn

    steps_executed = []
    total_steps    = len(write_plan)

    # Build protected files set upfront — delete refused if file also edited/created
    protected_files = {
        s.get("file", "")
        for s in write_plan
        if s.get("action") in ("edit", "create")
    }

    console.print(
        f"\n  [#5c6370]planning complete —[/#5c6370] "
        f"[#abb2bf]{total_steps}[/#abb2bf] "
        f"[#5c6370]step{'s' if total_steps != 1 else ''}[/#5c6370]\n"
    )

    with Progress(
        TextColumn("  "),
        BarColumn(
            bar_width=20,
            complete_style="#00ffaa",
            finished_style="#00ffaa",
            pulse_style="#1e1e2e",
        ),
        MofNCompleteColumn(),
        TextColumn("  [#5c6370]{task.description}[/#5c6370]"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("starting...", total=total_steps)

        for i, step in enumerate(write_plan):
            action = step.get("action")
            file   = step.get("file", "")

            # Normalize absolute path to relative
            project_root = str(Path(".").resolve())
            if file.startswith(project_root):
                file = file[len(project_root):].lstrip("\\/")
                step["file"] = file

            # Update bar description
            progress.update(task, description=f"[#e5c07b]{action}[/#e5c07b] [#61afef]{file}[/#61afef]")

            # Guard — never allow modifications to .vectormind
            if ".vectormind" in file:
                progress.stop()
                console.print(f"  [#e06c75]✗ refused — .vectormind is protected[/#e06c75]")
                progress.start()
                steps_executed.append({
                    "action": action, "file": file,
                    "success": False,
                    "error": "refused — .vectormind is protected",
                    "diff_preview": None,
                })
                progress.advance(task)
                continue

            if action == "edit":
                result = dispatch_tool("edit_file_lines", {
                    "path":        file,
                    "start_line":  str(step.get("start_line", 1)),
                    "end_line":    str(step.get("end_line", 1)),
                    "new_content": step.get("new_content", ""),
                    "stage_only":  "true",
                })
                # Show diff inline after step completes
                if result.get("success") and result.get("diff_preview"):
                    progress.stop()
                    show_diff(result["diff_preview"], title=f"edit — {file}")
                    progress.start()

            elif action == "create":
                result = dispatch_tool("create_file", {
                    "path":       file,
                    "content":    step.get("content", ""),
                    "stage_only": "true",
                })
                if not result.get("success") and "already exists" in str(result.get("error", "")):
                    progress.stop()
                    console.print(f"  [#5c6370]file exists — retrying as full replacement[/#5c6370]")
                    progress.start()
                    content_lines = step.get("content", "").splitlines()
                    result = dispatch_tool("edit_file_lines", {
                        "path":        file,
                        "start_line":  "1",
                        "end_line":    str(len(content_lines) + 100),
                        "new_content": step.get("content", ""),
                        "stage_only":  "true",
                    })
                    if result.get("success") and result.get("diff_preview"):
                        progress.stop()
                        show_diff(result["diff_preview"], title=f"create (replaced) — {file}")
                        progress.start()

            elif action == "delete":
                if file in protected_files:
                    result = {
                        "success": False,
                        "error": "refused — cannot delete a file that is also being edited",
                    }
                    progress.stop()
                    console.print(f"  [#e06c75]✗ refused — {file} is also being edited[/#e06c75]")
                    progress.start()
                else:
                    progress.stop()
                    console.print(f"  [#e06c75]⚠ delete requested:[/#e06c75] [#61afef]{file}[/#61afef]")
                    console.print(
                        f"  [#5c6370]Type [#e06c75]yes[/#e06c75] to confirm or anything else to skip:[/#5c6370] ",
                        end="",
                    )
                    try:
                        confirm = input().strip().lower()
                    except Exception:
                        confirm = ""
                    if confirm == "yes":
                        result = dispatch_tool("delete_file", {"path": file, "stage_only": "true"})
                    else:
                        result = {"success": False, "error": "skipped by user"}
                        console.print(f"  [#5c6370]skipped[/#5c6370]")
                    progress.start()

            else:
                result = {"success": False, "error": f"unknown action: {action}"}

            # Print error inline if failed
            if not result.get("success"):
                progress.stop()
                console.print(f"  [#e06c75]✗ {result.get('error', 'unknown error')}[/#e06c75]")
                progress.start()

            steps_executed.append({
                "action":       action,
                "file":         file,
                "success":      result.get("success", False),
                "error":        result.get("error"),
                "diff_preview": result.get("diff_preview"),
            })

            progress.advance(task)

    console.print()

    # Batch commit all staged changes after all steps complete
    from votor.tools import git_commit_staged
    successful_files = [s["file"] for s in steps_executed if s["success"]]
    if successful_files:
        files_summary = ", ".join(successful_files[:3])
        if len(successful_files) > 3:
            files_summary += f" +{len(successful_files) - 3} more"
        git_commit_staged(f"votor: edit session — {files_summary}")
        console.print(
            f"  [#00ffaa]✓[/#00ffaa] [#5c6370]committed {len(successful_files)} file(s)[/#5c6370]"
        )

    # -------------------------------------------------------------------------
    # Phase 4: Verification (if enabled)
    # -------------------------------------------------------------------------
    from votor.tools import git_log, read_file as read_file_tool
    recent_commits = git_log(limit=len(write_plan))
    verify_changes = config.get("verify_changes", False)

    if verify_changes:
        # Read full current contents of all edited/created files
        changed_files = list({
            step["file"] for step in steps_executed
            if step["success"] and step["action"] in ("edit", "create")
        })

        file_contents_after = {}
        for f in changed_files:
            result = read_file_tool(f)
            if result.get("exists"):
                file_contents_after[f] = result["content"]

        # Build diffs from steps_executed
        diffs = [
            s["diff_preview"] for s in steps_executed
            if s.get("diff_preview")
        ]

        verify_messages = [
            {"role": "system", "content": prompts["verify_changes_prompt"]},
            {"role": "user",   "content": (
                f"## User Request\n\n{question}\n\n"
                f"## Changes Made (diffs)\n\n" + "\n\n".join(diffs) + "\n\n"
                f"## Full File Contents After Edit\n\n" +
                "\n\n".join(f"### {f}\n```\n{c}\n```" for f, c in file_contents_after.items())
            )}
        ]

        console.print(f"\n  [#5c6370]main[/#5c6370] [#c678dd]{provider}/{model}[/#c678dd] [#5c6370]→ verifying changes[/#5c6370]")

        from votor.providers import stream_llm
        verify_result = _stream_to_console(
            stream_llm(provider, model, verify_messages, max_tokens=1024),
            show_thinking=False
        )
        total_input  += verify_result.get("input_tokens", 0)
        total_output += verify_result.get("output_tokens", 0)

        # Append verification result to summary context
        verification_note = f"## Verification Result\n\n{verify_result['content']}"
    else:
        verification_note = ""

    # -------------------------------------------------------------------------
    # Phase 5: Main summarizes results
    # -------------------------------------------------------------------------
    summary_messages = [
        {"role": "system", "content": prompts["write_summary_prompt"]},
        {"role": "user",   "content": (
            f"## User Request\n\n{question}\n\n"
            f"## Plan That Was Executed\n\n{json.dumps(write_plan, indent=2)}\n\n"
            f"## Execution Results\n\n{json.dumps(steps_executed, indent=2)}\n\n"
            f"## Git Commits Made\n\n{json.dumps(recent_commits, indent=2)}\n\n"
            + verification_note
        )}
    ]

    console.print(f"\n  [#5c6370]main[/#5c6370] [#c678dd]{provider}/{model}[/#c678dd] [#5c6370]→ summarizing results[/#5c6370]")

    from votor.providers import stream_llm
    summary_result = _stream_to_console(
        stream_llm(provider, model, summary_messages, max_tokens=1024),
        show_thinking=False
    )
    total_input  += summary_result.get("input_tokens", 0)
    total_output += summary_result.get("output_tokens", 0)

    return {
        "answer":         summary_result["content"],
        "steps_executed": steps_executed,
        "input_tokens":   total_input,
        "output_tokens":  total_output,
        "model":          model,
        "provider":       provider,
    }


# ---------------------------------------------------------------------------
# Sub agent — main failsafe signal detection
# ---------------------------------------------------------------------------

NEED_FILE_SIGNAL = '"need_file"'

def detect_file_request(content: str) -> str | None:
    """
    Detect if main is signalling it needs a file.
    Returns file path if signal detected, None otherwise.
    Main signals by outputting: {"need_file": "path/to/file.py"}
    """
    if NEED_FILE_SIGNAL not in content:
        return None
    try:
        start = content.index("{")
        end   = content.rindex("}") + 1
        block = json.loads(content[start:end])
        path  = block.get("need_file")
        return str(path) if path else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fallback logic
# ---------------------------------------------------------------------------

UNCERTAINTY_PHRASES = [
    "i don't have enough",
    "insufficient context",
    "not enough information",
    "cannot determine",
    "i cannot find",
]

def needs_fallback(answer: str, model: str, fallback: str) -> bool:
    if model == fallback:
        return False
    if len(answer.split()) < 30:
        if any(p in answer.lower() for p in UNCERTAINTY_PHRASES):
            return True
    return False


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------

def _stream_to_console(stream_gen, show_thinking: bool = False) -> dict:
    """
    Stream tokens to console. In headless mode (dashboard thread),
    consumes the stream silently without touching the console.
    """
    result       = {}
    full_content = ""

    if _is_headless():
        # Consume stream silently — no console interaction
        for chunk in stream_gen:
            if isinstance(chunk, str):
                full_content += chunk
            else:
                result = chunk
        if full_content and not result.get("content"):
            result["content"] = full_content
        return result

    from rich.control import Control

    style         = "#5c6370" if show_thinking else "#abb2bf"
    lines_printed = 0

    console.print()
    lines_printed += 1

    for chunk in stream_gen:
        if isinstance(chunk, str):
            full_content  += chunk
            lines_printed += chunk.count("\n")
            console.print(chunk, end="", style=style, highlight=False)
        else:
            result = chunk

    console.print()
    lines_printed += 1

    if lines_printed > 0:
        console.control(Control.move_to_column(0))
        for _ in range(lines_printed):
            console.control(Control.move(0, -1))
        console.file.write("\033[J")
        console.file.flush()

    return result


# ---------------------------------------------------------------------------
# Main query runner
# ---------------------------------------------------------------------------

def run_query(
    question: str,
    config: dict = None,
    show_sources: bool = False,
    show_thinking: bool = False,
    top_k: int = None,
) -> dict:
    if config is None:
        config = load_config()

    top_k         = top_k or config.get("top_k", 5)
    provider      = config.get("main_provider", "openai")
    model         = config.get("main_model", "gpt-4o-mini")
    fallback      = config.get("fallback_model", "gpt-4o")
    show_thinking = show_thinking or config.get("show_thinking", False)

    main_call_count  = 0
    total_sub_input  = 0
    total_sub_output = 0

    start_time = time.time()

    # Step 1: Embed
    t0 = time.time()
    with console.status("[#5c6370]embedding...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
        query_embedding = embed_query(question, config)
    t_embed = round(time.time() - t0, 2)

    # Step 2: Retrieve
    t0 = time.time()
    with console.status("[#5c6370]retrieving context...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
        client, _ = get_collection()
        results   = query_chunks(client, query_embedding, top_k=top_k)
    t_retrieve = round(time.time() - t0, 2)


    if not results["documents"]:
        return {
            "answer":          "No relevant context found. Try running `/index` first.",
            "model":           model,
            "provider":        provider,
            "input_tokens":    0,
            "output_tokens":   0,
            "total_tokens":    0,
            "cost":            0.0,
            "response_time":   0.0,
            "retrieval_score": 0.0,
            "t_embed":         t_embed,
            "t_retrieve":      t_retrieve,
            "t_llm":           0.0,
            "sources":         [],
            "error":           "no_context"
        }

    # Step 3: Assemble base context
    base_context = assemble_context(
        results["documents"],
        results["metadatas"],
        results["scores"]
    )

    avg_score = sum(results["scores"]) / len(results["scores"]) if results["scores"] else 0
    sources = [
        {"file": meta.get("file", "unknown"), "chunk": meta.get("chunk_index", 0), "score": score}
        for meta, score in zip(results["metadatas"], results["scores"])
    ]

    # Step 4: Sub classifies intent
    sub_provider = config.get("sub_provider", config.get("main_provider", "openai"))
    sub_model    = config.get("sub_model", config.get("main_model", "gpt-4o-mini"))
    console.print(f"  [#5c6370]sub[/#5c6370] [#c678dd]{sub_provider}/{sub_model}[/#c678dd] [#5c6370]→ classify[/#5c6370]")
    t0 = time.time()
    with console.status("[#5c6370]classifying intent...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
        classification = classify_intent(question, config)
    t_classify = round(time.time() - t0, 2)
    console.print(f"  [#5c6370]sub classified: intent=[/#5c6370][#e5c07b]{classification['intent']}[/#e5c07b] [#5c6370]reason=[/#5c6370][#abb2bf]{classification['reason']}[/#abb2bf]")
    total_sub_input  += classification["input_tokens"]
    total_sub_output += classification["output_tokens"]

    # Step 5: Sub runs tools if needed
    enriched_context = base_context
    files_read: list = []
    t_sub_tools = 0.0

    # Write intent — route to edit mode
    if classification["intent"] == "write":
        write_mode = config.get("write_mode", "edit")

        if write_mode == "edit":
            t0 = time.time()
            edit_result = run_edit_mode(
                question=question,
                config=config,
                base_context=base_context,
                classification=classification,
            )
            t_llm = round(time.time() - t0, 2)

            # Auto /update after edit to re-index changed files
            try:
                from votor.indexer import index_project
                from votor.db import close_client
                invalidate_full_context_cache()
                console.print(f"\n  [#5c6370]auto-updating index...[/#5c6370]")
                close_client()  # release Qdrant lock before re-opening in indexer
                index_project(incremental=True, force=False, config=config)
                invalidate_full_context_cache()
                console.print(f"  [#00ffaa]✓[/#00ffaa] [#5c6370]index updated[/#5c6370]\n")
            except Exception as e:
                console.print(f"  [#e06c75]index update failed: {e}[/#e06c75]\n")

            elapsed = round(time.time() - start_time, 2)
            cost    = calculate_cost(
                model=edit_result["model"],
                input_tokens=edit_result["input_tokens"],
                output_tokens=edit_result["output_tokens"]
            )

            return {
                "answer":          edit_result["answer"],
                "model":           edit_result["model"],
                "provider":        edit_result["provider"],
                "input_tokens":    edit_result["input_tokens"],
                "output_tokens":   edit_result["output_tokens"],
                "total_tokens":    edit_result["input_tokens"] + edit_result["output_tokens"],
                "cost":            cost,
                "response_time":   elapsed,
                "retrieval_score": avg_score,
                "savings_pct":     0,
                "tokens_saved":    0,
                "t_embed":         t_embed,
                "t_retrieve":      t_retrieve,
                "t_classify":      t_classify,
                "t_sub_tools":     0.0,
                "t_llm":           t_llm,
                "sources":         sources,
                "error":           None,
            }

    if classification["intent"] == "read":
        console.print(f"  [#5c6370]sub[/#5c6370] [#c678dd]{sub_provider}/{sub_model}[/#c678dd] [#5c6370]→ tool loop  files=[/#5c6370][#61afef]{classification['files']}[/#61afef]")
        t0 = time.time()
        with console.status("[#5c6370]sub: reading files...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
            sub_result = run_sub_tool_loop(
                config=config,
                base_context=base_context,
                question=question,
                likely_files=classification["files"]
            )
        t_sub_tools = round(time.time() - t0, 2)
        enriched_context  = sub_result["enriched_context"]
        files_read        = sub_result["files_read"]
        total_sub_input  += sub_result["input_tokens"]
        total_sub_output += sub_result["output_tokens"]

    # Step 6: Main call 1
    prompts = load_prompts()
    messages = [
        {"role": "system", "content": prompts["system_prompt"]},
        {"role": "user",   "content": f"## Context\n\n{enriched_context}\n\n## Question\n\n{question}"}
    ]

    console.print(f"  [#5c6370]main[/#5c6370] [#c678dd]{provider}/{model}[/#c678dd] [#5c6370]→ call 1/2[/#5c6370]")
    t0 = time.time()
    main_call_count += 1
    from votor.providers import stream_llm
    llm_result = _stream_to_console(
        stream_llm(provider, model, messages, max_tokens=2048),
        show_thinking=show_thinking,
    )
    t_llm = round(time.time() - t0, 2)

    # Step 7: Failsafe — did main signal it needs a file?
    if main_call_count < 2:
        requested_file = detect_file_request(llm_result["content"])
        if requested_file:
            # Strip the signal JSON from the content so it never reaches the user
            llm_result = dict(llm_result)
            llm_result["content"] = llm_result["content"].replace(
                f'{{"need_file": "{requested_file}"}}', ""
            ).strip()
            with console.status("[#5c6370]sub: failsafe read...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
                failsafe_result = run_sub_tool_loop(
                    config=config,
                    base_context=enriched_context,
                    question=question,
                    likely_files=[requested_file]
                )
            total_sub_input  += failsafe_result["input_tokens"]
            total_sub_output += failsafe_result["output_tokens"]
            files_read.extend(failsafe_result["files_read"])

            # Main call 2 — hard cap, always final
            messages = [
                {"role": "system", "content": prompts["system_prompt"]},
                {"role": "user",   "content": (
                    f"## Context\n\n{failsafe_result['enriched_context']}"
                    f"\n\n## Question\n\n{question}"
                )}
            ]
            console.print(f"  [#5c6370]main[/#5c6370] [#c678dd]{provider}/{model}[/#c678dd] [#5c6370]→ call 2/2 (failsafe)[/#5c6370]")
            t0 = time.time()
            main_call_count += 1  # now at 2 — hard cap reached
            llm_result = _stream_to_console(
                stream_llm(provider, model, messages, max_tokens=2048),
                show_thinking=show_thinking,
            )
            t_llm += round(time.time() - t0, 2)

    # Step 8: Fallback only after main call 1 — never after call 2
    if main_call_count == 1 and needs_fallback(llm_result["content"], model, fallback):
        console.print(f"  [#5c6370]main[/#5c6370] [#c678dd]{provider}/{fallback}[/#c678dd] [#5c6370]→ fallback call[/#5c6370]")
        t0 = time.time()
        llm_result = _stream_to_console(
            stream_llm(provider, fallback, messages, max_tokens=2048),
            show_thinking=show_thinking,
        )
        t_llm += round(time.time() - t0, 2)

    elapsed = round(time.time() - start_time, 2)

    cost         = calculate_cost(model=llm_result["model"], input_tokens=llm_result["input_tokens"], output_tokens=llm_result["output_tokens"])
    full_tokens  = get_full_context_tokens()
    tokens_used  = llm_result["input_tokens"]
    tokens_saved = max(0, full_tokens - tokens_used)
    savings_pct  = round((tokens_saved / full_tokens * 100), 1) if full_tokens > 0 else 0

    try:
        log_query(
            question=question,
            model=llm_result["model"],
            input_tokens=llm_result["input_tokens"],
            output_tokens=llm_result["output_tokens"],
            total_tokens=llm_result["input_tokens"] + llm_result["output_tokens"],
            cost=cost,
            response_time=elapsed,
            retrieval_score=avg_score,
            chunks_retrieved=len(results["documents"]),
            full_context_tokens=full_tokens,
            tokens_saved=tokens_saved,
            file_accesses=sources
        )
    except Exception:
        pass

    return {
        "answer":          llm_result["content"],
        "model":           llm_result["model"],
        "provider":        llm_result["provider"],
        "input_tokens":    llm_result["input_tokens"],
        "output_tokens":   llm_result["output_tokens"],
        "total_tokens":    llm_result["input_tokens"] + llm_result["output_tokens"],
        "cost":            cost,
        "response_time":   elapsed,
        "retrieval_score": avg_score,
        "savings_pct":     savings_pct,
        "tokens_saved":    tokens_saved,
        "t_embed":         t_embed,
        "t_retrieve":      t_retrieve,
        "t_classify":      t_classify,
        "t_sub_tools":     t_sub_tools,
        "t_llm":           t_llm,
        "sources":         sources,
        "error":           None,
    }