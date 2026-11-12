#!/usr/bin/env bash
# Create a fully local copy of this sandbox: no GitHub, no pull requests.
#
#   factory/local-setup.sh <target-dir> [--tracker files|kata] [--project <kata-project>]
#
# Makes <target-dir> (a working checkout) and <target-dir>.git (a bare
# repository that plays the part of `origin`), switches the factory to a local
# tracker and adds three demo issues, the first one labeled `factory`.
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
ORIGIN="$TARGET.git"
PROJECT="${PROJECT:-$(basename "$TARGET")}"
BASE="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['base_branch'])" "$SRC/factory/config.json")"

[ ! -e "$TARGET" ] || { echo "$TARGET already exists" >&2; exit 1; }
[ ! -e "$ORIGIN" ] || { echo "$ORIGIN already exists" >&2; exit 1; }

git init --quiet --bare "$ORIGIN"
git -C "$ORIGIN" symbolic-ref HEAD "refs/heads/$BASE"
git -C "$SRC" push --quiet "$ORIGIN" "HEAD:refs/heads/$BASE"
git clone --quiet "$ORIGIN" "$TARGET"
cd "$TARGET"
# A commit needs an author; fall back to a local one on a machine with none set.
git config user.name >/dev/null || git config user.name "${USER:-factory}"
git config user.email >/dev/null || git config user.email "${USER:-factory}@localhost"

# Switch the tracker without reformatting the file.
TRACKER="$TRACKER" python3 - <<'PY'
import json, os, pathlib
tracker = os.environ["TRACKER"]
p = pathlib.Path("factory/config.json")
s = p.read_text()
if '"tracker"' not in s:
    s = s.replace("{\n", '{\n  "tracker": "%s",\n' % tracker, 1)
else:
    s = s.replace('"tracker": "%s"' % json.loads(s)["tracker"], '"tracker": "%s"' % tracker, 1)
assert json.loads(s)["tracker"] == tracker
p.write_text(s, newline="\n")  # LF on Windows too
PY
git add factory/config.json

if [ "$TRACKER" = kata ]; then
  # kata refuses to parse a remote that is a path, so bind the project while
  # the checkout has no remote.
  git remote remove origin
  kata init --project "$PROJECT" >/dev/null
  git remote add origin "$ORIGIN"
  git fetch --quiet origin
  git branch --quiet --set-upstream-to="origin/$BASE" "$BASE"
  git add .kata.toml .gitignore
fi
# A source that is already in this mode has nothing to commit.
git diff --cached --quiet || git commit --quiet -m "Run the factory in local mode (tracker: $TRACKER)"
git push --quiet origin "$BASE"

# Demo issues: the first is labeled `factory`, so the first run has work to do.
if [ "$TRACKER" = files ]; then
  mkdir -p factory/issues
  cp factory/demo-issues/*.md factory/issues/
else
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

# Remember this state, so `./fx reset` can return to it after a rehearsal.
if [ "$TRACKER" = files ]; then ./fx baseline >/dev/null; fi

cat <<MSG
Local sandbox ready (tracker: $TRACKER).
  checkout: $TARGET
  origin:   $ORIGIN

Issues:
$(factory/bin/job issues | sed 's/^/  /')

Next:
  cd $TARGET
  ./fx doctor   # check this machine can run the factory
  ./fx up       # dashboard and presentation
  ./fx run      # Claude Code on /factory run
MSG
