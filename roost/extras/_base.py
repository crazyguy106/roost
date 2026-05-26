"""Base Bundle descriptor used by every extras/<name>/__init__.py.

A bundle's __init__.py is expected to construct exactly one Bundle
instance and bind it to module-level `BUNDLE`. The registry in
roost.extras imports each bundle module and reads `BUNDLE`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from fastapi import FastAPI


# A bundle's `register` callback gets the app and the FastMCP instance.
# It should mount any APIRouters and import its `tools_*` modules so the
# @mcp.tool() decorators run.
RegisterFn = Callable[["FastAPI", object], None]

# Schema callback returns the bundle's CREATE TABLE statements joined
# into one string. Idempotent — uses CREATE TABLE IF NOT EXISTS.
SchemaFn = Callable[[], str]


@dataclass(frozen=True)
class Bundle:
    name: str
    flag_name: str
    register: RegisterFn
    schema_sql: SchemaFn = lambda: ""

    def enabled(self) -> bool:
        from roost import config
        return bool(getattr(config, self.flag_name, False))
