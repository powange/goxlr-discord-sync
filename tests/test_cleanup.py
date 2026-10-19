import os
import time
from pathlib import Path

from goxlr_discord_sync import cleanup

OLD = time.time() - 2 * 3600


def make(temp: Path, name: str, *entries: str, age: float = OLD) -> Path:
    folder = temp / name
    folder.mkdir()
    for entry in entries:
        if entry.endswith("/"):
            (folder / entry).mkdir()
        else:
            (folder / entry).write_bytes(b"x")
    os.utime(folder, (age, age))
    return folder


def test_removes_only_our_old_unused_folders(tmp_path):
    ours_marked = make(tmp_path, "_MEI1", cleanup.MARKER, "python313.dll")
    ours_legacy = make(tmp_path, "_MEI2", "websockets/", "PIL/", "python313.dll")
    setup_dir = make(tmp_path, "_MEI3", "GoXLR_Discord_Sync.exe")
    other_app = make(tmp_path, "_MEI4", "python313.dll", "numpy/")
    fresh = make(tmp_path, "_MEI5", cleanup.MARKER, age=time.time())
    current = make(tmp_path, "_MEI6", cleanup.MARKER)
    not_mei = make(tmp_path, "cache", cleanup.MARKER)

    removed = cleanup.cleanup_extraction_dirs(tmp_path, current=str(current))

    assert sorted(f.name for f in removed) == ["_MEI1", "_MEI2", "_MEI3"]
    assert not ours_marked.exists() and not ours_legacy.exists() and not setup_dir.exists()
    assert other_app.exists()  # another program's folder
    assert fresh.exists()      # may be unpacking right now
    assert current.exists()    # this instance
    assert not_mei.exists()


def test_folder_whose_runtime_is_loaded_is_kept(tmp_path, monkeypatch):
    busy = make(tmp_path, "_MEI7", cleanup.MARKER, "python313.dll", "base_library.zip")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "python313.dll":
            raise PermissionError(5, "Access is denied")  # what Windows answers for a loaded DLL
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert cleanup.cleanup_extraction_dirs(tmp_path, current="") == []
    assert (busy / "base_library.zip").exists()  # nothing else was touched


def test_partial_leftover_without_runtime_is_removed(tmp_path):
    partial = make(tmp_path, "_MEI8", "websockets/", "PIL/")
    assert cleanup.cleanup_extraction_dirs(tmp_path, current="") == [partial]
