//! Every text shown to the user has a French translation with the same placeholders.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

fn catalog() -> HashMap<String, String> {
    serde_json::from_str(include_str!("../i18n/fr.json")).unwrap()
}

/// Literal strings passed to tr("…") (Rust), t("…") (JS) and data-i18n="…" (HTML).
fn texts() -> BTreeSet<(String, String)> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut files = vec![];
    for dir in ["crates/core/src", "src-tauri/src", "ui"] {
        for entry in std::fs::read_dir(root.join(dir)).unwrap() {
            files.push(entry.unwrap().path());
        }
    }
    let mut found = BTreeSet::new();
    for path in files {
        let source = std::fs::read_to_string(&path).unwrap_or_default();
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        for marker in ["tr(\"", " t(\"", "(t(\"", "data-i18n=\""] {
            for (i, _) in source.match_indices(marker) {
                // tr( must be a call of its own, not the end of push_str( or similar.
                let before = source[..i].chars().last().unwrap_or(' ');
                if marker == "tr(\"" && (before.is_alphanumeric() || before == '_') {
                    continue;
                }
                let rest = &source[i + marker.len()..];
                let mut text = String::new();
                let mut chars = rest.chars();
                while let Some(c) = chars.next() {
                    match c {
                        '\\' => text.push(chars.next().unwrap_or_default()),
                        '"' => break,
                        _ => text.push(c),
                    }
                }
                // The i18n unit test checks an untranslated text on purpose.
                if !text.is_empty() && text != "Text without translation" {
                    found.insert((text, name.clone()));
                }
            }
        }
    }
    // Labels shown through tables (config.rs) and the tray autostart entry.
    for table_text in [
        "Muted to everything",
        "Muted to Stream",
        "Muted to Voice Chat",
        "Muted to Headphones",
        "Muted to Line Out",
        "Toggle Discord mute",
        "Toggle Discord deafen",
        "Leave the voice channel",
        "Automatic",
        "First GoXLR found",
        "Not assigned",
        "Start with Windows",
        "Start on login",
    ] {
        found.insert((table_text.to_string(), "tables".into()));
    }
    found
}

fn placeholders(s: &str) -> Vec<String> {
    let mut v: Vec<String> = s
        .split('{')
        .skip(1)
        .filter_map(|p| p.split_once('}').map(|(n, _)| n.to_string()))
        .collect();
    v.sort();
    v
}

#[test]
fn every_text_is_translated() {
    let fr = catalog();
    let missing: Vec<String> = texts()
        .into_iter()
        .filter(|(text, _)| {
            !fr.contains_key(text)
                && !["English", "Français", "Discord", "GoXLR"].contains(&text.as_str())
        })
        .map(|(text, file)| format!("{file}: {text:?}"))
        .collect();
    assert!(
        missing.is_empty(),
        "missing French translations:\n{}",
        missing.join("\n")
    );
}

#[test]
fn placeholders_match() {
    for (en, fr) in catalog() {
        assert_eq!(placeholders(&en), placeholders(&fr), "{en:?}");
    }
}

#[test]
fn every_translation_is_used() {
    // A text can also reach tr() or t() through a table, so look for the literal anywhere.
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut sources = String::new();
    for dir in ["crates/core/src", "src-tauri/src", "ui"] {
        for entry in std::fs::read_dir(root.join(dir)).unwrap() {
            sources += &std::fs::read_to_string(entry.unwrap().path()).unwrap_or_default();
        }
    }
    let mut unused: Vec<String> = catalog()
        .into_keys()
        .filter(|text| !sources.contains(&serde_json::to_string(text).unwrap()))
        .collect();
    unused.sort();
    assert!(
        unused.is_empty(),
        "unused French translations:\n{}",
        unused.join("\n")
    );
}
