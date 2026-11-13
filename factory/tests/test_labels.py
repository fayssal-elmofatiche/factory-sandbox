"""Failure labels: why a round went back to the builder.

Run with `python3 -m pytest factory/tests -q`. Pure functions on the text of
the job's files, so no sandbox is needed.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "factory" / "bin"))
import labels  # noqa: E402


def review(verdict, findings):
    return (f"VERDICT: {verdict}\n\n## Summary\n- a line\n\n## Findings\n{findings}\n\n"
            "## Notes\n1. [bug] a note, not a finding\n")


def test_tagged_findings_of_a_changes_review_are_its_labels():
    text = review("CHANGES", "1. [weakened-test] tests/test_x.py:4 an assert was removed\n"
                             "2. [spec-gap] app.py:9 criterion 3 isn't met\n"
                             "3. [Weakened-Test] tests/test_y.py:1 same again")
    assert labels.review_labels(text) == ["weakened-test", "spec-gap", "weakened-test"]


def test_a_pass_review_has_no_labels_even_with_findings():
    assert labels.review_labels(review("PASS", "1. [bug] app.py:1 something")) == []


def test_untagged_and_unknown_findings_are_counted_not_dropped():
    text = review("CHANGES", "1. app.py:1 no tag\n2. [made-up] app.py:2 not in the list\n"
                             "   continued on a second line [bug]")
    assert labels.review_labels(text) == ["unlabelled", "other"]


def test_findings_none_gives_no_labels():
    assert labels.review_labels(review("CHANGES", "None.")) == []


CHECKS = ("GATE: FAIL\nCOMMIT: abc\n\n## Summary\n- x\n\n"
          "## Protected paths\n{protected}\n\n## checks.sh\nExit 1\n")


def test_gate_failures_are_labelled_by_cause():
    clean = CHECKS.format(protected="None changed.")
    assert labels.gate_labels(clean, "app.py:1:1: F401 unused\nFound 1 error.\n") == ["gate:lint"]
    assert labels.gate_labels(clean, "All checks passed!\nF\n1 failed, 3 passed in 0.1s\n") == ["gate:tests"]
    assert labels.gate_labels(clean, "checks: this tree collects 3 tests, origin/main collects 4. "
                                     "Removing tests needs a person's decision.") == ["gate:removed-tests"]
    skipped = "checks: tests were skipped, deselected or marked"
    assert labels.gate_labels(clean, skipped) == ["gate:skipped-tests"]
    assert labels.gate_labels(clean, "factory/checks.sh did not finish in 1800s") == ["gate:timeout"]
    assert labels.gate_labels(clean, "something else entirely") == ["gate:other"]


def test_a_protected_path_change_is_its_own_label():
    text = CHECKS.format(protected="- M factory/checks.sh\n\nA job may not change these.")
    assert labels.gate_labels(text, "All checks passed!\n3 passed") == ["gate:protected-path"]
    assert labels.gate_labels(text, "Found 2 errors.") == ["gate:protected-path", "gate:lint"]


def test_a_passing_gate_has_no_labels():
    assert labels.gate_labels("GATE: PASS\n", "Found 1 error.") == []


def test_round_failures_reads_every_round_of_a_job(tmp_path):
    (tmp_path / "round-1").mkdir()
    (tmp_path / "round-1" / "checks.md").write_text(CHECKS.format(protected="None changed."))
    (tmp_path / "round-1" / "checks.log").write_text("Found 1 error.")
    (tmp_path / "round-2").mkdir()
    (tmp_path / "round-2" / "checks.md").write_text("GATE: PASS\n")
    (tmp_path / "round-2" / "review-code.md").write_text(review("CHANGES", "1. [missing-test] a.py:1 x"))
    (tmp_path / "round-2" / "review-ux.md").write_text(review("PASS", "None."))
    (tmp_path / "round-3").mkdir()
    (tmp_path / "round-3" / "checks.md").write_text("GATE: PASS\n")
    (tmp_path / "round-3" / "merge-checks.md").write_text(CHECKS.format(protected="None changed."))
    assert labels.job_failures(tmp_path) == [
        {"round": 1, "source": "gate", "label": "gate:lint"},
        {"round": 2, "source": "code", "label": "missing-test"},
    ]


def test_every_label_names_where_a_fix_would_go():
    for label in [*labels.REVIEW_LABELS, *labels.GATE_LABELS]:
        assert labels.fix_target(label).startswith((".claude/", "AGENTS.md", "factory/"))
    assert labels.fix_target("unlabelled").startswith(".claude/agents/reviewer-")


def test_the_reviewer_prompts_offer_only_labels_retro_knows():
    for prompt in sorted((ROOT / ".claude" / "agents").glob("reviewer-*.md")):
        text = prompt.read_text()
        assert "1. [<label>]" in text, prompt.name
        offered = set(re.findall(r"`\[([a-z-]+)\]`", text))
        unknown = offered - set(labels.REVIEW_LABELS)
        assert offered and not unknown, (prompt.name, unknown)
