"""Three-way planner: last sync state x local folder x tablet folder -> list of actions.

Pure: no I/O. File hashes and handwriting digests are fetched through callables, and only
for documents whose cheap signature (size/mtime, lastModified/size) changed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from rmsync.models import Entry, Kind, LocalFile, RemoteDoc, Where, norm_key

# --- actions -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Upload:
    """Upload a local file; with ``replaces`` the old tablet document goes to the trash."""

    path: str
    replaces: str | None = None


@dataclass(frozen=True)
class WriteBack:
    """Render the tablet document (PDF + handwriting) over the local file."""

    doc_id: str
    path: str


@dataclass(frozen=True)
class Download:
    """A document new on the tablet: write it locally and start tracking it."""

    doc_id: str
    path: str


@dataclass(frozen=True)
class Adopt:
    """Same path on both sides but no state (first run after losing the state): link them."""

    doc_id: str
    path: str


@dataclass(frozen=True)
class MoveLocal:
    """The document was moved or renamed on the tablet."""

    doc_id: str
    src: str
    dst: str


@dataclass(frozen=True)
class MoveRemote:
    """The file was moved or renamed locally."""

    doc_id: str
    dst: str


@dataclass(frozen=True)
class TrashRemote:
    """The file was deleted locally: move the tablet document to the trash."""

    doc_id: str
    path: str


@dataclass(frozen=True)
class TrashLocal:
    """The document was deleted on the tablet: move the local file to rmsync's trash."""

    doc_id: str
    path: str


@dataclass(frozen=True)
class KeepConflict:
    """Both sides changed: move the local edit aside, so the tablet version can take its place."""

    doc_id: str
    path: str
    aside: str


@dataclass(frozen=True)
class Forget:
    """Stop tracking a document (nothing is transferred or deleted)."""

    doc_id: str
    reason: str


@dataclass(frozen=True)
class Refresh:
    """Record the current signatures without transferring anything."""

    doc_id: str
    local: bool = False
    remote: bool = False
    digest: str | None = None


@dataclass(frozen=True)
class Note:
    """Something the user should know; no change."""

    message: str


Action = Upload | WriteBack | Download | Adopt | MoveLocal | MoveRemote | TrashRemote | TrashLocal | KeepConflict | Forget | Refresh | Note

DELETING = (TrashLocal, TrashRemote)

# --- planner -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """Everything the planner looks at."""

    local: Mapping[str, LocalFile]
    remote: Mapping[str, RemoteDoc]
    state: Mapping[str, Entry]
    file_sha256: Callable[[str], str]
    remote_digest: Callable[[str], str | None]
    today: str  # YYYY-MM-DD, for conflict file names


def conflict_name(path: str, today: str, taken: set[str]) -> str:
    """``a/b.pdf`` -> ``a/b (conflict 2026-11-27).pdf``, numbered if that exists."""
    p = PurePosixPath(path)
    n = 1
    while True:
        tag = f" (conflict {today})" if n == 1 else f" (conflict {today} {n})"
        candidate = str(p.with_name(p.stem + tag + p.suffix))
        if norm_key(candidate) not in taken:
            return candidate
        n += 1


