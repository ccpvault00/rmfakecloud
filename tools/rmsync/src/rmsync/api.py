"""Client for rmfakecloud's web-UI API (``/ui/api``), standard library only."""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

TRASH = "trash"


class ApiError(RuntimeError):
    """The server refused or failed a request."""


class RemoteApi(Protocol):
    """What the engine needs from the server (a fake implements it in tests)."""

    def tree(self) -> dict[str, Any]:
        """The document tree (``{"Entries": [...], "Trash": [...]}``)."""
        ...

    def create_folder(self, name: str, parent: str) -> str:
        """Create a folder and return its id (parent "" = root)."""
        ...

    def upload(self, path: Path, parent: str) -> str:
        """Upload a PDF/EPUB into a folder and return the new document id."""
        ...

    def move(self, doc_id: str, name: str, parent: str) -> None:
        """Rename and/or reparent; parent ``trash`` trashes it."""
        ...

    def annotations(self, doc_id: str) -> dict[str, Any]:
        """Page order and handwriting."""
        ...

    def pdf(self, doc_id: str) -> bytes | None:
        """The unmodified PDF payload, None when the document has none (notebook)."""
        ...

    def epub(self, doc_id: str) -> bytes:
        """The EPUB payload."""
        ...


class _MultipartBody(io.RawIOBase):
    """Streams ``preamble + file + epilogue`` so a 500 MB book is never held in memory."""

    def __init__(self, preamble: bytes, fh: BinaryIO, epilogue: bytes) -> None:
        self._parts: list[BinaryIO] = [io.BytesIO(preamble), fh, io.BytesIO(epilogue)]

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        while self._parts:
            n = self._parts[0].readinto(b)  # type: ignore[attr-defined]
            if n:
                return int(n)
            self._parts.pop(0)
        return 0


class Client:
    """rmfakecloud web-UI client authenticated with a bearer token."""

    def __init__(self, base_url: str, token: str, timeout: float = 600) -> None:
        """Create a client.

        Args:
            base_url: Server URL, e.g. ``http://127.0.0.1:3010``.
            token: Token from :func:`login`.
            timeout: Socket timeout in seconds (uploads of large books take a while).
        """
        self.base = base_url.rstrip("/") + "/ui/api"
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> bytes:
        hdrs = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        data = body
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                out: bytes = resp.read()
                return out
        except HTTPError as err:
            detail = err.read()[:300].decode(errors="replace")
            raise ApiError(f"{method} {path}: HTTP {err.code} {detail}") from err

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        return json.loads(self._request(method, path, body) or b"null")

    def tree(self) -> dict[str, Any]:  # noqa: D102
        out: dict[str, Any] = self._json("GET", "/documents")
        return out

    def create_folder(self, name: str, parent: str) -> str:  # noqa: D102
        doc = self._json("POST", "/folders", {"name": name, "parentId": parent})
        return str(doc["ID"])

    def upload(self, path: Path, parent: str) -> str:  # noqa: D102
        boundary = uuid.uuid4().hex
        quoted = path.name.replace('"', "%22")
        pre = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="parent"\r\n\r\n{parent or "root"}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{quoted}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        post = f"\r\n--{boundary}--\r\n".encode()
        size = len(pre) + path.stat().st_size + len(post)
        with path.open("rb") as fh:
            raw = self._request(
                "POST",
                "/documents/upload",
                io.BufferedReader(_MultipartBody(pre, fh, post)),
                {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(size)},
            )
        docs = json.loads(raw)
        return str(docs[0]["ID"])

    def move(self, doc_id: str, name: str, parent: str) -> None:  # noqa: D102
        self._request("PUT", "/documents", {"documentId": doc_id, "name": name, "parentId": parent})

    def annotations(self, doc_id: str) -> dict[str, Any]:  # noqa: D102
        out: dict[str, Any] = self._json("GET", f"/documents/{quote(doc_id)}/annotations")
        return out

    def pdf(self, doc_id: str) -> bytes | None:  # noqa: D102
        try:
            return self._request("GET", f"/documents/{quote(doc_id)}/pdf")
        except ApiError as err:
            if "HTTP 404" in str(err):
                return None
            raise

    def epub(self, doc_id: str) -> bytes:  # noqa: D102
        archive = self._request("GET", f"/documents/{quote(doc_id)}?type=rmdoc")
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for name in zf.namelist():
                if name.endswith(".epub"):
                    return zf.read(name)
        raise ApiError(f"{doc_id}: no .epub in the exported archive")


def login(base_url: str, user: str, password: str) -> str:
    """Log in to the web UI and return the token (valid 24 h)."""
    req = Request(
        base_url.rstrip("/") + "/ui/api/login",
        data=json.dumps({"email": user, "password": password}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return str(resp.read().decode().strip())
    except HTTPError as err:
        raise ApiError(f"login as {user} failed: HTTP {err.code}") from err
