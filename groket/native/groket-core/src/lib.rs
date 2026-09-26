//! Grok ``updates.jsonl`` scan leaf for groket.
//!
//! Byte-needle prefilter matching ``groket.parser`` ``_TU_BYTES`` / ``_TERM_BYTES``.

mod scan;

pub use scan::{
    filter_updates, groket_filter_updates, groket_keep_updates_line, keep_updates_line,
};
