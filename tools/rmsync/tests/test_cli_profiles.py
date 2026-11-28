"""Plain `rmsync` runs every profile (*.yaml) in the config directory; --config runs one."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeApi, make_pdf

from rmsync import cli, config


def write_profile(cfg_dir: Path, name: str, root: Path, folder: str, tmp: Path) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / name
    path.write_text(f"url: http://x\nuser: u\nlocal_root: {root}\nremote_folder: {folder}\nstate_file: {tmp / (folder + '-state.json')}\ntrash_dir: {tmp / (folder + '-trash')}\n")
    return path


def one_pdf(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(make_pdf(1, name))
    return root


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, FakeApi]:
    fake = FakeApi()
    cfg_dir = tmp_path / "cfg"
    monkeypatch.setattr(cli, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cli, "_token", lambda cfg: "t")
    monkeypatch.setattr(cli.api, "Client", lambda url, token: fake)
    return cfg_dir, fake


def names(fake: FakeApi) -> set[str]:
    return {d.name for d in fake.docs.values()}


def test_profiles_default_first_then_sorted(tmp_path: Path):
    d = tmp_path / "cfg"
    d.mkdir()
    for n in ("zeta.yaml", "config.yaml", "alpha.yaml", "password", "notes.txt"):
        (d / n).write_text("")
    assert [p.name for p in config.profiles(d)] == ["config.yaml", "alpha.yaml", "zeta.yaml"]
    assert config.profiles(tmp_path / "missing") == []


@pytest.mark.parametrize(
    ("second", "message"),
    [
        (("other", "LIB"), "remote_folder"),  # tablet folder names compare case-insensitively
        (("raw/sub", "notes"), "inside"),  # nested local roots
    ],
)
def test_check_distinct_refuses_overlap(tmp_path: Path, second: tuple[str, str], message: str):
    d = tmp_path / "cfg"
    a = config.load(write_profile(d, "a.yaml", tmp_path / "raw", "lib", tmp_path))
    b = config.load(write_profile(d, "b.yaml", tmp_path / second[0], second[1], tmp_path))
    with pytest.raises(config.ConfigError, match=message):
        config.check_distinct([(Path("a.yaml"), a), (Path("b.yaml"), b)])


def test_check_distinct_refuses_shared_state(tmp_path: Path):
    d = tmp_path / "cfg"
    a = config.load(write_profile(d, "a.yaml", tmp_path / "x", "lib", tmp_path))
    b = config.load(write_profile(d, "b.yaml", tmp_path / "y", "notes", tmp_path))
    b = config.Config(**{**b.__dict__, "state_file": a.state_file})
    with pytest.raises(config.ConfigError, match="state_file"):
        config.check_distinct([(Path("a.yaml"), a), (Path("b.yaml"), b)])


def test_plain_rmsync_runs_every_profile(env, tmp_path: Path):
    cfg_dir, fake = env
    write_profile(cfg_dir, "config.yaml", one_pdf(tmp_path / "raw", "paper.pdf"), "lib", tmp_path)
    write_profile(cfg_dir, "qnote.yaml", one_pdf(tmp_path / "qnote", "note.pdf"), "qnote", tmp_path)
    assert cli.main([]) == 0
    assert names(fake) >= {"lib", "qnote", "paper", "note"}


def test_config_flag_runs_only_that_profile(env, tmp_path: Path):
    cfg_dir, fake = env
    write_profile(cfg_dir, "config.yaml", one_pdf(tmp_path / "raw", "paper.pdf"), "lib", tmp_path)
    q = write_profile(cfg_dir, "qnote.yaml", one_pdf(tmp_path / "qnote", "note.pdf"), "qnote", tmp_path)
    assert cli.main(["--config", str(q)]) == 0
    assert "note" in names(fake) and "paper" not in names(fake)


def test_overlapping_profiles_change_nothing(env, tmp_path: Path):
    cfg_dir, fake = env
    write_profile(cfg_dir, "config.yaml", one_pdf(tmp_path / "raw", "paper.pdf"), "lib", tmp_path)
    write_profile(cfg_dir, "dup.yaml", one_pdf(tmp_path / "other", "note.pdf"), "Lib", tmp_path)
    assert cli.main([]) == 1
    assert fake.docs == {}


def test_failed_profile_does_not_stop_the_others(env, tmp_path: Path):
    cfg_dir, fake = env
    write_profile(cfg_dir, "config.yaml", tmp_path / "missing-root", "lib", tmp_path)
    write_profile(cfg_dir, "qnote.yaml", one_pdf(tmp_path / "qnote", "note.pdf"), "qnote", tmp_path)
    assert cli.main([]) == 1
    assert "note" in names(fake)


def test_init_still_writes_the_default(env):
    cfg_dir, _ = env
    assert cli.main(["--init"]) == 0
    assert (cfg_dir / "config.yaml").read_text() == config.EXAMPLE
