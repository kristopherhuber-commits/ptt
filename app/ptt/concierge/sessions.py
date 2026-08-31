"""
Saved Concierge transcripts (FR-CG-13, design 5.1).

Sessions are **fresh every time** -- the pack and the memory note are the
context, never a prior transcript -- so nothing in this module is ever read back
into a prompt. It exists for the other half of FR-CG-13: "sessions can be named
and saved; saved transcripts exist for the user to reread". Loading one as extra
context is deferred to v3.1, and only if the note proves insufficient (design
5.1), which is why `load()` returns rows for a viewer and there is no path from
here into `agent.Context`.

**Why this lives in the harness rather than in `ptt.ui`.** It is durable state
beside `config.json`, which is the same class of object as the memory note, and
`MemoryNote` is in `tools.py` for the same reason: file IO with no Qt in it is
testable with PySide6 blocked, and CON-CG-6's import test is what keeps that
true rather than merely intended. Nothing here knows what a row *means* -- a row
is `{kind, text, detail}` and the kinds are the panel's vocabulary -- so the
store does not become a second place where the chat's shape is declared.

One file, not a directory. `build_portable.py`'s runtime-artifact rule matches
file names at the top level of `app/`, so a single JSON file is excluded by
adding one name to a frozenset; a directory would have needed a second
`EXCLUDED_DIRS` entry and the same argument all over again (Q27).
"""

import json
import os
import time
from typing import NamedTuple

from ptt.logging_setup import log_debug

#: A hard ceiling on one saved transcript, in characters of serialised JSON.
#: `concierge.history_limit` bounds how *many* are kept and nothing bounded how
#: *large* one could be: a session that read a log into the chat would otherwise
#: be saved in full, twenty times over, beside `config.json`. Rows are dropped
#: from the oldest end until it fits, and the drop is recorded in the saved
#: transcript itself rather than being silent (`OBS-1`).
TRANSCRIPT_MAX_CHARS = 200000


