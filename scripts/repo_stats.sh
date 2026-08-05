#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tracked_files=$(git ls-files | wc -l | tr -d ' ')
python_lines=$(git ls-files -- '*.py' | while IFS= read -r file; do wc -l < "$file"; done | awk '{sum += $1} END {print sum + 0}')

echo "Tracked files: $tracked_files"
echo "Python lines:  $python_lines"
