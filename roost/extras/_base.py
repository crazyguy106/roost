"""Base Bundle descriptor used by every extras/<name>/__init__.py.

A bundle's __init__.py is expected to construct exactly one Bundle
instance and bind it to module-level `BUNDLE`. The registry in
roost.extras imports each bundle module and reads `BUNDLE`.
"""

from __future__ import annotations

import sqlite3
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

# Migration callback runs after the bundle's CREATE TABLE batch. Receives
# a live connection; should perform any ALTER TABLE / data-migration steps
# and tolerate being re-run (e.g. catch sqlite3.OperationalError on
# "duplicate column").
MigrateFn = Callable[[sqlite3.Connection], None]


def _noop_migrate(_conn: sqlite3.Connection) -> None:
    return None


@dataclass(frozen=True)
class Bundle:
    name: str
    flag_name: str
    register: RegisterFn
    schema_sql: SchemaFn = lambda: ""
    migrate: MigrateFn = _noop_migrate

    def enabled(self) -> bool:
        from roost import config
        return bool(getattr(config, self.flag_name, False))
