"""Docker base-image profiles for eval containers.

Profiles let the runner/orchestrator bake a toolset into the image at build
time (before the per-task setup.sh). Extend :data:`FULLY_LOADED_APT_PACKAGES`
and :data:`FULLY_LOADED_EXTRA_RUN` when you want more "fully loaded" tools.

Runner / configs accept either:
  * a normal Docker image (``ubuntu:24.04``, ``debian:bookworm``, …)
  * a profile alias (``fully-loaded``, ``groket:fully-loaded``, ``full``, …)
  * ``image@profile`` (e.g. ``ubuntu:24.04@fully-loaded``) — base + profile

Stored ``docker_image`` strings are preserved as entered so recipes round-trip.

Default profile is :data:`DEFAULT_DOCKER_IMAGE` (``fully-loaded``): full toolset.
**All profiles** use the same entrypoint share loop (``grok share`` → ``groket-share.json``);
``minimal`` only skips the heavy tool layers (still installs python3 + Grok CLI so share works).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Runner / configs / batch default — fully-loaded so share URLs work out of the box.
DEFAULT_DOCKER_IMAGE = "fully-loaded"
# Underlying OS image when a profile alias does not specify one.
DEFAULT_BASE_OS_IMAGE = "ubuntu:24.04"

# apt / apk / dnf package names. Keep this list the single source of truth for
# "what fully-loaded means"; the Dockerfile generator maps them per package manager.
FULLY_LOADED_APT_PACKAGES: list[str] = [
    # VCS / CLI agents
    "git",
    "curl",
    "ca-certificates",
    "wget",
    "jq",
    "ripgrep",
    # Shell / utilities
    "bash",
    "coreutils",
    "findutils",
    "grep",
    "sed",
    "gawk",
    "tar",
    "gzip",
    "unzip",
    "openssh-client",
    "sudo",
    "less",
    "file",
    "procps",
    "locales",
    # Build / compile helpers (many agent tasks need these)
    "build-essential",
    "pkg-config",
    # Languages / runtimes
    "python3",
    "python3-pip",
    "python3-venv",
    "perl",
    "ruby",
    # Node: not from distro apt (often ancient). Latest current via NodeSource
    # in FULLY_LOADED_EXTRA_RUN so we get a modern node/npm/npx.
]

# Alpine package names when the base image uses apk (subset / renames).
FULLY_LOADED_APK_PACKAGES: list[str] = [
    "git",
    "curl",
    "ca-certificates",
    "wget",
    "jq",
    "ripgrep",
    "bash",
    "coreutils",
    "findutils",
    "grep",
    "sed",
    "gawk",
    "tar",
    "gzip",
    "unzip",
    "openssh-client",
    "sudo",
    "less",
    "file",
    "procps",
    "build-base",
    "pkgconf",
    "python3",
    "py3-pip",
    "perl",
    "ruby",
    # Alpine: install latest node from NodeSource-equivalent (fnm/binary) in EXTRA_RUN
]

# dnf/rpm package names (Fedora/RHEL family).
FULLY_LOADED_DNF_PACKAGES: list[str] = [
    "git",
    "curl",
    "ca-certificates",
    "wget",
    "jq",
    "ripgrep",
    "bash",
    "coreutils",
    "findutils",
    "grep",
    "sed",
    "gawk",
    "tar",
    "gzip",
    "unzip",
    "openssh-clients",
    "sudo",
    "less",
    "file",
    "procps-ng",
    "gcc",
    "gcc-c++",
    "make",
    "pkgconf",
    "python3",
    "python3-pip",
    "perl",
    "ruby",
]

# Extra Dockerfile RUN lines *after* package install (gh CLI, Node, etc.).
# Append strings here when packages aren't in distro repos or need installers.
FULLY_LOADED_EXTRA_RUN: list[str] = [
    # GitHub CLI (official apt repo when on Debian/Ubuntu; else install script)
    r"""RUN set -eux; \
    if command -v gh >/dev/null 2>&1; then echo "gh already present"; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update; \
        apt-get install -y --no-install-recommends curl ca-certificates gnupg; \
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg; \
        chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg; \
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
            > /etc/apt/sources.list.d/github-cli.list; \
        apt-get update && apt-get install -y gh && rm -rf /var/lib/apt/lists/*; \
    else \
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /tmp/gh.key 2>/dev/null || true; \
        (type -p wget >/dev/null && wget -qO- https://cli.github.com/packages/install.sh | bash) \
            || (curl -fsSL https://raw.githubusercontent.com/cli/cli/trunk/docs/install_linux.md >/dev/null 2>&1; \
                curl -fsSL https://cli.github.com/install.sh | bash) \
            || echo "WARNING: could not install gh CLI — agents may use git/API only"; \
    fi""",
    # Node.js: current (latest) from NodeSource on Debian/Ubuntu; binary tarball elsewhere.
    # Avoids distro nodejs/npm (often Node 12–18). Rebuild groket-base fully-loaded to pick up.
    r"""RUN set -eux; \
    if command -v node >/dev/null 2>&1 && node -v 2>/dev/null | grep -qE '^v(2[2-9]|[3-9][0-9])'; then \
        echo "node already recent: $(node -v)"; \
    elif command -v apt-get >/dev/null 2>&1; then \
        apt-get update; \
        apt-get install -y --no-install-recommends curl ca-certificates gnupg; \
        ( apt-get remove -y nodejs npm libnode* 2>/dev/null || true ); \
        curl -fsSL https://deb.nodesource.com/setup_current.x | bash -; \
        apt-get install -y nodejs; \
        rm -rf /var/lib/apt/lists/*; \
        node -v && npm -v; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache libstdc++; \
        ARCH=$(uname -m); \
        case "$ARCH" in x86_64) NARCH=x64;; aarch64) NARCH=arm64;; *) NARCH=x64;; esac; \
        VER=$(curl -fsSL https://nodejs.org/dist/index.json | sed -n 's/.*"version":"\([^"]*\)".*"lts":false.*/\1/p' | head -1); \
        if [ -z "$VER" ]; then VER=$(curl -fsSL https://nodejs.org/dist/latest/ | sed -n 's/.*node-\(v[0-9.]*\)-linux-.*/\1/p' | head -1); fi; \
        if [ -z "$VER" ]; then VER=v22.14.0; fi; \
        curl -fsSL "https://nodejs.org/dist/${VER}/node-${VER}-linux-${NARCH}.tar.xz" -o /tmp/node.tar.xz; \
        tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1; \
        rm -f /tmp/node.tar.xz; \
        node -v && npm -v; \
    elif command -v dnf >/dev/null 2>&1; then \
        curl -fsSL https://rpm.nodesource.com/setup_current.x | bash -; \
        dnf install -y nodejs && dnf clean all; \
        node -v && npm -v; \
    else \
        echo "WARNING: could not install Node.js on this base"; \
    fi""",
]


