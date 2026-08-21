"""
PTT Dictation -- the shared implementation behind both entry points.

This package intentionally imports nothing.

`ptt.transcribe` must not be loaded until the NVIDIA DLL directories have been
registered (see `transcribe.ensure_cuda_dll_dirs`). A convenience re-export here
would pull CTranslate2 in on `import ptt` and silently drop the application to
CPU inference -- a ~20x slowdown that raises no error and looks exactly like
working software (retrospective issue #1).

`ptt.audio` imports sounddevice and terminates PortAudio at module scope
(issue #6), so a re-export would also make `import ptt` touch audio hardware,
which the unit tests must be able to avoid.

Import submodules explicitly:

    from ptt import config
    from ptt.engine import Engine
"""

__all__ = []
