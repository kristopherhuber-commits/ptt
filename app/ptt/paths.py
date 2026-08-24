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
