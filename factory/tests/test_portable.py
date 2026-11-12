"""Tests for factory/bin/portable.py, the part that differs between Windows and Unix.

Run with `python3 -m pytest factory/tests -q`. Every test runs on every system;
each one checks the branch for the system it runs on.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "factory" / "bin"))
import portable  # noqa: E402


def test_script_runs_a_factory_script_without_its_shebang():
    cmd = portable.script(ROOT / "factory" / "bin" / "job", "help")
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert "job.json" in res.stdout + res.stderr and "Traceback" not in res.stderr  # it ran: its usage


def test_bash_is_a_bash_that_runs_here_not_wsl():
    cmd = [portable.bash(), "-c", "echo $BASH_VERSION; uname -s"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0 and res.stdout.strip()
    if portable.WINDOWS:  # Git Bash says MINGW or MSYS; System32's bash.exe would start Linux under WSL
        assert "Linux" not in res.stdout
        assert "system32" not in portable.bash().lower()


def test_alive_sees_a_running_process_and_does_not_end_it():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **portable.own_group())
    try:
        assert portable.alive(proc.pid)
        assert portable.alive(proc.pid)  # os.kill(pid, 0) would have ended it on Windows
        assert proc.poll() is None
    finally:
        portable.stop_group(proc.pid)
        proc.wait(timeout=10)
    assert not portable.alive(proc.pid)


def test_stop_group_ends_the_process_and_its_children(tmp_path):
    marker = tmp_path / "child.pid"
    code = ("import subprocess, sys, time;"
            " c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
            f" open(r'{marker}', 'w').write(str(c.pid)); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code], **portable.own_group())
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    child = int(marker.read_text())
    portable.stop_group(proc.pid)
    proc.wait(timeout=10)
    for _ in range(50):
        if not portable.alive(child):
            break
        time.sleep(0.1)
    assert not portable.alive(child)


def test_lock_lets_one_holder_in_at_a_time(tmp_path):
    path = tmp_path / ".lock"
    holder = (f"import sys, time; sys.path.insert(0, r'{ROOT / 'factory' / 'bin'}'); import portable;"
              f" f = open(r'{path}', 'w'); portable.lock(f); print('locked', flush=True); time.sleep(1.5)")
    first = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True)
    assert first.stdout.readline().strip() == "locked"
    start = time.monotonic()
    second = subprocess.run([sys.executable, "-c", holder.replace("time.sleep(1.5)", "")],
                            capture_output=True, text=True, timeout=30)
    waited = time.monotonic() - start
    first.wait(timeout=10)
    assert second.stdout.strip() == "locked" and waited > 0.5
    assert os.path.exists(path)
