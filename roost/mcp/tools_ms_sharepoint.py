"""MCP tools for Microsoft SharePoint — sites and document libraries via Graph API."""

from roost.mcp.server import mcp


@mcp.tool()
def ms_sharepoint_list_sites(query: str = "") -> dict:
    """List or search SharePoint sites.

    Without a query, returns sites the user follows.
    With a query, searches all accessible sites.

    Args:
        query: Optional search query (empty = list followed sites).
    """
    try:
        from roost.mcp.ms_graph_helpers import sharepoint_list_sites

        sites = sharepoint_list_sites(query)
        return {"count": len(sites), "sites": sites}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_sharepoint_list_files(site_id: str, path: str = "/", max_results: int = 100) -> dict:
    """List files and folders in a SharePoint site's document library.

    Args:
        site_id: The SharePoint site ID (from ms_sharepoint_list_sites).
        path: Path within the document library (e.g. "/" for root, "/Reports/Q4").
        max_results: Maximum items to return (default 100).
    """
    try:
        from roost.mcp.ms_graph_helpers import sharepoint_list_files

        entries = sharepoint_list_files(site_id, path, max_results)
        return {
            "site_id": site_id,
            "path": path,
            "count": len(entries),
            "entries": entries,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_sharepoint_download(site_id: str, file_path: str, local_dir: str = "/tmp/sharepoint-download/") -> dict:
    """Download a file from a SharePoint site's document library.

    Args:
        site_id: The SharePoint site ID.
        file_path: Path within the document library (e.g. "/Reports/Q4.xlsx").
        local_dir: Local directory to download into (default /tmp/sharepoint-download/).
    """
    try:
        from roost.mcp.ms_graph_helpers import sharepoint_download

        return sharepoint_download(site_id, file_path, local_dir)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_sharepoint_upload(site_id: str, local_path: str, remote_path: str) -> dict:
    """Upload a local file to a SharePoint site's document library (simple upload, <4MB).

    Args:
        site_id: The SharePoint site ID.
        local_path: Local file path to upload.
        remote_path: Path within the document library (e.g. "/Reports/Q4.xlsx").
    """
    try:
        from roost.mcp.ms_graph_helpers import sharepoint_upload

        return sharepoint_upload(site_id, local_path, remote_path)
    except Exception as e:
        return {"error": str(e)}
