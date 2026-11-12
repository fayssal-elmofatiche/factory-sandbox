"""fx bench: replay past jobs on other models, and compare.

Run with `python3 -m pytest factory/tests -q`. Uses the same throwaway sandbox
as test_factory.py. Nothing here starts an agent: `bench run` is not built yet
(it needs the driver loop), so a replay's files are written by hand, the way
test_factory.py writes the agents' files.
"""

import json

import pytest
from test_factory import Sandbox, sh, transcript


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def bench(sb, *args, check=True):
    return sh([str(sb.repo / "factory/bin/bench"), *args], sb.repo, check=check)


def merged_job(sb, **issue):
    job_id = sb.to_approved(**issue)
    sb.job("merge", job_id)
    return job_id


def test_plan_sets_up_one_case_per_job_and_model_choice(sb):
    job_id = merged_job(sb)
    start = [json.loads(x) for x in (sb.dir(job_id) / "events.jsonl").read_text().splitlines()
             if json.loads(x).get("to") == "spec"][0]["commit"]
    out = bench(sb, "plan", job_id, "--try", "builder=sonnet", "--try", "builder=haiku,code=sonnet",
                "--name", "b1").stdout
    assert "2 case(s)" in out
    root = sb.repo / "factory" / "bench" / "b1"
    cases = sorted(p for p in root.iterdir() if p.is_dir())
    assert len(cases) == 2
    for case in cases:
        meta = json.loads((case / "case.json").read_text())
        assert meta["job"] == job_id and meta["start_commit"] == start and meta["status"] == "planned"
        for name in ("issue.md", "intake.md", "spec.md"):
            assert (case / name).read_text() == (sb.dir(job_id) / name).read_text()
        wt = sb.repo / meta["worktree"]
        assert sh(["git", "rev-parse", "HEAD"], wt).stdout.strip() == start
    models = sorted(json.loads((c / "case.json").read_text())["models"]["builder"] for c in cases)
    assert models == ["haiku", "sonnet"]


def test_plan_refuses_a_job_that_never_started_building(sb):
    job_id = sb.job("create", sb.issue()).stdout.strip()
    res = bench(sb, "plan", job_id, "--try", "builder=sonnet", check=False)
    assert res.returncode == 1 and "no spec" in res.stderr
    assert not (sb.repo / "factory" / "bench").exists()


def test_plan_refuses_unknown_stages_and_models(sb):
    job_id = merged_job(sb)
    for choice in ("builder=gpt", "intake=sonnet", "builder", "builder=sonnet,builder=haiku"):
        res = bench(sb, "plan", job_id, "--try", choice, check=False)
        assert res.returncode != 0, choice
    assert not (sb.repo / "factory" / "bench").exists()


def test_plan_estimates_the_cost_and_refuses_over_the_cap(sb):
    job_id = merged_job(sb)
    transcript(sb, job_id, "claude-opus-5-5")  # $4.00 at opus input price
    sb.job("sync", job_id)
    out = bench(sb, "plan", job_id, "--try", "builder=haiku", "--name", "cheap").stdout
    assert "estimate" in out and "$" in out
    res = bench(sb, "plan", job_id, "--try", "builder=opus", "--max-usd", "1", "--name", "dear", check=False)
    assert res.returncode == 1 and "--max-usd" in res.stderr
    assert not (sb.repo / "factory" / "bench" / "dear").exists()


