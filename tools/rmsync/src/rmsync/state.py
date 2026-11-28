"""The last-sync state: one JSON file, rewritten atomically after every action."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from rmsync.models import Entry, Kind

VERSION = 1


class StateError(RuntimeError):
    """The state file cannot be used."""


def load(path: Path, local_root: Path, remote_folder: str) -> dict[str, Entry]:
    """Read the state; an absent file is an empty state (first run).

    Raises:
        StateError: if the file belongs to another local root / tablet folder or has another version.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if data.get("version") != VERSION:
        raise StateError(f"{path}: unsupported state version {data.get('version')!r}")
    if data.get("local_root") != str(local_root) or data.get("remote_folder") != remote_folder:
        raise StateError(f"{path} was written for {data.get('local_root')} <-> {data.get('remote_folder')}; use another state_file for this pair")
    out: dict[str, Entry] = {}
    for doc_id, raw in data.get("entries", {}).items():
        raw["kind"] = Kind(raw["kind"])
        out[doc_id] = Entry(**raw)
    return out


def save(path: Path, entries: dict[str, Entry], local_root: Path, remote_folder: str) -> None:
    """Write the state atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": VERSION,
        "local_root": str(local_root),
        "remote_folder": remote_folder,
        "entries": {k: {**asdict(e), "kind": e.kind.value} for k, e in sorted(entries.items())},
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(path)
