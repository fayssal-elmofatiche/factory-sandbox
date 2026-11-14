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

SCRIPTS = {"fx", "job", "gate", "route", "guard", "retro", "bench"}
GIT_ID = ["-c", "user.name=fx test", "-c", "user.email=fx@test.invalid"]
# The spec writer's acceptance tests, by hand: one that passes on the base already.
ACCEPTANCE = ('def test_ac1_the_package_imports():\n    """AC1: the package imports."""\n'
              '    import dayahead\n\n    assert dayahead\n')


def launch(cmd):
    """The command line for one of the factory's scripts. Unix runs them by their
    shebang line; Windows can't, so there Python and Git Bash start them."""
    if not portable.WINDOWS:
        return [str(c) for c in cmd]
    name = Path(str(cmd[0])).name
    if name == "bash":  # a bare "bash" may be Windows' shortcut to WSL
        return [portable.bash(), *map(str, cmd[1:])]
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
    (folder / "acceptance.py").write_text(ACCEPTANCE)
    run([job, "advance", job_id, "build"], cwd=box)
    with open(tree / "dayahead" / "seed.py", "a") as f:
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

COMMANDS = ["doctor", "up", "down", "deck", "demo", "run", "sweep", "issues", "new", "queue",
            "unqueue", "status", "watch", "retro", "bench", "config", "baseline", "reset"]


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
    res = run(["bash", "-c", f"'{(REPO / 'fx').as_posix()}' help | head -1"], cwd=REPO)
    assert "Traceback" not in res.stderr and "BrokenPipe" not in res.stderr


# ---- issues ----------------------------------------------------------------


def test_queue_and_unqueue_change_what_the_next_run_does(sandbox):
    row = "\t2\t-\t-\t"  # issue 2 in the queue: `new`, or `hold` while issue 1 has the one slot
    assert row not in fx(sandbox, "status").stdout
    fx(sandbox, "queue", "2")
    assert row in fx(sandbox, "status").stdout
    fx(sandbox, "unqueue", "2")
    assert row not in fx(sandbox, "status").stdout
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


# ---- retro -----------------------------------------------------------------


def test_retro_counts_why_rounds_went_back(sandbox):
    assert "No failures" in fx(sandbox, "retro").stdout
    job_id = "001-x"
    rd = sandbox / "factory" / "jobs" / job_id / "round-1"
    rd.mkdir(parents=True)
    (rd.parent / "job.json").write_text("{}")
    (rd / "review-code.md").write_text("VERDICT: CHANGES\n\n## Findings\n1. [bug] a.py:1 x\n")
    out = fx(sandbox, "retro", "--threshold", "1").stdout
    assert "bug" in out and "Propose" in out and f"{job_id} round 1 code" in out
    assert json.loads(fx(sandbox, "retro", "--json").stdout)["labels"]["bug"]["count"] == 1


def test_retro_report_writes_a_dated_report_and_nothing_else(sandbox):
    rd = sandbox / "factory" / "jobs" / "001-x" / "round-1"
    rd.mkdir(parents=True)
    (rd.parent / "job.json").write_text('{"stage": "merged"}')
    (rd / "review-code.md").write_text("VERDICT: CHANGES\n\n## Findings\n1. [bug] a.py:1 x\n")
    before = sorted(p.relative_to(sandbox) for p in (sandbox / "factory" / "jobs").rglob("*"))
    shown = fx(sandbox, "retro", "--report", "--print", "--threshold", "1", "--date", "2026-11-12").stdout
    assert "# Factory retro 2026-11-12" in shown and "`001-x/round-1/review-code.md:4`" in shown
    assert not (sandbox / "factory" / "retro").exists()
    out = fx(sandbox, "retro", "--report", "--threshold", "1", "--date", "2026-11-12").stdout
    assert "Wrote factory/retro/2026-11-12.md" in out
    assert (sandbox / "factory" / "retro" / "2026-11-12.md").read_text().rstrip() == shown.rstrip()
    assert sorted(p.relative_to(sandbox) for p in (sandbox / "factory" / "jobs").rglob("*")) == before


def test_bench_passes_its_arguments_through(sandbox):
    assert "No benches yet" in fx(sandbox, "bench", "list").stdout
    res = fx(sandbox, "bench", "plan", "001-nope", "--try", "builder=sonnet", check=False)
    assert res.returncode == 1 and "no job 001-nope" in res.stderr


# ---- config ----------------------------------------------------------------


def test_config_shows_the_settings(sandbox):
    out = fx(sandbox, "config").stdout
    assert "concurrency" in out and "review.low" in out and "files" in out


