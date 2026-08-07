//! Minimal JSON-RPC client for the groket control Unix socket.

use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use thiserror::Error;

/// Wall-clock budget for connect + one-shot RPC retries (macOS EAGAIN races).
const REQUEST_BUDGET: Duration = Duration::from_secs(30);
const CONNECT_BUDGET: Duration = Duration::from_secs(5);
const RETRY_INITIAL_SLEEP: Duration = Duration::from_millis(25);
const RETRY_MAX_SLEEP: Duration = Duration::from_millis(250);
/// Large catalogs (hundreds of sessions) can take >10s on cold disk; macOS
/// surfaces SO_RCVTIMEO expiry as EAGAIN (os error 35).
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
    env::var_os("HOME").map(PathBuf::from)
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
    use std::os::unix::net::UnixStream;

    let mut stream: UnixStream = connect_unix(path)?;
    stream
        .set_read_timeout(Some(IO_TIMEOUT))
        .map_err(|e| ControlError::Message(format!("set read timeout: {e}")))?;
    stream
        .set_write_timeout(Some(IO_TIMEOUT))
        .map_err(|e| ControlError::Message(format!("set write timeout: {e}")))?;
    let payload = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    });
    let line = serde_json::to_string(&payload).map_err(ControlError::Json)? + "\n";
    stream.write_all(line.as_bytes()).map_err(|e| {
        if is_transient_io_error(&e) {
            ControlError::Message(format!("write {method}: {e}"))
        } else {
            ControlError::Message(format!("write {method}: {e}"))
        }
    })?;
    stream
        .flush()
        .map_err(|e| ControlError::Message(format!("flush {method}: {e}")))?;
    let mut reader = BufReader::new(stream);
    let mut response = String::new();
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
    if let Some(err) = value.get("error") {
        let msg = err
            .get("message")
            .and_then(|m| m.as_str())
            .unwrap_or("control error");
        return Err(ControlError::Message(msg.to_string()));
    }
    Ok(value.get("result").cloned().unwrap_or(Value::Null))
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

pub fn session_list(query: &str, limit: u32) -> Result<Value, ControlError> {
    let mut params = json!({ "limit": limit });
    if !query.is_empty() {
        params["query"] = json!(query);
    }
    request("session/list", params)
}

pub fn session_get(session: &str) -> Result<Value, ControlError> {
    request("session/get", json!({ "session": session }))
}

pub fn session_open(session: &str, prompt_index: Option<u32>) -> Result<Value, ControlError> {
    request(
        "session/open",
        json!({ "session": session, "promptIndex": prompt_index }),
    )
}

pub fn session_overview(session: &str) -> Result<Value, ControlError> {
    // Timeline rows are lazy-loaded via session/timeline (offset/limit).
    request("session/overview", json!({ "session": session }))
}

pub fn session_turns(session: &str) -> Result<Value, ControlError> {
    request("session/turns", json!({ "session": session }))
}

pub fn session_timeline(session: &str, offset: u32, limit: u32) -> Result<Value, ControlError> {
    request(
        "session/timeline",
        json!({
            "session": session,
            "limit": limit,
            "offset": offset,
            // Enough for clean markdown fences in agent turns (server caps).
            "contentChars": 12_000,
        }),
    )
}

pub fn notes_list(session: &str) -> Result<Value, ControlError> {
    request("notes/list", json!({ "session": session }))
}

pub fn session_usage(session: &str) -> Result<Value, ControlError> {
    request("session/usage", json!({ "session": session }))
}

/// Spawn a background thread that holds a persistent control socket and
/// forwards JSON-RPC notifications to *on_notify*.
///
/// Requests stay on short-lived connections (:func:`request`); this stream is
/// notify-only so it never fights concurrent RPC readers.
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
    use super::{is_transient_control_error, is_transient_io_error, ControlError};
    use std::io::{Error, ErrorKind};

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
        let err = Error::new(
            ErrorKind::Other,
            "Resource temporarily unavailable (os error 35)",
        );
        assert!(is_transient_io_error(&err));
        let wrapped = ControlError::Message(
            "read initialize from /tmp/x: Resource temporarily unavailable (os error 35)".into(),
        );
        assert!(is_transient_control_error(&wrapped));
    }
}
