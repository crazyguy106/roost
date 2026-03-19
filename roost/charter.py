"""Charter loader — reads agent identity files from data/charter/.

The charter is the agent's core operating definition: purpose, voice,
values, and boundaries. Provider-specific files add behavioral tuning
for each AI engine.

Layout:
    data/charter/
    ├── charter.md       ← Core identity (always loaded)
    ├── gemini.md        ← Gemini-specific tuning (optional)
    ├── claude.md        ← Claude-specific tuning (optional)
    └── openai.md        ← OpenAI-specific tuning (optional)

The charter is loaded once and cached. Call reload_charter() after
edits to pick up changes without restart.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger("roost.charter")

# Charter directory — relative to project root (data/charter/)
_CHARTER_DIR = Path(os.getenv(
    "CHARTER_DIR",
    Path(__file__).parent.parent / "data" / "charter",
))

# Cache: provider -> assembled prompt
_cache: dict[str, str] = {}


def _read_file(path: Path) -> str:
    """Read a charter file, returning empty string if missing."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except Exception:
        logger.warning("Failed to read charter file: %s", path, exc_info=True)
        return ""


def get_charter(provider: str = "") -> str:
    """Load the assembled charter for a given provider.

    Returns charter.md content, plus provider-specific file if it exists.
    Results are cached — call reload_charter() to refresh after edits.

    Args:
        provider: AI provider name (gemini, claude, openai, ollama).
                  Empty string returns just the core charter.
    """
    cache_key = provider or "_core"
    if cache_key in _cache:
        return _cache[cache_key]

    # Core charter (always loaded)
    core = _read_file(_CHARTER_DIR / "charter.md")
    if not core:
        logger.info("No charter.md found at %s — using empty charter", _CHARTER_DIR)

    # Provider-specific addendum
    provider_text = ""
    if provider:
        # Normalise provider name (ollama uses openai-compatible, check both)
        provider_file = _CHARTER_DIR / f"{provider}.md"
        provider_text = _read_file(provider_file)

    # Assemble
    if provider_text:
        result = core + "\n\n---\n\n" + provider_text
    else:
        result = core

    _cache[cache_key] = result
    return result


def get_charter_raw() -> str:
    """Read the raw charter.md content (for editing in the web UI)."""
    return _read_file(_CHARTER_DIR / "charter.md")


def save_charter(content: str) -> None:
    """Write charter.md content and clear cache."""
    _CHARTER_DIR.mkdir(parents=True, exist_ok=True)
    (_CHARTER_DIR / "charter.md").write_text(content, encoding="utf-8")
    reload_charter()
    logger.info("Charter saved (%d chars)", len(content))


def get_provider_charter_raw(provider: str) -> str:
    """Read a provider-specific charter file (for editing)."""
    return _read_file(_CHARTER_DIR / f"{provider}.md")


def save_provider_charter(provider: str, content: str) -> None:
    """Write a provider-specific charter file and clear cache."""
    if provider not in ("gemini", "claude", "openai", "ollama"):
        raise ValueError(f"Unknown provider: {provider}")
    _CHARTER_DIR.mkdir(parents=True, exist_ok=True)
    (_CHARTER_DIR / f"{provider}.md").write_text(content, encoding="utf-8")
    reload_charter()
    logger.info("Provider charter saved: %s (%d chars)", provider, len(content))


def list_charter_files() -> list[dict]:
    """List all charter files with their sizes."""
    result = []
    if not _CHARTER_DIR.exists():
        return result
    for f in sorted(_CHARTER_DIR.glob("*.md")):
        result.append({
            "name": f.stem,
            "file": f.name,
            "size": f.stat().st_size,
            "is_core": f.name == "charter.md",
        })
    return result


def reload_charter() -> None:
    """Clear the cache so next get_charter() re-reads from disk."""
    _cache.clear()
    logger.debug("Charter cache cleared")
