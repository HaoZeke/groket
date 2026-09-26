//! iced application: state, RPC, hotkey, live poll.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use iced::keyboard::{key::Named, Key, Modifiers as KeyMods};
use iced::widget::scrollable::{self, AbsoluteOffset};
use iced::widget::text_input;
use iced::window::{self, Mode};
use iced::{
    event, keyboard, time, Element, Event, Pixels, Point, Settings, Size, Subscription, Task, Theme,
};
use serde_json::{json, Value};

use crate::control::{self, ControlError};
use crate::format::{control_down_message, new_note_id};
use crate::fuzzy::fuzzy_filter;
use crate::live::{
    card_marks_from_overview, clamp_scroll, first_list_fetch, index_outside_visible,
    is_partial_list_page, is_soft_notes_save_error, merge_catalog_rows, merge_timeline_by_index,
    next_list_offset, notes_schema_fields, patch_catalog_delta, patch_list_row_from_meta,
    plan_tick, session_needs_live_poll, session_rpc_ref, should_continue_timeline,
    should_fetch_timeline, timeline_coverage_complete, timeline_first_missing_offset,
    timeline_seek_offset, TickInput, IDLE_POLL_MS, LIST_ROW_H, LIVE_POLL_MS, LIVE_TAIL_LIMIT,
    TIMELINE_CHUNK, TIMELINE_OVERSCAN, TIMELINE_ROW_H,
};
use crate::model::{KindFilter, NoteDraft, SchemaField, SessionRow, Tab};
use crate::place;
use crate::prefs;
use crate::shortcut;
use crate::theme;
use crate::view;
use crate::wire::{
    decode_overview, decode_session_list, decode_session_list_response, decode_timeline_page,
    NotesBlock, Overview, TimelineEvent,
};

const HUD_W: f32 = 780.0;
const HUD_H: f32 = 560.0;

#[derive(Debug, Clone)]
pub enum Message {
    SearchChanged(String),
    SelectSession(usize),
    SetTab(Tab),
    TimelineQuery(String),
    TimelineKind(KindFilter),
    JumpTimeline(i64),
    SelectTimeline(i64),
    LoadMoreTimeline,
    StartNote {
        turn: String,
        event: String,
    },
    OpenNote(String),
    ResetDraft,
    NoteField {
        id: String,
        value: String,
    },
    NoteTurn(String),
    SaveNote,
    RequestDelete(String),
    PopOutWindow,
    Hotkey,
    Tick,
    FocusSearch(u8),
    RawEvent(Event),
    Inited(Result<String, String>),
    ListLoaded {
        quiet: bool,
        result: Result<Value, String>,
    },
    ListPage {
        offset: u32,
        result: Result<Value, String>,
    },
    OverviewLoaded {
        gen: u64,
        sid: String,
        quiet: bool,
        result: Result<Value, String>,
    },
    TimelineLoaded {
        gen: u64,
        sid: String,
        offset: u32,
        append: bool,
        result: Result<Value, String>,
    },
    NoteSaved(Result<Value, String>),
    NoteDeleted {
        id: String,
        result: Result<Value, String>,
    },
    WindowId(Option<window::Id>),
    WindowPos(Option<Point>),
    X11Focus {
        xid: u64,
        attempt: u8,
    },
    Hide,
    MdLink(String),
    ListScroll {
        y: f32,
        height: f32,
    },
    TimelineScroll {
        y: f32,
        height: f32,
    },
    ContinueTimeline {
        sid: String,
        gen: u64,
    },
}

pub struct Hud {
    query: String,
    all_sessions: Vec<SessionRow>,
    sessions: Vec<SessionRow>,
    active: usize,
    tab: Tab,
    overview: Option<Overview>,
    overview_sid: String,
    overview_pending: String,
    overview_gen: u64,
    timeline: Vec<TimelineEvent>,
    timeline_sid: String,
    timeline_total: u32,
    timeline_next: u32,
    timeline_gen: u64,
    timeline_loading: bool,
    timeline_query: String,
    timeline_kind: KindFilter,
    timeline_focus: Option<i64>,
    note_draft: NoteDraft,
    note_compose_lock: bool,
    note_saving: bool,
    note_delete_armed: String,
    note_delete_until: Option<Instant>,
    status: String,
    status_err: bool,
    hotkey_hint: String,
    window_mode: bool,
    visible: bool,
    palette_live: bool,
    palette_origin: Option<Point>,
    last_live: Instant,
    typing_notes: bool,
    search_id: text_input::Id,
    tl_search_id: text_input::Id,
    theme_name: String,
    _hotkeys: Option<GlobalHotKeyManager>,
    notify_q: Arc<Mutex<VecDeque<(String, Value)>>>,
    window_id: Option<window::Id>,
    catalog_revision: i64,
    list_scroll_y: f32,
    list_view_h: f32,
    tl_scroll_y: f32,
    tl_view_h: f32,
    tl_scroll_id: scrollable::Id,
}

impl Default for Hud {
    fn default() -> Self {
        Self {
            query: String::new(),
            all_sessions: vec![],
            sessions: vec![],
            active: 0,
            tab: Tab::Overview,
            overview: None,
            overview_sid: String::new(),
            overview_pending: String::new(),
            overview_gen: 0,
            timeline: vec![],
            timeline_sid: String::new(),
            timeline_total: 0,
            timeline_next: 0,
            timeline_gen: 0,
            timeline_loading: false,
            timeline_query: String::new(),
            timeline_kind: KindFilter::All,
            timeline_focus: None,
            note_draft: NoteDraft::default(),
            note_compose_lock: false,
            note_saving: false,
            note_delete_armed: String::new(),
            note_delete_until: None,
            status: "connecting…".into(),
            status_err: false,
            hotkey_hint: shortcut::default_shortcut_label().into(),
            window_mode: false,
            visible: true,
            palette_live: true,
            palette_origin: None,
            last_live: Instant::now(),
            typing_notes: false,
            search_id: text_input::Id::new("search"),
            tl_search_id: text_input::Id::new("tl-search"),
            theme_name: prefs::theme_name(),
            _hotkeys: None,
            notify_q: Arc::new(Mutex::new(VecDeque::new())),
            catalog_revision: 0,
            window_id: None,
            list_scroll_y: 0.0,
            list_view_h: 400.0,
            tl_scroll_y: 0.0,
            tl_view_h: 400.0,
            tl_scroll_id: scrollable::Id::new("hud-timeline"),
        }
    }
}

fn linux_app_id(win: &mut window::Settings) {
    #[cfg(target_os = "linux")]
    {
        win.platform_specific.application_id = "dev.indynull.groket-hud".into();
    }
    let _ = win;
}

fn with_hud_icon(mut win: window::Settings) -> window::Settings {
    win.icon = crate::brand::window_icon();
    win
}

