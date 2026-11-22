"""Crash-resumable request-to-merge controller. One journal and lock per delivery."""
import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from delivery_boundary import Docker
from delivery_github import GitHub
from delivery_workspace import Workspace

FINAL = {"delivered", "stopped"}
ROOT = Path(__file__).resolve().parent / "adw_data" / "deliveries"


class Delivery:
    def __init__(self, root, host=None):
        self.root = Path(root).resolve()
        self.path = self.root / "delivery.json"
        self.host = host
        self.docker = Docker()

    def save(self, state, phase):
        state["phase"] = phase
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, self.path)
        return state

    def start(self, spec, remote):
        for key in ("worker", "check", "smoke"):
            if not isinstance(spec[key], list) or not spec[key] or not all(isinstance(x, str) and x for x in spec[key]):
                raise ValueError(f"{key} requires an argument vector")
        for key in ("request", "criteria", "base", "image", "repo"):
            if not isinstance(spec[key], str) or not spec[key].strip():
                raise ValueError(f"missing {key}")
        subprocess.run(["git", "check-ref-format", "--branch", spec["base"]], check=True, capture_output=True)
        deadline = int(spec.get("deadline_seconds", 3600))
        if int(spec.get("attempt_seconds", deadline)) <= 0 or deadline <= 0 or spec.get("merge", "human") not in {"human", "queue"}:
            raise ValueError("invalid deadline or merge mode")
        image = self.docker.image(spec["image"])
        self.root.mkdir(parents=True, exist_ok=False)
        return self.save(dict(spec=spec, remote=remote, image=image, branch="sssf/" + self.root.name,
                              expires=time.time()+deadline, generation=0, retries=0, revisions=0,
                              refreshes=0, previous="", receipts=[]), "intake")

    def read(self):
        return json.loads(self.path.read_text())

    def tick(self, cancel=False):
        with (self.root / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            s = self.read()
            if s["phase"] in FINAL:
                return s
            spec = s["spec"]
            w = Workspace(self.root, s["remote"])
            host = self.host or GitHub(spec["repo"])
            if cancel or time.time() >= s["expires"]:
                s["reason"] = "cancelled" if cancel else "deadline"
                s.setdefault("stopped_from", s["phase"])
                self.save(s, "stop")
            phase = s["phase"]
            owner = self.root.name
            name = f"sssf-{owner}-{s['generation']}"
            def launch(job, commit):
                s.update(job=job, input=commit, generation=s["generation"]+1,
                         attempt_expires=time.time()+int(spec.get("attempt_seconds", spec.get("deadline_seconds", 3600))))
                return self.save(s, "prepare")
            def stop(reason):
                s["reason"] = reason
                return self.save(s, "stop")
            def pr():
                return host.lookup(s["branch"], spec["base"])
            if phase == "stop":
                self.docker.destroy(name, owner)
                if s.get("job") == "worker" and s.get("stopped_from") in {"run", "collect"} and w.files.exists() and s.get("input"):
                    _, s["partial_patch_sha256"] = w.capture(s["input"], s["generation"])
                w.clean_files()
                if s.get("published"):
                    item = pr()
                    if item and item["queued"]:
                        host.cancel_queue(item)
                        return s
                    if item and item["state"] == "MERGED":
                        s["merge_sha"] = item["merge_sha"]
                return self.save(s, "stopped")
            if phase == "intake":
                w.init()
                s["base"] = w.fetch(spec["base"])
                return launch("worker", s["base"])
            if phase == "prepare":
                w.prepare(s["input"])
                return self.save(s, "create")
            if phase in {"create", "run"}:
                state = self.docker.inspect(name, owner)
                if phase == "create":
                    if state is None:
                        env = {"SSSF_REQUEST": spec["request"], "SSSF_CRITERIA": spec["criteria"],
                               "SSSF_ATTEMPT": str(s["generation"]), "SSSF_FEEDBACK": s.get("feedback", "")}
                        self.docker.create(name, owner, s["image"], w.files, spec[s["job"]], env, "none")
                    return self.save(s, "run")
                if state and state["Status"] == "created":
                    self.docker.start(name)
                    return s
                if state and state["Running"]:
                    if time.time() >= s["attempt_expires"]:
                        s["exit"] = 143
                        return self.save(s, "collect")
                    return s
                s["exit"] = state["ExitCode"] if state else 137
                return self.save(s, "collect")
            if phase == "collect":
                self.docker.destroy(name, owner)
                if s["job"] == "worker":
                    tree, digest = w.capture(s["input"], s["generation"])
                    s["head"] = w.commit(s["input"], tree, spec["request"])
                    s["partial_patch_sha256"] = digest
                s["receipts"].append(dict(job=s["job"], generation=s["generation"], commit=s["input"],
                                          exit=s["exit"], container=name, removed=True))
                return self.save(s, "release")
            if phase == "release":
                w.clean_files()
                if s["exit"]:
                    if s["exit"] in {137, 143} and s["retries"] < 1:
                        s["retries"] += 1
                        return launch(s["job"], s["head"] if s["job"] == "worker" else s["input"])
                    return stop(f"{s['job']} exited {s['exit']}")
                if s["job"] == "worker":
                    if s["head"] in {s["base"], s["previous"]}:
                        return stop("worker produced no change")
                    return launch("check", s["head"])
                if s["job"] == "smoke":
                    return self.save(s, "delivered")
                if s.pop("postmerge_check", False):
                    return launch("smoke", s["merge_sha"])
                if s["input"] == s.get("candidate"):
                    s["tested"] = s["candidate"]
                    return self.save(s, "review")
                return self.save(s, "publish")
            if phase == "publish":
                try:
                    w.publish(s["branch"], s["head"], s["previous"])
                except ValueError as exc:
                    return stop(str(exc))
                s.update(previous=s["head"], published=True)
                return self.save(s, "pr")
            item = pr()
            if item is None:
                if phase != "pr":
                    return stop("PR disappeared")
                host.create(s["branch"], spec["base"], spec["request"].splitlines()[0], spec["criteria"])
                return s
            s["pr"] = item["number"]
            if item["head"] != s["head"] or item["state"] == "CLOSED":
                return stop("PR closed or head changed")
            if item["state"] == "MERGED":
                sha = item["merge_sha"]
                if not sha or not w.contains(w.fetch(spec["base"]), sha):
                    return stop("merge ancestry unverified")
                s["merge_sha"] = sha
                if not s.get("tested"):
                    return stop("merged before integration approval")
                if w.tree(sha) != w.tree(s["tested"]):
                    s["postmerge_check"] = True
                    return launch("check", sha)
                return launch("smoke", sha)
            if phase == "pr":
                return self.save(s, "review")
            if item["review"] == "CHANGES_REQUESTED":
                if s["revisions"] or item["queued"]:
                    return stop("review requires operator")
                s.update(revisions=1, feedback=item["changes_reason"])
                s.pop("tested", None)
                s.pop("integrated_base", None)
                return launch("worker", s["head"])
            if item["checks"] == "FAIL":
                return stop("remote checks failed")
            if item["review"] != "APPROVED" or item["checks"] != "PASS":
                return s
            base = w.fetch(spec["base"])
            if s.get("integrated_base") != base:
                if item["queued"]:
                    return s
                if s.get("integrated_base"):
                    if s["refreshes"]:
                        return stop("base advanced repeatedly")
                    s["refreshes"] += 1
                try:
                    candidate = w.integration(base, s["head"])
                except RuntimeError as exc:
                    return stop(f"integration failed: {exc}")
                s.update(candidate=candidate, integrated_base=base)
                return launch("check", candidate)
            if spec.get("merge") == "queue" and item["queue_supported"] and not item["queued"]:
                if s.get("queue_observed"):
                    return stop("removed from merge queue")
                host.enqueue(item)
            if item["queued"]:
                s["queue_observed"] = True
            return self.save(s, "await_merge")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "status", "watch", "cancel"])
    parser.add_argument("value", help="manifest path for start, delivery ID otherwise")
    args = parser.parse_args()
    if args.action == "start":
        spec = json.loads(Path(args.value).read_text())
        remote = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?", remote)
        if not match or match[1].lower() != spec["repo"].lower():
            parser.error("manifest repo must match the checkout's GitHub origin")
        uid = uuid.uuid4().hex[:16]
        Delivery(ROOT / uid).start(spec, remote)
        print(uid)
        return
    if not re.fullmatch("[0-9a-f]{16}", args.value):
        parser.error("invalid delivery ID")
    run = Delivery(ROOT / args.value)
    if args.action == "status":
        print(json.dumps(run.read(), indent=2))
        return
    while True:
        try:
            s = run.tick(cancel=args.action == "cancel")
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(str(exc), flush=True)
            time.sleep(5)
            continue
        if s["phase"] in FINAL:
            print(s["phase"], s.get("reason", ""))
            raise SystemExit(int(s["phase"] != "delivered"))
        time.sleep(5 if s["phase"] in {"review", "await_merge"} else 0.25)


if __name__ == "__main__":
    main()
