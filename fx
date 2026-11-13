#!/usr/bin/env python3
"""fx: one entry point for running and showing the software factory.

A thin wrapper. It opens the presentation, starts the dashboard, checks the
prerequisites and resets a demo sandbox. Everything about jobs still goes
through factory/bin/job; fx adds no factory logic of its own.

Run `fx` for the list of commands and `fx <command> --help` for one of them.
"""

import argparse
import calendar
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

if __name__ == "__main__":
    portable.utf8()

JOB = FACTORY / "bin" / "job"
DECK = REPO / "docs" / "presentation.html"
TAG = "demo-start"  # the starting point `fx reset` returns to; moves when the setup changes
ORIGIN_TAG = "demo-origin"  # the setup the sandbox was created with; `fx reset --setup` returns to it
# The setup: what you change to run an experiment. Everything else a run produces is work.
SETUP = ["factory/config.json", "factory/checks.sh", ".claude", "AGENTS.md"]
# Inside .claude but not setup: Claude Code's own worktrees and this machine's private settings.
# fx never copies, commits or deletes these.
NOT_SETUP = (".claude/worktrees/", ".claude/settings.local.json")
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
    return config().get("tracker", "files")


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


def folder_trusted():
    """True or False when Claude Code's own record says whether this folder's
    trust dialog was accepted; None when the record can't be read. Claude Code
    skips the hooks in project agents (the factory's guard among them) in a
    folder that was never trusted, and says nothing about it."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home())
    try:
        projects = json.loads((home / ".claude.json").read_text(encoding="utf-8")).get("projects", {})
    except (OSError, ValueError, AttributeError):
        return None
    # Windows records C:/Users/..., with forward slashes, and the drive letter's case may vary.
    def spell(path):
        return path.lower() if portable.WINDOWS else path
    wanted = {spell(k) for path in (REPO, REPO.resolve()) for k in (str(path), path.as_posix())}
    for key, project in projects.items():
        if spell(key) in wanted:
            return bool(project.get("hasTrustDialogAccepted"))
    return False


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
        out = sh(shutil.which("claude"), "--version").stdout
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        ver = tuple(int(x) for x in m.groups()) if m else None
        ok = ver is not None and ver >= MIN_CLAUDE
        rows.append(("ok" if ok else "MISSING", "Claude Code 2.1.257+",
                     out.strip() if ok else f"found {out.strip() or 'no version'}; update Claude Code"))
    else:
        rows.append(("MISSING", "Claude Code 2.1.257+", "install Claude Code and sign in"))

    t = tracker()
    rows.append(("ok", "tracker", t))
    if t == "kata":
        rows.append(("ok" if have("kata") else "MISSING", "kata",
                     "" if have("kata") else "go install go.kenn.io/kata/cmd/kata@latest"))

    # doctor only looks; ./fx run (or ./fx setup) does the setting up.
    origin = local_origin()
    rows.append(("ok" if is_set_up() else "warn", "local setup",
                 f"origin is {origin}" if is_set_up() else "not yet: ./fx run sets this checkout up (or ./fx setup)"))

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

    trusted = folder_trusted()
    rows.append(("ok" if trusted else "warn", "folder trusted in Claude Code",
                 "" if trusted else "could not tell; open `claude` here once and accept the trust prompt"
                 if trusted is None else
                 "not yet: the agents' guard hooks are skipped until you open `claude` here "
                 "(or ./fx run) and accept the trust prompt"))

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
    print("\nReady. Next: ./fx deck to present, then ./fx up and ./fx run.")
    return 0


# ---- dashboard and presentation --------------------------------------------


def dashboard_state():
    path = state_dir() / "dashboard.json"
    if not path.exists():
        return None
    try:
        st = json.loads(path.read_text())
        # A process id is reused after a reboot or a crash. Only a process that is alive
        # and serves the dashboard on the recorded port is ours to report, or to stop.
        if not portable.alive(st["pid"]) or not serves_dashboard(st["port"]):
            raise ValueError("the dashboard is gone")
        return st
    except (ValueError, KeyError, OSError):
        path.unlink(missing_ok=True)
        return None


def serves_dashboard(port):
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/dashboard.html", timeout=2) as res:
            return res.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


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


def dashboard():
    """The running dashboard server, started if it isn't up: {pid, port}."""
    st = dashboard_state()
    if not st:
        port = free_port()
        log = open(state_dir() / "dashboard.log", "w")
        proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=FACTORY, stdout=log, stderr=log, **portable.own_group())
        st = {"pid": proc.pid, "port": port}
        for _ in range(50):  # wait until it answers, so the next fx command recognises it
            if proc.poll() is not None or serves_dashboard(port):
                break
            time.sleep(0.1)
        if proc.poll() is not None or not serves_dashboard(port):
            portable.stop(proc.pid)
            die("the dashboard server did not start; see " + str(state_dir() / "dashboard.log"))
        (state_dir() / "dashboard.json").write_text(json.dumps(st))
    return st


