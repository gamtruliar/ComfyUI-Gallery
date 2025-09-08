# folder_monitor.py
import os
import time
import threading
from datetime import datetime
from typing import Any, Dict
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver
from watchdog.events import PatternMatchingEventHandler
from .folder_scanner import _scan_for_images  # Import folder scanner
from .metadata_extractor import buildMetadata
from server import PromptServer
from .gallery_config import gallery_log

# Module-level cache of file metadata
FileInfo = Dict[str, Any]
file_index: Dict[str, FileInfo] = {}


def _build_file_info(base_path: str, real_path: str) -> FileInfo:
    """Build metadata for a single file."""
    timestamp = os.path.getmtime(real_path)
    date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    rel_dir = os.path.relpath(os.path.dirname(real_path), base_path)
    filename = os.path.basename(real_path)
    subfolder = rel_dir if rel_dir != "." else ""
    if subfolder:
        url_path = f"/static_gallery/{subfolder}/{filename}"
    else:
        url_path = f"/static_gallery/{filename}"
    url_path = url_path.replace("\\", "/")

    metadata = {}
    if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        try:
            _, _, metadata = buildMetadata(real_path)
        except Exception as e:
            gallery_log(f"Gallery Node: Error building metadata for {real_path}: {e}")
            metadata = {}

    return {
        "name": filename,
        "url": url_path,
        "timestamp": timestamp,
        "date": date_str,
        "metadata": metadata,
        "type": "image" if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else "media",
    }


class GalleryEventHandler(PatternMatchingEventHandler):
    """Handles file system events, including symlinks, recursively."""

    def __init__(self, base_path, patterns=None, ignore_patterns=None, ignore_directories=False, case_sensitive=True, debounce_interval=0.5):
        super().__init__(patterns=patterns, ignore_patterns=ignore_patterns, ignore_directories=ignore_directories, case_sensitive=case_sensitive)
        self.base_path = os.path.realpath(base_path)  # Use realpath for base_path
        self.root_name = os.path.basename(self.base_path)
        self.debounce_timer = None
        self.debounce_interval = debounce_interval
        # Use a dictionary to track events, keyed by (event_type, real_path)
        self.processed_events = {}
        self.pending_changes = {"folders": {}}

    def on_any_event(self, event):
        """Handle file system events and update the file index."""
        if event.is_directory:
            return

        if event.src_path.endswith(('.swp', '.tmp', '~')):
            return

        real_path = os.path.realpath(event.src_path)

        event_key = (event.event_type, real_path)
        current_time = time.time()
        if event_key in self.processed_events:
            last_processed_time = self.processed_events[event_key]
            if current_time - last_processed_time < self.debounce_interval:
                return
        self.processed_events[event_key] = current_time

        rel_path = os.path.relpath(real_path, self.base_path).replace("\\", "/")
        folder_part = os.path.dirname(rel_path)
        folder_key = self.root_name if folder_part in ("", ".") else os.path.join(self.root_name, folder_part).replace("\\", "/")
        filename = os.path.basename(rel_path)

        if event.event_type == 'deleted':
            file_index.pop(rel_path, None)
            self.pending_changes["folders"].setdefault(folder_key, {})[filename] = {"action": "remove"}
        elif event.event_type == 'moved':
            dest_real = os.path.realpath(event.dest_path)
            dest_rel = os.path.relpath(dest_real, self.base_path).replace("\\", "/")
            dest_folder_part = os.path.dirname(dest_rel)
            dest_folder_key = self.root_name if dest_folder_part in ("", ".") else os.path.join(self.root_name, dest_folder_part).replace("\\", "/")
            dest_filename = os.path.basename(dest_rel)

            file_index.pop(rel_path, None)
            self.pending_changes["folders"].setdefault(folder_key, {})[filename] = {"action": "remove"}

            try:
                file_info = _build_file_info(self.base_path, dest_real)
                file_index[dest_rel] = file_info
                self.pending_changes["folders"].setdefault(dest_folder_key, {})[dest_filename] = {"action": "create", **file_info}
            except Exception as e:
                gallery_log(f"GalleryEventHandler: Error processing moved file {dest_real}: {e}")
        else:
            action = "create" if event.event_type == "created" else "update"
            try:
                file_info = _build_file_info(self.base_path, real_path)
                file_index[rel_path] = file_info
                self.pending_changes["folders"].setdefault(folder_key, {})[filename] = {"action": action, **file_info}
            except Exception as e:
                gallery_log(f"GalleryEventHandler: Error processing file {real_path}: {e}")

        if event.event_type in ('created', 'deleted', 'modified', 'moved'):
            gallery_log(f"Watchdog detected {event.event_type}: {event.src_path} (Real path: {real_path}) - debouncing")
            self.debounce_event()


    def debounce_event(self):
        """Debounces the file system event."""
        if self.debounce_timer and self.debounce_timer.is_alive():
            self.debounce_timer.cancel()

        self.debounce_timer = threading.Timer(self.debounce_interval, self.rescan_and_send_changes)
        self.debounce_timer.start()

    def rescan_and_send_changes(self):
        """Send pending changes to clients without rescanning."""
        if not self.pending_changes["folders"]:
            self.debounce_timer = None
            return

        try:
            from .server import sanitize_json_data
            PromptServer.instance.send_sync("Gallery.file_change", sanitize_json_data(self.pending_changes))
        except Exception as e:
            gallery_log(f"FileSystemMonitor: Error sending changes: {e}")
        finally:
            self.pending_changes = {"folders": {}}
            self.debounce_timer = None



class FileSystemMonitor:
    """Monitors the output directory, including symlinks, recursively."""

    def __init__(self, base_path, interval=1.0, use_polling_observer=False):
        self.base_path = base_path
        self.interval = interval
        self.use_polling_observer = use_polling_observer
        if use_polling_observer:
            self.observer = Observer()
        else:
            self.observer = PollingObserver()
        self.event_handler = GalleryEventHandler(base_path=base_path, patterns=["*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4", "*.gif", "*.webm"], debounce_interval=0.5)
        folder_name = os.path.basename(base_path)
        folders_data, _ = _scan_for_images(base_path, folder_name, True)
        for folder, files in folders_data.items():
            rel_dir = os.path.relpath(folder, folder_name)
            for filename, info in files.items():
                rel_path = os.path.join(rel_dir, filename) if rel_dir != '.' else filename
                file_index[rel_path.replace("\\", "/")] = info
        self.thread = None

    def start_monitoring(self):
        """Starts the Watchdog observer."""
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._start_observer_thread, daemon=True)
            self.thread.start()
            gallery_log("FileSystemMonitor: Watchdog monitoring thread started.")
        else:
            gallery_log("FileSystemMonitor: Watchdog monitoring thread already running.")

    def _start_observer_thread(self):
        self.observer.schedule(self.event_handler, self.base_path, recursive=True)
        self.observer.follow_directory_symlinks = True  # Ensure symlinks are followed
        self.observer.start()
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop_monitoring()

    def stop_monitoring(self):
        """Stops the Watchdog observer."""
        if self.thread and self.thread.is_alive():
            self.observer.stop()
            if self.observer.is_alive():
                self.observer.join()
            self.thread = None
            gallery_log("FileSystemMonitor: Watchdog monitoring thread stopped.")
        else:
            gallery_log("FileSystemMonitor: Watchdog monitoring thread was not running.")
