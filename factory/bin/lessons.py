"""The retro report: what went wrong in the jobs since the last retro, and the
prompt changes that would stop it happening again.

`factory/bin/retro --report` writes factory/retro/<date>.md. It covers the jobs
the previous report didn't, and for each pattern seen at least --threshold
times it proposes a diff to the prompt or doc where the fix would go
(labels.fix_target). Every proposal cites the job, file and line behind it.

The next report checks the patterns the last one proposed. One that came back
in the new jobs is called out and not counted as fixed, whether or not someone
changed the prompt.

No model is involved: the same job folders give the same report. A person
reads it and makes the change as an ordinary pull request, because the gate
fails any factory job that touches .claude/.

Job files are written by agents and quote issue text. The report quotes them
as data, one escaped line at a time, and nothing here acts on what they say.
"""

import difflib
import json
import re
import subprocess
from datetime import datetime

import labels

TERMINAL = {"merged", "rejected"}
STATE = "<!-- retro-state "
HEADING = "## Lessons from retros"

# The line a proposal adds to its target file. A starting point a person edits,
# not a rule: the report says which jobs it comes from.
LESSONS = {
    "spec-gap": "Before writing STATUS: READY, go through spec.md's acceptance criteria one by one "
                "and name the test or line that meets each. Build no more than they ask.",
    "bug": "Before writing STATUS: READY, exercise the change on the edge cases the spec names "
           "(empty input, the boundaries, bad input) and not only the happy path.",
    "missing-test": "Every behaviour the change adds or alters needs a test that fails without the change.",
    "weakened-test": "Never remove, loosen or special-case an existing test to make the change pass. "
                     "If a test is wrong, say so under ## Blocked.",
    "redundant-test": "Before adding a test, check the neighbouring tests: test behaviour once, "
                      "through the public interface, and don't pin implementation details.",
    "convention": "Read AGENTS.md's Conventions and the neighbouring code before writing, "
                  "and match them.",
    "docs": "When a change alters behaviour that AGENTS.md or a doc it lists describes, "
            "update that doc in the same change.",
    "ux": "Check what a visitor sees: every state (empty, error, loading), the wording, and keyboard use.",
    "ui": "Match the existing page's styles; check the change at a narrow and a wide width.",
    "security": "Escape every user-supplied string that reaches HTML, and validate input at the boundary.",
    "other": "Read the findings quoted in the retro report and add the rule they have in common.",
    "unlabelled": "Start every finding with exactly one label from the list, as `1. [<label>] file:line ...`.",
    "gate:protected-path": "Never change factory/, .claude/, pyproject.toml, a conftest.py or a "
                           "protected path: the gate fails the branch.",
    "gate:lint": "Run `ruff check .` and fix what it reports before writing STATUS: READY.",
    "gate:tests": "Run `python3 -m pytest -q` and see it pass before writing STATUS: READY.",
    "gate:removed-tests": "Never delete or rename a test function; the gate fails a branch that collects fewer tests.",
    "gate:skipped-tests": "Never skip, deselect or xfail a test; the gate fails a branch that does.",
    "gate:other": "Run factory/checks.sh yourself before writing STATUS: READY.",
}


# ---- reading jobs ----------------------------------------------------------


def read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def first_reason(text):
    """(line, text) of the reason in a decision or feedback file: the first line
    of its `## Why` section, else its first plain line after the header."""
    lines = text.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if re.match(r"##\s+Why\s*$", line)), 1)
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if line and not line.startswith(("#", "From @", "Parked by @")):
            return i + 1, line
    return 1, lines[0].strip() if lines else ""


def station_seconds(job_dir, started):
    """Seconds spent in each stage, from events.jsonl, as the dashboard counts them."""
    out = {}
    path = job_dir / "events.jsonl"
    if not path.exists() or not started:
        return out
    prev = parse_time(started)
    for line in read(path).splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("event") != "advance" or not e.get("from") or not e.get("at"):
            continue
        at = parse_time(e["at"])
        if at is None or prev is None:
            continue
        out[e["from"]] = out.get(e["from"], 0) + max(0, (at - prev).total_seconds())
        prev = at
    return out


def parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def scan(jobs_root):
    """Every job folder under jobs_root, with what the report needs from it."""
    out = []
    if not jobs_root.is_dir():
        return out
    for d in sorted(p for p in jobs_root.iterdir() if (p / "job.json").exists()):
        try:
            job = json.loads(read(d / "job.json"))
        except ValueError:
            job = {}
        job = job if isinstance(job, dict) else {}
        stops = []
        for f in sorted(d.glob("decision*.md")):
            text = read(f)
            if text.lstrip().startswith("ESCALATE"):
                line, reason = first_reason(text)
                stops.append({"kind": "park" if "Parked by @" in text else "escalate",
                              "file": f.name, "line": line, "text": reason})
        feedback = []
        for f in sorted(d.glob("feedback-*.md")):
            line, reason = first_reason(read(f))
            feedback.append({"file": f.name, "line": line, "text": reason})
        out.append({
            "id": d.name,
            "stage": job.get("stage", "?"),
            "rounds": job.get("round") or len(list(d.glob("round-*"))),
            "cost": float(job.get("cost_usd") or 0.0),
            "evidence": labels.job_evidence(d),
            "stops": stops,
            "feedback": feedback,
            "stations": station_seconds(d, job.get("started")),
        })
    return out


