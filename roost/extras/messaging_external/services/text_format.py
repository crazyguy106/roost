"""Outbound message-text normalisation shared across messaging channels.

Question packs and cadence templates are authored as YAML ``|`` block
scalars, hand-wrapped at ~70 columns for editor readability. ``|`` keeps
those wrap points as *hard* newlines, so the text renders with awkward
mid-sentence breaks on WhatsApp — and inconsistently across WhatsApp
clients. ``collapse_soft_wraps`` folds those soft wraps back into flowing
lines at send time, so every outbound surface is consistent regardless of
how the source text was formatted.
"""

from __future__ import annotations

import re

# Bullet or numbered list item: "- ", "* ", "• ", "1. ", "1) "
_LIST_RE = re.compile(r"^\s*([-*•]|\d+[.)])\s")

# Only fold a continuation into the previous line when that line is long
# enough to look like a wrap point. Hand-wrapped prose lines run ~55–75
# chars; intentionally short lines (signatures, labels, single-word lines)
# stay separate. 45 sits comfortably between the two.
_WRAP_THRESHOLD = 45


def collapse_soft_wraps(text: str, *, wrap_threshold: int = _WRAP_THRESHOLD) -> str:
    """Join soft-wrapped lines into flowing text for chat surfaces.

    Folds a newline between two lines into a space **only** when the
    preceding line is at least ``wrap_threshold`` chars long (i.e. it looks
    like a mid-paragraph wrap), preserving:

      * blank-line paragraph breaks,
      * list items (``-``, ``*``, ``•``, ``1.``/``1)``),
      * a line that ends in ``:`` (a label/intro before a list),
      * short intentional line breaks such as a signature block.

    Idempotent — running it on already-flowing text is a no-op.
    """
    if not text or "\n" not in text:
        return text or ""

    out: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            out.append("")  # preserve paragraph break
            continue
        prev = out[-1] if out else ""
        if (
            prev
            and len(prev) >= wrap_threshold
            and not prev.endswith(":")
            and not _LIST_RE.match(raw)
        ):
            out[-1] = f"{prev} {line}"
        else:
            out.append(line)

    collapsed = "\n".join(out)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)  # cap blank runs
    return collapsed.strip()
