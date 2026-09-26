#!/bin/bash
set -e

REPO_URL="${REPO_URL:-}"
MODEL="${MODEL:-grok-build}"
REPO_BRANCH="${REPO_BRANCH:-}"
PROMPT="${PROMPT:-}"
SKIP_SETUP="${SKIP_SETUP:-0}"

# Host uid/gid (set by orchestrator) — bind mounts are written as root inside
# the container; chown so the host can delete/re-launch (sessions + /workspace).
_gte_fix_bind_ownership() {
    local uid="${HOST_UID:-}"
    local gid="${HOST_GID:-}"
    if [ -z "$uid" ] || [ -z "$gid" ]; then
        return 0
    fi
    if [ -d /root/.grok/sessions ]; then
        if chown -R "${uid}:${gid}" /root/.grok/sessions 2>/dev/null; then
            echo ">>> Sessions volume ownership → ${uid}:${gid} (host user)"
        fi
    fi
    if [ ! -d /workspace ]; then
        return 0
    fi
    # External operator path (repo_path): do *not* recursive-chown the whole tree
    # (may be huge / mixed ownership). Only reclaim paths the agent created as
    # root so host edits stay host-owned.
    if [ "${WORKSPACE_EXTERNAL:-0}" = "1" ] || [ "${WORKSPACE_EXTERNAL:-}" = "true" ]; then
        local n=0
        # -user 0: root-owned files/dirs written by the container process.
        while IFS= read -r -d '' path; do
            if chown "${uid}:${gid}" "$path" 2>/dev/null; then
                n=$((n + 1))
            fi
        done < <(find /workspace -user 0 -print0 2>/dev/null)
        if [ "$n" -gt 0 ]; then
            echo ">>> External workspace: reclaimed ${n} root-owned path(s) → ${uid}:${gid}"
        else
            echo ">>> External workspace: no root-owned paths to reclaim"
        fi
        return 0
    fi
    if chown -R "${uid}:${gid}" /workspace 2>/dev/null; then
        echo ">>> Workspace ownership → ${uid}:${gid} (host user)"
    fi
}

trap '_gte_fix_bind_ownership' EXIT

