"""Service layer for SSH server management and remote command execution.

Provides server CRUD (SQLite-backed) and SSH/SCP subprocess helpers.
Uses the same subprocess pattern as tools_drive.py (rclone).
"""

import logging
import re
import subprocess
from datetime import datetime

from roost.database import get_connection
from roost.models import Server, ServerCreate, ServerUpdate

logger = logging.getLogger("roost.ssh_service")


def _resolve_uid(user_id: int | None = None) -> int:
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


# Validation
SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$")

# Limits
MAX_STDOUT = 50_000  # chars
MAX_STDERR = 10_000  # chars
DEFAULT_SSH_TIMEOUT = 30  # seconds
MAX_SSH_TIMEOUT = 600  # seconds
DEFAULT_SCP_TIMEOUT = 300  # seconds


# ── Server CRUD ─────────────────────────────────────────────────────


def create_server(data: ServerCreate, user_id: int | None = None) -> Server:
    """Register a new server."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO servers (name, host, port, user, key_path, password, description, tags, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.name, data.host, data.port, data.user,
             data.key_path, data.password, data.description, data.tags, uid),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM servers WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return Server(**dict(row))
    finally:
        conn.close()


def list_servers(active_only: bool = True, tag: str = "", user_id: int | None = None) -> list[Server]:
    """List registered servers, optionally filtered."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    try:
        query = "SELECT * FROM servers"
        conditions = ["user_id = ?"]
        params: list = [uid]

        if active_only:
            conditions.append("is_active = 1")
        if tag:
            conditions.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag},%")

        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY name"

        rows = conn.execute(query, params).fetchall()
        return [Server(**dict(r)) for r in rows]
    finally:
        conn.close()


def get_server(name: str, user_id: int | None = None) -> Server | None:
    """Get a server by name (scoped to current user)."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE name = ? AND user_id = ?", (name, uid)
        ).fetchone()
        return Server(**dict(row)) if row else None
    finally:
        conn.close()


def get_server_by_id(server_id: int, user_id: int | None = None) -> Server | None:
    """Get a server by ID (scoped to current user)."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE id = ? AND user_id = ?", (server_id, uid)
        ).fetchone()
        return Server(**dict(row)) if row else None
    finally:
        conn.close()


def update_server(server_id: int, data: ServerUpdate, user_id: int | None = None) -> Server | None:
    """Update a server's configuration (scoped to current user)."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    try:
        # Verify ownership
        owner_check = conn.execute(
            "SELECT id FROM servers WHERE id = ? AND user_id = ?", (server_id, uid)
        ).fetchone()
        if not owner_check:
            return None

        updates = []
        params = []
        for field, value in data.dict(exclude_unset=True).items():
            if value is not None:
                if field == "is_active":
                    updates.append(f"{field} = ?")
                    params.append(1 if value else 0)
                else:
                    updates.append(f"{field} = ?")
                    params.append(value)

        if not updates:
            return get_server_by_id(server_id, user_id=uid)

        updates.append("updated_at = datetime('now')")
        params.append(server_id)

        conn.execute(
            f"UPDATE servers SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return get_server_by_id(server_id, user_id=uid)
    finally:
        conn.close()


def delete_server(server_id: int, user_id: int | None = None) -> bool:
    """Delete a server by ID (scoped to current user)."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM servers WHERE id = ? AND user_id = ?", (server_id, uid)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _touch_last_connected(server_name: str) -> None:
    """Update last_connected_at timestamp."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE servers SET last_connected_at = datetime('now') WHERE name = ?",
            (server_name,),
        )
        conn.commit()
    finally:
        conn.close()


# ── SSH / SCP helpers ────────────────────────────────────────────────


def _build_ssh_args(server: Server) -> list[str]:
    """Build base SSH arguments for a server.

    When password is set (and no key_path), prepends sshpass for
    non-interactive password auth.
    """
    args = []
    use_password = server.password and not server.key_path
    if use_password:
        args.extend(["sshpass", "-p", server.password])

    args.extend([
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-p", str(server.port),
    ])
    if not use_password:
        args.extend(["-o", "BatchMode=yes"])
    if server.key_path:
        args.extend(["-i", server.key_path])
    args.append(f"{server.user}@{server.host}")
    return args


def _build_scp_args(server: Server) -> list[str]:
    """Build base SCP arguments for a server.

    When password is set (and no key_path), prepends sshpass for
    non-interactive password auth.
    """
    args = []
    use_password = server.password and not server.key_path
    if use_password:
        args.extend(["sshpass", "-p", server.password])

    args.extend([
        "scp",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-P", str(server.port),
    ])
    if not use_password:
        args.extend(["-o", "BatchMode=yes"])
    if server.key_path:
        args.extend(["-i", server.key_path])
    return args


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit, adding a marker if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"


def run_ssh(
    server_name: str,
    command: str,
    timeout: int = DEFAULT_SSH_TIMEOUT,
) -> tuple[str, str, int]:
    """Run a command on a remote server via SSH.

    Returns (stdout, stderr, returncode).
    """
    server = get_server(server_name)
    if not server:
        raise ValueError(f"Server '{server_name}' not found")
    if not server.is_active:
        raise ValueError(f"Server '{server_name}' is inactive")

    timeout = min(max(timeout, 1), MAX_SSH_TIMEOUT)

    args = _build_ssh_args(server)
    args.append(command)

    logger.info("SSH %s: %s", server_name, command[:100])

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    _touch_last_connected(server_name)

    stdout = _truncate(result.stdout, MAX_STDOUT)
    stderr = _truncate(result.stderr, MAX_STDERR)

    return stdout, stderr, result.returncode


def run_scp_upload(
    server_name: str,
    local_path: str,
    remote_path: str,
    timeout: int = DEFAULT_SCP_TIMEOUT,
) -> tuple[str, str, int]:
    """Upload a file to a remote server via SCP.

    Returns (stdout, stderr, returncode).
    """
    server = get_server(server_name)
    if not server:
        raise ValueError(f"Server '{server_name}' not found")
    if not server.is_active:
        raise ValueError(f"Server '{server_name}' is inactive")

    args = _build_scp_args(server)
    args.append(local_path)
    args.append(f"{server.user}@{server.host}:{remote_path}")

    logger.info("SCP upload %s: %s -> %s", server_name, local_path, remote_path)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    _touch_last_connected(server_name)

    return (
        _truncate(result.stdout, MAX_STDOUT),
        _truncate(result.stderr, MAX_STDERR),
        result.returncode,
    )


def run_scp_download(
    server_name: str,
    remote_path: str,
    local_path: str,
    timeout: int = DEFAULT_SCP_TIMEOUT,
) -> tuple[str, str, int]:
    """Download a file from a remote server via SCP.

    Returns (stdout, stderr, returncode).
    """
    server = get_server(server_name)
    if not server:
        raise ValueError(f"Server '{server_name}' not found")
    if not server.is_active:
        raise ValueError(f"Server '{server_name}' is inactive")

    args = _build_scp_args(server)
    args.append(f"{server.user}@{server.host}:{remote_path}")
    args.append(local_path)

    logger.info("SCP download %s: %s -> %s", server_name, remote_path, local_path)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    _touch_last_connected(server_name)

    return (
        _truncate(result.stdout, MAX_STDOUT),
        _truncate(result.stderr, MAX_STDERR),
        result.returncode,
    )
