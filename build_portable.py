import fnmatch
import hashlib
import os
import re
import sys
import subprocess
import shutil
import zipfile

#: Per-machine files the application writes next to itself at runtime. They live
#: inside app/, so os.walk picks them up and they ship to every target PC.
#: debug_log.txt has been doing so since this script was written.
#:
#: config.json is the dangerous one. install.ps1 copies app/ over an existing
#: installation with -Force, so a shipped config.json would overwrite the user's
#: saved hotkey and device preference on every reinstall.
#:
#: The five Concierge entries are v3.0's (concierge_handoff.md section 6, Q27).
#: concierge_state.json names a pid and a port that mean nothing on another
#: machine and would send the startup reap after a stranger's process;
#: concierge_key is one launch's API key; and the memory note and the saved
#: transcripts are the user's own words about their own machine, which have no
#: business in a distribution at all.
RUNTIME_ARTIFACTS = frozenset({
    "config.json", "debug_log.txt", "debug_log.prev.txt",
    "concierge_state.json", "concierge_key",
    "concierge_memory.txt", "concierge_memory.prev.txt",
    "concierge_sessions.json",
})

#: Directories under a zipped item that never ship, as path tuples relative to
#: the repository root.
#:
#: **This is Q27, and it exists because the file rule above could not do it.**
#: `should_skip`'s runtime-artifact test fires only when `root == "app"` -- the
#: top level -- and `os.walk` packs everything nested below unconditionally.
#: `design.md` section 2 records that same property approvingly, because it is
#: why `app/assets/` needs no allowlist entry. For a 6.87 GB GGUF at
#: `app/models/concierge/` it is CON-CG-4 breached in the most expensive
#: possible way, and nothing anywhere asked anyone to prevent it -- session 5's
#: prompt asked only that someone *verify* the weights were absent.
#:
#: Whisper model directories live under the same parent and are excluded by the
#: same rule, which is correct for the same reason: NFR-6 prefers a bundled
#: model when one is present, and "present on the build machine" is not the same
#: as "belongs in every distribution".
EXCLUDED_DIRS = frozenset({("app", "models")})


#: The bundled llama.cpp runtime (`app/llama/`), as an **allowlist** rather than
#: an exclusion. The unpacked archives hold 55 files and 1.10 GB; 8 of them are
#: `llama-server.exe`'s static dependency closure, and the rest are other
#: people's command-line tools.
#:
#: An allowlist, not a blocklist, because this directory is the one place in the
#: distribution whose contents are decided by somebody else. `build_llama_runtime
#: .py` unpacks whatever the pinned nightly ships; a rule of the form "skip the
#: tools we know about" silently ships the tools of the next build that adds one.
#:
#: **The saving is not the point.** Dropping all 28 unused files removes 5.1 MB
#: of 1104.7 MB -- the mass is three CUDA DLLs the server genuinely needs. What
#: the allowlist buys is that the distribution contains nothing nobody can
#: account for: `llama-bench.exe`, `llama-quantize.exe` and `ggml-rpc-server.exe`
#: are a benchmarking tool, a quantiser and a *network listener*, and shipping a
#: listener to every target PC because it happened to be in an archive is the
#: kind of thing that is only ever noticed by somebody else.
LLAMA_RUNTIME_DIR = ("app", "llama")

