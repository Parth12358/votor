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

CONFIG_FILE = Path(".vectormind/config.json")

SYSTEM_PROMPT = """You are votor, a project-aware coding assistant.
You have been given relevant code chunks retrieved from the project's vector database.
Use these chunks as your primary context to answer the user's question accurately.

Rules:
- Answer questions using the retrieved context first — do not read files unless the chunks are clearly insufficient
- Only use read_file if the user explicitly asks to see a full file, or if a specific function/class is missing from the retrieved chunks
- Only use create_file or edit_file if the user explicitly asks you to create or modify something
- Never delete files
- Be concise but complete
- Do not hallucinate code that isn't in the context"""


# ---------------------------------------------------------------------------
# Action detection — only enable tools for explicit file actions
# ---------------------------------------------------------------------------

ACTION_KEYWORDS = [
    "create", "edit", "modify", "update", "add", "delete",
    "remove", "write", "fix", "change", "refactor", "rename",
    "make", "implement", "build", "generate"
]

def _is_action_request(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in ACTION_KEYWORDS)


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

            tool_result = dispatch_tool(tool_name, tool_params)

            if tool_name == "edit_file" and tool_result.get("success") and tool_result.get("diff_preview"):
                show_diff(tool_result["diff_preview"], title=f"edit — {tool_params.get('path', '')}")

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
# Main query runner
# ---------------------------------------------------------------------------

def run_query(
    question: str,
    config: dict = None,
    show_sources: bool = False,
    top_k: int = None,
) -> dict:
    if config is None:
        config = load_config()

    top_k        = top_k or config.get("top_k", 5)
    provider     = config.get("main_provider", "openai")
    model        = config.get("main_model", "gpt-4o-mini")
    fallback     = config.get("fallback_model", "gpt-4o")
    allow_tools  = _is_action_request(question)

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

    # Step 3: Assemble context
    context = assemble_context(
        results["documents"],
        results["metadatas"],
        results["scores"]
    )

    # Step 4: Build messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"## Retrieved Context\n\n{context}\n\n## Question\n\n{question}"}
    ]

    # Step 5: Call LLM
    t0 = time.time()
    with console.status(f"[#5c6370]calling {model}...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
        llm_result = run_tool_loop(provider, model, messages, allow_tools=allow_tools)
    t_llm = round(time.time() - t0, 2)

    # Fallback if needed
    if needs_fallback(llm_result["content"], model, fallback):
        console.print(f"[#5c6370]falling back to {fallback}...[/#5c6370]")
        messages_copy = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"## Retrieved Context\n\n{context}\n\n## Question\n\n{question}"}
        ]
        t0 = time.time()
        with console.status(f"[#5c6370]calling {fallback}...[/#5c6370]", spinner="dots", spinner_style="#00ffaa"):
            llm_result = run_tool_loop(provider, fallback, messages_copy, allow_tools=allow_tools)
        t_llm = round(time.time() - t0, 2)

    elapsed = round(time.time() - start_time, 2)

    cost         = calculate_cost(model=llm_result["model"], input_tokens=llm_result["input_tokens"], output_tokens=llm_result["output_tokens"])
    full_tokens  = estimate_full_context_tokens()
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
        "t_llm":           t_llm,
        "sources":         sources,
        "error":           None,
    }