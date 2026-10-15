"""Pure decision logic: GoXLR mute states <-> Discord voice settings.

GoXLR Utility reports three mute states for the Cough button and faders:
``Unmuted``, ``MutedToX`` (muted to the target picked by ``mute_type``) and
``MutedToAll`` (hold-to-mute, muted everywhere).
"""

from collections.abc import Iterable


def effective_target(state: str | None, mute_type: str | None) -> str | None:
    """Where the audio is actually muted, or None when it is not muted."""
    if state == "MutedToAll":
        return "All"
    if state == "MutedToX":
        return mute_type or "All"
    return None


def desired_flag(state: str | None, mute_type: str | None, triggers: Iterable[str]) -> bool | None:
    """True/False for Discord, or None when the GoXLR state is unknown (do nothing)."""
    if state not in ("Unmuted", "MutedToX", "MutedToAll"):
        return None
    target = effective_target(state, mute_type)
    return target is not None and target in set(triggers)


def plan_discord_update(
    want_mute: bool | None,
    want_deaf: bool | None,
    known_mute: bool | None,
    known_deaf: bool | None,
) -> dict:
    """Arguments for SET_VOICE_SETTINGS that move Discord toward the wanted state.

    Discord forces self-mute while deafened and toggling mute while deafened
    also undeafens, so deafen is always settled first and mute is left alone
    whenever Discord is (or is about to be) deafened. Returns {} when nothing
    has to change.
    """
    if want_deaf is not None and want_deaf != known_deaf:
        return {"deaf": want_deaf}
    if known_deaf:
        return {}
    if want_mute is not None and want_mute != known_mute:
        return {"mute": want_mute}
    return {}


def goxlr_state_for(muted: bool, mute_type: str | None, triggers: Iterable[str]) -> str:
    """GoXLR mute state that reproduces a Discord mute/deafen flag (two-way sync)."""
    if not muted:
        return "Unmuted"
    if (mute_type or "All") in set(triggers):
        return "MutedToX"
    return "MutedToAll"