def test_config_changes_one_setting_and_commits_it(sandbox):
    fx(sandbox, "config", "concurrency", "2")
    fx(sandbox, "config", "review.low", "sonnet")
    assert cfg(sandbox)["concurrency"] == 2
    assert cfg(sandbox)["routing"]["low"]["code"] == "sonnet"
    assert run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=sandbox).stdout == ""
    assert "Set review.low to sonnet" in run(["git", "log", "-1", "--format=%s"], cwd=sandbox).stdout
    assert "already" in fx(sandbox, "config", "concurrency", "2").stdout


def test_config_sets_the_merge_switch(sandbox):
    assert re.search(r"auto_merge\s+on", fx(sandbox, "config").stdout)
    fx(sandbox, "config", "auto_merge", "off")
    assert cfg(sandbox)["auto_merge"] is False
    assert re.search(r"auto_merge\s+off", fx(sandbox, "config").stdout)
    assert run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=sandbox).stdout == ""


def test_config_sets_and_clears_the_cost_cap(sandbox):
    assert "max_cost_usd" in fx(sandbox, "config").stdout
    fx(sandbox, "config", "max_cost_usd", "2.5")
    assert cfg(sandbox)["max_cost_usd"] == 2.5
    assert "Set max_cost_usd to 2.5" in run(["git", "log", "-1", "--format=%s"], cwd=sandbox).stdout
    fx(sandbox, "config", "max_cost_usd", "off")
    assert cfg(sandbox)["max_cost_usd"] is None
    assert "max_cost_usd" in fx(sandbox, "config").stdout


@pytest.mark.parametrize("args", [("concurrency", "0"), ("concurrency", "many"), ("merge_method", "yolo"),
                                  ("review.low", "gpt"), ("nonsense", "1"), ("concurrency",),
                                  ("auto_merge", "maybe"), ("max_cost_usd", "0"), ("max_cost_usd", "-1"),
                                  ("max_cost_usd", "lots")])
def test_config_refuses_bad_input(sandbox, args):
    before = (sandbox / "factory" / "config.json").read_text()
    assert fx(sandbox, "config", *args, check=False).returncode != 0
    assert (sandbox / "factory" / "config.json").read_text() == before


# ---- reset -----------------------------------------------------------------


def head(box, rev="HEAD"):
    return run(["git", "rev-parse", rev], cwd=box).stdout.strip()


def test_reset_discards_the_work_and_keeps_the_setup(sandbox):
    fx(sandbox, "config", "concurrency", "2")
    job_id = merge_a_job(sandbox)
    with open(sandbox / "AGENTS.md", "a") as f:  # an uncommitted setup edit is kept too
        f.write("\nA note for the next run.\n")

    out = fx(sandbox, "reset", "--yes", "--keep").stdout
    assert job_id in out and "factory/config.json" in out

    assert cfg(sandbox)["concurrency"] == 2
    assert (sandbox / "AGENTS.md").read_text().endswith("A note for the next run.\n")
    assert "# a change" not in (sandbox / "dayahead" / "seed.py").read_text()
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
    with open(sandbox / "dayahead" / "seed.py", "a") as f:
        f.write("# stray\n")
    res = fx(sandbox, "reset", "--yes", check=False)
    assert res.returncode != 0 and "dayahead/seed.py" in res.stderr


def test_reset_and_baseline_refuse_a_repository_that_is_not_a_local_sandbox(tmp_path):
    # A clone of this checkout: its origin is a working repository, whatever its tracker is.
    clone = tmp_path / "clone"
    run(["git", "clone", "-q", str(REPO), str(clone)], cwd=tmp_path)
    shutil.copy2(REPO / "fx", clone / "fx")
    before = run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout
    for command in (["reset", "--yes"], ["baseline"]):
        res = run(["./fx", *command], cwd=clone, check=False)
        assert res.returncode != 0 and "set up" in res.stderr
    assert run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout == before


def test_reset_refuses_a_files_tracker_whose_origin_is_a_working_checkout(sandbox, tmp_path):
    clone = tmp_path / "clone-of-sandbox"  # tracker files, but origin is the sandbox's checkout
    run(["git", "clone", "-q", str(sandbox), str(clone)], cwd=tmp_path)
    res = run(["./fx", "reset", "--yes"], cwd=clone, check=False)
    assert res.returncode != 0 and "set up" in res.stderr


