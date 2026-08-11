//! Desktop notifications for session and analysis transitions.
//!
//! Linux uses the freedesktop Notifications bus (dunst, mako, fnott, swaync,
//! notification-daemon). macOS uses Notification Center. Windows uses toasts.

use std::collections::HashMap;
use std::thread;

use serde_json::Value;

use crate::format::list_status_label;

pub const APP_NAME: &str = "Groket HUD";
pub const ENV_NAME: &str = "GROKET_HUD_NOTIFY";

const ICON_PNG: &[u8] = include_bytes!("../../brand/png/groket-favicon-32.png");

/// Urgency the host daemon maps to its own levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UrgencyKind {
    Low,
    Normal,
    Critical,
}

/// One bubble to post to the host notification daemon.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DesktopNotice {
    pub summary: String,
    pub body: String,
    pub urgency: UrgencyKind,
}

/// How a status observation relates to the last value for that session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Observe {
    First,
    Same,
    Changed { from: String, to: String },
}

/// Env override: ``1``/``true``/``yes`` on, ``0``/``false``/``no`` off.
pub fn env_flag(value: Option<&str>) -> Option<bool> {
    let v = value.map(str::trim).filter(|s| !s.is_empty())?;
    match v.to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" => Some(true),
        "0" | "false" | "no" => Some(false),
        _ => None,
    }
}

/// True when desktop notifications should be posted.
pub fn notifications_enabled() -> bool {
    if let Some(flag) = env_flag(std::env::var(ENV_NAME).ok().as_deref()) {
        return flag;
    }
    crate::prefs::desktop_notifications()
}

pub fn observe_status(prev: Option<&str>, new: &str) -> Observe {
    let to = normalize(new);
    match prev {
        None => Observe::First,
        Some(from) if normalize(from) == to => Observe::Same,
        Some(from) => Observe::Changed {
            from: normalize(from),
            to,
        },
    }
}

/// Notice for a session status transition. First sightings produce none.
pub fn session_notice(title: &str, sid: &str, from: &str, to: &str) -> Option<DesktopNotice> {
    let kind = notice_kind(to)?;
    if normalize(from) == normalize(to) {
        return None;
    }
    let label = display_name(title, sid);
    Some(match kind {
        "awaiting" => DesktopNotice {
            summary: "Awaiting a reply".into(),
            body: format!("{label} is waiting for follow-up or Done"),
            urgency: UrgencyKind::Normal,
        },
        "complete" => DesktopNotice {
            summary: "Session complete".into(),
            body: label,
            urgency: UrgencyKind::Low,
        },
        "cancelled" => DesktopNotice {
            summary: "Session cancelled".into(),
            body: label,
            urgency: UrgencyKind::Normal,
        },
        "error" => DesktopNotice {
            summary: "Session failed".into(),
            body: label,
            urgency: UrgencyKind::Critical,
        },
        _ => return None,
    })
}

/// Notice for an analysis job that left the running state.
pub fn analysis_notice(
    title: &str,
    sid: &str,
    state: &str,
    finding_count: i64,
    error: &str,
) -> Option<DesktopNotice> {
    let label = display_name(title, sid);
    let st = normalize(state);
    match st.as_str() {
        "done" | "complete" => Some(DesktopNotice {
            summary: "Analysis finished".into(),
            body: if finding_count > 0 {
                format!("{label} · {finding_count} findings")
            } else {
                format!("{label} · no findings")
            },
            urgency: UrgencyKind::Low,
        }),
        "error" | "failed" => Some(DesktopNotice {
            summary: "Analysis failed".into(),
            body: if error.is_empty() {
                label
            } else {
                format!("{label} · {error}")
            },
            urgency: UrgencyKind::Critical,
        }),
        _ => None,
    }
}

/// Decode an ``analysis/changed`` payload.
pub fn analysis_from_params(params: &Value, title: &str) -> Option<DesktopNotice> {
    let sid = params
        .get("sessionId")
        .and_then(Value::as_str)
        .unwrap_or("");
    if sid.is_empty() {
        return None;
    }
    let state = params.get("state").and_then(Value::as_str).unwrap_or("");
    let findings = params
        .get("findingCount")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let error = params.get("error").and_then(Value::as_str).unwrap_or("");
    analysis_notice(title, sid, state, findings, error)
}

/// Record catalog rows. When *seed* is true, remember statuses without posting.
pub fn notices_from_rows(
    seen: &mut HashMap<String, String>,
    rows: &[(String, String, String)],
    seed: bool,
) -> Vec<DesktopNotice> {
    let mut out = Vec::new();
    for (sid, title, status) in rows {
        if sid.is_empty() {
            continue;
        }
        let to = list_status_label(status, "");
        match observe_status(seen.get(sid).map(String::as_str), &to) {
            Observe::First => {
                seen.insert(sid.clone(), normalize(&to));
            }
            Observe::Same => {}
            Observe::Changed { from, to } => {
                seen.insert(sid.clone(), to.clone());
                if !seed {
                    if let Some(n) = session_notice(title, sid, &from, &to) {
                        out.push(n);
                    }
                }
            }
        }
    }
    out
}

