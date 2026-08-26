"""
CON-CG-6: the harness imports no Qt, and the packaging rules that go with it.

`V-CG-79` … `V-CG-88`. The import test is the one that keeps the constraint
true rather than merely stated: PySide6 is *installed* in this environment, so
an accidental `from PySide6...` in a harness module would import perfectly well
and nothing would notice until the CLI rig was run on a machine without Qt --
or until session 5 discovered the qualification suite could not start.

The two things resting on it are named in the package docstring: the CLI rig
runs the real agent loop with zero app involvement, and every module here is
unit-testable without a `QApplication`.
"""

import builtins
import importlib
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = ("state", "tools", "llm", "agent", "server", "fetch",
           "sessions")


@pytest.fixture
def without_qt(monkeypatch):
    """
    Make every PySide6 import raise, then hand back a clean import cache.

    Deleting the modules from `sys.modules` is what makes this real: without it
    a harness module that had already been imported by another test would
    resolve from the cache and the block would never be exercised.
    """
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in ("PySide6", "shiboken6"):
            raise ImportError(f"{name} is not available (CON-CG-6)")
        return real_import(name, *args, **kwargs)

    for name in list(sys.modules):
        if name.startswith(("PySide6", "shiboken6", "ptt.concierge", "ptt.ui")):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    return blocked


@pytest.mark.parametrize("module", HARNESS)
def test_every_harness_module_imports_with_qt_absent(module, without_qt):
    imported = importlib.import_module(f"ptt.concierge.{module}")
    assert imported.__name__.endswith(module)


def test_the_whole_agent_runs_with_qt_absent(without_qt, tmp_path):
    """
    Not just importable -- *usable*. Constructing the registry, generating both
    request shapes and assembling a prefix are what the CLI rig does before it
    has said a word to a model, and all of it must work with no Qt anywhere.
    """
    config = importlib.import_module("ptt.config")
    tools = importlib.import_module("ptt.concierge.tools")
    llm = importlib.import_module("ptt.concierge.llm")
    agent = importlib.import_module("ptt.concierge.agent")

    settings = config.Settings(path=str(tmp_path / "config.json"))
    registry = tools.Registry(settings)
    assert len(llm.grammar_schema(registry)["oneOf"]) == 2
    assert len(llm.tools_array(registry)) == 8

    context = agent.Context("pack", "rules", registry)
    messages, trims = context.assemble([], 0)
    assert messages[0]["role"] == "system" and trims == []


def test_qt_really_is_blocked_by_the_fixture(without_qt):
    """
    A guard against the guard. If the fixture silently stopped working, every
    test above would pass for the wrong reason.
    """
    with pytest.raises(ImportError):
        importlib.import_module("PySide6.QtCore")


def harness_imports():
    """
    Every module name any harness module imports, parsed rather than grepped.

    `ast`, not a substring search over the source: these modules argue for their
    own design in their docstrings, and half of that argument is about Qt. A
    grep would score the explanation as the violation.
    """
    import ast

    directory = os.path.join(REPO, "app", "ptt", "concierge")
    found = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(directory, name), encoding="utf-8").read())
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        found[name] = modules
    return found


def test_no_harness_module_imports_qt_anywhere():
    """
    The static half, and it catches what the runtime test cannot: an import
    *inside a function* would pass the fixture above -- nothing calls it during
    the import -- while still tying the harness to Qt.
    """
    for name, modules in harness_imports().items():
        offenders = [m for m in modules
                     if m.split(".")[0] in ("PySide6", "shiboken6")]
        assert offenders == [], (name, offenders)


def test_no_harness_module_uses_qprocess():
    """
    Q8, named so it cannot come back quietly. `concierge_handoff.md` said
    `QProcess` in two places; design 2 struck it, because a `QProcess` cannot
    start a server outside a Qt event loop and the CLI rig -- with it the whole
    qualification suite -- would not exist.
    """
    import ast

    directory = os.path.join(REPO, "app", "ptt", "concierge")
    offenders = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(directory, name), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "QProcess":
                offenders.append(name)
            if isinstance(node, ast.Attribute) and node.attr == "QProcess":
                offenders.append(name)
    assert offenders == []


def test_the_harness_never_imports_the_ui_package():
    """
    Design 2's dependency arrow, in one direction only: the app imports the
    harness, the harness imports nothing from `ptt.ui`. `get_state` is where
    this would break first, which is why the shape is declared in `tools.py`
    instead of imported from `qt_statusview`.
    """
    for name, modules in harness_imports().items():
        offenders = [m for m in modules if m.startswith("ptt.ui")]
        assert offenders == [], (name, offenders)


