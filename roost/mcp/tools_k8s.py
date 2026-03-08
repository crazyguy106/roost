"""MCP tools for Kubernetes management on remote servers via SSH.

All kubectl commands are executed remotely through the ssh_exec helper.
The remote server must have kubectl configured with cluster access.
Output is sanitized to mask secrets and credentials.
"""

from roost.mcp.server import mcp
from roost.mcp.sanitize import mask_secrets

KUBECTL_TIMEOUT = 30  # seconds for most kubectl commands
KUBECTL_APPLY_TIMEOUT = 60  # seconds for apply operations


@mcp.tool()
def kubectl_get(
    server: str,
    resource: str,
    namespace: str = "",
    name: str = "",
    output: str = "",
    selector: str = "",
    all_namespaces: bool = False,
) -> dict:
    """Get Kubernetes resources on a remote server.

    Args:
        server: Registered server name.
        resource: Resource type (e.g. "pods", "services", "deployments", "nodes").
        namespace: Kubernetes namespace (empty = default namespace).
        name: Specific resource name (optional).
        output: Output format — "wide", "yaml", "json", "name" (optional).
        selector: Label selector (e.g. "app=nginx,tier=frontend").
        all_namespaces: List across all namespaces (default: false).
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"kubectl get {resource}"
        if name:
            cmd += f" {name}"
        if all_namespaces:
            cmd += " --all-namespaces"
        elif namespace:
            cmd += f" -n {namespace}"
        if output:
            cmd += f" -o {output}"
        if selector:
            cmd += f" -l '{selector}'"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=KUBECTL_TIMEOUT)
        return {
            "server": server,
            "resource": resource,
            "returncode": rc,
            "output": mask_secrets(stdout),
            "stderr": mask_secrets(stderr) if rc != 0 else "",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def kubectl_describe(
    server: str,
    resource: str,
    name: str,
    namespace: str = "",
) -> dict:
    """Describe a Kubernetes resource on a remote server.

    Args:
        server: Registered server name.
        resource: Resource type (e.g. "pod", "service", "deployment").
        name: Resource name.
        namespace: Kubernetes namespace (empty = default namespace).
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"kubectl describe {resource} {name}"
        if namespace:
            cmd += f" -n {namespace}"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=KUBECTL_TIMEOUT)
        return {
            "server": server,
            "resource": f"{resource}/{name}",
            "returncode": rc,
            "output": mask_secrets(stdout),
            "stderr": mask_secrets(stderr) if rc != 0 else "",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def kubectl_logs(
    server: str,
    pod: str,
    namespace: str = "",
    container: str = "",
    tail: int = 100,
    since: str = "",
    previous: bool = False,
) -> dict:
    """View logs from a Kubernetes pod on a remote server.

    Args:
        server: Registered server name.
        pod: Pod name.
        namespace: Kubernetes namespace (empty = default namespace).
        container: Container name (required for multi-container pods).
        tail: Number of lines from the end (default: 100).
        since: Show logs since duration (e.g. "1h", "30m", "5s").
        previous: Show logs from previous terminated container (default: false).
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"kubectl logs {pod} --tail={tail}"
        if namespace:
            cmd += f" -n {namespace}"
        if container:
            cmd += f" -c {container}"
        if since:
            cmd += f" --since={since}"
        if previous:
            cmd += " --previous"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=KUBECTL_TIMEOUT)
        return {
            "server": server,
            "pod": pod,
            "returncode": rc,
            "output": mask_secrets(stdout),
            "stderr": mask_secrets(stderr) if rc != 0 else "",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def kubectl_apply(
    server: str,
    file: str = "",
    yaml_content: str = "",
    namespace: str = "",
) -> dict:
    """Apply a Kubernetes manifest on a remote server.

    Provide either a file path (on the remote server) or inline YAML content.

    Args:
        server: Registered server name.
        file: Remote file path to a YAML manifest (e.g. "/opt/k8s/deployment.yaml").
        yaml_content: Inline YAML manifest content (piped to kubectl via stdin).
        namespace: Kubernetes namespace (empty = use manifest's namespace or default).
    """
    try:
        if not file and not yaml_content:
            return {"error": "Provide either 'file' or 'yaml_content'"}

        from roost.ssh_service import run_ssh

        if file:
            cmd = f"kubectl apply -f {file}"
        else:
            # Pipe YAML via heredoc
            # Escape single quotes in YAML content for shell safety
            escaped = yaml_content.replace("'", "'\\''")
            cmd = f"echo '{escaped}' | kubectl apply -f -"

        if namespace:
            cmd += f" -n {namespace}"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=KUBECTL_APPLY_TIMEOUT)
        return {
            "server": server,
            "returncode": rc,
            "output": mask_secrets(stdout + stderr),
            "status": "applied" if rc == 0 else "failed",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def kubectl_delete(
    server: str,
    resource: str,
    name: str,
    namespace: str = "",
    force: bool = False,
) -> dict:
    """Delete a Kubernetes resource on a remote server.

    Args:
        server: Registered server name.
        resource: Resource type (e.g. "pod", "service", "deployment").
        name: Resource name.
        namespace: Kubernetes namespace (empty = default namespace).
        force: Force deletion (default: false).
    """
    try:
        from roost.ssh_service import run_ssh

        cmd = f"kubectl delete {resource} {name}"
        if namespace:
            cmd += f" -n {namespace}"
        if force:
            cmd += " --force --grace-period=0"

        stdout, stderr, rc = run_ssh(server, cmd, timeout=KUBECTL_APPLY_TIMEOUT)
        return {
            "server": server,
            "resource": f"{resource}/{name}",
            "returncode": rc,
            "output": mask_secrets(stdout + stderr),
            "status": "deleted" if rc == 0 else "failed",
        }
    except Exception as e:
        return {"error": str(e)}
