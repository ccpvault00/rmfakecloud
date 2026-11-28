"""rmsync against a real rmfakecloud (the repo's dist/rmfakecloud-x64) on a throwaway data dir.

The tablet is simulated through the same server: a tablet-side annotation is an .rmdoc with a
real v6 page (internal/storage/rmv6/testdata/synthetic.rm) imported under the document's id.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import socket
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen

import pymupdf
import pytest
from conftest import make_pdf

from rmsync import api
from rmsync.engine import Engine, Options

REPO = Path(__file__).resolve().parents[3]
BINARY = REPO / "dist" / "rmfakecloud-x64"
V6_PAGE = REPO / "internal" / "storage" / "rmv6" / "testdata" / "synthetic.rm"

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not BINARY.exists(), reason="build dist/rmfakecloud-x64 first")]


@pytest.fixture
def server(tmp_path: Path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DATADIR": str(tmp_path / "data"), "PORT": str(port), "JWT_SECRET_KEY": secrets.token_hex(16), "LOGLEVEL": "warn"}
    env.pop("STORAGE_URL", None)
    log = (tmp_path / "server.log").open("w")
    proc = subprocess.Popen([str(BINARY)], env=env, stdout=log, stderr=subprocess.STDOUT, cwd=tmp_path)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urlopen(url, timeout=1).read()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("server did not start: " + (tmp_path / "server.log").read_text())
    token = api.login(url, "tester", "pw-" + secrets.token_hex(4))  # first login creates the admin
    yield api.Client(url, token)
    proc.terminate()
    proc.wait(10)


def tree_paths(client: api.Client) -> set[str]:
    out: set[str] = set()

    def walk(entries, prefix):
        for e in entries:
            name = f"{prefix}/{e['name']}" if prefix else e["name"]
            if e.get("isFolder"):
                walk(e.get("children") or [], name)
            else:
                out.add(name)

    walk(client.tree()["Entries"], "")
    return out


def trash_ids(client: api.Client) -> set[str]:
    return {e["id"] for e in client.tree()["Trash"]}


def find_id(client: api.Client, name: str) -> str:
    found: list[str] = []

    def walk(entries):
        for e in entries:
            if e.get("isFolder"):
                walk(e.get("children") or [])
            elif e["name"] == name:
                found.append(e["id"])

    walk(client.tree()["Entries"])
    (doc_id,) = found
    return doc_id


def annotate_on_tablet(client: api.Client, doc_id: str, tmp: Path) -> None:
    """Replace the document by a copy with one v6 page on PDF page 1, same id, newer lastModified."""
    archive = client._request("GET", f"/documents/{doc_id}?type=rmdoc")
    src = zipfile.ZipFile(io.BytesIO(archive))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith(".content"):
                content = json.loads(data or b"{}")
                content["formatVersion"] = 2
                content["cPages"] = {"pages": [{"id": "page-a", "idx": {"value": "ba"}, "redir": {"value": 0}}, {"id": "page-b", "idx": {"value": "bb"}, "redir": {"value": 1}}]}
                data = json.dumps(content).encode()
            if name.endswith(".metadata"):
                meta = json.loads(data)
                meta["lastModified"] = str(int(meta.get("lastModified", "0")) + 60000)
                parent = meta.get("parent", "")
                data = json.dumps(meta).encode()
            out.writestr(name, data)
        out.writestr(f"{doc_id}/page-a.rm", V6_PAGE.read_bytes())
    client._request("DELETE", f"/documents/{doc_id}")
    rmdoc = tmp / "annotated.rmdoc"
    rmdoc.write_bytes(buf.getvalue())
    client.upload(rmdoc, parent)


def test_two_way_against_real_server(server: api.Client, tmp_path: Path):
    root = tmp_path / "raw"
    (root / "papers" / "ml").mkdir(parents=True)
    (root / "books").mkdir()
    (root / "papers" / "ml" / "attention.pdf").write_bytes(make_pdf(2, "attention"))
    (root / "books" / "stats.pdf").write_bytes(make_pdf(2, "stats"))
    (root / "books" / "old.pdf").write_bytes(make_pdf(1, "old"))

    def run(**kw):
        rep = Engine(server, root, "ccp_library", tmp_path / "state.json", tmp_path / "trash", 20).run(Options(**kw), today="2026-11-27")
        assert not rep.failed, rep.failed
        return [type(a).__name__ for a in rep.actions if type(a).__name__ not in ("Refresh", "Note")]

    # 1. first run uploads into a new folder tree
    assert sorted(run()) == ["Upload"] * 3
    assert tree_paths(server) == {"ccp_library/papers/ml/attention", "ccp_library/books/stats", "ccp_library/books/old"}
    # 2. nothing changed -> nothing to do (signatures recorded from the real tree)
    assert run() == []

    # 3. handwriting on the tablet replaces the local PDF, once
    annotate_on_tablet(server, find_id(server, "stats"), tmp_path)
    assert run() == ["WriteBack"]
    out = pymupdf.open(root / "books" / "stats.pdf")
    assert out.page_count == 2 and "stats page 1" in out[0].get_text()
    drawings = out[0].get_drawings()
    assert any(d.get("color") and d["color"][2] > 0.7 for d in drawings), "blue ballpoint stroke"
    assert not out[1].get_drawings()
    assert run() == []

    # 4. local delete -> tablet trash; local rename -> tablet rename; tablet rename -> local rename
    old_id = find_id(server, "old")
    (root / "books" / "old.pdf").unlink()
    (root / "papers" / "ml" / "attention.pdf").rename(root / "papers" / "Attention.pdf")
    stats_id = find_id(server, "stats")
    server.move(stats_id, "Statistics", _folder_id(server, "books"))
    assert sorted(run()) == ["MoveLocal", "MoveRemote", "TrashRemote"]
    assert old_id in trash_ids(server)
    assert tree_paths(server) == {"ccp_library/papers/Attention", "ccp_library/books/Statistics"}
    assert (root / "books" / "Statistics.pdf").exists() and not (root / "books" / "stats.pdf").exists()
    assert run() == []

    # 5. a document new on the tablet comes back (real v6 handwriting rendered)
    new = tmp_path / "Tablet notes.pdf"
    new.write_bytes(make_pdf(2, "tablet"))
    new_id = server.upload(new, _folder_id(server, "papers"))
    annotate_on_tablet(server, new_id, tmp_path)
    assert run() == ["Download"]
    assert pymupdf.open(root / "papers" / "Tablet notes.pdf")[0].get_drawings()
    assert run() == []


def _folder_id(client: api.Client, name: str) -> str:
    found: list[str] = []

    def walk(entries):
        for e in entries:
            if e.get("isFolder"):
                if e["name"] == name:
                    found.append(e["id"])
                walk(e.get("children") or [])

    walk(client.tree()["Entries"])
    (fid,) = found
    return fid


def test_cli_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from rmsync import cli

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DATADIR": str(tmp_path / "data"), "PORT": str(port), "JWT_SECRET_KEY": secrets.token_hex(16)}
    proc = subprocess.Popen([str(BINARY)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=tmp_path)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urlopen(url, timeout=1).read()
                break
            except OSError:
                time.sleep(0.1)
        api.login(url, "cliuser", "s3cret")  # creates the user
        root = tmp_path / "raw"
        (root / "a").mkdir(parents=True)
        (root / "a" / "x.pdf").write_bytes(make_pdf(1))
        monkeypatch.setattr(cli, "TOKEN_FILE", tmp_path / "token")
        monkeypatch.delenv("RMSYNC_PASSWORD", raising=False)
        cfg = tmp_path / "config.yaml"
        assert cli.main(["--init", "--config", str(cfg)]) == 0
        text = cfg.read_text()
        for old, new in [
            ("http://127.0.0.1:3010", url),
            ("Jero8609", "cliuser"),
            ("~/.config/rmsync/password", str(tmp_path / "pw")),
            ("~/Dropbox/lonely_general/raw", str(root)),
            ("~/.local/share/rmsync/state.json", str(tmp_path / "state.json")),
            ("~/.local/share/rmsync/trash", str(tmp_path / "trash")),
        ]:
            assert old in text
            text = text.replace(old, new)
        cfg.write_text(text)
        assert cli.main(["--config", str(cfg)]) == 1  # no password file yet
        (tmp_path / "pw").write_text("s3cret\n")
        assert cli.main(["--config", str(cfg), "--dry-run"]) == 0
        assert not (tmp_path / "state.json").exists()
        assert cli.main(["--config", str(cfg)]) == 0
        assert (tmp_path / "token").stat().st_mode & 0o777 == 0o600
        assert tree_paths(api.Client(url, (tmp_path / "token").read_text())) == {"ccp_library/a/x"}
        assert cli.main(["--config", str(cfg)]) == 0  # cached token, nothing to do
    finally:
        proc.terminate()
        proc.wait(10)
