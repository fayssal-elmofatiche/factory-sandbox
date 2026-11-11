#!/usr/bin/env bash
# The repo's checks. Exit 0 only when the branch is fit to review.
#
# The gate runs this script from the main checkout with the job's worktree as
# the working directory. Keep it fast, deterministic and non-interactive: no
# watch modes, no prompts, nothing that rewrites files.
set -euo pipefail

FACTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# `-o addopts=` drops any extra pytest options the working tree sets.
PYTEST=(python3 -m pytest -q -p no:cacheprovider -o addopts=)

ruff check .

out="$("${PYTEST[@]}" 2>&1)" || { echo "$out"; exit 1; }
echo "$out"

# A test that doesn't run proves nothing: skips and expected failures fail the checks.
if echo "$out" | tail -n 1 | grep -Eq 'skipped|xfailed|xpassed|deselected'; then
  echo "checks: tests were skipped, deselected or marked as expected failures. Every test must run and pass." >&2
  exit 1
fi

# The branch may not have fewer tests than the base branch.
BASE="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['base_branch'])" "$FACTORY/config.json")"
if git rev-parse --verify --quiet "origin/$BASE^{commit}" >/dev/null; then
  count() { (cd "$1" && "${PYTEST[@]}" --collect-only 2>/dev/null | grep -c '::' || true); }
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  git archive "origin/$BASE" | tar -x -C "$tmp"
  here="$(count .)"
  there="$(count "$tmp")"
  if [ "$here" -lt "$there" ]; then
    echo "checks: this tree collects $here tests, origin/$BASE collects $there. Removing tests needs a person's decision." >&2
    exit 1
  fi
fi
