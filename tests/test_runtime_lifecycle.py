"""Exercise the public recipes with real Git and SQLite and an offline Pi test double."""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = "fixture-run"
PI = '''#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path
if '--list-models' in sys.argv:
    print('provider model context')
    print('openai-codex gpt-5.6-luna 272K')
    sys.exit()
assert Path.cwd().resolve() != Path(os.environ['OPERATOR']).resolve()
Path('worker.txt').write_text('worker edit\\n')
Path('worker new.bin').write_bytes(b'worker\\x00binary')
mode = os.environ['MODE']
if mode == 'interrupt':
    nested = subprocess.run(['just','build','recursive'],capture_output=True,text=True,timeout=10)
    assert nested.returncode and 'nested workflow rejected' in nested.stderr
    child = subprocess.Popen(['sleep', '60'])
    Path(os.environ['READY']).write_text(json.dumps([os.getpid(),child.pid]))
    time.sleep(60)
else:
    files = ['worker.txt','worker new.bin']
    if mode == 'false-claim':
        files = ['operator.txt']
    report = json.dumps(dict(status='success', summary='fixture', changed_files=files))
    print(json.dumps(dict(type='message_end',message=dict(role='assistant',
          usage=dict(totalTokens=100, cost=dict(total=0.1)),
          content=[dict(type='text',text=report)]))), flush=True)
'''


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sssf-preservation-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "operator"
        self.repo.mkdir()
        self.git("init", "-q")
        for name in (".gitignore", "justfile"):
            shutil.copyfile(ROOT / name, self.repo / name)
        paths = subprocess.check_output(["git", "ls-files", "-z", "adws"], cwd=ROOT)
        for name in filter(None, map(os.fsdecode, paths.split(b"\0"))):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        self.config = self.repo / "adws/adw_sssf_config/sssf.config.yaml"
        for name in ("worker.txt", "operator.txt"):
            (self.repo / name).write_text("committed\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "fixture")
        (self.repo / "operator.txt").write_text("staged bytes\n")
        self.git("add", "operator.txt")
        (self.repo / "operator.txt").write_text("unstaged bytes\n")
        (self.repo / "personal.bin").write_bytes(b"private\0bytes")
        self.pi = self.root / "pi"
        self.pi.write_text(PI)
        self.pi.chmod(0o755)
        self.marker = self.root / "ready"
        self.data = self.repo / "adws/adw_data"
        self.directory = self.data / "sessions" / RUN

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo)

    def snapshot(self):
        return (self.git("rev-parse", "HEAD"), self.git("write-tree"),
                self.git("diff", "--cached", "--binary"), self.git("diff", "--binary"),
                (self.repo / "operator.txt").read_bytes(), (self.repo / "personal.bin").read_bytes())

    def start(self, mode, run_id=RUN):
        return subprocess.Popen(["just", "build", "fixture", "--adw-id", run_id],
            cwd=self.repo, env={**os.environ, "PI_PATH": str(self.pi), "MODE": mode,
                                "READY": str(self.marker), "OPERATOR": str(self.repo)},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def just(self, *args):
        return subprocess.run(["just", *args], cwd=self.repo, capture_output=True, text=True, timeout=15)

    def query(self, sql, params=()):
        with sqlite3.connect(self.data / "sssf.db") as db:
            return db.execute(sql, params).fetchall()

    def assert_stopped(self, pid):
        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                               capture_output=True,text=True).stdout.strip()
        self.assertTrue(not state or state.startswith("Z"), (pid, state))

    def interrupted(self, sig):
        self.config.write_text(self.config.read_text().replace("defaults:\n", "defaults:\n  max_concurrency: 1\n", 1))
        before = self.snapshot()
        proc = self.start("interrupt")
        try:
            deadline = time.monotonic() + 20
            while not self.marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(self.marker.exists(), "worker never reached checkpoint")
            pids = json.loads(self.marker.read_text())
            denied = self.start("success", "concurrent-run")
            output, _ = denied.communicate(timeout=12)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("concurrent runs", output)
            [(owner,)] = self.query("SELECT pid FROM processes WHERE kind='adw' AND adw_id=?", (RUN,))
            os.kill(owner, sig)
            if sig == signal.SIGKILL:
                proc.wait(timeout=5)
                result = self.just("reconcile")
                self.assertEqual(result.returncode, 0, result.stderr)
            output, _ = proc.communicate(timeout=12)
            self.assertNotIn("PermissionError", output)
            self.assertNotEqual(proc.returncode, 0)
            for pid in pids:
                self.assert_stopped(pid)
            self.assertEqual(self.query("SELECT status FROM sessions WHERE adw_id=?", (RUN,)), [("interrupted",)])
            self.assertEqual(self.query("SELECT status FROM phases WHERE name='build'"), [("interrupted",)])
            self.assertEqual(self.query("SELECT parent_id FROM events WHERE type='admission_rejected'"), [(RUN,)])
            self.assertEqual(self.query("SELECT count(*) FROM run_leases"), [(0,)])
            self.assertEqual(self.snapshot(), before)
            self.assertFalse((self.directory / "worktree").exists())
            patch = self.directory / "worker.patch"
            review = self.just("review-run", RUN)
            self.assertEqual(review.returncode, 0, review.stderr)
            self.assertEqual(review.stdout.encode(), patch.read_bytes())
            paths = self.git("apply","--numstat","-z",str(patch)).split(b"\0")
            self.assertEqual({r.split(b"\t",2)[2] for r in paths if r}, {b"worker.txt",b"worker new.bin"})
            self.assertEqual((self.repo / "worker.txt").read_text(),"committed\n")
            self.assertFalse((self.repo / "worker new.bin").exists())
            result = self.just("apply-run",RUN)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.git("write-tree"), before[1])
            self.assertEqual((self.repo / "worker new.bin").read_bytes(), b"worker\0binary")
            self.assertEqual((self.repo / "operator.txt").read_bytes(), before[-2])
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()

    def test_sigterm(self):
        self.interrupted(signal.SIGTERM)

    def test_sigkill_then_reconcile(self):
        self.interrupted(signal.SIGKILL)

    def test_completed_work_needs_explicit_apply_and_conflicts_are_refused(self):
        before = self.snapshot()
        proc = self.start("success")
        output, _ = proc.communicate(timeout=15)
        self.assertEqual(proc.returncode, 0, output)
        self.assertEqual(self.snapshot(),before)
        (self.repo / "worker.txt").write_text("operator staged worker file\n")
        self.git("add","worker.txt")
        (self.repo / "worker.txt").write_text("committed\n")
        dirty = self.snapshot()
        result = self.just("apply-run",RUN)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("refusing",result.stderr)
        self.assertEqual(self.snapshot(),dirty)
        self.assertFalse((self.repo / "worker new.bin").exists())

    def test_false_claims_fail_the_build(self):
        proc = self.start("false-claim")
        output, _ = proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unreported changes",output)
        self.assertIn("unchanged claims",output)

    def test_configured_acceptance_failure_is_separate_from_diff(self):
        self.config.write_text(self.config.read_text().replace("defaults:\n", """defaults:
  acceptance_checks:
    - name: fixture_check
      area: backend
      operation: test
      argv: [python3, -c, "raise SystemExit(7)"]
""", 1))
        proc = self.start("success")
        output, _ = proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode, 0, output)
        self.assertEqual(self.query("SELECT DISTINCT gate,passed FROM gate_results ORDER BY gate"),
                         [("acceptance_checks",0),("diff_matches_claims",1)])
        self.assertIn("exit 7",output)

    def test_budget_stops_worker_on_reported_usage(self):
        self.config.write_text(self.config.read_text().replace("defaults:\n", "defaults:\n  budget_tokens: 50\n", 1))
        proc = self.start("success")
        output, _ = proc.communicate(timeout=15)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn("budget reached",output)
        self.assertEqual(self.query("select total_tokens,status from sessions"),[(100,"fail")])
