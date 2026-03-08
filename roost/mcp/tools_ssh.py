"""MCP tools for SSH server management and remote command execution.

Provides server CRUD and SSH/SCP operations via subprocess.
"""

import subprocess

from roost.mcp.server import mcp


@mcp.tool()
def register_server(
    name: str,
    host: str,
    user: str = "root",
    port: int = 22,
    key_path: str = "",
    password: str = "",
    description: str = "",
    tags: str = "",
) -> dict:
    """Register a remote server for SSH/Docker/K8s management.

    Args:
        name: Unique server name (alphanumeric, dots, hyphens, underscores, max 63 chars).
        host: Hostname or IP address.
        user: SSH username (default: root).
        port: SSH port (default: 22).
        key_path: Path to SSH private key (optional, uses default if empty).
        password: SSH password (optional, uses sshpass when set and no key_path).
        description: Human-readable description.
        tags: Comma-separated tags for filtering (e.g. "production,docker").
    """
    try:
        from roost.ssh_service import create_server
        from roost.models import ServerCreate

        data = ServerCreate(
            name=name, host=host, port=port, user=user,
            key_path=key_path, password=password,
            description=description, tags=tags,
        )
        server = create_server(data)
        return {
            "status": "created",
            "server": server.model_dump(),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_servers(active_only: bool = True, tag: str = "") -> dict:
    """List registered servers.

    Args:
        active_only: Only show active servers (default: true).
        tag: Filter by tag (e.g. "production"). Empty = no filter.
    """
    try:
        from roost.ssh_service import list_servers as _list_servers

        servers = _list_servers(active_only=active_only, tag=tag)
        return {
            "count": len(servers),
            "servers": [s.model_dump() for s in servers],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_server(
    name: str,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    key_path: str | None = None,
    password: str | None = None,
    description: str | None = None,
    tags: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """Update a registered server's configuration.

    Args:
        name: Server name to update (required).
        host: New hostname/IP.
        port: New SSH port.
        user: New SSH username.
        key_path: New key path.
        password: New password (uses sshpass when set and no key_path).
        description: New description.
        tags: New tags (comma-separated).
        is_active: Set active/inactive.
    """
    try:
        from roost.ssh_service import get_server, update_server as _update
        from roost.models import ServerUpdate

        server = get_server(name)
        if not server:
            return {"error": f"Server '{name}' not found"}

        data = ServerUpdate(
            host=host, port=port, user=user, key_path=key_path,
            password=password, description=description, tags=tags,
            is_active=is_active,
        )
        updated = _update(server.id, data)
        return {
            "status": "updated",
            "server": updated.model_dump() if updated else None,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def remove_server(name: str) -> dict:
    """Remove a registered server.

    Args:
        name: Server name to delete.
    """
    try:
        from roost.ssh_service import get_server, delete_server

        server = get_server(name)
        if not server:
            return {"error": f"Server '{name}' not found"}

        delete_server(server.id)
        return {"status": "deleted", "name": name}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ssh_exec(server: str, command: str, timeout: int = 30) -> dict:
    """Execute a command on a remote server via SSH.

    Args:
        server: Registered server name.
        command: Shell command to execute.
        timeout: Command timeout in seconds (default: 30, max: 600).
    """
    try:
        from roost.ssh_service import run_ssh
        from roost.mcp.sanitize import mask_secrets

        stdout, stderr, rc = run_ssh(server, command, timeout=timeout)
        return {
            "server": server,
            "command": command,
            "returncode": rc,
            "stdout": mask_secrets(stdout),
            "stderr": mask_secrets(stderr),
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def scp_upload(server: str, local_path: str, remote_path: str, timeout: int = 300) -> dict:
    """Upload a file to a remote server via SCP.

    Args:
        server: Registered server name.
        local_path: Local file path to upload.
        remote_path: Remote destination path.
        timeout: Transfer timeout in seconds (default: 300).
    """
    try:
        import os
        if not os.path.exists(local_path):
            return {"error": f"Local path not found: {local_path}"}

        from roost.ssh_service import run_scp_upload

        stdout, stderr, rc = run_scp_upload(server, local_path, remote_path, timeout=timeout)
        return {
            "server": server,
            "local_path": local_path,
            "remote_path": remote_path,
            "returncode": rc,
            "status": "success" if rc == 0 else "failed",
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"SCP upload timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def scp_download(server: str, remote_path: str, local_path: str, timeout: int = 300) -> dict:
    """Download a file from a remote server via SCP.

    Args:
        server: Registered server name.
        remote_path: Remote file path to download.
        local_path: Local destination path.
        timeout: Transfer timeout in seconds (default: 300).
    """
    try:
        from roost.ssh_service import run_scp_download

        stdout, stderr, rc = run_scp_download(server, remote_path, local_path, timeout=timeout)
        return {
            "server": server,
            "remote_path": remote_path,
            "local_path": local_path,
            "returncode": rc,
            "status": "success" if rc == 0 else "failed",
            "stderr": stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"SCP download timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}