#: `llama-server.exe` and the seven DLLs it imports, computed from the PE import
#: tables rather than guessed (`V-M-90`). `mtmd.dll` is on the list because
#: `llama-server-impl.dll` links it, not because anything ships an `mmproj`.
LLAMA_RUNTIME_FILES = frozenset({
    "llama-server.exe", "llama-server-impl.dll", "llama.dll",
    "llama-common.dll", "ggml.dll", "ggml-base.dll", "libomp.dll", "mtmd.dll",
    # Loaded by ggml-base at runtime, so they appear in no import table.
    "ggml-cuda.dll",
    # From the separate cudart archive. Without it the executable does not
    # start on a machine that has no CUDA toolkit -- which is every target PC.
    "cudart64_12.dll",
    #
    # **cuBLAS is deliberately absent, and that is what makes the distribution
    # one download.** `ggml-cuda.dll` statically imports `cublas64_12.dll` and
    # `cublasLt64_12.dll`, and llama.cpp's cuda-12.4 build brings its own copies
    # -- 547.0 MB of them. The application already ships cuBLAS for a different
    # consumer: `requirements.txt` pins `nvidia-cublas-cu12==12.9.2.10` for
    # CTranslate2, which faster-whisper needs, and that copy is 735.5 MB. One
    # distribution carrying 1.28 GB of the same library twice is what put the
    # archive 42 MiB over GitHub's 2 GiB release-asset limit.
    #
    # Dropping llama.cpp's pair saves 382 MiB compressed and brings the archive
    # to 1.668 GiB with 340 MiB to spare. `server.launch_env` puts the pinned
    # `nvidia/cublas/bin` on llama-server's PATH so its statically-imported
    # cuBLAS resolves there instead.
    #
    # **Measured, not assumed** (`V-M-96`). CUDA's minor-version compatibility
    # says a 12.9 cuBLAS should serve a binary built against 12.4, and the
    # experiment that settled it needed a control, because llama.cpp **skips a
    # backend it cannot load and runs on CPU without complaint**: all three
    # arms "started and generated tokens". VRAM and decode rate tell them
    # apart -- 8395 MiB / 25.3 tok/s on its own cuBLAS, 0 MiB / 5.7 tok/s with
    # none, and 8395 MiB / 25.7 tok/s on CTranslate2's.
    #
    # The cost is a coupling that did not exist before: two pins that were
    # independent now have to move together. Both are pinned exactly, and
    # `concierge_design.md` section 6's gate-zero step is where a change to
    # either gets re-measured.
    # Licences. llama.cpp's own is fetched by `build_llama_runtime.py`; the
    # OpenMP one is in the binaries archive. The OFL precedent (`V-M-64`) is
    # that a bundled component's licence file travels with it.
    "LICENSE-llama.cpp", "LICENSE-LLVM-OpenMP",
})

#: The CPU backends, one per instruction-set generation. ggml-base probes each
#: at startup and keeps the best the CPU admits to, so shipping only the one
#: this machine picked would tie the distribution to the build machine's CPU.
#: All fourteen cost 17 MB.
LLAMA_RUNTIME_PATTERNS = ("ggml-cpu-*.dll",)

#: One archive, and the reason it is one.
#:
#: A GitHub release asset must be under 2 GiB ("Each file included in a release
#: must be under 2 GiB", GitHub's own docs). With llama.cpp's own cuBLAS in it
#: the archive was 2.041 GiB, and the session that found that briefly split the
#: distribution in two along `app/llama/`. That was the wrong answer to the
#: right problem: a user extracts a zip, looks for something called install and
#: double-clicks it -- a second download they have to know about is a step most
#: people will not take, and the ones who skip it get an application whose
#: assistant does not work, for reasons they never read.
#:
#: It fits in one piece because it stopped shipping cuBLAS twice; see
#: `LLAMA_RUNTIME_FILES`. 1.67 GiB, with 340 MiB of headroom.
DISTRIBUTION_ARCHIVE = "ptt_dictate_dist.zip"

#: The payload manifest: one SHA-256 and one archive-relative path per line,
#: written into the archive and verified by `install.ps1` before it copies a
#: byte.
#:
#: **This exists because the archive is not what arrives.** v3.0 was reported as
#: a broken installer; the archive was intact, and Windows Explorer's own
#: "Extract All" had written 2,138 of its 8,550 files as the right number of NUL
#: bytes. `install.ps1` was one of them, so the failure the user saw was
#: PowerShell parsing 12,724 zeroes -- "The term '' is not recognized" -- and
#: nothing anywhere connected that to the extractor. A build cannot prevent
#: Explorer from doing this. It can hand the installer the means to notice, and
#: to say which files and what to do instead.
#:
#: Verification costs about three seconds: SHA-256 over 2.95 GiB measured at
#: ~1 GB/s on the reference machine, which is cheaper than the copy that
#: follows it.
MANIFEST_PATH = os.path.join("_internal", "manifest.sha256")
MANIFEST_ARCHIVE_NAME = "_internal/manifest.sha256"

#: GitHub's per-asset limit, checked against the finished archive. Not a
#: splitter -- a refusal. An archive 40 MB too big is not discovered by building
#: it; it is discovered at the end of a 2 GB upload, on release day.
MAX_ASSET_BYTES = 2 * 1024 ** 3

