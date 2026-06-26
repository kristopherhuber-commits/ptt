import os
import sys
import subprocess
import shutil
import zipfile

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
    pip_exe = os.path.join(".venv", "Scripts", "pip.exe")
    subprocess.run([pip_exe, "install", "--upgrade", "pip"], check=True)
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

    # 5. Package everything into ptt_dictate_dist.zip
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
                    for file in files:
                        if item == ".venv" and root == ".venv" and file == "pyvenv.cfg":
                            print("  Skipping pyvenv.cfg to ensure environment portability...")
                            continue
                        if file.lower().endswith(".old"):
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