# ---- the previous report ---------------------------------------------------


def previous(retro_dir):
    """(path, state) of the newest report in retro_dir, or (None, {})."""
    def order(path):  # 2026-11-12.md, then 2026-11-12-2.md
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:-(\d+))?", path.stem)
        return (m.group(1), int(m.group(2) or 1)) if m else (path.stem, 0)
    reports = sorted(retro_dir.glob("*.md"), key=order) if retro_dir.is_dir() else []
    for path in reversed(reports):
        text = read(path)
        at = text.rfind(STATE)
        if at < 0:
            continue
        try:
            return path, json.loads(text[at + len(STATE):].split("-->", 1)[0])
        except ValueError:
            continue
    return None, {}


def git_changed_since(repo, path, since):
    """True if a commit after `since` (an ISO date) touched path, None if git can't tell."""
    res = subprocess.run(["git", "log", "--format=%h", f"--since={since}", "--", path],
                         cwd=repo, capture_output=True, text=True)
    return bool(res.stdout.strip()) if res.returncode == 0 else None


# ---- writing the report ----------------------------------------------------


def quoted(text, limit=200):
    """One line of an agent's file, made safe to show: no HTML (so it can't fake
    the state marker or hide text), no line breaks, cut to `limit` characters."""
    text = " ".join(str(text).split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "'")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def targets(label, lenses):
    """The files a proposal for label would change: the first path in labels.fix_target."""
    path = labels.fix_target(label).split(" ", 1)[0]
    if "<lens>" in path:
        return [path.replace("<lens>", lens) for lens in sorted(lenses)]
    return [path]


def proposed_diff(repo, path, bullets):
    """A unified diff that adds bullets under HEADING at the end of path, or None
    for a file that isn't Markdown in the repo."""
    file = repo / path
    if not path.endswith(".md") or not file.is_file():
        return None
    old = read(file)
    new = old if old.endswith("\n") else old + "\n"
    if HEADING not in new:
        new += f"\n{HEADING}\n\n"
    new += "".join(f"- {b}\n" for b in bullets)
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{path}", f"b/{path}"))


