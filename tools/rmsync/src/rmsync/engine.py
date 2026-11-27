"""Run one sync: snapshot both sides, plan, check the deletion guard, apply, save state."""

from __future__ import annotations

import hashlib
import logging
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import cache
from pathlib import Path, PurePosixPath

from rmsync import plan as p
from rmsync import render, snapshot, state
from rmsync.api import TRASH, RemoteApi
from rmsync.models import SYNCED_SUFFIXES, Entry, Kind, RemoteDoc

log = logging.getLogger(__name__)


class SyncAborted(RuntimeError):
    """The run stopped before changing anything."""


@dataclass(frozen=True)
class Options:
    """Per-run switches."""

    dry_run: bool = False
    allow_deletes: bool = False


@dataclass
class Report:
    """What a run did."""

    actions: list[p.Action] = field(default_factory=list)
    done: Counter[str] = field(default_factory=Counter)
    failed: list[str] = field(default_factory=list)


def _parent_dir(rel: str) -> str:
    parent = str(PurePosixPath(rel).parent)
    return "" if parent == "." else parent


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in 1 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def describe(a: p.Action) -> str:
    """One log line per action."""
    match a:
        case p.Upload(path=path, replaces=None):
            return f"upload      {path}"
        case p.Upload(path=path):
            return f"re-upload   {path} (changed locally)"
        case p.WriteBack(path=path):
            return f"write back  {path} (handwriting from the tablet)"
        case p.Download(path=path):
            return f"download    {path} (new on the tablet)"
        case p.Adopt(path=path):
            return f"link        {path} (already on both sides)"
        case p.MoveLocal(src=src, dst=dst):
            return f"move local  {src} -> {dst}"
        case p.MoveRemote(dst=dst):
            return f"move tablet -> {dst}"
        case p.TrashRemote(path=path):
            return f"trash tablet {path} (deleted locally)"
        case p.TrashLocal(path=path):
            return f"trash local {path} (deleted on the tablet)"
        case p.KeepConflict(path=path, aside=aside):
            return f"conflict    {path}: local edit kept as {aside}"
        case p.Forget(reason=reason):
            return f"forget      {reason}"
        case p.Note(message=message):
            return f"note        {message}"
        case _:
            return ""


