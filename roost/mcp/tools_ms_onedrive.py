"""MCP tools for Microsoft OneDrive file operations."""

from roost.mcp.server import mcp


@mcp.tool()
def ms_onedrive_list(path: str = "/", max_results: int = 100) -> dict:
    """List files and folders in a OneDrive path.

    Args:
        path: OneDrive path (e.g. "/" for root, "/Documents/Reports").
            Defaults to root.
        max_results: Maximum items to return (default 100).
    """
    try:
        from roost.mcp.ms_graph_helpers import onedrive_list

        entries = onedrive_list(path=path, max_results=max_results)
        return {
            "path": path,
            "count": len(entries),
            "entries": entries,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_onedrive_download(remote_path: str, local_dir: str = "/tmp/onedrive-download/") -> dict:
    """Download a file from OneDrive to the local filesystem.

    Args:
        remote_path: OneDrive file path (e.g. "/Documents/report.docx").
        local_dir: Local directory to download into (default /tmp/onedrive-download/).
    """
    try:
        from roost.mcp.ms_graph_helpers import onedrive_download

        return onedrive_download(remote_path=remote_path, local_dir=local_dir)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_onedrive_upload(local_path: str, remote_path: str) -> dict:
    """Upload a local file to OneDrive. Auto-selects simple (<4MB) or resumable (up to 250MB) upload.

    Args:
        local_path: Local file path to upload.
        remote_path: OneDrive destination path (e.g. "/Documents/report.docx").
    """
    try:
        import os
        file_size = os.path.getsize(local_path)
        if file_size <= 4 * 1024 * 1024:
            from roost.mcp.ms_graph_helpers import onedrive_upload
            return onedrive_upload(local_path=local_path, remote_path=remote_path)
        else:
            from roost.mcp.ms_graph_helpers import onedrive_upload_large
            return onedrive_upload_large(local_path=local_path, remote_path=remote_path)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_onedrive_search(query: str) -> dict:
    """Search OneDrive for files by name or content.

    Args:
        query: Search query string (searches file names and content).
    """
    try:
        from roost.mcp.ms_graph_helpers import onedrive_search

        results = onedrive_search(query=query)
        return {
            "query": query,
            "count": len(results),
            "files": results,
        }
    except Exception as e:
        return {"error": str(e)}
