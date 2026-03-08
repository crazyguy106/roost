"""MCP tools for Docker management on remote servers via SSH.

All Docker commands are executed remotely through the ssh_exec helper,
so Docker does not need to be installed locally.
"""

from roost.mcp.server import mcp

DOCKER_TIMEOUT = 30  # seconds for most docker commands
COMPOSE_TIMEOUT = 120  # seconds for compose up/down


@mcp.tool()
def docker_ps(server: str, all: bool = False, format: str = "") -> dict:
    """List Docker containers on a remote server.

    Args:
        server: Registered server name.
        all: Show all containers including stopped (default: false, running only).
        format: Custom Go template format string (optional). If empty, uses a
                readable default.
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = "docker ps"
        if all:
            cmd += " -a"
        if format:
            cmd += f" --format '{format}'"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=DOCKER_TIMEOUT)
        return {
            "server": server,
            "returncode": rc,
            "output": stdout,
            "stderr": stderr if rc != 0 else "",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def docker_logs(
    server: str,
    container: str,
    tail: int = 100,
    since: str = "",
    follow: bool = False,
) -> dict:
    """View logs from a Docker container on a remote server.

    Args:
        server: Registered server name.
        container: Container name or ID.
        tail: Number of lines from the end (default: 100).
        since: Show logs since timestamp or relative (e.g. "1h", "2024-01-01").
        follow: Not supported in MCP (non-interactive). Ignored.
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"docker logs --tail {tail}"
        if since:
            cmd += f" --since '{since}'"
        cmd += f" {container}"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=DOCKER_TIMEOUT)
        # Docker logs writes to both stdout and stderr
        output = stdout + stderr if rc == 0 else stdout
        return {
            "server": server,
            "container": container,
            "returncode": rc,
            "output": output,
            "stderr": stderr if rc != 0 else "",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def docker_pull(server: str, image: str) -> dict:
    """Pull a Docker image on a remote server.

    Args:
        server: Registered server name.
        image: Image to pull (e.g. "nginx:latest", "ghcr.io/org/app:v1.2").
    """
    try:
        from roost.ssh_service import run_ssh

        stdout, stderr, rc = run_ssh(
            server, f"docker pull {image}", timeout=COMPOSE_TIMEOUT,
        )
        return {
            "server": server,
            "image": image,
            "returncode": rc,
            "output": stdout + stderr,
            "status": "success" if rc == 0 else "failed",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def docker_compose_up(
    server: str,
    path: str,
    detach: bool = True,
    build: bool = False,
    services: str = "",
) -> dict:
    """Start Docker Compose services on a remote server.

    Args:
        server: Registered server name.
        path: Remote path to docker-compose.yml directory.
        detach: Run in background (default: true).
        build: Rebuild images before starting (default: false).
        services: Space-separated service names to start (empty = all).
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"cd {path} && docker compose up"
        if detach:
            cmd += " -d"
        if build:
            cmd += " --build"
        if services:
            cmd += f" {services}"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=COMPOSE_TIMEOUT)
        return {
            "server": server,
            "path": path,
            "returncode": rc,
            "output": stdout + stderr,
            "status": "success" if rc == 0 else "failed",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def docker_compose_down(
    server: str,
    path: str,
    volumes: bool = False,
    remove_orphans: bool = False,
) -> dict:
    """Stop Docker Compose services on a remote server.

    Args:
        server: Registered server name.
        path: Remote path to docker-compose.yml directory.
        volumes: Remove named volumes (default: false).
        remove_orphans: Remove containers not defined in compose file (default: false).
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"cd {path} && docker compose down"
        if volumes:
            cmd += " -v"
        if remove_orphans:
            cmd += " --remove-orphans"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=COMPOSE_TIMEOUT)
        return {
            "server": server,
            "path": path,
            "returncode": rc,
            "output": stdout + stderr,
            "status": "success" if rc == 0 else "failed",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def docker_compose_ps(server: str, path: str) -> dict:
    """List Docker Compose service status on a remote server.

    Args:
        server: Registered server name.
        path: Remote path to docker-compose.yml directory.
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"cd {path} && docker compose ps"
        stdout, stderr, rc = run_ssh(server, cmd, timeout=DOCKER_TIMEOUT)
        return {
            "server": server,
            "path": path,
            "returncode": rc,
            "output": stdout,
            "stderr": stderr if rc != 0 else "",
        }
    except Exception as e:
        return {"error": str(e)}