pub fn palette_window_settings() -> window::Settings {
    let mut win = with_hud_icon(window::Settings {
        size: Size::new(HUD_W, HUD_H),
        position: window::Position::Centered,
        min_size: Some(Size::new(HUD_W, HUD_H)),
        max_size: Some(Size::new(HUD_W, HUD_H)),
        resizable: false,
        decorations: false,
        visible: true,
        level: window::Level::AlwaysOnTop,
        exit_on_close_request: false,
        ..window::Settings::default()
    });
    linux_app_id(&mut win);
    #[cfg(target_os = "linux")]
    {
        // X11: unmapped by the tiler so the overlay stays a 780x560 card.
        win.platform_specific.override_redirect = true;
    }
    win
}

pub fn app_window_settings() -> window::Settings {
    let mut win = with_hud_icon(window::Settings {
        size: Size::new(980.0, 700.0),
        position: window::Position::Default,
        min_size: Some(Size::new(640.0, 440.0)),
        max_size: None,
        resizable: true,
        decorations: true,
        visible: true,
        level: window::Level::Normal,
        exit_on_close_request: false,
        ..window::Settings::default()
    });
    linux_app_id(&mut win);
    #[cfg(target_os = "linux")]
    {
        // Normal client: tiling WMs insert this; stacking WMs just map it.
        win.platform_specific.override_redirect = false;
    }
    win
}

pub fn run() -> iced::Result {
    crate::log::info(&format!("hud start log={}", crate::log::path().display()));
    iced::daemon("groket", Hud::update, Hud::view)
        .subscription(Hud::subscription)
        .theme(Hud::theme)
        .settings(Settings {
            id: Some("dev.indynull.groket-hud".into()),
            antialiasing: true,
            default_text_size: Pixels(f32::from(crate::typo::BODY)),
            default_font: crate::typo::UI,
            fonts: vec![
                std::borrow::Cow::Borrowed(crate::typo::UI_BYTES),
                std::borrow::Cow::Borrowed(crate::typo::MONO_BYTES),
            ],
        })
        .run_with(Hud::new)
}

#[cfg(target_os = "macos")]
pub fn set_macos_accessory() {
    crate::macoswin::set_accessory_policy();
}

#[cfg(not(target_os = "macos"))]
pub fn set_macos_accessory() {}

impl Hud {
    fn new() -> (Self, Task<Message>) {
        let mut hud = Hud::default();
        let (hk, label) = shortcut::resolve_summon_shortcut();
        hud.hotkey_hint = label.clone();
        match GlobalHotKeyManager::new() {
            Ok(mgr) => {
                if let Err(err) = mgr.register(hk) {
                    let msg = format!("failed to register shortcut {label}: {err}");
                    crate::log::error(&msg);
                    eprintln!("groket-hud: {msg}");
                } else {
                    eprintln!("groket-hud: summon shortcut {label}");
                }
                hud._hotkeys = Some(mgr);
            }
            Err(err) => {
                let msg = format!("global hotkey unavailable: {err}");
                crate::log::error(&msg);
                eprintln!("groket-hud: {msg}");
            }
        }
        let q = hud.notify_q.clone();
        let _ = control::spawn_notify_listener(move |method, params| {
            if let Ok(mut g) = q.lock() {
                g.push_back((method, params));
                if g.len() > 64 {
                    g.pop_front();
                }
            }
        });
        let (id, open) = window::open(palette_window_settings());
        hud.window_id = Some(id);
        let boot = Task::batch([
            open.map(|id| Message::WindowId(Some(id))),
            Task::perform(rpc(control::initialize), |r| {
                Message::Inited(r.map(|_| String::new()))
            }),
            fetch_list(false, 0),
        ]);
        (hud, boot)
    }

    fn theme(&self, _window: window::Id) -> Theme {
        theme::iced_theme(&self.theme_name)
    }

