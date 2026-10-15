"""Import settings written by version 1.x (plain text files next to the executable)."""

import json
import logging
from pathlib import Path

from . import autostart, config, paths, secure_store

log = logging.getLogger(__name__)

LEGACY_FILES = ("client_id.txt", "client_secret.txt", "discord_token.json")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def migrate_legacy_files(directory: Path) -> bool:
    """Move 1.x credentials into config.json/secrets.dat and delete the plain files."""
    client_id_file = directory / "client_id.txt"
    secret_file = directory / "client_secret.txt"
    token_file = directory / "discord_token.json"
    if not any(f.exists() for f in (client_id_file, secret_file, token_file)):
        return False

    cfg = config.load()
    client_id = _read_text(client_id_file)
    if client_id and not cfg.client_id:
        cfg.client_id = client_id
        config.save(cfg)
    secret = _read_text(secret_file)
    if secret and not secure_store.get_client_secret():
        secure_store.set_client_secret(secret)
    if token_file.exists() and secure_store.get_token() is None:
        try:
            token = json.loads(token_file.read_text(encoding="utf-8"))
            if isinstance(token, dict) and token.get("refresh_token"):
                secure_store.set_token(token)  # no expires_at: refreshed on first use
        except (OSError, ValueError):
            pass

    for f in (client_id_file, secret_file, token_file):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("Cannot delete legacy file %s: %s", f, e)
    log.info("Migrated version 1.x settings from %s", directory)
    return True


def migrate_legacy_autostart() -> None:
    """Replace the 1.x Startup-folder VBS launcher by the Run registry entry."""
    if not paths.IS_WINDOWS:
        return
    vbs = autostart.legacy_vbs_path()
    if vbs is None or not vbs.exists() or autostart.registered_command():
        return
    try:
        autostart.enable()
    except OSError as e:
        log.warning("Cannot migrate autostart: %s", e)


def run() -> None:
    migrate_legacy_files(paths.app_dir())
    migrate_legacy_autostart()
