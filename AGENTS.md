# Operating rules for agents in this repo

## SSSF factory — you are the operator, not the worker

This repo has the Super Simple Software Factory stamped into it (`adws/`,
`justfile`). When the user asks for scouting, planning, building, reviewing,
documenting, or types a `just …` line, **execute the corresponding recipe via
bash** — do not perform the work directly in this session:

| The user asks to…                       | Run                                   |
|-----------------------------------------|---------------------------------------|
| find / locate / explore (read-only)     | `just scout "<their request>"`        |
| plan a feature or change                | `just plan "<their request>"`         |
| plan and implement                      | `just plan-build "<their request>"`   |
| plan, implement, and test               | `just sdlc "<their request>"`         |
| full chain incl. review + docs          | `just simple-sdlc "<their request>"`  |
| one-off question to a specific agent    | `just prompt "<request>" --agent <n>` |
| see recent runs / a run's phases        | `just sessions` / `just phases <id>`  |

Why: a factory run mints a session, enforces write boundaries, checks gates,
and streams every event to `adws/adw_data/sssf.db` where the trace UI shows it.
Work you do directly in-session is invisible to all of that.

After launching a recipe, relay its output (the `adw_id` banner, gate results,
final status) back to the user. If a run fails, read the traceback and the
trace (`just phases <adw_id>`, `just tail <adw_id>`) before proposing fixes.

Do answers-in-chat only for questions *about* the factory or the repo that
need no agent run (e.g. "what does the reviewer do?", "which model is the
planner on?").

## Guardrails

- Never edit `adws/adw_modules/`, `adws/adw_sssf_config/`, or `adws/adw_*.py`
  unless the user explicitly asks — that is the factory's machinery.
- Prompts live in `adws/adw_data/prompt_engineering/<agent>/` and are the
  user's to tune; edit them there when asked, never inside `.claude/skills/`.

## Long-running chains: launch detached, then poll

Your bash tool has a ~120 second timeout. Build/test chains run far longer.
NEVER run `just plan-build`, `just sdlc`, `just simple-sdlc`, or any
`adw_build*` / `adw_*quality*` workflow in the foreground — the timeout will
cut your wait while the run continues underneath, and it will look failed
when it is not. Do NOT relaunch it.

Instead:

1. Before launching anything, check for a live duplicate:
   `just sessions` (a running session with the same request = do not relaunch).
2. Launch detached:
   `nohup just sdlc "<request>" > /tmp/sssf-run-$$.log 2>&1 & disown`
3. Wait ~5s, then read the adw_id from the log:
   `sleep 5; grep -m1 'adw_id' /tmp/sssf-run-*.log`
   Report the adw_id to the user immediately.
4. Poll progress with `just phases <adw_id>` / `just tail <adw_id>` — short
   commands that never hit the timeout. Report status when asked or when the
   session row shows success/fail.

Short read-only runs (`just scout`, `just prompt`, `just sessions`) may run in
the foreground; a `just plan` at high thinking can take minutes — detach it too.
