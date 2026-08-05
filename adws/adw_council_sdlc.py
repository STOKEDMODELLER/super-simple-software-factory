#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Council SDLC — scout, plan, build, test, then a six-seat council votes.

Usage:
    uv run adws/adw_council_sdlc.py "<prompt or path/to/prompt.md>" [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4]

Phases: engineer(request)
        -> [per round] scout -> planner -> git(commit_plan)
           -> builder -> code(test) [-> builder(fix) ... bounded]
           -> chair-openai(split) -> chair-anthropic(confirm)
           -> seat x6 (each reviews its brief, votes) -> code(tally)
        -> green-lit: code(changes) -> documenter -> commits
        -> rejected: code writes the council's feedback, the loop re-enters at
           the scout with it, bounded by MAX_COUNCIL_ROUNDS.

The council replaces the single reviewer with eight agents in two tiers. The
chairs — one OpenAI, one Anthropic — run the council but never vote: the first
proposes how to divide the review into six concrete briefs, the second audits
that split against the real change and emits the final version. Code then fans
the briefs out to six seats drawn from both orgs' model families; each seat
reviews exactly one dimension and votes, and a rejection without a named
blocking item is a gate failure, not an opinion.

Code disposes of the votes. The tally is arithmetic, not judgement: green-light
at REQUIRED_APPROVALS of the six. A red suite that survives the fix loop counts
as an automatic rejection — seats never convene over code that does not run.

Rejection is a loop, not a verdict. Code collects every seat's blocking items
into one feedback envelope, writes it to context_handoff/council/, and the next
round re-enters at the SCOUT — recon focused on the gaps — then the planner
revises against the feedback, the builder rebuilds, and the council re-votes.
The chain accepts only a green-lit build; run.finish carries the last tally
either way.
"""

import argparse
import sys

from adw_modules import agents, changes, gates, git_helper, quality, session, utils
from adw_modules.data_types import (AgentCall, BuildOutput, ChangeCapture,
                                    CouncilSplitOutput, CouncilVoteOutput,
                                    DocumentOutput, GateReport, PhaseParams,
                                    PlanOutput, ReviewFinding, ReviewOutput,
                                    ScoutOutput)

SEATS = ["seat-fable", "seat-opus", "seat-sonnet", "seat-terra", "seat-luna", "seat-sol"]
CHAIR_PROPOSE = "chair-openai"
CHAIR_CONFIRM = "chair-anthropic"
REQUIRED_AGENTS = (["scout", "planner", "builder", "documenter",
                    CHAIR_PROPOSE, CHAIR_CONFIRM] + SEATS)

REQUIRED_APPROVALS = 5      # of the six seats — a supermajority green-lights
MAX_COUNCIL_ROUNDS = 2      # original attempt + one feedback-driven revision
MAX_FIX_LOOPS = 3

DIMENSIONS_HINT = ("plan-conformance, correctness & edge cases, security & "
                   "permissions, test adequacy, regressions & blast radius, "
                   "simplicity & maintainability")

DOCUMENT_NOTES = ("Read diff_path in full before writing. Document only what the "
                  "diff shows, then copy the write-up into app_docs/ as your task "
                  "describes.")


# ── council gates (inline one-offs; the shape is gate(envelope, run) -> report) ──

def assignments_cover_council(envelope, run) -> GateReport:
    """Every seat exactly one brief; no brief for a stranger."""
    report = GateReport()
    counts = {seat: 0 for seat in SEATS}
    for assignment in envelope.assignments:
        if assignment.seat in counts:
            counts[assignment.seat] += 1
        else:
            report.check(assignment.seat, False, "not a council seat")
    for seat, n in counts.items():
        report.check(seat, n == 1,
                     "no brief for this seat" if n == 0 else
                     ("" if n == 1 else f"{n} briefs — one seat, one brief"))
    return report


def vote_consistent(envelope, run) -> GateReport:
    """A rejection must say why; an approval must not carry blockers."""
    report = GateReport()
    if envelope.approve:
        report.check("approve", not envelope.blocking,
                     "approved but blocking items present" if envelope.blocking else "")
    else:
        report.check("reject", bool(envelope.blocking),
                     "" if envelope.blocking else
                     "rejected without a single blocking item — name the gap")
    return report


# ── feedback synthesis (code disposes: votes in, one envelope out) ──────────────

def council_feedback(votes: list[tuple[str, CouncilVoteOutput]], tally: str) -> ReviewOutput:
    """Fold every seat's rejection into the one envelope the next round consumes."""
    blocking = [f"[{seat}] {item}" for seat, vote in votes
                for item in vote.blocking if not vote.approve]
    findings = [ReviewFinding(requirement=f"[{seat}] {f.requirement}",
                              met=f.met, evidence=f.evidence)
                for seat, vote in votes for f in vote.findings]
    return ReviewOutput(
        status="success", approved=False, findings=findings, blocking=blocking,
        summary=f"council rejected the build ({tally})",
        notes_for_next_agent=("Address every blocking item. Each is prefixed with "
                              "the seat that raised it; the seat reviews in "
                              "context_handoff/council/ carry the full argument."))


