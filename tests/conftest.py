"""
Shared fixtures.

The suite is pure and fast by design (`docs/design.md` section 8): no Windows
API, no audio device, no model, no `QApplication`. Where a module reaches for one
of those it is given a seam instead -- `hotkey._key_state`, `Engine(chord_held=)`,
`paths.asset_path` -- and every one of those seams already existed in the code for
this reason.
"""

import json

import pytest

from ptt import paths


@pytest.fixture(autouse=True)
def log_lines(tmp_path, monkeypatch):
    """
    Redirect debug_log.txt into the test's own directory, and read it back.

    Autouse and not optional. `logging_setup.log_debug` appends to
    `paths.debug_log_path()` unconditionally and swallows every error, so an
    unguarded run of this suite would quietly append hundreds of lines to the
    log the user diagnoses real failures with -- and `logging_setup.init`'s
    docstring says that log is diffed against a captured baseline.

    Returning the lines turns that from housekeeping into coverage. `OBS-3`
    requires every fallback to log the reason that caused it, which nothing
    asserted before: `config.load` is happy to return a default silently as far
    as its own return value is concerned, and the log line is the only evidence
    the user gets. Tests that exercise a fallback check for it here.
    """
    log = tmp_path / "debug_log.txt"
    monkeypatch.setattr(paths, "debug_log_path", lambda: str(log))

    def read():
        if not log.exists():
            return []
        return log.read_text(encoding="utf-8").splitlines()

    return read


@pytest.fixture
def config_file(tmp_path):
    """
    Write a config.json and return its path.

    Acceptance criterion 8 names `future_setting` in `app/config.json` as the
    unknown-key round-trip case. That file is gitignored and is a per-machine
    runtime artifact, so a fresh clone does not have it (stage 0 review section
    3.14). The case belongs in a fixture, not in a file that may not exist.
    """
    def write(raw, name="config.json"):
        path = tmp_path / name
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        return str(path)

    return write
