//! Typed control-plane JSON (mirrors ``groket.session.control_views``).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::format::list_status_label;
use crate::model::KindFilter;

/// One ``session/list`` catalog row.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SessionListItem {
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub outcome: String,
    #[serde(default)]
    pub origin: String,
    #[serde(default)]
    pub task_id: String,
    #[serde(default)]
    pub duration_seconds: f64,
    #[serde(default)]
    pub num_events: i64,
    #[serde(default)]
    pub context_usage_compact: String,
    #[serde(default)]
    pub context_window_usage_pct: Option<f64>,
    #[serde(default)]
    pub context_tokens_used: Option<i64>,
    #[serde(default)]
    pub context_window_tokens: Option<i64>,
    #[serde(default)]
    pub tool_call_count: i64,
    #[serde(default)]
    pub error_count: i64,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub sort_epoch: f64,
}

/// ``session/list`` result body.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SessionListResponse {
    #[serde(default)]
    pub sessions: Vec<SessionListItem>,
    #[serde(default)]
    pub total: i64,
    #[serde(default)]
    pub matched: i64,
    #[serde(default)]
    pub revision: i64,
    #[serde(default)]
    pub unchanged: bool,
    #[serde(default)]
    pub removed: Vec<String>,
    #[serde(default)]
    pub delta: bool,
    #[serde(default)]
    pub incomplete: bool,
    #[serde(default)]
    pub building: bool,
}

/// ``session_meta_mapping`` / overview ``meta``.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SessionMeta {
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub path: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub model_id: String,
    #[serde(default)]
    pub reasoning_effort: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub outcome: String,
    #[serde(default)]
    pub origin: String,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub num_messages: i64,
    #[serde(default)]
    pub num_events: i64,
    #[serde(default)]
    pub duration_seconds: f64,
    #[serde(default)]
    pub duration: String,
    #[serde(default)]
    pub tool_call_count: i64,
    #[serde(default)]
    pub tool_failure_count: i64,
    #[serde(default)]
    pub error_count: i64,
    #[serde(default)]
    pub doom_loop_warnings: i64,
    #[serde(default)]
    pub lines_added: i64,
    #[serde(default)]
    pub lines_removed: i64,
    #[serde(default)]
    pub context_window_usage_pct: Option<f64>,
    #[serde(default)]
    pub context_tokens_used: Option<i64>,
    #[serde(default)]
    pub context_window_tokens: Option<i64>,
    #[serde(default)]
    pub context_usage: String,
    #[serde(default)]
    pub context_usage_compact: String,
    #[serde(default)]
    pub compaction_count: i64,
    #[serde(default)]
    pub git_repo: String,
    #[serde(default)]
    pub git_branch: String,
    #[serde(default)]
    pub git_commit: String,
    #[serde(default)]
    pub task_id: String,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub loop_count: i64,
    #[serde(default)]
    pub turn_in_progress: bool,
    #[serde(default)]
    pub turn_failed: bool,
}

/// One ``turn_segment_mapping`` row.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnRow {
    #[serde(default)]
    pub turn_index: i64,
    #[serde(default)]
    pub turn_number: Option<i64>,
    #[serde(default)]
    pub prompt_index: Option<i64>,
    #[serde(default)]
    pub outcome: String,
    #[serde(default)]
    pub open: bool,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub user_event_index: Option<i64>,
    #[serde(default)]
    pub assistant_summary: String,
    #[serde(default)]
    pub assistant_event_index: Option<i64>,
    #[serde(default)]
    pub event_count: i64,
    #[serde(default)]
    pub tool_call_count: i64,
    #[serde(default)]
    pub tool_error_count: i64,
    #[serde(default)]
    pub user_count: i64,
    #[serde(default)]
    pub assistant_count: i64,
    #[serde(default)]
    pub error_event_count: i64,
    #[serde(default)]
    pub first_index: Option<i64>,
    #[serde(default)]
    pub last_index: Option<i64>,
    #[serde(default)]
    pub duration_seconds: Option<f64>,
    #[serde(default)]
    pub event_indexes: Vec<i64>,
}

/// ``session/turns`` body (also overview ``turns``).
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TurnsBlock {
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub total: i64,
    #[serde(default)]
    pub turns: Vec<TurnRow>,
}

