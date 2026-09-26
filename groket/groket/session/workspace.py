"""Host-owned workspace checkouts for eval containers.

Each run gets a directory under ``<runs>/checkouts/<id>/`` bind-mounted as
``/workspace``.

Fork materialization (see :func:`prepare_host_checkout`):

1. **reflink CoW** when the filesystem supports it (btrfs, xfs with reflink).
2. Otherwise **do not** silently full-copy multi‑GB trees. Fall back to git
   clone / empty, and log a clear warning. Set
   ``GROKET_ALLOW_FULL_WORKSPACE_COPY=1`` to force a full recursive copy when
   preserving parent dirt is required on non-reflink filesystems (ext4).

Overlay mounts need root/CAP_SYS_ADMIN and are not used on the host path.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

CHECKOUTS_DIRNAME = "checkouts"
_FULL_COPY_ENV = "GROKET_ALLOW_FULL_WORKSPACE_COPY"


def checkouts_root(runs_dir: Path) -> Path:
    """``<runs_dir>/checkouts`` (created if needed)."""
    root = Path(runs_dir) / CHECKOUTS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def checkout_path(runs_dir: Path, checkout_id: str) -> Path:
    """Path for a single run checkout (``checkout_id`` is usually container name)."""
    cid = (checkout_id or "").strip()
    if not cid:
        raise ValueError("checkout_id is empty")
    return checkouts_root(runs_dir) / cid


def reflink_supported(on_path: Path) -> bool:
    """True when ``cp --reflink=always`` works on the filesystem of *on_path*."""
    base = Path(on_path)
    try:
        base = base if base.is_dir() else base.parent
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        with tempfile.TemporaryDirectory(dir=str(base)) as tmp:
            src = Path(tmp) / "src"
            dst = Path(tmp) / "dst"
            src.mkdir()
            (src / "probe").write_text("x\n", encoding="utf-8")
            proc = subprocess.run(
                ["cp", "-a", "--reflink=always", str(src), str(dst)],
                capture_output=True,
                text=True,
            )
            return proc.returncode == 0 and (dst / "probe").is_file()
    except OSError:
        return False


def full_workspace_copy_allowed() -> bool:
    """True when env opts into multi‑GB full copies without reflink."""
    return os.environ.get(_FULL_COPY_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def cow_copy_tree(src: Path, dest: Path, *, require_reflink: bool = False) -> str:
    """Copy *src* directory to *dest*.

    :param require_reflink: When true, use ``--reflink=always`` and raise if
        unsupported. When false, use ``--reflink=auto`` (may full-copy).
    :returns: ``\"reflink\"`` when CoW was requested with always; ``\"auto\"`` for
        auto mode (caller should treat as unknown cheapness on ext4).
    """
    source = Path(src)
    target = Path(dest)
    if not source.is_dir():
        raise FileNotFoundError(f"checkout source not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        from ..runs.run_configs import rmtree_robust

        rmtree_robust(target)
    flag = "--reflink=always" if require_reflink else "--reflink=auto"
    subprocess.run(
        ["cp", "-a", flag, str(source), str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    return "reflink" if require_reflink else "auto"


def parent_checkout_for_session(runs_dir: Path, session_dir: Path) -> Path | None:
    """Host checkout for the container that produced *session_dir*, if present.

    Traces layout: ``<runs>/traces/<container_name>/…/<session_id>/``.
    Checkout layout: ``<runs>/checkouts/<container_name>/``.
    """
    p = Path(session_dir).expanduser().resolve()
    try:
        container_vol = p.parent.parent
        name = container_vol.name
    except (OSError, IndexError):
        return None
    if not name or name.startswith("."):
        return None
    cand = checkout_path(runs_dir, name)
    if cand.is_dir():
        try:
            if any(cand.iterdir()):
                return cand
        except OSError:
            return None
    return None


def resolve_repo_path(repo_path: str | Path) -> Path:
    """Resolve a host directory to bind-mount as ``/workspace`` (no copy).

    :param repo_path: Operator path (``~`` expanded, made absolute).
    :returns: Absolute path to an existing directory.
    :raises ValueError: Empty path.
    :raises FileNotFoundError: Path missing or not a directory.
    """
    raw = str(repo_path or "").strip()
    if not raw:
        raise ValueError("repo_path is empty")
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(f"repo_path not found: {raw}") from exc
    if not path.is_dir():
        raise FileNotFoundError(f"repo_path is not a directory: {path}")
    return path


def is_managed_checkout(runs_dir: Path, path: Path) -> bool:
    """True when *path* is under ``<runs_dir>/checkouts/`` (safe to delete)."""
    try:
        root = checkouts_root(runs_dir).resolve()
        return Path(path).expanduser().resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _git_clone(
    dest: Path,
    *,
    repo_url: str,
    repo_branch: str = "",
    repo_commit: str = "",
) -> None:
    """Clone *repo_url* into empty *dest* (must not exist or be empty)."""
    dest.mkdir(parents=True, exist_ok=True)
    url = repo_url.strip()
    branch = (repo_branch or "").strip()
    commit = (repo_commit or "").strip()
    cmd = ["git", "clone", "--quiet"]
    if branch and not commit:
        cmd.extend(["--depth", "1", "--branch", branch])
    cmd.extend([url, str(dest)])
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if commit:
        fetch = subprocess.run(
            ["git", "-C", str(dest), "fetch", "--quiet", "--depth", "1", "origin", commit],
            capture_output=True,
            text=True,
        )
        if fetch.returncode != 0:
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "--quiet", "origin", commit],
                capture_output=True,
                text=True,
            )
        checkout = subprocess.run(
            ["git", "-C", str(dest), "checkout", "--quiet", commit],
            capture_output=True,
            text=True,
        )
        if checkout.returncode != 0:
            logger.warning(
                "Could not checkout %s in %s (using clone tip)",
                commit,
                dest,
            )


def _materialize_fork_from_parent(
    parent: Path,
    dest: Path,
    *,
    repo_url: str,
    repo_branch: str,
    repo_commit: str,
) -> Path:
    """Create *dest* from *parent* via reflink, opt-in full copy, or clone."""
    if reflink_supported(parent):
        cow_copy_tree(parent, dest, require_reflink=True)
        logger.info("Reflink CoW checkout %s ← %s", dest, parent)
        return dest.resolve()

    if full_workspace_copy_allowed():
        logger.warning(
            "Full-copying workspace (no reflink CoW on this filesystem): %s → %s "
            "(%s=1). Prefer btrfs/xfs with reflink for large trees.",
            parent,
            dest,
            _FULL_COPY_ENV,
        )
        # Explicit full recursive copy — do not claim CoW.
        if dest.exists():
            from ..runs.run_configs import rmtree_robust

            rmtree_robust(dest)
        shutil.copytree(parent, dest, symlinks=True)
        return dest.resolve()

    logger.warning(
        "Fork workspace: filesystem lacks reflink CoW; refusing silent full copy of %s. "
        "Falling back to git clone / empty (parent dirt not preserved). "
        "Set %s=1 to force a full copy, or use a reflink-capable volume.",
        parent,
        _FULL_COPY_ENV,
    )
    url = (repo_url or "").strip()
    if url:
        _git_clone(
            dest,
            repo_url=url,
            repo_branch=repo_branch,
            repo_commit=repo_commit,
        )
        return dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    return dest.resolve()


def prepare_host_checkout(
    runs_dir: Path,
    checkout_id: str,
    *,
    repo_url: str = "",
    repo_branch: str = "",
    repo_commit: str = "",
    parent_checkout: Path | None = None,
) -> Path:
    """Create or replace the host checkout for a run.

    Order:

    1. If *parent_checkout* is set (fork) → reflink CoW when possible; else
       opt-in full copy or git clone / empty (see module doc).
    2. Else if *repo_url* → ``git clone`` (optional branch / commit).
    3. Else → empty directory.

    :param runs_dir: Orchestrator work dir (``…/runs``).
    :param checkout_id: Stable id (container name).
    :param repo_url: Optional git remote.
    :param repo_branch: Optional branch for shallow clone.
    :param repo_commit: Optional SHA after clone.
    :param parent_checkout: Parent run checkout for fork.
    :returns: Absolute path of the host checkout (bind-mount source).
    """
    dest = checkout_path(runs_dir, checkout_id)
    if dest.exists():
        # Prior runs bind-mount this tree into the container; root-owned files
        # (e.g. eval_notes/) need the same robust remove as traces.
        from ..runs.run_configs import rmtree_robust

        rmtree_robust(dest)

    if parent_checkout is not None:
        parent = Path(parent_checkout)
        if parent.is_dir():
            return _materialize_fork_from_parent(
                parent,
                dest,
                repo_url=repo_url or "",
                repo_branch=repo_branch or "",
                repo_commit=repo_commit or "",
            )

    url = (repo_url or "").strip()
    if url:
        try:
            _git_clone(
                dest,
                repo_url=url,
                repo_branch=repo_branch,
                repo_commit=repo_commit,
            )
            logger.info("Cloned %s → %s", url, dest)
            return dest.resolve()
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.warning("Host git clone failed for %s: %s", url, exc)
            if dest.exists():
                from ..runs.run_configs import rmtree_robust

                try:
                    rmtree_robust(dest)
                except Exception:
                    logger.debug("cleanup after failed clone failed", exc_info=True)
            raise RuntimeError(f"host git clone failed: {url}: {exc}") from exc

    dest.mkdir(parents=True, exist_ok=True)
    return dest.resolve()