    fn subscription(&self) -> Subscription<Message> {
        let mut subs = vec![
            event::listen_with(interesting_hud_event),
            hotkey_subscription(),
            notify_subscription(),
            keyboard::on_key_press(|key, _mods| match key {
                Key::Named(Named::Escape) => Some(Message::Hide),
                _ => None,
            }),
        ];
        if self.visible {
            let any_live = session_needs_live_poll(
                &self.selected_status(),
                self.overview.as_ref().map(|o| &o.turns),
            ) || self
                .sessions
                .iter()
                .any(|r| session_needs_live_poll(&r.status, None));
            let poll = if any_live { LIVE_POLL_MS } else { IDLE_POLL_MS };
            subs.push(time::every(Duration::from_millis(poll)).map(|_| Message::Tick));
        }
        if self.note_delete_until.is_some() {
            subs.push(time::every(Duration::from_millis(250)).map(|_| Message::Tick));
        }
        Subscription::batch(subs)
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::SearchChanged(q) => {
                self.query = q;
                self.rerank_visible();
                Task::none()
            }
            Message::SelectSession(i) => {
                if i >= self.sessions.len() {
                    return self.focus_overlay();
                }
                let same = self.active == i && self.overview.is_some();
                self.active = i;
                if same {
                    return self.focus_overlay();
                }
                self.reset_detail_chrome();
                Task::batch([self.load_overview(false), self.focus_overlay()])
            }
            Message::SetTab(tab) => {
                self.tab = tab;
                let load = if tab == Tab::Timeline {
                    if let Some(sid) = self.selected_sid() {
                        self.ensure_timeline(sid, false)
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };
                Task::batch([load, self.focus_overlay()])
            }
            Message::TimelineQuery(q) => {
                self.timeline_query = q;
                if !self.timeline_query.trim().is_empty() && !self.timeline_complete() {
                    if let Some(sid) = self.selected_sid() {
                        return self.fill_timeline(sid);
                    }
                }
                Task::none()
            }
            Message::TimelineKind(k) => {
                self.timeline_kind = k;
                if k != KindFilter::All && !self.timeline_complete() {
                    if let Some(sid) = self.selected_sid() {
                        return self.fill_timeline(sid);
                    }
                }
                Task::none()
            }
            Message::JumpTimeline(ix) => {
                self.timeline_focus = Some(ix);
                self.tab = Tab::Timeline;
                self.timeline_query.clear();
                self.timeline_kind = KindFilter::All;
                let y = self
                    .timeline_focus_pos()
                    .map(|pos| pos as f32 * TIMELINE_ROW_H)
                    .unwrap_or(0.0);
                self.tl_scroll_y = y;
                let jump =
                    scrollable::scroll_to(self.tl_scroll_id.clone(), AbsoluteOffset { x: 0.0, y });
                if let Some(sid) = self.selected_sid() {
                    return Task::batch([jump, self.ensure_timeline(sid, true)]);
                }
                jump
            }
            Message::SelectTimeline(ix) => {
                if self.timeline_focus == Some(ix) {
                    self.timeline_focus = None;
                } else {
                    self.timeline_focus = Some(ix);
                }
                Task::none()
            }
            Message::LoadMoreTimeline => self.load_more_timeline(),
            Message::StartNote { turn, event } => {
                self.note_draft = NoteDraft {
                    id: String::new(),
                    turn_index: turn,
                    event_index: event,
                    fields: vec![],
                };
                self.note_compose_lock = true;
                self.typing_notes = true;
                self.tab = Tab::Notes;
                Task::none()
            }
            Message::OpenNote(nid) => {
                self.open_note(&nid);
                Task::none()
            }
            Message::ResetDraft => {
                self.note_draft = NoteDraft::default();
                self.note_compose_lock = false;
                self.typing_notes = false;
                self.note_saving = false;
                Task::none()
            }
            Message::NoteField { id, value } => {
                self.note_draft.set_field(&id, value);
                self.note_compose_lock = true;
                self.typing_notes = true;
                Task::none()
            }
            Message::NoteTurn(v) => {
                self.note_draft.turn_index = v;
                self.note_compose_lock = true;
                Task::none()
            }
            Message::SaveNote => self.save_note(),
            Message::RequestDelete(nid) => self.request_delete(nid),
            Message::PopOutWindow => self.pop_out_window(),
            Message::Hotkey => self.on_hotkey(),
            Message::ListScroll { y, height } => {
                if height > 1.0 {
                    self.list_view_h = height;
                }
                let content = self.sessions.len() as f32 * LIST_ROW_H;
                self.list_scroll_y = clamp_scroll(y, content, self.list_view_h);
                Task::none()
            }
            Message::TimelineScroll { y, height } => {
                if height > 1.0 {
                    self.tl_view_h = height;
                }
                // Iced's scrollable owns the pixel range (cards wrap). Do not
                // clamp to n * TIMELINE_ROW_H or the thumb fights real height.
                self.tl_scroll_y = y.max(0.0);
                if let Some(pos) = self.timeline_focus_pos() {
                    if index_outside_visible(
                        self.tl_scroll_y,
                        self.tl_view_h,
                        TIMELINE_ROW_H,
                        self.filtered_timeline().len(),
                        TIMELINE_OVERSCAN,
                        pos,
                    ) {
                        self.timeline_focus = None;
                    }
                }
                Task::none()
            }
            Message::ContinueTimeline { sid, gen } => {
                if gen != self.timeline_gen {
                    return Task::none();
                }
                self.fill_timeline(sid)
            }
            Message::Tick => self.on_tick(),
            Message::FocusSearch(attempt) => self.on_focus_search(attempt),
            Message::RawEvent(ev) => self.on_event(ev),
            Message::Inited(Ok(_)) => {
                self.mark_up();
                self.status = format!(
                    "ready · {}",
                    control::default_socket_path()
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("control.sock")
                );
                Task::none()
            }
            Message::Inited(Err(e)) => {
                self.mark_down(&e);
                Task::none()
            }
            Message::ListLoaded { quiet, result } => match result {
                Ok(v) => {
                    if quiet {
                        if let Ok(page) = decode_session_list_response(&v) {
                            if !page.unchanged
                                && !page.delta
                                && page.matched > page.sessions.len() as i64
                            {
                                return fetch_list(quiet, 0);
                            }
                        }
                        self.apply_list(v, quiet);
                        return Task::none();
                    }
                    self.apply_list(v.clone(), quiet);
                    let more = self.continue_catalog_pages(&v);
                    if self.sessions.is_empty() {
                        more
                    } else if self.overview.is_none() {
                        Task::batch([more, self.load_overview(false)])
                    } else {
                        more
                    }
                }
                Err(e) => {
                    self.mark_down(&e);
                    Task::none()
                }
            },
            Message::ListPage { offset: _, result } => match result {
                Ok(v) => {
                    let before = self.all_sessions.len();
                    self.apply_list(v.clone(), true);
                    if self.all_sessions.len() <= before {
                        Task::none()
                    } else {
                        self.continue_catalog_pages(&v)
                    }
                }
                Err(e) => {
                    self.mark_down(&e);
                    Task::none()
                }
            },
            Message::OverviewLoaded {
                gen,
                sid,
                quiet,
                result,
            } => {
                if gen != self.overview_gen {
                    return Task::none();
                }
                match result {
                    Ok(data) => {
                        let ov = match decode_overview(&data) {
                            Ok(o) => o,
                            Err(e) => {
                                self.mark_down(&e);
                                return Task::none();
                            }
                        };
                        patch_list_row_from_meta(&mut self.all_sessions, &sid, &ov.meta);
                        patch_list_row_from_meta(&mut self.sessions, &sid, &ov.meta);
                        if !quiet {
                            self.status = format!("{sid} · {}", ov.meta.status);
                        }
                        self.overview = Some(ov);
                        self.overview_sid = sid.clone();
                        self.overview_pending.clear();
                        self.mark_up();
                        if should_fetch_timeline(self.tab == Tab::Timeline) {
                            return Task::batch([
                                self.ensure_timeline(sid, false),
                                if quiet {
                                    Task::none()
                                } else {
                                    self.focus_overlay()
                                },
                            ]);
                        }
                        if !quiet {
                            return self.focus_overlay();
                        }
                    }
                    Err(e) => {
                        if !quiet {
                            self.overview = None;
                            self.overview_sid.clear();
                            self.overview_pending.clear();
                        }
                        self.mark_down(&e);
                    }
                }
                Task::none()
            }
            Message::TimelineLoaded {
                gen,
                sid,
                offset,
                append,
                result,
            } => {
                if gen != self.timeline_gen {
                    return Task::none();
                }
                self.timeline_loading = false;
                match result {
                    Ok(data) => {
                        let page = match decode_timeline_page(&data) {
                            Ok(p) => p,
                            Err(e) => {
                                self.mark_down(&e);
                                return Task::none();
                            }
                        };
                        let batch = page.events;
                        let total = if page.total > 0 {
                            page.total
                        } else {
                            self.timeline_total
                        };
                        let added =
                            if append && self.timeline_sid == sid && !self.timeline.is_empty() {
                                let merged = merge_timeline_by_index(&self.timeline, &batch);
                                let n = merged.added;
                                self.timeline = merged.events;
                                n
                            } else {
                                self.timeline = batch.clone();
                                batch.len()
                            };
                        self.timeline_sid = sid.clone();
                        self.timeline_total = total;
                        self.timeline_next = self
                            .timeline_next
                            .max(offset.saturating_add(batch.len() as u32));
                        if self.timeline_total > 0 {
                            self.timeline_next = self.timeline_next.min(self.timeline_total);
                        }
                        self.mark_up();
                        if should_continue_timeline(
                            self.tab == Tab::Timeline,
                            self.timeline_complete(),
                            self.timeline_loading,
                        ) && added > 0
                        {
                            return queue_timeline_continue(sid, self.timeline_gen);
                        }
                    }
                    Err(e) => self.mark_down(&e),
                }
                Task::none()
            }
            Message::NoteSaved(result) => {
                self.note_saving = false;
                match result {
                    Ok(snap) => {
                        self.apply_notes_snapshot(&snap);
                        self.note_draft = NoteDraft::default();
                        self.note_compose_lock = false;
                        self.typing_notes = false;
                        self.mark_up();
                        self.status = "Note saved".into();
                    }
                    Err(e) => {
                        if !is_soft_notes_save_error(&e) {
                            self.mark_down(&e);
                        } else {
                            crate::log::error(&format!("note save (soft): {e}"));
                        }
                        self.status = format!("Note save failed: {e}");
                        self.status_err = true;
                    }
                }
                Task::none()
            }
            Message::NoteDeleted { id, result } => {
                match result {
                    Ok(snap) => {
                        self.apply_notes_snapshot(&snap);
                        if self.note_draft.id == id {
                            self.note_draft = NoteDraft::default();
                            self.note_compose_lock = false;
                        }
                        self.mark_up();
                        self.status = "Note deleted".into();
                    }
                    Err(e) => {
                        if !is_soft_notes_save_error(&e) {
                            self.mark_down(&e);
                        } else {
                            crate::log::error(&format!("note delete (soft): {e}"));
                        }
                        self.status = format!("Note delete failed: {e}");
                        self.status_err = true;
                    }
                }
                Task::none()
            }
            Message::WindowId(id) => {
                let Some(id) = id else {
                    return Task::none();
                };
                self.window_id = Some(id);
                let mut tasks = vec![delayed_focus(0), self.apply_native_chrome(id)];
                if !self.window_mode {
                    tasks.push(self.place_overlay(id));
                }
                Task::batch(tasks)
            }
            Message::WindowPos(pos) => {
                if let Some(p) = pos {
                    if p.x.abs() > 8.0 || p.y.abs() > 8.0 {
                        self.palette_origin = Some(p);
                    }
                }
                Task::none()
            }
            Message::X11Focus { xid, attempt } => self.after_x11_focus(xid, attempt),
            Message::Hide => self.hide_palette(),
            Message::MdLink(url) => {
                self.status = url;
                self.status_err = false;
                Task::none()
            }
        }
    }

