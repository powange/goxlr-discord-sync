//! Pure decision rules: GoXLR mute states <-> Discord voice settings.
//!
//! GoXLR Utility reports three mute states for the Cough button and faders:
//! `Unmuted`, `MutedToX` (muted to the target picked by `mute_type`) and
//! `MutedToAll` (hold-to-mute, muted everywhere).

/// A change of one Discord voice setting.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VoiceChange {
    Mute(bool),
    Deaf(bool),
}

impl VoiceChange {
    pub fn field(self) -> &'static str {
        match self {
            VoiceChange::Mute(_) => "mute",
            VoiceChange::Deaf(_) => "deaf",
        }
    }

    pub fn value(self) -> bool {
        match self {
            VoiceChange::Mute(v) | VoiceChange::Deaf(v) => v,
        }
    }
}

/// Where the audio is actually muted, or None when it is not muted.
pub fn effective_target(state: Option<&str>, mute_type: Option<&str>) -> Option<String> {
    match state {
        Some("MutedToAll") => Some("All".into()),
        Some("MutedToX") => Some(mute_type.unwrap_or("All").to_string()),
        _ => None,
    }
}

/// true/false for Discord, or None when the GoXLR state is unknown (do nothing).
pub fn desired_flag(state: Option<&str>, mute_type: Option<&str>, triggers: &[String]) -> Option<bool> {
    if !matches!(state, Some("Unmuted" | "MutedToX" | "MutedToAll")) {
        return None;
    }
    Some(effective_target(state, mute_type).is_some_and(|t| triggers.contains(&t)))
}

/// The SET_VOICE_SETTINGS change that moves Discord toward the wanted state.
///
/// Discord forces self-mute while deafened and toggling mute while deafened
/// also undeafens, so deafen is always settled first and mute is left alone
/// whenever Discord is (or is about to be) deafened.
pub fn plan_discord_update(
    want_mute: Option<bool>,
    want_deaf: Option<bool>,
    known_mute: Option<bool>,
    known_deaf: Option<bool>,
) -> Option<VoiceChange> {
    if let Some(deaf) = want_deaf {
        if Some(deaf) != known_deaf {
            return Some(VoiceChange::Deaf(deaf));
        }
    }
    if known_deaf == Some(true) {
        return None;
    }
    match want_mute {
        Some(mute) if Some(mute) != known_mute => Some(VoiceChange::Mute(mute)),
        _ => None,
    }
}

/// GoXLR mute state that reproduces a Discord mute/deafen flag (two-way sync).
///
/// None when no GoXLR state maps to "muted" with these triggers: pushing
/// anything would be undone by the next GoXLR -> Discord reconciliation.
pub fn goxlr_state_for(muted: bool, mute_type: Option<&str>, triggers: &[String]) -> Option<&'static str> {
    if !muted {
        return Some("Unmuted");
    }
    if triggers.iter().any(|t| t == mute_type.unwrap_or("All")) {
        return Some("MutedToX");
    }
    if triggers.iter().any(|t| t == "All") {
        return Some("MutedToAll");
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use VoiceChange::{Deaf, Mute};

    fn mute() -> Vec<String> {
        vec!["All".into(), "ToVoiceChat".into()]
    }

    #[test]
    fn effective_target_cases() {
        assert_eq!(effective_target(Some("Unmuted"), Some("All")), None);
        assert_eq!(effective_target(Some("MutedToAll"), Some("ToStream")).as_deref(), Some("All"));
        assert_eq!(effective_target(Some("MutedToX"), Some("ToStream")).as_deref(), Some("ToStream"));
        assert_eq!(effective_target(Some("MutedToX"), None).as_deref(), Some("All"));
        assert_eq!(effective_target(None, Some("All")), None);
    }

    #[test]
    fn desired_flag_respects_mute_function() {
        assert_eq!(desired_flag(Some("Unmuted"), Some("All"), &mute()), Some(false));
        assert_eq!(desired_flag(Some("MutedToAll"), Some("ToStream"), &mute()), Some(true));
        assert_eq!(desired_flag(Some("MutedToX"), Some("ToStream"), &mute()), Some(false));
        assert_eq!(desired_flag(Some("MutedToX"), Some("ToVoiceChat"), &mute()), Some(true));
        assert_eq!(desired_flag(Some("MutedToX"), Some("All"), &mute()), Some(true));
    }

    #[test]
    fn unknown_state_means_no_action() {
        assert_eq!(desired_flag(None, Some("All"), &mute()), None);
        assert_eq!(desired_flag(Some("SomethingNew"), Some("All"), &mute()), None);
    }

    #[test]
    fn plan_noop_when_in_sync() {
        assert_eq!(plan_discord_update(Some(true), Some(false), Some(true), Some(false)), None);
        assert_eq!(plan_discord_update(None, None, Some(true), Some(true)), None);
    }

    #[test]
    fn plan_deafen_first_and_alone() {
        assert_eq!(plan_discord_update(Some(true), Some(true), Some(false), Some(false)), Some(Deaf(true)));
    }

    #[test]
    fn plan_never_touches_mute_while_deafened() {
        assert_eq!(plan_discord_update(Some(false), Some(true), Some(true), Some(true)), None);
        assert_eq!(plan_discord_update(Some(false), None, Some(true), Some(true)), None);
    }

    #[test]
    fn plan_undeafen_then_mute() {
        assert_eq!(plan_discord_update(Some(true), Some(false), Some(true), Some(true)), Some(Deaf(false)));
        assert_eq!(plan_discord_update(Some(true), Some(false), Some(false), Some(false)), Some(Mute(true)));
    }

    #[test]
    fn plan_unknown_discord_state_is_pushed() {
        assert_eq!(plan_discord_update(Some(false), Some(false), None, None), Some(Deaf(false)));
        assert_eq!(plan_discord_update(Some(false), None, None, None), Some(Mute(false)));
    }

    #[test]
    fn goxlr_state_for_cases() {
        assert_eq!(goxlr_state_for(false, Some("ToStream"), &mute()), Some("Unmuted"));
        assert_eq!(goxlr_state_for(true, Some("ToVoiceChat"), &mute()), Some("MutedToX"));
        assert_eq!(goxlr_state_for(true, Some("ToStream"), &mute()), Some("MutedToAll"));
        assert_eq!(goxlr_state_for(true, None, &mute()), Some("MutedToX"));
        assert_eq!(goxlr_state_for(true, Some("ToStream"), &["ToVoiceChat".into()]), None);
    }
}
