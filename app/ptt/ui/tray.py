"""
The system tray frontend: a pystray icon, its menu, and the state-to-icon map.

This module may import the engine; the engine must never import this one
(design.md section 4).

Threading. pystray owns the main thread inside `run()`, and the engine runs on a
daemon thread it spawns. So `on_state` is called from the engine thread and
mutates `icon.icon` / `icon.title` / `icon.menu` from there. That is deliberate
and is what the pre-split tray already did: pystray's setters post to the icon's
own message loop rather than requiring the caller to be on it. Adding
marshalling here would be a behaviour change, not a fix.
"""

import threading
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

from ptt import hotkey as hotkey_mod
from ptt.logging_setup import log_debug

if TYPE_CHECKING:                    # import for typing only; at runtime the
    from ptt.engine import Engine    # tray never needs the class itself


def create_icon_image(state):
    """Draw a status icon programmatically without needing external image assets."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if state == "recording":
        d.ellipse((4, 4, 60, 60), fill=(239, 68, 68), outline=(185, 28, 28), width=4)
        d.ellipse((20, 20, 44, 44), fill=(255, 255, 255))
    elif state == "transcribing":
        d.ellipse((4, 4, 60, 60), fill=(245, 158, 11), outline=(217, 119, 6), width=4)
        d.rounded_rectangle((22, 22, 42, 42), radius=4, fill=(255, 255, 255))
    elif state == "loading":
        d.ellipse((4, 4, 60, 60), fill=(59, 130, 246), outline=(29, 78, 216), width=4)
        d.arc((16, 16, 48, 48), start=0, end=270, fill=(255, 255, 255), width=5)
    else:  # "idle"
        d.ellipse((4, 4, 60, 60), fill=(13, 148, 136), outline=(15, 118, 110), width=4)
        d.rounded_rectangle((26, 16, 38, 36), radius=6, fill=(255, 255, 255))
        d.arc((20, 22, 44, 38), start=0, end=180, fill=(255, 255, 255), width=3)
        d.line((32, 38, 32, 46), fill=(255, 255, 255), width=3)
        d.line((24, 46, 40, 46), fill=(255, 255, 255), width=3)

    return img


class TrayApp:
    """
    Built in two phases, because the tray and the engine each need the other:

        tray = TrayApp(settings, cuda_supported)
        engine = Engine(settings, cuda_supported, on_state=tray.on_state)
        tray.attach(engine)
        tray.run()

    `on_state` is safe to call before `attach` and before `run`, since it only
    touches `self._icon`, which stays None until the icon is built.
    """

    def __init__(self, settings, cuda_supported):
        self._settings = settings
        self.cuda_supported = cuda_supported
        self._engine: "Engine"   # assigned by attach(), always before run()
        self._icon = None
        self._status = "Initializing..."

    def attach(self, engine):
        self._engine = engine

    # -- the Engine callback ------------------------------------------------

    def on_state(self, state, status_text=None):
        """Update icon image, tooltip, and status menu item dynamically."""
        self._status = status_text if status_text else state.capitalize()

        if self._icon:
            self._icon.icon = create_icon_image(state)
            self._icon.title = f"PTT Dictation ({self._status})"
            self._icon.menu = self._create_menu()

    # -- menu ---------------------------------------------------------------

    def _set_device_gpu(self, icon_obj, item_obj):
        if not self._settings.use_gpu:
            self._settings.use_gpu = True
            self._settings.save()
            self._engine.request_model_reload()

    def _set_device_cpu(self, icon_obj, item_obj):
        if self._settings.use_gpu:
            self._settings.use_gpu = False
            self._settings.save()
            self._engine.request_model_reload()

    def _on_exit(self, icon_obj, item_obj):
        log_debug("Exit requested by user.")
        self._engine.stop()
        # Do NOT join the engine thread here. It is a daemon and the process
        # exits via os._exit immediately after icon.run() returns; joining would
        # block for an in-flight transcription -- up to 30 s on CPU -- turning
        # "Exit" into "hang".
        icon_obj.stop()

    def _create_menu(self):
        """Build the dynamic tray icon menu."""
        status_label = f"Status: {self._status}"

        return pystray.Menu(
            item(status_label, lambda icon_obj, item_obj: None, enabled=False),
            item(f"Hotkey: {hotkey_mod.chord_label(self._settings.hotkey)}",
                 lambda icon_obj, item_obj: None, enabled=False),
            pystray.Menu.SEPARATOR,
            item(
                "Use GPU (CUDA)",
                self._set_device_gpu,
                checked=lambda item_obj: self._settings.use_gpu,
                enabled=lambda item_obj: self.cuda_supported
            ),
            item(
                "Use CPU",
                self._set_device_cpu,
                checked=lambda item_obj: not self._settings.use_gpu
            ),
            pystray.Menu.SEPARATOR,
            item("Exit", self._on_exit)
        )

    # -- run ----------------------------------------------------------------

    def _setup(self, icon_obj):
        icon_obj.visible = True
        log_debug("System Tray icon made visible. Starting background thread...")
        threading.Thread(target=self._engine.run, daemon=True).start()

    def run(self):
        """Build the icon and run pystray's loop. Blocks the calling thread."""
        self._icon = pystray.Icon(
            "ptt_dictate",
            create_icon_image("loading"),
            "PTT Dictation (Initializing...)",
            menu=self._create_menu()
        )
        log_debug("Starting tray icon event loop...")
        self._icon.run(self._setup)
