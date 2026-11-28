"""YAML configuration (``~/.config/rmsync/config.yaml``)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path("~/.config/rmsync/config.yaml").expanduser()

EXAMPLE = """\
# rmsync configuration. Paths may start with ~.
url: http://127.0.0.1:3010          # rmfakecloud, as reached from this machine
user: Jero8609                      # web-UI login
password_file: ~/.config/rmsync/password   # one line; chmod 600. Or set RMSYNC_PASSWORD.
local_root: ~/Dropbox/lonely_general/raw    # synced local folder
remote_folder: ccp_library          # top-level folder on the tablet (created if missing)
state_file: ~/.local/share/rmsync/state.json
trash_dir: ~/.local/share/rmsync/trash      # files deleted on the tablet are moved here
max_deletes: 20                     # more deletions than this in one run need --allow-deletes
"""


class ConfigError(RuntimeError):
    """The configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Validated settings; see EXAMPLE for the meaning of each field."""

    url: str
    user: str
    password_file: Path | None
    local_root: Path
    remote_folder: str
    state_file: Path
    trash_dir: Path
    max_deletes: int = 20

    def password(self) -> str:
        """``RMSYNC_PASSWORD`` or the first line of ``password_file``."""
        env = os.environ.get("RMSYNC_PASSWORD")
        if env:
            return env
        if self.password_file is None or not self.password_file.exists():
            raise ConfigError(f"no password: set RMSYNC_PASSWORD or create {self.password_file}")
        return self.password_file.read_text().splitlines()[0].strip()


def _path(raw: dict[str, Any], key: str) -> Path:
    if key not in raw:
        raise ConfigError(f"config: {key} is required")
    return Path(str(raw[key])).expanduser()


def profiles(directory: Path) -> list[Path]:
    """Every ``*.yaml`` in ``directory``: ``config.yaml`` first, then by name."""
    if not directory.is_dir():
        return []
    found = sorted(directory.glob("*.yaml"))
    return sorted(found, key=lambda p: p.name != DEFAULT_PATH.name)


def check_distinct(loaded: list[tuple[Path, Config]]) -> None:
    """Refuse profiles that would sync the same thing twice (they would fight over it)."""
    for i, (pa, a) in enumerate(loaded):
        for pb, b in loaded[i + 1 :]:
            pair = f"{pa.name} and {pb.name}"
            if a.remote_folder.casefold() == b.remote_folder.casefold():
                raise ConfigError(f"{pair} use the same remote_folder {a.remote_folder!r}")
            for key in ("state_file", "trash_dir"):
                if getattr(a, key).resolve() == getattr(b, key).resolve():
                    raise ConfigError(f"{pair} use the same {key} {getattr(a, key)}")
            ra, rb = a.local_root.resolve(), b.local_root.resolve()
            if ra == rb or ra.is_relative_to(rb) or rb.is_relative_to(ra):
                raise ConfigError(f"{pair}: one local_root is inside the other ({ra}, {rb})")


def load(path: Path = DEFAULT_PATH) -> Config:
    """Read and validate the config file."""
    if not path.exists():
        raise ConfigError(f"{path} does not exist; run `rmsync --init` to write an example")
    raw = yaml.safe_load(path.read_text()) or {}
    for key in ("url", "user", "remote_folder"):
        if not raw.get(key):
            raise ConfigError(f"config: {key} is required")
    folder = str(raw["remote_folder"])
    if "/" in folder:
        raise ConfigError("config: remote_folder must be a single top-level folder name")
    return Config(
        url=str(raw["url"]),
        user=str(raw["user"]),
        password_file=Path(str(raw["password_file"])).expanduser() if raw.get("password_file") else None,
        local_root=_path(raw, "local_root"),
        remote_folder=folder,
        state_file=_path(raw, "state_file"),
        trash_dir=_path(raw, "trash_dir"),
        max_deletes=int(raw.get("max_deletes", 20)),
    )
