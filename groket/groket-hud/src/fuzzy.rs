//! fzf-style fuzzy match (ported from groket.ui.fuzzy).
//!
//! Session catalog ranking is **ours** ([`session_search_score`]): title-first,
//! not a single bag-of-fields haystack and not icedtea.

use crate::model::SessionRow;

const SCORE_MATCH: i32 = 16;
const BONUS_BOUNDARY: i32 = 8;
const BONUS_BOUNDARY_WHITE: i32 = 10;
const BONUS_CAMEL: i32 = 10;
const BONUS_CONSECUTIVE: i32 = 4;
const BONUS_FIRST_MULT: i32 = 2;
const PENALTY_GAP_START: i32 = -3;
const PENALTY_GAP_EXTEND: i32 = -1;

fn is_boundary(c: char) -> bool {
    matches!(
        c,
        '/' | '-' | '_' | '.' | ' ' | ',' | ';' | ':' | '\\' | '\t'
    )
}

fn char_bonus(prev: Option<char>, curr: char) -> i32 {
    let Some(p) = prev else {
        return BONUS_BOUNDARY_WHITE;
    };
    if is_boundary(p) {
        return if p == ' ' || p == '\t' {
            BONUS_BOUNDARY_WHITE
        } else {
            BONUS_BOUNDARY
        };
    }
    if p.is_lowercase() && curr.is_uppercase() {
        return BONUS_CAMEL;
    }
    0
}

pub fn fzf_score(query: &str, candidate: &str) -> i32 {
    let ql: Vec<char> = query.to_lowercase().chars().collect();
    let cl: Vec<char> = candidate.to_lowercase().chars().collect();
    let orig: Vec<char> = candidate.chars().collect();
    let qchars: Vec<char> = query.chars().collect();
    if ql.is_empty() {
        return 0;
    }
    if ql.len() > cl.len() {
        return 0;
    }
    let mut positions = Vec::new();
    let mut j = 0;
    for (i, c) in cl.iter().enumerate() {
        if j < ql.len() && *c == ql[j] {
            positions.push(i);
            j += 1;
        }
    }
    if j < ql.len() {
        return 0;
    }
    let mut score = 0;
    let mut consecutive = 0;
    for (k, &pos) in positions.iter().enumerate() {
        let prev = if pos > 0 {
            orig.get(pos - 1).copied()
        } else {
            None
        };
        let curr = orig.get(pos).copied().unwrap_or(' ');
        let bonus = char_bonus(prev, curr);
        let mut char_score = SCORE_MATCH + bonus;
        if k == 0 && bonus > 0 {
            char_score += bonus * (BONUS_FIRST_MULT - 1);
        }
        if consecutive > 0 {
            char_score += BONUS_CONSECUTIVE;
        } else if k > 0 {
            let gap = pos - (positions[k - 1] + 1);
            if gap > 0 {
                char_score += PENALTY_GAP_START + PENALTY_GAP_EXTEND * (gap as i32 - 1);
            }
        }
        if qchars.get(k).copied() == orig.get(pos).copied() {
            char_score += 1;
        }
        score += char_score.max(0);
        if k > 0 && pos == positions[k - 1] + 1 {
            consecutive += 1;
        } else {
            consecutive = 0;
        }
    }
    score
}

