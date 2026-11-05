"""
Where the text cursor is on screen, for the double-tap's small mark (FR-13).

Two sources, in order:

1. **UI Automation**, the interface screen readers use: the focused element's
   text selection, widened to one character, gives the cell the next typed
   character would go in. Windows Terminal answers this way and no other
   (`spike/v4_caret_probe.ps1`, 2026-11-04).
2. **The Windows system caret** (`GetGUIThreadInfo`), which classic Win32 edit
   controls own.

Every rectangle is `(left, top, width, height)` in physical screen pixels.
`cursor_cell` never raises, and gives up after `timeout` seconds: a window that
has stopped responding can stall UI Automation indefinitely, so the lookup runs
on a thread of its own and is abandoned rather than waited for.
"""

import ctypes
import threading

from ptt.logging_setup import log_debug

UIA_TEXT_PATTERN_ID = 10014
TEXT_UNIT_CHARACTER = 0


def first_rect(values):
    """
    The first non-empty rectangle in UI Automation's flat list of doubles,
    `[left, top, width, height, ...]`, as integers; None if there is none.
    """
    values = list(values or ())
    for i in range(0, len(values) - 3, 4):
        left, top, width, height = values[i:i + 4]
        if width > 0 and height > 0:
            return round(left), round(top), round(width), round(height)
    return None


def cell_after_caret(left, top, right, bottom):
    """
    The system caret is a thin bar between characters; the next character goes
    in the square just right of its left edge.
    """
    height = bottom - top
    if height <= 0:
        return None
    return left, top, height, height


def _uia_cell():
    import comtypes
    import comtypes.client
    # The thread that first imports comtypes is initialised by the import
    # itself; any other lookup thread initialises COM here, and undoes it.
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        owned = True
    except OSError:
        owned = False
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia
        automation = comtypes.client.CreateObject(
            uia.CUIAutomation, interface=uia.IUIAutomation)
        element = automation.GetFocusedElement()
        if not element:
            return None
        pattern = element.GetCurrentPattern(UIA_TEXT_PATTERN_ID)
        if not pattern:
            return None
        text = pattern.QueryInterface(uia.IUIAutomationTextPattern)
        selection = text.GetSelection()
        if not selection or selection.Length == 0:
            return None
        cursor = selection.GetElement(0).Clone()
        cursor.ExpandToEnclosingUnit(TEXT_UNIT_CHARACTER)
        return first_rect(cursor.GetBoundingRectangles())
    finally:
        if owned:
            comtypes.CoUninitialize()


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
        ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", ctypes.c_long * 4),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _system_caret_cell():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    gti = _GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
    tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gti)) or not gti.hwndCaret:
        return None
    left, top, right, bottom = gti.rcCaret
    corner = _POINT(left, top)
    if not user32.ClientToScreen(ctypes.c_void_p(gti.hwndCaret), ctypes.byref(corner)):
        return None
    return cell_after_caret(corner.x, corner.y, corner.x + right - left,
                            corner.y + bottom - top)


def _lookup():
    for name, source in (("UI Automation", _uia_cell), ("system caret", _system_caret_cell)):
        try:
            cell = source()
        except Exception as e:
            log_debug(f"Cursor lookup through {name} failed: {e}")
            continue
        if cell is not None:
            return cell
    return None


def cursor_cell(timeout=0.5):
    """The text cursor's cell, or None if no source answers within `timeout`."""
    found = []
    worker = threading.Thread(target=lambda: found.append(_lookup()),
                              name="cursor-lookup", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        log_debug(f"Cursor lookup gave no answer within {timeout} s.")
        return None
    return found[0] if found else None