@dataclass(frozen=True)
class BaseProfile:
    """A named image profile (minimal vs fully-loaded, …)."""

    id: str
    label: str
    description: str
    # Canonical base image when the user only types the profile alias.
    default_base_image: str = DEFAULT_BASE_OS_IMAGE
    # Install the fully-loaded toolset layers.
    fully_loaded: bool = False
    aliases: tuple[str, ...] = ()


# Register profiles here; add new ones as needed (e.g. "ci-lite", "go-heavy").
PROFILES: dict[str, BaseProfile] = {}


def _register(p: BaseProfile) -> BaseProfile:
    PROFILES[p.id] = p
    for a in p.aliases:
        PROFILES[a] = p
    return p


MINIMAL = _register(
    BaseProfile(
        id="minimal",
        label="Minimal",
        description="git/curl/python3 + Grok CLI + share loop; task setup.sh for the rest",
        default_base_image=DEFAULT_BASE_OS_IMAGE,
        fully_loaded=False,
        aliases=("min", "bare"),
    )
)

FULLY_LOADED = _register(
    BaseProfile(
        id="fully-loaded",
        label="Fully loaded",
        description="Full toolset + share loop (default; TUI displays share URL)",
        default_base_image=DEFAULT_BASE_OS_IMAGE,
        fully_loaded=True,
        aliases=(
            "full",
            "loaded",
            "groket-fully-loaded",
            "groket:fully-loaded",
            "fully_loaded",
            "default",
        ),
    )
)


