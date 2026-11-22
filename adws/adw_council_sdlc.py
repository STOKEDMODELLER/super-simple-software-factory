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
at two thirds of applicable seats; on the FINAL round at least half of the
applicable seats can soft-accept — the build lands and the residual
blockers become a follow-up spec instead of a discarded working tree. A red
suite that survives the fix loop counts as an automatic rejection — seats never
convene over code that does not run.

Three mechanisms keep the loop convergent instead of a ratchet:
  1. Severity. Only `blocker` items veto; `major`/`nit` are advisories that
     ride into the record. A seat may approve WITH advisories.
  2. Spec freeze. Round 1's blockers are the complete acceptance bar for every
     later round — revision-round seats may reject only over carried-over
     blockers still unfixed or regressions the remediation itself introduced.
  3. Benching. A chair marks a seat `applicable: false` when its dimension does
     not exist in the change; the seat abstains.

Rejection is a loop, not a verdict. Code collects every seat's severity-blocker
items into one feedback envelope, writes it to context_handoff/council/, and the
next round re-enters at the SCOUT — recon focused on the gaps — then the planner
revises against the feedback, the builder rebuilds, and the council re-votes.
run.finish carries the last tally either way.
"""

import argparse
import sys
from pathlib import Path

from adw_modules import agents, changes, gates, git_helper, quality, session, utils
from adw_modules.data_types import (AgentCall, BuildOutput, ChangeCapture,
                                    CouncilSplitOutput, CouncilVoteOutput,
                                    DiagnosisOutput, DocumentOutput, GateReport,
                                    PhaseParams, PlanOutput, ReviewFinding,
                                    ReviewOutput, ScoutOutput)

SEATS = ["seat-fable", "seat-opus", "seat-sonnet", "seat-terra", "seat-luna", "seat-sol"]
CHAIR_PROPOSE = "chair-openai"
CHAIR_CONFIRM = "chair-anthropic"
REQUIRED_AGENTS = (["scout", "planner", "builder", "documenter", "medic",
                    CHAIR_PROPOSE, CHAIR_CONFIRM] + SEATS)

                            # ledger lands the build with reservations on record
MAX_COUNCIL_ROUNDS = 3      # original attempt + two feedback-driven revisions
MAX_FIX_LOOPS = 3

DIMENSIONS_HINT = ("derive the dimensions FROM THE REQUEST, don't copy a menu. "
                   "A user-facing change earns a product-fidelity/UX seat (does "
                   "the built thing serve the workflow the requester described); "
                   "a pipeline change earns data-integrity; every change earns "
                   "plan-conformance and regressions. Classic dimensions to draw "
                   "on when they apply: correctness & edge cases, security & "
                   "permissions, test adequacy, maintainability. A dimension "
                   "this change cannot violate is a benched seat (applicable: "
                   "false), never a filler brief")

CARRYOVER_RULE = ("Round discipline: this is a revision round. A seat may vote "
                  "reject ONLY over (a) a carried-over blocker from the previous "
                  "round that is still unfixed, or (b) a regression this "
                  "remediation itself introduced. Anything newly discovered that "
                  "the previous round did not flag is an advisory (severity "
                  "major/nit, origin new) — it rides into the record but does "
                  "not veto. The acceptance bar was set when the round-1 plan "
                  "was committed; it does not grow while the builder is fixing.")

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
    """A rejection needs at least one severity-blocker; an approval must not
    carry one. Majors and nits are advisories — they ride with either vote."""
    report = GateReport()
    hard = [b for b in envelope.blocking if b.severity == "blocker"]
    if envelope.approve:
        report.check("approve", not hard,
                     f"approved but {len(hard)} severity-blocker item(s) present — "
                     f"downgrade them to major/nit or vote reject" if hard else "")
    else:
        report.check("reject", bool(hard),
                     "" if hard else
                     "rejected without a single severity-blocker item — a major "
                     "or nit is an advisory, not a veto; name the genuine gap "
                     "or approve with advisories")
    return report


# ── feedback synthesis (code disposes: votes in, one envelope out) ──────────────

def council_tally(votes: list[tuple[str, CouncilVoteOutput]], final_round: bool) -> dict:
    """Return the decision and its evidence. An inapplicable seat has no vote."""
    benched = len(SEATS) - len(votes)
    reviewed = len(votes)
    approvals = sum(vote.approve for _, vote in votes)
    objected = reviewed - approvals
    # The denominator is real reviews, never automatic bench approvals.
    # A round with no applicable review cannot green-light by arithmetic.
    required = max(1, (2 * reviewed + 2) // 3)
    soft_floor = max(1, (reviewed + 1) // 2)
    green = reviewed > 0 and approvals >= required
    soft = final_round and not green and reviewed > 0 and approvals >= soft_floor
    residual = [f"[{seat}] {b.item}" for seat, vote in votes
                if not vote.approve for b in vote.blocking if b.severity == "blocker"]
    tally = (f"reviewed {reviewed}/{len(SEATS)}, approved {approvals}, "
             f"objected {objected}, benched {benched}; "
             f"{required} approvals required of reviewed seats"
             + (f" (soft-accept floor {soft_floor} on final round)"
                if final_round else ""))
    return dict(tally=tally, green_lit=green, soft_accepted=soft,
                reviewed=reviewed, approved=approvals, objected=objected,
                benched=benched, required_approvals=required, residual_blockers=residual,
                landing_decision="green" if green else "soft" if soft else "red")


def council_feedback(votes: list[tuple[str, CouncilVoteOutput]], tally: str) -> ReviewOutput:
    """Fold every seat's rejection into the one envelope the next round consumes.

    Only severity-blocker items become next-round obligations — this is the
    anti-ratchet. Majors and nits ride along as advisories the builder MAY take,
    so the acceptance bar is fixed once and the loop can converge.
    """
    blocking = [f"[{seat}] {b.item}" for seat, vote in votes
                for b in vote.blocking if not vote.approve and b.severity == "blocker"]
    advisories = [f"[{seat}] ({b.severity}) {b.item}" for seat, vote in votes
                  for b in vote.blocking if b.severity != "blocker"]
    findings = [ReviewFinding(requirement=f"[{seat}] {f.requirement}",
                              met=f.met, evidence=f.evidence)
                for seat, vote in votes for f in vote.findings]
    notes = ("Address every blocking item; each is prefixed with the seat that "
             "raised it, and the seat reviews in context_handoff/council/ carry "
             "the full argument. These blockers are the COMPLETE acceptance bar "
             "for the next round — the council may not add new ones.")
    if advisories:
        notes += (" Advisory (non-blocking, take them if cheap): "
                  + "; ".join(advisories))
    return ReviewOutput(
        status="success", approved=False, findings=findings, blocking=blocking,
        summary=f"council rejected the build ({tally})",
        notes_for_next_agent=notes)


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
        """Commit what the preceding phase produced, wherever it actually lives.

        commit_everywhere covers meta-repo layouts: an agent's diff may sit in a
        nested service repo the root repo .gitignores, where a root-level
        `git add -A` is blind — that exact blindness failed a unanimously
        approved run at its very last step.
        """
        message = envelope.commit_message or f"sssf({run.adw_id}): {envelope.summary}"
        commits = git_helper.commit_everywhere(message, root=run.repo_root)
        ph.log(commits=", ".join(f"{label}@{sha}" for label, sha in commits),
               message=message)

    def record(ph, result) -> None:
        passed = sum(1 for check in result.checks if check.passed)
        ph.log(passed=result.passed, checks=f"{passed}/{len(result.checks)}",
               artifacts=", ".join(result.artifacts))

    medic_calls = 0

    def diagnose(failed: str, error: Exception, previous=None) -> DiagnosisOutput:
        """Send the medic in. One level of recovery only — if the medic itself
        fails, the exception propagates and the caller decides."""
        nonlocal medic_calls
        medic_calls += 1
        diagnosis_path = council_dir / f"diagnosis_{medic_calls}.md"
        with run.phase(PhaseParams(name=f"medic_{medic_calls}", kind="agent", owner="medic",
                                   retries=1,
                                   description="Diagnose the failure from the evidence so the "
                                               "run recovers instead of dying")) as ph:
            return ph.call(AgentCall(
                output_type=DiagnosisOutput,
                prompt=(f"Failed segment: {failed}\n"
                        f"Error (verbatim):\n{error}\n\n"
                        f"Session dir: {run.session_dir}\n"
                        f"Repo root: {run.repo_root}\n\n"
                        f"The run's request:\n{prompt}\n\n"
                        f"Write your diagnosis to {diagnosis_path} and list it in "
                        f"artifacts."),
                previous=previous,
                gates=[gates.artifacts_exist]))

    def diagnosis_feedback(diagnosis: DiagnosisOutput, round_no: int) -> ReviewOutput:
        """A medic diagnosis, shaped as the same envelope council rejections use,
        so a crashed round re-enters the loop through the identical door."""
        blocking = [f"[medic] {diagnosis.cause or diagnosis.summary}"]
        if diagnosis.fix_instructions:
            blocking.append(f"[medic] fix: {diagnosis.fix_instructions}")
        return ReviewOutput(
            status="success", approved=False, blocking=blocking,
            summary=f"round {round_no} failed mid-flight; the medic diagnosed it",
            notes_for_next_agent=diagnosis.fix_instructions or diagnosis.cause)

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt, baseline=git_helper.short_sha(baseline),
               council=f"{len(SEATS)} seats, two thirds of applicable votes to green-light, "
                       f"max {MAX_COUNCIL_ROUNDS} round(s)")

    build: BuildOutput | None = None

    def do_round(round_no: int, feedback: ReviewOutput | None) -> tuple[str, str, ReviewOutput | None]:
        """One full round: scout → plan → build → test → council vote → tally.
        Returns (verdict, tally, feedback) where verdict is "green" (supermajority,
        land clean), "soft" (final round, majority — land with the residual
        blockers on record as a follow-up spec), or "red" (loop or fail)."""
        nonlocal build
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

        with run.phase(PhaseParams(name=f"scout{tag}", kind="agent", owner="scout", retries=1,
                                   description="Recon the code the request — and any council "
                                               "feedback — actually touches")) as ph:
            scouted = ph.call(AgentCall(output_type=ScoutOutput, prompt=scout_prompt,
                                        previous=feedback,
                                        gates=[gates.artifacts_exist]))

        with run.phase(PhaseParams(name=f"plan{tag}", kind="agent", owner="planner", retries=1,
                                   description="Turn the request — and any council feedback — "
                                               "into an implementable plan")) as ph:
            plan = ph.call(AgentCall(output_type=PlanOutput, prompt=plan_prompt,
                                     previous=scouted,
                                     gates=[gates.artifacts_exist, gates.files_non_empty]))

        with run.phase(PhaseParams(name=f"commit_plan{tag}", kind="code", owner="git",
                                   description="Put the spec on record before any code exists "
                                               "to blur it")) as ph:
            commit(ph, plan)

        with run.phase(PhaseParams(name=f"build{tag}", kind="agent", owner="builder", retries=1,
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
            return "red", "suite red — council not convened", test_feedback(test)

        # ── the council convenes ────────────────────────────────────────────
        seat_lines = "\n".join(f"- {seat}" for seat in SEATS)
        revision_note = ""
        if round_no > 1:
            feedback_path = council_dir / f"feedback_r{round_no}.md"
            revision_note = (
                f"\nThis is revision round {round_no}. The previous round's blockers "
                f"are at {feedback_path} — they are the COMPLETE acceptance bar. "
                f"Briefs must direct each seat to its carried-over blockers and to "
                f"regressions the remediation may have introduced; briefs must NOT "
                f"open new review scope. {CARRYOVER_RULE}\n")
        chair_context = (
            f"Beyond the assignments, fill `shared_context`: the context pack every "
            f"seat reads before voting. State (1) what the requester is actually "
            f"trying to achieve, in plain words; (2) the definition of done — which "
            f"plan sections are the acceptance criteria; (3) explicit non-goals; "
            f"(4) a do-not-block list: style, formatting, micro-optimisation, "
            f"hypothetical inputs the product cannot produce, and any work the "
            f"request never asked for. Severity legend for seats: only `blocker` "
            f"items veto; `major`/`nit` are advisories. A seat whose dimension "
            f"this change cannot violate gets `applicable: false` and is benched "
            f"(abstains) — bench honestly rather than inventing a lens.")

        with run.phase(PhaseParams(name=f"split{tag}", kind="agent", owner=CHAIR_PROPOSE, retries=1,
                                   description="First chair divides the review into one "
                                               "concrete brief per seat")) as ph:
            split = ph.call(AgentCall(
                output_type=CouncilSplitOutput,
                prompt=(f"PROPOSE the council's review split for this build.\n"
                        f"Seats (exact agent names, one brief each):\n{seat_lines}\n"
                        f"Strong seats: seat-fable, seat-opus, seat-terra — give them "
                        f"the subtlest dimensions.\n"
                        f"Dimensions: {DIMENSIONS_HINT}.\n"
                        f"{revision_note}\n{chair_context}\n\n"
                        f"The original request:\n{prompt}"),
                previous=build,
                gates=[assignments_cover_council]))

        with run.phase(PhaseParams(name=f"confirm{tag}", kind="agent", owner=CHAIR_CONFIRM, retries=1,
                                   description="Second chair audits the split against the real "
                                               "change and emits the final briefs")) as ph:
            final_split = ph.call(AgentCall(
                output_type=CouncilSplitOutput,
                prompt=(f"AUDIT the proposed split in previous_envelope against the "
                        f"actual change on disk. Reassign or rewrite any brief that "
                        f"is vague, redundant, or aimed at the wrong risk, then emit "
                        f"the FINAL assignments and the final `shared_context`.\n"
                        f"Seats (exact agent names, one brief each):\n{seat_lines}\n"
                        f"{revision_note}\n{chair_context}\n\n"
                        f"The original request:\n{prompt}"),
                previous=split,
                gates=[assignments_cover_council]))

        briefs = {a.seat: a for a in final_split.assignments}
        roster_lines = "\n".join(
            f"- {a.seat}: {a.focus}" + ("" if a.applicable else " (benched)")
            for a in final_split.assignments)
        votes: list[tuple[str, CouncilVoteOutput]] = []
        for seat in SEATS:
            brief = briefs[seat]
            if not brief.applicable:
                continue
            review_path = council_dir / f"{seat}{tag}.md"
            seat_rules = (CARRYOVER_RULE + "\n\n") if round_no > 1 else ""
            with run.phase(PhaseParams(name=f"vote_{seat}{tag}", kind="agent", owner=seat,
                                       retries=1,
                                       description=f"Seat reviews its brief — {brief.focus} — "
                                                   f"and casts its vote")) as ph:
                vote = ph.call(AgentCall(
                    output_type=CouncilVoteOutput,
                    prompt=(f"Council round {round_no}. Your focus: {brief.focus}\n"
                            f"Chairs' instructions: {brief.instructions}\n\n"
                            f"Chairs' shared context (read before voting):\n"
                            f"{final_split.shared_context or '(none provided)'}\n\n"
                            f"The full council, so you know what the other seats "
                            f"cover (do not wander into their lenses, and do not "
                            f"block on their behalf):\n{roster_lines}\n\n"
                            f"{seat_rules}"
                            f"The original request:\n{prompt}\n\n"
                            f"Write your review to {review_path} and list it in "
                            f"artifacts."),
                    previous=build,
                    gates=[gates.artifacts_exist, vote_consistent]))
                votes.append((seat, vote))

        result = council_tally(votes, round_no >= MAX_COUNCIL_ROUNDS)
        tally = result["tally"]
        with run.phase(PhaseParams(name=f"tally{tag}", kind="code", owner="council",
                                   description="Count the votes — the green light is "
                                               "arithmetic, not judgement")) as ph:
            ph.log(**result,
                   **{seat: "benched" for seat in SEATS if not briefs[seat].applicable},
                   **{seat: "approve" if vote.approve else "reject" for seat, vote in votes})

        if result["green_lit"]:
            return "green", tally, None
        if result["soft_accepted"]:
            # Land with reservations: the residual blockers become a follow-up
            # spec in the repo, not a discarded working tree.
            return "soft", tally, council_feedback(votes, tally)
        return "red", tally, council_feedback(votes, tally)

    # ── the recovery loop: a crashed round is diagnosed, never fatal ─────────
    # The medic's diagnosis re-enters through the same door a council rejection
    # uses, so recovery and revision are one mechanism. "abort" is the medic
    # saying a retry would fail identically — the run ends with the diagnosis
    # on record instead of a stack trace.
    verdict = "red"
    tally = "no round completed"
    feedback: ReviewOutput | None = None
    for round_no in range(1, MAX_COUNCIL_ROUNDS + 1):
        try:
            verdict, tally, feedback = do_round(round_no, feedback)
        except Exception as error:
            try:
                diagnosis = diagnose(f"round {round_no}", error, previous=build)
            except Exception:
                tally = f"round {round_no} failed undiagnosed: {error}"
                break
            tally = f"round {round_no} failed, diagnosed: {diagnosis.summary or diagnosis.cause}"
            if diagnosis.recommendation == "abort":
                break
            feedback = diagnosis_feedback(diagnosis, round_no)
            continue
        if verdict in ("green", "soft"):
            break

    green = verdict in ("green", "soft")

    # A soft acceptance lands the build but keeps the council's residual
    # blockers on record IN THE REPO as a follow-up spec — stranding N rounds
    # of working code uncommitted is worse than landing with reservations.
    if verdict == "soft" and feedback is not None:
        reservations_path = Path(run.repo_root) / "specs" / f"{run.adw_id}_council_reservations.md"
        reservations_path.parent.mkdir(parents=True, exist_ok=True)
        reservations_path.write_text(
            f"# Council reservations — {run.adw_id}\n\n"
            f"The council soft-accepted this build on its final round ({tally}).\n"
            f"Decision transparency is included in the tally above; its denominator is "
            f"applicable (reviewed) seats, with benched seats excluded.\n"
            f"These residual blockers were NOT resolved and are follow-up work:\n\n"
            + "\n".join(f"- {item}" for item in feedback.blocking) + "\n")

    # Only a green-lit (or soft-accepted) build lands. A rejected run leaves the
    # plans committed, the working tree dirty, and the reasons in context_handoff/.
    doc_note = ""
    if green and build is not None:
        try:
            with run.phase(PhaseParams(name="commit_build", kind="code", owner="git",
                                       description="Land the code only now: green suite, "
                                                   "council green-light")) as ph:
                commit(ph, build)
        except Exception as error:
            # A green-lit build that cannot land is the one failure that must
            # never pass silently — but it deserves a diagnosis, not a crash.
            try:
                diagnosis = diagnose("commit_build", error, previous=build)
                if diagnosis.recommendation == "retry":
                    with run.phase(PhaseParams(name="commit_build_retry", kind="code", owner="git",
                                               description="Retry the landing on the medic's "
                                                           "word that the cause is cleared")) as ph:
                        commit(ph, build)
                else:
                    return run.finish(accepted=False,
                                      reason=f"green-lit but the commit failed: "
                                             f"{diagnosis.cause or error}")
            except Exception as retry_error:
                return run.finish(accepted=False,
                                  reason=f"green-lit but the commit failed: {retry_error}")

        # Documentation is garnish on an approved, landed build: its failure is
        # diagnosed and noted, never allowed to flip a green run red.
        try:
            with run.phase(PhaseParams(name="changes", kind="code", owner="git",
                                       description="Diff the whole run against its pinned "
                                                   "baseline, for the documenter")) as ph:
                changeset = changes.capture(run, ChangeCapture(base=baseline))
                ph.log(base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
                       files=len(changeset.files) + len(changeset.untracked),
                       lines=f"+{changeset.insertions} -{changeset.deletions}",
                       diff=changeset.diff_path)
                if changeset.empty:
                    raise RuntimeError("nothing changed since the baseline at the root repo — "
                                       "the diff may live in a nested repo this capture "
                                       "cannot see.")

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
        except Exception as error:
            try:
                diagnosis = diagnose("documentation", error, previous=build)
                doc_note = f"; docs skipped — {diagnosis.summary or diagnosis.cause or error}"
            except Exception:
                doc_note = f"; docs skipped — {error}"

    reason = {"green": f"council green-lit the build ({tally})",
              "soft": f"council soft-accepted the build with reservations on "
                      f"record in specs/ ({tally})",
              }.get(verdict, f"the council never green-lit the build ({tally})")
    return run.finish(accepted=green, reason=f"{reason}{doc_note}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))
