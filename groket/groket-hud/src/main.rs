//! groket-hud — iced desktop palette (control client).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    groket_hud::log::install_panic_hook();
    if std::env::args().skip(1).any(|a| a == "--install-desktop") {
        let code = match groket_hud::install_desktop::run_cli() {
            Ok(_) => 0,
            Err(err) => {
                eprintln!("groket-hud: {err}");
                1
            }
        };
        std::process::exit(code);
    }
    if std::env::args().skip(1).any(|a| a == "--help" || a == "-h") {
        eprintln!(
            "groket-hud — session palette (control client)\n\
             \n\
             Options:\n\
               --install-desktop   Write user-local icons and a launcher entry\n\
                                   (Linux .desktop, macOS ~/Applications app,\n\
                                   Windows Start Menu shortcut). No system package.\n\
               -h, --help          Show this help\n\
             \n\
             With no options, starts the HUD (tray + summon hotkey)."
        );
        std::process::exit(0);
    }
    #[cfg(target_os = "macos")]
    groket_hud::app::set_macos_accessory();
    let code = match groket_hud::run() {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("groket-hud: {err}");
            1
        }
    };
    // Tray and notify threads outlive the iced loop.
    std::process::exit(code);
}
