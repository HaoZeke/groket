//! Byte-needle prefilter for Grok ``updates.jsonl`` lines.
//!
//! Streaming ``tool_call_update`` rows often *are* the multi-100MB file
//! (cumulative shell output). Skip JSON parse unless the line looks terminal.

/// Same needle as ``parser._TU_BYTES``.
const TU_BYTES: &[u8] = b"tool_call_update";

/// Same needles as ``parser._TERM_BYTES`` (spacing variants included).
const TERM_BYTES: &[&[u8]] = &[
    br#""status":"completed""#,
    br#""status": "completed""#,
    br#""status":"failed""#,
    br#""status": "failed""#,
    br#""isError":true"#,
    br#""isError": true"#,
];

const RC_OK: i32 = 0;
const RC_NOSPACE: i32 = -1;
const RC_NULL: i32 = -2;

fn contains_bytes(hay: &[u8], needle: &[u8]) -> bool {
    hay.windows(needle.len()).any(|w| w == needle)
}

fn strip_trailing_cr(line: &[u8]) -> &[u8] {
    line.strip_suffix(b"\r").unwrap_or(line)
}

/// Return ``false`` when a ``tool_call_update`` line is non-terminal (skip JSON).
///
/// Keep the line when it is not a ``tool_call_update``, or when it contains any
/// terminal status / error needle.
#[must_use]
pub fn keep_updates_line(line: &[u8]) -> bool {
    if contains_bytes(line, TU_BYTES)
        && !TERM_BYTES.iter().copied().any(|m| contains_bytes(line, m))
    {
        return false;
    }
    true
}

/// Split *data* on ``\\n`` and keep lines [`keep_updates_line`] accepts.
///
/// Trailing ``\\r`` is dropped. An incomplete last line (no ``\\n``) is kept
/// when nonempty.
#[must_use]
pub fn filter_updates(data: &[u8]) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for (i, &b) in data.iter().enumerate() {
        if b != b'\n' {
            continue;
        }
        let line = strip_trailing_cr(&data[start..i]);
        if keep_updates_line(line) {
            out.push(line.to_vec());
        }
        start = i + 1;
    }
    if start < data.len() {
        let line = strip_trailing_cr(&data[start..]);
        if !line.is_empty() && keep_updates_line(line) {
            out.push(line.to_vec());
        }
    }
    out
}

fn encoded_len(kept: &[Vec<u8>]) -> usize {
    if kept.is_empty() {
        0
    } else {
        kept.iter().map(|line| line.len() + 1).sum()
    }
}

unsafe fn slice_from_raw<'a>(ptr: *const u8, len: usize) -> Result<&'a [u8], i32> {
    if ptr.is_null() {
        if len == 0 {
            return Ok(&[]);
        }
        return Err(RC_NULL);
    }
    Ok(unsafe { std::slice::from_raw_parts(ptr, len) })
}

/// C ABI: ``1`` keep, ``0`` skip, ``-2`` when *ptr* is null and *len* is nonzero.
///
/// # Safety
///
/// *ptr* must be valid for *len* bytes, or null when *len* is 0.
#[no_mangle]
pub unsafe extern "C" fn groket_keep_updates_line(ptr: *const u8, len: usize) -> i32 {
    let line = match unsafe { slice_from_raw(ptr, len) } {
        Ok(s) => s,
        Err(code) => return code,
    };
    i32::from(keep_updates_line(line))
}

