"""Small, fail-closed GitHub CLI adapter for same-repository pull requests."""

from __future__ import annotations

import json
import subprocess
import tempfile

from typing import Any


_TIMEOUT = 30


class GitHub:
    def __init__(self, repo: str):
        parts = repo.strip().split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise ValueError("repo must be OWNER/REPOSITORY")
        self.repo = repo.strip()
        self.owner, self.name = parts

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        command = [
            "gh", "api", "graphql", "--input", "-",
        ]
        try:
            result = subprocess.run(
                command, input=json.dumps({"query": query, "variables": variables}), check=True, capture_output=True, text=True,
                timeout=_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            # In particular, do not echo gh's stderr: it can contain URLs or
            # diagnostic material supplied by an authentication helper.
            raise RuntimeError("GitHub request failed") from exc
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("GitHub returned invalid data") from exc
        if payload.get("errors"):
            raise RuntimeError("GitHub request was rejected")
        return payload.get("data") or {}

    @staticmethod
    def _review_status(pr: dict[str, Any], head: str) -> str:
        reviews = (pr.get("reviews") or {})
        if reviews.get("pageInfo", {}).get("hasNextPage"):
            return "REVIEW_REQUIRED"
        latest: dict[str, tuple[str, str]] = {}
        for review in reviews.get("nodes") or []:
            state = review.get("state")
            author = (review.get("author") or {}).get("login")
            commit = (review.get("commit") or {}).get("oid")
            if not author or not state or commit != head:
                continue
            when = review.get("submittedAt") or ""
            old = latest.get(author)
            if old is None or when >= old[1]:
                latest[author] = (state, when)
        states = {state for state, _ in latest.values()}
        if "CHANGES_REQUESTED" in states:
            return "CHANGES_REQUESTED"
        if "APPROVED" in states:
            return "APPROVED"
        return "REVIEW_REQUIRED"

    @staticmethod
    def _check_status(pr: dict[str, Any]) -> str:
        rollup = pr.get("statusCheckRollup")
        if not isinstance(rollup, dict):
            return "PENDING"
        contexts = rollup.get("contexts") or {}
        if contexts.get("pageInfo", {}).get("hasNextPage"):
            return "PENDING"
        nodes = contexts.get("nodes") or []
        # A successful aggregate with no contexts is not evidence of CI.
        if not nodes:
            return "PENDING"
        pending = False
        for node in nodes:
            kind = node.get("__typename")
            if kind == "CheckRun":
                status, conclusion = node.get("status"), node.get("conclusion")
                if status != "COMPLETED" or conclusion is None:
                    pending = True
                elif conclusion not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                    return "FAIL"
            elif kind == "StatusContext":
                state = node.get("state")
                if state in {"FAILURE", "ERROR"}:
                    return "FAIL"
                if state != "SUCCESS":
                    pending = True
            else:
                pending = True
        return "PENDING" if pending else "PASS"

    def lookup(self, branch: str, base: str) -> dict[str, Any] | None:
        query = """
        query($owner:String!, $name:String!, $head:String!, $base:String!) {
          repository(owner:$owner, name:$name) {
            mergeQueue(branch:$base) { id }
            pullRequests(first:20, headRefName:$head, baseRefName:$base,
              states:[OPEN,CLOSED,MERGED], orderBy:{field:UPDATED_AT,direction:DESC}) {
              nodes {
                number id state reviewDecision headRefName baseRefName isInMergeQueue
                headRefOid headRepository { name owner { login } }
                mergeCommit { oid }
                reviews(last:100) { nodes { state body submittedAt commit { oid } author { login } }
                  pageInfo { hasNextPage } }
                commits(last:1) { nodes { commit { statusCheckRollup { contexts(first:100) { totalCount nodes {
                  __typename ... on CheckRun { status conclusion }
                  ... on StatusContext { state }
                } pageInfo { hasNextPage } } } } } }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"owner": self.owner, "name": self.name,
                                     "head": branch, "base": base})
        repository = data.get("repository") or {}
        queue_supported = bool((repository.get("mergeQueue") or {}).get("id"))
        for pr in (repository.get("pullRequests") or {}).get("nodes") or []:
            head_repo = pr.get("headRepository") or {}
            if (pr.get("headRefName") != branch or pr.get("baseRefName") != base
                    or head_repo.get("name") != self.name
                    or (head_repo.get("owner") or {}).get("login") != self.owner):
                continue
            head = pr.get("headRefOid") or ""
            review = self._review_status(pr, head)
            if pr.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
                review = pr["reviewDecision"]
            nodes = pr["commits"]["nodes"]
            checks = self._check_status(nodes[0]["commit"] if nodes else {})
            reason = ""
            if not queue_supported:
                reason = "manual gate: protected merge queue could not be confirmed"
            elif review != "APPROVED":
                reason = "review gate is not approved at the current head"
            elif checks != "PASS":
                reason = "check gate is not passing at the current head"
            if review == "CHANGES_REQUESTED":
                reason = "\n".join(r.get("body", "") for r in pr["reviews"]["nodes"]
                                   if r["state"] == review and (r.get("commit") or {}).get("oid") == head)[-4000:]
            return {
                "number": pr.get("number"), "id": pr.get("id"), "head": head,
                "state": pr.get("state"),
                "merge_sha": (pr.get("mergeCommit") or {}).get("oid") or "",
                "review": review, "checks": checks,
                "queued": bool(pr.get("isInMergeQueue")),
                "queue_supported": queue_supported, "changes_reason": reason,
            }
        return None

    def create(self, branch: str, base: str, title: str, body: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            try:
                subprocess.run(
                    ["gh", "pr", "create", "--repo", self.repo, "--head", branch,
                     "--base", base, "--title", title, "--body-file", handle.name],
                    check=True, capture_output=True, text=True, timeout=_TIMEOUT,
                )
            except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                raise RuntimeError("GitHub pull request creation failed") from exc

    def enqueue(self, pr_dict: dict[str, Any]) -> None:
        if not pr_dict.get("queue_supported"):
            raise RuntimeError("protected merge queue could not be confirmed")
        self._queue_mutation("enqueuePullRequest", pr_dict)

    def cancel_queue(self, pr_dict: dict[str, Any]) -> None:
        self._queue_mutation("dequeuePullRequest", pr_dict)

    def _queue_mutation(self, mutation: str, pr_dict: dict[str, Any]) -> None:
        node_id, head = pr_dict.get("id"), pr_dict.get("head")
        if not node_id or not head:
            raise ValueError("pull request id and head are required")
        query = f"""
        mutation($input:{mutation[0].upper() + mutation[1:]}Input!) {{
          {mutation}(input:$input) {{ clientMutationId }}
        }}
        """
        fields = {"id": node_id}
        if mutation == "enqueuePullRequest":
            fields = {"pullRequestId": node_id, "expectedHeadOid": head}
        self._graphql(query, {"input": fields})
