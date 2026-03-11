"""
votor/chunker.py

Structure-aware chunking using pygments lexers.
Splits on function/class definition boundaries detected via token stream.
Falls back to word-count chunking for non-code files or lex failures.
Pure Python — no compiler required.
"""

from __future__ import annotations

from rich.console import Console

console = Console()


# ---------------------------------------------------------------------------
# Language map — extension -> pygments lexer alias
# ---------------------------------------------------------------------------

LANGUAGE_MAP: dict[str, str] = {
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".go":   "go",
    ".rs":   "rust",
    ".java": "java",
    ".cs":   "csharp",
    ".c":    "c",
    ".cpp":  "cpp",
    ".h":    "c",
    ".rb":   "ruby",
    ".php":  "php",
}

# File extensions that are not code — always use word-count
NON_CODE_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".env.example"}

# Token values that signal the start of a new top-level block
DEFINITION_KEYWORDS: dict[str, set[str]] = {
    "python":     {"def", "class", "async def"},
    "javascript": {"function", "class", "const", "let", "var", "export", "async"},
    "typescript": {"function", "class", "const", "let", "var", "export", "async", "interface", "type", "enum"},
    "go":         {"func", "type", "var", "const"},
    "rust":       {"fn", "impl", "struct", "enum", "trait", "pub"},
    "java":       {"class", "interface", "enum", "@interface"},
    "csharp":     {"class", "interface", "struct", "enum", "namespace", "void", "public", "private", "protected"},
    "c":          {"void", "int", "char", "float", "double", "struct", "enum", "typedef"},
    "cpp":        {"void", "int", "char", "float", "double", "struct", "enum", "class", "namespace", "template"},
    "ruby":       {"def", "class", "module"},
    "php":        {"function", "class", "interface", "trait"},
}


# ---------------------------------------------------------------------------
# Word-count fallback chunker (original logic preserved)
# ---------------------------------------------------------------------------

def _chunk_by_words(text: str, chunk_size: int, overlap: int) -> list[str]:
    lines = text.splitlines(keepends=True)
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        current.append(line)
        current_len += len(line.split())

        if current_len >= chunk_size:
            chunks.append("".join(current))
            overlap_lines = current[-overlap:] if overlap < len(current) else current
            current = list(overlap_lines)
            current_len = sum(len(l.split()) for l in current)

    if current:
        chunks.append("".join(current))

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# pygments structural chunker
# ---------------------------------------------------------------------------

def _get_definition_lines(text: str, lang_name: str) -> set[int]:
    """
    Tokenize the file with pygments and return line numbers
    where a top-level definition keyword is detected.
    """
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.token import Token

    try:
        lexer = get_lexer_by_name(lang_name)
    except Exception:
        return set()

    keywords = DEFINITION_KEYWORDS.get(lang_name, set())
    definition_lines = set()
    line_num = 1

    for ttype, value in lex(text, lexer):
        newlines = value.count("\n")

        if ttype in Token.Keyword or ttype in Token.Keyword.Declaration:
            if value.strip() in keywords:
                definition_lines.add(line_num)

        line_num += newlines

    return definition_lines


def _chunk_by_structure(
    text: str,
    lang_name: str,
    chunk_size: int,
    overlap: int,
    rel_path: str = ""
) -> list[str] | None:
    """
    Split file into chunks at pygments-detected definition boundaries.
    Returns None if lexing fails or produces no useful splits.
    """
    try:
        definition_lines = _get_definition_lines(text, lang_name)
    except Exception:
        return None

    lines = text.splitlines(keepends=True)

    if not definition_lines or len(definition_lines) < 2:
        # Not enough structure detected — fall back
        return None

    # Split lines into blocks at each definition boundary
    blocks: list[list[str]] = []
    current_block: list[str] = []

    for i, line in enumerate(lines, start=1):
        if i in definition_lines and current_block:
            blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)

    if current_block:
        blocks.append(current_block)

    if not blocks:
        return None

    # Merge small blocks and split oversized ones
    chunks: list[str] = []
    pending: list[str] = []
    pending_len = 0

    for block in blocks:
        block_text = "".join(block)
        block_len = len(block_text.split())

        if block_len > chunk_size * 2:
            # Oversized block — flush pending first, then word-count split
            if pending:
                chunks.append("".join(pending))
                pending = []
                pending_len = 0
            chunks.extend(_chunk_by_words(block_text, chunk_size, overlap))

        elif pending_len + block_len > chunk_size and pending:
            # Pending is full — flush and start new
            chunks.append("".join(pending))
            pending = block
            pending_len = block_len

        else:
            pending.extend(block)
            pending_len += block_len

    if pending:
        chunks.append("".join(pending))

    return [c for c in chunks if c.strip()] or None


# ---------------------------------------------------------------------------
# Public interface — called by indexer.py
# ---------------------------------------------------------------------------

def chunk_file(
    text: str,
    extension: str,
    chunk_size: int,
    chunk_overlap: int,
    rel_path: str = ""
) -> list[str]:
    """
    Chunk a file's text content.

    - Code files: pygments token-boundary chunking
    - Non-code files: word-count fallback
    - Lex failure or insufficient structure: word-count fallback + warning log

    Args:
        text:          Full file text
        extension:     File extension e.g. '.py'
        chunk_size:    Target words per chunk
        chunk_overlap: Overlap words for fallback chunker
        rel_path:      Relative file path (used for warning messages only)

    Returns:
        List of text chunks ready for embedding
    """
    # Non-code files — always use word-count
    if extension in NON_CODE_EXTENSIONS:
        return _chunk_by_words(text, chunk_size, chunk_overlap)

    lang_name = LANGUAGE_MAP.get(extension)

    # Unknown extension — use word-count silently
    if lang_name is None:
        return _chunk_by_words(text, chunk_size, chunk_overlap)

    # Attempt structure-aware chunking
    chunks = _chunk_by_structure(text, lang_name, chunk_size, chunk_overlap, rel_path)

    if chunks is not None:
        return chunks

    # Fallback — log warning
    console.print(
        f"  [yellow]⚠ structure detection failed for {rel_path or extension} "
        f"— falling back to word-count chunking[/yellow]"
    )
    return _chunk_by_words(text, chunk_size, chunk_overlap)


# ---------------------------------------------------------------------------
# Option B hook (not implemented)
# ---------------------------------------------------------------------------

# def summarize_chunks(chunks: list[str], config: dict) -> list[str]:
#     """
#     LLM-assisted summarization pass before embedding.
#     Each chunk gets a prepended natural language summary generated by sub agent.
#     Improves retrieval for plain-English queries against code.
#     Requires sub_provider and sub_model to be configured.
#     """
#     raise NotImplementedError
