"""What differs between Windows and the Unix systems (Linux, macOS, WSL).

The factory's scripts import this module for the few things the operating
systems do differently: running a sibling script, finding bash, locking a file,
and stopping a process with everything it started. Everything else in the
factory is the same on every system.

On Linux, macOS and WSL each function does what the scripts did before this
module existed. The Windows branches are used only when `os.name == "nt"`.
"""

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"


def utf8():
    """Run this script with text read and written as UTF-8.

    Before Python 3.15, Windows reads and writes files, pipes and the console
    in the ANSI code page (cp1252 on a Western machine). An issue with a curly
    quote or an arrow in it then fails to decode, or is stored garbled. UTF-8
    mode has to be on before Python starts, so start the script again with it.
    The scripts it runs (checks.sh, the tests, the other factory scripts)
    inherit the setting. Call this before anything else, and only when the
    file runs as a script.
    """
    if WINDOWS and not sys.flags.utf8_mode:
        os.environ["PYTHONUTF8"] = "1"
        try:
            sys.exit(subprocess.run([sys.executable, *sys.argv]).returncode)
        except KeyboardInterrupt:
            sys.exit(130)


def script(path, *args):
    """The command line that runs one of the factory's Python scripts.

    Windows can't run a file by its shebang line, so the script is handed to
    the Python that runs this one. That works the same everywhere.
    """
    return [sys.executable, str(path), *map(str, args)]


def bash():
    """The bash that runs checks.sh and the setup scripts.

    On Windows that is Git Bash, found through git itself. The first "bash" on
    PATH is often a shortcut to WSL instead (System32\\bash.exe or
    WindowsApps\\bash.exe): a different machine, with other tools, that can't
    read Windows paths.
    """
    if not WINDOWS:
        return "bash"
    roots = []
    try:  # <Git>/mingw64/libexec/git-core
        exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()
        if exec_path:
            roots.append(Path(exec_path).parents[2])
    except OSError:
        pass
    git = shutil.which("git")
    if git:  # git.exe lives in <Git>/cmd, <Git>/bin or <Git>/mingw64/bin
        roots += [Path(git).parent.parent, Path(git).parent.parent.parent]
    for root in roots:
        for candidate in (root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"):
            if candidate.exists():
                return str(candidate)
    found = shutil.which("bash")
    if found and not any(part in found.lower() for part in ("system32", "windowsapps")):
        return found
    sys.exit("factory: Git Bash not found. Install Git for Windows (https://git-scm.com/download/win).")


def lock(handle):
    """Hold an exclusive lock on an open file until the process exits."""
    if WINDOWS:
        import msvcrt
        import time
        while True:  # LK_LOCK gives up after ten seconds; a merge can take longer
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.2)
    import fcntl
    fcntl.flock(handle, fcntl.LOCK_EX)


def own_group():
    """Popen keyword arguments that start a process in its own group, so it and
    everything it starts can be stopped together with `stop_group`."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop_group(pid):
    """Stop a process started with `own_group`, and everything it started."""
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def alive(pid):
    """Is the process still running?

    Never os.kill(pid, 0) on Windows: there it doesn't check the process, it
    ends it.
    """
    if WINDOWS:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop(pid):
    """Ask a process to stop (SIGTERM on Unix; on Windows it is ended)."""
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def wsl():
    """Is this Linux running under WSL?"""
    return not WINDOWS and "microsoft" in os.uname().release.lower()
