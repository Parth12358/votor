# votor

> A project-aware coding assistant powered by a local vector database that travels with your code.

---

## What is votor?

Most AI coding tools solve the context problem by dumping your entire codebase into the context window  expensive, hits limits fast, and gets worse as your project grows.

Votor indexes your project into a local [Qdrant](https://qdrant.tech/) vector database stored at `.vectormind/` alongside your code. On every query, only semantically relevant chunks are retrieved and sent to the LLM. Token usage stays constant regardless of project size or session length.

A sub agent (local, free) handles intent classification and file operations. A main agent (API or local) focuses purely on answering questions and planning changes.

---

## Features

**Query**
- Ask questions about your codebase in plain English
- Relevant code chunks retrieved automatically via vector search
- `/sources`  toggle showing retrieved source chunks per query

**Edit**
- Ask votor to create, edit, or refactor files
- Main agent plans exact changes, sub agent executes them
- Every AI change committed to git with `votor:` prefix
- `/history`  show all AI changes
- `/undo`  revert last AI change
- `/revert <n>`  revert to before change #n
- `/diff <n>`  show diff of change #n

**Index**
- `/init`  initialize votor for this project
- `/index`  full re-index of entire project
- `/update`  incremental re-index of changed files only
- `/status`  index health, query stats, cost summary

**Other**
- `/config`  show current configuration
- `/provider`  instructions for switching provider
- `/dashboard`  launch analytics dashboard at localhost:8000
- `/thinking`  toggle raw model token stream
- `/clear`  clear the screen
- `/help`  show help
- `/exit`  exit votor

---

## Installation
```bash
pip install votor
```

Or from source:
```bash
git clone https://github.com/Parth12358/votor
cd votor
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e .
```

---

## Setup

Run votor from inside any project directory:
```bash
cd your-project
votor
```

On first run type `/init` to launch the setup wizard. It will ask for:

- **Main agent**  provider and model (answers questions, plans edits)
- **Sub agent**  provider and model (classifies intent, executes file operations)
- **Embedding**  provider and model
- **Git** remote setup (optional)
- **Index settings**  chunk size, overlap, file extensions

API keys are saved to `.env` in your project root and never committed.

---

## Recommended Configuration

**Hybrid (recommended)**  API for main, local for everything else:
```json
{
  "main_provider":      "anthropic",
  "main_model":         "claude-haiku-4-5-20251001",
  "sub_provider":       "ollama",
  "sub_model":          "qwen2.5:7b",
  "embedding_provider": "ollama",
  "embedding_model":    "nomic-embed-text"
}
```

**Full local**  no API costs:
```json
{
  "main_provider":      "ollama",
  "main_model":         "qwen2.5:14b",
  "sub_provider":       "ollama",
  "sub_model":          "qwen2.5:7b",
  "embedding_provider": "ollama",
  "embedding_model":    "nomic-embed-text"
}
```

For local models, install [Ollama](https://ollama.com) and pull:
```bash
ollama pull qwen2.5:7b
ollama pull qwen2.5:14b
ollama pull nomic-embed-text
```

---

## Providers

| Provider | LLM | Embeddings |
|---|---|---|
| OpenAI | gpt-4o-mini, gpt-4o | text-embedding-3-small, text-embedding-3-large |
| Anthropic | claude-haiku, claude-sonnet, claude-opus | falls back to OpenAI |
| Groq | llama-3.1-8b, llama-3.3-70b, mixtral | falls back to OpenAI |
| Ollama | any locally installed model | nomic-embed-text, mxbai-embed-large |

---

## Requirements

- Python 3.10+
- Git
- Ollama (optional, for local models) or at least one API key

---

## Roadmap

| Item | Effort | Priority | Status |
|---|---|---|---|
| 0 UI/UX redesign | Large | High | ✓ Done |
| 0b Dashboard | Large | High | ✓ Done |
| 1a egg-info exclude | Trivial | — | ✓ Done |
| 1b pyproject keywords | Trivial | — | ✓ Done |
| 1c Qdrant concurrent access | Small | — | ✓ Done |
| 1d debug print cleanup | Trivial | — | ✓ Done |
| 5 Multi-file edit support | Medium | High | ✓ Done |
| 2 Reason mode | Medium | Medium | Open |
| 4 Conversation memory | Medium | Medium | Open |
| 6 Step mode | Medium | High | Open |
| 7 Watch mode | Small | Low | Open |
| 8 Parallel client support | Medium | Low | Open |
| 9 File tree | Medium | Low | Open |
| 3 Chunk rewrite (Option B) | Large | Low | Shelved |
