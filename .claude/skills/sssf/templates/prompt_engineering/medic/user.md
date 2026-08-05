# Medic Task

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

`prompt` names the phase that failed, carries its error verbatim, and names the
file your written diagnosis must land at. `previous_envelope` is the last
successful envelope before the failure, when one exists.

1. Verify the error against the actual state of the repo and the session
   directory — do not diagnose from the message alone.
2. Establish the root cause and pick ONE recommendation: `retry`, `revise`,
   or `abort` (the system prompt defines when each applies).
3. Write your full investigation to the file the task names; list it in
   `artifacts`.

## Report

Respond with ONLY valid JSON matching `DiagnosisOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence: what broke>",
  "cause": "<the root cause, one or two sentences, grounded in what you verified>",
  "recommendation": "retry",
  "fix_instructions": "<concrete steps: what the retry/revision/engineer must do>",
  "artifacts": ["<the diagnosis file path the task named>"],
  "notes_for_next_agent": "<what whoever acts on this must know first>"
}
```

`status` is `success` when the diagnosis itself completed — a diagnosed failure is a successful diagnosis. `recommendation` must be exactly one of `retry`, `revise`, `abort`.