def fresh_clone(tmp_path, name="clone"):
    """A clone on main of this working tree's fx and factory, as a learner gets it from GitHub."""
    clone = tmp_path / name
    run(["git", "clone", "-q", "--no-checkout", str(REPO), str(clone)], cwd=tmp_path)
    head = run(["git", "rev-parse", "HEAD"], cwd=REPO).stdout.strip()
    run(["git", "checkout", "-q", "-B", "main", head], cwd=clone)
    run(["git", *GIT_ID, "config", "user.name", "fx test"], cwd=clone)
    run(["git", "config", "user.email", "fx@test.invalid"], cwd=clone)
    return clone


def test_setup_makes_the_clone_its_own_sandbox(tmp_path):
    clone = fresh_clone(tmp_path)
    came_from = run(["git", "remote", "get-url", "origin"], cwd=clone).stdout.strip()
    out = fx(clone, "setup").stdout
    assert "upstream" in out and "origin" in out and "demo issues" in out
    origin = Path(run(["git", "remote", "get-url", "origin"], cwd=clone).stdout.strip())
    assert origin.resolve() == (tmp_path / "clone.git").resolve()
    bare = run(["git", "-C", str(origin), "rev-parse", "--is-bare-repository"], cwd=tmp_path).stdout
    assert bare.strip() == "true"
    assert run(["git", "remote", "get-url", "upstream"], cwd=clone).stdout.strip() == came_from
    issues = sorted(x.name for x in (clone / "factory" / "issues").glob("*.md"))
    assert issues == [f"{n}.md" for n in range(1, 8)]
    assert "new\t1\t" in fx(clone, "status").stdout
    assert "Already set up" in fx(clone, "setup").stdout  # a second run changes nothing
    assert "Starting point" in fx(clone, "baseline").stdout  # fx reset and baseline accept it


def test_setup_works_where_git_finds_no_bare_repository_on_its_own(tmp_path, monkeypatch):
    # Some machines and terminals run git with safe.bareRepository=explicit; git -C <bare> fails there.
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'safe.bareRepository=explicit'")
    clone = fresh_clone(tmp_path)
    fx(clone, "setup")
    origin = run(["git", "remote", "get-url", "origin"], cwd=clone).stdout.strip()
    assert Path(origin).resolve() == (tmp_path / "clone.git").resolve()
    assert "Already set up" in fx(clone, "setup").stdout  # and it recognises its own origin again


def test_setup_that_refuses_leaves_the_remotes_as_they_were(tmp_path):
    clone = fresh_clone(tmp_path)
    (tmp_path / "clone.git").mkdir()  # in the way, and not a bare repository
    came_from = run(["git", "remote", "get-url", "origin"], cwd=clone).stdout.strip()
    res = fx(clone, "setup", check=False)
    assert res.returncode != 0 and "not a bare repository" in res.stderr
    assert run(["git", "remote", "get-url", "origin"], cwd=clone).stdout.strip() == came_from
    assert run(["git", "remote"], cwd=clone).stdout.split() == ["origin"]


def test_doctor_reports_the_setup_and_changes_nothing(tmp_path):
    clone = fresh_clone(tmp_path)
    before = run(["git", "remote", "-v"], cwd=clone).stdout
    out = fx(clone, "doctor", check=False).stdout
    assert "local setup" in out and "not yet" in out
    assert run(["git", "remote", "-v"], cwd=clone).stdout == before
    assert not (tmp_path / "clone.git").exists() and not (clone / "factory" / "issues").exists()


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
    (folder / "acceptance.py").write_text(ACCEPTANCE)
    run([job, "advance", job_id, "build"], cwd=box)
    with open(tree / "dayahead" / "seed.py", "a") as f:
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


def test_feedback_spec_sends_a_job_back_to_the_spec_writer(sandbox):
    job_id = escalate_a_job(sandbox)
    fx(sandbox, "feedback", "1", "--spec", "the", "tests", "are", "wrong")
    assert stage(sandbox, job_id) == "spec"
    folder = sandbox / "factory" / "jobs" / job_id
    assert (folder / "acceptance-1.py").exists() and not (folder / "acceptance.py").exists()
    assert "the tests are wrong" in (folder / "feedback-1.md").read_text()


def test_a_decision_without_words_changes_nothing(sandbox):
    job_id = escalate_a_job(sandbox)
    res = fx(sandbox, "reject", "1", check=False, stdin="  \n")
    assert res.returncode == 1 and "nothing changed" in res.stderr and stage(sandbox, job_id) == "needs-human"


def ask_a_question(box, issue="1"):
    """Drive one job to needs-info by writing intake's file by hand."""
    job = str(box / "factory" / "bin" / "job")
    job_id = run([job, "create", issue], cwd=box).stdout.strip()
    (box / "factory" / "jobs" / job_id / "intake.md").write_text(
        "DECISION: NEEDS-INFO\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- unclear\n\n"
        "## Questions\n1. Which hours count as peak?\n")
    run([job, "advance", job_id, "needs-info"], cwd=box)
    return job_id