pub fn fuzzy_filter<T, F>(query: &str, items: &[T], mut text_fn: F) -> Vec<T>
where
    T: Clone,
    F: FnMut(&T) -> String,
{
    let q = query.trim();
    if q.is_empty() {
        return items.to_vec();
    }
    let mut scored: Vec<(i32, T)> = Vec::new();
    for item in items {
        let text = text_fn(item);
        let score = fzf_score(q, &text);
        if score > 0 {
            scored.push((score, item.clone()));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    scored.into_iter().map(|(_, t)| t).collect()
}

/// Same rank as [`fuzzy_filter`], but returns source indices and does not clone *items*.
pub fn fuzzy_filter_indices<T, F>(query: &str, items: &[T], mut text_fn: F) -> Vec<usize>
where
    F: FnMut(&T) -> String,
{
    let q = query.trim();
    if q.is_empty() {
        return (0..items.len()).collect();
    }
    let mut scored: Vec<(i32, usize)> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        let text = text_fn(item);
        let score = fzf_score(q, &text);
        if score > 0 {
            scored.push((score, i));
        }
    }
    scored.sort_by_key(|b| std::cmp::Reverse(b.0));
    scored.into_iter().map(|(_, i)| i).collect()
}

/// Boosts so title hits always sort above bare id / model / status matches.
const BOOST_TITLE_PREFIX: i32 = 80_000;
const BOOST_TITLE_SUBSTR: i32 = 60_000;
const BOOST_TITLE_FUZZY: i32 = 40_000;
const BOOST_ID: i32 = 15_000;
const BOOST_META: i32 = 1_000;

/// Rank one catalog row for Spotlight search.
///
/// Order of preference:
/// 1. Title / label prefix (``disk`` → “Investigate high **disk** …”)
/// 2. Title / label substring
/// 3. Fuzzy match on title / label
/// 4. Session id
/// 5. Model / status / origin / outcome (weak)
///
/// Does not inspect turn text, paths, or event bodies.
pub fn session_search_score(query: &str, row: &SessionRow) -> i32 {
    let q = query.trim();
    if q.is_empty() {
        return 0;
    }
    let ql = q.to_ascii_lowercase();
    let title = row.display_title();
    let tl = title.to_ascii_lowercase();

    let mut best = 0_i32;

    if !title.is_empty() {
        if tl.starts_with(&ql) || title_word_prefix(&tl, &ql) {
            let base = fzf_score(q, title).max(SCORE_MATCH);
            best = best.max(base + BOOST_TITLE_PREFIX);
        } else if tl.contains(&ql) {
            let base = fzf_score(q, title).max(SCORE_MATCH);
            best = best.max(base + BOOST_TITLE_SUBSTR);
        } else {
            let t = fzf_score(q, title);
            if t > 0 {
                best = best.max(t + BOOST_TITLE_FUZZY);
            }
        }
    }

    // Label only if it differs from the display title we already scored.
    if !row.label.is_empty() && row.label.as_str() != title {
        let ll = row.label.to_ascii_lowercase();
        if ll.starts_with(&ql) || ll.contains(&ql) {
            let base = fzf_score(q, &row.label).max(SCORE_MATCH);
            best = best.max(base + BOOST_TITLE_SUBSTR);
        } else {
            let t = fzf_score(q, &row.label);
            if t > 0 {
                best = best.max(t + BOOST_TITLE_FUZZY);
            }
        }
    }

    if !row.session_id.is_empty() {
        let id = fzf_score(q, &row.session_id);
        if id > 0 {
            best = best.max(id + BOOST_ID);
        }
        let idl = row.session_id.to_ascii_lowercase();
        if idl.starts_with(&ql) || idl.contains(&ql) {
            best = best.max(SCORE_MATCH + BOOST_ID + 500);
        }
    }

    for field in [&row.model, &row.status, &row.origin, &row.outcome] {
        if field.is_empty() {
            continue;
        }
        let s = fzf_score(q, field);
        if s > 0 {
            best = best.max(s + BOOST_META);
        }
    }

    best
}

/// True when any whitespace-separated word in *title* starts with *query*.
fn title_word_prefix(title_lower: &str, query_lower: &str) -> bool {
    title_lower
        .split(|c: char| c.is_whitespace() || matches!(c, '/' | '-' | '_' | '.' | ':' | ','))
        .any(|w| !w.is_empty() && w.starts_with(query_lower))
}

/// Indices into *items* ordered by [`session_search_score`] (desc), then recency.
pub fn session_search_indices(query: &str, items: &[SessionRow]) -> Vec<usize> {
    let q = query.trim();
    if q.is_empty() {
        return (0..items.len()).collect();
    }
    let mut scored: Vec<(i32, f64, usize)> = Vec::new();
    for (i, row) in items.iter().enumerate() {
        let score = session_search_score(q, row);
        if score > 0 {
            scored.push((score, row.sort_epoch, i));
        }
    }
    scored.sort_by(|a, b| {
        b.0.cmp(&a.0)
            .then_with(|| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.2.cmp(&b.2))
    });
    scored.into_iter().map(|(_, _, i)| i).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_subsequence() {
        assert!(fzf_score("hud", "groket-hud palette") > 0);
        assert_eq!(fzf_score("zzz", "groket"), 0);
    }

    #[test]
    fn filter_keeps_order_by_score() {
        let items = ["zzz", "hud window", "session"];
        let out = fuzzy_filter("hud", &items, |s| (*s).to_string());
        assert_eq!(out, vec!["hud window"]);
    }

    #[test]
    fn filter_indices_match_owned_filter() {
        let items = ["zzz", "hud window", "session"];
        let owned = fuzzy_filter("hud", &items, |s| (*s).to_string());
        let idxs = fuzzy_filter_indices("hud", &items, |s| (*s).to_string());
        let via_idx: Vec<&str> = idxs.iter().map(|&i| items[i]).collect();
        assert_eq!(via_idx, owned);
        assert_eq!(
            fuzzy_filter_indices("", &items, |s| (*s).to_string()),
            vec![0, 1, 2]
        );
    }

    #[test]
    fn ranking_3000_catalog_rows_stays_interactive() {
        let rows: Vec<SessionRow> = (0..3000)
            .map(|i| SessionRow {
                session_id: format!("sess-{i:04}"),
                title: if i % 40 == 0 {
                    format!("needle host {i}")
                } else {
                    format!("other {i}")
                },
                ..SessionRow::default()
            })
            .collect();
        let start = std::time::Instant::now();
        let hits = session_search_indices("needle", &rows);
        let elapsed = start.elapsed();
        assert_eq!(hits.len(), 75);
        assert!(elapsed.as_millis() < 750, "catalog rank took {elapsed:?}");
    }

    #[test]
    fn title_outranks_session_id_and_status() {
        let by_title = SessionRow {
            session_id: "zzzz-other".into(),
            title: "Investigate high disk usage".into(),
            status: "complete".into(),
            sort_epoch: 1.0,
            ..SessionRow::default()
        };
        let by_id = SessionRow {
            session_id: "019feef7-disk-bbbb".into(),
            title: "Unrelated work".into(),
            status: "running".into(),
            sort_epoch: 99.0,
            ..SessionRow::default()
        };
        let by_status = SessionRow {
            session_id: "aaaa".into(),
            title: "Something else".into(),
            status: "disk-failed".into(),
            sort_epoch: 50.0,
            ..SessionRow::default()
        };
        let title_s = session_search_score("disk", &by_title);
        let id_s = session_search_score("disk", &by_id);
        let st_s = session_search_score("disk", &by_status);
        assert!(title_s > id_s, "title {title_s} vs id {id_s}");
        assert!(title_s > st_s, "title {title_s} vs status {st_s}");
        let rows = [by_id.clone(), by_status.clone(), by_title.clone()];
        let order = session_search_indices("disk", &rows);
        assert_eq!(rows[order[0]].session_id, by_title.session_id);
    }

    #[test]
    fn does_not_require_path_or_event_body() {
        let row = SessionRow {
            session_id: "s1".into(),
            title: "Clean target folders".into(),
            path: "/home/ali/.grok/sessions/secret-path-token".into(),
            ..SessionRow::default()
        };
        assert_eq!(session_search_score("secret-path-token", &row), 0);
        assert!(session_search_score("target", &row) > 0);
    }
}
