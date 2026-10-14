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


def _base_ref() -> str:
    for candidate in ("origin/main", "main"):
        try:
            _run_git("rev-parse", "--verify", f"{candidate}^{{commit}}")
            return candidate
        except subprocess.CalledProcessError:
            continue
    return "HEAD~1"


def _resolved_base_ref(base_ref: str | None) -> str:
    if base_ref:
        try:
            _run_git("rev-parse", "--verify", f"{base_ref}^{{commit}}")
            return base_ref
        except subprocess.CalledProcessError:
            pass
    return _base_ref()


def _resolved_head_ref(head_ref: str | None) -> str:
    if head_ref:
        try:
            _run_git("rev-parse", "--verify", f"{head_ref}^{{commit}}")
            return head_ref
        except subprocess.CalledProcessError:
            pass
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
    base_ref = _resolved_base_ref(args.base)
    head_ref = _resolved_head_ref(args.head)
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
