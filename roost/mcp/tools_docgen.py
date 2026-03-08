"""MCP tools for document generation — pandoc conversion + Drive upload."""

from roost.mcp.server import mcp


@mcp.tool()
def convert_document(
    input_path: str,
    output_format: str,
    output_path: str = "",
) -> dict:
    """Convert a document between formats using pandoc.

    Supports: markdown → docx, pdf, html, pptx, and many more.

    Args:
        input_path: Path to source file (e.g. "/home/dev/projects/doc.md").
        output_format: Target format — "docx", "pdf", "html", "pptx", etc.
        output_path: Output file path. Empty = same name with new extension.
    """
    try:
        from roost.docgen_service import convert_document as _convert
        return _convert(input_path, output_format, output_path or None)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def convert_and_upload(
    input_path: str,
    output_format: str,
    remote_path: str,
) -> dict:
    """Convert a document and upload to Google Drive in one step.

    Converts using pandoc, then uploads via rclone to the specified Drive path.

    Args:
        input_path: Path to source file.
        output_format: Target format — "docx", "pdf", etc.
        remote_path: Drive destination (e.g. "gdrive:My/Folder/").
    """
    try:
        from roost.docgen_service import convert_and_upload as _convert_upload
        return _convert_upload(input_path, output_format, remote_path)
    except Exception as e:
        return {"error": str(e)}
