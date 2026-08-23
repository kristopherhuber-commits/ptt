"""
Fonts and the stylesheet.

Both are loaded once, from `ptt.paths`, never from the working directory: the
application is launched from a Desktop shortcut and from the Startup folder, so
the cwd is not predictable.

**Fonts must be registered after QApplication exists**, not merely "before any
widget is created". QFontDatabase needs a live QGuiApplication; calling it
earlier returns -1 and every heading silently falls back to the default face,
which looks like a styling bug rather than an ordering one.

Failure here is never fatal. A missing stylesheet or an unregistered font
produces a working-but-plain window, so both paths log and continue rather than
taking the application down over cosmetics.
"""

from PySide6.QtGui import QFont, QFontDatabase

from ptt import paths
from ptt.logging_setup import log_debug

#: The four faces actually used: Barlow 400/500/700 for body text and values,
#: Barlow Condensed 600 for headings and the small-caps row labels. The repo
#: carries every other weight and italic; they are deliberately not registered.
FONT_FILES = (
    ("Barlow", "Barlow-Regular.ttf"),
    ("Barlow", "Barlow-Medium.ttf"),
    ("Barlow", "Barlow-Bold.ttf"),
    ("Barlow_Condensed", "BarlowCondensed-SemiBold.ttf"),
)

#: Used when Barlow cannot be registered. Named explicitly rather than left to
#: Qt, which would otherwise pick something arbitrary per machine.
FALLBACK_FAMILY = "Segoe UI"


def register_fonts():
    """
    Load the bundled TTFs into the application font database.

    Returns the list of families Qt actually registered, which is empty if none
    loaded. Never raises.
    """
    families = []
    for subdir, filename in FONT_FILES:
        path = paths.asset_path("fonts", subdir, filename)
        try:
            font_id = QFontDatabase.addApplicationFont(path)
        except Exception as e:
            log_debug(f"Font registration raised for {filename}: {e}")
            continue
        if font_id == -1:
            log_debug(f"WARNING: font failed to register: {path}")
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in families:
                families.append(family)

    if families:
        log_debug(f"Registered application fonts: {families}")
    else:
        log_debug(
            f"WARNING: no bundled fonts registered; falling back to "
            f"{FALLBACK_FAMILY}. Looked in {paths.assets_dir()}."
        )
    return families


def base_font(families):
    """The application-wide default face, given whatever actually registered."""
    family = "Barlow" if any(f == "Barlow" for f in families) else FALLBACK_FAMILY
    return QFont(family, 10)


def load_stylesheet():
    """
    Read style.qss. Returns "" if it is missing, having logged that it is.

    An empty stylesheet is survivable -- the window still works, it is just
    unstyled -- but it must not be silent, because "the colours are wrong" is
    otherwise very hard to trace back to a missing file in the distribution.
    """
    path = paths.asset_path("style.qss")
    try:
        with open(path, "r", encoding="utf-8") as f:
            qss = f.read()
        log_debug(f"Loaded stylesheet: {path} ({len(qss)} chars)")
        return qss
    except Exception as e:
        log_debug(f"WARNING: could not load stylesheet {path}: {e}. UI will be unstyled.")
        return ""


def apply_theme(app):
    """Register fonts and apply the stylesheet to a live QApplication."""
    families = register_fonts()
    app.setFont(base_font(families))
    qss = load_stylesheet()
    if qss:
        app.setStyleSheet(qss)
    return families