class Saved(NamedTuple):
    """
    One saved transcript.

    `rows` is empty in a listing and populated by `load()`: the panel's saved-
    sessions menu wants twenty names, not twenty transcripts, and reading every
    row to build a menu is the sort of thing that is fine until someone raises
    `history_limit` to 200.
    """
    id: str
    name: str
    saved_at: str
    row_count: int
    rows: tuple = ()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class SessionStore:
    """
    The last N saved transcripts, newest first, in one JSON file.

    `limit_provider` is read on every save rather than captured, the same
    live-re-read discipline `Server.start_idle_timer` uses for the residency
    slider and `Engine` uses for the hotkey: lowering `history_limit` has to
    take effect without a restart, and a captured integer would mean it did not.

    Never raises. A corrupt or unreadable file reads as "no saved sessions" with
    a logged reason, because the alternative is a chat panel that cannot open
    because a transcript from last week has a stray byte in it.
    """

    def __init__(self, path, limit_provider=None, clock=_now):
        self.path = path
        self._limit = limit_provider or (lambda: 20)
        self._clock = clock

    # -- reading ------------------------------------------------------------

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return []
        except Exception as e:
            log_debug(f"Concierge: could not read saved sessions ({str(e)}); "
                      f"treating the file as empty.")
            return []
        if not isinstance(raw, list):
            log_debug(f"Concierge: saved sessions is not a list "
                      f"({type(raw).__name__}); treating the file as empty.")
            return []
        return [entry for entry in raw if isinstance(entry, dict)]

    def list(self):
        """Every saved transcript, newest first, without its rows."""
        out = []
        for entry in self._read():
            rows = entry.get("rows")
            out.append(Saved(
                id=str(entry.get("id", "")),
                name=str(entry.get("name", "")) or "Untitled session",
                saved_at=str(entry.get("saved_at", "")),
                row_count=len(rows) if isinstance(rows, list) else 0,
            ))
        return tuple(out)

    def load(self, session_id):
        """One saved transcript, rows included, or None."""
        for entry in self._read():
            if str(entry.get("id", "")) != str(session_id):
                continue
            raw = entry.get("rows")
            rows = (tuple(r for r in raw if isinstance(r, dict))
                    if isinstance(raw, list) else ())
            return Saved(
                id=str(entry.get("id", "")),
                name=str(entry.get("name", "")) or "Untitled session",
                saved_at=str(entry.get("saved_at", "")),
                row_count=len(rows),
                rows=rows,
            )
        return None

    # -- writing ------------------------------------------------------------

    def _fresh_id(self, taken):
        """
        A session id no existing entry is using.

        The id is the save time in milliseconds, and that is unique for exactly
        as long as nobody saves twice inside one millisecond -- true of a person
        with a mouse, false of anything driving the store in a loop, including
        this module's own tests, where it failed. A collision does not produce
        two rows: `save` replaces the entry whose id matches, so the second save
        silently ate the first. Counting forward from the clock keeps the ids
        ordered and in the same format, and needs no randomness to do it.
        """
        stamp = int(time.time() * 1000)
        while f"s{stamp:x}" in taken:
            stamp += 1
        return f"s{stamp:x}"

    def save(self, name, rows, session_id=None):
        """
        Save (or re-save) one transcript. Returns `(saved, reason)`.

        Re-saving under the same `session_id` replaces the entry in place and
        moves it to the front, so pressing Save twice in one conversation leaves
        one transcript rather than two halves of the same one.
        """
        rows = [dict(r) for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            return None, "there is nothing in this session to save yet"

        name = (str(name or "").strip() or f"Session {self._clock()}")[:120]

        # Read once: the existing entries are both what a new id must avoid and
        # what the entry below is inserted in front of.
        existing = self._read()
        session_id = (str(session_id or "").strip()
                      or self._fresh_id({str(e.get("id", "")) for e in existing}))
        rows, dropped = fit(rows)
        if dropped:
            log_debug(f"Concierge: dropped {dropped} row(s) from the saved "
                      f"transcript {name!r}; it was over "
                      f"{TRANSCRIPT_MAX_CHARS} characters.")

        entry = {"id": session_id, "name": name, "saved_at": self._clock(),
                 "rows": rows}
        entries = [e for e in existing if str(e.get("id", "")) != session_id]
        entries.insert(0, entry)

        try:
            limit = max(1, int(self._limit()))
        except Exception:
            limit = 20
        entries = entries[:limit]

        ok, reason = self._write(entries)
        if not ok:
            return None, reason
        log_debug(f"Concierge: saved the session {name!r} ({len(rows)} rows, "
                  f"keeping {len(entries)} of {limit}).")
        return Saved(session_id, name, entry["saved_at"], len(rows)), None

    def rename(self, session_id, name):
        """Rename one saved transcript. `(ok, reason)`."""
        name = str(name or "").strip()[:120]
        if not name:
            return False, "a session name cannot be empty"
        entries = self._read()
        for entry in entries:
            if str(entry.get("id", "")) == str(session_id):
                entry["name"] = name
                return self._write(entries)
        return False, f"there is no saved session {session_id!r}"

    def delete(self, session_id):
        """Forget one saved transcript. `(ok, reason)`."""
        entries = self._read()
        kept = [e for e in entries if str(e.get("id", "")) != str(session_id)]
        if len(kept) == len(entries):
            return False, f"there is no saved session {session_id!r}"
        return self._write(kept)

    def _write(self, entries):
        """
        Replace the file atomically, for `Settings.save`'s reason.

        A truncating open that dies mid-write leaves a file that is neither
        version, and this one holds every transcript the user chose to keep.
        """
        tmp = self.path + ".tmp"
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            log_debug(f"Concierge: could not save sessions: {str(e)}")
            return False, f"could not write the saved sessions: {str(e)}"
        return True, None


def fit(rows, limit=TRANSCRIPT_MAX_CHARS):
    """
    Trim a transcript from the oldest end until it serialises under `limit`.

    Oldest-first, and a note saying so is prepended to what survives: the end of
    a conversation is the part with the answer in it, and a transcript that
    quietly lost its middle is worse than one that says it was shortened.
    """
    rows = list(rows)
    dropped = 0
    while rows and len(json.dumps(rows, ensure_ascii=False)) > limit:
        rows = rows[1:]
        dropped += 1
    if dropped:
        rows.insert(0, {
            "kind": "notice",
            "text": f"{dropped} earlier row(s) were dropped: this session was "
                    f"longer than a saved transcript may be.",
            "detail": "",
        })
    return rows, dropped
