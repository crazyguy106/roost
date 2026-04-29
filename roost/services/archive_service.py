"""Archive (ZIP) extraction with AES password support.

Insurance-broker portals frequently deliver per-policy bundles as
AES-encrypted ZIPs that the stdlib `zipfile` module cannot decrypt.
`pyzipper` is a drop-in replacement that handles both legacy ZipCrypto
and AES (WinZip) encryption.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pyzipper

logger = logging.getLogger("roost.services.archive_service")

__all__ = ["extract_zip", "BadZipPassword"]


class BadZipPassword(Exception):
    """Raised when the supplied password fails to decrypt the archive."""


def extract_zip(
    path: str | Path,
    password: str | None = None,
    dest: str | Path | None = None,
) -> list[Path]:
    """Extract a ZIP archive, optionally with a password.

    Returns the list of extracted file paths. Destination defaults to a
    sibling directory of the archive named after its stem.

    Raises:
        FileNotFoundError: archive missing
        BadZipPassword: password is wrong (or required but None)
        pyzipper.BadZipFile: archive is corrupt
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(f"Archive not found: {src}")

    out_dir = Path(dest) if dest else src.parent / src.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    pwd_bytes = password.encode() if password else None

    try:
        with pyzipper.AESZipFile(src) as zf:
            if pwd_bytes:
                zf.setpassword(pwd_bytes)
            try:
                zf.extractall(path=out_dir)
            except RuntimeError as e:
                # pyzipper raises RuntimeError("Bad password") on AES mismatch
                if "password" in str(e).lower():
                    raise BadZipPassword(str(e)) from e
                raise
    except pyzipper.BadZipFile:
        raise
    except Exception as e:
        # ZipCrypto wrong-password path returns a generic decompress error
        msg = str(e).lower()
        if "password" in msg or "bad" in msg and "crc" in msg:
            raise BadZipPassword(str(e)) from e
        raise

    extracted = sorted(p for p in out_dir.rglob("*") if p.is_file())
    logger.info("Extracted %d files from %s → %s", len(extracted), src.name, out_dir)
    return extracted
