import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "openai": {
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4-turbo",
        ],
        "embedding_models": [
            "text-embedding-3-small",
            "text-embedding-3-large",
            "text-embedding-ada-002",
        ],
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "models": [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
        ],
        "embedding_models": [],  # Anthropic uses Voyage for embeddings
        "env_key": "ANTHROPIC_API_KEY",
    },
    "groq": {
        "models": [
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "mixtral-8x7b-32768",
        ],
        "embedding_models": [],  # Groq uses OpenAI embeddings
        "env_key": "GROQ_API_KEY",
    },
    "ollama": {
        "models": [
            "llama3.2",
            "codellama",
            "deepseek-coder",
            "mistral",
        ],
        "embedding_models": [
            "nomic-embed-text",
            "mxbai-embed-large",
        ],
        "env_key": None,  # No key needed for local
    },
}

# Embedding provider fallback map
# If main provider has no embedding models, use this
EMBEDDING_FALLBACK = {
    "anthropic": "openai",
    "groq":      "openai",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def get_api_key(provider: str) -> str | None:
    """Get API key for provider from environment."""
    env_key = PROVIDERS[provider]["env_key"]
    if env_key is None:
        return None  # Ollama doesn't need one
    return os.getenv(env_key)


def validate_provider(provider: str) -> tuple[bool, str]:
    """Check provider is supported and has an API key."""
    if provider not in PROVIDERS:
        return False, f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDERS.keys())}"

    key = get_api_key(provider)
    if key is None and PROVIDERS[provider]["env_key"] is not None:
        env_var = PROVIDERS[provider]["env_key"]
        return False, f"Missing API key. Set {env_var} in your .env file."

    return True, ""


def validate_model(provider: str, model: str) -> tuple[bool, str]:
    """Check model is supported for provider."""
    if provider not in PROVIDERS:
        return False, f"Unknown provider '{provider}'"

    # Ollama serves user-installed models — any model string is valid
    # since we cannot know what the user has pulled locally
    if provider == "ollama":
        return True, ""

    available = PROVIDERS[provider]["models"]
    if model not in available:
        return False, f"Model '{model}' not available for {provider}. Choose from: {', '.join(available)}"

    return True, ""


# ---------------------------------------------------------------------------
# LLM client factory
# ---------------------------------------------------------------------------

# Module-level client cache — one client per provider, reused across all calls
_client_cache: dict[str, object] = {}

def get_llm_client(provider: str):
    """Return configured LLM client for provider. Cached per provider."""
    if provider in _client_cache:
        return _client_cache[provider]

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=get_api_key("openai"))

    elif provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=get_api_key("anthropic"))

    elif provider == "groq":
        from groq import Groq
        client = Groq(api_key=get_api_key("groq"))

    elif provider == "ollama":
        from openai import OpenAI
        client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama"
        )

    else:
        raise ValueError(f"Unknown provider: {provider}")

    _client_cache[provider] = client
    return client


def clear_client_cache():
    """
    Clear cached clients. Call this if provider config changes at runtime
    e.g. after /init --force or /provider command.
    """
    _client_cache.clear()


# ---------------------------------------------------------------------------
# LLM call — unified interface
# ---------------------------------------------------------------------------

def call_llm(
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> dict:
    """
    Unified LLM call across all providers.
    Returns dict with content, input_tokens, output_tokens.
    """
    client = get_llm_client(provider)

    if provider == "anthropic":
        # Anthropic has different message format — system prompt is separate
        system_msg = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                user_messages.append(m)

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_msg,
            messages=user_messages,
            temperature=temperature,
        )
        return {
            "content":       response.content[0].text,
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "model":         model,
            "provider":      provider,
        }

    else:
        # OpenAI-compatible: OpenAI, Groq, Ollama
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {
            "content":       response.choices[0].message.content,
            "input_tokens":  response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "model":         model,
            "provider":      provider,
        }