    fn view(&self, _window: window::Id) -> Element<'_, Message> {
        view::layout(self)
    }
}

impl Hud {
    pub fn query(&self) -> &str {
        &self.query
    }
    pub fn sessions(&self) -> &[SessionRow] {
        &self.sessions
    }
    pub fn active(&self) -> usize {
        self.active
    }
    pub fn tab(&self) -> Tab {
        self.tab
    }
    pub fn overview(&self) -> Option<&Overview> {
        self.overview.as_ref()
    }
    pub fn overview_sid(&self) -> &str {
        &self.overview_sid
    }
    pub fn overview_pending(&self) -> &str {
        &self.overview_pending
    }
    pub fn timeline_query(&self) -> &str {
        &self.timeline_query
    }
    pub fn timeline_kind(&self) -> KindFilter {
        self.timeline_kind
    }
    pub fn timeline_focus(&self) -> Option<i64> {
        self.timeline_focus
    }

    pub fn timeline_events(&self) -> &[crate::wire::TimelineEvent] {
        &self.timeline
    }
    pub fn timeline_loading(&self) -> bool {
        self.timeline_loading
    }
    pub fn note_draft(&self) -> &NoteDraft {
        &self.note_draft
    }
    pub fn note_saving(&self) -> bool {
        self.note_saving
    }
    pub fn note_delete_armed(&self) -> &str {
        &self.note_delete_armed
    }
    pub fn status(&self) -> &str {
        &self.status
    }
    pub fn status_err(&self) -> bool {
        self.status_err
    }
    pub fn hotkey_hint(&self) -> &str {
        &self.hotkey_hint
    }
    pub fn window_mode(&self) -> bool {
        self.window_mode
    }
    pub fn theme_name(&self) -> &str {
        &self.theme_name
    }
    pub fn tokens(&self) -> crate::theme::Tokens {
        crate::theme::tokens(&self.theme_name)
    }
    pub fn search_id(&self) -> text_input::Id {
        self.search_id.clone()
    }
    pub fn tl_search_id(&self) -> text_input::Id {
        self.tl_search_id.clone()
    }
    pub fn notes_schema(&self) -> Vec<SchemaField> {
        notes_schema_fields(self.overview.as_ref())
    }
    pub fn filtered_timeline(&self) -> Vec<&TimelineEvent> {
        if self.timeline_sid != self.overview_sid {
            return vec![];
        }
        let mut out: Vec<&TimelineEvent> = self
            .timeline
            .iter()
            .filter(|ev| ev.matches_kind(self.timeline_kind))
            .collect();
        let needle = self.timeline_query.trim();
        if needle.is_empty() {
            return out;
        }
        let owned: Vec<TimelineEvent> = out.iter().map(|v| (*v).clone()).collect();
        let filtered = fuzzy_filter(needle, &owned, TimelineEvent::haystack);
        let want: std::collections::HashSet<i64> = filtered.iter().map(|e| e.index).collect();
        out.retain(|e| want.contains(&e.index));
        out
    }
    pub fn timeline_meta(&self) -> String {
        if self.overview_sid.is_empty() {
            return String::new();
        }
        let shown = self.filtered_timeline().len();
        if self.timeline_complete() {
            format!("{shown}")
        } else {
            format!("{shown}+ · {}/{}", self.timeline.len(), self.timeline_total)
        }
    }
    pub fn card_marks(
        &self,
    ) -> (
        std::collections::HashMap<i64, crate::live::CardMark>,
        std::collections::HashMap<i64, crate::live::CardMark>,
    ) {
        match &self.overview {
            Some(o) => card_marks_from_overview(o),
            None => (Default::default(), Default::default()),
        }
    }

    pub fn timeline_complete(&self) -> bool {
        !self.timeline_sid.is_empty()
            && timeline_coverage_complete(self.timeline.len(), self.timeline_total)
    }

    fn selected_sid(&self) -> Option<String> {
        self.sessions
            .get(self.active)
            .map(|r| r.session_id.clone())
            .filter(|s| !s.is_empty())
    }

    fn selected_rpc_ref(&self) -> Option<String> {
        let row = self.sessions.get(self.active)?;
        let r = session_rpc_ref(&row.path, &row.session_id);
        if r.is_empty() {
            None
        } else {
            Some(r)
        }
    }

    fn overview_rpc_ref(&self) -> String {
        if let Some(o) = &self.overview {
            let r = session_rpc_ref(&o.meta.path, &self.overview_sid);
            if !r.is_empty() {
                return r;
            }
        }
        self.selected_rpc_ref()
            .or_else(|| self.selected_sid())
            .unwrap_or_default()
    }

