//! Parse and resolve the HUD global summon shortcut.

use global_hotkey::hotkey::{Code, HotKey, Modifiers};
use serde_json::Value;
use std::env;
use std::path::PathBuf;

pub fn default_hotkey() -> HotKey {
    if cfg!(target_os = "macos") {
        HotKey::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyG)
    } else {
        HotKey::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyG)
    }
}

pub fn default_shortcut_label() -> &'static str {
    if cfg!(target_os = "macos") {
        "Cmd+Shift+G"
    } else {
        "Ctrl+Shift+G"
    }
}

pub fn resolve_summon_shortcut() -> (HotKey, String) {
    match load_override_string() {
        Some(raw) => match parse_shortcut(&raw) {
            Ok((sc, label)) => (sc, label),
            Err(err) => {
                eprintln!(
                    "groket-hud: invalid global_shortcut {raw:?} ({err}); using default {}",
                    default_shortcut_label()
                );
                (default_hotkey(), default_shortcut_label().to_string())
            }
        },
        None => (default_hotkey(), default_shortcut_label().to_string()),
    }
}

fn load_override_string() -> Option<String> {
    if let Ok(s) = env::var("GROKET_HUD_SHORTCUT") {
        let t = s.trim();
        if !t.is_empty() {
            return Some(t.to_string());
        }
    }
    let home = env::var_os("HOME")?;
    let path = PathBuf::from(home).join(".groket").join("config.json");
    let text = std::fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    if let Some(s) = v
        .get("hud")
        .and_then(|h| h.get("global_shortcut"))
        .and_then(|x| x.as_str())
    {
        let t = s.trim();
        if !t.is_empty() {
            return Some(t.to_string());
        }
    }
    if let Some(s) = v.get("hud_global_shortcut").and_then(|x| x.as_str()) {
        let t = s.trim();
        if !t.is_empty() {
            return Some(t.to_string());
        }
    }
    None
}

pub fn parse_shortcut(raw: &str) -> Result<(HotKey, String), String> {
    let parts: Vec<&str> = raw
        .split('+')
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .collect();
    if parts.is_empty() {
        return Err("empty shortcut".into());
    }
    let mut mods = Modifiers::empty();
    let mut key_token: Option<&str> = None;
    for part in &parts {
        let low = part.to_ascii_lowercase();
        match low.as_str() {
            "cmd" | "command" | "super" | "meta" | "win" | "windows" | "logo" => {
                mods |= Modifiers::SUPER;
            }
            "ctrl" | "control" => {
                mods |= Modifiers::CONTROL;
            }
            "alt" | "option" | "opt" => {
                mods |= Modifiers::ALT;
            }
            "shift" => {
                mods |= Modifiers::SHIFT;
            }
            _ => {
                if key_token.is_some() {
                    return Err(format!("multiple keys in shortcut: {raw:?}"));
                }
                key_token = Some(part);
            }
        }
    }
    let key = key_token.ok_or_else(|| format!("no key in shortcut: {raw:?}"))?;
    let code = parse_key_code(key)?;
    if mods.is_empty() {
        return Err("shortcut needs at least one modifier (e.g. Cmd+Shift+G)".into());
    }
    let label = format_label(mods, key);
    Ok((HotKey::new(Some(mods), code), label))
}

fn parse_key_code(token: &str) -> Result<Code, String> {
    let low = token.to_ascii_lowercase();
    if low.len() == 1 {
        let c = low.chars().next().unwrap();
        if c.is_ascii_alphabetic() {
            return letter_code(c);
        }
        if c.is_ascii_digit() {
            return digit_code(c);
        }
    }
    match low.as_str() {
        "space" => Ok(Code::Space),
        "tab" => Ok(Code::Tab),
        "enter" | "return" => Ok(Code::Enter),
        "escape" | "esc" => Ok(Code::Escape),
        "backspace" => Ok(Code::Backspace),
        "delete" | "del" => Ok(Code::Delete),
        "f1" => Ok(Code::F1),
        "f2" => Ok(Code::F2),
        "f3" => Ok(Code::F3),
        "f4" => Ok(Code::F4),
        "f5" => Ok(Code::F5),
        "f6" => Ok(Code::F6),
        "f7" => Ok(Code::F7),
        "f8" => Ok(Code::F8),
        "f9" => Ok(Code::F9),
        "f10" => Ok(Code::F10),
        "f11" => Ok(Code::F11),
        "f12" => Ok(Code::F12),
        _ => Err(format!("unsupported key {token:?}")),
    }
}

