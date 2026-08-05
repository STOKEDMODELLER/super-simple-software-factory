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
coverage against the real change, fix weak or misassigned briefs, and emit the
final version).

Either way: read `<context_handoff_dir>/plan.md` and inspect the changed files
on disk first. Every seat named in `prompt` gets exactly one assignment.

## Report

Respond with ONLY valid JSON matching `CouncilSplitOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence: how the review was divided and why>",
  "assignments": [
    {
      "seat": "<exact seat agent name from the task>",
      "focus": "<the dimension this seat owns, e.g. plan-conformance>",
      "instructions": "<concrete: files to read first, claims to distrust, evidence that settles it>"
    }
  ],
  "artifacts": [],
  "notes_for_next_agent": "<anything every seat should know before reviewing>"
}
```

`status` is `success` when the split itself is complete and covers every seat exactly once.