@dataclass
class ResolvedDockerBase:
    """Result of resolving a runner/config ``docker_image`` string."""

    # Value to store in configs / run.json (user-facing, unchanged when possible).
    stored: str
    # FROM … base image for Dockerfile.
    base_image: str
    profile_id: str
    fully_loaded: bool
    profile_label: str = ""


def list_profiles() -> list[BaseProfile]:
    """Unique profiles (aliases collapsed)."""
    seen: set[str] = set()
    out: list[BaseProfile] = []
    for p in PROFILES.values():
        if p.id in seen:
            continue
        seen.add(p.id)
        out.append(p)
    return out


def profile_help_text() -> str:
    parts = []
    for p in list_profiles():
        aliases = ", ".join(sorted({p.id, *p.aliases}))
        parts.append(f"{p.id} — {p.description} (aliases: {aliases})")
    return " | ".join(parts)


def _norm_key(s: str) -> str:
    return (s or "").strip().lower().replace("_", "-")


def resolve_docker_base(docker_image: str | None) -> ResolvedDockerBase:
    """Map runner input to a real base image + profile flags.

    Examples:
      empty / ``fully-loaded`` / ``default`` → fully-loaded toolset (share loop always on)
      ``ubuntu:24.04`` / ``minimal`` → minimal deps (still share loop + grok CLI)
      ``debian:bookworm@fully-loaded`` → fully-loaded on debian:bookworm
    """
    raw = (docker_image or "").strip() or DEFAULT_DOCKER_IMAGE
    stored = raw

    base_image = raw
    profile = MINIMAL

    # image@profile form
    if "@" in raw and not raw.startswith("@"):
        left, right = raw.rsplit("@", 1)
        left, right = left.strip(), right.strip()
        if left and right:
            base_image = left
            key = _norm_key(right)
            profile = PROFILES.get(key) or PROFILES.get(right.lower()) or MINIMAL

    else:
        key = _norm_key(raw)
        # Bare profile alias (no slash/colon except groket:fully-loaded handled via aliases)
        if key in PROFILES or raw.lower() in PROFILES:
            profile = PROFILES.get(key) or PROFILES[raw.lower()]
            base_image = profile.default_base_image
        else:
            # Treat as normal image; optional suffix in path not used
            base_image = raw
            profile = MINIMAL

    return ResolvedDockerBase(
        stored=stored,
        base_image=base_image or DEFAULT_BASE_OS_IMAGE,
        profile_id=profile.id,
        fully_loaded=profile.fully_loaded,
        profile_label=profile.label,
    )


def _pkg_join(pkgs: list[str]) -> str:
    # Dockerfile line continuation friendly
    return " \\\n            ".join(pkgs)


def fully_loaded_install_dockerfile_block() -> str:
    """RUN blocks installing the fully-loaded toolset (inserted into eval Dockerfile)."""
    apt = _pkg_join(FULLY_LOADED_APT_PACKAGES)
    apk = _pkg_join(FULLY_LOADED_APK_PACKAGES)
    dnf = _pkg_join(FULLY_LOADED_DNF_PACKAGES)

    block = f"""\
RUN if command -v apt-get >/dev/null 2>&1; then \\
        apt-get update && apt-get install -y --no-install-recommends \\
            {apt} \\
        && rm -rf /var/lib/apt/lists/*; \\
    elif command -v apk >/dev/null 2>&1; then \\
        apk add --no-cache \\
            {apk}; \\
    elif command -v dnf >/dev/null 2>&1; then \\
        dnf install -y \\
            {dnf} \\
        && dnf clean all; \\
    fi
"""
    for extra in FULLY_LOADED_EXTRA_RUN:
        block += "\n" + extra.rstrip() + "\n"
    return block.rstrip() + "\n"


def minimal_deps_dockerfile_block() -> str:
    """Baseline deps for every profile (share loop needs python3 + groket-share-once.py).

    Grok CLI is installed in the shared base regardless of fully-loaded vs minimal.
    """
    return """\
# Detect package manager and install baseline deps (share loop + agent always need these)
RUN if command -v apt-get >/dev/null 2>&1; then \\
        apt-get update && apt-get install -y --no-install-recommends \\
            git curl ca-certificates python3 && rm -rf /var/lib/apt/lists/*; \\
    elif command -v apk >/dev/null 2>&1; then \\
        apk add --no-cache git curl ca-certificates bash python3; \\
    elif command -v dnf >/dev/null 2>&1; then \\
        dnf install -y git curl ca-certificates python3 && dnf clean all; \\
    fi
"""


