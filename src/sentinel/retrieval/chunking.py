"""Paragraph-aware chunking with frontmatter parsing for runbook markdown."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from sentinel.retrieval.models import RunbookChunk

logger = logging.getLogger(__name__)

# Default chunk parameters (tokens approximated as words * 1.3)
DEFAULT_CHUNK_SIZE_TOKENS = 512
DEFAULT_CHUNK_OVERLAP_TOKENS = 64
# Maximum indivisible block size before forced split
MAX_INDIVISIBLE_TOKENS = 768

# Approximate tokens per character (English prose)
CHARS_PER_TOKEN = 4.0


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length (rough heuristic)."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def _sha256(text: str) -> str:
    """Compute SHA-256 hex digest of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_frontmatter(content: str) -> tuple[dict[str, str | list[str]], str]:
    """Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (frontmatter_dict, body_without_frontmatter).
        If no frontmatter found, returns empty dict and full content.
    """
    try:
        import frontmatter

        post = frontmatter.loads(content)
        metadata = dict(post.metadata) if post.metadata else {}
        return metadata, post.content
    except Exception:
        # If python-frontmatter isn't available or parsing fails,
        # try manual parsing
        pass

    # Manual fallback: check for --- delimited YAML
    if not content.startswith("---"):
        return {}, content

    lines = content.split("\n")
    end_idx = -1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return {}, content

    # Simple YAML-like parsing for frontmatter
    metadata: dict[str, str | list[str]] = {}
    for line in lines[1:end_idx]:
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            # Handle list values like [checkout, payments]
            if value.startswith("[") and value.endswith("]"):
                items = [item.strip().strip("'\"") for item in value[1:-1].split(",")]
                metadata[key] = [item for item in items if item]
            else:
                metadata[key] = value.strip("'\"")

    body = "\n".join(lines[end_idx + 1 :]).strip()
    return metadata, body


def _extract_service_tags(metadata: dict[str, str | list[str]], body: str) -> list[str]:
    """Extract service tags from frontmatter and body content.

    Checks frontmatter 'service_tags' or 'services' field first,
    then falls back to regex matching against KNOWN_SERVICES in body.
    """
    from sentinel.models.incident import KNOWN_SERVICES

    tags: set[str] = set()

    # From frontmatter
    for key in ("service_tags", "services", "service"):
        if key in metadata:
            val = metadata[key]
            if isinstance(val, list):
                tags.update(val)
            elif isinstance(val, str):
                tags.update(t.strip() for t in val.split(","))

    # From body: match known services
    body_lower = body.lower()
    for service in KNOWN_SERVICES:
        if service in body_lower:
            tags.add(service)

    return sorted(tags)


def _is_code_block(text: str) -> bool:
    """Check if text starts with a fenced code block."""
    return text.strip().startswith("```")


def _is_table(text: str) -> bool:
    """Check if text contains a markdown table (pipe-delimited lines)."""
    lines = text.strip().split("\n")
    pipe_lines = sum(1 for line in lines if "|" in line and line.strip().startswith("|"))
    return pipe_lines >= 2


def _split_into_paragraphs(body: str) -> list[str]:
    """Split markdown body into paragraphs, preserving code blocks and tables.

    Code blocks (``` delimited) and tables are kept as single units.
    """
    paragraphs: list[str] = []
    current_block: list[str] = []
    in_code_block = False
    lines = body.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track code block boundaries
        if line.strip().startswith("```"):
            if not in_code_block:
                # Start of code block - flush any accumulated paragraph
                if current_block:
                    text = "\n".join(current_block).strip()
                    if text:
                        paragraphs.append(text)
                    current_block = []
                in_code_block = True
                current_block.append(line)
            else:
                # End of code block
                current_block.append(line)
                in_code_block = False
                text = "\n".join(current_block).strip()
                if text:
                    paragraphs.append(text)
                current_block = []
            i += 1
            continue

        if in_code_block:
            current_block.append(line)
            i += 1
            continue

        # Check for table start (line with pipes)
        if "|" in line and line.strip().startswith("|"):
            # Flush current paragraph
            if current_block:
                text = "\n".join(current_block).strip()
                if text:
                    paragraphs.append(text)
                current_block = []
            # Collect full table
            table_lines: list[str] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            paragraphs.append("\n".join(table_lines))
            continue

        # Empty line marks paragraph boundary
        if not line.strip():
            if current_block:
                text = "\n".join(current_block).strip()
                if text:
                    paragraphs.append(text)
                current_block = []
        else:
            current_block.append(line)
        i += 1

    # Flush remaining
    if current_block:
        text = "\n".join(current_block).strip()
        if text:
            paragraphs.append(text)

    return paragraphs


