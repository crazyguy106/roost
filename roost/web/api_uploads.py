"""Web file-upload API — list, upload, download, delete files in UPLOADS_DIR.

Auth-protected by the global UnifiedAuthMiddleware (these paths are not in
OPEN_PATHS). All filename sanitisation and containment lives in
``roost.services.uploads``; this router is a thin HTTP adapter.
"""

import logging

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from roost.services import uploads

router = APIRouter(prefix="/api/files", tags=["files"])
_logger = logging.getLogger("roost.web.uploads")


@router.get("")
def list_files():
    return {"files": [f.as_dict() for f in uploads.list_files()]}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    try:
        stored = uploads.save_upload(file.filename or "", data)
    except uploads.UploadError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _logger.info("web upload saved: %s (%s)", stored.name, stored.size_human)
    return JSONResponse(content={"ok": True, "file": stored.as_dict()})


@router.get("/{name}/download")
def download_file(name: str):
    try:
        path = uploads.resolve_path(name)
    except uploads.UploadError:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path, filename=path.name, media_type="application/octet-stream"
    )


@router.delete("/{name}")
def delete_file(name: str):
    try:
        uploads.delete_file(name)
    except uploads.UploadError:
        raise HTTPException(status_code=404, detail="File not found")
    return {"ok": True}
