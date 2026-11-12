"""A job's acceptance tests: black-box tests of the spec's criteria.

The spec writer writes them as <job folder>/acceptance.py before any code
exists, so they come from outside the builder's context. The builder may read
them and never edit them. factory/bin/job runs them once on the base when the
spec is accepted; factory/bin/gate runs them on every build; at merge they join
the permanent suite in acceptance/.

They run hermetically, so nothing the branch or the builder writes can change
how they run or what they report:

- from a copy in a fresh temporary folder, with an empty pytest configuration
  of their own and --noconftest: no conftest.py anywhere (the worktree, the
  main checkout, the job folder) is loaded, nor the project's settings;
- with no plugins loaded from the environment: PYTEST_* variables are dropped
  and plugin autoloading is off;
- in an isolated Python (-I): PYTHON* variables, the current folder and the
  script's folder are ignored, so a sitecustomize.py, usercustomize.py or
  module in the worktree is not imported at start-up. pytest is imported
  first, then the worktree is appended to the end of the import path: the
  standard library and site-packages always win, and the worktree supplies
  only the application's own packages. A file in the worktree named like a
  module pytest or the standard library has can't stand in for it.

The user's site-packages is added only when pytest is installed there.
"""

import ast
import importlib.util
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import portable

NAME = "acceptance.py"
# A test that doesn't run proves nothing, as in factory/checks.sh.
NO_TESTS = 5  # pytest's exit code when it collected nothing
NOT_RUN = re.compile(r"\b(skipped|xfailed|xpassed|deselected)\b")


def kept_path(job_id):
    """Where `job merge` adds a job's acceptance tests to the permanent suite."""
    return f"acceptance/test_{job_id.replace('-', '_')}.py"


def problem(path):
    """Why the file can't serve as acceptance tests, or None."""
    if not path.exists():
        return f"{NAME} doesn't exist: the spec writer writes it with spec.md"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=NAME)
    except (SyntaxError, ValueError, UnicodeDecodeError) as e:
        return f"{NAME} doesn't compile: {e}"
    if not any(collected(node) for node in tree.body):
        return (f"{NAME} has no test functions pytest collects: a module-level `def test_...`, "
                f"or a `class Test...` with one")
    return None


def collected(node):
    """A test function pytest collects with its default settings."""
    def test(n):
        return isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test_")
    return test(node) or (isinstance(node, ast.ClassDef) and node.name.startswith("Test")
                          and any(test(n) for n in node.body))


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


# Started with -I: pytest first, then the worktree at the END of sys.path, so the
# standard library and site-packages are always found before anything the branch adds.
BOOTSTRAP = ("import sys, site; u = sys.argv[1]; u and site.addsitedir(u); import pytest; "
             "sys.path.append(sys.argv[2]); sys.exit(pytest.main(sys.argv[3:]))")


def user_site():
    """The user's site-packages when pytest is installed there, else "". -I
    leaves it out, and only the operator, never the branch, installs there."""
    spec = importlib.util.find_spec("pytest")
    users = site.getusersitepackages() if site.ENABLE_USER_SITE else ""
    if users and spec and spec.origin and Path(spec.origin).resolve().is_relative_to(Path(users).resolve()):
        return users
    return ""


def environment():
    """The environment without anything that loads pytest plugins or options."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTEST_")}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # for anything the tests start; -I ignores it for pytest itself
    return env


def run(worktree, tests, timeout):
    """Run the acceptance tests against a worktree: (exit code, output, seconds)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as here:
        ini = os.path.join(here, "pytest.ini")
        with open(ini, "w") as f:
            f.write("[pytest]\n")
        copy = os.path.join(here, NAME)
        shutil.copyfile(tests, copy)
        utf8 = ["-X", "utf8"] if sys.flags.utf8_mode else []
        cmd = [sys.executable, "-I", "-B", *utf8, "-c", BOOTSTRAP, user_site(), str(worktree),
               "-q", "-p", "no:cacheprovider", "--noconftest", "-o", "addopts=", "-c", ini,
               "--rootdir", str(worktree), "--import-mode=importlib", copy]
        code, out, seconds = timed(cmd, worktree, timeout, f"The acceptance tests ({NAME})", environment())
    last = out.rstrip().splitlines()[-1] if out.strip() else ""
    if code == NO_TESTS:
        out += (f"\nacceptance: pytest collected no tests from {NAME} (exit 5). A test class with __init__, "
                f"or a test pytest can't import, is never run.\n")
    elif code == 0 and NOT_RUN.search(last):
        out += "\nacceptance: tests were skipped, deselected or marked as expected failures. Every test must run and pass.\n"
        code = 1
    elif code == 0 and counts(out)[0] == 0:
        out += "\nacceptance: pytest exited 0 but reported no passed test. Every test must run and pass.\n"
        code = 1
    return code, out, seconds


def counts(out):
    """(passed, failed) from pytest's last line; errors count as failed."""
    last = out.rstrip().splitlines()[-1] if out.strip() else ""
    found = {word: int(n) for n, word in re.findall(r"(\d+) (passed|failed|errors?)\b", last)}
    return found.get("passed", 0), found.get("failed", 0) + found.get("error", 0) + found.get("errors", 0)
