"""
Filesystem locations owned by the application.

This module is the single owner of every application-relative path. No other
module computes a directory.

That rule is not tidiness. The paths are anchored one level *above* this
package: before the split, `app/ptt_tray.py` derived them from its own
`__file__`, which sat in `app/`. This file sits in `app/ptt/`, so deriving from
`__file__` here without stepping back up would silently relocate config.json,
debug_log.txt and the models directory into `app/ptt/`, orphaning the saved
settings of every existing installation.
"""

import os
import sys

#: .../app/ptt -- where this package lives.
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

#: .../app -- the directory that owns config.json, debug_log.txt and models/.
#:
#: The sys.frozen branch is currently unreachable: the shipped ptt_dictate.exe
#: is a byte-for-byte copy of pythonw.exe (build_portable.py), not a PyInstaller
#: bundle, so sys.frozen is always False. It is kept because the adjacent
#: _MEIPASS handling in transcribe.py is likewise retained (design.md #9).
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(PACKAGE_DIR)


def config_path():
    """Absolute path of config.json (design.md section 7)."""
    return os.path.join(APP_DIR, "config.json")


def debug_log_path():
    """Absolute path of the current session's log (OBS-4)."""
    return os.path.join(APP_DIR, "debug_log.txt")


def previous_debug_log_path():
    """Where the log is rotated at startup, so one session survives the next."""
    return os.path.join(APP_DIR, "debug_log.prev.txt")


def local_model_dir(model_size):
    """Directory a bundled model would occupy, if one has been shipped."""
    return os.path.join(APP_DIR, "models", model_size)


# -- the Concierge (v3.0) ----------------------------------------------------
#
# Six per-machine artifacts and one shipped one. The five that live beside
# config.json are runtime state, not configuration, and `build_portable.py`
# excludes every one of them by name; `app/models/` is excluded as a whole
# directory, because a 6.87 GB GGUF inside a distribution zip is CON-CG-4
# breached in the most expensive possible way.

def concierge_model_dir():
    """Where the downloaded Concierge GGUF lives (`concierge_design.md` 10 Q4)."""
    return os.path.join(APP_DIR, "models", "concierge")


def concierge_state_path():
    """
    `{pid, create_time, port}` for the running llama-server (design 8.1, Q11).

    Written *before* `Popen` and deleted on clean shutdown, so a startup reap
    has something to identify an orphan by that does not require reading
    another process's command line.
    """
    return os.path.join(APP_DIR, "concierge_state.json")


def concierge_key_path():
    """The per-launch `--api-key-file` for the loopback listener (design 2, Q19)."""
    return os.path.join(APP_DIR, "concierge_key")


def memory_note_path():
    """The Concierge's durable memory note (FR-CG-14)."""
    return os.path.join(APP_DIR, "concierge_memory.txt")


def previous_memory_note_path():
    """
    The one kept previous version of the note (FR-CG-14, Q22).

    The `OBS-4` log-rotation idiom applied to the only durable state the
    Concierge has: without it, one bad autonomous write erases everything it has
    learned and repairing it by hand needs knowing what it used to say.
    """
    return os.path.join(APP_DIR, "concierge_memory.prev.txt")


def concierge_sessions_path():
    """
    The saved Concierge transcripts (FR-CG-13, `concierge/sessions.py`).

    One JSON file rather than a directory, and beside `config.json` rather than
    under it: `build_portable.py` excludes per-machine artifacts by file name at
    the top level of `app/`, so a file costs one entry in that frozenset where a
    directory would have cost a second exclusion rule (Q27).
    """
    return os.path.join(APP_DIR, "concierge_sessions.json")


def concierge_prompt_path():
    """
    The Concierge's system prompt, a versioned artifact in the package.

    D-CG-12 (design 4.5): the prompt is harness code in the same sense the
    grammar is, so it is a file in git, loaded at construction, never assembled
    inline -- and gate 2.5 records its hash in every scorecard row, because a
    prompt that moves between candidates makes the scorecards incomparable.

    Here rather than derived in `concierge/agent.py` because this module is the
    single owner of every path the application computes, and a resource beside a
    module is still a path.
    """
    return os.path.join(PACKAGE_DIR, "concierge", "system_prompt.md")


def knowledge_pack_path():
    """The generated knowledge pack, shipped as an asset (design 5.05)."""
    return asset_path("concierge_kb.md")


def llama_server_path():
    """The bundled llama-server executable (CON-CG-2), shipped under `app/`."""
    return os.path.join(APP_DIR, "llama", "llama-server.exe")


def startup_shortcut_path():
    """
    The Startup-folder shortcut `install.ps1` creates, whose presence the
    Advanced panel reports.

    Built from `%APPDATA%` rather than through a shell-folder COM lookup: this
    module owns paths and depends on nothing, and `install.ps1` writes the file
    with `[Environment]::GetFolderPath('Startup')`, which resolves to the same
    directory. The panel only reports whether it is there -- creating or
    removing it is the installer's job, and duplicating its `.lnk` construction
    and its run-as-admin byte patch inside the app is not in this pass.
    """
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
        "PTT Dictation.lnk",
    )


def assets_dir():
    """Directory holding the bundled fonts, stylesheet and benchmark clip."""
    return os.path.join(APP_DIR, "assets")


def asset_path(*parts):
    """
    Absolute path of one bundled asset.

        asset_path("style.qss")
        asset_path("fonts", "Barlow", "Barlow-Regular.ttf")

    The UI must resolve assets through here rather than from the working
    directory: the application is launched from a Desktop shortcut and from the
    Startup folder, so the cwd is not predictable. `build_portable.py` walks
    `app/` wholesale, so anything under this directory ships automatically.
    """
    return os.path.join(assets_dir(), *parts)

