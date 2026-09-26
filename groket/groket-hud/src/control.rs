//! Minimal JSON-RPC client for the groket control Unix socket.

use serde_json::{json, Value};
use std::env;
use std::path::PathBuf;
use thiserror::Error;

#[cfg(unix)]
use std::io::{BufRead, BufReader, Write};
#[cfg(unix)]
use std::path::Path;
#[cfg(unix)]
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
#[cfg(unix)]
use std::time::{Duration, Instant};

#[cfg(unix)]
static RPC_STREAM: Mutex<Option<std::os::unix::net::UnixStream>> = Mutex::new(None);
#[cfg(unix)]
static RPC_ID: AtomicU64 = AtomicU64::new(1);
static NOTIFY_WAKE: Mutex<Option<std::sync::mpsc::SyncSender<()>>> = Mutex::new(None);

/// Register the iced tick sender so control notifies wake the palette.
pub fn set_notify_wake(tx: std::sync::mpsc::SyncSender<()>) {
    if let Ok(mut slot) = NOTIFY_WAKE.lock() {
        *slot = Some(tx);
    }
}

#[cfg(unix)]
fn ping_notify_wake() {
    if let Ok(slot) = NOTIFY_WAKE.lock() {
        if let Some(tx) = slot.as_ref() {
            let _ = tx.try_send(());
        }
    }
}

/// Wall-clock budget for connect + one-shot RPC retries (macOS EAGAIN races).
#[cfg(unix)]
const REQUEST_BUDGET: Duration = Duration::from_secs(30);
#[cfg(unix)]
const CONNECT_BUDGET: Duration = Duration::from_secs(5);
#[cfg(unix)]
const RETRY_INITIAL_SLEEP: Duration = Duration::from_millis(25);
#[cfg(unix)]
const RETRY_MAX_SLEEP: Duration = Duration::from_millis(250);
/// Large catalogs (hundreds of sessions) can take >10s on cold disk; macOS
/// surfaces SO_RCVTIMEO expiry as EAGAIN (os error 35).
#[cfg(unix)]
const IO_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Debug, Error)]
pub enum ControlError {
    #[error("{0}")]
    Message(String),
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

impl serde::Serialize for ControlError {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(&self.to_string())
    }
}

pub fn default_socket_path() -> PathBuf {
    if let Ok(p) = env::var("GROKET_CONTROL_SOCKET") {
        let t = p.trim();
        if !t.is_empty() {
            return PathBuf::from(t);
        }
    }
    if let Ok(runtime) = env::var("XDG_RUNTIME_DIR") {
        let t = runtime.trim();
        if !t.is_empty() {
            return PathBuf::from(t).join("groket").join("control.sock");
        }
    }
    dirs_home()
        .map(|h| h.join(".groket").join("run").join("control.sock"))
        .unwrap_or_else(|| PathBuf::from("control.sock"))
}