def cmd_up(args):
    st = dashboard()
    dash = f"http://127.0.0.1:{st['port']}/dashboard.html"
    print(f"dashboard     {dash}")
    if "--no-open" not in args:
        open_url(dash)
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
    print("\nWhen you are ready to run the factory:  ./fx up, then ./fx run")
    return 0


def cmd_run(args, prompt=None):
    if not shutil.which("claude"):
        die("Claude Code is not installed; run ./fx doctor")
    if not is_set_up():
        cmd_setup(quiet=True)
    prompt = prompt or "/factory run" + (f" {args[0]}" if args else "")
    print(f"Starting Claude Code in {REPO} with: {prompt}")
    os.chdir(REPO)
    if portable.WINDOWS:  # Windows has no exec: run Claude Code and pass its exit code on
        return subprocess.run([shutil.which("claude"), prompt]).returncode
    os.execvp("claude", ["claude", prompt])


# ---- demo: replay a saved run ----------------------------------------------
#
# A run that `fx reset --keep` saved is a folder of finished job folders. Each
# has events.jsonl, the job's transitions in order, and the files the stations
# wrote. `fx demo` plays them back into factory/replay/, one step at a time,
# and the dashboard shows that folder instead of factory/jobs/. No agent runs
# and nothing in factory/jobs/, the issues or git changes.

REPLAY = FACTORY / "replay"
STOPS = {"needs-info", "needs-human", "merged", "rejected"}


def saved_run(name):
    """The folder of job folders to replay: the one named, else the run saved
    last under factory/runs/, else the one that ships in factory/demo-run/."""
    def jobs_in(d):
        return d.is_dir() and any((x / "job.json").exists() for x in d.iterdir())
    if name:
        for d in (Path(name), FACTORY / "runs" / name):
            if jobs_in(d):
                return d
        die(f"{name} is not a saved run: no job folders in it")
    runs = FACTORY / "runs"
    saved = sorted(d for d in runs.iterdir() if jobs_in(d)) if runs.exists() else []
    if saved:
        return saved[-1]
    if jobs_in(FACTORY / "demo-run"):
        return FACTORY / "demo-run"
    die("no saved run to replay. `./fx reset --keep` saves the current run under factory/runs/; "
        "after that, `./fx demo` plays it back")


