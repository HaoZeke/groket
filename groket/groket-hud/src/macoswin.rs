//! macOS activation and window collection for overlay vs desktop chrome.

use iced::window::raw_window_handle::HasWindowHandle;
#[cfg(target_os = "macos")]
use iced::window::raw_window_handle::WindowHandle;

/// `NSWindowCollectionBehavior` bits (same values as AppKit).
pub const JOIN_ALL_SPACES: u64 = 1 << 0;
pub const MANAGED: u64 = 1 << 2;
pub const TRANSIENT: u64 = 1 << 3;
pub const PARTICIPATES_IN_CYCLE: u64 = 1 << 5;
pub const IGNORES_CYCLE: u64 = 1 << 6;
pub const FULL_SCREEN_AUXILIARY: u64 = 1 << 8;
pub const FULL_SCREEN_DISALLOWS_TILING: u64 = 1 << 12;

/// Overlay: transient card the tiler must not insert. Desktop: normal space
/// citizen so a tiler (yabai) will tile it.
pub fn collection_mask(overlay: bool) -> u64 {
    if overlay {
        JOIN_ALL_SPACES
            | TRANSIENT
            | IGNORES_CYCLE
            | FULL_SCREEN_AUXILIARY
            | FULL_SCREEN_DISALLOWS_TILING
    } else {
        MANAGED | PARTICIPATES_IN_CYCLE
    }
}

/// Apply overlay or desktop native chrome. Returns whether a window was found.
pub fn apply(handle: &dyn HasWindowHandle, overlay: bool) -> bool {
    let Ok(handle) = handle.window_handle() else {
        return false;
    };
    #[cfg(target_os = "macos")]
    let ok = apply_macos(handle, overlay);
    #[cfg(not(target_os = "macos"))]
    let ok = {
        let _ = (handle, overlay);
        true
    };
    ok
}

/// Accessory while the overlay is the only surface; Regular when popped out
/// so a tiler sees a normal application window.
pub fn set_desktop_app(desktop: bool) {
    #[cfg(target_os = "macos")]
    set_activation_macos(desktop);
    #[cfg(not(target_os = "macos"))]
    let _ = desktop;
}

/// Boot: accessory (no Dock / Command-Tab) until pop-out.
pub fn set_accessory_policy() {
    set_desktop_app(false);
}

#[cfg(target_os = "macos")]
fn apply_macos(handle: WindowHandle<'_>, overlay: bool) -> bool {
    use iced::window::raw_window_handle::RawWindowHandle;
    use objc2::runtime::AnyObject;
    use objc2_app_kit::{
        NSAccessibility, NSAccessibilityStandardWindowSubrole, NSAccessibilitySystemDialogSubrole,
        NSView, NSWindowCollectionBehavior,
    };

    let RawWindowHandle::AppKit(appkit) = handle.as_raw() else {
        return false;
    };
    let view = appkit.ns_view.as_ptr().cast::<AnyObject>();
    if view.is_null() {
        return false;
    }
    // SAFETY: iced/winit hands a live NSView on the main thread.
    let view = unsafe { &*view.cast::<NSView>() };
    let Some(window) = view.window() else {
        return false;
    };
    window.setCollectionBehavior(NSWindowCollectionBehavior(collection_mask(overlay) as _));
    // SAFETY: AppKit string constants are immutable process-lifetime data.
    let subrole = unsafe {
        if overlay {
            NSAccessibilitySystemDialogSubrole
        } else {
            NSAccessibilityStandardWindowSubrole
        }
    };
    window.setAccessibilitySubrole(Some(subrole));
    true
}

#[cfg(target_os = "macos")]
fn set_activation_macos(desktop: bool) {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSApplication, NSApplicationActivationPolicy};

    let Some(mtm) = MainThreadMarker::new() else {
        eprintln!("groket-hud: activation policy skipped (not on main thread)");
        return;
    };
    let app = NSApplication::sharedApplication(mtm);
    let policy = if desktop {
        NSApplicationActivationPolicy::Regular
    } else {
        NSApplicationActivationPolicy::Accessory
    };
    if !app.setActivationPolicy(policy) {
        eprintln!(
            "groket-hud: setActivationPolicy({}) failed",
            if desktop { "Regular" } else { "Accessory" }
        );
    }
    set_app_icon_macos(&app);
}

#[cfg(target_os = "macos")]
fn set_app_icon_macos(app: &objc2_app_kit::NSApplication) {
    use objc2::AnyThread;
    use objc2_app_kit::NSImage;
    use objc2_foundation::NSData;

    let data = NSData::with_bytes(crate::brand::APP_ICON_PNG);
    let Some(img) = NSImage::initWithData(NSImage::alloc(), &data) else {
        eprintln!("groket-hud: NSImage from groket app icon failed");
        return;
    };
    // SAFETY: NSApplication is on the main thread; icon is retained by AppKit.
    unsafe { app.setApplicationIconImage(Some(&img)) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overlay_mask_is_transient_and_not_managed() {
        let m = collection_mask(true);
        assert_ne!(m & TRANSIENT, 0);
        assert_ne!(m & IGNORES_CYCLE, 0);
        assert_ne!(m & JOIN_ALL_SPACES, 0);
        assert_eq!(m & MANAGED, 0);
        assert_ne!(m & FULL_SCREEN_DISALLOWS_TILING, 0);
    }

    #[test]
    fn desktop_mask_is_a_normal_tiled_window() {
        let m = collection_mask(false);
        assert_ne!(m & MANAGED, 0);
        assert_ne!(m & PARTICIPATES_IN_CYCLE, 0);
        assert_eq!(m & TRANSIENT, 0);
        assert_eq!(m & JOIN_ALL_SPACES, 0);
        assert_eq!(m & FULL_SCREEN_DISALLOWS_TILING, 0);
    }
}