    pub fn list_scroll_y(&self) -> f32 {
        self.list_scroll_y
    }

    pub fn tl_scroll_y(&self) -> f32 {
        self.tl_scroll_y
    }

    pub fn tl_view_h(&self) -> f32 {
        self.tl_view_h
    }

    pub fn timeline_scroll_id(&self) -> scrollable::Id {
        self.tl_scroll_id.clone()
    }

    pub fn timeline_focus_pos(&self) -> Option<usize> {
        let focus = self.timeline_focus?;
        self.filtered_timeline()
            .iter()
            .position(|ev| ev.index == focus)
    }

    fn ensure_active_visible(&mut self) -> Task<Message> {
        let view_h = self.list_view_h.max(80.0);
        let top = self.active as f32 * LIST_ROW_H;
        let bot = top + LIST_ROW_H;
        let mut y = self.list_scroll_y;
        if top < y {
            y = top;
        } else if bot > y + view_h {
            y = (bot - view_h).max(0.0);
        }
        self.list_scroll_y = y;
        Task::none()
    }

    fn selected_status(&self) -> String {
        if let Some(o) = &self.overview {
            let s = o.meta.status_label();
            if !s.is_empty() {
                return s;
            }
        }
        self.sessions
            .get(self.active)
            .map(|r| r.status.clone())
            .unwrap_or_default()
    }

    fn mark_up(&mut self) {
        self.status_err = false;
    }

    fn mark_down(&mut self, err: &str) {
        crate::log::error(err);
        self.status_err = true;
        self.status = control_down_message(err);
    }

    fn reset_detail_chrome(&mut self) {
        self.tab = Tab::Overview;
        self.timeline_query.clear();
        self.timeline_kind = KindFilter::All;
        self.timeline.clear();
        self.timeline_sid.clear();
        self.timeline_total = 0;
        self.timeline_next = 0;
        self.tl_scroll_y = 0.0;
        self.timeline_gen += 1;
        self.timeline_focus = None;
        self.note_draft = NoteDraft::default();
        self.note_compose_lock = false;
        self.typing_notes = false;
        self.overview = None;
        self.overview_sid.clear();
    }

    fn apply_list(&mut self, listed: Value, quiet: bool) {
        let page = decode_session_list_response(&listed).ok();
        if let Some(ref page) = page {
            if page.revision > 0 {
                self.catalog_revision = page.revision;
            }
            if quiet && page.unchanged {
                self.mark_up();
                return;
            }
            if page.delta {
                let incoming = page.sessions.clone();
                self.all_sessions =
                    patch_catalog_delta(&self.all_sessions, incoming, &page.removed);
                self.rerank_visible();
                self.mark_up();
                if !quiet {
                    self.status = format!("{} sessions · ready", self.all_sessions.len());
                }
                return;
            }
        }
        let incoming = page
            .as_ref()
            .map(|p| p.sessions.clone())
            .unwrap_or_else(|| decode_session_list(&listed).unwrap_or_default());
        let matched = page.as_ref().map(|p| p.matched).unwrap_or(0);
        let incomplete = page.as_ref().is_some_and(|p| p.incomplete || p.building);
        let delta = page.as_ref().is_some_and(|p| p.delta);
        if !self.all_sessions.is_empty()
            && is_partial_list_page(
                incoming.len(),
                matched,
                delta,
                page.as_ref().is_some_and(|p| p.incomplete),
                page.as_ref().is_some_and(|p| p.building),
            )
        {
            if incoming.is_empty() {
                return;
            }
            self.all_sessions = patch_catalog_delta(&self.all_sessions, incoming, &[]);
            self.rerank_visible();
            self.mark_up();
            return;
        }
        let rows = merge_catalog_rows(&self.all_sessions, incoming);
        if (quiet || incomplete) && rows.is_empty() && !self.all_sessions.is_empty() {
            return;
        }
        self.all_sessions = rows;
        self.rerank_visible();
        self.mark_up();
        if !quiet {
            if self.sessions.is_empty() {
                self.status = if self.query.trim().is_empty() {
                    self.status_err = true;
                    crate::log::error("no sessions from control");
                    "No sessions from control · is groket serve running?".into()
                } else {
                    format!("No matches for “{}”", self.query.trim())
                };
            } else {
                self.status = format!("{} sessions · ready", self.all_sessions.len());
            }
        }
    }

    fn continue_catalog_pages(&self, listed: &Value) -> Task<Message> {
        let Ok(page) = decode_session_list_response(listed) else {
            return Task::none();
        };
        if page.delta || page.unchanged {
            return Task::none();
        }
        match next_list_offset(
            self.all_sessions.len(),
            first_list_fetch().0,
            page.matched,
            page.incomplete || page.building,
        ) {
            Some(offset) => fetch_list_page(offset),
            None => Task::none(),
        }
    }

