# Plan: scripts/repo_stats.sh

## Goal

Create `scripts/repo_stats.sh`, a minimal executable bash script that prints:
1. The number of git-tracked files in the repo.
2. The total line count across all Python (`*.py`) files in the repo.

## Context

- `scripts/` does not exist yet at the repo root and must be created.
- No existing shell script conventions in this repo to follow (no other scripts under `scripts/`, no shebang/style precedent found).
- Keep the script minimal — no argument parsing, no config, no error-handling beyond what's needed for correctness.

## Steps

1. Create the `scripts/` directory.
2. Create `scripts/repo_stats.sh` with:
   - `#!/usr/bin/env bash` shebang.
   - `set -euo pipefail` for safety (minimal, standard).
   - Use `git ls-files` to count git-tracked files: `git ls-files | wc -l`.
   - Use `git ls-files -- '*.py'` piped to `xargs wc -l` (or `cat | wc -l`) to sum line counts of tracked Python files only (tracked files is the natural, dynamic scope per the scout's note — no hardcoded paths).
   - Print two clearly labeled lines, e.g.:
     ```
     Tracked files: <N>
     Python lines: <N>
     ```
3. Make the script executable: `chmod +x scripts/repo_stats.sh`.
4. Run the script from the repo root to confirm it executes without error and prints sane numbers.

## Implementation sketch

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tracked_files=$(git ls-files | wc -l | tr -d ' ')
python_lines=$(git ls-files -- '*.py' | xargs cat 2>/dev/null | wc -l | tr -d ' ')

echo "Tracked files: ${tracked_files}"
echo "Python lines: ${python_lines}"
```

Notes:
- `cd` to repo root via `git rev-parse --show-toplevel` so the script works regardless of the caller's cwd.
- Use `git ls-files -- '*.py' | xargs cat` rather than `wc -l` directly on the file list, so that zero matching files doesn't error `wc` (an empty `xargs cat` just produces empty input, giving a count of 0).
- No need to handle non-git repos or missing `git` binary — this script's purpose only makes sense inside this git repo.

## Verification

- `bash -n scripts/repo_stats.sh` to check syntax.
- Run `./scripts/repo_stats.sh` from repo root and confirm output has two lines with non-negative integers.
- Confirm the file is executable (`ls -l scripts/repo_stats.sh` shows `x` bits) and has the correct shebang.

## Out of scope

- No CLI flags, no JSON output, no support for arbitrary target directories.
- No test suite — this is a one-shot utility script.
