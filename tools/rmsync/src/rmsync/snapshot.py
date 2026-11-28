"""Build the planner's views of the local folder and of the server's tree."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rmsync.models import SYNCED_SUFFIXES, Kind, LocalFile, RemoteDoc, Where

# --- local ---------------------------------------------------------------------------------


def _skipped(name: str) -> bool:
    # hidden files, Dropbox/Office temp files, our own partial writes
    return name.startswith((".", "~$", ".~")) or name.endswith(".rmsync-tmp")


def scan_local(root: Path) -> dict[str, LocalFile]:
    """Every PDF/EPUB under ``root`` (hidden files and folders skipped), keyed by POSIX relative path."""
    out: dict[str, LocalFile] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(_skipped(part) for part in rel.parts) or path.suffix.lower() not in SYNCED_SUFFIXES:
            continue
        if not path.is_file():
            continue
        st = path.stat()
        out[rel.as_posix()] = LocalFile(rel.as_posix(), st.st_size, st.st_mtime_ns)
    return out


# --- remote --------------------------------------------------------------------------------


@dataclass
class RemoteSnapshot:
    """The server's tree as the planner sees it."""

    docs: dict[str, RemoteDoc] = field(default_factory=dict)
    folders: dict[str, str] = field(default_factory=dict)  # relative dir ("" = synced folder) -> folder id
    root_id: str | None = None


def kind_of(doc_type: str) -> Kind:
    """A document's own ``.content`` fileType -> Kind (anything that is not a PDF or EPUB is a notebook)."""
    try:
        return Kind(doc_type)
    except ValueError:
        return Kind.NOTEBOOK


Resolver = Callable[[str], Kind]  # doc id -> Kind, for documents whose tree entry has no usable type


def _tree_kind(entry: dict[str, Any], resolve: Resolver | None) -> Kind:
    """Kind of a tree entry, trusting only the three real ``type`` values.

    rmfakecloud puts the document *name* in ``type`` for web-UI uploads (until it reloads the
    document), so anything else is
    resolved by ``resolve`` (or, outside the synced folder, where it does not matter, taken as a notebook).
    """
    raw = str(entry.get("type", ""))
    if raw in {k.value for k in Kind}:
        return Kind(raw)
    return resolve(str(entry["id"])) if resolve else Kind.NOTEBOOK


def _safe(name: str) -> str:
    # kept verbatim (a trailing space is legal and the local file has it); only "/" and empty names change
    return name.replace("/", "-") if name.strip() else "untitled"


def doc_path(reldir: str, name: str, kind: Kind) -> str:
    """Local path of a tablet document: notebooks come back as PDFs."""
    filename = _safe(name) + (".epub" if kind == Kind.EPUB else ".pdf")
    return f"{reldir}/{filename}" if reldir else filename


def _doc(entry: dict[str, Any], where: Where, kind: Kind, path: str = "") -> RemoteDoc:
    return RemoteDoc(
        doc_id=str(entry["id"]),
        name=str(entry.get("name", "")),
        kind=kind,
        modified=str(entry.get("lastModified", "")),
        size=int(entry.get("size", 0)),
        where=where,
        path=path,
    )


def _walk(entries: Iterable[dict[str, Any]], where: Where, snap: RemoteSnapshot, reldir: str | None, resolve: Resolver) -> None:
    for e in entries:
        if e.get("isFolder"):
            sub = None if reldir is None else (f"{reldir}/{_safe(e['name'])}" if reldir else _safe(e["name"]))
            if sub is not None and where == Where.SCOPE:
                snap.folders.setdefault(sub, str(e["id"]))
            _walk(e.get("children") or [], where, snap, sub, resolve)
        else:
            if where == Where.SCOPE and reldir is not None:
                kind = _tree_kind(e, resolve)
                snap.docs[str(e["id"])] = _doc(e, where, kind, doc_path(reldir, str(e.get("name", "")), kind))
            else:
                snap.docs[str(e["id"])] = _doc(e, Where.ELSEWHERE if where == Where.SCOPE else where, _tree_kind(e, None))


def scan_remote(tree: dict[str, Any], folder: str, resolve: Resolver) -> RemoteSnapshot:
    """Split the tree into documents in the synced folder, in the trash and elsewhere.

    ``resolve`` gives the kind of a synced-folder document whose tree ``type`` is not usable.

    Raises:
        ValueError: if more than one top-level folder has the synced folder's name.
    """
    snap = RemoteSnapshot()
    entries = tree.get("Entries") or []
    roots = [e for e in entries if e.get("isFolder") and e.get("name") == folder]
    if len(roots) > 1:
        raise ValueError(f"{len(roots)} top-level folders are named {folder!r} on the tablet; rename all but one")
    for e in entries:
        if roots and e is roots[0]:
            snap.root_id = str(e["id"])
            snap.folders[""] = snap.root_id
            _walk(e.get("children") or [], Where.SCOPE, snap, "", resolve)
        else:
            _walk([e], Where.SCOPE, snap, None, resolve)
    _walk(tree.get("Trash") or [], Where.TRASH, snap, None, resolve)
    return snap
