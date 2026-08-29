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
import inspect
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


def build_module():
    """`build_portable.py` as a module. It is a script at the repository root,
    not a package, so importing it needs the path put back."""
    sys.path.insert(0, REPO)
    import build_portable
    return build_portable


# -- the bundled llama.cpp runtime (V-CG-134 ... V-CG-137, session 5) ---------

LLAMA = os.path.join("app", "llama")


def test_only_llama_servers_dependency_closure_ships():
    """
    **`V-CG-134`.** `app/llama/` is the one directory in the distribution whose
    contents somebody else decides: `build_llama_runtime.py` unpacks whatever
    the pinned nightly publishes, which for `b10621` is 55 files and 1.10 GB.
    Eight of them are `llama-server.exe` and the DLLs it imports; the rest are
    other people's command-line tools, including a quantiser, a benchmark
    harness and `ggml-rpc-server.exe`, which is a *network listener*.

    An allowlist rather than a blocklist, because a blocklist ships the tool the
    next build adds.
    """
    build = build_module()

    for name in ("llama-server.exe", "llama-server-impl.dll", "llama.dll",
                 "llama-common.dll", "ggml.dll", "ggml-base.dll",
                 "libomp.dll", "mtmd.dll", "ggml-cuda.dll"):
        assert build.should_skip("app", LLAMA, name) is None, name

    for name in ("llama-bench.exe", "llama-cli.exe", "llama-quantize.exe",
                 "ggml-rpc-server.exe", "ggml-rpc.dll", "llama-tts.exe",
                 "llama-imatrix.exe", "llama.exe", "llama-mtmd-cli.exe"):
        assert build.should_skip("app", LLAMA, name), name


def test_the_cuda_runtime_dlls_ship_and_the_archives_they_came_in_do_not():
    """
    **`V-CG-135`.** Two halves of one mistake.

    The `cudart-*` archive is separate from the binaries archive, and a build
    that ships only the second produces an executable that will not start on a
    machine with no CUDA toolkit -- which is every target PC. cuBLAS arrives
    from the CTranslate2 wheels instead and is `V-CG-141`'s business; `cudart`
    has no second source and still ships.

    And `fetch.bundle_llama_runtime` leaves both downloaded zips **inside the
    destination directory**, which is `app/llama/`. Without a rule they are
    640 MB of installer packed into the installer.
    """
    build = build_module()

    assert build.should_skip("app", LLAMA, "cudart64_12.dll") is None

    for name in ("llama-b10621-bin-win-cuda-12.4-x64.zip",
                 "cudart-llama-bin-win-cuda-12.4-x64.zip"):
        assert build.should_skip("app", LLAMA, name), name


def test_every_cpu_backend_ships():
    """
    **`V-CG-136`.** `ggml-base` probes each `ggml-cpu-*.dll` at startup and
    keeps the best the CPU admits to. Shipping only the one this machine chose
    would tie the distribution to the build machine's instruction set; all
    fourteen cost 17 MB, which is the whole argument.
    """
    build = build_module()

    for arch in ("alderlake", "haswell", "icelake", "sandybridge", "sse42",
                 "x64", "zen4", "piledriver", "skylakex", "sapphirerapids"):
        name = f"ggml-cpu-{arch}.dll"
        assert build.should_skip("app", LLAMA, name) is None, name


def test_the_licence_of_a_bundled_component_travels_with_it():
    """
    **`V-CG-137`, and it is the OFL precedent (`V-M-64`) applied to CON-CG-2.**

    llama.cpp is MIT and neither published archive contains its licence: the
    binaries zip carries `LICENSE-LLVM-OpenMP` for its one vendored dependency
    and nothing for llama.cpp itself. So the notice has to be fetched, and a
    build without it is a build that must not run -- which is why the name is in
    `LLAMA_REQUIRED` and not merely in the allowlist.
    """
    build = build_module()

    assert build.should_skip("app", LLAMA, "LICENSE-llama.cpp") is None
    assert build.should_skip("app", LLAMA, "LICENSE-LLVM-OpenMP") is None
    assert "LICENSE-llama.cpp" in build.LLAMA_REQUIRED
    assert set(build.LLAMA_REQUIRED) <= build.LLAMA_RUNTIME_FILES


