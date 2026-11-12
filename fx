#!/usr/bin/env python3
"""fx: one entry point for running and showing the software factory.

A thin wrapper. It starts the dashboard, opens the presentation, checks the
prerequisites and resets a demo sandbox. Everything about jobs still goes
through factory/bin/job; fx adds no factory logic of its own.

Run `fx` for the list of commands and `fx <command> --help` for one of them.
"""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parent
FACTORY = REPO / "factory"
sys.path.insert(0, str(FACTORY / "bin"))
import portable  # noqa: E402  (lives next to the factory's scripts)

JOB = FACTORY / "bin" / "job"
DECK = REPO / "docs" / "presentation.html"
TAG = "demo-start"  # the starting point `fx reset` returns to; moves when the setup changes
ORIGIN_TAG = "demo-origin"  # the setup the sandbox was created with; `fx reset --setup` returns to it
# The setup: what you change to run an experiment. Everything else a run produces is work.
SETUP = ["factory/config.json", "factory/checks.sh", ".claude", "AGENTS.md"]
MODELS = {"haiku", "sonnet", "opus", "fable"}
MIN_CLAUDE = (2, 1, 257)


def die(msg):
    print(f"fx: {msg}", file=sys.stderr)
    sys.exit(1)


def sh(*cmd, cwd=REPO, check=False):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        die(f"`{' '.join(cmd)}` failed: {(res.stderr or res.stdout).strip()}")
    return res


def config():
    try:
        return json.loads((FACTORY / "config.json").read_text())
    except (OSError, ValueError) as e:
        die(f"cannot read factory/config.json: {e}")


def tracker():
    return config().get("tracker", "github")


def state_dir():
    """Where fx keeps its own state: inside .git, so it is never committed."""
    git_dir = sh("git", "rev-parse", "--absolute-git-dir", check=True).stdout.strip()
    d = Path(git_dir) / "fx"
    d.mkdir(exist_ok=True)
    return d


def job(*args):
    """Run factory/bin/job and pass its output and exit code through."""
    return subprocess.run(portable.script(JOB, *args), cwd=REPO).returncode


# ---- doctor ----------------------------------------------------------------


def cmd_doctor():
    rows = []  # (level, name, detail); level is ok, warn or MISSING

    def have(tool):
        return shutil.which(tool) is not None

    v = sys.version_info
    rows.append(("ok" if v >= (3, 10) else "MISSING", "Python 3.10+", f"{v.major}.{v.minor}.{v.micro}"))
    rows.append(("ok" if have("git") else "MISSING", "git", "" if have("git") else "install git"))
    pytest = sh(sys.executable, "-m", "pytest", "--version")
    rows.append(("ok" if pytest.returncode == 0 else "MISSING", "pytest",
                 (pytest.stdout or pytest.stderr).strip().splitlines()[0] if pytest.returncode == 0
                 else "python3 -m pytest does not run"))
    rows.append(("ok" if have("ruff") else "MISSING", "ruff", "" if have("ruff") else "pipx install ruff"))

    if have("claude"):
        out = sh("claude", "--version").stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        ver = tuple(int(x) for x in m.groups()) if m else None
        ok = ver is not None and ver >= MIN_CLAUDE
        rows.append(("ok" if ok else "MISSING", "Claude Code 2.1.257+",
                     out.strip() if ok else f"found {out.strip() or 'no version'}; update Claude Code"))
    else:
        rows.append(("MISSING", "Claude Code 2.1.257+", "install Claude Code and sign in"))

    t = tracker()
    rows.append(("ok", "tracker", t))
    if t == "github":
        if not have("gh"):
            rows.append(("MISSING", "gh", "install the GitHub CLI"))
        else:
            auth = sh("gh", "auth", "status").returncode == 0
            rows.append(("ok" if auth else "MISSING", "gh signed in", "" if auth else "run: gh auth login"))
    elif t == "kata":
        rows.append(("ok" if have("kata") else "MISSING", "kata",
                     "" if have("kata") else "go install go.kenn.io/kata/cmd/kata@latest"))

    remote = sh("git", "remote", "get-url", "origin")
    rows.append(("ok" if remote.returncode == 0 else "MISSING", "git remote origin",
                 remote.stdout.strip() if remote.returncode == 0 else "the factory branches every job from origin"))

    tools = [("agent-browser", "needed for issues that change the web page")]
    bash = "bash"
    if portable.WINDOWS:
        # checks.sh and the agents' hooks run in Git Bash and call `python3` there. Windows'
        # own python3 is often only a shortcut to the Microsoft Store, which runs nothing.
        try:
            bash = portable.bash()
            rows.append(("ok", "Git Bash", bash))
        except SystemExit:
            bash = None
            rows.append(("MISSING", "Git Bash", "install Git for Windows: https://git-scm.com/download/win"))
        if bash:
            py = sh(bash, "-c", "python3 -c 'import sys; print(sys.version_info >= (3, 10), sys.version.split()[0])'")
            ok = py.returncode == 0 and py.stdout.startswith("True")
            rows.append(("ok" if ok else "MISSING", "python3 in Git Bash",
                         py.stdout.split()[-1] if ok else "install one: uv python install 3.14 --default"))
    else:  # Windows agents use netstat and taskkill instead
        tools.append(("lsof", "used to stop a job's dev server"))
    for tool, why in tools:
        rows.append(("ok" if have(tool) else "warn", tool, "" if have(tool) else why))

    if bash:
        checks = sh(bash, str(FACTORY / "checks.sh"))
        tail = (checks.stdout or checks.stderr).strip().splitlines()
        rows.append(("ok" if checks.returncode == 0 else "MISSING", "factory/checks.sh passes",
                     tail[-1] if tail else ""))
    else:
        rows.append(("MISSING", "factory/checks.sh passes", "needs Git Bash"))

    width = max(len(name) for _, name, _ in rows)
    for level, name, detail in rows:
        print(f"  {level:<8}{name:<{width}}  {detail}".rstrip())
    missing = [name for level, name, _ in rows if level == "MISSING"]
    if missing:
        print(f"\n{len(missing)} thing(s) to fix before the factory can run: {', '.join(missing)}.")
        return 1
    print("\nReady. Next: ./fx up, then ./fx run.")
    return 0


