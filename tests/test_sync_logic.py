import pytest

from goxlr_discord_sync.sync_logic import desired_flag, effective_target, goxlr_state_for, plan_discord_update

MUTE = ["All", "ToVoiceChat"]


@pytest.mark.parametrize(
    ("state", "mute_type", "expected"),
    [
        ("Unmuted", "All", None),
        ("MutedToAll", "ToStream", "All"),
        ("MutedToX", "ToStream", "ToStream"),
        ("MutedToX", None, "All"),
        (None, "All", None),
    ],
)
def test_effective_target(state, mute_type, expected):
    assert effective_target(state, mute_type) == expected


def test_desired_flag_respects_mute_function():
    assert desired_flag("Unmuted", "All", MUTE) is False
    assert desired_flag("MutedToAll", "ToStream", MUTE) is True  # hold-to-mute mutes everything
    assert desired_flag("MutedToX", "ToStream", MUTE) is False  # only hidden from the stream
    assert desired_flag("MutedToX", "ToVoiceChat", MUTE) is True
    assert desired_flag("MutedToX", "All", MUTE) is True


def test_unknown_state_means_no_action():
    assert desired_flag(None, "All", MUTE) is None
    assert desired_flag("SomethingNew", "All", MUTE) is None


def test_plan_noop_when_in_sync():
    assert plan_discord_update(True, False, True, False) == {}
    assert plan_discord_update(None, None, True, True) == {}


def test_plan_deafen_first_and_alone():
    assert plan_discord_update(True, True, False, False) == {"deaf": True}


def test_plan_never_touches_mute_while_deafened():
    # Discord reports mute=True while deafened; unmuting would undeafen.
    assert plan_discord_update(False, True, True, True) == {}
    assert plan_discord_update(False, None, True, True) == {}


def test_plan_undeafen_then_mute():
    assert plan_discord_update(True, False, True, True) == {"deaf": False}
    assert plan_discord_update(True, False, False, False) == {"mute": True}


def test_plan_unknown_discord_state_is_pushed():
    assert plan_discord_update(False, False, None, None) == {"deaf": False}
    assert plan_discord_update(False, None, None, None) == {"mute": False}


def test_goxlr_state_for():
    assert goxlr_state_for(False, "ToStream", MUTE) == "Unmuted"
    assert goxlr_state_for(True, "ToVoiceChat", MUTE) == "MutedToX"
    assert goxlr_state_for(True, "ToStream", MUTE) == "MutedToAll"
    assert goxlr_state_for(True, None, MUTE) == "MutedToX"
    assert goxlr_state_for(True, "ToStream", ["ToVoiceChat"]) is None
