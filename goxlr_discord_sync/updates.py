"""New release check through the GitHub API."""

import logging
import re

import requests

from . import REPO

log = logging.getLogger(__name__)


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


def fetch_latest_release(timeout: float = 10) -> tuple[str, str] | None:
    """(version, html_url) of the latest published release, or None."""
    try:
        response = requests.get(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        log.info("Update check failed: %s", e)
        return None
    tag = str(data.get("tag_name") or "")
    url = str(data.get("html_url") or "")
    if not tag or not url:
        return None
    return tag.lstrip("v"), url
