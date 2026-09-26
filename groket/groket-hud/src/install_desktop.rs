//! User-local desktop integration (``--install-desktop``).
//!
//! No system package, DMG, or MSI. Writes icons and a launcher entry under the
//! current user's data directories so Alt-Tab / Dock / Start Menu can resolve the
//! mark. Re-run after moving the binary.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

use thiserror::Error;

/// Freedesktop / bundle id (matches iced ``application_id`` and tray id).
pub const APP_ID: &str = "dev.indynull.groket-hud";
/// Human name on menus and desktop entries.
pub const APP_NAME: &str = "Groket HUD";
/// One-line comment for desktop entries.
pub const APP_COMMENT: &str = "Session palette for the groket control plane";

/// Paths and notes from a successful install.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub wrote: Vec<PathBuf>,
    pub notes: Vec<String>,
}

impl Report {
    /// Lines for stdout (paths then notes).
    pub fn lines(&self) -> Vec<String> {
        let mut out: Vec<String> = self
            .wrote
            .iter()
            .map(|p| format!("wrote {}", p.display()))
            .collect();
        out.extend(self.notes.iter().cloned());
        out
    }
}

/// Where to place files and which binary the launcher should run.
#[derive(Debug, Clone)]
pub struct Layout {
    /// User home (``HOME`` / ``USERPROFILE``).
    pub home: PathBuf,
    /// Absolute path to ``groket-hud`` (or a wrapper target).
    pub exe: PathBuf,
    /// Linux only: override ``XDG_DATA_HOME`` (default ``$home/.local/share``).
    pub xdg_data_home: Option<PathBuf>,
}

#[derive(Debug, Error)]
pub enum Error {
    #[error("could not resolve home directory")]
    NoHome,
    #[error("could not resolve current executable: {0}")]
    CurrentExe(io::Error),
    #[error("{0}")]
    Io(#[from] io::Error),
    #[error("{0}")]
    Other(String),
}

/// Install using this process executable and the user home directory.
pub fn install_default() -> Result<Report, Error> {
    let home = user_home().ok_or(Error::NoHome)?;
    let exe = std::env::current_exe().map_err(Error::CurrentExe)?;
    let exe = canonicalize_best_effort(&exe);
    let xdg = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from);
    install(&Layout {
        home,
        exe,
        xdg_data_home: xdg,
    })
}

/// Install for an explicit layout (tests and custom roots).
pub fn install(layout: &Layout) -> Result<Report, Error> {
    #[cfg(target_os = "linux")]
    {
        install_linux(layout)
    }
    #[cfg(target_os = "macos")]
    {
        install_macos(layout)
    }
    #[cfg(target_os = "windows")]
    {
        install_windows(layout)
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        let _ = layout;
        Err(Error::Other(
            "desktop install is only implemented for Linux, macOS, and Windows".into(),
        ))
    }
}

/// CLI entry: install and print a short report.
pub fn run_cli() -> Result<Report, Error> {
    let report = install_default()?;
    for line in report.lines() {
        println!("{line}");
    }
    Ok(report)
}

fn user_home() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn canonicalize_best_effort(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

fn write_bytes(path: &Path, bytes: &[u8], wrote: &mut Vec<PathBuf>) -> Result<(), Error> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, bytes)?;
    wrote.push(path.to_path_buf());
    Ok(())
}

/// Freedesktop ``.desktop`` body (Linux).
pub fn linux_desktop_entry(exe: &Path, icon_name: &str) -> String {
    let exec = shell_escape_path(exe);
    format!(
        "[Desktop Entry]\n\
         Type=Application\n\
         Version=1.0\n\
         Name={APP_NAME}\n\
         Comment={APP_COMMENT}\n\
         Exec={exec}\n\
         TryExec={exec}\n\
         Icon={icon_name}\n\
         Terminal=false\n\
         Categories=Development;Utility;\n\
         StartupNotify=true\n\
         StartupWMClass={APP_ID}\n\
         X-GNOME-UsesNotifications=true\n\
         Keywords=groket;session;trace;hud;\n"
    )
}

