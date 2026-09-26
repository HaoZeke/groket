//! Map Textual theme tokens (``config.json`` ``theme``) onto iced.

use std::sync::OnceLock;

use iced::Color;
use iced::Theme;
use serde_json::Value;

use crate::format::BrandRole;

pub use icedtea::theme::{mix, relative_luma, Tokens};

/// TUI brand hex (``COMPLETE`` / ``FAILED`` / ``RUNNING`` / ``CANCELLED`` / ``CREAM``).
pub const BRAND_CREAM: Color = Color::from_rgb8(0xFB, 0xF1, 0xC7);
pub const BRAND_COMPLETE: Color = Color::from_rgb8(0x98, 0x97, 0x1A);
pub const BRAND_RUNNING: Color = Color::from_rgb8(0xD7, 0x99, 0x21);
pub const BRAND_FAILED: Color = Color::from_rgb8(0xCC, 0x24, 0x1D);
pub const BRAND_CANCELLED: Color = Color::from_rgb8(0x92, 0x83, 0x74);

/// Color for a TUI ``EVENT_TYPE_STYLE`` brand role.
pub fn brand_role_color(role: BrandRole) -> Color {
    match role {
        BrandRole::Cream => BRAND_CREAM,
        BrandRole::Complete => BRAND_COMPLETE,
        BrandRole::Running => BRAND_RUNNING,
        BrandRole::Failed => BRAND_FAILED,
        BrandRole::Cancelled => BRAND_CANCELLED,
    }
}

const CATALOG: &str = include_str!("../assets/textual-themes.json");

/// True when ``$surface`` is a dark canvas (gruvbox, nord, …).
pub fn canvas_is_dark(tok: Tokens) -> bool {
    relative_luma(tok.canvas) < 0.45
}

fn parse_hex(s: &str) -> Option<Color> {
    let t = s.trim().trim_start_matches('#');
    if t.len() < 6 || !t.as_bytes()[..6].iter().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let r = u8::from_str_radix(&t[0..2], 16).ok()?;
    let g = u8::from_str_radix(&t[2..4], 16).ok()?;
    let b = u8::from_str_radix(&t[4..6], 16).ok()?;
    Some(Color::from_rgb8(r, g, b))
}

fn color_of(colors: &Value, key: &str, fallback: Color) -> Color {
    colors
        .get(key)
        .and_then(Value::as_str)
        .and_then(parse_hex)
        .unwrap_or(fallback)
}

fn catalog_colors(name: &str) -> (String, Value) {
    let Ok(root) = serde_json::from_str::<Value>(CATALOG) else {
        return ("textual-dark".into(), Value::Null);
    };
    let key = if name.trim().is_empty() {
        "textual-dark"
    } else {
        name.trim()
    };
    let theme = root
        .get(key)
        .or_else(|| root.get("textual-dark"))
        .cloned()
        .unwrap_or(Value::Null);
    let resolved = if root.get(key).is_some() {
        key.to_string()
    } else {
        "textual-dark".into()
    };
    let colors = theme.get("colors").cloned().unwrap_or(Value::Null);
    (resolved, colors)
}

/// Tokens for ``theme`` in ``~/.groket/config.json`` (TUI name).
pub fn tokens(name: &str) -> Tokens {
    let (_key, colors) = catalog_colors(name);
    let fallback_bg = Color::from_rgb8(18, 18, 20);
    let canvas = color_of(&colors, "surface", fallback_bg);
    let text = color_of(&colors, "foreground", Color::from_rgb8(224, 224, 224));
    let muted = color_of(
        &colors,
        "foreground-darken-2",
        color_of(&colors, "foreground-muted", Color::from_rgb8(160, 160, 160)),
    );
    let primary = color_of(&colors, "primary", Color::from_rgb8(1, 120, 212));
    let accent = color_of(&colors, "accent", Color::from_rgb8(254, 166, 43));
    let highlight = color_of(&colors, "primary-background", mix(primary, canvas, 0.35));
    Tokens {
        canvas,
        surface: canvas,
        panel: color_of(&colors, "panel", mix(text, canvas, 0.10)),
        text,
        muted,
        primary,
        accent,
        success: color_of(
            &colors,
            "text-success",
            color_of(&colors, "success", Color::from_rgb8(78, 191, 113)),
        ),
        warning: color_of(
            &colors,
            "text-warning",
            color_of(&colors, "warning", Color::from_rgb8(254, 166, 43)),
        ),
        danger: color_of(
            &colors,
            "text-error",
            color_of(&colors, "error", Color::from_rgb8(185, 60, 91)),
        ),
        border: highlight,
        selection: mix(primary, canvas, 0.28),
        selection_text: text,
    }
}

/// Textual theme names registered on icedtea's catalog.
pub fn catalog() -> &'static icedtea::theme::ThemeCatalog {
    static CATALOG_MAP: OnceLock<icedtea::theme::ThemeCatalog> = OnceLock::new();
    CATALOG_MAP.get_or_init(|| {
        let mut cat = icedtea::theme::ThemeCatalog::new();
        let Ok(root) = serde_json::from_str::<Value>(CATALOG) else {
            return cat;
        };
        let Some(obj) = root.as_object() else {
            return cat;
        };
        for key in obj.keys() {
            let tok = tokens(key);
            cat.register(key.clone(), tok, canvas_is_dark(tok));
        }
        cat
    })
}

