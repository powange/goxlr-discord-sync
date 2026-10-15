"""Storage for the Discord client secret and OAuth tokens.

On Windows the data is encrypted with DPAPI (bound to the Windows user
account). Elsewhere it is written with 0600 permissions.
"""

import json
import logging

from . import paths

log = logging.getLogger(__name__)

_DPAPI_MAGIC = b"DPAPI1"
_PLAIN_MAGIC = b"PLAIN1"


if paths.IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01
    _ENTROPY = b"goxlr-discord-sync"

    def _blob(data: bytes) -> "_Blob":
        buf = ctypes.create_string_buffer(data, len(data))
        blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob._keepalive = buf  # the struct only holds a raw pointer
        return blob

    def _call(func, data: bytes) -> bytes:
        data_in = _blob(data)
        entropy = _blob(_ENTROPY)
        out = _Blob()
        ok = func(ctypes.byref(data_in), None, ctypes.byref(entropy), None, None,
                  _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise OSError(ctypes.get_last_error(), "DPAPI call failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    def _protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data)

    def _unprotect(data: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data)


def _encode(payload: dict) -> bytes:
    raw = json.dumps(payload).encode("utf-8")
    if paths.IS_WINDOWS:
        return _DPAPI_MAGIC + _protect(raw)
    return _PLAIN_MAGIC + raw


def _decode(blob: bytes) -> dict:
    if blob.startswith(_DPAPI_MAGIC):
        if not paths.IS_WINDOWS:
            raise ValueError("DPAPI data can only be read on Windows")
        raw = _unprotect(blob[len(_DPAPI_MAGIC):])
    elif blob.startswith(_PLAIN_MAGIC):
        raw = blob[len(_PLAIN_MAGIC):]
    else:
        raise ValueError("Unknown secrets file format")
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


class UnreadableSecrets(OSError):
    """secrets.dat exists but cannot be read: never overwrite it blindly."""


def _load_strict() -> dict:
    try:
        blob = paths.secrets_file().read_bytes()
    except FileNotFoundError:
        return {}
    try:
        return _decode(blob)
    except (OSError, ValueError) as e:
        raise UnreadableSecrets(f"Secrets file is unreadable: {e}") from e


def load() -> dict:
    try:
        return _load_strict()
    except OSError as e:
        log.warning("%s", e)
        return {}


def save(data: dict) -> None:
    paths.atomic_write_bytes(paths.secrets_file(), _encode(data), private=True)


def get_client_secret() -> str:
    return str(load().get("client_secret") or "")


def set_client_secret(secret: str) -> None:
    try:
        data = _load_strict()
    except UnreadableSecrets:
        data = {}  # a new secret replaces an unreadable file on purpose
    if data.get("client_secret") != secret:
        # A new application secret invalidates tokens issued for the old one.
        data.pop("token", None)
    data["client_secret"] = secret
    save(data)


def get_token() -> dict | None:
    token = load().get("token")
    return token if isinstance(token, dict) else None


def set_token(token: dict | None) -> None:
    data = _load_strict()  # raises instead of wiping the client secret
    if token is None:
        data.pop("token", None)
    else:
        data["token"] = token
    save(data)


def clear_all() -> None:
    try:
        paths.secrets_file().unlink()
    except FileNotFoundError:
        pass
