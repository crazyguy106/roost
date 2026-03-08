"""MCP tools for Google Drive operations via rclone.

All tools shell out to `rclone` which is configured with a `gdrive:` remote.
Designed for curriculum development workflows — pulling/pushing documents
between local filesystem and Google Drive.
"""

import json
import subprocess

from roost.mcp.server import mcp

RCLONE_TIMEOUT = 60  # seconds


def _run_rclone(args: list[str], timeout: int = RCLONE_TIMEOUT) -> tuple[str, str, int]:
    """Run an rclone command and return (stdout, stderr, returncode)."""
    result = subprocess.run(
        ["rclone"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


@mcp.tool()
def drive_list(path: str = "", max_depth: int = 1) -> dict:
    """List files and folders in a Google Drive path.

    Args:
        path: rclone remote path (e.g. "gdrive:My/Folder/").
              Defaults to empty (must be provided).
        max_depth: How many levels deep to list (default 1, max 5).
    """
    try:
        max_depth = min(max(max_depth, 1), 5)
        stdout, stderr, rc = _run_rclone([
            "lsjson", path,
            "--max-depth", str(max_depth),
        ])

        if rc != 0:
            return {"error": f"rclone failed: {stderr.strip()}"}

        items = json.loads(stdout) if stdout.strip() else []

        entries = []
        for item in items:
            entries.append({
                "name": item.get("Name", ""),
                "path": item.get("Path", ""),
                "is_dir": item.get("IsDir", False),
                "size": item.get("Size", 0),
                "modified": item.get("ModTime", ""),
                "mime_type": item.get("MimeType", ""),
            })

        # Sort: directories first, then by name
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

        return {
            "remote_path": path,
            "count": len(entries),
            "entries": entries,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"rclone timed out after {RCLONE_TIMEOUT}s"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def drive_download(remote_path: str, local_dir: str = "/tmp/drive-download/") -> dict:
    """Download a file or folder from Google Drive to the local filesystem.

    Args:
        remote_path: rclone remote path to download
            (e.g. "gdrive:My/Folder/subfolder/").
        local_dir: Local directory to download into (default /tmp/drive-download/).
    """
    try:
        # Ensure local directory exists
        import os
        os.makedirs(local_dir, exist_ok=True)

        stdout, stderr, rc = _run_rclone([
            "copy", remote_path, local_dir,
            "--verbose",
        ], timeout=300)  # 5 min timeout for downloads

        if rc != 0:
            return {"error": f"rclone copy failed: {stderr.strip()}"}

        # List what was downloaded
        downloaded = []
        for root, dirs, files in os.walk(local_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_dir)
                downloaded.append({
                    "path": full,
                    "relative": rel,
                    "size": os.path.getsize(full),
                })

        return {
            "remote_path": remote_path,
            "local_dir": local_dir,
            "files_downloaded": len(downloaded),
            "files": downloaded,
            "rclone_output": stderr.strip()[-500:] if stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"error": "rclone download timed out after 300s"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def drive_upload(local_path: str, remote_path: str) -> dict:
    """Upload a local file or directory to Google Drive.

    Args:
        local_path: Local file or directory path to upload.
        remote_path: rclone remote destination path
            (e.g. "gdrive:My/Folder/subfolder/").
    """
    try:
        import os
        if not os.path.exists(local_path):
            return {"error": f"Local path not found: {local_path}"}

        stdout, stderr, rc = _run_rclone([
            "copy", local_path, remote_path,
            "--verbose",
        ], timeout=300)

        if rc != 0:
            return {"error": f"rclone upload failed: {stderr.strip()}"}

        # Count what was uploaded
        if os.path.isfile(local_path):
            file_count = 1
        else:
            file_count = sum(len(files) for _, _, files in os.walk(local_path))

        return {
            "local_path": local_path,
            "remote_path": remote_path,
            "files_uploaded": file_count,
            "status": "success",
            "rclone_output": stderr.strip()[-500:] if stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"error": "rclone upload timed out after 300s"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def drive_search(pattern: str, path: str = "") -> dict:
    """Search for files on Google Drive by filename pattern.

    Recursively lists all files under the given path and filters by pattern.
    Useful for finding curriculum documents, slide decks, etc.

    Args:
        pattern: Case-insensitive substring to match against filenames
            (e.g. "M1", "lab-guide", ".docx", "assessment").
        path: rclone remote path to search under (default: programmes root).
    """
    try:
        stdout, stderr, rc = _run_rclone([
            "lsjson", path,
            "--recursive",
            "--files-only",
        ], timeout=120)  # 2 min for recursive listing

        if rc != 0:
            return {"error": f"rclone failed: {stderr.strip()}"}

        items = json.loads(stdout) if stdout.strip() else []

        pattern_lower = pattern.lower()
        matches = []
        for item in items:
            name = item.get("Name", "")
            file_path = item.get("Path", "")
            if pattern_lower in name.lower() or pattern_lower in file_path.lower():
                matches.append({
                    "name": name,
                    "path": file_path,
                    "size": item.get("Size", 0),
                    "modified": item.get("ModTime", ""),
                    "mime_type": item.get("MimeType", ""),
                })

        matches.sort(key=lambda m: m["path"].lower())

        return {
            "pattern": pattern,
            "search_path": path,
            "total_files_scanned": len(items),
            "matches": len(matches),
            "files": matches[:50],  # Cap at 50 results
        }
    except subprocess.TimeoutExpired:
        return {"error": "rclone search timed out after 120s"}
    except Exception as e:
        return {"error": str(e)}