class _Planner:
    def __init__(self, inp: Inputs) -> None:
        self.inp = inp
        self.actions: list[Action] = []
        tracked = {norm_key(e.path) for e in inp.state.values()}
        self.untracked_local = {norm_key(p): p for p in sorted(inp.local) if norm_key(p) not in tracked}
        self.local_by_key = {norm_key(p): f for p, f in inp.local.items()}
        self.taken = set(self.local_by_key)
        self.claimed_remote: set[str] = set()

    def local_changed(self, e: Entry, f: LocalFile) -> bool | None:
        """True/False, or None when only the signature changed (content identical)."""
        if (f.size, f.mtime_ns) == (e.local_size, e.local_mtime_ns):
            return False
        if f.size == e.local_size and self.inp.file_sha256(f.path) == e.local_sha256:
            return None
        return True

    def remote_changed(self, e: Entry, r: RemoteDoc) -> tuple[bool, str | None]:
        """(changed, digest). Opening a document changes its metadata, not its handwriting."""
        if (r.modified, r.size) == (e.remote_modified, e.remote_size):
            return False, e.ann_digest
        digest = self.inp.remote_digest(r.doc_id)
        return digest != e.ann_digest, digest

    def find_rename(self, e: Entry) -> str | None:
        for key, path in self.untracked_local.items():
            f = self.inp.local[path]
            if f.size == e.local_size and self.inp.file_sha256(path) == e.local_sha256:
                del self.untracked_local[key]
                return path
        return None

    def run(self) -> list[Action]:
        for e in sorted(self.inp.state.values(), key=lambda e: e.path):
            self.claimed_remote.add(e.doc_id)
            self.tracked(e)
        self.untracked()
        return self.actions

    def tracked(self, e: Entry) -> None:
        add = self.actions.append
        r = self.inp.remote.get(e.doc_id)
        f = self.local_by_key.get(norm_key(e.path))

        if r is not None and r.where == Where.ELSEWHERE:
            add(Forget(e.doc_id, f"{e.path}: moved out of the synced folder on the tablet; no longer synced (local file kept)"))
            return

        if r is None or r.where == Where.TRASH:
            if f is None:
                add(Forget(e.doc_id, f"{e.path}: deleted on both sides"))
            elif self.local_changed(e, f):
                add(Forget(e.doc_id, f"{e.path}: deleted on the tablet but edited locally; uploading it again"))
                self.untracked_local[norm_key(f.path)] = f.path
            else:
                add(TrashLocal(e.doc_id, f.path))
            return

        r_changed, digest = self.remote_changed(e, r)

        if f is None:
            moved = self.find_rename(e)
            if moved is not None:
                add(MoveRemote(e.doc_id, moved))
                if r_changed:
                    add(WriteBack(e.doc_id, moved))
                elif digest != e.ann_digest or (r.modified, r.size) != (e.remote_modified, e.remote_size):
                    add(Refresh(e.doc_id, remote=True, digest=digest))
            elif r_changed:
                add(Note(f"{e.path}: deleted locally but written on the tablet; restoring the tablet version"))
                add(WriteBack(e.doc_id, r.path))
            else:
                add(TrashRemote(e.doc_id, e.path))
            return

        path = f.path
        if norm_key(r.path) != norm_key(path):
            if norm_key(r.path) in self.taken:
                add(Note(f"{path}: moved on the tablet to {r.path}, but that local path exists; not moved"))
            else:
                add(MoveLocal(e.doc_id, path, r.path))
                self.taken.discard(norm_key(path))
                self.taken.add(norm_key(r.path))
                path = r.path

        l_changed = self.local_changed(e, f)
        if l_changed and r_changed:
            aside = conflict_name(path, self.inp.today, self.taken)
            self.taken.add(norm_key(aside))
            add(KeepConflict(e.doc_id, path, aside))
            add(WriteBack(e.doc_id, path))
            add(Upload(aside))
            return
        if r_changed:
            if e.kind == Kind.EPUB:
                add(Note(f"{path}: annotated on the tablet; EPUB annotations are not written back"))
                add(Refresh(e.doc_id, remote=True, digest=digest))
            else:
                add(WriteBack(e.doc_id, path))
            return
        if l_changed:
            if e.kind == Kind.NOTEBOOK:
                add(Note(f"{path}: edited locally, but it is an export of a tablet notebook; local edits are not uploaded"))
                add(Refresh(e.doc_id, local=True))
            else:
                add(Upload(path, replaces=e.doc_id))
            return
        if l_changed is None:
            add(Refresh(e.doc_id, local=True))
        if (r.modified, r.size) != (e.remote_modified, e.remote_size):
            add(Refresh(e.doc_id, remote=True, digest=digest))

    def untracked(self) -> None:
        add = self.actions.append
        remote_new: dict[str, RemoteDoc] = {}
        for r in sorted(self.inp.remote.values(), key=lambda r: r.path):
            if r.where != Where.SCOPE or r.doc_id in self.claimed_remote:
                continue
            key = norm_key(r.path)
            if key in remote_new:
                add(Note(f"{r.path}: two tablet documents map to this path; skipping {r.doc_id}"))
                continue
            remote_new[key] = r

        for key, path in sorted(self.untracked_local.items(), key=lambda kv: kv[1]):
            if key in remote_new:
                r = remote_new.pop(key)
                add(Adopt(r.doc_id, path))
            else:
                add(Upload(path))

        for key, r in remote_new.items():
            if key in self.taken:
                add(Note(f"{r.path}: new on the tablet, but that local path is tracked by another document; skipped"))
                continue
            add(Download(r.doc_id, r.path))


def plan(inp: Inputs) -> list[Action]:
    """Decide what to do. See the module docstring."""
    return _Planner(inp).run()
