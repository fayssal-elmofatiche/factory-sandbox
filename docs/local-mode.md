# Local mode: the factory without GitHub

By default the factory uses GitHub for three things: issues, pull requests and
the `origin` remote. Local mode replaces all three, so a run touches nothing
outside your machine.

| GitHub piece  | Local replacement                                    |
| ------------- | ---------------------------------------------------- |
| Issues        | kata, a local issue tracker built for agents         |
| Pull requests | None. The factory merges the job's branch with git   |
| `origin`      | A bare repository on disk, next to the checkout      |

The switch is one line in `factory/config.json`:

```json
"tracker": "kata"
```

Leave it out, or set it to `"github"`, and the factory behaves as upstream does.

## Set it up

You need Claude Code, Python 3.10+, and `kata` on your `PATH`
(`go install go.kenn.io/kata/cmd/kata@latest`).

```bash
factory/local-setup.sh ~/dev/factory-sandbox-local
```

That creates two folders: `~/dev/factory-sandbox-local`, the working checkout,
and `~/dev/factory-sandbox-local.git`, the bare repository that plays `origin`.
It commits the tracker switch there and binds the checkout to a kata project.
Pass a second argument to choose the kata project's name.

## Use it

```bash
cd ~/dev/factory-sandbox-local
kata create "Show seats left on the events page" --body "What you want built."
kata label add <ref> factory        # the label is still the trigger
claude                              # then: /factory run, or /factory run <ref>
```

Kata refs are short ids such as `2f2b`, where GitHub uses numbers. Browse the
issues with `kata tui` or `kata list --status all`.

## What changes in the pipeline

- **Intake** posts its verdict as a comment on the kata issue.
- **The builder** pushes its branch to the local `origin` and opens nothing.
- **APPROVE** makes `factory/bin/job merge` sync the branch with the base, run
  the gate again, merge the branch into the base and push. The push is the
  atomic step: git refuses it if the base moved after the sync, and the job
  goes to `needs-human`. The kata issue is then closed, with the merge commit
  as evidence.
- **ESCALATE** posts the decision on the issue and labels it
  `factory:needs-human`. To approve by hand, merge the branch into the base and
  push; the next `factory/bin/job queue` moves the job to `merged`.
- `merge_method` still applies: `merge` makes a merge commit, `squash` a single
  commit, and `rebase` fast-forwards the base to the synced branch.

Everything else is unchanged: the stages, the gate, risk routing, the review
rounds and the files in `factory/jobs/`. The dashboard works as before; job
cards show no PR link.

## What you give up

A pull request page where a person reads and comments on the change. In local
mode the review record is `factory/jobs/<job-id>/` and the dashboard, and the
diff is `git diff main...factory/<job-id>`.
