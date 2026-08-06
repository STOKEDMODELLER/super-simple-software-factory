# Council Chair Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Follow the instructions in `prompt`. You are either PROPOSING the split (the
`previous_envelope` is the builder's report) or AUDITING one (the
`previous_envelope` is the other chair's proposed assignments — verify its
coverage against the real change, fix weak or misassigned briefs, bench seats
whose dimension does not apply, tighten the shared context pack, and emit the
final version).

Either way: read `<context_handoff_dir>/plan.md` and inspect the changed files
on disk first. Every seat named in `prompt` gets exactly one assignment —
benched seats included, with `applicable: false` and a one-line reason as the
brief.

## Report

Respond with ONLY valid JSON matching `CouncilSplitOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence: how the review was divided and why>",
  "shared_context": "<the context pack every seat reads: what the requester is trying to achieve, the definition of done, non-goals, and the do-not-block list>",
  "assignments": [
    {
      "seat": "<exact seat agent name from the task>",
      "focus": "<the dimension this seat owns, e.g. product-fidelity>",
      "instructions": "<concrete: files to read first, claims to distrust, evidence that settles it>",
      "applicable": true
    },
    {
      "seat": "<exact seat agent name>",
      "focus": "security & permissions",
      "instructions": "benched: this change introduces no mutation, API, or auth path",
      "applicable": false
    }
  ],
  "artifacts": [],
  "notes_for_next_agent": "<anything every seat should know that does not fit shared_context>"
}
```

`status` is `success` when the split itself is complete and covers every seat
exactly once. `shared_context` is required — an empty context pack is a failed
split.