/// C ABI: write kept lines joined by ``\\n`` (trailing newline if any kept).
///
/// Returns ``0`` on success, ``-1`` if *out_cap* is too small (still sets
/// ``*out_len`` to the required size), ``-2`` on null args.
///
/// # Safety
///
/// *in_ptr* must be valid for *in_len* bytes, or null when *in_len* is 0.
/// *out_len* must be non-null. *out_ptr* must be valid for *out_cap* bytes,
/// or null when *out_cap* is 0.
#[no_mangle]
pub unsafe extern "C" fn groket_filter_updates(
    in_ptr: *const u8,
    in_len: usize,
    out_ptr: *mut u8,
    out_cap: usize,
    out_len: *mut usize,
) -> i32 {
    if out_len.is_null() {
        return RC_NULL;
    }
    if out_ptr.is_null() && out_cap != 0 {
        return RC_NULL;
    }
    let input = match unsafe { slice_from_raw(in_ptr, in_len) } {
        Ok(s) => s,
        Err(code) => return code,
    };
    let kept = filter_updates(input);
    let needed = encoded_len(&kept);
    unsafe {
        *out_len = needed;
    }
    if needed > out_cap {
        return RC_NOSPACE;
    }
    if needed == 0 {
        return RC_OK;
    }
    let out = unsafe { std::slice::from_raw_parts_mut(out_ptr, out_cap) };
    let mut pos = 0usize;
    for line in &kept {
        let end = pos + line.len();
        out[pos..end].copy_from_slice(line);
        out[end] = b'\n';
        pos = end + 1;
    }
    debug_assert_eq!(pos, needed);
    RC_OK
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skip_non_terminal_tool_call_update() {
        let line =
            br#"{"params":{"update":{"sessionUpdate":"tool_call_update","content":"partial"}}}"#;
        assert!(!keep_updates_line(line));
    }

    #[test]
    fn keep_terminal_completed_failed_is_error() {
        let cases: &[&[u8]] = &[
            br#"{"sessionUpdate":"tool_call_update","status":"completed"}"#,
            br#"{"sessionUpdate":"tool_call_update","status": "completed"}"#,
            br#"{"sessionUpdate":"tool_call_update","status":"failed"}"#,
            br#"{"sessionUpdate":"tool_call_update","status": "failed"}"#,
            br#"{"sessionUpdate":"tool_call_update","isError":true}"#,
            br#"{"sessionUpdate":"tool_call_update","isError": true}"#,
        ];
        for line in cases {
            assert!(
                keep_updates_line(line),
                "expected keep: {}",
                String::from_utf8_lossy(line)
            );
        }
    }

    #[test]
    fn keep_user_message_chunk() {
        let line =
            br#"{"params":{"update":{"sessionUpdate":"user_message_chunk","content":"hi"}}}"#;
        assert!(keep_updates_line(line));
    }

    #[test]
    fn filter_updates_drops_fat_streaming_lines_keeps_two_others() {
        let user = br#"{"sessionUpdate":"user_message_chunk","content":"hi"}"#;
        let fat = format!(
            r#"{{"sessionUpdate":"tool_call_update","content":"{}"}}"#,
            "x".repeat(256)
        );
        let done = br#"{"sessionUpdate":"tool_call_update","status":"completed"}"#;
        let mut data = Vec::new();
        data.extend_from_slice(user);
        data.push(b'\n');
        data.extend_from_slice(fat.as_bytes());
        data.push(b'\n');
        data.extend_from_slice(done);
        data.push(b'\n');
        let kept = filter_updates(&data);
        assert_eq!(kept, vec![user.to_vec(), done.to_vec()]);
    }

    #[test]
    fn filter_updates_strips_cr_and_keeps_incomplete_last_line() {
        let data = b"user_message_chunk\r\ntool_call_update fat\r\nuser_message_chunk again";
        let kept = filter_updates(data);
        assert_eq!(
            kept,
            vec![
                b"user_message_chunk".to_vec(),
                b"user_message_chunk again".to_vec()
            ]
        );
    }

    #[test]
    fn c_abi_keep_updates_line() {
        let skip = b"tool_call_update streaming";
        let keep = b"user_message_chunk";
        unsafe {
            assert_eq!(groket_keep_updates_line(skip.as_ptr(), skip.len()), 0);
            assert_eq!(groket_keep_updates_line(keep.as_ptr(), keep.len()), 1);
            assert_eq!(groket_keep_updates_line(std::ptr::null(), 0), 1);
            assert_eq!(groket_keep_updates_line(std::ptr::null(), 4), RC_NULL);
        }
    }

    #[test]
    fn c_abi_filter_updates_size_and_null() {
        let user = b"user_message_chunk\n";
        let fat = b"tool_call_update fat\n";
        let mut data = Vec::new();
        data.extend_from_slice(user);
        data.extend_from_slice(fat);
        let mut out_len = 0usize;
        unsafe {
            assert_eq!(
                groket_filter_updates(
                    data.as_ptr(),
                    data.len(),
                    std::ptr::null_mut(),
                    0,
                    std::ptr::null_mut()
                ),
                RC_NULL
            );
            let rc = groket_filter_updates(
                data.as_ptr(),
                data.len(),
                std::ptr::null_mut(),
                0,
                &mut out_len,
            );
            assert_eq!(rc, RC_NOSPACE);
            assert_eq!(out_len, b"user_message_chunk\n".len());
            let mut buf = vec![0u8; out_len];
            let rc = groket_filter_updates(
                data.as_ptr(),
                data.len(),
                buf.as_mut_ptr(),
                buf.len(),
                &mut out_len,
            );
            assert_eq!(rc, RC_OK);
            assert_eq!(&buf[..out_len], b"user_message_chunk\n");
            assert_eq!(
                groket_filter_updates(std::ptr::null(), 3, std::ptr::null_mut(), 0, &mut out_len),
                RC_NULL
            );
            assert_eq!(
                groket_filter_updates(
                    data.as_ptr(),
                    data.len(),
                    std::ptr::null_mut(),
                    8,
                    &mut out_len
                ),
                RC_NULL
            );
            let mut empty_len = 99usize;
            let rc =
                groket_filter_updates(std::ptr::null(), 0, std::ptr::null_mut(), 0, &mut empty_len);
            assert_eq!(rc, RC_OK);
            assert_eq!(empty_len, 0);
        }
    }

    #[test]
    fn filter_updates_empty_input() {
        assert!(filter_updates(b"").is_empty());
    }
}
