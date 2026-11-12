"""Tests for the fx command.

Run with `python3 -m pytest factory/tests -q`. They are not part of
factory/checks.sh: a job's gate tests the project, and fx belongs to the
factory around it.

Each test gets a throwaway local sandbox made by factory/local-setup.sh, with
the fx from this working tree copied in.
"""

import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "factory" / "bin"))
import portable  # noqa: E402

SCRIPTS = {"fx", "job", "gate", "route", "guard"}
GIT_ID = ["-c", "user.name=fx test", "-c", "user.email=fx@test.invalid"]


def launch(cmd):
    """The command line for one of the factory's scripts. Unix runs them by their
    shebang line; Windows can't, so there Python and Git Bash start them."""
    if not portable.WINDOWS:
        return [str(c) for c in cmd]
    name = Path(str(cmd[0])).name
    if name.endswith(".sh"):
        return [portable.bash(), *map(str, cmd)]
    if name in SCRIPTS:
        return [sys.executable, *map(str, cmd)]
    return [str(c) for c in cmd]


def run(cmd, cwd, check=True, stdin=None):
    res = subprocess.run(launch(cmd), cwd=cwd, capture_output=True, text=True, input=stdin)
    if check and res.returncode != 0:
        raise AssertionError(f"{cmd} failed ({res.returncode}):\n{res.stdout}\n{res.stderr}")
    return res


@pytest.fixture
def sandbox(tmp_path):
    """A local sandbox whose fx is the one under test, with its starting point set."""
    box = tmp_path / "box"
    run([str(REPO / "factory" / "local-setup.sh"), str(box)], cwd=REPO)
    if (REPO / "fx").read_bytes() != (box / "fx").read_bytes():
        shutil.copy2(REPO / "fx", box / "fx")
        run(["git", *GIT_ID, "commit", "-qam", "fx under test"], cwd=box)
        run(["git", "push", "-q", "origin", "main"], cwd=box)
        run(["./fx", "baseline"], cwd=box)
    yield box
    run(["./fx", "down"], cwd=box, check=False)


def fx(box, *args, check=True, stdin=None):
    return run(["./fx", *args], cwd=box, check=check, stdin=stdin)


def cfg(box):
    return json.loads((box / "factory" / "config.json").read_text())


def merge_a_job(box, issue="1"):
    """Drive one job to merged by writing the agents' files by hand."""
    job = str(box / "factory" / "bin" / "job")
    job_id = run([job, "create", issue], cwd=box).stdout.strip()
    folder, tree = box / "factory" / "jobs" / job_id, box / ".worktrees" / job_id
    (folder / "intake.md").write_text(
        "DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- ok\n")
    run([str(box / "factory" / "bin" / "route"), job_id], cwd=box)
    run([job, "advance", job_id, "spec"], cwd=box)
    (folder / "spec.md").write_text("# Spec\n")
    run([job, "advance", job_id, "build"], cwd=box)
    with open(tree / "evently" / "seed.py", "a") as f:
        f.write("# a change\n")
    run(["git", *GIT_ID, "commit", "-qam", "a change"], cwd=tree)
    run(["git", "push", "-q", "-u", "origin", f"factory/{job_id}"], cwd=tree)
    (folder / "build.md").write_text("STATUS: READY\n")
    run([job, "advance", job_id, "gate"], cwd=box)
    run([str(box / "factory" / "bin" / "gate"), job_id], cwd=box)
    run([job, "advance", job_id, "review"], cwd=box)
    (folder / "round-1" / "review-code.md").write_text("VERDICT: PASS\n")
    run([job, "advance", job_id, "approve"], cwd=box)
    (folder / "decision.md").write_text("APPROVE\n")
    run([job, "merge", job_id], cwd=box)
    run(["git", "pull", "-q", "origin", "main"], cwd=box)
    return job_id


# ---- help and errors -------------------------------------------------------

COMMANDS = ["doctor", "up", "down", "deck", "run", "issues", "queue", "unqueue",
            "status", "watch", "config", "baseline", "reset"]


def test_bare_fx_lists_every_command():
    out = run([str(REPO / "fx")], cwd=REPO).stdout
    for name in COMMANDS:
        assert f"\n    {name}" in out


