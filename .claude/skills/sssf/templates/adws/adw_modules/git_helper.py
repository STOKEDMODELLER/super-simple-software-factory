"""Low-level git operations for code phases. All low-level logic lives in adw_modules."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(*args: str, cwd: str | Path | None = None) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def create_branch(name: str) -> str:
    _git("checkout", "-b", name)
    return name


def is_repo() -> bool:
    result = subprocess.run(["git", "rev-parse", "--git-dir"],
                            capture_output=True, text=True)
    return result.returncode == 0


def repo_root() -> Path:
    """Absolute root of the codebase — where agents are spawned to work.

    The git toplevel when there is one, else the process cwd (ADWs run fine in a
    non-git dir; only a commit phase requires a repo). Always absolute, so it is
    safe to hand to a subprocess regardless of where the ADW was launched from.
    """
    if is_repo():
        return Path(_git("rev-parse", "--show-toplevel")).resolve()
    return Path.cwd().resolve()


def commit_all(message: str) -> str:
    """Stage the working tree and commit it. Returns the new short sha."""
    if not is_repo():
        raise RuntimeError(
            "not a git repository — a commit phase needs one. Run `git init` in the "
            "repo root (and make a first commit) before running an ADW that commits.")
    _git("add", "-A")
    if not _git("status", "--porcelain"):
        raise RuntimeError("nothing to commit — the preceding phases changed no files")
    _git("commit", "-m", message)
    return _git("rev-parse", "--short", "HEAD")


# ── nested repos (meta-repo layouts) ─────────────────────────────────────────
#
# A meta repo that coordinates service repos hides them from itself: each child
# has its own .git and the parent .gitignores it. `git add -A` at the top level
# therefore CANNOT see an agent's changes inside a child — a run died with
# "nothing to commit" after a unanimous council approval because the whole diff
# lived in a nested frontend repo. Where a change landed is a knowable fact,
# so code detects it and commits in the right place. No agent judgement.

def nested_repos(root: str | Path = ".", max_depth: int = 2) -> list[Path]:
    """Child git repos under `root` (their own .git), excluding `root` itself."""
    root = Path(root).resolve()
    found: list[Path] = []
    def walk(directory: Path, depth: int) -> None:
        for child in sorted(directory.iterdir()):
            if not child.is_dir() or child.name in (".git", "node_modules", "__pycache__"):
                continue
            if (child / ".git").exists():
                found.append(child)      # a repo owns everything under it — stop here
            elif depth < max_depth:
                walk(child, depth + 1)
    try:
        walk(root, 1)
    except OSError:
        pass
    return found


def commit_everywhere(message: str, root: str | Path = ".") -> list[tuple[str, str]]:
    """Commit the change wherever it actually lives: the root repo and/or any
    nested repo with a dirty tree. Returns [(repo_label, short_sha), ...].

    Raises only when NOTHING changed anywhere — the same contract commit_all
    has for a single repo, widened to the layout the repo actually has.
    """
    root = Path(root).resolve()
    results: list[tuple[str, str]] = []
    candidates: list[tuple[str, Path]] = [(".", root)]
    candidates += [(str(child.relative_to(root)), child) for child in nested_repos(root)]
    for label, repo in candidates:
        if not (repo / ".git").exists():
            continue
        _git("add", "-A", cwd=repo)
        if not _git("status", "--porcelain", cwd=repo):
            continue
        _git("commit", "-m", message, cwd=repo)
        results.append((label, _git("rev-parse", "--short", "HEAD", cwd=repo)))
    if not results:
        raise RuntimeError("nothing to commit in the root repo or any nested repo — "
                           "the preceding phases changed no files")
    return results


def changed_files() -> list[str]:
    out = _git("status", "--porcelain")
    return [line[3:] for line in out.splitlines() if line]


# ── diff plumbing (composed into a ChangeSet by documentation.py) ────────────

def ref_exists(ref: str) -> bool:
    """True when `ref` resolves to a commit. Never raises — this is a question."""
    result = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                            capture_output=True, text=True)
    return result.returncode == 0


def rev(ref: str = "HEAD") -> str:
    return _git("rev-parse", ref)


def short_sha(ref: str = "HEAD") -> str:
    return _git("rev-parse", "--short", ref)


def merge_base(ref: str, other: str = "HEAD") -> str:
    """The commit where `ref` and `other` diverged — the honest base of a branch.

    On the base branch itself this returns HEAD, which makes the diff exactly
    "what is not committed yet". Off it, the diff is the whole branch plus the
    working tree. One command covers both cases, so no ADW has to branch on it.
    """
    return _git("merge-base", ref, other)


def is_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def untracked_files() -> list[str]:
    out = _git("ls-files", "--others", "--exclude-standard")
    return [line for line in out.splitlines() if line]


def diff_files(base: str) -> list[str]:
    """Tracked files that differ between `base` and the working tree."""
    out = _git("diff", "--name-only", base)
    return [line for line in out.splitlines() if line]


def diff_stat(base: str) -> str:
    return _git("diff", "--stat", base)


def diff_counts(base: str) -> tuple[int, int]:
    """(insertions, deletions) across the diff. Binary files count as neither."""
    insertions = deletions = 0
    for line in _git("diff", "--numstat", base).splitlines():
        added, removed, *_ = line.split("\t")
        if added.isdigit():
            insertions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return insertions, deletions


def diff_text(base: str) -> str:
    return _git("diff", base)
