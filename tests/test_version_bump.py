import argparse
import subprocess

import pytest

from scripts import check_version_bump


@pytest.fixture
def version_repository(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Version Check Tests")
    git("config", "user.email", "version-check@example.invalid")
    (tmp_path / "version.py").write_text('__version__ = "26.8.89"\n')
    (tmp_path / "app.py").write_text("VALUE = 1\n")
    git("add", ".")
    git("commit", "-m", "Base version")
    base_sha = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", base_sha)

    monkeypatch.setattr(check_version_bump, "REPO_ROOT", tmp_path)
    return tmp_path, git, base_sha


@pytest.mark.parametrize(
    "base_ref",
    ["0" * 40, "f" * 40, "missing-branch", None],
)
@pytest.mark.parametrize("bump_version", [False, True])
def test_version_check_falls_back_from_missing_base_commit(
    version_repository, monkeypatch, capsys, base_ref, bump_version
):
    repository, git, _ = version_repository
    (repository / "app.py").write_text("VALUE = 2\n")
    if bump_version:
        (repository / "version.py").write_text('__version__ = "26.10.1"\n')
    git("add", ".")
    git("commit", "-m", "Change application")
    head_sha = git("rev-parse", "HEAD")
    monkeypatch.setattr(
        check_version_bump,
        "_parse_args",
        lambda: argparse.Namespace(base=base_ref, head=head_sha),
    )

    assert check_version_bump.main() == (0 if bump_version else 1)
    output = capsys.readouterr().out
    assert (
        "Version bump check passed."
        if bump_version
        else "Code changed but version.py was not updated."
    ) in output


def test_version_check_preserves_valid_base_commit(version_repository):
    _, _, base_sha = version_repository

    assert check_version_bump._resolved_base_ref(base_sha) == base_sha


@pytest.mark.parametrize("head_ref", ["0" * 40, "f" * 40, "missing-branch"])
def test_version_check_falls_back_from_missing_head_commit(
    version_repository, head_ref
):
    assert check_version_bump._resolved_head_ref(head_ref) == "HEAD"
