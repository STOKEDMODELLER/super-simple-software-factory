# Plan: scripts/repo_stats.sh (round 2 — council-directed revision)

## Goal

Create a small, executable Bash script at `scripts/repo_stats.sh` that prints:
1. The number of git-tracked files in the repo.
2. The total line count across all git-tracked Python (`*.py`) files.

## Why this is round 2

The round-1 build (`git ls-files -- '*.py' | while IFS= read -r file; do wc -l
< "$file"; done | awk '{sum += $1} END {print sum + 0}'`) was rejected by the
council on 4 blocking points, all proven with reproducers in
`context_handoff/council/feedback_r2.md` and the individual seat reviews
(`seat-fable_r1.md`, `seat-opus_r1.md`, `seat-terra_r1.md`). This plan
replaces the implementation to close every one of them. The other three
seats (`seat-sol`, `seat-luna`, `seat-sonnet`) approved round 1 and raised no
blockers — their notes on minimalism, test adequacy, and plan-conformance
still apply and should not be regressed by this fix.

## Blockers to close (from council round 1)

1. **[seat-fable] Unsafe path handling.** `git ls-files -- '*.py'` piped into
   a `while IFS= read -r file` loop breaks on filenames Git quotes: non-ASCII
   bytes (e.g. `über.py`) and embedded newlines both cause
   `"\303\274ber.py": No such file or directory`-style failures, because the
   loop reads the *quoted display string*, not the real path. Proven via a
   throwaway repo reproducer — exit 1, zero stdout, for both cases.
   **Fix:** use NUL-delimited output (`-z`) end to end, consumed with
   `xargs -0`, never a `read` loop over `git ls-files` text output.

2. **[seat-fable] Duplicate index stages.** `git ls-files` (and `git ls-files
   -- '*.py'`) emit one line per unmerged/conflicted index stage, so a file
   with a merge conflict is counted 2–3 times in "Tracked files" and its
   working-tree content (conflict markers included) is summed once per
   stage in "Python lines." Proven: a conflicted `conflict.py` inflated
   `Tracked files` from 3 to 5 and `Python lines` from 7 to 17.
   **Fix:** add `--deduplicate` (git ≥ 2.31; this repo runs git 2.54, see
   Verification below) to both `git ls-files` invocations so each tracked
   path is counted exactly once regardless of conflict state.

