#!/usr/bin/env python3
"""fx: one entry point for running and showing the software factory.

A thin wrapper. It starts the dashboard, opens the presentation, checks the
prerequisites and resets a demo sandbox. Everything about jobs still goes
through factory/bin/job; fx adds no factory logic of its own.

Usage:
  fx doctor            check that this machine can run the factory
  fx up [--no-open]    start the dashboard, open it and the presentation
  fx down              stop the dashboard
  fx deck              open the presentation
  fx run [issue]       start Claude Code on /factory run (you still watch it)
  fx issues            list the issues
  fx queue <issue>     label an issue so the factory picks it up
  fx unqueue <issue>   remove that label
  fx status            jobs, and what the next run would do
  fx watch             show job files as they are written (Ctrl-C to stop)
  fx config            show the settings worth changing in a demo
  fx config <key> <value>   change one and commit it (see `fx config` for the keys)

When a job waits for you (the "Needs human" column):
  fx show <job>        the decision, the change and your options
  fx approve <job>     approve it: the factory runs the checks again and merges
  fx feedback <job> [words]   send it back to work with your instructions
  fx reject <job> [words]     drop it, with your reason
  fx retry <job>       try a failed merge again
  fx park <job> [words]       stop a running job that is stuck

Words can follow the command, come on stdin, or be typed in your editor.
<job> is a job id or its issue number.

  fx baseline          mark the current state as the demo's starting point
  fx reset             discard the work, keep your setup changes, start again
  fx reset --setup     also discard setup changes: back to how the sandbox was made
                       both take --yes (don't ask) and --keep (save this run's
                       job files under factory/runs/ first)

The factory itself runs inside a Claude Code session: `fx run` opens one and
types the command, and from there it is yours to watch and approve.
"""

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
    return subprocess.run([str(JOB), *args], cwd=REPO).returncode


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

    for tool, why in (("agent-browser", "needed for issues that change the web page"),
                      ("lsof", "used to stop a job's dev server")):
        rows.append(("ok" if have(tool) else "warn", tool, "" if have(tool) else why))

    checks = sh("bash", str(FACTORY / "checks.sh"))
    tail = (checks.stdout or checks.stderr).strip().splitlines()
    rows.append(("ok" if checks.returncode == 0 else "MISSING", "factory/checks.sh passes",
                 tail[-1] if tail else ""))

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
        os.kill(st["pid"], 0)  # raises if the process is gone
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
    if "microsoft" in os.uname().release.lower():
        for tool in ("wslview", "explorer.exe"):
            if shutil.which(tool):
                subprocess.run([tool, url], capture_output=True)
                return
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass  # the URL is printed anyway


def deck_url():
    if "microsoft" in os.uname().release.lower() and shutil.which("wslpath"):
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
            cwd=FACTORY, stdout=log, stderr=log, start_new_session=True)
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
    try:
        os.kill(st["pid"], signal.SIGTERM)
    except OSError:
        pass
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
    os.execvp("claude", ["claude", prompt])


# ---- issues and jobs -------------------------------------------------------


def cmd_status():
    job("summary")
    print("\nNext run:")
    res = sh(str(JOB), "queue")
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
                    print(f"{time.strftime('%H:%M:%S', time.localtime(mtime))}  {verb}  {p.relative_to(jobs)}  {first}")
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
        print(f"{what} End with Ctrl-D on an empty line.")
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
    code = subprocess.run([str(JOB), verb, args[0]], cwd=REPO, input=text, text=True).returncode
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
        die(f"origin is {url}, not a folder on this machine; fx will not reset a shared repository")
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
        print(f"saved {len(jobs)} job folder(s) to {dest.relative_to(REPO)}")

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


def main(argv):
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the scripts we call
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__.strip())
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "up":
        return cmd_up(args)
    if cmd == "down":
        return cmd_down()
    if cmd == "deck":
        return cmd_deck()
    if cmd == "run" and len(args) <= 1:
        return cmd_run(args)
    if cmd == "issues" and not args:
        return job("issues")
    if cmd in {"queue", "unqueue"} and len(args) == 1:
        return job("label" if cmd == "queue" else "unlabel", args[0], config()["labels"]["queue"])
    if cmd == "status":
        return cmd_status()
    if cmd == "watch":
        return cmd_watch()
    if cmd == "show" and len(args) == 1:
        return job("show", args[0])
    if cmd == "approve":
        return cmd_approve(args)
    if cmd == "retry" and len(args) == 1:
        return job("merge", args[0])
    if cmd == "feedback":
        return decide("feedback", args, "What should change? Your words go to the builder.")
    if cmd == "reject":
        return decide("reject", args, "Why is this job dropped? Your reason is posted on the issue.")
    if cmd == "park":
        return decide("park", args, "Why is this job stopped? Your reason is posted on the issue.")
    if cmd == "config":
        return cmd_config(args)
    if cmd == "baseline":
        return cmd_baseline()
    if cmd == "reset":
        return cmd_reset(args)
    die(f"unknown command: {' '.join(argv)}\n\n{__doc__.strip()}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
