"""Session lifecycle: pin-or-create an adw_id, build the Run object.

`ensure(cfg, adw_id)` joins the session if it exists or creates it under
exactly that id (pinned ids for repeatable runs); omitted, a fresh id is
minted and printed so the next ADW can pick it up.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from .data_types import SSSFConfig, EventRecord
from .runner import Run
from .tracer import Tracer
from .utils import engineer_name, new_id, ensure_dir
from . import git_helper, runtime


def _finalize_when_killed(run: Run) -> None:
    """A killed run still closes its own trace.

    Python's default SIGTERM handling exits without unwinding, so `just kill`
    (or any `kill <pid>`) would leave the session reading `running` forever and
    its process rows open — the trace would claim work is in flight that is
    already dead. Turning the signal into SystemExit both finalizes here and
    lets the phase context manager record the phase as failed on the way out.
    """
    def handler(signum, _frame):
        run.save_patch()
        run.tracer.event(EventRecord(
            adw_id=run.adw_id, type="interrupted", name="signal",
            payload={"signal": signum, "patch": run.patch_path or ""}))
        run.tracer.session_finish(run.adw_id, ok=False, status="interrupted")
        runtime.release(run.tracer, run.adw_id)
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def ensure(cfg: SSSFConfig, adw_id: str | None = None) -> Run:
    operator_root = git_helper.repo_root()
    adw_id = adw_id or new_id(8)
    data_dir = (operator_root / cfg.defaults.data_dir).resolve()
    db_path = (operator_root / cfg.observability.db).resolve()
    session_dir = ensure_dir(data_dir / "sessions" / adw_id)
    tracer = Tracer(db_path, session_dir / "events.jsonl")
    parent = os.environ.get("SSSF_PARENT_ID", os.environ.get("ADW_PARENT_ID", ""))
    try:
        runtime.admit(tracer, adw_id, cfg.defaults.max_concurrency,
                      cfg.defaults.budget_tokens, cfg.defaults.budget_cost, parent)
    except runtime.AdmissionError as error:
        tracer.session_start(adw_id, engineer_name(), adw_name=Path(sys.argv[0]).stem)
        tracer.event(EventRecord(
            adw_id=adw_id, type="admission_rejected", name="admission", payload={"reason": str(error), "parent_id": parent}))
        tracer.session_finish(adw_id, ok=False)
        raise
    baseline = git_helper.rev("HEAD", cwd=operator_root)
    worktree = runtime.create_worktree(operator_root, session_dir)
    os.chdir(worktree)
    run = Run(cfg=cfg, adw_id=adw_id, tracer=tracer, engineer=engineer_name(),
              operator_root=operator_root, session_dir=session_dir, worktree=worktree)
    run.baseline = baseline
    os.environ["SSSF_PARENT_ID"] = adw_id
    tracer.session_start(adw_id, run.engineer, adw_name=Path(sys.argv[0]).stem)
    # This process is the run. Record it before any phase opens, so a run that
    # hangs in its first agent call is still killable by adw_id.
    tracer.process_start(adw_id, "adw", "", os.getpid(),
                         " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]))
    _finalize_when_killed(run)
    run.console.session_started(adw_id, run.engineer)
    return run
