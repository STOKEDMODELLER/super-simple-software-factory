"""Local run leases and recovery. Only the holder of a lease may retire its worktree."""
import fcntl
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import build_diff
from .utils import now_iso


class AdmissionError(RuntimeError):
    pass


def lease(directory):
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "run.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise AdmissionError(f"run {directory.name} is already active") from None
    return handle


def admit(db, run_id, maximum, operator, directory, base, engineer, workflow):
    conn = db.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        count, = conn.execute("SELECT COUNT(*) FROM run_leases").fetchone()
        if count >= maximum:
            raise AdmissionError(f"maximum concurrent runs reached ({maximum})")
        conn.execute("INSERT INTO run_leases VALUES (?,?,?,?)",
                     (run_id, str(operator), str(directory), base))
        db.session_start(run_id, engineer, workflow)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def record_patch(operator, directory, base):
    work = directory / "worktree"
    if not work.exists():
        return
    # Refuse to traverse a replacement directory or symlink during cleanup.
    if work.is_symlink() or Path(os.fsdecode(git(work, "rev-parse", "--show-toplevel")).strip()).resolve() != work.resolve():
        raise RuntimeError(f"unexpected worktree at {work}")
    ready = directory / "workspace.ready"
    if not ready.exists():
        # A crash while recreating a pinned workspace must preserve its prior patch.
        git(operator, "worktree", "remove", "--force", str(work))
        return
    target = directory / "worker.patch"
    tree = build_diff.snapshot(work)
    patch = git(work, "diff", "--binary", "--full-index", "--no-renames", "--no-ext-diff", base, tree)
    tmp = target.with_suffix(".pending")
    tmp.write_bytes(patch)
    tmp.replace(target)
    # The full delta is durable before disposing of any worker files.
    git(operator, "worktree", "remove", "--force", str(work))
    ready.unlink(missing_ok=True)


def safe_apply(patch_path, repo="."):
    repo = Path(repo).resolve()
    patch = Path(patch_path).resolve()
    if not patch.stat().st_size:
        return []
    rows = git(repo, "apply", "--numstat", "-z", str(patch)).split(b"\0")
    names = [os.fsdecode(row.split(b"\t", 2)[2]) for row in rows if row]
    if not names or any(not name for name in names):
        raise ValueError("expected a generated no-renames worker patch")
    dirty = git(repo, "--literal-pathspecs", "status", "--porcelain=v1", "-z",
                "--untracked-files=all", "--", *names)
    if dirty:
        raise RuntimeError("refusing to apply over staged, unstaged or untracked operator changes")
    git(repo, "apply", "--check", "--binary", str(patch))
    git(repo, "apply", "--binary", str(patch))
    return names


def nested_guard():
    parent = os.environ.get("SSSF_PARENT_ID")
    if not parent:
        return
    from .tracer import Tracer
    from .data_types import EventRecord
    db = os.environ.get("SSSF_PARENT_DB")
    log = os.environ.get("SSSF_PARENT_LOG")
    if db and log:
        tracer = Tracer(db, log)
        try:
            tracer.event(EventRecord(adw_id=parent, type="admission_rejected", name="nested workflow",
                                     parent_id=parent, payload={"parent_id": parent}))
        finally:
            tracer.conn.close()
    raise AdmissionError(f"nested workflow rejected (parent {parent})")


def process_birth(pid):
    row = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                         capture_output=True, text=True)
    return row.stdout.strip()


def terminate_group(pid):
    try:
        if os.getpgid(pid) != pid:
            return
        os.killpg(pid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(.05)
            states = subprocess.check_output(["ps", "-axo", "pgid=,stat="], text=True)
            if not any(group == str(pid) and not state.startswith("Z")
                       for group, state in (line.split() for line in states.splitlines())):
                return
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def retire(tracer, rid, operator, directory, base, status):
    record_patch(operator, directory, base)
    stamp = now_iso()
    conn = tracer.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE phases SET status=?,ended_at=? WHERE adw_id=? AND status='running'",
                     (status, stamp, rid))
        tracer.session_finish(rid, ok=status == "success", status=status)
        conn.execute("DELETE FROM run_leases WHERE adw_id=?", (rid,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def reconcile(tracer, stale_seconds):
    conn = tracer.conn
    for rid, operator, directory, base in conn.execute("SELECT * FROM run_leases").fetchall():
        directory = Path(directory)
        try:
            lock = lease(directory)
        except AdmissionError:
            continue
        with lock:
            # The preceding owner may have finished between SELECT and flock.
            if not conn.execute("SELECT 1 FROM run_leases WHERE adw_id=?", (rid,)).fetchone():
                continue
            for pid, birth in conn.execute("SELECT pid,birth FROM processes WHERE adw_id=? AND "
                                           "kind IN ('agent','check') AND ended_at IS NULL", (rid,)):
                if birth and process_birth(pid) == birth:
                    terminate_group(pid)
            retire(tracer, rid, Path(operator), directory, base, "interrupted")
    # Legacy runs have no lease. Never signal a historical PID. Require age AND
    # absence of the original workflow process before repairing its display.
    rows = conn.execute("SELECT adw_id,COALESCE(heartbeat_at,started_at) FROM sessions WHERE "
                        "status='running' AND adw_id NOT IN (SELECT adw_id FROM run_leases)").fetchall()
    for rid, stamp in rows:
        if not stamp or time.time() - datetime.fromisoformat(stamp).timestamp() < stale_seconds:
            continue
        processes = conn.execute("SELECT pid,command FROM processes WHERE kind='adw' AND "
                                 "ended_at IS NULL AND adw_id=?", (rid,)).fetchall()
        if any(command and command.split()[0] in subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
               for pid, command in processes):
            continue
        conn.execute("UPDATE phases SET status='interrupted', ended_at=? WHERE adw_id=? AND status='running'", (now_iso(), rid))
        tracer.session_finish(rid, ok=False, status="interrupted")
