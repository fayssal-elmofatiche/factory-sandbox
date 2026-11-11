# factory-sandbox

A small, working demo of an AI software factory built on [Claude Code](https://claude.com/claude-code).
A written request goes in, and a tested, reviewed code change comes out. Every
step leaves a file you can read afterwards.

The factory is [ZazenCodes' Software Factory v2](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2),
installed and tailored to a toy app called **evently**. This repo exists to show
the factory doing real work, and to be a place to try it.

**New here?** Open [docs/presentation.html](docs/presentation.html) in a browser,
or run `./fx deck`. It draws the factory as a control-room board. The camera
moves around the board and a job travels the line as you press the arrow key.
You can trip the gate, try illegal moves on the state machine and switch
reviewers in and out. It is a single file and works offline.

## Quick start

The fastest way to see it run is the local mode, which needs no GitHub account:

```bash
git clone https://github.com/fayssal-elmofatiche/factory-sandbox.git
cd factory-sandbox
factory/local-setup.sh ~/factory-demo
cd ~/factory-demo
./fx doctor     # check this machine can run the factory
./fx up         # start the dashboard, open it and the presentation
./fx run        # open Claude Code on /factory run
```

You need git, Python 3.10+ with pytest and ruff, and Claude Code 2.1.257 or
later on a plan that includes Fable. `./fx doctor` checks all of that and says
what is missing. The scripts are tested on macOS and Ubuntu; on Windows use WSL. See
[docs/local-mode.md](docs/local-mode.md) for the WSL steps.

## How it works

Each request becomes a job that moves through fixed stations:

```
intake → route → spec → build → gate → review → approve → merge
```

| Station | Who                        | What it produces                                        |
| ------- | -------------------------- | ------------------------------------------------------- |
| intake  | agent                      | accept, reject or ask for more, posted on the issue     |
| route   | `factory/bin/route` script | which reviewers run, picked from the risk rating        |
| spec    | agent                      | a spec with numbered acceptance criteria                |
| build   | agent                      | commits on a branch in its own git worktree             |
| gate    | `factory/bin/gate` script  | the result of `factory/checks.sh` and a protected-paths check |
| review  | routed agents              | code, security, UX and UI reviews, as risk and the diff demand |
| approve | agent                      | approve, or hand the decision to a person               |
| merge   | `factory/bin/job` script   | sync with `main`, run the gate again, merge             |

Three kinds of actor are involved:

- **The orchestrator** is the Claude Code session where you type `/factory run`.
  It follows a written playbook, `.claude/skills/factory/SKILL.md`. It starts
  one agent per station, and after each agent it calls a script. Its playbook
  forbids it to write code, specs, reviews or decisions, or to edit a job's
  record. It only coordinates.
- **Agents** are fresh AI sessions, one per station, defined in
  `.claude/agents/`. Each does one task, writes one file and ends. They never
  talk to each other: the next agent reads the files the earlier ones wrote.
- **Scripts** are ordinary programs in `factory/bin/`. `job` holds each job's
  stage and refuses a move unless the file that justifies it is there. `gate`
  runs the checks. `route` picks the reviewers. No AI is involved in them.

The design choices that carry the weight:

- **A script owns the state.** `factory/bin/job` is the only writer of
  `job.json`, and it refuses illegal stage transitions. It records the hash of
  every file it accepts and the commit each stage saw, so a verdict rewritten
  later, or a commit added after review, stops the job. Every transition is
  appended to the job's `events.jsonl`.
- **The checks come from `main`.** The gate runs `checks.sh` from the main
  checkout and fails a branch that touches the factory's own files or the
  configuration the checks read (`pyproject.toml`, any `conftest.py`). The
  checks fail on skipped tests and on a branch with fewer tests than the base.
- **Risk and the diff decide the review.** A low-risk change gets one code
  reviewer on the smallest model; a high-risk one always gets a security
  reviewer. After the build, the files the branch changed can add reviewers
  the issue's wording didn't call for (`surface_paths`). The tables are in
  `factory/config.json`.
- **Issue text is data.** Agents receive the issue title, text and comments as
  quoted data and are told to ignore instructions inside them. An issue edited
  after it was labeled is refused until the label is put on again.
- **Agents are fenced in, by instruction and by hook.** Each agent's prompt
  says what it may write, and a `PreToolUse` hook (`factory/bin/guard`) blocks
  the rest: other agents' files, `job.json`, pushes to the base, `gh pr merge`.

What this does not give you: a sandbox. Agents and the gate run on your
machine, with your credentials, and a shell can be talked into a lot. The
guard stops mistakes and obvious detours, not a determined attack. Use
`"auto_merge": false` until you trust it, protect the base branch, and run it
in a container when issues come from people you don't know. This is a demo
and a design to learn from, not something to point at a production repository
as it is.

## The `fx` command

`./fx` is one entry point for running and showing the factory. It wraps the
factory's own scripts and adds no factory logic.

| Command | What it does |
| --- | --- |
| `./fx doctor` | Checks the prerequisites and whether the checks pass |
| `./fx up` | Starts the dashboard on a free local port, opens it and the presentation |
| `./fx down` | Stops the dashboard |
| `./fx deck` | Opens the presentation |
| `./fx run [issue]` | Opens Claude Code on `/factory run` |
| `./fx issues` | Lists the issues |
| `./fx queue <issue>` | Labels an issue so the next run picks it up |
| `./fx status` | Shows the jobs and what the next run would do |
| `./fx watch` | Shows job files as they are written |
| `./fx baseline` | Marks the current state as a demo's starting point |
| `./fx reset` | Puts a local demo sandbox back to that starting point |

The factory itself runs inside a Claude Code session that you watch. `./fx run`
opens that session and types the command; it does not run the factory
unattended. `./fx reset` works only in a local sandbox, lists what it will
delete and asks before it does.

## Running it on GitHub

You need the GitHub CLI (`gh`) signed in with push access, and a fork or copy
of this repo. Label an issue `factory`, then:

```bash
./fx run          # every open issue labeled factory
./fx run 1        # one issue
```

Every job opens one pull request, and the approver merges what it approves.
Job files are written to `factory/jobs/<job-id>/` (gitignored). Configuration
is in `factory/config.json`, and the checks the gate runs are in
`factory/checks.sh`.

The factory spawns background agents that run shell commands. With Claude
Code's default permissions you approve each new kind of command once, in the
main session; `.claude/settings.json` denies the few things nobody should do
(merging a PR by hand, pushing to `main`, editing `job.json`).

When a job needs you:

```bash
factory/bin/job show <job>           # the decision, the diff and your options
factory/bin/job feedback <job>       # send it back to work, your words on stdin
factory/bin/job reject <job>         # drop it, your reason on stdin
factory/bin/job park <job>           # stop a running job that is stuck
factory/bin/job merge <job>          # retry a merge that failed
```

The factory's own scripts have tests: `python3 -m pytest factory/tests -q`.
CI runs them, and `factory/checks.sh`, on every pull request.

## Running it without GitHub

In local mode issues are Markdown files in `factory/issues/`, there are no pull
requests, and `origin` is a folder on disk. The factory merges a job's branch
itself after the gate has passed again.

```bash
factory/local-setup.sh ~/factory-demo
```

The setup adds three demo issues, one of them queued, and records the starting
point so `./fx reset` can return to it after a rehearsal. See
[docs/local-mode.md](docs/local-mode.md) for what changes in the pipeline.

## Example run

Issue [#1](https://github.com/fayssal-elmofatiche/factory-sandbox/issues/1)
asked for a way to look up an attendee's waitlist position. The factory accepted
it as low risk, routed it to a single code reviewer, passed the gate on the branch
and again after syncing with `main`, and merged
[PR #6](https://github.com/fayssal-elmofatiche/factory-sandbox/pull/6): one
method and eight tests, for about $1 in model usage.

The other open issues exercise the remaining paths: an interface change that
brings in UX and UI reviewers, an attendee export that should be handed to a
person, and an out-of-scope request that intake should reject.

## The app: evently

An in-memory event-registration library with a stdlib web page:

- `evently/registry.py`: events with a capacity, registrations, a waitlist that
  promotes in order on cancellation, and waitlist positions.
- `evently/web.py`: an HTML events page served on `127.0.0.1`.
- `tests/`: pytest tests for both.

```bash
python3 -m pytest -q               # tests
ruff check .                       # lint
PORT=8001 python3 -m evently.web   # events page on http://127.0.0.1:8001/
```

## What was changed from upstream

- Agents in `.claude/agents/` are tailored to evently: what the app is for,
  its security rules (escape HTML, treat emails as personal data, bind to
  localhost) and its style tokens.
- `factory/checks.sh` runs ruff and pytest.
- Trackers and hosts (November 2026): `factory/bin/job` talks to issues and
  to the code host through two small adapter sets, picked by `tracker`
  (`github`, `files`, `kata`) and `host` (`github`, `local`) in
  `config.json`. No prompt names `gh` any more: the orchestrator and the
  builder use `job answer`, `new-issue`, `issues`, `label`, `unlabel` and
  `open-change`.
- Hardening after a review of this repo (November 2026): commit pinning,
  sealed files and `events.jsonl`; guarded paths, a timeout, and checks that
  fail on skipped or removed tests; one lock for the whole factory; `job park`,
  `show`, `mark-merged`, merge retry, `auto_merge` and `max_cost_usd`; routing
  on the diff; `factory/bin/guard` and `.claude/settings.json`; an unpriced
  model no longer stops a job; an optional `factory/worktree-setup.sh` that
  prepares each job's worktree (env files, test credentials); tests in
  `factory/tests/`.
- `factory/local-setup.sh` creates a local sandbox, and `fx` wraps the common
  commands.
- `factory/dashboard.html`: reviewer chips show the model each reviewer runs
  on; cards show a merge commit, a quiet timer and an incomplete cost.
- `factory/config.json`: prices for `claude-fable-5-2` and a corrected
  cache-read price for `claude-sonnet-5-5`, from Anthropic's price list.
- `docs/presentation.html` and `docs/local-mode.md` are new.

## Licence

The factory is © ZazenCodes and licensed under the
[GNU General Public License v3](LICENSE). This repository, including the
modifications above and the evently app, is distributed under the same licence.