# GitHub CLI in automation: accept host-injected token when job opts into github_write
# (orchestrator sets GH_TOKEN from GH_TOKEN on the host).
if [ -z "${GH_TOKEN:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
    export GH_TOKEN="$GITHUB_TOKEN"
fi
export GH_PAGER="${GH_PAGER:-cat}"
export GIT_TERMINAL_PROMPT="${GIT_TERMINAL_PROMPT:-0}"

# Wire git push/fetch to use GH_TOKEN via gh (plain `git` does not read GH_TOKEN alone).
_gte_setup_git_gh_auth() {
    if [ -z "${GH_TOKEN:-}" ]; then
        return 0
    fi
    if ! command -v gh >/dev/null 2>&1; then
        echo ">>> WARNING: GH_TOKEN set but gh not installed — git push may fail (use fully-loaded profile)."
        return 0
    fi
    if ! command -v git >/dev/null 2>&1; then
        echo ">>> WARNING: GH_TOKEN set but git not installed."
        return 0
    fi
    # Non-interactive: gh as git credential helper so `git push` works with the PAT.
    set +e
    gh auth setup-git >/dev/null 2>&1
    git config --global credential.helper '!gh auth git-credential' >/dev/null 2>&1
    # Sensible defaults for agent commits inside eval (override in setup.sh if needed).
    git config --global user.email "${GIT_AUTHOR_EMAIL:-groket-eval@localhost}" >/dev/null 2>&1
    git config --global user.name "${GIT_AUTHOR_NAME:-groket-eval}" >/dev/null 2>&1
    set -e
    echo ">>> git credential helper → gh (GH_TOKEN) — git push/fetch over HTTPS should work."
}

if [ -n "${GH_TOKEN:-}" ]; then
    echo ">>> GH_TOKEN present — gh/API auth available (non-interactive)."
    if [ "${GITHUB_WRITE:-0}" = "1" ]; then
        echo ">>> GITHUB_WRITE=1 — write/push enabled for this job (repo-scoped token recommended)."
    fi
    _gte_setup_git_gh_auth
else
    # Informational only — public HTTPS clones do not need a token.
    echo ">>> No GH_TOKEN (optional): private remotes / git push need a persona PAT or host GH_TOKEN."
fi

# /workspace is a host bind-mount: checkouts/<container>/, or an external
# operator path (WORKSPACE_EXTERNAL=1 / repo_path). Host already prepared the
# tree. Only clone inside the container when the mount is empty (legacy / tests).
REPO_COMMIT="${REPO_COMMIT:-}"
if [ -d /workspace/.git ] || [ -n "$(ls -A /workspace 2>/dev/null)" ]; then
    if [ "${WORKSPACE_EXTERNAL:-0}" = "1" ] || [ "${WORKSPACE_EXTERNAL:-}" = "true" ]; then
        echo ">>> Using external host directory as /workspace ($(du -sh /workspace 2>/dev/null | awk '{print $1}'))"
    else
        echo ">>> Using host-mounted /workspace ($(du -sh /workspace 2>/dev/null | awk '{print $1}'))"
    fi
elif [ -n "$REPO_URL" ]; then
    if ! command -v git >/dev/null 2>&1; then
        echo ">>> ERROR: git not installed in the image; cannot clone $REPO_URL"
        echo ">>> Use docker_image fully-loaded (or an image with git)."
        exit 127
    fi
    echo ">>> Cloning $REPO_URL${REPO_BRANCH:+ (branch $REPO_BRANCH)} ..."
    set +e
    if [ -n "$REPO_BRANCH" ]; then
        git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" /workspace
        clone_rc=$?
    else
        git clone --depth 1 "$REPO_URL" /workspace
        clone_rc=$?
    fi
    set -e
    if [ "$clone_rc" -ne 0 ]; then
        echo ">>> ERROR: git clone failed (exit $clone_rc)"
        echo ">>>   url:    $REPO_URL"
        echo ">>>   branch: ${REPO_BRANCH:-"(default)"}"
        if [ -z "${GH_TOKEN:-}" ]; then
            echo ">>> Public HTTPS repos need network + git only."
            echo ">>> Private repos need GH_TOKEN (persona github_token / github_token_env, or host GH_TOKEN)."
        fi
        exit "$clone_rc"
    fi
    echo ">>> Clone complete."
    if [ -n "$REPO_COMMIT" ] && command -v git >/dev/null 2>&1; then
        echo ">>> Checking out REPO_COMMIT=$REPO_COMMIT ..."
        set +e
        git -C /workspace fetch --depth 1 origin "$REPO_COMMIT" >/dev/null 2>&1 \
            || git -C /workspace fetch origin "$REPO_COMMIT" >/dev/null 2>&1
        git -C /workspace checkout --quiet "$REPO_COMMIT" 2>/dev/null \
            || echo ">>> WARNING: could not checkout $REPO_COMMIT"
        set -e
    fi
else
    echo ">>> No REPO_URL — empty /workspace."
    mkdir -p /workspace
fi

cd /workspace

# Re-apply git↔gh wiring inside /workspace (local config can override globals in some images).
if [ -n "${GH_TOKEN:-}" ] && [ -d /workspace/.git ] && command -v git >/dev/null 2>&1; then
    set +e
    git config credential.helper '!gh auth git-credential' >/dev/null 2>&1
    set -e
fi

# Host-staged skill packs (persona skills + plugin skills) → writable ~/.grok/skills.
# One path: copy from /groket-skills-stage (no bind-mount onto ~/.grok/skills).
_gte_seed_skills_from_stage() {
    local stage="${1:-/groket-skills-stage}"
    local dest="${2:-/root/.grok/skills}"
    if [ ! -d "$stage" ]; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo ">>> WARNING: skills stage present but python3 missing — skip seed"
        return 0
    fi
    mkdir -p "$dest"
    python3 - "$stage" "$dest" <<'PY'
import shutil
import sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
n = 0
for child in sorted(src.iterdir()):
    if not child.is_dir():
        continue
    target = dst / child.name
    try:
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(child, target)
        n += 1
    except OSError as exc:
        print(f">>> WARNING: could not seed skill {child.name}: {exc}", file=sys.stderr)
if n:
    print(f">>> seeded {n} skill pack(s) into {dst}")
PY
}
_gte_seed_skills_from_stage

# Install persona/runner plugins from the host-staged stage dir (preferred) or
# by git-cloning source_url (origin/main behaviour) when no checkout is present.
# Manifest: /groket-plugins/plugins-manifest.json  (or legacy single-file mount).
# After install: link skills under installed-plugins into ~/.grok/skills when
# missing; recreate parent path aliases when RESUME_PLUGIN_DIR_ALIASES is set.
_gte_install_plugins_from_manifest() {
    local stage="${1:-/groket-plugins}"
    local manifest="$stage/plugins-manifest.json"
    # Legacy: orchestrator mounted only the JSON file at a fixed path.
    if [ ! -f "$manifest" ] && [ -f /groket-plugins-manifest.json ]; then
        manifest="/groket-plugins-manifest.json"
        stage=""
    fi
    if [ ! -f "$manifest" ]; then
        return 0
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo ">>> WARNING: plugins manifest present but python3 missing — skip install"
        return 0
    fi
    if ! command -v grok >/dev/null 2>&1; then
        echo ">>> WARNING: plugins manifest present but grok missing — skip install"
        return 0
    fi
    echo ">>> Installing Grok plugins from manifest ..."
    python3 - "$manifest" "$stage" <<'PY'
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

manifest_path = Path(sys.argv[1])
stage_raw = (sys.argv[2] or "").strip()
stage = Path(stage_raw) if stage_raw else None
try:
    items = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    print(f">>> WARNING: cannot read plugins manifest: {exc}", file=sys.stderr)
    sys.exit(0)
if not isinstance(items, list):
    sys.exit(0)

have_git = shutil.which("git") is not None


def _install_from_dir(name: str, src: Path, label: str) -> None:
    try:
        r = subprocess.run(
            ["grok", "plugin", "install", "--trust", str(src)],
            capture_output=True,
            timeout=300,
            text=True,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode != 0 or out.lower().startswith("error") or "install failed" in out.lower():
            print(
                f">>> WARNING: plugin {name} install failed: {out[:800] or r.returncode}",
                file=sys.stderr,
            )
        else:
            print(f">>> plugin {name} installed ({label})")
    except Exception as exc:
        print(f">>> WARNING: plugin {name} install failed: {exc}", file=sys.stderr)


def _clone_and_install(name: str, url: str, sha: str) -> None:
    if not have_git:
        print(
            f">>> WARNING: plugin {name} needs git clone of {url} but git is missing",
            file=sys.stderr,
        )
        return
    tmp = Path(tempfile.mkdtemp(prefix=f"groket-pl-{name}-"))
    src = tmp / "src"
    try:
        subprocess.run(
            ["git", "clone", "--quiet", url, str(src)],
            check=True,
            capture_output=True,
            timeout=300,
            text=True,
        )
        if sha:
            subprocess.run(
                ["git", "-C", str(src), "checkout", "--quiet", sha],
                check=True,
                capture_output=True,
                timeout=60,
                text=True,
            )
        _install_from_dir(name, src, url)
    except Exception as exc:
        err = getattr(exc, "stderr", None) or exc
        print(f">>> WARNING: plugin {name} install failed: {err}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


for item in items:
    if not isinstance(item, dict):
        continue
    name = str(item.get("name") or "").strip()
    rel = str(item.get("checkout") or "").strip()
    url = str(item.get("source_url") or "").strip()
    sha = str(item.get("sha") or "").strip()
    if not name:
        continue
    # Prefer host-staged checkout (no network in the container).
    if rel and stage is not None:
        src = stage / rel
        if src.is_dir() and any(src.iterdir()):
            _install_from_dir(name, src, rel)
            continue
        print(
            f">>> WARNING: plugin {name} checkout missing at {src} — try source_url",
            file=sys.stderr,
        )
    if url:
        _clone_and_install(name, url, sha)
        continue
    print(
        f">>> WARNING: plugin {name} has no staged checkout and no source_url — skip",
        file=sys.stderr,
    )

# Ensure ~/.grok/skills has every skill shipped by installed plugins (fill gaps).
skills_home = Path("/root/.grok/skills")
skills_home.mkdir(parents=True, exist_ok=True)
ip_root = Path("/root/.grok/installed-plugins")
if ip_root.is_dir():
    filled = 0
    for child in sorted(ip_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        sk = child / "skills"
        if not sk.is_dir():
            continue
        for skill_dir in sorted(sk.iterdir()):
            if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
                continue
            dest = skills_home / skill_dir.name
            if dest.exists():
                continue
            try:
                shutil.copytree(skill_dir, dest)
                filled += 1
            except OSError:
                pass
    if filled:
        print(f">>> filled {filled} plugin skill(s) into {skills_home}")

# Fork: parent chat may hardcode installed-plugins/<alias>/… from the parent container.
aliases = [
    a.strip()
    for a in (os.environ.get("RESUME_PLUGIN_DIR_ALIASES") or "").split(",")
    if a.strip()
]
if aliases and ip_root.is_dir():
    targets = [
        p
        for p in ip_root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / "skills").is_dir()
    ]
    if not targets:
        print(">>> WARNING: RESUME_PLUGIN_DIR_ALIASES set but no plugin install found", file=sys.stderr)
    else:
        target = max(targets, key=lambda p: p.stat().st_mtime)
        for alias in aliases:
            if alias == target.name:
                continue
            link = ip_root / alias
            if link.exists() or link.is_symlink():
                continue
            try:
                link.symlink_to(target.name, target_is_directory=True)
                print(f">>> resume plugin path alias {alias} → {target.name}")
            except OSError as exc:
                print(f">>> WARNING: could not alias {alias}: {exc}", file=sys.stderr)
PY
}

_gte_install_plugins_from_manifest

# Multiline initial commands (setup.sh baked into the image). Runs after clone
# so commands can populate /workspace, install packages, write seed files, etc.
if [ "$SKIP_SETUP" != "1" ] && [ -x /groket-setup.sh ]; then
    if grep -qvE '^[[:space:]]*(#|$)' /groket-setup.sh 2>/dev/null; then
        echo ">>> Running initial commands (/groket-setup.sh) ..."
        # Don't abort the whole job if setup fails unless set -e inside the script.
        set +e
        /bin/bash /groket-setup.sh
        setup_rc=$?
        set -e
        if [ "$setup_rc" -ne 0 ]; then
            echo ">>> WARNING: initial commands exited with code $setup_rc (continuing to grok)"
        else
            echo ">>> Initial commands done."
        fi
    fi
fi

echo "============================================"
echo "  Model:  $MODEL"
if [ -n "$REPO_URL" ]; then
    echo "  Repo:   $REPO_URL${REPO_BRANCH:+ @ $REPO_BRANCH}"
else
    echo "  Repo:   (none — no-repo job)"
fi
if [ -n "${GH_TOKEN:-}" ]; then
    if [ "${GITHUB_WRITE:-0}" = "1" ]; then
        echo "  GH:     write token injected (git+gh; prefer GH_TOKEN on host)"
    else
        echo "  GH:     token injected (GH_TOKEN set)"
    fi
else
    echo "  GH:     no token (pivot / unauthenticated)"
fi
echo "  CWD:    $(pwd)"
echo "============================================"
echo ""

# Resolve prompt into a file. Passing `-p "$PROMPT"` breaks when:
#   - PROMPT is empty / whitespace-only  → "prompt is empty"
#   - PROMPT starts with `-` (e.g. `--help`) → argparse eats it ("value required for --single")
#   - PROMPT has newlines/special chars  → env truncation / shell issues
# Prefer a host-mounted /groket-prompt.txt (orchestrator writes this); fall back to PROMPT env.
PROMPT_FILE="/tmp/groket-prompt.txt"
if [ -f /groket-prompt.txt ] && [ -s /groket-prompt.txt ]; then
    cp /groket-prompt.txt "$PROMPT_FILE"
elif [ -n "$PROMPT" ]; then
    # Preserve exact bytes from env (may still lose newlines if docker stripped them)
    printf '%s' "$PROMPT" > "$PROMPT_FILE"
    # If env had no trailing newline but content exists, fine; trim only all-whitespace
fi

# Trim: treat all-whitespace as missing
if [ ! -s "$PROMPT_FILE" ] || ! grep -q '[^[:space:]]' "$PROMPT_FILE" 2>/dev/null; then
    echo ">>> ERROR: No prompt provided (empty /groket-prompt.txt and PROMPT env)."
    echo ">>> Set a prompt in the Runner / task YAML before launching."
    exit 2
fi

echo ">>> Prompt file: $PROMPT_FILE ($(wc -c < "$PROMPT_FILE") bytes)"
# Prompt is exactly what the operator set (Runner / task YAML) — no extra preamble.

# True when *path* is resume substrate or a live symlink into it (not a real eval session).
_gte_is_resume_seed_path() {
    local p="$1"
    case "$p" in
        */.groket-resume-seed|*/.groket-resume-seed/*) return 0 ;;
    esac
    if [ -L "$p" ]; then
        local real
        real=$(readlink -f "$p" 2>/dev/null || true)
        case "$real" in
            */.groket-resume-seed|*/.groket-resume-seed/*) return 0 ;;
        esac
    fi
    return 1
}

# Primary session under bind-mounted traces (never a Grok subagent).
# Logic lives in /groket_find_primary_session.py (copied into the thin image).
_GTE_FIND_PRIMARY_PY="${_GTE_FIND_PRIMARY_PY:-/groket_find_primary_session.py}"
_GTE_SESSIONS_ROOT="${_GTE_SESSIONS_ROOT:-/root/.grok/sessions}"

_gte_find_session() {
    # Full path of newest/sticky primary session dir (share helper).
    if [ -f "$_GTE_FIND_PRIMARY_PY" ]; then
        python3 "$_GTE_FIND_PRIMARY_PY" "$_GTE_SESSIONS_ROOT" --path \
            ${SESSION_ID:+--preferred "$SESSION_ID"} 2>/dev/null || true
    fi
}

_gte_resolve_primary_session_id() {
    # Basename id for multi-turn --resume. Prefers sticky primary, then
    # prompt_history first row, then newest primary by mtime.
    local preferred="${1:-}"
    if [ ! -f "$_GTE_FIND_PRIMARY_PY" ]; then
        return 0
    fi
    if [ -n "$preferred" ]; then
        python3 "$_GTE_FIND_PRIMARY_PY" "$_GTE_SESSIONS_ROOT" --preferred "$preferred" 2>/dev/null || true
    else
        python3 "$_GTE_FIND_PRIMARY_PY" "$_GTE_SESSIONS_ROOT" 2>/dev/null || true
    fi
}

_gte_session_is_primary() {
    local sid="${1:-}"
    [ -n "$sid" ] || return 1
    [ -f "$_GTE_FIND_PRIMARY_PY" ] || return 1
    python3 "$_GTE_FIND_PRIMARY_PY" "$_GTE_SESSIONS_ROOT" --check "$sid" 2>/dev/null
}

# One share snapshot (force=1 refreshes even if a prior URL exists).
_gte_share_once() {
    local force_flag="${1:-}"
    local sess
    sess=$(_gte_find_session)
    if [ -z "$sess" ] || [ ! -d "$sess" ]; then
        echo ">>> [share] no session dir yet"
        return 1
    fi
    local sid
    sid=$(basename "$sess")
    echo ">>> [share] snapshot for $sid force=${force_flag:-0}"
    if [ -f /groket-share-once.py ]; then
        # Preserve helper exit codes: 3 = permanent entitlement failure.
        python3 /groket-share-once.py "$sid" "$sess" $force_flag
        return $?
    fi
    echo ">>> [share] /groket-share-once.py missing in image"
    return 1
}

# Background: periodic mid-run shares (default on ALL images; entrypoint always starts this).
# Final share runs after agent exits (below). Opt-out only via SHARE_DISABLE=1.
# Cadence tuned for less jitter / fewer overlapping ``grok share`` calls:
#   SHARE_INITIAL_DELAY_SECS (default 30) — wait for session + first turns
#   SHARE_INTERVAL_SECS (default 60) — min age before re-snapshot once URL exists
#   SHARE_LOOP_SLEEP_SECS (default 20) — idle poll between attempts
# First successful share uses non-force; subsequent snapshots force so the share page advances.
# Permanent share entitlement failure — stop looping (do not spam ``grok share``).
_gte_share_is_fatal_error() {
    local sess="$1"
    local f="$sess/groket-share.json"
    [ -f "$f" ] || return 1
    # Match account/plan disable messages from ``grok share``.
    if grep -qiE 'sharing is not available|session sharing is not available|share is not available' "$f" 2>/dev/null; then
        return 0
    fi
    if grep -qi 'not available for your account' "$f" 2>/dev/null && grep -qi 'shar' "$f" 2>/dev/null; then
        return 0
    fi
    return 1
}

_gte_share_loop() {
    local interval="${SHARE_INTERVAL_SECS:-60}"
    local loop_sleep="${SHARE_LOOP_SLEEP_SECS:-20}"
    sleep "${SHARE_INITIAL_DELAY_SECS:-30}"
    local had_url=0
    while true; do
        local sess
        sess=$(_gte_find_session)
        if [ -z "$sess" ] || [ ! -d "$sess" ]; then
            sleep "$loop_sleep"
            continue
        fi
        # Account cannot share — exit loop permanently (SHARE_DISABLE-equivalent).
        if _gte_share_is_fatal_error "$sess"; then
            echo ">>> [share] permanent failure in groket-share.json — stopping share loop"
            return 0
        fi
        if [ -f "$sess/groket-share.json" ] && grep -q '"share_url": "https' "$sess/groket-share.json" 2>/dev/null; then
            had_url=1
            local age=9999
            if command -v stat >/dev/null 2>&1; then
                age=$(( $(date +%s) - $(stat -c %Y "$sess/groket-share.json" 2>/dev/null || echo 0) ))
            fi
            if [ "$age" -lt "$interval" ]; then
                sleep "$loop_sleep"
                continue
            fi
        fi
        # Force only when refreshing an existing share (reduces first-snapshot races).
        local force_flag=""
        if [ "$had_url" -eq 1 ]; then
            force_flag="1"
        fi
        # Exit code 3 = permanent entitlement failure from groket-share-once.py
        _gte_share_once "$force_flag"
        local rc=$?
        if [ "$rc" -eq 3 ] || _gte_share_is_fatal_error "$sess"; then
            echo ">>> [share] permanent failure (rc=$rc) — stopping share loop"
            return 0
        fi
        sleep "$loop_sleep"
    done
}

SHARE_LOOP_PID=""
# Start share loop in background (best-effort; never blocks the main agent).
# Default ON for every profile; set SHARE_DISABLE=1 in container env to skip.
if [ "${SHARE_DISABLE:-}" = "1" ] || [ "${SHARE_DISABLE:-}" = "true" ]; then
    echo ">>> [share] disabled via SHARE_DISABLE (agent only; no groket-share.json loop)"
elif command -v python3 >/dev/null 2>&1 && command -v grok >/dev/null 2>&1; then
    _gte_share_loop &
    SHARE_LOOP_PID=$!
    echo ">>> [share] in-container share loop started (pid $SHARE_LOOP_PID) — default for all images"
else
    echo ">>> [share] python3/grok missing — no in-container share (TUI will show pending; rebuild image)"
fi

# Use stdbuf to force line-buffered stdout when available (GNU coreutils).
if command -v stdbuf >/dev/null 2>&1; then
    WRAP=(stdbuf -oL)
else
    WRAP=()
fi

# Multi-turn control files on the bind-mounted sessions volume (host can write).
TURN_DIR="${TURN_DIR:-/root/.grok/sessions/.groket-turn}"
mkdir -p "$TURN_DIR"
TURN_STATUS="$TURN_DIR/status.json"
TURN_NEXT="$TURN_DIR/next-prompt.txt"
TURN_CMD="$TURN_DIR/command" # "follow_up" | "done" (host writes)
TURN_SCRIPT="$TURN_DIR/scripted-turns.json" # optional JSON list of prompt strings

_gte_write_turn_status() {
    local state="$1"
    local session_id="${2:-}"
    local turn="${3:-0}"
    python3 - "$TURN_STATUS" "$state" "$session_id" "$turn" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
path, state, sid, turn = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
Path(path).write_text(
    json.dumps({"state": state, "session_id": sid, "turn": int(turn)}, indent=2) + "\n",
    encoding="utf-8",
)
PY
}

_gte_primary_id_file() {
    echo "${TURN_DIR}/primary-session-id"
}

_gte_load_primary_session_id() {
    local f
    f="$(_gte_primary_id_file)"
    if [ -f "$f" ]; then
        tr -d '[:space:]' < "$f"
    fi
}

_gte_save_primary_session_id() {
    local sid="${1:-}"
    [ -n "$sid" ] || return 0
    mkdir -p "$TURN_DIR"
    printf '%s\n' "$sid" > "$(_gte_primary_id_file)"
}

_gte_latest_session_id() {
    _gte_resolve_primary_session_id "${SESSION_ID:-}"
}

_gte_refresh_session_id() {
    # Authoritative file on the turn gate, else discover primary once and persist.
    local from_file discovered
    from_file="$(_gte_load_primary_session_id || true)"
    if [ -n "$from_file" ] && _gte_session_is_primary "$from_file"; then
        SESSION_ID="$from_file"
        return 0
    fi
    if [ -n "$SESSION_ID" ] && _gte_session_is_primary "$SESSION_ID"; then
        _gte_save_primary_session_id "$SESSION_ID"
        return 0
    fi
    if [ -n "$SESSION_ID" ]; then
        echo ">>> SESSION_ID=$SESSION_ID is not a primary session — re-resolving"
    fi
    discovered="$(_gte_resolve_primary_session_id "${SESSION_ID:-}" || true)"
    if [ -n "$discovered" ]; then
        if [ -n "$SESSION_ID" ] && [ "$SESSION_ID" != "$discovered" ]; then
            echo ">>> Multi-turn session id $SESSION_ID → $discovered (primary)"
        fi
        SESSION_ID="$discovered"
        _gte_save_primary_session_id "$SESSION_ID"
        return 0
    fi
    return 1
}

_gte_run_grok_turn() {
    local prompt_file="$1"
    local resume_id="${2:-}"
    # Optional 3rd arg: "fork" → --fork-session (new Grok session id from parent history).
    local resume_mode="${3:-}"
    # Optional 4th arg: UUID for --session-id when forking (names the new session).
    local fork_sid="${4:-}"
    # Strip optional model:effort if MODEL was passed as a compound token.
    local model_id="${MODEL%%:*}"
    # Current Grok Build CLI flags only (no help-grep multipath).
    # Default: --always-approve (non-interactive evals). Opt-in YOLO=1 → --yolo
    # (same auto-approve family; sets yolo_mode in session telemetry / config).
    local -a cmd=(grok -m "$model_id" --output-format streaming-json --prompt-file "$prompt_file")
    if [ "${YOLO:-0}" = "1" ] || [ "${YOLO:-}" = "true" ]; then
        cmd+=(--yolo)
    else
        cmd+=(--always-approve)
    fi
    local effort="${REASONING_EFFORT:-}"
    if [ -z "$effort" ] && [ "$MODEL" != "$model_id" ]; then
        effort="${MODEL#*:}"
    fi
    effort=$(printf '%s' "$effort" | tr '[:upper:]' '[:lower:]')
    case "$effort" in
        xhigh|max) effort=high ;;
        low|medium|high) ;;
        *) effort="" ;;
    esac
    if [ -n "$effort" ]; then
        cmd+=(--effort "$effort")
    fi
    local max_turns="${MAX_TURNS:-50}"
    if [ -n "$max_turns" ] && [ "$max_turns" -gt 0 ] 2>/dev/null; then
        cmd+=(--max-turns "$max_turns")
    fi
    if [ -n "$resume_id" ]; then
        cmd+=(--resume "$resume_id")
        if [ "$resume_mode" = "fork" ] || [ "$resume_mode" = "1" ]; then
            cmd+=(--fork-session)
            if [ -n "$fork_sid" ]; then
                cmd+=(--session-id "$fork_sid")
            fi
        fi
        if [ "${RESTORE_CODE:-0}" = "1" ] || [ "${RESTORE_CODE:-}" = "true" ]; then
            cmd+=(--restore-code)
        fi
    fi
    echo ">>> Launching: ${cmd[*]}"
    set +e
    "${WRAP[@]}" "${cmd[@]}"
    local rc=$?
    set -e
    return "$rc"
}

# Scripted batch turns (non-interactive multi-prompt) from host-written JSON list.
_gte_pop_scripted_turn() {
    python3 - "$TURN_SCRIPT" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    sys.exit(0)
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(data, list) or not data:
    sys.exit(0)
prompt = data.pop(0)
p.write_text(json.dumps(data) + "\n", encoding="utf-8")
print(prompt, end="")
PY
}

AGENT_EXIT=0
SESSION_ID=""
TURN_INDEX=0
INTERACTIVE="${INTERACTIVE:-0}"
# Host-seeded ended session: first turn uses grok --resume --fork-session (new branch id).
RESUME_SESSION_ID="${RESUME_SESSION_ID:-}"
# Optional host-chosen UUID for the forked session (--session-id with --fork-session).
FORK_SESSION_ID="${FORK_SESSION_ID:-}"
RESUME_FORK="${RESUME_FORK:-1}"

# Fail loud when fork is required but the image CLI is too old (no silent skip).
if [ -n "$RESUME_SESSION_ID" ] && [ "$RESUME_FORK" != "0" ] && [ "$RESUME_FORK" != "false" ]; then
    if ! grok --help 2>&1 | grep -qE -- '--fork-session'; then
        echo ">>> ERROR: grok CLI lacks --fork-session (image CLI too old for fork-resume)."
        echo ">>> Rebuild the eval Docker image so install.sh pulls a current Grok Build CLI."
        grok --version 2>&1 || true
        exit 3
    fi
fi

# Do NOT exec: we need a final share after the agent exits (exec would kill this shell
# and the share loop, leaving the share page mid-turn without the last assistant msg).
while true; do
    TURN_INDEX=$((TURN_INDEX + 1))
    if [ "$TURN_INDEX" -eq 1 ]; then
        if [ -n "$RESUME_SESSION_ID" ]; then
            if [ "$RESUME_FORK" = "0" ] || [ "$RESUME_FORK" = "false" ]; then
                echo ">>> Resuming Grok session id=$RESUME_SESSION_ID (same id, no fork)"
                SESSION_ID="$RESUME_SESSION_ID"
                _gte_run_grok_turn "$PROMPT_FILE" "$RESUME_SESSION_ID" ""
            else
                echo ">>> Fork-resume from parent=$RESUME_SESSION_ID fork=${FORK_SESSION_ID:-auto}"
                # Host-named fork id is authoritative once the turn starts.
                if [ -n "$FORK_SESSION_ID" ]; then
                    SESSION_ID="$FORK_SESSION_ID"
                fi
                _gte_run_grok_turn "$PROMPT_FILE" "$RESUME_SESSION_ID" "fork" "${FORK_SESSION_ID:-}"
            fi
        else
            _gte_run_grok_turn "$PROMPT_FILE" ""
        fi
        AGENT_EXIT=$?
    else
        # Later turns continue the primary (or forked) session id, no second fork.
        if [ -z "$SESSION_ID" ]; then
            echo ">>> ERROR: multi-turn resume without SESSION_ID (turn $TURN_INDEX) — aborting further turns"
            AGENT_EXIT=4
            break
        fi
        if ! _gte_session_is_primary "$SESSION_ID" 2>/dev/null; then
            # Should not happen after refresh; refuse to resume a subagent.
            if ! _gte_refresh_session_id; then
                echo ">>> ERROR: cannot resolve primary session for turn $TURN_INDEX"
                AGENT_EXIT=4
                break
            fi
        fi
        _gte_run_grok_turn "$PROMPT_FILE" "$SESSION_ID"
        AGENT_EXIT=$?
    fi
    # Prefer host-named fork id; else resolve/refresh primary (never sticky subagent).
    if [ -n "$FORK_SESSION_ID" ] && [ -n "$RESUME_SESSION_ID" ] \
        && [ "$RESUME_FORK" != "0" ] && [ "$RESUME_FORK" != "false" ]; then
        SESSION_ID="$FORK_SESSION_ID"
        _gte_save_primary_session_id "$SESSION_ID"
    else
        _gte_refresh_session_id || true
    fi
    echo ">>> Agent turn $TURN_INDEX exited with code $AGENT_EXIT (session=${SESSION_ID:-unknown})"
    # Mid-run chown so live TUI / host tools see non-root prompt_history.jsonl etc.
    _gte_fix_bind_ownership

    # Non-interactive scripted follow-ups (batch tasks.turns)
    scripted="$(_gte_pop_scripted_turn || true)"
    if [ -n "$scripted" ]; then
        if [ -z "$SESSION_ID" ]; then
            echo ">>> ERROR: scripted follow-up ready but no primary SESSION_ID — stopping"
            break
        fi
        printf '%s' "$scripted" > "$PROMPT_FILE"
        echo ">>> Scripted follow-up turn queued ($(wc -c < "$PROMPT_FILE") bytes) resume=$SESSION_ID"
        continue
    fi

    # Host sent a "last turn" follow-up — do not await further prompts.
    if [ -f "$TURN_DIR/final_turn" ]; then
        echo ">>> Final-turn flag set — not awaiting further follow-ups"
        rm -f "$TURN_DIR/final_turn" "$TURN_CMD" "$TURN_NEXT"
        _gte_write_turn_status "done" "$SESSION_ID" "$TURN_INDEX"
        break
    fi

    # Interactive: wait for host follow-up or done
    if [ "$INTERACTIVE" = "1" ] || [ "$INTERACTIVE" = "true" ]; then
        rm -f "$TURN_CMD" "$TURN_NEXT"
        _gte_write_turn_status "awaiting_follow_up" "$SESSION_ID" "$TURN_INDEX"
        echo ">>> Waiting for follow-up (write $TURN_NEXT + $TURN_CMD=follow_up|done)"
        idle_max="${INTERACTIVE_IDLE_SECS:-86400}"
        waited=0
        while [ "$waited" -lt "$idle_max" ]; do
            if [ -f "$TURN_CMD" ]; then
                cmd=$(tr -d '[:space:]' < "$TURN_CMD" | tr '[:upper:]' '[:lower:]')
                if [ "$cmd" = "done" ]; then
                    echo ">>> Host marked interactive session done"
                    _gte_write_turn_status "done" "$SESSION_ID" "$TURN_INDEX"
                    break 2
                fi
                if [ "$cmd" = "follow_up" ] && [ -s "$TURN_NEXT" ]; then
                    # Preserve final_turn across rm of command/next (host may set it).
                    FINAL_TURN=0
                    [ -f "$TURN_DIR/final_turn" ] && FINAL_TURN=1
                    cp "$TURN_NEXT" "$PROMPT_FILE"
                    rm -f "$TURN_CMD" "$TURN_NEXT"
                    [ "$FINAL_TURN" = "1" ] && printf '1\n' > "$TURN_DIR/final_turn"
                    _gte_write_turn_status "running" "$SESSION_ID" "$TURN_INDEX"
                    continue 2
                fi
            fi
            sleep 2
            waited=$((waited + 2))
        done
        echo ">>> Interactive idle timeout — finishing"
        _gte_write_turn_status "timeout" "$SESSION_ID" "$TURN_INDEX"
    fi
    break
done

echo ">>> Agent finished (last exit $AGENT_EXIT) — stopping share loop, then final snapshot"
if [ -n "$SHARE_LOOP_PID" ]; then
    kill "$SHARE_LOOP_PID" 2>/dev/null || true
    wait "$SHARE_LOOP_PID" 2>/dev/null || true
fi
if [ "${SHARE_DISABLE:-}" = "1" ] || [ "${SHARE_DISABLE:-}" = "true" ]; then
    echo ">>> [share] final snapshot skipped (SHARE_DISABLE)"
else
    # Settle so last chat/events/summary flush to the bind mount before share reads them.
    sleep "${SHARE_FINAL_DELAY_SECS:-5}"
    _gte_share_once 1 || true
    # Second pass: share CLI sometimes needs a beat after the first force snapshot.
    sleep 2
    _gte_share_once 1 || true
fi

# EXIT trap: workspace seed snapshot + chown sessions volume.
exit "$AGENT_EXIT"