def test_answer_posts_the_reply_and_the_job_can_go_on(sandbox):
    job_id = ask_a_question(sandbox)
    assert f"./fx answer {job_id}" in fx(sandbox, "show", "1").stdout
    assert f"./fx answer {job_id}" in fx(sandbox, "status").stdout
    out = fx(sandbox, "answer", "1", "08:00", "to", "20:00").stdout
    assert "fx run" in out
    assert "08:00 to 20:00" in (sandbox / "factory" / "issues" / "1.md").read_text()
    assert f"resume\t1\t{job_id}\tneeds-info\tauthor replied" in fx(sandbox, "status").stdout
    empty = fx(sandbox, "answer", "1", check=False, stdin=" \n")
    assert empty.returncode == 1 and "nothing changed" in empty.stderr


def test_new_files_a_queued_issue(sandbox):
    before = len(fx(sandbox, "issues").stdout.splitlines())
    out = fx(sandbox, "new", "Show the base price", "As", "a", "tile.").stdout
    ref = re.search(r"issue (\d+) filed", out).group(1)
    assert int(ref) == before + 1
    assert f"{ref}\topen\tfactory\tShow the base price" in fx(sandbox, "issues").stdout
    assert "As a tile." in (sandbox / "factory" / "issues" / f"{ref}.md").read_text()
    piped = fx(sandbox, "new", "From a pipe", stdin="The request.\n").stdout
    assert "filed and queued" in piped
    assert fx(sandbox, "new", "Nothing", check=False, stdin="").returncode == 1


# ---- demo: replay a saved run ----------------------------------------------


def replayed(box, job_id):
    return json.loads((box / "factory" / "replay" / job_id / "job.json").read_text())


def test_demo_replays_a_saved_run_without_touching_the_factory(sandbox):
    job_id = merge_a_job(sandbox)
    fx(sandbox, "reset", "--yes", "--keep")
    issues_before = fx(sandbox, "issues").stdout
    out = fx(sandbox, "demo", "--seconds", "0", "--no-open").stdout
    assert "dashboard.html?dir=replay" in out and f"{job_id}: approve -> merging" in out
    job = replayed(sandbox, job_id)
    assert job["stage"] == "merged" and job["round"] == 1 and job["merged_sha"]
    folder = sandbox / "factory" / "replay" / job_id
    for name in ("issue.md", "intake.md", "spec.md", "build.md", "decision.md", "round-1/checks.md",
                 "round-1/review-code.md", "events.jsonl"):
        assert (folder / name).exists(), name
    assert json.loads((sandbox / "factory" / "replay" / "clock.json").read_text())["done"] is True
    assert not (sandbox / "factory" / "jobs" / job_id).exists()
    assert fx(sandbox, "issues").stdout == issues_before
    assert run(["git", "status", "--porcelain"], cwd=sandbox).stdout == ""


def test_demo_step_by_step_shows_a_job_part_of_the_way(sandbox):
    job_id = merge_a_job(sandbox)
    fx(sandbox, "reset", "--yes", "--keep")
    # issue.md, the job, intake.md, intake -> spec, spec.md, spec -> build: six steps, then no more input.
    fx(sandbox, "demo", "--step", "--no-open", stdin="\n" * 6)
    job = replayed(sandbox, job_id)
    folder = sandbox / "factory" / "replay" / job_id
    assert job["stage"] == "build" and job["round"] == 1 and job["ended"] is None and not job["merged_sha"]
    assert (folder / "spec.md").exists() and not (folder / "build.md").exists()
    assert json.loads((sandbox / "factory" / "replay" / "clock.json").read_text())["done"] is False


def test_demo_without_a_saved_run_says_how_to_get_one(sandbox):
    res = fx(sandbox, "demo", "--no-open", check=False)
    assert res.returncode == 1 and "reset --keep" in res.stderr


def test_help_lists_the_decision_commands(sandbox):
    out = fx(sandbox).stdout
    for name in ("new", "show", "answer", "approve", "feedback", "reject", "retry", "park"):
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


# ---- found by the November 2026 review ---------------------------------------

def test_config_commits_only_the_config_file(sandbox):
    with open(sandbox / "dayahead" / "seed.py", "a") as f:
        f.write("# staged by hand\n")
    run(["git", "add", "dayahead/seed.py"], cwd=sandbox)
    fx(sandbox, "config", "concurrency", "2")
    committed = run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=sandbox).stdout.split()
    assert committed == ["factory/config.json"]
    assert "dayahead/seed.py" in run(["git", "diff", "--cached", "--name-only"], cwd=sandbox).stdout


