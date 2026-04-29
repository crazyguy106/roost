"""Validator for RPA flow config dicts.

Used by the loader and the `rpa_validate_flow` MCP tool. Keeps validation
close to the interpreter so the two cannot drift — the canonical list of
ops is imported from `_interpreter.HANDLERS`.
"""

from __future__ import annotations

import re
from typing import Any

PLACEHOLDER_RE = re.compile(r"^\$(otp|var:[\w.-]+|param:[\w.-]+|cred:[\w.-]+|state:[\w.-]+)$")

# Canonical op list. The interpreter cross-checks this matches HANDLERS at
# import time, so a new step type added to one but not the other fails fast.
KNOWN_OPS: tuple[str, ...] = (
    "goto", "fill", "click", "wait_for", "wait_ms", "press",
    "select_option",
    "get_otp", "await_user_session",
    "download_one", "download_each", "upload_drive",
    "screenshot", "whatsapp_send", "log",
)

REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "goto": ("url",),
    "fill": ("selector", "value"),
    "click": ("selector",),
    "wait_for": ("selector",),
    "wait_ms": ("ms",),
    "press": ("selector", "key"),
    "select_option": ("selector", "value"),
    "get_otp": (),
    "await_user_session": ("selector",),
    "download_one": ("trigger_selector",),
    "download_each": ("item_selector", "trigger_selector_within"),
    "upload_drive": ("remote_path",),
    "screenshot": (),
    "whatsapp_send": ("to",),
    "log": (),
}


class FlowValidationError(ValueError):
    pass


def validate_placeholder(value: Any) -> list[str]:
    """Return error strings if value contains malformed placeholders."""
    errors: list[str] = []
    if not isinstance(value, str):
        return errors
    if value.startswith("$") and not PLACEHOLDER_RE.match(value):
        errors.append(
            f"malformed placeholder {value!r} "
            "(expected $otp | $var:KEY | $param:KEY | $cred:KEY | $state:KEY)"
        )
    return errors


def validate_flow(cfg: dict) -> list[str]:
    """Return a list of human-readable errors. Empty list = valid."""
    errors: list[str] = []

    if not isinstance(cfg, dict):
        return [f"flow must be a mapping, got {type(cfg).__name__}"]

    portal = cfg.get("portal") or cfg.get("portal_slug")
    if not portal or not isinstance(portal, str):
        errors.append("missing or non-string `portal`")

    steps = cfg.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("`steps` must be a non-empty list")
        return errors

    known_ops = set(KNOWN_OPS)
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step #{i}: must be a mapping")
            continue
        op = step.get("op") or step.get("type")
        if not op:
            errors.append(f"step #{i}: missing `op`")
            continue
        if op not in known_ops:
            errors.append(
                f"step #{i}: unknown op {op!r} (known: {sorted(known_ops)})"
            )
            continue
        for arg in REQUIRED_ARGS.get(op, ()):
            if arg not in step:
                errors.append(f"step #{i} ({op}): missing required arg `{arg}`")
        for k, v in step.items():
            errors.extend(
                f"step #{i} ({op}).{k}: {e}" for e in validate_placeholder(v)
            )

    return errors


def assert_valid(cfg: dict) -> None:
    errs = validate_flow(cfg)
    if errs:
        raise FlowValidationError("; ".join(errs))
