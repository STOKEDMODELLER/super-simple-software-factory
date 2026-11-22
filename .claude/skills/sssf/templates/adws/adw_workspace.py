#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""Recover interrupted workers or apply a run's reviewed patch to the operator tree."""
import argparse
import re
import sys
import shutil

from adw_modules import agents, git_helper, runtime
from adw_modules.tracer import Tracer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("reconcile")
    for action in ("review", "apply"):
        sub.add_parser(action).add_argument("run_id")
    args = parser.parse_args()
    cfg = agents.load_config(args.config)
    root = git_helper.repo_root()
    data = root / cfg.defaults.data_dir
    db = root / cfg.observability.db
    if db.exists():
        tracer = Tracer(db, data / "sessions" / "maintenance.jsonl")
        runtime.reconcile(tracer, cfg.defaults.stale_after_seconds)
    if args.action in ("review", "apply"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", args.run_id):
            parser.error("invalid run ID")
        directory = data / "sessions" / args.run_id
        if not directory.is_dir():
            parser.error("unknown run")
        with runtime.lease(directory):
            patch = directory / "worker.patch"
            if not patch.is_file():
                parser.error("this run has no recorded worker patch")
            if args.action == "review":
                with patch.open("rb") as stream:
                    shutil.copyfileobj(stream, sys.stdout.buffer)
                sys.exit(0)
            paths = runtime.safe_apply(patch, root)
            print(f"Applied {len(paths)} reviewed path(s) to the working tree.")