3. **[seat-opus] Masked read failures — the most severe blocker.** In the
   round-1 loop, `set -e` does not survive into a `while` loop's exit status
   (the loop's status is only that of its *last* iteration), and it also does
   not survive into `$( … )` under bash 3.2 (macOS system bash, this
   script's actual shebang target) since bash 3.2 has no
   `inherit_errexit`. Net effect: breaking (permission-denied, deleted) any
   `.py` file **except the last one in sort order** produced a silently
   wrong (undercounted) `Python lines` value with **exit 0** — the dangerous
   case is the common one. Reproduced for both a `chmod 000`'d middle file
   and a tracked-but-deleted middle file.
   **Fix:** replace the loop entirely with a single pipeline whose failure
   `pipefail` can observe directly: `git ls-files -z --deduplicate -- '*.py'
   | xargs -0 cat | wc -l`. If `cat` fails to read any file in its batch
   (permission denied, missing, etc.), `xargs` exits non-zero, `pipefail`
   propagates that into the `python_lines=$( … )` assignment's exit status,
   and the outer `set -e` aborts the script **before either `echo` runs** —
   verified in this exact form against both the permission-denied-middle-file
   and deleted-middle-file reproductions (see Verification below): both now
   print nothing and exit 1, matching seat-opus's explicit requirement that
   "aborting with no output is acceptable; suppressing errors and printing a
   partial count is not."
   This also fixes seat-opus's minor, non-blocking note about the empty `cd`
   on a `git rev-parse` failure outside a repo: resolve to a variable first
   (`root=$(git rev-parse --show-toplevel); cd "$root"`) so a failure at that
   step aborts immediately via `set -e` instead of silently `cd`-ing into `""`
   (a bash no-op) and continuing in the wrong directory.

4. **[seat-terra] Missing semantics documentation.** The script's Python-line
   count is a hybrid: file *membership* is index-based (`git ls-files`), but
   line *content* is read from the working tree. This means an edit to an
   already-tracked `.py` file changes the reported count before it's staged,
   while a new untracked `.py` file is excluded entirely. This distinction
   was implemented correctly in round 1 but never stated anywhere.
   **Fix:** add one short comment above the `python_lines` line stating this
   working-tree-vs-index behavior, per seat-terra's explicit preference to
   keep the two-line stdout contract unchanged and put the explanation in a
   comment instead.

## Final implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

# Distinct tracked paths; --deduplicate collapses multi-stage entries from
# an unmerged/conflicted path down to one so it isn't counted 2-3x.
tracked_files=$(git ls-files --deduplicate | wc -l | tr -d ' ')

# Sums working-tree line counts for Git-indexed *.py paths only: path
# membership is index-based, so editing a tracked .py file moves this count
# before staging, and untracked .py files are excluded. NUL-delimited
# (-z / xargs -0) throughout so paths with spaces, newlines, or non-ASCII
# bytes are never mis-split by shell quoting; piping straight into wc -l
# (no per-file loop) lets `pipefail` see and abort on any unreadable or
# missing tracked .py file instead of silently undercounting.
python_lines=$(git ls-files -z --deduplicate -- '*.py' | xargs -0 cat | wc -l | tr -d ' ')

echo "Tracked files: $tracked_files"
echo "Python lines:  $python_lines"
```

## Verification performed during planning (throwaway repos, not committed)

All four fixes were built and verified live in scratch repos before writing
this plan, using the exact final pipeline above:

- **Baseline correctness:** 3 `.py` files (3+2+1 lines) → `Tracked files: 3`
  / `Python lines: 6`, exit 0.
- **Fail-closed, middle file broken (seat-opus's case A/C, the one that
  masked the bug in round 1):** `chmod 000` on the *middle* `.py` file (not
  the last) → stderr `cat: mmm.py: Permission denied`, **no stdout, exit 1**.
  Tracked-but-deleted middle file → same shape, `cat: mmm.py: No such file or
  directory`, no stdout, exit 1. Both now behave like the "acceptable" case
  the round-1 script only achieved by accident when the *last* file broke.
- **Unicode / newline / space filenames (seat-fable's case):** a repo with
  `über.py` (3 lines), a file whose name contains a literal newline (2
  lines), and `has space.py` (1 line) → `Tracked files: 4` / `Python lines: 6`
  (matches manual sum), exit 0. No quoting-related failures.
- **Conflicted index stages (seat-fable's case):** merged two branches with
  conflicting edits to `conflict.py` (plus one committed `has space.py`) and
  left the merge unresolved. `git ls-files -s` showed 3 raw stage entries for
  `conflict.py`. The script printed `Tracked files: 2` (correctly
  deduplicated: `conflict.py` + `has space.py`) and `Python lines: 8`
  (7 working-tree lines in the conflicted file, including conflict markers,
  counted once + 1 line in `has space.py`), exit 0 — no multi-stage
  inflation.
- **Outside a git repo:** `git rev-parse --show-toplevel` fails, the `root=`
  assignment fails, `set -e` aborts immediately — single `fatal: not a git
  repository …` message, no doubled output, exit 128.
- **git version dependency:** `--deduplicate` requires git ≥ 2.31; this
  repository's environment runs git 2.54.0, confirmed via `git --version`.

## Steps for the builder

1. Create the `scripts/` directory (does not exist yet).
2. Write `scripts/repo_stats.sh` with exactly the contents in "Final
   implementation" above.
3. `chmod +x scripts/repo_stats.sh`.
4. Re-run the smoke checks from the round-1 test-adequacy review (seat-sol,
   approved, no blockers): run from repo root and from a nested directory,
   confirm two lines of output, both non-negative integers, and that they
   match an independent cross-check (`git ls-files --deduplicate | wc -l`;
   `git ls-files -z --deduplicate -- '*.py' | xargs -0 cat | wc -l`). This
   repo currently has 158 git-tracked files; the Python line total will
   include the newly added `scripts/repo_stats.sh`'s own tracked-file count
   once committed (it is Bash, not Python, so it does not add to the Python
   line total).
5. Optionally re-run a couple of the round-2 hostile-input checks above
   (broken middle `.py` file, unicode filename) in a scratch repo if you
   want independent confirmation before handing back to the council — not
   required, since the checks in this plan were already run against the
   exact final pipeline.

## Acceptance criteria (updated)

- `scripts/repo_stats.sh` exists, is executable, and runs successfully from
  the repo root and from a subdirectory.
- Output is exactly two lines: tracked file count and total Python line
  count, both non-negative integers, in the same `Tracked files: N` /
  `Python lines:  M` format as round 1 (unchanged, per seat-terra's
  preference to document semantics via comment rather than change output).
- Both `git ls-files` invocations use `--deduplicate` so conflicted/unmerged
  paths are never multi-counted.
- The Python-line pipeline uses NUL-delimited paths (`-z` / `xargs -0`)
  throughout — no `read` loop over `git ls-files` text output anywhere.
- Any unreadable or missing tracked `.py` file — regardless of its position
  in the file list — causes the script to abort with **no stdout** and a
  non-zero exit code. Silently printing an undercount is not acceptable.
- A one-line comment documents that the Python line count reflects
  working-tree content for index-selected paths (index-based membership,
  working-tree-based line counts).
- `git rev-parse --show-toplevel` failure aborts immediately via `set -e`
  with a single clear message, rather than proceeding with an empty `cd`.
- No unnecessary flags, options, or dependencies beyond `git`, `wc`, `xargs`,
  `cat`, `tr` — all standard on macOS/Linux. (`awk` from round 1 is no longer
  needed since the loop it fed is gone.)
