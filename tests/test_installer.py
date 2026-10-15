import os
import threading
from pathlib import Path

import pytest

from goxlr_discord_sync import config, installer


@pytest.fixture
def files(tmp_path):
    bundled = tmp_path / "bundle" / "GoXLR_Discord_Sync.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"new")
    target = tmp_path / "install" / "GoXLR_Discord_Sync.exe"
    target.parent.mkdir()
    target.write_bytes(b"old")
    return bundled, target


def fake_app(version_written):
    """launch() replacement: the started exe reports (or not) that it runs."""
    launched = []

    def launch(command):
        launched.append(Path(command[0]).read_bytes())
        if version_written:
            installer.record_started(version_written)

    return launched, launch


def quick_wait(version):
    return installer.wait_for_start(version, timeout=0.3, poll=0.01)


def test_successful_update_keeps_no_backup(files):
    bundled, target = files
    launched, launch = fake_app("9.9.9")
    installer.perform_update(bundled, target, "9.9.9", launch=launch, stop=lambda: None, started=quick_wait)
    assert target.read_bytes() == b"new"
    assert launched == [b"new"]
    assert sorted(p.name for p in target.parent.iterdir()) == ["GoXLR_Discord_Sync.exe"]


def test_new_version_that_does_not_start_is_rolled_back(files):
    bundled, target = files
    launched, launch = fake_app(None)  # crashes before reporting
    stops = []
    with pytest.raises(installer.UpdateRolledBack):
        installer.perform_update(bundled, target, "9.9.9", launch=launch, stop=lambda: stops.append(1),
                                 started=quick_wait)
    assert target.read_bytes() == b"old"
    assert launched == [b"new", b"old"]  # the previous version runs again
    assert stops  # a hung new version is stopped before restoring
    assert sorted(p.name for p in target.parent.iterdir()) == ["GoXLR_Discord_Sync.exe"]


def test_stale_start_report_is_ignored(files):
    bundled, target = files
    installer.record_started("9.9.9")  # left by an earlier run of the same version
    _, launch = fake_app(None)
    with pytest.raises(installer.UpdateRolledBack):
        installer.perform_update(bundled, target, "9.9.9", launch=launch, stop=lambda: None, started=quick_wait)


def test_update_waits_for_the_old_app_to_exit(files, monkeypatch):
    bundled, target = files
    monkeypatch.setattr(installer.time, "sleep", lambda s: None)
    real_replace = type(target).replace
    busy = {"n": 3}

    def replace(self, dst):
        if self.name.endswith(".new") and busy["n"]:
            busy["n"] -= 1
            raise PermissionError(13, "in use")
        return real_replace(self, dst)

    monkeypatch.setattr(type(target), "replace", replace)
    _, launch = fake_app("9.9.9")
    installer.perform_update(bundled, target, "9.9.9", launch=launch, stop=lambda: None, started=quick_wait)
    assert target.read_bytes() == b"new"


def test_record_started():
    installer.record_started("1.2.3")
    assert config.load_state()["started_version"] == "1.2.3"
    assert installer.wait_for_start("1.2.3", timeout=0.1)
    assert not installer.wait_for_start("2.0.0", timeout=0.1, poll=0.02)


def test_secrets_lock_is_exclusive(monkeypatch):
    from goxlr_discord_sync import secure_store

    entered = threading.Event()
    release = threading.Event()

    def holder():
        with secure_store._locked():
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    entered.wait(5)
    with pytest.raises(TimeoutError), secure_store._locked(timeout=0.2):
        pass
    release.set()
    thread.join()
    secure_store.set_client_secret("ok")  # usable again once released
    assert secure_store.get_client_secret() == "ok"
    assert os.path.exists(secure_store.paths.data_dir() / "secrets.lock")