def test_config_does_not_push_another_branch_onto_the_base(sandbox):
    before = run(["git", "rev-parse", "origin/main"], cwd=sandbox).stdout
    run(["git", "checkout", "-q", "-b", "side"], cwd=sandbox)
    out = fx(sandbox, "config", "concurrency", "2").stdout
    run(["git", "fetch", "-q", "origin"], cwd=sandbox)
    assert run(["git", "rev-parse", "origin/main"], cwd=sandbox).stdout == before
    assert "Not pushed" in out and "side" in out


def test_reset_leaves_claude_codes_local_files_alone(sandbox):
    local = sandbox / ".claude" / "settings.local.json"
    local.write_text('{"mine": true}\n')
    inside = sandbox / ".claude" / "worktrees" / "wip" / "notes.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("work in progress\n")
    agent = sandbox / ".claude" / "agents" / "intake.md"
    with open(agent, "a", encoding="utf-8", newline="\n") as f:
        f.write("\nA setup change.\n")
    merge_a_job(sandbox)
    fx(sandbox, "reset", "--yes")
    assert local.read_text() == '{"mine": true}\n' and inside.read_text() == "work in progress\n"
    assert "A setup change." in agent.read_text(encoding="utf-8")  # the setup is still kept
    tracked = run(["git", "ls-files", ".claude"], cwd=sandbox).stdout
    assert "settings.local.json" not in tracked and "worktrees" not in tracked


def test_reset_keeps_a_new_setup_file_and_keeps_a_deleted_one_deleted(sandbox):
    new = sandbox / ".claude" / "agents" / "reviewer-docs.md"
    new.write_text("---\nname: reviewer-docs\n---\n")
    gone = sandbox / ".claude" / "agents" / "reviewer-ui.md"
    gone.unlink()
    fx(sandbox, "reset", "--yes")
    assert new.exists() and not gone.exists()
    assert run(["git", "status", "--porcelain"], cwd=sandbox).stdout.strip() == ""


def test_reset_keeps_a_changed_setup_file_in_a_folder_git_ignores(sandbox):
    # A repository that ignores .claude/skills/ but tracks the factory's skill in it.
    with open(sandbox / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
        f.write(".claude/skills/\n")
    skill = sandbox / ".claude" / "skills" / "factory" / "SKILL.md"
    run(["git", "ls-files", "--error-unmatch", ".claude/skills/factory/SKILL.md"], cwd=sandbox)
    with open(skill, "a", encoding="utf-8", newline="\n") as f:
        f.write("\nA setup change.\n")
    stray = skill.parent / "scratch.md"  # ignored and untracked: not part of the setup
    stray.write_text("mine\n")
    merge_a_job(sandbox)
    fx(sandbox, "reset", "--yes")
    assert "A setup change." in skill.read_text(encoding="utf-8")
    tracked = run(["git", "ls-files", ".claude/skills"], cwd=sandbox).stdout
    assert "SKILL.md" in tracked and "scratch.md" not in tracked
    assert run(["git", "status", "--porcelain"], cwd=sandbox).stdout.strip() == ""


def test_reset_says_which_new_issue_files_it_deletes(sandbox):
    run([str(sandbox / "factory" / "bin" / "job"), "new-issue", "Mine"], cwd=sandbox, stdin="body")
    out = fx(sandbox, "reset", check=False, stdin="no\n").stdout
    assert "delete 1 issue file(s) written since: 8.md" in out


def test_reset_refuses_while_an_earlier_resets_copy_is_still_there(sandbox):
    git_dir = Path(run(["git", "rev-parse", "--absolute-git-dir"], cwd=sandbox).stdout.strip())
    (git_dir / "fx" / "setup-aside").mkdir(parents=True)
    res = fx(sandbox, "reset", "--yes", check=False)
    assert res.returncode != 0 and "stopped half-way" in res.stderr


def test_down_never_stops_a_process_that_is_not_the_dashboard(sandbox):
    """A process id is reused after a reboot: the recorded one may be anything by now."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        git_dir = Path(run(["git", "rev-parse", "--absolute-git-dir"], cwd=sandbox).stdout.strip())
        (git_dir / "fx").mkdir(exist_ok=True)
        (git_dir / "fx" / "dashboard.json").write_text(json.dumps({"pid": other.pid, "port": 8999}))
        assert "not running" in fx(sandbox, "down").stdout
        assert other.poll() is None
    finally:
        other.kill()
