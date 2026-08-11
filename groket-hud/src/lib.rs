//! groket-hud library: control decode, domain helpers, and the iced palette.

pub mod app;
pub mod brand;
pub mod control;
pub mod desktop;
pub mod format;
pub mod fuzzy;
pub mod live;
pub mod log;
pub mod macoswin;
pub mod model;
pub mod place;
pub mod prefs;
pub mod scroll;
pub mod shortcut;
pub mod style;
pub mod theme;
pub mod tray;
pub mod typo;
pub mod view;
pub mod wire;

#[cfg(target_os = "linux")]
pub mod x11focus;

pub use app::run;
