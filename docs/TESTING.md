# Testing Guide

> Status: documentation only; no test was executed for this change. Commands below
> come from `pytest.ini` and the repository layout.

## Test classes

| Class | Purpose | Where | Safe without hardware? |
|---|---|---|---|
| Unit / logic tests | Protocol framing, state stores, task config, UI logic | `tests/` | Generally yes — uses fakes |
| Integration with serial | `half_duplex_master`, transport framing | `tests/` with fake transports | Yes, if no real port is opened |
| Hardware probe | Real HC-14 / LED / buzzer | run on the Pi only | **No** |
| Live mission | `screen_start_bridge.py`, `land_air_app.py` | `components/` | **No** |

## Documented commands

From the repository root:

```bash
python3 -m pytest tests
```

`pytest.ini` sets `testpaths = tests` and excludes `former_code`,
`pytest-cache-files-*`, `gstest-*` and `tmp*`.

Static checks:

```bash
python3 -m compileall -q components main.py
git diff --check
```

## What was not run here

- `pytest`: this documentation host has no PyQt5 / RPi.GPIO / pyserial stack, and
  some modules import UI or GPIO code. Marked **NOT RUN**, not "passing".
- LED daemon, buzzer and radio tests: **NOT RUN** (hardware).

## Hardware-free rules

- Tests must not open a real serial device, GPIO pin or LED socket.
- New protocol tests should use byte-level fixtures (see `tests/data/`).
- Keep Pi-only tests out of the default discovery or guard them behind an explicit
  environment flag.