def _split_large_block(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split a large block (code or table) into token-bounded chunks.

    Adds [truncated] marker when splitting indivisible blocks.
    """
    chars_per_chunk = int(max_tokens * CHARS_PER_TOKEN)
    overlap_chars = int(overlap_tokens * CHARS_PER_TOKEN)
    chunks: list[str] = []

    start = 0
    while start < len(text):
        end = start + chars_per_chunk
        if end < len(text):
            # Add truncation marker
            chunk = text[start:end] + "\n[truncated]"
        else:
            chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap_chars

    return chunks


def split_runbook(
    content: str,
    runbook_id: str,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
) -> list[RunbookChunk]:
    """Split a markdown runbook into retrieval-ready chunks.

    Strategy:
    1. Parse YAML frontmatter to extract environment and service_tags.
    2. Split body into paragraphs (preserving code blocks and tables as units).
    3. Merge paragraphs into chunks of ~chunk_size_tokens with overlap.
    4. Large indivisible blocks (code/tables) up to 768 tokens are kept whole.
    5. Blocks exceeding 768 tokens are split with [truncated] marker and logged.

    Args:
        content: Full markdown content including frontmatter.
        runbook_id: Unique identifier for this runbook.
        chunk_size_tokens: Target chunk size in tokens (default 512).
        chunk_overlap_tokens: Overlap between consecutive chunks (default 64).

    Returns:
        List of RunbookChunk objects ready for embedding and indexing.
    """
    # Parse frontmatter
    metadata, body = _parse_frontmatter(content)

    # Extract metadata fields
    title = str(metadata.get("title", runbook_id))
    environment = str(metadata.get("environment", "any")).lower()
    if environment not in ("prod", "staging", "dev", "any"):
        environment = "any"

    service_tags = _extract_service_tags(metadata, body)

    # Split into paragraphs
    paragraphs = _split_into_paragraphs(body)

    if not paragraphs:
        return []

    # Merge paragraphs into chunks respecting token limits
    chunks_text: list[str] = []
    current_chunk: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        # Handle large indivisible blocks (code blocks, tables)
        is_indivisible = _is_code_block(para) or _is_table(para)

        if is_indivisible and para_tokens > MAX_INDIVISIBLE_TOKENS:
            # Flush current chunk
            if current_chunk:
                chunks_text.append("\n\n".join(current_chunk))
                current_chunk = []
                current_tokens = 0

            # Split the large block
            logger.warning(
                "Runbook '%s': indivisible block exceeds %d tokens (%d tokens). "
                "Splitting with [truncated] marker.",
                runbook_id,
                MAX_INDIVISIBLE_TOKENS,
                para_tokens,
            )
            sub_chunks = _split_large_block(para, MAX_INDIVISIBLE_TOKENS, chunk_overlap_tokens)
            chunks_text.extend(sub_chunks)
            continue

        if is_indivisible and para_tokens > chunk_size_tokens:
            # Keep as single chunk (up to MAX_INDIVISIBLE_TOKENS)
            if current_chunk:
                chunks_text.append("\n\n".join(current_chunk))
                current_chunk = []
                current_tokens = 0
            chunks_text.append(para)
            continue

        # Normal paragraph merging
        if current_tokens + para_tokens > chunk_size_tokens and current_chunk:
            # Current chunk is full, start a new one
            chunks_text.append("\n\n".join(current_chunk))

            # Overlap: keep last portion of current chunk
            overlap_text = ""
            overlap_budget = chunk_overlap_tokens
            for prev_para in reversed(current_chunk):
                prev_tokens = _estimate_tokens(prev_para)
                if prev_tokens <= overlap_budget:
                    overlap_text = prev_para
                    overlap_budget -= prev_tokens
                    break

            current_chunk = [overlap_text] if overlap_text else []
            current_tokens = _estimate_tokens(overlap_text) if overlap_text else 0

        current_chunk.append(para)
        current_tokens += para_tokens

    # Flush remaining
    if current_chunk:
        chunks_text.append("\n\n".join(current_chunk))

    # Build RunbookChunk objects
    chunk_total = len(chunks_text)
    result: list[RunbookChunk] = []

    for idx, text in enumerate(chunks_text):
        result.append(
            RunbookChunk(
                runbook_id=runbook_id,
                runbook_title=title,
                chunk_index=idx,
                chunk_total=chunk_total,
                text=text,
                service_tags=service_tags,
                content_sha256=_sha256(text),
                environment=environment,
            )
        )

    return result


def split_runbook_file(
    file_path: Path,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
) -> list[RunbookChunk]:
    """Split a runbook markdown file into chunks.

    Derives runbook_id from the file stem (e.g., 'checkout_api_503' from
    'checkout_api_503.md').

    Args:
        file_path: Path to the markdown file.
        chunk_size_tokens: Target chunk size in tokens.
        chunk_overlap_tokens: Overlap between chunks.

    Returns:
        List of RunbookChunk objects.
    """
    content = file_path.read_text(encoding="utf-8")
    runbook_id = file_path.stem
    return split_runbook(content, runbook_id, chunk_size_tokens, chunk_overlap_tokens)
