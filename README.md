# factory-sandbox

A small, working demo of an AI software factory built on [Claude Code](https://claude.com/claude-code).
GitHub issues go in; reviewed, tested pull requests come out. Every step leaves a
written record you can read afterwards.

The factory is [ZazenCodes' Software Factory v2](https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2),
installed and tailored to a toy app called **evently**. This repo exists to show
the factory doing real work on real issues, in public.

**New here?** Open [docs/presentation.html](docs/presentation.html) in a browser.
It draws the factory as a control-room board. The camera moves around the board
and a job travels the line as you press the arrow key. You can trip the gate,
try illegal moves on the state machine and switch reviewers in and out. It is a
single file and works offline.

## What it does

Label an issue `factory`, then run `/factory run` in Claude Code. Each issue
becomes a job that moves through fixed stages:

```
intake → spec → build → gate → review → approve → merge
```

| Stage    | Who                         | What it produces                                   |
| -------- | --------------------------- | -------------------------------------------------- |
| intake   | agent                       | accept, reject or ask for more, posted on the issue |
| route    | `factory/bin/route` script  | which reviewers run, picked from risk in config    |
| spec     | agent                       | a spec with acceptance criteria                    |
| build    | agent                       | commits on a branch in its own git worktree        |
| gate     | `factory/bin/gate` script   | `factory/checks.sh` result and a protected-paths check |
| review   | routed agents               | code, security, UX and UI reviews, as risk demands |
| approve  | agent                       | merge, send back, or escalate to a human           |

A few design choices carry the weight:

- **Agents never write job state.** `factory/bin/job` is the only writer of
  `job.json` and refuses illegal stage transitions.
- **The branch can't grade itself.** The gate runs the checks from `main`, and
  fails the job if the branch touches a protected path such as `checks.sh` or
  `config.json`.
- **Risk decides the review.** A low-risk change gets one cheap code reviewer;
  a change that touches attendee data gets a security reviewer on a stronger
  model. The routing table lives in `factory/config.json`.
- **Issue text is data.** Agents receive issue bodies as quoted data and are told
  to ignore instructions inside them.

## Example run

Issue [#1](https://github.com/fayssal-elmofatiche/factory-sandbox/issues/1)
asked for a way to look up an attendee's waitlist position. The factory accepted
it as low risk, routed it to a single code reviewer, passed the gate on the branch
and again after syncing with `main`, and merged
[PR #6](https://github.com/fayssal-elmofatiche/factory-sandbox/pull/6): one
method and eight tests, for about $1 in model usage.

The other open issues are chosen to exercise the remaining paths: a UI change
that brings in UX and UI reviewers, an attendee export that should escalate to
a human, and an out-of-scope request that intake should reject.

## The app: evently

An in-memory event-registration library with a stdlib web page:

- `evently/registry.py`: events with a capacity, registrations, a waitlist that
  promotes in order on cancellation, and waitlist positions.
- `evently/web.py`: an HTML events page served on `127.0.0.1`.
- `tests/`: pytest tests for both.

It needs Python 3.10+, pytest and ruff, and nothing else.

```bash
python3 -m pytest -q          # tests
ruff check .                  # lint
PORT=8001 python3 -m evently.web   # events page on http://127.0.0.1:8001/
```

## Running the factory

You need Claude Code, the GitHub CLI (`gh`) signed in with push access, and a
fork or copy of this repo.

```bash
claude                        # in the repo root
/factory run                  # every open issue labelled `factory`
/factory run 1                # one issue
```

Watch progress on the dashboard:

```bash
cd factory && python3 -m http.server 8765 --bind 127.0.0.1
# open http://localhost:8765/dashboard.html
```

Job state, specs, reviews and decisions are written to `factory/jobs/<job-id>/`
(gitignored). Configuration is in `factory/config.json`, and the checks the gate
runs are in `factory/checks.sh`.

## Running it without GitHub

The factory also runs fully locally, with nothing to sign in to except Claude:
issues are Markdown files, there are no pull requests, and `origin` is a folder
on disk. It works on macOS, Linux and Windows with WSL.

```bash
factory/local-setup.sh ~/factory-demo
cd ~/factory-demo && claude        # then type: /factory run
```

The setup adds three demo issues, one of them queued. See
[docs/local-mode.md](docs/local-mode.md) for the prerequisites, the WSL steps
and what changes in the pipeline.

## What was changed from upstream

- Agents in `.claude/agents/` are tailored to evently: what the app is for,
  its security rules (escape HTML, treat emails as personal data, bind to
  localhost) and its style tokens.
- `factory/checks.sh` runs ruff and pytest.
- Local mode: `factory/bin/job` gains a `tracker` switch (`github`, `files`,
  `kata`) and the commands `answer`, `new-issue`, `issues`, `label` and `unlabel`; the skill, the builder and the approver each gain a
  short note. With the default tracker the factory behaves as upstream does.
- `factory/dashboard.html`: reviewer chips show the model each reviewer runs on.
- `factory/config.json`: a price entry for `claude-fable-5-2`.

## Licence

The factory is © ZazenCodes and licensed under the
[GNU General Public License v3](LICENSE). This repository, including the
modifications above and the evently app, is distributed under the same licence.
