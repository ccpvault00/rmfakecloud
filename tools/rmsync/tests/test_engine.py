"""End-to-end runs of the engine against the in-memory server, several runs per scenario."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from conftest import FakeApi, make_pdf, stroke

from rmsync import state
from rmsync.engine import Engine, Options, SyncAborted


@pytest.fixture
def env(tmp_path: Path, fake: FakeApi):
    root = tmp_path / "raw"
    (root / "papers" / "ml").mkdir(parents=True)
    (root / "books").mkdir()
    (root / "papers" / "ml" / "attention.pdf").write_bytes(make_pdf(3, "attention"))
    (root / "books" / "stats.pdf").write_bytes(make_pdf(2, "stats"))
    (root / "books" / "novel.epub").write_bytes(b"PK-epub")
    (root / "references.bib").write_text("@x{}")
    (root / ".hidden.pdf").write_bytes(b"%PDF")

    def engine(max_deletes: int = 20) -> Engine:
        return Engine(fake, root, "ccp_library", tmp_path / "state.json", tmp_path / "trash", max_deletes)

    def run(**kw: bool):
        return engine().run(Options(**kw), today="2026-11-27")

    return root, fake, engine, run, tmp_path


def names(fake: FakeApi) -> set[str]:
    def path(d) -> str:
        parts = [d.name]
        while d.parent not in ("", "trash"):
            d = fake.docs[d.parent]
            parts.append(d.name)
        return "/".join(reversed(parts))

    return {path(d) for d in fake.docs.values() if not d.folder and d.parent != "trash" and _in_trash(fake, d) is False}


def _in_trash(fake: FakeApi, d) -> bool:
    while d.parent not in ("", "trash"):
        d = fake.docs[d.parent]
    return d.parent == "trash"


def test_first_run_uploads_tree_and_second_run_is_idle(env):
    root, fake, _, run, _ = env
    rep = run()
    assert names(fake) == {"ccp_library/papers/ml/attention", "ccp_library/books/stats", "ccp_library/books/novel"}
    assert not rep.failed
    calls = len(fake.calls)
    rep2 = run()
    assert [type(a).__name__ for a in rep2.actions if type(a).__name__ != "Refresh"] == []
    assert fake.calls[calls:] == ["tree"]


def test_dry_run_changes_nothing(env):
    root, fake, _, run, tmp = env
    rep = run(dry_run=True)
    assert len(rep.actions) == 3
    assert fake.docs == {}
    assert not (tmp / "state.json").exists()


def test_handwriting_replaces_local_pdf_once(env):
    root, fake, _, run, _ = env
    run()
    doc = fake.find("stats")
    fake.write(doc.id, [stroke()], page=1)
    run()
    out = pymupdf.open(root / "books" / "stats.pdf")
    assert out.page_count == 2
    assert out[1].get_drawings(), "stroke drawn on page 2"
    assert not out[0].get_drawings()
    before = (root / "books" / "stats.pdf").stat().st_mtime_ns
    uploads = [c for c in fake.calls if c.startswith("upload")]
    rep = run()  # the rewritten local file must not bounce back as a local change
    assert [c for c in fake.calls if c.startswith("upload")] == uploads
    assert (root / "books" / "stats.pdf").stat().st_mtime_ns == before
    assert not [a for a in rep.actions if type(a).__name__ not in ("Refresh",)]


def test_metadata_only_change_on_tablet_does_not_rewrite(env):
    root, fake, _, run, _ = env
    run()
    d = fake.find("stats")
    d.meta += 5
    d.modified += 1  # opened on the tablet
    before = (root / "books" / "stats.pdf").stat().st_mtime_ns
    rep = run()
    assert (root / "books" / "stats.pdf").stat().st_mtime_ns == before
    assert {type(a).__name__ for a in rep.actions} <= {"Refresh"}


def test_local_edit_reuploads_and_trashes_old(env):
    root, fake, _, run, _ = env
    run()
    old = fake.find("stats").id
    (root / "books" / "stats.pdf").write_bytes(make_pdf(4, "stats v2"))
    run()
    assert fake.docs[old].parent == "trash"
    assert len(pymupdf.open(stream=fake.find("stats").payload, filetype="pdf")) == 4


def test_conflict_keeps_both(env):
    root, fake, _, run, _ = env
    run()
    fake.write(fake.find("stats").id, [stroke()])
    (root / "books" / "stats.pdf").write_bytes(make_pdf(5, "local edit"))
    run()
    aside = root / "books" / "stats (conflict 2026-11-27).pdf"
    assert pymupdf.open(aside).page_count == 5
    assert pymupdf.open(root / "books" / "stats.pdf")[0].get_drawings()
    assert "ccp_library/books/stats (conflict 2026-11-27)" in names(fake)


def test_deletions_mirror_both_ways(env):
    root, fake, _, run, tmp = env
    run()
    (root / "books" / "stats.pdf").unlink()
    fake.move(fake.find("attention").id, "attention", "trash")
    run()
    assert fake.find("novel")  # untouched
    assert all(d.name != "stats" or d.parent == "trash" for d in fake.docs.values())
    assert not (root / "papers" / "ml" / "attention.pdf").exists()
    assert list((tmp / "trash").rglob("attention.pdf"))
    assert state.load(tmp / "state.json", root, "ccp_library").keys() == {fake.find("novel").id}


def test_deletion_guard(env):
    root, fake, engine, run, _ = env
    run()
    for p in root.rglob("*.pdf"):
        p.unlink()
    with pytest.raises(SyncAborted, match="deletions planned"):
        engine(max_deletes=1).run(Options())
    assert all(d.parent != "trash" for d in fake.docs.values())
    engine(max_deletes=1).run(Options(allow_deletes=True))
    assert names(fake) == {"ccp_library/books/novel"}


def test_empty_local_root_aborts(env):
    root, fake, engine, run, _ = env
    run()
    for p in list(root.rglob("*.pdf")) + list(root.rglob("*.epub")):
        p.unlink()
    with pytest.raises(SyncAborted, match="no PDF/EPUB"):
        engine().run(Options(allow_deletes=True))


def test_moves_mirror_both_ways(env):
    root, fake, _, run, _ = env
    run()
    # local rename + move into a new folder
    (root / "archive").mkdir()
    (root / "books" / "stats.pdf").rename(root / "archive" / "Statistics.pdf")
    # tablet rename
    fake.move(fake.find("attention").id, "Attention Is All You Need", fake.find("attention").parent)
    rep = run()
    assert not rep.failed
    assert "ccp_library/archive/Statistics" in names(fake)
    assert (root / "papers" / "ml" / "Attention Is All You Need.pdf").exists()
    assert not [c for c in fake.calls if c.startswith("upload") and "Statistics" in c]
    rep = run()
    assert {type(a).__name__ for a in rep.actions} <= {"Refresh"}


def test_new_tablet_notebook_comes_back_as_pdf(env):
    root, fake, _, run, _ = env
    run()
    papers = fake.find("papers").id
    fake.add("Lab notes", papers, "notebook", ann={"fileType": "notebook", "pages": [{"id": "a", "pdfPage": -1, "lines": [stroke()]}, {"id": "b", "pdfPage": -1}]})
    run()
    out = pymupdf.open(root / "papers" / "Lab notes.pdf")
    assert out.page_count == 2
    assert out[0].get_drawings()
    # editing the exported PDF locally is not pushed into the notebook
    (root / "papers" / "Lab notes.pdf").write_bytes(make_pdf(1))
    rep = run()
    assert any("notebook" in getattr(a, "message", "") for a in rep.actions)
    assert not [c for c in fake.calls if "Lab notes" in c]


def test_lost_state_links_instead_of_uploading_twice(env):
    root, fake, _, run, tmp = env
    run()
    (tmp / "state.json").unlink()
    n = len([c for c in fake.calls if c.startswith("upload")])
    run()
    assert len([c for c in fake.calls if c.startswith("upload")]) == n


def test_tablet_epub_annotation_is_noted_not_written(env):
    root, fake, _, run, _ = env
    run()
    d = fake.find("novel")
    d.ann = {"pages": [{"id": "a", "pdfPage": 0, "lines": [stroke()]}]}
    d.modified += 1
    rep = run()
    assert (root / "books" / "novel.epub").read_bytes() == b"PK-epub"
    assert any("EPUB" in getattr(a, "message", "") for a in rep.actions)
    rep = run()
    assert {type(a).__name__ for a in rep.actions} <= {"Refresh"}


# --- the server reports the document name as `type` for web-UI uploads (seen live 2026-11-28) ---


def test_name_as_type_keeps_epub_names_and_is_idle(env):
    root, fake, _, run, _ = env
    fake.type_is_name = True
    run()
    rep = run()
    assert {type(a).__name__ for a in rep.actions} <= {"Refresh"}, rep.actions
    assert (root / "books" / "novel.epub").read_bytes() == b"PK-epub"
    assert not (root / "books" / "novel.pdf").exists()
    assert not [c for c in fake.calls if c.startswith("annotations")], "tracked docs use the recorded kind"


def test_name_as_type_writes_handwriting_over_the_pdf_not_blank_pages(env):
    root, fake, _, run, _ = env
    fake.type_is_name = True
    run()
    fake.write(fake.find("stats").id, [stroke()], page=1)
    run()
    out = pymupdf.open(root / "books" / "stats.pdf")
    assert out.page_count == 2
    assert "stats page 1" in out[0].get_text(), "original PDF content kept"
    assert out[1].get_drawings()


def test_name_as_type_new_tablet_documents_ask_the_server(env):
    root, fake, _, run, _ = env
    fake.type_is_name = True
    run()
    papers = fake.find("papers").id
    fake.add("Tablet book", papers, "epub", b"PK-tablet")
    fake.add("Tablet paper", papers, "pdf", make_pdf(1, "tp"))
    run()
    assert (root / "papers" / "Tablet book.epub").read_bytes() == b"PK-tablet"
    assert "tp page 1" in pymupdf.open(root / "papers" / "Tablet paper.pdf")[0].get_text()