class Engine:
    """Applies a plan against a server and a local folder."""

    def __init__(self, api: RemoteApi, local_root: Path, remote_folder: str, state_file: Path, trash_dir: Path, max_deletes: int) -> None:
        """Bind the engine to one local folder / tablet folder pair."""
        self.api = api
        self.root = local_root
        self.folder = remote_folder
        self.state_file = state_file
        self.trash_dir = trash_dir
        self.max_deletes = max_deletes
        self.entries: dict[str, Entry] = {}
        self.snap = snapshot.RemoteSnapshot()
        self.touched: set[str] = set()
        self._stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # --- helpers ---------------------------------------------------------------------------

    def _abs(self, rel: str) -> Path:
        return self.root.joinpath(*PurePosixPath(rel).parts)

    def _save(self) -> None:
        state.save(self.state_file, self.entries, self.root, self.folder)

    def _ensure_folder(self, reldir: str) -> str:
        if reldir in self.snap.folders:
            return self.snap.folders[reldir]
        if reldir == "":
            fid = self.api.create_folder(self.folder, "")
            self.snap.root_id = fid
        else:
            parent_id = self._ensure_folder(_parent_dir(reldir))
            fid = self.api.create_folder(PurePosixPath(reldir).name, parent_id)
        self.snap.folders[reldir] = fid
        return fid

    def _local_sig(self, rel: str) -> tuple[int, int, str]:
        path = self._abs(rel)
        st = path.stat()
        return st.st_size, st.st_mtime_ns, sha256_file(path)

    def _remote(self, doc_id: str) -> RemoteDoc:
        return self.snap.docs[doc_id]

    def _write_atomic(self, rel: str, data: bytes) -> None:
        dst = self._abs(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".rmsync-tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)

    def _to_trash(self, rel: str) -> Path:
        dst = self.trash_dir / self._stamp / Path(*PurePosixPath(rel).parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self._abs(rel)), dst)
        return dst

    def _fetch(self, doc_id: str) -> tuple[bytes, str | None]:
        """The document as it should look locally, and its handwriting digest."""
        r = self._remote(doc_id)
        ann = self.api.annotations(doc_id)
        digest = render.annotation_digest(ann)
        if r.kind == Kind.EPUB:
            return self.api.epub(doc_id), digest
        pdf = self.api.pdf(doc_id) if r.kind == Kind.PDF else None
        if r.kind == Kind.PDF and pdf is None:
            raise RuntimeError(f"{r.path}: the server has no PDF for this document")
        if digest is None and pdf is not None:
            return pdf, None
        return render.render(pdf, ann), digest

    def _track(self, doc_id: str, rel: str, origin: str, digest: str | None) -> None:
        r = self._remote(doc_id)
        size, mtime, sha = self._local_sig(rel)
        kind = r.kind if r.kind == Kind.NOTEBOOK else SYNCED_SUFFIXES[PurePosixPath(rel).suffix.lower()]
        self.entries[doc_id] = Entry(doc_id, rel, kind, origin, size, mtime, sha, r.modified, r.size, digest)

    # --- actions ---------------------------------------------------------------------------

    def apply(self, a: p.Action) -> None:  # noqa: C901 - one branch per action type
        """Carry out one action and update the in-memory state."""
        match a:
            case p.Upload(path=path, replaces=old):
                parent = self._ensure_folder(_parent_dir(path))
                new_id = self.api.upload(self._abs(path), parent)
                size, mtime, sha = self._local_sig(path)
                kind = SYNCED_SUFFIXES[PurePosixPath(path).suffix.lower()]
                if old is not None:
                    old_entry = self.entries.pop(old)
                    self.api.move(old, self._remote(old).name, TRASH)
                    origin = old_entry.origin
                else:
                    origin = "local"
                self.entries[new_id] = Entry(new_id, path, kind, origin, size, mtime, sha, "", -1, None)
                self.touched.add(new_id)
            case p.WriteBack(doc_id=doc_id, path=path):
                data, digest = self._fetch(doc_id)
                self._write_atomic(path, data)
                self._track(doc_id, path, self.entries[doc_id].origin, digest)
            case p.Download(doc_id=doc_id, path=path):
                data, digest = self._fetch(doc_id)
                self._write_atomic(path, data)
                self._track(doc_id, path, "tablet", digest)
            case p.Adopt(doc_id=doc_id, path=path):
                self._track(doc_id, path, "local", None)
                self.entries[doc_id].ann_digest = render.annotation_digest(self.api.annotations(doc_id))
            case p.MoveLocal(doc_id=doc_id, src=src, dst=dst):
                self._abs(dst).parent.mkdir(parents=True, exist_ok=True)
                self._abs(src).replace(self._abs(dst))
                self.entries[doc_id] = replace(self.entries[doc_id], path=dst)
            case p.MoveRemote(doc_id=doc_id, dst=dst):
                parent = self._ensure_folder(_parent_dir(dst))
                self.api.move(doc_id, PurePosixPath(dst).stem, parent)
                size, mtime, _ = self._local_sig(dst)
                self.entries[doc_id] = replace(self.entries[doc_id], path=dst, local_size=size, local_mtime_ns=mtime)
                self.touched.add(doc_id)
            case p.TrashRemote(doc_id=doc_id):
                self.api.move(doc_id, self._remote(doc_id).name, TRASH)
                del self.entries[doc_id]
            case p.TrashLocal(doc_id=doc_id, path=path):
                where = self._to_trash(path)
                log.info("            moved to %s", where)
                del self.entries[doc_id]
            case p.KeepConflict(path=path, aside=aside):
                self._abs(path).replace(self._abs(aside))
            case p.Forget(doc_id=doc_id):
                self.entries.pop(doc_id, None)
            case p.Refresh(doc_id=doc_id, local=loc, remote=rem, digest=digest):
                e = self.entries[doc_id]
                if loc:
                    size, mtime, sha = self._local_sig(e.path)
                    e = replace(e, local_size=size, local_mtime_ns=mtime, local_sha256=sha)
                if rem:
                    r = self._remote(doc_id)
                    e = replace(e, remote_modified=r.modified, remote_size=r.size, ann_digest=digest)
                self.entries[doc_id] = e
            case p.Note():
                pass

    # --- run -------------------------------------------------------------------------------

    def run(self, opts: Options, today: str | None = None) -> Report:
        """One complete sync.

        Raises:
            SyncAborted: when a safety check fails; nothing has been changed then.
        """
        if not self.root.is_dir():
            raise SyncAborted(f"local folder {self.root} does not exist (Dropbox not mounted?)")
        self.entries = state.load(self.state_file, self.root, self.folder)
        local = snapshot.scan_local(self.root)
        if self.entries and not local:
            raise SyncAborted(f"{self.root} has no PDF/EPUB files but {len(self.entries)} were synced before; refusing to mirror that")
        self.snap = snapshot.scan_remote(self.api.tree(), self.folder)

        hasher: Callable[[str], str] = cache(lambda rel: sha256_file(self._abs(rel)))

        def digest(doc_id: str) -> str | None:
            return render.annotation_digest(self.api.annotations(doc_id))

        actions = p.plan(p.Inputs(local, self.snap.docs, self.entries, hasher, cache(digest), today or datetime.now().strftime("%Y-%m-%d")))
        report = Report(actions=actions)
        deletes = [a for a in actions if isinstance(a, p.DELETING)]
        for a in actions:
            if line := describe(a):
                log.info("%s%s", "[dry run] " if opts.dry_run else "", line)
        if len(deletes) > self.max_deletes and not opts.allow_deletes:
            raise SyncAborted(f"{len(deletes)} deletions planned (limit {self.max_deletes}); check the list above and rerun with --allow-deletes")
        if opts.dry_run:
            return report

        for a in actions:
            try:
                self.apply(a)
                report.done[type(a).__name__] += 1
            except Exception as err:  # noqa: BLE001 - one failed document must not stop the other 1400
                log.error("failed: %s: %s", describe(a) or type(a).__name__, err)
                report.failed.append(f"{describe(a)}: {err}")
            self._save()

        if self.touched:
            self.snap = snapshot.scan_remote(self.api.tree(), self.folder)
            for doc_id in self.touched & self.entries.keys():
                r = self.snap.docs.get(doc_id)
                if r is not None:
                    self.entries[doc_id] = replace(self.entries[doc_id], remote_modified=r.modified, remote_size=r.size)
            self._save()
        return report