@pytest.mark.parametrize("name", COMMANDS)
def test_every_command_has_help(name):
    res = run([str(REPO / "fx"), name, "--help"], cwd=REPO)
    assert res.stdout.startswith(f"usage: fx {name}")


def test_unknown_command_and_missing_argument_exit_2():
    assert run([str(REPO / "fx"), "bogus"], cwd=REPO, check=False).returncode == 2
    assert run([str(REPO / "fx"), "queue"], cwd=REPO, check=False).returncode == 2


def test_output_cut_short_by_a_pipe_leaves_no_traceback():
    res = run(["bash", "-c", f"{REPO / 'fx'} help | head -1"], cwd=REPO)
    assert "Traceback" not in res.stderr and "BrokenPipe" not in res.stderr


# ---- issues ----------------------------------------------------------------


def test_queue_and_unqueue_change_what_the_next_run_does(sandbox):
    assert "new\t2\t" not in fx(sandbox, "status").stdout
    fx(sandbox, "queue", "2")
    assert "new\t2\t" in fx(sandbox, "status").stdout
    fx(sandbox, "unqueue", "2")
    assert "new\t2\t" not in fx(sandbox, "status").stdout
    assert fx(sandbox, "queue", "99", check=False).returncode != 0


# ---- dashboard -------------------------------------------------------------


def test_up_serves_the_dashboard_on_localhost_and_down_stops_it(sandbox):
    first = fx(sandbox, "up", "--no-open").stdout
    url = first.split()[1]
    assert url.startswith("http://127.0.0.1:")
    assert urllib.request.urlopen(url, timeout=5).status == 200
    assert fx(sandbox, "up", "--no-open").stdout.split()[1] == url  # reuses the same server
    assert "stopped" in fx(sandbox, "down").stdout
    assert "not running" in fx(sandbox, "down").stdout
    with pytest.raises(OSError):
        urllib.request.urlopen(url, timeout=2)


@pytest.mark.skipif(portable.wsl(), reason="under WSL fx hands URLs to Windows, not to $BROWSER")
def test_up_opens_only_the_dashboard_and_deck_opens_the_presentation(sandbox, tmp_path, monkeypatch):
    """The presentation comes first, on its own; `fx up` must not open it again."""
    opened = tmp_path / "opened.txt"
    recorder = tmp_path / "record.py"
    recorder.write_text(f"import sys\nopen({str(opened)!r}, 'a').write(sys.argv[1] + '\\n')\n")
    monkeypatch.setenv("BROWSER", f'"{sys.executable}" "{recorder}" %s')
    up = fx(sandbox, "up").stdout
    assert "presentation" not in up
    assert [u for u in opened.read_text().split() if u] == [up.split()[1]]
    fx(sandbox, "deck")
    urls = opened.read_text().split()
    assert len(urls) == 2 and urls[1].endswith("presentation.html")


# ---- config ----------------------------------------------------------------


def test_config_shows_the_settings(sandbox):
    out = fx(sandbox, "config").stdout
    assert "concurrency" in out and "review.low" in out and "files" in out


def test_config_changes_one_setting_and_commits_it(sandbox):
    fx(sandbox, "config", "concurrency", "1")
    fx(sandbox, "config", "review.low", "sonnet")
    assert cfg(sandbox)["concurrency"] == 1
    assert cfg(sandbox)["routing"]["low"]["code"] == "sonnet"
    assert run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=sandbox).stdout == ""
    assert "Set review.low to sonnet" in run(["git", "log", "-1", "--format=%s"], cwd=sandbox).stdout
    assert "already" in fx(sandbox, "config", "concurrency", "1").stdout


@pytest.mark.parametrize("args", [("concurrency", "0"), ("concurrency", "many"), ("merge_method", "yolo"),
                                  ("review.low", "gpt"), ("nonsense", "1"), ("concurrency",)])
def test_config_refuses_bad_input(sandbox, args):
    before = (sandbox / "factory" / "config.json").read_text()
    assert fx(sandbox, "config", *args, check=False).returncode != 0
    assert (sandbox / "factory" / "config.json").read_text() == before


