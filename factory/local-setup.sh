#!/usr/bin/env bash
# Make another sandbox from this checkout: a copy that runs the factory locally.
#
#   factory/local-setup.sh <target-dir> [--tracker files|kata] [--project <kata-project>]
#
# You don't need this to run the factory: a clone sets itself up on the first
# `./fx run` (or `./fx setup`). Use it for a second, separate copy, for example
# to rehearse without touching your own.
#
# Clones this checkout's current commit into <target-dir> as `main`, then runs
# `./fx setup` there: <target-dir>.git, a repository on disk, plays `origin`,
# and the demo issues go into the tracker.
#
#   files (default)  issues are Markdown files in factory/issues/; nothing to install
#   kata             issues live in kata; needs `kata` on PATH
set -euo pipefail

usage() { echo "usage: factory/local-setup.sh <target-dir> [--tracker files|kata] [--project <kata-project>]" >&2; exit 2; }

TARGET_ARG="" TRACKER="files" PROJECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tracker) [ $# -ge 2 ] || usage; TRACKER="$2"; shift 2 ;;
    --project) [ $# -ge 2 ] || usage; PROJECT="$2"; shift 2 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [ -z "$TARGET_ARG" ] || usage; TARGET_ARG="$1"; shift ;;
  esac
done
[ -n "$TARGET_ARG" ] || usage
case "$TRACKER" in files|kata) ;; *) usage ;; esac
if [ "$TRACKER" = kata ]; then
  command -v kata >/dev/null || { echo "kata is not installed or not on PATH" >&2; exit 1; }
fi

SRC="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
mkdir -p "$(dirname "$TARGET_ARG")"
TARGET="$(cd "$(dirname "$TARGET_ARG")" && pwd)/$(basename "$TARGET_ARG")"
PROJECT="${PROJECT:-$(basename "$TARGET")}"
BASE="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['base_branch'])" "$SRC/factory/config.json")"

[ ! -e "$TARGET" ] || { echo "$TARGET already exists" >&2; exit 1; }
[ ! -e "$TARGET.git" ] || { echo "$TARGET.git already exists" >&2; exit 1; }

git clone --quiet --no-checkout "$SRC" "$TARGET"
cd "$TARGET"
git checkout --quiet -B "$BASE" "$(git -C "$SRC" rev-parse HEAD)"
# A commit needs an author; fall back to a local one on a machine with none set.
git config user.name >/dev/null || git config user.name "${USER:-factory}"
git config user.email >/dev/null || git config user.email "${USER:-factory}@localhost"

if [ "$TRACKER" = kata ]; then
  # Switch the tracker without reformatting the file.
  python3 - <<'PY'
import json, pathlib
p = pathlib.Path("factory/config.json")
s = p.read_text()
if '"tracker"' not in s:
    s = s.replace("{\n", '{\n  "tracker": "kata",\n', 1)
else:
    s = s.replace('"tracker": "%s"' % json.loads(s)["tracker"], '"tracker": "kata"', 1)
assert json.loads(s)["tracker"] == "kata"
p.write_text(s, newline="\n")  # LF on Windows too
PY
  # kata refuses to parse a remote that is a path, so bind the project while
  # the checkout has no remote.
  git remote remove origin
  kata init --project "$PROJECT" >/dev/null
  git add factory/config.json .kata.toml .gitignore
  git commit --quiet -m "Run the factory with the kata tracker"
fi

./fx setup >/dev/null

if [ "$TRACKER" = kata ]; then
  python3 - <<'PY'
import pathlib, re, subprocess
for path in sorted(pathlib.Path("factory/demo-issues").glob("*.md")):
    head, _, body = path.read_text()[4:].partition("\n---\n")
    meta = dict(re.findall(r"^(\w+):[ \t]*(.*)$", head, re.M))
    out = subprocess.run(["kata", "create", meta["title"], "--body", body.strip(), "--agent"],
                         check=True, capture_output=True, text=True).stdout
    ref = re.search(r"OK create (\S+)", out).group(1)
    for name in filter(None, (l.strip() for l in meta.get("labels", "").split(","))):
        subprocess.run(["kata", "label", "add", ref, name, "-q"], check=True, capture_output=True)
PY
fi

cat <<MSG
Local sandbox ready (tracker: $TRACKER).
  checkout: $TARGET
  origin:   $TARGET.git

Issues:
$(python3 factory/bin/job issues | sed 's/^/  /')

Next:
  cd $TARGET
  ./fx doctor   # check this machine can run the factory
  ./fx deck     # the presentation
  ./fx up       # the dashboard
  ./fx run      # Claude Code on /factory run
MSG