# ---- dashboard and presentation --------------------------------------------


def dashboard_state():
    path = state_dir() / "dashboard.json"
    if not path.exists():
        return None
    try:
        st = json.loads(path.read_text())
        if not portable.alive(st["pid"]):
            raise ValueError("the dashboard is gone")
        return st
    except (ValueError, KeyError, OSError):
        path.unlink(missing_ok=True)
        return None


def free_port(start=8765):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    die(f"no free port between {start} and {start + 49}")


def open_url(url):
    """Open a URL in the browser; under WSL, hand it to Windows."""
    if portable.wsl():
        for tool in ("wslview", "explorer.exe"):
            if shutil.which(tool):
                subprocess.run([tool, url], capture_output=True)
                return
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass  # the URL is printed anyway


def deck_url():
    if portable.wsl() and shutil.which("wslpath"):
        win = sh("wslpath", "-w", str(DECK)).stdout.strip()
        if win:
            return win
    return DECK.as_uri()


def cmd_up(args):
    st = dashboard_state()
    if not st:
        port = free_port()
        log = open(state_dir() / "dashboard.log", "w")
        proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=FACTORY, stdout=log, stderr=log, **portable.own_group())
        st = {"pid": proc.pid, "port": port}
        (state_dir() / "dashboard.json").write_text(json.dumps(st))
        time.sleep(0.4)
        if proc.poll() is not None:
            die("the dashboard server stopped at once; see " + str(state_dir() / "dashboard.log"))
    dash = f"http://127.0.0.1:{st['port']}/dashboard.html"
    print(f"dashboard     {dash}")
    if DECK.exists():
        print(f"presentation  {DECK}")
    if "--no-open" not in args:
        open_url(dash)
        if DECK.exists():
            open_url(deck_url())
    print("\nNext, in this folder:  ./fx run     (opens Claude Code on /factory run)")
    print("Stop the dashboard:    ./fx down")
    return 0


def cmd_down():
    st = dashboard_state()
    if not st:
        print("the dashboard is not running")
        return 0
    portable.stop(st["pid"])
    (state_dir() / "dashboard.json").unlink(missing_ok=True)
    print(f"dashboard on port {st['port']} stopped")
    return 0


def cmd_deck():
    if not DECK.exists():
        die("docs/presentation.html is not in this checkout")
    print(DECK)
    open_url(deck_url())
    return 0


