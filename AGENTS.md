# evently

A small event-registration app: events with a capacity, registrations, a
waitlist that promotes in order when someone cancels, and an HTML events page.
It exists to demo a software factory, so keep changes small and well tested.

## Layout

- `evently/registry.py` — the domain: `Event`, `Registry`, `RegistrationError`.
- `evently/web.py` — the events page (`render_events`) and a stdlib HTTP server.
- `evently/seed.py` — sample data for the dev server.
- `tests/` — pytest tests. Every behaviour change comes with a test.

## Setup

No dependencies beyond Python 3.10+, pytest and ruff. A fresh checkout needs
no install step and no env files.

## Dev server

```bash
PORT=<port> python3 -m evently.web
```

It serves the events page at `http://127.0.0.1:<port>/`.

## Validation

These are the commands `factory/checks.sh` runs:

```bash
ruff check .
python3 -m pytest -q
```

## Conventions

- Pure functions in `registry.py`; HTML only in `web.py`.
- Escape every user-supplied string that reaches HTML with `html.escape`.
- Emails are stored stripped and lower-cased.