pub fn iced_theme(name: &str) -> Theme {
    let (key, _) = catalog_colors(name);
    icedtea::theme::iced_theme(&key, tokens(name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn textual_dark_uses_screen_surface_not_void_background() {
        let t = tokens("textual-dark");
        assert_eq!(t.canvas, Color::from_rgb8(0x1E, 0x1E, 0x1E));
        assert_ne!(t.canvas, Color::from_rgb8(0x12, 0x12, 0x12));
        assert_eq!(t.primary, Color::from_rgb8(0x01, 0x78, 0xD4));
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection, icedtea::theme::mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection_text, t.text);
        assert_eq!(t.border, Color::from_rgb8(0x33, 0x42, 0x4E));
    }

    #[test]
    fn gruvbox_is_in_catalog() {
        let t = tokens("gruvbox");
        assert_ne!(t.canvas, tokens("textual-dark").canvas);
        assert_ne!(t.canvas, tokens("nord").canvas);
        assert!(canvas_is_dark(t));
        assert!(canvas_is_dark(tokens("textual-dark")));
        assert!(!canvas_is_dark(tokens("solarized-light")));
        assert!(catalog().get("gruvbox").is_some());
        assert!(catalog().get("textual-dark").is_some());
    }

    #[test]
    fn flexoki_matches_tui_screen_surface() {
        let t = tokens("flexoki");
        assert_eq!(t.canvas, Color::from_rgb8(0x1C, 0x1B, 0x1A));
        assert_ne!(t.canvas, Color::from_rgb8(0x10, 0x0F, 0x0F));
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection_text, t.text);
        assert_eq!(t.panel, Color::from_rgb8(0x28, 0x27, 0x26));
    }

    #[test]
    fn solarized_light_selected_row_keeps_readable_ink() {
        let t = tokens("solarized-light");
        assert_ne!(t.selection, t.accent);
        assert_eq!(t.selection_text, t.text);
        assert!((relative_luma(t.selection) - relative_luma(t.text)).abs() > 0.20);
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
    }

    #[test]
    fn catalog_hex_drops_textual_alpha_suffix() {
        let t = tokens("gruvbox");
        assert_eq!(t.text, Color::from_rgb8(0xFB, 0xF1, 0xC7));
        assert_eq!(t.text.a, 1.0);
        assert_eq!(t.muted.a, 1.0);
        assert_eq!(t.muted, Color::from_rgb8(0xD0, 0xC6, 0x9E));
        assert_eq!(t.accent, Color::from_rgb8(0xF9, 0xBD, 0x2F));
        assert_eq!(t.warning, Color::from_rgb8(0xFE, 0xAB, 0x67));
        assert_eq!(t.danger, Color::from_rgb8(0xFC, 0x86, 0x79));
    }

    #[test]
    fn mix_is_opaque_between_endpoints() {
        let a = Color::from_rgb8(255, 0, 0);
        let b = Color::from_rgb8(0, 0, 0);
        let m = mix(a, b, 0.5);
        assert!((m.r - 0.5).abs() < 0.01);
        assert_eq!(m.a, 1.0);
    }

    #[test]
    fn brand_role_colors_match_tui_hex() {
        use crate::format::BrandRole;
        assert_eq!(brand_role_color(BrandRole::Cream), BRAND_CREAM);
        assert_eq!(brand_role_color(BrandRole::Complete), BRAND_COMPLETE);
        assert_eq!(brand_role_color(BrandRole::Running), BRAND_RUNNING);
        assert_eq!(brand_role_color(BrandRole::Failed), BRAND_FAILED);
        assert_eq!(brand_role_color(BrandRole::Cancelled), BRAND_CANCELLED);
        assert_eq!(BRAND_CREAM, Color::from_rgb8(0xFB, 0xF1, 0xC7));
        assert_eq!(BRAND_COMPLETE, Color::from_rgb8(0x98, 0x97, 0x1A));
        assert_eq!(BRAND_RUNNING, Color::from_rgb8(0xD7, 0x99, 0x21));
        assert_eq!(BRAND_FAILED, Color::from_rgb8(0xCC, 0x24, 0x1D));
        assert_eq!(BRAND_CANCELLED, Color::from_rgb8(0x92, 0x83, 0x74));
    }

    #[test]
    fn named_theme_tokens_are_icedtea_tokens() {
        let t = tokens("textual-dark");
        assert_eq!(t.selection, icedtea::theme::mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.surface, t.canvas);
        let registered = catalog().resolve("textual-dark");
        assert_eq!(registered.selection, t.selection);
        assert_eq!(registered.primary, t.primary);
        let light = tokens("solarized-light");
        assert_ne!(light.canvas, t.canvas);
        assert_eq!(
            light.selection,
            icedtea::theme::mix(light.primary, light.canvas, 0.28)
        );
    }
}
