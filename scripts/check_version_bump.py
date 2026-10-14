from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = REPO_ROOT / "version.py"
VERSION_PATTERN = re.compile(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"')

NON_CODE_PATH_PREFIXES = (
    ".github/",
)
NON_CODE_SUFFIXES = (
    ".md",
    ".txt",
)


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


DEFAULT_BRANCH_CANDIDATES = (
    "origin/HEAD",
    "origin/master",
    "origin/main",
    "master",
    "main",
)


def _commit_sha(ref: str) -> str | None:
    try:
        return _run_git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        return None


def _commit_exists(ref: str) -> bool:
    return _commit_sha(ref) is not None


def _base_ref(head_ref: str = "HEAD") -> str | None:
    head_sha = _commit_sha(head_ref)
    for candidate in DEFAULT_BRANCH_CANDIDATES:
        candidate_sha = _commit_sha(candidate)
        if candidate_sha and candidate_sha != head_sha:
            return candidate
    parent = f"{head_ref}~1"
    if _commit_exists(parent):
        return parent
    return None


def _resolved_base_ref(base_ref: str | None, head_ref: str = "HEAD") -> str | None:
    if base_ref and _commit_exists(base_ref):
        return base_ref
    return _base_ref(head_ref)


def _resolved_head_ref(head_ref: str | None) -> str:
    if head_ref and _commit_exists(head_ref):
        return head_ref
    return "HEAD"


def _is_code_change(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith(NON_CODE_PATH_PREFIXES):
        return False
    if normalized.endswith(NON_CODE_SUFFIXES):
        return False
    return True


def _parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.search(value)
    if match is None:
        raise ValueError("Could not parse __version__ in version.py.")
    return tuple(int(part) for part in match.groups())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate version.py bump when code changes are present."
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Base revision (commit, branch, or tag) for comparison.",
    )
    parser.add_argument(
        "--head",
        default=None,
        help="Head revision (commit, branch, or tag) for comparison.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    head_ref = _resolved_head_ref(args.head)
    base_ref = _resolved_base_ref(args.base, head_ref)
    if base_ref is None:
        print("No base commit to compare against; version bump check skipped.")
        return 0
    changed_files = [
        line
        for line in _run_git("diff", "--name-only", f"{base_ref}..{head_ref}").splitlines()
        if line
    ]

    code_changes = [path for path in changed_files if _is_code_change(path)]
    if not code_changes:
        print("No code changes detected; version bump check skipped.")
        return 0

    if "version.py" not in changed_files:
        print("Code changed but version.py was not updated.")
        return 1

    current_version = _parse_version(_run_git("show", f"{head_ref}:version.py"))
    try:
        base_version_content = _run_git("show", f"{base_ref}:version.py")
    except subprocess.CalledProcessError:
        print(f"Could not read version.py from {base_ref}.")
        return 1
    base_version = _parse_version(base_version_content)

    if current_version <= base_version:
        print(
            "version.py must be incremented for code changes. "
            f"Current: {current_version}, base: {base_version}."
        )
        return 1

    print(
        "Version bump check passed. "
        f"Current: {current_version}, base: {base_version}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
