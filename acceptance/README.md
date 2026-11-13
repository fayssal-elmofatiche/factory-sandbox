# Acceptance tests

Black-box tests of what merged jobs promised. The factory writes this folder:
for each job, the spec writer turns the spec's acceptance criteria into
`acceptance.py` before any code exists, the gate runs it on every build, and
`factory/bin/job merge` adds it here as `test_<job_id>.py`. From then on
`factory/checks.sh` runs it with the rest of the suite, so every later job must
keep it passing.

A job may not change this folder: it is in `guarded_paths`, and the gate fails
a branch that touches it. A person edits it on purpose only, for example when a
product decision changes what an earlier job promised.