def stamp(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def replay_steps(run):
    """Every step of a saved run, in the order it happened, as
    (time, job id, what to write, the job's record after the step, a line to print).

    The saved folder holds only how each job ended, so the steps are rebuilt
    from its event log: each transition names the stage a job left, and that
    stage's file is shown just before the job moves on."""
    steps = []
    for folder in sorted(d for d in run.iterdir() if (d / "job.json").exists()):
        final = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        log = folder / "events.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        events = [json.loads(line) for line in lines if line.strip()]
        moves = [e for e in events if e.get("event") in {"create", "advance"} and e.get("at")]
        if not moves:
            continue
        names = {p.relative_to(folder).as_posix() for p in folder.rglob("*")
                 if p.is_file() and p.name not in {"job.json", "events.jsonl"}}
        shown = set()   # names as they are in the saved folder
        written = {}    # stem -> how often that file has been written so far
        state = {**final, "stage": "intake", "round": 0, "intake": None, "agents": None, "gated_sha": None,
                 "merged_sha": None, "merge_failed": False, "ended": None, "cost_usd": 0.0, "reviews": {}}
        state.pop("cost_incomplete", None)
        log_so_far = []

        def versioned(stem):
            """decision.md and intake.md are rewritten and the old one kept as
            <stem>-<k>.md: the k-th writing is <stem>-<k>.md if the job kept it."""
            k = written[stem] = written.get(stem, 0) + 1
            src = f"{stem}-{k}.md" if f"{stem}-{k}.md" in names else f"{stem}.md"
            if src in shown or src not in names:
                return []
            shown.add(src)
            keep = [("rename", f"{stem}.md", f"{stem}-{k - 1}.md")] if k > 1 else []
            return keep + [("copy", src, f"{stem}.md")]

        def plain(*wanted):
            out = []
            for name in sorted(n for n in names if n not in shown and any(
                    n == w or (w.endswith("*") and n.startswith(w[:-1])) for w in wanted)):
                shown.add(name)
                out.append(("copy", name, name))
            return out

        def add(at, writes, note=None):
            steps.append((at, final["id"], writes + [("events", list(log_so_far))], dict(state), note))

        before = stamp(moves[0]["at"])
        for n, e in enumerate(moves):
            at = stamp(e["at"])
            old, new, rnd = e.get("from"), e.get("to"), e.get("round", 0)
            last_round = rnd - 1 if new == "build" and old in {"gate", "review"} else rnd
            reveal = []  # each entry is one step: the files a station wrote before the job moved on
            if e["event"] == "create":
                reveal.append(plain("issue.md"))
            elif old == "intake":
                reveal.append(versioned("intake"))
            elif old == "spec":
                reveal.append(plain("spec.md"))
            elif old == "build" and new == "gate":
                reveal.append(plain("build.md"))
            elif old == "gate":
                reveal.append(plain(f"round-{last_round}/checks.md", f"round-{last_round}/checks.log"))
            elif old == "review":
                reveal += [[w] for w in plain(f"round-{last_round}/review-*")]
            elif old == "needs-human" and new in {"build", "intake", "spec"}:
                reveal.append(plain(f"feedback-{written.get('feedback', 0) + 1}.md"))
                written["feedback"] = written.get("feedback", 0) + 1
            if new == "rejected":
                reveal.append(plain("rejection.md"))
            if new == "merged":
                reveal.append(plain(f"round-{rnd}/merge-checks.md", f"round-{rnd}/merge-checks.log"))
            if new == "needs-human" or (new == "merging" and old in {"approve", "needs-human"}):
                reveal.append(versioned("decision"))
            if n == len(moves) - 1:  # whatever the rules above missed is there at the end
                reveal.append(plain("*"))
            reveal = [w for w in reveal if w]
            for k, writes in enumerate(reveal):
                if "intake.md" in {w[2] for w in writes}:
                    state["intake"] = final.get("intake")
                state["updated"] = iso(before + (at - before) * (k + 1) / (len(reveal) + 1))
                add(stamp(state["updated"]), writes)
            log_so_far.append(e)
            if e["event"] == "advance":
                state["stage"], state["round"] = new, rnd
                if old == "intake" and new == "spec":
                    state["agents"] = final.get("agents")
                if old == "gate" and new == "review":
                    state["gated_sha"] = final.get("gated_sha")
                state["merged_sha"] = final.get("merged_sha") if new == "merged" else None
                state["merge_failed"] = bool(final.get("merge_failed")) and n == len(moves) - 1
                state["ended"] = e["at"] if new in STOPS else None
            state["updated"] = e["at"]
            state["cost_usd"] = round(float(final.get("cost_usd") or 0) * (n + 1) / len(moves), 2)
            note = (f"{final['id']}: new, issue #{final['issue']}" if e["event"] == "create"
                    else f"{final['id']}: {old} -> {new}" + (f" (round {rnd})" if rnd else ""))
            add(at, [], note)
            before = at
    return sorted(steps, key=lambda s: s[0])


def cmd_demo(ns):
    run = saved_run(ns.run)
    steps = replay_steps(run)
    if not steps:
        die(f"{run} has no event logs to replay")
    shutil.rmtree(REPLAY, ignore_errors=True)
    REPLAY.mkdir(parents=True)
    token = f"{time.time():.3f}"

    def clock(at, done=False):
        tmp = REPLAY / "clock.tmp"
        tmp.write_text(json.dumps({"now": iso(at), "run": token, "done": done}))
        tmp.replace(REPLAY / "clock.json")

    clock(steps[0][0])
    port = dashboard()["port"]
    url = f"http://127.0.0.1:{port}/dashboard.html?dir=replay"
    try:
        shown = run.relative_to(REPO).as_posix()
    except ValueError:
        shown = str(run)
    print(f"Replaying {shown}: {len(steps)} steps. No agent runs and nothing is merged.")
    print(f"dashboard     {url}")
    if not ns.no_open:
        open_url(url)
    pause = 0 if ns.step else min(3.0, ns.seconds / len(steps))
    if ns.step:
        print("Press Enter for each step (Ctrl-C to stop).")
    try:
        for at, job_id, writes, state, note in steps:
            if ns.step:
                if not sys.stdin.readline():
                    break  # nothing more to read: stop where the replay is
            elif pause:
                time.sleep(pause)
            folder = REPLAY / job_id
            folder.mkdir(exist_ok=True)
            for what, *rest in writes:
                if what == "copy":
                    (folder / rest[1]).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(run / job_id / rest[0], folder / rest[1])
                elif what == "rename" and (folder / rest[0]).exists():
                    (folder / rest[0]).replace(folder / rest[1])
                elif what == "events":
                    (folder / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in rest[0]))
            tmp = folder / "job.tmp"
            tmp.write_text(json.dumps(state, indent=2) + "\n")
            tmp.replace(folder / "job.json")
            clock(at)
            if note:
                print(f"  {note}")
        else:
            clock(steps[-1][0], done=True)
            print("\nThe replay is finished and stays on the dashboard. `./fx demo` plays it again;")
            print("`./fx up` shows the live board, and `./fx run` runs the factory for real.")
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


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


