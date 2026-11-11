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
  fx baseline          mark the current state as the demo's starting point
  fx reset [--yes]     put a local demo sandbox back to that starting point

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
TAG = "demo-start"
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


# ---- baseline and reset ----------------------------------------------------


def local_sandbox():
    """Refuse anything that is not a local demo sandbox, and say why."""
    if tracker() != "files":
        die("reset works only in a local sandbox whose tracker is files (see docs/local-mode.md)")
    url = sh("git", "remote", "get-url", "origin", check=True).stdout.strip()
    if re.match(r"^(https?://|ssh://|git@|git://)", url) or not Path(url).exists():
        die(f"origin is {url}, not a folder on this machine; fx will not reset a shared repository")
    return config()["base_branch"]


def cmd_baseline():
    base = local_sandbox()
    if sh("git", "status", "--porcelain").stdout.strip():
        die("commit or discard your changes first; the starting point must be a commit")
    if sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != base:
        die(f"switch to {base} first")
    sh("git", "tag", "-f", TAG, check=True)
    snap = state_dir() / "baseline-issues"
    shutil.rmtree(snap, ignore_errors=True)
    issues = FACTORY / "issues"
    if issues.exists():
        shutil.copytree(issues, snap)
    head = sh("git", "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"Starting point set at {head}, with {len(list(snap.glob('*.md'))) if snap.exists() else 0} issue file(s).")
    print("./fx reset brings the sandbox back to this state.")
    return 0


def cmd_reset(args):
    base = local_sandbox()
    if sh("git", "rev-parse", "--verify", "--quiet", TAG).returncode != 0:
        die(f"no starting point yet; run ./fx baseline when the sandbox is in the state you want to return to")
    if sh("git", "status", "--porcelain").stdout.strip():
        die("this checkout has uncommitted changes; commit or discard them first")
    if sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != base:
        die(f"switch to {base} first")

    sh("git", "fetch", "origin", check=True)
    jobs = sorted(p.name for p in (FACTORY / "jobs").iterdir() if p.is_dir()) if (FACTORY / "jobs").exists() else []
    commits = sh("git", "rev-list", "--count", f"{TAG}..origin/{base}").stdout.strip() or "0"
    branches = [b.strip() for b in sh("git", "branch", "--list", "factory/*", "--format=%(refname:short)").stdout.split()]
    remote = [b.split("/", 1)[1] for b in sh("git", "branch", "-r", "--list", "origin/factory/*",
                                             "--format=%(refname:short)").stdout.split()]
    print(f"Reset this sandbox to its starting point ({sh('git', 'rev-parse', '--short', TAG).stdout.strip()}):")
    print(f"  delete {len(jobs)} job record(s): {', '.join(jobs) or 'none'}")
    print(f"  drop {commits} commit(s) from {base}, here and in origin")
    print(f"  delete {len(set(branches) | set(remote))} job branch(es)")
    print("  restore the issue files to how they were at the starting point")
    if "--yes" not in args:
        if input("Type yes to continue: ").strip().lower() != "yes":
            print("nothing changed")
            return 1

    for wt in sorted((REPO / ".worktrees").iterdir()) if (REPO / ".worktrees").exists() else []:
        sh("git", "worktree", "remove", "--force", str(wt))
    sh("git", "worktree", "prune")
    for b in branches:
        sh("git", "branch", "-D", b)
    for b in remote:
        sh("git", "push", "origin", "--delete", b)
    sh("git", "reset", "--hard", TAG, check=True)
    sh("git", "push", "--force", "origin", f"{TAG}^{{commit}}:refs/heads/{base}", check=True)
    sh("git", "fetch", "origin", check=True)
    shutil.rmtree(FACTORY / "jobs", ignore_errors=True)

    issues, snap = FACTORY / "issues", state_dir() / "baseline-issues"
    source = snap if snap.exists() else FACTORY / "demo-issues"
    shutil.rmtree(issues, ignore_errors=True)
    shutil.copytree(source, issues)
    print("\nDone. The sandbox is back at its starting point:")
    job("issues")
    return 0


# ---- main ------------------------------------------------------------------


def main(argv):
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
    if cmd == "baseline":
        return cmd_baseline()
    if cmd == "reset":
        return cmd_reset(args)
    die(f"unknown command: {' '.join(argv)}\n\n{__doc__.strip()}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
