# factory-sandbox

A small, working demo of an AI software factory built on [Claude Code](https://claude.com/claude-code).
A written request goes in, and a tested, reviewed code change comes out. Every
step leaves a file you can read afterwards. It all runs on your own machine:
no GitHub account, no pull requests, no server.

The factory is [ZazenCodes' Software Factory v2](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2),
installed and tailored to a toy energy-trading app called **dayahead**, a
day-ahead power auction. This repo exists to show
the factory doing real work, and to be a place to try it.

**New here?** Open [docs/presentation.html](docs/presentation.html) in a browser,
or run `./fx deck`. It draws the factory as a control-room board. The camera
moves around the board and a job travels the line as you press the arrow key.
You can trip the gate, try illegal moves on the state machine and switch
reviewers in and out. Press F or the Full screen button to present it. It is a
single file and works offline.

## Quick start

```bash
git clone https://github.com/fayssal-elmofatiche/factory-sandbox.git
cd factory-sandbox
./fx doctor   # check this machine
./fx deck     # the presentation
./fx up       # the dashboard
./fx run      # sets the checkout up the first time, then Claude Code on /factory run
```

On Windows PowerShell, use `.\fx.cmd` instead of `./fx`. Windows needs Git for
Windows (for Git Bash) and a Python that answers to `python3`; the steps are in
[docs/local-mode.md](docs/local-mode.md).

Everything runs on your machine. Issues are Markdown files in
`factory/issues/`, and `origin` is a repository on disk next to the checkout,
where the factory merges. The first `./fx run` sets this up (`./fx setup` does
the same by hand); the place you cloned from becomes the remote `upstream`,
and `git pull upstream main` brings in course updates.

You need git, Python 3.10+ with pytest and ruff, and Claude Code 2.1.257 or
later on a plan that includes Fable. `./fx doctor` checks all of that and says
what is missing. The scripts are tested on macOS, Ubuntu and Windows, natively or
in WSL. See [docs/local-mode.md](docs/local-mode.md) for the setup and the
Windows steps.

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
| merge   | `factory/bin/job` script   | sync with `main`, gate again, merge, close the issue    |

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
- **Risk decides who merges.** `human_merge` in `factory/config.json` lists
  the risks, and the surfaces, that a person must merge. `job merge` checks it
  itself, so an approver's APPROVE on a high-risk job still stops at
  `needs-human` until someone runs `./fx approve`. A surface counts when intake
  named it or when the branch changes a file `surface_paths` maps to it.
- **A measured regression decides who merges, too.** When the checks pass, the
  gate runs `factory/budget.py` on the branch and on the base and compares
  them against `budgets` in `factory/config.json` (to start with, at most 10%
  more Python instructions to clear a fixed day of orders). A branch over
  budget still passes the gate, but `job merge` stops it at `needs-human` with
  the numbers in `round-N/checks.md` and `./fx show`. The budget counts
  instructions, not milliseconds: the count is the same on a busy machine, the
  time isn't.
- **Issue text is data.** Agents receive the issue title, text and comments as
  quoted data and are told to ignore instructions inside them.
- **Agents are fenced in, by instruction and by hook.** Each agent's prompt
  says what it may write, and a `PreToolUse` hook (`factory/bin/guard`) blocks
  the rest: other agents' files, `job.json`, `factory/bin/job`, pushes to the
  base.
  Claude Code runs that hook only in a folder you have trusted (the prompt it
  shows the first time you open `claude` there); `./fx doctor` checks it.

What this does not give you: a sandbox. Agents and the gate run on your
machine, with your credentials, and a shell can be talked into a lot. The
guard stops mistakes and obvious detours, not a determined attack. The gate
runs the branch's own tests, and test code can print whatever it likes: the
gate catches a branch that fails honestly, and the reviewers read the diff
for one that cheats. Use
`"auto_merge": false` until you trust it, and run it in a container when
issues come from people you don't know: [docs/container.md](docs/container.md)
has a recipe. This is a demo
and a design to learn from, not something to point at a production repository
as it is.

## The `fx` command

`./fx` is one entry point for running and showing the factory. It wraps the
factory's own scripts and adds no factory logic. It is one Python file with no
dependencies. `./fx` lists the commands, and `./fx <command> --help` explains
one. Its tests run with `python3 -m pytest factory/tests -q`.

| Command | What it does |
| --- | --- |
| `./fx setup` | Sets this checkout up: `origin` on disk, the demo issues, the starting point. `./fx run` does it the first time |
| `./fx doctor` | Checks the prerequisites and whether the checks pass |
| `./fx up` | Starts the dashboard on a free local port and opens it |
| `./fx down` | Stops the dashboard |
| `./fx deck` | Opens the presentation; show it before `./fx up` and `./fx run` |
| `./fx run [issue]` | Opens Claude Code on `/factory run` |
| `./fx sweep <kind>` | Opens Claude Code on `/factory sweep`: proposes `perf`, `security`, `cleanup` or `docs` work as unlabelled issues |
| `./fx drive [issue]` | Runs the same steps with a script instead of a Claude Code session (see below) |
| `./fx issues` | Lists the issues |
| `./fx queue <issue>` | Labels an issue so the next run picks it up |
| `./fx unqueue <issue>` | Takes that label off again |
| `./fx status` | Shows the jobs and what the next run would do |
| `./fx watch` | Shows job files as they are written |
| `./fx retro` | Counts why rounds went back across jobs, and says where a fix would go |
| `./fx bench` | Sets up replays of past jobs on other models, and compares them with the original |
| `./fx config` | Shows the settings worth changing; `./fx config concurrency 1` changes one |
| `./fx show <job>` | For a job that waits for you: the decision, the change and your options |
| `./fx approve <job>` | Approves it: the factory runs the checks again and merges |
| `./fx feedback <job>` | Sends it back to work with your instructions |
| `./fx reject <job>` | Drops it, with your reason |
| `./fx retry <job>` | Tries a failed merge again |
| `./fx park <job>` | Stops a running job that is stuck |
| `./fx baseline` | Marks the current state as a demo's starting point |
| `./fx reset` | Discards the work and keeps your setup changes |
| `./fx reset --setup` | Also discards the setup changes: back to how the sandbox was made |

The factory itself runs inside a Claude Code session that you watch. `./fx run`
opens that session and types the command; it does not run the factory
unattended.

`./fx drive` (`factory/bin/drive`) takes the same steps with no orchestrating
session. A script reads `factory/bin/job queue`, starts each agent as its own
headless `claude -p --agent <name>` process, reads the first line of the file
the agent writes and makes the next `factory/bin/job` call, until nothing more
can run. Killed, it resumes from the job files on the next run. Claude Code skips
the hooks in project agents under `claude -p` unless the folder is trusted, so
the driver passes the guard on each agent's command line as well. The agents
then run unattended on this machine's credentials: do that in a container,
with an API key that has a spending limit and `max_cost_usd` set.
`/factory <anything>` stays a conversation with a Claude Code session.

### Trying a different setup

The sandbox is made for experiments. Change the setup, run, reset, and run
again:

```bash
./fx config review.low sonnet   # or edit factory/config.json, .claude/ or AGENTS.md
./fx run
./fx reset --keep               # start again; this run's files go to factory/runs/
```

`./fx reset` treats two things differently. The work (jobs, branches, merged
changes, the state of the issues) is discarded. The setup (`factory/config.json`,
`factory/checks.sh`, `.claude/` and `AGENTS.md`) is kept and becomes part of
the new starting point. It lists what it will delete and asks before it does.

Each job has a cost cap, `max_cost_usd` in `factory/config.json`, $5 to start
with (the first real job cost $1.11). `factory/bin/job` checks it whenever a
job moves to its next step and refuses to move a job that has gone over; the
orchestrator then parks it for you to decide. A step that has started always
finishes, so a job can end up one step over the cap. To show the cap in a demo, lower it with
`./fx config max_cost_usd 0.5` and run a job. `./fx config max_cost_usd off`
removes it.

## Running it

Label an issue `factory` (`./fx queue <issue>`), then:

```bash
./fx run          # every open issue labeled factory
./fx run 1        # one issue
```

Every job works on one branch, and the factory merges what the approver
approves into `main` on `origin` and closes the issue. Your own checkout stays
where it was; `git pull` shows the merged work. Job files are written to
`factory/jobs/<job-id>/` (gitignored). Configuration is in
`factory/config.json`, and the checks the gate runs are in
`factory/checks.sh`.

The factory spawns background agents that run shell commands. With Claude
Code's default permissions you approve each new kind of command once, in the
main session; `.claude/settings.json` denies the few things nobody should do
(pushing to `main`, force pushes, editing `job.json`).

When a job lands in the dashboard's "Needs human" column, open its card: it
shows why, and the commands for your options with a copy button each.

```bash
./fx show <job>         # the decision, the change and your options
./fx approve <job>      # approve it: the factory runs the checks again and merges
./fx feedback <job>     # send it back to work; you write what should change
./fx reject <job>       # drop it; you write the reason
./fx retry <job>        # try a failed merge again
```

Approving never skips the checks. `fx approve` only takes a job whose tip is a
commit the gate has passed, syncs it with `main`, runs the gate again and
merges.

The factory's own scripts have tests: `python3 -m pytest factory/tests -q`.
Run them, and `bash factory/checks.sh`, before merging a change to the factory itself.

`./fx setup` adds six demo issues, one of them queued, and records the
starting point so `./fx reset` can return to it after a rehearsal. For a
second, separate copy, `factory/local-setup.sh <dir>` clones this checkout and
sets the copy up. See [docs/local-mode.md](docs/local-mode.md).

## Example run

The first run was on the toy app this repo had before **dayahead**, an
event-registration app. Issue 1 asked for a way to look up an attendee's
waitlist position. The factory accepted it as low risk, routed it to a single
code reviewer, passed the gate on the branch and again after syncing with
`main`, and merged it: one method and eight tests, for about $1 in model
usage. `OrderBook.merit_order_rank`
in **dayahead** is the same kind of change, carried over by hand; a factory run
on **dayahead** is still to do.

The demo issues in `factory/demo-issues/` take the factory down every path.
Queue one with `./fx queue <n>`; only issue 1 is queued at the start.

These outcomes are what the issues and the agents' instructions are written
to produce. A model decides at each step, so a run can differ.

| # | Request | What should happen |
|---|---------|--------------------|
| 1 | Chart the day's hourly prices, with base and peak lines | Merges after UX, UI and security review |
| 2 | A merit-order page per hour: supply and demand step chart, marginal order | Waits until issue 1 merges (`blocked_by: 1`: both change the prices page), then merges after a larger build, possibly in more than one round |
| 3 | A form to place and withdraw bids and offers | Built and reviewed, then the approver escalates: a new endpoint that changes orders, with no sign-in |
| 4 | A CSV of accepted orders, with pro-rata fills at the clearing price | Built and reviewed, then the approver escalates: an export of per-participant data |
| 5 | Block orders across several hours | Intake asks first: how blocks clear is a market-design decision |
| 6 | A Telegram bot for crypto prices | Intake rejects it as out of scope |

## The app: dayahead

An in-memory day-ahead power auction with a stdlib web page:

- `dayahead/orderbook.py`: bids and offers per delivery hour in one bidding
  zone, each with a volume in MWh and a limit price in EUR/MWh. Each hour
  clears by merit order: offers stack cheapest first, bids dearest first, and
  the clearing price is the lowest price at which the most volume trades.
  `merit_order_rank` gives an offer's place in its hour's merit order.
- `dayahead/web.py`: an HTML page of the hourly clearing prices and cleared
  volumes, served on `127.0.0.1`. It shows only hourly totals, never who bid
  what.
- `dayahead/seed.py`: one day of sample orders (wind, solar, lignite, gas and
  two buyers) for the dev server.
- `tests/`: pytest tests for all three.

```bash
python3 -m pytest -q               # tests
ruff check .                       # lint
PORT=8001 python3 -m dayahead.web  # prices page on http://127.0.0.1:8001/
```

## What was changed from upstream

- Agents in `.claude/agents/` are tailored to dayahead: what the app is for,
  its security rules (escape HTML, keep who-bid-what off public pages, bind
  to localhost) and its style tokens.
- The toy app (November 2026): **dayahead**, a day-ahead power auction,
  replaced the earlier event-registration app.
- `factory/checks.sh` runs ruff and pytest.
- Local only (November 2026): issues live in `factory/issues/` (or in kata,
  picked by `tracker` in `config.json`), and `origin` is a repository on disk
  where the factory merges approved branches itself. The orchestrator uses
  `job answer`, `new-issue`, `issues`, `label` and `unlabel`; the builder only
  pushes its branch.
- Hardening after a review of this repo (November 2026): commit pinning,
  sealed files and `events.jsonl`; guarded paths, a timeout, and checks that
  fail on skipped or removed tests; one lock for the whole factory; `job park`,
  `show`, `mark-merged`, merge retry, `auto_merge` and `max_cost_usd`; routing
  on the diff; `factory/bin/guard` and `.claude/settings.json`; an unpriced
  model no longer stops a job; an optional `factory/worktree-setup.sh` that
  prepares each job's worktree (env files, test credentials); tests in
  `factory/tests/`.
- Sweeps: `./fx sweep <kind>` reads the base branch and files at most
  `sweep.max_issues` findings as issues without the `factory` label, skipping
  any that match an open issue (`job propose`). Nothing a sweep files runs
  until a person queues it. Scheduling sweeps is not built yet.
- An issue file can name the issues it waits for (`blocked_by: 3, 4`);
  `job queue` holds it, without a slot, until they are closed.
- `fx` wraps the common commands, and `./fx setup` makes a clone its own
  sandbox; `factory/local-setup.sh` makes an extra copy.
- Windows (November 2026): the factory runs natively on Windows as well as on
  Linux, macOS and in WSL. `factory/bin/portable.py` holds what differs
  (starting scripts with Python, finding Git Bash rather than the WSL shortcut,
  file locks, stopping process groups); `fx.cmd` runs `fx` from PowerShell and
  cmd; the guard reads Windows paths. `.github/workflows/factory-tests.yml`
  runs the checks and the factory's tests on Ubuntu and Windows.
- `factory/dashboard.html`: reviewer chips show the model each reviewer runs
  on; cards show a merge commit, a quiet timer and an incomplete cost.
  A summary strip shows the factory as a whole: requests by outcome, estimated
  cost, time to merge, first-try merges, gate failures, which reviewers asked
  for changes, why rounds went back, time by station, and the run
  `./fx reset --keep` saved last.
- Failure labels (`factory/bin/labels.py`, `factory/bin/retro`, `./fx retro`):
  every failed gate is labelled by its cause (lint, a failing test, removed or
  skipped tests, a timeout, a protected path), and reviewers tag each finding
  (`[spec-gap]`, `[redundant-test]` and so on). `job sync` counts them into
  `job.json`; `./fx retro` counts them across jobs and, for a label seen often
  enough, names the prompt or file a fix would most likely change. A person
  makes that change: the gate fails any job that edits `.claude/` or
  `factory/config.json`, so the factory can't rewrite its own setup.
- Model replay (`factory/bin/bench`, `./fx bench`): public benchmarks say little
  about how a model does on this repository, so `./fx bench plan <job>...
  --try builder=sonnet --try builder=haiku` sets up replays of jobs the factory
  already did, each from the job's starting commit and with its sealed spec.
  `./fx bench report <bench>` compares each choice with the jobs as they first
  ran: gate passes, approvals, rounds, cost and why rounds went back. A replay
  spends real tokens, so `plan` prints a rough estimate and `--max-usd` refuses
  a bench over it. `./fx bench run` is not built yet: starting the agents with
  no one at the keyboard needs the deterministic driver loop. Until the
  acceptance tests land, "approved" means only that the gate, the reviewers and
  the approver passed it.
- `factory/config.json`: prices for `claude-fable-5-2` and a corrected
  cache-read price for `claude-sonnet-5-5`, from Anthropic's price list.
- `docs/presentation.html`, `docs/local-mode.md` and `docs/container.md` are new.

## Licence

The factory is © ZazenCodes and licensed under the
[GNU General Public License v3](LICENSE). This repository, including the
modifications above and the dayahead app, is distributed under the same licence.