    fn rerank_visible(&mut self) {
        let keep = self
            .sessions
            .get(self.active)
            .map(|r| r.session_id.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| self.overview_sid.clone());
        let mut ranked = if self.query.trim().is_empty() {
            self.all_sessions.clone()
        } else {
            fuzzy_filter(self.query.trim(), &self.all_sessions, SessionRow::haystack)
        };
        ranked.sort_by(|a, b| {
            b.sort_epoch
                .partial_cmp(&a.sort_epoch)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.session_id.cmp(&b.session_id))
        });
        self.sessions = ranked;
        if !keep.is_empty() {
            if let Some(idx) = self.sessions.iter().position(|r| r.session_id == keep) {
                self.active = idx;
            } else {
                self.active = 0;
            }
        } else if self.active >= self.sessions.len() {
            self.active = self.sessions.len().saturating_sub(1);
        }
        let content = self.sessions.len() as f32 * LIST_ROW_H;
        self.list_scroll_y = clamp_scroll(self.list_scroll_y, content, self.list_view_h.max(1.0));
    }

    fn load_overview(&mut self, quiet: bool) -> Task<Message> {
        let Some(sid) = self.selected_sid() else {
            self.overview = None;
            self.overview_sid.clear();
            self.overview_pending.clear();
            return Task::none();
        };
        let Some(rpc_ref) = self.selected_rpc_ref() else {
            return Task::none();
        };
        self.overview_pending = sid.clone();
        self.overview_gen += 1;
        let gen = self.overview_gen;
        Task::perform(
            rpc(move || control::session_overview(&rpc_ref)),
            move |result| Message::OverviewLoaded {
                gen,
                sid: sid.clone(),
                quiet,
                result,
            },
        )
    }

    fn ensure_timeline(&mut self, sid: String, force: bool) -> Task<Message> {
        if !force && self.timeline_sid == sid && !self.timeline.is_empty() && !self.timeline_loading
        {
            return Task::none();
        }
        self.timeline_gen += 1;
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        if force || self.timeline_sid != sid {
            self.timeline.clear();
            self.timeline_total = 0;
            self.timeline_next = 0;
            self.tl_scroll_y = 0.0;
        }
        let seed = self
            .timeline_focus
            .map(|i| timeline_seek_offset(i, 20))
            .unwrap_or(0);
        let rpc_ref = self.overview_rpc_ref();
        fetch_timeline(rpc_ref, sid, seed, false, gen, 120)
    }

    fn fill_timeline(&mut self, sid: String) -> Task<Message> {
        if self.timeline_complete() || self.timeline_loading {
            return Task::none();
        }
        let off = timeline_first_missing_offset(&self.timeline, self.timeline_total);
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        let rpc_ref = self.overview_rpc_ref();
        fetch_timeline(rpc_ref, sid, off, true, gen, TIMELINE_CHUNK)
    }

    fn load_more_timeline(&mut self) -> Task<Message> {
        if self.tab != Tab::Timeline || self.timeline_loading || self.timeline_complete() {
            return Task::none();
        }
        let Some(sid) = self.selected_sid() else {
            return Task::none();
        };
        if self.timeline_sid != sid {
            return Task::none();
        }
        self.fill_timeline(sid)
    }

    fn refresh_timeline_tail(&mut self, sid: String) -> Task<Message> {
        if self.timeline_loading {
            return Task::none();
        }
        if self.timeline_sid.is_empty() {
            return self.ensure_timeline(sid, false);
        }
        if self.timeline_sid != sid {
            return Task::none();
        }
        let gen = self.timeline_gen;
        let offset = self.timeline_next.saturating_sub(4);
        let rpc_ref = self.overview_rpc_ref();
        fetch_timeline(rpc_ref, sid, offset, true, gen, LIVE_TAIL_LIMIT)
    }

    fn open_note(&mut self, nid: &str) {
        let Some(o) = &self.overview else {
            self.tab = Tab::Notes;
            return;
        };
        let Some(n) = o.notes.notes.iter().find(|r| r.id == nid) else {
            self.tab = Tab::Notes;
            return;
        };
        let mut fields = Vec::new();
        if let Some(map) = n.fields.as_object() {
            for (k, v) in map {
                fields.push((
                    k.clone(),
                    match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    },
                ));
            }
        }
        self.note_draft = NoteDraft {
            id: nid.to_string(),
            turn_index: n.turn_index.map(|i| i.to_string()).unwrap_or_default(),
            event_index: n
                .event_indices
                .first()
                .map(|x| x.to_string())
                .unwrap_or_default(),
            fields,
        };
        self.note_compose_lock = true;
        self.tab = Tab::Notes;
    }

    fn save_note(&mut self) -> Task<Message> {
        let sid = self.overview_rpc_ref();
        let Some(o) = &self.overview else {
            self.status = "Select a session before saving a note".into();
            return Task::none();
        };
        if sid.is_empty() {
            self.status = "Select a session before saving a note".into();
            return Task::none();
        }
        if !self.note_draft.has_content() {
            self.status = "Enter a note field before saving".into();
            return Task::none();
        }
        let rev = o.notes.revision.clone();
        let mut id = self.note_draft.id.trim().to_string();
        if id.is_empty() {
            id = new_note_id();
        }
        let mut turn_index = 0i64;
        let turn_raw = self.note_draft.turn_index.trim();
        if !turn_raw.is_empty() {
            match turn_raw.parse::<i64>() {
                Ok(n) if n >= 0 => turn_index = n,
                _ => {
                    self.status = "Turn must be a non-negative integer".into();
                    return Task::none();
                }
            }
        }
        let prev = o.notes.notes.iter().find(|n| n.id == id);
        let mut fields = json!({});
        if let Some(p) = prev {
            if let Some(obj) = p.fields.as_object() {
                fields = Value::Object(obj.clone());
            }
        }
        if let Some(map) = fields.as_object_mut() {
            for (k, v) in &self.note_draft.fields {
                map.insert(k.clone(), json!(v));
            }
        }
        let mut event_indices = prev
            .map(|p| json!(p.event_indices.clone()))
            .unwrap_or_else(|| json!([]));
        if prev.is_none() && !self.note_draft.event_index.trim().is_empty() {
            if let Ok(n) = self.note_draft.event_index.trim().parse::<i64>() {
                event_indices = json!([n]);
            }
        }
        let note = json!({
            "id": id,
            "turnIndex": turn_index,
            "fields": fields,
            "eventIndices": event_indices,
        });
        self.note_saving = true;
        Task::perform(
            rpc(move || control::notes_upsert(&sid, note, &rev)),
            Message::NoteSaved,
        )
    }

    fn request_delete(&mut self, nid: String) -> Task<Message> {
        if nid.is_empty() {
            return Task::none();
        }
        if self.note_delete_armed == nid {
            self.note_delete_armed.clear();
            self.note_delete_until = None;
            return self.delete_note(nid);
        }
        self.note_delete_armed = nid;
        self.note_delete_until = Some(Instant::now() + Duration::from_millis(2500));
        self.status = "Press Delete again to confirm".into();
        Task::none()
    }

    fn delete_note(&mut self, nid: String) -> Task<Message> {
        let sid = self.overview_rpc_ref();
        let Some(o) = &self.overview else {
            return Task::none();
        };
        let rev = o.notes.revision.clone();
        Task::perform(
            rpc({
                let id = nid.clone();
                move || control::notes_delete(&sid, &id, &rev)
            }),
            move |result| Message::NoteDeleted {
                id: nid.clone(),
                result,
            },
        )
    }

    fn apply_notes_snapshot(&mut self, snap: &Value) {
        let Some(o) = self.overview.as_mut() else {
            return;
        };
        if let Some(block) = NotesBlock::from_control_snapshot(snap, &o.notes) {
            o.notes = block;
        }
    }

    fn win_task(&self, f: impl FnOnce(window::Id) -> Task<Message>) -> Task<Message> {
        match self.window_id {
            Some(id) => f(id),
            None => Task::none(),
        }
    }

    fn place_overlay(&self, id: window::Id) -> Task<Message> {
        if let Some(origin) = place::active_palette_origin(HUD_W, HUD_H) {
            window::move_to(id, origin)
        } else if let Some(origin) = self.palette_origin {
            window::move_to(id, origin)
        } else {
            window::get_position(id).map(Message::WindowPos)
        }
    }

    fn apply_native_chrome(&self, id: window::Id) -> Task<Message> {
        let overlay = !self.window_mode;
        window::run_with_handle(id, move |handle| {
            if !crate::macoswin::apply(handle, overlay) {
                eprintln!("groket-hud: native chrome apply missed the window");
            }
        })
        .discard()
    }

    fn pop_out_window(&mut self) -> Task<Message> {
        if self.window_mode {
            return Task::none();
        }
        self.window_mode = true;
        self.visible = true;
        self.palette_live = true;
        crate::macoswin::set_desktop_app(true);
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let old = self.window_id.take();
        let (id, open) = window::open(app_window_settings());
        self.window_id = Some(id);
        let close_old = match old {
            Some(prev) if prev != id => window::close(prev),
            _ => Task::none(),
        };
        Task::batch([open.map(|id| Message::WindowId(Some(id))), close_old])
    }

    fn dismiss_window(&mut self) -> Task<Message> {
        self.window_mode = false;
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let close = match self.window_id.take() {
            Some(id) => window::close(id),
            None => Task::none(),
        };
        crate::macoswin::set_desktop_app(false);
        close
    }

    fn hide_palette(&mut self) -> Task<Message> {
        if self.window_mode {
            return Task::none();
        }
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        match self.window_id {
            Some(id) => window::change_mode(id, Mode::Hidden),
            None => Task::none(),
        }
    }

    fn show_palette(&mut self) -> Task<Message> {
        self.window_mode = false;
        self.visible = true;
        self.palette_live = true;
        self.last_live = Instant::now();
        self.sync_theme();
        if self.window_id.is_none() {
            let (id, open) = window::open(palette_window_settings());
            self.window_id = Some(id);
            return Task::batch([
                open.map(|id| Message::WindowId(Some(id))),
                delayed_focus(0),
                fetch_list(true, self.catalog_revision),
            ]);
        }
        let chrome = match self.window_id {
            Some(id) => Task::batch([
                window::change_mode(id, Mode::Windowed),
                window::change_level(id, window::Level::AlwaysOnTop),
                window::resize(id, Size::new(HUD_W, HUD_H)),
                self.apply_native_chrome(id),
                self.place_overlay(id),
            ]),
            None => Task::none(),
        };
        Task::batch([
            chrome,
            delayed_focus(0),
            fetch_list(true, self.catalog_revision),
        ])
    }

    fn sync_theme(&mut self) {
        let name = prefs::theme_name();
        if name != self.theme_name {
            self.theme_name = name;
        }
    }

    fn x11_focus_only(&self, attempt: u8) -> Task<Message> {
        let Some(id) = self.window_id else {
            return Task::none();
        };
        #[cfg(target_os = "linux")]
        {
            Task::batch([
                window::gain_focus(id),
                window::get_raw_id::<Message>(id)
                    .map(move |xid| Message::X11Focus { xid, attempt }),
            ])
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = attempt;
            window::gain_focus(id)
        }
    }

    fn focus_overlay(&self) -> Task<Message> {
        self.on_focus_search(0)
    }

    fn on_focus_search(&self, attempt: u8) -> Task<Message> {
        if !self.visible {
            return Task::none();
        }
        let input = if self.note_compose_lock {
            Task::none()
        } else {
            text_input::focus(self.search_id.clone())
        };
        Task::batch([self.x11_focus_only(attempt), input])
    }

    fn after_x11_focus(&self, xid: u64, attempt: u8) -> Task<Message> {
        #[cfg(target_os = "linux")]
        {
            if !crate::x11focus::focus_window(xid) && attempt < 8 {
                return delayed_focus(attempt.saturating_add(1));
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (xid, attempt);
        }
        Task::none()
    }

    fn on_hotkey(&mut self) -> Task<Message> {
        if self.visible && !self.window_mode {
            self.hide_palette()
        } else if self.visible && self.window_mode {
            self.win_task(window::gain_focus)
        } else {
            self.show_palette()
        }
    }

    fn on_tick(&mut self) -> Task<Message> {
        self.sync_theme();
        if let Some(until) = self.note_delete_until {
            if Instant::now() >= until {
                self.note_delete_armed.clear();
                self.note_delete_until = None;
                self.status = "Delete cancelled".into();
            }
        }
        let mut cmds = Vec::new();
        let notifies: Vec<(String, Value)> = if let Ok(mut g) = self.notify_q.lock() {
            g.drain(..).collect()
        } else {
            vec![]
        };
        let notify_pairs: Vec<(String, String)> = notifies
            .iter()
            .map(|(method, params)| {
                let sid = params
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                (method.clone(), sid)
            })
            .collect();
        let selected = self.selected_sid().unwrap_or_default();
        let live = session_needs_live_poll(
            &self.selected_status(),
            self.overview.as_ref().map(|o| &o.turns),
        );
        let any_live = live
            || self
                .sessions
                .iter()
                .any(|r| session_needs_live_poll(&r.status, None));
        let elapsed = self.last_live.elapsed().as_millis() as u64;
        let plan = plan_tick(TickInput {
            notifies: &notify_pairs,
            selected_sid: &selected,
            overview_sid: &self.overview_sid,
            palette_live: self.palette_live && self.visible,
            list_elapsed_ms: elapsed,
            selected_live: live,
            any_live,
            on_timeline: self.tab == Tab::Timeline,
            notes_locked: self.note_compose_lock,
        });
        if plan.fetch_list {
            cmds.push(fetch_list(true, self.catalog_revision));
        }
        if plan.load_overview {
            cmds.push(self.load_overview(true));
        }
        if plan.refresh_timeline {
            if let Some(sid) = self.selected_sid() {
                cmds.push(self.refresh_timeline_tail(sid));
            }
        }
        if plan.fetch_list || plan.load_overview || plan.refresh_timeline {
            self.last_live = Instant::now();
        }
        Task::batch(cmds)
    }

    fn on_event(&mut self, ev: Event) -> Task<Message> {
        match ev {
            Event::Window(window::Event::CloseRequested) => {
                if self.window_mode {
                    return self.dismiss_window();
                }
                return self.hide_palette();
            }
            Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) => {
                return self.on_key(key, modifiers);
            }
            Event::Mouse(iced::mouse::Event::ButtonPressed(_)) if self.visible => {
                return self.on_focus_search(0);
            }
            _ => {}
        }
        Task::none()
    }

    fn on_key(&mut self, key: Key, modifiers: KeyMods) -> Task<Message> {
        if matches!(key, Key::Named(Named::Escape)) {
            return self.hide_palette();
        }
        if modifiers.command() || modifiers.control() {
            if let Key::Character(c) = &key {
                if let Some(n) = c.chars().next().and_then(|ch| ch.to_digit(10)) {
                    if (1..=5).contains(&n) {
                        return self.update(Message::SetTab(Tab::ALL[(n as usize) - 1]));
                    }
                }
            }
        }
        if self.typing_notes {
            return Task::none();
        }
        if matches!(key, Key::Character(ref c) if c.as_str() == "/") && self.tab == Tab::Timeline {
            return text_input::focus(self.tl_search_id.clone());
        }
        if matches!(key, Key::Named(Named::Tab)) && (modifiers.control() || modifiers.command()) {
            let i = Tab::ALL.iter().position(|t| *t == self.tab).unwrap_or(0);
            let next = if modifiers.shift() {
                (i + Tab::ALL.len() - 1) % Tab::ALL.len()
            } else {
                (i + 1) % Tab::ALL.len()
            };
            return self.update(Message::SetTab(Tab::ALL[next]));
        }
        match key {
            Key::Named(Named::ArrowDown) if !self.sessions.is_empty() => {
                self.active = (self.active + 1) % self.sessions.len();
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::ArrowUp) if !self.sessions.is_empty() => {
                self.active = (self.active + self.sessions.len() - 1) % self.sessions.len();
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::Home) if !self.sessions.is_empty() => {
                self.active = 0;
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::End) if !self.sessions.is_empty() => {
                self.active = self.sessions.len() - 1;
                self.reset_detail_chrome();
                Task::batch([self.ensure_active_visible(), self.load_overview(false)])
            }
            Key::Named(Named::Enter) => self.load_overview(false),
            _ => Task::none(),
        }
    }
}