def test_the_build_refuses_to_ship_without_the_runtime(tmp_path):
    """
    **`V-CG-137`.** The failure this prevents surfaces on the user's machine and
    nowhere on the build machine: a distribution whose `app/llama/` is empty
    installs, runs and dictates, and then reports that the Concierge could not
    start. On the build machine the developer's own runtime is one directory
    away, so nothing looks wrong.
    """
    build = build_module()

    assert build.check_llama_runtime(str(tmp_path / "absent"))
    empty = tmp_path / "llama"
    empty.mkdir()
    assert len(build.check_llama_runtime(str(empty))) == len(build.LLAMA_REQUIRED)

    for name in build.LLAMA_REQUIRED:
        (empty / name).write_text("x", encoding="utf-8")
    assert build.check_llama_runtime(str(empty)) == []

    (empty / "LICENSE-llama.cpp").unlink()
    assert build.check_llama_runtime(str(empty)) == [
        os.path.join(str(empty), "LICENSE-llama.cpp") + " is missing"]


def test_the_licence_fetch_refuses_to_run_in_the_shipped_app():
    """
    **`V-CG-137`.** `fetch_llama_licence` reaches raw.githubusercontent.com,
    which is outside FR-CG-10's allowlist, so it carries the same explicit
    build-time token `bundle_llama_runtime` does. The guard is an argument the
    caller has to pass on purpose.
    """
    from ptt.concierge import fetch

    with pytest.raises(RuntimeError, match="never runs in the shipped app"):
        fetch.fetch_llama_licence("anywhere")
    with pytest.raises(RuntimeError, match="never runs in the shipped app"):
        fetch.fetch_llama_licence("anywhere", build_time="yes please")


def installer_text():
    return open(os.path.join(REPO, "_internal", "install.ps1"),
                encoding="utf-8").read()


def test_the_root_of_the_distribution_offers_one_executable():
    """
    **`V-CG-140`.** Windows hides known extensions by default -- `HideFileExt`
    is `1` on a stock install -- so `install.bat` and `install.ps1` side by side
    both render as `install`, and the user chooses by icon. The distribution's
    root therefore offers exactly one thing that looks clickable, and the
    helpers live in `_internal/`.

    Checked against the source tree rather than a built archive so it runs in
    L1; `build_portable.py` asserts the same property over the finished zip,
    which is the artefact that matters.
    """
    executable = (".exe", ".bat", ".cmd", ".com")
    offered = sorted(n for n in os.listdir(REPO)
                     if os.path.isfile(os.path.join(REPO, n))
                     and n.lower().endswith(executable))
    assert offered == ["install.bat"], offered

    helpers = os.path.join(REPO, "_internal")
    assert os.path.isfile(os.path.join(helpers, "install.ps1"))
    assert os.path.isfile(os.path.join(helpers, "run_tray.bat"))


def test_the_installer_and_launcher_reach_out_of_their_own_directory():
    """
    **`V-CG-140`.** Both helpers moved one level down, so both have to climb
    back up. `install.ps1` installs its *parent*, and `run_tray.bat` launches
    an interpreter that lives beside `app/`, not beside itself.
    """
    assert "$SourceDir = Split-Path $PSScriptRoot -Parent" in installer_text()

    launcher = open(os.path.join(REPO, "_internal", "run_tray.bat"),
                    encoding="utf-8").read()
    assert 'cd /d "%~dp0.."' in launcher

    entry = open(os.path.join(REPO, "install.bat"), encoding="utf-8").read()
    assert "_internal\\install.ps1" in entry

    # And the shortcuts the installer writes have to point at the new location,
    # or every Desktop icon on an upgraded machine targets a file that moved.
    text = installer_text()
    assert text.count('$TargetDir\\_internal\\run_tray.bat') >= 3


def test_cublas_is_not_shipped_twice():
    """
    **`V-CG-141`, and it is what makes the distribution one download.**

    `ggml-cuda.dll` statically imports `cublas64_12.dll` and
    `cublasLt64_12.dll`; llama.cpp's cuda-12.4 build carries its own 547 MB of
    them, and `requirements.txt` already pins `nvidia-cublas-cu12` for
    CTranslate2 at 735 MB. Shipping both put the archive 42 MiB over GitHub's
    2 GiB release-asset limit.

    `V-M-96` measured that llama-server runs on the pinned copy -- 8395 MiB of
    VRAM and 25.7 tok/s against 8395 MiB and 25.3 tok/s on its own, where a
    server with **no** cuBLAS silently falls back to CPU at 5.7 tok/s and still
    answers, which is why that measurement needed a control.
    """
    build = build_module()

    for name in ("cublas64_12.dll", "cublasLt64_12.dll"):
        assert name not in build.LLAMA_RUNTIME_FILES
        assert build.should_skip("app", LLAMA, name)
    # cudart is a different library and still ships: nothing else provides it.
    assert build.should_skip("app", LLAMA, "cudart64_12.dll") is None
    assert build.should_skip("app", LLAMA, "ggml-cuda.dll") is None

    requirements = open(os.path.join(REPO, "requirements.txt"),
                        encoding="utf-8").read()
    assert "nvidia-cublas-cu12==" in requirements, (
        "llama-server now resolves cuBLAS from the CTranslate2 wheels, so an "
        "unpinned version would move that library underneath it")


