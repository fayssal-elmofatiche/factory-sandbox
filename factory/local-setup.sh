#!/usr/bin/env bash
# Create a fully local copy of this sandbox: no GitHub, issues in kata.
#
#   factory/local-setup.sh <target-dir> [kata-project-name]
#
# Makes <target-dir> (a working checkout) and <target-dir>.git (a bare
# repository that plays the part of `origin`), switches the factory to
# `"tracker": "kata"` and binds the checkout to a kata project.
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: factory/local-setup.sh <target-dir> [kata-project-name]" >&2; exit 2; }
command -v kata >/dev/null || { echo "kata is not installed or not on PATH" >&2; exit 1; }

SRC="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
mkdir -p "$(dirname "$1")"
TARGET="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
ORIGIN="$TARGET.git"
PROJECT="${2:-$(basename "$TARGET")}"
BASE="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['base_branch'])" "$SRC/factory/config.json")"

[ ! -e "$TARGET" ] || { echo "$TARGET already exists" >&2; exit 1; }
[ ! -e "$ORIGIN" ] || { echo "$ORIGIN already exists" >&2; exit 1; }

git init --quiet --bare --initial-branch="$BASE" "$ORIGIN"
git -C "$SRC" push --quiet "$ORIGIN" "HEAD:refs/heads/$BASE"
git clone --quiet "$ORIGIN" "$TARGET"
cd "$TARGET"

# Switch the tracker without reformatting the file.
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("factory/config.json")
s = p.read_text()
if '"tracker"' not in s:
    s = s.replace("{\n", '{\n  "tracker": "kata",\n', 1)
else:
    cfg = json.loads(s)
    s = s.replace(f'"tracker": "{cfg["tracker"]}"', '"tracker": "kata"', 1)
assert json.loads(s)["tracker"] == "kata"
p.write_text(s)
PY

kata init --project "$PROJECT" >/dev/null
git add factory/config.json .kata.toml .gitignore
git commit --quiet -m "Run the factory in local mode (tracker: kata)"
git push --quiet origin "$BASE"

cat <<MSG
Local sandbox ready.
  checkout: $TARGET
  origin:   $ORIGIN
  kata:     project $PROJECT

Next:
  cd $TARGET
  kata create "Short imperative title" --body "What you want built."
  kata label add <ref> factory
  claude      # then: /factory run
MSG