def test_feedback(result) -> ReviewOutput:
    """A red suite is an automatic rejection — the council never saw the build."""
    return ReviewOutput(
        status="success", approved=False,
        blocking=[f"[suite] {failure}" for failure in result.failures],
        summary="the suite stayed red through the fix loop — automatic rejection",
        notes_for_next_agent="Revise the plan so the builder can make the suite pass.")


def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml", adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)
    baseline = git_helper.rev("HEAD")     # pinned before this run commits anything

    council_dir = run.context_handoff_dir / "council"
    council_dir.mkdir(parents=True, exist_ok=True)

    def commit(ph, envelope) -> None:
        """Commit what the preceding phase produced, in that agent's own words."""
        message = envelope.commit_message or f"sssf({run.adw_id}): {envelope.summary}"
        ph.log(sha=git_helper.commit_all(message), message=message)

    def record(ph, result) -> None:
        passed = sum(1 for check in result.checks if check.passed)
        ph.log(passed=result.passed, checks=f"{passed}/{len(result.checks)}",
               artifacts=", ".join(result.artifacts))

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt, baseline=git_helper.short_sha(baseline),
               council=f"{len(SEATS)} seats, green-light at {REQUIRED_APPROVALS}, "
                       f"max {MAX_COUNCIL_ROUNDS} round(s)")

    feedback: ReviewOutput | None = None
    build: BuildOutput | None = None
    green = False
    tally = "no round completed"

    for round_no in range(1, MAX_COUNCIL_ROUNDS + 1):
        tag = f"_r{round_no}"

        if feedback is None:
            scout_prompt = prompt
            plan_prompt = prompt
        else:
            feedback_path = council_dir / f"feedback{tag}.md"
            feedback_path.write_text(
                f"# Council feedback — round {round_no - 1}\n\n{feedback.summary}\n\n"
                + "\n".join(f"- {item}" for item in feedback.blocking) + "\n")
            scout_prompt = (f"{prompt}\n\nThe council rejected the previous build. "
                            f"Scout the code paths its blocking items point at — "
                            f"the feedback is in your previous_envelope and at "
                            f"{feedback_path} — so the planner can revise with "
                            f"specifics, not guesses.")
            plan_prompt = (f"{prompt}\n\nThis is revision round {round_no}: the "
                           f"council rejected the previous build for the blocking "
                           f"items in {feedback_path}. Your revised plan must "
                           f"close every one of them; the seat reviews in "
                           f"{council_dir} carry the full arguments.")

        with run.phase(PhaseParams(name=f"scout{tag}", kind="agent", owner="scout",
                                   description="Recon the code the request — and any council "
                                               "feedback — actually touches")) as ph:
            scouted = ph.call(AgentCall(output_type=ScoutOutput, prompt=scout_prompt,
                                        previous=feedback,
                                        gates=[gates.artifacts_exist]))

        with run.phase(PhaseParams(name=f"plan{tag}", kind="agent", owner="planner",
                                   description="Turn the request — and any council feedback — "
                                               "into an implementable plan")) as ph:
            plan = ph.call(AgentCall(output_type=PlanOutput, prompt=plan_prompt,
                                     previous=scouted,
                                     gates=[gates.artifacts_exist, gates.files_non_empty]))

        with run.phase(PhaseParams(name=f"commit_plan{tag}", kind="code", owner="git",
                                   description="Put the spec on record before any code exists "
                                               "to blur it")) as ph:
            commit(ph, plan)

        with run.phase(PhaseParams(name=f"build{tag}", kind="agent", owner="builder",
                                   description="Implement the plan exactly")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=plan,
                                      gates=[gates.diff_matches_claims]))

        test = None
        for i in range(1, MAX_FIX_LOOPS + 1):
            with run.phase(PhaseParams(name=f"test{tag}_{i}", kind="code", owner="quality",
                                       description="Run the suite — a known command, so code "
                                                   "runs it and no agent rediscovers it")) as ph:
                test = quality.run_tests(run)
                record(ph, test)
            if test.passed:
                break
            with run.phase(PhaseParams(name=f"fix{tag}_{i}", kind="agent", owner="builder",
                                       retries=1,
                                       description="Repair what the suite reported, from its "
                                                   "verbatim output")) as ph:
                build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                          previous=quality.as_envelope(test, "tests"),
                                          gates=[gates.diff_matches_claims]))

        if test is not None and not test.passed:
            tally = "suite red — council not convened"
            feedback = test_feedback(test)
            continue

        # ── the council convenes ────────────────────────────────────────────
        seat_lines = "\n".join(f"- {seat}" for seat in SEATS)
        with run.phase(PhaseParams(name=f"split{tag}", kind="agent", owner=CHAIR_PROPOSE,
                                   description="First chair divides the review into one "
                                               "concrete brief per seat")) as ph:
            split = ph.call(AgentCall(
                output_type=CouncilSplitOutput,
                prompt=(f"PROPOSE the council's review split for this build.\n"
                        f"Seats (exact agent names, one brief each):\n{seat_lines}\n"
                        f"Strong seats: seat-fable, seat-opus, seat-terra — give them "
                        f"the subtlest dimensions.\n"
                        f"Dimensions to adapt, not copy: {DIMENSIONS_HINT}.\n\n"
                        f"The original request:\n{prompt}"),
                previous=build,
                gates=[assignments_cover_council]))

        with run.phase(PhaseParams(name=f"confirm{tag}", kind="agent", owner=CHAIR_CONFIRM,
                                   description="Second chair audits the split against the real "
                                               "change and emits the final briefs")) as ph:
            final_split = ph.call(AgentCall(
                output_type=CouncilSplitOutput,
                prompt=(f"AUDIT the proposed split in previous_envelope against the "
                        f"actual change on disk. Reassign or rewrite any brief that "
                        f"is vague, redundant, or aimed at the wrong risk, then emit "
                        f"the FINAL assignments.\n"
                        f"Seats (exact agent names, one brief each):\n{seat_lines}\n\n"
                        f"The original request:\n{prompt}"),
                previous=split,
                gates=[assignments_cover_council]))

        briefs = {a.seat: a for a in final_split.assignments}
        votes: list[tuple[str, CouncilVoteOutput]] = []
        for seat in SEATS:
            brief = briefs[seat]
            review_path = council_dir / f"{seat}{tag}.md"
            with run.phase(PhaseParams(name=f"vote_{seat}{tag}", kind="agent", owner=seat,
                                       retries=1,
                                       description=f"Seat reviews its brief — {brief.focus} — "
                                                   f"and casts its vote")) as ph:
                vote = ph.call(AgentCall(
                    output_type=CouncilVoteOutput,
                    prompt=(f"Council round {round_no}. Your focus: {brief.focus}\n"
                            f"Chairs' instructions: {brief.instructions}\n\n"
                            f"The original request:\n{prompt}\n\n"
                            f"Write your review to {review_path} and list it in "
                            f"artifacts."),
                    previous=build,
                    gates=[gates.artifacts_exist, vote_consistent]))
                votes.append((seat, vote))

        approvals = sum(1 for _, vote in votes if vote.approve)
        tally = f"{approvals}/{len(SEATS)} approvals, {REQUIRED_APPROVALS} required"
        with run.phase(PhaseParams(name=f"tally{tag}", kind="code", owner="council",
                                   description="Count the votes — the green light is "
                                               "arithmetic, not judgement")) as ph:
            ph.log(tally=tally, green_lit=approvals >= REQUIRED_APPROVALS,
                   **{seat: ("approve" if vote.approve else "reject")
                      for seat, vote in votes})

        if approvals >= REQUIRED_APPROVALS:
            green = True
            break
        feedback = council_feedback(votes, tally)

    # Only a green-lit build lands. A rejected run leaves the plans committed,
    # the working tree dirty, and the council's reasons in context_handoff/.
    if green and build is not None:
        with run.phase(PhaseParams(name="commit_build", kind="code", owner="git",
                                   description="Land the code only now: green suite, "
                                               "council green-light")) as ph:
            commit(ph, build)

        with run.phase(PhaseParams(name="changes", kind="code", owner="git",
                                   description="Diff the whole run against its pinned "
                                               "baseline, for the documenter")) as ph:
            changeset = changes.capture(run, ChangeCapture(base=baseline))
            ph.log(base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
                   files=len(changeset.files) + len(changeset.untracked),
                   lines=f"+{changeset.insertions} -{changeset.deletions}",
                   diff=changeset.diff_path)
            if changeset.empty:
                raise RuntimeError("nothing changed since the baseline — "
                                   "there is nothing to document.")

        with run.phase(PhaseParams(name="document", kind="agent", owner="documenter",
                                   retries=1,
                                   description="Write up the council-approved change")) as ph:
            document = ph.call(AgentCall(output_type=DocumentOutput, prompt=prompt,
                                         previous=changes.as_envelope(changeset, DOCUMENT_NOTES),
                                         gates=[gates.artifacts_exist, gates.files_non_empty]))

        with run.phase(PhaseParams(name="commit_docs", kind="code", owner="git",
                                   description="Ship the write-up in its own commit, beside "
                                               "the code it describes")) as ph:
            commit(ph, document)

    return run.finish(accepted=green,
                      reason=f"the council never green-lit the build ({tally})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))
