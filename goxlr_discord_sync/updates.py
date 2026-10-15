"""New release check and installer download through the GitHub API.

Updates are always confirmed by the user, then installed by the release's
GoXLR_Setup.exe started with --update: the setup replaces the executable once
the application has quit, and starts it again.
"""

import hashlib
import logging
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from . import REPO

log = logging.getLogger(__name__)

SETUP_ASSET = "GoXLR_Setup.exe"
SUMS_ASSET = "SHA256SUMS.txt"
MAX_DOWNLOAD = 200 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    page_url: str
    notes: str = ""
    setup_url: str = ""
    sums_url: str = ""

    @property
    def installable(self) -> bool:
        return bool(self.setup_url and self.sums_url)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "Release | None":
        if not isinstance(data, dict) or not data.get("version") or not data.get("page_url"):
            return None
        return cls(**{k: str(data.get(k) or "") for k in ("version", "page_url", "notes", "setup_url", "sums_url")})


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+(?:\.\d+)*)", text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def parse_release(data: dict) -> Release | None:
    tag = str(data.get("tag_name") or "")
    page = str(data.get("html_url") or "")
    if not tag or not page or data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name"): a.get("browser_download_url", "") for a in data.get("assets") or [] if isinstance(a, dict)}
    return Release(
        version=tag.lstrip("v"),
        page_url=page,
        notes=str(data.get("body") or ""),
        setup_url=str(assets.get(SETUP_ASSET) or ""),
        sums_url=str(assets.get(SUMS_ASSET) or ""),
    )


def fetch_latest_release(timeout: float = 10) -> Release | None:
    try:
        response = requests.get(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        return parse_release(response.json())
    except (requests.RequestException, ValueError) as e:
        log.info("Update check failed: %s", e)
        return None


def notes_excerpt(notes: str, limit: int = 600) -> str:
    """Release notes as plain text for a dialog box."""
    text = re.sub(r"[#*`>]", "", notes)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


def expected_sha256(sums_text: str, filename: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == filename and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download_setup(release: Release, dest_dir: Path | None = None, timeout: float = 30) -> Path:
    """Download the release installer and check it against SHA256SUMS.txt."""
    if not release.installable:
        raise UpdateError("This release has no installer to download")
    try:
        sums = requests.get(release.sums_url, timeout=timeout)
        sums.raise_for_status()
        expected = expected_sha256(sums.text, SETUP_ASSET)
        if expected is None:
            raise UpdateError(f"{SUMS_ASSET} does not list {SETUP_ASSET}")

        dest_dir = dest_dir or Path(tempfile.gettempdir()) / "goxlr-discord-sync-update"
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"GoXLR_Setup-{release.version}.exe"
        partial = target.with_suffix(".part")
        digest = hashlib.sha256()
        size = 0
        with requests.get(release.setup_url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with open(partial, "wb") as f:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise UpdateError("The installer is unexpectedly large")
                    digest.update(chunk)
                    f.write(chunk)
    except requests.RequestException as e:
        raise UpdateError(f"Download failed: {e}") from e

    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer is corrupted (checksum mismatch)")
    partial.replace(target)
    log.info("Downloaded %s (%d KiB, checksum OK)", target, size // 1024)
    return target