def test_the_state_shape_is_declared_here_not_imported(without_qt):
    """
    Q26. `UiState` is a plain dataclass, but its module imports PySide6 at
    column 0, so importing the type would breach CON-CG-6. The harness declares
    the shape; the Qt adapter satisfies it.
    """
    tools = importlib.import_module("ptt.concierge.tools")
    assert tools.STATE_KEYS
    assert "UiState" not in open(
        os.path.join(REPO, "app", "ptt", "concierge", "tools.py"),
        encoding="utf-8").read().replace("`UiState`", "")


def test_the_declared_state_keys_cover_what_the_banner_shows():
    """
    The Qt-side half of Q26's seam lands in session 3. What can be checked now
    is that the harness asks for every field `UiState` actually has, plus its
    derived detail line -- so the adapter has something to fill rather than
    something to invent.
    """
    from ptt.concierge import tools
    from ptt.ui.qt_statusview import UiState

    fields = set(UiState.__dataclass_fields__)
    assert fields <= set(tools.STATE_KEYS)
    assert "detail" in tools.STATE_KEYS


# -- packaging (Q27) ----------------------------------------------------------

def test_model_weights_are_never_packed():
    """
    **Q27, and the reason it needed a directory rule.** `should_skip`'s
    runtime-artifact test fires only when `root == "app"` -- the top level -- and
    `os.walk` packs everything nested below unconditionally. For a 6.87 GB GGUF
    at `app/models/concierge/` that is CON-CG-4 breached in the most expensive
    possible way.
    """
    sys.path.insert(0, REPO)
    import build_portable

    assert build_portable.should_skip_dir("app", "models")
    assert build_portable.should_skip_dir("app", "assets") is None
    assert build_portable.should_skip(
        "app", os.path.join("app", "models", "concierge"),
        "gemma-4-12B-it-Q4_K_M.gguf")
    assert build_portable.should_skip(
        "app", os.path.join("app", "models"), "anything.bin")
    assert build_portable.should_skip(
        "app", os.path.join("app", "assets"), "concierge_kb.md") is None


def test_the_concierge_runtime_artifacts_never_ship():
    """
    `concierge_state.json` names a pid and a port that mean nothing on another
    machine; `concierge_key` is one launch's API key; the memory note is the
    user's own text about their own machine.
    """
    sys.path.insert(0, REPO)
    import build_portable

    for name in ("concierge_state.json", "concierge_key",
                 "concierge_memory.txt", "concierge_memory.prev.txt"):
        assert name in build_portable.RUNTIME_ARTIFACTS, name
        assert build_portable.should_skip("app", "app", name)


def test_config_json_still_never_ships():
    """The rule this extends, unchanged: a shipped config.json overwrites the
    user's saved settings on every reinstall."""
    sys.path.insert(0, REPO)
    import build_portable

    assert build_portable.should_skip("app", "app", "config.json")
    assert build_portable.should_skip("app", "app", "debug_log.txt")


def test_the_knowledge_pack_does_ship():
    """
    The one Concierge artifact that must be in the archive. It lives under
    `app/assets/`, which `os.walk` picks up automatically -- the property
    `design.md` section 2 records approvingly and which Q27 had to carve an
    exception out of for weights.
    """
    sys.path.insert(0, REPO)
    import build_portable

    assert build_portable.should_skip(
        "app", os.path.join("app", "assets"), "concierge_kb.md") is None


def test_the_installer_preserves_models_and_settings():
    """
    Q27's other half. `install.ps1` deleted `$TargetDir` recursively before
    copying, so every reinstall destroyed the downloaded weights *and* the
    user's settings -- an existing v2 defect that v3 makes expensive rather than
    merely annoying.
    """
    text = open(os.path.join(REPO, "install.ps1"), encoding="utf-8").read()
    assert "$PreserveDir" in text
    assert 'Move-Item -Path "$TargetDir\\app\\models"' in text
    assert 'Move-Item -Path "$TargetDir\\app\\config.json"' in text
    # Set aside *before* the delete, put back *after* the copy.
    assert text.index("$PreservedModels = $true") < text.index("Remove-Item -Path $TargetDir")
    assert text.index("Remove-Item -Path $TargetDir") < text.index("Restoring downloaded models")
