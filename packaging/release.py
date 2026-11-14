"""Release Ignite: check everything, tag the version, and (with --push) publish it.

    python packaging/release.py            # check, run the tests, tag vX.Y.Z on this commit
    python packaging/release.py --push     # ...then push main and the tag: GitHub builds and publishes it

Before running it: set __version__ in src/ignite_live/__init__.py, add a "## X.Y.Z" section at the top of
CHANGELOG.md (the release page starts with it), and commit both ("Ignite X.Y.Z").

Pushing the tag starts .github/workflows/build.yml: it builds Windows and Linux, and publishes the GitHub
release that Ignite's auto-updater offers to everyone. It can't be taken back quietly, so --push asks you to
type the version first (--yes skips that, for scripts).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REMOTE = "origin"
BRANCH = "main"


class ReleaseError(Exception):
    """Why it can't release yet, in a sentence that says what to do."""


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def version() -> str:
    found = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/ignite_live/__init__.py").read_text("utf-8"))
    if not found:
        raise ReleaseError("No __version__ in src/ignite_live/__init__.py.")
    return found.group(1)


def changelog_section(ver: str, text: str | None = None) -> str:
    """This version's notes, as the release workflow takes them: "## X.Y.Z" up to the next "## "."""
    text = (ROOT / "CHANGELOG.md").read_text("utf-8") if text is None else text
    lines, found = [], False
    for line in text.splitlines():
        if line == f"## {ver}" or line.startswith(f"## {ver} "):
            found = True
            continue
        if found and line.startswith("## "):
            break
        if found:
            lines.append(line)
    return "\n".join(lines).strip()


def check(ver: str, run_tests: bool) -> str:
    """Everything that must be true before tagging. Returns the tag name."""
    tag = f"v{ver}"
    if git("rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        raise ReleaseError(f"Release from {BRANCH}: switch to it first (git switch {BRANCH}).")
    if git("status", "--porcelain"):
        raise ReleaseError("There are uncommitted changes. Commit them (\"Ignite " + ver + "\") or stash them.")
    git("fetch", "--quiet", "--tags", REMOTE)
    behind = git("rev-list", "--count", f"HEAD..{REMOTE}/{BRANCH}") != "0"
    ahead = git("rev-list", "--count", f"{REMOTE}/{BRANCH}..HEAD") != "0"
    if behind and ahead:
        raise ReleaseError(f"{BRANCH} and {REMOTE}/{BRANCH} have diverged (history rewritten here, or new commits "
                           f"there). Don't pull blindly: compare them (git log --oneline --graph {BRANCH} "
                           f"{REMOTE}/{BRANCH}) and push or rebase on purpose.")
    if behind:
        raise ReleaseError(f"{BRANCH} is behind {REMOTE}/{BRANCH}: pull first (git pull --ff-only).")
    if git("tag", "--list", tag) or git("ls-remote", "--tags", REMOTE, f"refs/tags/{tag}"):
        raise ReleaseError(f"{tag} already exists. Bump __version__ for a new release.")
    if not changelog_section(ver):
        raise ReleaseError(f"CHANGELOG.md has no \"## {ver}\" section: add what's new (the release page shows it).")
    if run_tests and (ROOT / "tests").is_dir():
        print("Running the tests…")
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT)
        if result.returncode != 0:
            raise ReleaseError("The tests failed. Fix them, or pass --skip-tests if you're sure.")
    return tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release", description="Tag and publish an Ignite release.")
    parser.add_argument("--push", action="store_true", help="push main and the tag: GitHub builds and publishes it")
    parser.add_argument("--yes", action="store_true", help="don't ask before pushing")
    parser.add_argument("--skip-tests", action="store_true", help="don't run the tests first")
    args = parser.parse_args(argv)
    try:
        ver = version()
        tag = check(ver, run_tests=not args.skip_tests)
        commit = git("log", "-1", "--format=%h %s")
        git("tag", tag)  # Lightweight, like every earlier release tag.
        print(f"Tagged {tag} on {commit}.")
        if not args.push:
            print(f"To publish it: git push {REMOTE} {BRANCH} && git push {REMOTE} {tag}")
            print(f"Or run: python packaging/release.py --push   (after: git tag -d {tag})")
            return 0
        if not args.yes:
            typed = input(f"Publish Ignite {ver} to everyone? Type {ver} to confirm: ").strip()
            if typed != ver:
                git("tag", "-d", tag)
                print(f"Not published; removed the local {tag} tag.")
                return 1
        git("push", REMOTE, BRANCH)
        git("push", REMOTE, tag)
        url = git("remote", "get-url", REMOTE).removesuffix(".git").replace("git@github.com:", "https://github.com/")
        print(f"Pushed {tag}. Building and publishing: {url}/actions")
        return 0
    except ReleaseError as error:
        print(f"Can't release yet: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
