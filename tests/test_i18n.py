import string

import pytest

from goxlr_discord_sync import i18n
from goxlr_discord_sync.config import Config
from goxlr_discord_sync.engine import Engine
from goxlr_discord_sync.i18n_fr import FR

from .i18n_catalog import translatable_texts

TEXTS = translatable_texts()


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("text", sorted(TEXTS))
def test_every_text_has_a_french_translation(text):
    assert text in FR, f"missing French translation for {text!r} ({TEXTS[text]})"
    assert placeholders(FR[text]) == placeholders(text), f"placeholders differ for {text!r}"


def test_no_stale_translations():
    assert sorted(set(FR) - set(TEXTS)) == []


def test_tr_switches_language():
    assert i18n.tr("Quit") == "Quit"
    i18n.set_language("fr")
    assert i18n.tr("Quit") == "Quitter"
    assert i18n.tr("GoXLR {serial} not connected", serial="S1") == "GoXLR S1 non connecté"
    i18n.set_language("en")
    assert i18n.tr("GoXLR {serial} not connected", serial="S1") == "GoXLR S1 not connected"


def test_auto_language_follows_the_session(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    assert i18n.set_language("auto") == "fr"
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    assert i18n.set_language("auto") == "en"


def test_engine_texts_in_french():
    i18n.set_language("fr")
    engine = Engine(Config())
    assert engine.summary() == "En attente de Discord"
    engine.status.auth = "not_configured"
    assert engine.summary() == "Configuration requise"
    assert "GoXLR Utility : indisponible" in engine.details()
