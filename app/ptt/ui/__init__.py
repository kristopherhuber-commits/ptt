"""
Frontends: the tray icon and, later, the hotkey picker dialog.

`ptt.engine` must never import from this package. The engine reports state
through a callback its caller supplies, which is what allows one core to serve
both a tray icon and a console (design.md section 4).
"""

__all__ = []
