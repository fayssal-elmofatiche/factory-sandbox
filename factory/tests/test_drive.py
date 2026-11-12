"""factory/bin/drive, the driver loop, with fake agents in Claude Code's place.

fake_claude.py stands in for `claude -p --agent <name>`: it writes the file the
agent would write, from a plan each test sets. The scripts, the gate and the
repo's checks are real. No model is called.
"""

import json
import re
import subprocess
import sys
import time

import pytest
from test_factory import ENV, ROOT, Sandbox, launch, sh

sys.path.insert(0, str(ROOT / "factory" / "bin"))
import portable  # noqa: E402

FAKE = ROOT / "factory" / "tests" / "fake_claude.py"
NORMAL = ["spec", "build", "gate", "review", "approve", "merging", "merged"]


class Drive:
    def __init__(self, sb, plan):
        self.sb = sb
        self.dir = sb.repo.parent / "fake"
        self.dir.mkdir(exist_ok=True)
        (self.dir / "plan.json").write_text(json.dumps(plan))
        self.env = {**ENV, "FACTORY_DRIVE_CLAUDE": str(FAKE), "FAKE_PLAN": str(self.dir / "plan.json"),
                    "FACTORY_DRIVE_POLL": "0.05"}

    def cmd(self, *args):
        return launch([self.sb.repo / "factory/bin/drive", *args])

    def run(self, *args, check=True):
        res = subprocess.run(self.cmd(*args), cwd=self.sb.repo, env=self.env, capture_output=True, text=True,
                             timeout=600)
        if check:
            assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
        return res

    def start(self, *args):
        return subprocess.Popen(self.cmd(*args), cwd=self.sb.repo, env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)

    def calls(self, agent=None):
        """The fake's calls, each once, with its end time if it finished."""
        log = self.dir / "calls.jsonl"
        if not log.exists():
            return []
        by_key = {}
        for line in log.read_text().splitlines():
            e = json.loads(line)
            by_key.setdefault((e["pid"], e["start"]), {}).update(e)
        return [c for c in by_key.values() if agent is None or c["agent"] == agent]


def queue(sb, *titles):
    refs = [sb.issue(title=t) for t in titles]
    for ref in refs:
        sb.job("label", ref, "factory")
    return refs


def moves(sb, job_id):
    events = [json.loads(x) for x in (sb.dir(job_id) / "events.jsonl").read_text().splitlines()]
    return [e["to"] for e in events if e["event"] == "advance"]


def only_job(sb):
    return sb.job("summary").stdout.splitlines()[1].split()[0]


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def test_a_job_goes_from_intake_to_merged_with_no_orchestrator(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {})
    out = drive.run().stdout
    job_id = only_job(sb)
    s = sb.state(job_id)
    assert s["stage"] == "merged" and s["merged_sha"]
    assert moves(sb, job_id) == NORMAL  # the same transitions a /factory run makes
    assert "# fake change, round 1" in sb.base_file("dayahead/seed.py")
    agents = [c["agent"] for c in drive.calls()]
    assert agents == ["intake", "spec-writer", "builder", "reviewer-code", "approver"]
    assert "merged" in out and job_id in out and "git pull" in out
    assert not (sb.repo / "factory/jobs/.drive/drive.json").exists()