def cmd_run(args):
    if not shutil.which("claude"):
        die("Claude Code is not installed; run ./fx doctor")
    prompt = "/factory run" + (f" {args[0]}" if args else "")
    print(f"Starting Claude Code in {REPO} with: {prompt}")
    os.chdir(REPO)
    if portable.WINDOWS:  # Windows has no exec: run Claude Code and pass its exit code on
        return subprocess.run([shutil.which("claude"), prompt]).returncode
    os.execvp("claude", ["claude", prompt])


# ---- issues and jobs -------------------------------------------------------


def cmd_status():
    job("summary")
    print("\nNext run:")
    res = sh(*portable.script(JOB, "queue"))
    print(res.stdout.rstrip() or "  nothing is queued; label an issue with ./fx queue <issue>")
    if res.returncode:
        print(res.stderr.strip(), file=sys.stderr)
    return res.returncode


def cmd_watch():
    jobs = FACTORY / "jobs"
    print("Watching factory/jobs/ (Ctrl-C to stop)\n")
    seen = {}
    try:
        while True:
            files = sorted((p for p in jobs.rglob("*.md")), key=lambda p: p.stat().st_mtime) if jobs.exists() else []
            for p in files:
                mtime = p.stat().st_mtime
                if seen.get(p) != mtime:
                    verb = "new    " if p not in seen else "changed"
                    seen[p] = mtime
                    first = p.read_text(errors="replace").split("\n", 1)[0][:60]
                    print(f"{time.strftime('%H:%M:%S', time.localtime(mtime))}  {verb}  {p.relative_to(jobs).as_posix()}  {first}")
            time.sleep(1.5)
    except KeyboardInterrupt:
        print()
        return 0


# ---- a person's decisions --------------------------------------------------


def words(args, what):
    """The text for a decision: the rest of the command line, stdin when it is
    piped, or whatever the person writes in their editor."""
    if args:
        return " ".join(args)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        eof = "Ctrl-Z and Enter" if portable.WINDOWS else "Ctrl-D"
        print(f"{what} End with {eof} on an empty line.")
        return sys.stdin.read()
    import shlex
    import tempfile
    with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
        f.write(f"\n# {what}\n# Lines starting with # are ignored. Leave it empty to cancel.\n")
        path = f.name
    try:
        subprocess.run([*shlex.split(editor), path])
        lines = Path(path).read_text().splitlines()
    finally:
        os.unlink(path)
    return "\n".join(line for line in lines if not line.startswith("#")).strip()


def decide(verb, args, what):
    if not args:
        die(f"usage: fx {verb} <job> [words]")
    text = words(args[1:], what)
    if not text.strip():
        die("nothing written; nothing changed")
    code = subprocess.run(portable.script(JOB, verb, args[0]), cwd=REPO, input=text, text=True).returncode
    if code == 0 and verb == "feedback":
        print("Run ./fx run to put the factory back to work on it.")
    return code


def cmd_approve(args):
    yes = "--yes" in args
    args = [a for a in args if a != "--yes"]
    if len(args) != 1:
        die("usage: fx approve <job> [--yes]")
    if job("show", args[0]):
        return 1
    if not yes:
        if not sys.stdin.isatty():
            die("approving merges code; pass --yes to do it without a prompt")
        if input("\nApprove and merge this job? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Nothing changed.")
            return 0
    return job("approve", args[0])


# ---- baseline, reset and config --------------------------------------------


def local_sandbox():
    """Refuse anything that is not a local demo sandbox, and say why."""
    if tracker() != "files":
        die("this works only in a local sandbox whose tracker is files (see docs/local-mode.md)")
    url = sh("git", "remote", "get-url", "origin", check=True).stdout.strip()
    if re.match(r"^(https?://|ssh://|git@|git://)", url) or not Path(url).exists():
        die(f"origin is {url}, not a folder on this machine; this works only in a local sandbox")
    # local-setup.sh gives a sandbox a bare origin. A clone of somebody's checkout has a
    # working repository as its origin, and that is not ours to rewrite.
    if sh("git", "-C", url, "rev-parse", "--is-bare-repository").stdout.strip() != "true":
        die(f"origin ({url}) is a working checkout, not the bare repository of a local sandbox")
    return config()["base_branch"]


def has_tag(tag):
    return sh("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}").returncode == 0


def short(rev):
    return sh("git", "rev-parse", "--short", rev).stdout.strip()


