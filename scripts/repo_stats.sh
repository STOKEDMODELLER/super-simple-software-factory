#!/usr/bin/env bash
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tracked_files=$(git ls-files | wc -l | tr -d ' ')
python_lines=$(git ls-files -- '*.py' | xargs cat 2>/dev/null | wc -l | tr -d ' ')

echo "Tracked files: ${tracked_files}"
echo "Python lines: ${python_lines}"
