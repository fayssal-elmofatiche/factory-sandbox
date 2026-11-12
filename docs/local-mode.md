# Local mode: the factory without GitHub

By default the factory uses GitHub for three things: issues, pull requests and
the `origin` remote. Local mode replaces all three, so a run touches nothing
outside your machine and needs no account anywhere except Claude.

| GitHub piece  | Local replacement                                          |
| ------------- | ---------------------------------------------------------- |
| Issues        | Markdown files in `factory/issues/`, or kata               |
| Pull requests | None. The factory merges the job's branch with git         |
| `origin`      | A bare repository on disk, next to the checkout            |

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
factory/local-setup.sh ~/factory-demo
cd ~/factory-demo
./fx doctor     # check this machine can run the factory
./fx deck       # open the presentation, to show what the factory does
./fx up         # start the dashboard and open it
./fx run        # open Claude Code on /factory run
```

`./fx up` starts the dashboard on a free port bound to `127.0.0.1` and prints
its address. `./fx run` opens Claude Code and types `/factory run`; you watch
and approve from there. After a rehearsal, `./fx reset` discards the work and
puts the issues back, and keeps any change you made to the setup. `./fx reset
--setup` goes all the way back to how the sandbox was made.

The setup script creates two folders: `~/factory-demo`, the working checkout,
and `~/factory-demo.git`, the bare repository that plays `origin`. It adds
three demo issues. The first is labeled `factory`, so the first run has work
to do.

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
Git Bash there, and the factory does the same for `checks.sh` and the setup
script. You need:

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

Open a new terminal, sign in with `claude`, then run the quick start in **Git
Bash**: `factory/local-setup.sh` is a bash script. After that `./fx` works in
Git Bash and `.\fx.cmd` works in PowerShell and cmd, with the same commands. `./fx doctor` checks that Git Bash finds a real
`python3`.

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
`<!-- /comment -->`, or use `factory/bin/job answer`. A file the factory can't
read is skipped with a warning; other files in the folder are ignored. You can
write issue files by hand or use the commands:

```bash
./fx issues                            # list issues: number, state, labels, title
./fx queue 2                           # queue issue 2; the label is the trigger
factory/bin/job new-issue "Short imperative title" <<'TXT'
What you want built.
TXT
```

`factory/issues/` is gitignored, like `factory/jobs/`: both are state on your
machine, not part of the project.

## What changes in the pipeline

- **Intake** posts its verdict as a comment on the issue.
- **The builder** pushes its branch to the local `origin` and opens nothing.
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
- `merge_method` still applies: `merge` makes a merge commit, `squash` a single
  commit, and `rebase` fast-forwards the base to the synced branch (which may
  itself contain a merge of the base; no commits are rewritten).
- If the base moves while a job merges, the factory syncs and gates again, up
  to three times. A merge that still fails waits in `needs-human`;
  `factory/bin/job merge <job>` retries it without a new round.
- Your own checkout is never touched: run `git pull` in it to see what merged.
- The first `./fx run` in a new sandbox shows Claude Code's trust prompt.
  Accept it: until then Claude Code skips the hooks that keep each agent
  inside its job. `./fx doctor` shows whether the folder is trusted.

Everything else is unchanged: the stages, the gate, risk routing, the review
rounds and the files in `factory/jobs/`. The dashboard works as before; job
cards show no PR link.

## What you give up

A pull request page where a person reads and comments on the change. In local
mode the review record is `factory/jobs/<job-id>/` and the dashboard, and the
diff is `git diff main...factory/<job-id>`.

## The trackers

`tracker` in `factory/config.json` picks one. `local-setup.sh` sets it for you.

| `tracker`          | Issues live in             | Needs            |
| ------------------ | -------------------------- | ---------------- |
| `github` (default) | GitHub, with pull requests | `gh`, signed in  |
| `files`            | `factory/issues/*.md`      | nothing extra    |
| `kata`             | a kata project             | `kata` on `PATH` |

For kata, a local issue tracker built for agents
(`go install go.kenn.io/kata/cmd/kata@latest`):

```bash
factory/local-setup.sh ~/factory-demo --tracker kata
```

Kata refs are short ids such as `2f2b`. Browse the issues with `kata tui`.

## Adding a tracker or a host

`factory/bin/job` reaches the outside world through two sets of small classes,
and nothing else in the factory names a tool:

- A **tracker** holds issues: `whoami`, `get`, `queued`, `rows`, `comment`,
  `label`, `close`, `create`, `edited_after_label`. See `GitHubTracker`,
  `FilesTracker` and `KataTracker`.
- A **host** merges code: `find_change`, `open_change`, `landed`, `comment`,
  `close`, `merge`, `where`. See `GitHubHost` and `LocalHost`.

For GitLab, write `GitLabTracker` and `GitLabHost` on top of `glab` or the REST
API (issues are addressed by `iid`, "Closes #n" works in a merge request, and
`merge` returns `"waiting"` when a pipeline has to pass first), add them to
`TRACKERS` and `HOSTS`, and set `"tracker": "gitlab", "host": "gitlab"`. The
tests in `factory/tests/` run against the files tracker and the local host;
copy the ones that matter for a new pair.
