"""Layer 3 of AI CDR: sanitize obvious prompt injection patterns.

NOT a primary defense — the primary defense is architectural (no tools).
This is defense-in-depth that catches unsophisticated attacks.
"""

import re

# Patterns that indicate prompt injection attempts.
# These catch obvious attacks; sophisticated attacks will bypass them.
# That's fine — the tool-less classifier is the real defense.
_INJECTION_PATTERNS = [
    # Direct instruction override
    r"(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?(?:previous|prior|above|earlier|existing|original)\s+(?:instructions|rules|prompts|directives|guidelines|context)",
    # Role reassignment
    r"you\s+are\s+now\s+(?:a|an|the|my)",
    r"(?:act|behave|respond|pretend)\s+as\s+(?:a|an|the|if)",
    # New instruction injection
    r"(?:new|updated|revised|current|real)\s+(?:instructions|rules|system\s*prompt|directives)\s*:",
    # System/admin tags
    r"<\s*/?\s*(?:system|admin|root|internal)\s*>",
    r"\[\s*(?:SYSTEM|ADMIN|OVERRIDE|PRIORITY|URGENT)\s*(?:MESSAGE|UPDATE|COMMAND|PROMPT)?\s*\]",
    # Hidden instruction markers
    r"(?:BEGIN|START)\s+(?:HIDDEN|SECRET|REAL)\s+(?:INSTRUCTIONS|PROMPT)",
    # Translation-based evasion
    r"(?:translate|convert)\s+.*?(?:ignore|disregard|override)",
    # Conversation reset
    r"(?:reset|clear|wipe)\s+(?:your\s+)?(?:context|memory|history|conversation)",
    # Prompt leaking
    r"(?:show|reveal|output|print|display)\s+(?:your\s+)?(?:system\s*prompt|instructions|rules)",
    # Developer mode / jailbreak
    r"(?:developer|debug|admin|god)\s+mode",
    r"(?:DAN|STAN|DUDE)\s+(?:mode|prompt)",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def sanitize(text: str) -> str:
    """Strip obvious prompt injection patterns from text.

    Returns cleaned text with injection attempts replaced by [removed].
    This is Layer 3 of the AI CDR pipeline — defense in depth, not primary.
    """
    result = text
    for pattern in _COMPILED_PATTERNS:
        result = pattern.sub("[removed]", result)
    return result