#: What the archive must contain for the Concierge to start at all. Checked
#: before zipping, and a failure stops the build: a distribution whose
#: `app/llama/` is empty produces an application that installs, runs, dictates,
#: and then reports that the Concierge could not start -- a fault that surfaces
#: on the user's machine and nowhere on the build machine, because on the build
#: machine the developer's own runtime is one directory away.
LLAMA_REQUIRED = ("llama-server.exe", "ggml-cuda.dll", "cudart64_12.dll",
                  "LICENSE-llama.cpp")


def file_digest(path, chunk=1024 * 1024):
    """SHA-256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _parts(path):
    return tuple(p for p in path.replace("\\", "/").split("/") if p and p != ".")


def should_skip_dir(root, dirname):
    """
    Whether `os.walk` should prune one directory. Checked before descending.

    Pruning rather than filtering per file: walking `app/models/concierge/` to
    reject one 6.87 GB entry works, and walking it to reject ten thousand is a
    build that looks hung.
    """
    if _parts(os.path.join(root, dirname)) in EXCLUDED_DIRS:
        return "model weights never ship (CON-CG-4)"
    return None


def in_llama_allowlist(filename):
    """Whether one file inside `app/llama/` belongs in the distribution."""
    if filename in LLAMA_RUNTIME_FILES:
        return True
    return any(fnmatch.fnmatch(filename, pattern)
               for pattern in LLAMA_RUNTIME_PATTERNS)


def check_llama_runtime(directory=os.path.join(*LLAMA_RUNTIME_DIR)):
    """
    What is missing from the bundled runtime, as a list of reasons.

    Separate from the walk so the build can refuse *before* spending four
    minutes zipping 1.4 GB, and so a test can ask the question without one.
    """
    if not os.path.isdir(directory):
        return [f"{directory}{os.sep} does not exist"]
    present = set(os.listdir(directory))
    return [f"{directory}{os.sep}{name} is missing" for name in LLAMA_REQUIRED
            if name not in present]


def should_skip(item, root, filename):
    """Whether one walked file is excluded from the distribution archive."""
    # pyvenv.cfg pins the venv to the build machine's interpreter path
    if item == ".venv" and root == ".venv" and filename == "pyvenv.cfg":
        return "environment portability"
    if item == "app" and root == "app" and filename.lower() in RUNTIME_ARTIFACTS:
        return "per-machine runtime artifact"
    # The llama.cpp runtime is an allowlist: anything not named ships nothing.
    # Tested before the EXCLUDED_DIRS loop because it is a different kind of
    # rule, and after the two above because they are about `app/` itself.
    if _parts(root) == LLAMA_RUNTIME_DIR and not in_llama_allowlist(filename):
        return "not part of llama-server's dependency closure"
    # The same rule as should_skip_dir, applied per file. Belt to its braces:
    # pruning is what makes the build fast, and this is what makes the exclusion
    # true even if someone later walks the tree a different way.
    parts = _parts(root)
    for depth in range(1, len(parts) + 1):
        if parts[:depth] in EXCLUDED_DIRS:
            return "model weights never ship (CON-CG-4)"
    if filename.lower().endswith(".old"):
        return "backup file"
    return None


def read_version(path=os.path.join("app", "ptt", "__init__.py")):
    """
    The released version, read out of `app/ptt/__init__.py` without importing it.

    An import would mean putting `app/` on `sys.path` from a script that has no
    other reason to, and would run the package's module-scope code on the build
    machine. A regular expression over one assignment is the smaller thing.
    """
    with open(path, "r", encoding="utf-8") as f:
        match = re.search(r'^__version__\s*=\s*"([^"]+)"', f.read(), re.M)
    if not match:
        raise RuntimeError(f"no __version__ in {path}")
    return match.group(1)


def archive_name(path):
    """One walked path as the archive spells it: relative, forward slashes."""
    return path.replace("\\", "/")


def payload_files(items, on_skip=None):
    """
    Every file the distribution archive contains, in walk order.

    Lifted out of `main`'s zip loop when the manifest arrived, so that the list
    of files hashed and the list of files packed come from one traversal rule
    instead of two that have to be kept saying the same thing. A manifest that
    disagreed with the archive would fail every installation on earth, and it
    would do it on release day.

    `on_skip(path, reason)` is called for each exclusion, so the packing pass
    can print what it left out and the hashing pass can stay quiet.
    """
    for item in items:
        if not os.path.exists(item):
            continue
        if not os.path.isdir(item):
            yield item
            continue
        for root, dirs, files in os.walk(item):
            if "__pycache__" in dirs:
                dirs.remove("__pycache__")
            if ".pytest_cache" in dirs:
                dirs.remove(".pytest_cache")
            for name in list(dirs):
                reason = should_skip_dir(root, name)
                if reason:
                    if on_skip:
                        on_skip(os.path.join(root, name) + os.sep, reason)
                    dirs.remove(name)
            for name in files:
                reason = should_skip(item, root, name)
                if reason:
                    if on_skip:
                        on_skip(os.path.join(root, name), reason)
                    continue
                yield os.path.join(root, name)


def write_manifest(path, version, entries):
    """
    Write the payload manifest: a commented header, then `<sha256>  <path>`.

    Two spaces between the fields, which is `sha256sum`'s format, so the file is
    checkable with any of the usual tools as well as by `install.ps1`. Written
    with LF endings and no BOM: PowerShell's `Get-Content` reads that correctly,
    and it keeps the file's own hash stable across machines.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# PTT Dictation {version} -- payload manifest for "
                f"{DISTRIBUTION_ARCHIVE}\n")
        f.write("#\n")
        f.write("# One line per file: <sha256>  <path>, relative to the folder\n")
        f.write("# this archive extracts to. install.ps1 checks every one of\n")
        f.write("# them before it installs anything, because Windows Explorer's\n")
        f.write("# 'Extract All' has been observed writing whole files as NUL\n")
        f.write("# bytes from an archive that was itself intact.\n")
        f.write(f"# {MANIFEST_ARCHIVE_NAME} is absent from its own list.\n")
        for digest, name in entries:
            f.write(f"{digest}  {name}\n")