def build_shared_base_dockerfile(*, base_image: str, fully_loaded: bool = False) -> str:
    """Heavy layers only — packages + Grok CLI. No per-task setup/entrypoint.

    Built once as ``groket-base:<profile>-<hash>`` and reused by every eval run so
    apt/gh/grok install is not repeated when only setup.sh changes.
    Templates live under :mod:`groket.docker.resources`.
    """
    from .resources import dockerfile_shared_base

    tool_block = (
        fully_loaded_install_dockerfile_block() if fully_loaded else minimal_deps_dockerfile_block()
    )
    profile_comment = (
        "# Profile: fully-loaded (preinstalled agent toolset; entrypoint always runs share loop)"
        if fully_loaded
        else "# Profile: minimal (baseline + share loop; use setup.sh for task deps)"
    )
    return dockerfile_shared_base(
        base_image=base_image,
        profile_comment=profile_comment,
        tool_block=tool_block,
    )


def build_run_dockerfile(*, shared_base_tag: str) -> str:
    """Thin per-run image: shared base + setup.sh + entrypoint only."""
    from .resources import dockerfile_run

    return dockerfile_run(shared_base_tag=shared_base_tag)


def build_dockerfile(*, base_image: str, fully_loaded: bool = False) -> str:
    """Monolithic Dockerfile for tests; prefer shared base + run split in production."""
    from .resources import dockerfile_monolithic_suffix

    shared = build_shared_base_dockerfile(base_image=base_image, fully_loaded=fully_loaded)
    return shared.rstrip() + dockerfile_monolithic_suffix()


def _slug_image_ref(base_image: str) -> str:
    """ubuntu:24.04 → ubuntu-24.04"""
    s = (base_image or "ubuntu").strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    return s.strip("-")[:48] or "base"


def shared_base_content_hash(*, base_image: str, fully_loaded: bool) -> str:
    """Short hash of the shared Dockerfile so toolset edits get a new tag (rebuild once)."""
    import hashlib

    body = build_shared_base_dockerfile(base_image=base_image, fully_loaded=fully_loaded)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]


def shared_base_image_tag(*, base_image: str, fully_loaded: bool, profile_id: str = "") -> str:
    """Stable local image name for the cached agent base (never deleted per-run)."""
    prof = "fl" if fully_loaded else "min"
    if profile_id and profile_id not in ("minimal", "fully-loaded"):
        prof = re.sub(r"[^a-z0-9]+", "", profile_id.lower())[:12] or prof
    slug = _slug_image_ref(base_image)
    h = shared_base_content_hash(base_image=base_image, fully_loaded=fully_loaded)
    return f"groket-base:{prof}-{slug}-{h}"


def shared_base_build_dirname(*, base_image: str, fully_loaded: bool, profile_id: str = "") -> str:
    """Fixed directory under docker-build/ so layer cache and tags align."""
    tag = shared_base_image_tag(
        base_image=base_image, fully_loaded=fully_loaded, profile_id=profile_id
    )
    # groket-base:fl-ubuntu-24.04-abc123 → fl-ubuntu-24.04-abc123
    return tag.split(":", 1)[-1]


__all__ = [
    "BaseProfile",
    "DEFAULT_BASE_OS_IMAGE",
    "DEFAULT_DOCKER_IMAGE",
    "FULLY_LOADED",
    "FULLY_LOADED_APT_PACKAGES",
    "FULLY_LOADED_APK_PACKAGES",
    "FULLY_LOADED_DNF_PACKAGES",
    "FULLY_LOADED_EXTRA_RUN",
    "MINIMAL",
    "PROFILES",
    "ResolvedDockerBase",
    "build_dockerfile",
    "build_run_dockerfile",
    "build_shared_base_dockerfile",
    "fully_loaded_install_dockerfile_block",
    "list_profiles",
    "profile_help_text",
    "resolve_docker_base",
    "shared_base_build_dirname",
    "shared_base_content_hash",
    "shared_base_image_tag",
]
