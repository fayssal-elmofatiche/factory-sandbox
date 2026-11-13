# Setup: the factory on your machine

The factory runs entirely on your machine. Issues are Markdown files in
`factory/issues/` (or live in kata), `origin` is a repository on disk next to
the checkout, and the factory merges a job's branch there itself. A run
touches nothing outside your machine and needs no account anywhere except
Claude.

## Quick start

The scripts are tested on macOS, on Ubuntu 22.04 and 24.04, and on Windows,
either natively (see [Windows without WSL](#windows-without-wsl)) or in WSL. You need:

- git and Python 3.10 or later, with pytest and ruff
- Claude Code 2.1.257 or later, on a plan that includes Fable (intake runs on it)
- Optional, for issues that change the web page: the
  [`agent-browser`](https://github.com/vercel-labs/agent-browser) CLI, a
  browser and `lsof`. The builder and the UX and UI reviewers use them to look
  at the page. Without them they read the page's HTML instead and say so.

```bash
git clone https://github.com/fayssal-elmofatiche/factory-sandbox.git
cd factory-sandbox
./fx doctor   # check this machine
./fx deck     # the presentation
./fx up       # the dashboard
./fx run      # sets the checkout up the first time, then Claude Code on /factory run
```

On Windows PowerShell, use `.\fx.cmd` instead of `./fx`.

`./fx up` starts the dashboard on a free port bound to `127.0.0.1` and prints
its address. `./fx run` opens Claude Code and types `/factory run`; you watch
and approve from there. After a rehearsal, `./fx reset` discards the work and
puts the issues back, and keeps any change you made to the setup. `./fx reset
--setup` goes all the way back to how the sandbox was made.

## What `./fx setup` does

Your clone is the sandbox. The first `./fx run` sets it up; `./fx setup` does
the same by hand, and `./fx doctor` only says whether it has happened. Run it
on `main`. It:

- creates `<checkout>.git`, a bare repository next to the checkout, pushes
  `main` to it and makes it `origin`. The factory pushes job branches there and
  merges into its `main`.
- renames the remote you cloned from to `upstream`. The factory never uses it;
  `git pull upstream main` brings in course updates.
- copies the demo issues from `factory/demo-issues/` into `factory/issues/`.
  The first is labeled `factory`, so the first run has work to do.
- records the starting point that `./fx reset` returns to.

Running it again changes nothing.

### Windows with WSL

Do everything inside WSL, in your Linux home folder. A checkout under `/mnt/c`
is slow and its file locking is unreliable. On Ubuntu:

```bash
sudo apt update && sudo apt install -y git python3 python3-pytest pipx curl lsof
pipx install ruff && pipx ensurepath
curl -fsSL https://claude.ai/install.sh | bash
```

Open a new terminal so `ruff` and `claude` are on your `PATH`, sign in with
`claude`, then follow the quick start. The dashboard opens in your Windows
browser at the same `localhost` address.

### Windows without WSL

The factory also runs on Windows itself. Claude Code runs its shell commands in
Git Bash there, and the factory does the same for `checks.sh`. You need:

- [Git for Windows](https://git-scm.com/download/win), which brings Git Bash
- Python 3.10 or later that answers to `python3` in Git Bash. The `python3`
  Windows ships is often only a shortcut to the Microsoft Store and runs
  nothing; [uv](https://docs.astral.sh/uv/) installs a real one
- pytest and ruff
- Claude Code, signed in

In PowerShell:

```powershell
winget install --id Git.Git
winget install --id astral-sh.uv
uv python install 3.14 --default
uv tool install ruff
uv pip install --python 3.14 --system --break-system-packages pytest
winget install --id Anthropic.ClaudeCode
```

Open a new terminal, sign in with `claude`, then run the quick start. In
PowerShell and cmd use `.\fx.cmd`; in Git Bash `./fx` works, with the same
commands. `./fx doctor` checks that Git Bash finds a real `python3`.

What is different from Linux:

- The factory's scripts have no `.exe` and Windows ignores their first line, so
  the factory starts them with Python and starts `checks.sh` with Git Bash.
  A plain `bash` on Windows may be the WSL launcher, which the factory skips.
- The agents stop a dev server with `netstat` and `taskkill`, as `lsof` doesn't
  exist on Windows.
- Agents run with your Windows account and everything it can reach. WSL kept a
  wall between the factory and Windows; native Windows doesn't. Keep
  `"auto_merge": false` and run only issues you wrote yourself.

## Working with issues

An issue is a Markdown file, `factory/issues/<number>.md`:

```markdown
---
title: Show the day's base and peak price on the prices page
state: open
labels: factory
author: ada
created: 2026-11-12T09:00:00Z
---

What you want built, in plain words.
```

The factory appends its comments below the body, each between a
`<!-- comment by <name> at <time> -->` line and a `<!-- /comment -->` line, and
sets `state: closed` when the job merges. To answer a question the factory
asked, type your reply at the end of the file, below the last
`<!-- /comment -->`, or use `./fx answer <job>`.

An issue that must wait for others names them in its header, for example
`blocked_by: 3, 4`. Until every one of them is closed, `job queue` shows the
issue as `wait` with what blocks it, gives it no slot, and `job create` refuses
it. An issue closes when its job merges, so the blocked one starts on the next
run after that. A number that has no issue file keeps it waiting too. The
kata tracker has no `blocked_by` and never holds an issue.

A file the factory can't
read is skipped with a warning; other files in the folder are ignored. You can
write issue files by hand or use the commands:

```bash
./fx issues                            # list issues: number, state, labels, title
./fx queue 2                           # queue issue 2; the label is the trigger
./fx new "Short imperative title" What you want built.
```

`factory/issues/` is gitignored, like `factory/jobs/`: both are state on your
machine, not part of the project.

## How a job merges

- **Intake** posts its verdict as a comment on the issue.
- **The builder** commits and pushes its `factory/<job-id>` branch to `origin`.
- **APPROVE** makes `factory/bin/job merge` sync the branch with the base, run
  the gate again, merge the branch into the base and push. The push is the
  atomic step: git refuses it if the base moved after the sync, and the factory
  then syncs and gates again. The issue is then closed, with the merge commit noted
  on it.
- **ESCALATE** posts the decision on the issue and labels it
  `factory:needs-human`. To approve, run `./fx approve <job>`: the factory
  syncs the branch with the base, runs the gate again, merges and pushes. You
  can also merge the branch into the base and push by hand; the next
  `factory/bin/job queue` moves the job to `merged`. This works
  for a branch that has passed the gate, merged with a merge commit or a
  fast-forward.
  For anything else (a squash, a rebase, a branch that never passed the
  gate), git can't trace the result back to a gated commit: tell the factory
  with `factory/bin/job mark-merged <job> <sha>`.
- `merge_method` applies: `merge` makes a merge commit, `squash` a single
  commit, and `rebase` fast-forwards the base to the synced branch (which may
  itself contain a merge of the base; no commits are rewritten).
- If the base moves while a job merges, the factory syncs and gates again, up
  to three times. A merge that still fails waits in `needs-human`;
  `factory/bin/job merge <job>` retries it without a new round.
- Your own checkout is never touched: run `git pull` in it to see what merged.
- The first `./fx run` in a new checkout shows Claude Code's trust prompt.
  Accept it: until then Claude Code skips the hooks that keep each agent
  inside its job. `./fx doctor` shows whether the folder is trusted.

The review record is `factory/jobs/<job-id>/` and the dashboard, and the diff
is `git diff main...factory/<job-id>`.

## Another copy: `local-setup.sh`

You don't need it to run the factory. For a second, separate sandbox, for
example to rehearse without touching your own:

```bash
factory/local-setup.sh ~/factory-demo
```

It clones this checkout's current commit into `~/factory-demo` as `main` and
runs `./fx setup` there, so `~/factory-demo.git` plays its `origin`. It is a
bash script: on Windows run it in Git Bash.

## The trackers

`tracker` in `factory/config.json` picks one.

| `tracker`         | Issues live in        | Needs            |
| ----------------- | --------------------- | ---------------- |
| `files` (default) | `factory/issues/*.md` | nothing extra    |
| `kata`            | a kata project        | `kata` on `PATH` |

For kata, a local issue tracker built for agents
(`go install go.kenn.io/kata/cmd/kata@latest`), make a copy with:

```bash
factory/local-setup.sh ~/factory-demo --tracker kata
```

Kata refs are short ids such as `2f2b`. Browse the issues with `kata tui`.

## Adding a tracker

`factory/bin/job` reaches issues through one set of small classes, and nothing
else in the factory names a tool. A **tracker** holds issues: `whoami`, `get`,
`queued`, `rows`, `comment`, `label`, `close`, `create`,
`edited_after_label`. See `FilesTracker` and `KataTracker`. Write a class with
the same methods, add it to `TRACKERS` and set `"tracker"` to its name. Code
has one host, `LocalHost`: it merges into `origin` on disk.

The tests in `factory/tests/` run against the files tracker and the local
host; copy the ones that matter for a new tracker.
