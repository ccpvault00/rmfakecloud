from __future__ import annotations

from pathlib import Path

import pytest

from rmsync import config, snapshot, state
from rmsync.models import Entry, Kind, Where


def test_scan_local_filters(tmp_path: Path):
    (tmp_path / "a" / ".git").mkdir(parents=True)
    for rel in ["a/x.pdf", "a/Y.PDF", "a/b.epub", "a/.git/z.pdf", "a/~$tmp.pdf", "a/n.bib", "a/x.pdf.rmsync-tmp"]:
        (tmp_path / rel).write_bytes(b"1")
    assert set(snapshot.scan_local(tmp_path)) == {"a/x.pdf", "a/Y.PDF", "a/b.epub"}


def _no_resolve(doc_id: str) -> Kind:
    raise AssertionError(f"resolver called for {doc_id}")


def test_scan_remote_resolves_untrusted_types():
    """rmfakecloud reports the name as `type` for web-UI uploads; such entries are resolved, others are not."""
    tree = {
        "Entries": [
            {
                "id": "f1",
                "name": "ccp_library",
                "isFolder": True,
                "children": [
                    {"id": "d1", "name": "Novel", "type": "Novel", "lastModified": "t", "size": 1},
                    {"id": "d2", "name": "Paper", "type": "pdf", "lastModified": "t", "size": 1},
                    {"id": "d4", "name": "Louis - I nternational ", "type": "pdf", "lastModified": "t", "size": 1},
                    {"id": "d5", "name": "  ", "type": "pdf", "lastModified": "t", "size": 1},
                ],
            },
            {"id": "d3", "name": "outside", "type": "outside", "lastModified": "t", "size": 1},
        ],
        "Trash": [],
    }
    asked: list[str] = []

    def resolve(doc_id: str) -> Kind:
        asked.append(doc_id)
        return Kind.EPUB

    snap = snapshot.scan_remote(tree, "ccp_library", resolve)
    assert snap.docs["d1"].kind == Kind.EPUB and snap.docs["d1"].path == "Novel.epub"
    assert snap.docs["d2"].path == "Paper.pdf"
    assert snap.docs["d4"].path == "Louis - I nternational .pdf", "names are not trimmed (the local file keeps the space)"
    assert snap.docs["d5"].path == "untitled.pdf"
    assert asked == ["d1"], "only synced-folder documents with an unusable type are resolved"


def test_scan_remote_scopes():
    tree = {
        "Entries": [
            {
                "id": "f1",
                "name": "ccp_library",
                "isFolder": True,
                "children": [
                    {
                        "id": "f2",
                        "name": "papers",
                        "isFolder": True,
                        "children": [
                            {"id": "d1", "name": "A/B", "type": "pdf", "lastModified": "t", "size": 3},
                            {"id": "d2", "name": "notes", "type": "notebook", "lastModified": "t", "size": 1},
                        ],
                    },
                    {"id": "d3", "name": "book", "type": "epub", "lastModified": "t", "size": 1},
                ],
            },
            {"id": "d4", "name": "outside", "type": "pdf", "lastModified": "t", "size": 1},
        ],
        "Trash": [{"id": "d5", "name": "gone", "type": "pdf", "lastModified": "t", "size": 1}],
    }
    snap = snapshot.scan_remote(tree, "ccp_library", _no_resolve)
    assert snap.root_id == "f1"
    assert snap.folders == {"": "f1", "papers": "f2"}
    assert snap.docs["d1"].path == "papers/A-B.pdf"
    assert snap.docs["d2"].path == "papers/notes.pdf" and snap.docs["d2"].kind == Kind.NOTEBOOK
    assert snap.docs["d3"].path == "book.epub"
    assert snap.docs["d4"].where == Where.ELSEWHERE
    assert snap.docs["d5"].where == Where.TRASH


def test_scan_remote_refuses_two_roots():
    tree = {"Entries": [{"id": a, "name": "ccp_library", "isFolder": True, "children": []} for a in "ab"]}
    with pytest.raises(ValueError, match="2 top-level"):
        snapshot.scan_remote(tree, "ccp_library", _no_resolve)


def test_state_roundtrip_and_pair_check(tmp_path: Path):
    f = tmp_path / "s.json"
    e = Entry("d", "a.pdf", Kind.PDF, "local", 1, 2, "h", "t", 3, None)
    state.save(f, {"d": e}, tmp_path, "lib")
    assert state.load(f, tmp_path, "lib") == {"d": e}
    with pytest.raises(state.StateError):
        state.load(f, tmp_path, "other")


def test_config_load_and_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text(config.EXAMPLE.replace("~/.config/rmsync/password", str(tmp_path / "pw")))
    cfg = config.load(cfg_file)
    assert cfg.remote_folder == "ccp_library" and cfg.max_deletes == 20
    assert cfg.local_root == Path("~/Dropbox/lonely_general/raw").expanduser()
    monkeypatch.delenv("RMSYNC_PASSWORD", raising=False)
    with pytest.raises(config.ConfigError):
        cfg.password()
    (tmp_path / "pw").write_text("secret\n")
    assert cfg.password() == "secret"
    monkeypatch.setenv("RMSYNC_PASSWORD", "env")
    assert cfg.password() == "env"
