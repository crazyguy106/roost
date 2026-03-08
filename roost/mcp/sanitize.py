"""Output sanitizer — masks secrets and sensitive data in command output.

Applied to SSH exec and Kubernetes tool output to prevent credential
leaks into the LLM context window.
"""

import re

# Patterns that strongly indicate secrets — replace the value portion
_PATTERNS = [
    # AWS access keys (AKIA...)
    (re.compile(r"(AKIA[0-9A-Z]{16})"), "AKIA***REDACTED***"),
    # AWS secret keys (40-char base64 after common prefixes)
    (re.compile(r"(?i)(aws_secret_access_key\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(aws_access_key_id\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    # Generic API keys/tokens/secrets in key=value or key: value format
    (re.compile(r"(?i)((?:api[_-]?key|api[_-]?secret|auth[_-]?token|access[_-]?token|secret[_-]?key|private[_-]?key|password|passwd|db_password|database_password|secret)\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    # Bearer tokens
    (re.compile(r"(?i)(Bearer\s+)\S+"), r"\1***REDACTED***"),
    # Private key blocks
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "***PRIVATE KEY REDACTED***"),
    # Passwords in URLs (proto://user:pass@host)
    (re.compile(r"(://[^:]+:)[^@]+(@)"), r"\1***REDACTED***\2"),
    # GitHub/GitLab personal access tokens (ghp_, glpat-)
    (re.compile(r"(ghp_)[A-Za-z0-9]{20,}"), r"\1***REDACTED***"),
    (re.compile(r"(glpat-)[A-Za-z0-9\-]{20,}"), r"\1***REDACTED***"),
    # Slack tokens (xoxb-, xoxp-, xoxs-)
    (re.compile(r"(xox[bps]-)[A-Za-z0-9\-]+"), r"\1***REDACTED***"),
    # base64-encoded K8s secrets (data fields in kubectl output)
    (re.compile(r"(?i)((?:password|token|secret|key|cert):\s*)\S{20,}"), r"\1***REDACTED***"),
]


def mask_secrets(text: str) -> str:
    """Apply all secret patterns to text, replacing matches with REDACTED."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