fn dirs_home() -> Option<PathBuf> {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// Transient socket failures that succeed on a short retry.
///
/// macOS surfaces listen-queue pressure and SO_RCVTIMEO expiry as EAGAIN
/// (os error 35, "Resource temporarily unavailable") — not always as
/// ``WouldBlock``. Also retry refused/missing path while the control owner
/// binds after ``groket serve`` / auto-serve spawn.
#[cfg(unix)]
fn is_transient_io_error(err: &std::io::Error) -> bool {
    use std::io::ErrorKind;
    if matches!(
        err.kind(),
        ErrorKind::WouldBlock
            | ErrorKind::ConnectionRefused
            | ErrorKind::ConnectionReset
            | ErrorKind::ConnectionAborted
            | ErrorKind::NotFound
            | ErrorKind::Interrupted
            | ErrorKind::TimedOut
            | ErrorKind::BrokenPipe
    ) {
        return true;
    }
    match err.raw_os_error() {
        // EAGAIN / EWOULDBLOCK: Linux=11, macOS/*BSD=35
        Some(11) | Some(35) => true,
        // EINTR
        Some(4) => true,
        // ECONNREFUSED: Linux=111, macOS=61
        Some(61) | Some(111) => true,
        // ENOENT
        Some(2) => true,
        // ECONNRESET
        Some(54) | Some(104) => true,
        _ => {
            let msg = err.to_string().to_ascii_lowercase();
            msg.contains("resource temporarily unavailable")
                || msg.contains("connection refused")
                || msg.contains("connection reset")
                || msg.contains("broken pipe")
                || msg.contains("no such file")
        }
    }
}

#[cfg(unix)]
fn is_transient_control_error(err: &ControlError) -> bool {
    match err {
        ControlError::Io(e) => is_transient_io_error(e),
        ControlError::Message(m) => {
            let m = m.to_ascii_lowercase();
            m.contains("resource temporarily unavailable")
                || m.contains("connection refused")
                || m.contains("connection reset")
                || m.contains("broken pipe")
                || m.contains("no such file")
                || m.contains("empty control response")
                || m.contains("timed out")
                || m.contains("os error 35")
                || m.contains("os error 11")
        }
        ControlError::Json(_) => false,
    }
}

#[cfg(unix)]
fn connect_unix(path: &Path) -> Result<std::os::unix::net::UnixStream, ControlError> {
    use std::os::unix::net::UnixStream;
    use std::thread;

    let deadline = Instant::now() + CONNECT_BUDGET;
    let mut sleep = RETRY_INITIAL_SLEEP;
    let mut last_err: Option<std::io::Error> = None;
    while Instant::now() < deadline {
        match UnixStream::connect(path) {
            Ok(stream) => return Ok(stream),
            Err(e) if is_transient_io_error(&e) => {
                last_err = Some(e);
                thread::sleep(sleep);
                sleep = (sleep * 2).min(RETRY_MAX_SLEEP);
            }
            Err(e) => {
                return Err(ControlError::Message(format!(
                    "connect {}: {e} (run: groket serve start -d)",
                    path.display()
                )));
            }
        }
    }
    let e = last_err.unwrap_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            "control socket connect budget exceeded",
        )
    });
    Err(ControlError::Message(format!(
        "connect {}: {e} (run: groket serve start -d)",
        path.display()
    )))
}

#[cfg(unix)]
fn request_once(path: &Path, method: &str, params: &Value) -> Result<Value, ControlError> {
    let mut slot = RPC_STREAM
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let mut stream = match slot.take() {
        Some(existing) => existing,
        None => connect_unix(path)?,
    };
    stream
        .set_read_timeout(Some(IO_TIMEOUT))
        .map_err(|e| ControlError::Message(format!("set read timeout: {e}")))?;
    stream
        .set_write_timeout(Some(IO_TIMEOUT))
        .map_err(|e| ControlError::Message(format!("set write timeout: {e}")))?;
    let request_id = json!(RPC_ID.fetch_add(1, Ordering::Relaxed));
    let payload = json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    });
    let line = serde_json::to_string(&payload).map_err(ControlError::Json)? + "\n";
    if let Err(e) = stream
        .write_all(line.as_bytes())
        .and_then(|_| stream.flush())
    {
        return Err(ControlError::Message(format!("write {method}: {e}")));
    }
    let mut reader = BufReader::new(&mut stream);
    let mut response = String::new();
    let reply = loop {
        response.clear();
        reader.read_line(&mut response).map_err(|e| {
            // macOS SO_RCVTIMEO often returns EAGAIN (35) instead of ETIMEDOUT.
            ControlError::Message(format!("read {method} from {}: {e}", path.display()))
        })?;
        if response.trim().is_empty() {
            return Err(ControlError::Message(format!(
                "empty control response for {method} ({})",
                path.display()
            )));
        }
        let value: Value = serde_json::from_str(&response).map_err(ControlError::Json)?;
        match take_rpc_reply(&request_id, &value) {
            Some(out) => break out,
            None => continue,
        }
    };
    *slot = Some(stream);
    reply
}

