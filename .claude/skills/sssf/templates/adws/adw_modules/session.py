"""Admit a workflow, bind it to its own checkout, and preserve its patch on exit."""
import atexit
import os
import re
import signal
import sqlite3
import sys
import threading
from pathlib import Path

from . import git_helper, runtime
from .runner import Run
from .tracer import Tracer
from .utils import engineer_name, new_id, now_iso


def ensure(cfg, adw_id=None):
    runtime.nested_guard()
    operator = git_helper.repo_root()
    rid = adw_id or new_id(8)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", rid):
        raise ValueError("invalid run ID")
    directory = (operator / cfg.defaults.data_dir / "sessions" / rid).resolve()
    db = (operator / cfg.observability.db).resolve()
    tracer = Tracer(db, directory / "events.jsonl")
    runtime.reconcile(tracer, cfg.defaults.stale_after_seconds)
    lock = runtime.lease(directory)
    base_file = directory / "base.commit"
    base = base_file.read_text().strip() if base_file.exists() else git_helper.rev("HEAD")
    engineer = engineer_name()
    try:
        runtime.admit(tracer, rid, cfg.defaults.max_concurrency, operator, directory, base,
                      engineer, Path(sys.argv[0]).stem)
    except BaseException:
        lock.close()
        raise
    work = directory / "worktree"
    stop = threading.Event()
    interrupted = False
    finished = False

    def finalize(status="fail"):
        nonlocal finished
        if finished:
            return
        finished = True
        stop.set()
        os.chdir(operator)
        try:
            runtime.retire(tracer, rid, operator, directory, base, "interrupted" if interrupted else status)
        finally:
            lock.close()

    def on_signal(number, frame):
        nonlocal interrupted
        interrupted = True
        raise SystemExit(128 + number)

    atexit.register(finalize)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, on_signal)
    try:
        tracer.process_start(rid, "adw", "", os.getpid(), " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]))
        base_file.write_text(base + "\n")
        (directory / "workspace.ready").unlink(missing_ok=True)
        runtime.git(operator, "worktree", "add", "--quiet", "--detach", str(work), base)
        patch = directory / "worker.patch"
        if patch.exists() and patch.stat().st_size:
            runtime.git(work, "apply", "--binary", str(patch))
        (directory / "workspace.ready").touch()
        os.chdir(work)
        run = Run(cfg, rid, tracer, engineer, directory, work)
        run.finalize = finalize
        os.environ.update(SSSF_PARENT_ID=rid, SSSF_PARENT_DB=str(db),
                          SSSF_PARENT_LOG=str(directory / "events.jsonl"))
    except BaseException:
        finalize()
        raise

    def heartbeat():
        with sqlite3.connect(db, timeout=5, isolation_level=None) as conn:
            while not stop.wait(min(15, max(1, cfg.defaults.stale_after_seconds / 3))):
                try:
                    conn.execute("UPDATE sessions SET heartbeat_at=? WHERE adw_id=? AND status='running'",
                                 (now_iso(), rid))
                except sqlite3.OperationalError as exc:
                    if getattr(exc, "sqlite_errorcode", None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                        raise

    threading.Thread(target=heartbeat, daemon=True).start()
    run.console.session_started(rid, run.engineer)
    return run
