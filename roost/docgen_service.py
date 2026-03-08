"""Document generation service — pandoc wrapper for format conversion.

Converts between document formats (Markdown → DOCX, PDF, etc.) and
optionally uploads to Google Drive via rclone.
"""

import logging
import os
import subprocess
import shutil

logger = logging.getLogger("roost.docgen")


def check_pandoc() -> bool:
    """Check if pandoc is available."""
    return shutil.which("pandoc") is not None


def convert_document(
    input_path: str,
    output_format: str,
    output_path: str | None = None,
) -> dict:
    """Convert a document using pandoc.

    Args:
        input_path: Path to source file.
        output_format: Target format (docx, pdf, html, pptx, etc.).
        output_path: Output file path. If None, uses input path with new extension.

    Returns:
        Dict with output_path and status.
    """
    if not check_pandoc():
        return {"error": "pandoc not found — install with: apt install pandoc"}

    if not os.path.isfile(input_path):
        return {"error": f"Input file not found: {input_path}"}

    if not output_path:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}.{output_format}"

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    cmd = ["pandoc", input_path, "-o", output_path]

    # PDF needs a latex engine — use tectonic or wkhtmltopdf as fallback
    if output_format == "pdf":
        if shutil.which("tectonic"):
            cmd.extend(["--pdf-engine=tectonic"])
        elif shutil.which("xelatex"):
            cmd.extend(["--pdf-engine=xelatex"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return {"error": f"pandoc failed: {result.stderr.strip()}"}

        size = os.path.getsize(output_path)
        logger.info("Converted %s → %s (%d bytes)", input_path, output_path, size)

        return {
            "ok": True,
            "input": input_path,
            "output": output_path,
            "format": output_format,
            "size_bytes": size,
        }
    except subprocess.TimeoutExpired:
        return {"error": "pandoc timed out (120s limit)"}
    except Exception as e:
        return {"error": str(e)}


def convert_and_upload(
    input_path: str,
    output_format: str,
    remote_path: str,
) -> dict:
    """Convert a document and upload to Google Drive.

    Args:
        input_path: Path to source file.
        output_format: Target format (docx, pdf, etc.).
        remote_path: rclone remote path (e.g. "gdrive:My/Folder/").

    Returns:
        Dict with conversion and upload status.
    """
    if not shutil.which("rclone"):
        return {"error": "rclone not found"}

    # Convert first
    base = os.path.basename(input_path)
    name, _ = os.path.splitext(base)
    tmp_output = f"/tmp/{name}.{output_format}"

    conv_result = convert_document(input_path, output_format, tmp_output)
    if "error" in conv_result:
        return conv_result

    # Upload via rclone
    try:
        result = subprocess.run(
            ["rclone", "copy", tmp_output, remote_path, "--verbose"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return {
                "error": f"rclone upload failed: {result.stderr.strip()}",
                "conversion": conv_result,
            }

        remote_file = f"{remote_path.rstrip('/')}/{name}.{output_format}"
        logger.info("Uploaded %s → %s", tmp_output, remote_file)

        return {
            "ok": True,
            "input": input_path,
            "local_output": tmp_output,
            "remote_path": remote_file,
            "format": output_format,
            "size_bytes": conv_result["size_bytes"],
        }
    except subprocess.TimeoutExpired:
        return {"error": "rclone upload timed out (120s limit)"}
    except Exception as e:
        return {"error": str(e)}
