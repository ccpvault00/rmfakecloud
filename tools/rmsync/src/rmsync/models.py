"""Value types shared by the snapshot builders, the planner and the engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath


class Kind(str, Enum):
    """What a synced document is on the tablet."""

    PDF = "pdf"
    EPUB = "epub"
    NOTEBOOK = "notebook"


class Where(str, Enum):
    """Where a tablet document lives relative to the synced folder."""

    SCOPE = "scope"
    TRASH = "trash"
    ELSEWHERE = "elsewhere"


SYNCED_SUFFIXES = {".pdf": Kind.PDF, ".epub": Kind.EPUB}


def norm_key(path: str) -> str:
    """Comparison key for a relative path: the tablet drops the suffix, so its case does not count."""
    p = PurePosixPath(path)
    return str(p.with_suffix(p.suffix.lower()))


@dataclass(frozen=True)
class LocalFile:
    """A file under the local root that takes part in the sync."""

    path: str  # POSIX path relative to the local root
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RemoteDoc:
    """A document in the server's tree."""

    doc_id: str
    name: str
    kind: Kind
    modified: str
    size: int
    where: Where
    path: str = ""  # relative path (with suffix) when where is SCOPE


@dataclass
class Entry:
    """What the last sync left behind for one document; the base for three-way decisions."""

    doc_id: str
    path: str
    kind: Kind
    origin: str  # "local" (uploaded by rmsync) or "tablet" (created there)
    local_size: int
    local_mtime_ns: int
    local_sha256: str
    remote_modified: str
    remote_size: int
    ann_digest: str | None = None  # digest of the handwriting last written back; None = none
