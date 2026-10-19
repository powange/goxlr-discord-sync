//! User interface language (logs stay in English). Texts are written in English
//! in the code and passed through `tr`; French texts are in i18n/fr.json
//! (catalog inherited from the Python version, kept clean by tests/i18n.rs).

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

pub const LANGUAGES: [(&str, &str); 3] =
    [("auto", "Automatic"), ("en", "English"), ("fr", "Français")];

static FRENCH: AtomicBool = AtomicBool::new(false);

fn catalog() -> &'static HashMap<String, String> {
    static CATALOG: OnceLock<HashMap<String, String>> = OnceLock::new();
    CATALOG.get_or_init(|| {
        serde_json::from_str(include_str!("../i18n/fr.json")).expect("valid fr.json")
    })
}

/// Language of the Windows display (or of the session locale elsewhere).
pub fn detect() -> &'static str {
    #[cfg(windows)]
    {
        #[link(name = "kernel32")]
        extern "system" {
            fn GetUserDefaultUILanguage() -> u16;
        }
        let lang = unsafe { GetUserDefaultUILanguage() };
        if lang & 0x3FF == 0x0C {
            "fr"
        } else {
            "en"
        }
    }
    #[cfg(not(windows))]
    {
        for var in ["LC_ALL", "LC_MESSAGES", "LANG"] {
            if let Ok(value) = std::env::var(var) {
                if !value.is_empty() {
                    return if value.to_lowercase().starts_with("fr") {
                        "fr"
                    } else {
                        "en"
                    };
                }
            }
        }
        "en"
    }
}

/// "auto", "en" or "fr"; returns the language now in use.
pub fn set_language(code: &str) -> &'static str {
    let lang = match code {
        "en" => "en",
        "fr" => "fr",
        _ => detect(),
    };
    FRENCH.store(lang == "fr", Ordering::Relaxed);
    lang
}

pub fn current() -> &'static str {
    if FRENCH.load(Ordering::Relaxed) {
        "fr"
    } else {
        "en"
    }
}

/// Translate `text` and fill its `{name}` placeholders.
pub fn tr(text: &str, values: &[(&str, &str)]) -> String {
    let mut out = if FRENCH.load(Ordering::Relaxed) {
        catalog()
            .get(text)
            .cloned()
            .unwrap_or_else(|| text.to_string())
    } else {
        text.to_string()
    };
    for (name, value) in values {
        out = out.replace(&format!("{{{name}}}"), value);
    }
    out
}

/// The whole catalog for the settings window (English text -> current language).
pub fn catalog_for_ui() -> HashMap<String, String> {
    if FRENCH.load(Ordering::Relaxed) {
        catalog().clone()
    } else {
        HashMap::new()
    }
}

#[cfg(test)]
pub(crate) static LANGUAGE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn switches_language_and_fills_placeholders() {
        let _guard = LANGUAGE_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        set_language("en");
        assert_eq!(
            tr("GoXLR {serial} not connected", &[("serial", "S1")]),
            "GoXLR S1 not connected"
        );
        set_language("fr");
        assert_eq!(tr("Quit", &[]), "Quitter");
        assert_eq!(
            tr("GoXLR {serial} not connected", &[("serial", "S1")]),
            "GoXLR S1 non connecté"
        );
        assert_eq!(
            tr("Text without translation", &[]),
            "Text without translation"
        );
        set_language("en");
    }

    #[test]
    fn catalog_placeholders_match() {
        let names = |s: &str| {
            let mut v: Vec<String> = s
                .split('{')
                .skip(1)
                .filter_map(|p| p.split_once('}').map(|(n, _)| n.to_string()))
                .collect();
            v.sort();
            v
        };
        for (en, fr) in catalog() {
            assert_eq!(names(en), names(fr), "placeholders differ for {en:?}");
        }
    }
}
