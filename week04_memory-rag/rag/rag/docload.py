"""Load supported corpus files into LlamaIndex documents."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from llama_index.core import Document

from .errors import ConfigurationError

EXT_TO_TYPE = {
    ".md": "doc",
    ".markdown": "doc",
    ".py": "code",
    ".html": "web",
    ".htm": "web",
    ".csv": "table",
    ".pdf": "pdf",
    ".txt": "text",
}

DOC_TYPES = tuple(sorted(set(EXT_TO_TYPE.values())))


def clean_text(text: str) -> str:
    """Normalize parser artifacts while preserving meaningful line structure."""
    text = re.sub(r"<!--\s*page\s*\d+\s*-->", "", text, flags=re.IGNORECASE)
    text = text.replace("\u3000", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Parse simple scalar YAML front matter without adding a YAML dependency."""
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    end = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if end is None:
        return {}, text

    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()

    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return metadata, body


def load_html(path: Path) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")
    parts: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if tag.name and tag.name.startswith("h"):
            level = int(tag.name[1])
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def load_table(path: Path) -> str:
    content = path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    lines = []
    for row in rows:
        cells = "；".join(f"{key}是{value}" for key, value in row.items() if value)
        if cells:
            lines.append(f"{cells}。")
    return "\n".join(lines)


def load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for page_number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        pages.append(f"<!-- page {page_number} -->\n{text}")
    return "\n\n".join(pages)


def _relative_path(path: Path, corpus: Path) -> str:
    return path.relative_to(corpus).as_posix()


def load_all(corpus: Path) -> list[Document]:
    """Recursively load supported files from a corpus directory."""
    corpus = corpus.expanduser().resolve()
    if not corpus.is_dir():
        raise ConfigurationError(f"Corpus directory does not exist: {corpus}")

    documents: list[Document] = []
    for path in sorted(corpus.rglob("*")):
        relative_parts = path.relative_to(corpus).parts
        if (
            not path.is_file()
            or path.suffix.lower() not in EXT_TO_TYPE
            or any(part.startswith(".") for part in relative_parts)
        ):
            continue

        suffix = path.suffix.lower()
        base_metadata = {
            "file_path": _relative_path(path, corpus),
            "doc_type": EXT_TO_TYPE[suffix],
            "source": suffix.lstrip("."),
        }

        if suffix in {".md", ".markdown", ".py", ".txt"}:
            front_matter, body = parse_front_matter(
                path.read_text(encoding="utf-8", errors="replace")
            )
            metadata = {**base_metadata, **front_matter}
            text = body
        elif suffix in {".html", ".htm"}:
            metadata = base_metadata
            text = clean_text(load_html(path))
        elif suffix == ".csv":
            metadata = base_metadata
            text = clean_text(load_table(path))
        else:
            metadata = base_metadata
            text = clean_text(load_pdf(path))

        if text.strip():
            documents.append(
                Document(
                    text=text,
                    metadata=metadata,
                    id_=base_metadata["file_path"],
                )
            )

    if not documents:
        raise ConfigurationError(
            f"No supported, non-empty documents found under {corpus}. "
            f"Supported suffixes: {', '.join(sorted(EXT_TO_TYPE))}"
        )
    return documents