def test_report_compares_each_model_choice_with_the_original(sb):
    job_id = merged_job(sb)
    bench(sb, "plan", job_id, "--try", "builder=sonnet", "--try", "builder=haiku", "--name", "b1")
    root = sb.repo / "factory" / "bench" / "b1"
    by_model = {json.loads((c / "case.json").read_text())["models"]["builder"]: c
                for c in root.iterdir() if c.is_dir()}
    # sonnet: passes the gate in round 1, approved
    s = by_model["sonnet"]
    (s / "round-1").mkdir()
    (s / "round-1" / "checks.md").write_text("GATE: PASS\nCOMMIT: x\n")
    (s / "round-1" / "review-code.md").write_text("VERDICT: PASS\n")
    (s / "decision.md").write_text("APPROVE\n")
    # haiku: fails lint in round 1, a spec gap in round 2, and is still not approved
    h = by_model["haiku"]
    (h / "round-1").mkdir()
    (h / "round-1" / "checks.md").write_text("GATE: FAIL\nCOMMIT: x\n\n## Protected paths\nNone changed.\n")
    (h / "round-1" / "checks.log").write_text("Found 1 error.\n")
    (h / "round-2").mkdir()
    (h / "round-2" / "checks.md").write_text("GATE: PASS\nCOMMIT: y\n")
    (h / "round-2" / "review-code.md").write_text("VERDICT: CHANGES\n\n## Findings\n1. [spec-gap] a.py:1 x\n")
    meta = json.loads((h / "case.json").read_text())
    meta["cost_usd"] = 0.25
    (h / "case.json").write_text(json.dumps(meta))

    out = bench(sb, "report", "b1").stdout
    assert "as run" in out and "builder=sonnet" in out and "builder=haiku" in out
    assert "gate:lint" in out and "spec-gap" in out
    assert "acceptance tests" in out  # says what "passed" means today
    data = json.loads(bench(sb, "report", "b1", "--json").stdout)
    rows = {r["try"]: r for r in data["rows"]}
    assert rows["builder=sonnet"]["approved"] == 1 and rows["builder=sonnet"]["rounds"] == [1]
    assert rows["builder=haiku"]["approved"] == 0 and rows["builder=haiku"]["rounds"] == [2]
    assert rows["builder=haiku"]["cost_usd"] == 0.25
    assert rows["builder=haiku"]["failures"] == {"gate:lint": 1, "spec-gap": 1}
    assert rows["as run"]["approved"] == 1


def test_a_planned_case_reports_as_not_run(sb):
    job_id = merged_job(sb)
    bench(sb, "plan", job_id, "--try", "builder=sonnet", "--name", "b1")
    data = json.loads(bench(sb, "report", "b1", "--json").stdout)
    row = [r for r in data["rows"] if r["try"] == "builder=sonnet"][0]
    assert row["cases"] == 1 and row["ran"] == 0


def test_run_says_it_needs_the_driver_loop(sb):
    job_id = merged_job(sb)
    bench(sb, "plan", job_id, "--try", "builder=sonnet", "--name", "b1")
    res = bench(sb, "run", "b1", check=False)
    assert res.returncode == 2 and "driver loop" in res.stderr


def test_list_and_clean(sb):
    job_id = merged_job(sb)
    bench(sb, "plan", job_id, "--try", "builder=sonnet", "--name", "b1")
    assert "b1" in bench(sb, "list").stdout
    case = next((sb.repo / "factory/bench/b1").glob("*/case.json"))
    wt = sb.repo / json.loads(case.read_text())["worktree"]
    assert wt.exists()
    bench(sb, "clean", "b1")
    assert not wt.exists()
    assert (sb.repo / "factory" / "bench" / "b1").exists()  # results are kept; only worktrees go


def test_a_job_from_before_events_jsonl_starts_where_its_branch_forked(sb):
    job_id = merged_job(sb)
    events = sb.dir(job_id) / "events.jsonl"
    lines = [json.loads(x) for x in events.read_text().splitlines()]
    start = [e for e in lines if e.get("to") == "spec"][0]["commit"]
    events.unlink()
    sb.move_base()  # the base moved on after the job merged; the fork point must not
    bench(sb, "plan", job_id, "--try", "builder=sonnet", "--name", "old")
    meta = json.loads(next((sb.repo / "factory/bench/old").glob("*/case.json")).read_text())
    assert meta["start_commit"] == start
