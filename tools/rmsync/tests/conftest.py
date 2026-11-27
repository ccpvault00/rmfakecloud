"""An in-memory stand-in for rmfakecloud's web-UI API, shaped like its /documents reply."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf
import pytest


def make_pdf(pages: int = 2, text: str = "x") -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), f"{text} page {i + 1}")
    return doc.tobytes()


@dataclass
class FakeDoc:
    id: str
    name: str
    parent: str
    type: str  # pdf, epub, notebook, or "" for a folder
    payload: bytes = b""
    ann: dict[str, Any] = field(default_factory=lambda: {"pages": []})
    modified: int = 1
    meta: int = 0  # grows with metadata-only changes (rename, open)

    @property
    def folder(self) -> bool:
        return self.type == ""

    @property
    def size(self) -> int:
        return len(self.payload) + len(str(self.ann)) + self.meta


class FakeApi:
    def __init__(self) -> None:
        self.docs: dict[str, FakeDoc] = {}
        self._ids = (f"id{n}" for n in itertools.count(1))
        self.calls: list[str] = []

    # tablet-side helpers
    def add(self, name: str, parent: str, type_: str, payload: bytes = b"", ann: dict[str, Any] | None = None) -> str:
        d = FakeDoc(next(self._ids), name, parent, type_, payload, ann or {"pages": []})
        self.docs[d.id] = d
        return d.id

    def find(self, name: str) -> FakeDoc:
        (d,) = [d for d in self.docs.values() if d.name == name and d.parent != "trash"]
        return d

    def write(self, doc_id: str, lines: list[dict[str, Any]], page: int = 0) -> None:
        d = self.docs[doc_id]
        pages = d.ann.get("pages") or [{"id": f"p{i}", "pdfPage": i} for i in range(pymupdf.open(stream=d.payload, filetype="pdf").page_count)]
        pages[page] = {**pages[page], "lines": pages[page].get("lines", []) + lines}
        d.ann = {"fileType": d.type, "hasPdf": True, "pages": pages}
        d.modified += 1

    # RemoteApi
    def tree(self) -> dict[str, Any]:
        self.calls.append("tree")

        def entry(d: FakeDoc) -> dict[str, Any]:
            if d.folder:
                kids = [entry(c) for c in self.docs.values() if c.parent == d.id]
                return {"id": d.id, "name": d.name, "children": kids, "lastModified": "t", "isFolder": True}
            return {"id": d.id, "name": d.name, "type": d.type, "lastModified": f"t{d.modified}", "size": d.size}

        return {
            "Entries": [entry(d) for d in self.docs.values() if d.parent == ""],
            "Trash": [entry(d) for d in self.docs.values() if d.parent == "trash"],
        }

    def create_folder(self, name: str, parent: str) -> str:
        self.calls.append(f"folder {name}")
        return self.add(name, parent, "")

    def upload(self, path: Path, parent: str) -> str:
        self.calls.append(f"upload {path.name}")
        return self.add(path.stem, parent, path.suffix.lstrip(".").lower(), path.read_bytes())

    def move(self, doc_id: str, name: str, parent: str) -> None:
        self.calls.append(f"move {name} {parent}")
        d = self.docs[doc_id]
        d.name, d.parent = name, parent
        d.meta += 1

    def annotations(self, doc_id: str) -> dict[str, Any]:
        return self.docs[doc_id].ann

    def pdf(self, doc_id: str) -> bytes | None:
        d = self.docs[doc_id]
        return d.payload if d.type == "pdf" else None

    def epub(self, doc_id: str) -> bytes:
        return self.docs[doc_id].payload


def stroke(x: float = 0.0, y: float = 300.0, color: int = 0) -> dict[str, Any]:
    pts: list[float] = []
    for i in range(10):
        pts += [x + i * 20, y, 0, 8, 0, 200]
    return {"t": 17, "c": color, "s": 2.0, "p": pts}


@pytest.fixture
def fake() -> FakeApi:
    return FakeApi()