def read_manifest(text):
    """Parse a manifest back into `{path: sha256}`. The installer's half, in Python."""
    entries = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        if name:
            entries[name.strip()] = digest.strip()
    return entries


def main():
    print("=== Push-to-Talk Portable Build & Package Script ===")
    
    # 1. Ensure requirements.txt exists
    if not os.path.exists("requirements.txt"):
        print("Error: requirements.txt not found!")
        return

    # 2. Create virtual environment if it doesn't exist
    if not os.path.isdir(".venv"):
        print("Creating virtual environment (.venv)...")
        subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
    else:
        print("Virtual environment (.venv) already exists.")

    # 3. Upgrade pip and install requirements
    print("Installing dependencies in .venv (this might take a few minutes)...")
    python_exe = os.path.join(".venv", "Scripts", "python.exe")
    pip_exe = os.path.join(".venv", "Scripts", "pip.exe")
    # Use `python -m pip` for the upgrade: pip.exe cannot overwrite its own
    # running executable on Windows ("To modify pip, please run ... -m pip").
    subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([pip_exe, "install", "-r", "requirements.txt"], check=True)

    # 4. Copy core Python executable and DLLs to .venv/Scripts/
    print("Configuring standalone signed interpreter in .venv...")
    python_dir = sys.base_prefix
    target_scripts = os.path.join(".venv", "Scripts")

    # Determine Python DLL name based on current python version (e.g. python314.dll)
    major, minor = sys.version_info.major, sys.version_info.minor
    py_dll = f"python{major}{minor}.dll"

    files_to_copy = [
        ("pythonw.exe", "ptt_dictate.exe"),
        ("python.exe", "python.exe"),
        ("python3.dll", "python3.dll"),
        (py_dll, py_dll),
        ("vcruntime140.dll", "vcruntime140.dll"),
        ("vcruntime140_1.dll", "vcruntime140_1.dll")
    ]

    #: `V-M-91`. These six files are locked whenever anything is running out of
    #: `.venv\Scripts` -- the application (`V-M-71`'s case) or the build itself,
    #: since the only interpreter that can run the knowledge-pack step is the
    #: one inside `.venv`. The old branch warned and carried on, which is safe
    #: exactly while the existing copy is already current and silently wrong the
    #: first time it is not: a Python upgrade would ship the old interpreter
    #: beside the new standard library, and nothing would say so.
    #:
    #: So a lock is no longer a warning. It is a **verification**: if the file
    #: that could not be overwritten is byte-for-byte what would have been
    #: written, the build is correct and says why; if it differs, the build
    #: stops. "Close the application before building a release" stops being
    #: advice nobody can check and becomes a thing the script knows.
    stale = []
    for src_name, dest_name in files_to_copy:
        src_path = os.path.join(python_dir, src_name)
        dest_path = os.path.join(target_scripts, dest_name)
        if not os.path.exists(src_path):
            print(f"  Warning: {src_name} not found in base Python directory.")
            continue
        print(f"  Copying {src_name} -> {dest_name}...")
        try:
            shutil.copy2(src_path, dest_path)
        except PermissionError:
            if not os.path.exists(dest_path):
                stale.append(f"{dest_name} is locked and there is no copy to fall back on")
            elif file_digest(src_path) == file_digest(dest_path):
                print(f"    locked by a running process, and identical to "
                      f"{src_name}: the existing copy is current")
            else:
                stale.append(f"{dest_name} is locked and DIFFERS from "
                             f"{os.path.join(python_dir, src_name)}")
    if stale:
        for reason in stale:
            print(f"  {reason}")
        print("Error: the archive would ship an interpreter that is not the one "
              "in this environment. Close everything running out of .venv and "
              "build again.")
        return

    # Copy base python DLLs and Lib directories to make the virtual environment fully portable
    print("Copying DLLs folder from base Python...")
    src_dlls = os.path.join(python_dir, "DLLs")
    dest_dlls = os.path.join(".venv", "DLLs")
    if os.path.isdir(src_dlls):
        shutil.copytree(src_dlls, dest_dlls, dirs_exist_ok=True)

    print("Copying standard library (Lib) from base Python...")
    src_lib = os.path.join(python_dir, "Lib")
    dest_lib = os.path.join(".venv", "Lib")
    if os.path.isdir(src_lib):
        for name in os.listdir(src_lib):
            if name.lower() == "site-packages":
                continue
            src_item = os.path.join(src_lib, name)
            dest_item = os.path.join(dest_lib, name)
            if os.path.isdir(src_item):
                shutil.copytree(src_item, dest_item, dirs_exist_ok=True)
            else:
                shutil.copy2(src_item, dest_item)

    # 5. The bundled llama.cpp runtime (CON-CG-2). A check, not a step: the
    # download is build_llama_runtime.py's, deliberately outside app/, so the
    # only thing to do here is refuse to ship without it.
    print("Checking the bundled llama.cpp runtime...")
    missing = check_llama_runtime()
    if missing:
        for reason in missing:
            print(f"  {reason}")
        print("Error: the Concierge runtime is not bundled. Run "
              "`python build_llama_runtime.py` first.")
        return
    print(f"  {os.path.join(*LLAMA_RUNTIME_DIR)}: complete")

    # 6. Generate the Concierge knowledge pack (concierge_design.md 5.05).
    #
    # A build step, not a checked-in artifact: half of it is generated from
    # config.py's FIELDS table, so a pack in git would go stale the first time a
    # setting changed. It ERRORS rather than skipping when a source is missing --
    # the spike's version silently listed a docs/validation.md that has never
    # existed, and a step that quietly ships a smaller pack produces a Concierge
    # that answers from nothing and looks like a bad model.
    print("Generating the Concierge knowledge pack...")
    try:
        import build_knowledge_pack
        output, manifest, size = build_knowledge_pack.build()
        print(f"  {output}: {size} characters (~{(size + 3) // 4} tokens)")
    except Exception as e:
        print(f"Error: could not build the knowledge pack: {e}")
        return

    # 7. Package everything into the two archives.
    zip_name = DISTRIBUTION_ARCHIVE
    if os.path.exists(zip_name):
        print(f"Removing old {zip_name}...")
        os.remove(zip_name)

    items_to_zip = [
        ".venv",
        "app",
        "_internal",
        "install.bat",
        "README.md"
    ]

    # 6a. The payload manifest, written before the archive so that it is in it.
    #
    # Its own path is skipped rather than assumed absent: a rebuild runs with
    # last build's manifest still on disk, and a file that hashed itself would
    # be wrong in a way nothing downstream could detect.
    version = read_version()
    print(f"Hashing the payload for {MANIFEST_PATH} (PTT Dictation {version})...")
    entries = []
    for filepath in payload_files(items_to_zip):
        if os.path.normpath(filepath) == os.path.normpath(MANIFEST_PATH):
            continue
        entries.append((file_digest(filepath), archive_name(filepath)))
    write_manifest(MANIFEST_PATH, version, entries)
    print(f"  {len(entries)} files hashed, "
          f"{os.path.getsize(MANIFEST_PATH) / 1024:.0f} KB")

    print("Zipping directories and files...")
    count = 0
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        def announce(path, reason):
            print(f"  Skipping {path} ({reason})...")

        for filepath in payload_files(items_to_zip, on_skip=announce):
            zipf.write(filepath, filepath)
            count += 1

    # 8. Audit the finished archive. The two checks above are about the source
    # tree; this one is about the artefact that leaves the building, which is
    # the only thing a user ever sees. Four of the properties it asserts have
    # already gone wrong once each in this project's history -- a licence file
    # that was meant to travel with its component (`V-M-64`), a weights
    # directory that `os.walk` packed because nothing told it not to (Q27), an
    # asset too large for the release meant to carry it, and a root folder
    # offering the user two files both displayed as `install`.
    print("Auditing the archive...")
    with zipfile.ZipFile(zip_name) as zipf:
        names = set(n.replace("\\", "/") for n in zipf.namelist())
    prefix = "/".join(LLAMA_RUNTIME_DIR) + "/"

    problems = [f"{prefix}{name} is not in the archive"
                for name in LLAMA_REQUIRED if prefix + name not in names]
    problems += [f"{name} is in the archive and must never be"
                 for name in sorted(names)
                 if name.lower().endswith(".gguf")
                 or name.rsplit("/", 1)[-1] in ("concierge_state.json",
                                                "concierge_key")]
    if not any(n.endswith("app/assets/concierge_kb.md") for n in names):
        problems.append("app/assets/concierge_kb.md is not in the archive")
    for required in ("install.bat", "_internal/install.ps1",
                     "_internal/run_tray.bat", MANIFEST_ARCHIVE_NAME):
        if required not in names:
            problems.append(f"{required} is not in the archive")

    # The manifest is checked against the archive, not against the source tree
    # it was computed from. Those are the same list twice only while nothing
    # changes between the two passes, and "only while" is the part that goes
    # wrong. A manifest that names a file the archive lacks fails every
    # installation, so it is worth the second read.
    if MANIFEST_ARCHIVE_NAME in names:
        with zipfile.ZipFile(zip_name) as zipf:
            listed = read_manifest(
                zipf.read(MANIFEST_ARCHIVE_NAME).decode("utf-8"))
        expected = names - {MANIFEST_ARCHIVE_NAME}
        for missing in sorted(expected - set(listed))[:5]:
            problems.append(f"{missing} is in the archive but not the manifest")
        for extra in sorted(set(listed) - expected)[:5]:
            problems.append(f"{extra} is in the manifest but not the archive")
        if MANIFEST_ARCHIVE_NAME in listed:
            problems.append(f"{MANIFEST_ARCHIVE_NAME} lists itself")

    # Exactly one thing in the root may look clickable. Windows hides known
    # extensions by default, so `install.bat` and `install.ps1` side by side
    # both render as `install`, and the user picks by icon.
    offered = sorted(n for n in names if "/" not in n
                     and n.lower().endswith((".exe", ".bat", ".cmd", ".com")))
    if offered != ["install.bat"]:
        problems.append(f"the archive root offers {offered}; it must offer "
                        f"exactly ['install.bat']")

    size = os.path.getsize(zip_name)
    if size >= MAX_ASSET_BYTES:
        problems.append(
            f"the archive is {size / 1024 ** 3:.3f} GiB, over GitHub's 2 GiB "
            f"per-asset limit by {(size - MAX_ASSET_BYTES) / 1048576:.1f} MiB")

    if problems:
        for problem in problems:
            print(f"  {problem}")
        print(f"Error: {zip_name} failed its own audit and is not fit to ship.")
        return
    print(f"  {len(names)} entries, {size / 1024 ** 3:.3f} GiB "
          f"({100 * size / MAX_ASSET_BYTES:.0f} % of GitHub's asset limit); "
          f"runtime, licences and knowledge pack present; no weights, no "
          f"per-launch state; one executable in the root; the manifest "
          f"describes every other entry")

    print(f"\nSuccess! Created ready-to-distribute package: {zip_name}")
    print(f"Total size: {size / (1024*1024):.2f} MB")
    print(f"Total files zipped: {count}")


if __name__ == "__main__":
    main()
