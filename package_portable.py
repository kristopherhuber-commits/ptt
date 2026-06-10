import os
import zipfile

def main():
    print("=== Starting Push-to-Talk Portable Packager ===")
    
    # Target zip name
    zip_name = "ptt_dictate_dist.zip"
    
    if os.path.exists(zip_name):
        print(f"Removing old {zip_name}...")
        try:
            os.remove(zip_name)
        except Exception as e:
            print(f"Error removing old zip: {str(e)}")
            return
        
    print("Zipping directories and files...")
    
    # Files/folders to package
    items_to_zip = [
        ".venv",
        "app",
        "run_tray.bat",
        "install.bat",
        "install.ps1",
        "README.md"
    ]
    
    # Package files using ZIP_DEFLATED compression
    count = 0
    with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for item in items_to_zip:
            if not os.path.exists(item):
                print(f"Warning: {item} does not exist, skipping.")
                continue
                
            if os.path.isdir(item):
                print(f"Adding directory: {item} ...")
                for root, dirs, files in os.walk(item):
                    # Skip cache folders to reduce size
                    if "__pycache__" in dirs:
                        dirs.remove("__pycache__")
                    if ".pytest_cache" in dirs:
                        dirs.remove(".pytest_cache")
                    
                    for file in files:
                        filepath = os.path.join(root, file)
                        # Preserve relative paths in the zip
                        zipf.write(filepath, filepath)
                        count += 1
                        if count % 1000 == 0:
                            print(f"  Added {count} files...")
            else:
                print(f"Adding file: {item} ...")
                zipf.write(item, item)
                count += 1
                
    print(f"\nSuccess! Created ready-to-distribute package: {zip_name}")
    print(f"Total size: {os.path.getsize(zip_name) / (1024*1024):.2f} MB")
    print(f"Total files zipped: {count}")

if __name__ == "__main__":
    main()
