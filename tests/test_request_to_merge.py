import uuid
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adws"))
from delivery import Delivery


class Host:
    def __init__(self, remote, operator):
        self.remote = remote
        self.operator = operator
        self.item = None
        self.creates = self.enqueues = 0

    def lookup(self, branch, base):
        return self.item

    def create(self, branch, base, title, body):
        self.creates += 1
        head = git(self.remote, "rev-parse", branch).decode().strip()
        self.item = dict(number=1, head=head, review="REVIEW_REQUIRED", checks="PENDING",
                         state="OPEN", queued=False, queue_supported=True)
        raise RuntimeError("lost response")

    def enqueue(self, item):
        self.enqueues += 1
        item["queued"] = True

    def merge(self, branch):
        git(self.operator, "fetch", "origin", branch)
        git(self.operator, "merge", "--no-ff", "--no-edit", "FETCH_HEAD")
        git(self.operator, "push", "origin", "main")
        self.item.update(queued=False, state="MERGED", merge_sha=git(self.operator, "rev-parse", "HEAD").decode().strip())


def git(path, *args):
    return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c", "user.email=t@example.org",
                                   "-C", str(path), *args], stderr=subprocess.PIPE)


@unittest.skipUnless(os.environ.get("SSSF_DOCKER_E2E") == "1", "set SSSF_DOCKER_E2E=1 for real Docker")
class EndToEnd(unittest.TestCase):
    def test_interrupted_worker_through_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            remote, caller, merger = (root / name for name in ("remote", "caller", "merger"))
            git(root, "init", "--bare", "-b", "main", str(remote))
            git(root, "clone", str(remote), str(caller))
            tracked = caller / "input"
            tracked.write_text("base")
            git(caller, "add", "input")
            git(caller, "commit", "-m", "base")
            git(caller, "push", "origin", "main")
            git(root, "clone", str(remote), str(merger))
            tracked.write_text("staged")
            git(caller, "add", "input")
            tracked.write_text("unstaged")
            (caller / "local.bin").write_bytes(bytes(range(256)))
            def snapshot():
                return (git(caller, "rev-parse", "HEAD"), git(caller, "write-tree"),
                        git(caller, "diff", "--cached", "--binary"), git(caller, "diff", "--binary"),
                        git(caller, "status", "--porcelain"), (caller / "local.bin").read_bytes())
            before = snapshot()
            host = Host(remote, merger)
            owner = uuid.uuid4().hex[:16]
            run = Delivery(root / owner, host)
            worker = """import os, subprocess, time
from pathlib import Path
if os.environ['SSSF_ATTEMPT'] == '1':
    subprocess.Popen(['sleep','300'], start_new_session=True)
    Path('work').write_text('partial')
    time.sleep(300)
else:
    assert Path('work').read_text() == 'partial'
    Path('work').write_text('completed')
"""
            check = ["python", "-c", "assert open('work').read() == 'completed'; assert open('input').read() == 'base'"]
            run.start(dict(repo="fixture/repo", request="Deliver", criteria="work completed", base="main",
                           image="python:3.12-alpine", worker=["python", "-c", worker], check=check,
                           smoke=check, merge="queue"), str(remote))
            killed = False
            try:
                for _ in range(400):
                    try:
                        s = run.tick()
                    except RuntimeError as exc:
                        if str(exc) != "lost response":
                            raise
                        continue
                    if not killed and (run.root / "files/work").exists():
                        name = f"sssf-{owner}-1"
                        processes = run.docker.cli("top", name, "-o", "pid,args").splitlines()
                        self.assertTrue(any(line.split()[1:] == ["sleep", "300"] for line in processes))
                        run.docker.cli("kill", name)
                        run = Delivery(run.root, host)
                        killed = True
                    if s["phase"] == "review" and host.item["review"] == "REVIEW_REQUIRED":
                        self.assertFalse(run.docker.cli("ps", "-aq", "--filter", f"label=sssf.delivery={owner}"))
                        host.item.update(review="APPROVED", checks="PASS")
                    if host.item and host.item["queued"]:
                        host.merge(s["branch"])
                    if s["phase"] in {"delivered", "stopped"}:
                        break
                    time.sleep(.05)
                self.assertEqual(s["phase"], "delivered", s)
                self.assertEqual((killed, s["retries"], host.creates, host.enqueues), (True, 1, 1, 1))
                self.assertEqual([r["exit"] for r in s["receipts"]], [137, 0, 0, 0, 0])
                self.assertEqual(s["receipts"][-1]["commit"], host.item["merge_sha"])
                self.assertEqual(snapshot(), before)
                self.assertFalse((run.root / "files").exists())
                self.assertEqual(run.docker.cli("ps", "-aq", "--filter", f"label=sssf.delivery={owner}"), "")
            finally:
                for ident in run.docker.cli("ps", "-aq", "--filter", f"label=sssf.delivery={owner}").split():
                    run.docker.cli("rm", "-fv", ident)