/// Quote a path for a desktop ``Exec=`` key (spaces / special chars).
pub fn shell_escape_path(path: &Path) -> String {
    let s = path.to_string_lossy();
    if s.chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '_' | '-' | '.' | ':' | '+'))
    {
        return s.into_owned();
    }
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Build a multi-image ICO with PNG payloads (Vista+).
pub fn ico_from_png_entries(entries: &[(u32, &[u8])]) -> Result<Vec<u8>, Error> {
    if entries.is_empty() {
        return Err(Error::Other("ICO needs at least one PNG".into()));
    }
    let count =
        u16::try_from(entries.len()).map_err(|_| Error::Other("too many ICO images".into()))?;
    let header_len = 6 + 16 * entries.len();
    let mut offset = u32::try_from(header_len).map_err(|_| Error::Other("ICO too large".into()))?;
    let mut out =
        Vec::with_capacity(header_len + entries.iter().map(|(_, p)| p.len()).sum::<usize>());
    out.extend_from_slice(&0u16.to_le_bytes()); // reserved
    out.extend_from_slice(&1u16.to_le_bytes()); // type icon
    out.extend_from_slice(&count.to_le_bytes());
    let mut dir = Vec::with_capacity(16 * entries.len());
    for &(edge, png) in entries {
        let w = if edge >= 256 { 0u8 } else { edge as u8 };
        let h = w;
        let size =
            u32::try_from(png.len()).map_err(|_| Error::Other("PNG too large for ICO".into()))?;
        dir.push(w);
        dir.push(h);
        dir.push(0); // color count
        dir.push(0); // reserved
        dir.extend_from_slice(&1u16.to_le_bytes()); // planes
        dir.extend_from_slice(&32u16.to_le_bytes()); // bit count
        dir.extend_from_slice(&size.to_le_bytes());
        dir.extend_from_slice(&offset.to_le_bytes());
        offset = offset
            .checked_add(size)
            .ok_or_else(|| Error::Other("ICO offset overflow".into()))?;
    }
    out.extend_from_slice(&dir);
    for &(_, png) in entries {
        out.extend_from_slice(png);
    }
    Ok(out)
}