def test_each_agent_gets_the_prompt_model_tools_and_guard_skill_md_gives_it(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {})
    drive.run()
    job_id = only_job(sb)
    s = sb.state(job_id)
    folder = sb.dir(job_id)
    head = (f"Job: {job_id}. Job folder: {folder}/. Main checkout: {sb.repo}. Worktree: {s['worktree']}. "
            f"Branch: factory/{job_id}. Base: origin/main. Issue: #{s['issue']}.")
    n = int(job_id[:3])
    calls = {c["agent"]: c for c in drive.calls()}
    assert calls["intake"]["prompt"] == head
    assert calls["spec-writer"]["prompt"] == head
    assert calls["builder"]["prompt"] == (f"{head} Build round 1. Port: {5100 + 10 * n}. "
                                          "This is the first round: implement spec.md.")
    assert calls["reviewer-code"]["prompt"] == f"{head} Review round 1."
    assert calls["approver"]["prompt"] == f"{head} Final round: 1. Port: {5103 + 10 * n}."
    for agent, c in calls.items():
        argv = c["argv"]
        assert argv[:3] == ["-p", "--agent", agent]
        assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
        frontmatter = (sb.repo / ".claude/agents" / f"{agent}.md").read_text()
        tools = re.search(r"^tools:\s*(.+)$", frontmatter, re.M).group(1)
        assert argv[argv.index("--allowedTools") + 1] == ",".join(t.strip() for t in tools.split(","))
        # The guard the frontmatter declares, passed again so it runs even in an untrusted folder.
        guard = re.search(r"command: '(.+/guard \S+)'", frontmatter).group(1)
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert [h["command"] for h in settings["hooks"]["PreToolUse"][0]["hooks"]] == [guard]
        assert ("Stop" in settings["hooks"]) == (agent == "builder")
        # Reviewers take their model from the job; the others from their frontmatter.
        model = argv[argv.index("--model") + 1] if "--model" in argv else None
        assert model == (s["agents"]["reviewers"]["code"] if agent == "reviewer-code" else None)


def test_changes_start_a_new_round(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {"reviews": {"1": "CHANGES"}})
    drive.run()
    job_id = only_job(sb)
    s = sb.state(job_id)
    assert s["stage"] == "merged" and s["round"] == 2
    assert moves(sb, job_id) == ["spec", "build", "gate", "review", "build", "gate", "review", "approve",
                                 "merging", "merged"]
    second = drive.calls("builder")[1]["prompt"]
    assert second.endswith("Build round 2. Port: 5110. Round 1 reviews are in round-1/. Address every "
                           "VERDICT: CHANGES, push, and update build.md for round 2.")


def test_changes_in_the_last_round_go_to_a_person(sb):
    sb.job("label", sb.issue(), "factory")
    drive = Drive(sb, {"reviews": {"1": "CHANGES", "2": "CHANGES", "3": "CHANGES"}})
    drive.run()
    s = sb.state(only_job(sb))
    assert s["stage"] == "needs-human" and s["round"] == 3
    assert len(drive.calls("builder")) == 3


def test_an_agent_whose_file_is_missing_is_resumed_once(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {"skip": {"intake": 1}, "intake": "REJECT"})
    drive.run()
    assert sb.state(only_job(sb))["stage"] == "rejected"
    first, second = drive.calls("intake")
    assert "--resume" in second["argv"]
    assert second["argv"][second["argv"].index("--resume") + 1]  # the first call's session
    assert second["prompt"].endswith(f"Your file {sb.dir(only_job(sb)) / 'intake.md'} does not exist. "
                                     "Write it now, then reply with its first line.")


def test_a_file_still_missing_after_the_resume_parks_the_job(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {"skip": {"spec-writer": 2}})
    out = drive.run().stdout
    job_id = only_job(sb)
    s = sb.state(job_id)
    assert s["stage"] == "needs-human" and s["parked_from"] == "spec"
    assert len(drive.calls("spec-writer")) == 2
    assert "spec-writer finished twice without writing" in (sb.dir(job_id) / "decision.md").read_text()
    assert "parked" in out


def test_a_killed_driver_resumes_where_it_stopped(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {"hang": {"builder": 1}})
    first = drive.start()
    for _ in range(600):
        if drive.calls("builder"):
            break
        time.sleep(0.1)
    assert drive.calls("builder"), "the builder never started"
    first.kill()  # no clean-up: SIGKILL on Unix, TerminateProcess on Windows
    first.wait()
    job_id = only_job(sb)
    assert sb.state(job_id)["stage"] == "build"
    orphan = drive.calls("builder")[0]["pid"]
    assert portable.alive(orphan)  # the builder the dead driver left behind

    out = drive.run().stdout
    assert f"stopping builder (pid {orphan})" in out
    for _ in range(50):
        if not portable.alive(orphan):
            break
        time.sleep(0.1)
    assert not portable.alive(orphan)
    s = sb.state(job_id)
    assert s["stage"] == "merged"
    assert moves(sb, job_id) == NORMAL
    resumed = drive.calls("builder")[1]["prompt"]
    assert "You are resuming this round after the factory stopped" in resumed
    assert [c["agent"] for c in drive.calls()].count("intake") == 1  # nothing ran twice but the builder


