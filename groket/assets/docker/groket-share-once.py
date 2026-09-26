#!/usr/bin/env python3
"""Run ``grok share <session-id>`` once; write groket-share.json for the TUI to display.

Only path for creating shares. No ACP / agent stdio fallback.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sid = sys.argv[1] if len(sys.argv) > 1 else ""
sess = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
force = (sys.argv[3] if len(sys.argv) > 3 else "").strip().lower() in ("1", "force", "yes", "true")
cwd = "/workspace"
env = os.environ.copy()
env["GROK_AGENT_DASHBOARD"] = "0"
SHARE_PATH = sess / "groket-share.json"
URL_RE = re.compile(r"https?://[^\s\"'<>]+(?:share|build/share)[^\s\"'<>]*", re.I)


def _utc_now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prev():
    if not SHARE_PATH.is_file():
        return {}
    try:
        d = json.loads(SHARE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ready():
    if force:
        return True
    ch = sess / "chat_history.jsonl"
    up = sess / "updates.jsonl"
    assistantish = 0
    n = 0
    if ch.is_file():
        try:
            with ch.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    n += 1
                    if '"role": "assistant"' in line or '"role":"assistant"' in line:
                        assistantish += 1
                    if '"role": "tool_result"' in line or '"role":"tool_result"' in line:
                        assistantish += 1
        except Exception:
            pass
    if assistantish >= 1 or n >= 4:
        return True
    if up.is_file():
        try:
            if up.stat().st_size >= 50_000:
                return True
        except Exception:
            pass
    return False


def _extract_url(text):
    if not text:
        return ""
    m = URL_RE.search(text)
    if m:
        return m.group(0).rstrip(").,;\"'")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("https://") and "share" in line.lower():
            return line.split()[0].rstrip(").,;\"'")
    m2 = re.search(r"https://grok\.com/[^\s\"'<>]+", text or "")
    if m2:
        return m2.group(0).rstrip(").,;\"'")
    return ""


def _write(payload):
    try:
        SHARE_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    except Exception:
        pass


if not sid:
    print(">>> [share] missing session id", flush=True)
    sys.exit(2)

if not _ready():
    print(">>> [share] skip (waiting for session activity)", flush=True)
    sys.exit(0)

prev = _prev()
prev_url = str(prev.get("share_url") or "").strip()
snapshot_n = int(prev.get("snapshot_n") or 0) + 1
grok = shutil.which("grok") or "grok"
cmd = [grok, "share", sid]
leader = (os.environ.get("SHARE_LEADER_SOCKET") or "").strip()
if leader:
    cmd.extend(["--leader-socket", leader])
timeout = float(os.environ.get("SHARE_CLI_TIMEOUT", "300"))

print(">>> [share] %s" % (" ".join(cmd),), flush=True)
url = ""
err = ""
try:
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    url = _extract_url(out)
    if not url:
        err = (out.strip() or "grok share exit %s" % proc.returncode)[:2000]
except subprocess.TimeoutExpired:
    err = "grok share timed out after %ss" % int(timeout)
except FileNotFoundError:
    err = "grok not found in container PATH"
except Exception as exc:
    err = str(exc)

# One success model: share_url is set only when this attempt produced a URL.
# Never recycle a previous URL as the current share (that made failed refreshes
# look "ready" in the TUI). Keep previous_share_url for history only.
payload = {
    "session_id": sid,
    "session_dir": str(sess),
    "share_url": url,
    "error": err if not url else "",
    "source": "incontainer",
    "method": "cli",
    "snapshot_n": snapshot_n if url else int(prev.get("snapshot_n") or 0),
    "snapshot_at": _utc_now() if url else prev.get("snapshot_at", ""),
    "created_at": prev.get("created_at") or _utc_now(),
    "updated_at": _utc_now(),
    "note": "Created by: grok share <session-id> in eval container",
}
if prev_url and prev_url != url:
    payload["previous_share_url"] = prev_url
_write(payload)

if url:
    print(">>> [share] SNAPSHOT #%s: %s" % (snapshot_n, url), flush=True)
elif err:
    print(">>> [share] failed: %s" % (err[:300],), flush=True)
    # Exit 3 = permanent (entrypoint stops the share loop). Transient = 1.
    el = err.lower()
    permanent = (
        "sharing is not available" in el
        or "session sharing is not available" in el
        or "share is not available" in el
        or ("not available for your account" in el and "shar" in el)
    )
    sys.exit(3 if permanent else 1)