fn letter_code(c: char) -> Result<Code, String> {
    Ok(match c {
        'a' => Code::KeyA,
        'b' => Code::KeyB,
        'c' => Code::KeyC,
        'd' => Code::KeyD,
        'e' => Code::KeyE,
        'f' => Code::KeyF,
        'g' => Code::KeyG,
        'h' => Code::KeyH,
        'i' => Code::KeyI,
        'j' => Code::KeyJ,
        'k' => Code::KeyK,
        'l' => Code::KeyL,
        'm' => Code::KeyM,
        'n' => Code::KeyN,
        'o' => Code::KeyO,
        'p' => Code::KeyP,
        'q' => Code::KeyQ,
        'r' => Code::KeyR,
        's' => Code::KeyS,
        't' => Code::KeyT,
        'u' => Code::KeyU,
        'v' => Code::KeyV,
        'w' => Code::KeyW,
        'x' => Code::KeyX,
        'y' => Code::KeyY,
        'z' => Code::KeyZ,
        _ => return Err(format!("unsupported letter {c:?}")),
    })
}

fn digit_code(c: char) -> Result<Code, String> {
    Ok(match c {
        '0' => Code::Digit0,
        '1' => Code::Digit1,
        '2' => Code::Digit2,
        '3' => Code::Digit3,
        '4' => Code::Digit4,
        '5' => Code::Digit5,
        '6' => Code::Digit6,
        '7' => Code::Digit7,
        '8' => Code::Digit8,
        '9' => Code::Digit9,
        _ => return Err(format!("unsupported digit {c:?}")),
    })
}

fn format_label(mods: Modifiers, key: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if mods.contains(Modifiers::CONTROL) {
        parts.push("Ctrl".into());
    }
    if mods.contains(Modifiers::ALT) {
        parts.push("Alt".into());
    }
    if mods.contains(Modifiers::SUPER) {
        if cfg!(target_os = "macos") {
            parts.push("Cmd".into());
        } else {
            parts.push("Super".into());
        }
    }
    if mods.contains(Modifiers::SHIFT) {
        parts.push("Shift".into());
    }
    let key_label = match key.to_ascii_lowercase().as_str() {
        "space" => "Space".to_string(),
        "escape" | "esc" => "Esc".to_string(),
        "enter" | "return" => "Enter".to_string(),
        "tab" => "Tab".to_string(),
        other => other.to_ascii_uppercase(),
    };
    parts.push(key_label);
    parts.join("+")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_cmd_shift_g() {
        let (_sc, label) = parse_shortcut("Cmd+Shift+G").unwrap();
        assert!(label.contains('G'));
        assert!(label.is_ascii());
        if cfg!(target_os = "macos") {
            assert_eq!(label, "Cmd+Shift+G");
        } else {
            assert_eq!(label, "Super+Shift+G");
        }
    }

    #[test]
    fn parse_control_shift_space() {
        let (_sc, label) = parse_shortcut("Control+Shift+Space").unwrap();
        assert_eq!(label, "Ctrl+Shift+Space");
    }

    #[test]
    fn default_label_is_words_not_glyphs() {
        let label = default_shortcut_label();
        assert!(label.is_ascii());
        if cfg!(target_os = "macos") {
            assert_eq!(label, "Cmd+Shift+G");
        } else {
            assert_eq!(label, "Ctrl+Shift+G");
        }
    }

    #[test]
    fn reject_bare_key() {
        assert!(parse_shortcut("G").is_err());
    }
}
