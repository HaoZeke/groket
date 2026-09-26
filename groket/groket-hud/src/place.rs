//! Center the palette on the display that currently has the pointer.

#[cfg(target_os = "macos")]
use iced::Point;

/// Axis-aligned rectangle in AppKit coordinates (origin bottom-left, y up).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    pub const fn new(x: f64, y: f64, w: f64, h: f64) -> Self {
        Self { x, y, w, h }
    }

    fn contains(self, px: f64, py: f64) -> bool {
        px >= self.x && px < self.x + self.w && py >= self.y && py < self.y + self.h
    }
}

/// Iced/winit logical top-left for a palette centered on the display under
/// ``mouse`` (AppKit points). ``frames[0]`` is the primary display; ``visible``
/// is the matching ``visibleFrame`` list (menu bar / dock excluded).
pub fn origin_on_pointer_display(
    frames: &[Rect],
    visible: &[Rect],
    mouse: (f64, f64),
    win_w: f64,
    win_h: f64,
) -> Option<(f32, f32)> {
    if frames.is_empty() || frames.len() != visible.len() {
        return None;
    }
    let idx = frames
        .iter()
        .position(|f| f.contains(mouse.0, mouse.1))
        .unwrap_or(0);
    let host = visible[idx];
    let primary_h = frames[0].h;
    let ax = host.x + (host.w - win_w) / 2.0;
    let ay = host.y + (host.h - win_h) / 2.0;
    let winit_y = primary_h - win_h - ay;
    Some((ax as f32, winit_y as f32))
}

/// Logical top-left for the active display, when the platform can report it.
#[cfg(target_os = "macos")]
pub fn active_palette_origin(win_w: f32, win_h: f32) -> Option<Point> {
    macos_origin(f64::from(win_w), f64::from(win_h)).map(|(x, y)| Point::new(x, y))
}

#[cfg(not(target_os = "macos"))]
pub fn active_palette_origin(_win_w: f32, _win_h: f32) -> Option<iced::Point> {
    None
}

#[cfg(target_os = "macos")]
fn macos_origin(win_w: f64, win_h: f64) -> Option<(f32, f32)> {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSEvent, NSScreen};

    let mtm = MainThreadMarker::new()?;
    let screens = NSScreen::screens(mtm);
    let count = screens.count();
    if count == 0 {
        return None;
    }
    let mouse = NSEvent::mouseLocation();
    let mut frames = Vec::with_capacity(count);
    let mut visible = Vec::with_capacity(count);
    for i in 0..count {
        let screen = screens.objectAtIndex(i);
        let f = screen.frame();
        let v = screen.visibleFrame();
        frames.push(Rect::new(
            f.origin.x,
            f.origin.y,
            f.size.width,
            f.size.height,
        ));
        visible.push(Rect::new(
            v.origin.x,
            v.origin.y,
            v.size.width,
            v.size.height,
        ));
    }
    origin_on_pointer_display(&frames, &visible, (mouse.x, mouse.y), win_w, win_h)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centers_on_primary_visible_frame() {
        let frames = [Rect::new(0.0, 0.0, 1440.0, 900.0)];
        let visible = [Rect::new(0.0, 0.0, 1440.0, 875.0)];
        let (x, y) =
            origin_on_pointer_display(&frames, &visible, (100.0, 100.0), 780.0, 560.0).unwrap();
        assert!((x - 330.0).abs() < 0.01);
        // winit y: primary_h - win_h - appkit_bottom
        assert!((y - (900.0 - 560.0 - 157.5) as f32).abs() < 0.01);
    }

    #[test]
    fn follows_pointer_onto_external_display() {
        let frames = [
            Rect::new(0.0, 0.0, 1440.0, 900.0),
            Rect::new(1440.0, -270.0, 2560.0, 1440.0),
        ];
        let visible = [
            Rect::new(0.0, 0.0, 1440.0, 875.0),
            Rect::new(1440.0, -270.0, 2560.0, 1440.0),
        ];
        let (x, y) =
            origin_on_pointer_display(&frames, &visible, (2000.0, 400.0), 780.0, 560.0).unwrap();
        assert!((x - (1440.0 + (2560.0 - 780.0) / 2.0) as f32).abs() < 0.01);
        let ay = -270.0 + (1440.0 - 560.0) / 2.0;
        assert!((y - (900.0 - 560.0 - ay) as f32).abs() < 0.01);
    }

    #[test]
    fn menu_bar_still_selects_that_display() {
        let frames = [Rect::new(0.0, 0.0, 1440.0, 900.0)];
        let visible = [Rect::new(0.0, 0.0, 1440.0, 875.0)];
        let on_menu = origin_on_pointer_display(&frames, &visible, (100.0, 890.0), 780.0, 560.0);
        let on_desktop = origin_on_pointer_display(&frames, &visible, (100.0, 100.0), 780.0, 560.0);
        assert_eq!(on_menu, on_desktop);
    }

    #[test]
    fn empty_or_mismatched_lists_are_none() {
        assert_eq!(
            origin_on_pointer_display(&[], &[], (0.0, 0.0), 780.0, 560.0),
            None
        );
        assert_eq!(
            origin_on_pointer_display(
                &[Rect::new(0.0, 0.0, 100.0, 100.0)],
                &[],
                (0.0, 0.0),
                10.0,
                10.0
            ),
            None
        );
    }
}
