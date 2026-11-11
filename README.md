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
| review   | routed agents               | code, security, UX and UI reviews, as risk and the diff demand |
| approve  | agent                       | approve the merge, or escalate to a human          |

A few design choices carry the weight:

- **One script owns job state.** `factory/bin/job` writes `job.json` and
  refuses illegal stage transitions. It records the hash of every file it
  accepts and the commit each stage saw, so a verdict rewritten later, or a
  commit added after review, stops the job. Every transition is appended to
  the job's `events.jsonl`.
- **The branch can't grade itself.** The gate runs the main checkout's copy of
  `checks.sh`, and fails a branch that touches the factory's own files or the
  configuration the checks read (`pyproject.toml`, any `conftest.py`). The
  checks fail on skipped tests and on a branch with fewer tests than the base.
- **Risk and the diff decide the review.** A low-risk change gets one cheap
  code reviewer; a high-risk one always gets a security reviewer on a stronger
  model. After the build, the files the branch changed can add reviewers that
  the issue's wording didn't call for (`surface_paths`). The tables live in
  `factory/config.json`.
- **Issue text is data.** Agents receive the issue title, body and comments as
  quoted data and are told to ignore instructions inside them. An issue edited
  after it was labeled is refused until the label is put on again.
- **Agents are fenced in, by instruction and by hook.** Each agent's prompt
  says what it may write, and a `PreToolUse` hook (`factory/bin/guard`) blocks
  the rest: other agents' files, `job.json`, pushes to the base, `gh pr merge`.

What this does not give you: a sandbox. Agents and the gate run on your
machine, with your credentials, and a shell can be talked into a lot. Use
`"auto_merge": false` until you trust it, protect the base branch, and run it
in a container when issues come from people you don't know.

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

The factory spawns background agents that run shell commands. With Claude
Code's default permissions you approve each new kind of command once, in the
main session; `.claude/settings.json` denies the few things nobody should do
(merging a PR by hand, pushing to `main`, editing `job.json`). Agents that
check a page use the [`agent-browser`](https://github.com/vercel-labs/agent-browser)
CLI when it is installed and fall back to reading the HTML when it is not.

When a job needs you:

```bash
factory/bin/job show <job>           # the decision, the diff and your options
factory/bin/job feedback <job>       # send it back to work, your words on stdin
factory/bin/job reject <job>         # drop it, your reason on stdin
factory/bin/job park <job>           # stop a running job that is stuck
factory/bin/job merge <job>          # retry a merge that failed
```

The factory's own scripts have tests: `python3 -m pytest factory/tests -q`
(one to two minutes). CI runs them, and `factory/checks.sh`, on every pull
request.

## Running it without GitHub

The factory also runs fully locally, with nothing to sign in to except Claude:
issues are Markdown files, there are no pull requests, and `origin` is a folder
on disk. The scripts are tested on macOS and Ubuntu; on Windows use WSL.

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
- `factory/dashboard.html`: reviewer chips show the model each reviewer runs
  on; cards show a merge commit, a quiet timer and an incomplete cost.
- `factory/config.json`: prices for `claude-fable-5-2` and a corrected
  cache-read price for `claude-sonnet-5-5`, from Anthropic's price list.

## Licence

The factory is © ZazenCodes and licensed under the
[GNU General Public License v3](LICENSE). This repository, including the
modifications above and the evently app, is distributed under the same licence.