/// Pick the JSON-RPC reply for *request_id*.
///
/// Notifications (`session/changed`, …) have no ``id`` and can land on a
/// one-shot HUD socket while ``session/timeline`` is still parsing. Skip
/// those; only a matching ``id`` is the call's result.
pub fn take_rpc_reply(request_id: &Value, value: &Value) -> Option<Result<Value, ControlError>> {
    match value.get("id") {
        None => None,
        Some(id) if id != request_id => None,
        Some(_) => {
            if let Some(err) = value.get("error") {
                let msg = err
                    .get("message")
                    .and_then(|m| m.as_str())
                    .unwrap_or("control error");
                Some(Err(ControlError::Message(msg.to_string())))
            } else {
                Some(Ok(value.get("result").cloned().unwrap_or(Value::Null)))
            }
        }
    }
}

fn request(method: &str, params: Value) -> Result<Value, ControlError> {
    #[cfg(unix)]
    {
        use std::thread;

        let path = default_socket_path();
        let deadline = Instant::now() + REQUEST_BUDGET;
        let mut sleep = RETRY_INITIAL_SLEEP;
        let mut last: Option<ControlError> = None;
        while Instant::now() < deadline {
            match request_once(&path, method, &params) {
                Ok(v) => return Ok(v),
                Err(e) if is_transient_control_error(&e) => {
                    last = Some(e);
                    thread::sleep(sleep);
                    sleep = (sleep * 2).min(RETRY_MAX_SLEEP);
                }
                Err(e) => return Err(e),
            }
        }
        Err(last.unwrap_or_else(|| {
            ControlError::Message(format!(
                "control {method} timed out on {} (run: groket serve start -d)",
                path.display()
            ))
        }))
    }
    #[cfg(not(unix))]
    {
        let _ = (method, params);
        Err(ControlError::Message(
            "Unix control socket only (Windows named pipe not yet)".into(),
        ))
    }
}

pub fn initialize() -> Result<Value, ControlError> {
    request(
        "initialize",
        json!({
            "protocolVersion": 1,
            "clientInfo": { "name": "groket-hud" }
        }),
    )
}

pub const SESSION_LIST_PAGE: u32 = 200;

pub fn session_list(
    query: &str,
    limit: u32,
    offset: u32,
    since_revision: i64,
) -> Result<Value, ControlError> {
    let mut params = json!({ "limit": limit });
    if !query.is_empty() {
        params["query"] = json!(query);
    }
    if offset > 0 {
        params["offset"] = json!(offset);
    }
    if since_revision > 0 {
        params["sinceRevision"] = json!(since_revision);
    }
    request("session/list", params)
}

pub fn session_list_all(query: &str) -> Result<Value, ControlError> {
    use crate::live::catalog_drain_next;
    use crate::wire::decode_session_list_response;

    let first = decode_session_list_response(&session_list(query, SESSION_LIST_PAGE, 0, 0)?)
        .map_err(ControlError::Message)?;
    let mut total = first.total;
    let mut matched = first.matched;
    let mut revision = first.revision;
    let mut sessions = first.sessions;
    if sessions.is_empty() {
        return Ok(json!({
            "sessions": sessions,
            "total": total,
            "matched": matched,
            "revision": revision,
            "unchanged": false,
            "delta": false,
        }));
    }
    let first_id = sessions[0].session_id.clone();
    let Some(mut offset) = catalog_drain_next(0, sessions.len(), SESSION_LIST_PAGE, matched, false)
    else {
        if matched <= 0 {
            matched = i64::try_from(sessions.len()).unwrap_or(i64::MAX);
        }
        if total <= 0 {
            total = matched;
        }
        return Ok(json!({
            "sessions": sessions,
            "total": total,
            "matched": matched,
            "revision": revision,
            "unchanged": false,
            "delta": false,
        }));
    };
    loop {
        let page =
            decode_session_list_response(&session_list(query, SESSION_LIST_PAGE, offset, 0)?)
                .map_err(ControlError::Message)?;
        total = page.total;
        matched = page.matched;
        if page.revision > 0 {
            revision = page.revision;
        }
        if page.sessions.is_empty() {
            break;
        }
        if page.sessions[0].session_id == first_id {
            break;
        }
        let n = page.sessions.len();
        sessions.extend(page.sessions);
        match catalog_drain_next(offset, n, SESSION_LIST_PAGE, matched, false) {
            Some(next) => offset = next,
            None => break,
        }
    }
    if matched <= 0 {
        matched = i64::try_from(sessions.len()).unwrap_or(i64::MAX);
    }
    if total <= 0 {
        total = matched;
    }
    Ok(json!({
        "sessions": sessions,
        "total": total,
        "matched": matched,
        "revision": revision,
        "unchanged": false,
        "delta": false,
    }))
}

