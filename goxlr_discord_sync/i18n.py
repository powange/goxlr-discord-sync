"""User interface language (logs stay in English).

Texts are written in English in the code and wrapped in tr(); the French
catalog lives in i18n_fr.py. tests/test_i18n.py checks that every tr() text
has a translation.
"""

import locale
import os
import sys

LANGUAGES = {"auto": "Automatic", "en": "English", "fr": "Français"}

_language = "en"


def detect() -> str:
    """Language of the Windows display (or of the Linux session)."""
    if sys.platform == "win32":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "fr" if lang_id & 0x3FF == 0x0C else "en"
        except (AttributeError, OSError):
            return "en"
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "fr" if value.lower().startswith("fr") else "en"
    code = (locale.getlocale()[0] or "").lower()
    return "fr" if code.startswith("fr") else "en"


def set_language(code: str) -> str:
    global _language
    _language = detect() if code not in ("en", "fr") else code
    return _language


def current() -> str:
    return _language


def tr(text: str, **values) -> str:
    if _language == "fr":
        from .i18n_fr import FR

        text = FR.get(text, text)
    return text.format(**values) if values else text
