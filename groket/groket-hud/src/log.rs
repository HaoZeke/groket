//! Append-only HUD log at ``~/.groket/hud.log`` (override: ``GROKET_HUD_LOG``).

use std::env;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

static LAST: Mutex<String> = Mutex::new(String::new());

/// Default and override path for the HUD log.
pub fn path() -> PathBuf {
    if let Ok(raw) = env::var("GROKET_HUD_LOG") {
        let t = raw.trim();
        if !t.is_empty() {
            return PathBuf::from(t);
        }
    }
    env::var_os("HOME")
        .map(|h| PathBuf::from(h).join(".groket").join("hud.log"))
        .unwrap_or_else(|| PathBuf::from("hud.log"))
}

/// One UTC timestamp + level + message line.
pub fn write(level: &str, msg: &str) {
    let msg = msg.trim();
    if msg.is_empty() {
        return;
    }
    if let Ok(mut last) = LAST.lock() {
        if *last == msg {
            return;
        }
        *last = msg.to_string();
    }
    let line = format!("{} {level} {msg}\n", format_utc(unix_secs()));
    let path = path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(line.as_bytes());
    }
}

pub fn error(msg: &str) {
    write("error", msg);
}

pub fn info(msg: &str) {
    write("info", msg);
}

/// Write panics to the HUD log (detached processes have no terminal).
pub fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let loc = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "?".into());
        let msg = if let Some(s) = info.payload().downcast_ref::<&str>() {
            (*s).to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            info.to_string()
        };
        error(&format!("panic at {loc}: {msg}"));
        previous(info);
    }));
}

fn unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// UTC ``YYYY-MM-DDTHH:MM:SSZ`` from Unix seconds (no extra crate).
pub fn format_utc(secs: u64) -> String {
    let z = secs as i64;
    let days = z.div_euclid(86400);
    let tod = z.rem_euclid(86400) as u64;
    let (year, month, day) = civil_from_days(days);
    let hour = tod / 3600;
    let minute = (tod % 3600) / 60;
    let second = tod % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

/// Civil date from days since Unix epoch (Howard Hinnant).
fn civil_from_days(days: i64) -> (i32, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let mut year = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    if month <= 2 {
        year += 1;
    }
    (year as i32, month as u32, day as u32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};

    static ENV: Mutex<()> = Mutex::new(());
    static N: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn format_utc_known_instants() {
        assert_eq!(format_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(format_utc(1_704_067_200), "2024-01-01T00:00:00Z");
        assert_eq!(format_utc(1_704_067_200 + 3661), "2024-01-01T01:01:01Z");
    }

    #[test]
    fn write_appends_and_dedupes() {
        let _guard = ENV.lock().expect("env lock");
        let n = N.fetch_add(1, Ordering::SeqCst);
        let dir = env::temp_dir().join(format!("groket-hud-log-{n}"));
        let _ = fs::create_dir_all(&dir);
        let file = dir.join("hud.log");
        let _ = fs::remove_file(&file);
        env::set_var("GROKET_HUD_LOG", &file);
        if let Ok(mut last) = LAST.lock() {
            last.clear();
        }
        write("error", "  boom  ");
        write("error", "boom");
        write("error", "other");
        let text = fs::read_to_string(&file).expect("log");
        assert_eq!(text.matches(" boom\n").count(), 1);
        assert!(text.contains(" other\n"));
        assert!(text.contains(" error boom\n"));
        let _ = fs::remove_file(&file);
        env::remove_var("GROKET_HUD_LOG");
    }

    #[test]
    fn path_honors_override() {
        let _guard = ENV.lock().expect("env lock");
        env::set_var("GROKET_HUD_LOG", "/tmp/custom-hud.log");
        assert_eq!(path(), PathBuf::from("/tmp/custom-hud.log"));
        env::remove_var("GROKET_HUD_LOG");
    }
}