def snapshot(prefix):
    """Copy the issue files and job records into fx's state as <prefix>-issues and <prefix>-jobs."""
    counts = {}
    for name in ("issues", "jobs"):
        snap = state_dir() / f"{prefix}-{name}"
        shutil.rmtree(snap, ignore_errors=True)
        if (FACTORY / name).exists():
            shutil.copytree(FACTORY / name, snap, ignore=shutil.ignore_patterns(".lock"))
        counts[name] = len([x for x in snap.iterdir() if not x.name.startswith(".")]) if snap.exists() else 0
    return counts


def dirty(outside_setup_only=False):
    """Tracked files with uncommitted changes. Untracked files are left alone."""
    lines = sh("git", "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    paths = [line[3:].split(" -> ")[-1] for line in lines]
    if outside_setup_only:
        paths = [x for x in paths if not any(x == s or x.startswith(s + "/") for s in SETUP)]
    return paths


def cmd_baseline():
    base = local_sandbox()
    if dirty():
        die("commit or discard your changes first; the starting point must be a commit")
    if sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != base:
        die(f"switch to {base} first")
    sh("git", "tag", "-f", TAG, check=True)
    counts = snapshot("baseline")
    if not has_tag(ORIGIN_TAG):  # the first starting point is also the original setup
        sh("git", "tag", ORIGIN_TAG, check=True)
        snapshot("origin")
    print(f"Starting point set at {short('HEAD')}, with {counts['issues']} issue file(s) and {counts['jobs']} job record(s).")
    print("./fx reset brings the sandbox back to this state and keeps later setup changes.")
    return 0


def cmd_reset(args):
    base = local_sandbox()
    unknown = [a for a in args if a not in {"--yes", "--setup", "--keep"}]
    if unknown:
        die(f"unknown option {unknown[0]}; reset takes --yes, --setup and --keep")
    if not has_tag(TAG):
        die("no starting point yet; run ./fx baseline when the sandbox is in the state you want to return to")
    if sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != base:
        die(f"switch to {base} first")
    other = dirty(outside_setup_only=True)
    if other:
        die(f"uncommitted changes outside the setup files ({', '.join(other[:3])}); commit or discard them first")
    if not has_tag(ORIGIN_TAG):  # a sandbox made before this option existed
        sh("git", "tag", ORIGIN_TAG, TAG, check=True)
        for name in ("issues", "jobs"):
            src = state_dir() / f"baseline-{name}"
            if src.exists():
                shutil.copytree(src, state_dir() / f"origin-{name}", dirs_exist_ok=True)

    to_origin = "--setup" in args
    target = ORIGIN_TAG if to_origin else TAG
    snap = "origin" if to_origin else "baseline"
    sh("git", "fetch", "origin", check=True)
    kept = state_dir() / f"{snap}-jobs"
    keep = {x.name for x in kept.iterdir()} if kept.exists() else set()
    jobs = sorted(x.name for x in (FACTORY / "jobs").iterdir()
                  if x.is_dir() and x.name not in keep) if (FACTORY / "jobs").exists() else []
    commits = sh("git", "rev-list", "--count", f"{target}..origin/{base}").stdout.strip() or "0"
    branches = sh("git", "branch", "--list", "factory/*", "--format=%(refname:short)").stdout.split()
    remote = [b.split("/", 1)[1] for b in sh("git", "branch", "-r", "--list", "origin/factory/*",
                                             "--format=%(refname:short)").stdout.split()]
    changed = [] if to_origin else setup_changes(target)

    print(f"Reset this sandbox to {'the setup it was created with' if to_origin else 'its starting point'} ({short(target)}):")
    print(f"  delete {len(jobs)} job record(s): {', '.join(jobs) or 'none'}")
    print(f"  drop {commits} commit(s) from {base}, here and in origin")
    print(f"  delete {len(set(branches) | set(remote))} job branch(es)")
    print("  restore the issue files and job records to how they were then")
    if to_origin:
        print("  discard every setup change made since the sandbox was created")
    else:
        print(f"  keep your setup changes: {', '.join(changed) or 'none to keep'}")
    if "--keep" in args and jobs:
        print("  save this run's job files under factory/runs/ first")
    if "--yes" not in args:
        if input("Type yes to continue: ").strip().lower() != "yes":
            print("nothing changed")
            return 1

    if "--keep" in args and jobs:
        dest = FACTORY / "runs" / time.strftime("%Y%m%d-%H%M%S")
        for name in jobs:
            shutil.copytree(FACTORY / "jobs" / name, dest / name, ignore=shutil.ignore_patterns(".lock"))
        print(f"saved {len(jobs)} job folder(s) to {dest.relative_to(REPO).as_posix()}")

    # Set the setup aside, as it is in the working tree now (committed or not).
    aside = state_dir() / "setup-aside"
    shutil.rmtree(aside, ignore_errors=True)
    if not to_origin:
        for rel in SETUP:
            src = REPO / rel
            if src.is_dir():
                shutil.copytree(src, aside / rel)
            elif src.exists():
                (aside / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, aside / rel)

    for wt in sorted((REPO / ".worktrees").iterdir()) if (REPO / ".worktrees").exists() else []:
        sh("git", "worktree", "remove", "--force", str(wt))
    sh("git", "worktree", "prune")
    for b in branches:
        sh("git", "branch", "-D", b)
    for b in remote:
        sh("git", "push", "origin", "--delete", b)
    sh("git", "reset", "--hard", target, check=True)

    if not to_origin:  # put the setup back and make it part of the starting point
        for rel in SETUP:
            src, dst = aside / rel, REPO / rel
            if src.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
            elif src.exists():
                shutil.copy2(src, dst)
        sh("git", "add", "--", *[rel for rel in SETUP if (REPO / rel).exists()], check=True)
        if sh("git", "diff", "--cached", "--quiet").returncode != 0:
            sh("git", "commit", "-m", "Setup for the next run", check=True)
    shutil.rmtree(aside, ignore_errors=True)

    sh("git", "tag", "-f", TAG, check=True)
    sh("git", "push", "--force", "origin", f"HEAD:refs/heads/{base}", check=True)
    sh("git", "fetch", "origin", check=True)
    shutil.rmtree(FACTORY / "jobs", ignore_errors=True)
    if kept.exists():
        shutil.copytree(kept, FACTORY / "jobs")
    issues, saved = FACTORY / "issues", state_dir() / f"{snap}-issues"
    shutil.rmtree(issues, ignore_errors=True)
    shutil.copytree(saved if saved.exists() else FACTORY / "demo-issues", issues)
    if to_origin:  # the original setup is the starting point again
        snapshot("baseline")

    print(f"\nDone. The sandbox starts again at {short('HEAD')}:")
    job("issues")
    return 0


def setup_changes(since):
    """Setup files that differ from the starting point, committed or not."""
    out = sh("git", "diff", "--name-only", since, "--", *SETUP).stdout.split()
    return sorted(set(out))


CONFIG_KEYS = {
    "concurrency": "how many jobs run at once (1 or more)",
    "max_rounds": "how often build, gate and review may repeat before a person decides (1 or more)",
    "merge_method": "merge, squash or rebase",
    "review.low": "model of the code reviewer for low-risk changes: haiku, sonnet, opus",
    "review.medium": "the same for medium risk",
    "review.high": "the same for high risk",
}


def cmd_config(args):
    cfg = config()
    if not args:
        print(f"  tracker        {cfg.get('tracker', 'github')}")
        for key in ("concurrency", "max_rounds", "merge_method"):
            print(f"  {key:<14} {cfg[key]}")
        for risk, reviewers in cfg["routing"].items():
            print(f"  review.{risk:<7} " + ", ".join(f"{k} on {v}" for k, v in reviewers.items()))
        for surface, reviewers in cfg["surface_reviewers"].items():
            print(f"  touches {surface:<6} adds " + ", ".join(f"{k} on {v}" for k, v in reviewers.items()))
        print("\nChange one with ./fx config <key> <value>:")
        for key, what in CONFIG_KEYS.items():
            print(f"  {key:<14} {what}")
        return 0
    if len(args) != 2 or args[0] not in CONFIG_KEYS:
        die("usage: fx config <key> <value>; run ./fx config for the keys")
    key, value = args
    path = FACTORY / "config.json"
    text = path.read_text()
    if key in {"concurrency", "max_rounds"}:
        if not value.isdigit() or int(value) < 1:
            die(f"{key} must be a whole number, 1 or more")
        new = re.sub(rf'("{key}":\s*)\d+', rf"\g<1>{int(value)}", text, count=1)
        cfg[key] = int(value)
    elif key == "merge_method":
        if value not in {"merge", "squash", "rebase"}:
            die("merge_method must be merge, squash or rebase")
        new = re.sub(r'("merge_method":\s*")\w+(")', rf"\g<1>{value}\g<2>", text, count=1)
        cfg[key] = value
    else:
        risk = key.split(".", 1)[1]
        if value not in MODELS:
            die(f"the model must be one of: {', '.join(sorted(MODELS))}")
        new = re.sub(rf'("{risk}":\s*\{{\s*"code":\s*")\w+(")', rf"\g<1>{value}\g<2>", text, count=1)
        cfg["routing"][risk]["code"] = value
    try:
        same = json.loads(new) == cfg
    except ValueError:
        same = False
    if not same:  # the file isn't laid out the way the pattern expects: rewrite it whole
        new = json.dumps(cfg, indent=2) + "\n"
    if new == text:
        print(f"{key} is already {value}")
        return 0
    path.write_text(new)
    sh("git", "add", "factory/config.json", check=True)
    sh("git", "commit", "-m", f"Set {key} to {value}", check=True)
    print(f"{key} is now {value} (committed as {short('HEAD')}).")
    url = sh("git", "remote", "get-url", "origin").stdout.strip()
    if tracker() == "files" and url and Path(url).exists():
        base = cfg["base_branch"]
        pushed = sh("git", "push", "origin", f"HEAD:refs/heads/{base}")
        print("It applies to the next job." if pushed.returncode == 0
              else f"Could not push it to origin: {pushed.stderr.strip()}")
    else:
        print("Push it when you are ready; the factory reads the main checkout.")
    return 0


# ---- main ------------------------------------------------------------------

INTRO = """One entry point for running and showing the software factory.

The factory itself runs inside a Claude Code session that you watch.
`fx run` opens that session and types the command for you."""

EXAMPLES = """examples:
  fx doctor                  is this machine ready?
  fx up                      dashboard and presentation
  fx queue 2 && fx run       queue issue 2, then run the factory
  fx config review.low sonnet
  fx show 1 && fx approve 1  look at a job that waits for you, then approve it
  fx feedback 1 use the same wording as the events page
  fx reset --keep            start again, keeping your setup and a copy of this run

Run `fx <command> --help` for the details of one command."""

# name, one-line help, longer description (None = the one-liner)
COMMANDS = [
    ("doctor", "check that this machine can run the factory",
     "Checks Python, git, pytest, ruff, the Claude Code version, the tracker's tool, the\n"
     "optional browser tools and whether factory/checks.sh passes. Exits 1 if something\n"
     "the factory needs is missing, and says what."),
    ("up", "start the dashboard, open it and the presentation",
     "Starts the dashboard on a free port bound to 127.0.0.1 and opens it and\n"
     "docs/presentation.html in the browser. Running it again reuses the same server."),
    ("down", "stop the dashboard", None),
    ("deck", "open the presentation", None),
    ("run", "open Claude Code on /factory run",
     "Opens Claude Code in this folder and types `/factory run`, or `/factory run <issue>`\n"
     "for one issue. From there the session is yours to watch and approve."),
    ("issues", "list the issues: ref, state, labels, title", None),
    ("queue", "label an issue so the next run picks it up", None),
    ("unqueue", "remove that label from an issue", None),
    ("status", "show the jobs and what the next run would do", None),
    ("watch", "show job files as they are written (Ctrl-C to stop)", None),
    ("show", "a waiting job: the decision, the change, your options", None),
    ("approve", "approve a waiting job; the factory re-checks and merges",
     "Shows the job, asks, then approves it. The factory syncs the branch with the base,\n"
     "runs the gate again and merges. Only a job whose tip is a commit the gate has\n"
     "passed can be approved, so the checks are never skipped."),
    ("feedback", "send a waiting job back to work with your instructions",
     "Your words go to the factory as feedback-<k>.md and the job goes back to work.\n"
     + "Write them after the job, pipe them in, or leave them out to type them in your editor.\n"
     "Then run `fx run` so the factory picks the job up."),
    ("reject", "drop a waiting job, with your reason",
     "Your reason is posted on the issue. Write it after the job, pipe it in, or leave\n"
     "it out to type it in your editor."),
    ("retry", "try a failed merge again", None),
    ("park", "stop a running job that is stuck; it waits for you instead",
     "Moves a job out of a running stage into `needs-human`, with your reason. Use it\n"
     "when an agent died or an issue went away. `fx feedback` resumes the job."),
    ("config", "show the settings worth changing, or change one",
     "Without arguments, shows the settings and the keys you can change.\n"
     "With a key and a value, checks the value, changes factory/config.json and commits it."),
    ("baseline", "mark the current state as the starting point for `fx reset`",
     "Records the current commit, issue files and finished job records as the state\n"
     "`fx reset` returns to. Local sandboxes only."),
    ("reset", "discard the work, keep your setup changes, start again",
     "Discards the work: jobs, job branches, merged changes and the state of the issues.\n"
     "Keeps your changes to the setup (factory/config.json, factory/checks.sh, .claude/\n"
     "and AGENTS.md), committed or not, and makes them part of the new starting point.\n"
     "Local sandboxes only. It lists what it will delete and asks first."),
]


def build_parser():
    fmt = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(prog="fx", description=INTRO, epilog=EXAMPLES, formatter_class=fmt)
    sub = parser.add_subparsers(dest="command", metavar="<command>", title="commands")
    made = {}
    for name, short_help, long_help in COMMANDS:
        made[name] = sub.add_parser(name, help=short_help, description=long_help or short_help.capitalize() + ".",
                                    formatter_class=fmt)
    made["up"].add_argument("--no-open", action="store_true", help="start the dashboard without opening a browser")
    made["run"].add_argument("issue", nargs="?", help="run only this issue")
    made["queue"].add_argument("issue", help="the issue's ref, as `fx issues` shows it")
    made["unqueue"].add_argument("issue", help="the issue's ref, as `fx issues` shows it")
    for name in ("show", "approve", "feedback", "reject", "retry", "park"):
        made[name].add_argument("job", help="a job id, or its issue's ref")
    for name, what in (("feedback", "what should change"), ("reject", "why it is dropped"),
                       ("park", "why it is stopped")):
        made[name].add_argument("words", nargs="*", help=f"{what}; left out, it comes from stdin or your editor")
    made["approve"].add_argument("--yes", action="store_true", help="don't ask for confirmation")
    made["config"].add_argument("key", nargs="?", choices=sorted(CONFIG_KEYS), metavar="key",
                                help="one of: " + ", ".join(sorted(CONFIG_KEYS)))
    made["config"].add_argument("value", nargs="?", help="the new value")
    made["reset"].add_argument("--setup", action="store_true",
                               help="also discard setup changes: back to how the sandbox was made")
    made["reset"].add_argument("--keep", action="store_true",
                               help="save this run's job folders under factory/runs/ first")
    made["reset"].add_argument("--yes", action="store_true", help="don't ask for confirmation")
    return parser


def dispatch(ns):
    cmd = ns.command
    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "up":
        return cmd_up(["--no-open"] if ns.no_open else [])
    if cmd == "down":
        return cmd_down()
    if cmd == "deck":
        return cmd_deck()
    if cmd == "run":
        return cmd_run([ns.issue] if ns.issue else [])
    if cmd == "issues":
        return job("issues")
    if cmd in {"queue", "unqueue"}:
        return job("label" if cmd == "queue" else "unlabel", ns.issue, config()["labels"]["queue"])
    if cmd == "status":
        return cmd_status()
    if cmd == "watch":
        return cmd_watch()
    if cmd == "show":
        return job("show", ns.job)
    if cmd == "approve":
        return cmd_approve([ns.job] + (["--yes"] if ns.yes else []))
    if cmd == "retry":
        return job("merge", ns.job)
    if cmd == "feedback":
        return decide("feedback", [ns.job, *ns.words], "What should change? Your words go to the builder.")
    if cmd == "reject":
        return decide("reject", [ns.job, *ns.words], "Why is this job dropped? Your reason is posted on the issue.")
    if cmd == "park":
        return decide("park", [ns.job, *ns.words], "Why is this job stopped? Your reason is posted on the issue.")
    if cmd == "config":
        if (ns.key is None) != (ns.value is None):
            die("give both a key and a value, or neither; run `fx config` for the keys")
        return cmd_config([ns.key, ns.value] if ns.key else [])
    if cmd == "baseline":
        return cmd_baseline()
    if cmd == "reset":
        return cmd_reset([flag for flag, on in (("--setup", ns.setup), ("--keep", ns.keep), ("--yes", ns.yes)) if on])


def main(argv):
    if hasattr(signal, "SIGPIPE"):  # `fx status | head` ends quietly instead of with a traceback
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the scripts we call
    parser = build_parser()
    if not argv or argv[0] == "help":
        parser.print_help()
        return 0
    return dispatch(parser.parse_args(argv)) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
