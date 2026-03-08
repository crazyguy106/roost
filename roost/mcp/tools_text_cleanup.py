"""MCP tools for local text cleanup — dash normalization, smart quotes, etc."""

import logging

from roost.mcp.server import mcp

logger = logging.getLogger(__name__)

# Replacement map: character -> replacement
DASH_REPLACEMENTS = {
    "\u2014": "-",  # em-dash —
    "\u2013": "-",  # en-dash –
}

SMART_QUOTE_REPLACEMENTS = {
    "\u2018": "'",  # left single quote '
    "\u2019": "'",  # right single quote '
    "\u201c": '"',  # left double quote "
    "\u201d": '"',  # right double quote "
}

ALL_REPLACEMENTS = {**DASH_REPLACEMENTS, **SMART_QUOTE_REPLACEMENTS}

SUPPORTED_EXTENSIONS = {".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".html"}


def _fix_text(text: str, replacements: dict[str, str]) -> tuple[str, dict[str, int]]:
    """Apply replacements to text, return (cleaned_text, counts)."""
    counts: dict[str, int] = {}
    for char, replacement in replacements.items():
        n = text.count(char)
        if n > 0:
            text = text.replace(char, replacement)
            counts[repr(char)] = n
    return text, counts


def _process_file(path: str, replacements: dict[str, str]) -> dict:
    """Process a single file. Returns result dict."""
    import pathlib

    p = pathlib.Path(path)
    if not p.exists():
        return {"file": path, "status": "skipped", "reason": "not found"}
    if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return {"file": path, "status": "skipped", "reason": f"unsupported extension {p.suffix}"}

    try:
        original = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"file": path, "status": "skipped", "reason": "not utf-8"}

    cleaned, counts = _fix_text(original, replacements)

    if not counts:
        return {"file": path, "status": "unchanged", "replacements": {}}

    p.write_text(cleaned, encoding="utf-8")
    total = sum(counts.values())
    return {"file": path, "status": "fixed", "replacements": counts, "total_fixes": total}


@mcp.tool()
def fix_dashes(
    path: str,
    include_smart_quotes: bool = False,
    dry_run: bool = False,
) -> dict:
    """Replace em-dashes and en-dashes with regular hyphens in files.

    Works on a single file or recursively on a directory (.md, .txt, .yaml,
    .yml, .json, .csv, .html files).

    Args:
        path: Absolute path to a file or directory.
        include_smart_quotes: Also replace smart/curly quotes with straight ones.
        dry_run: If True, report what would change without modifying files.
    """
    try:
        import pathlib

        replacements = dict(DASH_REPLACEMENTS)
        if include_smart_quotes:
            replacements.update(SMART_QUOTE_REPLACEMENTS)

        p = pathlib.Path(path)

        if not p.exists():
            return {"error": f"Path not found: {path}"}

        files: list[pathlib.Path] = []
        if p.is_file():
            files = [p]
        else:
            for ext in SUPPORTED_EXTENSIONS:
                files.extend(p.rglob(f"*{ext}"))
            files.sort()

        if dry_run:
            results = []
            for f in files:
                try:
                    text = f.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                _, counts = _fix_text(text, replacements)
                if counts:
                    results.append({
                        "file": str(f),
                        "would_fix": counts,
                        "total": sum(counts.values()),
                    })
            return {
                "mode": "dry_run",
                "files_scanned": len(files),
                "files_with_issues": len(results),
                "details": results,
            }

        results = []
        fixed_count = 0
        total_replacements = 0

        for f in files:
            r = _process_file(str(f), replacements)
            if r["status"] == "fixed":
                fixed_count += 1
                total_replacements += r["total_fixes"]
                results.append(r)

        return {
            "mode": "applied",
            "files_scanned": len(files),
            "files_fixed": fixed_count,
            "total_replacements": total_replacements,
            "details": results,
        }

    except Exception as e:
        logger.exception("fix_dashes failed")
        return {"error": str(e)}
