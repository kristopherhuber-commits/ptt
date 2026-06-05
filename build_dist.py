"""
build_dist.py — Build script to package ptt_tray into a Windows executable distribution.
"""

import os
import sys
import subprocess
import shutil

def main():
    print("=== Starting Push-to-Talk Dictation App Build Process ===")
    
    # 1. Resolve virtual environment paths
    venv_dir = sys.prefix
    site_packages = os.path.join(venv_dir, "Lib", "site-packages")
    pyinstaller_exe = os.path.join(venv_dir, "Scripts", "pyinstaller.exe")
    
    if not os.path.isfile(pyinstaller_exe):
        print(f"Error: PyInstaller not found at {pyinstaller_exe}.")
        print("Please run: .venv\\Scripts\\pip.exe install pyinstaller")
        sys.exit(1)
        
    print(f"Venv location: {venv_dir}")
    print(f"Site-packages: {site_packages}")
    
    # 2. Start building PyInstaller command
    cmd = [
        pyinstaller_exe,
        "--onedir",
        "--noconsole",
        "--uac-admin",
        "--name=ptt_dictate",
        "--workpath=build",
        "--distpath=dist",
        "--clean",
        "--noconfirm",
        "--collect-binaries=ctranslate2",
        "--collect-binaries=sounddevice",
        "--collect-data=faster_whisper",
    ]
    
    # 3. Dynamically resolve NVIDIA CUDA DLL paths
    nvidia_dir = os.path.join(site_packages, "nvidia")
    if os.path.isdir(nvidia_dir):
        print("Locating nvidia-cu12 libraries...")
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            bin_dir = os.path.join(nvidia_dir, sub, "bin")
            if os.path.isdir(bin_dir):
                # PyInstaller binary mapping syntax: source_path;dest_path
                # We map the DLLs inside nvidia/cublas/bin/* to nvidia/cublas/bin
                # inside the output distribution folder.
                binary_map = f"{bin_dir}{os.sep}*;nvidia/{sub}/bin"
                cmd.extend(["--add-binary", binary_map])
                print(f"  Added CUDA binary mapping: nvidia/{sub}/bin")
            else:
                print(f"  Warning: bin directory not found for nvidia/{sub}")
    else:
        print("Warning: nvidia packages not found in site-packages. Build might fail to run on GPU.")
        
    # 4. Target script
    target_script = os.path.join("app", "ptt_tray.py")
    if not os.path.isfile(target_script):
        print(f"Error: Target script {target_script} not found.")
        sys.exit(1)
        
    cmd.append(target_script)
    
    print("\nRunning PyInstaller command:")
    print(" ".join(cmd))
    print("\nCompiling application (this may take a few minutes)...")
    
    try:
        # Run PyInstaller
        result = subprocess.run(cmd, check=True)
        if result.returncode == 0:
            print("\nPyInstaller compilation completed successfully!")
        else:
            print(f"\nPyInstaller failed with return code {result.returncode}")
            sys.exit(1)
    except Exception as e:
        print(f"\nError running PyInstaller: {str(e)}")
        sys.exit(1)
        
    # 5. Create the ZIP archive
    dist_dir = os.path.join("dist", "ptt_dictate")
    if not os.path.isdir(dist_dir):
        print(f"Error: Distribution directory {dist_dir} does not exist.")
        sys.exit(1)
        
    zip_name = "ptt_dictate_dist"
    print(f"\nPackaging distribution directory into {zip_name}.zip ...")
    
    # We remove any old zip file first
    if os.path.isfile(f"{zip_name}.zip"):
        os.remove(f"{zip_name}.zip")
        
    try:
        # Packages dist/ptt_dictate into a folder named ptt_dictate inside ptt_dictate_dist.zip
        shutil.make_archive(zip_name, "zip", "dist", "ptt_dictate")
        print(f"\nSuccess! Created ready-to-distribute package: {zip_name}.zip")
        print(f"Total size: {os.path.getsize(zip_name + '.zip') / (1024*1024):.2f} MB")
    except Exception as e:
        print(f"Error zipping package: {str(e)}")
        sys.exit(1)
        
    print("\n=== Build Complete ===")

if __name__ == "__main__":
    main()
