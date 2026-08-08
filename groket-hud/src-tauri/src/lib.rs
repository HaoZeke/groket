mod control;
mod shortcut;

use std::path::Path;
use std::sync::Mutex;

use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

struct HudState {
    summon_label: String,
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct InitialSelection {
    session: String,
    session_id: String,
    prompt_index: Option<u32>,
}

/// Run blocking Unix-socket RPC off the UI/main thread.
///
/// Sync commands freeze the WebView while the control owner works (catalog /
/// overview can take seconds). ``spawn_blocking`` keeps list selection and
/// painting responsive.
async fn control_blocking<F>(f: F) -> Result<serde_json::Value, control::ControlError>
where
    F: FnOnce() -> Result<serde_json::Value, control::ControlError> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(f)
        .await
        .map_err(|e| control::ControlError::Message(format!("control worker: {e}")))?
}

#[tauri::command]
async fn control_initialize() -> Result<serde_json::Value, control::ControlError> {
    control_blocking(control::initialize).await
}

#[tauri::command]
async fn control_session_list(
    query: Option<String>,
    limit: Option<u32>,
) -> Result<serde_json::Value, control::ControlError> {
    let q = query.unwrap_or_default();
    let lim = limit.unwrap_or(80);
    control_blocking(move || control::session_list(&q, lim)).await
}

#[tauri::command]
async fn control_session_get(session: String) -> Result<serde_json::Value, control::ControlError> {
    control_blocking(move || control::session_get(&session)).await
}

#[tauri::command]
async fn control_session_open(
    session: String,
    prompt_index: Option<u32>,
) -> Result<serde_json::Value, control::ControlError> {
    control_blocking(move || control::session_open(&session, prompt_index)).await
}

#[tauri::command]
async fn control_session_overview(
    session: String,
) -> Result<serde_json::Value, control::ControlError> {
    control_blocking(move || control::session_overview(&session)).await
}

#[tauri::command]
async fn control_session_turns(
    session: String,
) -> Result<serde_json::Value, control::ControlError> {
    control_blocking(move || control::session_turns(&session)).await
}

#[tauri::command]
async fn control_session_timeline(
    session: String,
    offset: Option<u32>,
    limit: Option<u32>,
) -> Result<serde_json::Value, control::ControlError> {
    let off = offset.unwrap_or(0);
    let lim = limit.unwrap_or(80);
    control_blocking(move || control::session_timeline(&session, off, lim)).await
}

#[tauri::command]
async fn control_notes_list(session: String) -> Result<serde_json::Value, control::ControlError> {
    control_blocking(move || control::notes_list(&session)).await
}

#[tauri::command]
async fn control_session_usage(
    session: String,
) -> Result<serde_json::Value, control::ControlError> {
    control_blocking(move || control::session_usage(&session)).await
}

#[tauri::command]
fn control_socket_path() -> String {
    control::default_socket_path().display().to_string()
}

#[tauri::command]
fn hud_summon_shortcut(state: State<'_, Mutex<HudState>>) -> String {
    state
        .lock()
        .map(|s| s.summon_label.clone())
        .unwrap_or_else(|_| shortcut::default_shortcut_label().to_string())
}

#[tauri::command]
fn hud_initial_selection() -> Option<InitialSelection> {
    let session = std::env::var("GROKET_HUD_INITIAL_SESSION").ok()?;
    let session = session.trim().to_string();
    if session.is_empty() {
        return None;
    }
    let session_id = Path::new(&session)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(&session)
        .to_string();
    let prompt_index = std::env::var("GROKET_HUD_INITIAL_PROMPT_INDEX")
        .ok()
        .and_then(|raw| raw.trim().parse::<u32>().ok());
    Some(InitialSelection {
        session,
        session_id,
        prompt_index,
    })
}

fn show_on_start() -> bool {
    matches!(
        std::env::var("GROKET_HUD_SHOW_ON_START")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase()
            .as_str(),
        "1" | "true" | "yes"
    )
}

fn toggle_palette(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("palette") {
        match win.is_visible() {
            Ok(true) => {
                let _ = win.hide();
            }
            _ => {
                show_palette(&win);
            }
        }
    }
}

fn show_palette(win: &WebviewWindow) {
    // Centered sheet (Finder Go to Folder family). Focus is claimed here; the
    // webview focuses #q on palette-shown so typing works immediately.
    let _ = win.show();
    let _ = win.center();
    let _ = win.set_focus();
    let _ = win.emit("palette-shown", ());
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Default ⌘⇧G / Ctrl+Shift+G; override via GROKET_HUD_SHORTCUT or
    // ~/.groket/config.json ``hud.global_shortcut``.
    let (summon, summon_label) = shortcut::resolve_summon_shortcut();
    eprintln!("groket-hud: summon shortcut {summon_label}");

    tauri::Builder::default()
        .manage(Mutex::new(HudState {
            summon_label: summon_label.clone(),
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, _shortcut, event| {
                    if event.state == ShortcutState::Pressed {
                        toggle_palette(app);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            control_initialize,
            control_session_list,
            control_session_get,
            control_session_open,
            control_session_overview,
            control_session_turns,
            control_session_timeline,
            control_notes_list,
            control_session_usage,
            control_socket_path,
            hud_summon_shortcut,
            hud_initial_selection,
        ])
        .setup(move |app| {
            // Sol-like agent: no Dock icon, no ⌘Tab entry (macOS only).
            #[cfg(target_os = "macos")]
            {
                let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
            }
            if let Err(err) = app.global_shortcut().register(summon.clone()) {
                eprintln!(
                    "groket-hud: failed to register shortcut {summon_label}: {err}; trying default"
                );
                let def = shortcut::default_shortcut();
                app.global_shortcut().register(def)?;
                if let Ok(mut st) = app.state::<Mutex<HudState>>().lock() {
                    st.summon_label = shortcut::default_shortcut_label().to_string();
                }
            }
            if let Some(win) = app.get_webview_window("palette") {
                if show_on_start() {
                    show_palette(&win);
                } else {
                    // Stay hidden until the global hotkey or an explicit show request.
                    let _ = win.hide();
                }
            }
            // Persistent notify stream: session/changed, notes/changed, analysis/changed.
            let handle = app.handle().clone();
            let _ = control::spawn_notify_listener(move |method, params| {
                if method == "hud/show" {
                    if let Some(win) = handle.get_webview_window("palette") {
                        show_palette(&win);
                    }
                }
                let payload = serde_json::json!({ "method": method, "params": params });
                let _ = handle.emit("control-notify", payload);
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running groket-hud");
}

#[cfg(test)]
mod tests {
    use super::{startup_show_requested, tray_menu_action, TrayAction};

    #[test]
    fn tray_menu_routes_show_and_quit_without_fallback_actions() {
        assert_eq!(tray_menu_action("show-hud"), TrayAction::Show);
        assert_eq!(tray_menu_action("quit-hud"), TrayAction::Quit);
        assert_eq!(tray_menu_action("unknown"), TrayAction::Ignore);
    }

    #[test]
    fn startup_show_accepts_only_explicit_truthy_values() {
        for raw in ["1", "true", "TRUE", "yes", " Yes "] {
            assert!(startup_show_requested(Some(raw)));
        }
        for raw in ["", "0", "false", "show"] {
            assert!(!startup_show_requested(Some(raw)));
        }
        assert!(!startup_show_requested(None));
    }
}