CUT = "#fx:"  # marks fx's own lines in the editor; a Markdown heading or "#12" is the person's text


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
        f.write(f"\n{CUT} {what}\n{CUT} Lines starting with {CUT} are ignored. Leave it empty to cancel.\n")
        path = f.name
    try:
        # posix=False keeps the backslashes of a Windows path such as C:\Tools\vim.exe.
        command = shlex.split(editor, posix=not portable.WINDOWS)
        subprocess.run([*(c.strip('"') for c in command), path])
        lines = Path(path).read_text().splitlines()
    finally:
        os.unlink(path)
    return "\n".join(line for line in lines if not line.startswith(CUT)).strip()


def decide(verb, args, what):
    if not args:
        die(f"usage: fx {verb} <job> [words]")
    text = words(args[1:], what)
    if not text.strip():
        die("nothing written; nothing changed")
    code = subprocess.run(portable.script(JOB, verb, args[0]), cwd=REPO, input=text, text=True).returncode
    if code == 0 and verb in {"feedback", "answer"}:
        print("Run ./fx run to put the factory back to work on it.")
    return code


def cmd_new(args):
    """File an issue: the title on the command line, the request after it,
    from a pipe or from the editor. The issue is queued."""
    title, rest = args[0], args[1:]
    text = words(rest, f"What should be built? This is the request for: {title}")
    if not text.strip():
        die("nothing written; no issue filed")
    res = subprocess.run(portable.script(JOB, "new-issue", title), cwd=REPO, input=text, text=True,
                         capture_output=True)
    if res.returncode:
        print(res.stderr.strip(), file=sys.stderr)
        return res.returncode
    ref = res.stdout.strip()
    print(f"issue {ref} filed and queued: {title}")
    print(f"Run ./fx run {ref} to build it, or ./fx unqueue {ref} to hold it back.")
    return 0


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
        die("this works only with the files tracker (see docs/local-mode.md)")
    if not local_origin():
        die("this checkout isn't set up to run the factory yet; run ./fx setup (./fx run does it too)")
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


def local_origin():
    """The repository on disk that plays `origin`, or None while origin is elsewhere."""
    res = sh("git", "remote", "get-url", "origin")
    url = res.stdout.strip()
    if res.returncode != 0 or re.match(r"^(https?://|ssh://|git@|git://)", url) or not Path(url).exists():
        return None
    if sh("git", "-C", url, "rev-parse", "--is-bare-repository").stdout.strip() != "true":
        return None
    return url


def is_set_up():
    issues = FACTORY / "issues"
    return bool(local_origin()) and (tracker() != "files" or (issues.exists() and any(issues.glob("*.md"))))