pub fn session_overview(session: &str) -> Result<Value, ControlError> {
    // Timeline rows are lazy-loaded via session/timeline (offset/limit).
    request("session/overview", json!({ "session": session }))
}

/// Full turn segments (long assistant wrap-ups). Overview keeps a short list preview.
pub fn session_turns(session: &str) -> Result<Value, ControlError> {
    request("session/turns", json!({ "session": session }))
}

pub struct TimelineRequest<'a> {
    pub session: &'a str,
    pub offset: u32,
    pub limit: u32,
    pub content_chars: u32,
    pub kind: &'a str,
    pub query: &'a str,
    pub around_index: Option<i64>,
    pub at_index: Option<i64>,
    pub prompt_index: Option<i64>,
}

pub fn session_timeline(req: TimelineRequest<'_>) -> Result<Value, ControlError> {
    let mut params = json!({
        "session": req.session,
        "limit": req.limit,
        "offset": req.offset,
        "contentChars": req.content_chars,
    });
    if !req.kind.is_empty() {
        params["kind"] = json!(req.kind);
    }
    if !req.query.is_empty() {
        params["query"] = json!(req.query);
    }
    if let Some(ix) = req.around_index {
        params["aroundIndex"] = json!(ix);
    }
    if let Some(ix) = req.at_index {
        params["atIndex"] = json!(ix);
    }
    if let Some(ix) = req.prompt_index {
        params["promptIndex"] = json!(ix);
    }
    request("session/timeline", params)
}

pub fn session_follow_up(
    session: &str,
    prompt: &str,
    final_turn: bool,
) -> Result<Value, ControlError> {
    request(
        "session/follow_up",
        json!({
            "session": session,
            "prompt": prompt,
            "final": final_turn,
        }),
    )
}

pub fn session_done(session: &str) -> Result<Value, ControlError> {
    request("session/done", json!({ "session": session }))
}

pub fn notes_upsert(
    session: &str,
    note: Value,
    expected_revision: &str,
) -> Result<Value, ControlError> {
    request(
        "notes/upsert",
        json!({
            "session": session,
            "expectedRevision": expected_revision,
            "note": note,
        }),
    )
}

pub fn notes_delete(
    session: &str,
    note_id: &str,
    expected_revision: &str,
) -> Result<Value, ControlError> {
    request(
        "notes/delete",
        json!({
            "session": session,
            "noteId": note_id,
            "expectedRevision": expected_revision,
        }),
    )
}

