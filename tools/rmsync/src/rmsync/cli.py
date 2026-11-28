"""``rmsync``: two-way sync of a local folder with a folder on the reMarkable via rmfakecloud."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from rmsync import __version__, api, config
from rmsync.engine import Engine, Options, SyncAborted

log = logging.getLogger("rmsync")

TOKEN_FILE = Path("~/.cache/rmsync/token").expanduser()
TOKEN_MAX_AGE = 20 * 3600  # the server's tokens last 24 h
CONFIG_DIR = config.DEFAULT_PATH.parent  # plain `rmsync` syncs every profile (*.yaml) here


def _token(cfg: config.Config) -> str:
    """Cached web-UI token, refreshed by logging in when older than 20 h."""
    if TOKEN_FILE.exists() and time.time() - TOKEN_FILE.stat().st_mtime < TOKEN_MAX_AGE:
        return TOKEN_FILE.read_text().strip()
    token = api.login(cfg.url, cfg.user, cfg.password())
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
    return token


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rmsync",
        description=(
            "Two-way sync between a local folder and a top-level folder on the reMarkable (through rmfakecloud). "
            "New/changed local PDFs and EPUBs are uploaded; handwriting from the tablet is drawn into the local PDF (replacing it); "
            "notebooks come back as PDFs; moves and deletions are mirrored (local deletions go to trash_dir, tablet ones to the tablet trash)."
        ),
    )
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--allow-deletes", action="store_true", help="allow more deletions than max_deletes in this run")
    ap.add_argument("--config", type=Path, help=f"sync only this config file (default: every *.yaml in {CONFIG_DIR}, config.yaml first)")
    ap.add_argument("--init", action="store_true", help=f"write an example config file (default {CONFIG_DIR / config.DEFAULT_PATH.name}) and exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    ap.add_argument("--version", action="version", version=f"rmsync {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit status."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s", stream=sys.stderr)

    if args.init:
        target: Path = args.config or CONFIG_DIR / config.DEFAULT_PATH.name
        if target.exists():
            log.error("%s already exists; not overwriting", target)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(config.EXAMPLE)
        log.info("wrote %s; edit it, then put the web-UI password in the password_file (chmod 600)", target)
        return 0

    paths = [args.config] if args.config else config.profiles(CONFIG_DIR) or [CONFIG_DIR / config.DEFAULT_PATH.name]
    try:
        loaded = [(p, config.load(p)) for p in paths]
        config.check_distinct(loaded)
    except (config.ConfigError, ValueError, OSError) as err:  # nothing has run yet
        log.error("error: %s", err)
        return 1

    status = 0
    for path, cfg in loaded:
        if len(loaded) > 1:
            log.info("== %s: %s <-> %s", path.name, cfg.local_root, cfg.remote_folder)
        status = max(status, _sync(cfg, args))
    return status


def _sync(cfg: config.Config, args: argparse.Namespace) -> int:
    """One profile; 0 ok, 1 not run (error/abort), 2 some actions failed."""
    try:
        client = api.Client(cfg.url, _token(cfg))
        engine = Engine(client, cfg.local_root, cfg.remote_folder, cfg.state_file, cfg.trash_dir, cfg.max_deletes)
        report = engine.run(Options(dry_run=args.dry_run, allow_deletes=args.allow_deletes))
    except api.ApiError as err:
        if "HTTP 401" in str(err):
            TOKEN_FILE.unlink(missing_ok=True)
        log.error("error: %s", err)
        return 1
    except (config.ConfigError, SyncAborted, ValueError, OSError) as err:
        log.error("error: %s", err)
        return 1

    changes = [a for a in report.actions if type(a).__name__ not in ("Refresh", "Note")]
    if args.dry_run:
        log.info("dry run: %d change(s) planned, nothing done", len(changes))
    elif not changes and not report.failed:
        log.info("up to date")
    else:
        summary = ", ".join(f"{n} {k}" for k, n in sorted(report.done.items()) if k not in ("Refresh", "Note"))
        log.info("done: %s%s", summary or "nothing", f"; {len(report.failed)} failed" if report.failed else "")
    return 2 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