/// macOS ``Info.plist`` for a minimal user ``.app`` bundle.
pub fn macos_info_plist(has_icns: bool) -> String {
    let icon_keys = if has_icns {
        "  <key>CFBundleIconFile</key>\n  <string>AppIcon</string>\n"
    } else {
        ""
    };
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>groket-hud</string>
  <key>CFBundleIdentifier</key>
  <string>{APP_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key>
  <string>{APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSUIElement</key>
  <false/>
{icon_keys}</dict>
</plist>
"#
    )
}

/// Launcher script inside the macOS app (points at the installed binary path).
pub fn macos_launcher_script(exe: &Path) -> String {
    format!(
        "#!/bin/sh\n# Generated by groket-hud --install-desktop\nexec {} \"$@\"\n",
        shell_escape_path(exe)
    )
}

/// PowerShell that creates a Start Menu shortcut (Windows).
pub fn windows_shortcut_ps1(lnk: &Path, exe: &Path, ico: &Path) -> String {
    let lnk_s = ps_single_quote(&lnk.to_string_lossy());
    let exe_s = ps_single_quote(&exe.to_string_lossy());
    let ico_s = ps_single_quote(&ico.to_string_lossy());
    format!(
        "$ws = New-Object -ComObject WScript.Shell\n\
         $s = $ws.CreateShortcut({lnk_s})\n\
         $s.TargetPath = {exe_s}\n\
         $s.IconLocation = {ico_s}\n\
         $s.Description = '{APP_COMMENT}'\n\
         $s.WorkingDirectory = {wd}\n\
         $s.Save()\n",
        wd = ps_single_quote(
            &exe.parent()
                .unwrap_or_else(|| Path::new("."))
                .to_string_lossy()
        ),
    )
}

fn ps_single_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

fn icon_pngs_for_ico() -> Vec<(u32, &'static [u8])> {
    crate::brand::desktop_icon_pngs()
        .iter()
        .copied()
        .filter(|(e, _)| *e <= 256)
        .collect()
}

#[cfg(target_os = "linux")]
fn install_linux(layout: &Layout) -> Result<Report, Error> {
    let data = layout
        .xdg_data_home
        .clone()
        .unwrap_or_else(|| layout.home.join(".local/share"));
    let mut wrote = Vec::new();
    let mut notes = Vec::new();

    for &(edge, png) in crate::brand::desktop_icon_pngs() {
        if edge > 512 {
            // 1024 is not a standard hicolor size; skip.
            continue;
        }
        let path = data
            .join("icons/hicolor")
            .join(format!("{edge}x{edge}"))
            .join("apps")
            .join(format!("{APP_ID}.png"));
        write_bytes(&path, png, &mut wrote)?;
    }

    let desktop_path = data.join("applications").join(format!("{APP_ID}.desktop"));
    let body = linux_desktop_entry(&layout.exe, APP_ID);
    write_bytes(&desktop_path, body.as_bytes(), &mut wrote)?;

    // Best-effort caches (missing tools are fine).
    let _ = Command::new("update-desktop-database")
        .arg(data.join("applications"))
        .status();
    let hicolor = data.join("icons/hicolor");
    let _ = Command::new("gtk-update-icon-cache")
        .args(["-f", "-t"])
        .arg(&hicolor)
        .status();
    notes.push(format!(
        "Linux: launcher {APP_ID}.desktop → {}",
        layout.exe.display()
    ));
    notes.push(
        "If the icon is stale, log out or run: gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor"
            .into(),
    );
    Ok(Report { wrote, notes })
}

/// Write a macOS ``.app`` under ``home/Applications`` (testable on any host).
pub fn write_macos_app_bundle(layout: &Layout) -> Result<Report, Error> {
    let app = layout
        .home
        .join("Applications")
        .join(format!("{APP_NAME}.app"));
    let contents = app.join("Contents");
    let macos_dir = contents.join("MacOS");
    let resources = contents.join("Resources");
    fs::create_dir_all(&macos_dir)?;
    fs::create_dir_all(&resources)?;

    let mut wrote = Vec::new();
    let mut notes = Vec::new();

    // iconset + optional iconutil
    let iconset = resources.join("AppIcon.iconset");
    fs::create_dir_all(&iconset)?;
    let pairs: &[(u32, &str)] = &[
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ];
    let pngs: std::collections::HashMap<u32, &[u8]> =
        crate::brand::desktop_icon_pngs().iter().copied().collect();
    for &(edge, name) in pairs {
        if let Some(png) = pngs.get(&edge) {
            write_bytes(&iconset.join(name), png, &mut wrote)?;
        }
    }

    let icns = resources.join("AppIcon.icns");
    let has_icns = match Command::new("iconutil")
        .args(["-c", "icns"])
        .arg(&iconset)
        .arg("-o")
        .arg(&icns)
        .status()
    {
        Ok(st) if st.success() && icns.is_file() => {
            wrote.push(icns.clone());
            true
        }
        _ => {
            notes.push(
                "iconutil not available or failed; Dock may use a generic mark until you re-run on macOS with Xcode CLT"
                    .into(),
            );
            false
        }
    };
    // Drop intermediate iconset after icns when we have one (keeps tree tidy).
    if has_icns {
        let _ = fs::remove_dir_all(&iconset);
        wrote.retain(|p| !p.starts_with(&iconset));
    }

    let plist = contents.join("Info.plist");
    write_bytes(&plist, macos_info_plist(has_icns).as_bytes(), &mut wrote)?;

    let launcher = macos_dir.join("groket-hud");
    write_bytes(
        &launcher,
        macos_launcher_script(&layout.exe).as_bytes(),
        &mut wrote,
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&launcher)?.permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&launcher, perms)?;
    }

    notes.push(format!("macOS app bundle: {}", app.display()));
    notes.push(format!("Launches: {}", layout.exe.display()));
    notes.push(
        "Overlay mode stays out of the Dock until you pop out; the app still shows under Applications."
            .into(),
    );
    Ok(Report { wrote, notes })
}

#[cfg(target_os = "macos")]
fn install_macos(layout: &Layout) -> Result<Report, Error> {
    write_macos_app_bundle(layout)
}