/// Post on a worker thread. Host failure is ignored (no daemon is fine).
pub fn post(notice: DesktopNotice) {
    if !notifications_enabled() {
        return;
    }
    let _ = thread::Builder::new()
        .name("groket-notify".into())
        .spawn(move || {
            if let Err(err) = send_blocking(&notice) {
                crate::log::error(&format!("desktop notify: {err}"));
            }
        });
}

fn send_blocking(notice: &DesktopNotice) -> Result<(), String> {
    let mut n = notify_rust::Notification::new();
    n.appname(APP_NAME)
        .summary(&notice.summary)
        .body(&notice.body);
    if let Some(path) = icon_file() {
        n.icon(&path);
    }
    n.urgency(match notice.urgency {
        UrgencyKind::Low => notify_rust::Urgency::Low,
        UrgencyKind::Normal => notify_rust::Urgency::Normal,
        UrgencyKind::Critical => notify_rust::Urgency::Critical,
    });
    n.show().map(|_| ()).map_err(|e| e.to_string())
}

fn icon_file() -> Option<String> {
    let home = std::env::var_os("HOME")?;
    let path = std::path::PathBuf::from(home)
        .join(".groket")
        .join("hud-notify.png");
    if !path.is_file() {
        let _ = std::fs::create_dir_all(path.parent()?);
        let _ = std::fs::write(&path, ICON_PNG);
    }
    path.is_file().then(|| path.to_string_lossy().into_owned())
}

fn display_name(title: &str, sid: &str) -> String {
    let t = title.trim();
    if !t.is_empty() {
        return t.to_string();
    }
    let s = sid.trim();
    if s.len() > 12 {
        format!("{}…", &s[..12])
    } else {
        s.to_string()
    }
}

fn normalize(status: &str) -> String {
    let s = list_status_label(status, "")
        .trim()
        .to_ascii_lowercase()
        .replace(char::is_whitespace, "_");
    if s.contains("await") {
        return "awaiting".into();
    }
    if s.contains("fail") || s == "error" || s.contains("timeout") {
        return "error".into();
    }
    if s.contains("cancel") || s.contains("interrupt") || s.contains("abort") {
        return "cancelled".into();
    }
    if s.contains("complete") || s == "ok" || s == "success" {
        return "complete".into();
    }
    s
}

fn notice_kind(status: &str) -> Option<&'static str> {
    match normalize(status).as_str() {
        "awaiting" => Some("awaiting"),
        "complete" => Some("complete"),
        "cancelled" => Some("cancelled"),
        "error" => Some("error"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_flag_parses() {
        assert_eq!(env_flag(Some("1")), Some(true));
        assert_eq!(env_flag(Some("NO")), Some(false));
        assert_eq!(env_flag(None), None);
        assert_eq!(env_flag(Some("")), None);
    }

    #[test]
    fn first_sighting_is_silent() {
        let mut seen = HashMap::new();
        let rows = vec![("abc".into(), "Demo".into(), "running".into())];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("running"));
    }

    #[test]
    fn awaiting_transition_notifies() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "awaiting".into())];
        let notes = notices_from_rows(&mut seen, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Awaiting a reply");
        assert!(notes[0].body.contains("Demo"));
    }

    #[test]
    fn seed_pass_swallows_transitions() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &rows, true).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("complete"));
    }

    #[test]
    fn running_is_silent() {
        assert!(session_notice("t", "s", "pending", "running").is_none());
    }

    #[test]
    fn analysis_done_and_error() {
        let done = analysis_notice("Pack", "sid", "done", 3, "").unwrap();
        assert_eq!(done.summary, "Analysis finished");
        assert!(done.body.contains("3 findings"));
        let err = analysis_notice("Pack", "sid", "error", 0, "boom").unwrap();
        assert_eq!(err.summary, "Analysis failed");
        assert!(err.body.contains("boom"));
        assert!(analysis_notice("Pack", "sid", "running", 0, "").is_none());
    }

    #[test]
    fn analysis_params_need_session() {
        assert!(analysis_from_params(&serde_json::json!({"state": "done"}), "T").is_none());
        let n = analysis_from_params(
            &serde_json::json!({
                "sessionId": "s1",
                "state": "done",
                "findingCount": 1
            }),
            "T",
        )
        .unwrap();
        assert_eq!(n.summary, "Analysis finished");
    }
}
