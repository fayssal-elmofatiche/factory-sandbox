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

Works on macOS, Linux and Windows with WSL. You need:

- git and Python 3.10 or later, with pytest and ruff
- Claude Code 2.1.257 or later, on a plan that includes Fable (intake runs on it)
- For issues that change the web page: the `agent-browser` CLI and `lsof`. The
  builder and the UX and UI reviewers use them to look at the page. Start with
  an issue that has no interface if you do not have them.

```bash
git clone https://github.com/fayssal-elmofatiche/factory-sandbox.git
cd factory-sandbox
factory/local-setup.sh ~/factory-demo
cd ~/factory-demo
claude
```

In Claude Code, type `/factory run`. To watch the jobs, open a second terminal:

```bash
cd ~/factory-demo/factory && python3 -m http.server 8765 --bind 127.0.0.1
```

Then open `http://localhost:8765/dashboard.html`.

The setup script creates two folders: `~/factory-demo`, the working checkout,
and `~/factory-demo.git`, the bare repository that plays `origin`. It adds
three demo issues. The first is labeled `factory`, so the first run has work
to do.

### Windows with WSL

Do everything inside WSL, in your Linux home folder. A checkout under `/mnt/c`
is slow and its file locking is unreliable. On Ubuntu:

```bash
sudo apt update && sudo apt install -y git python3 python3-pytest pipx
pipx install ruff && pipx ensurepath
curl -fsSL https://claude.ai/install.sh | bash
```

Open a new terminal so `ruff` and `claude` are on your `PATH`, sign in with
`claude`, then follow the quick start. The dashboard opens in your Windows
browser at the same `localhost` address.

## Working with issues

An issue is a Markdown file, `factory/issues/<number>.md`:

```markdown
---
title: Show seats left on the events page
state: open
labels: factory
author: ada
created: 2026-11-12T09:00:00Z
---

What you want built, in plain words.
```

The factory appends its comments below the body and sets `state: closed` when
the job merges. You can write these files by hand or use the commands:

```bash
factory/bin/job issues                 # list issues: number, state, labels, title
factory/bin/job label 2 factory        # queue issue 2; the label is the trigger
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
  atomic step: git refuses it if the base moved after the sync, and the job
  goes to `needs-human`. The issue is then closed, with the merge commit noted
  on it.
- **ESCALATE** posts the decision on the issue and labels it
  `factory:needs-human`. To approve by hand, merge the branch into the base and
  push; the next `factory/bin/job queue` moves the job to `merged`. This works
  for a branch that has passed the gate, merged with a merge commit or a
  fast-forward.
- `merge_method` still applies: `merge` makes a merge commit, `squash` a single
  commit, and `rebase` fast-forwards the base to the synced branch.

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
