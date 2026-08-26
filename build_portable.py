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


def should_skip(item, root, filename):
    """Whether one walked file is excluded from the distribution archive."""
    # pyvenv.cfg pins the venv to the build machine's interpreter path
    if item == ".venv" and root == ".venv" and filename == "pyvenv.cfg":
        return "environment portability"
    if item == "app" and root == "app" and filename.lower() in RUNTIME_ARTIFACTS:
        return "per-machine runtime artifact"
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

    for src_name, dest_name in files_to_copy:
        src_path = os.path.join(python_dir, src_name)
        dest_path = os.path.join(target_scripts, dest_name)
        if os.path.exists(src_path):
            print(f"  Copying {src_name} -> {dest_name}...")
            try:
                shutil.copy2(src_path, dest_path)
            except PermissionError:
                print(f"  Warning: {dest_name} is currently in use/locked. Skipping copy since it already exists.")
        else:
            print(f"  Warning: {src_name} not found in base Python directory.")

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

    # 5. Generate the Concierge knowledge pack (concierge_design.md 5.05).
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

    # 6. Package everything into ptt_dictate_dist.zip
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

    print(f"\nSuccess! Created ready-to-distribute package: {zip_name}")
    print(f"Total size: {os.path.getsize(zip_name) / (1024*1024):.2f} MB")
    print(f"Total files zipped: {count}")

if __name__ == "__main__":
    main()
