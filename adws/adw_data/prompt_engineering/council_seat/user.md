# Council Seat Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

`prompt` contains your brief from the chairs: your focus, their concrete
instructions, their shared context pack, the full council roster, the original
request, and the path your written review must land at. `previous_envelope` is
the builder's report of what was built.

1. Read the chairs' shared context pack in `prompt` — it defines what "done"
   means and what must not block.
2. Read `<context_handoff_dir>/plan.md` if it exists — the plan is the refined ask.
3. Review the change strictly through your assigned focus, starting from the
   files your brief and `previous_envelope.changed_files` name.
4. Write your review to the file path your brief specifies, and list it in
   `artifacts`.
5. Vote.

## Report

Respond with ONLY valid JSON matching `CouncilVoteOutput` — no prose before or after:

```json
{
  "status": "success",
  "approve": false,
  "summary": "<one sentence: your lens, and what the vote turns on>",
  "findings": [
    { "requirement": "<what your lens demanded>", "met": true, "evidence": "server/index.ts:42 — bounds checked" }
  ],
  "blocking": [
    { "item": "<what must change>", "severity": "blocker", "origin": "plan" },
    { "item": "<worth fixing later, does not veto>", "severity": "major", "origin": "new" }
  ],
  "artifacts": ["<the review file path your brief specified>"],
  "notes_for_next_agent": "<what the planner/builder must address, or how you verified if approving>"
}
```

`status` is `success` when your review completed — it is not the vote. The vote
is `approve`. Severity rules: only `blocker` items veto; `major`/`nit` are
advisories and may accompany an approval. A rejection without at least one
`blocker`-severity item will be sent back to you as a gate failure — as will an
approval that carries one. `origin` is one of `request`, `plan`, `carryover`,
`new`; in revision rounds, `new` items may be `blocker` only when they are
regressions the remediation itself introduced.