/// Write Windows icons + shortcut script; create ``.lnk`` when PowerShell works.
pub fn write_windows_desktop_files(layout: &Layout) -> Result<Report, Error> {
    let base = layout
        .home
        .join("AppData")
        .join("Local")
        .join("Groket")
        .join("hud");
    // Also honor LOCALAPPDATA when home is a real Windows profile via env in install_windows.
    let mut wrote = Vec::new();
    let mut notes = Vec::new();

    for &(edge, png) in crate::brand::desktop_icon_pngs() {
        if edge > 256 {
            continue;
        }
        let path = base.join(format!("groket-hud-{edge}.png"));
        write_bytes(&path, png, &mut wrote)?;
    }

    let ico_path = base.join("groket-hud.ico");
    let ico = ico_from_png_entries(&icon_pngs_for_ico())?;
    write_bytes(&ico_path, &ico, &mut wrote)?;

    let programs = layout
        .home
        .join("AppData")
        .join("Roaming")
        .join("Microsoft")
        .join("Windows")
        .join("Start Menu")
        .join("Programs");
    fs::create_dir_all(&programs)?;
    let lnk = programs.join(format!("{APP_NAME}.lnk"));
    let ps1 = base.join("create-start-menu-shortcut.ps1");
    let script = windows_shortcut_ps1(&lnk, &layout.exe, &ico_path);
    write_bytes(&ps1, script.as_bytes(), &mut wrote)?;

    notes.push(format!("Windows assets under {}", base.display()));
    notes.push(format!("Start Menu shortcut target: {}", lnk.display()));

    Ok(Report { wrote, notes })
}