def stream_llm(
    provider: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int = 2048,
):
    """
    Streaming version of call_llm.
    Yields string tokens as they arrive.
    When stream is exhausted, yields a final dict with full content and token counts.

    Usage:
        for chunk in stream_llm(provider, model, messages):
            if isinstance(chunk, str):
                print(chunk, end="", flush=True)
            else:
                result = chunk  # final dict
    """
    client = get_llm_client(provider)

    if provider == "anthropic":
        system_msg    = ""
        user_messages = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                user_messages.append(m)

        full_content  = ""
        input_tokens  = 0
        output_tokens = 0

        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system_msg,
            messages=user_messages,
            temperature=temperature,
        ) as stream:
            for text in stream.text_stream:
                full_content += text
                yield text
            final         = stream.get_final_message()
            input_tokens  = final.usage.input_tokens
            output_tokens = final.usage.output_tokens

        yield {
            "content":       full_content,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "model":         model,
            "provider":      provider,
        }

    else:
        # OpenAI-compatible: OpenAI, Groq, Ollama
        full_content  = ""
        input_tokens  = 0
        output_tokens = 0

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                full_content += text
                yield text
            if chunk.usage:
                input_tokens  = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens

        yield {
            "content":       full_content,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "model":         model,
            "provider":      provider,
        }


# ---------------------------------------------------------------------------
# Embedding client factory
# ---------------------------------------------------------------------------

def get_embedding_provider(config: dict) -> str:
    """
    Determine which provider to use for embeddings.
    Reads embedding_provider from config first.
    Falls back to main_provider, then OpenAI if neither has embedding models.
    """
    # Explicit embedding provider takes priority
    explicit = config.get("embedding_provider")
    if explicit and explicit in PROVIDERS and PROVIDERS[explicit]["embedding_models"]:
        return explicit

    # Fall back to main provider if it supports embeddings
    main = config.get("main_provider", "openai")
    if main in PROVIDERS and PROVIDERS[main]["embedding_models"]:
        return main

    # Final fallback — OpenAI
    return EMBEDDING_FALLBACK.get(main, "openai")


def embed_texts(
    texts: list[str],
    config: dict,
) -> list[list[float]]:
    """
    Embed a list of texts using configured embedding provider.
    Returns list of embedding vectors.
    """
    provider = get_embedding_provider(config)
    model    = config.get("embedding_model", "text-embedding-3-small")

    if provider in ("openai", "groq"):
        client = get_llm_client("openai")

        # Batch in groups of 100
        all_embeddings = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            response = client.embeddings.create(input=batch, model=model)
            all_embeddings.extend([e.embedding for e in response.data])
        return all_embeddings

    elif provider == "ollama":
        client = get_llm_client("ollama")
        all_embeddings = []
        for i in range(0, len(texts), 10):
            batch = texts[i:i + 10]
            response = client.embeddings.create(input=batch, model=model)
            all_embeddings.extend([e.embedding for e in response.data])
        return all_embeddings

    else:
        raise ValueError(f"No embedding support for provider: {provider}")


def embed_query(text: str, config: dict) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text], config)[0]


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

# Pricing per 1K tokens in USD
PRICING = {
    "gpt-4o-mini":              {"input": 0.000150, "output": 0.000600},
    "gpt-4o":                   {"input": 0.002500, "output": 0.010000},
    "gpt-4-turbo":              {"input": 0.010000, "output": 0.030000},
    "claude-haiku-4-5-20251001":{"input": 0.000800, "output": 0.004000},
    "claude-sonnet-4-6":        {"input": 0.003000, "output": 0.015000},
    "claude-opus-4-6":          {"input": 0.015000, "output": 0.075000},
    "llama-3.1-8b-instant":     {"input": 0.000050, "output": 0.000080},
    "llama-3.3-70b-versatile":  {"input": 0.000590, "output": 0.000790},
    "mixtral-8x7b-32768":       {"input": 0.000270, "output": 0.000270},
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a call."""
    pricing = PRICING.get(model, {"input": 0.0, "output": 0.0})
    cost = (input_tokens / 1000) * pricing["input"]
    cost += (output_tokens / 1000) * pricing["output"]
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Provider info helpers (used by init_flow and /provider command)
# ---------------------------------------------------------------------------

def list_providers() -> list[str]:
    return list(PROVIDERS.keys())


def list_models(provider: str) -> list[str]:
    return PROVIDERS.get(provider, {}).get("models", [])


def list_embedding_models(provider: str) -> list[str]:
    return PROVIDERS.get(provider, {}).get("embedding_models", [])


def provider_needs_key(provider: str) -> bool:
    return PROVIDERS[provider]["env_key"] is not None