"""A stand-in for `claude -p --agent <name>` in the driver's tests: no model is called.

factory/bin/drive runs it when FACTORY_DRIVE_CLAUDE names this file. It reads
the prompt on stdin, finds the job from the `Job folder:` in it, writes the
file that agent would write, and prints the JSON line `claude -p --output-format
json` prints. What it writes comes from the plan in FAKE_PLAN (JSON):

  intake     DECISION for intake.md                      (default ACCEPT)
  risk       RISK for intake.md                          (default low)
  reviews    {"<round>": "PASS" | "CHANGES"}             (default PASS)
  decision   first line of decision.md                   (default APPROVE)
  skip       {"<agent>": n}: the first n calls write nothing
  hang       {"<agent>": n}: the first n calls sleep until they are killed
  sleep      seconds every call takes                    (default 0)
  tokens     input tokens each call reports, on claude-opus-5-5, in a session
             transcript laid out like the one `claude -p` writes (default none)

Every call is appended to calls.jsonl next to the plan: agent, job, argv, prompt,
pid, start and end times.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

PLAN_PATH = Path(os.environ["FAKE_PLAN"])
PLAN = json.loads(PLAN_PATH.read_text())
LOG = PLAN_PATH.parent / "calls.jsonl"


def arg(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def nth_call(agent):
    """1 for this agent's first call, 2 for its second, and so on."""
    counter = PLAN_PATH.parent / f"count-{agent}"
    n = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(n))
    return n


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


ACCEPTANCE = ('from pathlib import Path\n\nimport dayahead.seed\n\n\n'
              'def test_ac1_the_builder_changed_the_seed():\n'
              '    """AC1: dayahead/seed.py carries the fake change."""\n'
              '    assert "fake change" in Path(dayahead.seed.__file__).read_text()\n')


def write(agent, folder, job, round_n):
    if agent == "intake":
        (folder / "intake.md").write_text(
            f"DECISION: {PLAN.get('intake', 'ACCEPT')}\nRISK: {PLAN.get('risk', 'low')}\nSURFACES: none\n"
            "RESCOPED: no\n\n## Summary\n- fake intake\n\n## Scope\nscope\n")
    elif agent == "spec-writer":
        (folder / "spec.md").write_text("# Spec\n\nfake spec\n")
        # With acceptance on (the default), the spec writer also writes black-box tests. These fail
        # on main and pass once the fake builder has changed dayahead/seed.py.
        (folder / "acceptance.py").write_text(ACCEPTANCE)
    elif agent == "builder":
        wt = Path(job["worktree"])
        with open(wt / "dayahead" / "seed.py", "a") as f:
            f.write(f"# fake change, round {round_n}\n")
        git(wt, "add", "dayahead/seed.py")
        git(wt, "commit", "-qm", f"Fake change for round {round_n}")
        git(wt, "push", "-q", "-u", "origin", job["branch"])
        (folder / "build.md").write_text(f"STATUS: READY\n\n# Build round {round_n}\n")
    elif agent.startswith("reviewer-"):
        verdict = PLAN.get("reviews", {}).get(str(round_n), "PASS")
        name = agent.removeprefix("reviewer-")
        review = folder / f"round-{round_n}" / f"review-{name}.md"
        review.write_text(f"VERDICT: {verdict}\n\n## Findings\n- fake\n")
    elif agent == "approver":
        (folder / "decision.md").write_text(f"{PLAN.get('decision', 'APPROVE')}\n\n## Summary\n- fake\n")


def session_transcript(folder, prompt, session, tokens):
    """What `claude -p` leaves in <config>/projects/<project>/<session>.jsonl: the prompt is
    the first user message, not the first line."""
    repo = folder.resolve().parents[2]
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(repo))
    d.mkdir(parents=True, exist_ok=True)
    usage = {"input_tokens": tokens, "output_tokens": 0}
    entries = [{"type": "queue-operation"},
               {"type": "user", "message": {"role": "user", "content": prompt}},
               {"type": "assistant", "message": {"id": session, "model": "claude-opus-5-5", "usage": usage}}]
    (d / f"{session}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries))


def main(argv):
    start = time.time()
    prompt = sys.stdin.read()
    agent = arg(argv, "--agent")
    folder = Path(re.search(r"Job folder: (.+?)/\. ", prompt).group(1))
    job = json.loads((folder / "job.json").read_text())
    n = nth_call(agent)
    entry = {"agent": agent, "job": job["id"], "argv": argv, "prompt": prompt, "pid": os.getpid(),
             "start": start, "call": n}
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    if n <= PLAN.get("hang", {}).get(agent, 0):
        time.sleep(600)
    time.sleep(PLAN.get("sleep", 0))
    if n > PLAN.get("skip", {}).get(agent, 0):
        write(agent, folder, job, job["round"])
    with open(LOG, "a") as f:
        f.write(json.dumps({**entry, "end": time.time()}) + "\n")
    session = str(uuid.uuid4())
    if PLAN.get("tokens"):
        session_transcript(folder, prompt, session, PLAN["tokens"])
    print(json.dumps({"type": "result", "session_id": session, "result": "done",
                      "total_cost_usd": 0}))


if __name__ == "__main__":
    main(sys.argv[1:])
