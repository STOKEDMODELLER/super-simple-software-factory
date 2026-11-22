"""Run admission, worktree isolation, heartbeats, and safe patch application."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .utils import ensure_dir, now_iso


class AdmissionError(RuntimeError):
    pass


def _git(*args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def admit(db, adw_id: str, max_concurrency: int, budget_tokens: int = 0,
          budget_cost: float = 0.0, parent_id: str = "") -> None:
    """Atomically reserve a run slot in the operator database."""
    conn = db.conn
    conn.execute("CREATE TABLE IF NOT EXISTS runtime_admission (adw_id TEXT PRIMARY KEY, "
                 "parent_id TEXT, reserved_at TEXT, tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0)")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if parent_id:
            raise AdmissionError(f"nested workflow rejected: parent={parent_id}")
        active = conn.execute("SELECT COUNT(*) FROM runtime_admission WHERE adw_id != ?", (adw_id,)).fetchone()[0]
        if max_concurrency > 0 and active >= max_concurrency:
            raise AdmissionError(f"max concurrency reached ({max_concurrency})")
        totals = conn.execute("SELECT COALESCE(SUM(tokens),0), COALESCE(SUM(cost),0) FROM runtime_admission").fetchone()
        if budget_tokens and totals[0] >= budget_tokens or budget_cost and totals[1] >= budget_cost:
            raise AdmissionError("runtime budget exhausted")
        conn.execute("INSERT OR IGNORE INTO runtime_admission(adw_id,parent_id,reserved_at) VALUES (?,?,?)",
                     (adw_id, parent_id, now_iso()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def release(db, adw_id: str) -> None:
    db.conn.execute("DELETE FROM runtime_admission WHERE adw_id=?", (adw_id,))


def create_worktree(operator_root: Path, session_dir: Path) -> Path:
    worktree = session_dir / "worktree"
    if worktree.exists():
        return worktree.resolve()
    ensure_dir(session_dir)
    _git("worktree", "add", "--detach", str(worktree), "HEAD", cwd=operator_root)
    return worktree.resolve()


def _patch_names(patch: str) -> set[str]:
    names = set()
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            names.add(line[6:].split("\t", 1)[0])
        elif line.startswith("--- a/") and line[6:].split("\t", 1)[0] != "/dev/null":
            names.add(line[6:].split("\t", 1)[0])
    return names


def safe_apply(patch_path: str | Path, repo: str | Path = ".") -> list[str]:
    """Apply a saved patch only when none of its paths are dirty."""
    repo = Path(repo).resolve()
    patch = Path(patch_path).read_text()
    names = _patch_names(patch)
    dirty = set()
    result = subprocess.run(["git", "status", "--porcelain", "--", *names], cwd=repo,
                            capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line:
            dirty.add(line[3:].strip().split(" -> ")[-1])
    if dirty:
        raise RuntimeError("refusing patch: touched paths are dirty: " + ", ".join(sorted(dirty)))
    checked = subprocess.run(["git", "apply", "--check", "--binary", str(Path(patch_path).resolve())],
                             cwd=repo, capture_output=True, text=True)
    if checked.returncode:
        raise RuntimeError(checked.stderr.strip() or "patch does not apply cleanly")
    subprocess.run(["git", "apply", "--binary", str(Path(patch_path).resolve())], cwd=repo, check=True)
    return sorted(names)


def kill_process_tree(pid: int, command: str = "", run_id: str = "") -> bool:
    """Kill only a recorded process whose live command still identifies this run."""
    try:
        actual = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    if run_id and run_id not in actual and (command and command not in actual):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False


def recover_stale(tracer, stale_seconds: int = 900) -> list[str]:
    """Mark stale runs interrupted and terminate their identity-checked children."""
    rows = tracer.conn.execute("SELECT adw_id, COALESCE(heartbeat_at, started_at) FROM sessions WHERE status='running'").fetchall()
    recovered = []
    for adw_id, started in rows:
        # ISO timestamps are comparable after converting through epoch.
        age = time.time() - __import__('datetime').datetime.fromisoformat(started).timestamp()
        if age < stale_seconds:
            continue
        for pid, command in tracer.conn.execute("SELECT pid,command FROM processes WHERE adw_id=? AND ended_at IS NULL", (adw_id,)):
            kill_process_tree(pid, command, adw_id)
        tracer.session_finish(adw_id, ok=False, status="interrupted")
        recovered.append(adw_id)
    return recovered