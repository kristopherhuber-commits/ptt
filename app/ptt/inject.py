"""
Keystroke injection. The only module permitted to call `keybd_event`.

Five rules, each bought with a bug report (docs/design.md section 5):

1. **Every event carries a real scan code** from `MapVirtualKeyW`. UWP targets
   reject synthetic keys that have none (FR-C1, issue #8).
2. **Navigation-block keys set `KEYEVENTF_EXTENDEDKEY`** (0x01). `Insert` is in
   that block (FR-C1, issue #8).
3. **Alt is disarmed before it is released.** See `suppress_alt_menu` (FR-C3,
   issue #11).
4. **Modifier release is conditional and side-aware.** Only modifiers actually
   reported down are released, and `VK_LCONTROL`/`VK_RCONTROL` are released
   explicitly: injecting the unsided `VK_CONTROL` release leaves the right-hand
   key state set.
5. **The clipboard is saved and restored around every paste** (FR-C4, issue #5).
   Insertion goes via the clipboard, so the user's contents must come back.

Insertion uses `Shift+Insert` rather than `Ctrl+V` because WSL and terminal
targets accept the former, and a single paste keystroke rather than per-character
typing because a modifier still physically held turns typed characters into
shortcuts (FR-C1, issue #5).
"""

import ctypes
import time

import keyboard
import pyperclip

#: Every virtual key that counts as Alt, for the menu-activation guard.
ALT_VKS = (0x12, 0xA4, 0xA5)

#: Modifiers neutralised before pasting. Both sides are listed explicitly:
#: releasing the unsided VK_CONTROL leaves the right-hand key state set.
NEUTRALISE_VKS = (0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)

#: Reserved, unassigned virtual key. Produces no character and no command, so
#: it is safe to inject purely to break up an Alt press (see suppress_alt_menu).
VK_NONAME = 0xFC

#: The chord `paste_text` injects, named here so the Advanced panel reports the
#: method actually in force instead of a string transcribed beside it. It is
#: Shift+Insert rather than Ctrl+V because WSL and bash terminals accept the
#: former and swallow the latter (rule 5 above, FR-C1).
PASTE_CHORD_LABEL = "Shift+Insert"

GUI_CARETBLINKING = 0x00000001


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("flags", ctypes.c_uint),
        ("hwndActive", ctypes.c_void_p), ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p), ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p), ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", ctypes.c_long * 4),
    ]


def _send_key(vk, keyup=False, extended=False):
    """Inject one key event carrying a real hardware scan code (UWP apps reject bare VKs)."""
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = (0x01 if extended else 0) | (0x02 if keyup else 0)
    ctypes.windll.user32.keybd_event(vk, scan, flags, 0)


def _alt_is_down():
    return any((ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in ALT_VKS)


def suppress_alt_menu():
    """
    Stop an Alt release from opening the focused window's menu bar.

    Windows activates the menu -- or, in WinUI apps like Windows 11 Notepad, the
    access-key layer -- when Alt goes up and no other key was pressed in between.
    That moves keyboard focus off the document: the caret disappears and every
    subsequent injected keystroke, Shift+Insert and Ctrl+V alike, is discarded.
    Tapping a reserved unassigned key while Alt is still held supplies the
    missing intervening keypress, so the release becomes inert.
    """
    if not _alt_is_down():
        return
    try:
        _send_key(VK_NONAME)
        _send_key(VK_NONAME, keyup=True)
    except Exception:
        pass


def target_accepts_keys():
    """
    Report whether the focused window still owns a text caret.

    A missing caret is how a swallowed paste announces itself: menu or access-key
    activation moves focus off the document, and injected keystrokes vanish
    silently. Diagnostic only -- pasting is attempted either way.

    Known false positive: consoles that draw their own cursor rather than owning
    a Win32 caret (Windows Terminal, class CASCADIA_HOSTING_WINDOW_CLASS) always
    report False here even though the paste lands.
    """
    try:
        gti = GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(GUITHREADINFO)
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
        if not ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            return True
        return bool(gti.flags & GUI_CARETBLINKING) or bool(gti.hwndCaret)
    except Exception:
        return True


def foreground_window_class():
    """Window class of the paste target, recorded so failures name the culprit app."""
    try:
        buf = ctypes.create_unicode_buffer(256)
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return "?"


def paste_text(text):
    """Insert text at the cursor by copying to clipboard and simulating Shift+Insert via native Win32 API."""
    if not text:
        return

    # Save original clipboard contents
    try:
        old_clipboard = pyperclip.paste()
    except Exception:
        old_clipboard = None

    # Copy the transcribed text to clipboard
    try:
        pyperclip.copy(text)
    except Exception:
        # Fallback to direct typing if clipboard copy fails
        try:
            keyboard.write(text)
        except Exception:
            pass
        return

    # Neutralise any modifier still physically held, so the paste chord is not
    # reinterpreted as a shortcut by the target window. Alt is disarmed first:
    # releasing it bare would activate the window's menu and steal focus.
    try:
        suppress_alt_menu()
        for vk in NEUTRALISE_VKS:
            if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                _send_key(vk, keyup=True)
    except Exception:
        for key in ("ctrl", "alt", "win"):
            try:
                keyboard.release(key)
            except Exception:
                pass

    # Simulate Shift+Insert to paste via native Win32 keybd_event with scan codes and extended key flags
    try:
        time.sleep(0.01)
        _send_key(0x10)                              # Shift down
        _send_key(0x2D, extended=True)               # Insert down (extended)
        _send_key(0x2D, keyup=True, extended=True)   # Insert up (extended)
        _send_key(0x10, keyup=True)                  # Shift up
    except Exception:
        try:
            keyboard.press_and_release("shift+insert")
        except Exception:
            pass

    # Wait for Windows to process the paste before restoring clipboard
    time.sleep(0.1)

    # Restore original clipboard contents
    if old_clipboard is not None:
        try:
            pyperclip.copy(old_clipboard)
        except Exception:
            pass