/// One ``timeline_event_mapping`` row.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TimelineEvent {
    #[serde(default)]
    pub index: i64,
    #[serde(default, rename = "type")]
    pub event_type: String,
    #[serde(default)]
    pub type_label: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub tool_family: String,
    #[serde(default)]
    pub heading: String,
    #[serde(default)]
    pub harness_chrome: bool,
    #[serde(default)]
    pub timestamp: Value,
    #[serde(default)]
    pub time: String,
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub content_truncated: bool,
    #[serde(default)]
    pub content_length: i64,
    #[serde(default)]
    pub tool_name: String,
    #[serde(default)]
    pub tool_call_id: String,
    #[serde(default)]
    pub is_error: bool,
    #[serde(default)]
    pub update_index: i64,
    #[serde(default)]
    pub prompt_index: Option<i64>,
    #[serde(default)]
    pub turn_index: Option<i64>,
    #[serde(default)]
    pub preview: String,
    #[serde(default)]
    pub raw_input: Value,
    #[serde(default)]
    pub tool_fields: Vec<ToolFieldRow>,
    #[serde(default)]
    pub image_path: String,
    #[serde(default)]
    pub match_field: String,
    #[serde(default)]
    pub match_snippet: String,
}

/// One ``toolFields`` row from ``session/timeline``.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ToolFieldRow {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub value: String,
}

/// ``session/timeline`` page.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct TimelinePage {
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub total: u32,
    #[serde(default)]
    pub offset: u32,
    #[serde(default)]
    pub limit: u32,
    #[serde(default)]
    pub events: Vec<TimelineEvent>,
}

/// Cached analysis finding (overview ``findings.findings[]``).
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct FindingRow {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub plugin_id: String,
    #[serde(default)]
    pub severity: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub category: String,
    #[serde(default)]
    pub event_indices: Vec<i64>,
    #[serde(default)]
    pub turn_indices: Vec<i64>,
    #[serde(default)]
    pub primary_event_index: Option<i64>,
    #[serde(default)]
    pub primary_turn_index: Option<i64>,
}

/// Overview ``findings`` block.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct FindingsBlock {
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub total: i64,
    #[serde(default)]
    pub count: i64,
    #[serde(default)]
    pub truncated: bool,
    #[serde(default)]
    pub plugins: Vec<String>,
    #[serde(default)]
    pub findings: Vec<FindingRow>,
}

/// Notes schema field (overview ``notes.schema.fields[]``).
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NoteSchemaField {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub choices: Vec<String>,
    #[serde(default)]
    pub pick: String,
}

/// Overview ``notes.schema``.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NotesSchema {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub fields: Vec<NoteSchemaField>,
}

/// One operator note in overview ``notes.notes[]``.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NoteRow {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub turn_index: Option<i64>,
    #[serde(default)]
    pub fields: Value,
    #[serde(default)]
    pub event_indices: Vec<i64>,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
}

/// Overview ``notes`` block.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct NotesBlock {
    #[serde(default)]
    pub revision: String,
    #[serde(default)]
    pub count: i64,
    #[serde(default)]
    pub notes: Vec<NoteRow>,
    #[serde(default)]
    pub schema: NotesSchema,
}

impl NotesBlock {
    /// Apply a ``notes/upsert`` / ``notes/delete`` snapshot.
    ///
    /// Control ``notes_snapshot_mapping`` has ``revision`` / ``schema`` /
    /// ``notes`` and **no** ``count``. Overview chrome uses ``count``; keep it
    /// equal to ``notes.len()`` (same as the previous Value-bag path).
    pub fn from_control_snapshot(snap: &Value, prev: &Self) -> Option<Self> {
        let mut block: Self = serde_json::from_value(snap.clone()).ok()?;
        block.count = i64::try_from(block.notes.len()).unwrap_or(i64::MAX);
        if block.schema.fields.is_empty() {
            block.schema = prev.schema.clone();
        }
        if block.revision.is_empty() {
            block.revision = prev.revision.clone();
        }
        Some(block)
    }
}

