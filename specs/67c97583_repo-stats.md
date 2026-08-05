# Plan: scripts/repo_stats.sh

## Goal

Create a small, executable Bash script at `scripts/repo_stats.sh` that prints:
1. The number of git-tracked files in the repo.
2. The total line count across all Python (`*.py`) files in the repo.

## Context

- `scripts/` does not exist yet and must be created.
- `scripts/repo_stats.sh` does not exist yet.
- Keep the script minimal — no argument parsing, no config, no error-handling
  beyond what's needed for correctness (e.g. handling the case of zero `.py`
  files so `wc -l` doesn't error out on an empty file list).

## Steps

1. Create the `scripts/` directory.
2. Create `scripts/repo_stats.sh` with:
   - A `#!/usr/bin/env bash` shebang.
   - `set -euo pipefail` for safety.
   - Git-tracked file count: `git ls-files | wc -l` (trim whitespace).
   - Python line count: sum lines across all git-tracked `*.py` files using
     `git ls-files -- '*.py'` piped to `xargs wc -l` (or `wc -l` with a
     fallback to `0` when there are no Python files, to avoid `wc: no
     input files` errors).
   - Print two clear lines of output, e.g.:
     ```
     Tracked files: <N>
     Python lines:  <M>
     ```
3. Make the script executable: `chmod +x scripts/repo_stats.sh`.
4. Verify by running `./scripts/repo_stats.sh` from the repo root and
   confirming it prints two sane numbers with no errors.

## Suggested implementation

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tracked_files=$(git ls-files | wc -l | tr -d ' ')

py_files=$(git ls-files -- '*.py')
if [ -n "$py_files" ]; then
  python_lines=$(echo "$py_files" | xargs wc -l | tail -1 | awk '{print $1}')
else
  python_lines=0
fi

echo "Tracked files: $tracked_files"
echo "Python lines:  $python_lines"
```

Note: `xargs wc -l | tail -1` grabs the "total" line when there are multiple
files; if there's exactly one Python file, `wc -l` won't print a "total" line
and `tail -1` still correctly returns the single count line — but the awk
`$1` extraction handles both cases uniformly.

## Acceptance criteria

- `scripts/repo_stats.sh` exists, is executable, and runs successfully from
  the repo root.
- Output is exactly two lines: tracked file count and total Python line
  count, both non-negative integers.
- Script works correctly even if run from a subdirectory (uses
  `git rev-parse --show-toplevel` to cd to repo root first).
- No unnecessary flags, options, or dependencies beyond `git`, `wc`, `xargs`,
  `awk` — all standard on macOS/Linux.
