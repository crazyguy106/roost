"""Tests for the web file-upload service + API.

No mocks: the service writes to a real temp directory (UPLOADS_DIR is
monkeypatched on the module). The API test builds the real FastAPI app
with the auth middleware stripped, mirroring tests/test_bundle_pages.py.
"""

from __future__ import annotations

import pytest

from roost.services import uploads


@pytest.fixture
def tmp_uploads(tmp_path, monkeypatch):
    """Point the uploads service at a fresh temp dir for each test."""
    monkeypatch.setattr(uploads, "UPLOADS_DIR", str(tmp_path))
    return tmp_path


# ── Service: save / list / resolve / delete ──────────────────────────


def test_save_and_list(tmp_uploads):
    stored = uploads.save_upload("report.pdf", b"hello world")
    assert stored.name == "report.pdf"
    assert stored.size == 11
    assert (tmp_uploads / "report.pdf").read_bytes() == b"hello world"

    files = uploads.list_files()
    assert [f.name for f in files] == ["report.pdf"]


def test_list_newest_first(tmp_uploads):
    import os
    import time

    uploads.save_upload("old.txt", b"a")
    # Force a distinct, older mtime so ordering is deterministic.
    old = tmp_uploads / "old.txt"
    os.utime(old, (time.time() - 100, time.time() - 100))
    uploads.save_upload("new.txt", b"b")

    assert [f.name for f in uploads.list_files()] == ["new.txt", "old.txt"]


def test_resolve_and_delete(tmp_uploads):
    uploads.save_upload("doc.csv", b"x,y")
    path = uploads.resolve_path("doc.csv")
    assert path == (tmp_uploads / "doc.csv").resolve()

    uploads.delete_file("doc.csv")
    assert not (tmp_uploads / "doc.csv").exists()
    with pytest.raises(uploads.UploadError):
        uploads.resolve_path("doc.csv")


# ── Service: filename sanitisation ───────────────────────────────────


def test_traversal_collapses_to_basename(tmp_uploads):
    stored = uploads.save_upload("../../etc/passwd", b"data")
    # Only the basename survives, written inside the uploads root.
    assert stored.name == "passwd"
    assert (tmp_uploads / "passwd").exists()
    assert not (tmp_uploads.parent / "passwd").exists()


def test_leading_dots_stripped(tmp_uploads):
    assert uploads.safe_name(".env") == "env"
    assert uploads.safe_name("..hidden") == "hidden"


def test_disallowed_chars_replaced(tmp_uploads):
    assert uploads.safe_name("my file*name?.txt") == "my file_name_.txt"


def test_empty_name_rejected(tmp_uploads):
    with pytest.raises(uploads.UploadError):
        uploads.safe_name("")
    with pytest.raises(uploads.UploadError):
        uploads.save_upload("", b"data")


def test_size_cap(tmp_uploads, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(uploads.UploadError):
        uploads.save_upload("big.bin", b"12345")
    # Under the cap is fine.
    uploads.save_upload("ok.bin", b"123")


def test_resolve_missing_file(tmp_uploads):
    with pytest.raises(uploads.UploadError):
        uploads.resolve_path("nope.txt")


# ── API roundtrip ────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "UPLOADS_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from roost.web.app import create_app

    app = create_app()
    app.user_middleware = [
        m for m in app.user_middleware if "UnifiedAuth" not in m.cls.__name__
    ]
    app.middleware_stack = app.build_middleware_stack()
    return TestClient(app)


def test_api_upload_list_download_delete(client, tmp_path):
    # Upload
    res = client.post(
        "/api/files/upload",
        files={"file": ("notes.txt", b"agent reference", "text/plain")},
    )
    assert res.status_code == 200, res.text
    assert res.json()["file"]["name"] == "notes.txt"

    # List
    res = client.get("/api/files")
    names = [f["name"] for f in res.json()["files"]]
    assert "notes.txt" in names

    # Download
    res = client.get("/api/files/notes.txt/download")
    assert res.status_code == 200
    assert res.content == b"agent reference"

    # Delete
    res = client.delete("/api/files/notes.txt")
    assert res.status_code == 200
    assert not (tmp_path / "notes.txt").exists()


def test_api_empty_upload_rejected(client):
    res = client.post(
        "/api/files/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert res.status_code == 400


def test_api_download_missing_404(client):
    res = client.get("/api/files/ghost.txt/download")
    assert res.status_code == 404
