import fnmatch
import hashlib
import os
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
    # The separate cudart archive. Without these the executable does not start
    # on a machine that has no CUDA toolkit -- which is every target PC.
    "cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll",
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

    # 7. Package everything into ptt_dictate_dist.zip
    zip_name = "ptt_dictate_dist.zip"
    if os.path.exists(zip_name):
        print(f"Removing old {zip_name}...")
        os.remove(zip_name)

    print("Zipping directories and files...")
    items_to_zip = [
        ".venv",
        "app",
        "run_tray.bat",
        "install.bat",
        "install.ps1",
        "README.md"
    ]

    count = 0
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for item in items_to_zip:
            if not os.path.exists(item):
                continue
            if os.path.isdir(item):
                print(f"Adding directory: {item} ...")
                for root, dirs, files in os.walk(item):
                    if "__pycache__" in dirs:
                        dirs.remove("__pycache__")
                    if ".pytest_cache" in dirs:
                        dirs.remove(".pytest_cache")
                    for name in list(dirs):
                        reason = should_skip_dir(root, name)
                        if reason:
                            print(f"  Skipping {os.path.join(root, name)}{os.sep} ({reason})...")
                            dirs.remove(name)
                    for file in files:
                        reason = should_skip(item, root, file)
                        if reason:
                            print(f"  Skipping {os.path.join(root, file)} ({reason})...")
                            continue
                        filepath = os.path.join(root, file)
                        zipf.write(filepath, filepath)
                        count += 1
            else:
                print(f"Adding file: {item} ...")
                zipf.write(item, item)
                count += 1

    # 8. Audit the finished archive. The two checks above are about the source
    # tree; this one is about the artefact that leaves the building, which is
    # the only thing a user ever sees. Both properties it asserts have already
    # gone wrong once each in this project's history -- a licence file that was
    # meant to travel with its component (`V-M-64`), and a weights directory
    # that `os.walk` packed because nothing told it not to (Q27).
    print("Auditing the archive...")
    with zipfile.ZipFile(zip_name) as zipf:
        names = set(n.replace("\\", "/") for n in zipf.namelist())
    prefix = "/".join(LLAMA_RUNTIME_DIR) + "/"
    problems = [f"{prefix}{name} is not in the archive" for name in LLAMA_REQUIRED
                if prefix + name not in names]
    problems += [f"{name} is in the archive and must never be"
                 for name in sorted(names)
                 if name.lower().endswith(".gguf")
                 or name.rsplit("/", 1)[-1] in ("concierge_state.json",
                                                "concierge_key")]
    if not any(n.endswith("app/assets/concierge_kb.md") for n in names):
        problems.append("app/assets/concierge_kb.md is not in the archive")
    if problems:
        for problem in problems:
            print(f"  {problem}")
        print(f"Error: {zip_name} failed its own audit and is not fit to ship.")
        return
    print(f"  {len(names)} entries; runtime, licences and knowledge pack "
          f"present; no weights, no per-launch state")

    print(f"\nSuccess! Created ready-to-distribute package: {zip_name}")
    print(f"Total size: {os.path.getsize(zip_name) / (1024*1024):.2f} MB")
    print(f"Total files zipped: {count}")

if __name__ == "__main__":
    main()
