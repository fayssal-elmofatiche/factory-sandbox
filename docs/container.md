# Running the factory in a container

The factory gives you no sandbox. Agents and the gate run on your machine with
your credentials, and `factory/bin/guard` stops mistakes and obvious detours,
not a determined attack. A container puts a wall around the agents: inside it
they see only one work folder and the credentials you pass in. Your home
folder, SSH keys, cloud logins and other repositories stay out of reach.

A container leaves the network open. An agent can still send what it can read
inside the container to the internet, and it can use the key you give it. Give
it a key you can revoke, with a spending limit, and keep the work folder free of
anything private.

This recipe was tested with Docker Desktop on macOS: the image builds, `./fx
setup` and `./fx doctor` work, and `factory/checks.sh` and the factory's own
tests pass inside the container. Signing in and a full `./fx run` need your own
login and were not part of that test.

## Layout

One folder on your machine holds everything the factory touches, and the
container sees only that folder:

```
~/factory-work/
  Dockerfile
  factory-sandbox/        the checkout; agents work here
  factory-sandbox.git/    the repository on disk that plays origin
  claude-state/           Claude Code's sign-in and trust record
```

`./fx setup` puts `origin` next to the checkout as `factory-sandbox.git`, so
one mounted folder covers both.

## The image

Save this as `~/factory-work/Dockerfile`:

```dockerfile
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 python3-pytest pipx curl ca-certificates lsof \
    && rm -rf /var/lib/apt/lists/*
# A user that matches yours, so files in the mounted folder stay yours.
ARG UID=1000
# ubuntu:24.04 ships a user "ubuntu" with uid 1000; remove it so ours can take the id.
RUN userdel -r ubuntu 2>/dev/null; useradd -m -u ${UID} -s /bin/bash agent
USER agent
ENV HOME=/home/agent PATH=/home/agent/.local/bin:$PATH
RUN pipx install ruff \
 && curl -fsSL https://claude.ai/install.sh | bash
# Claude Code keeps its sign-in and trust record here, a folder you mount,
# so the binaries in ~/.local stay visible.
ENV CLAUDE_CONFIG_DIR=/claude-state
WORKDIR /work
```

Build it with your own user id:

```bash
cd ~/factory-work
docker build --build-arg UID=$(id -u) -t factory-box .
```

## First run

```bash
cd ~/factory-work
git clone https://github.com/fayssal-elmofatiche/factory-sandbox.git
mkdir -p claude-state
docker run --rm -it \
  -v "$PWD":/work \
  -v "$PWD/claude-state":/claude-state \
  -w /work/factory-sandbox \
  factory-box bash
```

Inside the container:

```bash
claude        # sign in, accept the trust prompt for this folder, then /exit
./fx doctor   # every line ok, apart from agent-browser
./fx run      # sets up origin the first time, then Claude Code on /factory run
```

`claude-state` keeps the sign-in between runs. It also holds Claude Code's
`.claude.json`, where `./fx doctor` reads whether the folder is trusted. Claude
Code skips the agents' guard hooks in a folder that was never trusted, so don't
skip the trust prompt.

Mount `claude-state`, never a whole home folder: a folder mounted over
`/home/agent` hides `claude` and `ruff`, which the image installs in
`~/.local`.

Instead of signing in, you can pass `-e ANTHROPIC_API_KEY=...` to `docker run`.
Use a key made for this, with a spending limit.

Later runs are the same `docker run` line, then `./fx run`. To keep the
container running after you close the terminal, start it with `docker run -d`
and attach with `docker exec -it <container> bash`.

## Getting the work out

The factory merges approved jobs into `main` in `factory-sandbox.git`. From any
clone on your machine:

```bash
git pull ~/factory-work/factory-sandbox.git main
```

## The dashboard

Inside the container, `./fx up` binds to `127.0.0.1`, so a published port
can't reach it. The job files are in the mounted folder, so start the
dashboard on your machine instead, in the same checkout:

```bash
cd ~/factory-work/factory-sandbox && ./fx up
```

`./fx up` and `./fx status` only read files and work from there. Run
everything that changes state (`./fx run`, `approve`, `feedback`, `reset`)
inside the container: `origin` is `/work/factory-sandbox.git`, a path that
exists only in the container.

## Tightening it

In order of value:

1. Pass in a revocable API key with a spending limit, and nothing else.
2. Set `"auto_merge": false` in `factory/config.json` until you trust the
   runs. A person then merges every approved job.
3. Mount nothing else. Never mount your home folder, `~/.ssh`, `~/.aws` or
   `~/.config`.
4. Add `--cap-drop ALL --security-opt no-new-privileges` to `docker run`. The
   checks, git and `lsof` work with them.
5. Limiting the network means an allowlist (Anthropic's API and the package
   index you use) through a proxy or firewall, which this recipe doesn't cover.

## Limits

- The image has no `agent-browser`. On issues that change the web page, the
  builder and the UX and UI reviewers read the page's HTML instead and say so.
  Demo issue 1 is one of those; for a first run, queue one that isn't.
- On Linux, files the container writes in `~/factory-work` belong to the user
  whose id you passed to `docker build`. On macOS, Docker Desktop maps them to
  you either way.