# ---- reset -----------------------------------------------------------------


def head(box, rev="HEAD"):
    return run(["git", "rev-parse", rev], cwd=box).stdout.strip()


def test_reset_discards_the_work_and_keeps_the_setup(sandbox):
    fx(sandbox, "config", "concurrency", "1")
    job_id = merge_a_job(sandbox)
    with open(sandbox / "AGENTS.md", "a") as f:  # an uncommitted setup edit is kept too
        f.write("\nA note for the next run.\n")

    out = fx(sandbox, "reset", "--yes", "--keep").stdout
    assert job_id in out and "factory/config.json" in out

    assert cfg(sandbox)["concurrency"] == 1
    assert (sandbox / "AGENTS.md").read_text().endswith("A note for the next run.\n")
    assert "# a change" not in (sandbox / "evently" / "seed.py").read_text()
    assert not (sandbox / "factory" / "jobs" / job_id).exists()
    assert list((sandbox / "factory" / "runs").glob(f"*/{job_id}/job.json"))
    assert "1\topen\tfactory" in fx(sandbox, "issues").stdout
    assert head(sandbox) == head(sandbox, "origin/main") == head(sandbox, "demo-start^{commit}")
    assert run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=sandbox).stdout == ""
    assert run(["git", "branch", "-a", "--list", "*factory/*"], cwd=sandbox).stdout == ""


def test_reset_setup_goes_back_to_how_the_sandbox_was_made(sandbox):
    original = (sandbox / "factory" / "config.json").read_text()
    fx(sandbox, "config", "max_rounds", "2")
    fx(sandbox, "reset", "--yes")
    assert cfg(sandbox)["max_rounds"] == 2
    fx(sandbox, "reset", "--yes", "--setup")
    assert (sandbox / "factory" / "config.json").read_text() == original
    assert head(sandbox) == head(sandbox, "origin/main")


def test_a_job_finished_before_the_starting_point_survives_a_reset(sandbox):
    first = merge_a_job(sandbox)
    fx(sandbox, "baseline")
    fx(sandbox, "queue", "2")
    second = merge_a_job(sandbox, "2")
    fx(sandbox, "reset", "--yes")
    assert (sandbox / "factory" / "jobs" / first / "job.json").exists()
    assert not (sandbox / "factory" / "jobs" / second).exists()


def test_reset_asks_first_and_does_nothing_on_no(sandbox):
    job_id = merge_a_job(sandbox)
    res = fx(sandbox, "reset", stdin="no\n", check=False)
    assert res.returncode == 1 and "nothing changed" in res.stdout
    assert (sandbox / "factory" / "jobs" / job_id).exists()


def test_reset_refuses_uncommitted_changes_outside_the_setup(sandbox):
    with open(sandbox / "evently" / "seed.py", "a") as f:
        f.write("# stray\n")
    res = fx(sandbox, "reset", "--yes", check=False)
    assert res.returncode != 0 and "evently/seed.py" in res.stderr


def test_reset_and_baseline_refuse_a_repository_that_is_not_a_local_sandbox(tmp_path):
    # A clone of this checkout: its origin is a working repository, whatever its tracker is.
    clone = tmp_path / "clone"
    run(["git", "clone", "-q", str(REPO), str(clone)], cwd=tmp_path)
    shutil.copy2(REPO / "fx", clone / "fx")
    before = run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout
    for command in (["reset", "--yes"], ["baseline"]):
        res = run(["./fx", *command], cwd=clone, check=False)
        assert res.returncode != 0 and "local sandbox" in res.stderr
    assert run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout == before


def test_reset_refuses_a_files_tracker_whose_origin_is_a_working_checkout(sandbox, tmp_path):
    clone = tmp_path / "clone-of-sandbox"  # tracker files, but origin is the sandbox's checkout
    run(["git", "clone", "-q", str(sandbox), str(clone)], cwd=tmp_path)
    res = run(["./fx", "reset", "--yes"], cwd=clone, check=False)
    assert res.returncode != 0 and "working checkout" in res.stderr


# ---- a person's decisions ------------------------------------------------------