def test_only_one_driver_runs_at_a_time(sb):
    state = sb.repo / "factory/jobs/.drive"
    state.mkdir(parents=True)
    me = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (state / "drive.json").write_text(json.dumps({"pid": me.pid, "agents": {}}))
        res = Drive(sb, {}).run(check=False)
        assert res.returncode == 1 and f"another driver is running (pid {me.pid})" in res.stderr
    finally:
        me.kill()
        me.wait()


def test_concurrency_is_respected(sb):
    data = json.loads((sb.repo / "factory/config.json").read_text())
    data["concurrency"] = 2
    (sb.repo / "factory/config.json").write_text(json.dumps(data, indent=2))
    queue(sb, "First thing", "Second thing", "Third thing")
    drive = Drive(sb, {"sleep": 0.3})
    drive.run()
    jobs = [line.split()[0] for line in sb.job("summary").stdout.splitlines()[1:-1]]
    assert len(jobs) == 3 and all(sb.state(j)["stage"] == "merged" for j in jobs)
    # At no moment do agents of more than two jobs run at once.
    spans = [(c["start"], c["end"], c["job"]) for c in drive.calls()]
    for t, _, _ in spans:
        assert len({j for s, e, j in spans if s <= t < e}) <= 2
    # And two did run at once: the limit, not a queue of one.
    assert any(len({j for s, e, j in spans if s <= t < e}) == 2 for t, _, _ in spans)


def test_a_job_over_max_cost_usd_is_parked_counting_headless_sessions(sb):
    queue(sb, "Add a thing")
    drive = Drive(sb, {"tokens": 1_000_000})  # $4.00 a call on claude-opus-5-5; the cap is $5.00
    drive.run()
    job_id = only_job(sb)
    s = sb.state(job_id)
    assert s["stage"] == "needs-human" and s["parked_from"] == "spec" and s["cost_usd"] == 8.0
    assert "over max_cost_usd" in (sb.dir(job_id) / "decision.md").read_text()
    assert [c["agent"] for c in drive.calls()] == ["intake", "spec-writer"]  # no agent resumed with it


def test_a_closed_issue_parks_its_running_job(sb):
    ref = queue(sb, "Add a thing")[0]
    drive = Drive(sb, {"intake": "ACCEPT"})
    job_id = sb.job("create", ref).stdout.strip()
    sb.job("unlabel", ref, "factory")
    out = drive.run().stdout
    assert sb.state(job_id)["stage"] == "needs-human" and "parked" in out
    assert drive.calls() == []


def test_the_builders_stop_hook_finds_the_job_in_a_headless_session(sb):
    """A headless builder is the main session: its Stop names the session's transcript,
    whose first lines are not the prompt."""
    job_id = sb.to_build()
    sb.commit(job_id)
    transcript = sb.repo.parent / "session.jsonl"
    prompt = f"Job: {job_id}. Job folder: {sb.dir(job_id)}/. Build round 1."
    transcript.write_text("\n".join(json.dumps(e) for e in [
        {"type": "queue-operation"}, {"type": "attachment"},
        {"type": "user", "message": {"role": "user", "content": prompt}}]) + "\n")
    event = json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript),
                        "stop_hook_active": False})
    gate = [sb.repo / "factory/bin/gate", "--hook"]
    assert sh(gate, sb.repo, stdin=event).returncode == 0
    sb.commit(job_id, path="dayahead/broken.py", text="import os\n")  # ruff: unused import
    res = sh(gate, sb.repo, check=False, stdin=event)
    assert res.returncode == 2 and "factory/checks.sh fails in your worktree" in res.stderr
