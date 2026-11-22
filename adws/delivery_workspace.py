"""A delivery's Git metadata stays outside the only directory mounted into its worker."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def command(argv, *, cwd=None, env=None, data=None, timeout=60):
    result = subprocess.run(argv, cwd=cwd, env=env, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip()[-1500:] or
                           f"{argv[0]} exited {result.returncode}")
    return result.stdout


class Workspace:
    def __init__(self, directory, remote):
        self.root = Path(directory)
        self.gitdir = self.root / "objects.git"
        self.files = self.root / "files"
        self.remote = remote

    def git(self, *args, data=None, checkout=False):
        args = ["git", f"--git-dir={self.gitdir}", "-c", "core.hooksPath=/dev/null",
                "-c", "user.name=SSSF", "-c", "user.email=factory@localhost", *args]
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("GIT_") and key not in ("GIT_SSH_COMMAND", "GIT_ASKPASS"):
                del env[key]
        env["GIT_TERMINAL_PROMPT"] = "0"
        if checkout:
            env.update(GIT_WORK_TREE=str(self.files), GIT_INDEX_FILE=str(self.root / "index"))
        return command(args, env=env, data=data, cwd=self.files if checkout else None)

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.gitdir.exists():
            command(["git", "init", "--bare", "--quiet", str(self.gitdir)])

    def fetch(self, branch):
        self.git("fetch", "--quiet", "--no-tags", self.remote, "refs/heads/" + branch)
        return self.git("rev-parse", "FETCH_HEAD").decode().strip()

    def tree(self, commit):
        return self.git("rev-parse", "--verify", commit + "^{tree}").decode().strip()

    def prepare(self, commit):
        self.clean_files()
        self.files.mkdir()
        self.git("read-tree", commit, checkout=True)
        self.git("checkout-index", "-a", "-f", "--prefix=" + str(self.files) + "/", checkout=True)

    def capture(self, base, attempt):
        self.git("add", "-A", "--", ":/", checkout=True)
        tree = self.git("write-tree", checkout=True).decode().strip()
        patch = self.git("diff", "--no-ext-diff", "--no-renames", "--binary", "--full-index", base, tree)
        target = self.root / f"attempt-{attempt}.patch"
        tmp = target.with_suffix(".pending")
        with tmp.open("wb") as output:
            output.write(patch)
            output.flush()
            os.fsync(output.fileno())
        tmp.replace(target)
        digest = hashlib.sha256(patch).hexdigest()
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("patch checksum mismatch")
        return tree, digest

    def commit(self, base, tree, message):
        if tree == self.tree(base):
            return base
        return self.git("commit-tree", tree, "-p", base, data=message.encode()).decode().strip()

    def integration(self, base, head):
        tree = self.git("merge-tree", "--write-tree", base, head).decode().splitlines()[0]
        return self.git("commit-tree", tree, "-p", base, "-p", head,
                        data=b"SSSF integration candidate").decode().strip()

    def remote_head(self, branch):
        rows = self.git("ls-remote", "--heads", self.remote, "refs/heads/" + branch).split()
        return rows[0].decode() if rows else ""

    def publish(self, branch, head, previous):
        actual = self.remote_head(branch)
        if actual == head:
            return
        if actual != previous:
            raise ValueError("feature branch changed outside the delivery")
        ref = "refs/heads/" + branch
        self.git("push", f"--force-with-lease={ref}:{previous}", self.remote, f"{head}:{ref}")

    def contains(self, base, commit):
        if commit == base:
            return True
        shared = self.git("merge-base", base, commit).decode().strip()
        return shared == commit

    def clean_files(self):
        if self.files.exists():
            shutil.rmtree(self.files)
        (self.root / "index").unlink(missing_ok=True)
