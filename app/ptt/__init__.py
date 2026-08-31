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

#: The released version, and the single place it is written down.
#:
#: `build_portable.py` reads it out of this file with a regular expression
#: rather than importing it, because importing `ptt` from the repository root
#: means putting `app/` on `sys.path`, and the build script has no other reason
#: to. It stamps the archive's payload manifest with what it finds, which is how
#: `install.ps1` can name the version it is installing without a second copy of
#: the number to keep in step.
__version__ = "3.0.1"
