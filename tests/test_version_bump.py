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


def test_version_check_skips_repository_first_commit(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "--initial-branch=master"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Version Check Tests"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "version-check@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "version.py").write_text('__version__ = "26.10.1"\n')
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Initial"], cwd=tmp_path, check=True)
    monkeypatch.setattr(check_version_bump, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        check_version_bump,
        "_parse_args",
        lambda: argparse.Namespace(base="0" * 40, head="HEAD"),
    )

    assert check_version_bump.main() == 0
    assert "No base commit to compare against" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("path", "is_code"),
    [
        ("app.py", True),
        ("static/app.js", True),
        ("README.md", False),
        ("docs/images/voting.png", False),
        (".github/workflows/ci.yml", False),
    ],
)
def test_version_check_classifies_code_changes(path, is_code):
    assert check_version_bump._is_code_change(path) is is_code


def test_version_check_uses_parent_when_default_branch_is_head(
    version_repository, monkeypatch
):
    repository, git, base_sha = version_repository
    (repository / "app.py").write_text("VALUE = 2\n")
    git("add", ".")
    git("commit", "-m", "Change application")
    head_sha = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head_sha)

    assert git("rev-parse", check_version_bump._resolved_base_ref("0" * 40, head_sha)) == base_sha


@pytest.mark.parametrize("bump_version", [False, True])
def test_version_check_falls_back_to_master_default_branch(
    version_repository, monkeypatch, capsys, bump_version
):
    repository, git, base_sha = version_repository
    git("update-ref", "-d", "refs/remotes/origin/main")
    git("branch", "-m", "main", "master")
    git("update-ref", "refs/remotes/origin/master", base_sha)
    (repository / "app.py").write_text("VALUE = 2\n")
    if bump_version:
        (repository / "version.py").write_text('__version__ = "26.10.1"\n')
    git("add", ".")
    git("commit", "-m", "Change application")
    monkeypatch.setattr(
        check_version_bump,
        "_parse_args",
        lambda: argparse.Namespace(base="0" * 40, head=git("rev-parse", "HEAD")),
    )

    assert check_version_bump._resolved_base_ref("0" * 40) == "origin/master"
    assert check_version_bump.main() == (0 if bump_version else 1)