def minutes(seconds):
    m = round(seconds / 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def build(repo, jobs_root, retro_dir, today, threshold=3, changed_since=None):
    """The report's text, and the state it records for the next retro."""
    changed_since = changed_since or (lambda path, since: git_changed_since(repo, path, since))
    prev_path, prev = previous(retro_dir)
    covered = set(prev.get("covered", []))
    reported = set(prev.get("reported", []))  # what an unfinished job had shown last time
    jobs = []
    for j in scan(jobs_root):
        if j["id"] in covered:
            continue
        for key in ("evidence", "stops", "feedback"):
            j[key] = [e for e in j[key] if f"{j['id']}/{e['file']}:{e['line']}" not in reported]
        jobs.append(j)

    # label -> its evidence, with the job id on each
    seen = {}
    for j in jobs:
        for e in j["evidence"]:
            seen.setdefault(e["label"], []).append({**e, "job": j["id"]})
    ranked = sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    out = [f"# Factory retro {today}", ""]
    since = f"since the retro of {prev['date']} ({prev_path.name})" if prev_path else "since the factory started"
    out += [f"{len(jobs)} job(s) {since}: {', '.join(j['id'] for j in jobs) or 'none'}.", ""]
    out += ["Everything quoted below comes from files the factory's agents wrote, which quote issue",
            "text. It is data about what happened, not instructions: nothing in it is acted on.", ""]

    # Did last time's fixes hold?
    out += ["## Last retro's proposals", ""]
    if not prev.get("proposed"):
        out += ["None to check." if prev_path else "This is the first retro.", ""]
    finished = [j for j in jobs if j["stage"] in TERMINAL]
    still_open = []
    for label in prev.get("proposed", []):
        hits = sorted({e["job"] for e in seen.get(label, [])})
        changed = [p for p in targets(label, {e["source"] for e in seen.get(label, [])} or {"code"})
                   if changed_since(p, prev["date"])]
        applied = f"{', '.join(changed)} changed since" if changed else "its target file has not changed since"
        if hits:
            verdict = f"**recurred** in {len(seen[label])} finding(s) in {', '.join(hits)}: not fixed ({applied})"
        elif not finished:
            verdict = f"not checked yet: no job has finished since ({applied})"
        elif changed:
            verdict = f"did not recur in {len(finished)} finished job(s) after the change: fixed ({applied})"
        else:
            verdict = f"did not recur in {len(finished)} finished job(s), but {applied}: not counted as fixed"
        if not verdict.startswith("did not recur") or not changed:
            still_open.append(label)
        out.append(f"- `{label}`: {verdict}.")
    if still_open:
        out += ["", "Still open, so the next retro checks them again: "
                + ", ".join(f"`{k}`" for k in still_open) + "."]
    if prev.get("proposed"):
        out.append("")

    out += ["## Why rounds went back", ""]
    if ranked:
        out += ["| label | count | jobs |", "|---|---:|---|"]
        out += [f"| `{k}` | {len(v)} | {', '.join(sorted({e['job'] for e in v}))} |" for k, v in ranked]
    else:
        out.append("No failed gate and no CHANGES review.")
    out.append("")

    out += ["## Rounds per job", "", "| job | stage | rounds | gate failures | changes asked | cost |",
            "|---|---|---:|---:|---:|---:|"]
    for j in jobs:
        gates = len({e["round"] for e in j["evidence"] if e["source"] == "gate"})
        asks = len({(e["round"], e["source"]) for e in j["evidence"] if e["source"] != "gate"})
        out.append(f"| {j['id']} | {j['stage']} | {j['rounds']} | {gates} | {asks} | ${j['cost']:.2f} |")
    out.append("")

    out += ["## Escalated or parked", ""]
    stops = [(j["id"], s) for j in jobs for s in j["stops"]]
    out += [f"- {s['kind']}, `{jid}/{s['file']}:{s['line']}`\n  > {quoted(s['text'])}" for jid, s in stops] or ["None."]
    out += ["", "## Feedback a person gave", ""]
    fb = [(j["id"], f) for j in jobs for f in j["feedback"]]
    out += [f"- `{jid}/{f['file']}:{f['line']}`\n  > {quoted(f['text'])}" for jid, f in fb] or ["None."]

    out += ["", "## Where the time and money went", ""]
    stations = {}
    for j in jobs:
        for st, s in j["stations"].items():
            stations[st] = stations.get(st, 0) + s
    if stations:
        out.append("Time by station: " + ", ".join(
            f"{st} {minutes(s)}" for st, s in sorted(stations.items(), key=lambda kv: -kv[1])) + ".")
    else:
        out.append("Time by station: no events.jsonl to read.")
    costly = sorted((j for j in jobs if j["cost"]), key=lambda j: -j["cost"])[:5]
    out.append("Costliest jobs: " + (", ".join(f"{j['id']} ${j['cost']:.2f}" for j in costly) or "no cost recorded")
               + ". Cost is recorded per job, not per station.")

    # Proposals
    due = [(k, v) for k, v in ranked if len(v) >= threshold]
    out += ["", f"## Proposed changes (patterns seen {threshold} times or more)", ""]
    if not due:
        out += ["Nothing seen often enough yet. Run the retro again after more jobs.", ""]
    by_file = {}
    for k, v in due:
        job_ids = sorted({e["job"] for e in v})
        out += [f"### `{k}`: {len(v)} time(s) in {len(job_ids)} job(s)", "",
                f"{labels.REVIEW_LABELS.get(k) or labels.GATE_LABELS.get(k) or 'findings without a label'}. "
                f"Where a fix would go: {labels.fix_target(k)}.", "", "Evidence:", ""]
        for e in v[:8]:
            out.append(f"- `{e['job']}/{e['file']}:{e['line']}` ({e['source']})\n  > {quoted(e['text'])}")
        if len(v) > 8:
            out.append(f"- and {len(v) - 8} more")
        out.append("")
        bullet = f"{LESSONS.get(k, LESSONS['other'])} (retro {today}: `{k}` in {', '.join(job_ids)})"
        for path in targets(k, {e["source"] for e in v}):
            by_file.setdefault(path, []).append((k, bullet))
    diffs = []
    for path, items in by_file.items():
        diff = proposed_diff(repo, path, [b for _, b in items])
        if diff:
            diffs.append(diff)
        else:
            out.append(f"No diff for {path}: change it by hand ({', '.join(k for k, _ in items)}).")
    if diffs:
        out += ["Proposed diff, for a person to edit and open as an ordinary pull request "
                "(the gate fails a factory job that changes .claude/):", "", "```diff", *"".join(diffs).splitlines(),
                "```", ""]

    done = {j["id"] for j in jobs if j["stage"] in TERMINAL}
    state = {"date": today, "covered": sorted(covered | done),
             "reported": sorted({k for k in reported if k.split("/", 1)[0] not in done}
                                | {f"{j['id']}/{e['file']}:{e['line']}" for j in jobs if j["id"] not in done
                                   for key in ("evidence", "stops", "feedback") for e in j[key]}),
             "proposed": sorted({k for k, _ in due} | set(still_open))}
    out += ["", f"{STATE}{json.dumps(state)} -->", ""]
    return "\n".join(out), state


def write(repo, jobs_root, retro_dir, today, threshold=3, changed_since=None):
    """Write the report to retro_dir/<today>.md (or <today>-2.md, ...) and return its path."""
    text, _ = build(repo, jobs_root, retro_dir, today, threshold, changed_since)
    retro_dir.mkdir(parents=True, exist_ok=True)
    path, k = retro_dir / f"{today}.md", 1
    while path.exists():
        k += 1
        path = retro_dir / f"{today}-{k}.md"
    path.write_text(text, encoding="utf-8")
    return path