#[cfg(target_os = "windows")]
fn install_windows(layout: &Layout) -> Result<Report, Error> {
    // Expect layout.home = USERPROFILE so AppData\Local and Roaming resolve.
    let mut report = write_windows_desktop_files(layout)?;

    let base = layout
        .home
        .join("AppData")
        .join("Local")
        .join("Groket")
        .join("hud");
    let ps1 = base.join("create-start-menu-shortcut.ps1");
    match Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
        ])
        .arg(&ps1)
        .status()
    {
        Ok(st) if st.success() => {
            let lnk = layout
                .home
                .join("AppData")
                .join("Roaming")
                .join("Microsoft")
                .join("Windows")
                .join("Start Menu")
                .join("Programs")
                .join(format!("{APP_NAME}.lnk"));
            if lnk.is_file() {
                report.wrote.push(lnk);
            }
            report
                .notes
                .push("Start Menu shortcut created (Groket HUD).".into());
        }
        Ok(st) => {
            report.notes.push(format!(
                "PowerShell shortcut script exited {}; run {}",
                st.code().unwrap_or(-1),
                ps1.display()
            ));
        }
        Err(err) => {
            report.notes.push(format!(
                "PowerShell unavailable ({err}); run {}",
                ps1.display()
            ));
        }
    }
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_home(label: &str) -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let dir = std::env::temp_dir().join(format!("groket-hud-install-{label}-{n}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn desktop_entry_names_app_and_exec() {
        let exe = Path::new("/opt/groket/bin/groket-hud");
        let body = linux_desktop_entry(exe, APP_ID);
        assert!(body.contains("Name=Groket HUD"));
        assert!(body.contains("Exec=/opt/groket/bin/groket-hud"));
        assert!(body.contains(&format!("Icon={APP_ID}")));
        assert!(body.contains("Type=Application"));
    }

    #[test]
    fn shell_escape_quotes_spaces() {
        let p = Path::new("/home/user/My Apps/groket-hud");
        let s = shell_escape_path(p);
        assert!(s.starts_with('"'));
        assert!(s.contains("My Apps"));
    }

    #[test]
    fn ico_contains_png_signature() {
        let png = crate::brand::TRAY_32_PNG;
        let ico = ico_from_png_entries(&[(32, png)]).unwrap();
        assert_eq!(&ico[0..4], &[0, 0, 1, 0]); // reserved + type
        assert!(ico.windows(4).any(|w| w == b"\x89PNG"));
    }

    #[test]
    fn ico_rejects_empty() {
        assert!(ico_from_png_entries(&[]).is_err());
    }

    #[test]
    fn macos_plist_includes_bundle_id() {
        let with_icon = macos_info_plist(true);
        assert!(with_icon.contains(APP_ID));
        assert!(with_icon.contains("AppIcon"));
        let bare = macos_info_plist(false);
        assert!(!bare.contains("CFBundleIconFile"));
    }

    #[test]
    fn macos_launcher_execs_path() {
        let s = macos_launcher_script(Path::new("/tmp/groket-hud"));
        assert!(s.contains("#!/bin/sh"));
        assert!(s.contains("exec /tmp/groket-hud"));
    }

    #[test]
    fn windows_ps1_quotes_paths() {
        let s = windows_shortcut_ps1(
            Path::new(r"C:\Users\a\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\G.lnk"),
            Path::new(r"C:\tools\groket-hud.exe"),
            Path::new(r"C:\Users\a\AppData\Local\Groket\hud\g.ico"),
        );
        assert!(s.contains("CreateShortcut"));
        assert!(s.contains("groket-hud.exe"));
        assert!(s.contains(".ico"));
    }

    #[test]
    fn write_macos_bundle_into_temp_home() {
        let home = temp_home("mac");
        let exe = home.join("bin").join("groket-hud");
        fs::create_dir_all(exe.parent().unwrap()).unwrap();
        fs::write(&exe, b"#!/bin/sh\n").unwrap();
        let report = write_macos_app_bundle(&Layout {
            home: home.clone(),
            exe: exe.clone(),
            xdg_data_home: None,
        })
        .unwrap();
        let app = home.join("Applications").join("Groket HUD.app");
        assert!(app.join("Contents/Info.plist").is_file());
        assert!(app.join("Contents/MacOS/groket-hud").is_file());
        let plist = fs::read_to_string(app.join("Contents/Info.plist")).unwrap();
        assert!(plist.contains(APP_ID));
        assert!(!report.wrote.is_empty());
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn write_windows_files_into_temp_home() {
        let home = temp_home("win");
        let exe = home.join("groket-hud.exe");
        fs::write(&exe, b"MZ").unwrap();
        let report = write_windows_desktop_files(&Layout {
            home: home.clone(),
            exe,
            xdg_data_home: None,
        })
        .unwrap();
        let ico = home.join("AppData/Local/Groket/hud/groket-hud.ico");
        assert!(ico.is_file());
        assert!(ico.metadata().unwrap().len() > 64);
        let ps1 = home.join("AppData/Local/Groket/hud/create-start-menu-shortcut.ps1");
        assert!(ps1.is_file());
        assert!(report.wrote.iter().any(|p| p.ends_with("groket-hud.ico")));
        let _ = fs::remove_dir_all(&home);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn install_linux_writes_desktop_and_icons() {
        let home = temp_home("linux");
        let data = home.join("xdg-data");
        let exe = home.join("bin/groket-hud");
        fs::create_dir_all(exe.parent().unwrap()).unwrap();
        fs::write(&exe, b"#!/bin/sh\n").unwrap();
        let report = install(&Layout {
            home: home.clone(),
            exe: exe.clone(),
            xdg_data_home: Some(data.clone()),
        })
        .unwrap();
        let desktop = data.join("applications").join(format!("{APP_ID}.desktop"));
        assert!(desktop.is_file());
        let body = fs::read_to_string(&desktop).unwrap();
        assert!(body.contains(&exe.to_string_lossy().to_string()) || body.contains("groket-hud"));
        assert!(data
            .join("icons/hicolor/128x128/apps")
            .join(format!("{APP_ID}.png"))
            .is_file());
        assert!(data
            .join("icons/hicolor/256x256/apps")
            .join(format!("{APP_ID}.png"))
            .is_file());
        assert!(report.lines().iter().any(|l| l.starts_with("wrote ")));
        let _ = fs::remove_dir_all(&home);
    }

    #[test]
    fn report_lines_include_notes() {
        let r = Report {
            wrote: vec![PathBuf::from("/tmp/a")],
            notes: vec!["hello".into()],
        };
        let lines = r.lines();
        assert_eq!(lines[0], "wrote /tmp/a");
        assert_eq!(lines[1], "hello");
    }

    #[test]
    fn brand_desktop_icons_are_png() {
        for &(edge, bytes) in crate::brand::desktop_icon_pngs() {
            assert!(edge >= 16, "edge {edge}");
            assert_eq!(&bytes[1..4], b"PNG", "edge {edge}");
        }
        assert_eq!(crate::brand::APP_ICON_PNG[1..4], *b"PNG");
    }
}