/// ``session/overview`` body.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Overview {
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub meta: SessionMeta,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub turns: TurnsBlock,
    #[serde(default)]
    pub notes: NotesBlock,
    #[serde(default)]
    pub findings: FindingsBlock,
}

impl SessionListItem {
    pub fn display_title(&self) -> &str {
        if !self.title.is_empty() {
            &self.title
        } else if !self.label.is_empty() {
            &self.label
        } else {
            &self.session_id
        }
    }

    /// Flat string for cheap “any field” tests. Prefer
    /// [`crate::fuzzy::session_search_score`] for ranking — title outranks
    /// id / model / status. Path and event bodies are not included.
    pub fn haystack(&self) -> String {
        format!(
            "{} {} {} {} {} {} {}",
            self.session_id,
            self.title,
            self.label,
            self.model,
            self.status,
            self.origin,
            self.outcome
        )
    }

    pub fn status_label(&self) -> String {
        list_status_label(&self.status, &self.outcome)
    }

    fn ensure_sort_epoch(&mut self) {
        if self.sort_epoch > 0.0 {
            return;
        }
        for raw in [&self.updated_at, &self.created_at] {
            if let Ok(t) = chrono_like_secs(raw) {
                self.sort_epoch = t;
                return;
            }
        }
    }
}

impl SessionMeta {
    pub fn status_label(&self) -> String {
        list_status_label(&self.status, &self.outcome)
    }

    pub fn context_compact(&self) -> &str {
        if !self.context_usage_compact.is_empty() {
            &self.context_usage_compact
        } else {
            &self.context_usage
        }
    }
}

impl TimelineEvent {
    pub fn matches_kind(&self, mode: KindFilter) -> bool {
        crate::format::event_matches_kind(&self.kind, self.is_error, mode)
    }

    pub fn haystack(&self) -> String {
        format!(
            "{} {} {} {} {} {} {}",
            self.kind,
            self.heading,
            self.tool_name,
            self.preview,
            self.content,
            self.type_label,
            self.time
        )
    }

    pub fn fingerprint(&self) -> String {
        let content_tail: String = self
            .content
            .chars()
            .rev()
            .take(64)
            .collect::<String>()
            .chars()
            .rev()
            .collect();
        let content_head: String = self.content.chars().take(64).collect();
        format!(
            "{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}\u{0001}{}",
            self.index,
            self.turn_index.map(|n| n.to_string()).unwrap_or_default(),
            self.type_label,
            self.heading,
            self.kind,
            self.tool_name,
            i32::from(self.is_error),
            i32::from(self.content_truncated),
            self.content_length,
            self.preview,
            self.content.len(),
            content_head,
            content_tail,
            self.time,
        )
    }
}

impl TurnsBlock {
    pub fn has_open_turn(&self) -> bool {
        self.turns.iter().any(|t| t.open)
    }
}

/// Decode ``session/list`` (bare result, wrapped ``result``, or a raw array).
pub fn decode_session_list_response(value: &Value) -> Result<SessionListResponse, String> {
    let body = if let Some(result) = value.get("result") {
        if result.get("sessions").is_some() || result.is_array() {
            result
        } else {
            value
        }
    } else {
        value
    };
    if body.is_array() {
        let mut items: Vec<SessionListItem> =
            serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
        for row in &mut items {
            row.ensure_sort_epoch();
        }
        let n = i64::try_from(items.len()).unwrap_or(i64::MAX);
        return Ok(SessionListResponse {
            sessions: items,
            total: n,
            matched: n,
            ..SessionListResponse::default()
        });
    }
    let mut resp: SessionListResponse =
        serde_json::from_value(body.clone()).map_err(|e| e.to_string())?;
    for row in &mut resp.sessions {
        row.ensure_sort_epoch();
    }
    Ok(resp)
}

pub fn decode_session_list(value: &Value) -> Result<Vec<SessionListItem>, String> {
    Ok(decode_session_list_response(value)?.sessions)
}

pub fn decode_overview(value: &Value) -> Result<Overview, String> {
    serde_json::from_value(value.clone()).map_err(|e| e.to_string())
}

pub fn decode_turns(value: &Value) -> Result<TurnsBlock, String> {
    serde_json::from_value(value.clone()).map_err(|e| e.to_string())
}

