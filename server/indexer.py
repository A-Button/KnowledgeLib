"""Markdown chunking, Chinese-aware tokenisation and BM25 retrieval."""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from .library import PathRef, iter_paths, parse_index

CJK_RUN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE = re.compile(r"^\s*(" + chr(96) * 3 + "|~~~)")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in CJK_RUN.finditer(text or ""):
        run = match.group(0)
        if run[0].isascii():
            lowered = run.lower()
            if len(lowered) >= 2 or lowered.isdigit():
                tokens.append(lowered)
        else:
            if len(run) == 1:
                tokens.append(run)
            else:
                for i in range(len(run) - 1):
                    tokens.append(run[i : i + 2])
    return tokens


def _split_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    lines: list[str] = []
    start = 0
    offset = 0
    in_fence = False

    def flush(end: int) -> None:
        body = "".join(lines)
        if body.strip():
            breadcrumb = " / ".join(item[1] for item in stack)
            sections.append(
                {
                    "breadcrumb": breadcrumb,
                    "heading": stack[-1][1] if stack else "",
                    "text": body,
                    "start": start,
                    "end": end,
                }
            )

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if FENCE.match(stripped):
            in_fence = not in_fence
        if not in_fence:
            heading = HEADING.match(stripped)
            if heading:
                flush(offset)
                level = len(heading.group(1))
                title = heading.group(2).strip()
                stack = [item for item in stack if item[0] < level]
                stack.append((level, title))
                lines = []
                start = offset
            else:
                lines.append(line)
        else:
            lines.append(line)
        offset += len(line)
    flush(offset)
    return sections


def _split_long(section: dict[str, Any], target: int, hard: int, overlap: int) -> list[dict[str, Any]]:
    text = section["text"]
    if len(text) <= hard:
        return [section]

    paragraphs = re.split(r"(\n\s*\n)", text)
    pieces: list[str] = []
    buffer = ""
    in_fence = False
    for para in paragraphs:
        if not para:
            continue
        fences = len(FENCE.findall(para))
        if fences % 2 == 1:
            in_fence = not in_fence
        if len(buffer) + len(para) > hard and buffer.strip() and not in_fence:
            pieces.append(buffer)
            buffer = ""
        if len(para) > hard * 2 and not in_fence:
            if buffer.strip():
                pieces.append(buffer)
                buffer = ""
            for i in range(0, len(para), target):
                pieces.append(para[i : i + target])
            continue
        buffer += para
        if len(buffer) >= target and not in_fence:
            pieces.append(buffer)
            buffer = ""
    if buffer.strip():
        pieces.append(buffer)

    chunks: list[dict[str, Any]] = []
    cursor = 0
    for index, piece in enumerate(pieces):
        piece_start = text.find(piece, cursor)
        if piece_start < 0:
            piece_start = cursor
        cursor = piece_start + len(piece)
        body = piece
        if index > 0 and overlap > 0:
            tail = chunks[-1]["raw"][-overlap:]
            if tail.strip():
                body = tail + "\n\n" + piece
                piece_start = max(0, piece_start - len(tail))
        chunks.append(
            {
                "breadcrumb": section["breadcrumb"],
                "heading": section["heading"],
                "text": body,
                "raw": piece,
                "start": section["start"] + piece_start,
                "end": section["start"] + piece_start + len(piece),
            }
        )
    return chunks


def split_markdown(text: str, target: int = 800, hard: int = 1200, overlap: int = 80) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for section in _split_sections(text):
        for chunk in _split_long(section, target, hard, overlap):
            chunk.pop("raw", None)
            if chunk["text"].strip():
                chunks.append(chunk)
    if not chunks and text.strip():
        chunks.append({"breadcrumb": "", "heading": "", "text": text[:hard], "start": 0, "end": min(len(text), hard)})
    return chunks


class IndexCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunks: dict[tuple[str, int, int], list[dict[str, Any]]] = {}

    def chunks_for(self, path: Path) -> list[dict[str, Any]]:
        try:
            stat = path.stat()
        except OSError:
            return []
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._chunks.get(key)
            if cached is not None:
                return cached
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        chunks = split_markdown(text)
        with self._lock:
            self._chunks[key] = chunks
            for old in [k for k in self._chunks if k[0] == str(path) and k != key]:
                self._chunks.pop(old, None)
        return chunks

    def invalidate_dir(self, directory: Path) -> None:
        prefix = str(Path(directory).resolve())
        with self._lock:
            for key in [k for k in self._chunks if k[0].startswith(prefix)]:
                self._chunks.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()


DEFAULT_CACHE = IndexCache()


def _collect_chunks(kb_dir: Path, cache: IndexCache) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for ref in iter_paths(kb_dir):
        for file_info in ref.files:
            full = ref.directory / file_info["name"]
            for position, chunk in enumerate(cache.chunks_for(full)):
                docs.append(
                    {
                        "pathId": ref.id,
                        "pathName": ref.name,
                        "file": file_info["name"],
                        "fileIndex": parse_index(Path(file_info["name"]).name),
                        "heading": chunk["heading"],
                        "breadcrumb": chunk["breadcrumb"],
                        "text": chunk["text"],
                        "start": chunk["start"],
                        "position": position,
                    }
                )
    return docs


def search(
    kb_dir: Path,
    query: str,
    top_k: int = 6,
    cache: IndexCache | None = None,
) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        return []
    cache = cache or DEFAULT_CACHE
    docs = _collect_chunks(Path(kb_dir), cache)
    if not docs:
        return []

    query_terms = list(dict.fromkeys(tokenize(query)))
    if not query_terms:
        return []
    doc_tokens = [tokenize(doc["text"]) for doc in docs]
    lengths = [len(tokens) for tokens in doc_tokens]
    avg_len = sum(lengths) / max(len(lengths), 1)
    df: Counter[str] = Counter()
    for tokens in doc_tokens:
        for term in set(tokens):
            df[term] += 1
    total = len(docs)
    qset = set(query_terms)

    scored: list[tuple[float, dict[str, Any]]] = []
    for doc, tokens, length in zip(docs, doc_tokens, lengths):
        if not tokens:
            continue
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            freq = counts.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (total - df[term] + 0.5) / (df[term] + 0.5))
            denominator = freq + 1.5 * (1 - 0.75 + 0.75 * length / max(avg_len, 1))
            score += idf * (freq * 2.5) / denominator
        if score <= 0:
            continue
        title_tokens = set(
            tokenize(f"{doc['pathName']} {doc['file']} {doc['breadcrumb']} {doc['heading']}")
        )
        boost = len(qset & title_tokens) * 0.55
        score += boost
        scored.append((score, doc))

    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[dict[str, Any]] = []
    per_file: Counter[str] = Counter()
    for score, doc in scored:
        if per_file[doc["file"]] >= 3:
            continue
        per_file[doc["file"]] += 1
        results.append(
            {
                "ref": f"K{len(results) + 1}",
                "kind": "kb",
                "pathId": doc["pathId"],
                "pathName": doc["pathName"],
                "file": doc["file"],
                "fileIndex": doc["fileIndex"],
                "heading": doc["heading"],
                "breadcrumb": doc["breadcrumb"],
                "text": doc["text"][:1200],
                "score": round(score, 4),
                "charStart": doc["start"],
            }
        )
        if len(results) >= max(1, min(top_k, 20)):
            break
    return results
