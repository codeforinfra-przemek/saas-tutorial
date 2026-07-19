"""Immutable raw-document snapshots used by Extractor evidence artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from ..schemas import SourceDocument


_MEDIA_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/xhtml+xml": ".xhtml",
    "text/html": ".html",
    "text/plain": ".txt",
}


def document_archive_directory_name(iteration: int, *, free: bool) -> str:
    stem = "documents" if iteration == 1 else f"documents-r{iteration:03d}"
    return f"{stem}-free" if free else stem


class RawDocumentArchive:
    """Store fetched bytes once and return a path relative to the result JSON."""

    def __init__(self, directory: Path | str, *, reference_root: Path | str) -> None:
        self.directory = Path(directory)
        self.reference_root = Path(reference_root).resolve()
        resolved_directory = self.directory.resolve()
        if not resolved_directory.is_relative_to(self.reference_root):
            raise ValueError("Raw-document archive must be inside its result directory.")

    def store(self, document: SourceDocument, content: bytes) -> str:
        if document.content_sha256 is None:
            raise ValueError("Raw document cannot be archived without content SHA-256.")
        digest = hashlib.sha256(content).hexdigest()
        if digest != document.content_sha256:
            raise ValueError("Raw document bytes do not match content SHA-256.")
        if document.content_bytes is not None and len(content) != document.content_bytes:
            raise ValueError("Raw document bytes do not match content_bytes.")

        extension = _MEDIA_EXTENSIONS.get(document.media_type or "", ".bin")
        filename = f"{document.document_id}-{digest[:16]}{extension}"
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / filename
        if target.exists():
            if target.read_bytes() != content:
                raise FileExistsError(
                    f"Raw-document snapshot already exists with other bytes: {target}"
                )
            return target.resolve().relative_to(self.reference_root).as_posix()

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{filename}.",
                suffix=".tmp",
                dir=self.directory,
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                if target.read_bytes() != content:
                    raise
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return target.resolve().relative_to(self.reference_root).as_posix()
