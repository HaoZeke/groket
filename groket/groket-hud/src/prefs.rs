//! Read ``~/.groket/config.json`` HUD fields (theme name).

use serde_json::{json, Value};
use std::env;
use std::fs;
use std::path::PathBuf;

fn config_path() -> Option<PathBuf> {
    let home = env::var_os("HOME")?;
    Some(PathBuf::from(home).join(".groket").join("config.json"))
}

fn read_value() -> Value {
    let Some(path) = config_path() else {
        return json!({});
    };
    let Ok(text) = fs::read_to_string(path) else {
        return json!({});
    };
    serde_json::from_str(&text).unwrap_or_else(|_| json!({}))
}

/// Desktop notifications (dunst / mako / Notification Center / toasts).
///
/// Default on. ``hud.desktop_notifications`` in config.json overrides.
pub fn desktop_notifications() -> bool {
    match read_value()
        .get("hud")
        .and_then(|h| h.get("desktop_notifications"))
    {
        Some(Value::Bool(v)) => *v,
        _ => true,
    }
}

/// TUI theme name from config, or ``textual-dark``.
pub fn theme_name() -> String {
    read_value()
        .get("theme")
        .and_then(|x| x.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("textual-dark")
        .to_string()
}

/// Merge ``hud.window_mode`` into an existing config object (other keys kept).
pub fn merge_window_mode(mut root: Value, window_mode: bool) -> Value {
    if !root.is_object() {
        root = json!({});
    }
    let obj = root.as_object_mut().expect("object");
    let hud = obj.entry("hud").or_insert_with(|| json!({}));
    if !hud.is_object() {
        *hud = json!({});
    }
    if let Some(hud_obj) = hud.as_object_mut() {
        hud_obj.insert("window_mode".into(), Value::Bool(window_mode));
        hud_obj.remove("pinned");
    }
    root
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merge_window_mode_keeps_theme_and_shortcut() {
        let src = json!({
            "theme": "gruvbox",
            "hud": { "global_shortcut": "Ctrl+Shift+G", "pinned": true }
        });
        let out = merge_window_mode(src, true);
        assert_eq!(out["theme"], "gruvbox");
        assert_eq!(out["hud"]["global_shortcut"], "Ctrl+Shift+G");
        assert_eq!(out["hud"]["window_mode"], true);
        assert!(out["hud"].get("pinned").is_none());
    }

    #[test]
    fn merge_window_mode_creates_hud_object() {
        let out = merge_window_mode(json!({"theme": "nord"}), false);
        assert_eq!(out["hud"]["window_mode"], false);
    }
}