/// Spawn a background thread that holds a persistent control socket and
/// forwards JSON-RPC notifications to *on_notify*.
///
/// Requests reuse one RPC stream (:func:`request`); this stream is notify-only
/// so it never fights concurrent RPC readers.
#[cfg(unix)]
pub fn spawn_notify_listener<F>(on_notify: F) -> std::thread::JoinHandle<()>
where
    F: Fn(String, Value) + Send + 'static,
{
    use std::io::{BufRead, BufReader, Write};
    use std::thread;

    thread::spawn(move || {
        loop {
            let path = default_socket_path();
            let stream = match connect_unix(&path) {
                Ok(s) => s,
                Err(_) => {
                    thread::sleep(Duration::from_millis(750));
                    continue;
                }
            };
            if stream
                .set_read_timeout(Some(Duration::from_secs(30)))
                .is_err()
            {
                thread::sleep(Duration::from_millis(500));
                continue;
            }
            let mut writer = match stream.try_clone() {
                Ok(w) => w,
                Err(_) => {
                    thread::sleep(Duration::from_millis(500));
                    continue;
                }
            };
            let init = json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientInfo": { "name": "groket-hud-notify" }
                }
            });
            let line = match serde_json::to_string(&init) {
                Ok(s) => s + "\n",
                Err(_) => {
                    thread::sleep(Duration::from_millis(500));
                    continue;
                }
            };
            if writer.write_all(line.as_bytes()).is_err() || writer.flush().is_err() {
                thread::sleep(Duration::from_millis(500));
                continue;
            }
            // stream moved into BufReader after clone for writes
            let mut reader = BufReader::new(stream);
            let mut buf = String::new();
            loop {
                buf.clear();
                match reader.read_line(&mut buf) {
                    Ok(0) => break,
                    Ok(_) => {
                        let trimmed = buf.trim();
                        if trimmed.is_empty() {
                            continue;
                        }
                        let Ok(value) = serde_json::from_str::<Value>(trimmed) else {
                            continue;
                        };
                        let method = value
                            .get("method")
                            .and_then(|m| m.as_str())
                            .unwrap_or("")
                            .to_string();
                        if method.is_empty() {
                            continue;
                        }
                        let params = value.get("params").cloned().unwrap_or(Value::Null);
                        on_notify(method, params);
                        ping_notify_wake();
                    }
                    Err(e) if is_transient_io_error(&e) => {
                        // idle timeout — keep listening
                        continue;
                    }
                    Err(_) => break,
                }
            }
            thread::sleep(Duration::from_millis(500));
        }
    })
}

#[cfg(not(unix))]
pub fn spawn_notify_listener<F>(on_notify: F) -> std::thread::JoinHandle<()>
where
    F: Fn(String, Value) + Send + 'static,
{
    let _ = on_notify;
    std::thread::spawn(|| {})
}

#[cfg(all(test, unix))]
mod tests {
    use super::{is_transient_control_error, is_transient_io_error, take_rpc_reply, ControlError};
    use serde_json::json;
    use std::io::{Error, ErrorKind};

    #[test]
    fn take_rpc_reply_skips_notifications_and_other_ids() {
        let id = json!(42);
        assert!(take_rpc_reply(
            &id,
            &json!({"jsonrpc": "2.0", "method": "session/changed", "params": {"sessionId": "s"}})
        )
        .is_none());
        assert!(take_rpc_reply(&id, &json!({"jsonrpc": "2.0", "id": 2, "result": {}})).is_none());
        let ok = take_rpc_reply(
            &id,
            &json!({"jsonrpc": "2.0", "id": 42, "result": {"total": 3, "events": []}}),
        )
        .unwrap()
        .unwrap();
        assert_eq!(ok["total"], 3);
        let err = take_rpc_reply(
            &id,
            &json!({"jsonrpc": "2.0", "id": 42, "error": {"message": "session not found"}}),
        )
        .unwrap()
        .unwrap_err();
        assert!(err.to_string().contains("session not found"));
    }

    #[test]
    fn eagain_os_error_35_is_transient() {
        let err = Error::from_raw_os_error(35);
        assert!(is_transient_io_error(&err));
    }

    #[test]
    fn connection_refused_is_transient() {
        let err = Error::new(ErrorKind::ConnectionRefused, "refused");
        assert!(is_transient_io_error(&err));
    }

    #[test]
    fn permission_denied_is_not_transient() {
        let err = Error::new(ErrorKind::PermissionDenied, "denied");
        assert!(!is_transient_io_error(&err));
    }

    #[test]
    fn resource_temporarily_unavailable_message_is_transient() {
        let err = Error::other("Resource temporarily unavailable (os error 35)");
        assert!(is_transient_io_error(&err));
        let wrapped = ControlError::Message(
            "read initialize from /tmp/x: Resource temporarily unavailable (os error 35)".into(),
        );
        assert!(is_transient_control_error(&wrapped));
    }
}
