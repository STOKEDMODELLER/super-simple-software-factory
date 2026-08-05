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
