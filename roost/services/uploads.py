"""User file uploads — the web counterpart to the Telegram file handlers.

Files land in ``UPLOADS_DIR`` (same directory the Telegram bot and
``gemini_agent`` already read from), so anything uploaded here is
immediately usable by the agent surfaces. This module is pure Python and
holds the only sanitisation/containment logic; the web layer
(``roost/web/api_uploads.py``) is a thin adapter over it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from roost.config import UPLOADS_DIR

# Web uploads are capped well under the Telegram 50MB document ceiling —
# these sit on the data volume and are meant for reference files
# (CSVs, PDFs, images, spreadsheets), not bulk storage.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Conservative filename allowlist. Anything outside it is replaced with "_".
_NAME_SUB = re.compile(r"[^A-Za-z0-9._ \-()]+")


class UploadError(ValueError):
    """Raised for invalid filenames or oversized uploads."""


@dataclass
class StoredFile:
    name: str
    size: int
    size_human: str
    modified: float

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "size_human": self.size_human,
            "modified": self.modified,
        }


def _root() -> Path:
    root = Path(UPLOADS_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n}B"


def safe_name(name: str) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Strips any directory components, leading dots (no hidden/dotfiles, and
    kills ``..``/``.``), and replaces disallowed characters. Raises
    ``UploadError`` if nothing usable remains.
    """
    base = os.path.basename((name or "").strip()).lstrip(".")
    base = _NAME_SUB.sub("_", base).strip()
    if not base or base in (".", ".."):
        raise UploadError("invalid filename")
    return base[:200]


def resolve_path(name: str) -> Path:
    """Resolve a stored file by name, guaranteeing it stays inside the root.

    Returns the absolute path. Raises ``UploadError`` if the resolved path
    escapes ``UPLOADS_DIR`` or the file does not exist.
    """
    root = _root().resolve()
    target = (root / safe_name(name)).resolve()
    if target != root and root not in target.parents:
        raise UploadError("path escapes uploads directory")
    if not target.is_file():
        raise UploadError("file not found")
    return target


def save_upload(filename: str, data: bytes) -> StoredFile:
    """Persist uploaded bytes under a sanitised name. Last write wins."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"file too large ({_human_size(len(data))}); "
            f"limit is {_human_size(MAX_UPLOAD_BYTES)}"
        )
    name = safe_name(filename)
    root = _root().resolve()
    dest = (root / name).resolve()
    # Belt-and-suspenders: safe_name already strips traversal, but verify
    # the resolved destination is still contained before writing.
    if dest != root and root not in dest.parents:
        raise UploadError("path escapes uploads directory")
    dest.write_bytes(data)
    st = dest.stat()
    return StoredFile(name, st.st_size, _human_size(st.st_size), st.st_mtime)


def list_files() -> list[StoredFile]:
    """List stored files, newest first. Directories are ignored."""
    root = _root()
    out: list[StoredFile] = []
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        st = entry.stat()
        out.append(
            StoredFile(entry.name, st.st_size, _human_size(st.st_size), st.st_mtime)
        )
    out.sort(key=lambda f: f.modified, reverse=True)
    return out


def delete_file(name: str) -> None:
    """Delete a stored file. Raises ``UploadError`` if it can't be resolved."""
    resolve_path(name).unlink()
