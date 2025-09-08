# folder_scanner.py
import os
from datetime import datetime
from .metadata_extractor import buildMetadata  # Import metadata extractor

def _scan_for_images(full_base_path, base_path, include_subfolders):
    """Scans directories for images, videos, and GIFs and their metadata."""
    folders_data = {}
    current_files = set()
    changed = False

    def scan_directory(dir_path, relative_path=""):
        """Recursively scans a directory for image, video, and GIF files."""
        nonlocal changed
        folder_content = {}  # Dictionary to hold files for the current folder
        try:
            file_entries = []

            for entry in os.scandir(dir_path):
                if entry.is_dir():
                    if include_subfolders and not entry.name.startswith("."):
                        next_relative_path = os.path.join(relative_path, entry.name)
                        scan_directory(entry.path, next_relative_path)
                elif entry.is_file():
                    file_entries.append(entry)
                    current_files.add(entry.path)

            for entry in file_entries:
                name = entry.name
                if name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.mp4', '.gif', '.webm')): # ADDED: .mp4 and .gif extensions
                    try:
                        timestamp = entry.stat().st_mtime
                        date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                        rel_path = os.path.relpath(dir_path, full_base_path)
                        filename = name
                        subfolder = rel_path if rel_path != "." else ""
                        if len(subfolder) > 0:
                          url_path = f"/static_gallery/{subfolder}/{filename}"
                        else:
                          url_path = f"/static_gallery/{filename}"
                        url_path = url_path.replace("\\", "/")

                        metadata = {} # Videos and GIFs will have empty metadata for now
                        if name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')): # Only build metadata for images
                            # Extract metadata here
                            try:
                                _, _, metadata = buildMetadata(entry.path)
                            except Exception as e:
                                print(f"Gallery Node: Error building metadata for {entry.path}: {e}")
                                metadata = {}

                        folder_content[filename] = { # Store file info in folder_content dict
                            "name": name,
                            "url": url_path,
                            "timestamp": timestamp,
                            "date": date_str,
                            "metadata": metadata,
                            "type": "image" if name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')) else "media" # Added type to distinguish images and media
                        }
                    except Exception as e:
                        print(f"Gallery Node: Error processing file {entry.path}: {e}")

            folder_key = os.path.join(base_path, relative_path) if relative_path else base_path
            if folder_content: # Only add folder if it has content
                folders_data[folder_key] = folder_content

        except Exception as e:
            print(f"Gallery Node: Error scanning directory {dir_path}: {e}")

    scan_directory(full_base_path, "")
    return folders_data, changed