fn fetch_list(quiet: bool, since: i64) -> Task<Message> {
    Task::perform(
        rpc(move || {
            if quiet && since > 0 {
                control::session_list("", 10_000, 0, since)
            } else if quiet {
                control::session_list_all("")
            } else {
                let (limit, offset, since_rev) = first_list_fetch();
                control::session_list("", limit, offset, since_rev)
            }
        }),
        move |result| Message::ListLoaded { quiet, result },
    )
}

fn fetch_list_page(offset: u32) -> Task<Message> {
    let limit = first_list_fetch().0;
    Task::perform(
        rpc(move || control::session_list("", limit, offset, 0)),
        move |result| Message::ListPage { offset, result },
    )
}

fn queue_timeline_continue(sid: String, gen: u64) -> Task<Message> {
    Task::perform(
        async {
            tokio::time::sleep(Duration::from_millis(50)).await;
        },
        move |()| Message::ContinueTimeline {
            sid: sid.clone(),
            gen,
        },
    )
}

fn fetch_timeline(
    rpc_ref: String,
    sid: String,
    offset: u32,
    append: bool,
    gen: u64,
    limit: u32,
) -> Task<Message> {
    Task::perform(
        rpc(move || control::session_timeline(&rpc_ref, offset, limit)),
        move |result| Message::TimelineLoaded {
            gen,
            sid: sid.clone(),
            offset,
            append,
            result,
        },
    )
}

