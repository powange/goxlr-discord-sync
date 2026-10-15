import hashlib

import pytest

from goxlr_discord_sync import updates

PAYLOAD = b"MZ fake installer" * 1000
GOOD_SUMS = f"{hashlib.sha256(PAYLOAD).hexdigest()}  GoXLR_Setup.exe\n{'0' * 64}  GoXLR_Discord_Sync.exe\n"

API_RELEASE = {
    "tag_name": "v2.1.0",
    "html_url": "https://github.com/powange/goxlr-discord-sync/releases/tag/v2.1.0",
    "body": "## What's new\n* **Faster** sync, see [PR](https://example.com)\n",
    "assets": [
        {"name": "GoXLR_Setup.exe", "browser_download_url": "https://dl/setup"},
        {"name": "SHA256SUMS.txt", "browser_download_url": "https://dl/sums"},
    ],
}


class FakeResponse:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status
        self.text = content.decode(errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def json(self):
        return API_RELEASE

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_get(files):
    def _get(url, **kwargs):
        return FakeResponse(files[url]) if url in files else FakeResponse(b"", 404)
    return _get


def test_parse_release_with_assets():
    release = updates.parse_release(API_RELEASE)
    assert release.version == "2.1.0"
    assert release.installable
    assert updates.Release.from_dict(release.to_dict()) == release


def test_release_without_installer_or_prerelease():
    assert not updates.parse_release({**API_RELEASE, "assets": []}).installable
    assert updates.parse_release({**API_RELEASE, "prerelease": True}) is None
    assert updates.Release.from_dict({"version": "1"}) is None


def test_fetch_latest_release(monkeypatch):
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: FakeResponse(b"{}"))
    assert updates.fetch_latest_release().version == "2.1.0"


def test_expected_sha256():
    assert updates.expected_sha256(GOOD_SUMS, "GoXLR_Setup.exe") == hashlib.sha256(PAYLOAD).hexdigest()
    assert updates.expected_sha256(GOOD_SUMS, "missing.exe") is None
    assert updates.expected_sha256("nothex  GoXLR_Setup.exe", "GoXLR_Setup.exe") is None


def test_download_verifies_checksum(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.requests, "get", fake_get({"https://dl/setup": PAYLOAD,
                                                           "https://dl/sums": GOOD_SUMS.encode()}))
    path = updates.download_setup(updates.parse_release(API_RELEASE), tmp_path)
    assert path.name == "GoXLR_Setup-2.1.0.exe"
    assert path.read_bytes() == PAYLOAD


def test_download_rejects_corrupted_file(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.requests, "get", fake_get({"https://dl/setup": PAYLOAD + b"tampered",
                                                           "https://dl/sums": GOOD_SUMS.encode()}))
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.download_setup(updates.parse_release(API_RELEASE), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_download_errors_are_update_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(updates.requests, "get", fake_get({}))
    with pytest.raises(updates.UpdateError, match="Download failed"):
        updates.download_setup(updates.parse_release(API_RELEASE), tmp_path)
    with pytest.raises(updates.UpdateError, match="no installer"):
        updates.download_setup(updates.Release("2.1.0", "https://page"), tmp_path)


def test_notes_excerpt_is_plain_text():
    text = updates.notes_excerpt(API_RELEASE["body"])
    assert text == "What's new\n Faster sync, see PR"
    assert updates.notes_excerpt("line\n" * 400, limit=50).endswith("…")


def test_interrupted_download_leaves_no_partial_file(monkeypatch, tmp_path):
    import requests

    class Broken(FakeResponse):
        def iter_content(self, chunk_size):
            yield PAYLOAD[:10]
            raise requests.ConnectionError("reset")

    def get(url, **kwargs):
        return Broken(PAYLOAD) if url == "https://dl/setup" else FakeResponse(GOOD_SUMS.encode())

    monkeypatch.setattr(updates.requests, "get", get)
    with pytest.raises(updates.UpdateError, match="Download failed"):
        updates.download_setup(updates.parse_release(API_RELEASE), tmp_path)
    assert list(tmp_path.iterdir()) == []
