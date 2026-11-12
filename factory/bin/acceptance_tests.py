"""A job's acceptance tests: black-box tests of the spec's criteria.

The spec writer writes them as <job folder>/acceptance.py before any code
exists, so they come from outside the builder's context. The builder may read
them and never edit them. factory/bin/job runs them once on the base when the
spec is accepted; factory/bin/gate runs them on every build; at merge they join
the permanent suite in acceptance/.

They run with an empty pytest configuration of their own, so neither the
project's settings nor any conftest.py can change how they run, and with the
worktree first on the import path, so they test the branch and nothing else.
"""

import ast
import os
import re
import subprocess
import sys
import tempfile
import time

import portable

NAME = "acceptance.py"
# A test that doesn't run proves nothing, as in factory/checks.sh.
NOT_RUN = re.compile(r"\b(skipped|xfailed|xpassed|deselected)\b")


def problem(path):
    """Why the file can't serve as acceptance tests, or None."""
    if not path.exists():
        return f"{NAME} doesn't exist: the spec writer writes it with spec.md"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=NAME)
    except (SyntaxError, ValueError, UnicodeDecodeError) as e:
        return f"{NAME} doesn't compile: {e}"
    if not any(isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
               for node in ast.walk(tree)):
        return f"{NAME} has no test functions (test_...)"
    return None


def timed(cmd, cwd, timeout, what, env=None):
    """Run a command with a time limit. One that hangs fails with exit 124."""
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, **portable.own_group())
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        portable.stop_group(proc.pid)
        out, _ = proc.communicate()
        out += f"\n{what} did not finish in {timeout}s (gate_timeout_seconds in config.json); stopped.\n"
        code = 124
    return code, out, int(time.monotonic() - start)


def run(worktree, tests, timeout):
    """Run the acceptance tests against a worktree: (exit code, output, seconds)."""
    env = {**os.environ, "PYTHONPATH": str(worktree), "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory() as empty:
        ini = os.path.join(empty, "pytest.ini")
        with open(ini, "w") as f:
            f.write("[pytest]\n")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
               "-c", ini, "--rootdir", str(worktree), "--import-mode=importlib", str(tests)]
        code, out, seconds = timed(cmd, worktree, timeout, f"The acceptance tests ({NAME})", env)
    last = out.rstrip().splitlines()[-1] if out.strip() else ""
    if code == 0 and NOT_RUN.search(last):
        out += "\nacceptance: tests were skipped, deselected or marked as expected failures. Every test must run and pass.\n"
        code = 1
    return code, out, seconds


def counts(out):
    """(passed, failed) from pytest's last line; errors count as failed."""
    last = out.rstrip().splitlines()[-1] if out.strip() else ""
    found = {word: int(n) for n, word in re.findall(r"(\d+) (passed|failed|errors?)\b", last)}
    return found.get("passed", 0), found.get("failed", 0) + found.get("error", 0) + found.get("errors", 0)