fn interesting_hud_event(event: Event, status: event::Status, _id: window::Id) -> Option<Message> {
    match event {
        Event::Window(window::Event::CloseRequested) => Some(Message::RawEvent(event)),
        Event::Keyboard(keyboard::Event::KeyPressed { .. }) if status == event::Status::Ignored => {
            Some(Message::RawEvent(event))
        }
        _ => None,
    }
}

fn notify_subscription() -> Subscription<Message> {
    Subscription::run(notify_stream)
}

fn notify_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        let (tx, rx) = std::sync::mpsc::sync_channel::<()>(64);
        control::set_notify_wake(tx);
        let rx = std::sync::Arc::new(std::sync::Mutex::new(rx));
        loop {
            let rx = rx.clone();
            let got = tokio::task::spawn_blocking(move || {
                rx.lock().ok().and_then(|guard| guard.recv().ok())
            })
            .await
            .ok()
            .flatten();
            if got.is_none() {
                break;
            }
            if iced::futures::SinkExt::send(&mut output, Message::Tick)
                .await
                .is_err()
            {
                break;
            }
        }
    })
}

async fn rpc<F>(f: F) -> Result<Value, String>
where
    F: FnOnce() -> Result<Value, ControlError> + Send + 'static,
{
    tokio::task::spawn_blocking(f)
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

fn delayed_focus(attempt: u8) -> Task<Message> {
    let wait_ms = if attempt == 0 {
        30
    } else {
        40 + u64::from(attempt) * 20
    };
    Task::perform(
        async move {
            tokio::time::sleep(Duration::from_millis(wait_ms)).await;
        },
        move |_| Message::FocusSearch(attempt),
    )
}

fn hotkey_subscription() -> Subscription<Message> {
    Subscription::run(hotkey_stream)
}

fn hotkey_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        loop {
            let pressed = tokio::task::spawn_blocking(|| {
                GlobalHotKeyEvent::receiver()
                    .recv()
                    .map(|ev| ev.state == HotKeyState::Pressed)
                    .unwrap_or(false)
            })
            .await
            .unwrap_or(false);
            if pressed {
                let _ = iced::futures::SinkExt::send(&mut output, Message::Hotkey).await;
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn palette_settings_are_fixed_overlay() {
        let w = palette_window_settings();
        assert_eq!(w.size, Size::new(HUD_W, HUD_H));
        assert!(!w.decorations);
        assert!(!w.resizable);
        assert_eq!(w.level, window::Level::AlwaysOnTop);
        assert!(w.icon.is_some());
        #[cfg(target_os = "linux")]
        assert!(w.platform_specific.override_redirect);
    }

    #[test]
    fn interesting_hud_event_ignores_mouse_motion() {
        let ev = Event::Mouse(iced::mouse::Event::CursorMoved {
            position: Point::new(1.0, 1.0),
        });
        assert!(interesting_hud_event(ev, event::Status::Ignored, window::Id::unique()).is_none());
        let key = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::ArrowDown),
            modified_key: Key::Named(Named::ArrowDown),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::ArrowDown),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
        });
        assert!(
            interesting_hud_event(key.clone(), event::Status::Ignored, window::Id::unique())
                .is_some()
        );
        assert!(
            interesting_hud_event(key, event::Status::Captured, window::Id::unique()).is_none()
        );
    }

    #[test]
    fn app_window_settings_are_decorated() {
        let w = app_window_settings();
        assert!(w.decorations);
        assert!(w.resizable);
        assert_eq!(w.level, window::Level::Normal);
        assert!(w.icon.is_some());
        #[cfg(target_os = "linux")]
        assert!(!w.platform_specific.override_redirect);
    }

    #[test]
    fn boot_is_palette_not_window() {
        let hud = Hud::default();
        assert!(!hud.window_mode());
    }

    #[test]
    fn close_requested_keeps_process_in_window_mode() {
        // Decorated close must not iced::exit — dismiss_window closes the
        // surface and the summon hotkey opens a fresh palette.
        let mut hud = Hud {
            window_mode: true,
            visible: true,
            palette_live: true,
            ..Hud::default()
        };
        let _ = hud.dismiss_window();
        assert!(!hud.window_mode());
        assert!(!hud.visible);
        assert!(hud.window_id.is_none());
    }

    #[test]
    fn window_id_none_does_not_replace_live_id() {
        let id = window::Id::unique();
        let mut hud = Hud {
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.update(Message::WindowId(None));
        assert_eq!(hud.window_id, Some(id));
    }
}