def escalate_a_job(box, issue="1"):
    """Drive one job to needs-human, with a commit the gate has passed."""
    job = str(box / "factory" / "bin" / "job")
    job_id = run([job, "create", issue], cwd=box).stdout.strip()
    folder, tree = box / "factory" / "jobs" / job_id, box / ".worktrees" / job_id
    (folder / "intake.md").write_text(
        "DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- ok\n")
    run([str(box / "factory" / "bin" / "route"), job_id], cwd=box)
    run([job, "advance", job_id, "spec"], cwd=box)
    (folder / "spec.md").write_text("# spec\n")
    run([job, "advance", job_id, "build"], cwd=box)
    with open(tree / "evently" / "seed.py", "a") as f:
        f.write("# escalated change\n")
    run(["git", *GIT_ID, "commit", "-qam", "Change"], cwd=tree)
    run(["git", "push", "-q", "-u", "origin", f"factory/{job_id}"], cwd=tree)
    (folder / "build.md").write_text("STATUS: READY\n")
    run([job, "advance", job_id, "gate"], cwd=box)
    run([str(box / "factory" / "bin" / "gate"), job_id], cwd=box)
    run([job, "advance", job_id, "review"], cwd=box)
    (folder / "round-1" / "review-code.md").write_text("VERDICT: PASS\n")
    run([job, "advance", job_id, "approve"], cwd=box)
    (folder / "decision.md").write_text("ESCALATE\n\n## For the human\nIs this wording right?\n")
    run([job, "advance", job_id, "needs-human"], cwd=box)
    return job_id


def stage(box, job_id):
    return json.loads((box / "factory" / "jobs" / job_id / "job.json").read_text())["stage"]


def test_show_then_approve_merges_a_waiting_job(sandbox):
    job_id = escalate_a_job(sandbox)
    out = fx(sandbox, "show", "1").stdout
    assert "Is this wording right?" in out and "approve" in out
    refused = fx(sandbox, "approve", "1", check=False, stdin="")
    assert refused.returncode == 1 and "--yes" in refused.stderr and stage(sandbox, job_id) == "needs-human"
    fx(sandbox, "approve", "1", "--yes")
    assert stage(sandbox, job_id) == "merged"


def test_feedback_takes_words_from_the_command_line_or_stdin(sandbox):
    job_id = escalate_a_job(sandbox)
    out = fx(sandbox, "feedback", "1", "use", "shorter", "wording").stdout
    assert "fx run" in out and stage(sandbox, job_id) == "build"
    folder = sandbox / "factory" / "jobs" / job_id
    assert "use shorter wording" in (folder / "feedback-1.md").read_text()
    fx(sandbox, "park", job_id, stdin="the builder died")
    assert stage(sandbox, job_id) == "needs-human"
    fx(sandbox, "reject", job_id, stdin="not needed after all")
    assert stage(sandbox, job_id) == "rejected"


def test_a_decision_without_words_changes_nothing(sandbox):
    job_id = escalate_a_job(sandbox)
    res = fx(sandbox, "reject", "1", check=False, stdin="  \n")
    assert res.returncode == 1 and "nothing changed" in res.stderr and stage(sandbox, job_id) == "needs-human"


def test_help_lists_the_decision_commands(sandbox):
    out = fx(sandbox).stdout
    for name in ("show", "approve", "feedback", "reject", "retry", "park"):
        assert f"\n    {name} " in out
    assert "--yes" in fx(sandbox, "approve", "--help").stdout


def test_doctor_says_whether_the_folder_is_trusted(sandbox, tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    out = fx(sandbox, "doctor", check=False).stdout
    assert re.search(r"warn\s+folder trusted in Claude Code\s+could not tell", out)
    (home / ".claude.json").write_text(json.dumps({"projects": {}}))
    out = fx(sandbox, "doctor", check=False).stdout
    assert re.search(r"warn\s+folder trusted in Claude Code\s+not yet", out) and "guard hooks" in out
    record = {"projects": {str(sandbox.resolve()): {"hasTrustDialogAccepted": True}}}
    (home / ".claude.json").write_text(json.dumps(record))
    out = fx(sandbox, "doctor", check=False).stdout
    assert re.search(r"ok\s+folder trusted in Claude Code", out)
