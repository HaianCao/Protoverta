# Incremental strict typing

Install `.[test,typing]` in an isolated development environment, then run:

```text
python scripts/check_types.py
```

The optional `typing` extra pins mypy 2.4.0. It is not a runtime dependency.
The same command is used by core CI, release, ecosystem CI and the coordinated
local validator. It first checks the configured baseline, then a valid consumer
fixture and an intentionally invalid fixture. The negative fixture must produce
exactly two bad-argument errors and one assignment error; a missing checker,
broken import or syntax error does not count as successful rejection.

## Current enforced scope

- `admission.py`: SQLite admission policy, storage boundary and lease API.
- `failures.py`: error taxonomy and wire envelopes.
- `lifetime.py`: client disconnect watcher and context lifetime.
- `observability.py`: fixed diagnostic records and observer interface.
- `retry.py`: structural admission/budget/opener interfaces and retry results.
- `sse.py`: incremental framing and event types.

The baseline uses strict checking, unreachable-code diagnostics and no blanket
missing-import suppression or `Any` replacement. `mypy_path` points to this
checkout so an installed older wheel cannot satisfy fixture imports instead.
`follow_imports="silent"` retains analysis of imports but reports errors only
for explicitly selected files. This is an incremental boundary, not a claim that
the imported codecs or launchers pass strict checking. This behavior is described
in the [mypy import documentation](https://mypy.readthedocs.io/en/stable/running_mypy.html#following-imports).

There is no package-wide `py.typed` marker yet. Do not advertise complete PEP 561
support based on this baseline. Codec JSON structures, event-state narrowing,
server orchestration and consumer optional-dependency boundaries are follow-up
work. Expand the explicit file list as each tranche is annotated and tested;
do not make a failing tranche green with blanket ignores or unchecked casts.

Runtime validation remains mandatory. Static types cannot validate untrusted
HTTP/JSON payloads and must not replace existing strict conversion checks.
