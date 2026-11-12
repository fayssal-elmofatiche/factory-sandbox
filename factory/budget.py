#!/usr/bin/env python3
"""The repo's performance budget: does the branch make clearing the auction dearer?

Usage:
  python3 factory/budget.py <tree>     print a JSON report; exit 0 unless the script itself breaks

The gate runs this file from the main checkout, as it runs checks.sh, so a
branch can't change how it is measured. It measures the job's tree and
origin/<base_branch> with the same interpreter, one after the other, and
compares them against `budgets` in config.json:

  "budgets": {"clearing_ops": {"max_regression_pct": 10}}

Metrics, measured on a fixed order book (FIXTURE, seeded, 24 hours):

  clearing_ops   Python bytecode instructions executed by OrderBook.results(),
                 counted with sys.monitoring (3.12+) or sys.settrace (older).
                 A count of zero means the tracer failed: the tree counts as
                 not measured, never as within budget. Deterministic: the
                 same code gives the same number on every run and every load,
                 so a 10% budget never flakes. It counts work done in Python; time spent in C code or
                 waiting (time.sleep, I/O) is not counted.
  clearing_ms    Median wall-clock milliseconds of RUNS untraced calls. Reported
                 for information. It moves by tens of percent when the machine
                 is busy, so budget it only with a wide margin.

The report has one entry per budgeted metric and a "status":
  within       every budgeted metric is inside its margin
  over         at least one is not: a person decides whether to merge
  unmeasured   the branch's tree could not be measured (its API changed, or it
               crashed): a person decides
  skipped      no budgets, or the base could not be measured: nothing to compare
"""

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

FACTORY = Path(__file__).resolve().parent
CONFIG = json.loads((FACTORY / "config.json").read_text())
RUNS = 15
TIMEOUT = 120

# Runs in a fresh interpreter with the measured tree first on sys.path.
FIXTURE = r'''
import json, random, statistics, sys, time
from datetime import date
sys.path.insert(0, sys.argv[1])
from dayahead.orderbook import Order, OrderBook

rng = random.Random(20261112)
book = OrderBook("DE-LU", date(2026, 11, 12))
for i in range(24 * 24):
    book.add_order(Order(id=f"o{i}", participant=f"p{i % 7}", side="bid" if i % 2 else "offer",
                         hour=i % 24, volume_mwh=round(rng.uniform(1, 50), 1),
                         price=round(rng.uniform(-50, 300), 2)))

ops = 0
book.results()  # warm up
if hasattr(sys, "monitoring"):  # 3.12+: 3.12's settrace reports no opcode events this way
    mon = sys.monitoring
    def count(code, offset):
        global ops
        ops += 1
    mon.use_tool_id(mon.PROFILER_ID, "budget")
    mon.register_callback(mon.PROFILER_ID, mon.events.INSTRUCTION, count)
    mon.set_events(mon.PROFILER_ID, mon.events.INSTRUCTION)
    book.results()
    mon.set_events(mon.PROFILER_ID, 0)
else:
    def tracer(frame, event, arg):
        global ops
        frame.f_trace_opcodes = True
        if event == "opcode":
            ops += 1
        return tracer
    sys.settrace(tracer)
    book.results()
    sys.settrace(None)
if not ops:
    sys.exit(f"counted no instructions: Python {sys.version.split()[0]}'s tracer reported none")
times = []
for _ in range(int(sys.argv[2])):
    start = time.perf_counter()
    book.results()
    times.append((time.perf_counter() - start) * 1000)
print(json.dumps({"clearing_ops": ops, "clearing_ms": round(statistics.median(times), 3)}))
'''


def measure(tree):
    """The metrics for one tree, or the reason it could not be measured."""
    env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        res = subprocess.run([sys.executable, "-I", "-c", FIXTURE, str(tree), str(RUNS)],
                             capture_output=True, text=True, env=env, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"the fixture did not finish in {TIMEOUT}s"
    if res.returncode != 0:
        lines = (res.stderr or res.stdout).strip().splitlines()
        return None, lines[-1] if lines else f"exit {res.returncode}"
    return json.loads(res.stdout.strip().splitlines()[-1]), None


def base_tree(tree, into):
    """origin/<base_branch> unpacked into `into`, as checks.sh does it."""
    ref = f"origin/{CONFIG['base_branch']}"
    res = subprocess.run(["git", "archive", ref], cwd=tree, capture_output=True)
    if res.returncode != 0:
        return f"`git archive {ref}` failed: {res.stderr.decode(errors='replace').strip()}"
    with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(into, filter="data")
        else:
            tar.extractall(into)
    return None


def report(tree):
    budgets = CONFIG.get("budgets") or {}
    out = {"status": "skipped", "metrics": {}, "note": None}
    if not budgets:
        out["note"] = "no budgets in config.json"
        return out
    with tempfile.TemporaryDirectory() as base:
        err = base_tree(tree, base)
        base_m, base_err = (None, err) if err else measure(base)
        branch_m, branch_err = measure(tree)
    if branch_err:
        out.update(status="unmeasured", note=f"the branch could not be measured: {branch_err}")
        return out
    if base_err:
        out["note"] = (f"origin/{CONFIG['base_branch']} could not be measured, "
                       f"so there is nothing to compare: {base_err}")
        return out
    over = False
    for name, rule in budgets.items():
        if name not in branch_m:
            out.update(status="unmeasured",
                       note=f"config.json budgets {name!r}, which this script doesn't measure")
            return out
        margin = rule.get("max_regression_pct", 0)
        was, now = base_m[name], branch_m[name]
        change = (now - was) / was * 100 if was else (0.0 if now == was else float("inf"))
        exceeded = change > margin
        over |= exceeded
        out["metrics"][name] = {"base": was, "branch": now, "change_pct": round(change, 1),
                                "max_regression_pct": margin, "over": exceeded}
    out["info"] = {k: {"base": base_m[k], "branch": branch_m[k]} for k in branch_m if k not in budgets}
    out["status"] = "over" if over else "within"
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip())
    print(json.dumps(report(Path(sys.argv[1]).resolve())))