pub fn decode_timeline_page(value: &Value) -> Result<TimelinePage, String> {
    serde_json::from_value(value.clone()).map_err(|e| e.to_string())
}

fn chrono_like_secs(raw: &str) -> Result<f64, ()> {
    let t = raw.trim();
    if t.is_empty() {
        return Err(());
    }
    if let Ok(n) = t.parse::<f64>() {
        if n > 1_000_000.0 {
            return Ok(if n > 10_000_000_000.0 { n / 1000.0 } else { n });
        }
    }
    if t.len() >= 19 && t.as_bytes()[4] == b'-' {
        let y: i32 = t[0..4].parse().map_err(|_| ())?;
        let mo: u32 = t[5..7].parse().map_err(|_| ())?;
        let d: u32 = t[8..10].parse().map_err(|_| ())?;
        let h: u32 = t[11..13].parse().map_err(|_| ())?;
        let mi: u32 = t[14..16].parse().map_err(|_| ())?;
        let s: u32 = t[17..19].parse().map_err(|_| ())?;
        let days =
            i64::from(y - 1970) * 365 + i64::from(mo.saturating_sub(1) * 30 + d.saturating_sub(1));
        return Ok((days * 86400 + i64::from(h) * 3600 + i64::from(mi) * 60 + i64::from(s)) as f64);
    }
    Err(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn fixture(name: &str) -> Value {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(name);
        let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path:?}: {e}"));
        serde_json::from_str(&text).expect("fixture json")
    }

    #[test]
    fn decode_python_list_overview_turns_timeline() {
        let list = decode_session_list(&fixture("list.json")).expect("list");
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].session_id, "sess-wire");
        assert_eq!(list[0].status, "running");
        assert_eq!(list[0].outcome, "running");

        let ov = decode_overview(&fixture("overview.json")).expect("overview");
        assert_eq!(ov.session_id, "sess-wire");
        assert_eq!(ov.meta.status, "running");
        assert_eq!(ov.meta.session_id, "sess-wire");
        assert_eq!(ov.turns.total, 1);
        assert_eq!(ov.turns.turns[0].assistant_summary, "hello agent");
        assert_eq!(ov.turns.turns[0].assistant_event_index, Some(1));
        assert_eq!(ov.turns.turns[0].summary, "hello user");

        let turns = decode_turns(&fixture("turns.json")).expect("turns");
        assert_eq!(turns.session_id, "sess-wire");
        assert_eq!(turns.turns[0].assistant_summary, "hello agent");

        let page = decode_timeline_page(&fixture("timeline.json")).expect("timeline");
        assert_eq!(page.session_id, "sess-wire");
        assert_eq!(page.total, 3);
        assert_eq!(page.events[0].kind, "user");
        assert_eq!(page.events[1].kind, "agent");
        assert_eq!(page.events[1].content, "hello agent");
        assert_eq!(page.events[2].kind, "tool");
        assert_eq!(page.events[2].tool_name, "read_file");
        assert!(page.events[1].matches_kind(KindFilter::Asst));
        assert!(!page.events[2].matches_kind(KindFilter::Asst));
        assert!(page.events[1].haystack().contains("hello agent"));
        assert!(!page.events[1].fingerprint().is_empty());
        let with_hit = decode_timeline_page(&serde_json::json!({
            "sessionId": "s",
            "total": 1,
            "offset": 0,
            "limit": 1,
            "events": [{
                "index": 3,
                "type": "agent_message_chunk",
                "kind": "agent",
                "content": "see needle-token here",
                "matchField": "content",
                "matchSnippet": "see needle-token here"
            }]
        }))
        .expect("hit page");
        assert_eq!(with_hit.events[0].match_field, "content");
        assert!(with_hit.events[0].match_snippet.contains("needle-token"));
    }

    #[test]
    fn list_item_helpers_and_wrapped_payload() {
        let mut row = SessionListItem {
            session_id: "s".into(),
            title: String::new(),
            label: "L".into(),
            status: "—".into(),
            outcome: "completed".into(),
            updated_at: "2026-08-08T12:00:00Z".into(),
            ..SessionListItem::default()
        };
        assert_eq!(row.display_title(), "L");
        row.title = "T".into();
        assert_eq!(row.display_title(), "T");
        assert_eq!(row.status_label(), "complete");
        row.ensure_sort_epoch();
        assert!(row.sort_epoch > 0.0);
        row.title.clear();
        row.label.clear();
        row.session_id = "sid".into();
        assert_eq!(row.display_title(), "sid");
        assert!(row.haystack().contains("sid"));
        row.path = "/home/ali/.grok/sessions/sid".into();
        assert!(
            !row.haystack().contains(".grok/sessions"),
            "path must not be in the search haystack"
        );

        let wrapped = serde_json::json!({
            "result": {
                "sessions": [{
                    "sessionId": "w1",
                    "status": "awaiting",
                    "title": "n"
                }],
                "total": 1,
                "matched": 1
            }
        });
        let decoded = decode_session_list(&wrapped).expect("wrapped");
        assert_eq!(decoded[0].session_id, "w1");
        assert_eq!(decoded[0].status, "awaiting");
        let page = decode_session_list_response(&wrapped).expect("page");
        assert_eq!(page.matched, 1);
        assert_eq!(page.total, 1);

        let arr = serde_json::json!([{"sessionId": "a1", "status": "running"}]);
        let decoded = decode_session_list(&arr).expect("array");
        assert_eq!(decoded[0].session_id, "a1");

        assert!(decode_session_list(&serde_json::json!("nope")).is_err());
        assert!(decode_overview(&serde_json::json!(true)).is_err());
        assert!(decode_turns(&serde_json::json!(null)).is_err());
        assert!(decode_timeline_page(&serde_json::json!(1)).is_err());
    }

    #[test]
    fn meta_status_and_open_turns() {
        let meta = SessionMeta {
            status: String::new(),
            outcome: "awaiting_follow_up".into(),
            context_usage: "x".into(),
            ..SessionMeta::default()
        };
        assert_eq!(meta.status_label(), "awaiting");
        assert_eq!(meta.context_compact(), "x");
        let mut compact = meta.clone();
        compact.context_usage_compact = "12%".into();
        assert_eq!(compact.context_compact(), "12%");
        let turns = TurnsBlock {
            turns: vec![TurnRow {
                open: true,
                ..TurnRow::default()
            }],
            ..TurnsBlock::default()
        };
        assert!(turns.has_open_turn());
    }

    #[test]
    fn notes_snapshot_without_count_sets_count_from_notes_len() {
        // Same keys as groket.session.access.notes_snapshot_mapping (no count).
        let snap = serde_json::json!({
            "revision": "rev-after-upsert",
            "schema": {
                "id": "default",
                "fields": [
                    {"id": "summary", "label": "Summary", "choices": [], "pick": "one-of"},
                    {"id": "detail", "label": "Detail", "choices": [], "pick": "one-of"}
                ]
            },
            "notes": [
                {
                    "id": "n-aaa111bbb222",
                    "turnIndex": 0,
                    "fields": {"summary": "saved note"},
                    "eventIndices": [3],
                    "createdAt": "2026-08-08T12:00:00Z",
                    "updatedAt": "2026-08-08T12:01:00Z"
                }
            ]
        });
        let prev = NotesBlock {
            revision: "rev-before".into(),
            count: 0,
            notes: vec![],
            schema: NotesSchema::default(),
        };
        let after = NotesBlock::from_control_snapshot(&snap, &prev).expect("snapshot");
        assert_eq!(after.count, i64::try_from(after.notes.len()).unwrap());
        assert_eq!(after.count, 1);
        assert_eq!(after.notes[0].id, "n-aaa111bbb222");
        assert_eq!(after.revision, "rev-after-upsert");
        assert_eq!(after.schema.fields.len(), 2);

        let deleted = serde_json::json!({
            "revision": "rev-after-delete",
            "schema": {"id": "default", "fields": []},
            "notes": []
        });
        let empty = NotesBlock::from_control_snapshot(&deleted, &after).expect("delete");
        assert_eq!(empty.count, 0);
        assert!(empty.notes.is_empty());
        assert_eq!(empty.revision, "rev-after-delete");
        assert_eq!(empty.schema.fields.len(), 2);
    }
}