def ps_list(name):
    """One `$Name = @( ... )` array, read out of `install.ps1`."""
    text = installer_text()
    body = text[text.index(f"${name} = @("):]
    body = body[: body.index(")")]
    return {line.strip().strip('",') for line in body.splitlines()[1:]
            if line.strip()}


def preserved_files():
    """The `$PreservedFiles` list, read out of `install.ps1`."""
    return ps_list("PreservedFiles")


def disposable_files():
    """The `$DisposableFiles` list, read out of `install.ps1`."""
    return ps_list("DisposableFiles")


def test_the_installer_preserves_models_and_settings():
    """
    Q27's other half. `install.ps1` deleted `$TargetDir` recursively before
    copying, so every reinstall destroyed the downloaded weights *and* the
    user's settings -- an existing v2 defect that v3 makes expensive rather than
    merely annoying.
    """
    text = installer_text()
    assert "$PreserveDir" in text
    assert 'Move-Item -Path "$TargetDir\\app\\models"' in text
    assert "config.json" in preserved_files()
    # Set aside *before* the delete, put back *after* the copy.
    assert text.index("$PreservedModels = $true") < text.index("Remove-Item -Path $TargetDir")
    assert text.index("Remove-Item -Path $TargetDir") < text.index("Restoring downloaded models")


def test_the_installer_preserves_every_durable_artifact():
    """
    **Derived, so the next durable file cannot be forgotten (session 3).** Q27
    named two things to preserve because two existed when it was written; the
    Concierge then added a memory note, its kept previous version and the saved
    transcripts, and a reinstall deleted all three while carefully keeping
    6.9 GB of weights.

    The rule, stated once: everything `build_portable.py` calls a per-machine
    runtime artifact is either **preserved** across a reinstall or **listed
    here as deliberately disposable**. A new name in `RUNTIME_ARTIFACTS` that is
    in neither set fails this test, which is the only moment anybody is thinking
    about that file.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_portable", os.path.join(REPO, "build_portable.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

    #: Rotated or per-launch, and worthless after a reinstall: both debug logs
    #: are replaced at the next start (`OBS-4`), and the state file and key
    #: describe one launch of one process -- keeping them would carry a stale
    #: pid and a dead API key into a new installation.
    #:
    #: **Read from the installer, not repeated here (session 5).** It used to be
    #: a literal in this test, which made "disposable" a claim the test made
    #: about the installer rather than a thing the installer did -- and the
    #: installer did not do it: `Copy-Item` takes the whole of `app`, so
    #: installing from a directory the application had been run in carried that
    #: run's key, log and state file into the installation (`V-M-93`).
    disposable = disposable_files()

    kept = preserved_files()
    assert kept | disposable == set(build.RUNTIME_ARTIFACTS), (
        "a per-machine artifact is neither preserved nor declared disposable: "
        f"{set(build.RUNTIME_ARTIFACTS) - kept - disposable}")
    assert not (kept & disposable)


def test_the_installer_discards_every_per_launch_file_it_may_have_copied():
    """
    **`V-CG-139`, and it is `V-M-93`.** `build_portable.py` keeps per-launch
    artifacts out of the *archive*; it cannot keep them out of a *source
    directory*, and `Copy-Item -Recurse` takes the whole of `app`. Extract the
    run the application once, then install: without this the installation
    receives that run's `concierge_key`, its `debug_log.txt` and a
    `concierge_state.json` naming a pid that has already exited.

    Removed **after** the copy and **after** the preserved files are restored,
    so the ordering cannot discard something the user owns.
    """
    text = installer_text()
    assert "$DisposableFiles" in text
    assert disposable_files() == {"debug_log.txt", "debug_log.prev.txt",
                                  "concierge_state.json", "concierge_key"}
    assert text.index("Copy-Item -Path \"$SourceDir\\app\"") < \
        text.index("foreach ($Name in $DisposableFiles)")
    assert text.index("foreach ($Name in $PreservedNames)") < \
        text.index("foreach ($Name in $DisposableFiles)")


def test_every_preserved_file_is_put_back_after_the_copy():
    """One loop over one list, so a file cannot be set aside and left there."""
    text = installer_text()
    assert text.index("foreach ($Name in $PreservedFiles)") < \
        text.index("Remove-Item -Path $TargetDir")
    assert text.index("Remove-Item -Path $TargetDir") < \
        text.index("foreach ($Name in $PreservedNames)")
