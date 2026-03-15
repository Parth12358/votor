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
            "needs_tools":   bool(parsed.get("needs_tools", False)),
            "likely_files":  parsed.get("likely_files", []),
            "reason":        parsed.get("reason", ""),
            "input_tokens":  result["input_tokens"],
            "output_tokens": result["output_tokens"],
        }
    except Exception:
        return {"needs_tools": False, "likely_files": [], "reason": "classification_failed",
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
    Consume a stream_llm() generator, printing tokens in real time.
    Clears the streamed output after completion so print_response()
    can re-render a clean markdown panel.
    Returns the final result dict (last item yielded by stream_gen).
    """
    import sys
    result       = {}
    full_content = ""
    style        = "#5c6370" if show_thinking else "#abb2bf"

    console.print()

    for chunk in stream_gen:
        if isinstance(chunk, str):
            full_content += chunk
            console.print(chunk, end="", style=style, highlight=False)
        else:
            result = chunk

    console.print()

    line_count = full_content.count("\n") + 2
    sys.stdout.write(f"\033[{line_count}A\033[J")
    sys.stdout.flush()

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

    # Step 4: Sub classifies intent
    sub_provider = config.get("sub_provider", config.get("main_provider", "openai"))
    sub_model    = config.get("sub_model", config.get("main_model", "gpt-4o-mini"))
    console.print(f"  [#5c6370]sub[/#5c6370] [#c678dd]{sub_provider}/{sub_model}[/#c678dd] [#5c6370]→ classify[/#5c6370]")
    t0 = time.time()
    with console.status("[#5c6370]classifying intent...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
        classification = classify_intent(question, config)
    t_classify = round(time.time() - t0, 2)
    console.print(f"  [#5c6370]sub classified: needs_tools=[/#5c6370][#e5c07b]{classification['needs_tools']}[/#e5c07b] [#5c6370]reason=[/#5c6370][#abb2bf]{classification['reason']}[/#abb2bf]")
    total_sub_input  += classification["input_tokens"]
    total_sub_output += classification["output_tokens"]

    # Step 5: Sub runs tools if needed
    enriched_context = base_context
    files_read: list = []
    t_sub_tools = 0.0

    if classification["needs_tools"]:
        console.print(f"  [#5c6370]sub[/#5c6370] [#c678dd]{sub_provider}/{sub_model}[/#c678dd] [#5c6370]→ tool loop  files=[/#5c6370][#61afef]{classification['likely_files']}[/#61afef]")
        t0 = time.time()
        with console.status("[#5c6370]sub: reading files...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
            sub_result = run_sub_tool_loop(
                config=config,
                base_context=base_context,
                question=question,
                likely_files=classification["likely_files"]
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
    avg_score    = sum(results["scores"]) / len(results["scores"]) if results["scores"] else 0

    sources = [
        {"file": meta.get("file", "unknown"), "chunk": meta.get("chunk_index", 0), "score": score}
        for meta, score in zip(results["metadatas"], results["scores"])
    ]

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