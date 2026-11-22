"""Compare a builder's working tree at phase entry and after each attempt."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def snapshot(root: Path) -> str:
    """Return a Git tree including nonignored new files, without staging anything."""
    root = Path(root)
    index = subprocess.check_output(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "index"], cwd=root,
        text=True).rstrip("\n")
    with tempfile.TemporaryDirectory(prefix="sssf-index-") as tmp:
        copy = Path(tmp) / "index"
        if Path(index).is_file():
            shutil.copyfile(index, copy)
        env = {**os.environ, "GIT_INDEX_FILE": str(copy)}
        subprocess.run(["git", "add", "-A", "--", "."], cwd=root, env=env, check=True)
        return subprocess.check_output(["git", "write-tree"], cwd=root, env=env,
                                       text=True).strip()


def changed_paths(root: Path, before: str) -> set[str]:
    after = snapshot(root)
    names = subprocess.check_output(
        ["git", "diff", "--no-renames", "--name-only", "-z", before, after], cwd=root)
    return {os.fsdecode(name) for name in names.split(b"\0") if name}