def cmd_setup(quiet=False):
    """Make this checkout its own sandbox: a repository on disk next to it plays
    `origin`, the demo issues go into factory/issues/, and the starting point for
    `fx reset` is recorded. Running it again changes nothing."""
    base = config()["base_branch"]
    done = []
    if not local_origin():
        if sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != base:
            die(f"switch to {base} first: the factory starts every job from {base}")
        # A commit needs an author; fall back to a local one on a machine with none set.
        for key, value in (("user.name", os.environ.get("USER") or os.environ.get("USERNAME") or "factory"),
                           ("user.email", "factory@localhost")):
            if sh("git", "config", key).returncode != 0:
                sh("git", "config", key, value, check=True)
        bare = REPO.parent / f"{REPO.name}.git"
        old = sh("git", "remote", "get-url", "origin")
        if old.returncode == 0:
            # Keep the place this checkout came from as `upstream`, for course updates.
            if sh("git", "remote", "get-url", "upstream").returncode == 0:
                # `upstream` is taken, so the old origin can't be kept under that name. Say where it pointed.
                sh("git", "remote", "remove", "origin", check=True)
                done.append(f"old origin  {old.stdout.strip()} removed (`upstream` already exists and is kept); "
                            f"add it back under another name with `git remote add <name> <url>` if you need it")
            else:
                sh("git", "remote", "rename", "origin", "upstream", check=True)
                done.append(f"upstream    {old.stdout.strip()} (where this checkout came from)")
        if bare.exists():
            if sh("git", "-C", str(bare), "rev-parse", "--is-bare-repository").stdout.strip() != "true":
                die(f"{bare} exists and is not a bare repository; move it away and run ./fx setup again")
        else:
            sh("git", "init", "--quiet", "--bare", str(bare), check=True)
            sh("git", "-C", str(bare), "symbolic-ref", "HEAD", f"refs/heads/{base}", check=True)
        sh("git", "remote", "add", "origin", bare.as_posix(), check=True)
        sh("git", "push", "--quiet", "origin", f"HEAD:refs/heads/{base}", check=True)
        sh("git", "fetch", "--quiet", "origin", check=True)
        sh("git", "branch", "--quiet", f"--set-upstream-to=origin/{base}", base, check=True)
        done.append(f"origin      {bare.as_posix()} (a repository on disk; the factory merges here)")
    issues = FACTORY / "issues"
    if tracker() == "files" and not (issues.exists() and any(issues.glob("*.md"))):
        issues.mkdir(parents=True, exist_ok=True)
        for demo in sorted((FACTORY / "demo-issues").glob("*.md")):
            shutil.copy2(demo, issues / demo.name)
        done.append(f"issues      {len(list(issues.glob('*.md')))} demo issues in factory/issues/")
    if tracker() == "files" and not has_tag(TAG) and not dirty():
        sh("git", "tag", "-f", TAG, check=True)
        snapshot("baseline")
        if not has_tag(ORIGIN_TAG):
            sh("git", "tag", ORIGIN_TAG, check=True)
            snapshot("origin")
        done.append("start       recorded; ./fx reset returns here")
    if not quiet or done:
        print("This checkout runs the factory locally." if done else "Already set up: this checkout runs the factory locally.")
        for line in done:
            print(f"  {line}")
        if done:
            print()
    return 0


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

    aside = state_dir() / "setup-aside"
    if aside.exists():
        die(f"an earlier reset stopped half-way. Its copy of your setup files is in {aside}: "
            "copy back what you need, delete that folder, then run ./fx reset again")

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
    then = state_dir() / f"{snap}-issues"
    known = {x.name for x in (then if then.exists() else FACTORY / "demo-issues").glob("*.md")}
    new_issues = sorted((x.name for x in (FACTORY / "issues").glob("*.md") if x.name not in known),
                        key=lambda n: (len(n), n))

    print(f"Reset this sandbox to {'the setup it was created with' if to_origin else 'its starting point'} ({short(target)}):")
    print(f"  delete {len(jobs)} job record(s): {', '.join(jobs) or 'none'}")
    print(f"  drop {commits} commit(s) from {base}, here and in origin")
    print(f"  delete {len(set(branches) | set(remote))} job branch(es)")
    print("  restore the issue files and job records to how they were then")
    if new_issues:
        print(f"  delete {len(new_issues)} issue file(s) written since: {', '.join(new_issues)}")
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
    kept_setup = [] if to_origin else setup_files()
    for rel in kept_setup:
        (aside / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, aside / rel)

    left = []  # what could not be cleaned up; the reset goes on and says so at the end
    for wt in sorted((REPO / ".worktrees").iterdir()) if (REPO / ".worktrees").exists() else []:
        sh("git", "worktree", "remove", "--force", str(wt))
        if wt.exists():
            shutil.rmtree(wt, ignore_errors=True)
        if wt.exists():
            left.append(f"{wt.relative_to(REPO).as_posix()} (something still uses it; delete the folder once it is free)")
    sh("git", "worktree", "prune")
    for b in branches:
        if sh("git", "branch", "-D", b).returncode != 0:
            left.append(f"branch {b}")
    for b in remote:
        if sh("git", "push", "origin", "--delete", b).returncode != 0:
            left.append(f"branch {b} in origin")
    sh("git", "reset", "--hard", target, check=True)

    if not to_origin:  # put the setup back and make it part of the starting point
        for rel in setup_files():  # a setup file that was deleted before the reset stays deleted
            if rel not in kept_setup:
                (REPO / rel).unlink()
        for rel in kept_setup:
            (REPO / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(aside / rel, REPO / rel)
        # Stage the setup files one by one, and the ones that are gone: never a whole folder,
        # which would sweep in whatever else lies in .claude.
        gone = sh("git", "ls-files", "-z", "--deleted", "--", *SETUP, check=True).stdout.split("\0")
        paths = sorted({*setup_files(), *(rel for rel in gone if rel and not rel.startswith(NOT_SETUP))})
        for i in range(0, len(paths), 100):  # in batches: a command line has a length limit
            sh("git", "add", "-A", "--", *paths[i:i + 100], check=True)
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

    if left:
        print("\nCould not remove:")
        for item in left:
            print(f"  {item}")
    print(f"\nDone. The sandbox starts again at {short('HEAD')}:")
    job("issues")
    return 0


def setup_files():
    """The setup files in the working tree now: tracked ones and new ones git doesn't ignore."""
    out = sh("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *SETUP, check=True).stdout
    return sorted({rel for rel in out.split("\0")
                   if rel and not rel.startswith(NOT_SETUP) and (REPO / rel).is_file()})


def setup_changes(since):
    """Setup files that differ from the starting point, committed or not."""
    out = sh("git", "diff", "--name-only", since, "--", *SETUP).stdout.split()
    return sorted(rel for rel in set(out) if not rel.startswith(NOT_SETUP))


CONFIG_KEYS = {
    "concurrency": "how many jobs run at once (1 or more)",
    "max_rounds": "how often build, gate and review may repeat before a person decides (1 or more)",
    "merge_method": "merge, squash or rebase",
    "auto_merge": "on: the factory merges what the approver approves; off: every merge waits for you",
    "max_cost_usd": "a job stops between steps once it costs more than this, in dollars; off for no cap",
    "review.low": "model of the code reviewer for low-risk changes: haiku, sonnet, opus",
    "review.medium": "the same for medium risk",
    "review.high": "the same for high risk",
}


def cmd_config(args):
    cfg = config()
    if not args:
        print(f"  tracker        {cfg.get('tracker', 'files')}")
        for key in ("concurrency", "max_rounds", "merge_method"):
            print(f"  {key:<14} {cfg[key]}")
        print(f"  {'auto_merge':<14} {'on' if cfg.get('auto_merge', True) else 'off'}")
        cap = cfg.get("max_cost_usd")
        print(f"  max_cost_usd   {f'${cap:.2f} per job' if cap else 'off'}")
        for risk, reviewers in cfg["routing"].items():
            print(f"  review.{risk:<7} " + ", ".join(f"{k} on {v}" for k, v in reviewers.items()))
        for surface, reviewers in cfg["surface_reviewers"].items():
            print(f"  touches {surface:<8} adds " + ", ".join(f"{k} on {v}" for k, v in reviewers.items()))
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
    elif key == "auto_merge":
        if value not in {"on", "off"}:
            die("auto_merge must be on or off")
        cfg[key] = value == "on"
        new = re.sub(r'("auto_merge":\s*)(true|false)', rf"\g<1>{json.dumps(cfg[key])}", text, count=1)
        value = json.dumps(cfg[key])
    elif key == "max_cost_usd":
        if value == "off":
            cap = None
        else:
            try:
                cap = float(value)
            except ValueError:
                cap = 0.0
            if not cap > 0 or cap == float("inf"):
                die("max_cost_usd must be a dollar amount above 0, or off")
        new = re.sub(r'("max_cost_usd":\s*)[^,\n}]+', rf"\g<1>{json.dumps(cap)}", text, count=1)
        cfg[key] = cap
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
    path.write_text(new, newline="\n")  # LF on Windows too, as .gitattributes wants
    # Only this file: whatever else is staged stays staged and out of the commit.
    sh("git", "commit", "-m", f"Set {key} to {value}", "--", "factory/config.json", check=True)
    print(f"{key} is now {value} (committed as {short('HEAD')}).")
    base = cfg["base_branch"]
    if tracker() == "files" and local_origin():
        branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != base:
            print(f"Not pushed: you are on {branch}, and jobs start from {base} in origin. "
                  f"Merge this into {base} and push for it to apply.")
            return 0
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
  fx deck                    the presentation, to show the factory first
  fx up && fx run            the dashboard, then the factory
  fx queue 2 && fx run       queue issue 2, then run the factory
  fx config review.low sonnet
  fx new "Show the base price" The average of all hours, as a tile.
  fx answer 5 Yes, a block may set the price.
  fx show 1 && fx approve 1  look at a job that waits for you, then approve it
  fx feedback 1 use the same wording as the prices page
  fx reset --keep            start again, keeping your setup and a copy of this run
  fx demo                    play that copy back on the dashboard, at no cost

Run `fx <command> --help` for the details of one command."""

# name, one-line help, longer description (None = the one-liner)
COMMANDS = [
    ("setup", "set this checkout up to run the factory locally",
     "Makes this checkout its own sandbox: a repository on disk next to it becomes `origin`\n"
     "(where the factory merges), the one it came from becomes `upstream`, the demo issues go\n"
     "into factory/issues/, and the starting point for `fx reset` is recorded. `fx run` does\n"
     "this the first time; running it again changes nothing."),
    ("doctor", "check that this machine can run the factory",
     "Checks Python, git, pytest, ruff, the Claude Code version, the tracker's tool, the\n"
     "optional browser tools and whether factory/checks.sh passes. Exits 1 if something\n"
     "the factory needs is missing, and says what."),
    ("up", "start the dashboard and open it",
     "Starts the dashboard on a free port bound to 127.0.0.1 and opens it in the\n"
     "browser. Running it again reuses the same server. `fx deck` opens the presentation."),
    ("down", "stop the dashboard", None),
    ("deck", "open the presentation",
     "Opens docs/presentation.html in the browser. Show it before you start the\n"
     "dashboard with `fx up` and the factory with `fx run`."),
    ("demo", "replay a saved run on the dashboard, without running any agent",
     "Plays a finished run back on the dashboard, step by step: the jobs move along the\n"
     "line, the files appear, a job stops for a person. It reads a run that\n"
     "`fx reset --keep` saved under factory/runs/ (the last one, or the one you name) and\n"
     "writes only to factory/replay/. No agent runs, nothing is merged, and it costs nothing."),
    ("run", "open Claude Code on /factory run",
     "Opens Claude Code in this folder and types `/factory run`, or `/factory run <issue>`\n"
     "for one issue. From there the session is yours to watch and approve."),
    ("sweep", "open Claude Code on /factory sweep: propose work as unlabelled issues",
     "Opens Claude Code in this folder and types `/factory sweep <kind>`. The sweep reads\n"
     "the base branch for perf, security, cleanup or docs work and files at most\n"
     "sweep.max_issues findings (config.json) as issues WITHOUT the factory label,\n"
     "skipping any that match an open issue. Nothing it files runs until you queue it\n"
     "with `fx queue <issue>`. It writes no code."),
    ("issues", "list the issues: ref, state, labels, title", None),
    ("new", "file a new issue and queue it",
     "Files an issue with the title you give and queues it. Write the request after the\n"
     "title, pipe it in, or leave it out to type it in your editor."),
    ("queue", "label an issue so the next run picks it up", None),
    ("unqueue", "remove that label from an issue", None),
    ("status", "show the jobs and what the next run would do", None),
    ("watch", "show job files as they are written (Ctrl-C to stop)", None),
    ("retro", "count why rounds went back, and propose where a fix would go",
     "Counts the labels on every failed gate and every CHANGES review across the jobs:\n"
     "lint, a failing test, a spec gap, a redundant test and so on. A label seen often\n"
     "enough gets a proposal: the agent prompt or file a fix would most likely change.\n"
     "A person makes that change; the factory can't edit its own setup. Read-only."),
    ("bench", "replay past jobs on other models and compare: plan, report, list, clean",
     "Sets up replays of jobs the factory already did, from the same starting commit and\n"
     "the same sealed spec, with another model at one or more stations, and compares them\n"
     "with the original: gate passes, rounds, approvals, cost, why rounds went back.\n"
     "  fx bench plan <job>... --try builder=sonnet [--try builder=haiku] [--max-usd 5]\n"
     "  fx bench report <bench>\n"
     "`fx bench run` is not built yet: it needs the deterministic driver loop."),
    ("show", "a waiting job: the decision, the change, your options", None),
    ("answer", "answer the question a job asked, so it can go on",
     "For a job that waits in `needs-info`: your words are posted on the issue as the\n"
     "answer. Write them after the job, pipe them in, or leave them out to type them in\n"
     "your editor. Then run `fx run` so the factory picks the job up."),
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
    made["demo"].add_argument("run", nargs="?", help="a folder under factory/runs/, or a path; default: the last saved run")
    made["demo"].add_argument("--seconds", type=float, default=90, help="how long the whole replay takes (default 90)")
    made["demo"].add_argument("--step", action="store_true", help="wait for Enter before each step")
    made["demo"].add_argument("--no-open", action="store_true", help="don't open a browser")
    made["run"].add_argument("issue", nargs="?", help="run only this issue")
    made["sweep"].add_argument("kind", choices=["perf", "security", "cleanup", "docs"],
                               help="perf, security, cleanup or docs")
    made["queue"].add_argument("issue", help="the issue's ref, as `fx issues` shows it")
    made["unqueue"].add_argument("issue", help="the issue's ref, as `fx issues` shows it")
    made["new"].add_argument("title", help="a short imperative title, in quotes")
    made["new"].add_argument("words", nargs="*", help="the request; left out, it comes from stdin or your editor")
    for name in ("show", "answer", "approve", "feedback", "reject", "retry", "park"):
        made[name].add_argument("job", help="a job id, or its issue's ref")
    for name, what in (("answer", "your answer"), ("feedback", "what should change"), ("reject", "why it is dropped"),
                       ("park", "why it is stopped")):
        made[name].add_argument("words", nargs="*", help=f"{what}; left out, it comes from stdin or your editor")
    made["approve"].add_argument("--yes", action="store_true", help="don't ask for confirmation")
    made["retro"].add_argument("--threshold", type=int, default=3, help="propose a fix from this many (default 3)")
    made["retro"].add_argument("--all", action="store_true", help="also count runs saved by `fx reset --keep`")
    made["retro"].add_argument("--json", action="store_true", help="machine-readable output")
    made["bench"].add_argument("args", nargs=argparse.REMAINDER, help="plan, run, report, list or clean, and theirs")
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
    if cmd == "setup":
        return cmd_setup()
    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "up":
        return cmd_up(["--no-open"] if ns.no_open else [])
    if cmd == "down":
        return cmd_down()
    if cmd == "deck":
        return cmd_deck()
    if cmd == "demo":
        return cmd_demo(ns)
    if cmd == "run":
        return cmd_run([ns.issue] if ns.issue else [])
    if cmd == "sweep":
        return cmd_run([], prompt=f"/factory sweep {ns.kind}")
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
    if cmd == "bench":
        return subprocess.run(portable.script(FACTORY / "bin" / "bench", *ns.args), cwd=REPO).returncode
    if cmd == "retro":
        args = ["--threshold", ns.threshold] + (["--all"] if ns.all else []) + (["--json"] if ns.json else [])
        return subprocess.run(portable.script(FACTORY / "bin" / "retro", *args), cwd=REPO).returncode
    if cmd == "approve":
        return cmd_approve([ns.job] + (["--yes"] if ns.yes else []))
    if cmd == "retry":
        return job("merge", ns.job)
    if cmd == "new":
        return cmd_new([ns.title, *ns.words])
    if cmd == "answer":
        return decide("answer", [ns.job, *ns.words], "Your answer to the question. It is posted on the issue.")
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
