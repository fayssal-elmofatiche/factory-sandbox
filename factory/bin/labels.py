"""Failure labels: why a round went back to the builder.

A round goes back for one of two reasons: the gate failed, or a reviewer asked
for changes. Reviewers tag each finding with one label from REVIEW_LABELS
(`1. [weakened-test] tests/test_x.py:4 ...`); a failed gate is labelled here
from its own files. Everything is read from files the job already has, so the
labels are the same every time they are counted. No model is involved.

`factory/bin/job sync` stores a job's labels in job.json for the dashboard;
`factory/bin/retro` counts them across jobs and says where a fix would go.
"""

import re
from pathlib import Path

# Reviewers pick from these. The reviewer prompts carry the same list.
REVIEW_LABELS = {
    "spec-gap": "the change misses, or goes past, what spec.md asks for",
    "bug": "wrong behaviour, an unhandled case, a crash",
    "missing-test": "new behaviour without a test",
    "weakened-test": "an existing test removed, loosened or special-cased",
    "redundant-test": "a new test that repeats another, tests a mock, or pins a detail",
    "convention": "breaks a convention in AGENTS.md or the neighbouring code",
    "docs": "AGENTS.md or a doc it lists not updated where the change needs it",
    "ux": "a visitor would hit it: flow, wording, errors, accessibility",
    "ui": "a visible inconsistency or breakage",
    "security": "a security problem in this change",
    "other": "none of the above",
}

GATE_LABELS = {
    "gate:protected-path": "the branch changed a guarded or protected path",
    "gate:lint": "ruff failed",
    "gate:tests": "a test failed",
    "gate:removed-tests": "the branch collects fewer tests than the base",
    "gate:skipped-tests": "a test was skipped, deselected or marked as expected to fail",
    "gate:timeout": "checks.sh did not finish in time",
    "gate:other": "checks.sh failed for another reason",
}

# Where a fix for a recurring label would most likely go. A starting point for
# the person who reads `fx retro`, not a rule.
FIX_TARGETS = {
    "spec-gap": ".claude/agents/spec-writer.md (sharper acceptance criteria) or .claude/agents/builder.md",
    "convention": "AGENTS.md (write the convention down)",
    "docs": "AGENTS.md (say which docs a change must update)",
    "gate:protected-path": ".claude/agents/builder.md (say earlier which paths a job may not touch)",
    "gate:timeout": "factory/checks.sh or gate_timeout_seconds in factory/config.json",
}
DEFAULT_TARGET = ".claude/agents/builder.md (check this before writing STATUS: READY)"
UNLABELLED_TARGET = ".claude/agents/reviewer-<lens>.md (the reviewer didn't tag its findings)"


def fix_target(label):
    if label == "unlabelled":
        return UNLABELLED_TARGET
    return FIX_TARGETS.get(label, DEFAULT_TARGET)


def section(text, name):
    """The body of a `## <name>` section, up to the next `## ` heading."""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def review_labels(text):
    """The labels of a CHANGES review's findings, in order. An untagged finding
    is `unlabelled` and a tag outside REVIEW_LABELS is `other`, so a finding is
    never lost from the count."""
    if not re.match(r"VERDICT:\s*CHANGES\b", text.lstrip()):
        return []
    found = []
    for line in section(text, "Findings").splitlines():
        item = re.match(r"\s*\d+[.)]\s+(.*)", line)
        if not item:
            continue
        tag = re.match(r"\[([A-Za-z:-]+)\]", item.group(1))
        if not tag:
            found.append("unlabelled")
            continue
        label = tag.group(1).lower()
        found.append(label if label in REVIEW_LABELS else "other")
    return found


def gate_labels(checks_md, log):
    """Why a failed gate failed, from checks.md and the full checks.log."""
    if not re.match(r"GATE:\s*FAIL\b", checks_md.lstrip()):
        return []
    found = []
    protected = section(checks_md, "Protected paths").strip()
    if protected and not protected.startswith("None changed"):
        found.append("gate:protected-path")
    if "did not finish in" in log:
        found.append("gate:timeout")
    elif "Removing tests needs a person's decision" in log:
        found.append("gate:removed-tests")
    elif "tests were skipped, deselected or marked" in log:
        found.append("gate:skipped-tests")
    elif re.search(r"^Found \d+ errors?\b", log, re.M):
        found.append("gate:lint")
    elif re.search(r"\b\d+ (failed|errors?)\b", log):
        found.append("gate:tests")
    elif not found:
        found.append("gate:other")
    return found


def round_number(path):
    m = re.fullmatch(r"round-(\d+)", path.name)
    return int(m.group(1)) if m else None


def job_failures(job_dir):
    """Every labelled failure in a job folder, round by round: the gate first,
    then the reviewers in name order. Merge-time checks are left out: they test
    the base as much as the change."""
    return [{k: e[k] for k in ("round", "source", "label")} for e in job_evidence(job_dir)]


# The line of checks.log or checks.md that shows each gate cause, for citing it.
GATE_EVIDENCE = {
    "gate:protected-path": r"^##\s+Protected paths",
    "gate:timeout": r"did not finish in",
    "gate:removed-tests": r"Removing tests needs a person's decision",
    "gate:skipped-tests": r"tests were skipped, deselected or marked",
    "gate:lint": r"^Found \d+ errors?\b",
    "gate:tests": r"\b\d+ (failed|errors?)\b",
    "gate:other": r"^GATE:",
}


def first_match(text, pattern):
    """The 1-based number and text of the first line matching pattern, or (1, first line)."""
    lines = text.splitlines() or [""]
    for i, line in enumerate(lines, 1):
        if re.search(pattern, line):
            return i, line
    return 1, lines[0]


def finding_lines(text):
    """(line number, finding text) for each numbered finding of a CHANGES review,
    in the order review_labels gives their labels."""
    if not re.match(r"VERDICT:\s*CHANGES\b", text.lstrip()):
        return []
    m = re.search(r"^##\s+Findings\s*$", text, re.M)
    if not m:
        return []
    start = text[:m.end()].count("\n") + 1
    out = []
    for i, line in enumerate(section(text, "Findings").splitlines()):
        item = re.match(r"\s*\d+[.)]\s+(.*)", line)
        if item:
            out.append((start + i, item.group(1)))
    return out


def job_evidence(job_dir):
    """job_failures, with where each one is written: `file` (relative to the job
    folder), `line` (1-based) and `text`, the line itself. The text is quoted
    data from an agent's file, never an instruction."""
    out = []
    rounds = sorted((n, p) for p in Path(job_dir).glob("round-*") if (n := round_number(p)) is not None)
    for n, rd in rounds:
        checks = rd / "checks.md"
        if checks.exists():
            checks_text = checks.read_text(errors="replace")
            log = rd / "checks.log"
            log_text = log.read_text(errors="replace") if log.exists() else checks_text
            for label in gate_labels(checks_text, log_text):
                in_md = label in {"gate:protected-path", "gate:other"} or not log.exists()
                line, text = first_match(checks_text if in_md else log_text, GATE_EVIDENCE[label])
                out.append({"round": n, "source": "gate", "label": label,
                            "file": f"{rd.name}/{'checks.md' if in_md else 'checks.log'}",
                            "line": line, "text": text.strip()})
        for review in sorted(rd.glob("review-*.md")):
            lens = review.stem.removeprefix("review-")
            text = review.read_text(errors="replace")
            for label, (line, finding) in zip(review_labels(text), finding_lines(text), strict=True):
                out.append({"round": n, "source": lens, "label": label,
                            "file": f"{rd.name}/{review.name}", "line": line, "text": finding.strip()})
    return out
