//! Pure decision rules, ported from goxlr_discord_sync/sync_logic.py.

/// Where the audio is actually muted, or None when it is not muted.
pub fn effective_target(state: &str, mute_type: Option<&str>) -> Option<String> {
    match state {
        "MutedToAll" => Some("All".into()),
        "MutedToX" => Some(mute_type.unwrap_or("All").to_string()),
        _ => None,
    }
}

/// true/false for Discord, or None when the GoXLR state is unknown (do nothing).
pub fn desired_flag(state: Option<&str>, mute_type: Option<&str>, triggers: &[String]) -> Option<bool> {
    let state = state?;
    if !matches!(state, "Unmuted" | "MutedToX" | "MutedToAll") {
        return None;
    }
    Some(effective_target(state, mute_type).is_some_and(|t| triggers.contains(&t)))
}

/// One SET_VOICE_SETTINGS change toward the wanted state: deafen first, never mute while deafened.
pub fn plan(want_mute: Option<bool>, want_deaf: Option<bool>, known_mute: Option<bool>,
            known_deaf: Option<bool>) -> Option<(&'static str, bool)> {
    if let Some(deaf) = want_deaf {
        if Some(deaf) != known_deaf {
            return Some(("deaf", deaf));
        }
    }
    if known_deaf == Some(true) {
        return None;
    }
    match want_mute {
        Some(mute) if Some(mute) != known_mute => Some(("mute", mute)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mute() -> Vec<String> {
        vec!["All".into(), "ToVoiceChat".into()]
    }

    #[test]
    fn desired_respects_mute_function() {
        assert_eq!(desired_flag(Some("Unmuted"), Some("All"), &mute()), Some(false));
        assert_eq!(desired_flag(Some("MutedToAll"), Some("ToStream"), &mute()), Some(true));
        assert_eq!(desired_flag(Some("MutedToX"), Some("ToStream"), &mute()), Some(false));
        assert_eq!(desired_flag(Some("MutedToX"), Some("ToVoiceChat"), &mute()), Some(true));
        assert_eq!(desired_flag(None, Some("All"), &mute()), None);
        assert_eq!(desired_flag(Some("Something"), None, &mute()), None);
    }

    #[test]
    fn plan_matches_the_python_rules() {
        assert_eq!(plan(Some(true), Some(false), Some(true), Some(false)), None);
        assert_eq!(plan(Some(true), Some(true), Some(false), Some(false)), Some(("deaf", true)));
        assert_eq!(plan(Some(false), Some(true), Some(true), Some(true)), None); // never unmute while deafened
        assert_eq!(plan(Some(true), Some(false), Some(true), Some(true)), Some(("deaf", false)));
        assert_eq!(plan(Some(true), Some(false), Some(false), Some(false)), Some(("mute", true)));
        assert_eq!(plan(Some(false), None, None, None), Some(("mute", false)));
    }
}
